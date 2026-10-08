"""cimitir_flow_b.py — Flux B: MAIN existent (NONEMPTY) → cloud nou (DRAFT + EMPTY), NUMAI pe simulatorul local (Etapa 4, Pasul 4).

preflight → plan_hash → bootstrap_run → snapshot/cut local → staging → verify → publish atomic → binding → finalize local.
Un singur punct de intrare reluabil: `execute_flow_b` se reapelează cu ACELAȘI plan după orice cădere; faza se deduce din starea durabilă (cut, staging, run-ul cloud),
iar `checkpoint.phase` din bootstrap_runs (snapshot|entities|media|verify|publish|finalize_local) este doar raportare. Rollback complet înainte de publish;
după COMMIT-ul cloud se merge doar ÎNAINTE (finalizare locală idempotentă). Fără HTTP, fără PostgreSQL/Supabase, fără Flux C/D/E, fără Sync V2.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Callable, Dict, Optional

import cimitir_bootstrap as bs
import cimitir_cloud_control as cc
import cimitir_cloud_media as cm
import cimitir_cloud_publish as cpub
import cimitir_cloud_staging as cs
import cimitir_entities as ents
import cimitir_identity as ident
import cimitir_manifest as mf
import cimitir_preflight as pf
import cimitir_snapshot as snap
import cimitir_transfer as tr
import cimitir_transfer_contract as tc
from cimitir_secretstore import SecretStore

PHASES = ("begin", "redeemed", "snapshot", "entities", "media", "verify", "publish", "finalize_local", "done")


class VerificationFailed(tc.TransferBlocked):
    pass


# ------------------------------------------------------------------ plan
def plan_flow_b(binding, cp, actor: str, inst: str, clock: Callable[[], float] = time.time) -> Dict[str, Any]:
    """Preflight + plan (doar citire + un rând local `planned`). Refuză tot ce nu e exact B: cloud ACTIVE/NONEMPTY, local EMPTY/METADATA_ONLY, alt tenant,
    bootstrap incomplet/concurent, FK invalid, media coruptă."""
    if not cp.can(actor, "bootstrap", binding.organization_uid):
        raise cc.PermissionDenied("bootstrap: capabilitate lipsă în această organizație")
    inc = bs.incomplete_bootstrap(binding)
    if inc:
        raise bs.BlockedFlow("INCOMPLETE_BOOTSTRAP", f"bootstrap incomplet {inc['bootstrap_id']} (faza {inc['phase']!r}): reia-l sau anulează-l")
    plan = pf.build_plan(binding, cp, actor, inst, clock=clock)
    lm = plan["local_manifest"]
    if plan["decision"] != "B":
        raise bs.BlockedFlow(plan["decision"], f"Flux B indisponibil: {plan['reason']}")
    if lm["integrity"]["orphan_total"] or lm["integrity"]["fk_check_violations"]:
        raise bs.BlockedFlow("LOCAL_FK_INVALID", "sursa are chei străine invalide; Flux B refuzat")
    if lm["media"]["mismatch"]:
        raise bs.BlockedFlow("MEDIA_CORRUPT", f"{lm['media']['mismatch']} fișiere media nu corespund SHA/mărimii înregistrate; Flux B refuzat")
    plan.update(bootstrap_id=str(uuid.uuid4()), kind="B", media_missing=[i["name"] for i in lm["media"]["items"] if not i["present"]])
    bs.LocalRegistry(binding).run_insert(plan["bootstrap_id"], "B", plan["plan_hash"])
    return plan


# ------------------------------------------------------------------ verificarea dinaintea publicării
def verify_before_publish(cp, actor: str, sb, plan: Dict[str, Any]) -> Dict[str, Any]:
    org, cem, bid = plan["organization_uid"], plan["cemetery_uid"], plan["bootstrap_id"]
    src = mf.build_manifest("local", mf.local_snapshot(sb))
    stg = mf.build_manifest("staging", cs.staging_snapshot(cp, actor, org, cem, bid))
    status = cs.staging_status(cp, actor, org, cem, bid)
    mrep = cm.media_verify(cp, actor, org, cem, bid)
    checks = {
        "staging_complete": bool(status["complete"]),
        "counts_per_entity": all(src["entities"][e]["count"] == stg["entities"][e]["count"] for e in ents.ORDER),
        "digest_per_entity": all(src["entities"][e]["digest"] == stg["entities"][e]["digest"] for e in ents.ORDER),
        "global_digest": src["content_digest"] == stg["content_digest"] == plan["local_manifest"]["content_digest"],
        "media_count": src["media"]["count"] == stg["media"]["count"],
        "media_bytes": src["media"]["bytes"] == stg["media"]["bytes"],
        "media_sha256": src["media"]["digest"] == stg["media"]["digest"] and mrep["ok"],
        "fk_orphans_zero": stg["integrity"]["orphan_total"] == 0 and src["integrity"]["orphan_total"] == 0 and not src["integrity"]["fk_check_violations"],
        "manifest_staging_eq_snapshot": src["content_digest"] == stg["content_digest"] and src["total_rows"] == stg["total_rows"],
    }
    rep = {"ok": all(checks.values()), "checks": checks, "media": mrep, "source_content_digest": src["content_digest"], "staging_content_digest": stg["content_digest"]}
    if not rep["ok"]:
        raise VerificationFailed(f"verificare înainte de publish eșuată: {[k for k, v in checks.items() if not v]}")
    return rep


def _expect(plan: Dict[str, Any]) -> Dict[str, Any]:
    lm = plan["local_manifest"]
    return {"plan_hash": plan["plan_hash"], "source_manifest_digest": lm["content_digest"], "entity_counts": {e: lm["entities"][e]["count"] for e in ents.ORDER},
            "media_count": lm["media"]["count"], "data_epoch": cpub.INITIAL_EPOCH}


def _cloud_completed(cp, bid: str) -> bool:
    try:
        return cp.get_run(bid)["state"] == "completed"
    except cc.ControlError:
        return False


# ------------------------------------------------------------------ execuție
def execute_flow_b(binding, cp, store: SecretStore, actor: str, plan: Dict[str, Any], confirm_hash: str, code: str, inst: str, cloud_url: str = "local://control-plane",
                   clock: Callable[[], float] = time.time, hook: Optional[Callable[[str], None]] = None, accept_missing_media: bool = False,
                   batch_size: int = tr.DEFAULT_BATCH_SIZE, chunk_size: int = tr.DEFAULT_CHUNK_SIZE) -> Dict[str, Any]:
    local = bs.LocalRegistry(binding)
    bid, org, cem = plan["bootstrap_id"], binding.organization_uid, binding.cemetery_uid
    run = local.run_get(bid)
    if run is None or run["plan_hash"] != plan["plan_hash"] or run["kind"] != "B":
        raise bs.PlanError("plan necunoscut local")
    if confirm_hash != plan["plan_hash"]:
        raise bs.PlanError("confirmarea nu corespunde planului (plan_hash)")
    if run["state"] == "completed":
        return json.loads(run["report"])
    if run["state"] in ("failed", "rolled_back"):
        raise bs.PlanError(f"planul este {run['state']}; creează unul nou")
    try:
        bs.acquire_lock(binding, bid)
    except cc.BootstrapLocked:
        if run["state"] == "planned":
            local.run_update(bid, state="rolled_back", error="refuzat: lock ținut de alt proces")
        raise
    fresh = run["state"] == "planned"
    try:
        known = local.get("cloud_secret_ref")
        if known and store.get(known) and hasattr(cp, "use_binding_key"):
            cp.use_binding_key(store.get(known))  # transport: reluarea se autentifică cu binding-ul deja confirmat, nu cu codul consumat
        if not cp.can(actor, "bootstrap", org):
            raise cc.PermissionDenied("bootstrap: capabilitate lipsă în această organizație")
        if plan.get("media_missing") and not accept_missing_media:
            raise bs.BlockedFlow("MEDIA_MISSING", f"{len(plan['media_missing'])} fișiere media declarate dar lipsă pe disc ({plan['media_missing'][:5]}): "
                                                  "Flux B cere confirmare explicită (accept_missing_media=True); nimic nu a fost modificat")
        if fresh:
            if clock() > plan["expires_at"]:
                raise pf.PlanExpired("planul a expirat; refă preflight-ul")
            pf.gate_execution(plan, binding, cp, actor, confirm_hash, clock, allowed=("B",))
    except BaseException as exc:
        if fresh and not (isinstance(exc, bs.BlockedFlow) and exc.decision == "MEDIA_MISSING"):
            local.run_update(bid, state="rolled_back", error=str(exc)[:500])
        bs.release_lock(binding)
        raise
    tick = hook or (lambda phase: None)
    key: Optional[str] = None
    try:
        local.run_update(bid, state="running", phase="begin")
        local.put(sync_state="BOOTSTRAPPING")
        tick("begin")
        ref = local.get("cloud_secret_ref")
        key = store.get(ref) if ref else None
        if key:
            cp.resume_run(key, bid, plan["plan_hash"])
        else:
            cp.begin_run(code, inst, bid, "B", cem, plan["plan_hash"])
        tick("after_begin")
        if not key:
            res = cp.redeem_code(code, inst, plan.get("device_name") or "MAIN", cem, plan.get("cemetery_name", ""), bootstrap_id=bid, pending=True)
            key = res["binding_key"]
            tick("after_redeem")
            store.set(bs.secret_name(res["binding_uid"]), key)
            tick("after_secret")
            local.put(cloud_binding_uid=res["binding_uid"], cloud_device_uid=res["device_uid"], cloud_url=cloud_url, cloud_secret_ref=bs.secret_name(res["binding_uid"]),
                      sync_epoch=res["sync_epoch"], last_change_seq=0)
            local.run_update(bid, phase="redeemed")
        tick("redeemed")
        transfer: Dict[str, Any] = {}
        if cp.get_run(bid)["state"] != "completed":
            transfer = _stage_verify_publish(binding, cp, actor, plan, key, local, tick, accept_missing_media, batch_size, chunk_size, clock)
        return _finalize_local(binding, cp, actor, plan, key, inst, local, tick, transfer, accept_missing_media)
    except bs.Crash:
        raise
    except BaseException as exc:
        if _cloud_completed(cp, bid):  # cloud publicat: nu se mai poate da înapoi; starea rămâne reluabilă (finalizare locală)
            bs.release_lock(binding)
            raise
        _abort_b(binding, cp, store, actor, bid, inst, f"{type(exc).__name__}: {exc}")
        raise


def _stage_verify_publish(binding, cp, actor, plan, key, local, tick, accept_missing, batch_size, chunk_size, clock) -> Dict[str, Any]:
    bid, org, cem = plan["bootstrap_id"], plan["organization_uid"], plan["cemetery_uid"]
    lm = plan["local_manifest"]
    local.run_update(bid, phase="snapshot")
    tick("snapshot:before")
    cut = snap.take_cut(binding, bid, lm["content_digest"], hook=tick)
    sb = snap.snapshot_binding(binding, bid)
    local.run_update(bid, phase="entities")
    t = tr.start_transfer(binding, cp, actor, plan, plan["plan_hash"], bid, batch_size=batch_size, chunk_size=chunk_size, clock=clock, source=sb)
    ident.reserve_media(binding, bid, t.state.mapping())
    tick("entities:begin")
    rep = t.run(hook=tick)
    local.run_update(bid, phase="media")
    tick("media:begin")
    up = t.upload_media(hook=tick)
    tick("media:done")
    local.run_update(bid, state="verifying", phase="verify")
    tick("verify:before")
    cp.mark_verifying(key, bid)
    cur = mf.cloud_manifest(cp, actor, org, cem)
    changed = mf.diff_manifests(plan["cloud_manifest"], cur)
    if changed:
        raise pf.PlanInvalidated("ținta cloud s-a schimbat după plan", [f"cloud:{x}" for x in changed])
    ver = verify_before_publish(cp, actor, sb, plan)
    tick("verify:after")
    local.run_update(bid, phase="publish")
    tick("publish:before")
    pub = cpub.publish_flow_b(cp, actor, org, cem, bid, key, _expect(plan), accept_missing, hook=tick)
    return {"cut": cut, "transfer": {k: rep[k] for k in ("stored", "skipped_already_staged")}, "media_upload": up, "verify": ver, "publish": pub}


def _finalize_local(binding, cp, actor, plan, key, inst, local, tick, transfer, accept_missing) -> Dict[str, Any]:
    bid, org, cem = plan["bootstrap_id"], plan["organization_uid"], plan["cemetery_uid"]
    lm = plan["local_manifest"]
    local.run_update(bid, state="verifying", phase="finalize_local")
    tick("finalize:before")
    auth = cp.authenticate(key, cem)
    cloud = mf.cloud_manifest(cp, actor, org, cem)
    if auth["sync_epoch"] != cpub.INITIAL_EPOCH or cloud["data_epoch"] != cpub.INITIAL_EPOCH or cloud["cemetery_status"] != "ACTIVE":
        raise bs.BootstrapError("epocă/stare cloud inconsistente după publicare")
    prom = tr.promote_transfer(binding, bid, True, hook=lambda p: tick("finalize:" + p))
    tick("finalize:after_promote")
    missing = lm["media"]["missing"]
    local.put(bound_installation_uid=inst, sync_state="BOUND_MEDIA_PENDING" if missing else "BOUND", sync_epoch=auth["sync_epoch"], last_change_seq=0)
    tick("finalize:after_bound")
    cut = snap.cut_info(binding, bid) or {}
    snap.finalize_cut(binding, bid)
    tr.release_local_state(binding, bid, True)
    tick("finalize:before_done")
    report = {"bootstrap_id": bid, "kind": "B", "plan_hash": plan["plan_hash"], "organization_uid": org, "cemetery_uid": cem, "binding_uid": auth["binding_uid"],
              "sync_epoch": auth["sync_epoch"], "entity_counts": {e: lm["entities"][e]["count"] for e in ents.ORDER}, "cloud_rows": cloud["total_rows"],
              "media": {"logical": lm["media"]["count"], "missing": missing, "bytes": lm["media"]["bytes"], "accepted_missing": bool(missing and accept_missing)},
              "source_content_digest": lm["content_digest"], "cloud_content_digest": cloud["content_digest"],
              "post_publish_verified": cloud["content_digest"] == lm["content_digest"], "outbox_archived": cut.get("outbox_archived"),
              "identity_promotion": {k: prom.get(k) for k in ("row_count", "already")}, "result": "completed"}
    local.run_update(bid, state="completed", phase="done", report=report)
    bs.release_lock(binding)
    return report


# ------------------------------------------------------------------ rollback / abort
def _abort_b(binding, cp, store: SecretStore, actor: str, bid: str, inst: str, error: str) -> None:
    """Rollback complet (înainte de publish): staging + bytes media cloud, legătura + secretul, outbox-ul arhivat, identități pending, snapshot, mapping, stare."""
    local = bs.LocalRegistry(binding)
    org, cem = binding.organization_uid, binding.cemetery_uid
    ref = local.get("cloud_secret_ref")
    if not ref:
        try:
            buid = cp.get_run(bid).get("binding_uid")
            ref = bs.secret_name(buid) if buid else ""
        except cc.ControlError:
            ref = ""
    key = store.get(ref) if ref else None
    try:
        cm.media_cleanup(cp, actor, org, cem, bid)
        cs.staging_cleanup(cp, actor, org, cem, bid)
    except (cc.ControlError, tc.TransferBlocked):
        pass
    try:
        if key:
            try:
                cp.rollback_run(key, bid)
            except cc.ConflictError:
                cp.revoke_by_key(key)
        else:
            cp.mark_failed_unauthenticated(bid, inst, error)
    except cc.ControlError:
        pass
    if ref:
        store.delete(ref)
    local.clear(*bs.BINDING_SETTINGS)
    snap.restore_outbox(binding, bid)
    ident.discard_pending_media(binding, bid)
    snap.remove_snapshot(binding, bid)
    tr.StateStore(binding, bid).delete()
    local.put(sync_state="UNBOUND")
    local.run_update(bid, state="rolled_back", error=error[:500])
    bs.release_lock(binding)


def abort_flow_b(binding, cp, store: SecretStore, actor: str, bootstrap_id: str, inst: str) -> None:
    """Rollback explicit al unui bootstrap B neterminat. Refuzat cât timp un proces viu îl rulează și după COMMIT-ul cloud (acolo se reia finalizarea)."""
    local = bs.LocalRegistry(binding)
    r = local.run_get(bootstrap_id)
    if not r or r["state"] not in ("planned", "running", "verifying"):
        return
    if r["state"] == "planned":
        bs.cancel_plan(binding, bootstrap_id)
        return
    bs.acquire_lock(binding, bootstrap_id)
    if _cloud_completed(cp, bootstrap_id):
        bs.release_lock(binding)
        raise bs.BootstrapError("cloud-ul este deja publicat pentru acest bootstrap: nu se mai poate anula, doar reia finalizarea locală (resume)")
    _abort_b(binding, cp, store, actor, bootstrap_id, inst, "abandonat de utilizator")
