"""Etapa 4 / Pasul 3 — contract de transfer, staging, UID mapping, resume, media simulat (MAIN → Cloud). Doar simulator local + foldere temporare.
NU este Flux B (nu activează, nu face swap, nu crește epoca); NU atinge Supabase/Render/producție/Banovița; fără endpoint-uri HTTP.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path

import pytest

import cimitir_cloud_control as cc
import cimitir_cloud_staging as cs
import cimitir_entities as ents
import cimitir_manifest as mf
import cimitir_preflight as pf
import cimitir_transfer as tr
import cimitir_transfer_contract as tc
from test_etapa4_pas1 import World, env  # noqa: F401  (fixture `env`)
from test_etapa4_pas2_manifest import DATA, add_file, put_local, sql, tree_state

LEAKS: list = []


class Interrupted(Exception):
    pass


def q(path, query, *args):
    c = sqlite3.connect(path)
    try:
        return c.execute(query, args).fetchall()
    finally:
        c.close()


def b_plan(env, w, b, files=False):
    put_local(b, DATA)
    if files:
        add_file(b, "a.jpg", b"AAAA")
        add_file(b, "b.jpg", b"AAAA")  # duplicat după hash
        add_file(b, "c.bin", b"CCCCCCC")
        sql(b, "INSERT INTO documents (entity_type, entity_id, doc_type, file_path) VALUES ('loc','A-1','poza','lipsa.jpg')")  # lipsă pe disc
    env.cp.create_cemetery(w.owner1, b.organization_uid, b.cemetery_uid, "Cimitir")
    plan = pf.build_plan(b, env.cp, w.owner1, env.inst, clock=env.clock)
    assert plan["decision"] == "B", plan["reason"]
    return plan


def start(env, w, b, plan, bid=None, **kw):
    return tr.start_transfer(b, env.cp, w.owner1, plan, plan["plan_hash"], bid or str(uuid.uuid4()), clock=env.clock, **kw)


def total_batches(t):
    return sum(t.meta["entity_plan"].values())


def stg(env, bid):
    return {"rows": q(env.cp.path, "SELECT COUNT(*) FROM staging_rows WHERE bootstrap_id=?", bid)[0][0],
            "batches": q(env.cp.path, "SELECT COUNT(*) FROM staging_batches WHERE bootstrap_id=?", bid)[0][0]}


def interrupt_after(n):
    seen = []

    def hook(entity, no):
        seen.append((entity, no))
        if len(seen) == n:
            raise Interrupted()
    return hook


# ================================================================== 1. CONTRACT
def test_contract_fields_and_shape_validation(env):
    w = World(env)
    b = w.local(w.org1)
    plan = b_plan(env, w, b)
    t = start(env, w, b, plan, batch_size=2)
    t.open_staging()
    snap = mf.local_snapshot(b)
    ent, no, count, rows = t._batches(snap)[0]
    c = tc.make_contract(t.session, t.bootstrap_id, ent, no, count, rows)
    assert set(tc.FIELDS) <= set(c) and c["batch_sha256"] == tc.batch_sha256(rows) and c["source_manifest_digest"] == plan["local_manifest"]["content_digest"]
    assert c["plan_hash"] == plan["plan_hash"] and c["data_epoch"] == 1 and c["schema_version"] == "local-v1" and c["organization_uid"] == b.organization_uid
    tc.check_shape(c, rows)
    assert tc.batch_sha256(list(reversed(rows))) == c["batch_sha256"]  # independent de ordinea rândurilor din batch
    for field in tc.FIELDS:
        bad = {k: v for k, v in c.items() if k != field}
        with pytest.raises(tc.ContractError):
            tc.check_shape(bad, rows)
    for patch in ({"batch_no": 0}, {"batch_no": count + 1}, {"entity_type": "nu_exista"}, {"contract_version": 99}):
        with pytest.raises(tc.ContractError):
            tc.check_shape({**c, **patch}, rows)
    with pytest.raises(tc.CorruptBatch):
        tc.check_shape({**c, "batch_sha256": "0" * 64}, rows)
    with pytest.raises(tc.CorruptBatch):
        tc.check_shape(c, rows[:-1])
    with pytest.raises(tc.ContractError):
        tc.check_shape({**c, "row_count": 2}, [rows[0], rows[0]])


# ================================================================== 2. IDEMPOTENȚĂ
def test_batch_idempotency_resend_and_rerun_do_not_duplicate(env):
    w = World(env)
    b = w.local(w.org1)
    plan = b_plan(env, w, b)
    t = start(env, w, b, plan, batch_size=2)
    rep = t.run()
    assert rep["stored"] == total_batches(t) and rep["verified"] is True
    before = stg(env, t.bootstrap_id)
    assert before["rows"] == 11 and before["batches"] == total_batches(t)
    snap = mf.local_snapshot(b)
    for ent, no, _c, _r in t._batches(snap):
        assert t.send(ent, no, snap)["status"] == "duplicate"
    rep2 = t.run()
    assert rep2["stored"] == 0 and rep2["skipped_already_staged"] == total_batches(t) and stg(env, t.bootstrap_id) == before
    t2 = start(env, w, b, plan, bid=t.bootstrap_id)  # re-pornire cu același plan = același transfer
    assert t2.run()["stored"] == 0 and stg(env, t.bootstrap_id) == before


def test_conflicting_batch_hash_and_corrupted_batch_are_blocked(env):
    w = World(env)
    b = w.local(w.org1)
    plan = b_plan(env, w, b)
    t = start(env, w, b, plan, batch_size=2)
    t.run()
    before = stg(env, t.bootstrap_id)
    snap = mf.local_snapshot(b)
    ent, no, count, rows = t._batches(snap)[0]
    altered = copy.deepcopy(rows)
    altered[0]["data"]["sector"] = "FALS"
    forged = tc.make_contract(t.session, t.bootstrap_id, ent, no, count, altered)  # SHA consistent cu conținutul alterat, dar diferit de cel existent
    with pytest.raises(tc.ConflictingBatch):
        cs.staging_put_batch(env.cp, w.owner1, w.org1, b.cemetery_uid, forged, altered)
    genuine = tc.make_contract(t.session, t.bootstrap_id, ent, no, count, rows)
    with pytest.raises(tc.CorruptBatch):  # conținut modificat în tranzit (SHA din contract nu mai corespunde)
        cs.staging_put_batch(env.cp, w.owner1, w.org1, b.cemetery_uid, genuine, altered)
    assert stg(env, t.bootstrap_id) == before
    assert q(env.cp.path, "SELECT content FROM staging_rows WHERE bootstrap_id=? AND uid=?", t.bootstrap_id, rows[0]["uid"])[0][0].count("FALS") == 0


def test_corrupted_batch_blocks_then_valid_resend_completes(env):
    w = World(env)
    b = w.local(w.org1)
    plan = b_plan(env, w, b)
    t = start(env, w, b, plan, batch_size=3)
    t.open_staging()
    snap = mf.local_snapshot(b)
    ent, no, count, rows = t._batches(snap)[0]
    tampered = copy.deepcopy(rows)
    tampered[0]["data"]["cod_loc"] = "XX"
    with pytest.raises(tc.CorruptBatch):
        cs.staging_put_batch(env.cp, w.owner1, w.org1, b.cemetery_uid, tc.make_contract(t.session, t.bootstrap_id, ent, no, count, rows), tampered)
    assert stg(env, t.bootstrap_id) == {"rows": 0, "batches": 0}
    assert t.run()["verified"] is True


# ================================================================== 3. UID MAPPING
def test_uid_mapping_deterministic_persistent_and_not_applied_to_live(env):
    w = World(env)
    b = w.local(w.org1)
    plan = b_plan(env, w, b)
    live_before = hashlib.sha256(b.database.read_bytes()).hexdigest()
    cols_before = {t_: sql(b, f"PRAGMA table_info({t_})") for t_ in ents.ORDER}
    bid = str(uuid.uuid4())
    t = start(env, w, b, plan, bid=bid, batch_size=2)
    m1 = t.state.mapping()
    assert len(m1) == 11 and len(set(m1.values())) == 11 and all(uuid.UUID(u) for u in m1.values())
    assert hashlib.sha256(b.database.read_bytes()).hexdigest() == live_before and {t_: sql(b, f"PRAGMA table_info({t_})") for t_ in ents.ORDER} == cols_before
    assert not sql(b, "SELECT 1 FROM locuri_de_veci WHERE uid IS NOT NULL")  # live: niciun uid atribuit
    assert t.state.path.parent == b.root / "bootstrap_state" and t.state.path.name == f"{bid}.sqlite"
    t.state.delete()  # stare pierdută => aceeași derivare deterministă dă aceleași UID-uri
    assert start(env, w, b, plan, bid=bid, batch_size=2).state.mapping() == m1
    other = start(env, w, b, plan, bid=str(uuid.uuid4()), batch_size=2).state.mapping()
    assert set(other.values()).isdisjoint(m1.values())  # alt bootstrap => alte UID-uri
    assert tr.derive_uid(bid, b.cemetery_uid, "locuri_de_veci", 1) == tr.derive_uid(bid, b.cemetery_uid, "locuri_de_veci", 1)


def test_resume_reads_persisted_mapping_and_never_regenerates(env, monkeypatch):
    w = World(env)
    b = w.local(w.org1)
    plan = b_plan(env, w, b)
    t = start(env, w, b, plan, batch_size=2)
    mapping = t.state.mapping()
    with pytest.raises(Interrupted):
        t.run(after_batch=interrupt_after(1))

    def boom(*a, **k):
        raise AssertionError("resume nu are voie să regenereze UID-uri")
    monkeypatch.setattr(tr, "derive_uid", boom)
    t2 = tr.resume_transfer(b, env.cp, w.owner1, t.bootstrap_id, plan["plan_hash"])  # obiect nou, citit de pe disc
    t2.run()
    assert t2.state.mapping() == mapping
    staged = {r[0] for r in q(env.cp.path, "SELECT uid FROM staging_rows WHERE bootstrap_id=? AND entity_type!='media'", t.bootstrap_id)}
    assert staged == {u for (e, _r), u in mapping.items() if e != "media"}
    c = sqlite3.connect(t.state.path)  # un mapping alterat pe disc este detectat (digest de batch-uri din plan)
    c.execute("UPDATE uid_map SET uid=? WHERE entity='locuri_de_veci' AND ref='1'", (str(uuid.uuid4()),))
    c.commit()
    c.close()
    with pytest.raises(tc.SourceDrift):
        tr.resume_transfer(b, env.cp, w.owner1, t.bootstrap_id, plan["plan_hash"]).run()


def test_existing_local_uid_is_reused_and_fk_wires_use_mapped_uids(env):
    w = World(env)
    b = w.local(w.org1)
    put_local(b, DATA)
    sql(b, "UPDATE locuri_de_veci SET uid='loc-uid-'||cod_loc")
    env.cp.create_cemetery(w.owner1, w.org1, b.cemetery_uid, "C")
    plan = pf.build_plan(b, env.cp, w.owner1, env.inst, clock=env.clock)
    t = start(env, w, b, plan, batch_size=5)
    t.run()
    uids = {r[0] for r in q(env.cp.path, "SELECT uid FROM staging_rows WHERE bootstrap_id=? AND entity_type='locuri_de_veci'", t.bootstrap_id)}
    assert uids == {"loc-uid-A-1", "loc-uid-A-2", "loc-uid-B-1"}
    con = [json.loads(r[0]) for r in q(env.cp.path, "SELECT content FROM staging_rows WHERE bootstrap_id=? AND entity_type='contracte'", t.bootstrap_id)]
    assert {c_["loc_uid"] for c_ in con} <= uids and all(c_["concesionar_uid"] in {x[0] for x in q(env.cp.path, "SELECT uid FROM staging_rows WHERE entity_type='concesionari'")} for c_ in con)


# ================================================================== 4. ORDINEA FK
def test_fk_ordering_parents_before_children_and_no_orphans(env):
    w = World(env)
    b = w.local(w.org1)
    plan = b_plan(env, w, b)
    t = start(env, w, b, plan, batch_size=1)
    t.open_staging()
    snap = mf.local_snapshot(b)
    batches = {(e, n): (c_, r) for e, n, c_, r in t._batches(snap)}
    assert list(t.meta["entity_plan"]) == ents.ORDER and tc.ENTITY_ORDER[-1] == "media"
    cnt, rows = batches[("contracte", 1)]
    with pytest.raises(tc.OrderViolation):  # copil înaintea părinților
        cs.staging_put_batch(env.cp, w.owner1, w.org1, b.cemetery_uid, tc.make_contract(t.session, t.bootstrap_id, "contracte", 1, cnt, rows), rows)
    cnt0, rows0 = batches[("locuri_de_veci", 1)]
    t.send("locuri_de_veci", 1, snap)
    with pytest.raises(tc.OrderViolation):  # părintele (locuri) nu e complet: 3 batch-uri
        t.send("concesionari", 1, snap)
    for e in ("locuri_de_veci", "concesionari"):
        for n in range(1, t.meta["entity_plan"][e] + 1):
            t.send(e, n, snap)
    bad = copy.deepcopy(rows)
    bad[0]["data"]["loc_uid"] = str(uuid.uuid4())  # FK către un părinte inexistent în staging
    with pytest.raises(tc.OrphanFK):
        cs.staging_put_batch(env.cp, w.owner1, w.org1, b.cemetery_uid, tc.make_contract(t.session, t.bootstrap_id, "contracte", 1, cnt, bad), bad)
    assert t.send("locuri_de_veci", 1, snap)["status"] == "duplicate"  # reluarea independentă a unui batch vechi rămâne permisă
    rep = t.run()
    assert rep["verified"] is True and rep["staging_orphans"] == 0 and rep["status"]["complete"]


def test_source_with_orphan_fk_is_blocked_before_any_batch_is_staged(env):
    w = World(env)
    b = w.local(w.org1)
    put_local(b, DATA)
    sql(b, "UPDATE contracte SET id_loc=9999 WHERE numar_contract='C1'")  # sursa inconsecventă încă dinainte de plan
    env.cp.create_cemetery(w.owner1, w.org1, b.cemetery_uid, "C")
    plan = pf.build_plan(b, env.cp, w.owner1, env.inst, clock=env.clock)
    assert plan["decision"] == "B" and plan["local_manifest"]["integrity"]["orphan_total"] == 1
    bid = str(uuid.uuid4())
    with pytest.raises(tc.OrphanFK):  # blocat la pornire, înainte de orice stare locală sau batch
        start(env, w, b, plan, bid=bid, batch_size=1)
    assert not tr.StateStore(b, bid).exists() and "staging_sessions" not in {r[0] for r in q(env.cp.path, "SELECT name FROM sqlite_master")}


# ================================================================== 5. IZOLARE STAGING / TENANȚI
def test_staging_is_not_live_and_cleanup_is_scoped(env):
    w = World(env)
    b1, b2 = w.local(w.org1, "T1"), w.local(w.org2, "T2")
    p1 = b_plan(env, w, b1)
    put_local(b2, DATA)
    env.cp.create_cemetery(w.owner2, w.org2, b2.cemetery_uid, "T2")
    p2 = pf.build_plan(b2, env.cp, w.owner2, env.inst, clock=env.clock)
    live_before = mf.cloud_manifest(env.cp, w.owner1, w.org1, b1.cemetery_uid)
    t1 = start(env, w, b1, p1, batch_size=2)
    t2 = tr.start_transfer(b2, env.cp, w.owner2, p2, p2["plan_hash"], str(uuid.uuid4()), batch_size=2, clock=env.clock)
    t1.run(), t2.run()
    live_after = mf.cloud_manifest(env.cp, w.owner1, w.org1, b1.cemetery_uid)
    assert live_after["stable_digest"] == live_before["stable_digest"] and live_after["classification"] == "EMPTY" and live_after["total_rows"] == 0
    assert q(env.cp.path, "SELECT COUNT(*) FROM registry_rows")[0][0] == 0 and q(env.cp.path, "SELECT COUNT(*) FROM media_objects")[0][0] == 0
    assert pf.build_plan(b1, env.cp, w.owner1, env.inst, clock=env.clock)["decision"] == "B"  # preflight vede în continuare cloud-ul ca gol
    assert not q(env.cp.path, "SELECT 1 FROM staging_rows WHERE bootstrap_id IS NULL OR cemetery_uid IS NULL")
    assert {r[0] for r in q(env.cp.path, "SELECT DISTINCT cemetery_uid FROM staging_rows WHERE bootstrap_id=?", t1.bootstrap_id)} == {b1.cemetery_uid}
    # alt tenant: citire/scriere/cleanup refuzate sau „inexistent”
    for call in (lambda: cs.staging_status(env.cp, w.owner2, w.org1, b1.cemetery_uid, t1.bootstrap_id),
                 lambda: cs.staging_snapshot(env.cp, w.owner2, w.org1, b1.cemetery_uid, t1.bootstrap_id),
                 lambda: cs.staging_cleanup(env.cp, w.owner2, w.org1, b1.cemetery_uid, t1.bootstrap_id)):
        with pytest.raises(cc.PermissionDenied):
            call()
    for call in (lambda: cs.staging_status(env.cp, w.owner2, w.org2, b2.cemetery_uid, t1.bootstrap_id),  # propriul tenant, bootstrap_id străin
                 lambda: cs.staging_snapshot(env.cp, w.owner2, w.org2, b2.cemetery_uid, t1.bootstrap_id),
                 lambda: cs.staging_cleanup(env.cp, w.owner2, w.org2, b2.cemetery_uid, t1.bootstrap_id)):
        with pytest.raises(cc.ScopeError):
            call()
    snap_b1 = cs.staging_snapshot(env.cp, w.owner1, w.org1, b1.cemetery_uid, t1.bootstrap_id)
    dump = json.dumps(snap_b1)
    for needle in (b2.cemetery_uid, w.org2, t2.bootstrap_id):
        if needle in dump:
            LEAKS.append(needle)
    snap1 = mf.local_snapshot(b1)
    ent, no, count, rows = t1._batches(snap1)[0]
    contract = tc.make_contract(t1.session, t1.bootstrap_id, ent, no, count, rows)
    with pytest.raises(cc.PermissionDenied):
        cs.staging_put_batch(env.cp, w.owner2, w.org1, b1.cemetery_uid, contract, rows)
    with pytest.raises(cc.ScopeError):
        cs.staging_put_batch(env.cp, w.owner2, w.org2, b2.cemetery_uid, contract, rows)
    n2 = stg(env, t2.bootstrap_id)
    live_rows = q(env.cp.path, "SELECT COUNT(*) FROM registry_rows")[0][0]
    out = cs.staging_cleanup(env.cp, w.owner1, w.org1, b1.cemetery_uid, t1.bootstrap_id)
    assert out["rows"] == 11 and stg(env, t1.bootstrap_id) == {"rows": 0, "batches": 0} and stg(env, t2.bootstrap_id) == n2  # doar staging-ul lui
    assert q(env.cp.path, "SELECT COUNT(*) FROM registry_rows")[0][0] == live_rows
    assert q(env.cp.path, "SELECT state FROM staging_sessions WHERE bootstrap_id=?", t1.bootstrap_id) == [("cleaned",)]
    with pytest.raises(tc.SessionMismatch):  # sesiune curățată nu mai primește batch-uri
        cs.staging_put_batch(env.cp, w.owner1, w.org1, b1.cemetery_uid, contract, rows)
    assert LEAKS == [], LEAKS


def test_one_open_staging_per_cemetery(env):
    w = World(env)
    b = w.local(w.org1)
    plan = b_plan(env, w, b)
    t = start(env, w, b, plan)
    t.open_staging()
    t_other = start(env, w, b, plan)  # alt bootstrap_id, același plan
    with pytest.raises(tc.SessionMismatch):
        t_other.open_staging()
    tr.abort_transfer(b, env.cp, w.owner1, t.bootstrap_id)
    assert not t.state.exists()
    t_other.open_staging()


# ================================================================== 6. RESUME / DRIFT
@pytest.mark.parametrize("after", [1, 3, 6, 10])
def test_resume_multi_batch_continues_where_it_stopped(env, after):
    w = World(env)
    b = w.local(w.org1)
    plan = b_plan(env, w, b)
    t = start(env, w, b, plan, batch_size=1)
    total = total_batches(t)
    assert total == 11
    with pytest.raises(Interrupted):
        t.run(after_batch=interrupt_after(after))
    assert stg(env, t.bootstrap_id)["batches"] == after
    sent = []
    t2 = tr.resume_transfer(b, env.cp, w.owner1, t.bootstrap_id, plan["plan_hash"])
    rep = t2.run(after_batch=lambda e, n: sent.append((e, n)))
    assert rep["skipped_already_staged"] == after and rep["stored"] == total - after and len(sent) == total - after  # continuă de la următorul batch
    assert rep["verified"] and stg(env, t.bootstrap_id)["rows"] == 11
    assert t2.state.mapping() == t.state.mapping()


def test_resume_blocked_on_plan_source_manifest_and_cemetery_drift(env):
    w = World(env)
    b, b_other = w.local(w.org1, "A"), w.local(w.org1, "Z")
    plan = b_plan(env, w, b)
    t = start(env, w, b, plan, batch_size=2)
    with pytest.raises(Interrupted):
        t.run(after_batch=interrupt_after(2))
    with pytest.raises(tc.SessionMismatch):  # plan_hash schimbat
        tr.resume_transfer(b, env.cp, w.owner1, t.bootstrap_id, "0" * 64)
    with pytest.raises(tc.SessionMismatch):  # alt cemetery_uid (registru fără starea acestui bootstrap)
        tr.resume_transfer(b_other, env.cp, w.owner1, t.bootstrap_id, plan["plan_hash"])
    put_local(b_other, DATA)
    (b_other.root / "bootstrap_state").mkdir()
    (b_other.root / "bootstrap_state" / f"{t.bootstrap_id}.sqlite").write_bytes(t.state.path.read_bytes())  # stare copiată din alt cimitir
    with pytest.raises(tc.SessionMismatch):
        tr.resume_transfer(b_other, env.cp, w.owner1, t.bootstrap_id, plan["plan_hash"])
    # contract cu alt cemetery_uid sau alt plan_hash direct către cloud
    snap = mf.local_snapshot(b)
    ent, no, count, rows = t._batches(snap)[-1]
    forged = {**tc.make_contract(t.session, t.bootstrap_id, ent, no, count, rows), "cemetery_uid": b_other.cemetery_uid}
    with pytest.raises(tc.SessionMismatch):
        cs.staging_put_batch(env.cp, w.owner1, w.org1, b.cemetery_uid, forged, rows)
    with pytest.raises(cc.ScopeError):
        cs.staging_put_batch(env.cp, w.owner1, w.org1, b_other.cemetery_uid, tc.make_contract(t.session, t.bootstrap_id, ent, no, count, rows), rows)
    with pytest.raises(tc.SessionMismatch):
        cs.staging_put_batch(env.cp, w.owner1, w.org1, b.cemetery_uid, {**tc.make_contract(t.session, t.bootstrap_id, ent, no, count, rows), "plan_hash": "1" * 64}, rows)
    with pytest.raises(tc.SessionMismatch):
        cs.staging_put_batch(env.cp, w.owner1, w.org1, b.cemetery_uid, {**tc.make_contract(t.session, t.bootstrap_id, ent, no, count, rows), "source_manifest_digest": "2" * 64}, rows)
    done = stg(env, t.bootstrap_id)
    # sursa se schimbă după întrerupere
    sql(b, "UPDATE locuri_de_veci SET sector='Q' WHERE cod_loc='A-2'")
    with pytest.raises(tc.SourceDrift):
        tr.resume_transfer(b, env.cp, w.owner1, t.bootstrap_id, plan["plan_hash"])
    with pytest.raises(tc.SourceDrift):
        t.run()
    assert stg(env, t.bootstrap_id) == done  # nimic nou scris
    sql(b, "UPDATE locuri_de_veci SET sector='A' WHERE cod_loc='A-2'")  # revenire => resume permis
    assert tr.resume_transfer(b, env.cp, w.owner1, t.bootstrap_id, plan["plan_hash"]).run()["verified"]


def test_start_requires_valid_unexpired_B_plan_and_new_plan_cannot_hijack_bootstrap(env):
    w = World(env)
    b = w.local(w.org1)
    plan = b_plan(env, w, b)
    with pytest.raises(pf.PreflightError):
        tr.start_transfer(b, env.cp, w.owner1, plan, "0" * 64, str(uuid.uuid4()), clock=env.clock)
    t = start(env, w, b, plan, batch_size=2)
    sql(b, "INSERT INTO locuri_de_veci (cod_loc) VALUES ('NOU')")  # sursa se schimbă => planul vechi invalid
    with pytest.raises(pf.PlanInvalidated):
        start(env, w, b, plan)
    plan2 = pf.build_plan(b, env.cp, w.owner1, env.inst, clock=env.clock)
    with pytest.raises(tc.SessionMismatch):  # alt plan_hash pe același bootstrap_id
        tr.start_transfer(b, env.cp, w.owner1, plan2, plan2["plan_hash"], t.bootstrap_id, clock=env.clock)
    env.clock.t += pf.PLAN_TTL + 1
    with pytest.raises(pf.PlanExpired):
        start(env, w, b, plan2)
    env.clock.t -= pf.PLAN_TTL + 1
    env.cp.sim_set_status(b.cemetery_uid, "DRAFT", data_epoch=2)  # cloud schimbat după plan
    with pytest.raises(pf.PlanInvalidated):
        start(env, w, b, plan2)


def test_non_B_decisions_cannot_start_a_transfer(env):
    w = World(env)
    b = w.local(w.org1)
    plan = pf.build_plan(b, env.cp, w.owner1, env.inst, clock=env.clock)  # local gol => A
    assert plan["decision"] == "A"
    with pytest.raises(pf.FlowNotImplemented):
        start(env, w, b, plan)


def test_cloud_changes_midway_block_further_batches(env):
    w = World(env)
    b = w.local(w.org1)
    plan = b_plan(env, w, b)
    t = start(env, w, b, plan, batch_size=2)
    with pytest.raises(Interrupted):
        t.run(after_batch=interrupt_after(1))
    env.cp.sim_set_status(b.cemetery_uid, "DRAFT", data_epoch=2)
    with pytest.raises(tc.SessionMismatch):
        tr.resume_transfer(b, env.cp, w.owner1, t.bootstrap_id, plan["plan_hash"]).run()
    env.cp.sim_set_status(b.cemetery_uid, "ACTIVE", data_epoch=1)
    with pytest.raises(tc.SessionMismatch):
        tr.resume_transfer(b, env.cp, w.owner1, t.bootstrap_id, plan["plan_hash"]).run()
    assert stg(env, t.bootstrap_id)["batches"] == 1


def test_source_changing_during_run_is_detected_at_the_end(env):
    w = World(env)
    b = w.local(w.org1)
    plan = b_plan(env, w, b)
    t = start(env, w, b, plan, batch_size=4)

    def mutate(entity, no):
        if (entity, no) == ("locuri_de_veci", 1):
            sql(b, "UPDATE concesionari SET telefon='0700' WHERE nume='Ionescu'")
    with pytest.raises(tc.SourceDrift):
        t.run(after_batch=mutate)


# ================================================================== 7. MEDIA STAGING (simulat)
def test_media_staging_chunks_sha_duplicates_missing_and_not_published(env):
    w = World(env)
    b = w.local(w.org1)
    plan = b_plan(env, w, b, files=True)
    t = start(env, w, b, plan, batch_size=2, chunk_size=3)
    rep = t.run()
    assert rep["verified"] is True
    m = rep["media"]
    assert m["count"] == 4 and m["missing"] == ["lipsa.jpg"] and m["unique_objects"] == 2 and [d["count"] for d in m["duplicates"]] == [2]
    assert m["published"] is False and m["uploaded"] is False and m["bytes"] == 15
    rows = {json.loads(r[0])["name"]: json.loads(r[0]) for r in q(env.cp.path, "SELECT content FROM staging_rows WHERE bootstrap_id=? AND entity_type='media'", t.bootstrap_id)}
    assert [c_["size"] for c_ in rows["c.bin"]["chunks"]] == [3, 3, 1] and rows["a.jpg"]["chunk_count"] == 2
    assert rows["c.bin"]["chunks"][0]["sha256"] == hashlib.sha256(b"CCC").hexdigest() and rows["a.jpg"]["sha256"] == hashlib.sha256(b"AAAA").hexdigest()
    assert rows["lipsa.jpg"]["present"] is False and rows["lipsa.jpg"]["chunks"] == [] and rows["lipsa.jpg"]["status"] == "missing"
    uid_a = q(env.cp.path, "SELECT uid FROM staging_rows WHERE content LIKE '%\"name\": \"a.jpg\"%' AND bootstrap_id=?", t.bootstrap_id)[0][0]
    assert rows["b.jpg"]["dedupe_of"] == uid_a and rows["a.jpg"]["dedupe_of"] is None and all(r["published"] is False for r in rows.values())
    staged = mf.build_manifest("staging", cs.staging_snapshot(env.cp, w.owner1, w.org1, b.cemetery_uid, t.bootstrap_id))
    assert staged["media"]["digest"] == mf.local_manifest(b)["media"]["digest"] and staged["media"]["missing"] == 1  # digest media staging = sursă
    live = mf.cloud_manifest(env.cp, w.owner1, w.org1, b.cemetery_uid)
    assert live["media"]["count"] == 0 and q(env.cp.path, "SELECT COUNT(*) FROM media_objects")[0][0] == 0


def test_media_resume_after_interruption_and_file_changed_is_blocked(env):
    w = World(env)
    b = w.local(w.org1)
    plan = b_plan(env, w, b, files=True)
    t = start(env, w, b, plan, batch_size=1, chunk_size=2)
    media_batches = t.meta["entity_plan"]["media"]
    assert media_batches == 4
    n_before_media = total_batches(t) - media_batches
    with pytest.raises(Interrupted):
        t.run(after_batch=interrupt_after(n_before_media + 2))  # întrerupt în mijlocul media
    t2 = tr.resume_transfer(b, env.cp, w.owner1, t.bootstrap_id, plan["plan_hash"])
    assert t2.status()["entities"]["media"]["missing"] == [3, 4]
    assert t2.run()["verified"] is True
    f = b.media / "c.bin"
    f.write_bytes(b"ZZZZZZZ")  # fișier schimbat după plan
    with pytest.raises(tc.SourceDrift):
        tr.resume_transfer(b, env.cp, w.owner1, t.bootstrap_id, plan["plan_hash"])


# ================================================================== 8. ZERO LIVE WRITES
def _cloud_live_dump(env):
    c = sqlite3.connect(env.cp.path)
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'staging_%' AND name NOT IN ('audit_log','sqlite_sequence') ORDER BY name")]
    out = {t_: sorted(c.execute(f"SELECT * FROM {t_}").fetchall(), key=repr) for t_ in tables}
    c.close()
    return out


def test_transfer_makes_zero_live_writes(env):
    w = World(env)
    b = w.local(w.org1)
    plan = b_plan(env, w, b, files=True)
    cloud_before = _cloud_live_dump(env)
    audit_before = q(env.cp.path, "SELECT COUNT(*) FROM audit_log")[0][0]
    db_hash = hashlib.sha256(b.database.read_bytes()).hexdigest()
    local_before = {k: v for k, v in tree_state(b.root).items()}
    settings = sql(b, "SELECT key, value FROM settings ORDER BY key")
    tables = sql(b, "SELECT name, sql FROM sqlite_master ORDER BY name")
    local_before = {k: v for k, v in tree_state(b.root).items()}  # după interogările de bază (conexiuni scriibile)
    t = start(env, w, b, plan, batch_size=2, chunk_size=3)
    with pytest.raises(Interrupted):
        t.run(after_batch=interrupt_after(4))
    tr.resume_transfer(b, env.cp, w.owner1, t.bootstrap_id, plan["plan_hash"]).run()
    assert _cloud_live_dump(env) == cloud_before  # live neatins: cemeteries/registry_rows/bindings/devices/tombstones/media/pairing/runs/epoch
    assert q(env.cp.path, "SELECT status, data_epoch FROM cemeteries WHERE uid=?", b.cemetery_uid) == [("DRAFT", 1)]
    assert not q(env.cp.path, "SELECT 1 FROM bindings") and not q(env.cp.path, "SELECT 1 FROM bootstrap_runs")
    new_audit = q(env.cp.path, "SELECT action FROM audit_log ORDER BY id")[audit_before:]
    assert {a[0] for a in new_audit} == {"staging_open"}
    assert hashlib.sha256(b.database.read_bytes()).hexdigest() == db_hash
    assert sql(b, "SELECT key, value FROM settings ORDER BY key") == settings and sql(b, "SELECT name, sql FROM sqlite_master ORDER BY name") == tables
    after = tree_state(b.root)
    changed = {k for k in set(after) | set(local_before) if after.get(k) != local_before.get(k)}
    assert all("bootstrap_state" in k for k in changed), changed  # singura scriere locală: starea transferului (în afara bazei live)
    assert (b.root / "bootstrap_state").exists() and not (b.root / "bootstrap.lock").exists()
    assert env.bs.LocalRegistry(b).sync_state == "UNBOUND"
    pf.build_plan(b, env.cp, w.owner1, env.inst, clock=env.clock)
    tr.abort_transfer(b, env.cp, w.owner1, t.bootstrap_id)
    assert _cloud_live_dump(env) == cloud_before and not (b.root / "bootstrap_state").exists()
    assert stg(env, t.bootstrap_id) == {"rows": 0, "batches": 0}
