"""cimitir_field_inbox.py — Field Inbox v1 (service layer): elemente din teren (telefon) care rămân PENDING în cloud până le aprobă operatorul MAIN.

Telefon -> HTTPS -> FastAPI (cimitir_field_inbox_http) -> acest strat -> PostgreSQL (cp_field_inbox_items, RLS) + Storage privat (FieldMediaStore, 6B).
Nu scrie în SQLite și nu importă nimic: ACCEPTED = „aprobat în cloud”; IMPORTED îl marchează MAIN DUPĂ importul local (etapă ulterioară).

Garanții:
- autorizare: cheia legăturii (binding) a dispozitivului; tenantul (organizație/cimitir) vine din legătură, niciodată din corpul cererii; RLS `cimitir_cp_app`.
- roluri: telefonul (device MOBILE) creează/atașează/vede DOAR itemurile proprii; accept/reject/imported și descărcarea fișierelor altora = doar MAIN (și DB-ul impune asta prin trigger).
- idempotență: (organizație, cimitir, idempotency_key) = același item; aceeași cheie cu conținut diferit = IdempotencyConflict.
- tranziții: PENDING->ACCEPTED|REJECTED, ACCEPTED->IMPORTED, compare-and-set în DB (UPDATE ... WHERE status=<de la>) + trigger; repetarea aceleiași tranziții = replay fără efect.
- fișiere: 0..N obiecte 6B (cp_storage_objects) legate prin cp_field_inbox_attachments; fără octeți în PostgreSQL; URL semnat <= 300 s, doar item PENDING/ACCEPTED și obiect READY.
- nu există DELETE remote: respingerea e doar schimbare de status.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import cimitir_cloud_control as cc
import cimitir_cloud_control_pg as ccp
import cimitir_field_storage as fs
import cimitir_storage as st

ITEM_TYPES = ("DECEASED", "WORK", "PERMIT", "DOCUMENT", "GRAVE_PHOTO", "WORK_PHOTO")
WORK_STAGES = ("BEFORE", "DURING", "AFTER")
STATUSES = ("PENDING", "ACCEPTED", "REJECTED", "IMPORTED")
TRANSITIONS = {"ACCEPTED": "PENDING", "REJECTED": "PENDING", "IMPORTED": "ACCEPTED"}
MAX_FILES_PER_ITEM = 20
URL_STATUSES = ("PENDING", "ACCEPTED")
NEEDS_FILE = ("DOCUMENT", "GRAVE_PHOTO", "WORK_PHOTO")
PHOTO_TYPES = ("GRAVE_PHOTO", "WORK_PHOTO")
# tip -> (câmpuri obligatorii, câmpuri opționale); CNP și date contractuale/financiare nu fac parte din Field Inbox v1
_FIELDS = {
    "DECEASED": (("nume",), ("prenume", "data_nasterii", "data_deces", "data_inhumare", "sex", "observatii")),
    "WORK": (("descriere",), ("data_inceput", "data_sfarsit", "executant", "observatii")),
    "PERMIT": (("nr_aviz",), ("data_aviz", "lucrare", "observatii")),
    "DOCUMENT": (("doc_type",), ("caption", "nr_evidenta")),
    "GRAVE_PHOTO": ((), ("caption",)),
    "WORK_PHOTO": ((), ("caption",)),
}
_LONG = ("descriere", "observatii", "lucrare")
_IDEM_RE = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
_REF_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_COLS = ("uid, organization_uid, cemetery_uid, item_type, work_stage, target_uid, grave_uid, payload, status, idempotency_key, request_hash, created_at, created_by, "
         "reviewed_at, reviewed_by, imported_at, imported_by, rejection_reason")


class FieldInboxError(Exception):
    pass


class InvalidItem(FieldInboxError):
    pass


class InvalidTransition(FieldInboxError):
    pass


class IdempotencyConflict(FieldInboxError):
    pass


def validate_item(item_type: str, payload: Any, grave_uid: Optional[str], target_uid: Optional[str], work_stage: Optional[str]) -> Dict[str, Any]:
    if item_type not in ITEM_TYPES:
        raise InvalidItem("item_type necunoscut")
    if (item_type == "WORK_PHOTO") != (work_stage is not None) or (work_stage is not None and work_stage not in WORK_STAGES):
        raise InvalidItem("work_stage (BEFORE/DURING/AFTER) este obligatoriu doar pentru WORK_PHOTO")
    for what, v in (("grave_uid", grave_uid), ("target_uid", target_uid)):
        if v is not None and not _REF_RE.match(str(v)):
            raise InvalidItem(f"{what} invalid")
    if not grave_uid and not target_uid:
        raise InvalidItem("grave_uid sau target_uid este obligatoriu")
    if not isinstance(payload, dict):
        raise InvalidItem("payload trebuie să fie obiect")
    req, opt = _FIELDS[item_type]
    extra = set(payload) - set(req) - set(opt)
    if extra:
        raise InvalidItem(f"câmpuri nepermise pentru {item_type}: {sorted(extra)}")
    clean: Dict[str, str] = {}
    for k in (*req, *opt):
        v = payload.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            if k in req:
                raise InvalidItem(f"câmp obligatoriu lipsă: {k}")
            continue
        if not isinstance(v, str) or len(v) > (2000 if k in _LONG else 200):
            raise InvalidItem(f"câmp invalid: {k}")
        clean[k] = v.strip()
    return clean


def request_hash(item_type: str, work_stage: Optional[str], target_uid: Optional[str], grave_uid: Optional[str], payload: Dict[str, Any]) -> str:
    body = json.dumps({"t": item_type, "s": work_stage, "target": target_uid, "grave": grave_uid, "p": payload}, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _iso(v: Any) -> Optional[str]:
    return v.isoformat() if isinstance(v, datetime) else (str(v) if v is not None else None)


def _public(r) -> Dict[str, Any]:
    return {"uid": r["uid"], "organization_uid": r["organization_uid"], "cemetery_uid": r["cemetery_uid"], "item_type": r["item_type"], "work_stage": r["work_stage"],
            "target_uid": r["target_uid"], "grave_uid": r["grave_uid"], "payload": json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"],
            "status": r["status"], "idempotency_key": r["idempotency_key"], "created_at": _iso(r["created_at"]), "created_by": r["created_by"],
            "reviewed_at": _iso(r["reviewed_at"]), "reviewed_by": r["reviewed_by"], "imported_at": _iso(r["imported_at"]), "imported_by": r["imported_by"],
            "rejection_reason": r["rejection_reason"]}


def _norm(v: str, what: str) -> str:
    try:
        return str(uuid.UUID(str(v)))
    except ValueError:
        raise InvalidItem(f"{what} invalid") from None


class FieldInboxService:
    def __init__(self, cp, media: fs.FieldMediaStore):
        for ref in ccp.PROTECTED_PROJECT_REFS:        # producția e blocată și pe partea de Storage (cp-ul își blochează singur URL-urile DB)
            if ref in str(getattr(media.storage, "base", "")):
                raise RuntimeError("proiectul de producție este blocat permanent pentru Field Inbox")
        self.cp, self.media = cp, media

    # ------------------------------------------------------------------ autorizare
    def authenticate(self, binding_key: str, cemetery_uid: Optional[str] = None) -> Dict[str, Any]:
        """Legătura activă (cheia) -> {binding_uid, organization_uid, cemetery_uid, device_type}. ScopeError dacă cheia/cimitirul nu corespund."""
        cem = _norm(cemetery_uid, "cemetery_uid") if cemetery_uid is not None else None
        a = self.cp.authenticate(binding_key, cem)
        c = self.cp._c()
        try:
            r = c.execute("SELECT device_type FROM devices WHERE device_uid=? AND organization_uid=?", (a["device_uid"], a["organization_uid"])).fetchone()
        finally:
            c.close()
        if not r:
            raise cc.ScopeError("legătură invalidă")
        return {"binding_uid": a["binding_uid"], "organization_uid": a["organization_uid"], "cemetery_uid": a["cemetery_uid"], "device_type": r["device_type"],
                "main": r["device_type"] == "MAIN"}

    def _need_main(self, a: Dict[str, Any]) -> None:
        if not a["main"]:
            raise cc.PermissionDenied("operația cere un dispozitiv MAIN")

    def _scope_sql(self, a: Dict[str, Any]) -> tuple:
        """Telefonul (non-MAIN) vede doar itemurile proprii; MAIN le vede pe toate din cimitirul legăturii (RLS limitează tenantul)."""
        return ("", ()) if a["main"] else (" AND created_by=?", (a["binding_uid"],))

    def _get(self, c, a: Dict[str, Any], item_uid: str, lock: str = ""):
        extra, p = self._scope_sql(a)
        r = c.execute(f"SELECT {_COLS} FROM cp_field_inbox_items WHERE cemetery_uid=? AND uid=?{extra}{lock}", (a["cemetery_uid"], item_uid, *p)).fetchone()
        if not r:
            raise cc.ScopeError("item inexistent")
        return r

    def _files(self, c, a: Dict[str, Any], item_uid: str) -> List[Dict[str, Any]]:
        rows = c.execute("SELECT o.uid, o.filename, o.mime, o.size, o.sha256, o.status, o.kind FROM cp_field_inbox_attachments x "
                         "JOIN cp_storage_objects o ON o.cemetery_uid=x.cemetery_uid AND o.uid=x.object_uid WHERE x.cemetery_uid=? AND x.item_uid=? "
                         "ORDER BY x.created_at, o.uid", (a["cemetery_uid"], item_uid)).fetchall()
        return [{k: r[k] for k in ("uid", "filename", "mime", "size", "sha256", "status", "kind")} for r in rows]

    # ------------------------------------------------------------------ creare (idempotentă)
    def create_item(self, binding_key: str, cemetery_uid: Optional[str], idempotency_key: str, item_type: str, payload: Dict[str, Any],
                    grave_uid: Optional[str] = None, target_uid: Optional[str] = None, work_stage: Optional[str] = None) -> Dict[str, Any]:
        if not isinstance(idempotency_key, str) or not _IDEM_RE.match(idempotency_key):
            raise InvalidItem("idempotency_key invalid (16-128 caractere [A-Za-z0-9._:-])")
        clean = validate_item(item_type, payload, grave_uid, target_uid, work_stage)
        h = request_hash(item_type, work_stage, target_uid, grave_uid, clean)
        a = self.authenticate(binding_key, cemetery_uid)
        c = self.cp._c()
        try:
            try:
                cur = c.execute(f"INSERT INTO cp_field_inbox_items (uid, organization_uid, cemetery_uid, item_type, work_stage, target_uid, grave_uid, payload, idempotency_key, "
                                f"request_hash, created_by) VALUES (?,?,?,?,?,?,?,?::jsonb,?,?,?) ON CONFLICT (organization_uid, cemetery_uid, idempotency_key) DO NOTHING "
                                f"RETURNING {_COLS}", (str(uuid.uuid4()), a["organization_uid"], a["cemetery_uid"], item_type, work_stage, target_uid, grave_uid,
                                                      json.dumps(clean, sort_keys=True), idempotency_key, h, a["binding_uid"]))
            except sqlite3.IntegrityError as exc:
                raise InvalidItem(f"item respins de baza de date: {exc}") from None
            row = cur.fetchone()
            if row:
                return {"item": _public(row), "replayed": False}
            row = c.execute(f"SELECT {_COLS} FROM cp_field_inbox_items WHERE organization_uid=? AND cemetery_uid=? AND idempotency_key=?",
                            (a["organization_uid"], a["cemetery_uid"], idempotency_key)).fetchone()
            if not row or (not a["main"] and row["created_by"] != a["binding_uid"]):
                raise IdempotencyConflict("idempotency_key deja folosit")
            if row["request_hash"] != h:
                raise IdempotencyConflict("același idempotency_key cu conținut diferit")
            return {"item": _public(row), "replayed": True}
        finally:
            c.close()

    # ------------------------------------------------------------------ fișiere (0..N / item) peste infrastructura 6B
    def attach_file(self, binding_key: str, cemetery_uid: Optional[str], item_uid: str, file_uid: str, filename: str, mime: str, data: bytes) -> Dict[str, Any]:
        item_uid, file_uid = _norm(item_uid, "item_uid"), _norm(file_uid, "file_uid")
        a = self.authenticate(binding_key, cemetery_uid)
        if mime not in fs.MAGIC:
            raise fs.BadContent("tip neacceptat (PDF/JPEG/PNG)")
        c = self.cp._c()
        try:
            it = self._get(c, a, item_uid)
            if it["status"] != "PENDING":
                raise InvalidTransition(f"itemul nu mai primește fișiere ({it['status']})")
            if it["item_type"] in PHOTO_TYPES and mime == "application/pdf":
                raise fs.BadContent("un item foto acceptă doar JPEG/PNG")
            known = c.execute("SELECT 1 FROM cp_field_inbox_attachments WHERE cemetery_uid=? AND item_uid=? AND object_uid=?", (a["cemetery_uid"], item_uid, file_uid)).fetchone()
            if not known and c.execute("SELECT count(*) AS n FROM cp_field_inbox_attachments WHERE cemetery_uid=? AND item_uid=?", (a["cemetery_uid"], item_uid)).fetchone()["n"] >= MAX_FILES_PER_ITEM:
                raise InvalidItem(f"maximum {MAX_FILES_PER_ITEM} fișiere per item")
        finally:
            c.close()
        kind = "document" if mime == "application/pdf" else "photo"
        self.media.begin_upload(binding_key, a["cemetery_uid"], file_uid, filename, mime, len(data), hashlib.sha256(data).hexdigest(), kind)
        self.media.upload(binding_key, a["cemetery_uid"], file_uid, data)
        c = self.cp._c()
        try:
            try:
                n = c.execute("INSERT INTO cp_field_inbox_attachments (cemetery_uid, organization_uid, item_uid, object_uid, created_by) VALUES (?,?,?,?,?) "
                              "ON CONFLICT (cemetery_uid, object_uid) DO NOTHING", (a["cemetery_uid"], a["organization_uid"], item_uid, file_uid, a["binding_uid"])).rowcount
            except sqlite3.IntegrityError as exc:
                self._drop_unlinked(binding_key, a, c, file_uid)
                raise InvalidTransition(f"atașare respinsă: {exc}") from None
            if n == 0:
                owner = c.execute("SELECT item_uid FROM cp_field_inbox_attachments WHERE cemetery_uid=? AND object_uid=?", (a["cemetery_uid"], file_uid)).fetchone()
                if not owner or owner["item_uid"] != item_uid:
                    raise fs.Conflict("fișierul aparține altui item")
            return {"item_uid": item_uid, "file": next(f for f in self._files(c, a, item_uid) if f["uid"] == file_uid), "replayed": n == 0}
        finally:
            c.close()

    def _drop_unlinked(self, binding_key: str, a: Dict[str, Any], c, file_uid: str) -> None:
        if c.execute("SELECT 1 FROM cp_field_inbox_attachments WHERE cemetery_uid=? AND object_uid=?", (a["cemetery_uid"], file_uid)).fetchone():
            return
        try:
            self.media.delete(binding_key, a["cemetery_uid"], file_uid)
        except (fs.FieldStorageError, st.StorageError, cc.ControlError):
            pass

    def file_url(self, binding_key: str, cemetery_uid: Optional[str], item_uid: str, file_uid: str, ttl: int = 60) -> Dict[str, Any]:
        item_uid, file_uid = _norm(item_uid, "item_uid"), _norm(file_uid, "file_uid")
        a = self.authenticate(binding_key, cemetery_uid)
        c = self.cp._c()
        try:
            it = self._get(c, a, item_uid)
            if not c.execute("SELECT 1 FROM cp_field_inbox_attachments WHERE cemetery_uid=? AND item_uid=? AND object_uid=?", (a["cemetery_uid"], item_uid, file_uid)).fetchone():
                raise cc.ScopeError("fișier inexistent")
            if it["status"] not in URL_STATUSES:
                raise fs.NotReady(f"itemul nu permite descărcarea ({it['status']})")
        finally:
            c.close()
        return self.media.download_url(binding_key, a["cemetery_uid"], file_uid, min(max(1, int(ttl)), fs.MAX_SIGNED_TTL))

    # ------------------------------------------------------------------ citire
    def list_items(self, binding_key: str, cemetery_uid: Optional[str], status: Optional[str] = None, item_type: Optional[str] = None,
                   limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        if status is not None and status not in STATUSES or item_type is not None and item_type not in ITEM_TYPES:
            raise InvalidItem("filtru invalid")
        a = self.authenticate(binding_key, cemetery_uid)
        extra, p = self._scope_sql(a)
        sql, args = f"SELECT {_COLS} FROM cp_field_inbox_items WHERE cemetery_uid=?{extra}", [a["cemetery_uid"], *p]
        for col, v in (("status", status), ("item_type", item_type)):
            if v:
                sql, args = sql + f" AND {col}=?", [*args, v]
        c = self.cp._c()
        try:
            rows = c.execute(sql + " ORDER BY created_at, uid LIMIT ? OFFSET ?", (*args, min(max(1, int(limit)), 200), max(0, int(offset)))).fetchall()
            return [_public(r) for r in rows]
        finally:
            c.close()

    def get_item(self, binding_key: str, cemetery_uid: Optional[str], item_uid: str) -> Dict[str, Any]:
        item_uid = _norm(item_uid, "item_uid")
        a = self.authenticate(binding_key, cemetery_uid)
        c = self.cp._c()
        try:
            item = _public(self._get(c, a, item_uid))
            item["files"] = self._files(c, a, item_uid)
            return item
        finally:
            c.close()

    # ------------------------------------------------------------------ accept / reject / imported (MAIN)
    def _transition(self, binding_key: str, cemetery_uid: Optional[str], item_uid: str, to: str, reason: Optional[str] = None) -> Dict[str, Any]:
        item_uid = _norm(item_uid, "item_uid")
        a = self.authenticate(binding_key, cemetery_uid)
        self._need_main(a)
        c = self.cp._c()
        try:
            c.execute("BEGIN")
            try:
                it = self._get(c, a, item_uid, " FOR UPDATE")
                if it["status"] == to:                                   # retry după răspuns pierdut: fără efect
                    c.execute("COMMIT")
                    return {"item": _public(it), "changed": False}
                if it["status"] != TRANSITIONS[to]:
                    raise InvalidTransition(f"tranziția {it['status']} -> {to} nu este permisă")
                files = self._files(c, a, item_uid)
                if to == "ACCEPTED" and ((it["item_type"] in NEEDS_FILE and not files) or any(f["status"] != "READY" for f in files)):
                    raise InvalidTransition("itemul cere cel puțin un fișier, toate READY")
                if to == "IMPORTED":
                    sets, args = "status='IMPORTED', imported_at=now(), imported_by=?", (a["binding_uid"],)
                elif to == "ACCEPTED":
                    sets, args = "status='ACCEPTED', reviewed_at=now(), reviewed_by=?", (a["binding_uid"],)
                else:
                    sets, args = "status='REJECTED', reviewed_at=now(), reviewed_by=?, rejection_reason=?", (a["binding_uid"], reason)
                n = c.execute(f"UPDATE cp_field_inbox_items SET {sets} WHERE cemetery_uid=? AND uid=? AND status=?", (*args, a["cemetery_uid"], item_uid, TRANSITIONS[to])).rowcount
                if n != 1:
                    raise InvalidTransition("starea itemului s-a schimbat între timp")
                if to == "IMPORTED":
                    c.execute("UPDATE cp_storage_objects SET status='IMPORTED', updated_at=now() WHERE cemetery_uid=? AND status='READY' AND uid IN "
                              "(SELECT object_uid FROM cp_field_inbox_attachments WHERE cemetery_uid=? AND item_uid=?)", (a["cemetery_uid"], a["cemetery_uid"], item_uid))
                row = self._get(c, a, item_uid)
                c.execute("COMMIT")
                return {"item": _public(row), "changed": True}
            except BaseException:
                if c.in_transaction:
                    c.execute("ROLLBACK")
                raise
        except sqlite3.IntegrityError as exc:
            raise InvalidTransition(f"tranziție respinsă de baza de date: {exc}") from None
        finally:
            c.close()

    def accept(self, binding_key: str, cemetery_uid: Optional[str], item_uid: str) -> Dict[str, Any]:
        return self._transition(binding_key, cemetery_uid, item_uid, "ACCEPTED")

    def reject(self, binding_key: str, cemetery_uid: Optional[str], item_uid: str, reason: str) -> Dict[str, Any]:
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise InvalidItem("motivul respingerii este obligatoriu (max 500 caractere)")
        return self._transition(binding_key, cemetery_uid, item_uid, "REJECTED", reason.strip())

    def mark_imported(self, binding_key: str, cemetery_uid: Optional[str], item_uid: str) -> Dict[str, Any]:
        return self._transition(binding_key, cemetery_uid, item_uid, "IMPORTED")


def build_service_from_env() -> FieldInboxService:
    """Serviciul real: planul de control PostgreSQL (CIMITIR_CP_DATABASE_URL) + Supabase Storage privat (SUPABASE_URL/SUPABASE_SECRET_KEY/SUPABASE_BUCKET) — doar din mediu."""
    cp = ccp.PostgresControlPlane()
    env = os.environ.get
    storage = st.SupabaseStorage(env("SUPABASE_URL", ""), env("SUPABASE_SECRET_KEY", "") or env("SUPABASE_SERVICE_ROLE_KEY", ""), env("SUPABASE_BUCKET", ""), env("SUPABASE_PREFIX", ""))
    return FieldInboxService(cp, fs.FieldMediaStore(cp, storage))
