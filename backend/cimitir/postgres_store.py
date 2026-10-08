"""postgres_store.py — motorul PostgreSQL (online) al aplicației Cimitir.

Oferă ACEEAȘI interfață publică ca nucleul SQLite (`shared/cimitir_database.py`) — nume, argumente și forme de
răspuns identice — ca endpointurile FastAPI existente să funcționeze fără modificări. Selectarea motorului se face în
`store.py` prin `CIMITIR_DB_ENGINE=postgres`.

Configurare (doar variabile de mediu; nicio credențială în cod):
    DATABASE_URL                postgresql://<utilizator>:<parola>@<host>:5432/<baza>?sslmode=require
    CIMITIR_PG_POOL_MAX / _MIN  dimensiunea pool-ului (implicit 20 / 1)
    CIMITIR_PG_CONNECT_TIMEOUT  secunde (implicit 10)

Schema se creează / actualizează automat (`init_db`) din `pg_migrations/NNN_*.sql`, urmărită în `schema_migrations`.
SQL-ul intern folosește `?` ca placeholder; `pg_compat` îl traduce pentru psycopg.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pg_compat
from pg_compat import get_pool, close_pool  # noqa: F401  (re-export)

# Utilitarele de fișiere media rămân comune cu SQLite (media pe disc; Supabase Storage vine într-o etapă ulterioară).
_SHARED = Path(__file__).resolve().parents[2] / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
import cimitir_database as _sqlite  # noqa: E402

ENGINE = "postgres"
DATA_DIR = _sqlite.DATA_DIR
DB_PATH = ""  # nu există fișier; păstrat pentru compatibilitate
HEALTH_TIMEOUT = 3.0  # secunde
MIGRATIONS_DIR = Path(__file__).resolve().parent / "pg_migrations"

import uuid  # noqa: E402

import_media_file = _sqlite.import_media_file
_media_dir = _sqlite._media_dir
_is_managed_media = _sqlite._is_managed_media


# =============================================================================
# CONEXIUNE / HELPERI
# =============================================================================
def get_connection(timeout: Optional[float] = None) -> pg_compat.Connection:
    """Conexiune din pool, cu interfață `sqlite3` (vezi pg_compat)."""
    return pg_compat.connect(timeout)


def set_active_db_path(_path: str) -> None:  # compat cu SQLite (un singur Postgres per proces)
    return None


def current_db_path() -> str:
    try:
        return pg_compat.mask_url(pg_compat.database_url())
    except pg_compat.PgConfigError:
        return ""


_TENANT_COLS = ("organization_uid", "cemetery_uid")


def _row_to_dict(row) -> Dict[str, Any]:
    """Row -> dict cu None -> '' (ca în varianta SQLite, util pentru UI)."""
    if row is None:
        return {}
    keys = row.keys()
    hidden = (("version", "change_seq") if "change_seq" in keys else ()) + _TENANT_COLS   # metadate de sincronizare / tenant, nu date de business
    return {k: ("" if row[k] is None else row[k]) for k in keys if k not in hidden}


def _plain(row) -> Dict[str, Any]:
    """dict(row) fără coloanele interne de tenant (aceeași formă ca pe SQLite)."""
    return {k: row[k] for k in row.keys() if k not in _TENANT_COLS}


def _jload(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _jdump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _num(value: Any) -> Optional[float]:
    """'' / None -> NULL; altfel float (SQLite accepta '' în coloane REAL, PostgreSQL nu)."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@contextmanager
def _db():
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def _now_ui() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M")


# =============================================================================
# MIGRAȚII / INIȚIALIZARE
# =============================================================================
_initialized: set = set()


TENANT_MIGRATIONS_FROM = 8   # 008–013: multi-tenant (organizații, cimitire, chei compuse, RLS)


def tenant_migration_block_reason(url: str, allow: Optional[str] = None) -> str:
    """'' dacă migrările multi-tenant (>=008) pot rula pe această bază; altfel motivul. Producția e blocată permanent, fără ocolire."""
    import cimitir_test_guard as guard
    if guard.has_protected_ref(url or ""):
        return "baza indică proiectul de producție: migrările multi-tenant (008–013) sunt blocate permanent în această etapă"
    if (os.environ.get("CIMITIR_ALLOW_TENANT_MIGRATIONS") if allow is None else allow) != "1":
        return "migrările multi-tenant (008–013) se aplică doar explicit, cu CIMITIR_ALLOW_TENANT_MIGRATIONS=1 (bază de test)"
    return ""


def migrate() -> List[str]:
    """Aplică migrațiile SQL lipsă (idempotent, serializat între procese prin advisory lock). Returnează numele aplicate."""
    applied_now: List[str] = []
    conn = get_connection()
    raw = conn.raw
    try:
        raw.execute("SELECT pg_advisory_lock(%s)", (pg_compat.MIGRATION_LOCK_KEY,))
        try:
            raw.execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
                        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
            done = {r[0] for r in raw.execute("SELECT version FROM schema_migrations").fetchall()}
            for path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
                version = int(path.name[:3])
                if version in done:
                    continue
                if version >= TENANT_MIGRATIONS_FROM:
                    reason = tenant_migration_block_reason(pg_compat.database_url())
                    if reason:
                        raise RuntimeError(f"Migrarea {path.name} NU a fost aplicată: {reason}.")
                with raw.transaction():
                    raw.execute(path.read_text(encoding="utf-8"))
                    raw.execute("INSERT INTO schema_migrations (version, name) VALUES (%s, %s)", (version, path.name))
                applied_now.append(path.name)
        finally:
            raw.execute("SELECT pg_advisory_unlock(%s)", (pg_compat.MIGRATION_LOCK_KEY,))
    finally:
        conn.close()
    return applied_now


def tenant_scope(alias: str = "", level: str = "cemetery") -> str:
    """Fragment SQL de filtrare pe tenantul activ (cimitir sau organizație) pentru modulele care rulează pe ambele motoare."""
    p = f"{alias}." if alias else ""
    return f"{p}organization_uid = app_org_uid()" if level == "org" else f"{p}cemetery_uid = app_default_cemetery_uid()"


def _resolve_tenant() -> None:
    """Tenantul servit de proces = cimitirul din settings.cemetery_id (creat de migrarea 009); toate conexiunile îl primesc."""
    with contextlib.closing(pg_compat.connect()) as conn:
        row = conn.execute("SELECT c.organization_uid::text AS org, c.uid::text AS cem FROM cemeteries c "
                           "JOIN settings s ON s.key = 'cemetery_id' AND s.value = c.uid::text").fetchone()
    if not row:
        raise RuntimeError("Cimitirul activ (settings.cemetery_id) nu există în tabela cemeteries; migrarea 009 nu a rulat corect.")
    pg_compat.set_tenant(row["org"], row["cem"])


def schema_version() -> int:
    with _db() as conn:
        return int(conn.execute("SELECT COALESCE(MAX(version),0) AS v FROM schema_migrations").fetchone()["v"])


def init_db(force: bool = False) -> None:
    """Creează/actualizează schema (migrații) și datele de bază. Rapid la apelurile ulterioare (cache per DSN)."""
    key = pg_compat.database_url()
    if key in _initialized and not force:
        return
    migrate()
    _resolve_tenant()
    with _db() as conn:
        if not conn.execute("SELECT 1 FROM settings WHERE key='cemetery_id'").fetchone():
            conn.execute("INSERT INTO settings (key, value) VALUES ('cemetery_id', ?) ON CONFLICT (key) DO NOTHING",
                         (str(uuid.uuid4()),))
        for (rid,) in conn.execute(
                "SELECT id FROM cereri_concesionare WHERE cod_urmarire IS NULL OR cod_urmarire=''").fetchall():
            conn.execute("UPDATE cereri_concesionare SET cod_urmarire=? WHERE id=?", (_new_tracking_code(conn), rid))
    _initialized.add(key)


def drop_all_for_tests() -> None:
    """DOAR pentru teste: șterge tot schema `public`. Cere CIMITIR_PG_ALLOW_DROP=1."""
    if os.environ.get("CIMITIR_PG_ALLOW_DROP") != "1":
        raise RuntimeError("drop_all_for_tests cere CIMITIR_PG_ALLOW_DROP=1")
    with _db() as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    pg_compat.set_tenant(None, None)
    _initialized.clear()


def ping() -> bool:
    try:
        with _db() as conn:
            return conn.execute("SELECT 1 AS ok").fetchone()["ok"] == 1
    except Exception:
        return False


def health() -> Dict[str, Any]:
    """Stare minimală pentru endpointul /api/status (fără host, parolă sau mesaje de eroare brute)."""
    started = time.perf_counter()
    info: Dict[str, Any] = {"engine": ENGINE, "connected": False, "schema_version": None, "latency_ms": None}
    try:
        with contextlib.closing(get_connection(timeout=HEALTH_TIMEOUT)) as conn:  # răspuns rapid chiar dacă baza e căzută
            row = conn.execute("SELECT current_setting('server_version') AS sv, current_database() AS db").fetchone()
            info["connected"] = True
            info["server_version"] = row["sv"]
            info["database"] = row["db"]
            try:
                info["schema_version"] = int(conn.execute(
                    "SELECT COALESCE(MAX(version),0) AS v FROM schema_migrations").fetchone()["v"])
            except Exception:
                info["schema_version"] = 0  # schema încă neinițializată
    except pg_compat.PgConfigError:
        info["error"] = "DATABASE_URL lipsește"
    except Exception as exc:
        info["error"] = type(exc).__name__
    info["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return info


# =============================================================================
# LOCURI DE VECI
# =============================================================================
_LOC_LIST_COLS = ("id, cod_loc, sector, parcela, rand, numar, capacitate, disponibilitate, tip, stare, categorie, "
                  "suprafata, nr_vechi, numar_cf, ocupat, observatii")


def load_loc_by_cod(cod_loc: str) -> Optional[Dict[str, Any]]:
    with _db() as conn:
        row = conn.execute(
            "SELECT id, cod_loc, sector, parcela, rand, numar, capacitate, disponibilitate, tip, stare, categorie, "
            "suprafata, nr_vechi, numar_cf, points, color, extra_data, "
            "(COALESCE(are_inhumari, 0) > 0 OR EXISTS (SELECT 1 FROM inhumati i WHERE i.id_loc = locuri_de_veci.id))::int "
            "AS are_inhumari FROM locuri_de_veci WHERE cod_loc = ? AND cemetery_uid = app_default_cemetery_uid()", (cod_loc,)).fetchone()
        if not row:
            return None
        d = {k: row[k] for k in row.keys()}
        d["points"] = _jload(row["points"], [])
        d["extra_data"] = _jload(row["extra_data"], {})
        return d


def get_loc_id_by_cod(cod_loc: str) -> Optional[int]:
    if not cod_loc:
        return None
    with _db() as conn:
        row = conn.execute("SELECT id FROM locuri_de_veci WHERE cod_loc = ? AND cemetery_uid = app_default_cemetery_uid()", (cod_loc,)).fetchone()
        return row["id"] if row else None


def save_plot_to_db(cod_loc: str, points: List[tuple], color: str) -> Optional[int]:
    try:
        with _db() as conn:
            # UPDATE întâi (nu consumă valori din secvența `id`); INSERT doar dacă locul lipsește (ON CONFLICT = anti-cursă)
            row = conn.execute("UPDATE locuri_de_veci SET points=?, color=? WHERE cod_loc=? AND cemetery_uid = app_default_cemetery_uid() RETURNING id",
                               (_jdump(points), color, cod_loc)).fetchone()
            if row:
                return row["id"]
            row = conn.execute(
                "INSERT INTO locuri_de_veci (cod_loc, points, color, extra_data) VALUES (?, ?, ?, '{}') "
                "ON CONFLICT (cemetery_uid, cod_loc) DO UPDATE SET points = EXCLUDED.points, color = EXCLUDED.color RETURNING id",
                (cod_loc, _jdump(points), color)).fetchone()
            return row["id"] if row else None
    except Exception as e:
        print(f"[PG ERROR save_plot_to_db] {e}")
        return None


def _ensure_loc(conn, cod_loc: str) -> None:
    if not conn.execute("SELECT 1 FROM locuri_de_veci WHERE cod_loc=? AND cemetery_uid = app_default_cemetery_uid()", (cod_loc,)).fetchone():
        conn.execute("INSERT INTO locuri_de_veci (cod_loc, points, color, extra_data) "
                     "VALUES (?, '[]', '#0078FF', '{}') ON CONFLICT (cemetery_uid, cod_loc) DO NOTHING", (cod_loc,))


_LOC_META_ALLOWED = ("sector", "parcela", "rand", "numar", "capacitate", "disponibilitate", "tip", "stare",
                     "categorie", "suprafata", "nr_vechi", "numar_cf", "ocupat", "observatii")


def save_loc_metadata(cod_loc: str, metadata: Dict[str, Any]) -> bool:
    """Actualizează metadatele unui loc (creează rândul dacă lipsește)."""
    try:
        with _db() as conn:
            _ensure_loc(conn, cod_loc)
            fields, values = [], []
            for k in _LOC_META_ALLOWED:
                if k in metadata:
                    v = metadata[k]
                    if k == "suprafata":
                        v = _num(v)
                    elif k == "ocupat":
                        v = _int(v, 0)
                    fields.append(f"{k}=?")
                    values.append(v)
            if "extra_data" in metadata:
                fields.append("extra_data=?")
                values.append(_jdump(metadata["extra_data"]))
            if not fields:
                return True
            conn.execute(f"UPDATE locuri_de_veci SET {', '.join(fields)} WHERE cod_loc=? AND cemetery_uid = app_default_cemetery_uid()", (*values, cod_loc))
            return True
    except Exception as e:
        print(f"[PG ERROR save_loc_metadata] {e}")
        return False


def delete_plot(cod_loc: str) -> bool:
    try:
        with _db() as conn:
            return conn.execute("DELETE FROM locuri_de_veci WHERE cod_loc = ? AND cemetery_uid = app_default_cemetery_uid()", (cod_loc,)).rowcount > 0
    except Exception as e:
        print(f"[PG ERROR delete_plot] {e}")
        return False


def list_locuri() -> List[Dict[str, Any]]:
    with _db() as conn:
        return [_row_to_dict(r) for r in conn.execute(
            f"SELECT {_LOC_LIST_COLS} FROM locuri_de_veci WHERE cemetery_uid = app_default_cemetery_uid() ORDER BY cod_loc").fetchall()]


def search_locuri(filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Filtre: cod_loc, nr_vechi, numar_cf, sector, parcela, rand, numar (LIKE / exact dacă _precise);
    capacitate, disponibilitate, tip, stare, categorie (exact)."""
    filters = filters or {}
    precise = bool(filters.get("_precise", False))
    clauses: List[str] = []
    params: List[Any] = []
    for f in ("cod_loc", "nr_vechi", "numar_cf", "sector", "parcela", "rand", "numar"):
        val = filters.get(f)
        if val:
            clauses.append(f"{f} = ?" if precise else f"{f} ILIKE ?")
            params.append(val if precise else f"%{val}%")
    for f in ("capacitate", "disponibilitate", "tip", "stare", "categorie"):
        val = filters.get(f)
        if val:
            clauses.append(f"{f} = ?")
            params.append(val)
    where_sql = "WHERE " + " AND ".join(["cemetery_uid = app_default_cemetery_uid()"] + clauses)
    with _db() as conn:
        return [_row_to_dict(r) for r in conn.execute(
            f"SELECT {_LOC_LIST_COLS} FROM locuri_de_veci {where_sql} ORDER BY cod_loc", tuple(params)).fetchall()]


# =============================================================================
# CONCESIONARI
# =============================================================================
_CONCESIONAR_FIELDS = ["nume", "prenume", "cnp", "bi_ci", "adresa", "telefon", "email", "observatii"]


def save_concesionar(data: Dict[str, Any], concesionar_id: Optional[int] = None) -> Optional[int]:
    values = [data.get(k, "") or "" for k in _CONCESIONAR_FIELDS]
    try:
        with _db() as conn:
            if concesionar_id:
                conn.execute(f"UPDATE concesionari SET {', '.join(f'{k}=?' for k in _CONCESIONAR_FIELDS)} WHERE id=? AND cemetery_uid = app_default_cemetery_uid()",
                             (*values, concesionar_id))
                return concesionar_id
            return conn.execute(
                f"INSERT INTO concesionari ({', '.join(_CONCESIONAR_FIELDS)}) "
                f"VALUES ({', '.join('?' * len(_CONCESIONAR_FIELDS))})", tuple(values)).lastrowid
    except Exception as e:
        print(f"[PG ERROR save_concesionar] {e}")
        return None


def load_concesionari(_filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    with _db() as conn:
        return [_row_to_dict(r) for r in conn.execute(
            "SELECT id, nume, prenume, cnp, bi_ci, adresa, telefon, email, observatii "
            "FROM concesionari WHERE cemetery_uid = app_default_cemetery_uid() ORDER BY nume, prenume").fetchall()]


def get_concesionar(concesionar_id: int) -> Optional[Dict[str, Any]]:
    with _db() as conn:
        row = conn.execute("SELECT * FROM concesionari WHERE id = ? AND cemetery_uid = app_default_cemetery_uid()", (concesionar_id,)).fetchone()
        return _row_to_dict(row) if row else None


def delete_concesionar(concesionar_id: int) -> bool:
    try:
        with _db() as conn:
            return conn.execute("DELETE FROM concesionari WHERE id = ? AND cemetery_uid = app_default_cemetery_uid()", (concesionar_id,)).rowcount > 0
    except Exception as e:
        print(f"[PG ERROR delete_concesionar] {e}")
        return False


# =============================================================================
# CONTRACTE
# =============================================================================
_CONTRACT_FIELDS = (
    "numar_contract", "data_contract", "data_inceput", "data_sfarsit",
    "concesionar_id", "id_loc", "cod_loc", "valabilitate", "observatii",
    "extra_data", "owner_fullname", "owner_address", "owner_id_card_serie_number",
    "owner_cnp", "owner_phone", "owner_email", "cemetery_parcel", "cemetery_row",
    "cemetery_tomb", "tomb_surface_m2", "owner_fullname_2", "sector",
)


def save_contract(data: Dict[str, Any], contract_id: Optional[int] = None) -> Optional[int]:
    """Salvează/actualizează un contract; completează automat din locul `cod_loc` (parcelă, rând, suprafață, sector)."""
    try:
        with _db() as conn:
            cod_loc_val = data.get("cod_loc") or ""
            id_loc = None
            if cod_loc_val:
                loc = conn.execute("SELECT id, parcela, rand, numar, suprafata, sector FROM locuri_de_veci "
                                   "WHERE cod_loc = ? AND cemetery_uid = app_default_cemetery_uid()", (cod_loc_val,)).fetchone()
                if loc:
                    id_loc = loc["id"]
                    if not data.get("cemetery_parcel"):
                        data["cemetery_parcel"] = loc["parcela"] or ""
                    if not data.get("cemetery_row"):
                        data["cemetery_row"] = loc["rand"] or ""
                    if not data.get("cemetery_tomb"):
                        data["cemetery_tomb"] = loc["numar"] or ""
                    if not data.get("tomb_surface_m2"):
                        data["tomb_surface_m2"] = loc["suprafata"] if loc["suprafata"] is not None else ""
                    if not data.get("sector"):
                        data["sector"] = loc["sector"] or ""

            concesionar_id = _int(data.get("concesionar_id"))
            if concesionar_id and not conn.execute("SELECT 1 FROM concesionari WHERE id = ? AND cemetery_uid = app_default_cemetery_uid()",
                                                   (concesionar_id,)).fetchone():
                print(f"[PG WARN save_contract] concesionar_id invalid: {concesionar_id}")
                return None

            def norm(x):
                return "" if x is None else x

            extra = data.get("extra_data")
            values = [
                norm(data.get("numar_contract")), norm(data.get("data_contract")),
                norm(data.get("data_inceput")), norm(data.get("data_sfarsit")),
                concesionar_id, id_loc, norm(cod_loc_val),
                norm(data.get("valabilitate")), norm(data.get("observatii")),
                _jdump(extra) if extra is not None and extra != "" else None,
                norm(data.get("owner_fullname")), norm(data.get("owner_address")),
                norm(data.get("owner_id_card_serie_number")), norm(data.get("owner_cnp")),
                norm(data.get("owner_phone")), norm(data.get("owner_email")),
                norm(data.get("cemetery_parcel")), norm(data.get("cemetery_row")),
                norm(data.get("cemetery_tomb")), _num(data.get("tomb_surface_m2")),
                norm(data.get("owner_fullname_2")), norm(data.get("sector")),
            ]
            if contract_id:
                conn.execute(f"UPDATE contracte SET {', '.join(f'{f}=?' for f in _CONTRACT_FIELDS)} WHERE id=? AND cemetery_uid = app_default_cemetery_uid()",
                             (*values, contract_id))
                return contract_id
            return conn.execute(
                f"INSERT INTO contracte ({', '.join(_CONTRACT_FIELDS)}) "
                f"VALUES ({', '.join('?' * len(_CONTRACT_FIELDS))})", tuple(values)).lastrowid
    except Exception as e:
        print(f"[PG ERROR save_contract] {e}")
        return None


def load_contracte(extra_where: str = "") -> List[Dict[str, Any]]:
    with _db() as conn:
        where = (extra_where.strip() + " AND cemetery_uid = app_default_cemetery_uid()") if extra_where.strip() else "WHERE cemetery_uid = app_default_cemetery_uid()"
        rows = conn.execute(f"SELECT id, {', '.join(_CONTRACT_FIELDS)} FROM contracte {where}").fetchall()
        out = []
        for r in rows:
            d = _row_to_dict(r)
            d["extra"] = _jload(d.get("extra_data"), {})
            out.append(d)
        return out


def get_contract(contract_id: int) -> Optional[Dict[str, Any]]:
    with _db() as conn:
        row = conn.execute("SELECT * FROM contracte WHERE id = ? AND cemetery_uid = app_default_cemetery_uid()", (contract_id,)).fetchone()
        if not row:
            return None
        d = _row_to_dict(row)
        if d.get("extra_data"):
            d["extra_data"] = _jload(d["extra_data"], {})
        return d


def delete_contract(contract_id: int) -> bool:
    try:
        with _db() as conn:
            return conn.execute("DELETE FROM contracte WHERE id = ? AND cemetery_uid = app_default_cemetery_uid()", (contract_id,)).rowcount > 0
    except Exception as e:
        print(f"[PG ERROR delete_contract] {e}")
        return False


# =============================================================================
# ÎNHUMAȚI
# =============================================================================
_INHUMAT_FIELDS = [
    "nume", "prenume", "cnp", "data_nasterii", "data_deces", "data_inhumare", "data_deshumare", "sex",
    "cauza_deces", "profesie", "religie", "ultima_adresa", "observatii", "nr_certificat_deces",
    "nr_adeverinta_inhumare",
]
_INHUMAT_SELECT = ("i.id, i.nume, i.prenume, i.cnp, i.data_nasterii, i.data_deces, i.data_inhumare, i.data_deshumare, "
                   "i.sex, i.cauza_deces, i.profesie, i.religie, i.ultima_adresa, i.observatii, "
                   "i.nr_certificat_deces, i.nr_adeverinta_inhumare, i.id_loc, i.contract_id, l.cod_loc")


def save_inhumat(cod_loc: str, data: Dict[str, Any], inhumat_id: Optional[int] = None) -> Optional[int]:
    id_loc = get_loc_id_by_cod(cod_loc)
    if id_loc is None:
        print(f"[PG WARN save_inhumat] Locul '{cod_loc}' nu există în DB.")
        return None
    values = [data.get(k, "") or "" for k in _INHUMAT_FIELDS]
    try:
        with _db() as conn:
            if inhumat_id:
                conn.execute(f"UPDATE inhumati SET {', '.join(f'{k}=?' for k in _INHUMAT_FIELDS + ['id_loc'])} "
                             f"WHERE id=? AND cemetery_uid = app_default_cemetery_uid()", (*values, id_loc, inhumat_id))
                return inhumat_id
            cols = _INHUMAT_FIELDS + ["id_loc"]
            return conn.execute(f"INSERT INTO inhumati ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                                (*values, id_loc)).lastrowid
    except Exception as e:
        print(f"[PG ERROR save_inhumat] {e}")
        return None


def load_inhumati_for_loc(cod_loc: str) -> List[Dict[str, Any]]:
    with _db() as conn:
        return [_row_to_dict(r) for r in conn.execute(
            f"SELECT {_INHUMAT_SELECT} FROM inhumati i LEFT JOIN locuri_de_veci l ON i.id_loc = l.id "
            f"WHERE l.cod_loc = ? AND i.cemetery_uid = app_default_cemetery_uid() AND l.cemetery_uid = app_default_cemetery_uid() ORDER BY i.data_inhumare DESC NULLS LAST, i.id DESC", (cod_loc,)).fetchall()]


def load_all_inhumati(filters: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    filters = filters or {}
    clauses, params = [], []
    for key, col in (("nume", "i.nume"), ("prenume", "i.prenume"), ("cnp", "i.cnp"), ("cod_loc", "l.cod_loc")):
        if filters.get(key):
            clauses.append(f"{col} ILIKE ?")
            params.append(f"%{filters[key]}%")
    where_sql = "WHERE " + " AND ".join(["i.cemetery_uid = app_default_cemetery_uid()"] + clauses)
    with _db() as conn:
        return [_row_to_dict(r) for r in conn.execute(
            f"SELECT {_INHUMAT_SELECT} FROM inhumati i LEFT JOIN locuri_de_veci l ON i.id_loc = l.id {where_sql} "
            f"ORDER BY l.cod_loc ASC, i.data_inhumare DESC NULLS LAST, i.id DESC", tuple(params)).fetchall()]


def get_inhumat(inhumat_id: int) -> Optional[Dict[str, Any]]:
    with _db() as conn:
        row = conn.execute("SELECT i.*, l.cod_loc FROM inhumati i LEFT JOIN locuri_de_veci l ON i.id_loc = l.id "
                           "WHERE i.id = ? AND i.cemetery_uid = app_default_cemetery_uid()", (inhumat_id,)).fetchone()
        return _row_to_dict(row) if row else None


def delete_inhumat(inhumat_id: int) -> bool:
    try:
        with _db() as conn:
            return conn.execute("DELETE FROM inhumati WHERE id = ? AND cemetery_uid = app_default_cemetery_uid()", (inhumat_id,)).rowcount > 0
    except Exception as e:
        print(f"[PG ERROR delete_inhumat] {e}")
        return False


# =============================================================================
# SECTOARE
# =============================================================================
def save_sector_to_db(sector_id: str, points: List[tuple], color: str) -> bool:
    try:
        with _db() as conn:
            conn.execute("INSERT INTO sectoare (id, points, color) VALUES (?, ?, ?) "
                         "ON CONFLICT (cemetery_uid, id) DO UPDATE SET points = EXCLUDED.points, color = EXCLUDED.color",
                         (sector_id, _jdump(points), color))
        return True
    except Exception as e:
        print(f"[PG ERROR save_sector_to_db] {e}")
        return False


def delete_sector_from_db(sector_id: str) -> bool:
    try:
        with _db() as conn:
            return conn.execute("DELETE FROM sectoare WHERE id = ? AND cemetery_uid = app_default_cemetery_uid()", (sector_id,)).rowcount > 0
    except Exception as e:
        print(f"[PG ERROR delete_sector_from_db] {e}")
        return False


def load_all_sectors() -> List[Dict[str, Any]]:
    with _db() as conn:
        return [{"id": r["id"], "points": _jload(r["points"], []), "color": r["color"]}
                for r in conn.execute("SELECT id, points, color FROM sectoare WHERE cemetery_uid = app_default_cemetery_uid() ORDER BY id").fetchall()]


# =============================================================================
# PHOTOS
# =============================================================================
def list_photos_for_loc(cod_loc: str) -> List[Dict[str, Any]]:
    with _db() as conn:
        rows = conn.execute("SELECT id, file_path, caption, uploader, date_added, is_cover, media_id FROM photos "
                            "WHERE cod_loc = ? AND cemetery_uid = app_default_cemetery_uid() ORDER BY is_cover DESC, id", (cod_loc,)).fetchall()
        return [{"id": r["id"], "full_path": _local_media(r["media_id"], r["file_path"]) if r["media_id"] else r["file_path"],
                 "caption": r["caption"] or "",
                 "uploader": r["uploader"] or "", "date_added": r["date_added"] or "",
                 "is_cover": bool(r["is_cover"])} for r in rows]


def save_photo(cod_loc: str, file_path: str, metadata: Optional[Dict[str, Any]] = None) -> Optional[int]:
    metadata = metadata or {}
    try:
        with _db() as conn:
            photo_id = conn.execute(
                "INSERT INTO photos (cod_loc, file_path, caption, uploader, date_added, is_cover) "
                "VALUES (?, ?, ?, ?, ?, 0)",
                (cod_loc, file_path, metadata.get("caption", ""), metadata.get("uploader", ""),
                 metadata.get("date_added", _now_ui()))).lastrowid
    except Exception as e:
        print(f"[PG ERROR save_photo] {e}")
        return None
    if file_path and os.path.exists(file_path):
        _register_media("photos", "photos", photo_id, "loc", cod_loc, "poza_loc", file_path)
    return photo_id


def delete_photo(photo_id: int) -> bool:
    try:
        with _db() as conn:
            row = conn.execute("SELECT media_id FROM photos WHERE id = ? AND cemetery_uid = app_default_cemetery_uid()", (photo_id,)).fetchone()
            deleted = conn.execute("DELETE FROM photos WHERE id = ? AND cemetery_uid = app_default_cemetery_uid()", (photo_id,)).rowcount > 0
        if deleted and row and row["media_id"]:
            import media_service
            media_service.delete_object(row["media_id"])
        return deleted
    except Exception as e:
        print(f"[PG ERROR delete_photo] {e}")
        return False


def set_cover_photo(cod_loc: str, photo_id: int) -> bool:
    try:
        with _db() as conn:
            conn.execute("UPDATE photos SET is_cover = 0 WHERE cod_loc = ? AND cemetery_uid = app_default_cemetery_uid()", (cod_loc,))
            return conn.execute("UPDATE photos SET is_cover = 1 WHERE id = ? AND cemetery_uid = app_default_cemetery_uid()", (photo_id,)).rowcount > 0
    except Exception as e:
        print(f"[PG ERROR set_cover_photo] {e}")
        return False


# =============================================================================
# AMENAJĂRI / AVIZE
# =============================================================================
def list_avize_for_loc(cod_loc: str) -> List[Dict[str, Any]]:
    with _db() as conn:
        rows = conn.execute("SELECT id, nr_aviz, data_aviz, lucrare, created_at FROM amenajari_avize "
                            "WHERE cod_loc = ? AND cemetery_uid = app_default_cemetery_uid() ORDER BY id", (cod_loc,)).fetchall()
        return [{"id": r["id"], "nr": r["nr_aviz"], "data": r["data_aviz"], "lucrare": r["lucrare"] or "",
                 "created_at": r["created_at"]} for r in rows]


def add_aviz(cod_loc: str, nr: str, data_aviz: Optional[str], lucrare: str = "") -> Optional[int]:
    try:
        with _db() as conn:
            return conn.execute("INSERT INTO amenajari_avize (cod_loc, nr_aviz, data_aviz, lucrare) VALUES (?, ?, ?, ?)",
                                (cod_loc, nr, data_aviz, lucrare or "")).lastrowid
    except Exception as e:
        print(f"[PG ERROR add_aviz] {e}")
        return None


def delete_aviz(aviz_id: int) -> bool:
    try:
        with _db() as conn:
            return conn.execute("DELETE FROM amenajari_avize WHERE id = ? AND cemetery_uid = app_default_cemetery_uid()", (aviz_id,)).rowcount > 0
    except Exception as e:
        print(f"[PG ERROR delete_aviz] {e}")
        return False


# =============================================================================
# LOC EXTRA (JSON per cod_loc)
# =============================================================================
def load_extra_data(cod_loc: str) -> Dict[str, Any]:
    with _db() as conn:
        row = conn.execute("SELECT extra_json FROM loc_extra WHERE cod_loc = ? AND cemetery_uid = app_default_cemetery_uid()", (cod_loc,)).fetchone()
        return _jload(row["extra_json"], {}) if row else {}


def save_extra_data(cod_loc: str, extra: Dict[str, Any]) -> bool:
    try:
        with _db() as conn:
            conn.execute("INSERT INTO loc_extra (cod_loc, extra_json) VALUES (?, ?) "
                         "ON CONFLICT (cemetery_uid, cod_loc) DO UPDATE SET extra_json = EXCLUDED.extra_json",
                         (cod_loc, _jdump(extra)))
        return True
    except Exception as e:
        print(f"[PG ERROR save_extra_data] {e}")
        return False


# =============================================================================
# GUIDES / ROWS (linii și rânduri de ghidare)
# =============================================================================
def add_guide(kind: str, points: List[tuple], color: str = "#00AAFF") -> Optional[int]:
    try:
        with _db() as conn:
            return conn.execute("INSERT INTO guides (kind, points, color) VALUES (?, ?, ?)",
                                (kind, _jdump(points), color)).lastrowid
    except Exception as e:
        print(f"[PG ERROR add_guide] {e}")
        return None


def list_guides() -> List[Dict[str, Any]]:
    with _db() as conn:
        return [{"id": r["id"], "kind": r["kind"], "points": _jload(r["points"], []), "color": r["color"]}
                for r in conn.execute("SELECT id, kind, points, color FROM guides WHERE cemetery_uid = app_default_cemetery_uid() ORDER BY id").fetchall()]


def delete_guide(guide_id: int) -> bool:
    with _db() as conn:
        return conn.execute("DELETE FROM guides WHERE id = ? AND cemetery_uid = app_default_cemetery_uid()", (guide_id,)).rowcount > 0


def clear_guides() -> None:
    with _db() as conn:
        conn.execute("DELETE FROM guides WHERE cemetery_uid = app_default_cemetery_uid()")


def add_row(sector_label: str, name: str, order_index: int, baseline: List[tuple], width: float,
            depth: float) -> Optional[int]:
    try:
        with _db() as conn:
            return conn.execute(
                "INSERT INTO rows_ghid (sector_label, name, order_index, baseline, width, depth) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sector_label, name, _int(order_index), _jdump(baseline), _num(width), _num(depth))).lastrowid
    except Exception as e:
        print(f"[PG ERROR add_row] {e}")
        return None


def list_rows(sector_label: Optional[str] = None) -> List[Dict[str, Any]]:
    with _db() as conn:
        if sector_label:
            rows = conn.execute("SELECT * FROM rows_ghid WHERE sector_label = ? AND cemetery_uid = app_default_cemetery_uid() ORDER BY order_index, id",
                                (sector_label,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM rows_ghid WHERE cemetery_uid = app_default_cemetery_uid() ORDER BY order_index, id").fetchall()
        return [{"id": r["id"], "sector_label": r["sector_label"], "name": r["name"],
                 "order_index": r["order_index"], "baseline": _jload(r["baseline"], []),
                 "width": r["width"], "depth": r["depth"]} for r in rows]


def update_row(row_id: int, **fields) -> bool:
    allowed = ("sector_label", "name", "order_index", "baseline", "width", "depth")
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "baseline":
            v = _jdump(v)
        elif k in ("width", "depth"):
            v = _num(v)
        elif k == "order_index":
            v = _int(v)
        sets.append(f"{k} = ?")
        vals.append(v)
    if not sets:
        return False
    with _db() as conn:
        return conn.execute(f"UPDATE rows_ghid SET {', '.join(sets)} WHERE id = ? AND cemetery_uid = app_default_cemetery_uid()", (*vals, row_id)).rowcount > 0


def delete_row(row_id: int) -> bool:
    with _db() as conn:
        return conn.execute("DELETE FROM rows_ghid WHERE id = ? AND cemetery_uid = app_default_cemetery_uid()", (row_id,)).rowcount > 0


def shift_rows_order(sector_label: str, from_index: int, delta: int = 1) -> None:
    with _db() as conn:
        conn.execute("UPDATE rows_ghid SET order_index = order_index + ? WHERE sector_label = ? AND order_index >= ? AND cemetery_uid = app_default_cemetery_uid()",
                     (delta, sector_label, from_index))


# =============================================================================
# PLĂȚI
# =============================================================================
def add_plata(cod_loc, suma, data_plata, tip="", chitanta_nr="", observatii="", contract_id=None):
    try:
        with _db() as conn:
            return conn.execute(
                "INSERT INTO plati (cod_loc, contract_id, suma, data_plata, tip, chitanta_nr, observatii) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (cod_loc, _int(contract_id), float(suma or 0), data_plata, tip, chitanta_nr, observatii)).lastrowid
    except Exception as e:
        print(f"[PG ERROR add_plata] {e}")
        return None


def list_plati(cod_loc: Optional[str] = None) -> List[Dict[str, Any]]:
    with _db() as conn:
        if cod_loc:
            rows = conn.execute("SELECT * FROM plati WHERE cod_loc = ? AND cemetery_uid = app_default_cemetery_uid() ORDER BY data_plata DESC, id DESC",
                                (cod_loc,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM plati WHERE cemetery_uid = app_default_cemetery_uid() ORDER BY data_plata DESC, id DESC").fetchall()
        return [_plain(r) for r in rows]


def delete_plata(plata_id: int) -> bool:
    with _db() as conn:
        return conn.execute("DELETE FROM plati WHERE id = ? AND cemetery_uid = app_default_cemetery_uid()", (plata_id,)).rowcount > 0


def total_plati(cod_loc: Optional[str] = None) -> float:
    with _db() as conn:
        if cod_loc:
            row = conn.execute("SELECT COALESCE(SUM(suma),0) AS t FROM plati WHERE cod_loc = ? AND cemetery_uid = app_default_cemetery_uid()", (cod_loc,)).fetchone()
        else:
            row = conn.execute("SELECT COALESCE(SUM(suma),0) AS t FROM plati WHERE cemetery_uid = app_default_cemetery_uid()").fetchone()
        return float(row["t"] or 0)


def expiring_contracts(days: int = 30) -> List[Dict[str, Any]]:
    """Contracte cu `data_sfarsit` (ISO yyyy-mm-dd) în următoarele `days` zile sau deja expirate."""
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT * FROM contracte WHERE cemetery_uid = app_default_cemetery_uid() AND data_sfarsit ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' "
                "AND substr(data_sfarsit, 1, 10)::date <= (current_date + ?::int) ORDER BY data_sfarsit ASC",
                (int(days),)).fetchall()
            out = []
            for r in rows:
                d = _plain(r)
                d["_date_col"] = "data_sfarsit"
                d["_expira"] = d.get("data_sfarsit")
                out.append(d)
            return out
    except Exception as e:
        print(f"[PG ERROR expiring_contracts] {e}")
        return []


# =============================================================================
# DOCUMENTE
# =============================================================================
def add_document(entity_type: str, entity_id: Any, doc_type: str, src_path: str, caption: str = "",
                 nr_evidenta: str = "", copy: bool = True) -> Optional[int]:
    path = import_media_file(src_path, entity_type) if copy else src_path
    if not path:
        return None
    try:
        with _db() as conn:
            doc_id = conn.execute(
                "INSERT INTO documents (entity_type, entity_id, doc_type, file_path, caption, nr_evidenta, date_added) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (entity_type, str(entity_id), doc_type or "", path, caption or "", nr_evidenta or "",
                 _now_ui())).lastrowid
    except Exception as e:
        print(f"[PG ERROR add_document] {e}")
        return None
    _register_media("documents", "documents", doc_id, entity_type, str(entity_id), doc_type or "document", path, caption)
    return doc_id


def _register_media(table: str, kind_table: str, row_id: int, entity_type: str, entity_ref: str, doc_type: str,
                    path: str, caption: str = "") -> None:
    """Etapa 7: fișierul nou merge și în storage (octeții NU intră în PostgreSQL); fișierul local rămâne. Eșecul storage-ului nu
    pierde documentul: rămâne cu `media_id` gol și poate fi încărcat ulterior cu `media_service.register_file`."""
    try:
        import media_service
        import cimitir_storage as storage_mod
        uid = media_service.entity_uid_for(entity_type, entity_ref)
        obj = media_service.register_file(kind_table, entity_type, doc_type, uid, path,
                                          filename=storage_mod.display_name(path), uploaded_by="server")
        with _db() as conn:
            conn.execute(f"UPDATE {table} SET media_id = ? WHERE id = ? AND cemetery_uid = app_default_cemetery_uid()", (obj["id"], row_id))
    except Exception as e:
        print(f"[PG media] {table} {row_id} neîncărcat în storage: {e}")


def set_media_resolver(fn) -> None:
    """Paritate cu SQLite: serverul își aduce singur fișierele din storage (`media_service.materialize`), deci nu folosește resolver."""
    return None


def _local_media(media_id: Optional[str], path: Optional[str]) -> str:
    if path and os.path.exists(path):
        return path
    if media_id:
        try:
            import media_service
            return media_service.materialize(media_id) or (path or "")
        except Exception as e:
            print(f"[PG media] {e}")
    return path or ""


def list_documents(entity_type: str, entity_id: Any, doc_type: Optional[str] = None) -> List[Dict[str, Any]]:
    with _db() as conn:
        if doc_type:
            rows = conn.execute("SELECT * FROM documents WHERE entity_type=? AND entity_id=? AND doc_type=? AND cemetery_uid = app_default_cemetery_uid() ORDER BY id",
                                (entity_type, str(entity_id), doc_type)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM documents WHERE entity_type=? AND entity_id=? AND cemetery_uid = app_default_cemetery_uid() ORDER BY id",
                                (entity_type, str(entity_id))).fetchall()
        out = [_plain(r) for r in rows]
    for d in out:
        if d.get("media_id"):
            d["file_path"] = _local_media(d["media_id"], d.get("file_path"))
    return out


def delete_document(doc_id: int) -> bool:
    try:
        with _db() as conn:
            row = conn.execute("SELECT file_path, media_id FROM documents WHERE id=? AND cemetery_uid = app_default_cemetery_uid()", (doc_id,)).fetchone()
            deleted = conn.execute("DELETE FROM documents WHERE id=? AND cemetery_uid = app_default_cemetery_uid()", (doc_id,)).rowcount > 0
        if deleted and row and row["media_id"]:
            import media_service
            media_service.delete_object(row["media_id"])
        if row and row["file_path"] and _is_managed_media(row["file_path"]) and os.path.exists(row["file_path"]):
            try:
                os.remove(row["file_path"])
            except Exception:
                pass
        return deleted
    except Exception as e:
        print(f"[PG ERROR delete_document] {e}")
        return False


def get_document_path(doc_id: int) -> Optional[str]:
    with _db() as conn:
        row = conn.execute("SELECT file_path, media_id FROM documents WHERE id=? AND cemetery_uid = app_default_cemetery_uid()", (doc_id,)).fetchone()
    if not row:
        return None
    return _local_media(row["media_id"], row["file_path"]) if row["media_id"] else row["file_path"]


# =============================================================================
# SETTINGS
# =============================================================================
def get_setting(key: str) -> Optional[str]:
    with _db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    with _db() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (key, value))


# =============================================================================
# CERERI DE CONCESIONARE
# =============================================================================
_CERERE_FIELDS = ["cod_loc", "nume", "prenume", "cnp", "bi_ci", "adresa", "telefon", "email", "grad_rudenie",
                  "observatii"]


def _new_tracking_code(conn) -> str:
    import secrets
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "CR-" + "".join(secrets.choice(alphabet) for _ in range(8))
        if not conn.execute("SELECT 1 FROM cereri_concesionare WHERE cod_urmarire=?", (code,)).fetchone():
            return code


def add_cerere_concesionare(data: Dict[str, Any], cod_urmarire: Optional[str] = None) -> Optional[int]:
    values = [(data.get(k) or "").strip() if isinstance(data.get(k), str) else (data.get(k) or "")
              for k in _CERERE_FIELDS]
    try:
        with _db() as conn:
            return conn.execute(
                f"INSERT INTO cereri_concesionare (created_at, status, cod_urmarire, {', '.join(_CERERE_FIELDS)}) "
                f"VALUES (?, 'nou', ?, {', '.join('?' * len(_CERERE_FIELDS))})",
                (_now_ui(), cod_urmarire or _new_tracking_code(conn), *values)).lastrowid
    except Exception as e:
        print(f"[PG ERROR add_cerere_concesionare] {e}")
        return None


def get_cerere_tracking(cod_urmarire: str) -> Optional[Dict[str, Any]]:
    with _db() as conn:
        row = conn.execute("SELECT cod_urmarire, status, created_at, reviewed_at, cod_loc FROM cereri_concesionare "
                           "WHERE cod_urmarire = ? AND cemetery_uid = app_default_cemetery_uid()", ((cod_urmarire or "").strip().upper(),)).fetchone()
        return _row_to_dict(row) if row else None


def list_cereri_concesionare(status: Optional[str] = None) -> List[Dict[str, Any]]:
    with _db() as conn:
        if status:
            rows = conn.execute("SELECT * FROM cereri_concesionare WHERE status = ? AND cemetery_uid = app_default_cemetery_uid() ORDER BY id DESC",
                                (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM cereri_concesionare WHERE cemetery_uid = app_default_cemetery_uid() ORDER BY id DESC").fetchall()
        return [_row_to_dict(r) for r in rows]


def get_cerere_concesionare(cerere_id: int) -> Optional[Dict[str, Any]]:
    with _db() as conn:
        row = conn.execute("SELECT * FROM cereri_concesionare WHERE id = ? AND cemetery_uid = app_default_cemetery_uid()", (cerere_id,)).fetchone()
        return _row_to_dict(row) if row else None


def set_cerere_status(cerere_id: int, status: str, review_note: str = "",
                      concesionar_id: Optional[int] = None) -> bool:
    try:
        with _db() as conn:
            return conn.execute(
                "UPDATE cereri_concesionare SET status=?, review_note=?, concesionar_id=?, reviewed_at=? WHERE id=? AND cemetery_uid = app_default_cemetery_uid()",
                (status, review_note or "", _int(concesionar_id), _now_ui(), cerere_id)).rowcount > 0
    except Exception as e:
        print(f"[PG ERROR set_cerere_status] {e}")
        return False


def count_cereri_noi() -> int:
    with _db() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM cereri_concesionare WHERE status = 'nou' AND cemetery_uid = app_default_cemetery_uid()").fetchone()
        return int(row["n"]) if row else 0


# =============================================================================
# HELPERI PENTRU FACADE-UL `store` (hartă publică)
# =============================================================================
def map_rows() -> List[Dict[str, Any]]:
    with _db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT cod_loc, points, color, sector, parcela, rand, numar, stare, disponibilitate, ocupat, tip, "
            "capacitate, suprafata FROM locuri_de_veci WHERE cemetery_uid = app_default_cemetery_uid()").fetchall()]


def sector_rows() -> List[Dict[str, Any]]:
    with _db() as conn:
        return [dict(r) for r in conn.execute("SELECT id, points, color FROM sectoare WHERE cemetery_uid = app_default_cemetery_uid()").fetchall()]


# =============================================================================
# UTILIZATORI (personal)
# =============================================================================
def user_get(username: str) -> Optional[Dict[str, Any]]:
    with _db() as conn:
        row = conn.execute("SELECT id, username, password_hash, role FROM users WHERE username=?",
                           (username.strip(),)).fetchone()
        return dict(row) if row else None


def user_touch_login(user_id: int) -> None:
    with _db() as conn:
        conn.execute("UPDATE users SET last_login=? WHERE id=?", (datetime.now().isoformat(timespec="seconds"), user_id))


def user_set_hash(user_id: int, password_hash: str) -> bool:
    with _db() as conn:
        return conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id)).rowcount > 0


def users_list() -> List[Dict[str, Any]]:
    with _db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, username, role, created_at, last_login FROM users ORDER BY username").fetchall()]


def users_count() -> int:
    with _db() as conn:
        return int(conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"])


def user_insert(username: str, password_hash: str, role: str, created_at: str) -> Optional[int]:
    try:
        with _db() as conn:
            return conn.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
                                (username.strip(), password_hash, role, created_at)).lastrowid
    except Exception as e:
        print(f"[PG user_insert] {e}")
        return None


def user_delete(user_id: int) -> bool:
    with _db() as conn:
        return conn.execute("DELETE FROM users WHERE id=?", (user_id,)).rowcount > 0
