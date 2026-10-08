"""cimitir_cloud_control.py — planul de control cloud (Etapa 4, Pasul 1) într-un SQLite LOCAL/TEMPORAR.

Aceleași reguli ca în ETAPA4_BOOTSTRAP_DESIGN.md v2 (§8, §12): roluri (Platform Admin / Organization Owner / REBUILD / Admin), coduri de
pairing/provizionare (doar hash, expirare, încercări limitate), legături device × cemetery cu cheie (doar hash), `bootstrap_runs` cu stări
persistente și un singur bootstrap activ per cimitir. Backend-ul PostgreSQL + endpoint-urile HTTP vin după închiderea Pasului 1.
NU se conectează la nicio bază reală.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional

OWNER, REBUILD, ADMIN = "owner", "rebuild", "admin"
ACTIVE_RUN_STATES = ("planned", "running", "verifying")
RUN_STATES = ("planned", "running", "verifying", "completed", "failed", "rolled_back")
PLAN_TTL = 600
CODE_TTL = 900
MAX_FAILED_REDEEMS = 5
PERMISSIONS = {  # acțiune -> roluri permise în organizație
    "create_cemetery": {OWNER}, "issue_code": {OWNER}, "revoke_binding": {OWNER}, "archive_cemetery": {OWNER},
    "rebuild": {OWNER, REBUILD}, "bootstrap": {OWNER}, "purge_archive": {OWNER}, "set_retention": {OWNER}, "grant_role": {OWNER}, "read": {OWNER, REBUILD, ADMIN},
}


class ControlError(Exception):
    pass


class ScopeError(ControlError):
    """Resursă inexistentă SAU din altă organizație/cimitir (nu dezvăluim diferența)."""


class PermissionDenied(ControlError):
    pass


class CodeError(ControlError):
    pass


class ConflictError(ControlError):
    pass


class BootstrapLocked(ControlError):
    pass


def _h(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS principals (uid TEXT PRIMARY KEY, name TEXT NOT NULL, platform_admin INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS memberships (principal_uid TEXT NOT NULL, organization_uid TEXT NOT NULL, role TEXT NOT NULL,
  PRIMARY KEY (principal_uid, organization_uid, role));
CREATE TABLE IF NOT EXISTS organizations (uid TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ACTIVE',
  tombstone_retention_days INTEGER NOT NULL DEFAULT 180);
CREATE TABLE IF NOT EXISTS cemeteries (uid TEXT PRIMARY KEY, organization_uid TEXT NOT NULL REFERENCES organizations(uid), name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'DRAFT', data_epoch INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS registry_rows (cemetery_uid TEXT NOT NULL, entity TEXT NOT NULL, uid TEXT NOT NULL, PRIMARY KEY (cemetery_uid, entity, uid));
CREATE TABLE IF NOT EXISTS registry_row_data (cemetery_uid TEXT NOT NULL, entity TEXT NOT NULL, uid TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
  content TEXT NOT NULL DEFAULT '{}', change_seq INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (cemetery_uid, entity, uid));
CREATE TABLE IF NOT EXISTS media_objects (cemetery_uid TEXT NOT NULL, uid TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'document', entity_type TEXT, entity_id TEXT,
  name TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL, present INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (cemetery_uid, uid));
CREATE TABLE IF NOT EXISTS open_conflicts (cemetery_uid TEXT NOT NULL, uid TEXT NOT NULL, PRIMARY KEY (cemetery_uid, uid));
CREATE TABLE IF NOT EXISTS tombstones (cemetery_uid TEXT NOT NULL, entity TEXT NOT NULL, uid TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pairing_codes (code_hash TEXT PRIMARY KEY, organization_uid TEXT NOT NULL, cemetery_uid TEXT, issued_by TEXT NOT NULL,
  expires_at REAL NOT NULL, used_at REAL, used_bootstrap_id TEXT);
CREATE TABLE IF NOT EXISTS failed_redeems (installation_uid TEXT PRIMARY KEY, n INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS devices (device_uid TEXT PRIMARY KEY, organization_uid TEXT NOT NULL, installation_uid TEXT NOT NULL, name TEXT,
  UNIQUE (organization_uid, installation_uid));
CREATE TABLE IF NOT EXISTS bindings (binding_uid TEXT PRIMARY KEY, device_uid TEXT NOT NULL, organization_uid TEXT NOT NULL,
  cemetery_uid TEXT NOT NULL REFERENCES cemeteries(uid), key_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
  sync_epoch INTEGER NOT NULL DEFAULT 1, last_change_seq INTEGER NOT NULL DEFAULT 0, UNIQUE (device_uid, cemetery_uid));
CREATE TABLE IF NOT EXISTS bootstrap_runs (bootstrap_id TEXT PRIMARY KEY, organization_uid TEXT NOT NULL, cemetery_uid TEXT NOT NULL, kind TEXT NOT NULL,
  binding_uid TEXT, installation_uid TEXT, plan_hash TEXT NOT NULL, state TEXT NOT NULL, checkpoint TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL, finished_at REAL, error TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_bootstrap ON bootstrap_runs (cemetery_uid) WHERE state IN ('planned','running','verifying');
CREATE TABLE IF NOT EXISTS binding_issuers (binding_uid TEXT PRIMARY KEY, principal_uid TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, actor TEXT, action TEXT, organization_uid TEXT, cemetery_uid TEXT, detail TEXT);
"""


class LocalControlPlane:
    def __init__(self, path, clock: Callable[[], float] = time.time) -> None:
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        c = self._c()
        c.executescript(_SCHEMA)
        c.close()

    def _c(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        return c

    def _audit(self, c, actor, action, org=None, cem=None, **detail) -> None:
        c.execute("INSERT INTO audit_log (ts, actor, action, organization_uid, cemetery_uid, detail) VALUES (?,?,?,?,?,?)",
                  (self.clock(), actor, action, org, cem, json.dumps(detail, sort_keys=True)))

    # ------------------------------------------------------------ principals / roluri
    def create_principal(self, name: str, platform_admin: bool = False) -> str:
        uid = str(uuid.uuid4())
        c = self._c()
        c.execute("INSERT INTO principals VALUES (?,?,?)", (uid, name, 1 if platform_admin else 0))
        c.close()
        return uid

    def _roles(self, c, principal: str, org: str) -> set:
        return {r["role"] for r in c.execute("SELECT role FROM memberships WHERE principal_uid=? AND organization_uid=?", (principal, org))}

    def can(self, principal: str, action: str, organization_uid: str) -> bool:
        c = self._c()
        try:
            return bool(self._roles(c, principal, organization_uid) & PERMISSIONS[action])
        finally:
            c.close()

    def _require(self, c, principal: str, action: str, org: str) -> None:
        if not c.execute("SELECT 1 FROM organizations WHERE uid=?", (org,)).fetchone():
            raise ScopeError("organizație inexistentă")
        if not (self._roles(c, principal, org) & PERMISSIONS[action]):
            raise PermissionDenied(f"{action}: rol insuficient în organizație")

    def create_organization(self, actor: str, organization_uid: str, name: str, first_owner: Optional[str] = None) -> None:
        c = self._c()
        try:
            row = c.execute("SELECT platform_admin FROM principals WHERE uid=?", (actor,)).fetchone()
            if not row or not row["platform_admin"]:
                raise PermissionDenied("doar Platform Admin creează organizații")
            uuid.UUID(organization_uid)
            try:
                c.execute("INSERT INTO organizations (uid, name) VALUES (?,?)", (organization_uid, name))
            except sqlite3.IntegrityError:
                raise ConflictError("organizația există deja (UID-urile nu se reutilizează)") from None
            if first_owner:
                c.execute("INSERT OR IGNORE INTO memberships VALUES (?,?,?)", (first_owner, organization_uid, OWNER))
            self._audit(c, actor, "create_organization", organization_uid)
        finally:
            c.close()

    def grant_role(self, actor: str, principal: str, organization_uid: str, role: str) -> None:
        c = self._c()
        try:
            self._require(c, actor, "grant_role", organization_uid)
            if role not in (REBUILD, ADMIN):
                raise PermissionDenied("Owner poate acorda doar REBUILD/ADMIN; Owner nou = Platform Admin")
            c.execute("INSERT OR IGNORE INTO memberships VALUES (?,?,?)", (principal, organization_uid, role))
            self._audit(c, actor, "grant_role", organization_uid, role=role, principal=principal)
        finally:
            c.close()

    # ------------------------------------------------------------ cimitire
    def create_cemetery(self, actor: str, organization_uid: str, cemetery_uid: str, name: str) -> None:
        c = self._c()
        try:
            self._require(c, actor, "create_cemetery", organization_uid)
            self._insert_cemetery(c, organization_uid, cemetery_uid, name)
            self._audit(c, actor, "create_cemetery", organization_uid, cemetery_uid)
        finally:
            c.close()

    def _insert_cemetery(self, c, org: str, uid: str, name: str) -> None:
        uuid.UUID(uid)
        if c.execute("SELECT 1 FROM cemeteries WHERE uid=?", (uid,)).fetchone():
            raise ConflictError("UID de cimitir deja folosit (definitiv, nu se reutilizează)")
        c.execute("INSERT INTO cemeteries (uid, organization_uid, name) VALUES (?,?,?)", (uid, org, name))

    def _state_of(self, c, cemetery_uid: str) -> Dict[str, Any]:
        n = c.execute("SELECT COUNT(*) FROM registry_rows WHERE cemetery_uid=?", (cemetery_uid,)).fetchone()[0]
        t = c.execute("SELECT COUNT(*) FROM tombstones WHERE cemetery_uid=?", (cemetery_uid,)).fetchone()[0]
        cem = c.execute("SELECT * FROM cemeteries WHERE uid=?", (cemetery_uid,)).fetchone()
        return {"exists": cem is not None, "status": cem["status"] if cem else None, "data_epoch": cem["data_epoch"] if cem else None,
                "rows": n, "tombstones": t, "classification": "nonempty" if n else ("metadata_only" if t else "empty")}

    def inspect_as(self, actor: str, organization_uid: str, cemetery_uid: str) -> Dict[str, Any]:
        c = self._c()
        try:
            self._require(c, actor, "read", organization_uid)
            row = c.execute("SELECT organization_uid FROM cemeteries WHERE uid=?", (cemetery_uid,)).fetchone()
            if row and row["organization_uid"] != organization_uid:
                return self._state_of(c, str(uuid.uuid4()))  # din altă organizație ≡ inexistent
            return self._state_of(c, cemetery_uid)
        finally:
            c.close()

    # ------------------------------------------------------------ SIMULATOR: date de test și citire read-only pentru manifest (Pasul 2)
    def sim_put_row(self, cemetery_uid: str, entity: str, uid: str, data: Dict[str, Any], version: int = 1) -> None:
        """Doar simulator/teste: plasează un rând de entitate (format pe fir: coloane + FK ca uid de părinte)."""
        c = self._c()
        try:
            seq = c.execute("SELECT COALESCE(MAX(change_seq),0)+1 FROM registry_row_data WHERE cemetery_uid=?", (cemetery_uid,)).fetchone()[0]
            c.execute("INSERT OR IGNORE INTO registry_rows (cemetery_uid, entity, uid) VALUES (?,?,?)", (cemetery_uid, entity, uid))
            c.execute("INSERT OR REPLACE INTO registry_row_data (cemetery_uid, entity, uid, version, content, change_seq) VALUES (?,?,?,?,?,?)",
                      (cemetery_uid, entity, uid, version, json.dumps(data, sort_keys=True), seq))
        finally:
            c.close()

    def sim_delete_row(self, cemetery_uid: str, entity: str, uid: str) -> None:
        c = self._c()
        try:
            c.execute("DELETE FROM registry_row_data WHERE cemetery_uid=? AND entity=? AND uid=?", (cemetery_uid, entity, uid))
            if c.execute("DELETE FROM registry_rows WHERE cemetery_uid=? AND entity=? AND uid=?", (cemetery_uid, entity, uid)).rowcount:
                c.execute("INSERT INTO tombstones (cemetery_uid, entity, uid) VALUES (?,?,?)", (cemetery_uid, entity, uid))
        finally:
            c.close()

    def sim_put_media(self, cemetery_uid: str, uid: str, name: str, size: int, sha256: str, kind: str = "document", entity_type: str = "",
                      entity_id: str = "", present: bool = True) -> None:
        c = self._c()
        try:
            c.execute("INSERT OR REPLACE INTO media_objects VALUES (?,?,?,?,?,?,?,?,?)",
                      (cemetery_uid, uid, kind, entity_type, entity_id, name, size, sha256, 1 if present else 0))
        finally:
            c.close()

    def sim_set_status(self, cemetery_uid: str, status: str, data_epoch: Optional[int] = None) -> None:
        c = self._c()
        try:
            c.execute("UPDATE cemeteries SET status=? WHERE uid=?", (status, cemetery_uid))
            if data_epoch is not None:
                c.execute("UPDATE cemeteries SET data_epoch=? WHERE uid=?", (data_epoch, cemetery_uid))
        finally:
            c.close()

    def sim_add_conflict(self, cemetery_uid: str, uid: str) -> None:
        c = self._c()
        try:
            c.execute("INSERT OR IGNORE INTO open_conflicts VALUES (?,?)", (cemetery_uid, uid))
        finally:
            c.close()

    def export_snapshot(self, actor: str, organization_uid: str, cemetery_uid: str) -> Dict[str, Any]:
        """CITIRE STRICT READ-ONLY (conexiune `mode=ro`) a stării unui cimitir, pentru manifest. Scope: doar organizația `actor`-ului;
        un cimitir din altă organizație ≡ inexistent (fără scurgeri)."""
        c = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True, timeout=15)
        c.row_factory = sqlite3.Row
        try:
            self._require(c, actor, "read", organization_uid)
            cem = c.execute("SELECT * FROM cemeteries WHERE uid=? AND organization_uid=?", (cemetery_uid, organization_uid)).fetchone()
            snap: Dict[str, Any] = {"exists": cem is not None, "organization_uid": organization_uid, "cemetery_uid": cemetery_uid,
                                    "status": cem["status"] if cem else None, "data_epoch": cem["data_epoch"] if cem else None,
                                    "schema_version": "sim-control-v1", "entities": {}, "media": [], "tombstones": 0, "open_conflicts": 0, "change_seq": None}
            if not cem:
                return snap
            for r in c.execute("SELECT r.entity, r.uid, COALESCE(d.version,1) AS version, COALESCE(d.content,'{}') AS content FROM registry_rows r "
                               "LEFT JOIN registry_row_data d ON d.cemetery_uid=r.cemetery_uid AND d.entity=r.entity AND d.uid=r.uid "
                               "WHERE r.cemetery_uid=? ORDER BY r.entity, r.uid", (cemetery_uid,)):
                snap["entities"].setdefault(r["entity"], []).append({"ref": r["uid"], "uid": r["uid"], "version": r["version"], "data": json.loads(r["content"])})
            for r in c.execute("SELECT * FROM media_objects WHERE cemetery_uid=? ORDER BY uid", (cemetery_uid,)):
                snap["media"].append({"uid": r["uid"], "kind": r["kind"], "entity_type": r["entity_type"], "entity_id": r["entity_id"], "name": r["name"],
                                      "size": r["size"], "sha256": r["sha256"], "present": bool(r["present"])})
            snap["tombstones"] = c.execute("SELECT COUNT(*) FROM tombstones WHERE cemetery_uid=?", (cemetery_uid,)).fetchone()[0]
            snap["open_conflicts"] = c.execute("SELECT COUNT(*) FROM open_conflicts WHERE cemetery_uid=?", (cemetery_uid,)).fetchone()[0]
            snap["change_seq"] = c.execute("SELECT MAX(change_seq) FROM registry_row_data WHERE cemetery_uid=?", (cemetery_uid,)).fetchone()[0]
            return snap
        finally:
            c.close()

    # ------------------------------------------------------------ coduri / pairing
    def issue_code(self, actor: str, organization_uid: str, cemetery_uid: Optional[str] = None) -> str:
        c = self._c()
        try:
            self._require(c, actor, "issue_code", organization_uid)
            if cemetery_uid:
                row = c.execute("SELECT organization_uid FROM cemeteries WHERE uid=?", (cemetery_uid,)).fetchone()
                if not row or row["organization_uid"] != organization_uid:
                    raise ScopeError("cimitir inexistent")
            code = secrets.token_urlsafe(12)
            c.execute("INSERT INTO pairing_codes (code_hash, organization_uid, cemetery_uid, issued_by, expires_at) VALUES (?,?,?,?,?)",
                      (_h(code), organization_uid, cemetery_uid, actor, self.clock() + CODE_TTL))
            self._audit(c, actor, "issue_code", organization_uid, cemetery_uid, kind="pair" if cemetery_uid else "provision")
            return code
        finally:
            c.close()

    def _code_row(self, c, code: str, installation_uid: str, allow_used_by: Optional[str] = None):
        fails = c.execute("SELECT n FROM failed_redeems WHERE installation_uid=?", (installation_uid,)).fetchone()
        if fails and fails["n"] >= MAX_FAILED_REDEEMS:
            raise CodeError("prea multe încercări greșite; cere un cod nou")
        row = c.execute("SELECT * FROM pairing_codes WHERE code_hash=?", (_h(code or ""),)).fetchone()
        ok = row is not None and row["expires_at"] > self.clock() and (
            row["used_at"] is None or (allow_used_by is not None and row["used_bootstrap_id"] == allow_used_by))
        if not ok:
            raise CodeError("cod invalid, expirat sau deja folosit")
        return row

    def _note_failed_redeem(self, installation_uid: str) -> None:
        """Contorul de încercări greșite se scrie în afara tranzacției care face ROLLBACK."""
        c = self._c()
        try:
            c.execute("INSERT INTO failed_redeems (installation_uid, n) VALUES (?,1) ON CONFLICT(installation_uid) DO UPDATE SET n=n+1", (installation_uid,))
        finally:
            c.close()

    def preflight_for_code(self, code: str, installation_uid: str, cemetery_uid: str) -> Dict[str, Any]:
        """Starea cimitirului propus/legat, strict în organizația codului (nu consumă codul)."""
        c = self._c()
        try:
            row = self._code_row(c, code, installation_uid)
            if row["cemetery_uid"] and row["cemetery_uid"] != cemetery_uid:
                raise ScopeError("codul nu este valabil pentru acest cimitir")
            cem = c.execute("SELECT organization_uid FROM cemeteries WHERE uid=?", (cemetery_uid,)).fetchone()
            if cem and cem["organization_uid"] != row["organization_uid"]:
                raise ScopeError("cimitir inexistent")
            return {**self._state_of(c, cemetery_uid), "organization_uid": row["organization_uid"]}
        except CodeError:
            self._note_failed_redeem(installation_uid)
            raise
        finally:
            c.close()

    def redeem_code(self, code: str, installation_uid: str, device_name: str, cemetery_uid: str, cemetery_name: str = "",
                    bootstrap_id: Optional[str] = None, pending: bool = False) -> Dict[str, Any]:
        """Consumă codul: creează/leagă cimitirul (UID propus de MAIN pentru coduri de provizionare), dispozitivul și legătura.
        Cheia se întoarce O SINGURĂ DATĂ (în cloud rămâne doar hash). Reluare (același bootstrap + aceeași instalare) = rotește cheia."""
        c = self._c()
        try:
            c.execute("BEGIN IMMEDIATE")
            row = self._code_row(c, code, installation_uid, allow_used_by=bootstrap_id)
            org = row["organization_uid"]
            if row["cemetery_uid"] and row["cemetery_uid"] != cemetery_uid:
                raise ScopeError("codul nu este valabil pentru acest cimitir")
            cem = c.execute("SELECT organization_uid FROM cemeteries WHERE uid=?", (cemetery_uid,)).fetchone()
            if cem is not None and cem["organization_uid"] != org:
                raise ScopeError("cimitir inexistent")
            if bootstrap_id:
                import cimitir_plan_guard as pg
                run = c.execute("SELECT * FROM bootstrap_runs WHERE bootstrap_id=?", (bootstrap_id,)).fetchone()
                pg.check_pairing(self, dict(run) if run else None, row, installation_uid, cemetery_uid, pending)
            if cem is None:
                if row["cemetery_uid"]:
                    raise ScopeError("cimitir inexistent")
                self._insert_cemetery(c, org, cemetery_uid, cemetery_name or "Cimitir")
            dev = c.execute("SELECT device_uid FROM devices WHERE organization_uid=? AND installation_uid=?", (org, installation_uid)).fetchone()
            device_uid = dev["device_uid"] if dev else str(uuid.uuid4())
            if not dev:
                c.execute("INSERT INTO devices VALUES (?,?,?,?)", (device_uid, org, installation_uid, device_name))
            b = c.execute("SELECT binding_uid FROM bindings WHERE device_uid=? AND cemetery_uid=?", (device_uid, cemetery_uid)).fetchone()
            binding_uid = b["binding_uid"] if b else str(uuid.uuid4())
            secret = secrets.token_urlsafe(32)
            key = f"cimitir.{binding_uid}.{secret}"
            if b:
                c.execute("UPDATE bindings SET key_hash=?, status=? WHERE binding_uid=?", (_h(key), "pending" if pending else "active", binding_uid))
            else:
                c.execute("INSERT INTO bindings (binding_uid, device_uid, organization_uid, cemetery_uid, key_hash, status) VALUES (?,?,?,?,?,?)",
                          (binding_uid, device_uid, org, cemetery_uid, _h(key), "pending" if pending else "active"))
            c.execute("UPDATE pairing_codes SET used_at=?, used_bootstrap_id=? WHERE code_hash=?", (self.clock(), bootstrap_id, _h(code)))
            c.execute("DELETE FROM failed_redeems WHERE installation_uid=?", (installation_uid,))
            c.execute("INSERT OR REPLACE INTO binding_issuers (binding_uid, principal_uid) VALUES (?,?)", (binding_uid, row["issued_by"]))
            if bootstrap_id:
                c.execute("UPDATE bootstrap_runs SET binding_uid=?, state='running' WHERE bootstrap_id=? AND cemetery_uid=? AND state IN ('planned','running')",
                          (binding_uid, bootstrap_id, cemetery_uid))
            self._audit(c, device_uid, "redeem_code", org, cemetery_uid, binding_uid=binding_uid)
            c.execute("COMMIT")
            e = self._state_of(c, cemetery_uid)
            return {"organization_uid": org, "cemetery_uid": cemetery_uid, "device_uid": device_uid, "binding_uid": binding_uid,
                    "binding_key": key, "sync_epoch": e["data_epoch"], "last_change_seq": 0}
        except Exception as exc:
            if c.in_transaction:
                c.execute("ROLLBACK")
            if isinstance(exc, CodeError) and "prea multe" not in str(exc):
                self._note_failed_redeem(installation_uid)
            raise
        finally:
            c.close()

    # ------------------------------------------------------------ autentificarea cheii legăturii (scope strict)
    def authenticate(self, binding_key: str, cemetery_uid: Optional[str] = None, allow_pending: bool = False) -> Dict[str, Any]:
        parts = (binding_key or "").split(".")
        c = self._c()
        try:
            row = c.execute("SELECT * FROM bindings WHERE binding_uid=?", (parts[1] if len(parts) == 3 else "",)).fetchone()
            if not row or row["status"] not in (("active", "pending") if allow_pending else ("active",)) or not hmac.compare_digest(row["key_hash"], _h(binding_key)):
                raise ScopeError("legătură invalidă")
            if cemetery_uid is not None and cemetery_uid != row["cemetery_uid"]:
                raise ScopeError("cimitir inexistent")  # cheia nu are voie pe alt cimitir
            return dict(row)
        finally:
            c.close()

    def revoke_binding(self, actor: str, organization_uid: str, binding_uid: str) -> None:
        c = self._c()
        try:
            self._require(c, actor, "revoke_binding", organization_uid)
            n = c.execute("UPDATE bindings SET status='revoked' WHERE binding_uid=? AND organization_uid=?", (binding_uid, organization_uid)).rowcount
            if not n:
                raise ScopeError("legătură inexistentă")
            self._audit(c, actor, "revoke_binding", organization_uid, binding_uid=binding_uid)
        finally:
            c.close()

    # ------------------------------------------------------------ bootstrap_runs (cloud)
    def _expire_planned(self, c) -> None:
        c.execute("UPDATE bootstrap_runs SET state='rolled_back', error='plan expirat', finished_at=? WHERE state='planned' AND created_at<?",
                  (self.clock(), self.clock() - PLAN_TTL))

    def begin_run(self, code: str, installation_uid: str, bootstrap_id: str, kind: str, cemetery_uid: str, plan_hash: str,
                  plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """`plan` (Pasul 5.1) = preimaginea plan_hash: validată pe server ÎNAINTE de orice scriere (obligatorie prin HTTP)."""
        c = self._c()
        try:
            c.execute("BEGIN IMMEDIATE")
            row = self._code_row(c, code, installation_uid, allow_used_by=bootstrap_id)
            if row["cemetery_uid"] and row["cemetery_uid"] != cemetery_uid:
                raise ScopeError("codul nu este valabil pentru acest cimitir")
            owner = c.execute("SELECT organization_uid FROM cemeteries WHERE uid=?", (cemetery_uid,)).fetchone()
            if owner and owner["organization_uid"] != row["organization_uid"]:
                raise ScopeError("cimitirul aparține altei organizații")
            if plan is None:
                self._expire_planned(c)
            existing = c.execute("SELECT * FROM bootstrap_runs WHERE bootstrap_id=?", (bootstrap_id,)).fetchone()
            meta = None
            if plan is not None:
                import cimitir_plan_guard as pg
                meta = pg.check_begin(self, c, row, installation_uid, bootstrap_id, kind, cemetery_uid, plan_hash, plan, dict(existing) if existing else None)
            if existing:
                if existing["plan_hash"] != plan_hash or existing["cemetery_uid"] != cemetery_uid:
                    raise ConflictError("bootstrap_id existent cu alt plan")
            else:
                if plan is not None:
                    self._expire_planned(c)
                try:
                    c.execute("INSERT INTO bootstrap_runs (bootstrap_id, organization_uid, cemetery_uid, kind, installation_uid, plan_hash, state, checkpoint, created_at)"
                              " VALUES (?,?,?,?,?,?,'planned',?,?)",
                              (bootstrap_id, row["organization_uid"], cemetery_uid, kind, installation_uid, plan_hash, json.dumps(meta or {}, sort_keys=True), self.clock()))
                except sqlite3.IntegrityError:
                    raise BootstrapLocked("un alt bootstrap este activ pentru acest cimitir") from None
            c.execute("COMMIT")
            return self.get_run(bootstrap_id)
        except Exception as exc:
            if c.in_transaction:
                c.execute("ROLLBACK")
            if isinstance(exc, CodeError) and "prea multe" not in str(exc):
                self._note_failed_redeem(installation_uid)
            raise
        finally:
            c.close()

    def resume_run(self, binding_key: str, bootstrap_id: str, plan_hash: str) -> Dict[str, Any]:
        """Reluare după redeem: autentificare doar cu cheia legăturii (codul poate fi expirat), același plan_hash, run neterminat sau completat."""
        b = self.authenticate(binding_key, allow_pending=True)
        run = self.get_run(bootstrap_id)
        if run["cemetery_uid"] != b["cemetery_uid"] or run["plan_hash"] != plan_hash:
            raise ScopeError("bootstrap inexistent")
        if run["state"] not in ACTIVE_RUN_STATES + ("completed",):
            raise ConflictError(f"bootstrap {run['state']}")
        return run

    def get_run(self, bootstrap_id: str) -> Dict[str, Any]:
        c = self._c()
        try:
            row = c.execute("SELECT * FROM bootstrap_runs WHERE bootstrap_id=?", (bootstrap_id,)).fetchone()
            if not row:
                raise ScopeError("bootstrap inexistent")
            return dict(row)
        finally:
            c.close()

    def _transition(self, binding_key: str, bootstrap_id: str, allowed: tuple, new: str, error: Optional[str] = None) -> Dict[str, Any]:
        b = self.authenticate(binding_key, allow_pending=True)
        c = self._c()
        try:
            run = c.execute("SELECT * FROM bootstrap_runs WHERE bootstrap_id=? AND cemetery_uid=?", (bootstrap_id, b["cemetery_uid"])).fetchone()
            if not run:
                raise ScopeError("bootstrap inexistent")
            if run["state"] == new:
                return dict(run)  # idempotent
            if run["state"] not in allowed:
                raise ConflictError(f"tranziție invalidă {run['state']} -> {new}")
            c.execute("UPDATE bootstrap_runs SET state=?, error=?, finished_at=? WHERE bootstrap_id=?",
                      (new, error, self.clock() if new in ("completed", "failed", "rolled_back") else None, bootstrap_id))
            self._audit(c, b["device_uid"], f"bootstrap_{new}", b["organization_uid"], b["cemetery_uid"], bootstrap_id=bootstrap_id)
            return dict(c.execute("SELECT * FROM bootstrap_runs WHERE bootstrap_id=?", (bootstrap_id,)).fetchone())
        finally:
            c.close()

    def mark_verifying(self, binding_key: str, bootstrap_id: str):
        return self._transition(binding_key, bootstrap_id, ("running",), "verifying")

    def verify_empty(self, binding_key: str, bootstrap_id: str) -> Dict[str, Any]:
        b = self.authenticate(binding_key, allow_pending=True)
        c = self._c()
        try:
            return self._state_of(c, b["cemetery_uid"])
        finally:
            c.close()

    def complete_run(self, binding_key: str, bootstrap_id: str) -> Dict[str, Any]:
        import cimitir_plan_guard as pg
        b = self.authenticate(binding_key, allow_pending=True)
        c = self._c()
        try:
            cur = c.execute("SELECT * FROM bootstrap_runs WHERE bootstrap_id=? AND cemetery_uid=?", (bootstrap_id, b["cemetery_uid"])).fetchone()
            if cur and cur["state"] != "completed":
                pg.check_complete(self, c, dict(cur))
        finally:
            c.close()
        run = self._transition(binding_key, bootstrap_id, ("verifying",), "completed")
        c = self._c()
        try:
            c.execute("UPDATE cemeteries SET status='ACTIVE', data_epoch=1 WHERE uid=? AND status IN ('DRAFT','ACTIVE')", (run["cemetery_uid"],))
        finally:
            c.close()
        return run

    def fail_run(self, binding_key: str, bootstrap_id: str, error: str):
        return self._transition(binding_key, bootstrap_id, ("planned", "running", "verifying"), "failed", error)

    def rollback_run(self, binding_key: str, bootstrap_id: str) -> Dict[str, Any]:
        """Revocă legătura creată de acest bootstrap; cimitirul rămâne DRAFT (arhivabil de Owner), deci Flux A se poate reîncerca."""
        b = self.authenticate(binding_key, allow_pending=True)
        run = self._transition(binding_key, bootstrap_id, ("planned", "running", "verifying", "failed"), "rolled_back")
        c = self._c()
        try:
            c.execute("UPDATE bindings SET status='revoked' WHERE binding_uid=?", (b["binding_uid"],))
        finally:
            c.close()
        return run

    def revoke_by_key(self, binding_key: str) -> None:
        """Rollback după ce run-ul a fost deja `completed` în cloud: legătura se revocă (cimitirul rămâne ACTIVE; ACTIVE+gol cere aprobare)."""
        b = self.authenticate(binding_key, allow_pending=True)
        c = self._c()
        try:
            c.execute("UPDATE bindings SET status='revoked' WHERE binding_uid=?", (b["binding_uid"],))
            self._audit(c, b["device_uid"], "revoke_after_completed_run", b["organization_uid"], b["cemetery_uid"], binding_uid=b["binding_uid"])
        finally:
            c.close()

    def mark_failed_unauthenticated(self, bootstrap_id: str, installation_uid: str, error: str) -> None:
        """Rollback FĂRĂ cheie (cheia nu a ajuns în SecretStore: cădere după redeem sau înainte de el): autorizat de `bootstrap_id` (UUID aleator)
        + `installation_uid` al run-ului. Revocă legătura creată de acest run (dacă există) și închide run-ul, ca să nu rămână legături orfane."""
        c = self._c()
        try:
            run = c.execute("SELECT * FROM bootstrap_runs WHERE bootstrap_id=? AND installation_uid=? AND state IN ('planned','running','verifying')",
                            (bootstrap_id, installation_uid)).fetchone()
            if not run:
                return
            if run["binding_uid"]:
                c.execute("UPDATE bindings SET status='revoked' WHERE binding_uid=?", (run["binding_uid"],))
            c.execute("UPDATE bootstrap_runs SET state='rolled_back', error=?, finished_at=? WHERE bootstrap_id=?", (error[:500], self.clock(), bootstrap_id))
            self._audit(c, installation_uid, "bootstrap_rolled_back_without_key", run["organization_uid"], run["cemetery_uid"], bootstrap_id=bootstrap_id)
        finally:
            c.close()
