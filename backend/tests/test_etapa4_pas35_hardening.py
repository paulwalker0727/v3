"""Etapa 4 / Pasul 3.5 — hardening transfer/staging: identitate durabilă, batching determinist, kill real, staging STALE, permisiuni bootstrap vs rebuild.
NU este Flux B. Simulator local + foldere temporare; nu atinge Supabase/Render/producție/Banovița/014/Sync V2.
"""
from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import time
import uuid
from pathlib import Path

import pytest

import cimitir_cloud_control as cc
import cimitir_cloud_staging as cs
import cimitir_entities as ents
import cimitir_identity as ident
import cimitir_manifest as mf
import cimitir_preflight as pf
import cimitir_transfer as tr
import cimitir_transfer_contract as tc
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import finish, kill9, marker, renv, spawn, wait_marker  # noqa: F401
from test_etapa4_pas2_manifest import DATA, add_file, put_local, sql
from test_etapa4_pas3_transfer import Interrupted, b_plan, interrupt_after, q, start, stg


def live_dump(env):
    c = sqlite3.connect(env.cp.path)
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'staging_%' AND name NOT IN ('audit_log','sqlite_sequence') ORDER BY name")]
    out = {t_: sorted(c.execute(f"SELECT * FROM {t_}").fetchall(), key=repr) for t_ in tables}
    c.close()
    return out


def entity_rows(b):
    return {t_: sql(b, f"SELECT * FROM {t_} ORDER BY id") for t_ in ents.ORDER}


def completed_transfer(env, w, b, files=False, bid=None):
    plan = b_plan(env, w, b, files=files)
    t = start(env, w, b, plan, bid=bid, batch_size=2)
    assert t.run()["verified"]
    return plan, t


# ================================================================== 1. IDENTITATE DURABILĂ + PROMOVARE ATOMICĂ
def test_durable_identity_promotion_atomic_idempotent_then_release(env):
    w = World(env)
    b = w.local(w.org1)
    plan, t = completed_transfer(env, w, b)
    rows_before = entity_rows(b)
    with pytest.raises(tc.TransferBlocked):
        tr.release_local_state(b, t.bootstrap_id, bootstrap_completed=True)  # nepromovat => nu se șterge
    with pytest.raises(tc.TransferBlocked):
        tr.promote_transfer(b, t.bootstrap_id, bootstrap_completed=False)
    assert t.state.exists() and not ident.promotion_status(b, t.bootstrap_id)["promoted"]
    assert ident.existing_identities(b) == {}
    r = tr.promote_transfer(b, t.bootstrap_id, bootstrap_completed=True)
    assert r == {"promoted": True, "already": False, "row_count": 11, "added": 11}
    ids = ident.existing_identities(b)
    mapping = {k: v for k, v in t.state.mapping().items() if k[0] != "media"}
    assert ids == mapping and len(set(ids.values())) == 11
    assert q(b.database, "SELECT DISTINCT cemetery_uid, status, source_bootstrap_id FROM cloud_entity_identity") == [(b.cemetery_uid, "active", t.bootstrap_id)]
    assert tr.promote_transfer(b, t.bootstrap_id, True)["already"] is True and len(ident.existing_identities(b)) == 11
    tr.release_local_state(b, t.bootstrap_id, bootstrap_completed=True)
    assert not t.state.exists() and ident.existing_identities(b) == ids  # identitatea supraviețuiește ștergerii mapping-ului temporar
    assert tr.promote_transfer(b, t.bootstrap_id, True)["already"] is True  # fără stare temporară, martorul răspunde
    assert entity_rows(b) == rows_before  # entitățile locale neatinse (nicio coloană/PK schimbat)
    assert not sql(b, "SELECT 1 FROM locuri_de_veci WHERE uid IS NOT NULL")


def test_identity_continuity_second_bootstrap_reuses_cloud_uids(env):
    w = World(env)
    b = w.local(w.org1)
    plan, t1 = completed_transfer(env, w, b)
    tr.promote_transfer(b, t1.bootstrap_id, True)
    ids = ident.existing_identities(b)
    tr.abort_transfer(b, env.cp, w.owner1, t1.bootstrap_id)  # cloud rămâne DRAFT; un nou bootstrap pe aceleași date
    plan2 = pf.build_plan(b, env.cp, w.owner1, env.inst, clock=env.clock)
    t2 = start(env, w, b, plan2, batch_size=2)
    assert {k: v for k, v in t2.state.mapping().items() if k[0] != "media"} == ids  # aceeași entitate => același cloud_uid, nu derivare nouă
    assert t2.run()["verified"] and tr.promote_transfer(b, t2.bootstrap_id, True)["added"] == 0
    assert q(b.database, "SELECT COUNT(*) FROM cloud_entity_identity")[0][0] == 11


def test_identity_conflicts_roll_back_everything_and_uniqueness_is_enforced(env):
    w = World(env)
    b = w.local(w.org1)
    plan, t = completed_transfer(env, w, b)
    tr.promote_transfer(b, t.bootstrap_id, True)
    ids = ident.existing_identities(b)
    ref = ("locuri_de_veci", "1")
    other = str(uuid.uuid4())
    with pytest.raises(ident.IdentityConflict):  # aceeași entitate, alt cloud_uid activ
        ident.promote(b, str(uuid.uuid4()), {ref: other}, b.cemetery_uid)
    with pytest.raises(ident.IdentityConflict):  # primul rând bun, al doilea în conflict => nimic nu se inserează
        ident.promote(b, str(uuid.uuid4()), {("concesionari", "99"): str(uuid.uuid4()), ref: other}, b.cemetery_uid)
    with pytest.raises(ident.IdentityConflict):  # același cloud_uid pe altă entitate (de același tip)
        ident.promote(b, str(uuid.uuid4()), {("locuri_de_veci", "77"): ids[ref]}, b.cemetery_uid)
    with pytest.raises(ident.IdentityConflict):  # mapping din alt cimitir
        ident.promote(b, str(uuid.uuid4()), {("plati", "5"): str(uuid.uuid4())}, str(uuid.uuid4()))
    assert ident.existing_identities(b) == ids and q(b.database, "SELECT COUNT(*) FROM cloud_identity_promotions")[0][0] == 1
    c = sqlite3.connect(b.database)
    with pytest.raises(sqlite3.IntegrityError):  # indecșii unici parțiali
        c.execute("INSERT INTO cloud_entity_identity VALUES (?,?,?,?,?,?,?)", (b.cemetery_uid, "locuri_de_veci", "1", other, "active", "x", "now"))
    c.execute("UPDATE cloud_entity_identity SET status='retired' WHERE entity_type='locuri_de_veci' AND local_ref='1'")
    c.execute("INSERT INTO cloud_entity_identity VALUES (?,?,?,?,?,?,?)", (b.cemetery_uid, "locuri_de_veci", "1", other, "active", "x", "now"))  # după retragere: permis
    c.commit()
    c.close()


def test_identity_travels_with_registry_backup(env):
    from cimitir_backup import backup
    w = World(env)
    b = w.local(w.org1)
    plan, t = completed_transfer(env, w, b)
    tr.promote_transfer(b, t.bootstrap_id, True)
    bk = backup(b.root, env.tmp / "bk")
    dbs = [f for f in Path(bk).rglob("*") if f.is_file() and f.suffix in (".db", ".sqlite")]
    assert any("cloud_entity_identity" in {r[0] for r in sqlite3.connect(f).execute("SELECT name FROM sqlite_master")} for f in dbs)


# ================================================================== 2. BATCHING DETERMINIST
def batch_view(t, snap):
    return [(e, n, c_, tc.batch_sha256(r), [x["uid"] for x in r]) for e, n, c_, r in t._batches(snap)]


def shuffled(snap, seed):
    rnd = random.Random(seed)
    out = {**snap, "entities": {k: rnd.sample(v, len(v)) for k, v in snap["entities"].items()}, "media": rnd.sample(snap["media"], len(snap["media"]))}
    return out


@pytest.mark.parametrize("seed", range(5))
def test_partitioning_is_independent_of_physical_row_order(env, seed):
    w = World(env)
    b = w.local(w.org1)
    plan = b_plan(env, w, b, files=True)
    t = start(env, w, b, plan, batch_size=2, chunk_size=3)
    snap = mf.local_snapshot(b)
    base = batch_view(t, snap)
    assert base == batch_view(t, shuffled(snap, seed))  # același entity_type + batch_no + set de rânduri + batch_sha256
    assert {e: [x[3] for x in base if x[0] == e] for e in {x[0] for x in base}} == t.meta["batch_digests"]
    rep = t.run()  # rulat efectiv și cu SHA-urile din staging identice cu cele planificate
    assert rep["verified"] and [r[0] for r in q(env.cp.path, "SELECT batch_sha256 FROM staging_batches WHERE bootstrap_id=? ORDER BY entity_type, batch_no", t.bootstrap_id)] == \
        [x[3] for x in sorted(base, key=lambda y: (y[0], y[1]))]


def test_partitioning_same_across_restart_and_across_registries_with_different_ids(env):
    w = World(env)
    a, b2 = w.local(w.org1, "A"), w.local(w.org1, "B")
    plan_a = b_plan(env, w, a, files=True)
    ta = start(env, w, a, plan_a, batch_size=2, chunk_size=3)
    ta.state.delete()  # „restart”: stare locală pierdută, aceeași sursă + același plan
    ta2 = start(env, w, a, plan_a, bid=ta.bootstrap_id, batch_size=2, chunk_size=3)
    assert ta2.meta["batch_digests"] == ta.meta["batch_digests"]
    put_local(b2, DATA, reverse=True)  # alte id-uri locale, altă ordine fizică
    add_file(b2, "a.jpg", b"AAAA")
    add_file(b2, "b.jpg", b"AAAA")
    add_file(b2, "c.bin", b"CCCCCCC")
    sql(b2, "INSERT INTO documents (entity_type, entity_id, doc_type, file_path) VALUES ('loc','A-1','poza','lipsa.jpg')")
    env.cp.create_cemetery(w.owner1, b2.organization_uid, b2.cemetery_uid, "C")

    def composition(binding):
        canon = tr.canonicalize(mf.local_snapshot(binding))
        hashes: dict = {}
        out = {}
        for e in ents.ORDER:
            mf.entity_summary(e, canon["entities"][e], hashes)
            seq = [hashes[e][r["ref"]] for r in canon["entities"][e]]
            out[e] = [seq[i:i + 2] for i in range(0, len(seq), 2)]
        return out
    assert composition(a) == composition(b2) and a.cemetery_uid != b2.cemetery_uid
    assert [m["name"] for m in tr.canonicalize(mf.local_snapshot(a))["media"]] == [m["name"] for m in tr.canonicalize(mf.local_snapshot(b2))["media"]]


# ================================================================== 3. KILL REAL ÎN TIMPUL TRANSFERULUI
def real_plan(env, w, b):
    put_local(b, DATA)
    add_file(b, "a.jpg", b"AAAA")
    add_file(b, "b.jpg", b"AAAA")
    add_file(b, "c.bin", b"CCCCCCC")
    sql(b, "INSERT INTO documents (entity_type, entity_id, doc_type, file_path) VALUES ('loc','A-1','poza','lipsa.jpg')")
    env.cp.create_cemetery(w.owner1, b.organization_uid, b.cemetery_uid, "Cimitir")
    plan = pf.build_plan(b, env.cp, w.owner1, env.inst)  # ceas real: procesul copil validează planul cu time.time
    assert plan["decision"] == "B"
    f = env.tmp / "tplan.json"
    f.write_text(json.dumps({"plan": plan, "batch_size": 1, "chunk_size": 3}), encoding="utf-8")
    return plan, f


ORDER_15 = [(e, n) for e, c_ in (("locuri_de_veci", 3), ("concesionari", 2), ("contracte", 2), ("inhumati", 2), ("plati", 1), ("amenajari_avize", 1), ("media", 4))
            for n in range(1, c_ + 1)]


@pytest.mark.parametrize("crash,committed", [("before:locuri_de_veci:1", 0), ("after:locuri_de_veci:1", 1), ("after:locuri_de_veci:2", 2),
                                             ("after:media:2", 13), ("before_verify", 15)])
def test_real_kill9_during_transfer_then_resume(renv, crash, committed):
    env = renv
    w = World(env)
    b = w.local(w.org1)
    plan, f = real_plan(env, w, b)
    bid = str(uuid.uuid4())
    live_before = live_dump(env)
    rows_before = entity_rows(b)
    kw = dict(plan_file=f, actor=w.owner1, bootstrap_id=bid)
    victim = spawn(env, "v", b, action="transfer", crash_at=crash, **kw)
    wait_marker(env, "v", crash.replace(":", "_"))
    kill9(victim)
    st = tr.StateStore(b, bid)
    m1 = st.mapping()
    assert len(m1) == 15 and st.exists()
    got = stg(env, bid)
    assert got["batches"] == committed and len({r[0] for r in q(env.cp.path, "SELECT uid FROM staging_rows WHERE bootstrap_id=?", bid)}) == got["rows"]  # fără dubluri
    assert live_dump(env) == live_before and q(env.cp.path, "SELECT status, data_epoch FROM cemeteries") == [("DRAFT", 1)]
    code, r = finish(spawn(env, "r", b, action="resume_transfer", **kw))
    assert code == 0 and r["report"]["verified"] is True, r
    assert r["report"]["skipped_already_staged"] == committed and r["report"]["stored"] == 15 - committed
    assert stg(env, bid) == {"rows": 15, "batches": 15} and st.mapping() == m1  # mapping identic după restart
    again = finish(spawn(env, "r2", b, action="resume_transfer", **kw))[1]
    assert again["report"]["stored"] == 0 and stg(env, bid) == {"rows": 15, "batches": 15}  # niciun batch dublat
    assert live_dump(env) == live_before and entity_rows(b) == rows_before and not sql(b, "SELECT 1 FROM sqlite_master WHERE name='cloud_entity_identity'")
    assert q(env.cp.path, "SELECT COUNT(*) FROM bindings")[0][0] == 0 and not (b.root / "bootstrap.lock").exists()


@pytest.mark.parametrize("crash", ["in_txn", "after_commit"])
def test_real_kill9_during_identity_promotion(renv, crash):
    env = renv
    w = World(env)
    b = w.local(w.org1)
    plan, f = real_plan(env, w, b)
    bid = str(uuid.uuid4())
    kw = dict(plan_file=f, actor=w.owner1, bootstrap_id=bid)
    assert finish(spawn(env, "t", b, action="transfer", **kw))[0] == 0
    victim = spawn(env, "p", b, action="promote", crash_at=crash, **kw)
    wait_marker(env, "p", crash)
    kill9(victim)
    st = tr.StateStore(b, bid)
    assert st.exists()  # mapping-ul temporar nu a fost șters
    if crash == "in_txn":  # tranzacție neterminată => nimic parțial
        assert not sql(b, "SELECT 1 FROM sqlite_master WHERE name='cloud_identity_promotions'") or q(b.database, "SELECT COUNT(*) FROM cloud_identity_promotions")[0][0] == 0
        assert not ident.existing_identities(b) and not ident.promotion_status(b, bid)["promoted"]
    else:
        assert ident.promotion_status(b, bid)["promoted"] and len(ident.existing_identities(b)) == 11
    code, r = finish(spawn(env, "p2", b, action="promote", **kw))
    assert code == 0 and r["result"]["promoted"] is True and r["result"]["already"] is (crash == "after_commit")
    assert len(ident.existing_identities(b)) == 11 and q(b.database, "SELECT COUNT(*) FROM cloud_identity_promotions")[0][0] == 1
    tr.release_local_state(b, bid, True)
    assert not st.exists() and len(ident.existing_identities(b)) == 11


# ================================================================== 4. STAGING STALE (metadate, fără ștergere automată)
def test_stale_staging_metadata_marking_reopen_and_explicit_cleanup(env):
    w = World(env)
    b = w.local(w.org1)
    plan = b_plan(env, w, b)
    t = start(env, w, b, plan, batch_size=2)
    with pytest.raises(Interrupted):
        t.run(after_batch=interrupt_after(2))
    opened = env.clock.t
    meta = t.status()["meta"]
    assert meta["status"] == "open" and meta["created_at"] <= meta["last_activity_at"] == opened and meta["eligible_for_cleanup"] is False
    assert meta["eligible_for_cleanup_at"] == opened + cs.STALE_AFTER and cs.staging_mark_stale(env.cp, w.owner1, w.org1, b.cemetery_uid) == []
    env.clock.t += cs.STALE_AFTER + 1
    assert t.status()["meta"]["eligible_for_cleanup"] is True and t.status()["meta"]["status"] == "open"  # eligibil, dar încă nemarcat
    assert cs.staging_mark_stale(env.cp, w.owner1, w.org1, b.cemetery_uid) == [t.bootstrap_id]
    before = stg(env, t.bootstrap_id)
    assert t.status()["meta"]["status"] == "stale" and before["batches"] == 2  # marcat STALE, NU șters
    snap = mf.local_snapshot(b)
    ent, no, count, rows = t._batches(snap)[2]
    with pytest.raises(tc.SessionMismatch):  # nu se mai primesc batch-uri până la redeschidere explicită
        cs.staging_put_batch(env.cp, w.owner1, w.org1, b.cemetery_uid, tc.make_contract(t.session, t.bootstrap_id, ent, no, count, rows), rows)
    env.clock.t += 5
    rep = tr.resume_transfer(b, env.cp, w.owner1, t.bootstrap_id, plan["plan_hash"]).run()  # resume redeschide (aceleași valori)
    assert rep["verified"] and t.status()["meta"]["status"] == "open" and t.status()["meta"]["last_activity_at"] > opened
    env.clock.t += cs.STALE_AFTER + 1
    cs.staging_mark_stale(env.cp, w.owner1, w.org1, b.cemetery_uid)
    plan2 = pf.build_plan(b, env.cp, w.owner1, env.inst, clock=env.clock)
    t_new = start(env, w, b, plan2, batch_size=2)
    t_new.open_staging()  # un staging STALE nu blochează un bootstrap nou…
    with pytest.raises(tc.SessionMismatch):
        t.open_staging()  # …dar nici nu se poate redeschide peste altul activ
    out = cs.staging_cleanup(env.cp, w.owner1, w.org1, b.cemetery_uid, t.bootstrap_id)  # cleanup explicit, strict pe bootstrap_id + cemetery_uid
    assert out["rows"] == 11 and stg(env, t.bootstrap_id) == {"rows": 0, "batches": 0}
    assert q(env.cp.path, "SELECT state FROM staging_sessions WHERE bootstrap_id=?", t_new.bootstrap_id) == [("open",)]


# ================================================================== 5. PERMISIUNI: BOOTSTRAP ≠ REBUILD
def can(env, actor, org, action):
    c = env.cp._c()
    try:
        env.cp._require(c, actor, action, org)
        return True
    except cc.PermissionDenied:
        return False
    finally:
        c.close()


def test_bootstrap_and_rebuild_permissions_are_distinct(env):
    w = World(env)
    b = w.local(w.org1)
    plan = b_plan(env, w, b)
    adm, reb, stranger = (env.cp.create_principal(n) for n in ("adm", "reb", "strain"))
    env.cp.grant_role(w.owner1, adm, w.org1, cc.ADMIN)
    env.cp.grant_role(w.owner1, reb, w.org1, cc.REBUILD)
    assert cc.PERMISSIONS["bootstrap"] == {cc.OWNER} and cc.PERMISSIONS["rebuild"] == {cc.OWNER, cc.REBUILD}
    assert can(env, w.owner1, w.org1, "bootstrap") and can(env, w.owner1, w.org1, "rebuild")  # Owner le are pe ambele
    assert can(env, reb, w.org1, "rebuild") and not can(env, reb, w.org1, "bootstrap")  # REBUILD ≠ BOOTSTRAP
    assert not can(env, adm, w.org1, "bootstrap") and not can(env, stranger, w.org1, "bootstrap") and not can(env, w.owner2, w.org1, "bootstrap")
    t = start(env, w, b, plan, batch_size=2)
    t.open_staging()
    snap = mf.local_snapshot(b)
    ent, no, count, rows = t._batches(snap)[0]
    contract = tc.make_contract(t.session, t.bootstrap_id, ent, no, count, rows)
    leaks = []
    for actor in (adm, reb, stranger, w.owner2):
        for call in (lambda a=actor: cs.staging_open(env.cp, a, w.org1, b.cemetery_uid, t.bootstrap_id, t.session, t.meta["entity_plan"]),
                     lambda a=actor: cs.staging_put_batch(env.cp, a, w.org1, b.cemetery_uid, contract, rows),
                     lambda a=actor: cs.staging_status(env.cp, a, w.org1, b.cemetery_uid, t.bootstrap_id),
                     lambda a=actor: cs.staging_snapshot(env.cp, a, w.org1, b.cemetery_uid, t.bootstrap_id),
                     lambda a=actor: cs.staging_mark_stale(env.cp, a, w.org1, b.cemetery_uid),
                     lambda a=actor: cs.staging_cleanup(env.cp, a, w.org1, b.cemetery_uid, t.bootstrap_id)):
            try:
                call()
                leaks.append(actor)
            except cc.PermissionDenied:
                pass
    assert leaks == [] and stg(env, t.bootstrap_id) == {"rows": 0, "batches": 0}  # CROSS-TENANT/ROLE LEAKAGE = 0
    assert t.run()["verified"]  # Owner continuă normal
