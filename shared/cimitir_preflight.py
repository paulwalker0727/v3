"""cimitir_preflight.py — preflight comun (Etapa 4, Pasul 2): clasificare EMPTY/METADATA_ONLY/NONEMPTY, matricea de decizie, plan legat de manifeste,
invalidarea planului la orice schimbare a sursei sau a țintei.

STRICT READ-ONLY: nu creează binding, nu schimbă epoca, nu atinge outbox/date, nu scrie `installation_uid` (se primește ca argument).
Nu execută niciun flux: doar RECOMANDĂ (B/C/LINK); `executable` este adevărat numai pentru Flux A (executat de `cimitir_bootstrap`, Pasul 1).
Fluxurile B–E vor apela `gate_execution` înainte de orice scriere.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Dict, Optional

import cimitir_manifest as mf

PLAN_TTL = 600
BOUND_STATES = ("BOUND", "BOUND_MEDIA_PENDING")
DECISIONS = ("A", "B", "C", "LINK_CANDIDATE", "BLOCK_DIFFERENT", "BLOCKED_ACTIVE_EMPTY", "BLOCKED_ARCHIVED", "BLOCKED_ALARM", "BLOCKED_INCOMPLETE",
             "REVIEW_METADATA_ONLY", "ALREADY_BOUND", "REBUILD")


class PreflightError(Exception):
    pass


class PlanInvalidated(PreflightError):
    def __init__(self, message: str, changed: list):
        super().__init__(message)
        self.changed = changed


class PlanExpired(PreflightError):
    pass


class FlowNotImplemented(PreflightError):
    pass


def decide(local: Dict[str, Any], cloud: Dict[str, Any]) -> Dict[str, str]:
    """Matricea aprobată (§1.4 + decizia ACTIVE+gol). Ordinea regulilor contează: primele sunt cele mai restrictive."""
    lc, cc_ = local["classification"], cloud["classification"]
    state, status = local.get("sync_state") or "UNBOUND", cloud.get("cemetery_status")

    def out(d: str, why: str) -> Dict[str, str]:
        return {"decision": d, "reason": why}

    if cloud["exists"] and status == "ARCHIVED":
        return out("BLOCKED_ARCHIVED", "cimitirul este arhivat în cloud (UID-ul nu se reutilizează)")
    if state == "REBUILD_REQUIRED":
        return out("REBUILD", "registrul cere rebuild (D/E explicit, neimplementat)")
    if state in BOUND_STATES:
        if lc != mf.EMPTY and cc_ == mf.EMPTY:
            return out("BLOCKED_ALARM", "registrul e legat, local are date, iar cloud-ul pare gol: posibilă ștergere accidentală")
        return out("ALREADY_BOUND", "registrul este deja legat")
    if state == "BOOTSTRAPPING":
        return out("BLOCKED_INCOMPLETE", "un bootstrap incomplet există: reia-l sau anulează-l (Pasul 1)")
    if cloud["exists"] and status == "ACTIVE" and cc_ == mf.EMPTY:
        return out("BLOCKED_ACTIVE_EMPTY", "cloud ACTIVE dar gol: nu este „nou” (posibilă ștergere în cloud); regula așteaptă aprobare (§13.2)")
    if mf.METADATA_ONLY in (lc, cc_):
        return out("REVIEW_METADATA_ONLY", "o parte nu are date, dar are rămășițe (outbox/conflicte/tombstone-uri): se revizuiește manual, fără flux automat")
    if lc == mf.EMPTY and cc_ == mf.EMPTY:
        return out("A", "local gol + cloud gol/DRAFT/inexistent → Flux A permis")
    if lc == mf.NONEMPTY and cc_ == mf.EMPTY:
        return out("B", "local nevid + cloud gol → se recomandă Flux B (neexecutat)")
    if lc == mf.EMPTY and cc_ == mf.NONEMPTY:
        return out("C", "local gol + cloud nevid → se recomandă Flux C (neexecutat)")
    if local["content_digest"] == cloud["content_digest"]:
        return out("LINK_CANDIDATE", "ambele nevide, conținut identic → candidat LINK (neexecutat)")
    return out("BLOCK_DIFFERENT", "ambele nevide cu conținut diferit: fără merge automat; doar D/E explicit, ulterior")


def _plan_hash_fields(decision: str, local: Dict[str, Any], cloud: Dict[str, Any], inst: str) -> Dict[str, Any]:
    return {"decision": decision, "organization_uid": local["organization_uid"], "cemetery_uid": local["cemetery_uid"],
            "local": local["stable_digest"], "cloud": cloud["stable_digest"], "installation_uid": inst,
            "versions": [local["manifest_version"], local["protocol_version"], local["schema_version"], cloud["schema_version"]]}


def build_plan(binding, cp, actor: str, installation_uid: str, clock: Callable[[], float] = time.time) -> Dict[str, Any]:
    """Generează manifestele ambelor părți (citire), decide și păstrează manifestele în plan; `plan_hash` le leagă."""
    local = mf.local_manifest(binding)
    cloud = mf.cloud_manifest(cp, actor, binding.organization_uid, binding.cemetery_uid)
    d = decide(local, cloud)
    flow = {"A": "A", "B": "B", "C": "C", "LINK_CANDIDATE": "LINK"}.get(d["decision"])
    return {"plan_id": str(uuid.uuid4()), "kind": "preflight", "decision": d["decision"], "reason": d["reason"], "recommended_flow": flow,
            "executable": d["decision"] == "A", "organization_uid": binding.organization_uid, "cemetery_uid": binding.cemetery_uid,
            "installation_uid": installation_uid, "local_manifest": local, "cloud_manifest": cloud,
            "plan_hash": mf.sha(_plan_hash_fields(d["decision"], local, cloud, installation_uid)),
            "created_at": clock(), "expires_at": clock() + PLAN_TTL}


def validate_plan(plan: Dict[str, Any], binding, cp, actor: str, clock: Callable[[], float] = time.time) -> None:
    """Regenerează manifestele (citire) și le compară cu cele din plan. Orice diferență → `PlanInvalidated`: planul NU se mai poate executa."""
    if clock() > plan["expires_at"]:
        raise PlanExpired("planul a expirat; generează unul nou")
    local = mf.local_manifest(binding)
    cloud = mf.cloud_manifest(cp, actor, binding.organization_uid, binding.cemetery_uid)
    changed = [f"local:{x}" for x in mf.diff_manifests(plan["local_manifest"], local)] + [f"cloud:{x}" for x in mf.diff_manifests(plan["cloud_manifest"], cloud)]
    fresh = mf.sha(_plan_hash_fields(plan["decision"], local, cloud, plan["installation_uid"]))
    if changed or fresh != plan["plan_hash"]:
        raise PlanInvalidated("sursa sau ținta s-au schimbat după generarea planului; generează un plan nou", changed or ["plan_hash"])


def gate_execution(plan: Dict[str, Any], binding, cp, actor: str, confirm_hash: str, clock: Callable[[], float] = time.time,
                   allowed: Optional[tuple] = None) -> None:
    """Poarta pe care o apelează orice flux ÎNAINTE de a scrie: confirmare = plan_hash, plan valid, flux implementat.
    `allowed` (Pasul 3): decizii acceptate de infrastructura de transfer (ex. ("B",)) în locul regulii „doar Flux A”."""
    if confirm_hash != plan["plan_hash"]:
        raise PreflightError("confirmarea nu corespunde planului (plan_hash)")
    validate_plan(plan, binding, cp, actor, clock)
    if allowed is not None:
        if plan["decision"] not in allowed:
            raise FlowNotImplemented(f"decizia {plan['decision']} nu este acceptată de acest transfer (permise: {allowed})")
        return
    if not plan["executable"]:
        raise FlowNotImplemented(f"decizia {plan['decision']} nu este executabilă în Pasul 2 (doar Flux A, prin Pasul 1)")
