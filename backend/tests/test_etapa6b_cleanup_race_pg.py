# ruff: noqa: F811
"""6B / migration 016 — drepturi delete vs cleanup + cursa cleanup vs upload/finalizare, pe PostgreSQL LOCAL REAL (conexiuni/tranzacții separate, fire reale),
Storage = Supabase simulat. Rulează doar cu CIMITIR_TEST_PG_ADMIN_URL=postgresql://<superuser>@127.0.0.1:<port>/postgres (altfel SKIP). NU atinge Supabase."""
from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import psycopg
import pytest

import cimitir_bootstrap as bs
import cimitir_cloud_control as cc
import cimitir_cloud_control_pg as ccp
import cimitir_field_storage as fs
import cimitir_storage as st
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_field_storage import sup  # noqa: F401

ADMIN = os.environ.get("CIMITIR_TEST_PG_ADMIN_URL", "")
MIG = Path(__file__).resolve().parents[1] / "cimitir" / "pg_migrations"
WORKER = "cp6b_cleanup_worker"
pytestmark = pytest.mark.skipif(not ADMIN or urlparse(ADMIN).hostname not in ("127.0.0.1", "localhost", "::1"), reason="PostgreSQL local de test indisponibil")


def _dsn(db, user=None):
    u = urlparse(ADMIN)
    return u._replace(path="/" + db, netloc=f"{user or u.username}@{u.hostname}:{u.port}").geturl()


def pdf(n=300):
    return b"%PDF-1.4\n" + os.urandom(n) + b"\n%%EOF\n"


def sha(b):
    return hashlib.sha256(b).hexdigest()


@pytest.fixture(scope="module")
def pgdb():
    name = "cp6b_" + uuid.uuid4().hex[:8]
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
        if not c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (WORKER,)).fetchone():
            c.execute(f"CREATE ROLE {WORKER} LOGIN NOSUPERUSER NOBYPASSRLS")
    with psycopg.connect(_dsn(name), autocommit=True) as c:
        for f in sorted(MIG.glob("*.sql")):
            c.execute(f.read_text(encoding="utf-8"))
        c.execute(f"GRANT cimitir_storage_cleanup TO {WORKER}")
    yield {"name": name, "admin": _dsn(name), "app": _dsn(name, "cimitir_cp_app"), "worker": _dsn(name, WORKER)}
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        c.execute(f"DROP ROLE IF EXISTS {WORKER}")


def adm(pgdb):
    return psycopg.connect(pgdb["admin"], autocommit=True)


def app(pgdb, org=None, cem=None):
    c = psycopg.connect(pgdb["app"], autocommit=True)
    if org:
        c.execute("SELECT set_config('cp.org', %s, false), set_config('cp.cems', %s, false)", (org, cem))
    return c


def wrk(pgdb):
    return psycopg.connect(pgdb["worker"], autocommit=True)


@pytest.fixture()
def world(renv, pgdb, sup, monkeypatch):
    with adm(pgdb) as c:
        c.execute("TRUNCATE cp_storage_objects, cp_bootstrap_runs, cp_failed_redeems, cp_audit_log, cp_platform_admins, cp_org_roles, cp_binding_issuers, device_pairing_codes, "
                  "device_cemetery_bindings, devices, cemeteries, organizations, identities CASCADE")
    monkeypatch.setenv("CIMITIR_CP_DATABASE_URL", pgdb["app"])
    monkeypatch.setenv("CIMITIR_CP_ADMIN_DATABASE_URL", pgdb["admin"])
    monkeypatch.setenv("CIMITIR_ALLOW_TEST_SECRETSTORE", "1")
    renv.cp = ccp.PostgresControlPlane()
    w, T = World(renv), {}
    for tag, org, owner in (("A", w.org1, w.owner1), ("B", w.org2, w.owner2)):
        b = w.local(org, name=f"6B-016 {tag}")
        code = renv.cp.issue_code(owner, org)
        plan = bs.plan_flow_a(b, renv.cp, code, renv.inst, cemetery_name=f"6B-016 {tag}")
        bs.execute_flow_a(b, renv.cp, renv.store, plan, plan["plan_hash"], code, renv.inst)
        T[tag] = {"org": org, "cem": b.cemetery_uid, "key": renv.store.get(bs.LocalRegistry(b).get("cloud_secret_ref"))}
    store = fs.FieldMediaStore(renv.cp, sup, cleanup_url=pgdb["worker"])
    return {"T": T, "store": store, "sup": sup, "pg": pgdb}


def new(world, tag="A", data=None, put=False):
    """Rând declarat (PENDING); put=True scrie și obiectul în Storage (ca după o întrerupere)."""
    t, store = world["T"][tag], world["store"]
    data = data or pdf()
    uid = str(uuid.uuid4())
    store.begin_upload(t["key"], t["cem"], uid, "f.pdf", "application/pdf", len(data), sha(data))
    if put:
        world["sup"].put_bytes(fs.storage_path(t["org"], t["cem"], uid), data, "application/pdf")
    return uid, data


def age(world, tag, uid, secs=7200):
    with adm(world["pg"]) as c:
        c.execute("UPDATE cp_storage_objects SET updated_at = now() - make_interval(secs => %s) WHERE cemetery_uid=%s AND uid=%s", (secs, world["T"][tag]["cem"], uid))


def status(world, tag, uid):
    with adm(world["pg"]) as c:
        r = c.execute("SELECT status FROM cp_storage_objects WHERE cemetery_uid=%s AND uid=%s", (world["T"][tag]["cem"], uid)).fetchone()
        return r[0] if r else None


def force(world, tag, uid, st_):
    with adm(world["pg"]) as c:
        c.execute("UPDATE cp_storage_objects SET status=%s WHERE cemetery_uid=%s AND uid=%s", (st_, world["T"][tag]["cem"], uid))


def claim(conn, t, token=None, older=0, limit=100, lease=900):
    token = token or str(uuid.uuid4())
    rows = conn.execute("SELECT out_uid, out_storage_path, out_prev_status FROM cp_storage_cleanup_claim(%s,%s,%s,%s,%s,%s)", (t["org"], t["cem"], token, older, limit, lease)).fetchall()
    return token, rows


def in_store(world, tag):
    t = world["T"][tag]
    return [o["name"] for o in world["sup"].list_prefix(f"field/{t['org']}/{t['cem']}")]


# ===================================================================== 1. drepturi: delete (owner) vs cleanup (serviciu)
def test_privileges_cp_app_has_no_delete_and_cannot_call_cleanup(world):
    pg, t = world["pg"], world["T"]["A"]
    uid, _ = new(world)
    with app(pg, t["org"], t["cem"]) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("DELETE FROM cp_storage_objects WHERE uid=%s", (uid,))
        for fn, args in (("cp_storage_cleanup_claim", "(%s,%s,%s,0,10,900)"), ("cp_storage_cleanup_claim_object", "(%s,%s,%s,%s,0,99999)"),
                         ("cp_storage_cleanup_finish", "(%s,%s,%s,%s)"), ("cp_storage_cleanup_release", "(%s,%s,%s,%s)")):
            n = args.count("%s")
            vals = [t["org"], t["cem"], uid, str(uuid.uuid4())][:n] if "object" in fn or "finish" in fn or "release" in fn else [t["org"], t["cem"], str(uuid.uuid4())]
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(f"SELECT * FROM {fn}{args}", vals)
        with pytest.raises(psycopg.errors.CheckViolation):          # nici prin UPDATE direct nu poate revendica / forța CLEANING
            c.execute("UPDATE cp_storage_objects SET status='CLEANING', cleanup_token=%s, cleanup_prev_status='PENDING', cleanup_claimed_at=now() WHERE uid=%s", (str(uuid.uuid4()), uid))
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("INSERT INTO cp_storage_objects (cemetery_uid, uid, organization_uid, kind, filename, mime, size, sha256, storage_path, status, cleanup_token, cleanup_prev_status, cleanup_claimed_at) "
                      "VALUES (%s,%s,%s,'document','x','application/pdf',1,%s,%s,'CLEANING',%s,'DELETED',now())", (t["cem"], str(uuid.uuid4()), t["org"], "a" * 64, "field/q", str(uuid.uuid4())))
        with pytest.raises(psycopg.errors.CheckViolation):          # câmpurile de identitate sunt imutabile pentru aplicație
            c.execute("UPDATE cp_storage_objects SET storage_path='field/other' WHERE uid=%s", (uid,))
        assert c.execute("UPDATE cp_storage_objects SET status='DELETED' WHERE uid=%s", (uid,)).rowcount == 1   # delete-ul normal (RLS, UPDATE) rămâne
    with wrk(pg) as w:                                               # rolul de cleanup nu are drepturi directe pe tabel
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            w.execute("SELECT 1 FROM cp_storage_objects LIMIT 1")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            w.execute("DELETE FROM cp_storage_objects")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            w.execute("UPDATE cp_storage_objects SET status='READY'")
    with adm(pg) as c:
        oids = [r[0] for r in c.execute("SELECT p.oid FROM pg_proc p WHERE proname LIKE 'cp_storage_cleanup_%%' AND proname <> 'cp_storage_cleanup_scope'").fetchall()]
        assert len(oids) == 4
        for oid in oids:
            assert not c.execute("SELECT has_function_privilege('cimitir_cp_app', %s, 'EXECUTE')", (oid,)).fetchone()[0]
            assert c.execute("SELECT has_function_privilege('cimitir_storage_cleanup', %s, 'EXECUTE')", (oid,)).fetchone()[0]
            assert not c.execute("SELECT has_function_privilege('public', %s, 'EXECUTE')", (oid,)).fetchone()[0]
        assert c.execute("SELECT rolcanlogin, rolsuper, rolbypassrls FROM pg_roles WHERE rolname='cimitir_storage_cleanup'").fetchone() == (False, False, False)
        assert not c.execute("SELECT has_table_privilege('cimitir_cp_app','cp_storage_objects','DELETE')").fetchone()[0]
        for oid in oids:                                             # SECURITY DEFINER + search_path fixat; nicio funcție nu primește o cale de la apelant
            row = c.execute("SELECT prosecdef, proconfig, proargnames FROM pg_proc WHERE oid=%s", (oid,)).fetchone()
            assert row[0] and any(x.startswith("search_path=") for x in row[1]) and not any("path" in n for n in row[2] if n.startswith("p_"))


def test_cleanup_without_service_url_is_refused(world):
    t = world["T"]["A"]
    store = fs.FieldMediaStore(world["store"].cp, world["sup"], cleanup_url=None)
    store.cleanup_url = None
    with pytest.raises(fs.FieldStorageError):
        store.cleanup_orphans(t["key"], t["cem"], older_than_s=0)


def test_tenant_a_cannot_cleanup_or_delete_tenant_b(world):
    pg, A, B, store = world["pg"], world["T"]["A"], world["T"]["B"], world["store"]
    b_uid, _ = new(world, "B", put=True)
    age(world, "B", b_uid)
    with wrk(pg) as w:
        for fn, args in (("cp_storage_cleanup_claim", (A["org"], B["cem"], str(uuid.uuid4()), 0, 10, 900)),
                         ("cp_storage_cleanup_claim_object", (A["org"], B["cem"], b_uid, str(uuid.uuid4()), 0, 99999))):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):    # perechea (org, cimitir) nepotrivită
                w.execute(f"SELECT * FROM {fn}({','.join(['%s'] * len(args))})", args)
        _, rows = claim(w, A)                                              # cleanup legitim pe A nu vede rândul lui B
        assert rows == []
        assert w.execute("SELECT cp_storage_cleanup_claim_object(%s,%s,%s,%s,0,99999)", (A["org"], A["cem"], b_uid, str(uuid.uuid4()))).fetchone()[0].startswith(f"field/{A['org']}/{A['cem']}/")
    assert status(world, "B", b_uid) == "PENDING" and b_uid in in_store(world, "B")
    with pytest.raises(cc.ScopeError):
        store.cleanup_orphans(A["key"], B["cem"], older_than_s=0)
    with pytest.raises(cc.ScopeError):
        store.delete(A["key"], B["cem"], b_uid)
    with pytest.raises(fs.Conflict):
        store.delete(A["key"], A["cem"], b_uid)                            # același uid, alt cimitir: în A există doar santinela CLEANING (calea din A), nu obiectul lui B
    assert status(world, "B", b_uid) == "PENDING" and b_uid in in_store(world, "B")
    assert store.cleanup_orphans(B["key"], B["cem"], older_than_s=0) == {"stale_rows": 1, "orphan_objects": 0}
    assert b_uid not in in_store(world, "B") and status(world, "B", b_uid) == "DELETED"


# ===================================================================== 2. eligibilitate (STAGING doar; READY/IMPORTED niciodată; fără cale arbitrară)
def test_claim_takes_only_stale_staging_never_ready_or_imported(world):
    pg, t = world["pg"], world["T"]["A"]
    ids = {k: new(world)[0] for k in ("PENDING", "UPLOADED", "FAILED", "READY", "IMPORTED", "DELETED", "FRESH")}
    for k in ("UPLOADED", "FAILED", "READY", "DELETED"):
        force(world, "A", ids[k], k)
    force(world, "A", ids["IMPORTED"], "READY")
    force(world, "A", ids["IMPORTED"], "IMPORTED")
    for k, u in ids.items():
        if k != "FRESH":
            age(world, "A", u)
    with wrk(pg) as w:
        token, rows = claim(w, t, older=3600)
        assert {r[0] for r in rows} == {ids["PENDING"], ids["UPLOADED"], ids["FAILED"]}
        assert {r[2] for r in rows} == {"PENDING", "UPLOADED", "FAILED"}
        assert all(r[1] == fs.storage_path(t["org"], t["cem"], r[0]) for r in rows)          # calea o construiește DB
        assert [r[0] for r in claim(w, t, older=0)[1]] == [ids["FRESH"]]                      # READY/IMPORTED/DELETED/CLEANING nu apar niciodată
    for k in ("READY", "IMPORTED", "DELETED"):
        assert status(world, "A", ids[k]) == k
    assert status(world, "A", ids["FRESH"]) == "CLEANING"
    with wrk(pg) as w:                                                    # claim_object nu atinge READY/IMPORTED/PENDING/CLEANING
        for k in ("READY", "IMPORTED", "FAILED", "UPLOADED", "PENDING"):
            assert w.execute("SELECT cp_storage_cleanup_claim_object(%s,%s,%s,%s,0,99999)", (t["org"], t["cem"], ids[k], str(uuid.uuid4()))).fetchone()[0] is None
    assert status(world, "A", ids["READY"]) == "READY" and status(world, "A", ids["IMPORTED"]) == "IMPORTED"
    with adm(pg) as c:                                                    # CLEANING nu devine READY/IMPORTED; READY/IMPORTED nu devine CLEANING (nici pentru proprietar)
        for bad in (("CLEANING", "READY"), ("CLEANING", "IMPORTED")):
            u = ids["PENDING"]
            assert status(world, "A", u) == "CLEANING"
            with pytest.raises(psycopg.errors.CheckViolation):
                c.execute("UPDATE cp_storage_objects SET status=%s WHERE uid=%s", (bad[1], u))
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("UPDATE cp_storage_objects SET status='CLEANING', cleanup_token=%s, cleanup_prev_status='PENDING', cleanup_claimed_at=now() WHERE uid=%s", (str(uuid.uuid4()), ids["READY"]))


def test_imported_cannot_be_deleted_or_cleaned_by_store(world):
    t, store = world["T"]["A"], world["store"]
    data = pdf()
    uid, _ = new(world, data=data)
    store.upload(t["key"], t["cem"], uid, data)
    force(world, "A", uid, "IMPORTED")
    age(world, "A", uid)
    with pytest.raises(fs.Conflict):
        store.delete(t["key"], t["cem"], uid)
    assert store.cleanup_orphans(t["key"], t["cem"], older_than_s=0) == {"stale_rows": 0, "orphan_objects": 0}
    assert status(world, "A", uid) == "IMPORTED" and uid in in_store(world, "A")
    assert store.status(t["key"], t["cem"], uid)["status"] == "IMPORTED" and store.upload(t["key"], t["cem"], uid, data)["status"] == "IMPORTED"


def test_arbitrary_path_cannot_be_deleted(world):
    t, store, sup_ = world["T"]["A"], world["store"], world["sup"]
    other_prefix = "field/other-org/other-cem/" + str(uuid.uuid4())
    outside = "documents/" + str(uuid.uuid4())
    junk_in_prefix = f"field/{t['org']}/{t['cem']}/not-a-uuid.pdf"
    for k in (other_prefix, outside, junk_in_prefix):
        sup_.put_bytes(k, pdf(), "application/pdf")
    with wrk(world["pg"]) as w:
        for bad in ("../../etc/passwd", "../" + str(uuid.uuid4()), "not-a-uuid.pdf", "", "A" * 10):
            assert w.execute("SELECT cp_storage_cleanup_claim_object(%s,%s,%s,%s,0,99999)", (t["org"], t["cem"], bad, str(uuid.uuid4()))).fetchone()[0] is None
    rep = store.cleanup_orphans(t["key"], t["cem"], older_than_s=0)
    assert rep["orphan_objects"] == 0
    for k in (other_prefix, outside, junk_in_prefix):
        assert sup_.stat(k) is not None, k
    with adm(world["pg"]) as c:
        assert c.execute("SELECT COUNT(*) FROM cp_storage_objects WHERE storage_path = ANY(%s)", ([other_prefix, outside, junk_in_prefix],)).fetchone()[0] == 0


# ===================================================================== 3. claim atomic: token, lease, un singur worker
def test_second_worker_cannot_reclaim_and_finish_needs_exact_token(world):
    pg, t = world["pg"], world["T"]["A"]
    uid, _ = new(world, put=True)
    age(world, "A", uid)
    with wrk(pg) as w1, wrk(pg) as w2:
        tok1, r1 = claim(w1, t, older=3600)
        assert [r[0] for r in r1] == [uid] and status(world, "A", uid) == "CLEANING"
        tok2, r2 = claim(w2, t, older=0, lease=900)
        assert r2 == []                                                                 # revendicat deja: alt worker nu-l mai poate lua
        assert w2.execute("SELECT cp_storage_cleanup_claim_object(%s,%s,%s,%s,0,99999)", (t["org"], t["cem"], uid, tok2)).fetchone()[0] is None
        assert w2.execute("SELECT cp_storage_cleanup_finish(%s,%s,%s,%s)", (t["org"], t["cem"], uid, tok2)).fetchone()[0] is False      # nu e al lui
        assert w2.execute("SELECT cp_storage_cleanup_release(%s,%s,%s,%s)", (t["org"], t["cem"], uid, tok2)).fetchone()[0] is False
        assert status(world, "A", uid) == "CLEANING"
        time.sleep(0.05)
        tok3, r3 = claim(w2, t, older=3600, lease=0.01)                                 # lease expirat (worker căzut) -> reluare cu token nou
        assert [r[0] for r in r3] == [uid] and r3[0][2] == "PENDING"
        assert w1.execute("SELECT cp_storage_cleanup_finish(%s,%s,%s,%s)", (t["org"], t["cem"], uid, tok1)).fetchone()[0] is False     # tokenul vechi nu mai are efect
        assert w2.execute("SELECT cp_storage_cleanup_finish(%s,%s,%s,%s)", (t["org"], t["cem"], uid, tok3)).fetchone()[0] is True
    assert status(world, "A", uid) == "DELETED"
    with wrk(pg) as w:
        assert w.execute("SELECT cp_storage_cleanup_finish(%s,%s,%s,%s)", (t["org"], t["cem"], uid, tok3)).fetchone()[0] is False       # a doua oară: nimic


def test_service_deletes_only_what_it_claimed_and_release_on_storage_error(world, monkeypatch):
    t, store, sup_ = world["T"]["A"], world["store"], world["sup"]
    mine, _ = new(world, put=True)
    theirs, _ = new(world, put=True)
    age(world, "A", mine)                                                              # theirs rămâne proaspăt: neeligibil
    assert store.cleanup_orphans(t["key"], t["cem"], older_than_s=3600) == {"stale_rows": 1, "orphan_objects": 0}
    assert mine not in in_store(world, "A") and theirs in in_store(world, "A") and status(world, "A", theirs) == "PENDING"
    age(world, "A", theirs)
    real = sup_.delete
    monkeypatch.setattr(sup_, "delete", lambda k: (_ for _ in ()).throw(st.StorageError("simulat")))
    rep = store.cleanup_orphans(t["key"], t["cem"], older_than_s=3600)
    assert rep["stale_rows"] == 0 and rep["errors"] == 1 and status(world, "A", theirs) == "PENDING"      # eșec Storage -> rândul revine, nu rămâne CLEANING
    monkeypatch.setattr(sup_, "delete", real)
    assert store.cleanup_orphans(t["key"], t["cem"], older_than_s=3600) == {"stale_rows": 1, "orphan_objects": 0}
    assert theirs not in in_store(world, "A") and status(world, "A", theirs) == "DELETED"


def test_orphan_object_without_row_is_claimed_atomically_and_uid_is_burned(world):
    t, store, sup_ = world["T"]["A"], world["store"], world["sup"]
    o = str(uuid.uuid4())
    sup_.put_bytes(fs.storage_path(t["org"], t["cem"], o), pdf(), "application/pdf")
    data = pdf()
    rep = store.cleanup_orphans(t["key"], t["cem"], older_than_s=0)
    assert rep == {"stale_rows": 0, "orphan_objects": 1} and o not in in_store(world, "A") and status(world, "A", o) == "DELETED"
    with pytest.raises(fs.Conflict):                                                   # begin_upload după claim: uid-ul nu mai poate fi reutilizat peste un cleanup
        store.begin_upload(t["key"], t["cem"], o, "f.pdf", "application/pdf", len(data), sha(data))
    assert store.cleanup_orphans(t["key"], t["cem"], older_than_s=0) == {"stale_rows": 0, "orphan_objects": 0}


def test_orphan_claim_vs_begin_upload_exactly_one_wins(world):
    pg, t, store = world["pg"], world["T"]["A"], world["store"]
    for _ in range(12):
        o, data = str(uuid.uuid4()), pdf()
        bar, out = threading.Barrier(2), {}

        def do_claim():
            with wrk(pg) as w:
                bar.wait()
                out["claim"] = w.execute("SELECT cp_storage_cleanup_claim_object(%s,%s,%s,%s,0,99999)", (t["org"], t["cem"], o, str(uuid.uuid4()))).fetchone()[0]

        def do_begin():
            bar.wait()
            try:
                store.begin_upload(t["key"], t["cem"], o, "f.pdf", "application/pdf", len(data), sha(data))
                out["begin"] = "ok"
            except fs.Conflict:
                out["begin"] = "conflict"

        th = [threading.Thread(target=do_claim), threading.Thread(target=do_begin)]
        [x.start() for x in th]
        [x.join(30) for x in th]
        assert (out["claim"] is not None) != (out["begin"] == "ok"), out                 # exact unul câștigă
        assert status(world, "A", o) == ("CLEANING" if out["claim"] else "PENDING")


def test_two_workers_claim_disjoint_sets(world):
    pg, t = world["pg"], world["T"]["A"]
    ids = {new(world)[0] for _ in range(24)}
    with adm(pg) as c:
        c.execute("UPDATE cp_storage_objects SET updated_at = now() - interval '2 hours' WHERE cemetery_uid=%s", (t["cem"],))
    bar, got = threading.Barrier(3), {}

    def worker(n):
        with wrk(pg) as w:
            bar.wait()
            got[n] = {r[0] for r in claim(w, t, older=3600, limit=24)[1]}

    th = [threading.Thread(target=worker, args=(n,)) for n in range(3)]
    [x.start() for x in th]
    [x.join(60) for x in th]
    sets = list(got.values())
    assert set().union(*sets) == ids and sum(len(s) for s in sets) == len(ids)          # nicio suprapunere, nimic pierdut


# ===================================================================== 4. cursa STAGING -> READY vs STAGING -> CLEANING -> DELETED (conexiuni separate)
READY_CAS = "UPDATE cp_storage_objects SET status='READY', ready_at=now(), updated_at=now() WHERE cemetery_uid=%s AND uid=%s AND status IN ('PENDING','UPLOADED')"


def test_ready_committed_first_cleanup_cannot_claim(world):
    pg, t = world["pg"], world["T"]["A"]
    uid, _ = new(world)
    age(world, "A", uid)
    with app(pg, t["org"], t["cem"]) as up_, wrk(pg) as w:
        up_.execute("BEGIN")
        assert up_.execute(READY_CAS, (t["cem"], uid)).rowcount == 1                      # upload a trecut rândul în READY (necomis)
        _, rows = claim(w, t, older=0)                                                    # cleanup concurent: rândul e blocat -> SKIP LOCKED
        assert rows == []
        up_.execute("COMMIT")
        assert claim(w, t, older=0)[1] == []                                              # după commit: READY nu mai e revendicabil
    assert status(world, "A", uid) == "READY"


def test_cleanup_claim_committed_first_upload_cannot_finalize(world):
    pg, t = world["pg"], world["T"]["A"]
    uid, _ = new(world)
    age(world, "A", uid)
    with wrk(pg) as w, app(pg, t["org"], t["cem"]) as up_:
        _, rows = claim(w, t, older=0)
        assert [r[0] for r in rows] == [uid]
        assert up_.execute(READY_CAS, (t["cem"], uid)).rowcount == 0                      # CAS pierdut: CLEANING nu devine READY
        with pytest.raises(psycopg.errors.CheckViolation):                                 # nici forțat, prin trigger
            up_.execute("UPDATE cp_storage_objects SET status='READY' WHERE cemetery_uid=%s AND uid=%s", (t["cem"], uid))
    assert status(world, "A", uid) == "CLEANING"


def test_upload_blocked_on_row_lock_loses_after_cleanup_commit(world):
    pg, t = world["pg"], world["T"]["A"]
    uid, _ = new(world)
    age(world, "A", uid)
    res = {}
    with wrk(pg) as w:
        w.execute("BEGIN")
        _, rows = claim(w, t, older=0)                                                    # cleanup a revendicat, necomis
        assert [r[0] for r in rows] == [uid]

        def upload_side():
            with app(pg, t["org"], t["cem"]) as up_:
                res["rc"] = up_.execute(READY_CAS, (t["cem"], uid)).rowcount               # așteaptă lacătul rândului

        th = threading.Thread(target=upload_side)
        th.start()
        time.sleep(0.6)
        assert th.is_alive()                                                              # chiar blocat (nu a decis pe o citire veche)
        w.execute("COMMIT")
        th.join(30)
    assert res["rc"] == 0 and status(world, "A", uid) == "CLEANING"


def test_cleanup_blocked_behind_upload_loses_and_ready_survives(world):
    pg, t = world["pg"], world["T"]["A"]
    uid, _ = new(world)
    age(world, "A", uid)
    res = {}
    with app(pg, t["org"], t["cem"]) as up_:
        up_.execute("BEGIN")
        assert up_.execute(READY_CAS, (t["cem"], uid)).rowcount == 1

        def cleanup_side():
            with wrk(pg) as w:
                res["rows"] = claim(w, t, older=0)[1]

        th = threading.Thread(target=cleanup_side)
        th.start()
        th.join(30)                                                                        # SKIP LOCKED: nu se blochează, nu ia rândul
        assert not th.is_alive() and res["rows"] == []
        up_.execute("COMMIT")
    assert status(world, "A", uid) == "READY"


def test_race_stress_exactly_one_side_wins_every_round(world):
    pg, t = world["pg"], world["T"]["A"]
    wins = {"upload": 0, "cleanup": 0}
    for _ in range(40):
        uid, _ = new(world)
        age(world, "A", uid)
        bar, out = threading.Barrier(2), {}

        def up_side():
            with app(pg, t["org"], t["cem"]) as c:
                bar.wait()
                out["up"] = c.execute(READY_CAS, (t["cem"], uid)).rowcount

        def cl_side():
            with wrk(pg) as w:
                bar.wait()
                out["cl"] = len(claim(w, t, older=0)[1])

        th = [threading.Thread(target=up_side), threading.Thread(target=cl_side)]
        [x.start() for x in th]
        [x.join(30) for x in th]
        assert out["up"] + out["cl"] <= 1, out                                            # niciodată ambele
        final = status(world, "A", uid)
        assert final == ("READY" if out["up"] else "CLEANING" if out["cl"] else "PENDING")
        wins["upload" if out["up"] else "cleanup" if out["cl"] else "skip"] = wins.get("upload" if out["up"] else "cleanup" if out["cl"] else "skip", 0) + 1
    assert wins["upload"] + wins["cleanup"] >= 30                                         # cursa a fost reală, nu degenerată


# ===================================================================== 5. același lucru prin FieldMediaStore (cu Storage simulat)
class _PausingStorage:
    """Delegă către Storage-ul simulat; put_bytes se oprește între 'lease' și scriere ca să putem rula cleanup-ul exact în fereastra cursei."""

    def __init__(self, inner):
        self.inner, self.reached, self.go = inner, threading.Event(), threading.Event()

    def put_bytes(self, key, data, mime=None, upsert=False):
        self.reached.set()
        assert self.go.wait(30)
        return self.inner.put_bytes(key, data, mime, upsert=upsert)

    def __getattr__(self, n):
        return getattr(self.inner, n)


def test_store_upload_loses_to_cleanup_claim_and_leftover_is_swept(world):
    t = world["T"]["A"]
    pausing = _PausingStorage(world["sup"])
    up_store = fs.FieldMediaStore(world["store"].cp, pausing, cleanup_url=world["pg"]["worker"])
    uid, data = new(world)
    out = {}

    def do_upload():
        try:
            out["r"] = up_store.upload(t["key"], t["cem"], uid, data)
        except fs.FieldStorageError as e:
            out["e"] = e

    th = threading.Thread(target=do_upload)
    th.start()
    assert pausing.reached.wait(30)
    time.sleep(0.05)
    assert world["store"].cleanup_orphans(t["key"], t["cem"], older_than_s=0) == {"stale_rows": 1, "orphan_objects": 0}     # cleanup câștigă în fereastra de scriere
    pausing.go.set()
    th.join(30)
    assert "r" not in out and isinstance(out["e"], fs.Conflict)                                                          # upload-ul NU raportează READY
    assert status(world, "A", uid) == "DELETED"
    with pytest.raises(fs.NotReady):
        world["store"].download_url(t["key"], t["cem"], uid)
    assert uid in in_store(world, "A")                                                                                    # rămășiță scrisă după ștergere...
    assert world["store"].cleanup_orphans(t["key"], t["cem"], older_than_s=0) == {"stale_rows": 0, "orphan_objects": 1}   # ...curățată de al doilea cleanup
    assert uid not in in_store(world, "A") and status(world, "A", uid) == "DELETED"


def test_store_upload_wins_and_cleanup_never_touches_ready(world):
    t, store = world["T"]["A"], world["store"]
    uid, data = new(world)
    assert store.upload(t["key"], t["cem"], uid, data)["status"] == "READY"
    age(world, "A", uid)
    assert store.cleanup_orphans(t["key"], t["cem"], older_than_s=0) == {"stale_rows": 0, "orphan_objects": 0}
    assert status(world, "A", uid) == "READY" and uid in in_store(world, "A")
    assert store.upload(t["key"], t["cem"], uid, data)["status"] == "READY"                                              # repetare idempotentă
    assert store.download_url(t["key"], t["cem"], uid)["sha256"] == sha(data)


def test_store_upload_and_delete_refuse_cleaning_rows(world):
    pg, t, store, sup_ = world["pg"], world["T"]["A"], world["store"], world["sup"]
    uid, data = new(world)
    age(world, "A", uid)
    with wrk(pg) as w:
        assert [r[0] for r in claim(w, t, older=0)[1]] == [uid]
    with pytest.raises(fs.Conflict):
        store.upload(t["key"], t["cem"], uid, data)
    assert sup_.stat(fs.storage_path(t["org"], t["cem"], uid)) is None                                                   # nimic scris în Storage
    with pytest.raises(fs.Conflict):
        store.delete(t["key"], t["cem"], uid)
    with pytest.raises(fs.Conflict):
        store.begin_upload(t["key"], t["cem"], uid, "f.pdf", "application/pdf", len(data), sha(data))
    assert status(world, "A", uid) == "CLEANING"


def test_store_threaded_upload_vs_cleanup_end_state_is_consistent(world):
    t = world["T"]["A"]
    for _ in range(8):
        uid, data = new(world)
        age(world, "A", uid)
        bar, out = threading.Barrier(2), {}

        def up_side():
            bar.wait()
            try:
                out["up"] = world["store"].upload(t["key"], t["cem"], uid, data)["status"]
            except fs.Conflict:
                out["up"] = "conflict"

        def cl_side():
            bar.wait()
            out["cl"] = world["store"].cleanup_orphans(t["key"], t["cem"], older_than_s=0)

        th = [threading.Thread(target=up_side), threading.Thread(target=cl_side)]
        [x.start() for x in th]
        [x.join(60) for x in th]
        world["store"].cleanup_orphans(t["key"], t["cem"], older_than_s=0)        # convergență: un al doilea pas curăță rămășițele
        final, present = status(world, "A", uid), uid in in_store(world, "A")
        if final == "READY":                                                          # upload a câștigat: obiectul trebuie să existe și să fie cel declarat
            assert out["up"] == "READY" and present and world["sup"].stat(fs.storage_path(t["org"], t["cem"], uid))["size"] == len(data)
        else:                                                                          # cleanup a câștigat: upload nu a raportat READY, nu rămâne obiect
            assert final == "DELETED" and out["up"] == "conflict" and not present, (final, out, present)


def test_delete_owner_flow_and_leftover_retry(world, monkeypatch):
    t, store, sup_ = world["T"]["A"], world["store"], world["sup"]
    uid, data = new(world)
    store.upload(t["key"], t["cem"], uid, data)
    real = sup_.delete
    monkeypatch.setattr(sup_, "delete", lambda k: (_ for _ in ()).throw(st.StorageError("simulat")))
    with pytest.raises(st.StorageError):
        store.delete(t["key"], t["cem"], uid)
    assert status(world, "A", uid) == "DELETED"                                       # marcat în DB întâi: nu mai poate fi descărcat / finalizat
    with pytest.raises(fs.NotReady):
        store.download_url(t["key"], t["cem"], uid)
    monkeypatch.setattr(sup_, "delete", real)
    assert store.delete(t["key"], t["cem"], uid)["status"] == "DELETED" and uid not in in_store(world, "A")        # reluarea șterge rămășița


def test_migration_016_is_additive_and_015_untouched():
    import subprocess
    root = Path(__file__).resolve().parents[2]
    if subprocess.run(["git", "cat-file", "-e", "257230c"], cwd=root, capture_output=True).returncode != 0:
        pytest.skip("commitul 257230c nu e disponibil")
    out = subprocess.run(["git", "diff", "--quiet", "257230c", "--", "backend/cimitir/pg_migrations/015_field_storage.sql"], cwd=root)
    assert out.returncode == 0, "015_field_storage.sql a fost modificată"
