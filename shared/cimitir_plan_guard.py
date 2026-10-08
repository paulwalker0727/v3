"""cimitir_plan_guard.py — validarea planului PE SERVER (Etapa 4, Pasul 5.1). Serverul nu crede MAIN și nici un client care îl ocolește.

Se recalculează pe server: plan_hash (din preimaginea trimisă), expirarea (pe ceasul serverului), bootstrap_id (UUID), tenant/scope, starea run-ului,
drift-ul țintei (starea cloud curentă == cea din plan) și al sursei (digestul sursei, legat criptografic de plan, înregistrat la begin și cerut la staging/publish),
plus compatibilitatea fluxului (A: sursă + țintă goale; B: sursă nevidă, țintă DRAFT/inexistentă + goală). Capabilitatea BOOTSTRAP rămâne verificată la autentificare.
Toate funcțiile sunt DOAR CITIRE și rulează înaintea oricărei scrieri: un refuz nu lasă nicio urmă în baza cloud.
HTTP: 400 PLAN_INVALID · 410 PLAN_EXPIRED · 409 TARGET_DRIFT / PLAN_BLOCKED / SOURCE_DRIFT.
Limită: conținutul local al MAIN nu e vizibil serverului; sursa e ancorată prin manifestul local semnat de plan_hash și verificată la publish (staging == sursă).
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Optional

import cimitir_cloud_control as cc
import cimitir_manifest as mf
import cimitir_preflight as pf
import cimitir_transfer_contract as tc

PLAN_TTL = cc.PLAN_TTL
MAX_CLOCK_SKEW = 60
EMPTY_SOURCE_DIGEST = mf.sha([])
TRANSFER_WRITES = ("staging_open", "staging_put_batch", "media_put_chunk", "media_finalize")


class PlanRejected(cc.ControlError):
    """400 PLAN_INVALID: plan lipsă, incomplet, plan_hash ≠ preimagine, bootstrap_id/tip invalid."""


class PlanExpired(PlanRejected):
    """410 PLAN_EXPIRED: planul a expirat pe ceasul serverului."""


class TargetDrift(PlanRejected):
    """409 TARGET_DRIFT: ținta cloud diferă de cea din plan."""


class PlanBlocked(PlanRejected):
    """409 PLAN_BLOCKED: planul e consistent, dar fluxul nu e permis pentru această stare."""


def claim_for(plan: Dict[str, Any]) -> Dict[str, Any]:
    """MAIN: preimaginea plan_hash + fereastra de valabilitate (+ manifestul sursei pentru B), trimise serverului la begin_run."""
    if plan["kind"] == "A":
        return {"kind": "A", "fields": plan["fields"], "created_at": plan["created_at"], "expires_at": plan["expires_at"]}
    lm = plan["local_manifest"]
    return {"kind": "B", "fields": pf._plan_hash_fields(plan["decision"], lm, plan["cloud_manifest"], plan["installation_uid"]), "local_manifest": lm,
            "created_at": plan["created_at"], "expires_at": plan["expires_at"]}


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _uuid(v: Any) -> bool:
    try:
        return str(uuid.UUID(str(v))) == str(v)
    except ValueError:
        return False


def meta(run: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return json.loads(run.get("checkpoint") or "{}")
    except ValueError:
        return {}


def check_not_expired(cp, run: Dict[str, Any]) -> None:
    """Un run `planned` (nimic executat încă) expiră pe ceasul serverului; după pairing transferul poate dura oricât."""
    if run["state"] == "planned" and cp.clock() > meta(run).get("plan_expires_at", run["created_at"] + PLAN_TTL):
        raise PlanExpired("planul a expirat pe server; refă preflight-ul")


def _check_source(kind: str, f: Dict[str, Any], claim: Dict[str, Any]) -> Optional[str]:
    if kind == "A":
        sd = f.get("source_digest")
        if not isinstance(sd, dict) or not sd or any(v != EMPTY_SOURCE_DIGEST for v in sd.values()):
            raise PlanBlocked("Flux A cere sursa locală goală")
        return None
    lm = claim.get("local_manifest")
    if not isinstance(lm, dict) or mf.stable_digest(lm) != f.get("local") or lm.get("stable_digest") != f.get("local"):
        raise PlanRejected("manifestul sursei nu corespunde planului")
    if lm.get("organization_uid") != f["organization_uid"] or lm.get("cemetery_uid") != f["cemetery_uid"]:
        raise PlanRejected("manifestul sursei aparține altui scope")
    if lm.get("classification") != mf.NONEMPTY:
        raise PlanBlocked("Flux B cere sursa locală nevidă")
    return lm["content_digest"]


def _check_target(cp, c, kind: str, f: Dict[str, Any], actor: str, org: str, cem: str) -> None:
    if kind == "A":
        st = cp._state_of(c, cem)
        if {k: st[k] for k in ("exists", "status", "rows", "tombstones")} != f.get("target") or (st["data_epoch"] or 1) != f.get("epoch"):
            raise TargetDrift("ținta cloud s-a schimbat după plan")
        if st["status"] not in (None, "DRAFT") or st["rows"] or st["tombstones"]:
            raise PlanBlocked(f"Flux A nu este permis pentru ținta {st['status']}/{st['classification']}")
        return
    m = mf.cloud_manifest(cp, actor, org, cem)
    if m["stable_digest"] != f.get("cloud"):
        raise TargetDrift("ținta cloud s-a schimbat după plan")
    if m["classification"] != mf.EMPTY or m["cemetery_status"] not in (None, "DRAFT"):
        raise PlanBlocked(f"Flux B nu este permis pentru ținta {m['cemetery_status']}/{m['classification']}")


def check_begin(cp, c, code_row, inst: str, bid: str, kind: str, cem: str, plan_hash: str, claim: Any,
                existing: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """În tranzacția begin_run, ÎNAINTE de orice scriere. Întoarce metadatele de înregistrat pentru un run nou (None la reluare idempotentă)."""
    if not _uuid(bid):
        raise PlanRejected("bootstrap_id invalid (UUID)")
    if kind not in ("A", "B"):
        raise PlanRejected("tip de bootstrap necunoscut")
    if not isinstance(claim, dict) or claim.get("kind") != kind or not isinstance(claim.get("fields"), dict) \
            or not _num(claim.get("created_at")) or not _num(claim.get("expires_at")):
        raise PlanRejected("planul lipsește sau este incomplet")
    f, org = claim["fields"], code_row["organization_uid"]
    if mf.sha(f) != plan_hash:
        raise PlanRejected("plan_hash nu corespunde planului")
    if f.get("organization_uid") != org or f.get("cemetery_uid") != cem:
        raise cc.ScopeError("planul nu aparține acestui scope")
    if f.get("installation_uid") != inst:
        raise PlanRejected("planul aparține altei instalări")
    if (f.get("kind") if kind == "A" else f.get("decision")) != kind:
        raise PlanRejected("decizia planului nu corespunde fluxului cerut")
    source = _check_source(kind, f, claim)
    if existing:
        if existing["plan_hash"] != plan_hash or existing["cemetery_uid"] != cem or existing["kind"] != kind or existing["installation_uid"] != inst:
            raise cc.ConflictError("bootstrap_id existent cu alt plan")
        if existing["state"] in ("failed", "rolled_back"):
            raise cc.ConflictError(f"bootstrap {existing['state']}")
        if existing["state"] == "planned":  # nimic executat încă: expirarea și ținta se reverifică pe server
            check_not_expired(cp, existing)
            _check_target(cp, c, kind, f, code_row["issued_by"], org, cem)
        return None
    now = cp.clock()
    if now > claim["expires_at"]:
        raise PlanExpired("planul a expirat; refă preflight-ul")
    if claim["expires_at"] - claim["created_at"] > PLAN_TTL + 1 or claim["created_at"] > now + MAX_CLOCK_SKEW:
        raise PlanRejected("fereastra de valabilitate a planului este invalidă")
    _check_target(cp, c, kind, f, code_row["issued_by"], org, cem)
    return {"source_content_digest": source, "plan_expires_at": min(claim["expires_at"], now + PLAN_TTL)}


def check_pairing(cp, run: Optional[Dict[str, Any]], code_row, inst: str, cem: str, pending: bool) -> None:
    if not run or run["organization_uid"] != code_row["organization_uid"] or run["cemetery_uid"] != cem:
        raise cc.ScopeError("bootstrap inexistent")
    if run["installation_uid"] != inst:
        raise PlanRejected("bootstrap-ul aparține altei instalări")
    if run["state"] not in ("planned", "running"):
        raise cc.ConflictError(f"bootstrap {run['state']}")
    if (run["kind"] == "B") != bool(pending):
        raise PlanRejected("Flux B cere binding pending, Flux A binding activ")
    check_not_expired(cp, run)


def check_complete(cp, c, run: Dict[str, Any]) -> None:
    if run["kind"] != "A":
        raise cc.ConflictError("Flux B se finalizează doar prin publish atomic")
    st = cp._state_of(c, run["cemetery_uid"])
    if st["rows"] or st["tombstones"]:
        raise TargetDrift("ținta cloud nu mai este goală")


def check_transfer(cp, binding_uid: str, org: str, cem: str, op: str, rest: list) -> None:
    """Operațiile de transfer (staging/media/publish) cer un run B real, al acestui binding, în starea potrivită, legat de același plan și aceeași sursă."""
    contract = rest[0] if op == "staging_put_batch" else None
    bid = contract.get("bootstrap_id") if isinstance(contract, dict) else rest[0]
    run = cp.get_run(bid)
    if run["organization_uid"] != org or run["cemetery_uid"] != cem or run["binding_uid"] != binding_uid:
        raise cc.ScopeError("bootstrap inexistent în scope")
    if run["kind"] != "B":
        raise PlanRejected("transferul există doar pentru Flux B")
    if op in TRANSFER_WRITES and run["state"] not in ("running", "verifying"):
        raise cc.ConflictError(f"bootstrap {run['state']}")
    declared = {"staging_open": rest[1] if op == "staging_open" else None, "staging_put_batch": contract,
                "publish_flow_b": rest[2] if op == "publish_flow_b" else None}.get(op)
    if declared is None or run["state"] == "completed":
        return
    if not isinstance(declared, dict) or declared.get("plan_hash") != run["plan_hash"]:
        raise PlanRejected("plan_hash diferit de cel al bootstrap-ului")
    src = meta(run).get("source_content_digest")
    if src and declared.get("source_manifest_digest") != src:
        raise tc.SourceDrift("sursa declarată diferă de cea din plan")
