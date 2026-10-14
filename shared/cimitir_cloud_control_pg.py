"""cimitir_cloud_control_pg.py — planul de control Etapa 4 peste PostgreSQL (Pasul 6A): aceeași interfață ca `LocalControlPlane`, altă persistență.

Tenancy-ul rămâne cel canonic (008–013: organizations, cemeteries, devices, device_cemetery_bindings, device_pairing_codes); tabelele `cp_*` (migrarea 014)
sunt doar starea bootstrap-ului. Logica de bootstrap (plan guard, staging, publish, Flux A/B, transport HTTP) NU e rescrisă: modulele existente rulează peste
`PgConn`, un strat subțire care imită `sqlite3` (`?`, rânduri cu nume, IntegrityError, tranzacții explicite) și redenumește tabelele de bootstrap `cp_*`.

Rol runtime: `cimitir_cp_app` (LOGIN, NOBYPASSRLS). Contextul RLS = GUC `cp.*`, setat pe conexiune DUPĂ ce serverul a autentificat/autorizat cererea
(`_require`, `_code_row`, `authenticate`); fără context, politicile refuză tot. URL-urile se citesc DOAR din mediu (CIMITIR_CP_DATABASE_URL, opțional
CIMITIR_CP_ADMIN_DATABASE_URL pentru seed/test și CIMITIR_CP_SCHEMA); nu se loghează și nu apar în mesaje de eroare.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

import psycopg
from psycopg.types.string import TextLoader

import cimitir_cloud_control as cc
import cimitir_manifest as mf

PROTECTED_PROJECT_REFS = ("txuxkjgftopznvmooexq",)
TEST_PROJECT_REF = "nhhczdmcocbobptogvsw"
CLOUD_REF_ENV = "CIMITIR_CLOUD_PROJECT_REF"      # deploy cloud: singurul proiect Supabase (neprotejat) acceptat de serverul public
_CTX_KEYS = {"platform": "cp.platform", "org": "cp.org", "cems": "cp.cems", "principal": "cp.principal", "binding_uid": "cp.binding_uid",
             "key_hash": "cp.key_hash", "code_hash": "cp.code_hash", "installation": "cp.installation", "run_id": "cp.run_id",
             "pair_buckets": "cp.pair_buckets"}
_TABLES = {"bootstrap_runs": "cp_bootstrap_runs", "staging_sessions": "cp_staging_sessions", "staging_batches": "cp_staging_batches",
           "staging_rows": "cp_staging_rows", "staging_media_blobs": "cp_staging_media_blobs", "staging_media_chunks": "cp_staging_media_chunks",
           "registry_rows": "cp_registry_rows", "registry_row_data": "cp_registry_row_data", "media_objects": "cp_media_objects",
           "media_blobs": "cp_media_blobs", "media_published": "cp_media_published", "open_conflicts": "cp_open_conflicts", "tombstones": "cp_tombstones",
           "failed_redeems": "cp_failed_redeems", "audit_log": "cp_audit_log", "binding_issuers": "cp_binding_issuers",
           "pairing_codes": "device_pairing_codes", "bindings": "device_cemetery_bindings"}
_TABLE_RE = re.compile(r"\b(" + "|".join(sorted(_TABLES, key=len, reverse=True)) + r")\b")
# INSERT OR REPLACE: (coloane în ordinea din DDL fără organization_uid, cheia de conflict)
_REPLACE = {"cp_registry_row_data": (("cemetery_uid", "entity", "uid", "version", "content", "change_seq"), ("cemetery_uid", "entity", "uid")),
            "cp_media_objects": (("cemetery_uid", "uid", "kind", "entity_type", "entity_id", "name", "size", "sha256", "present"), ("cemetery_uid", "uid")),
            "cp_staging_media_blobs": (("bootstrap_id", "cemetery_uid", "uid", "sha256", "size", "chunk_count", "state"), ("bootstrap_id", "uid"))}
_BEGIN_RE = re.compile(r"^\s*BEGIN(\s+(IMMEDIATE|EXCLUSIVE|DEFERRED))?\s*;?\s*$", re.I)
_WRITE_RE = re.compile(r"^\s*(INSERT|UPDATE|DELETE)\b", re.I)


class Row(tuple):
    def __new__(cls, values, index):
        o = super().__new__(cls, values)
        o._index = index
        return o

    def keys(self) -> List[str]:
        return list(self._index)

    def __getitem__(self, k):
        return tuple.__getitem__(self, self._index[k] if isinstance(k, str) else k)


def _row_factory(cur):
    index = {d.name: i for i, d in enumerate(cur.description or [])}
    return lambda values: Row(values, index)


def _bind(sql: str) -> str:
    out, in_str = [], False
    for ch in sql:
        if ch == "'":
            in_str = not in_str
            out.append(ch)
        elif ch == "%":
            out.append("%%")
        elif ch == "?" and not in_str:
            out.append("%s")
        else:
            out.append(ch)
    return "".join(out)


def translate(sql: str, nparams: int = 0) -> str:
    text = sql.strip().rstrip(";")
    text = _TABLE_RE.sub(lambda m: _TABLES[m.group(1)], text)
    suffix = ""
    if re.match(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO", text, re.I):
        text, suffix = re.sub(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", text, count=1, flags=re.I), " ON CONFLICT DO NOTHING"
    else:
        m = re.match(r"^\s*INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)\s*(\(([^)]*)\))?", text, re.I)
        if m:
            table = m.group(1)
            cols_all, keys = _REPLACE[table]
            cols = [c.strip() for c in m.group(3).split(",")] if m.group(3) else list(cols_all[:nparams])
            text = text[:m.start()] + f"INSERT INTO {table} ({', '.join(cols)})" + text[m.end():]
            upd = [c for c in cols if c not in keys]
            suffix = f" ON CONFLICT ({', '.join(keys)}) DO " + (("UPDATE SET " + ", ".join(f"{c}=EXCLUDED.{c}" for c in upd)) if upd else "NOTHING")
    return _bind(text) + suffix


class _Cur:
    def __init__(self, raw):
        self._raw, self.rowcount, self.description = raw, raw.rowcount if raw is not None else 0, raw.description if raw is not None else None

    def fetchone(self):
        return self._raw.fetchone() if self._raw is not None and self._raw.description else None

    def fetchall(self):
        return self._raw.fetchall() if self._raw is not None and self._raw.description else []

    def __iter__(self):
        return iter(self.fetchall())


class PgConn:
    """Conexiune cu semantică sqlite3 (autocommit; tranzacții explicite; IntegrityError sqlite3; statement-level atomicity prin SAVEPOINT)."""

    def __init__(self, raw, cp):
        self._raw, self._cp = raw, cp
        raw.adapters.register_loader("uuid", TextLoader)
        raw.adapters.register_loader("jsonb", TextLoader)
        raw.adapters.register_loader("json", TextLoader)
        self.row_factory = None

    @property
    def in_transaction(self) -> bool:
        return self._raw.info.transaction_status != psycopg.pq.TransactionStatus.IDLE

    def set_ctx(self, **kw) -> None:
        for k, v in kw.items():
            self._raw.execute("SELECT set_config(%s, %s, false)", (_CTX_KEYS[k], "" if v is None else str(v)))
            self._cp._tls.ctx[k] = v

    def execute(self, sql: str, params=()) -> _Cur:
        if re.match(r"^\s*PRAGMA\b", sql, re.I):
            return _Cur(None)
        if _BEGIN_RE.match(sql):
            self._raw.execute("BEGIN")
            return _Cur(None)
        params = tuple(params) if params is not None else ()
        text = translate(sql, len(params))
        in_txn = self.in_transaction and _WRITE_RE.match(text)
        cur = self._raw.cursor(row_factory=_row_factory)
        if in_txn:
            self._raw.execute("SAVEPOINT cp_stmt")
        try:
            cur.execute(text, params)
        except psycopg.errors.IntegrityError as exc:
            if in_txn:
                self._raw.execute("ROLLBACK TO SAVEPOINT cp_stmt")
            raise sqlite3.IntegrityError(exc.diag.message_primary or "integrity") from exc
        except psycopg.Error:
            if in_txn:
                self._raw.execute("ROLLBACK TO SAVEPOINT cp_stmt")
            raise
        if in_txn:
            self._raw.execute("RELEASE SAVEPOINT cp_stmt")
        return _Cur(cur)

    def close(self) -> None:
        try:
            if self.in_transaction:
                self._raw.execute("ROLLBACK")
        finally:
            self._raw.close()


def _guard(url: Optional[str]) -> None:
    if not url:
        return
    for ref in PROTECTED_PROJECT_REFS:
        if ref in url:
            raise RuntimeError("proiectul de producție este blocat permanent pentru planul de control")
    if "supabase.com" in url or "supabase.co" in url:
        m = re.search(r"//[^:@/]+\.([a-z]{20})(?::|@)", url) or re.search(r"//[^/@]*@db\.([a-z]{20})\.supabase\.co", url)
        ref = m.group(1) if m else ""
        if ref and ref == os.environ.get(CLOUD_REF_ENV, ""):
            return
        if os.environ.get("CIMITIR_LIVE_TESTS") != "1" or os.environ.get("CIMITIR_TEST_PROJECT_REF") != TEST_PROJECT_REF or ref != TEST_PROJECT_REF:
            raise RuntimeError("conexiune Supabase refuzată: CIMITIR_LIVE_TESTS=1 și CIMITIR_TEST_PROJECT_REF=<ref TEST> obligatorii, iar ref-ul trebuie să coincidă")


class PostgresControlPlane(cc.LocalControlPlane):
    schema_managed = True
    _FRESH = ("create_organization", "grant_role", "create_cemetery", "inspect_as", "export_snapshot", "issue_code", "preflight_for_code", "redeem_code",
              "authenticate", "revoke_binding", "begin_run", "resume_run", "get_run", "mark_verifying", "verify_empty", "complete_run", "fail_run",
              "rollback_run", "revoke_by_key", "mark_failed_unauthenticated", "can", "code_row", "binding_issuer", "run_state")
    _SIM = ("sim_put_row", "sim_delete_row", "sim_put_media", "sim_set_status", "sim_add_conflict")

    def __init__(self, url: Optional[str] = None, clock: Callable[[], float] = time.time, schema: Optional[str] = None, admin_url: Optional[str] = None):
        self.url = url or os.environ["CIMITIR_CP_DATABASE_URL"]
        self.admin_url = admin_url or os.environ.get("CIMITIR_CP_ADMIN_DATABASE_URL")
        self.schema = schema or os.environ.get("CIMITIR_CP_SCHEMA")
        _guard(self.url)
        _guard(self.admin_url)
        self.clock, self.path = clock, None
        self._tls = threading.local()
        for name in self._FRESH:
            setattr(self, name, self._fresh(getattr(self, name)))
        for name in self._SIM:
            setattr(self, name, self._admin(getattr(self, name)))

    # ------------------------------------------------------------------ conexiuni / context
    @property
    def _t(self):
        if not hasattr(self._tls, "ctx"):
            self._tls.ctx, self._tls.admin = {}, False
        return self._tls

    def begin_request(self) -> None:
        self._tls.ctx, self._tls.admin = {}, False

    def _fresh(self, fn):
        def wrapper(*a, **k):
            outer = self._t.admin
            self._tls.ctx = {}
            try:
                return fn(*a, **k)
            finally:
                self._tls.admin = outer
        return wrapper

    def _admin(self, fn):
        def wrapper(*a, **k):
            self._t.admin = True
            try:
                return fn(*a, **k)
            finally:
                self._tls.admin = False
        return wrapper

    def _c(self, **ctx) -> PgConn:
        t = self._t
        url = self.admin_url if t.admin else self.url
        if not url:
            raise RuntimeError("CIMITIR_CP_ADMIN_DATABASE_URL nu este setat (necesar pentru seed/teste)")
        kw = {"options": f"-c search_path={self.schema}"} if self.schema else {}
        c = PgConn(psycopg.connect(url, autocommit=True, connect_timeout=15, **kw), self)
        if not t.admin:
            t.ctx.update(ctx)
            c.set_ctx(**t.ctx)
        return c

    def _c_ro(self) -> PgConn:
        c = self._c()
        c.execute("BEGIN")
        c._raw.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        return c

    def _lock_cemetery(self, c, cemetery_uid: str) -> None:
        c.execute("SELECT cp_lock_cemetery(?)", (cemetery_uid,))

    def _cemetery_org(self, c, cemetery_uid: str) -> Optional[str]:
        return c.execute("SELECT cp_cemetery_org(?)", (cemetery_uid,)).fetchone()[0]

    # ------------------------------------------------------------------ principals / roluri
    def create_principal(self, name: str, platform_admin: bool = False) -> str:
        self._t.admin = True
        try:
            uid, c = str(uuid.uuid4()), self._c()
            try:
                c.execute("INSERT INTO identities (uid, display_name, password_hash) VALUES (?,?,'!')", (uid, name))
                if platform_admin:
                    c.execute("INSERT INTO cp_platform_admins (identity_uid) VALUES (?)", (uid,))
            finally:
                c.close()
            return uid
        finally:
            self._tls.admin = False

    def _roles(self, c, principal: str, org: str) -> set:
        c.set_ctx(principal=principal)
        return {r["role"] for r in c.execute("SELECT role FROM cp_org_roles WHERE identity_uid=? AND organization_uid=?", (principal, org))}

    def can(self, principal: str, action: str, organization_uid: str) -> bool:
        c = self._c()
        try:
            return bool(self._roles(c, principal, organization_uid) & cc.PERMISSIONS[action])
        finally:
            c.close()

    def _require(self, c, principal: str, action: str, org: str) -> None:
        c.set_ctx(principal=principal, org=None, cems=None, platform=None)
        if not c.execute("SELECT cp_org_exists(?)", (org,)).fetchone()[0]:
            raise cc.ScopeError("organizație inexistentă")
        if not (self._roles(c, principal, org) & cc.PERMISSIONS[action]):
            raise cc.PermissionDenied(f"{action}: rol insuficient în organizație")
        c.set_ctx(org=org, cems="*", principal=principal)

    def create_organization(self, actor: str, organization_uid: str, name: str, first_owner: Optional[str] = None) -> None:
        c = self._c(principal=actor)
        try:
            if not c.execute("SELECT 1 FROM cp_platform_admins WHERE identity_uid=?", (actor,)).fetchone():
                raise cc.PermissionDenied("doar Platform Admin creează organizații")
            uuid.UUID(organization_uid)
            c.set_ctx(platform="1", org=organization_uid)
            try:
                c.execute("INSERT INTO organizations (uid, organization_type, official_name, display_name) VALUES (?, 'OTHER', ?, ?)", (organization_uid, name, name))
            except sqlite3.IntegrityError:
                raise cc.ConflictError("organizația există deja (UID-urile nu se reutilizează)") from None
            if first_owner:
                c.execute("INSERT INTO cp_org_roles (identity_uid, organization_uid, role) VALUES (?,?,?) ON CONFLICT DO NOTHING", (first_owner, organization_uid, cc.OWNER))
            self._audit(c, actor, "create_organization", organization_uid)
        finally:
            c.close()

    def grant_role(self, actor: str, principal: str, organization_uid: str, role: str) -> None:
        c = self._c()
        try:
            self._require(c, actor, "grant_role", organization_uid)
            if role not in (cc.REBUILD, cc.ADMIN):
                raise cc.PermissionDenied("Owner poate acorda doar REBUILD/ADMIN; Owner nou = Platform Admin")
            c.execute("INSERT INTO cp_org_roles (identity_uid, organization_uid, role) VALUES (?,?,?) ON CONFLICT DO NOTHING", (principal, organization_uid, role))
            self._audit(c, actor, "grant_role", organization_uid, role=role, principal=principal)
        finally:
            c.close()

    # ------------------------------------------------------------------ cimitire
    def _insert_cemetery(self, c, org: str, uid: str, name: str) -> None:
        uuid.UUID(uid)
        if self._cemetery_org(c, uid):
            raise cc.ConflictError("UID de cimitir deja folosit (definitiv, nu se reutilizează)")
        try:
            c.execute("INSERT INTO cemeteries (uid, organization_uid, name, internal_code, status, data_epoch) VALUES (?,?,?,?,'DRAFT',1)", (uid, org, name, uid))
        except sqlite3.IntegrityError:
            raise cc.ConflictError("UID de cimitir deja folosit (definitiv, nu se reutilizează)") from None

    # ------------------------------------------------------------------ coduri / pairing (device_pairing_codes canonic)
    def issue_code(self, actor: str, organization_uid: str, cemetery_uid: Optional[str] = None) -> str:
        c = self._c()
        try:
            self._require(c, actor, "issue_code", organization_uid)
            if cemetery_uid and self._cemetery_org(c, cemetery_uid) != organization_uid:
                raise cc.ScopeError("cimitir inexistent")
            code = secrets.token_urlsafe(12)
            c.execute("INSERT INTO device_pairing_codes (code_hash, organization_uid, cemetery_uid, created_by, expires_at) VALUES (?,?,?,?,to_timestamp(?))",
                      (cc._h(code), organization_uid, cemetery_uid, actor, self.clock() + cc.CODE_TTL))
            self._audit(c, actor, "issue_code", organization_uid, cemetery_uid, kind="pair" if cemetery_uid else "provision")
            return code
        finally:
            c.close()

    _CODE_SQL = ("SELECT code_hash, organization_uid, cemetery_uid, created_by AS issued_by, EXTRACT(EPOCH FROM expires_at)::float8 AS expires_at, "
                 "EXTRACT(EPOCH FROM used_at)::float8 AS used_at, used_bootstrap_id FROM device_pairing_codes WHERE code_hash=? AND device_type='MAIN'")

    def _code_row(self, c, code: str, installation_uid: str, allow_used_by: Optional[str] = None):
        c.set_ctx(installation=installation_uid, code_hash=cc._h(code or ""), org=None, cems=None, platform=None)
        fails = c.execute("SELECT n FROM failed_redeems WHERE installation_uid=?", (installation_uid,)).fetchone()
        if fails and fails["n"] >= cc.MAX_FAILED_REDEEMS:
            raise cc.CodeError("prea multe încercări greșite; cere un cod nou")
        row = c.execute(self._CODE_SQL, (cc._h(code or ""),)).fetchone()
        ok = row is not None and row["expires_at"] > self.clock() and (
            row["used_at"] is None or (allow_used_by is not None and row["used_bootstrap_id"] == allow_used_by))
        if not ok:
            raise cc.CodeError("cod invalid, expirat sau deja folosit")
        c.set_ctx(org=row["organization_uid"], cems="*")
        return row

    def code_row(self, secret: str):
        c = self._c(code_hash=cc._h(secret))
        try:
            row = c.execute(self._CODE_SQL, (cc._h(secret),)).fetchone()
            return row
        finally:
            c.close()

    def _note_failed_redeem(self, installation_uid: str) -> None:
        c = self._c(installation=installation_uid)
        try:
            c.execute("INSERT INTO failed_redeems (installation_uid, n) VALUES (?,1) ON CONFLICT(installation_uid) DO UPDATE SET n=cp_failed_redeems.n+1", (installation_uid,))
        finally:
            c.close()

    def redeem_code(self, code: str, installation_uid: str, device_name: str, cemetery_uid: str, cemetery_name: str = "",
                    bootstrap_id: Optional[str] = None, pending: bool = False) -> Dict[str, Any]:
        c = self._c()
        try:
            c.execute("BEGIN")
            self._lock_cemetery(c, cemetery_uid)
            row = self._code_row(c, code, installation_uid, allow_used_by=bootstrap_id)
            org = row["organization_uid"]
            if row["cemetery_uid"] and row["cemetery_uid"] != cemetery_uid:
                raise cc.ScopeError("codul nu este valabil pentru acest cimitir")
            owner_org = self._cemetery_org(c, cemetery_uid)
            if owner_org is not None and owner_org != org:
                raise cc.ScopeError("cimitir inexistent")
            if bootstrap_id:
                import cimitir_plan_guard as pg
                run = c.execute("SELECT * FROM bootstrap_runs WHERE bootstrap_id=?", (bootstrap_id,)).fetchone()
                c.set_ctx(run_id=bootstrap_id)
                pg.check_pairing(self, dict(run) if run else None, row, installation_uid, cemetery_uid, pending)
            if owner_org is None:
                if row["cemetery_uid"]:
                    raise cc.ScopeError("cimitir inexistent")
                self._insert_cemetery(c, org, cemetery_uid, cemetery_name or "Cimitir")
            dev = c.execute("SELECT device_uid FROM devices WHERE organization_uid=? AND installation_uid=?", (org, installation_uid)).fetchone()
            device_uid = dev["device_uid"] if dev else str(uuid.uuid4())
            if not dev:
                c.execute("INSERT INTO devices (device_uid, organization_uid, installation_uid, device_name, device_type) VALUES (?,?,?,?,'MAIN')",
                          (device_uid, org, installation_uid, device_name or "MAIN"))
            b = c.execute("SELECT binding_uid FROM bindings WHERE device_uid=? AND cemetery_uid=? AND revoked_at IS NULL", (device_uid, cemetery_uid)).fetchone()
            binding_uid = b["binding_uid"] if b else str(uuid.uuid4())
            key = f"cimitir.{binding_uid}.{secrets.token_urlsafe(32)}"
            if b:
                c.execute("UPDATE bindings SET sync_key_hash=?, key_prefix=?, active=? WHERE binding_uid=?", (cc._h(key), key[:16], not pending, binding_uid))
            else:
                old = c.execute("SELECT binding_uid FROM bindings WHERE device_uid=? AND cemetery_uid=? AND revoked_at IS NOT NULL ORDER BY revoked_at DESC LIMIT 1",
                                (device_uid, cemetery_uid)).fetchone()
                c.execute("INSERT INTO bindings (binding_uid, device_uid, organization_uid, cemetery_uid, sync_key_hash, key_prefix, active) VALUES (?,?,?,?,?,?,?)",
                          (binding_uid, device_uid, org, cemetery_uid, cc._h(key), key[:16], not pending))
                if old:
                    c.execute("UPDATE bindings SET replaced_by_binding_uid=? WHERE binding_uid=?", (binding_uid, old["binding_uid"]))
            c.execute("UPDATE pairing_codes SET used_at=to_timestamp(?), used_bootstrap_id=?, used_by_binding_uid=? WHERE code_hash=?",
                      (self.clock(), bootstrap_id, binding_uid, cc._h(code)))
            c.execute("DELETE FROM failed_redeems WHERE installation_uid=?", (installation_uid,))
            c.execute("INSERT INTO binding_issuers (binding_uid, organization_uid, principal_uid) VALUES (?,?,?) "
                      "ON CONFLICT (binding_uid) DO UPDATE SET principal_uid=EXCLUDED.principal_uid", (binding_uid, org, row["issued_by"]))
            if bootstrap_id:
                c.execute("UPDATE bootstrap_runs SET binding_uid=?, state='running', started_at=COALESCE(started_at, ?) WHERE bootstrap_id=? AND cemetery_uid=? "
                          "AND state IN ('planned','running')", (binding_uid, self.clock(), bootstrap_id, cemetery_uid))
            self._audit(c, device_uid, "redeem_code", org, cemetery_uid, binding_uid=binding_uid)
            c.execute("COMMIT")
            return {"organization_uid": org, "cemetery_uid": cemetery_uid, "device_uid": device_uid, "binding_uid": binding_uid, "binding_key": key,
                    "sync_epoch": self._state_of(c, cemetery_uid)["data_epoch"], "last_change_seq": 0}
        except Exception as exc:
            if c.in_transaction:
                c.execute("ROLLBACK")
            if isinstance(exc, cc.CodeError) and "prea multe" not in str(exc):
                self._note_failed_redeem(installation_uid)
            raise
        finally:
            c.close()

    # ------------------------------------------------------------------ autentificarea cheii legăturii
    def authenticate(self, binding_key: str, cemetery_uid: Optional[str] = None, allow_pending: bool = False) -> Dict[str, Any]:
        parts = (binding_key or "").split(".")
        try:
            buid = str(uuid.UUID(parts[1])) if len(parts) == 3 else ""
        except ValueError:
            buid = ""
        if not buid:
            raise cc.ScopeError("legătură invalidă")
        c = self._c(binding_uid=buid, key_hash=cc._h(binding_key))
        try:
            row = c.execute("SELECT binding_uid, device_uid, organization_uid, cemetery_uid, sync_key_hash AS key_hash, last_change_seq, active, "
                            "(revoked_at IS NOT NULL) AS revoked FROM bindings WHERE binding_uid=?", (buid,)).fetchone()
            status = None if not row else ("revoked" if row["revoked"] else "active" if row["active"] else "pending")
            if not row or status not in (("active", "pending") if allow_pending else ("active",)) or not hmac.compare_digest(row["key_hash"], cc._h(binding_key)):
                raise cc.ScopeError("legătură invalidă")
            if cemetery_uid is not None and cemetery_uid != row["cemetery_uid"]:
                raise cc.ScopeError("cimitir inexistent")
            c.set_ctx(org=row["organization_uid"], cems=row["cemetery_uid"])
            epoch = c.execute("SELECT data_epoch FROM cemeteries WHERE uid=?", (row["cemetery_uid"],)).fetchone()
            return {"binding_uid": row["binding_uid"], "device_uid": row["device_uid"], "organization_uid": row["organization_uid"], "cemetery_uid": row["cemetery_uid"],
                    "key_hash": row["key_hash"], "status": status, "sync_epoch": epoch["data_epoch"] if epoch else 1, "last_change_seq": row["last_change_seq"]}
        finally:
            c.close()

    def activate_binding(self, c, binding_uid: str, epoch: int) -> None:
        c.execute("UPDATE bindings SET active=true WHERE binding_uid=?", (binding_uid,))

    def binding_issuer(self, binding_uid: str) -> Optional[str]:
        c = self._c(binding_uid=binding_uid)
        try:
            r = c.execute("SELECT principal_uid FROM binding_issuers WHERE binding_uid=?", (binding_uid,)).fetchone()
            return r["principal_uid"] if r else None
        finally:
            c.close()

    def revoke_binding(self, actor: str, organization_uid: str, binding_uid: str) -> None:
        c = self._c()
        try:
            self._require(c, actor, "revoke_binding", organization_uid)
            n = c.execute("UPDATE bindings SET active=false, revoked_at=COALESCE(revoked_at, now()), revoked_by=COALESCE(revoked_by, ?) WHERE binding_uid=? "
                          "AND organization_uid=?", (actor, binding_uid, organization_uid)).rowcount
            if not n:
                raise cc.ScopeError("legătură inexistentă")
            self._audit(c, actor, "revoke_binding", organization_uid, binding_uid=binding_uid)
        finally:
            c.close()

    _REVOKE = "UPDATE bindings SET active=false, revoked_at=COALESCE(revoked_at, now()) WHERE binding_uid=?"

    def rollback_run(self, binding_key: str, bootstrap_id: str) -> Dict[str, Any]:
        b = self.authenticate(binding_key, allow_pending=True)
        run = self._transition(binding_key, bootstrap_id, ("planned", "running", "verifying", "failed"), "rolled_back")
        c = self._c(org=b["organization_uid"], cems=b["cemetery_uid"], binding_uid=b["binding_uid"], key_hash=cc._h(binding_key))
        try:
            c.execute(self._REVOKE, (b["binding_uid"],))
        finally:
            c.close()
        return run

    def revoke_by_key(self, binding_key: str) -> None:
        b = self.authenticate(binding_key, allow_pending=True)
        c = self._c(org=b["organization_uid"], cems=b["cemetery_uid"], binding_uid=b["binding_uid"], key_hash=cc._h(binding_key))
        try:
            c.execute(self._REVOKE, (b["binding_uid"],))
            self._audit(c, b["device_uid"], "revoke_after_completed_run", b["organization_uid"], b["cemetery_uid"], binding_uid=b["binding_uid"])
        finally:
            c.close()

    # ------------------------------------------------------------------ bootstrap runs
    def _record_manifest_target(self, c, bootstrap_id: str, summary: Dict[str, Any]) -> None:
        c.execute("UPDATE bootstrap_runs SET manifest_target=?::jsonb WHERE bootstrap_id=?", (json.dumps(summary, sort_keys=True), bootstrap_id))

    def begin_run(self, code, installation_uid, bootstrap_id, kind, cemetery_uid, plan_hash, plan=None):
        run = cc.LocalControlPlane.begin_run(self, code, installation_uid, bootstrap_id, kind, cemetery_uid, plan_hash, plan)
        if plan and plan.get("local_manifest") is not None:
            c = self._c(run_id=bootstrap_id, installation=installation_uid, org=run["organization_uid"], cems=cemetery_uid)
            try:
                c.execute("UPDATE bootstrap_runs SET manifest_source=?::jsonb WHERE bootstrap_id=? AND manifest_source IS NULL",
                          (json.dumps(mf.summary(plan["local_manifest"]), sort_keys=True), bootstrap_id))
            finally:
                c.close()
        return run

    def get_run(self, bootstrap_id: str) -> Dict[str, Any]:
        try:
            uuid.UUID(bootstrap_id)
        except (ValueError, AttributeError, TypeError):
            raise cc.ScopeError("bootstrap inexistent") from None
        c = self._c(run_id=bootstrap_id)
        try:
            row = c.execute("SELECT * FROM bootstrap_runs WHERE bootstrap_id=?", (bootstrap_id,)).fetchone()
            if not row:
                raise cc.ScopeError("bootstrap inexistent")
            return dict(row)
        finally:
            c.close()

    def mark_failed_unauthenticated(self, bootstrap_id: str, installation_uid: str, error: str) -> None:
        c = self._c(run_id=bootstrap_id, installation=installation_uid)
        try:
            run = c.execute("SELECT * FROM bootstrap_runs WHERE bootstrap_id=? AND installation_uid=? AND state IN ('planned','running','verifying')",
                            (bootstrap_id, installation_uid)).fetchone()
            if not run:
                return
            c.set_ctx(org=run["organization_uid"], cems=run["cemetery_uid"])
            if run["binding_uid"]:
                c.execute(self._REVOKE, (run["binding_uid"],))
            c.execute("UPDATE bootstrap_runs SET state='rolled_back', error=?, finished_at=? WHERE bootstrap_id=?", (error[:500], self.clock(), bootstrap_id))
            self._audit(c, installation_uid, "bootstrap_rolled_back_without_key", run["organization_uid"], run["cemetery_uid"], bootstrap_id=bootstrap_id)
        finally:
            c.close()
