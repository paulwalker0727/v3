"""pg_compat.py — strat subțire peste psycopg 3 care imită API-ul `sqlite3` folosit de restul aplicației.

De ce există: o parte din cod (sync_api, citizen_api, cimitir_auth, cimitir_requests, cimitir_notify, ...) rulează SQL
direct prin `get_connection()` cu `?` ca placeholder și rânduri `sqlite3.Row`. Ca să nu rescriem tot, conexiunea
PostgreSQL oferă aceeași interfață:

  * `conn.execute(sql, params)` -> cursor cu `fetchone/fetchall/__iter__/rowcount/lastrowid`
  * rânduri accesibile după nume, index, `.keys()` și `dict(row)`
  * traducere SQL (SQLite -> PostgreSQL): `?` -> `%s`, `LIKE` -> `ILIKE`, `INSERT OR IGNORE/REPLACE`,
    `AUTOINCREMENT`, `BEGIN IMMEDIATE`, `lastrowid` (prin `RETURNING id`)

Conexiunea se configurează EXCLUSIV din variabile de mediu (DATABASE_URL). Nicio credențială în cod.
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------- configurare (doar din mediu)
ENV_DATABASE_URL = "DATABASE_URL"
ENV_POOL_MAX = "CIMITIR_PG_POOL_MAX"
ENV_POOL_MIN = "CIMITIR_PG_POOL_MIN"
ENV_CONNECT_TIMEOUT = "CIMITIR_PG_CONNECT_TIMEOUT"
ENV_ACQUIRE_TIMEOUT = "CIMITIR_PG_ACQUIRE_TIMEOUT"  # cât așteaptă o cerere o conexiune din pool (implicit 10 s)
ENV_RETRY_SECONDS = "CIMITIR_PG_RETRY_SECONDS"   # cât timp nu mai reîncercăm conectarea după un eșec (implicit 3 s)

# Cheia advisory-lock care înlocuiește `BEGIN IMMEDIATE` din SQLite (un singur scriitor în secțiunile critice).
WRITE_LOCK_KEY = 7346101
MIGRATION_LOCK_KEY = 7346102

# Tabele cu coloană `id` numerică (pentru `lastrowid` via RETURNING id).
ID_TABLES = {
    "locuri_de_veci", "concesionari", "contracte", "inhumati", "photos", "amenajari_avize", "guides", "rows_ghid",
    "plati", "documents", "citizens", "citizen_resets", "cereri_concesionare", "users", "audit_log", "email_outbox",
}
# Cheia de conflict pentru `INSERT OR REPLACE` (upsert).
CONFLICT_KEYS = {
    "settings": ("key",), "sectoare": ("cemetery_uid", "id"), "loc_extra": ("cemetery_uid", "cod_loc"),
    "loc_holders": ("cemetery_uid", "cod_loc", "holder_hash"), "sync_ops": ("op_id",),
}


class PgConfigError(RuntimeError):
    """DATABASE_URL lipsește sau este invalid."""


class PgUnavailable(RuntimeError):
    """PostgreSQL nu poate fi contactat (mesaj fără host/parolă)."""


def database_url() -> str:
    # În mod normal mediul e deja populat (load_env din cimitir_config). Încercăm și încărcarea lui, dacă e disponibil.
    url = os.environ.get(ENV_DATABASE_URL, "").strip()
    if not url:
        try:
            import cimitir_config
            cimitir_config.load_env()
            url = os.environ.get(ENV_DATABASE_URL, "").strip()
        except Exception:
            pass
    if not url:
        raise PgConfigError("DATABASE_URL nu este setat (necesar pentru CIMITIR_DB_ENGINE=postgres).")
    if url.startswith("postgres://"):  # alias folosit de unii furnizori
        url = "postgresql://" + url[len("postgres://"):]
    return url


def mask_url(url: str) -> str:
    """Ascunde parola din URL / DSN (forma URL cu utilizator și parolă, ?password=..., password=...) pentru loguri."""
    url = re.sub(r"(://[^:/@\s]+):[^@\s]*@", r"\1:***@", url)
    url = re.sub(r"(?i)([?&]password=)[^&\s]*", r"\1***", url)
    return re.sub(r"(?i)(\bpassword\s*=\s*)('[^']*'|\S+)", r"\1***", url)


def redact(text: str, url: Optional[str] = None) -> str:
    """Scoate din `text` orice fragment sensibil: URL-ul complet, parola extrasă din el și formele mascate standard."""
    text = str(text)
    secrets_ = []
    if url:
        secrets_.append(url)
        m = re.search(r"://[^:/@\s]+:([^@\s]+)@", url) or re.search(r"(?i)password=([^&\s']+)", url)
        if m:
            secrets_.append(m.group(1))
    for secret in sorted(set(filter(None, secrets_)), key=len, reverse=True):
        text = text.replace(secret, "***")
    if url:  # adresa serverului și utilizatorul nu trebuie să apărea în loguri / mesaje de eroare
        info = {}
        try:
            from psycopg.conninfo import conninfo_to_dict  # înțelege și URL, și DSN „key=value”
            info = conninfo_to_dict(url)
        except Exception:
            pass
        for key, label in (("host", "<host>"), ("hostaddr", "<host>"), ("user", "<user>"), ("dbname", "<db>")):
            value = str(info.get(key) or "")
            if len(value) > 2:
                text = text.replace(value, label)
        if info.get("password"):
            text = text.replace(str(info["password"]), "***")
    return mask_url(text)


# ---------------------------------------------------------------------------- rânduri tip sqlite3.Row
class Row(tuple):
    """Tuplu cu acces și după nume: `row["col"]`, `row[0]`, `row.keys()`, `dict(row)`, `for (a,) in rows`."""

    def __new__(cls, values: Sequence[Any], index: Dict[str, int]):
        obj = super().__new__(cls, values)
        obj._index = index
        return obj

    def keys(self) -> List[str]:
        return list(self._index.keys())

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                return tuple.__getitem__(self, self._index[key])
            except KeyError:
                raise IndexError(f"No item with that key: {key!r}") from None
        return tuple.__getitem__(self, key)


def _row_factory(cursor):
    cols = [d.name for d in (cursor.description or [])]
    index = {name: i for i, name in enumerate(cols)}

    def make(values):
        return Row(values, index)
    return make


# ---------------------------------------------------------------------------- traducere SQL
_RE_INSERT_OR_IGNORE = re.compile(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO", re.I)
_RE_INSERT_OR_REPLACE = re.compile(r"^\s*INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)\s*\(([^)]*)\)", re.I)
_RE_INSERT_TABLE = re.compile(r"^\s*INSERT\s+INTO\s+(\w+)", re.I)
_RE_BEGIN = re.compile(r"^\s*BEGIN(\s+(IMMEDIATE|EXCLUSIVE|DEFERRED))?(\s+TRANSACTION)?\s*;?\s*$", re.I)
_RE_PRAGMA = re.compile(r"^\s*PRAGMA\b", re.I)
_RE_LIKE = re.compile(r"\bLIKE\b", re.I)
_RE_CREATE = re.compile(r"^\s*CREATE\s+TABLE", re.I)
_RE_IFNULL = re.compile(r"\bIFNULL\s*\(", re.I)


def _escape_and_bind(sql: str) -> str:
    """`?` -> `%s` și `%` -> `%%`, doar în afara literalelor între apostrofuri (în literale doar `%` se dublează)."""
    out: List[str] = []
    in_str = False
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


def translate(sql: str) -> Tuple[str, bool]:
    """Traduce un statement SQLite în PostgreSQL. Returnează (sql, wants_lastrowid)."""
    text = sql.strip().rstrip(";")
    wants_id = False

    if _RE_CREATE.match(text):
        text = re.sub(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY",
                      text, flags=re.I)
        text = re.sub(r"\bREAL\b", "DOUBLE PRECISION", text, flags=re.I)
        text = re.sub(r"datetime\('now'\)", "to_char(now() AT TIME ZONE 'utc','YYYY-MM-DD HH24:MI:SS')", text,
                      flags=re.I)

    suffix = ""
    if _RE_INSERT_OR_IGNORE.match(text):
        text = _RE_INSERT_OR_IGNORE.sub("INSERT INTO", text, count=1)
        suffix = " ON CONFLICT DO NOTHING"
    else:
        m = _RE_INSERT_OR_REPLACE.match(text)
        if m:
            table, cols = m.group(1).lower(), [c.strip() for c in m.group(2).split(",")]
            keys = CONFLICT_KEYS.get(table)
            if not keys:
                raise ValueError(f"INSERT OR REPLACE nesuportat pentru tabela {table}")
            text = _RE_INSERT_OR_REPLACE.sub(lambda mm: f"INSERT INTO {mm.group(1)} ({mm.group(2)})", text, count=1)
            updates = [c for c in cols if c not in keys]
            suffix = (f" ON CONFLICT ({', '.join(keys)}) DO UPDATE SET " +
                      ", ".join(f"{c}=EXCLUDED.{c}" for c in updates)) if updates else \
                     f" ON CONFLICT ({', '.join(keys)}) DO NOTHING"

    text = _RE_IFNULL.sub("COALESCE(", text)
    text = _RE_LIKE.sub("ILIKE", text)  # LIKE din SQLite ignoră majusculele; în PostgreSQL trebuie ILIKE
    text = _escape_and_bind(text) + suffix

    m = _RE_INSERT_TABLE.match(text)
    if m and m.group(1).lower() in ID_TABLES and "RETURNING" not in text.upper():
        text += " RETURNING id"
        wants_id = True
    return text, wants_id


# ---------------------------------------------------------------------------- cursor / conexiune
class Cursor:
    """Cursor compatibil sqlite3: rezultatele sunt deja pregătite, deci `fetch*` și iterarea sunt simple."""

    def __init__(self, raw, lastrowid: Optional[int] = None):
        self._raw = raw
        self.lastrowid = lastrowid
        self.rowcount = raw.rowcount if raw is not None else 0
        self.description = raw.description if raw is not None else None

    def fetchone(self):
        if self._raw is None or self._raw.description is None:
            return None
        return self._raw.fetchone()

    def fetchall(self):
        if self._raw is None or self._raw.description is None:
            return []
        return self._raw.fetchall()

    def fetchmany(self, size: int = 1):
        if self._raw is None or self._raw.description is None:
            return []
        return self._raw.fetchmany(size)

    def __iter__(self):
        return iter(self.fetchall())

    def close(self) -> None:
        if self._raw is not None:
            self._raw.close()


class Connection:
    """Conexiune din pool, cu semantica `sqlite3` (autocommit; tranzacții explicite prin BEGIN/COMMIT/ROLLBACK)."""

    def __init__(self, pool, raw):
        self._pool = pool
        self._raw = raw
        self._closed = False
        self.row_factory = None  # ignorat: rândurile sunt mereu `Row`

    # -- executare
    def execute(self, sql: str, params: Sequence[Any] = ()) -> Cursor:
        if _RE_PRAGMA.match(sql):
            return Cursor(None)
        m = _RE_BEGIN.match(sql)
        if m:
            self._raw.execute("BEGIN")
            if (m.group(2) or "").upper() in ("IMMEDIATE", "EXCLUSIVE"):
                # înlocuiește blocarea de scriere SQLite: secțiunile critice se serializează între ele
                self._raw.execute("SELECT pg_advisory_xact_lock(%s)", (WRITE_LOCK_KEY,))
            return Cursor(None)
        text, wants_id = translate(sql)
        cur = self._raw.cursor(row_factory=_row_factory)
        cur.execute(text, tuple(params) if params is not None else None)
        lastrowid = None
        if wants_id:
            row = cur.fetchone()
            lastrowid = row[0] if row else None
        return Cursor(cur, lastrowid)

    def executemany(self, sql: str, seq: Sequence[Sequence[Any]]) -> Cursor:
        text, _ = translate(sql)
        cur = self._raw.cursor(row_factory=_row_factory)
        cur.executemany(text, [tuple(p) for p in seq])
        return Cursor(cur)

    def cursor(self):
        return _CursorShim(self)

    def commit(self) -> None:  # autocommit: COMMIT explicit se trimite prin execute("COMMIT")
        pass

    def rollback(self) -> None:
        if self._raw.info.transaction_status != 0:
            self._raw.execute("ROLLBACK")

    @property
    def raw(self):
        return self._raw

    # -- ciclu de viață
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._raw.info.transaction_status != 0:  # tranzacție rămasă deschisă -> anulată
                self._raw.execute("ROLLBACK")
        finally:
            self._pool.putconn(self._raw)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class _CursorShim:
    """`conn.cursor()` -> obiect cu `.execute()` care deleagă la conexiune (compatibilitate)."""

    def __init__(self, conn: Connection):
        self._conn = conn
        self._last: Optional[Cursor] = None
        self.lastrowid = None
        self.rowcount = 0

    def execute(self, sql, params=()):
        self._last = self._conn.execute(sql, params)
        self.lastrowid, self.rowcount = self._last.lastrowid, self._last.rowcount
        return self

    def fetchone(self):
        return self._last.fetchone() if self._last else None

    def fetchall(self):
        return self._last.fetchall() if self._last else []

    def __iter__(self):
        return iter(self.fetchall())


# ---------------------------------------------------------------------------- pool
_pool = None
_pool_url: Optional[str] = None
_pool_lock = threading.Lock()
_fail_until = 0.0  # circuit breaker: până atunci cererile eșuează imediat, nu așteaptă fiecare timeout-ul de conectare


def _configure(raw) -> None:
    """Valori JSONB / TIMESTAMPTZ ca text -> codul existent (`json.loads(row["points"])`) rămâne valabil."""
    from psycopg.types.string import TextLoader
    for name in ("jsonb", "json", "timestamptz", "timestamp", "date", "uuid"):
        raw.adapters.register_loader(name, TextLoader)


def get_pool():
    """Pool unic per proces, creat leneș din DATABASE_URL (reînnoit dacă URL-ul se schimbă, ex. în teste)."""
    global _pool, _pool_url, _fail_until
    url = database_url()
    with _pool_lock:
        if _pool is not None and _pool_url == url:
            return _pool
        if _pool is not None:
            try:
                _pool.close()
            except Exception:
                pass
            _pool = None
        if time.monotonic() < _fail_until:
            raise PgUnavailable("PostgreSQL indisponibil (reîncercare în curând)")
        from psycopg_pool import ConnectionPool
        timeout = float(os.environ.get(ENV_CONNECT_TIMEOUT, "10") or 10)
        pool = ConnectionPool(
            conninfo=url,
            min_size=int(os.environ.get(ENV_POOL_MIN, "1") or 1),
            max_size=int(os.environ.get(ENV_POOL_MAX, "20") or 20),
            kwargs={"autocommit": True, "connect_timeout": max(1, int(timeout))},
            configure=_configure,
            check=ConnectionPool.check_connection,  # validează conexiunea la preluare (ex. închisă de furnizor după inactivitate)
            timeout=float(os.environ.get(ENV_ACQUIRE_TIMEOUT, "10") or 10),
            open=False,
        )
        try:
            pool.open(wait=True, timeout=timeout + 1)
        except Exception as exc:
            pool.close()  # oprește firele de reconectare
            _fail_until = time.monotonic() + float(os.environ.get(ENV_RETRY_SECONDS, "3") or 3)
            raise PgUnavailable("PostgreSQL indisponibil") from exc
        _pool, _pool_url = pool, url
        return _pool


_tenant: Optional[Tuple[str, str]] = None   # (organization_uid, cemetery_uid) al cimitirului servit de acest proces


def set_tenant(org_uid: Optional[str], cemetery_uid: Optional[str]) -> None:
    """Fixează tenantul activ; fiecare conexiune preluată din pool primește contextul `app.org_uid` / `app.cemetery_uids`."""
    global _tenant
    _tenant = (str(org_uid), str(cemetery_uid)) if org_uid and cemetery_uid else None


def tenant() -> Optional[Tuple[str, str]]:
    return _tenant


def _apply_tenant(raw) -> None:
    ctx = _tenant or ("", "")
    if getattr(raw, "_cim_ctx", None) == ctx:
        return
    raw.execute("SELECT set_config('app.org_uid', %s, false), set_config('app.cemetery_uids', %s, false)", ctx)
    raw._cim_ctx = ctx


def close_pool() -> None:
    global _pool, _pool_url, _tenant
    _tenant = None
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.close()
            finally:
                _pool, _pool_url = None, None


def connect(timeout: Optional[float] = None) -> Connection:
    """Conexiune din pool; `timeout` (secunde) suprascrie așteptarea implicită (ex. health-check rapid)."""
    pool = get_pool()
    raw = pool.getconn(timeout=timeout) if timeout else pool.getconn()
    try:
        _apply_tenant(raw)
    except Exception:
        pool.putconn(raw)
        raise
    return Connection(pool, raw)
