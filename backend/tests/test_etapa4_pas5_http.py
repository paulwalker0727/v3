# ruff: noqa: F811
"""Etapa 4 / Pasul 5 — FastAPI local transport pentru Flux A/B (MAIN → HTTP localhost → FastAPI → simulator SQLite). Fără Supabase/PostgreSQL/Render/producție.
Serverul rulează în PROCES SEPARAT (uvicorn pe 127.0.0.1); logica de bootstrap NU e rescrisă: aceleași execute_flow_a/b rulează peste RemoteControlPlane."""
from __future__ import annotations

import json
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

import cimitir_bootstrap as bs
import cimitir_cloud_client as ccl
import cimitir_cloud_control as cc
import cimitir_cloud_http as ch
import cimitir_cloud_wire as wire
import cimitir_flow_b as fb
import cimitir_manifest as mf
import cimitir_snapshot as snap
import cimitir_transfer_contract as tc
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import finish, kill9, renv, spawn, wait_marker  # noqa: F401
from test_etapa4_pas3_transfer import q
from test_etapa4_pas4_flux_b import assert_completed, b_world, cem_state, cloud_counts, outbox_rows

SERVER = Path(__file__).resolve().parent / "etapa4_http_server.py"


def start_server(env, fault=None, port=None):
    port = port or _free_port()
    (env.tmp / "logs").mkdir(exist_ok=True)
    log = open(env.tmp / "logs" / f"server-{port}.log", "a")
    p = subprocess.Popen([sys.executable, str(SERVER), str(env.cp.path), str(port), json.dumps(fault) if fault else ""], stdout=log, stderr=log)
    env.procs.append(p)
    url = f"http://127.0.0.1:{port}"
    end = time.time() + 30
    while time.time() < end:
        try:
            if httpx.get(url + wire.API + "/health", timeout=1).status_code == 200:
                return p, url, port
        except httpx.HTTPError:
            time.sleep(0.1)
    raise AssertionError("serverul nu a pornit")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture()
def renv_logs(renv):
    (renv.tmp / "logs").mkdir(exist_ok=True)
    return renv


def db(env, query, *args):
    return q(env.cp.path, query, *args)


def run_b_http(env, w, b, code, url, hook=None, rcp=None, plan=None, **kw):
    rcp = rcp or ccl.RemoteControlPlane(url, code, **kw)
    plan = plan or fb.plan_flow_b(b, rcp, w.owner1, env.inst)
    rep = fb.execute_flow_b(b, rcp, env.store, w.owner1, plan, plan["plan_hash"], code, env.inst, hook=hook, batch_size=2, chunk_size=3)
    return plan, rep, rcp


# ================================================================== FLUX B + A PESTE HTTP
def test_flow_b_over_http_full_with_pending_then_active_binding(renv_logs):
    env = renv_logs
    w, b, code = b_world(env)
    _p, url, _ = start_server(env)
    seen = {}

    def hook(phase):
        if phase in ("verify:after", "publish:before"):
            seen[phase] = db(env, "SELECT status FROM bindings")
        if phase == "publish:after_commit":
            seen[phase] = db(env, "SELECT status FROM bindings")
        if phase == "publish:before":
            key = env.store.get(bs.LocalRegistry(b).get("cloud_secret_ref"))
            assert httpx.post(url + wire.API + "/auth/binding", headers={"Authorization": f"Binding {key}"}, json={"args": [None, b.cemetery_uid]}).status_code == 401  # pending ≠ activ

    plan, rep, rcp = run_b_http(env, w, b, code, url, hook=hook)
    assert seen["verify:after"] == [("pending",)] and seen["publish:before"] == [("pending",)] and seen["publish:after_commit"] == [("active",)]
    assert_completed(env, w, b, plan, rep)
    assert db(env, "SELECT COUNT(*) FROM bindings")[0][0] == 1 and bs.ping(b, rcp, env.store, env.inst)["sync_epoch"] == 1  # ping peste HTTP
    assert rep["outbox_archived"] == 3 and outbox_rows(b) == []


def test_flow_a_over_http(renv_logs):
    env = renv_logs
    w = World(env)
    b = w.local(w.org1)
    code = env.cp.issue_code(w.owner1, b.organization_uid)
    _p, url, _ = start_server(env)
    rcp = ccl.RemoteControlPlane(url, code)
    plan = bs.plan_flow_a(b, rcp, code, env.inst, cemetery_name="Cimitir HTTP")
    rep = bs.execute_flow_a(b, rcp, env.store, plan, plan["plan_hash"], code, env.inst)
    assert cem_state(env, b.cemetery_uid) == ("ACTIVE", 1) and db(env, "SELECT COUNT(*) FROM bindings WHERE status='active'")[0][0] == 1
    assert bs.LocalRegistry(b).sync_state == "BOUND" and bs.ping(b, rcp, env.store, env.inst)
    again = bs.execute_flow_a(b, rcp, env.store, plan, plan["plan_hash"], code, env.inst)  # idempotent
    assert again == rep and db(env, "SELECT COUNT(*) FROM bindings")[0][0] == 1 and db(env, "SELECT COUNT(*) FROM bootstrap_runs")[0][0] == 1


# ================================================================== CONTRACT HTTP: STATUS + COD INTERN STABIL
@pytest.mark.parametrize("exc,status,code", [
    (wire.Unauthenticated("x"), 401, "UNAUTHENTICATED"), (cc.ScopeError("x"), 404, "SCOPE_MISMATCH"), (cc.PermissionDenied("x"), 403, "FORBIDDEN"),
    (cc.CodeError("x"), 401, "INVALID_CODE"), (cc.BootstrapLocked("x"), 409, "BOOTSTRAP_IN_PROGRESS"), (cc.ConflictError("x"), 409, "CONFLICT"),
    (tc.ConflictingBatch("x"), 409, "BATCH_CONFLICT"), (tc.SourceDrift("x"), 409, "SOURCE_DRIFT"), (tc.SessionMismatch("x"), 409, "SESSION_MISMATCH"),
    (tc.OrphanFK("x"), 422, "ORPHAN_FK"), (tc.CorruptBatch("x"), 400, "BATCH_CORRUPT"), (tc.ContractError("x"), 400, "CONTRACT_INVALID"),
    (KeyError("x"), 400, "REQUEST_INVALID"), (RuntimeError("secret-SECRETVAL"), 500, "INTERNAL")])
def test_stable_status_and_internal_codes_and_client_roundtrip(exc, status, code):
    r = ch._error(exc, "SECRETVAL")
    body = json.loads(r.body)
    assert r.status_code == status and body["code"] == code and "SECRETVAL" not in r.body.decode() and body["ok"] is False
    if type(exc) in [c for c, _s, _c in wire.ERRORS]:
        assert wire.CODE_TO_CLASS[code] is type(exc)  # codul intern se transformă înapoi în aceeași excepție în MAIN


def test_raw_http_contract_auth_and_no_generic_endpoints(renv_logs):
    env = renv_logs
    w, b, code = b_world(env)
    _p, url, _ = start_server(env)
    H = {"Authorization": f"Pairing {code}"}
    ep = url + wire.API + wire.OPS["export_snapshot"][0]
    body = {"args": [None, b.organization_uid, b.cemetery_uid]}
    assert httpx.post(ep, json=body).json()["code"] == "UNAUTHENTICATED"
    assert httpx.post(ep, json=body, headers={"Authorization": "Pairing nu-exista"}).status_code == 401
    assert httpx.post(ep + "?code=" + code, json=body).status_code == 401  # secretul în URL nu este acceptat
    assert httpx.post(ep, json=body, headers={"Authorization": f"Bearer {code}"}).status_code == 401
    ok = httpx.post(ep, json=body, headers=H)
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert httpx.post(ep, content=b"{nu e json", headers=H).json()["code"] == "REQUEST_INVALID"
    assert httpx.post(ep, json={"args": "x"}, headers=H).status_code == 400
    assert httpx.post(ep, json={"args": [None, b.organization_uid, str(uuid.uuid4())]}, headers=H).json()["code"] == "SCOPE_MISMATCH"  # cimitir arbitrar: nu se crede în client
    assert httpx.post(ep, json={"args": [None, str(uuid.uuid4()), b.cemetery_uid]}, headers=H).status_code == 404
    bind_only = url + wire.API + wire.OPS["resume_run"][0]
    assert httpx.post(bind_only, json={"args": [None, str(uuid.uuid4()), "x"]}, headers=H).status_code == 401  # cod de pairing ≠ binding
    for path in ("/sql", "/exec", "/db", "/cloud/query"):
        assert httpx.post(url + wire.API + path, json={}, headers=H).status_code in (404, 405)
    for path in ("/docs", "/openapi.json", wire.API + "/openapi.json"):
        assert httpx.get(url + path).status_code == 404
    other = httpx.post(url + wire.API + wire.OPS["begin_run"][0], headers=H, json={"args": [None, env.inst, str(uuid.uuid4()), "B", b.cemetery_uid, "h" * 64]})
    assert other.status_code == 200
    second = httpx.post(url + wire.API + wire.OPS["begin_run"][0], headers=H, json={"args": [None, env.inst, str(uuid.uuid4()), "B", b.cemetery_uid, "h" * 64]})
    assert second.status_code == 409 and second.json()["code"] in ("BOOTSTRAP_IN_PROGRESS", "CONFLICT")  # bootstrap concurent
    assert db(env, "SELECT COUNT(*) FROM bootstrap_runs")[0][0] == 1


# ================================================================== IDEMPOTENȚĂ: COMMIT FĂCUT, RĂSPUNS PIERDUT (server viu)
@pytest.mark.parametrize("op,nth", [("begin_run", 1), ("redeem_code", 1), ("staging_put_batch", 2), ("media_put_chunk", 2), ("staging_open", 1), ("media_finalize", 1),
                                    ("publish_flow_b", 1), ("mark_verifying", 1)])
def test_commit_but_response_lost_then_retry_has_no_duplicates(renv_logs, op, nth):
    env = renv_logs
    w, b, code = b_world(env)
    _p, url, _ = start_server(env, fault={"op": op, "nth": nth, "mode": "delay", "delay": 2.0})
    plan, rep, _ = run_b_http(env, w, b, code, url, timeout=0.8, retries=4, backoff=0.1)
    assert_completed(env, w, b, plan, rep)
    assert db(env, "SELECT COUNT(*) FROM bindings")[0][0] == 1 and db(env, "SELECT COUNT(*) FROM bootstrap_runs")[0][0] == 1
    assert db(env, "SELECT COUNT(*) FROM audit_log WHERE action='publish_flow_b'")[0][0] == 1  # publish o singură dată
    assert db(env, "SELECT COUNT(*) FROM staging_sessions")[0][0] == 1 and db(env, "SELECT COUNT(*) FROM registry_rows")[0][0] == 11
    assert db(env, "SELECT COUNT(*) FROM media_published")[0][0] == 3 and db(env, "SELECT COUNT(*) FROM media_objects")[0][0] == 3
    assert db(env, "SELECT COUNT(*) FROM devices")[0][0] == 1


# ================================================================== SERVER OMORÂT DUPĂ COMMIT, ÎNAINTE DE RĂSPUNS → RESTART → RESUME DIN MAIN
@pytest.mark.parametrize("op,nth", [("redeem_code", 1), ("staging_put_batch", 3), ("media_put_chunk", 2), ("publish_flow_b", 1)])
def test_server_killed_after_commit_before_response_then_restart_and_resume(renv_logs, op, nth):
    env = renv_logs
    w, b, code = b_world(env)
    p, url, port = start_server(env, fault={"op": op, "nth": nth, "mode": "kill"})
    rcp = ccl.RemoteControlPlane(url, code, timeout=2, retries=1, backoff=0.05)
    plan = fb.plan_flow_b(b, rcp, w.owner1, env.inst)
    with pytest.raises(ccl.TransportError):
        fb.execute_flow_b(b, rcp, env.store, w.owner1, plan, plan["plan_hash"], code, env.inst, batch_size=1, chunk_size=3)
    assert p.wait(timeout=10) == 137 and bs.LocalRegistry(b).run_get(plan["bootstrap_id"])["state"] in ("running", "verifying")  # MAIN rămâne reluabil, fără rollback orb
    if op == "publish_flow_b":
        assert cem_state(env, b.cemetery_uid) == ("ACTIVE", 1)  # commit-ul s-a făcut, răspunsul s-a pierdut
    _p2, url2, _ = start_server(env)  # restart (alt port; aceeași bază)
    rcp2 = ccl.RemoteControlPlane(url2, code)
    rep = fb.execute_flow_b(b, rcp2, env.store, w.owner1, plan, plan["plan_hash"], code, env.inst, batch_size=1, chunk_size=3)
    assert_completed(env, w, b, plan, rep)
    assert db(env, "SELECT COUNT(*) FROM bindings")[0][0] == 1 and db(env, "SELECT COUNT(*) FROM bootstrap_runs")[0][0] == 1 and db(env, "SELECT COUNT(*) FROM devices")[0][0] == 1


# ================================================================== CLIENT (MAIN) OMORÂT ÎN TIMPUL BATCH-ULUI; TIMEOUT; PENDING ABANDONAT
def http_kw(env, w, plan, f, url, **extra):
    return dict(plan_file=f, actor=w.owner1, bootstrap_id=plan["bootstrap_id"], cloud_url=url, **extra)


def http_plan(env, w, b, code, url):
    rcp = ccl.RemoteControlPlane(url, code)
    plan = fb.plan_flow_b(b, rcp, w.owner1, env.inst)
    f = env.tmp / f"hplan-{uuid.uuid4().hex[:6]}.json"
    f.write_text(json.dumps({"plan": plan, "code": code, "batch_size": 1, "chunk_size": 3, "accept_missing": False}), encoding="utf-8")
    return plan, f


@pytest.mark.parametrize("crash", ["after:locuri_de_veci:1", "media:after_chunk:1", "verify:after"])
def test_client_killed_mid_transfer_then_resume_from_main(renv_logs, crash):
    env = renv_logs
    w, b, code = b_world(env)
    _p, url, _ = start_server(env)
    plan, f = http_plan(env, w, b, code, url)
    p = spawn(env, "v", b, action="flow_b", crash_at=crash, **http_kw(env, w, plan, f, url))
    wait_marker(env, "v", crash.replace(":", "_"))
    kill9(p)
    assert db(env, "SELECT status FROM bindings") == [("pending",)] and cem_state(env, b.cemetery_uid) == ("DRAFT", 1)  # binding pending, cloud încă nepublicat
    code2, r = finish(spawn(env, "r", b, action="flow_b", **http_kw(env, w, plan, f, url)))
    assert code2 == 0 and r["report"]["result"] == "completed", r
    assert_completed(env, w, b, plan, r["report"])
    assert db(env, "SELECT status FROM bindings") == [("active",)] and db(env, "SELECT COUNT(*) FROM bindings")[0][0] == 1


@pytest.mark.parametrize("crash", ["after_redeem", "snapshot:after_commit", "verify:after", "media:after_chunk:1"])
def test_abandoned_pending_binding_is_cleaned_by_rollback_before_publish(renv_logs, crash):
    env = renv_logs
    w, b, code = b_world(env)
    _p, url, _ = start_server(env)
    plan, f = http_plan(env, w, b, code, url)
    p = spawn(env, "v", b, action="flow_b", crash_at=crash, **http_kw(env, w, plan, f, url))
    wait_marker(env, "v", crash.replace(":", "_"))
    kill9(p)
    st = db(env, "SELECT status FROM bindings")
    assert st in ([("pending",)], []) and cem_state(env, b.cemetery_uid) == ("DRAFT", 1) and "active" not in str(st)
    code2, r = finish(spawn(env, "a", b, action="flow_b_abort", **http_kw(env, w, plan, f, url)))
    assert code2 == 0, r
    assert db(env, "SELECT status FROM bindings") in ([("revoked",)], []) and not db(env, "SELECT 1 FROM bindings WHERE status IN ('active','pending')")
    assert cem_state(env, b.cemetery_uid) == ("DRAFT", 1) and cloud_counts(env, b.cemetery_uid)["rows"] == 0 and len(outbox_rows(b)) == 3
    assert db(env, "SELECT state FROM bootstrap_runs") == [("rolled_back",)] and bs.LocalRegistry(b).get("cloud_secret_ref") == ""
    assert not snap.snapshot_binding(b, plan["bootstrap_id"]).root.exists()


def test_http_timeout_raises_transport_error_without_rollback_and_resume_works(renv_logs):
    env = renv_logs
    w, b, code = b_world(env)
    _p, url, _ = start_server(env, fault={"op": "staging_open", "nth": 1, "mode": "delay", "delay": 3.0})
    rcp = ccl.RemoteControlPlane(url, code, timeout=0.5, retries=0)
    plan = fb.plan_flow_b(b, rcp, w.owner1, env.inst)
    with pytest.raises(ccl.TransportError):
        fb.execute_flow_b(b, rcp, env.store, w.owner1, plan, plan["plan_hash"], code, env.inst, batch_size=2, chunk_size=3)
    assert bs.LocalRegistry(b).run_get(plan["bootstrap_id"])["state"] == "running" and db(env, "SELECT status FROM bindings") == [("pending",)]
    time.sleep(2.7)
    rep = fb.execute_flow_b(b, ccl.RemoteControlPlane(url, code), env.store, w.owner1, plan, plan["plan_hash"], code, env.inst, batch_size=2, chunk_size=3)
    assert_completed(env, w, b, plan, rep)


def test_post_publish_retry_creates_no_second_binding_or_publish(renv_logs):
    env = renv_logs
    w, b, code = b_world(env)
    _p, url, _ = start_server(env)
    plan, rep, rcp = run_b_http(env, w, b, code, url)
    key = env.store.get(bs.LocalRegistry(b).get("cloud_secret_ref"))
    before = db(env, "SELECT COUNT(*) FROM audit_log")[0][0]
    for _ in range(2):  # retrimiteri târzii: publish + redeem cu același cod
        assert rcp._call("publish_flow_b", [None, b.organization_uid, b.cemetery_uid, plan["bootstrap_id"], None, fb._expect(plan)])["already"] is True
    with pytest.raises((wire.Unauthenticated, cc.ControlError)):
        ccl.RemoteControlPlane(url, code).redeem_code(code, env.inst, "MAIN", b.cemetery_uid, bootstrap_id=str(uuid.uuid4()), pending=True)
    assert db(env, "SELECT COUNT(*) FROM bindings")[0][0] == 1 and db(env, "SELECT COUNT(*) FROM audit_log WHERE action='publish_flow_b'")[0][0] == 1
    assert env.store.get(bs.LocalRegistry(b).get("cloud_secret_ref")) == key and before >= 1


# ================================================================== AUTORIZARE / SCOPE / SCURGERI
def test_adversarial_scope_and_no_cross_tenant_or_secret_leakage(renv_logs):
    env = renv_logs
    w, b, code = b_world(env)
    other = w.local(w.org2, "Alt cimitir")
    env.cp.create_cemetery(w.owner2, other.organization_uid, other.cemetery_uid, "Altul")
    code2 = env.cp.issue_code(w.owner2, other.organization_uid, other.cemetery_uid)
    prov2 = env.cp.issue_code(w.owner2, other.organization_uid)
    _p, url, _ = start_server(env)
    leaks = []
    rcp1, rcp2 = ccl.RemoteControlPlane(url, code), ccl.RemoteControlPlane(url, code2)
    plan1, rep1, rcp1 = run_b_http(env, w, b, code, url, rcp=rcp1)
    key1 = rcp1.key
    bid1 = plan1["bootstrap_id"]
    # org B: cod valid pentru cimitirul B, bootstrap început pe B
    bid2 = str(uuid.uuid4())
    rcp2.begin_run(code2, env.inst, bid2, "B", other.cemetery_uid, "p" * 64)
    key2 = rcp2.redeem_code(code2, env.inst, "MAIN2", other.cemetery_uid, bootstrap_id=bid2, pending=True)["binding_key"]

    def post(op, args, auth):
        r = httpx.post(url + wire.API + wire.OPS[op][0], json={"args": args}, headers={"Authorization": auth})
        if r.status_code == 200 and op in ("export_snapshot", "get_run", "staging_status", "staging_snapshot"):
            leaks.append((op, r.text[:80]))
        return r

    bk1, bk2 = f"Binding {key1}", f"Binding {key2}"
    code = env.cp.issue_code(w.owner1, b.organization_uid, b.cemetery_uid)  # cod proaspăt (cel vechi e consumat și bootstrap-ul e încheiat)
    used = httpx.post(url + wire.API + wire.OPS["export_snapshot"][0], json={"args": [None, b.organization_uid, b.cemetery_uid]}, headers={"Authorization": f"Pairing {rcp1.pairing_code}"})
    assert used.status_code == 401 and used.json()["code"] == "UNAUTHENTICATED"  # cod consumat + bootstrap încheiat = mort
    for r in (post("export_snapshot", [None, other.organization_uid, other.cemetery_uid], f"Pairing {code}"),         # org A → cimitir B (cod)
              post("export_snapshot", [None, b.organization_uid, other.cemetery_uid], f"Pairing {code}"),
              post("get_run", [bid2], f"Pairing {code}"), post("get_run", [bid2], bk1),                               # bootstrap_id străin
              post("get_run", [bid1], bk2), post("staging_status", [None, other.organization_uid, other.cemetery_uid, bid2], bk1),   # secret binding A pentru B
              post("staging_snapshot", [None, other.organization_uid, other.cemetery_uid, bid2], bk1),
              post("staging_status", [None, b.organization_uid, b.cemetery_uid, bid2], bk1),                         # bootstrap străin pe cimitirul propriu
              post("staging_put_batch", [None, other.organization_uid, other.cemetery_uid, {"bootstrap_id": bid2}, []], bk1),  # batch străin
              post("resume_run", [None, bid2, "p" * 64], bk1), post("rollback_run", [None, bid2], bk1), post("mark_verifying", [None, bid2], bk1),
              post("mark_failed_unauthenticated", [bid2, env.inst, "x"], f"Pairing {code}")):
        assert r.status_code in (403, 404) and r.json()["code"] in ("SCOPE_MISMATCH", "FORBIDDEN"), r.text
    assert httpx.post(url + wire.API + wire.OPS["begin_run"][0], headers={"Authorization": f"Pairing {prov2}"},
                      json={"args": [None, env.inst, str(uuid.uuid4()), "B", b.cemetery_uid, "h" * 64]}).status_code in (403, 404)   # cod de provizionare străin pe cimitirul A
    with pytest.raises((cc.ScopeError, cc.PermissionDenied, cc.CodeError)):
        ccl.RemoteControlPlane(url, prov2).redeem_code(prov2, env.inst, "X", b.cemetery_uid, bootstrap_id=str(uuid.uuid4()))
    assert db(env, "SELECT status FROM bindings WHERE cemetery_uid=?", other.cemetery_uid) == [("pending",)]   # B neatins de A
    assert cem_state(env, other.cemetery_uid) == ("DRAFT", 1) and cloud_counts(env, other.cemetery_uid) == {"rows": 0, "media": 0, "blobs": 0}
    # emitent fără capabilitatea BOOTSTRAP => 403 la begin_run
    adm = env.cp.create_principal("reb")
    env.cp.grant_role(w.owner1, adm, w.org1, cc.REBUILD)
    c = sqlite3.connect(env.cp.path)
    c.execute("UPDATE pairing_codes SET issued_by=? WHERE code_hash=?", (adm, cc._h(prov2)))
    c.execute("UPDATE pairing_codes SET organization_uid=? WHERE code_hash=?", (w.org1, cc._h(prov2)))
    c.commit()
    c.close()
    r = httpx.post(url + wire.API + wire.OPS["begin_run"][0], headers={"Authorization": f"Pairing {prov2}"},
                   json={"args": [None, env.inst, str(uuid.uuid4()), "B", str(uuid.uuid4()), "h" * 64]})
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"
    assert leaks == []
    # scurgeri de secrete: nici în baza SQLite (plaintext), nici în logurile serverului/clienților, nici în registrul local, nici în răspunsuri de eroare
    secrets = [code, code2, prov2, key1, key2]
    blobs = [f.read_bytes() for f in Path(env.cp.path).parent.glob("control.sqlite*")] + [f.read_bytes() for f in (env.tmp / "logs").glob("*.log")]
    blobs += [f.read_bytes() for f in b.root.rglob("*") if f.is_file() and f.name != "bootstrap.lock"]
    found = [s for s in secrets for blob in blobs if s.encode() in blob]
    assert found == [], "secret în plaintext"
    for s in secrets:
        assert s not in json.dumps(db(env, "SELECT * FROM audit_log"))
