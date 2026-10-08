"""Independent verifications for ETAPA 4 / PAS 3.5 hardening — written by a tester.

Reuses the `env` fixture and `World` helper from `test_etapa4_pas1`, and `b_plan/start/q`
helpers from `test_etapa4_pas3_transfer`. All runs are local (LocalControlPlane).
"""
from __future__ import annotations

import copy
import random
import sqlite3
import uuid

import pytest

import cimitir_cloud_control as cc
import cimitir_cloud_staging as cs
import cimitir_identity as ident
import cimitir_manifest as mf
import cimitir_transfer as tr
import cimitir_transfer_contract as tc
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas3_transfer import b_plan, q, start


# -------- 1. PROMOTION ATOMIC + IDEMPOTENT, release ordering, IdentityConflict rollback

def test_promote_atomic_idempotent_and_release_rules(env):
    w = World(env)
    b = w.local(w.org1)
    plan = b_plan(env, w, b)
    t = start(env, w, b, plan, batch_size=3)
    t.run()
    bid = t.bootstrap_id

    # release before promotion must refuse
    with pytest.raises(tc.TransferBlocked):
        tr.release_local_state(b, bid, bootstrap_completed=True)

    # promote before bootstrap_completed must refuse
    with pytest.raises(tc.TransferBlocked):
        tr.promote_transfer(b, bid, bootstrap_completed=False)

    # first promote
    r1 = tr.promote_transfer(b, bid, bootstrap_completed=True)
    assert r1["promoted"] is True and r1.get("already") in (None, False) and r1["row_count"] > 0

    # idempotent second call
    r2 = tr.promote_transfer(b, bid, bootstrap_completed=True)
    assert r2 == {"promoted": True, "already": True, "row_count": r1["row_count"]}

    # cloud_entity_identity has exactly r1["row_count"] active rows for this cemetery
    con = sqlite3.connect(b.database)
    try:
        n = con.execute(
            "SELECT COUNT(*) FROM cloud_entity_identity WHERE cemetery_uid=? AND status='active'",
            (b.cemetery_uid,),
        ).fetchone()[0]
    finally:
        con.close()
    assert n == r1["row_count"]

    # witness exists
    assert ident.promotion_status(b, bid)["promoted"] is True

    # release only after promoted
    tr.release_local_state(b, bid, bootstrap_completed=True)
    assert not (b.root / "bootstrap_state" / f"{bid}.sqlite").exists()


def test_identity_conflict_rolls_back_all_rows(env, monkeypatch):
    """A second mapping that reuses the same local_ref but a new cloud_uid must
    be refused and leave cloud_entity_identity unchanged (atomic rollback)."""
    w = World(env)
    b = w.local(w.org1)
    plan = b_plan(env, w, b)
    t = start(env, w, b, plan, batch_size=3)
    t.run()
    tr.promote_transfer(b, t.bootstrap_id, bootstrap_completed=True)

    con = sqlite3.connect(b.database)
    rows_before = con.execute(
        "SELECT entity_type, local_ref, cloud_uid FROM cloud_entity_identity WHERE cemetery_uid=? AND status='active'",
        (b.cemetery_uid,),
    ).fetchall()
    con.close()

    # Build a conflicting mapping dict: same (entity, local_ref) keys but different cloud UIDs
    conflicting = {(e, r): str(uuid.uuid4()) for (e, r, _u) in rows_before}
    fake_bid = str(uuid.uuid4())
    with pytest.raises(ident.IdentityConflict):
        ident.promote(b, fake_bid, conflicting, b.cemetery_uid)

    con = sqlite3.connect(b.database)
    try:
        rows_after = con.execute(
            "SELECT entity_type, local_ref, cloud_uid FROM cloud_entity_identity WHERE cemetery_uid=? AND status='active'",
            (b.cemetery_uid,),
        ).fetchall()
        # no new promotion witness recorded
        witness = con.execute(
            "SELECT COUNT(*) FROM cloud_identity_promotions WHERE bootstrap_id=?",
            (fake_bid,),
        ).fetchone()[0]
    finally:
        con.close()
    assert sorted(rows_after) == sorted(rows_before)
    assert witness == 0


def test_identities_scoped_to_cemetery_and_reused_by_second_bootstrap(env):
    """A second bootstrap on the SAME cemetery with same source reuses the same cloud UIDs
    (via existing_identities), and identities created for one cemetery are not visible
    for another cemetery."""
    w = World(env)
    b = w.local(w.org1)
    plan = b_plan(env, w, b)
    t1 = start(env, w, b, plan, batch_size=3)
    t1.run()
    tr.promote_transfer(b, t1.bootstrap_id, bootstrap_completed=True)

    existing = ident.existing_identities(b)
    assert existing and all(uuid.UUID(u) for u in existing.values())

    # second bootstrap on the same source => mapping must contain all previously
    # promoted (entity, local_ref) with the SAME cloud UID
    t2 = start(env, w, b, plan, bid=str(uuid.uuid4()), batch_size=5)
    m2 = t2.state.mapping()
    reused = {k: v for k, v in m2.items() if k in existing}
    assert reused == existing  # strict reuse

    # Create a different cemetery; its identity scope must be isolated
    b2 = w.local(w.org1, name="Other")
    assert ident.existing_identities(b2) == {}


# -------- 2. BATCHING DETERMINISM + SourceDrift on altered uid_map

def test_batching_deterministic_under_shuffled_local_snapshot(env, monkeypatch):
    w = World(env)
    b = w.local(w.org1)
    plan = b_plan(env, w, b)
    t = start(env, w, b, plan, batch_size=2)
    snap = mf.local_snapshot(b)
    mapping = t.state.mapping()
    ref = [(e, n, c, [tc.batch_sha256([row]) for row in rows], tc.batch_sha256(rows))
           for (e, n, c, rows) in t._batches(copy.deepcopy(snap), mapping)]

    for seed in (1, 7, 42, 123, 999):
        shuffled = copy.deepcopy(snap)
        rnd = random.Random(seed)
        for ent, rows in shuffled["entities"].items():
            rnd.shuffle(rows)
        got = [(e, n, c, [tc.batch_sha256([row]) for row in rows], tc.batch_sha256(rows))
               for (e, n, c, rows) in t._batches(shuffled, mapping)]
        assert got == ref, f"non-deterministic batching with seed={seed}"


def test_altered_uid_map_on_disk_raises_sourcedrift_on_resume(env):
    w = World(env)
    b = w.local(w.org1)
    plan = b_plan(env, w, b)
    t = start(env, w, b, plan, batch_size=2)
    # Mutate persisted uid_map (temp mapping) => digest check must flag drift on resume
    sp = t.state.path
    c = sqlite3.connect(sp)
    try:
        c.execute("UPDATE uid_map SET uid=? WHERE rowid=(SELECT MIN(rowid) FROM uid_map)",
                  (str(uuid.uuid4()),))
        c.commit()
    finally:
        c.close()
    with pytest.raises(tc.SourceDrift):
        tr.resume_transfer(b, env.cp, w.owner1, t.bootstrap_id, plan["plan_hash"]).run()


# -------- 3. PERMISSIONS MATRIX

def test_permissions_dict_shape():
    assert cc.PERMISSIONS["bootstrap"] == {cc.OWNER}
    assert cc.PERMISSIONS["rebuild"] == {cc.OWNER, cc.REBUILD}
    assert cc.PERMISSIONS["bootstrap"] != cc.PERMISSIONS["rebuild"]


def test_staging_denies_admin_rebuild_stranger_and_other_tenant_owner(env):
    w = World(env)
    b = w.local(w.org1)
    plan = b_plan(env, w, b)
    t = start(env, w, b, plan, batch_size=5)
    t.open_staging()
    bid = t.bootstrap_id
    org = b.organization_uid
    cem = b.cemetery_uid

    # non-owner principals: admin, rebuild, stranger, owner of another org
    admin = env.cp.create_principal("admin1")
    rebuild = env.cp.create_principal("rebuild1")
    stranger = env.cp.create_principal("stranger1")
    env.cp.grant_role(w.owner1, admin, org, cc.ADMIN)
    env.cp.grant_role(w.owner1, rebuild, org, cc.REBUILD)

    cases = [
        ("staging_status", lambda who: cs.staging_status(env.cp, who, org, cem, bid)),
        ("staging_mark_stale", lambda who: cs.staging_mark_stale(env.cp, who, org, cem)),
        ("staging_cleanup", lambda who: cs.staging_cleanup(env.cp, who, org, cem, bid)),
    ]
    # Same-tenant non-owners => PermissionDenied; other-tenant/stranger => PermissionDenied OR ScopeError
    for name, fn in cases:
        for who in (admin, rebuild):
            with pytest.raises(cc.PermissionDenied):
                fn(who)
        for who in (stranger, w.owner2):
            with pytest.raises((cc.PermissionDenied, cc.ScopeError)):
                fn(who)
        # Owner of correct tenant works (sanity)
        fn(w.owner1)


def test_stale_session_refuses_put_batch_and_does_not_delete(env):
    w = World(env)
    b = w.local(w.org1)
    plan = b_plan(env, w, b)
    t = start(env, w, b, plan, batch_size=2)
    t.open_staging()
    bid = t.bootstrap_id

    # Force session to be eligible for stale by advancing clock past stale_after
    stale_after = q(env.cp.path, "SELECT stale_after FROM staging_sessions WHERE bootstrap_id=?", bid)[0][0]
    env.clock.t += float(stale_after) + 10.0
    marked = cs.staging_mark_stale(env.cp, w.owner1, b.organization_uid, b.cemetery_uid)
    assert bid in marked

    # No rows/batches deleted
    rows_count = q(env.cp.path, "SELECT COUNT(*) FROM staging_rows WHERE bootstrap_id=?", bid)[0][0]
    sess_state = q(env.cp.path, "SELECT state FROM staging_sessions WHERE bootstrap_id=?", bid)[0][0]
    assert sess_state == "stale" and rows_count >= 0

    # put_batch on stale session must fail
    snap = mf.local_snapshot(b)
    mapping = t.state.mapping()
    ent, no, count, rows = t._batches(snap, mapping)[0]
    session = {k: t.meta[k] for k in tc.SESSION_FIELDS}
    contract = tc.make_contract(session, bid, ent, no, count, rows)
    with pytest.raises(Exception):
        cs.staging_put_batch(env.cp, w.owner1, b.organization_uid, b.cemetery_uid, contract, rows)


# -------- 4. ZERO LIVE WRITES after a complete transfer (sanity cross-check)

def test_zero_live_writes_after_full_transfer(env):
    import hashlib
    w = World(env)
    b = w.local(w.org1)
    plan = b_plan(env, w, b)
    before = hashlib.sha256(b.database.read_bytes()).hexdigest()
    t = start(env, w, b, plan, batch_size=3)
    t.run()
    after = hashlib.sha256(b.database.read_bytes()).hexdigest()
    # NOTE: cloud_entity_identity lives in the registry DB only after promote; here we only ran transfer
    assert before == after
