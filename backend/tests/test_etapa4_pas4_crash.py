# ruff: noqa: F811
"""Etapa 4 / Pasul 4 — Flux B: procese REALE omorâte cu SIGKILL în fiecare fereastră de cădere (simulator local; fără Supabase/Render/producție)."""
from __future__ import annotations

import json
import uuid

import pytest

import cimitir_bootstrap as bs
import cimitir_flow_b as fb
import cimitir_identity as ident
import cimitir_manifest as mf
import cimitir_snapshot as snap
import cimitir_transfer as tr
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import finish, kill9, renv, spawn, wait_marker  # noqa: F401
from test_etapa4_pas35_hardening import live_dump
from test_etapa4_pas3_transfer import q
from test_etapa4_pas4_flux_b import assert_completed, b_world, cem_state, cloud_counts, outbox_rows

PRE_PUBLISH = ["snapshot:before", "snapshot:in_txn", "snapshot:after_commit", "after:locuri_de_veci:1", "media:after_chunk:1", "verify:after", "publish:in_txn"]
POST_COMMIT = ["publish:after_commit", "finalize:in_txn", "finalize:after_commit", "finalize:after_bound"]


def real_b(env):
    w, b, code = b_world(env)
    plan = fb.plan_flow_b(b, env.cp, w.owner1, env.inst)  # ceas real: procesul copil validează planul cu time.time
    f = env.tmp / f"bplan-{uuid.uuid4().hex[:6]}.json"
    f.write_text(json.dumps({"plan": plan, "code": code, "batch_size": 1, "chunk_size": 3, "accept_missing": False}), encoding="utf-8")
    return w, b, code, plan, f


def kw(w, plan, f):
    return dict(plan_file=f, actor=w.owner1, bootstrap_id=plan["bootstrap_id"])


def victim(env, w, b, plan, f, crash):
    p = spawn(env, "v", b, action="flow_b", crash_at=crash, **kw(w, plan, f))
    wait_marker(env, "v", crash.replace(":", "_"))
    kill9(p)
    return bs.incomplete_bootstrap(b)


def archive_count(b):
    return len(snap.archived_outbox(b, b.bootstrap_id)) if hasattr(b, "bootstrap_id") else None


@pytest.mark.parametrize("crash", PRE_PUBLISH + POST_COMMIT + ["after_redeem", "after_secret"])
def test_real_kill9_then_safe_resume_no_duplicates(renv, crash):
    env = renv
    w, b, code, plan, f = real_b(env)
    bid = plan["bootstrap_id"]
    inc = victim(env, w, b, plan, f, crash)
    assert inc and inc["bootstrap_id"] == bid and inc["holder_alive"] is False and inc["actions"] == ["resume", "rollback"] and not bs.lock_held(b)
    cut = snap.cut_info(b, bid)
    published = q(env.cp.path, "SELECT state FROM bootstrap_runs WHERE bootstrap_id=?", bid)[0][0] == "completed"
    if crash in ("snapshot:before", "snapshot:in_txn", "after_redeem", "after_secret"):  # cutul nu s-a confirmat: outbox intact, fără cut
        assert cut is None and len(outbox_rows(b)) == 3
    if crash in ("snapshot:after_commit", "after:locuri_de_veci:1", "media:after_chunk:1", "verify:after", "publish:in_txn"):
        assert cut["state"] == "active" and outbox_rows(b) == [] and len(snap.archived_outbox(b, bid)) == 3 and snap.snapshot_binding(b, bid).database.exists()
    if crash in PRE_PUBLISH:
        assert not published and cem_state(env, b.cemetery_uid) == ("DRAFT", 1) and cloud_counts(env, b.cemetery_uid)["rows"] == 0
    if crash in POST_COMMIT:
        assert published and cem_state(env, b.cemetery_uid) == ("ACTIVE", 1) and cloud_counts(env, b.cemetery_uid) == {"rows": 11, "media": 3, "blobs": 2}
        assert bs.LocalRegistry(b).run_get(bid)["state"] != "completed" and bs.LocalRegistry(b).sync_state in ("BOOTSTRAPPING", "BOUND")
    if crash == "publish:after_commit":
        assert ident.existing_identities(b) == {} and not ident.promotion_status(b, bid)["promoted"]
    if crash == "finalize:in_txn":  # promovare întreruptă în tranzacție => nimic parțial
        assert ident.existing_identities(b) == {} and ident.existing_media_identities(b) == {} and not ident.promotion_status(b, bid)["promoted"]
    if crash in ("finalize:after_commit", "finalize:after_bound"):
        assert ident.promotion_status(b, bid)["promoted"] and len(ident.existing_identities(b)) == 11 and len(ident.existing_media_identities(b)) == 3
    ref = bs.LocalRegistry(b).get("cloud_secret_ref")
    key_before = env.store.get(ref) if ref else None
    buid_before = q(env.cp.path, "SELECT binding_uid FROM bindings")
    dump_published = live_dump(env) if published else None
    code2, r = finish(spawn(env, "r", b, action="flow_b", **kw(w, plan, f)))
    assert code2 == 0 and r["report"]["result"] == "completed", r
    assert_completed(env, w, b, plan, r["report"])
    assert q(env.cp.path, "SELECT binding_uid FROM bindings")[:1] == buid_before[:1] or not buid_before  # același binding, fără al doilea
    assert q(env.cp.path, "SELECT COUNT(*) FROM bindings")[0][0] == 1 and q(env.cp.path, "SELECT COUNT(*) FROM registry_rows")[0][0] == 11
    assert q(env.cp.path, "SELECT COUNT(*) FROM media_objects")[0][0] == 3 and q(env.cp.path, "SELECT COUNT(*) FROM media_published")[0][0] == 3
    if key_before:
        assert env.store.get(bs.LocalRegistry(b).get("cloud_secret_ref")) == key_before or crash in ("after_redeem",)  # cheia recuperată din SecretStore, nu regenerată
    if dump_published is not None:  # cloud deja publicat: finalizarea locală nu mai scrie nimic în cloud
        after = live_dump(env)
        assert {k: v for k, v in after.items() if k not in ("audit_log",)} == {k: v for k, v in dump_published.items() if k not in ("audit_log",)}
    assert len(snap.archived_outbox(b, bid)) == 3 and outbox_rows(b) == []
    again = finish(spawn(env, "r2", b, action="flow_b", **kw(w, plan, f)))
    assert again[0] == 0 and again[1]["report"] == r["report"]  # idempotent
    assert bs.incomplete_bootstrap(b) is None and not (b.root / "bootstrap_state" / f"{bid}.sqlite").exists()


@pytest.mark.parametrize("crash", PRE_PUBLISH)
def test_real_kill9_then_safe_rollback_then_retry(renv, crash):
    env = renv
    w, b, code, plan, f = real_b(env)
    bid = plan["bootstrap_id"]
    original = outbox_rows(b)
    assert victim(env, w, b, plan, f, crash)["bootstrap_id"] == bid
    ref = bs.LocalRegistry(b).get("cloud_secret_ref")
    code2, r = finish(spawn(env, "a", b, action="flow_b_abort", **kw(w, plan, f)))
    assert code2 == 0, r
    loc = bs.LocalRegistry(b)
    assert loc.run_get(bid)["state"] == "rolled_back" and loc.sync_state == "UNBOUND" and loc.get("cloud_secret_ref") == "" and bs.incomplete_bootstrap(b) is None
    assert outbox_rows(b) == original  # nimic pierdut, ID-uri originale
    assert cem_state(env, b.cemetery_uid) == ("DRAFT", 1) and cloud_counts(env, b.cemetery_uid)["rows"] == 0
    assert q(env.cp.path, "SELECT status FROM bindings") == [("revoked",)] and (not ref or env.store.get(ref) is None)
    assert q(env.cp.path, "SELECT state FROM bootstrap_runs") == [("rolled_back",)]
    assert not snap.snapshot_binding(b, bid).root.exists() and not tr.StateStore(b, bid).exists() and not ident.media_identity_rows(b, bid)
    assert not q(env.cp.path, "SELECT 1 FROM staging_rows") and not q(env.cp.path, "SELECT 1 FROM staging_media_chunks") and not q(env.cp.path, "SELECT 1 FROM media_blobs")
    code3 = env.cp.issue_code(w.owner1, b.organization_uid, b.cemetery_uid)
    plan2 = fb.plan_flow_b(b, env.cp, w.owner1, env.inst)
    rep = fb.execute_flow_b(b, env.cp, env.store, w.owner1, plan2, plan2["plan_hash"], code3, env.inst, batch_size=2, chunk_size=3)
    assert rep["result"] == "completed" and cem_state(env, b.cemetery_uid) == ("ACTIVE", 1)
    assert mf.cloud_manifest(env.cp, w.owner1, b.organization_uid, b.cemetery_uid)["content_digest"] == plan2["local_manifest"]["content_digest"]


@pytest.mark.parametrize("crash", POST_COMMIT)
def test_after_cloud_commit_rollback_is_refused_and_resume_finalizes(renv, crash):
    env = renv
    w, b, code, plan, f = real_b(env)
    bid = plan["bootstrap_id"]
    victim(env, w, b, plan, f, crash)
    code2, r = finish(spawn(env, "a", b, action="flow_b_abort", **kw(w, plan, f)))
    assert code2 == 3 and r["kind"] == "blocked" and "publicat" in r["error"]  # nu se poate da înapoi după COMMIT-ul cloud
    assert cem_state(env, b.cemetery_uid) == ("ACTIVE", 1) and cloud_counts(env, b.cemetery_uid)["rows"] == 11
    assert q(env.cp.path, "SELECT status FROM bindings") == [("active",)] and bs.LocalRegistry(b).get("cloud_secret_ref")
    code3, r3 = finish(spawn(env, "r", b, action="flow_b", **kw(w, plan, f)))
    assert code3 == 0 and r3["report"]["post_publish_verified"] is True
    assert_completed(env, w, b, plan, r3["report"])


def test_parallel_second_process_is_refused_while_first_is_alive(renv):
    env = renv
    w, b, code, plan, f = real_b(env)
    p = spawn(env, "v", b, action="flow_b", crash_at="verify:after", **kw(w, plan, f))
    wait_marker(env, "v", "verify_after")
    code2, r = finish(spawn(env, "second", b, action="flow_b", **kw(w, plan, f)))  # același plan, proces viu ține lock-ul
    assert code2 == 3 and r["kind"] == "locked"
    code3, r3 = finish(spawn(env, "ab", b, action="flow_b_abort", **kw(w, plan, f)))
    assert code3 == 3 and r3["kind"] == "locked"  # rollback refuzat cât rulează un proces viu
    kill9(p)
    assert cem_state(env, b.cemetery_uid) == ("DRAFT", 1) and bs.incomplete_bootstrap(b)["holder_alive"] is False
    assert finish(spawn(env, "r", b, action="flow_b", **kw(w, plan, f)))[0] == 0
