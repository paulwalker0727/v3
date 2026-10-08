# ruff: noqa: F811
"""Etapa 4 / Pasul 4 — tester independent, teste ADVERSARIALE suplimentare pentru Flux B.

Nu modifică nimic în /app/v2/shared/. Doar expunere la cazuri de abuz / cross-tenant / imposibilitate de dubluri.
"""
from __future__ import annotations

import re
import sqlite3
import uuid
from pathlib import Path

import pytest

import cimitir_bootstrap as bs
import cimitir_cloud_control as cc
import cimitir_cloud_media as cm
import cimitir_cloud_publish as cpub
import cimitir_cloud_staging as cs
import cimitir_flow_b as fb
import cimitir_identity as ident
import cimitir_manifest as mf
import cimitir_snapshot as snap
import cimitir_transfer as tr
import cimitir_transfer_contract as tc
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas2_manifest import DATA, add_file, put_local, sql
from test_etapa4_pas3_transfer import q
from test_etapa4_pas4_flux_b import (
    assert_completed,
    b_world,
    cem_state,
    cloud_counts,
    outbox_rows,
    plan_b,
    run_b,
    sql_cloud,
)


ROOT = Path(__file__).resolve().parents[2]


# ========================= (verificare import-uri) =========================
def test_no_production_imports_in_flux_b_modules():
    """shared/cimitir_flow_b.py & co. nu importă supabase/psycopg/fastapi/HTTP clients."""
    files = ["cimitir_flow_b.py", "cimitir_snapshot.py", "cimitir_cloud_publish.py",
             "cimitir_cloud_media.py", "cimitir_identity.py", "cimitir_transfer.py", "cimitir_manifest.py"]
    forbidden = re.compile(
        r"^\s*(import|from)\s+(supabase|psycopg|fastapi|starlette|requests|httpx|aiohttp|urllib\.request|socket)\b",
        re.M,
    )
    url_forbidden = re.compile(r"supabase\.co|onrender\.com|postgres(ql)?://")
    for name in files:
        src = (ROOT / "shared" / name).read_text(encoding="utf-8")
        m = forbidden.search(src)
        assert not m, f"{name}: import interzis: {m.group(0)}"
        m2 = url_forbidden.search(src)
        assert not m2, f"{name}: URL producție interzis: {m2.group(0)}"


# ========================= (3) binding străin la publish =========================
def test_publish_rejects_binding_key_of_other_cemetery(env):
    """Cheia de binding a altui cimitir nu publică bootstrap-ul curent."""
    w, b, code = b_world(env)
    # Alt cimitir (același owner, altă legătură)
    other = w.local(w.org1, "Altul")
    env.cp.create_cemetery(w.owner1, w.org1, other.cemetery_uid, "Altul")
    other_code = env.cp.issue_code(w.owner1, w.org1, other.cemetery_uid)
    other_res = env.cp.redeem_code(other_code, env.inst, "ALT", other.cemetery_uid)
    other_key = other_res["binding_key"]

    plan = plan_b(env, w, b)
    seen = {}

    def hook(phase):
        if phase == "publish:before":
            with pytest.raises((cc.ScopeError, cpub.PublishBlocked)):
                cpub.publish_flow_b(env.cp, w.owner1, b.organization_uid, b.cemetery_uid,
                                    plan["bootstrap_id"], other_key, fb._expect(plan))
            seen["ok"] = cloud_counts(env, b.cemetery_uid)

    rep = run_b(env, w, b, plan, code, hook=hook)
    assert seen["ok"] == {"rows": 0, "media": 0, "blobs": 0}
    assert rep["result"] == "completed"


# ========================= (3b) binding key cu bootstrap_id eronat =========================
def test_publish_with_valid_key_but_foreign_bootstrap_id_is_scope_error(env):
    w, b, code = b_world(env)
    plan = plan_b(env, w, b)
    seen = {}

    def hook(phase):
        if phase == "publish:before":
            key = env.store.get(bs.LocalRegistry(b).get("cloud_secret_ref"))
            with pytest.raises((cc.ScopeError, cc.ConflictError, cpub.PublishBlocked)):
                cpub.publish_flow_b(env.cp, w.owner1, b.organization_uid, b.cemetery_uid,
                                    str(uuid.uuid4()), key, fb._expect(plan))
            seen["ok"] = True

    run_b(env, w, b, plan, code, hook=hook)
    assert seen.get("ok")


# ========================= (8) cross-tenant: owner2 nu poate acționa pe staging/media owner1 =========================
def test_cross_tenant_cannot_publish_staging_cleanup_or_media_cleanup(env):
    w, b, code = b_world(env)
    plan = plan_b(env, w, b)
    hits = []

    def hook(phase):
        if phase == "verify:before":
            # owner2 încearcă publish, staging_cleanup, media_cleanup pe staging-ul lui owner1
            key = env.store.get(bs.LocalRegistry(b).get("cloud_secret_ref"))
            for fn in (
                lambda: cpub.publish_flow_b(env.cp, w.owner2, b.organization_uid, b.cemetery_uid,
                                            plan["bootstrap_id"], key, fb._expect(plan)),
                lambda: cs.staging_cleanup(env.cp, w.owner2, b.organization_uid, b.cemetery_uid, plan["bootstrap_id"]),
                lambda: cm.media_cleanup(env.cp, w.owner2, b.organization_uid, b.cemetery_uid, plan["bootstrap_id"]),
                lambda: cs.staging_snapshot(env.cp, w.owner2, b.organization_uid, b.cemetery_uid, plan["bootstrap_id"]),
            ):
                try:
                    fn()
                    hits.append("LEAK")
                except (cc.PermissionDenied, cc.ScopeError, tc.TransferBlocked):
                    pass

    rep = run_b(env, w, b, plan, code, hook=hook)
    assert hits == [] and rep["result"] == "completed"


# ========================= (7) staging_cleanup după publish NU șterge datele publicate =========================
def test_staging_cleanup_or_media_cleanup_after_publish_do_not_touch_live_data(env):
    w, b, code = b_world(env)
    plan = plan_b(env, w, b)
    rep = run_b(env, w, b, plan, code)
    before = {
        "rows": cloud_counts(env, b.cemetery_uid),
        "state": cem_state(env, b.cemetery_uid),
        "media_published": q(env.cp.path, "SELECT COUNT(*) FROM media_published WHERE cemetery_uid=?", b.cemetery_uid)[0][0],
        "blobs": q(env.cp.path, "SELECT COUNT(*) FROM media_blobs WHERE cemetery_uid=?", b.cemetery_uid)[0][0],
    }
    # încercări explicite de cleanup "orfan" după publish
    for fn in (
        lambda: cs.staging_cleanup(env.cp, w.owner1, b.organization_uid, b.cemetery_uid, plan["bootstrap_id"]),
        lambda: cm.media_cleanup(env.cp, w.owner1, b.organization_uid, b.cemetery_uid, plan["bootstrap_id"]),
    ):
        try:
            fn()
        except (cc.ControlError, tc.TransferBlocked):
            pass
    after = {
        "rows": cloud_counts(env, b.cemetery_uid),
        "state": cem_state(env, b.cemetery_uid),
        "media_published": q(env.cp.path, "SELECT COUNT(*) FROM media_published WHERE cemetery_uid=?", b.cemetery_uid)[0][0],
        "blobs": q(env.cp.path, "SELECT COUNT(*) FROM media_blobs WHERE cemetery_uid=?", b.cemetery_uid)[0][0],
    }
    assert before == after and rep["result"] == "completed"


# ========================= (5) conflict de identitate media activă existentă =========================
def test_media_identity_conflict_blocks_second_bootstrap_with_different_uid(env):
    """Dacă pentru același local_ref există deja un cloud_uid activ, un bootstrap nou cu alt UID NU poate suprascrie."""
    w, b, code = b_world(env)
    plan1 = plan_b(env, w, b)
    rep = run_b(env, w, b, plan1, code)
    assert_completed(env, w, b, plan1, rep)
    active_before = ident.existing_media_identities(b)
    assert len(active_before) == 3
    # încercăm reserve_media cu un UID DIFERIT pentru același local_ref → IdentityConflict
    bad_mapping = {(tc.MEDIA, k): str(uuid.uuid4()) for k in active_before}  # alt UID
    with pytest.raises(ident.IdentityConflict):
        ident.reserve_media(b, str(uuid.uuid4()), bad_mapping)
    # identitățile active rămân intacte
    assert ident.existing_media_identities(b) == active_before


# ========================= (2) restore_outbox cu tabel outbox fără AUTOINCREMENT =========================
def test_restore_outbox_handles_id_reuse_when_outbox_lacks_autoincrement(env):
    """Dacă tabelul outbox nu are AUTOINCREMENT, SQLite poate reutiliza ID-uri. restore_outbox trebuie să nu piardă rânduri."""
    w, b, code = b_world(env, outbox=3)
    plan = plan_b(env, w, b)
    original_outbox = outbox_rows(b)
    assert len(original_outbox) == 3

    def hook(phase):
        if phase == "snapshot:after_commit":
            # Reconstruim tabelul outbox FĂRĂ AUTOINCREMENT ca să permită reutilizarea de ID-uri.
            c = sqlite3.connect(b.database)
            cols = [r[1] for r in c.execute("PRAGMA table_info(cloud_sync_outbox)")]
            c.execute("ALTER TABLE cloud_sync_outbox RENAME TO cloud_sync_outbox_old")
            # recreăm fără AUTOINCREMENT, aceleași coloane
            c.execute(
                "CREATE TABLE cloud_sync_outbox (id INTEGER PRIMARY KEY, tracking_code TEXT, status TEXT, note TEXT, "
                "created_at TEXT, op_id TEXT, kind TEXT, payload TEXT, attempts INTEGER DEFAULT 0, last_error TEXT, "
                "entity TEXT, entity_uid TEXT, action TEXT, base_version INTEGER DEFAULT 0, state TEXT DEFAULT 'pending', conflict TEXT)"
            )
            # inserăm o intrare „nouă” după cut (ca orice modificare post-snapshot) cu ID posibil = 1
            c.execute(
                "INSERT INTO cloud_sync_outbox (kind, entity, entity_uid, action, payload, state) "
                "VALUES ('entity','locuri_de_veci','after-cut','upsert','{}','pending')"
            )
            c.commit()
            c.close()
        if phase == "media:begin":
            raise RuntimeError("fail-pentru-rollback")

    with pytest.raises(RuntimeError):
        run_b(env, w, b, plan, code, hook=hook)

    rows = outbox_rows(b)
    # după rollback, cele 3 arhivate trebuie să fi fost readuse + rândul "after-cut" să rămână; total ≥ 4
    uids = [r[1] for r in rows]
    for r in original_outbox:
        assert r[1] in uids, f"outbox restaurat a pierdut uid={r[1]}"
    assert "after-cut" in uids
    # fără dubluri
    assert len(uids) == len(set(uids))


# ========================= (9) după commit cloud: rollback refuzat, finalize re-rulat idempotent =========================
def test_after_cloud_commit_rollback_refused_and_resume_idempotent(env):
    w, b, code = b_world(env)
    plan = plan_b(env, w, b)
    bid = plan["bootstrap_id"]

    # oprim între publish:after_commit și finalize:in_txn
    def hook(phase):
        if phase == "publish:after_commit":
            raise bs.Crash("oprit post-commit")

    with pytest.raises(bs.Crash):
        run_b(env, w, b, plan, code, hook=hook)

    # cloud e deja publicat
    assert cem_state(env, b.cemetery_uid) == ("ACTIVE", 1) and cloud_counts(env, b.cemetery_uid)["rows"] == 11
    # abort_flow_b trebuie refuzat
    with pytest.raises(bs.BootstrapError, match="publicat"):
        fb.abort_flow_b(b, env.cp, env.store, w.owner1, bid, env.inst)
    # finalizarea locală trebuie să fie idempotentă la reluare
    rep1 = run_b(env, w, b, plan, code)
    rep2 = run_b(env, w, b, plan, code)
    assert rep1 == rep2 and rep1["result"] == "completed"
    assert_completed(env, w, b, plan, rep1)


# ========================= (4b) resume după crash fără secret în SecretStore =========================
def test_resume_after_pre_redeem_crash_without_secret_in_store_proceeds_cleanly(env):
    """Dacă a crăpat ÎNAINTE de redeem (nu există cheie în SecretStore), resume refolosește codul inițial."""
    w, b, code = b_world(env)
    plan = plan_b(env, w, b)

    def hook(phase):
        if phase == "after_begin":
            raise bs.Crash("înainte de redeem")

    with pytest.raises(bs.Crash):
        run_b(env, w, b, plan, code, hook=hook)
    # cheia NU există în SecretStore
    ref = bs.LocalRegistry(b).get("cloud_secret_ref")
    assert not ref or env.store.get(ref) is None
    # resume cu același cod funcționează
    rep = run_b(env, w, b, plan, code)
    assert rep["result"] == "completed"


# ========================= (10) atomicitate publish la excepție în mijlocul INSERT-urilor =========================
def test_publish_atomic_exception_mid_insert_leaves_nothing_partial(env, monkeypatch):
    """Un monkeypatch pe cm.assemble care aruncă la mijlocul INSERT-urilor media => NIMIC publicat."""
    w, b, code = b_world(env)
    plan = plan_b(env, w, b)

    orig_assemble = cm.assemble
    calls = {"n": 0}

    def bad_assemble(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] >= 2:  # al doilea blob fizic => eșuează la mijloc
            raise RuntimeError("boom-în-publish-media")
        return orig_assemble(*args, **kwargs)

    monkeypatch.setattr(cm, "assemble", bad_assemble)

    with pytest.raises(Exception):
        run_b(env, w, b, plan, code)

    # cloud rămâne gol/DRAFT; nimic publicat parțial
    assert cem_state(env, b.cemetery_uid) == ("DRAFT", 1)
    assert cloud_counts(env, b.cemetery_uid) == {"rows": 0, "media": 0, "blobs": 0}
    assert not q(env.cp.path, "SELECT 1 FROM media_published")


# ========================= (6b) staging_media_chunks: bucată în afara planului (uid necunoscut) =========================
def test_media_put_chunk_rejects_unknown_media_uid(env):
    w, b, code = b_world(env)
    plan = plan_b(env, w, b)
    bid = plan["bootstrap_id"]

    seen = {}

    def hook(phase):
        if phase == "media:done":
            # încercăm să trimitem o bucată pentru un media_uid necunoscut pe staging-ul curent
            fake_uid = str(uuid.uuid4())
            with pytest.raises((tc.TransferBlocked, cc.ControlError, Exception)):
                cm.media_put_chunk(env.cp, w.owner1, b.organization_uid, b.cemetery_uid, bid,
                                   fake_uid, 1, b"XXX",
                                   __import__("hashlib").sha256(b"XXX").hexdigest())
            seen["ok"] = True

    rep = run_b(env, w, b, plan, code, hook=hook)
    assert seen.get("ok") and rep["result"] == "completed"


# ========================= (1b) modificare concurentă la MAIN în timpul cutului — writer refuzat =========================
def test_concurrent_writer_during_cut_is_locked_out(env):
    """Writer paralel pe MAIN primește OperationalError pe durata cutului; după cut, scrisul merge și se găsește în noul outbox."""
    w, b, code = b_world(env)
    plan = plan_b(env, w, b)

    def hook(phase):
        if phase == "snapshot:locked":
            # un alt proces încearcă un INSERT; trebuie să eșueze
            writer = sqlite3.connect(b.database, timeout=0.3)
            with pytest.raises(sqlite3.OperationalError):
                writer.execute("INSERT INTO locuri_de_veci (cod_loc) VALUES ('CONCURRENT')")
            writer.close()
        if phase == "snapshot:after_commit":
            # după cut, scrisul concurent merge și NU afectează snapshotul
            c = sqlite3.connect(b.database)
            c.execute("INSERT INTO locuri_de_veci (cod_loc) VALUES ('AFTER-CUT')")
            c.commit()
            c.close()

    rep = run_b(env, w, b, plan, code, hook=hook)
    assert rep["result"] == "completed"
    # modificarea de după cut nu a ajuns în cloud
    assert not q(env.cp.path, "SELECT 1 FROM registry_row_data WHERE content LIKE '%AFTER-CUT%'")
    # modificarea de după cut e în MAIN (păstrată)
    assert sql(b, "SELECT 1 FROM locuri_de_veci WHERE cod_loc='AFTER-CUT'")


# ========================= cloud = ACTIVE între plan și publish → refuz =========================
def test_target_turns_active_between_plan_and_publish_refuses_cleanly(env):
    """Dacă între plan și publish cineva aduce cloud-ul ACTIVE, publish refuză curat; nimic publicat pe acest bootstrap."""
    w, b, code = b_world(env)
    plan = plan_b(env, w, b)

    def hook(phase):
        if phase == "publish:before":
            env.cp.sim_set_status(b.cemetery_uid, "ACTIVE")

    with pytest.raises((cpub.PublishBlocked, fb.VerificationFailed, bs.BlockedFlow, cc.ConflictError, Exception)):
        run_b(env, w, b, plan, code, hook=hook)
    # verificăm că nu s-au publicat rânduri de la acest bootstrap
    assert not q(env.cp.path, "SELECT 1 FROM registry_rows WHERE cemetery_uid=?", b.cemetery_uid)
