"""cimitir_cloud_admin.py — autentificare administrativă Cloud (Owner/Admin) + emiterea/revocarea codurilor + dispozitive asociate, peste PostgreSQL (migrarea 023).

Garanții:
- parole: scrypt cu sare, doar hash în DB; blocare 15 min după 5 încercări greșite (persistată) + limitare per IP; mesaj uniform (fără oracol de cont);
- sesiuni: token opac (256 biți), în DB doar sha256; expirare absolută 12 h, inactivitate 8 h; revocabile (logout / schimbare parolă / recuperare);
- primul administrator: DOAR cu BOOTSTRAP_TOKEN (mediu) valid + neexpirat, o singură dată (starea „bootstrap_completed” e persistentă în PostgreSQL, nu în memorie);
- recuperare: token de unică folosință emis din consola serverului (scripts/cloud_admin_recovery.py), valabil scurt, fără resetarea bazei;
- autorizarea fiecărei operații se face pe server prin rolurile din `cp_org_roles` (RLS cu rolul cimitir_cp_app, fără BYPASSRLS); niciun endpoint nu folosește utilizatorul postgres.
Codurile și cheile apar în clar o singură dată (la emitere). Nimic din acest modul nu loghează parole, tokenuri sau coduri.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import uuid
from collections import deque
from typing import Any, Deque, Dict, List, Optional

import psycopg

import cimitir_cloud_control as cc
import cimitir_phone_pairing as pp

SESSION_TTL = 12 * 3600
MAX_FAILED = 5
LOCK_MINUTES = 15
MIN_PASSWORD = 12
BOOTSTRAP_MIN_TOKEN = 32
ENV_TOKEN, ENV_TOKEN_EXPIRES = "BOOTSTRAP_TOKEN", "BOOTSTRAP_TOKEN_EXPIRES_AT"
_SCRYPT = (2 ** 14, 8, 1)
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s.]{2,}$")
_CODE_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_DUMMY = None


class AdminError(Exception):
    status, code = 400, "REQUEST_INVALID"

    def __init__(self, message: str = ""):
        super().__init__(message or self.code)


class BadRequest(AdminError):
    status, code = 400, "REQUEST_INVALID"


class AuthRequired(AdminError):
    status, code = 401, "UNAUTHENTICATED"


class Forbidden(AdminError):
    status, code = 403, "FORBIDDEN"


class NotFound(AdminError):
    status, code = 404, "NOT_FOUND"


class Locked(AdminError):
    status, code = 429, "RATE_LIMITED"


class BootstrapClosed(AdminError):
    status, code = 409, "BOOTSTRAP_CLOSED"


class SchemaOutdated(AdminError):
    status, code = 501, "SCHEMA_OUTDATED"


# ------------------------------------------------------------------ parole
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def hash_password(password: str) -> str:
    n, r, p = _SCRYPT
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=64 * 1024 * 1024)
    return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, dk = stored.split("$")
        if scheme != "scrypt":
            return False
        got = hashlib.scrypt(password.encode("utf-8"), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=32, maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(got, _unb64(dk))
    except (ValueError, TypeError):
        return False


def validate_password(password: Any, email: str = "") -> str:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise BadRequest(f"parola trebuie să aibă cel puțin {MIN_PASSWORD} caractere")
    if len(password) > 256 or len(set(password)) < 5 or (email and password.lower() == email.lower()):
        raise BadRequest("parolă prea slabă")
    return password


def validate_email(email: Any) -> str:
    e = (email or "").strip().lower() if isinstance(email, str) else ""
    if not _EMAIL_RE.match(e):
        raise BadRequest("adresă de email invalidă")
    return e


def _h(v: str) -> str:
    return hashlib.sha256(v.encode("utf-8")).hexdigest()


def _uuid(v: Any, what: str = "identificator") -> str:
    try:
        return str(uuid.UUID(str(v)))
    except (ValueError, AttributeError, TypeError):
        raise BadRequest(f"{what} invalid") from None


def group_code(code: str) -> str:
    return pp.display_code(code) if len(code) == pp.CODE_LEN else code


class _Limiter:
    """Fereastră glisantă în memorie (apărare suplimentară peste blocarea persistată în DB)."""

    def __init__(self, limit: int, window: float, clock=time.time):
        self.limit, self.window, self.clock, self._d, self._lock = limit, window, clock, {}, threading.Lock()

    def hit(self, key: str) -> None:
        now = self.clock()
        with self._lock:
            q: Deque[float] = self._d.setdefault(key, deque())
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                raise Locked("prea multe încercări; reîncearcă mai târziu")
            q.append(now)


def bootstrap_token_state(env=None, clock=time.time) -> str:
    """'ok' | 'missing' | 'weak' | 'no_expiry' | 'expired' (fără a expune valori)."""
    env = os.environ if env is None else env
    tok = env.get(ENV_TOKEN) or ""
    if not tok:
        return "missing"
    if len(tok) < BOOTSTRAP_MIN_TOKEN:
        return "weak"
    exp = (env.get(ENV_TOKEN_EXPIRES) or "").strip()
    if not exp:
        return "no_expiry"
    try:
        ts = float(exp)
    except ValueError:
        try:
            from datetime import datetime
            ts = datetime.fromisoformat(exp.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return "no_expiry"
    return "expired" if clock() > ts else "ok"


class AdminService:
    def __init__(self, cp, clock=time.time, environ=None):
        self.cp, self.clock, self.env = cp, clock, environ
        self._login_ip = _Limiter(20, 900, clock)
        self._boot_ip = _Limiter(10, 900, clock)
        self._boot_all = _Limiter(30, 3600, clock)
        self._rec_ip = _Limiter(10, 900, clock)

    def _e(self):
        return os.environ if self.env is None else self.env

    def _conn(self, **ctx):
        self.cp.begin_request()
        return self.cp._c(**ctx)

    # ------------------------------------------------------------------ stare / bootstrap
    def status(self) -> Dict[str, Any]:
        c = self._conn()
        try:
            st = json.loads(c.execute("SELECT cp_admin_status()").fetchone()[0])
        finally:
            c.close()
        initialized = bool(st["completed"]) or int(st["admins"]) > 0
        return {"initialized": initialized, "bootstrap_open": (not initialized) and bootstrap_token_state(self._e(), self.clock) == "ok"}

    def bootstrap(self, token: Any, email: Any, password: Any, display_name: Any = "", organization_uid: Any = None, organization_name: Any = "",
                  cemetery_uid: Any = None, cemetery_name: Any = "", ip: str = "") -> Dict[str, Any]:
        self._boot_ip.hit(ip or "?")
        self._boot_all.hit("all")
        if self.status()["initialized"]:
            raise BootstrapClosed("instalarea are deja un administrator; inițializarea nu mai este permisă")
        env = self._e()
        if bootstrap_token_state(env, self.clock) != "ok":
            raise Forbidden("inițializarea nu este activă (BOOTSTRAP_TOKEN lipsă/expirat); generează un token nou pe server")
        if not isinstance(token, str) or not hmac.compare_digest(_h(token.strip()), _h(env.get(ENV_TOKEN, ""))):
            raise Forbidden("token de inițializare invalid")
        email = validate_email(email)
        validate_password(password, email)
        org = _uuid(organization_uid, "UID organizație") if organization_uid else str(uuid.uuid4())
        cem = _uuid(cemetery_uid, "UID cimitir") if cemetery_uid else None
        org_name = (organization_name or "").strip()[:200] if isinstance(organization_name, str) else ""
        cem_name = (cemetery_name or "").strip()[:200] if isinstance(cemetery_name, str) else ""
        display = (display_name or "").strip()[:120] if isinstance(display_name, str) else ""
        c = self._conn()
        try:
            try:
                row = c.execute("SELECT cp_admin_bootstrap(?, ?, ?, ?::uuid, ?, ?::uuid, ?)",
                                (email, display, hash_password(password), org, org_name, cem, cem_name)).fetchone()[0]
            except psycopg.errors.InsufficientPrivilege as exc:
                if "bootstrap_closed" in str(exc):
                    raise BootstrapClosed("inițializarea nu mai este permisă") from None
                if "cemetery_other_org" in str(exc):
                    raise BadRequest("cimitirul aparține altei organizații") from None
                raise Forbidden("operație refuzată") from None
            except psycopg.errors.InvalidParameterValue:
                raise BadRequest("denumirea organizației este obligatorie pentru o organizație nouă") from None
            res = json.loads(row) if isinstance(row, str) else row
            # fără cimitir, politica RLS a jurnalului nu permite încă o organizație nouă (contextul tenant lipsește): se jurnalizează fără organizație, cu uid în detalii
            self.cp._audit(c, res["identity_uid"], "admin_bootstrap", res["organization_uid"] if res.get("cemetery_uid") else None, res.get("cemetery_uid"),
                           organization_uid=res["organization_uid"])
        finally:
            c.close()
        return {"identity_uid": res["identity_uid"], "organization_uid": res["organization_uid"], "cemetery_uid": res.get("cemetery_uid"), "email": email}

    # ------------------------------------------------------------------ sesiuni
    def login(self, email: Any, password: Any, ip: str = "") -> Dict[str, Any]:
        global _DUMMY
        self._login_ip.hit(ip or "?")
        email = validate_email(email) if isinstance(email, str) else ""
        if not email or not isinstance(password, str) or len(password) > 256:
            raise AuthRequired("email sau parolă incorecte")
        c = self._conn(admin_email=email)
        try:
            row = c.execute("SELECT identity_uid, password_hash, disabled, (locked_until IS NOT NULL AND locked_until > now()) AS locked "
                            "FROM cp_admin_credentials WHERE lower(email)=?", (email,)).fetchone()
            if row is None:
                _DUMMY = _DUMMY or hash_password("dummy-password-for-timing")
                verify_password(password, _DUMMY)
                raise AuthRequired("email sau parolă incorecte")
            uid = row["identity_uid"]
            if row["locked"]:
                raise Locked(f"cont blocat temporar după încercări greșite; reîncearcă peste {LOCK_MINUTES} minute")
            if row["disabled"] or not verify_password(password, row["password_hash"]):
                c.execute("UPDATE cp_admin_credentials SET failed_attempts = failed_attempts + 1, locked_until = CASE WHEN failed_attempts + 1 >= ? "
                          "THEN now() + make_interval(mins => ?) ELSE locked_until END WHERE identity_uid=?", (MAX_FAILED, LOCK_MINUTES, uid))
                self.cp._audit(c, uid, "admin_login_failed", None, None)
                raise AuthRequired("email sau parolă incorecte")
            c.execute("UPDATE cp_admin_credentials SET failed_attempts=0, locked_until=NULL, last_login_at=now() WHERE identity_uid=?", (uid,))
            token = secrets.token_urlsafe(32)
            c.set_ctx(principal=uid)
            c.execute("INSERT INTO cp_admin_sessions (session_hash, identity_uid, expires_at, ip_hash) VALUES (?,?, now() + make_interval(secs => ?), ?)",
                      (_h(token), uid, SESSION_TTL, _h("ip|" + (ip or ""))[:32]))
            self.cp._audit(c, uid, "admin_login", None, None)
        finally:
            c.close()
        return {"token": token, "expires_in": SESSION_TTL, "identity_uid": uid}

    def principal(self, token: Optional[str]) -> str:
        if not token or len(token) > 200:
            raise AuthRequired("sesiune lipsă sau expirată")
        c = self._conn()
        try:
            uid = c.execute("SELECT cp_admin_session_identity(?)", (_h(token),)).fetchone()[0]
        finally:
            c.close()
        if not uid:
            raise AuthRequired("sesiune lipsă sau expirată")
        return str(uid)

    def logout(self, token: Optional[str]) -> None:
        if token:
            c = self._conn()
            try:
                c.execute("SELECT cp_admin_session_revoke(?)", (_h(token),))
            finally:
                c.close()

    def change_password(self, identity: str, current_token: str, old: Any, new: Any) -> None:
        c = self._conn(principal=identity)
        try:
            row = c.execute("SELECT password_hash, email FROM cp_admin_credentials WHERE identity_uid=?", (identity,)).fetchone()
            if not row or not isinstance(old, str) or not verify_password(old, row["password_hash"]):
                raise AuthRequired("parola curentă este incorectă")
            validate_password(new, row["email"])
            c.execute("UPDATE cp_admin_credentials SET password_hash=?, must_change=false, password_changed_at=now() WHERE identity_uid=?", (hash_password(new), identity))
            c.execute("UPDATE cp_admin_sessions SET revoked_at=COALESCE(revoked_at, now()) WHERE identity_uid=? AND session_hash<>?", (identity, _h(current_token)))
            self.cp._audit(c, identity, "admin_password_changed", None, None)
        finally:
            c.close()

    def recover(self, token: Any, email: Any, new_password: Any, ip: str = "") -> None:
        self._rec_ip.hit(ip or "?")
        email = validate_email(email)
        validate_password(new_password, email)
        if not isinstance(token, str) or not (20 <= len(token) <= 200):
            raise Forbidden("token de recuperare invalid sau expirat")
        c = self._conn()
        try:
            ok = c.execute("SELECT cp_admin_recover(?, ?, ?)", (_h(token.strip()), email, hash_password(new_password))).fetchone()[0]
            if not ok:
                raise Forbidden("token de recuperare invalid sau expirat")
            self.cp._audit(c, None, "admin_recovered", None, None)
        finally:
            c.close()

    def issue_recovery(self, email: str, ttl: int = 900) -> Optional[str]:
        """Doar din consola serverului (CLI). Returnează tokenul în clar (o singură dată) sau None dacă emailul nu există."""
        token = "rec-" + secrets.token_urlsafe(32)
        c = self._conn()
        try:
            ok = c.execute("SELECT cp_admin_recovery_issue(?, ?, ?)", (_h(token), validate_email(email), int(ttl))).fetchone()[0]
        finally:
            c.close()
        return token if ok else None

    # ------------------------------------------------------------------ profil
    def me(self, identity: str) -> Dict[str, Any]:
        c = self._conn(principal=identity)
        try:
            cred = c.execute("SELECT email, display_name, must_change FROM cp_admin_credentials WHERE identity_uid=?", (identity,)).fetchone()
            roles = c.execute("SELECT organization_uid, role FROM cp_org_roles WHERE identity_uid=? ORDER BY organization_uid, role", (identity,)).fetchall()
        finally:
            c.close()
        orgs: Dict[str, Dict[str, Any]] = {}
        for r in roles:
            o = orgs.setdefault(r["organization_uid"], {"organization_uid": r["organization_uid"], "roles": []})
            o["roles"].append(r["role"])
        for org, o in orgs.items():
            c = self._conn()
            try:
                self.cp._require(c, identity, "read", org)
                row = c.execute("SELECT display_name FROM organizations WHERE uid=?", (org,)).fetchone()
                o["name"] = row["display_name"] if row else ""
                o["is_owner"] = cc.OWNER in o["roles"]
                o["cemeteries"] = [{"uid": r["uid"], "name": r["name"], "status": r["status"]} for r in
                                   c.execute("SELECT uid, name, status FROM cemeteries WHERE organization_uid=? ORDER BY name", (org,)).fetchall()]
            except (cc.PermissionDenied, cc.ScopeError):
                o["name"], o["cemeteries"], o["is_owner"] = "", [], False
            finally:
                c.close()
        return {"identity_uid": identity, "email": cred["email"] if cred else "", "display_name": cred["display_name"] if cred else "",
                "must_change_password": bool(cred["must_change"]) if cred else False, "organizations": list(orgs.values())}

    # ------------------------------------------------------------------ autorizare pe organizație
    def _authz(self, identity: str, action: str, org: str):
        org = _uuid(org, "UID organizație")
        c = self._conn()
        try:
            self.cp._require(c, identity, action, org)
        except cc.PermissionDenied as exc:
            c.close()
            raise Forbidden(str(exc)) from None
        except cc.ScopeError:
            c.close()
            raise NotFound("organizație inexistentă") from None
        return c, org

    # ------------------------------------------------------------------ coduri
    def issue_code(self, identity: str, org: str, cemetery_uid: str, kind: str, ttl: Optional[int] = None) -> Dict[str, Any]:
        if kind not in ("main", "phone"):
            raise BadRequest("tip de cod necunoscut (main|phone)")
        cem = _uuid(cemetery_uid, "UID cimitir")
        c, org = self._authz(identity, "issue_code", org)
        try:
            owner = c.execute("SELECT cp_cemetery_org(?::uuid)", (cem,)).fetchone()[0]
            if str(owner or "") != org:
                raise NotFound("cimitir inexistent în organizație")
            n = c.execute("SELECT count(*) AS n FROM device_pairing_codes WHERE cemetery_uid=? AND used_at IS NULL AND revoked_at IS NULL AND expires_at > now()", (cem,)).fetchone()["n"]
            if n >= pp.MAX_OPEN_CODES:
                raise BadRequest(f"există deja {pp.MAX_OPEN_CODES} coduri active pentru acest cimitir; revocă unul")
        finally:
            c.close()
        if kind == "main":
            code = self.cp.issue_code(identity, org, cem)
            life = cc.CODE_TTL
        else:
            life = min(max(pp.MIN_TTL, int(ttl or pp.DEFAULT_TTL)), pp.MAX_TTL)
            code = pp.new_code()
            c, org = self._authz(identity, "issue_code", org)
            try:
                c.execute("INSERT INTO device_pairing_codes (code_hash, organization_uid, cemetery_uid, device_type, created_by, expires_at) "
                          "VALUES (?,?,?,'MOBILE',?, now() + make_interval(secs => ?))", (pp._h(code), org, cem, identity, life))
                self.cp._audit(c, identity, "admin_phone_code_issued", org, cem, ttl=life)
            finally:
                c.close()
        return {"kind": kind, "code": code, "display": group_code(code), "ttl": life, "expires_at": self.clock() + life, "organization_uid": org, "cemetery_uid": cem,
                "code_id": _h(code)[:16] if kind == "phone" else cc._h(code)[:16]}

    def list_codes(self, identity: str, org: str, cemetery_uid: Optional[str] = None) -> List[Dict[str, Any]]:
        c, org = self._authz(identity, "read", org)
        try:
            args: List[Any] = [org]
            q = ("SELECT left(code_hash, 16) AS code_id, device_type, cemetery_uid, EXTRACT(EPOCH FROM created_at)::float8 AS created_at, "
                 "EXTRACT(EPOCH FROM expires_at)::float8 AS expires_at, "
                 "CASE WHEN revoked_at IS NOT NULL THEN 'revoked' WHEN used_at IS NOT NULL THEN 'used' WHEN expires_at <= now() THEN 'expired' ELSE 'active' END AS state "
                 "FROM device_pairing_codes WHERE organization_uid=? AND created_at > now() - interval '7 days' ")
            if cemetery_uid:
                q += "AND cemetery_uid=? "
                args.append(_uuid(cemetery_uid, "UID cimitir"))
            rows = c.execute(q + "ORDER BY created_at DESC LIMIT 100", tuple(args)).fetchall()
            return [{"code_id": r["code_id"], "kind": "phone" if r["device_type"] == "MOBILE" else "main", "cemetery_uid": r["cemetery_uid"],
                     "created_at": r["created_at"], "expires_at": r["expires_at"], "state": r["state"]} for r in rows]
        finally:
            c.close()

    def revoke_code(self, identity: str, org: str, code_id: Any) -> Dict[str, Any]:
        if not isinstance(code_id, str) or not _CODE_ID_RE.match(code_id):
            raise BadRequest("identificator de cod invalid")
        c, org = self._authz(identity, "issue_code", org)
        try:
            n = c.execute("UPDATE device_pairing_codes SET revoked_at=now(), revoked_by=? WHERE organization_uid=? AND left(code_hash, 16)=? AND used_at IS NULL "
                          "AND revoked_at IS NULL AND expires_at > now()", (identity, org, code_id)).rowcount
            if n < 1:
                raise NotFound("cod inexistent, deja folosit, expirat sau revocat")
            self.cp._audit(c, identity, "admin_code_revoked", org, None, code_id=code_id)
            return {"revoked": n}
        finally:
            c.close()

    # ------------------------------------------------------------------ dispozitive
    def list_devices(self, identity: str, org: str) -> List[Dict[str, Any]]:
        c, org = self._authz(identity, "read", org)
        try:
            names = {r["uid"]: r["name"] for r in c.execute("SELECT uid, name FROM cemeteries WHERE organization_uid=?", (org,)).fetchall()}
            rows = c.execute("SELECT b.binding_uid, b.device_uid, d.device_name, d.device_type, b.cemetery_uid, b.active, b.created_at, b.last_seen_at, b.revoked_at "
                             "FROM device_cemetery_bindings b JOIN devices d ON d.device_uid=b.device_uid AND d.organization_uid=b.organization_uid "
                             "WHERE b.organization_uid=? ORDER BY b.created_at DESC LIMIT 200", (org,)).fetchall()
            iso = lambda v: v.isoformat() if v is not None else None            # noqa: E731
            return [{"binding_uid": r["binding_uid"], "device_uid": r["device_uid"], "device_name": r["device_name"], "kind": "phone" if r["device_type"] == "MOBILE" else "main",
                     "cemetery_uid": r["cemetery_uid"], "cemetery_name": names.get(r["cemetery_uid"], ""), "created_at": iso(r["created_at"]),
                     "last_seen_at": iso(r["last_seen_at"]), "revoked_at": iso(r["revoked_at"]),
                     "state": "revoked" if r["revoked_at"] else "active" if r["active"] else "pending"} for r in rows]
        finally:
            c.close()

    def revoke_device(self, identity: str, org: str, binding_uid: Any) -> Dict[str, Any]:
        org = _uuid(org, "UID organizație")
        bu = _uuid(binding_uid, "UID legătură")
        try:
            self.cp.revoke_binding(identity, org, bu)
        except cc.PermissionDenied as exc:
            raise Forbidden(str(exc)) from None
        except cc.ScopeError:
            raise NotFound("legătură inexistentă") from None
        return {"binding_uid": bu, "revoked": True}

    # ------------------------------------------------------------------ provizionarea registrului MAIN (UID-uri originale, migrarea 025)
    def registry_status(self, identity: str, org: Any, cemetery_uid: Any) -> Dict[str, Any]:
        """Starea registrului local în Cloud, fără a scrie nimic: există organizația/cimitirul? are administratorul drepturi? poate provizionă?"""
        org, cem = _uuid(org, "UID organizație"), _uuid(cemetery_uid, "UID cimitir")
        c = self._conn()
        try:
            try:
                st = c.execute("SELECT cp_admin_registry_lookup(?::uuid, ?::uuid, ?::uuid)", (identity, org, cem)).fetchone()[0]
            except psycopg.errors.InsufficientPrivilege:
                raise Forbidden("contul nu este administrator activ") from None
            except psycopg.errors.UndefinedFunction:
                raise SchemaOutdated("Cloud-ul nu are migrarea 025 (provizionare registru); administratorul serverului rulează scripts/apply_cloud_provision_migration.py") from None
        finally:
            c.close()
        st = json.loads(st) if isinstance(st, str) else st
        if st["cemetery_in_other_organization"]:
            action, msg = "conflict", "Cimitirul cu acest UID există deja în Cloud, dar în altă organizație. Nu se poate asocia (UID-urile nu se modifică)."
        elif st["organization_exists"] and not st["is_owner"]:
            action, msg = "forbidden", "Organizația există în Cloud, dar contul dumneavoastră nu este Owner al ei."
        elif st["organization_exists"] and st["cemetery_in_organization"]:
            action, msg = "ready", "Registrul există în Cloud (același UID de organizație și cimitir). Se poate genera cod de asociere."
        elif st["organization_exists"]:
            action, msg = "provision_cemetery", "Organizația există în Cloud; cimitirul lipsește și va fi creat cu UID-ul original."
        elif st["can_provision_organization"]:
            action, msg = "provision_all", "Registrul nu există în Cloud. Organizația și cimitirul pot fi create automat, cu UID-urile originale."
        else:
            action, msg = "forbidden", "Registrul nu există în Cloud, iar contul dumneavoastră nu are dreptul de a crea organizații noi (cere administratorului instalării Cloud)."
        return {**st, "action": action, "message": msg, "organization_uid": org, "cemetery_uid": cem}

    def provision_registry(self, identity: str, org: Any, org_name: Any, cemetery_uid: Any, cemetery_name: Any) -> Dict[str, Any]:
        org, cem = _uuid(org, "UID organizație"), _uuid(cemetery_uid, "UID cimitir")
        on = (org_name or "").strip()[:200] if isinstance(org_name, str) else ""
        cn = (cemetery_name or "").strip()[:200] if isinstance(cemetery_name, str) else ""
        c = self._conn()
        try:
            try:
                row = c.execute("SELECT cp_admin_provision(?::uuid, ?::uuid, ?, ?::uuid, ?)", (identity, org, on, cem, cn)).fetchone()[0]
            except psycopg.errors.InsufficientPrivilege as exc:
                m = str(exc)
                if "cemetery_other_org" in m:
                    raise BadRequest("cimitirul aparține deja altei organizații (UID-urile nu se modifică)") from None
                if "not_owner" in m:
                    raise Forbidden("nu ești Owner al organizației existente în Cloud") from None
                if "not_provisioner" in m:
                    raise Forbidden("contul nu are dreptul de a crea organizații noi în Cloud") from None
                raise Forbidden("operație refuzată") from None
            except psycopg.errors.InvalidParameterValue:
                raise BadRequest("denumirea organizației este obligatorie pentru o organizație nouă") from None
            except psycopg.errors.UndefinedFunction:
                raise SchemaOutdated("Cloud-ul nu are migrarea 025 (provizionare registru); administratorul serverului rulează scripts/apply_cloud_provision_migration.py") from None
            res = json.loads(row) if isinstance(row, str) else row
            c.set_ctx(org=org, cems="*", principal=identity)
            self.cp._audit(c, identity, "admin_provision_registry", org, cem, organization_created=bool(res["organization_created"]),
                           cemetery_created=bool(res["cemetery_created"]))
        finally:
            c.close()
        return {"organization_uid": org, "cemetery_uid": cem, "organization_created": res["organization_created"],
                "cemetery_created": res["cemetery_created"], "cemetery_status": res["cemetery_status"]}

    def create_cemetery(self, identity: str, org: str, name: Any, cemetery_uid: Any = None) -> Dict[str, Any]:
        org = _uuid(org, "UID organizație")
        nm = (name or "").strip()[:200] if isinstance(name, str) else ""
        if not nm:
            raise BadRequest("denumirea cimitirului este obligatorie")
        uid = _uuid(cemetery_uid, "UID cimitir") if cemetery_uid else str(uuid.uuid4())
        try:
            self.cp.create_cemetery(identity, org, uid, nm)
        except cc.PermissionDenied as exc:
            raise Forbidden(str(exc)) from None
        except cc.ConflictError as exc:
            raise BadRequest(str(exc)) from None
        except cc.ScopeError:
            raise NotFound("organizație inexistentă") from None
        return {"cemetery_uid": uid, "organization_uid": org, "name": nm, "status": "DRAFT"}
