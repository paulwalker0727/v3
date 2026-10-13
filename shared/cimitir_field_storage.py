"""cimitir_field_storage.py — 6B: obiecte media din teren (PDF/JPEG/PNG) în Storage PRIVAT; PostgreSQL păstrează doar metadata.

Fundația pentru Field Inbox (fără UI): telefon -> HTTPS -> acest strat -> Supabase Storage privat + `cp_storage_objects` (tenant, SHA-256, cale, status).
Nu importă nimic în SQLite și nu face sync automat: Field Inbox va folosi ulterior PENDING -> MAIN verifică -> ACCEPT/REJECT -> IMPORTED.

Garanții:
- autorizare: fiecare operație cere cheia legăturii (binding) a cimitirului; contextul RLS al `cimitir_cp_app` limitează rândurile la tenant;
- calea din Storage se construiește DOAR pe server din (organizație, cimitir, uid) — clientul nu trimite niciodată o cale;
- SHA-256 + mărime + semnătura fișierului (magic bytes) se verifică ÎNAINTE de orice scriere în Storage; un hash greșit nu lasă obiecte;
- idempotent: același (uid, sha256) = același rezultat; reluare după întrerupere (obiect scris, PostgreSQL neactualizat) verifică obiectul și finalizează;
- descărcare: doar URL semnat scurt (<= 300 s), emis după autorizare, pentru obiecte READY; cheia secretă nu iese din server.
- 016: tranzițiile de stare sunt compare-and-set în DB (UPDATE ... WHERE status IN (...) + trigger): STAGING -> READY și STAGING -> CLEANING -> DELETED sunt mutual exclusive;
  `delete` (owner, RLS) nu folosește DELETE SQL; `cleanup_orphans` e operație de serviciu, printr-un rol separat (cimitir_storage_cleanup) și funcții SQL înguste.
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import uuid
from typing import Any, Dict, Optional

import psycopg

import cimitir_cloud_control as cc
import cimitir_cloud_control_pg as ccp
import cimitir_storage as st

MAX_BYTES = 25 * 1024 * 1024
MAX_SIGNED_TTL = 300
MAGIC = {"application/pdf": (b"%PDF-",), "image/jpeg": (b"\xff\xd8\xff",), "image/png": (b"\x89PNG\r\n\x1a\n",)}
KINDS = ("document", "photo")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_STAGING = ("PENDING", "UPLOADED", "FAILED")
CLEANUP_URL_ENV = "CIMITIR_CP_CLEANUP_DATABASE_URL"


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
    def __init__(self, cp, storage: st.SupabaseStorage, cleanup_url: Optional[str] = None):
        self.cp, self.storage = cp, storage
        self.cleanup_url = cleanup_url or os.environ.get(CLEANUP_URL_ENV)

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
            if r["status"] in ("DELETED", "CLEANING"):
                raise Conflict("obiect șters" if r["status"] == "DELETED" else "obiect revendicat de cleanup")
            if (r["sha256"], int(r["size"]), r["mime"]) != (sha256, int(size), mime):
                raise Conflict("același uid cu conținut declarat diferit")
            return self._public(r)
        finally:
            c.close()

    # ------------------------------------------------------------------ 2. încărcare (+ reluare)
    def _cas(self, c, cem: str, uid: str, from_status: tuple, status: str, error: Optional[str] = None, ready: bool = False) -> bool:
        """Compare-and-set atomic în DB: True doar dacă rândul era (încă) într-una din `from_status` — cleanup-ul concurent (CLEANING) face rowcount=0."""
        marks = ",".join("?" * len(from_status))
        cur = c.execute(f"UPDATE cp_storage_objects SET status=?, error=?, updated_at=now(){', ready_at=now()' if ready else ''} "
                        f"WHERE cemetery_uid=? AND uid=? AND status IN ({marks})", (status, error, cem, uid, *from_status))
        return cur.rowcount == 1

    def _lost(self, c, cem: str, uid: str) -> Dict[str, Any]:
        """CAS pierdut: READY (altă încercare a câștigat) = idempotent; altfel obiectul e șters/revendicat de cleanup."""
        r = self._row(c, cem, uid)
        if r and r["status"] in ("READY", "IMPORTED"):
            return self._public(r)
        raise Conflict("obiect revendicat de cleanup sau șters" if r else "obiect inexistent")

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
            if r["status"] in ("READY", "IMPORTED"):
                return self._public(r)
            if r["status"] in ("DELETED", "CLEANING"):
                raise Conflict("obiect șters" if r["status"] == "DELETED" else "obiect revendicat de cleanup")
            if len(data) != int(r["size"]) or hashlib.sha256(data).hexdigest() != r["sha256"]:
                self._cas(c, cem, uid, _STAGING, "FAILED", "sha256/mărime nu corespund declarației")
                raise HashMismatch("SHA-256 sau mărime incorecte; nimic nu a fost scris în Storage")
            if not any(data.startswith(m) for m in MAGIC[r["mime"]]):
                self._cas(c, cem, uid, _STAGING, "FAILED", "semnătura fișierului nu corespunde tipului")
                raise BadContent("conținutul nu corespunde tipului declarat")
            path = r["storage_path"]
            if not self._cas(c, cem, uid, _STAGING, r["status"], r["error"]):      # „lease” de scriere: reîmprospătează updated_at; cleanup-ul care a revendicat deja rândul ne oprește ÎNAINTE de Storage
                return self._lost(c, cem, uid)
            try:
                self.storage.put_bytes(path, data, r["mime"], upsert=False)
            except st.AlreadyExists:                          # reluare: obiectul există deja -> trebuie să fie exact același
                if self._hash_remote(path) != (r["sha256"], int(r["size"])):
                    self._cas(c, cem, uid, _STAGING, "FAILED", "obiect existent diferit")
                    raise Conflict("calea există cu alt conținut") from None
            if _crash_after_storage:
                raise Interrupted("întrerupere după scrierea în Storage")
            if not self._cas(c, cem, uid, _STAGING, "UPLOADED"):
                return self._lost(c, cem, uid)
            info = self.storage.stat(path)
            if not info or info["size"] != int(r["size"]):
                self._cas(c, cem, uid, _STAGING, "FAILED", "verificare post-upload eșuată")
                raise FieldStorageError("verificare post-upload eșuată")
            if not self._cas(c, cem, uid, ("PENDING", "UPLOADED"), "READY", ready=True):    # STAGING -> READY: câștigă doar dacă cleanup nu a revendicat între timp
                return self._lost(c, cem, uid)
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

    # ------------------------------------------------------------------ 4. ștergere (owner, RLS) + curățare orfani (serviciu, rol separat)
    def delete(self, binding_key: str, cemetery_uid: str, uid: str) -> Dict[str, Any]:
        """Ștergerea normală a unui obiect al tenantului: UPDATE condiționat -> DELETED (fără DELETE SQL), APOI Storage; o ștergere din Storage eșuată lasă o rămășiță
        pe care o reia `delete` sau o curăță `cleanup_orphans` (rând DELETED). Nu ia niciodată un obiect revendicat de cleanup (CLEANING) și nu șterge IMPORTED."""
        a = self._auth(binding_key, cemetery_uid)
        uid, cem = _norm_uuid(uid, "uid"), a["cemetery_uid"]
        c = self.cp._c()
        try:
            r = self._row(c, cem, uid)
            if not r:
                raise cc.ScopeError("obiect inexistent")
            try:
                cas_ok = r["status"] == "DELETED" or self._cas(c, cem, uid, _STAGING + ("READY",), "DELETED")
            except sqlite3.IntegrityError:                    # 018: obiect legat de un item Field Inbox activ
                raise Conflict("obiectul este legat de un item Field Inbox activ") from None
            if not cas_ok:
                r = self._row(c, cem, uid)
                if r["status"] != "DELETED":
                    raise Conflict("obiect revendicat de cleanup" if r["status"] == "CLEANING" else f"obiectul nu poate fi șters ({r['status']})")
            self.storage.delete(r["storage_path"])
            return self._public(self._row(c, cem, uid))
        finally:
            c.close()

    def _cleanup_conn(self):
        if not self.cleanup_url:
            raise FieldStorageError(f"cleanup indisponibil: {CLEANUP_URL_ENV} nu este setat (rol cimitir_storage_cleanup)")
        ccp._guard(self.cleanup_url)
        kw = {"options": f"-c search_path={self.cp.schema}"} if getattr(self.cp, "schema", None) else {}
        return psycopg.connect(self.cleanup_url, autocommit=True, connect_timeout=15, **kw)

    def cleanup_orphans(self, binding_key: str, cemetery_uid: str, older_than_s: float = 3600, limit: int = 100) -> Dict[str, int]:
        """Operație de SERVICIU (rol DB separat, doar funcții SQL înguste): DB revendică atomic ce e eligibil (STAGING abandonat / rămășițe DELETED / obiecte fără rând),
        abia apoi se șterge din Storage și se închide revendicarea cu tokenul propriu. READY/IMPORTED nu pot fi revendicate; alt worker nu poate relua un obiect deja revendicat.
        (1) rânduri STAGING abandonate -> obiectul șters, rând DELETED; (2) obiecte din Storage fără rând / cu rând DELETED învechit -> șterse."""
        from datetime import datetime, timezone
        a = self._auth(binding_key, cemetery_uid)
        cem, org = a["cemetery_uid"], a["organization_uid"]
        rep = {"stale_rows": 0, "orphan_objects": 0}
        token, older = str(uuid.uuid4()), float(older_than_s)
        conn = self._cleanup_conn()

        def _delete_claimed(uid: str, path: str) -> bool:
            try:
                self.storage.delete(path)
            except st.StorageError:
                conn.execute("SELECT cp_storage_cleanup_release(%s,%s,%s,%s)", (org, cem, uid, token))
                rep["errors"] = rep.get("errors", 0) + 1
                return False
            return bool(conn.execute("SELECT cp_storage_cleanup_finish(%s,%s,%s,%s)", (org, cem, uid, token)).fetchone()[0])

        try:
            while True:
                claimed = conn.execute("SELECT out_uid, out_storage_path FROM cp_storage_cleanup_claim(%s,%s,%s,%s,%s)", (org, cem, token, older, int(limit))).fetchall()
                done = sum(_delete_claimed(uid, path) for uid, path in claimed)
                rep["stale_rows"] += done
                if len(claimed) < int(limit) or done == 0:
                    break
            now = datetime.now(timezone.utc)
            for o in self.storage.list_prefix(f"field/{org}/{cem}"):
                uid = o["name"]
                age = (now - datetime.fromisoformat(o["updated_at"].replace("Z", "+00:00"))).total_seconds() if o.get("updated_at") else 0
                path = conn.execute("SELECT cp_storage_cleanup_claim_object(%s,%s,%s,%s,%s,%s)", (org, cem, uid, token, older, age)).fetchone()[0]
                if path:
                    rep["orphan_objects"] += _delete_claimed(uid, path)
            return rep
        finally:
            conn.close()
