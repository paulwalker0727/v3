"""cimitir_cloud_wire.py — contractul HTTP v1 (Etapa 4, Pasul 5): căi, tip de autentificare, serializare, coduri de eroare stabile. Partajat de server și client.

Autentificare (NICIODATĂ în URL/log): `Authorization: Pairing <cod>` (provizionare/pairing, ÎNAINTE de binding) sau `Authorization: Binding <cheie>` (după redeem).
"""
from __future__ import annotations

import base64
from typing import Any, Dict, Tuple

import cimitir_cloud_control as cc
import cimitir_cloud_media as cm
import cimitir_cloud_publish as cpub
import cimitir_transfer_contract as tc

API = "/api/v1"
# op -> (cale, autentificare): pairing | binding | either
OPS: Dict[str, Tuple[str, str]] = {
    "can": ("/capabilities/check", "either"), "export_snapshot": ("/cloud/inspect", "either"), "preflight_for_code": ("/bootstrap/preflight", "pairing"),
    "begin_run": ("/bootstrap/runs", "pairing"), "redeem_code": ("/bootstrap/pair", "pairing"), "mark_failed_unauthenticated": ("/bootstrap/abandon", "pairing"),
    "get_run": ("/bootstrap/status", "either"), "resume_run": ("/bootstrap/resume", "binding"), "mark_verifying": ("/bootstrap/verifying", "binding"),
    "verify_empty": ("/bootstrap/verify-empty", "binding"), "complete_run": ("/bootstrap/complete", "binding"), "rollback_run": ("/bootstrap/rollback", "binding"),
    "revoke_by_key": ("/bootstrap/revoke", "binding"), "authenticate": ("/auth/binding", "binding"),
    "staging_open": ("/staging/open", "binding"), "staging_status": ("/staging/status", "binding"), "staging_put_batch": ("/staging/batches", "binding"),
    "staging_snapshot": ("/staging/snapshot", "binding"), "staging_cleanup": ("/staging/cleanup", "binding"),
    "media_put_chunk": ("/media/chunks", "binding"), "media_finalize": ("/media/finalize", "binding"), "media_verify": ("/media/verify", "binding"),
    "media_cleanup": ("/media/cleanup", "binding"), "publish_flow_b": ("/bootstrap/publish", "binding"),
}


class Unauthenticated(cc.ScopeError):
    """401: autentificare lipsă/invalidă."""


# (clasă, status, cod) — subclasele ÎNAINTEA claselor de bază
ERRORS = [
    (Unauthenticated, 401, "UNAUTHENTICATED"), (cc.ScopeError, 404, "SCOPE_MISMATCH"), (cc.PermissionDenied, 403, "FORBIDDEN"), (cc.CodeError, 401, "INVALID_CODE"),
    (cc.BootstrapLocked, 409, "BOOTSTRAP_IN_PROGRESS"), (cc.ConflictError, 409, "CONFLICT"),
    (tc.ConflictingBatch, 409, "BATCH_CONFLICT"), (tc.SourceDrift, 409, "SOURCE_DRIFT"), (tc.SessionMismatch, 409, "SESSION_MISMATCH"),
    (tc.OrderViolation, 409, "ORDER_VIOLATION"), (tc.OrphanFK, 422, "ORPHAN_FK"), (cm.MediaCorrupt, 400, "MEDIA_CORRUPT"), (tc.CorruptBatch, 400, "BATCH_CORRUPT"),
    (tc.ContractError, 400, "CONTRACT_INVALID"), (cm.MediaMissingBlocked, 409, "MEDIA_MISSING"), (cm.MediaNotVerified, 409, "MEDIA_NOT_VERIFIED"),
    (cpub.PublishBlocked, 409, "PUBLISH_BLOCKED"), (tc.TransferBlocked, 409, "TRANSFER_BLOCKED"), (cc.ControlError, 400, "REQUEST_INVALID"),
]
CODE_TO_CLASS = {code: cls for cls, _s, code in ERRORS}


def encode(obj: Any) -> Any:
    if isinstance(obj, (bytes, bytearray)):
        return {"__b64__": base64.b64encode(bytes(obj)).decode("ascii")}
    if isinstance(obj, dict):
        return {k: encode(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [encode(v) for v in obj]
    return obj


def decode(obj: Any) -> Any:
    if isinstance(obj, dict):
        if set(obj) == {"__b64__"}:
            return base64.b64decode(obj["__b64__"])
        return {k: decode(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [decode(v) for v in obj]
    return obj
