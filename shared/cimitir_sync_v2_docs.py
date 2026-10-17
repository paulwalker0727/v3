"""cimitir_sync_v2_docs.py — documente și fotografii MAIN <-> Cloud <-> Mobile (metadate în `sync_v2_documents`, octeți în Storage PRIVAT).

Autentificare exclusiv `Binding`; tenantul vine din binding și este impus de RLS (rolul cimitir_cp_app). Telefonul nu primește niciodată URL-uri/chei de Storage:
octeții se descarcă doar prin API, după autorizare. Calea din Storage se construiește pe server. Verificări: tip (semnătură), mărime, SHA-256 înainte de scriere.
Fără duplicate (uid unic + aceeași entitate/SHA-256), ștergere doar logică și numai de MAIN (octeții rămân în Storage).
"""
from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any, Dict, List, Optional

import cimitir_cloud_control as cc
import cimitir_entities as ents
import cimitir_field_storage as fs
import cimitir_storage as st
import cimitir_sync_v2 as sv

MAX_BYTES = fs.MAX_BYTES
MAGIC = fs.MAGIC
BLOCKED_DOC_TYPES = ("carte_identitate", "cerere_doc")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_UID = re.compile(r"^([0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")
_COLS = "uid, entity, entity_uid, doc_type, kind, filename, mime, size, sha256, caption, status, origin, version, change_seq, created_at, modified_at, created_by_binding, storage_path"


def storage_path(org: str, cem: str, uid: str) -> str:
    return f"docs/{org}/{cem}/{uid}"


def _public(r) -> Dict[str, Any]:
    d = {k: r[k] for k in ("uid", "entity", "entity_uid", "doc_type", "kind", "filename", "mime", "size", "sha256", "caption", "status", "origin", "version")}
    d.update(seq=int(r["change_seq"] or 0), created_at=sv._jsonable(r["created_at"]), modified_at=sv._jsonable(r["modified_at"]), size=int(r["size"]), deleted=r["status"] == "DELETED")
    return d


class DocumentService:
    def __init__(self, sync: sv.SyncV2Service, storage: Any):
        self.sync, self.cp, self.storage = sync, sync.cp, storage

    # ------------------------------------------------------------------ autorizare
    def _auth(self, key: str) -> Dict[str, Any]:
        return self.sync._auth(key)

    def _can_read(self, c, a, entity: str) -> bool:
        return entity in sv.readable_entities(c, a)

    def _need_upload(self, c, a) -> None:
        if a["device_type"] == "MAIN":
            return
        caps = sv.caps_of(c, a)
        if caps is None or "upload_documents" not in caps:
            raise cc.PermissionDenied("nu ai dreptul de a încărca documente sau fotografii")

    def _row(self, c, a, uid: str):
        return c.execute(f"SELECT {_COLS} FROM sync_v2_documents WHERE cemetery_uid=CAST(? AS uuid) AND uid=?", (a["cemetery_uid"], uid)).fetchone()

    # ------------------------------------------------------------------ 1. declarare (idempotentă)
    def declare(self, key: str, meta: Any) -> Dict[str, Any]:
        a = self._auth(key)
        if not isinstance(meta, dict):
            raise ValueError("cerere invalidă")
        uid = str(meta.get("uid") or "").lower()
        entity, entity_uid = str(meta.get("entity") or ""), str(meta.get("entity_uid") or "")[:80]
        doc_type, kind = str(meta.get("doc_type") or "")[:60], str(meta.get("kind") or "document")
        mime, sha = str(meta.get("mime") or ""), str(meta.get("sha256") or "").lower()
        try:
            size = int(meta.get("size"))
        except (TypeError, ValueError):
            raise fs.BadContent("mărime invalidă") from None
        if not _UID.match(uid) or entity not in ents.ENTITIES or not entity_uid or kind not in fs.KINDS or mime not in MAGIC or not _SHA.match(sha) or not (0 < size <= MAX_BYTES):
            raise fs.BadContent("tip, mărime sau hash neacceptate")
        if doc_type in BLOCKED_DOC_TYPES:
            raise fs.BadContent("acest tip de document (act de identitate) nu se sincronizează")
        name = st.safe_name(str(meta.get("filename") or "fisier"))[:200]
        caption = str(meta.get("caption") or "")[:500]
        c = self.cp._c()
        try:
            self._need_upload(c, a)
            if not self._can_read(c, a, entity):
                raise cc.PermissionDenied("nu ai dreptul de acces la acest registru")
            parent = c.execute(f"SELECT 1 AS x FROM {entity} WHERE uid=? AND organization_uid=CAST(? AS uuid) AND cemetery_uid=CAST(? AS uuid)",
                               (entity_uid, a["organization_uid"], a["cemetery_uid"])).fetchone()
            if parent is None:
                return {"status": "retry", "reason": "missing_parent"}
            c.execute("INSERT OR IGNORE INTO sync_v2_documents (organization_uid, cemetery_uid, uid, entity, entity_uid, doc_type, kind, filename, mime, size, sha256, caption, "
                      "storage_path, status, origin, created_by_binding) VALUES (CAST(? AS uuid),CAST(? AS uuid),?,?,?,?,?,?,?,?,?,?,?,'PENDING',?,CAST(? AS uuid))",
                      (a["organization_uid"], a["cemetery_uid"], uid, entity, entity_uid, doc_type, kind, name, mime, size, sha, caption,
                       storage_path(a["organization_uid"], a["cemetery_uid"], uid), a["device_type"], a["binding_uid"]))
            r = self._row(c, a, uid)
            if r is None:         # alt uid, aceeași entitate + același conținut: nu se creează duplicat
                dup = c.execute(f"SELECT {_COLS} FROM sync_v2_documents WHERE cemetery_uid=CAST(? AS uuid) AND entity=? AND entity_uid=? AND sha256=? AND status<>'DELETED'",
                                (a["cemetery_uid"], entity, entity_uid, sha)).fetchone()
                if dup is None:
                    raise fs.Conflict("document neînregistrat")
                return {"status": "duplicate", "duplicate_of": dup["uid"], "document": _public(dup)}
            if r["status"] == "DELETED":
                raise fs.Conflict("documentul a fost șters")
            if (r["sha256"], int(r["size"]), r["mime"], r["entity"], r["entity_uid"]) != (sha, size, mime, entity, entity_uid):
                raise fs.Conflict("același uid cu conținut sau legătură diferite")
            return {"status": "ready" if r["status"] == "READY" else "upload", "document": _public(r)}
        finally:
            c.close()

    # ------------------------------------------------------------------ 2. încărcare octeți
    def upload(self, key: str, uid: str, data: bytes) -> Dict[str, Any]:
        a = self._auth(key)
        uid = str(uid).lower()
        if not _UID.match(uid):
            raise fs.BadContent("uid invalid")
        c = self.cp._c()
        try:
            self._need_upload(c, a)
            r = self._row(c, a, uid)
            if r is None:
                raise fs.NotReady("încărcare nedeclarată")
            if a["device_type"] != "MAIN" and str(r["created_by_binding"]) != str(a["binding_uid"]):
                raise cc.PermissionDenied("documentul aparține altui dispozitiv")
            if r["status"] == "READY":
                return _public(r)
            if r["status"] == "DELETED":
                raise fs.Conflict("documentul a fost șters")
            if len(data) != int(r["size"]) or hashlib.sha256(data).hexdigest() != r["sha256"]:
                self._mark(c, a, uid, "FAILED", "sha256/mărime nu corespund declarației")
                raise fs.HashMismatch("SHA-256 sau mărime incorecte; nimic nu a fost scris în Storage")
            if not any(data.startswith(m) for m in MAGIC[r["mime"]]):
                self._mark(c, a, uid, "FAILED", "semnătura fișierului nu corespunde tipului")
                raise fs.BadContent("conținutul nu corespunde tipului declarat")
            path = r["storage_path"]
            try:
                self.storage.put_bytes(path, data, r["mime"], upsert=False)
            except st.AlreadyExists:       # reluare după întrerupere: obiectul există deja, trebuie să fie exact același
                h = hashlib.sha256()
                for block in self.storage.open_stream(path):
                    h.update(block)
                if h.hexdigest() != r["sha256"]:
                    self._mark(c, a, uid, "FAILED", "obiect existent diferit")
                    raise fs.Conflict("calea există cu alt conținut") from None
            info = self.storage.stat(path)
            if not info or int(info["size"]) != int(r["size"]):
                self._mark(c, a, uid, "FAILED", "verificare post-upload eșuată")
                raise fs.FieldStorageError("verificare post-upload eșuată")
            self._mark(c, a, uid, "READY", None)
            return _public(self._row(c, a, uid))
        finally:
            c.close()

    def _mark(self, c, a, uid: str, status: str, error: Optional[str]) -> None:
        c.execute("UPDATE sync_v2_documents SET status=?, error=? WHERE cemetery_uid=CAST(? AS uuid) AND uid=? AND status IN ('PENDING','FAILED')", (status, error, a["cemetery_uid"], uid))

    # ------------------------------------------------------------------ 3. listare / descărcare
    def list(self, key: str, since: int = 0, entity: str = "", entity_uid: str = "", limit: int = 200) -> Dict[str, Any]:
        a = self._auth(key)
        since, limit = max(0, int(since)), max(1, min(int(limit), 500))
        c = self.cp._c()
        try:
            allowed = [e for e in ents.ORDER if self._can_read(c, a, e)]
            if not allowed or (entity and entity not in allowed):
                return {"documents": [], "cursor": since, "more": False}
            sql = (f"SELECT {_COLS} FROM sync_v2_documents WHERE organization_uid=CAST(? AS uuid) AND cemetery_uid=CAST(? AS uuid) AND change_seq > ? "
                   f"AND entity IN ({','.join('?' for _ in allowed)}) AND status IN ('READY','DELETED')")
            params: List[Any] = [a["organization_uid"], a["cemetery_uid"], since, *allowed]
            if entity:
                sql += " AND entity=?"
                params.append(entity)
            if entity_uid:
                sql += " AND entity_uid=?"
                params.append(entity_uid[:80])
            rows = c.execute(sql + " ORDER BY change_seq LIMIT ?", (*params, limit + 1)).fetchall()
            more = len(rows) > limit
            rows = rows[:limit]
            docs = [_public(r) for r in rows]
            return {"documents": docs, "cursor": docs[-1]["seq"] if docs else since, "more": more}
        finally:
            c.close()

    def fetch(self, key: str, uid: str) -> Dict[str, Any]:
        """Octeții unui document READY, după autorizare (citire pe registrul entității). Verifică SHA-256 înainte de livrare."""
        a = self._auth(key)
        uid = str(uid).lower()
        if not _UID.match(uid):
            raise cc.ScopeError("document inexistent")
        c = self.cp._c()
        try:
            r = self._row(c, a, uid)
            if r is None or not self._can_read(c, a, r["entity"]):
                raise cc.ScopeError("document inexistent")        # RLS/permisiuni: nu se confirmă existența documentelor altui tenant
            if r["status"] != "READY":
                raise fs.NotReady(f"documentul nu este disponibil ({r['status']})")
        finally:
            c.close()
        data = b"".join(self.storage.open_stream(r["storage_path"]))
        if hashlib.sha256(data).hexdigest() != r["sha256"]:
            raise fs.HashMismatch("integritate: SHA-256 diferit în Storage")
        return {"data": data, "mime": r["mime"], "filename": r["filename"], "sha256": r["sha256"]}

    # ------------------------------------------------------------------ 4. ștergere logică (doar MAIN)
    def delete(self, key: str, uid: str) -> Dict[str, Any]:
        a = self._auth(key)
        self.sync._need_main(a)
        uid = str(uid).lower()
        c = self.cp._c()
        try:
            r = self._row(c, a, uid)
            if r is None:
                raise cc.ScopeError("document inexistent")
            if r["status"] != "DELETED":
                c.execute("UPDATE sync_v2_documents SET status='DELETED', deleted_by_binding=CAST(? AS uuid) WHERE cemetery_uid=CAST(? AS uuid) AND uid=?",
                          (a["binding_uid"], a["cemetery_uid"], uid))
            return _public(self._row(c, a, uid))
        finally:
            c.close()


def new_uid() -> str:
    return uuid.uuid4().hex
