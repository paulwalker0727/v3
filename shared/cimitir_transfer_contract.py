"""cimitir_transfer_contract.py — contractul canonic de transfer MAIN → Cloud (Etapa 4, Pasul 3): câmpuri, hash de batch, erori de BLOCARE.

Un batch = (bootstrap_id, entity_type, batch_no). Idempotent: același batch cu același `batch_sha256` = no-op; același batch cu alt SHA = BLOCK.
`batch_sha256` = SHA-256 pe JSON canonic al rândurilor sortate după uid ({"uid","version","data"}).
"""
from __future__ import annotations

from typing import Any, Dict, List

import cimitir_entities as ents
import cimitir_manifest as mf

CONTRACT_VERSION = 1
MEDIA = "media"
ENTITY_ORDER: List[str] = list(ents.ORDER) + [MEDIA]  # părinții înaintea copiilor; media la urmă (metadate, fără upload)
FIELDS = ("bootstrap_id", "plan_hash", "organization_uid", "cemetery_uid", "entity_type", "batch_no", "batch_count", "batch_sha256",
          "source_manifest_digest", "schema_version", "data_epoch")
SESSION_FIELDS = ("plan_hash", "organization_uid", "cemetery_uid", "source_manifest_digest", "schema_version", "data_epoch")


class TransferBlocked(Exception):
    """Orice încălcare a contractului: transferul se oprește (BLOCK); nimic nu se scrie în live."""


class ContractError(TransferBlocked):
    pass


class CorruptBatch(TransferBlocked):
    pass


class ConflictingBatch(TransferBlocked):
    pass


class OrderViolation(TransferBlocked):
    pass


class OrphanFK(TransferBlocked):
    pass


class SessionMismatch(TransferBlocked):
    """plan_hash / manifest sursă / schemă / epocă / cimitir diferite de cele cu care s-a deschis staging-ul."""


class SourceDrift(TransferBlocked):
    pass


def batch_sha256(rows: List[Dict[str, Any]]) -> str:
    return mf.sha(sorted(rows, key=lambda r: r["uid"]))


def make_contract(session: Dict[str, Any], bootstrap_id: str, entity_type: str, batch_no: int, batch_count: int, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    c = {"contract_version": CONTRACT_VERSION, "bootstrap_id": bootstrap_id, "entity_type": entity_type, "batch_no": batch_no, "batch_count": batch_count,
         "batch_sha256": batch_sha256(rows), "row_count": len(rows)}
    c.update({k: session[k] for k in SESSION_FIELDS})
    return c


def check_shape(c: Dict[str, Any], rows: List[Dict[str, Any]]) -> None:
    missing = [f for f in FIELDS if c.get(f) in (None, "")]
    if missing:
        raise ContractError(f"contract incomplet: lipsesc {missing}")
    if c.get("contract_version") != CONTRACT_VERSION:
        raise ContractError("versiune de contract necunoscută")
    if c["entity_type"] not in ENTITY_ORDER:
        raise ContractError(f"entity_type necunoscut: {c['entity_type']!r}")
    if not (isinstance(c["batch_no"], int) and isinstance(c["batch_count"], int) and 1 <= c["batch_no"] <= c["batch_count"]):
        raise ContractError("batch_no/batch_count invalide")
    if c.get("row_count") != len(rows):
        raise CorruptBatch("numărul de rânduri nu corespunde contractului")
    for r in rows:
        if not isinstance(r.get("uid"), str) or not r["uid"] or not isinstance(r.get("data"), dict):
            raise ContractError("rând invalid (uid/data)")
    if len({r["uid"] for r in rows}) != len(rows):
        raise ContractError("uid duplicat în același batch")
    if batch_sha256(rows) != c["batch_sha256"]:
        raise CorruptBatch("batch_sha256 nu corespunde conținutului (batch corupt)")
