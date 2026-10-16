"""cloud_profiles.py — profile de conexiune Cloud/Server pentru MAIN (TEST / PRODUCTION / CUSTOM), un singur profil activ.

- Secretele (parola bazei de date, Supabase Secret Key, cheia de sincronizare) trăiesc EXCLUSIV în `shared/cimitir_secretstore.py`
  (DPAPI pe Windows). SQLite păstrează doar `*_ref` (numele secretului); valorile nu apar în SQLite, JSON, loguri, audit sau excepții.
- Profilul normal vorbește doar HTTPS cu API-ul (MAIN -> API -> PostgreSQL/Storage). `db_password`/`supabase_secret_key` sunt
  opționale (Advanced / Server mode) și nu pleacă niciodată spre API sau telefon.
- `test_connection` este READ-ONLY (GET-uri, tranzacție PostgreSQL read-only, GET bucket).
- Backend-ul Linux rămâne pe variabile de mediu; acest modul nu îl atinge.
"""
from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

import cimitir_secretstore as ss
import database as db

ENVIRONMENTS = ("TEST", "PRODUCTION", "CUSTOM")
SSL_MODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")
SECURE_SSL = ("require", "verify-ca", "verify-full")
MASK = "********"
EXPECTED_MIN_SCHEMA = 19  # ultima migrare cunoscută de MAIN (019 Phone Pairing)

SECRET_KINDS = {"db_password": "db_password_ref", "supabase_secret_key": "supabase_secret_ref", "sync_key": "sync_key_ref"}
PLAIN_FIELDS = ("name", "environment", "project_ref", "api_base_url", "supabase_url", "db_host", "db_port", "db_name",
                "db_user", "sslmode", "storage_bucket")
LEGACY_REF = "cimitir/cloud/legacy_sync_key"
SET_LEGACY_URL = "cloud_api_url"
SET_LEGACY_KEY = "cloud_sync_key"
SET_LEGACY_KEY_REF = "cloud_sync_key_ref"
SET_PROFILES_MODE = "cloud_profiles_mode"
TIMEOUT = 20
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
_REF_RE = re.compile(r"^[a-z0-9]{8,32}$")
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
_IDENT_RE = re.compile(r"^[A-Za-z0-9_.$-]{1,100}$")


class ProfileError(Exception):
    pass


class ValidationFailed(ProfileError):
    def __init__(self, errors: List[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class ConfirmationRequired(ProfileError):
    def __init__(self, kind: str):
        super().__init__(f"confirmare necesară: {kind}")
        self.kind = kind


class SecretDeleteError(ProfileError):
    """Ștergerea unui secret din SecretStore a eșuat: profilul NU este șters."""


@dataclass
class Profile:
    id: str
    name: str
    environment: str
    project_ref: str = ""
    api_base_url: str = ""
    supabase_url: str = ""
    db_host: str = ""
    db_port: int = 5432
    db_name: str = ""
    db_user: str = ""
    sslmode: str = "require"
    storage_bucket: str = ""
    db_password_ref: str = ""
    supabase_secret_ref: str = ""
    sync_key_ref: str = ""
    is_active: bool = False

    def has_secret(self, kind: str) -> bool:
        return bool(getattr(self, SECRET_KINDS[kind]))

    def masked(self, kind: str) -> str:
        return MASK if self.has_secret(kind) else ""

    @property
    def advanced(self) -> bool:
        return bool(self.db_password_ref or self.supabase_secret_ref)


@dataclass
class Check:
    name: str
    status: str  # OK | FAIL | WARN | SKIP
    detail: str = ""


@dataclass
class TestReport:
    __test__ = False
    mode: str
    checks: List[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(c.status in ("OK", "WARN", "SKIP") for c in self.checks) and any(c.status == "OK" for c in self.checks)

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.checks.append(Check(name, status, detail))

    def text(self) -> str:
        return "\n".join(f"[{c.status}] {c.name}" + (f" — {c.detail}" if c.detail else "") for c in self.checks)


# ------------------------------------------------------------------ SecretStore
def get_store() -> ss.SecretStore:
    return ss.default_store()


def _store(store: Optional[ss.SecretStore]) -> ss.SecretStore:
    return store if store is not None else get_store()


def _secret_name(profile_id: str, kind: str) -> str:
    return f"cimitir/cloud/profiles/{profile_id}/{kind}"


def _write_verified(store: ss.SecretStore, name: str, value: str) -> None:
    """Scrie secretul și verifică read-back; la nepotrivire restaurează valoarea veche (sau o șterge)."""
    previous = None
    try:
        previous = store.get(name)
    except ss.SecretStoreError:
        previous = None
    try:
        store.set(name, value)
        if store.get(name) != value:
            raise ss.SecretStoreError("verificarea read-back a eșuat")
    except Exception as exc:
        try:
            if previous is not None:
                store.set(name, previous)
            else:
                store.delete(name)
        except Exception:
            pass
        raise ProfileError(f"Secretul nu a putut fi salvat în SecretStore ({type(exc).__name__}).") from None


def _delete_verified(store: ss.SecretStore, name: str) -> None:
    try:
        store.delete(name)
        gone = not store.exists(name)
    except Exception as exc:
        raise SecretDeleteError(f"Ștergerea secretului din SecretStore a eșuat ({type(exc).__name__}).") from None
    if not gone:
        raise SecretDeleteError("Secretul încă există în SecretStore după ștergere.")


# ------------------------------------------------------------------ tabel local
_COLS = ("id", "name", "environment", "project_ref", "api_base_url", "supabase_url", "db_host", "db_port", "db_name", "db_user",
         "sslmode", "storage_bucket", "db_password_ref", "supabase_secret_ref", "sync_key_ref", "is_active")


def ensure_table() -> None:
    conn = db.get_connection()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS cloud_connection_profiles ("
                     "id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, "
                     "environment TEXT NOT NULL CHECK (environment IN ('TEST','PRODUCTION','CUSTOM')), "
                     "project_ref TEXT NOT NULL DEFAULT '', api_base_url TEXT NOT NULL DEFAULT '', supabase_url TEXT NOT NULL DEFAULT '', "
                     "db_host TEXT NOT NULL DEFAULT '', db_port INTEGER NOT NULL DEFAULT 5432, db_name TEXT NOT NULL DEFAULT '', "
                     "db_user TEXT NOT NULL DEFAULT '', sslmode TEXT NOT NULL DEFAULT 'require', storage_bucket TEXT NOT NULL DEFAULT '', "
                     "db_password_ref TEXT NOT NULL DEFAULT '', supabase_secret_ref TEXT NOT NULL DEFAULT '', "
                     "sync_key_ref TEXT NOT NULL DEFAULT '', is_active INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0,1)), "
                     "created_at TEXT, updated_at TEXT)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_cloud_profiles_one_active ON cloud_connection_profiles(is_active) WHERE is_active=1")
    finally:
        conn.close()


def _row(r: sqlite3.Row) -> Profile:
    d = {c: r[c] for c in _COLS}
    d["is_active"] = bool(d["is_active"])
    return Profile(**d)


def list_profiles() -> List[Profile]:
    ensure_table()
    conn = db.get_connection()
    try:
        return [_row(r) for r in conn.execute(f"SELECT {','.join(_COLS)} FROM cloud_connection_profiles ORDER BY name COLLATE NOCASE")]
    finally:
        conn.close()


def get_profile(profile_id: str) -> Optional[Profile]:
    ensure_table()
    conn = db.get_connection()
    try:
        r = conn.execute(f"SELECT {','.join(_COLS)} FROM cloud_connection_profiles WHERE id=?", (profile_id,)).fetchone()
        return _row(r) if r else None
    finally:
        conn.close()


def active_profile() -> Optional[Profile]:
    ensure_table()
    conn = db.get_connection()
    try:
        r = conn.execute(f"SELECT {','.join(_COLS)} FROM cloud_connection_profiles WHERE is_active=1").fetchone()
        return _row(r) if r else None
    finally:
        conn.close()


def _audit(action: str, profile: Optional[Profile], detail: str = "") -> None:
    try:
        import cimitir_requests
        cimitir_requests.audit("system", "cloud_profiles", action, profile.id if profile else "", f"{profile.name if profile else ''} {detail}".strip())
    except Exception:
        pass


# ------------------------------------------------------------------ validare
def derive_project_refs(f: Dict[str, Any]) -> Dict[str, str]:
    """Referința proiectului Supabase dedusă din supabase_url / db_host / db_user (doar ce se poate deduce)."""
    out: Dict[str, str] = {}
    sh = (urlparse(f.get("supabase_url") or "").hostname or "")
    m = re.match(r"^([a-z0-9]+)\.supabase\.(co|in|net)$", sh)
    if m:
        out["supabase_url"] = m.group(1)
    m = re.match(r"^db\.([a-z0-9]+)\.supabase\.(co|in|net)$", f.get("db_host") or "")
    if m:
        out["db_host"] = m.group(1)
    m = re.match(r"^[A-Za-z0-9_]+\.([a-z0-9]+)$", f.get("db_user") or "")
    if m:
        out["db_user"] = m.group(1)
    return out


def validate_fields(fields: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    errors: List[str] = []
    c: Dict[str, Any] = {}
    for k in PLAIN_FIELDS:
        v = fields.get(k)
        c[k] = (str(v).strip() if v is not None else "")
    c["environment"] = c["environment"].upper()
    if not c["name"] or len(c["name"]) > 80 or re.search(r"[\r\n\x00]", c["name"]):
        errors.append("Numele profilului este obligatoriu (max 80 caractere).")
    if c["environment"] not in ENVIRONMENTS:
        errors.append("Mediul trebuie să fie TEST, PRODUCTION sau CUSTOM.")
    for k in ("project_ref", "api_base_url", "supabase_url", "db_host", "db_name", "db_user", "storage_bucket"):
        if re.search(r"[\s\x00]", c[k]):
            errors.append(f"{k}: valoarea nu poate conține spații sau linii noi.")
    if c["project_ref"] and not _REF_RE.match(c["project_ref"]):
        errors.append("project_ref: doar litere mici și cifre (8-32 caractere).")
    if c["api_base_url"]:
        import cloud_sync
        msg = cloud_sync.validate_url(c["api_base_url"])
        if msg:
            errors.append("api_base_url: " + msg)
        c["api_base_url"] = c["api_base_url"].rstrip("/")
    if c["supabase_url"]:
        u = urlparse(c["supabase_url"])
        if u.scheme != "https" or not u.hostname:
            errors.append("supabase_url: trebuie să fie https://<proiect>.supabase.co")
        c["supabase_url"] = c["supabase_url"].rstrip("/")
    if c["db_host"] and not _HOST_RE.match(c["db_host"]):
        errors.append("db_host: gazdă invalidă.")
    port_raw = fields.get("db_port")
    if port_raw in (None, ""):
        c["db_port"] = 5432
    else:
        try:
            c["db_port"] = int(str(port_raw).strip())
            if not 1 <= c["db_port"] <= 65535:
                raise ValueError
        except ValueError:
            errors.append("db_port: număr între 1 și 65535.")
            c["db_port"] = 5432
    for k in ("db_name", "db_user"):
        if c[k] and not _IDENT_RE.match(c[k]):
            errors.append(f"{k}: caractere nepermise.")
    c["sslmode"] = c["sslmode"].lower() or "require"
    if c["sslmode"] not in SSL_MODES:
        errors.append("sslmode: " + ", ".join(SSL_MODES) + ".")
    if c["storage_bucket"] and not _BUCKET_RE.match(c["storage_bucket"]):
        errors.append("storage_bucket: doar litere mici, cifre, - și _ (2-63 caractere).")
    if c["project_ref"]:
        for src, ref in derive_project_refs(c).items():
            if ref != c["project_ref"]:
                errors.append(f"project_ref nu corespunde cu {src}.")
    return c, errors


def _validate_secret(kind: str, value: str) -> None:
    if not isinstance(value, str) or not value or re.search(r"[\r\n\x00]", value):
        raise ValidationFailed([f"{kind}: valoare goală sau cu linii noi."])
    if kind == "sync_key":
        import cloud_sync
        msg = cloud_sync.validate_key(value)
        if msg:
            raise ValidationFailed([msg])
    elif kind == "supabase_secret_key" and (len(value) < 20 or '"' in value or "'" in value or re.search(r"\s", value)):
        raise ValidationFailed(["supabase_secret_key: valoare invalidă."])


# ------------------------------------------------------------------ CRUD
def create_profile(fields: Dict[str, Any], secrets: Optional[Dict[str, str]] = None,
                   store: Optional[ss.SecretStore] = None) -> Profile:
    secrets = {k: v for k, v in (secrets or {}).items() if v}
    for k, v in secrets.items():
        if k not in SECRET_KINDS:
            raise ValidationFailed([f"secret necunoscut: {k}"])
        _validate_secret(k, v)
    clean, errors = validate_fields(fields)
    if errors:
        raise ValidationFailed(errors)
    ensure_table()
    pid = uuid.uuid4().hex[:12]
    st = _store(store) if secrets else None
    written: List[str] = []
    refs = {SECRET_KINDS[k]: "" for k in SECRET_KINDS}
    try:
        for kind, value in secrets.items():
            name = _secret_name(pid, kind)
            _write_verified(st, name, value)
            written.append(name)
            refs[SECRET_KINDS[kind]] = name
        now = datetime.now().isoformat(timespec="seconds")
        conn = db.get_connection()
        try:
            conn.execute("INSERT INTO cloud_connection_profiles (id,name,environment,project_ref,api_base_url,supabase_url,db_host,db_port,"
                         "db_name,db_user,sslmode,storage_bucket,db_password_ref,supabase_secret_ref,sync_key_ref,is_active,created_at,updated_at) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)",
                         (pid, clean["name"], clean["environment"], clean["project_ref"], clean["api_base_url"], clean["supabase_url"],
                          clean["db_host"], clean["db_port"], clean["db_name"], clean["db_user"], clean["sslmode"], clean["storage_bucket"],
                          refs["db_password_ref"], refs["supabase_secret_ref"], refs["sync_key_ref"], now, now))
        except sqlite3.IntegrityError:
            raise ValidationFailed(["Există deja un profil cu acest nume."]) from None
        finally:
            conn.close()
    except Exception:
        for name in written:
            try:
                st.delete(name)
            except Exception:
                pass
        raise
    prof = get_profile(pid)
    _audit("profile_created", prof, prof.environment)
    return prof


def update_profile(profile_id: str, fields: Dict[str, Any], secrets: Optional[Dict[str, str]] = None,
                   store: Optional[ss.SecretStore] = None) -> Profile:
    """Câmpurile se înlocuiesc integral; un secret apare în `secrets` doar dacă e înlocuit (absent = neschimbat)."""
    old = get_profile(profile_id)
    if old is None:
        raise ProfileError("Profilul nu există.")
    secrets = {k: v for k, v in (secrets or {}).items() if v}
    for k, v in secrets.items():
        if k not in SECRET_KINDS:
            raise ValidationFailed([f"secret necunoscut: {k}"])
        _validate_secret(k, v)
    clean, errors = validate_fields(fields)
    if errors:
        raise ValidationFailed(errors)
    st = _store(store) if secrets else None
    undo: List[Tuple[str, Optional[str]]] = []
    refs = {SECRET_KINDS[k]: getattr(old, SECRET_KINDS[k]) for k in SECRET_KINDS}
    try:
        for kind, value in secrets.items():
            name = refs[SECRET_KINDS[kind]] or _secret_name(profile_id, kind)
            try:
                prev = st.get(name)
            except ss.SecretStoreError:
                prev = None
            _write_verified(st, name, value)
            undo.append((name, prev))
            refs[SECRET_KINDS[kind]] = name
        conn = db.get_connection()
        try:
            conn.execute("UPDATE cloud_connection_profiles SET name=?,environment=?,project_ref=?,api_base_url=?,supabase_url=?,db_host=?,"
                         "db_port=?,db_name=?,db_user=?,sslmode=?,storage_bucket=?,db_password_ref=?,supabase_secret_ref=?,sync_key_ref=?,"
                         "updated_at=? WHERE id=?",
                         (clean["name"], clean["environment"], clean["project_ref"], clean["api_base_url"], clean["supabase_url"],
                          clean["db_host"], clean["db_port"], clean["db_name"], clean["db_user"], clean["sslmode"], clean["storage_bucket"],
                          refs["db_password_ref"], refs["supabase_secret_ref"], refs["sync_key_ref"],
                          datetime.now().isoformat(timespec="seconds"), profile_id))
        except sqlite3.IntegrityError:
            raise ValidationFailed(["Există deja un profil cu acest nume."]) from None
        finally:
            conn.close()
    except Exception:
        for name, prev in undo:
            try:
                st.set(name, prev) if prev is not None else st.delete(name)
            except Exception:
                pass
        raise
    prof = get_profile(profile_id)
    _audit("profile_updated", prof, ("secrets_replaced=" + ",".join(sorted(secrets))) if secrets else "")
    return prof


def activate_profile(profile_id: str, confirm_production: bool = False) -> Profile:
    prof = get_profile(profile_id)
    if prof is None:
        raise ProfileError("Profilul nu există.")
    if prof.environment == "PRODUCTION" and not confirm_production:
        raise ConfirmationRequired("production_activation")
    conn = db.get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("UPDATE cloud_connection_profiles SET is_active=0 WHERE is_active=1")
            conn.execute("UPDATE cloud_connection_profiles SET is_active=1, updated_at=? WHERE id=?",
                         (datetime.now().isoformat(timespec="seconds"), profile_id))
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, '1')", (SET_PROFILES_MODE,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    prof = get_profile(profile_id)
    _audit("profile_activated", prof, prof.environment)
    return prof


def deactivate_profile(profile_id: str) -> Profile:
    prof = get_profile(profile_id)
    if prof is None:
        raise ProfileError("Profilul nu există.")
    conn = db.get_connection()
    try:
        conn.execute("UPDATE cloud_connection_profiles SET is_active=0, updated_at=? WHERE id=?",
                     (datetime.now().isoformat(timespec="seconds"), profile_id))
    finally:
        conn.close()
    prof = get_profile(profile_id)
    _audit("profile_deactivated", prof)
    return prof


def delete_profile(profile_id: str, confirm_active: bool = False, store: Optional[ss.SecretStore] = None) -> None:
    """Șterge secretele din SecretStore și abia apoi rândul. Dacă un secret nu poate fi șters, profilul rămâne (cu refurile rămase)."""
    prof = get_profile(profile_id)
    if prof is None:
        raise ProfileError("Profilul nu există.")
    if prof.is_active and not confirm_active:
        raise ConfirmationRequired("active_profile_deletion")
    try:
        st = _store(store) if any(getattr(prof, c) for c in SECRET_KINDS.values()) else None
    except Exception as exc:
        raise SecretDeleteError(f"SecretStore indisponibil ({type(exc).__name__}); profilul nu a fost șters.") from None
    for kind, col in SECRET_KINDS.items():
        ref = getattr(prof, col)
        if not ref:
            continue
        _delete_verified(st, ref)
        conn = db.get_connection()
        try:
            conn.execute(f"UPDATE cloud_connection_profiles SET {col}='' WHERE id=?", (profile_id,))
        finally:
            conn.close()
    conn = db.get_connection()
    try:
        conn.execute("DELETE FROM cloud_connection_profiles WHERE id=?", (profile_id,))
    finally:
        conn.close()
    _audit("profile_deleted", prof, "active" if prof.is_active else "")


# ------------------------------------------------------------------ conexiunea efectivă (folosită de cloud_sync)
SET_LAST_ERROR = "cloud_sync_last_error"
UNREADABLE_MSG = "Cheia de sincronizare nu poate fi citită din SecretStore (DPAPI). Înlocuiește cheia în profil / reintrodu-o."


def _read_ref(ref: str, store: Optional[ss.SecretStore], report: bool = False) -> str:
    """Valoarea secretului sau ''. Cu `report=True`, un secret existent dar ilizibil lasă o eroare vizibilă (nu o oprire tăcută)."""
    if not ref:
        return ""
    try:
        return (_store(store).get(ref) or "").strip()
    except Exception as exc:
        if report:
            try:
                db.set_setting(SET_LAST_ERROR, f"{UNREADABLE_MSG} ({type(exc).__name__})")
            except Exception:
                pass
        return ""


def effective_connection(store: Optional[ss.SecretStore] = None) -> Tuple[str, str]:
    """(url API, cheie sync): profilul activ; fără profil activ după ce s-au folosit profile -> neconfigurat; altfel setările vechi."""
    prof = active_profile()
    if prof is not None:
        return prof.api_base_url.rstrip("/"), _read_ref(prof.sync_key_ref, store, report=True)
    if (db.get_setting(SET_PROFILES_MODE) or "") == "1":
        return "", ""
    return legacy_connection(store)


def legacy_connection(store: Optional[ss.SecretStore] = None) -> Tuple[str, str]:
    url = (db.get_setting(SET_LEGACY_URL) or "").strip().rstrip("/")
    ref = (db.get_setting(SET_LEGACY_KEY_REF) or "").strip()
    key = _read_ref(ref, store, report=True)
    if not key:  # migrare neterminată: valoarea veche rămâne utilizabilă
        key = (db.get_setting(SET_LEGACY_KEY) or "").strip()
    return url, key


def save_legacy_key(key: str, store: Optional[ss.SecretStore] = None) -> None:
    """Cheia din fereastra clasică de sincronizare: direct în SecretStore (niciodată în SQLite)."""
    key = (key or "").strip()
    if not key:
        return
    _write_verified(_store(store), LEGACY_REF, key)
    db.set_setting(SET_LEGACY_KEY_REF, LEGACY_REF)
    _purge_setting(SET_LEGACY_KEY)


def _purge_setting(key: str) -> None:
    conn = db.get_connection()
    try:
        conn.execute("PRAGMA secure_delete=ON")
        conn.execute("DELETE FROM settings WHERE key=?", (key,))
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def migrate_legacy_sync_key(store: Optional[ss.SecretStore] = None) -> str:
    """plaintext -> SecretStore -> read-back -> secret_ref -> abia apoi ștergerea plaintextului.
    Întoarce 'nothing' | 'migrated'; la eșec ridică ProfileError, iar valoarea veche rămâne neatinsă (operația se poate relua)."""
    plain = (db.get_setting(SET_LEGACY_KEY) or "").strip()
    if not plain:
        return "nothing"
    st = _store(store)
    _write_verified(st, LEGACY_REF, plain)  # include read-back; la eșec restaurează
    try:
        db.set_setting(SET_LEGACY_KEY_REF, LEGACY_REF)
        if (db.get_setting(SET_LEGACY_KEY_REF) or "") != LEGACY_REF:
            raise ProfileError("referința nu a putut fi salvată")
        if st.get(LEGACY_REF) != plain:
            raise ProfileError("verificare finală eșuată")
        _purge_setting(SET_LEGACY_KEY)
    except Exception as exc:
        raise ProfileError(f"Migrarea cheii de sincronizare a eșuat ({type(exc).__name__}); valoarea veche a fost păstrată.") from None
    _audit("legacy_sync_key_migrated", None)
    return "migrated"


# ------------------------------------------------------------------ Test Connection (READ-ONLY)
def _scrub(text: str, secrets: List[str]) -> str:
    for s in secrets:
        if s and len(s) >= 4:
            text = text.replace(s, MASK)
    return text


def _pg_hint(exc: BaseException) -> str:
    low = f"{type(exc).__name__} {exc}".lower()
    for needle, hint in (("password authentication", "autentificare eșuată"), ("authentication failed", "autentificare eșuată"),
                         ("translate host", "gazdă necunoscută (DNS)"), ("name or service", "gazdă necunoscută (DNS)"),
                         ("getaddrinfo", "gazdă necunoscută (DNS)"), ("timeout", "timeout"), ("timed out", "timeout"),
                         ("ssl", "problemă SSL"), ("refused", "conexiune refuzată"), ("does not exist", "bază/utilizator inexistent")):
        if needle in low:
            return hint
    return "conexiune imposibilă"


def _default_pg_connect(**kw):
    import psycopg
    return psycopg.connect(**kw)


def _default_storage(url: str, key: str, bucket: str):
    import cimitir_storage
    return cimitir_storage.SupabaseStorage(url, key, bucket)


def test_connection(profile: Profile, mode: str = "api", store: Optional[ss.SecretStore] = None, http: Any = None,
                    pg_connect: Optional[Callable[..., Any]] = None,
                    storage_factory: Optional[Callable[[str, str, str], Any]] = None) -> TestReport:
    """mode 'api' (profil normal) sau 'advanced' (PostgreSQL + Storage). Doar citire; mesajele nu conțin secrete."""
    report = TestReport(mode=mode)
    try:
        if mode == "advanced":
            _test_advanced(profile, report, store, pg_connect or _default_pg_connect, storage_factory or _default_storage)
        else:
            _test_api(profile, report, store, http or requests)
    except Exception as exc:  # nu propagă valori
        report.add("Test", "FAIL", f"eroare internă ({type(exc).__name__})")
    return report


def _test_api(p: Profile, rep: TestReport, store, http) -> None:
    import cloud_sync
    err = cloud_sync.validate_url(p.api_base_url)
    if err:
        rep.add("API reachable", "FAIL", err)
        return

    key = _read_ref(p.sync_key_ref, store)
    base = p.api_base_url.rstrip("/")

    try:
        health = http.get(f"{base}/api/v1/health", timeout=TIMEOUT)
    except requests.RequestException as exc:
        rep.add("API reachable", "FAIL", f"serverul nu r?spunde ({type(exc).__name__})")
        return

    if health.status_code != 200:
        rep.add("API reachable", "FAIL", f"/api/v1/health a r?spuns {health.status_code}")
        return

    try:
        hj = health.json()
    except ValueError:
        rep.add("API reachable", "FAIL", "r?spuns invalid la /api/v1/health")
        return

    if not hj.get("ok"):
        rep.add("API reachable", "FAIL", "backend-ul cloud nu este ready")
        return

    rep.add("API reachable", "OK", base)

    if not key:
        rep.add("Tenant/device binding", "FAIL", "cheia de sincronizare lipse?te din profil")
        rep.add("Cemetery binding", "SKIP", "")
        return

    headers = {"Authorization": f"Binding {key}"}

    try:
        resp = http.get(f"{base}/api/v1/field-inbox/me", headers=headers, timeout=TIMEOUT)
    except requests.RequestException as exc:
        rep.add("Tenant/device binding", "FAIL", f"f?r? conexiune ({type(exc).__name__})")
        return

    if resp.status_code == 401:
        rep.add("Tenant/device binding", "FAIL", "cheie de sincronizare invalid? (401)")
        return
    if resp.status_code == 429:
        rep.add("Tenant/device binding", "FAIL", "prea multe ?ncerc?ri (429)")
        return
    if resp.status_code >= 400:
        rep.add("Tenant/device binding", "FAIL", f"serverul a refuzat ({resp.status_code})")
        return

    try:
        body = resp.json()
        info = body.get("result", {}) if isinstance(body, dict) else {}
    except ValueError:
        rep.add("Tenant/device binding", "FAIL", "r?spuns invalid")
        return

    server_cem = str(info.get("cemetery_uid") or "")
    device_type = str(info.get("device_type") or "")

    if device_type and device_type != "MAIN":
        rep.add("Tenant/device binding", "FAIL", f"cheia apar?ine unui dispozitiv {device_type}, nu MAIN")
        return

    rep.add("Tenant/device binding", "OK", "cheia MAIN este acceptat?")

    bound = db.get_setting("cloud_bound_cemetery") or ""

    if not server_cem:
        rep.add("Cemetery binding", "FAIL", "serverul nu a returnat cemetery_uid")
    elif bound and bound != server_cem:
        rep.add("Cemetery binding", "FAIL", "MAIN este legat local de alt cimitir")
    else:
        rep.add("Cemetery binding", "OK", f"cimitir valid: {server_cem}")


def _test_advanced(p: Profile, rep: TestReport, store, pg_connect, storage_factory) -> None:
    pw = _read_ref(p.db_password_ref, store)
    sk = _read_ref(p.supabase_secret_ref, store)
    secrets = [pw, sk]
    if not (p.db_password_ref and pw):
        rep.add("PostgreSQL reachable", "FAIL", "parola bazei de date nu este setată în profil")
    elif not (p.db_host and p.db_name and p.db_user):
        rep.add("PostgreSQL reachable", "FAIL", "db_host/db_name/db_user incomplete")
    else:
        _test_pg(p, pw, rep, pg_connect, secrets)
    _check_project_ref(p, rep)
    if not (p.supabase_secret_ref and sk):
        rep.add("Storage bucket", "FAIL", "Supabase Secret Key nu este setat în profil")
    elif not (p.supabase_url and p.storage_bucket):
        rep.add("Storage bucket", "FAIL", "supabase_url/storage_bucket incomplete")
    else:
        _test_storage(p, sk, rep, storage_factory, secrets)


def _test_pg(p: Profile, pw: str, rep: TestReport, pg_connect, secrets) -> None:
    remote = p.db_host not in _LOCAL_HOSTS
    if remote and p.sslmode not in SECURE_SSL:
        rep.add("sslmode", "FAIL", f"sslmode={p.sslmode} nu e permis pentru o gazdă externă (cere require/verify-ca/verify-full)")
    else:
        rep.add("sslmode", "OK", p.sslmode)
    conn = None
    try:
        conn = pg_connect(host=p.db_host, port=p.db_port, dbname=p.db_name, user=p.db_user, password=pw,
                          sslmode=p.sslmode, connect_timeout=8, autocommit=False)
        conn.read_only = True
        conn.execute("SELECT 1").fetchone()
        rep.add("PostgreSQL reachable", "OK", "conectat (tranzacție read-only)")
        row = conn.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()
        ver = int(row[0])
        rep.add("schema_migrations max version", "OK" if ver >= EXPECTED_MIN_SCHEMA else "FAIL",
                f"{ver} (minim așteptat {EXPECTED_MIN_SCHEMA})")
        try:
            ssl_row = conn.execute("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()").fetchone()
            if ssl_row is not None and remote:
                rep.add("TLS activ pe conexiune", "OK" if ssl_row[0] else "FAIL", "")
        except Exception:
            rep.add("TLS activ pe conexiune", "WARN", "nu se poate verifica")
    except Exception as exc:
        rep.add("PostgreSQL reachable", "FAIL", _scrub(f"{type(exc).__name__}: {_pg_hint(exc)}", secrets))
    finally:
        if conn is not None:
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass


def _check_project_ref(p: Profile, rep: TestReport) -> None:
    derived = derive_project_refs({"supabase_url": p.supabase_url, "db_host": p.db_host, "db_user": p.db_user})
    if not p.project_ref:
        rep.add("project ref", "WARN", "project_ref nu este completat")
    elif not derived:
        rep.add("project ref", "WARN", "nu poate fi dedus din URL/gazdă/utilizator (profil CUSTOM)")
    elif all(v == p.project_ref for v in derived.values()):
        rep.add("project ref", "OK", p.project_ref)
    else:
        rep.add("project ref", "FAIL", "nu corespunde cu supabase_url/db_host/db_user")


def _test_storage(p: Profile, sk: str, rep: TestReport, storage_factory, secrets) -> None:
    try:
        info = storage_factory(p.supabase_url, sk, p.storage_bucket).bucket_info()
    except Exception as exc:
        rep.add("Storage bucket exists", "FAIL", _scrub(f"{type(exc).__name__}: acces respins sau gazdă inaccesibilă", secrets))
        return
    if info is None:
        rep.add("Storage bucket exists", "FAIL", "bucketul nu există")
        return
    rep.add("Storage bucket exists", "OK", p.storage_bucket)
    rep.add("Bucket public=false", "FAIL" if info.get("public") else "OK",
            "bucketul este PUBLIC" if info.get("public") else "privat")
