"""cimitir_field_storage.py — 6B: obiecte media din teren (PDF/JPEG/PNG) în Storage PRIVAT; PostgreSQL păstrează doar metadata.

Fundația pentru Field Inbox (fără UI): telefon -> HTTPS -> acest strat -> Supabase Storage privat + `cp_storage_objects` (tenant, SHA-256, cale, status).
Nu importă nimic în SQLite și nu face sync automat: Field Inbox va folosi ulterior PENDING -> MAIN verifică -> ACCEPT/REJECT -> IMPORTED.

Garanții:
- autorizare: fiecare operație cere cheia legăturii (binding) a cimitirului; contextul RLS al `cimitir_cp_app` limitează rândurile la tenant;
- calea din Storage se construiește DOAR pe server din (organizație, cimitir, uid) — clientul nu trimite niciodată o cale;
- SHA-256 + mărime + semnătura fișierului (magic bytes) se verifică ÎNAINTE de orice scriere în Storage; un hash greșit nu lasă obiecte;
- idempotent: același (uid, sha256) = același rezultat; reluare după întrerupere (obiect scris, PostgreSQL neactualizat) verifică obiectul și finalizează;
- descărcare: doar URL semnat scurt (<= 300 s), emis după autorizare, pentru obiecte READY; cheia secretă nu iese din server.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any, Dict, Optional

import cimitir_cloud_control as cc
import cimitir_storage as st

MAX_BYTES = 25 * 1024 * 1024
MAX_SIGNED_TTL = 300
MAGIC = {"application/pdf": (b"%PDF-",), "image/jpeg": (b"\xff\xd8\xff",), "image/png": (b"\x89PNG\r\n\x1a\n",)}
KINDS = ("document", "photo")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class FieldStorageError(Exception):
    pass


class HashMismatch(FieldStorageError):
    pass


class BadContent(FieldStorageError):
    pass


class Conflict(FieldStorageError):
    pass


class NotReady(FieldStorageError):
    pass


class Interrupted(RuntimeError):
    """Simulează o întrerupere (crash) în teste."""


def storage_path(organization_uid: str, cemetery_uid: str, uid: str) -> str:
    return f"field/{organization_uid}/{cemetery_uid}/{uid}"


def _norm_uuid(v: str, what: str) -> str:
    try:
        return str(uuid.UUID(str(v)))
    except ValueError:
        raise FieldStorageError(f"{what} invalid") from None


class FieldMediaStore:
    def __init__(self, cp, storage: st.SupabaseStorage):
        self.cp, self.storage = cp, storage

    # ------------------------------------------------------------------ helpers
    def _auth(self, binding_key: str, cemetery_uid: str) -> Dict[str, Any]:
        cem = _norm_uuid(cemetery_uid, "cemetery_uid")
        a = self.cp.authenticate(binding_key, cem)           # ScopeError dacă legătura nu e a acestui cimitir; setează contextul RLS
        return a

    def _row(self, c, cem: str, uid: str):
        return c.execute("SELECT uid, kind, filename, mime, size, sha256, storage_path, status, error, organization_uid FROM cp_storage_objects "
                         "WHERE cemetery_uid=? AND uid=?", (cem, uid)).fetchone()

    @staticmethod
    def _public(r) -> Dict[str, Any]:
        return {"uid": r["uid"], "kind": r["kind"], "filename": r["filename"], "mime": r["mime"], "size": r["size"], "sha256": r["sha256"],
                "status": r["status"], "error": r["error"]}

    # ------------------------------------------------------------------ 1. declarare
    def begin_upload(self, binding_key: str, cemetery_uid: str, uid: str, filename: str, mime: str, size: int, sha256: str, kind: str = "document") -> Dict[str, Any]:
        a = self._auth(binding_key, cemetery_uid)
        uid = _norm_uuid(uid, "uid")
        sha256 = (sha256 or "").lower()
        if mime not in MAGIC or kind not in KINDS or not _SHA_RE.match(sha256) or not (0 < int(size) <= MAX_BYTES):
            raise BadContent("tip/mărime/hash neacceptate")
        name = st.safe_name(filename)
        path = storage_path(a["organization_uid"], a["cemetery_uid"], uid)
        c = self.cp._c()
        try:
            c.execute("INSERT OR IGNORE INTO cp_storage_objects (cemetery_uid, uid, organization_uid, kind, filename, mime, size, sha256, storage_path, status, created_by_binding) "
                      "VALUES (?,?,?,?,?,?,?,?,?,'PENDING',?)", (a["cemetery_uid"], uid, a["organization_uid"], kind, name, mime, int(size), sha256, path, a["binding_uid"]))
            r = self._row(c, a["cemetery_uid"], uid)
            if (r["sha256"], int(r["size"]), r["mime"]) != (sha256, int(size), mime):
                raise Conflict("același uid cu conținut declarat diferit")
            if r["status"] == "DELETED":
                raise Conflict("obiect șters")
            return self._public(r)
        finally:
            c.close()

    # ------------------------------------------------------------------ 2. încărcare (+ reluare)
    def _set(self, c, cem: str, uid: str, status: str, error: Optional[str] = None, ready: bool = False) -> None:
        c.execute(f"UPDATE cp_storage_objects SET status=?, error=?, updated_at=now(){', ready_at=now()' if ready else ''} WHERE cemetery_uid=? AND uid=?", (status, error, cem, uid))

    def _hash_remote(self, path: str) -> tuple:
        h, n = hashlib.sha256(), 0
        for block in self.storage.open_stream(path):
            h.update(block)
            n += len(block)
        return h.hexdigest(), n

    def upload(self, binding_key: str, cemetery_uid: str, uid: str, data: bytes, _crash_after_storage: bool = False) -> Dict[str, Any]:
        a = self._auth(binding_key, cemetery_uid)
        uid = _norm_uuid(uid, "uid")
        cem = a["cemetery_uid"]
        c = self.cp._c()
        try:
            r = self._row(c, cem, uid)
            if not r:
                raise NotReady("upload nedeclarat (begin_upload)")
            if r["status"] == "READY":
                return self._public(r)
            if r["status"] == "DELETED":
                raise Conflict("obiect șters")
            if len(data) != int(r["size"]) or hashlib.sha256(data).hexdigest() != r["sha256"]:
                self._set(c, cem, uid, "FAILED", "sha256/mărime nu corespund declarației")
                raise HashMismatch("SHA-256 sau mărime incorecte; nimic nu a fost scris în Storage")
            if not any(data.startswith(m) for m in MAGIC[r["mime"]]):
                self._set(c, cem, uid, "FAILED", "semnătura fișierului nu corespunde tipului")
                raise BadContent("conținutul nu corespunde tipului declarat")
            path = r["storage_path"]
            try:
                self.storage.put_bytes(path, data, r["mime"], upsert=False)
            except st.AlreadyExists:                          # reluare: obiectul există deja -> trebuie să fie exact același
                if self._hash_remote(path) != (r["sha256"], int(r["size"])):
                    self._set(c, cem, uid, "FAILED", "obiect existent diferit")
                    raise Conflict("calea există cu alt conținut") from None
            if _crash_after_storage:
                raise Interrupted("întrerupere după scrierea în Storage")
            self._set(c, cem, uid, "UPLOADED")
            info = self.storage.stat(path)
            if not info or info["size"] != int(r["size"]):
                self._set(c, cem, uid, "FAILED", "verificare post-upload eșuată")
                raise FieldStorageError("verificare post-upload eșuată")
            self._set(c, cem, uid, "READY", ready=True)
            return self._public(self._row(c, cem, uid))
        finally:
            c.close()

    # ------------------------------------------------------------------ 3. citire / descărcare
    def status(self, binding_key: str, cemetery_uid: str, uid: str) -> Optional[Dict[str, Any]]:
        a = self._auth(binding_key, cemetery_uid)
        c = self.cp._c()
        try:
            r = self._row(c, a["cemetery_uid"], _norm_uuid(uid, "uid"))
            return self._public(r) if r else None
        finally:
            c.close()

    def download_url(self, binding_key: str, cemetery_uid: str, uid: str, ttl: int = 60) -> Dict[str, Any]:
        a = self._auth(binding_key, cemetery_uid)
        c = self.cp._c()
        try:
            r = self._row(c, a["cemetery_uid"], _norm_uuid(uid, "uid"))
        finally:
            c.close()
        if not r:
            raise cc.ScopeError("obiect inexistent")             # RLS: obiectele altui tenant nu sunt vizibile
        if r["status"] != "READY":
            raise NotReady(f"obiectul nu este READY ({r['status']})")
        ttl = max(1, min(int(ttl), MAX_SIGNED_TTL))
        return {"url": self.storage.signed_url(r["storage_path"], ttl), "expires_in": ttl, "sha256": r["sha256"], "size": r["size"], "mime": r["mime"]}

    # ------------------------------------------------------------------ 4. ștergere + curățare orfani
    def delete(self, binding_key: str, cemetery_uid: str, uid: str) -> Dict[str, Any]:
        a = self._auth(binding_key, cemetery_uid)
        uid = _norm_uuid(uid, "uid")
        c = self.cp._c()
        try:
            r = self._row(c, a["cemetery_uid"], uid)
            if not r:
                raise cc.ScopeError("obiect inexistent")
            self.storage.delete(r["storage_path"])
            self._set(c, a["cemetery_uid"], uid, "DELETED")
            return self._public(self._row(c, a["cemetery_uid"], uid))
        finally:
            c.close()

    def cleanup_orphans(self, binding_key: str, cemetery_uid: str, older_than_s: float = 3600) -> Dict[str, int]:
        """Doar în prefixul tenantului: (1) rânduri abandonate (PENDING/UPLOADED/FAILED mai vechi de prag) -> obiectul șters, rând DELETED;
        (2) obiecte din Storage fără rând (sau cu rând DELETED/FAILED), mai vechi de prag -> șterse."""
        a = self._auth(binding_key, cemetery_uid)
        cem, org = a["cemetery_uid"], a["organization_uid"]
        rep = {"stale_rows": 0, "orphan_objects": 0}
        c = self.cp._c()
        try:
            stale = c.execute("SELECT uid, storage_path FROM cp_storage_objects WHERE cemetery_uid=? AND status IN ('PENDING','UPLOADED','FAILED') "
                              "AND updated_at < now() - make_interval(secs => ?::double precision)", (cem, float(older_than_s))).fetchall()
            for r in stale:
                self.storage.delete(r["storage_path"])
                self._set(c, cem, r["uid"], "DELETED", "abandonat (cleanup)")
                rep["stale_rows"] += 1
            keep = {r["uid"] for r in c.execute("SELECT uid FROM cp_storage_objects WHERE cemetery_uid=? AND status IN ('PENDING','UPLOADED','READY')", (cem,)).fetchall()}
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            prefix = f"field/{org}/{cem}"
            for o in self.storage.list_prefix(prefix):
                age = (now - datetime.fromisoformat(o["updated_at"].replace("Z", "+00:00"))).total_seconds() if o.get("updated_at") else 0
                if o["name"] not in keep and age >= older_than_s:
                    self.storage.delete(f"{prefix}/{o['name']}")
                    rep["orphan_objects"] += 1
            return rep
        finally:
            c.close()
