"""cimitir_registry.py — registre locale multiple (organizații -> cimitire) pentru MAIN, local-first.

Fiecare cimitir are un registru separat, identificat DOAR prin UUID:
    <DATA_DIR>/registries/<cemetery_uid>/{database.sqlite, media/, cache/, logs/, outbox/}
Catalogul (organizații, cimitire, registrul activ) este un SQLite mic: <DATA_DIR>/registry_catalog.sqlite.

Legarea la un registru (binding):
  * MAIN (proces cu un singur registru activ) -> `activate()` (implicit al procesului);
  * portal/admin local (cereri concurente)    -> `use_binding()` per cerere (ContextVar), după registrul activ din catalog;
  * fire de fundal (sync)                     -> `pinned()`: operațiunea rămâne pe registrul pe care a început, iar
                                                 `activate()` așteaptă să se termine înainte de a comuta.
Fără internet: totul este local (sqlite3 din biblioteca standard).
"""
from __future__ import annotations

import contextlib
import contextvars
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

CATALOG_NAME = "registry_catalog.sqlite"
REGISTRIES_DIR = "registries"
DB_NAME = "database.sqlite"
MARKER_NAME = "registry.json"

ORG_TYPES: Dict[str, str] = {
    "parohie": "Parohie",
    "primarie": "Primărie / UAT",
    "operator_privat": "Operator privat",
    "manastire": "Mănăstire",
    "asociatie": "Asociație",
    "altul": "Altul",
}

ORG_FIELDS = ("org_type", "official_name", "display_name", "cui", "county", "locality", "address", "phone", "email",
              "logo_path")
CEM_FIELDS = ("name", "county", "locality", "address")


class RegistryError(Exception):
    pass


class RegistryBindingError(RegistryError):
    """O operațiune ar atinge alt registru decât cel activ."""


class RegistryBusyError(RegistryError):
    """Registrul activ are operațiuni în curs; comutarea nu se poate face acum."""


class RegistrySwitchedError(RegistryError):
    """Registrul activ s-a schimbat între timp."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_uid() -> str:
    return str(uuid.uuid4())


def valid_uid(value: str) -> str:
    """Validează un UUID (împiedică orice cale/identificator arbitrar). Întoarce forma canonică."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise RegistryError(f"UID invalid: {value!r}") from None


# ------------------------------------------------------------------ binding
@dataclass(frozen=True)
class Binding:
    organization_uid: str
    cemetery_uid: str
    root: Path
    activation_id: str = ""

    @property
    def database(self) -> Path:
        return self.root / DB_NAME

    @property
    def media(self) -> Path:
        return self.root / "media"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def outbox(self) -> Path:
        return self.root / "outbox"

    def ensure_dirs(self) -> None:
        for p in (self.root, self.media, self.cache, self.logs, self.outbox):
            p.mkdir(parents=True, exist_ok=True)

    def contains(self, path: Any) -> bool:
        try:
            root = os.path.realpath(str(self.root))
            return os.path.commonpath([root, os.path.realpath(str(path))]) == root
        except ValueError:
            return False


_lock = threading.RLock()
_cond = threading.Condition(_lock)
_default: Optional[Binding] = None
_ctx: "contextvars.ContextVar[Optional[Binding]]" = contextvars.ContextVar("cimitir_registry_binding", default=None)
_pins: Dict[str, int] = {}
_listeners: List[Callable[[Optional[Binding], Binding], None]] = []
_initialized: set = set()


def current_binding() -> Optional[Binding]:
    return _ctx.get() or _default


def require_binding() -> Binding:
    b = current_binding()
    if b is None:
        raise RegistryError("Niciun registru activ.")
    return b


def add_switch_listener(fn: Callable[[Optional[Binding], Binding], None]) -> None:
    _listeners.append(fn)


def assert_current(binding: Binding) -> None:
    """Refuză lucrul cu un registru care nu mai este cel activ (cod care a păstrat un binding vechi)."""
    cur = current_binding()
    if cur is None or cur.cemetery_uid != binding.cemetery_uid or cur.activation_id != binding.activation_id:
        raise RegistrySwitchedError("Registrul activ s-a schimbat; operațiunea a fost oprită.")


@contextlib.contextmanager
def use_binding(binding: Optional[Binding]):
    """Leagă contextul curent (cerere/fir) de un registru; revine la cel anterior la ieșire."""
    token = _ctx.set(binding)
    try:
        yield binding
    finally:
        _ctx.reset(token)


@contextlib.contextmanager
def pinned():
    """Operațiune care NU poate fi întreruptă de o comutare: rămâne pe registrul curent până la final."""
    with _lock:
        b = require_binding()
        _pins[b.activation_id] = _pins.get(b.activation_id, 0) + 1
    token = _ctx.set(b)
    try:
        yield b
    finally:
        _ctx.reset(token)
        with _cond:
            _pins[b.activation_id] -= 1
            if _pins[b.activation_id] <= 0:
                _pins.pop(b.activation_id, None)
            _cond.notify_all()


# ------------------------------------------------------------------ catalog
def _data_dir(data_dir: Any = None) -> Path:
    if data_dir is not None:
        return Path(data_dir)
    import cimitir_config
    return Path(cimitir_config.data_dir())


def catalog_path(data_dir: Any = None) -> Path:
    return _data_dir(data_dir) / CATALOG_NAME


def catalog_exists(data_dir: Any = None) -> bool:
    return catalog_path(data_dir).is_file()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS organizations (
  uid TEXT PRIMARY KEY, org_type TEXT NOT NULL, official_name TEXT NOT NULL, display_name TEXT NOT NULL,
  cui TEXT DEFAULT '', county TEXT DEFAULT '', locality TEXT DEFAULT '', address TEXT DEFAULT '',
  phone TEXT DEFAULT '', email TEXT DEFAULT '', logo_path TEXT DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS cemeteries (
  uid TEXT PRIMARY KEY, organization_uid TEXT NOT NULL REFERENCES organizations(uid),
  name TEXT NOT NULL, county TEXT DEFAULT '', locality TEXT DEFAULT '', address TEXT DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS app_state (key TEXT PRIMARY KEY, value TEXT);
"""


_schema_ready: set = set()


class RegistryCatalog:
    def __init__(self, data_dir: Any = None):
        self.data_dir = _data_dir(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / CATALOG_NAME
        if str(self.path) not in _schema_ready or not self.path.is_file():
            with self._conn() as c:
                c.executescript(_SCHEMA)
            _schema_ready.add(str(self.path))

    # -- infrastructură
    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        return _Closing(c)

    def registry_root(self, cemetery_uid: str) -> Path:
        return self.data_dir / REGISTRIES_DIR / valid_uid(cemetery_uid)

    # -- organizații
    def create_organization(self, org_type: str, official_name: str, display_name: str = "", **extra: str) -> str:
        if org_type not in ORG_TYPES:
            raise RegistryError(f"Tip de organizație necunoscut: {org_type!r}")
        official_name = (official_name or "").strip()
        if not official_name:
            raise RegistryError("Denumirea oficială este obligatorie.")
        uid = new_uid()
        vals = {k: (extra.get(k) or "").strip() for k in ("cui", "county", "locality", "address", "phone", "email", "logo_path")}
        with self._conn() as c:
            c.execute("INSERT INTO organizations (uid, org_type, official_name, display_name, cui, county, locality, address,"
                      " phone, email, logo_path, active, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
                      (uid, org_type, official_name, (display_name or official_name).strip(), vals["cui"], vals["county"],
                       vals["locality"], vals["address"], vals["phone"], vals["email"], vals["logo_path"], _now(), _now()))
        return uid

    def update_organization(self, uid: str, **fields: Any) -> None:
        uid = valid_uid(uid)
        bad = set(fields) - set(ORG_FIELDS)
        if bad:
            raise RegistryError(f"Câmpuri necunoscute: {sorted(bad)}")
        if "org_type" in fields and fields["org_type"] not in ORG_TYPES:
            raise RegistryError(f"Tip de organizație necunoscut: {fields['org_type']!r}")
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._conn() as c:
            cur = c.execute(f"UPDATE organizations SET {sets}, updated_at=? WHERE uid=?", (*fields.values(), _now(), uid))
            if cur.rowcount == 0:
                raise RegistryError("Organizația nu există.")

    def set_organization_active(self, uid: str, active: bool) -> None:
        uid = valid_uid(uid)
        with self._conn() as c:
            c.execute("UPDATE organizations SET active=?, updated_at=? WHERE uid=?", (1 if active else 0, _now(), uid))

    def get_organization(self, uid: str) -> Optional[Dict[str, Any]]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM organizations WHERE uid=?", (valid_uid(uid),)).fetchone()
        return dict(row) if row else None

    def list_organizations(self, include_inactive: bool = False) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM organizations" + ("" if include_inactive else " WHERE active=1") + " ORDER BY display_name COLLATE NOCASE"
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql)]

    # -- cimitire
    def create_cemetery(self, organization_uid: str, name: str, **extra: str) -> str:
        org = self.get_organization(organization_uid)
        if not org or not org["active"]:
            raise RegistryError("Organizația nu există sau este arhivată.")
        name = (name or "").strip()
        if not name:
            raise RegistryError("Denumirea cimitirului este obligatorie.")
        uid = new_uid()
        vals = {k: (extra.get(k) or "").strip() for k in ("county", "locality", "address")}
        with self._conn() as c:
            c.execute("INSERT INTO cemeteries (uid, organization_uid, name, county, locality, address, active, created_at, updated_at)"
                      " VALUES (?,?,?,?,?,?,1,?,?)", (uid, org["uid"], name, vals["county"], vals["locality"], vals["address"], _now(), _now()))
        self.binding_for(uid).ensure_dirs()
        return uid

    def update_cemetery(self, uid: str, **fields: Any) -> None:
        uid = valid_uid(uid)
        bad = set(fields) - set(CEM_FIELDS)
        if bad:
            raise RegistryError(f"Câmpuri necunoscute: {sorted(bad)}")
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._conn() as c:
            cur = c.execute(f"UPDATE cemeteries SET {sets}, updated_at=? WHERE uid=?", (*fields.values(), _now(), uid))
            if cur.rowcount == 0:
                raise RegistryError("Cimitirul nu există.")

    def archive_cemetery(self, uid: str, archived: bool = True) -> None:
        """Arhivarea ascunde registrul din selector și îl blochează la deschidere; datele rămân pe disc."""
        uid = valid_uid(uid)
        with self._conn() as c:
            c.execute("UPDATE cemeteries SET active=?, updated_at=? WHERE uid=?", (0 if archived else 1, _now(), uid))
        if archived and self.get_state("active_cemetery_uid") == uid:
            self.set_state("active_cemetery_uid", "")

    def get_cemetery(self, uid: str) -> Optional[Dict[str, Any]]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM cemeteries WHERE uid=?", (valid_uid(uid),)).fetchone()
        return dict(row) if row else None

    def list_cemeteries(self, organization_uid: Optional[str] = None, include_archived: bool = False) -> List[Dict[str, Any]]:
        sql, params = "SELECT * FROM cemeteries WHERE 1=1", []
        if organization_uid:
            sql, params = sql + " AND organization_uid=?", [valid_uid(organization_uid)]
        if not include_archived:
            sql += " AND active=1"
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql + " ORDER BY name COLLATE NOCASE", params)]

    # -- stare aplicație
    def get_state(self, key: str, default: str = "") -> str:
        with self._conn() as c:
            row = c.execute("SELECT value FROM app_state WHERE key=?", (key,)).fetchone()
        return row["value"] if row and row["value"] is not None else default

    def set_state(self, key: str, value: str) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO app_state (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    # -- binding
    def binding_for(self, cemetery_uid: str, activation_id: str = "") -> Binding:
        cem = self.get_cemetery(cemetery_uid)
        if not cem:
            raise RegistryError("Cimitirul nu există în catalog.")
        return Binding(cem["organization_uid"], cem["uid"], self.registry_root(cem["uid"]), activation_id)

    def openable_binding(self, cemetery_uid: str, activation_id: str = "") -> Binding:
        cem = self.get_cemetery(cemetery_uid)
        if not cem:
            raise RegistryError("Cimitirul nu există în catalog.")
        org = self.get_organization(cem["organization_uid"])
        if not cem["active"] or not org or not org["active"]:
            raise RegistryError("Registrul este arhivat și nu poate fi deschis.")
        return Binding(cem["organization_uid"], cem["uid"], self.registry_root(cem["uid"]), activation_id)

    def active_binding(self) -> Optional[Binding]:
        uid = self.get_state("active_cemetery_uid")
        if not uid:
            return None
        try:
            return self.openable_binding(uid)
        except RegistryError:
            return None


class _Closing:
    """Context manager: COMMIT/ROLLBACK + close (sqlite3 nu închide conexiunea la `with`)."""

    def __init__(self, conn: sqlite3.Connection):
        self._c = conn

    def __enter__(self):
        return self._c

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._c.commit()
            else:
                self._c.rollback()
        finally:
            self._c.close()
        return False


# ------------------------------------------------------------------ pregătirea bazei registrului
def _write_marker(binding: Binding) -> None:
    import json
    marker = binding.root / MARKER_NAME
    if not marker.exists():
        marker.write_text(json.dumps({"cemetery_uid": binding.cemetery_uid,
                                      "organization_uid": binding.organization_uid, "created": _now()}), encoding="utf-8")


def prepare_registry_db(binding: Binding) -> None:
    """Creează/actualizează schema bazei registrului și o leagă de (organizație, cimitir). Refuză o bază străină."""
    import cimitir_database as cdb
    binding.ensure_dirs()
    _write_marker(binding)
    with use_binding(binding):
        cdb.init_db()
        stored_cem = cdb.get_setting("cemetery_uid") or ""
        stored_org = cdb.get_setting("organization_uid") or ""
        if stored_cem and stored_cem != binding.cemetery_uid:
            raise RegistryBindingError("Baza din acest folder aparține altui cimitir; deschiderea este refuzată.")
        if stored_org and stored_org != binding.organization_uid:
            raise RegistryBindingError("Baza din acest folder aparține altei organizații; deschiderea este refuzată.")
        if not stored_cem:
            cdb.set_setting("cemetery_uid", binding.cemetery_uid)
        if not stored_org:
            cdb.set_setting("organization_uid", binding.organization_uid)
    try:
        import cimitir_auth
        with use_binding(binding):
            cimitir_auth.init_auth()
    except Exception:
        pass
    _initialized.add(str(binding.database))


def ensure_prepared(binding: Binding) -> None:
    if str(binding.database) not in _initialized:
        prepare_registry_db(binding)


def _checkpoint(binding: Binding) -> None:
    try:
        conn = sqlite3.connect(binding.database, timeout=5)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except sqlite3.Error:
        pass


# ------------------------------------------------------------------ comutare (MAIN)
def activate(cemetery_uid: str, data_dir: Any = None, wait_seconds: float = 10.0,
             catalog: Optional[RegistryCatalog] = None) -> Binding:
    """Face registrul activ pentru TOT procesul MAIN (SQLite, media, cache, outbox, setări). Așteaptă operațiunile fixate."""
    global _default
    cat = catalog or RegistryCatalog(data_dir)
    new = cat.openable_binding(cemetery_uid, activation_id=new_uid())
    prepare_registry_db(new)
    deadline = time.monotonic() + wait_seconds
    with _cond:
        old = _default
        while old is not None and _pins.get(old.activation_id, 0) > 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RegistryBusyError("Registrul activ are operațiuni în curs (sincronizare/scriere); încearcă din nou.")
            _cond.wait(remaining)
        if old is not None and old.cemetery_uid != new.cemetery_uid:
            _checkpoint(old)
        _default = new
        cat.set_state("active_cemetery_uid", new.cemetery_uid)
        cat.set_state("legacy_mode", "0")
    for fn in list(_listeners):
        try:
            fn(old, new)
        except Exception:  # un ascultător defect nu blochează comutarea
            pass
    return new


def deactivate() -> None:
    global _default
    with _cond:
        _default = None


# ------------------------------------------------------------------ portal: registrul activ din catalog, per cerere
def binding_from_catalog(data_dir: Any = None) -> Optional[Binding]:
    """Registrul marcat activ de MAIN, citit din catalog la fiecare apel (câteva interogări mici: schimbarea e văzută imediat).
    None dacă nu există mod registre sau nu e ales niciun registru."""
    override = os.environ.get("CIMITIR_REGISTRY_UID", "").strip()
    if not catalog_path(data_dir).is_file():
        return None
    cat = RegistryCatalog(data_dir)
    if override:
        try:
            return cat.openable_binding(override, activation_id=override)
        except RegistryError:
            return None
    binding = cat.active_binding()
    if binding is not None:
        binding = Binding(binding.organization_uid, binding.cemetery_uid, binding.root, binding.cemetery_uid)
    return binding


def bind_context_from_catalog(data_dir: Any = None) -> Optional[Binding]:
    """Leagă contextul curent (cerere/fir portal) de registrul activ și îl pregătește o singură dată."""
    binding = binding_from_catalog(data_dir)
    if binding is None:
        _ctx.set(None)
        return None
    with _lock:
        ensure_prepared(binding)
    _ctx.set(binding)
    return binding


# ------------------------------------------------------------------ decizie la pornirea MAIN
def startup_plan(data_dir: Any = None) -> Dict[str, Any]:
    """Ce trebuie să facă MAIN la pornire: 'auto' (intră direct) sau 'selector'. Nu modifică nimic."""
    plan: Dict[str, Any] = {"action": "selector", "cemetery_uid": "", "registry_count": 0}
    if not catalog_exists(data_dir):
        return plan
    cat = RegistryCatalog(data_dir)
    cems = [c for c in cat.list_cemeteries() if (cat.get_organization(c["organization_uid"]) or {}).get("active")]
    plan["registry_count"] = len(cems)
    if len(cems) == 1 and cat.get_state("auto_open") == "1":
        plan.update(action="auto", cemetery_uid=cems[0]["uid"])
    return plan


def set_auto_open(enabled: bool, data_dir: Any = None) -> None:
    RegistryCatalog(data_dir).set_state("auto_open", "1" if enabled else "0")


def working_dir(data_dir: Any = None) -> Path:
    """Folderul de lucru pentru copii de siguranță: registrul activ (dacă există mod registre), altfel DATA_DIR."""
    b = current_binding()
    if b is None and catalog_exists(data_dir):
        try:
            b = binding_from_catalog(data_dir)
        except Exception:
            b = None
    return b.root if b is not None else _data_dir(data_dir)


def set_legacy_mode(enabled: bool, data_dir: Any = None) -> None:
    """MAIN continuă explicit cu baza veche (cimitir.db), fără registre."""
    RegistryCatalog(data_dir).set_state("legacy_mode", "1" if enabled else "0")


def legacy_fallback_allowed(data_dir: Any = None) -> bool:
    """Portalul poate folosi cimitir.db când nu există registru activ? Doar pentru o instalație clasică (fără catalog),
    una care a ales explicit baza veche, sau una cu cimitir.db existent. Un catalog gol și fără bază veche NU creează
    o bază „la întâmplare”: portalul așteaptă să deschidă MAIN un registru."""
    if not catalog_exists(data_dir):
        return True
    cat = RegistryCatalog(data_dir)
    return cat.get_state("legacy_mode") == "1" or (_data_dir(data_dir) / "cimitir.db").is_file()
