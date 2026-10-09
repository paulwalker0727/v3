# ruff: noqa: F811
"""Etapa 4 / Pasul 6A — planul de control PE POSTGRESQL LOCAL (014 peste 008–013). NU atinge Supabase/production. Se rulează doar cu
CIMITIR_TEST_PG_ADMIN_URL=postgresql://<superuser>@127.0.0.1:<port>/postgres (altfel SKIP). Logica Flux A/B NU e rescrisă: aceleași execute_flow_a/b."""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
import psycopg
import pytest

import cimitir_bootstrap as bs
import cimitir_cloud_client as ccl
import cimitir_cloud_control as cc
import cimitir_cloud_control_pg as ccp
import cimitir_cloud_publish as cpub
import cimitir_cloud_staging as cs
import cimitir_cloud_wire as wire
import cimitir_flow_b as fb
import cimitir_manifest as mf
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa4_pas4_flux_b import b_world

ADMIN = os.environ.get("CIMITIR_TEST_PG_ADMIN_URL", "")
MIG = Path(__file__).resolve().parents[1] / "cimitir" / "pg_migrations"
SERVER = Path(__file__).resolve().parent / "etapa4_http_server.py"
LEAKS: list = []
pytestmark = pytest.mark.skipif(not ADMIN or urlparse(ADMIN).hostname not in ("127.0.0.1", "localhost", "::1"), reason="PostgreSQL local de test indisponibil")


def _dsn(db, user=None):
    u = urlparse(ADMIN)
    return u._replace(path="/" + db, netloc=f"{user or u.username}@{u.hostname}:{u.port}").geturl()


def _apply(url, upto=14, files=None, schema=None):
    with psycopg.connect(url, autocommit=True) as c:
        if schema:
            c.execute(f'CREATE SCHEMA "{schema}"')
            c.execute(f'SET search_path = "{schema}"')
        for f in sorted(MIG.glob("*.sql")):
            if int(f.name[:3]) <= upto and (files is None or int(f.name[:3]) in files):
                c.execute(f.read_text(encoding="utf-8"))


def _mkdb(name):
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}"')
        c.execute(f'CREATE DATABASE "{name}"')


@pytest.fixture(scope="module")
def pgdb():
    name = "cp6a_" + uuid.uuid4().hex[:8]
    _mkdb(name)
    _apply(_dsn(name))
    yield {"name": name, "admin": _dsn(name), "app": _dsn(name, "cimitir_cp_app")}
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def pq(pgdb, sql, *a):
    with psycopg.connect(pgdb["admin"], autocommit=True) as c:
        cur = c.execute(sql, a or None)
        return cur.fetchall() if cur.description else []


@pytest.fixture()
def penv(renv, pgdb, monkeypatch):
    pq(pgdb, "TRUNCATE cp_bootstrap_runs, cp_failed_redeems, cp_audit_log, cp_platform_admins, cp_org_roles, cp_binding_issuers, device_pairing_codes, "
             "device_cemetery_bindings, devices, cemeteries, organizations, identities CASCADE")
    monkeypatch.setenv("CIMITIR_CP_DATABASE_URL", pgdb["app"])
    monkeypatch.setenv("CIMITIR_CP_ADMIN_DATABASE_URL", pgdb["admin"])
    renv.cp = ccp.PostgresControlPlane()
    renv.pg = pgdb
    return renv


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def start_server(env, fault=None):
    port = _free_port()
    (env.tmp / "logs").mkdir(exist_ok=True)
    log = open(env.tmp / "logs" / f"pg-server-{port}.log", "a")
    p = subprocess.Popen([sys.executable, str(SERVER), "pg:env", str(port), json.dumps(fault) if fault else "", ""], stdout=log, stderr=log)
    env.procs.append(p)
    url = f"http://127.0.0.1:{port}"
    end = time.time() + 30
    while time.time() < end:
        try:
            if httpx.get(url + wire.API + "/health", timeout=1).status_code == 200:
                return p, url
        except httpx.HTTPError:
            time.sleep(0.1)
    raise AssertionError("serverul nu a pornit")


def cnt(env, sql, *a):
    return pq(env.pg, sql, *a)[0][0]


def assert_b_done(env, w, b, plan, rep):
    cem = b.cemetery_uid
    assert rep["result"] == "completed" and rep["post_publish_verified"] is True and rep["sync_epoch"] == 1
    assert pq(env.pg, "SELECT status, data_epoch FROM cemeteries WHERE uid=%s", cem)[0] == ("ACTIVE", 1)
    cloud = mf.cloud_manifest(env.cp, w.owner1, b.organization_uid, cem)
    assert cloud["content_digest"] == plan["local_manifest"]["content_digest"] == rep["source_content_digest"] and cloud["total_rows"] == 11
    assert cnt(env, "SELECT COUNT(*) FROM cp_registry_rows WHERE cemetery_uid=%s", cem) == 11
    assert cnt(env, "SELECT COUNT(*) FROM device_cemetery_bindings WHERE cemetery_uid=%s AND active AND revoked_at IS NULL", cem) == 1
    assert cnt(env, "SELECT data_epoch FROM cp_bootstrap_runs WHERE bootstrap_id=%s", plan["bootstrap_id"]) == 1
    src, tgt = pq(env.pg, "SELECT manifest_source, manifest_target FROM cp_bootstrap_runs WHERE bootstrap_id=%s", plan["bootstrap_id"])[0]
    assert src["content_digest"] == tgt["content_digest"] == cloud["content_digest"] and src["entity_counts"] == tgt["entity_counts"] and tgt["total_rows"] == 11
    assert src["media_count"] == tgt["media_count"] == cloud["media"]["count"]


# ================================================================== SCHEMĂ 014: fără duplicate de tenancy, JSONB, manifeste, indecși, RLS, grants
def test_schema_no_duplicate_tenancy_and_required_columns(pgdb):
    tabs = {r[0] for r in pq(pgdb, "SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    for dup in ("cp_organizations", "cp_cemeteries", "cp_devices", "cp_bindings", "cp_pairing_codes", "cp_memberships", "cp_principals"):
        assert dup not in tabs
    assert {"organizations", "cemeteries", "devices", "device_cemetery_bindings", "device_pairing_codes"} <= tabs
    cols = dict(pq(pgdb, "SELECT column_name, data_type FROM information_schema.columns WHERE table_name='cp_bootstrap_runs'"))
    assert cols["checkpoint"] == "jsonb" and cols["manifest_source"] == "jsonb" and cols["manifest_target"] == "jsonb"
    for c in ("plan_hash", "bootstrap_id", "data_epoch", "created_at", "started_at", "finished_at"):
        assert c in cols
    fks = {r[0] for r in pq(pgdb, "SELECT confrelid::regclass::text FROM pg_constraint WHERE contype='f' AND conrelid IN "
                                  "('cp_staging_sessions'::regclass,'cp_bootstrap_runs'::regclass,'cp_binding_issuers'::regclass)")}
    assert {"organizations", "cemeteries", "device_cemetery_bindings"} <= fks
    idx = dict(pq(pgdb, "SELECT indexname, indexdef FROM pg_indexes WHERE indexname IN ('cp_one_active_bootstrap','cp_one_open_staging_per_cemetery')"))
    assert "(cemetery_uid)" in idx["cp_one_active_bootstrap"] and "planned" in idx["cp_one_active_bootstrap"] and "verifying" in idx["cp_one_active_bootstrap"]
    assert "(cemetery_uid)" in idx["cp_one_open_staging_per_cemetery"] and "'open'" in idx["cp_one_open_staging_per_cemetery"]


def test_rls_enabled_and_minimal_grants(pgdb):
    cp = [r[0] for r in pq(pgdb, "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'cp\\_%'")]
    assert len(cp) == 18
    assert all(r[1] for r in pq(pgdb, "SELECT relname, relrowsecurity FROM pg_class WHERE relname LIKE 'cp\\_%' AND relkind='r'"))
    assert pq(pgdb, "SELECT rolcanlogin, rolbypassrls, rolsuper FROM pg_roles WHERE rolname='cimitir_cp_app'") == [(True, False, False)]
    assert not pq(pgdb, "SELECT 1 FROM information_schema.table_privileges WHERE grantee='cimitir_cp_app' AND table_name IN ('locuri_de_veci','inhumati','users','identities')")
    assert not pq(pgdb, "SELECT 1 FROM information_schema.role_usage_grants WHERE grantee='cimitir_cp_app' AND object_name NOT LIKE 'cp\\_%' AND object_type='SEQUENCE'")
    assert not pq(pgdb, "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prosecdef AND p.proname LIKE 'cp\\_%' "
                        "AND NOT EXISTS (SELECT 1 FROM unnest(p.proconfig) s WHERE s LIKE 'search_path=%')")


def test_fresh_install_in_temporary_schema_does_not_touch_public(pgdb):
    name = "cp6a_" + uuid.uuid4().hex[:8]
    _mkdb(name)
    try:
        url = _dsn(name)
        _apply(url, schema="tmp_cp")
        with psycopg.connect(url, autocommit=True) as c:
            assert c.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'").fetchone()[0] == 0
            assert c.execute("SELECT count(*) FROM pg_tables WHERE schemaname='tmp_cp' AND tablename LIKE 'cp\\_%'").fetchone()[0] == 18
            cfg = [r[0] for r in c.execute("SELECT unnest(proconfig) FROM pg_proc WHERE proname IN ('cp_org_exists','cp_cemetery_org')")]
            assert cfg and all("tmp_cp" in x and "public" not in x for x in cfg)
            c.execute("DROP SCHEMA tmp_cp CASCADE")
    finally:
        with psycopg.connect(ADMIN, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_upgrade_013_to_014_preserves_data_and_is_idempotent():
    name = "cp6a_" + uuid.uuid4().hex[:8]
    _mkdb(name)
    try:
        url = _dsn(name)
        _apply(url, upto=13)
        org, cem = str(uuid.uuid4()), str(uuid.uuid4())
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("INSERT INTO organizations (uid, organization_type, official_name, display_name) VALUES (%s,'OTHER','O','O')", (org,))
            c.execute("INSERT INTO cemeteries (uid, organization_uid, name, internal_code) VALUES (%s,%s,'C','c1')", (cem, org))
            before = c.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'").fetchone()[0]
        _apply(url, files={14})
        _apply(url, files={14})
        with psycopg.connect(url, autocommit=True) as c:
            assert c.execute("SELECT status, data_epoch FROM cemeteries WHERE uid=%s", (cem,)).fetchone() == ("ACTIVE", 1)
            assert c.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'").fetchone()[0] == before + 18
    finally:
        with psycopg.connect(ADMIN, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_production_ref_hard_blocked_and_supabase_requires_exact_test_ref(monkeypatch):
    prod = "postgresql://postgres.txuxkjgftopznvmooexq:x@aws-0-eu-west-1.pooler.supabase.com:5432/postgres?sslmode=require"
    test = "postgresql://postgres.nhhczdmcocbobptogvsw:x@aws-0-eu-west-1.pooler.supabase.com:5432/postgres?sslmode=require"
    monkeypatch.setenv("CIMITIR_LIVE_TESTS", "1")
    monkeypatch.setenv("CIMITIR_TEST_PROJECT_REF", "txuxkjgftopznvmooexq")
    with pytest.raises(RuntimeError):
        ccp._guard(prod)
    with pytest.raises(RuntimeError):
        ccp._guard(test)  # ref din env ≠ ref TEST
    monkeypatch.setenv("CIMITIR_TEST_PROJECT_REF", "nhhczdmcocbobptogvsw")
    ccp._guard(test)
    monkeypatch.setenv("CIMITIR_LIVE_TESTS", "0")
    with pytest.raises(RuntimeError):
        ccp._guard(test)


# ================================================================== FLUX A / FLUX B PE POSTGRESQL (direct)
def test_flow_a_local_pg(penv):
    env = penv
    w = World(env)
    b = w.local(w.org1)
    code = env.cp.issue_code(w.owner1, b.organization_uid)
    plan = bs.plan_flow_a(b, env.cp, code, env.inst, cemetery_name="Cimitir PG")
    rep = bs.execute_flow_a(b, env.cp, env.store, plan, plan["plan_hash"], code, env.inst)
    assert pq(env.pg, "SELECT status, data_epoch FROM cemeteries WHERE uid=%s", b.cemetery_uid)[0] == ("ACTIVE", 1)
    assert cnt(env, "SELECT COUNT(*) FROM device_cemetery_bindings WHERE active AND revoked_at IS NULL") == 1 and bs.LocalRegistry(b).sync_state == "BOUND"
    assert bs.execute_flow_a(b, env.cp, env.store, plan, plan["plan_hash"], code, env.inst) == rep  # idempotent
    assert cnt(env, "SELECT COUNT(*) FROM device_cemetery_bindings") == 1 and cnt(env, "SELECT COUNT(*) FROM cp_bootstrap_runs") == 1
    assert cnt(env, "SELECT COUNT(*) FROM organizations") == 2 and cnt(env, "SELECT COUNT(*) FROM cemeteries") == 1  # fără duplicate de tenancy


def test_flow_b_local_pg_with_manifest_and_initial_epoch(penv):
    env = penv
    w, b, code = b_world(env)
    plan = fb.plan_flow_b(b, env.cp, w.owner1, env.inst)
    rep = fb.execute_flow_b(b, env.cp, env.store, w.owner1, plan, plan["plan_hash"], code, env.inst, batch_size=2, chunk_size=3)
    assert_b_done(env, w, b, plan, rep)
    assert cnt(env, "SELECT manifest_source IS NOT NULL FROM cp_bootstrap_runs") is True
    assert cnt(env, "SELECT COUNT(*) FROM cp_media_published") == 3 and cnt(env, "SELECT COUNT(*) FROM cp_staging_rows") == 0  # staging curățat la publish
    assert cnt(env, "SELECT COUNT(*) FROM cp_staging_sessions WHERE state='published'") == 1


# ================================================================== HTTP: FLUX B + COMMIT-DAR-RĂSPUNS-PIERDUT + RESTART + RETRY
def _rb(env, w, b, code, url, **kw):
    rcp = ccl.RemoteControlPlane(url, code, **kw)
    plan = fb.plan_flow_b(b, rcp, w.owner1, env.inst)
    rep = fb.execute_flow_b(b, rcp, env.store, w.owner1, plan, plan["plan_hash"], code, env.inst, batch_size=2, chunk_size=3)
    return plan, rep


def test_flow_b_over_http_pg_and_flow_a_over_http(penv):
    env = penv
    w, b, code = b_world(env)
    _p, url = start_server(env)
    plan, rep = _rb(env, w, b, code, url)
    assert_b_done(env, w, b, plan, rep)
    assert bs.ping(b, ccl.RemoteControlPlane(url, code), env.store, env.inst)["sync_epoch"] == 1
    b2 = w.local(w.org2)
    code2 = env.cp.issue_code(w.owner2, b2.organization_uid)
    rcp = ccl.RemoteControlPlane(url, code2)
    plan2 = bs.plan_flow_a(b2, rcp, code2, env.inst, cemetery_name="A http")
    bs.execute_flow_a(b2, rcp, env.store, plan2, plan2["plan_hash"], code2, env.inst)
    assert cnt(env, "SELECT COUNT(*) FROM device_cemetery_bindings WHERE active") == 2


@pytest.mark.parametrize("op,nth", [("begin_run", 1), ("redeem_code", 1), ("staging_put_batch", 2), ("media_put_chunk", 2), ("publish_flow_b", 1)])
def test_commit_but_response_lost_then_retry_no_duplicates_pg(penv, op, nth):
    env = penv
    w, b, code = b_world(env)
    _p, url = start_server(env, fault={"op": op, "nth": nth, "mode": "delay", "delay": 2.0})
    plan, rep = _rb(env, w, b, code, url, timeout=0.8, retries=4, backoff=0.1)
    assert_b_done(env, w, b, plan, rep)
    assert cnt(env, "SELECT COUNT(*) FROM device_cemetery_bindings") == 1 and cnt(env, "SELECT COUNT(*) FROM cp_bootstrap_runs") == 1
    assert cnt(env, "SELECT COUNT(*) FROM cp_audit_log WHERE action='publish_flow_b'") == 1 and cnt(env, "SELECT COUNT(*) FROM devices") == 1
    assert cnt(env, "SELECT COUNT(*) FROM cp_registry_rows") == 11 and cnt(env, "SELECT COUNT(*) FROM cp_staging_sessions") == 1


@pytest.mark.parametrize("op,nth", [("redeem_code", 1), ("staging_put_batch", 3), ("publish_flow_b", 1)])
def test_server_killed_after_commit_restart_and_resume_pg(penv, op, nth):
    env = penv
    w, b, code = b_world(env)
    p, url = start_server(env, fault={"op": op, "nth": nth, "mode": "kill"})
    rcp = ccl.RemoteControlPlane(url, code, timeout=2, retries=1, backoff=0.05)
    plan = fb.plan_flow_b(b, rcp, w.owner1, env.inst)
    with pytest.raises(ccl.TransportError):
        fb.execute_flow_b(b, rcp, env.store, w.owner1, plan, plan["plan_hash"], code, env.inst, batch_size=1, chunk_size=3)
    assert p.wait(timeout=10) == 137
    if op == "publish_flow_b":
        assert pq(env.pg, "SELECT status, data_epoch FROM cemeteries WHERE uid=%s", b.cemetery_uid)[0] == ("ACTIVE", 1)
    _p2, url2 = start_server(env)
    rep = fb.execute_flow_b(b, ccl.RemoteControlPlane(url2, code), env.store, w.owner1, plan, plan["plan_hash"], code, env.inst, batch_size=1, chunk_size=3)
    assert_b_done(env, w, b, plan, rep)
    assert cnt(env, "SELECT COUNT(*) FROM device_cemetery_bindings") == 1 and cnt(env, "SELECT COUNT(*) FROM cp_bootstrap_runs") == 1


def test_concurrent_bootstrap_single_active_run_and_advisory_lock(penv):
    env = penv
    w, b, code = b_world(env)
    plan = fb.plan_flow_b(b, env.cp, w.owner1, env.inst)
    import cimitir_plan_guard as pg
    claim = pg.claim_for(plan)
    res = []

    def go():
        cp = ccp.PostgresControlPlane()
        try:
            cp.begin_run(code, env.inst, str(uuid.uuid4()), "B", b.cemetery_uid, plan["plan_hash"], plan=claim)
            res.append("ok")
        except Exception as e:  # noqa: BLE001
            res.append(type(e).__name__)
    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert res.count("ok") == 1 and set(res) - {"ok"} <= {"BootstrapLocked", "ConflictError", "PlanRejected", "CodeError"}, res
    assert cnt(env, "SELECT COUNT(*) FROM cp_bootstrap_runs WHERE state IN ('planned','running','verifying')") == 1
    with psycopg.connect(env.pg["admin"], autocommit=True) as c1, psycopg.connect(env.pg["admin"], autocommit=True) as c2:
        c1.execute("BEGIN")
        c1.execute("SELECT cp_lock_cemetery(%s)", (b.cemetery_uid,))
        assert c2.execute("SELECT cp_try_lock_cemetery(%s)", (b.cemetery_uid,)).fetchone()[0] is False  # pg_advisory_lock per cimitir
        other = str(uuid.uuid4())
        c2.execute("BEGIN")
        assert c2.execute("SELECT cp_try_lock_cemetery(%s)", (other,)).fetchone()[0] is True  # alt cimitir nu e blocat
        c1.execute("ROLLBACK")
        c2.execute("ROLLBACK")
        c2.execute("BEGIN")
        assert c2.execute("SELECT cp_try_lock_cemetery(%s)", (b.cemetery_uid,)).fetchone()[0] is True
        c2.execute("ROLLBACK")


# ================================================================== RLS / IZOLARE TENANT (2 org × 2 cimitire)
def _ctx_conn(pg, **ctx):
    c = psycopg.connect(pg["app"], autocommit=True)
    for k, v in ctx.items():
        c.execute("SELECT set_config(%s, %s, false)", (f"cp.{k}", v))
    return c


def _two_by_two(env):
    w = World(env)
    cp, out = env.cp, {}
    for tag, org, owner in (("A", w.org1, w.owner1), ("B", w.org2, w.owner2)):
        for n in (1, 2):
            cem = str(uuid.uuid4())
            cp.create_cemetery(owner, org, cem, f"{tag}{n}")
            cp.sim_put_row(cem, "locuri_de_veci", f"{tag}{n}-uid", {"cod": f"{tag}{n}"})
            out[f"{tag}{n}"] = cem
    return w, out


def _count(c, sql, *a):
    return c.execute(sql, a).fetchone()[0]


def test_rls_tenant_isolation_two_orgs_two_cemeteries_leakage_zero(penv):
    env = penv
    w, cems = _two_by_two(env)
    a = _ctx_conn(env.pg, org=w.org1, cems="*")
    assert _count(a, "SELECT COUNT(*) FROM cemeteries") == 2 and _count(a, "SELECT COUNT(*) FROM cp_registry_rows") == 2
    LEAKS.extend(r for r in a.execute("SELECT cemetery_uid FROM cp_registry_rows").fetchall() if str(r[0]) in (cems["B1"], cems["B2"]))
    assert _count(a, "SELECT COUNT(*) FROM organizations") == 1
    a1 = _ctx_conn(env.pg, org=w.org1, cems=cems["A1"])
    assert _count(a1, "SELECT COUNT(*) FROM cp_registry_rows WHERE cemetery_uid=%s", cems["A2"]) == 0  # A1 nu vede/modifică A2
    assert a1.execute("UPDATE cp_registry_row_data SET content='x' WHERE cemetery_uid=%s", (cems["A2"],)).rowcount == 0
    with pytest.raises(psycopg.errors.Error):
        a1.execute("INSERT INTO cp_registry_rows (cemetery_uid, entity, uid) VALUES (%s,'e','u')", (cems["A2"],))
    with pytest.raises(psycopg.errors.Error):
        a.execute("INSERT INTO cp_registry_rows (cemetery_uid, organization_uid, entity, uid) VALUES (%s,%s,'e','u')", (cems["B1"], w.org2))
    none = _ctx_conn(env.pg)  # fără context ⇒ nimic
    assert _count(none, "SELECT COUNT(*) FROM cemeteries") == 0 and _count(none, "SELECT COUNT(*) FROM cp_registry_rows") == 0
    assert _count(none, "SELECT COUNT(*) FROM organizations") == 0
    assert not LEAKS


def test_foreign_bootstrap_staging_and_binding_secret_refused(penv):
    env = penv
    w, cems = _two_by_two(env)
    cp = env.cp
    codeA = cp.issue_code(w.owner1, w.org1, cems["A1"])
    resA = cp.redeem_code(codeA, env.inst, "MAIN", cems["A1"])
    keyA = resA["binding_key"]
    with pytest.raises(cc.ScopeError):
        cp.authenticate(keyA, cems["B1"])  # secretul A nu accesează B
    with pytest.raises(cc.ScopeError):
        cp.authenticate(keyA.replace(resA["binding_uid"], str(uuid.uuid4())))
    c = _ctx_conn(env.pg, binding_uid=resA["binding_uid"], key_hash=cc._h(keyA))
    assert _count(c, "SELECT COUNT(*) FROM device_cemetery_bindings") == 1  # doar propria legătură
    wrong = _ctx_conn(env.pg, binding_uid=resA["binding_uid"], key_hash=cc._h(keyA + "x"))
    assert _count(wrong, "SELECT COUNT(*) FROM device_cemetery_bindings") == 0
    sess = {k: "x" for k in cs.tc.SESSION_FIELDS}
    sess.update(organization_uid=w.org1, cemetery_uid=cems["A1"])
    with pytest.raises((cc.ScopeError, cc.PermissionDenied)):
        cs.staging_open(cp, w.owner2, w.org1, cems["A1"], str(uuid.uuid4()), sess, {})  # owner B → org A
    with pytest.raises((cc.ScopeError, cc.PermissionDenied)):
        cp.begin_run(cp.issue_code(w.owner1, w.org1, cems["A1"]), env.inst, str(uuid.uuid4()), "A", cems["B1"], "p" * 64)  # cod A → cimitir B
    assert cp.inspect_as(w.owner1, w.org1, cems["B1"])["exists"] is False  # alt tenant ≡ inexistent
    assert cnt(env, "SELECT COUNT(*) FROM cp_bootstrap_runs") == 0 and cnt(env, "SELECT COUNT(*) FROM cp_staging_sessions") == 0


def test_failed_redeems_scoped_per_installation_not_global(penv):
    env = penv
    pq(env.pg, "INSERT INTO cp_failed_redeems (installation_uid, n) VALUES (%s,2),(%s,3)", "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002")
    c = _ctx_conn(env.pg, installation="00000000-0000-0000-0000-000000000001")
    assert c.execute("SELECT n FROM cp_failed_redeems").fetchall() == [(2,)]
    assert c.execute("UPDATE cp_failed_redeems SET n=0 WHERE installation_uid='00000000-0000-0000-0000-000000000002'").rowcount == 0
    assert _count(_ctx_conn(env.pg), "SELECT COUNT(*) FROM cp_failed_redeems") == 0
    inst = "00000000-0000-0000-0000-0000000000aa"
    for _ in range(cc.MAX_FAILED_REDEEMS):
        with pytest.raises(cc.CodeError):
            env.cp.preflight_for_code("cod-gresit", inst, str(uuid.uuid4()))
    with pytest.raises(cc.CodeError, match="prea multe"):
        env.cp.preflight_for_code("cod-gresit", inst, str(uuid.uuid4()))


# ================================================================== MANIFEST_TARGET (publish verifică ținta LIVE înainte de `completed`)
def _expect(plan):
    lm = plan["local_manifest"]
    return {"plan_hash": plan["plan_hash"], "source_manifest_digest": lm["content_digest"], "entity_counts": {e: v["count"] for e, v in lm["entities"].items()},
            "media_count": lm["media"]["count"], "data_epoch": 1}


def test_manifest_source_equals_target_after_flux_b(penv):
    env = penv
    w, b, code = b_world(env)
    plan = fb.plan_flow_b(b, env.cp, w.owner1, env.inst)
    rep = fb.execute_flow_b(b, env.cp, env.store, w.owner1, plan, plan["plan_hash"], code, env.inst, batch_size=2, chunk_size=3)
    assert_b_done(env, w, b, plan, rep)
    src, tgt, state = pq(env.pg, "SELECT manifest_source, manifest_target, state FROM cp_bootstrap_runs")[0]
    assert state == "completed" and src == tgt == {**src, "side": src["side"]} or (src["content_digest"] == tgt["content_digest"])
    assert src["content_digest"] == plan["local_manifest"]["content_digest"] and src["total_rows"] == tgt["total_rows"] == 11


@pytest.mark.parametrize("how", ["row_missing", "uncomputable"])
def test_target_manifest_mismatch_or_uncomputable_fails_before_completed(penv, monkeypatch, how):
    env = penv
    w, b, code = b_world(env)
    plan = fb.plan_flow_b(b, env.cp, w.owner1, env.inst)
    real = env.cp._snapshot_on

    def bad(c, org, cem):
        import inspect
        if not any(f.function == "publish_flow_b" for f in inspect.stack()):
            return real(c, org, cem)
        if how == "uncomputable":
            raise RuntimeError("snapshot indisponibil")
        snap = real(c, org, cem)
        ent = next(e for e, rows in snap["entities"].items() if rows)
        snap["entities"][ent] = snap["entities"][ent][1:]
        return snap
    monkeypatch.setattr(env.cp, "_snapshot_on", bad)
    with pytest.raises(Exception):  # noqa: B017
        fb.execute_flow_b(b, env.cp, env.store, w.owner1, plan, plan["plan_hash"], code, env.inst, batch_size=2, chunk_size=3)
    assert cnt(env, "SELECT COUNT(*) FROM cp_bootstrap_runs WHERE state='completed'") == 0
    assert pq(env.pg, "SELECT status, data_epoch FROM cemeteries WHERE uid=%s", b.cemetery_uid)[0] == ("DRAFT", 1)  # publish anulat integral
    assert cnt(env, "SELECT COUNT(*) FROM cp_registry_rows") == 0 and cnt(env, "SELECT COUNT(*) FROM cp_media_published") == 0
    assert cnt(env, "SELECT manifest_target IS NULL FROM cp_bootstrap_runs") is True
    assert cnt(env, "SELECT COUNT(*) FROM cp_audit_log WHERE action='publish_flow_b'") == 0
    assert bs.LocalRegistry(b).sync_state != "BOUND"


def test_retry_after_publish_reverifies_target_without_second_publish(penv):
    env = penv
    w, b, code = b_world(env)
    plan = fb.plan_flow_b(b, env.cp, w.owner1, env.inst)
    fb.execute_flow_b(b, env.cp, env.store, w.owner1, plan, plan["plan_hash"], code, env.inst, batch_size=2, chunk_size=3)
    before = pq(env.pg, "SELECT manifest_target, finished_at FROM cp_bootstrap_runs")[0]
    seq = cnt(env, "SELECT MAX(change_seq) FROM cp_registry_row_data")
    exp = _expect(plan)
    for _ in range(2):
        assert cpub.publish_flow_b(env.cp, w.owner1, b.organization_uid, b.cemetery_uid, plan["bootstrap_id"], "nu-conteaza", exp) == {"published": True, "already": True}
    assert pq(env.pg, "SELECT manifest_target, finished_at FROM cp_bootstrap_runs")[0] == before  # nimic rescris
    assert cnt(env, "SELECT COUNT(*) FROM cp_audit_log WHERE action='publish_flow_b'") == 1 and cnt(env, "SELECT MAX(change_seq) FROM cp_registry_row_data") == seq
    env.cp.sim_put_row(b.cemetery_uid, "locuri_de_veci", "extra-uid", {"cod": "X"})  # ținta LIVE s-a schimbat după publish → retry-ul o detectează
    with pytest.raises(cpub.PublishBlocked):
        cpub.publish_flow_b(env.cp, w.owner1, b.organization_uid, b.cemetery_uid, plan["bootstrap_id"], "nu-conteaza", exp)


def test_response_lost_after_publish_resume_verifies_target_over_http(penv):
    env = penv
    w, b, code = b_world(env)
    p, url = start_server(env, fault={"op": "publish_flow_b", "nth": 1, "mode": "kill"})
    rcp = ccl.RemoteControlPlane(url, code, timeout=2, retries=1, backoff=0.05)
    plan = fb.plan_flow_b(b, rcp, w.owner1, env.inst)
    with pytest.raises(ccl.TransportError):
        fb.execute_flow_b(b, rcp, env.store, w.owner1, plan, plan["plan_hash"], code, env.inst, batch_size=1, chunk_size=3)
    assert p.wait(timeout=10) == 137
    tgt0 = pq(env.pg, "SELECT manifest_target FROM cp_bootstrap_runs")[0][0]
    assert tgt0 and tgt0["content_digest"] == plan["local_manifest"]["content_digest"]  # commit-ul a scris ținta înainte de răspuns
    _p2, url2 = start_server(env)
    rep = fb.execute_flow_b(b, ccl.RemoteControlPlane(url2, code), env.store, w.owner1, plan, plan["plan_hash"], code, env.inst, batch_size=1, chunk_size=3)
    assert_b_done(env, w, b, plan, rep)
    assert pq(env.pg, "SELECT manifest_target FROM cp_bootstrap_runs")[0][0] == tgt0 and cnt(env, "SELECT COUNT(*) FROM cp_audit_log WHERE action='publish_flow_b'") == 1


def test_cross_tenant_target_manifest_refused(penv):
    env = penv
    w, b, code = b_world(env)
    plan = fb.plan_flow_b(b, env.cp, w.owner1, env.inst)
    fb.execute_flow_b(b, env.cp, env.store, w.owner1, plan, plan["plan_hash"], code, env.inst, batch_size=2, chunk_size=3)
    with pytest.raises((cc.ScopeError, cc.PermissionDenied)):
        mf.cloud_manifest(env.cp, w.owner2, b.organization_uid, b.cemetery_uid)  # owner B → org A
    foreign = str(uuid.uuid4())
    env.cp.create_cemetery(w.owner2, w.org2, foreign, "B")
    assert mf.cloud_manifest(env.cp, w.owner1, b.organization_uid, foreign)["total_rows"] == 0  # cimitir B văzut de A ≡ inexistent
    with pytest.raises((cc.ScopeError, cc.PermissionDenied)):
        cpub.publish_flow_b(env.cp, w.owner2, b.organization_uid, b.cemetery_uid, plan["bootstrap_id"], "x", _expect(plan))
    other = _ctx_conn(env.pg, org=w.org2, cems="*")
    assert _count(other, "SELECT COUNT(*) FROM cp_bootstrap_runs") == 0 and other.execute("SELECT manifest_target FROM cp_bootstrap_runs").fetchall() == []
    mine = _ctx_conn(env.pg, org=b.organization_uid, cems="*")
    assert _count(mine, "SELECT COUNT(*) FROM cp_bootstrap_runs WHERE manifest_target IS NOT NULL") == 1
