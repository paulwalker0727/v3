"""Etapa 4 / Pasul 4 — FLUX B: MAIN existent → cloud nou (DRAFT + EMPTY), NUMAI simulator local SQLite + foldere temporare.
Nu atinge Supabase/Render/producție/Banovița/014; fără HTTP; fără Flux C/D/E; fără Sync V2.
"""
# ruff: noqa: F811
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

import pytest

import cimitir_bootstrap as bs
import cimitir_cloud_control as cc
import cimitir_cloud_media as cm
import cimitir_cloud_publish as cpub
import cimitir_cloud_staging as cs
import cimitir_entities as ents
import cimitir_flow_b as fb
import cimitir_identity as ident
import cimitir_manifest as mf
import cimitir_preflight as pf
import cimitir_snapshot as snap
import cimitir_transfer as tr
import cimitir_transfer_contract as tc
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import finish, kill9, renv, spawn, wait_marker  # noqa: F401
from test_etapa4_pas2_manifest import DATA, add_file, put_local, sql
from test_etapa4_pas35_hardening import live_dump
from test_etapa4_pas3_transfer import q

LEAKS: list = []
OUTBOX = ("INSERT INTO cloud_sync_outbox (kind, entity, entity_uid, action, payload, state) VALUES ('entity','locuri_de_veci','{u}','upsert','{{\"n\":{n}}}','pending')")


class Boom(Exception):
    pass


def make_outbox(b, n=3):
    c = sqlite3.connect(b.database)
    c.executescript(snap._DDL)
    for i in range(n):
        c.execute(OUTBOX.format(u=f"u{i}", n=i))
    c.commit()
    c.close()


def outbox_rows(b):
    return sql(b, "SELECT id, entity_uid, payload FROM cloud_sync_outbox ORDER BY id")


def b_world(env, files=True, missing=False, outbox=3, corrupt=False):
    w = World(env)
    b = w.local(w.org1)
    put_local(b, DATA)
    if files:
        add_file(b, "a.jpg", b"AAAA")
        add_file(b, "b.jpg", b"AAAA")
        add_file(b, "c.bin", b"CCCCCCC")
    if missing:
        sql(b, "INSERT INTO documents (entity_type, entity_id, doc_type, file_path) VALUES ('loc','A-1','poza','lipsa.jpg')")
    if corrupt:
        sql(b, "UPDATE documents SET sha256=? WHERE file_path='c.bin'", "0" * 64)
    if outbox:
        make_outbox(b, outbox)
    env.cp.create_cemetery(w.owner1, b.organization_uid, b.cemetery_uid, "Cimitir")
    code = env.cp.issue_code(w.owner1, b.organization_uid, b.cemetery_uid)
    return w, b, code


def plan_b(env, w, b, actor=None):
    return fb.plan_flow_b(b, env.cp, actor or w.owner1, env.inst, clock=env.clock)


def run_b(env, w, b, plan, code, actor=None, hook=None, **kw):
    kw.setdefault("batch_size", 2)
    kw.setdefault("chunk_size", 3)
    return fb.execute_flow_b(b, env.cp, env.store, actor or w.owner1, plan, plan["plan_hash"], code, env.inst, clock=env.clock, hook=hook, **kw)


def cloud_counts(env, cem):
    return {"rows": q(env.cp.path, "SELECT COUNT(*) FROM registry_rows WHERE cemetery_uid=?", cem)[0][0],
            "media": q(env.cp.path, "SELECT COUNT(*) FROM media_objects WHERE cemetery_uid=?", cem)[0][0],
            "blobs": q(env.cp.path, "SELECT COUNT(*) FROM media_blobs WHERE cemetery_uid=?", cem)[0][0] if _has(env, "media_blobs") else 0}


def _has(env, table):
    return bool(q(env.cp.path, "SELECT 1 FROM sqlite_master WHERE name=?", table))


def cem_state(env, cem):
    return q(env.cp.path, "SELECT status, data_epoch FROM cemeteries WHERE uid=?", cem)[0]


def assert_completed(env, w, b, plan, rep, media_logical=3, blobs=2):
    cem = b.cemetery_uid
    assert rep["result"] == "completed" and rep["post_publish_verified"] is True and rep["sync_epoch"] == 1
    assert cem_state(env, cem) == ("ACTIVE", 1)
    cloud = mf.cloud_manifest(env.cp, w.owner1, b.organization_uid, cem)
    assert cloud["content_digest"] == plan["local_manifest"]["content_digest"] == rep["source_content_digest"] and cloud["total_rows"] == 11
    assert cloud["media"]["count"] == media_logical
    assert cloud_counts(env, cem) == {"rows": 11, "media": media_logical, "blobs": blobs}
    assert q(env.cp.path, "SELECT COUNT(*) FROM bindings WHERE cemetery_uid=?", cem)[0][0] == 1
    assert q(env.cp.path, "SELECT COUNT(*) FROM bootstrap_runs WHERE cemetery_uid=? AND state='completed'", cem)[0][0] == 1
    loc = bs.LocalRegistry(b)
    assert loc.sync_state in ("BOUND", "BOUND_MEDIA_PENDING") and loc.get("bound_installation_uid") == env.inst and loc.get("sync_epoch") == "1"
    assert loc.run_get(plan["bootstrap_id"])["state"] == "completed" and loc.phase(plan["bootstrap_id"]) == "done"
    assert not (b.root / "bootstrap.lock").exists() or not bs.lock_held(b)
    assert not tr.StateStore(b, plan["bootstrap_id"]).exists() and not snap.snapshot_binding(b, plan["bootstrap_id"]).root.exists()
    assert len(ident.existing_identities(b)) == 11 and len(ident.existing_media_identities(b)) == media_logical
    assert not q(env.cp.path, "SELECT 1 FROM staging_rows WHERE bootstrap_id=?", plan["bootstrap_id"])
    assert not q(env.cp.path, "SELECT 1 FROM staging_media_chunks WHERE bootstrap_id=?", plan["bootstrap_id"])
    assert bs.ping(b, env.cp, env.store, env.inst)["sync_epoch"] == 1


# ================================================================== 1. FLUX B COMPLET + CALE FERICITĂ
def test_flow_b_happy_path_end_to_end(env):
    w, b, code = b_world(env)
    rows_before = {t_: sql(b, f"SELECT * FROM {t_} ORDER BY id") for t_ in ents.ORDER}
    plan = plan_b(env, w, b)
    assert plan["decision"] == "B" and plan["kind"] == "B" and plan["media_missing"] == [] and bs.LocalRegistry(b).run_get(plan["bootstrap_id"])["state"] == "planned"
    assert bs.LocalRegistry(b).sync_state == "UNBOUND"  # planul nu atinge starea de sincronizare
    old_outbox = outbox_rows(b)
    phases = []
    rep = run_b(env, w, b, plan, code, hook=phases.append)
    assert_completed(env, w, b, plan, rep)
    assert {t_: sql(b, f"SELECT * FROM {t_} ORDER BY id") for t_ in ents.ORDER} == rows_before  # MAIN nemodificat
    assert rep["outbox_archived"] == 3 and outbox_rows(b) == [] and len(snap.archived_outbox(b, plan["bootstrap_id"])) == 3
    assert [r["entity_uid"] for r in snap.archived_outbox(b, plan["bootstrap_id"])] == [r[1] for r in old_outbox]
    for ph in ("begin", "redeemed", "snapshot:in_txn", "snapshot:after_commit", "entities:begin", "media:begin", "verify:before", "verify:after", "publish:before",
               "publish:in_txn", "publish:after_commit", "finalize:in_txn", "finalize:after_commit", "finalize:before_done"):
        assert ph in phases, ph
    assert phases.index("snapshot:after_commit") < phases.index("media:begin") < phases.index("verify:after") < phases.index("publish:in_txn") < phases.index("finalize:in_txn")
    assert q(env.cp.path, "SELECT COUNT(*) FROM media_published")[0][0] == 3 and rep["identity_promotion"]["row_count"] == 11
    key = env.store.get(bs.LocalRegistry(b).get("cloud_secret_ref"))
    assert key and all(key.encode() not in f.read_bytes() for f in b.root.rglob("*") if f.is_file() and f.name != "bootstrap.lock")  # cheia doar în SecretStore


def test_flow_b_idempotent_rerun_and_double_publish(env):
    w, b, code = b_world(env)
    plan = plan_b(env, w, b)
    rep = run_b(env, w, b, plan, code)
    before = live_dump(env)
    assert run_b(env, w, b, plan, code) == rep  # a doua rulare întoarce raportul, fără efecte
    key = env.store.get(bs.LocalRegistry(b).get("cloud_secret_ref"))
    res = cpub.publish_flow_b(env.cp, w.owner1, b.organization_uid, b.cemetery_uid, plan["bootstrap_id"], key, fb._expect(plan))
    assert res == {"published": True, "already": True} and live_dump(env) == before
    with pytest.raises(bs.PlanError):
        fb.execute_flow_b(b, env.cp, env.store, w.owner1, plan, "x" * 64, code, env.inst, clock=env.clock)
    assert tr.promote_transfer(b, plan["bootstrap_id"], True)["already"] is True


def test_initial_epoch_is_one_without_artificial_epoch_two(env):
    w, b, code = b_world(env)
    plan = plan_b(env, w, b)
    run_b(env, w, b, plan, code)
    assert q(env.cp.path, "SELECT data_epoch FROM cemeteries") == [(1,)] and q(env.cp.path, "SELECT sync_epoch FROM bindings") == [(1,)]
    assert not q(env.cp.path, "SELECT 1 FROM cemeteries WHERE data_epoch<>1") and bs.LocalRegistry(b).get("sync_epoch") == "1"
    assert not any("archive" in r[0].lower() for r in q(env.cp.path, "SELECT name FROM sqlite_master WHERE type='table'"))  # fără arhivă de epocă veche în B


# ================================================================== 2. SNAPSHOT CONSISTENT + MODIFICĂRI ULTERIOARE PĂSTRATE
def test_consistent_snapshot_and_post_snapshot_changes_preserved(env):
    w, b, code = b_world(env)
    plan = plan_b(env, w, b)
    seen = {}

    def hook(phase):
        if phase == "snapshot:after_commit":  # MAIN continuă să fie folosit în timpul transferului
            seen["snap_digest"] = mf.build_manifest("local", mf.local_snapshot(snap.snapshot_binding(b, plan["bootstrap_id"])))["content_digest"]
            sql(b, "INSERT INTO locuri_de_veci (cod_loc, sector) VALUES ('Z-9','Z')")
            sql(b, "UPDATE locuri_de_veci SET sector='MOD' WHERE cod_loc='A-1'")
            sql(b, OUTBOX.format(u="post1", n=99))
            seen["live_after"] = mf.local_manifest(b)["content_digest"]
        if phase == "media:begin":  # transferul lucrează pe snapshot: live schimbat, snapshot neschimbat
            assert mf.build_manifest("local", mf.local_snapshot(snap.snapshot_binding(b, plan["bootstrap_id"])))["content_digest"] == seen["snap_digest"]
        if phase == "publish:before":
            sql(b, OUTBOX.format(u="post2", n=100))

    rep = run_b(env, w, b, plan, code, hook=hook)
    assert seen["snap_digest"] == plan["local_manifest"]["content_digest"] != seen["live_after"]
    assert_completed(env, w, b, plan, rep)
    assert sql(b, "SELECT cod_loc FROM locuri_de_veci WHERE cod_loc='Z-9'") == [("Z-9",)] and sql(b, "SELECT sector FROM locuri_de_veci WHERE cod_loc='A-1'") == [("MOD",)]
    assert [r[1] for r in outbox_rows(b)] == ["post1", "post2"]  # modificările de după cut rămân în outbox (pentru Sync V2)
    assert len(snap.archived_outbox(b, plan["bootstrap_id"])) == 3
    cloud_a1 = q(env.cp.path, "SELECT content FROM registry_row_data WHERE entity='locuri_de_veci'")
    assert not any("Z-9" in r[0] or "MOD" in r[0] for r in cloud_a1)  # cloud = snapshot-ul, nu live-ul modificat
    assert min(r[0] for r in outbox_rows(b)) > max(r["id"] for r in snap.archived_outbox(b, plan["bootstrap_id"]))  # ID-urile nu se reutilizează
    ids = ident.existing_identities(b)
    assert ("locuri_de_veci", sql(b, "SELECT id FROM locuri_de_veci WHERE cod_loc='Z-9'")[0][0].__str__()) not in ids


def test_rollback_restores_archived_outbox_and_keeps_new_entries(env):
    w, b, code = b_world(env)
    plan = plan_b(env, w, b)
    original = outbox_rows(b)

    def hook(phase):
        if phase == "snapshot:after_commit":
            sql(b, OUTBOX.format(u="after-cut", n=7))
            assert outbox_rows(b) == [r for r in outbox_rows(b) if r[1] == "after-cut"]
        if phase == "media:begin":
            raise Boom("eșec în transfer")

    with pytest.raises(Boom):
        run_b(env, w, b, plan, code, hook=hook)
    rows = outbox_rows(b)
    assert [r for r in rows if r[1] != "after-cut"] == original and [r[1] for r in rows if r[1] == "after-cut"] == ["after-cut"]  # nimic pierdut, ID-uri originale
    loc = bs.LocalRegistry(b)
    assert loc.run_get(plan["bootstrap_id"])["state"] == "rolled_back" and loc.sync_state == "UNBOUND" and loc.get("cloud_secret_ref") == ""
    assert cem_state(env, b.cemetery_uid) == ("DRAFT", 1) and cloud_counts(env, b.cemetery_uid)["rows"] == 0
    assert q(env.cp.path, "SELECT status FROM bindings") == [("revoked",)] and not env.store.get("binding/" + q(env.cp.path, "SELECT binding_uid FROM bindings")[0][0])
    assert not snap.snapshot_binding(b, plan["bootstrap_id"]).root.exists() and not tr.StateStore(b, plan["bootstrap_id"]).exists()
    assert not ident.media_identity_rows(b, plan["bootstrap_id"]) and ident.existing_media_identities(b) == {}
    assert snap.cut_info(b, plan["bootstrap_id"])["state"] == "rolled_back" and not snap.archived_outbox(b, plan["bootstrap_id"])
    assert not q(env.cp.path, "SELECT 1 FROM staging_rows") and not q(env.cp.path, "SELECT 1 FROM staging_media_chunks")
    # B poate fi reîncercat (cloud-ul a rămas DRAFT gol; sursa s-a schimbat => plan nou)
    code2 = env.cp.issue_code(w.owner1, b.organization_uid, b.cemetery_uid)
    plan2 = plan_b(env, w, b)
    assert plan2["bootstrap_id"] != plan["bootstrap_id"]
    rep = run_b(env, w, b, plan2, code2)
    assert rep["result"] == "completed" and cem_state(env, b.cemetery_uid) == ("ACTIVE", 1)


# ================================================================== 3. IDENTITATE DURABILĂ MEDIA + STĂRI pending → active
def test_media_identity_pending_until_publish_then_active(env):
    w, b, code = b_world(env)
    plan = plan_b(env, w, b)
    bid = plan["bootstrap_id"]
    snapshots = {}

    def hook(phase):
        if phase in ("verify:before", "publish:in_txn"):
            snapshots[phase] = ident.media_identity_rows(b, bid)
            assert ident.existing_media_identities(b) == {} and ident.existing_identities(b) == {}  # nimic activ înainte de confirmarea publicării
        if phase == "publish:after_commit":
            snapshots["after_commit"] = (ident.media_identity_rows(b, bid), ident.existing_identities(b), ident.promotion_status(b, bid))

    rep = run_b(env, w, b, plan, code, hook=hook)
    assert len(snapshots["verify:before"]) == 3 and all(v["status"] == "pending" for v in snapshots["verify:before"].values())
    assert set(snapshots["verify:before"]) == {f"document:{i}" for i in (1, 2, 3)}
    rows, ents_, prom = snapshots["after_commit"]
    assert all(v["status"] == "pending" for v in rows.values()) and ents_ == {} and prom["promoted"] is False  # cloud publicat, local încă neactivat
    final = ident.media_identity_rows(b, bid)
    assert all(v["status"] == "active" for v in final.values()) and ident.existing_media_identities(b) == {k: v["cloud_uid"] for k, v in final.items()}
    cloud_uids = {r[0] for r in q(env.cp.path, "SELECT uid FROM media_objects")}
    assert cloud_uids == {v["cloud_uid"] for v in final.values()} and len(cloud_uids) == 3
    assert_completed(env, w, b, plan, rep)


def test_media_identity_rename_same_name_same_sha_and_resume_stability(env):
    w, b, code = b_world(env)
    sql(b, "INSERT INTO documents (entity_type, entity_id, doc_type, file_path) VALUES ('inh','1','poza','x/c.bin')")
    (b.media / "x").mkdir(parents=True)
    (b.media / "x" / "c.bin").write_bytes(b"OTHER-CONTENT")  # același nume de fișier ca c.bin, alt context și alt conținut
    plan = plan_b(env, w, b)
    t = tr.start_transfer(b, env.cp, w.owner1, plan, plan["plan_hash"], plan["bootstrap_id"], clock=env.clock, batch_size=2, chunk_size=3)
    m1 = t.state.mapping()
    media = {k: v for k, v in m1.items() if k[0] == tc.MEDIA}
    assert set(k[1] for k in media) == {f"document:{i}" for i in (1, 2, 3, 4)} and len(set(media.values())) == 4  # nume identic ≠ identitate; SHA identic = 2 atașamente
    wire = t._wire_media_rows(tr.canonicalize(mf.local_snapshot(b)), m1)
    dedup = [r for r in wire if r["data"]["dedupe_of"]]
    assert len(dedup) == 1 and dedup[0]["data"]["sha256"] == mf.file_sha256(b.media / "a.jpg")  # dedup fizic, dar două UID-uri logice
    # resume (alt obiect de transfer, aceeași stare persistată) păstrează aceleași UID-uri
    t2 = tr.resume_transfer(b, env.cp, w.owner1, plan["bootstrap_id"], plan["plan_hash"])
    assert t2.state.mapping() == m1
    # redenumirea fișierului nu schimbă identitatea (cheia este documents.id, nu numele)
    (b.media / "a.jpg").rename(b.media / "renamed.jpg")
    sql(b, "UPDATE documents SET file_path='renamed.jpg' WHERE id=1")
    snap2 = mf.local_snapshot(b)
    mp = tr._build_mapping(str(uuid.uuid4()), b.cemetery_uid, snap2, existing_media={"document:1": media[(tc.MEDIA, "document:1")]})
    assert mp[(tc.MEDIA, "document:1")] == media[(tc.MEDIA, "document:1")]
    other = tr._build_mapping(str(uuid.uuid4()), b.cemetery_uid, snap2)
    assert other[(tc.MEDIA, "document:1")] != media[(tc.MEDIA, "document:1")]  # fără identitate durabilă existentă se derivă una nouă (rezervată pending)


# ================================================================== 4. MEDIA STRICT
def test_media_missing_blocks_by_default_and_needs_explicit_audited_confirmation(env):
    w, b, code = b_world(env, missing=True)
    plan = plan_b(env, w, b)
    assert plan["media_missing"] == ["lipsa.jpg"]
    with pytest.raises(bs.BlockedFlow) as e:
        run_b(env, w, b, plan, code)
    assert e.value.decision == "MEDIA_MISSING"
    loc = bs.LocalRegistry(b)
    assert loc.run_get(plan["bootstrap_id"])["state"] == "planned" and loc.sync_state == "UNBOUND" and not q(env.cp.path, "SELECT 1 FROM bootstrap_runs")
    assert cem_state(env, b.cemetery_uid) == ("DRAFT", 1) and outbox_rows(b) and not (b.root / "bootstrap.lock").exists() or not bs.lock_held(b)
    rep = run_b(env, w, b, plan, code, accept_missing_media=True)
    assert rep["media"]["accepted_missing"] is True and rep["media"]["missing"] == 1 and loc.sync_state == "BOUND_MEDIA_PENDING"
    assert q(env.cp.path, "SELECT present FROM media_objects ORDER BY present") == [(0,), (1,), (1,), (1,)]
    audit = q(env.cp.path, "SELECT detail FROM audit_log WHERE action='publish_flow_b'")
    assert json.loads(audit[0][0])["accepted_missing_media"] is True and json.loads(audit[0][0])["missing_media"]
    cloud = mf.cloud_manifest(env.cp, w.owner1, b.organization_uid, b.cemetery_uid)
    assert cloud["content_digest"] == plan["local_manifest"]["content_digest"] and cloud["media"]["missing"] == 1


def test_cloud_publish_itself_refuses_missing_media_without_confirmation(env):
    w, b, code = b_world(env, missing=True)
    plan = plan_b(env, w, b)
    seen = {}

    def hook(phase):
        if phase == "publish:before":
            key = env.store.get(bs.LocalRegistry(b).get("cloud_secret_ref"))
            with pytest.raises(cm.MediaMissingBlocked):
                cpub.publish_flow_b(env.cp, w.owner1, b.organization_uid, b.cemetery_uid, plan["bootstrap_id"], key, fb._expect(plan), accept_missing_media=False)
            seen["blocked"] = cloud_counts(env, b.cemetery_uid)

    run_b(env, w, b, plan, code, hook=hook, accept_missing_media=True)
    assert seen["blocked"] == {"rows": 0, "media": 0, "blobs": 0}


def test_corrupt_source_media_refused_at_plan(env):
    w, b, code = b_world(env, corrupt=True)
    with pytest.raises(bs.BlockedFlow) as e:
        plan_b(env, w, b)
    assert e.value.decision == "MEDIA_CORRUPT" and not q(env.cp.path, "SELECT 1 FROM bootstrap_runs")


@pytest.mark.parametrize("tamper", ["flip_chunk", "drop_chunk", "forged_chunk_upload"])
def test_media_tamper_in_cloud_or_transit_blocks_publish_and_rolls_back(env, tamper):
    w, b, code = b_world(env)
    plan = plan_b(env, w, b)
    bid = plan["bootstrap_id"]

    def hook(phase):
        if phase == "media:done":
            if tamper == "flip_chunk":
                sql_cloud(env, "UPDATE staging_media_chunks SET data=? WHERE rowid=(SELECT MIN(rowid) FROM staging_media_chunks)", b"XXX")
            elif tamper == "drop_chunk":
                sql_cloud(env, "DELETE FROM staging_media_chunks WHERE rowid=(SELECT MAX(rowid) FROM staging_media_chunks)")
            else:
                uid = q(env.cp.path, "SELECT uid FROM staging_media_blobs LIMIT 1")[0][0]
                with pytest.raises(cm.MediaCorrupt):
                    cm.media_put_chunk(env.cp, w.owner1, b.organization_uid, b.cemetery_uid, bid, uid, 1, b"EVIL", "0" * 64)
                import hashlib
                with pytest.raises(cm.MediaCorrupt):  # SHA-ul bucății corect pentru bytes-ii falși, dar diferit de cel declarat în rând
                    cm.media_put_chunk(env.cp, w.owner1, b.organization_uid, b.cemetery_uid, bid, uid, 1, b"EVIL", hashlib.sha256(b"EVIL").hexdigest())
                raise Boom("trecut")

    with pytest.raises((Boom, tc.TransferBlocked)):
        run_b(env, w, b, plan, code, hook=hook)
    assert cem_state(env, b.cemetery_uid) == ("DRAFT", 1) and cloud_counts(env, b.cemetery_uid) == {"rows": 0, "media": 0, "blobs": 0}
    assert bs.LocalRegistry(b).run_get(bid)["state"] == "rolled_back" and len(outbox_rows(b)) == 3


def sql_cloud(env, query, *args):
    c = sqlite3.connect(env.cp.path)
    c.execute(query, args)
    c.commit()
    c.close()


def test_pre_publish_verification_reports_every_check(env):
    w, b, code = b_world(env)
    plan = plan_b(env, w, b)
    got = {}

    def hook(phase):
        if phase == "publish:before":
            got["ver"] = fb.verify_before_publish(env.cp, w.owner1, snap.snapshot_binding(b, plan["bootstrap_id"]), plan)

    run_b(env, w, b, plan, code, hook=hook)
    assert set(got["ver"]["checks"]) == {"staging_complete", "counts_per_entity", "digest_per_entity", "global_digest", "media_count", "media_bytes", "media_sha256",
                                         "fk_orphans_zero", "manifest_staging_eq_snapshot"} and got["ver"]["ok"]
    assert got["ver"]["media"]["physical_objects"] == 2 and got["ver"]["media"]["deduped"] == 1 and got["ver"]["media"]["bytes_logical"] == 15 and got["ver"]["media"]["bytes_physical"] == 11


def test_verification_failure_never_publishes(env):
    w, b, code = b_world(env)
    plan = plan_b(env, w, b)

    def hook(phase):
        if phase == "verify:before":  # staging alterat după transfer: un rând schimbat
            sql_cloud(env, "UPDATE staging_rows SET content=replace(content,'A-2','Q-2') WHERE entity_type='locuri_de_veci' AND content LIKE '%A-2%'")

    with pytest.raises(fb.VerificationFailed):
        run_b(env, w, b, plan, code, hook=hook)
    assert cem_state(env, b.cemetery_uid) == ("DRAFT", 1) and cloud_counts(env, b.cemetery_uid) == {"rows": 0, "media": 0, "blobs": 0}
    assert not q(env.cp.path, "SELECT 1 FROM media_published") and bs.LocalRegistry(b).run_get(plan["bootstrap_id"])["state"] == "rolled_back"


# ================================================================== 5. PUBLISH ATOMIC
def test_publish_is_atomic_all_or_nothing_then_resume(env):
    w, b, code = b_world(env)
    plan = plan_b(env, w, b)
    before = {}

    def hook(phase):
        if phase == "publish:before":
            before["cloud"] = live_dump(env)
        if phase == "publish:in_txn":  # TOATE scrierile (rânduri, media, blobs, ACTIVE) sunt făcute dar necommise
            raise bs.Crash("cădere în publish")

    with pytest.raises(bs.Crash):
        run_b(env, w, b, plan, code, hook=hook)
    after = live_dump(env)
    assert {k: v for k, v in after.items() if k not in ("bootstrap_runs", "bindings")} == {k: v for k, v in before["cloud"].items() if k not in ("bootstrap_runs", "bindings")}
    assert cem_state(env, b.cemetery_uid) == ("DRAFT", 1) and cloud_counts(env, b.cemetery_uid) == {"rows": 0, "media": 0, "blobs": 0}
    assert q(env.cp.path, "SELECT state FROM bootstrap_runs") == [("verifying",)] and bs.LocalRegistry(b).get("sync_state") == "BOOTSTRAPPING"
    rep = run_b(env, w, b, plan, code)  # resume sigur
    assert_completed(env, w, b, plan, rep)


def test_publish_refuses_non_empty_or_changed_target_and_wrong_run(env):
    w, b, code = b_world(env)
    plan = plan_b(env, w, b)
    seen = []

    def hook(phase):
        if phase == "publish:before":
            key = env.store.get(bs.LocalRegistry(b).get("cloud_secret_ref"))
            args = (env.cp, w.owner1, b.organization_uid, b.cemetery_uid, plan["bootstrap_id"], key)
            env.cp.sim_put_row(b.cemetery_uid, "locuri_de_veci", str(uuid.uuid4()), {"cod_loc": "INTRUS"})  # cineva scrie în țintă între timp
            with pytest.raises(cpub.PublishBlocked):
                cpub.publish_flow_b(*args, fb._expect(plan))
            env.cp.sim_delete_row(b.cemetery_uid, "locuri_de_veci", q(env.cp.path, "SELECT uid FROM registry_rows")[0][0])
            sql_cloud(env, "DELETE FROM tombstones")
            with pytest.raises(cc.ConflictError):
                cpub.publish_flow_b(*args, {**fb._expect(plan), "plan_hash": "0" * 64})
            with pytest.raises(cc.ScopeError):
                cpub.publish_flow_b(env.cp, w.owner1, b.organization_uid, b.cemetery_uid, str(uuid.uuid4()), key, fb._expect(plan))
            with pytest.raises(tc.SessionMismatch):
                cpub.publish_flow_b(*args, {**fb._expect(plan), "source_manifest_digest": "f" * 64})
            with pytest.raises(cpub.PublishBlocked):
                cpub.publish_flow_b(*args, {**fb._expect(plan), "entity_counts": {**fb._expect(plan)["entity_counts"], "plati": 9}})
            seen.append(cloud_counts(env, b.cemetery_uid))

    rep = run_b(env, w, b, plan, code, hook=hook)
    assert seen == [{"rows": 0, "media": 0, "blobs": 0}] and rep["result"] == "completed"


# ================================================================== 6. REFUZURI (puncte 10)
def refused(env, w, b, decision):
    with pytest.raises(bs.BlockedFlow) as e:
        plan_b(env, w, b)
    assert e.value.decision == decision
    assert not q(env.cp.path, "SELECT 1 FROM bindings")
    return e.value


def test_refused_cloud_active_even_if_empty(env):
    w, b, code = b_world(env)
    env.cp.sim_set_status(b.cemetery_uid, "ACTIVE")
    refused(env, w, b, "BLOCKED_ACTIVE_EMPTY")


def test_refused_cloud_nonempty(env):
    w, b, code = b_world(env)
    env.cp.sim_put_row(b.cemetery_uid, "locuri_de_veci", str(uuid.uuid4()), {"cod_loc": "X"})
    with pytest.raises(bs.BlockedFlow) as e:
        plan_b(env, w, b)
    assert e.value.decision in ("BLOCK_DIFFERENT", "LINK_CANDIDATE", "C") and cloud_counts(env, b.cemetery_uid)["rows"] == 1


def test_refused_local_empty_and_metadata_only(env):
    w = World(env)
    b = w.local(w.org1)
    env.cp.create_cemetery(w.owner1, b.organization_uid, b.cemetery_uid, "C")
    assert pytest.raises(bs.BlockedFlow, plan_b, env, w, b).value.decision == "A"  # local gol + cloud gol = A, nu B
    make_outbox(b, 2)
    assert pytest.raises(bs.BlockedFlow, plan_b, env, w, b).value.decision == "REVIEW_METADATA_ONLY"
    assert not q(env.cp.path, "SELECT 1 FROM bootstrap_runs") and cem_state(env, b.cemetery_uid) == ("DRAFT", 1)


def test_refused_other_tenant_and_missing_capability(env):
    w, b, code = b_world(env)
    for actor in (w.owner2, env.cp.create_principal("nimeni")):
        with pytest.raises((cc.PermissionDenied, cc.ScopeError)):
            plan_b(env, w, b, actor=actor)
    plan = plan_b(env, w, b)
    with pytest.raises((cc.PermissionDenied, cc.ScopeError)):
        run_b(env, w, b, plan, code, actor=w.owner2)
    other = env.cp.issue_code(w.owner2, w.org2)
    with pytest.raises((cc.ScopeError, cc.CodeError)):
        env.cp.redeem_code(other, env.inst, "X", b.cemetery_uid, bootstrap_id=plan["bootstrap_id"])  # cod din altă organizație pe cimitirul org1
    with pytest.raises((cc.ScopeError, cc.PermissionDenied)):
        cs.staging_status(env.cp, w.owner2, b.organization_uid, b.cemetery_uid, plan["bootstrap_id"])
    assert cloud_counts(env, b.cemetery_uid)["rows"] == 0


def test_bootstrap_capability_is_distinct_and_not_owner_hardcoded(env, monkeypatch):
    w, b, code = b_world(env)
    adm, reb = env.cp.create_principal("adm"), env.cp.create_principal("reb")
    env.cp.grant_role(w.owner1, adm, w.org1, cc.ADMIN)
    env.cp.grant_role(w.owner1, reb, w.org1, cc.REBUILD)
    assert cc.PERMISSIONS["bootstrap"] == {cc.OWNER} and cc.PERMISSIONS["rebuild"] == {cc.OWNER, cc.REBUILD}  # capabilități distincte; Owner o are implicit
    for actor in (adm, reb):
        with pytest.raises(cc.PermissionDenied):
            plan_b(env, w, b, actor=actor)
    monkeypatch.setitem(cc.PERMISSIONS, "bootstrap", {cc.OWNER, cc.ADMIN})  # delegare doar prin tabel de permisiuni, nu prin cod
    plan = plan_b(env, w, b, actor=adm)
    rep = run_b(env, w, b, plan, code, actor=adm)
    assert rep["result"] == "completed"
    assert q(env.cp.path, "SELECT 1 FROM audit_log WHERE action='publish_flow_b' AND actor=?", adm)
    with pytest.raises(cc.PermissionDenied):
        env.cp._require(env.cp._c(), reb, "bootstrap", w.org1)


def test_refused_plan_expired_and_manifest_changed(env):
    w, b, code = b_world(env)
    plan = plan_b(env, w, b)
    env.clock.t += pf.PLAN_TTL + 5
    with pytest.raises(pf.PlanExpired):
        run_b(env, w, b, plan, code)
    assert bs.LocalRegistry(b).run_get(plan["bootstrap_id"])["state"] == "rolled_back" and not q(env.cp.path, "SELECT 1 FROM bindings") and len(outbox_rows(b)) == 3
    plan = plan_b(env, w, b)
    sql(b, "UPDATE locuri_de_veci SET sector='ALT' WHERE cod_loc='A-2'")  # sursa se schimbă după plan
    with pytest.raises(pf.PlanInvalidated):
        run_b(env, w, b, plan, code)
    plan = plan_b(env, w, b)
    env.cp.sim_set_status(b.cemetery_uid, "DRAFT", data_epoch=1)
    sql_cloud(env, "UPDATE cemeteries SET name='Redenumit'")  # nume nu intră în manifest; epoca da
    env.cp.sim_set_status(b.cemetery_uid, "DRAFT", data_epoch=2)
    with pytest.raises(pf.PlanInvalidated):
        run_b(env, w, b, plan, code)
    assert len(outbox_rows(b)) == 3 and not q(env.cp.path, "SELECT 1 FROM bindings")


def test_refused_concurrent_bootstrap_and_foreign_staging(env):
    w, b, code = b_world(env)
    plan = plan_b(env, w, b)
    with pytest.raises(bs.BlockedFlow) as e:  # al doilea bootstrap cât primul e `running`/planificat
        def hook(phase):
            if phase == "media:begin":
                plan_b(env, w, b)
        run_b(env, w, b, plan, code, hook=hook)
    assert e.value.decision == "INCOMPLETE_BOOTSTRAP" and bs.LocalRegistry(b).run_get(plan["bootstrap_id"])["state"] == "rolled_back"
    code2 = env.cp.issue_code(w.owner1, b.organization_uid, b.cemetery_uid)
    plan2 = plan_b(env, w, b)
    bs.acquire_lock(b, str(uuid.uuid4()))  # alt proces/bootstrap ține lock-ul
    try:
        with pytest.raises(cc.BootstrapLocked):
            run_b(env, w, b, plan2, code2)
    finally:
        bs.release_lock(b)
    # staging aparținând ALTUI bootstrap activ pe același cimitir
    foreign = str(uuid.uuid4())
    bs.LocalRegistry(b).run_update(plan2["bootstrap_id"], state="rolled_back")
    plan3 = plan_b(env, w, b)
    t = tr.start_transfer(b, env.cp, w.owner1, plan3, plan3["plan_hash"], foreign, clock=env.clock, batch_size=2)
    t.open_staging()
    code3 = env.cp.issue_code(w.owner1, b.organization_uid, b.cemetery_uid)
    bs.LocalRegistry(b).run_update(plan3["bootstrap_id"], state="rolled_back")
    plan4 = plan_b(env, w, b)
    with pytest.raises((tc.SessionMismatch, tc.TransferBlocked)):
        run_b(env, w, b, plan4, code3)
    assert cem_state(env, b.cemetery_uid) == ("DRAFT", 1) and cloud_counts(env, b.cemetery_uid)["rows"] == 0
    assert q(env.cp.path, "SELECT state FROM staging_sessions WHERE bootstrap_id=?", foreign) == [("open",)]  # sesiunea străină neatinsă


def test_refused_invalid_fk_in_source(env):
    w, b, code = b_world(env)
    c = sqlite3.connect(b.database)
    c.execute("PRAGMA foreign_keys=OFF")
    c.execute("UPDATE contracte SET id_loc=9999 WHERE numar_contract='C1'")
    c.commit()
    c.close()
    refused(env, w, b, "LOCAL_FK_INVALID")


# ================================================================== 7. ZERO SUPRASCRIERE + FĂRĂ SCURGERI ÎNTRE TENANȚI
def test_zero_unintended_overwrite_and_no_cross_tenant_leakage(env):
    w, b, code = b_world(env)
    other = w.local(w.org2, "Alt cimitir")
    env.cp.create_cemetery(w.owner2, other.organization_uid, other.cemetery_uid, "Altul")
    uid = str(uuid.uuid4())
    env.cp.sim_put_row(other.cemetery_uid, "locuri_de_veci", uid, {"cod_loc": "O-1"})
    env.cp.sim_put_media(other.cemetery_uid, "m-other", "x.jpg", 3, "a" * 64)
    before_other = {t_: q(env.cp.path, f"SELECT * FROM {t_} WHERE cemetery_uid=?", other.cemetery_uid) for t_ in ("registry_rows", "registry_row_data", "media_objects")}
    state_other = q(env.cp.path, "SELECT status, data_epoch FROM cemeteries WHERE uid=?", other.cemetery_uid)
    plan = plan_b(env, w, b)
    rep = run_b(env, w, b, plan, code)
    assert_completed(env, w, b, plan, rep)
    assert {t_: q(env.cp.path, f"SELECT * FROM {t_} WHERE cemetery_uid=?", other.cemetery_uid) for t_ in before_other} == before_other
    assert q(env.cp.path, "SELECT status, data_epoch FROM cemeteries WHERE uid=?", other.cemetery_uid) == state_other
    for t_, col in (("registry_rows", "cemetery_uid"), ("registry_row_data", "cemetery_uid"), ("media_objects", "cemetery_uid"), ("media_blobs", "cemetery_uid"),
                    ("media_published", "cemetery_uid"), ("bindings", "cemetery_uid")):
        assert not q(env.cp.path, f"SELECT 1 FROM {t_} WHERE {col} NOT IN (?, ?)", b.cemetery_uid, other.cemetery_uid)
    assert not q(env.cp.path, "SELECT 1 FROM bindings WHERE cemetery_uid=?", other.cemetery_uid)
    for fn in (lambda: env.cp.export_snapshot(w.owner2, w.org2, b.cemetery_uid), lambda: env.cp.export_snapshot(w.owner1, w.org1, other.cemetery_uid),
               lambda: cs.staging_snapshot(env.cp, w.owner2, w.org2, other.cemetery_uid, plan["bootstrap_id"])):
        try:
            res = fn()
            if isinstance(res, dict) and (res.get("exists") and (res.get("entities") or res.get("media"))):
                LEAKS.append(res)
        except (cc.ScopeError, cc.PermissionDenied):
            pass
    key = env.store.get(bs.LocalRegistry(b).get("cloud_secret_ref"))
    with pytest.raises(cc.ScopeError):
        env.cp.authenticate(key, other.cemetery_uid)  # cheia legăturii nu are voie pe alt cimitir
    assert LEAKS == []


# ================================================================== 8. LEGĂTURA bootstrap_id ↔ MAȘINA DE STĂRI + CUT LOCK + FOTO/CĂI ABSOLUTE
def test_bootstrap_run_linkage_states_and_phases(env):
    w, b, code = b_world(env)
    plan = plan_b(env, w, b)
    bid, loc, seen = plan["bootstrap_id"], bs.LocalRegistry(b), {}

    def hook(phase):
        run, crun = loc.run_get(bid), q(env.cp.path, "SELECT state, kind, plan_hash, binding_uid FROM bootstrap_runs WHERE bootstrap_id=?", bid)
        seen[phase] = (run["state"], loc.phase(bid), crun[0][0] if crun else None)

    rep = run_b(env, w, b, plan, code, hook=hook)
    assert seen["begin"][:2] == ("running", "begin") and seen["snapshot:before"][:2] == ("running", "snapshot") and seen["entities:begin"][:2] == ("running", "entities")
    assert seen["media:begin"][:2] == ("running", "media") and seen["verify:before"][:2] == ("verifying", "verify") and seen["publish:before"][:2] == ("verifying", "publish")
    assert seen["publish:in_txn"][2] == "verifying" and seen["publish:after_commit"][2] == "completed" and seen["finalize:before"][:2] == ("verifying", "finalize_local")
    assert seen["media:begin"][2] == "running"
    crun = q(env.cp.path, "SELECT kind, plan_hash, state, binding_uid, installation_uid FROM bootstrap_runs WHERE bootstrap_id=?", bid)[0]
    assert crun[:3] == ("B", plan["plan_hash"], "completed") and crun[3] == rep["binding_uid"] and crun[4] == env.inst
    assert loc.run_get(bid)["plan_hash"] == plan["plan_hash"] and loc.run_get(bid)["kind"] == "B"
    assert q(env.cp.path, "SELECT state FROM staging_sessions WHERE bootstrap_id=?", bid) == [("published",)]
    acts = [a[0] for a in q(env.cp.path, "SELECT action FROM audit_log WHERE cemetery_uid=? ORDER BY id", b.cemetery_uid)]
    assert acts.index("redeem_code") < acts.index("bootstrap_verifying") < acts.index("publish_flow_b")


def test_cut_holds_write_lock_so_snapshot_and_outbox_archive_are_atomic(env):
    w, b, code = b_world(env)
    plan = plan_b(env, w, b)
    got = {}

    def hook(phase):
        if phase == "snapshot:locked":
            writer = sqlite3.connect(b.database, timeout=0.3)
            with pytest.raises(sqlite3.OperationalError):
                writer.execute("INSERT INTO locuri_de_veci (cod_loc) VALUES ('RACE')")
            writer.close()
            got["read"] = mf.local_manifest(b)["content_digest"]  # MAIN rămâne citibil în timpul cutului

    run_b(env, w, b, plan, code, hook=hook)
    assert got["read"] == plan["local_manifest"]["content_digest"] and not sql(b, "SELECT 1 FROM locuri_de_veci WHERE cod_loc='RACE'")


def test_photos_absolute_paths_and_subfolders_are_snapshotted_and_published(env, tmp_path):
    w, b, code = b_world(env)
    (b.media / "poze").mkdir()
    (b.media / "poze" / "p1.jpg").write_bytes(b"PHOTO-ONE")
    outside = tmp_path / "extern" / "p2.jpg"
    outside.parent.mkdir()
    outside.write_bytes(b"PHOTO-TWO-ABS")
    sql(b, "INSERT INTO photos (cod_loc, file_path) VALUES ('A-1','poze/p1.jpg')")
    sql(b, "INSERT INTO photos (cod_loc, file_path) VALUES ('B-1', ?)", str(outside))
    plan = plan_b(env, w, b)
    assert plan["local_manifest"]["media"]["count"] == 5

    def hook(phase):
        if phase == "snapshot:after_commit":
            outside.write_bytes(b"CHANGED-AFTER-CUT")  # fișierul live se schimbă după snapshot: transferul folosește copia din snapshot

    rep = run_b(env, w, b, plan, code, hook=hook)
    assert rep["result"] == "completed" and cloud_counts(env, b.cemetery_uid) == {"rows": 11, "media": 5, "blobs": 4}
    assert q(env.cp.path, "SELECT sha256 FROM media_objects WHERE name='p2.jpg'")[0][0] == hashlib.sha256(b"PHOTO-TWO-ABS").hexdigest()
    assert set(ident.existing_media_identities(b)) == {f"document:{i}" for i in (1, 2, 3)} | {"photo:1", "photo:2"}
    assert not snap.snapshot_binding(b, plan["bootstrap_id"]).root.exists()


def test_cancel_planned_flow_b_and_blocked_resume_of_rolled_back_plan(env):
    w, b, code = b_world(env)
    plan = plan_b(env, w, b)
    fb.abort_flow_b(b, env.cp, env.store, w.owner1, plan["bootstrap_id"], env.inst)
    assert bs.LocalRegistry(b).run_get(plan["bootstrap_id"])["state"] == "rolled_back" and not q(env.cp.path, "SELECT 1 FROM bootstrap_runs") and len(outbox_rows(b)) == 3
    with pytest.raises(bs.PlanError):
        run_b(env, w, b, plan, code)
