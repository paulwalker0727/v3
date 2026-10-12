# database.py
"""
Modul centralizat pentru accesul la baza de date SQLite a aplicației.

Principii:
    - O singură cale absolută (`DB_PATH`) folosită de întreaga aplicație,
      calculată pornind de la locația acestui fișier (NU de la CWD).
    - Conexiuni sqlite3 cu row_factory=Row, foreign_keys ON și WAL mode
      pentru citiri/scrieri concurente sigure.
    - Toate funcțiile CRUD principale pentru: locuri_de_veci, concesionari,
      contracte, inhumati, sectoare, settings, photos, amenajari_avize.
    - Migrare sigură (ADD COLUMN IF MISSING) - nu distruge datele existente.
"""

import sqlite3
import os
import json
import shutil
from typing import Optional, List, Dict, Any
from datetime import datetime

# -----------------------------------------------------------------------------
# CALE DB — o singură sursă: cimitir_config (CIMITIR_DATA_DIR / .env)
# -----------------------------------------------------------------------------
import sys as _sys
_SELF_DIR = os.path.dirname(os.path.abspath(__file__))
if _SELF_DIR not in _sys.path:
    _sys.path.insert(0, _SELF_DIR)
import cimitir_config as _config


def _resolve_data_dir() -> str:
    return _config.data_dir()


_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = _resolve_data_dir()
DB_PATH = os.path.join(DATA_DIR, "cimitir.db")

# Calea DB activă pe request (multi-cimitir). Se setează dintr-un ContextVar,
# astfel încât fiecare cerere web să poată lucra cu altă bază SQLite.
import contextvars
_active_db_path: "contextvars.ContextVar[str]" = contextvars.ContextVar("_active_db_path", default="")


def _registry_binding():
    """Registrul local activ (cimitir_registry), sau None în modul clasic (un singur cimitir.db)."""
    try:
        import cimitir_registry
    except ImportError:
        return None
    return cimitir_registry.current_binding()


def set_active_db_path(path: str) -> None:
    b = _registry_binding()
    if b is not None and path and os.path.realpath(path) != os.path.realpath(str(b.database)):
        import cimitir_registry
        raise cimitir_registry.RegistryBindingError("Scriere între registre refuzată: calea bazei nu aparține registrului activ.")
    _active_db_path.set(path or "")


def current_db_path() -> str:
    b = _registry_binding()
    if b is not None:
        return str(b.database)
    return _active_db_path.get() or DB_PATH


def media_root() -> str:
    """Folderul media al registrului activ (sau <DATA_DIR>/media în modul clasic)."""
    b = _registry_binding()
    return str(b.media) if b is not None else os.path.join(DATA_DIR, "media")


def cache_dir(name: str = "") -> str:
    """Folderul de cache al registrului activ (sau <DATA_DIR>/<name> în modul clasic)."""
    b = _registry_binding()
    base = str(b.cache) if b is not None else DATA_DIR
    return os.path.join(base, name) if name else base


# -----------------------------------------------------------------------------
# CONEXIUNE
# -----------------------------------------------------------------------------
def tenant_scope(alias: str = "", level: str = "cemetery") -> str:
    """SQLite = un singur cimitir per fișier: nu există coloane de tenant, deci nu se filtrează."""
    return "1=1"


def get_connection() -> sqlite3.Connection:
    """
    Returnează o conexiune sqlite3 configurată:
        - row_factory = sqlite3.Row (acces cu chei)
        - foreign_keys ON
        - journal_mode = WAL (permite citiri simultane cu scrierea)
        - synchronous = NORMAL (echilibru viteză/siguranță)
    """
    conn = sqlite3.connect(current_db_path(), timeout=15.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 15000")
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_wal(conn)
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _ensure_wal(conn: sqlite3.Connection) -> None:
    """Trece în WAL doar dacă e nevoie; comutarea pe o bază nouă nu respectă busy_timeout."""
    import time
    last_error = None
    for attempt in range(40):
        try:
            if str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal":
                return
            conn.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            last_error = exc
            time.sleep(0.05 + 0.02 * attempt)
    print(f"[DB WARN] Nu am putut activa WAL ({last_error}); continui cu modul curent.")


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Converteste un sqlite3.Row în dict cu None -> '' (util pentru UI)."""
    if row is None:
        return {}
    d = dict(row)
    for k, v in list(d.items()):
        if v is None:
            d[k] = ""
    return d


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        cur = conn.execute(f"PRAGMA table_info({table})")
        cols = [r["name"] for r in cur.fetchall()]
        return column in cols
    except Exception:
        return False


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column_def: str):
    col_name = column_def.strip().split()[0]
    if not _column_exists(conn, table, col_name):
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")
        except sqlite3.OperationalError:
            pass


# -----------------------------------------------------------------------------
# INIT / MIGRATIONS
# -----------------------------------------------------------------------------
def init_db() -> None:
    """Creează sau actualizează schema fără să distrugă datele existente."""
    conn = get_connection()
    try:
        c = conn.cursor()

        # ---- locuri_de_veci ----
        c.execute("""
            CREATE TABLE IF NOT EXISTS locuri_de_veci (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cod_loc TEXT UNIQUE,
                points TEXT,
                color TEXT,
                sector TEXT,
                parcela TEXT,
                rand TEXT,
                numar TEXT,
                capacitate TEXT,
                disponibilitate TEXT,
                tip TEXT,
                stare TEXT,
                categorie TEXT,
                suprafata REAL,
                nr_vechi TEXT,
                numar_cf TEXT,
                ocupat INTEGER DEFAULT 0,
                observatii TEXT,
                extra_data TEXT
            )
        """)

        # ---- concesionari ----
        c.execute("""
            CREATE TABLE IF NOT EXISTS concesionari (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nume TEXT,
                prenume TEXT,
                cnp TEXT,
                bi_ci TEXT,
                adresa TEXT,
                telefon TEXT,
                email TEXT,
                observatii TEXT
            )
        """)

        # ---- contracte ----
        c.execute("""
            CREATE TABLE IF NOT EXISTS contracte (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                numar_contract TEXT,
                data_contract TEXT,
                data_inceput TEXT,
                data_sfarsit TEXT,
                concesionar_id INTEGER,
                id_loc INTEGER,
                cod_loc TEXT,
                valabilitate TEXT,
                observatii TEXT,
                extra_data TEXT,
                owner_fullname TEXT,
                owner_fullname_2 TEXT,
                owner_address TEXT,
                owner_id_card_serie_number TEXT,
                owner_cnp TEXT,
                owner_phone TEXT,
                owner_email TEXT,
                cemetery_parcel TEXT,
                cemetery_row TEXT,
                cemetery_tomb TEXT,
                tomb_surface_m2 REAL,
                sector TEXT,
                FOREIGN KEY(concesionar_id) REFERENCES concesionari(id) ON DELETE SET NULL ON UPDATE CASCADE,
                FOREIGN KEY(id_loc) REFERENCES locuri_de_veci(id) ON DELETE SET NULL ON UPDATE CASCADE
            )
        """)

        # ---- inhumati ----
        c.execute("""
            CREATE TABLE IF NOT EXISTS inhumati (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nume TEXT,
                prenume TEXT,
                cnp TEXT,
                data_nasterii TEXT,
                data_deces TEXT,
                data_inhumare TEXT,
                data_deshumare TEXT,
                sex TEXT,
                cauza_deces TEXT,
                profesie TEXT,
                religie TEXT,
                ultima_adresa TEXT,
                observatii TEXT,
                id_loc INTEGER,
                contract_id INTEGER,
                FOREIGN KEY(id_loc) REFERENCES locuri_de_veci(id) ON DELETE SET NULL ON UPDATE CASCADE,
                FOREIGN KEY(contract_id) REFERENCES contracte(id) ON DELETE SET NULL ON UPDATE CASCADE
            )
        """)

        # ---- sectoare ----
        c.execute("""
            CREATE TABLE IF NOT EXISTS sectoare (
                id TEXT PRIMARY KEY,
                points TEXT,
                color TEXT
            )
        """)

        # ---- settings ----
        c.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # ---- photos ----
        c.execute("""
            CREATE TABLE IF NOT EXISTS photos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cod_loc TEXT NOT NULL,
                file_path TEXT NOT NULL,
                caption TEXT,
                uploader TEXT,
                date_added TEXT,
                is_cover INTEGER DEFAULT 0,
                FOREIGN KEY(cod_loc) REFERENCES locuri_de_veci(cod_loc) ON DELETE CASCADE
            )
        """)

        # ---- amenajari_avize ----
        c.execute("""
            CREATE TABLE IF NOT EXISTS amenajari_avize (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cod_loc TEXT NOT NULL,
                nr_aviz TEXT NOT NULL,
                data_aviz TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY(cod_loc) REFERENCES locuri_de_veci(cod_loc) ON DELETE CASCADE
            )
        """)

        # ---- loc_extra (fallback JSON) ----
        c.execute("""
            CREATE TABLE IF NOT EXISTS loc_extra (
                cod_loc TEXT PRIMARY KEY,
                extra_json TEXT
            )
        """)

        # ---- guides (linii de ghidare persistente) ----
        c.execute("""
            CREATE TABLE IF NOT EXISTS guides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT,
                points TEXT,
                color TEXT
            )
        """)

        # ---- rows_ghid (rânduri definite ca linii reale) ----
        c.execute("""
            CREATE TABLE IF NOT EXISTS rows_ghid (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sector_label TEXT,
                name TEXT,
                order_index INTEGER,
                baseline TEXT,
                width REAL,
                depth REAL
            )
        """)

        # ---- plati ----
        c.execute("""
            CREATE TABLE IF NOT EXISTS plati (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cod_loc TEXT,
                contract_id INTEGER,
                suma REAL,
                data_plata TEXT,
                tip TEXT,
                chitanta_nr TEXT,
                observatii TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # ---- documents (poze/documente unificate: loc, inhumat, aviz) ----
        c.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                doc_type TEXT,
                file_path TEXT NOT NULL,
                caption TEXT,
                nr_evidenta TEXT,
                date_added TEXT
            )
        """)

        # ---- cereri de concesionare primite din portalul public ----
        c.execute("""
            CREATE TABLE IF NOT EXISTS cereri_concesionare (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT,
                status TEXT DEFAULT 'nou',
                cod_loc TEXT,
                nume TEXT,
                prenume TEXT,
                cnp TEXT,
                bi_ci TEXT,
                adresa TEXT,
                telefon TEXT,
                email TEXT,
                grad_rudenie TEXT,
                observatii TEXT,
                concesionar_id INTEGER,
                reviewed_at TEXT,
                review_note TEXT
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL,
                last_login TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS citizens (
                id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE, email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL, full_name TEXT, created_at TEXT, active INTEGER DEFAULT 1
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor_type TEXT, actor TEXT,
                action TEXT, target TEXT, detail TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS loc_holders (
                cod_loc TEXT, holder_hash TEXT, PRIMARY KEY (cod_loc, holder_hash)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS sync_ops (
                op_id TEXT PRIMARY KEY,
                kind TEXT,
                applied_at TEXT,
                result TEXT
            )
        """)

        # ---- MIGRATIONS: ADD COLUMN IF MISSING ----
        for col in [
            ("contracte", "cod_loc TEXT"),
            ("contracte", "extra_data TEXT"),
            ("contracte", "id_loc INTEGER"),
            ("contracte", "valabilitate TEXT"),
            ("contracte", "cemetery_parcel TEXT"),
            ("contracte", "cemetery_row TEXT"),
            ("contracte", "cemetery_tomb TEXT"),
            ("contracte", "tomb_surface_m2 REAL"),
            ("contracte", "owner_fullname TEXT"),
            ("contracte", "owner_fullname_2 TEXT"),
            ("contracte", "owner_address TEXT"),
            ("contracte", "owner_id_card_serie_number TEXT"),
            ("contracte", "owner_cnp TEXT"),
            ("contracte", "owner_phone TEXT"),
            ("contracte", "owner_email TEXT"),
            ("contracte", "sector TEXT"),
            ("inhumati", "contract_id INTEGER"),
            ("inhumati", "nr_certificat_deces TEXT"),
            ("inhumati", "nr_adeverinta_inhumare TEXT"),
            ("amenajari_avize", "lucrare TEXT"),
            ("amenajari_avize", "stare TEXT"),
            ("amenajari_avize", "observatii TEXT"),
            ("amenajari_avize", "data_finalizare TEXT"),
            ("locuri_de_veci", "extra_data TEXT"),
            ("cereri_concesionare", "cod_urmarire TEXT"),
            ("cereri_concesionare", "sync_version INTEGER DEFAULT 0"),
            ("cereri_concesionare", "acked INTEGER DEFAULT 0"),
            ("cereri_concesionare", "citizen_id INTEGER"),
            ("cereri_concesionare", "tip TEXT DEFAULT 'concesionare'"),
            ("cereri_concesionare", "payload TEXT"),
            ("cereri_concesionare", "revision INTEGER DEFAULT 0"),
            ("cereri_concesionare", "contract_id INTEGER"),
            ("cereri_concesionare", "privacy_version TEXT"),
            ("cereri_concesionare", "privacy_ack_at TEXT"),
            ("cereri_concesionare", "closed_at TEXT"),
            ("cereri_concesionare", "public_message TEXT"),
            ("documents", "media_id TEXT"),
            ("photos", "media_id TEXT"),
            ("documents", "sha256 TEXT"),
            ("documents", "size INTEGER"),
            ("locuri_de_veci", "uid TEXT"),
            ("locuri_de_veci", "sync_version INTEGER DEFAULT 0"),
            ("locuri_de_veci", "are_inhumari INTEGER DEFAULT 0"),
            ("users", "uid TEXT"),
            ("users", "rights TEXT"),
            ("users", "active INTEGER DEFAULT 1"),
            ("users", "cemetery_id TEXT"),
            ("users", "version INTEGER DEFAULT 1"),
            ("users", "updated_at TEXT"),
            ("users", "origin TEXT DEFAULT 'local'"),
            ("users", "lease_until TEXT"),
        ]:
            _add_column_if_missing(conn, col[0], col[1])

        # ---- indexuri utile ----
        for idx in [
            "CREATE INDEX IF NOT EXISTS idx_locuri_cod ON locuri_de_veci(cod_loc)",
            "CREATE INDEX IF NOT EXISTS idx_inhumati_loc ON inhumati(id_loc)",
            "CREATE INDEX IF NOT EXISTS idx_inhumati_contract ON inhumati(contract_id)",
            "CREATE INDEX IF NOT EXISTS idx_concesionari_nume ON concesionari(nume)",
            "CREATE INDEX IF NOT EXISTS idx_contracte_loc ON contracte(id_loc)",
            "CREATE INDEX IF NOT EXISTS idx_contracte_concesionar ON contracte(concesionar_id)",
            "CREATE INDEX IF NOT EXISTS idx_contracte_numar ON contracte(numar_contract)",
            "CREATE INDEX IF NOT EXISTS idx_photos_codloc ON photos(cod_loc)",
            "CREATE INDEX IF NOT EXISTS idx_avize_codloc ON amenajari_avize(cod_loc)",
            "CREATE INDEX IF NOT EXISTS idx_documents_entity ON documents(entity_type, entity_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_cereri_urmarire ON cereri_concesionare(cod_urmarire)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_locuri_uid ON locuri_de_veci(uid)",
        ]:
            try:
                conn.execute(idx)
            except Exception:
                pass

        import uuid as _uuid
        for table in ("locuri_de_veci", "users"):
            for (rid,) in conn.execute(f"SELECT id FROM {table} WHERE uid IS NULL OR uid=''").fetchall():
                conn.execute(f"UPDATE {table} SET uid=? WHERE id=?", (str(_uuid.uuid4()), rid))
        conn.execute("UPDATE users SET active=1 WHERE active IS NULL")
        conn.execute("UPDATE users SET version=1 WHERE version IS NULL")
        if not conn.execute("SELECT 1 FROM settings WHERE key='cemetery_id'").fetchone():
            conn.execute("INSERT INTO settings (key, value) VALUES ('cemetery_id', ?)", (str(_uuid.uuid4()),))
        for (rid,) in conn.execute(
                "SELECT id FROM cereri_concesionare WHERE cod_urmarire IS NULL OR cod_urmarire=''").fetchall():
            conn.execute("UPDATE cereri_concesionare SET cod_urmarire=? WHERE id=?",
                         (_new_tracking_code(conn), rid))
    finally:
        conn.close()


# =============================================================================
# LOCURI DE VECI
# =============================================================================
def load_loc_by_cod(cod_loc: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT id, cod_loc, sector, parcela, rand, numar, capacitate,
                   disponibilitate, tip, stare, categorie, suprafata,
                   nr_vechi, numar_cf, points, color, extra_data, are_inhumari, observatii
            FROM locuri_de_veci WHERE cod_loc = ?
        """, (cod_loc,)).fetchone()
        if not row:
            return None
        try:
            points = json.loads(row["points"]) if row["points"] else []
        except Exception:
            points = []
        try:
            extra = json.loads(row["extra_data"]) if row["extra_data"] else {}
        except Exception:
            extra = {}
        return {
            "id": row["id"], "cod_loc": row["cod_loc"], "sector": row["sector"],
            "parcela": row["parcela"], "rand": row["rand"], "numar": row["numar"],
            "capacitate": row["capacitate"], "disponibilitate": row["disponibilitate"],
            "tip": row["tip"], "stare": row["stare"], "categorie": row["categorie"],
            "suprafata": row["suprafata"], "nr_vechi": row["nr_vechi"], "numar_cf": row["numar_cf"],
            "points": points, "color": row["color"], "extra_data": extra, "are_inhumari": row["are_inhumari"],
            "observatii": row["observatii"],
        }
    finally:
        conn.close()


def get_loc_id_by_cod(cod_loc: str) -> Optional[int]:
    """Helper — returnează id-ul intern al unui loc după cod."""
    if not cod_loc:
        return None
    conn = get_connection()
    try:
        row = conn.execute("SELECT id FROM locuri_de_veci WHERE cod_loc = ?", (cod_loc,)).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def save_plot_to_db(cod_loc: str, points: List[tuple], color: str) -> Optional[int]:
    conn = get_connection()
    try:
        pts = json.dumps(points)
        row = conn.execute("SELECT id FROM locuri_de_veci WHERE cod_loc = ?", (cod_loc,)).fetchone()
        if row:
            conn.execute("UPDATE locuri_de_veci SET points=?, color=? WHERE cod_loc=?", (pts, color, cod_loc))
            return row["id"]
        cur = conn.execute(
            "INSERT INTO locuri_de_veci (cod_loc, points, color, extra_data) VALUES (?, ?, ?, ?)",
            (cod_loc, pts, color, json.dumps({}))
        )
        return cur.lastrowid
    except Exception as e:
        print(f"[DB ERROR save_plot_to_db] {e}")
        return None
    finally:
        conn.close()


def save_loc_metadata(cod_loc: str, metadata: Dict[str, Any]) -> bool:
    """Actualizează metadatele unui loc (creează rândul dacă lipsește)."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT id FROM locuri_de_veci WHERE cod_loc = ?", (cod_loc,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO locuri_de_veci (cod_loc, points, color, extra_data) VALUES (?, ?, ?, ?)",
                (cod_loc, json.dumps([]), "#0078FF", json.dumps({}))
            )
        allowed = ("sector", "parcela", "rand", "numar", "capacitate", "disponibilitate",
                   "tip", "stare", "categorie", "suprafata", "nr_vechi", "numar_cf",
                   "ocupat", "observatii")
        fields, values = [], []
        for k in allowed:
            if k in metadata:
                fields.append(f"{k}=?")
                values.append(metadata[k])
        if "extra_data" in metadata:
            fields.append("extra_data=?")
            values.append(json.dumps(metadata["extra_data"]))
        if not fields:
            return True
        values.append(cod_loc)
        conn.execute(f"UPDATE locuri_de_veci SET {', '.join(fields)} WHERE cod_loc=?", tuple(values))
        return True
    except Exception as e:
        print(f"[DB ERROR save_loc_metadata] {e}")
        return False
    finally:
        conn.close()


def delete_plot(cod_loc: str) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM locuri_de_veci WHERE cod_loc = ?", (cod_loc,))
        return cur.rowcount > 0
    except Exception as e:
        print(f"[DB ERROR delete_plot] {e}")
        return False
    finally:
        conn.close()


def list_locuri() -> List[Dict[str, Any]]:
    """Returnează toate locurile de veci (fără points/extra_data pentru a fi mai rapid)."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT id, cod_loc, sector, parcela, rand, numar, capacitate,
                   disponibilitate, tip, stare, categorie, suprafata,
                   nr_vechi, numar_cf, ocupat, observatii
            FROM locuri_de_veci
            ORDER BY cod_loc
        """).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def search_locuri(filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    Caută locuri după filtre. Chei acceptate:
        - cod_loc, nr_vechi, numar_cf, sector, parcela, rand, numar (TEXT — LIKE)
        - capacitate, disponibilitate, tip, stare, categorie (TEXT — exact match)
        - _precise (bool) — dacă True, TOATE câmpurile text folosesc match exact
    Returnează listă de dicts (aceleași câmpuri ca `list_locuri`).
    """
    filters = filters or {}
    precise = bool(filters.get("_precise", False))

    text_like_fields = ("cod_loc", "nr_vechi", "numar_cf", "sector", "parcela", "rand", "numar")
    text_exact_fields = ("capacitate", "disponibilitate", "tip", "stare", "categorie")

    clauses: List[str] = []
    params: List[Any] = []

    for f in text_like_fields:
        val = filters.get(f)
        if val:
            if precise:
                clauses.append(f"{f} = ?")
                params.append(val)
            else:
                clauses.append(f"{f} LIKE ?")
                params.append(f"%{val}%")

    for f in text_exact_fields:
        val = filters.get(f)
        if val:
            clauses.append(f"{f} = ?")
            params.append(val)

    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""
        SELECT id, cod_loc, sector, parcela, rand, numar, capacitate,
               disponibilitate, tip, stare, categorie, suprafata,
               nr_vechi, numar_cf, ocupat, observatii
        FROM locuri_de_veci
        {where_sql}
        ORDER BY cod_loc
    """
    conn = get_connection()
    try:
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


# =============================================================================
# CONCESIONARI
# =============================================================================
def save_concesionar(data: Dict[str, Any], concesionar_id: Optional[int] = None) -> Optional[int]:
    fields = ["nume", "prenume", "cnp", "bi_ci", "adresa", "telefon", "email", "observatii"]
    values = [data.get(k, "") or "" for k in fields]
    conn = get_connection()
    try:
        if concesionar_id:
            set_clause = ", ".join([f"{k}=?" for k in fields])
            conn.execute(f"UPDATE concesionari SET {set_clause} WHERE id=?", (*values, concesionar_id))
            return concesionar_id
        cur = conn.execute(
            f"INSERT INTO concesionari ({', '.join(fields)}) VALUES ({', '.join(['?']*len(fields))})",
            tuple(values)
        )
        return cur.lastrowid
    except Exception as e:
        print(f"[DB ERROR save_concesionar] {e}")
        return None
    finally:
        conn.close()


def load_concesionari(_filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, nume, prenume, cnp, bi_ci, adresa, telefon, email, observatii "
            "FROM concesionari ORDER BY nume, prenume"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_concesionar(concesionar_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM concesionari WHERE id = ?", (concesionar_id,)).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def delete_concesionar(concesionar_id: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM concesionari WHERE id = ?", (concesionar_id,))
        return cur.rowcount > 0
    except Exception as e:
        print(f"[DB ERROR delete_concesionar] {e}")
        return False
    finally:
        conn.close()


# =============================================================================
# CONTRACTE
# =============================================================================
_CONTRACT_FIELDS = (
    "numar_contract", "data_contract", "data_inceput", "data_sfarsit",
    "concesionar_id", "id_loc", "cod_loc", "valabilitate", "observatii",
    "extra_data", "owner_fullname", "owner_address", "owner_id_card_serie_number",
    "owner_cnp", "owner_phone", "owner_email", "cemetery_parcel", "cemetery_row",
    "cemetery_tomb", "tomb_surface_m2", "owner_fullname_2", "sector"
)


def save_contract(data: Dict[str, Any], contract_id: Optional[int] = None) -> Optional[int]:
    """Salvează/actualizează un contract. Autofill din locuri_de_veci după cod_loc."""
    conn = get_connection()
    try:
        cod_loc_val = data.get("cod_loc") or ""
        id_loc = None
        if cod_loc_val:
            loc_row = conn.execute(
                "SELECT id, parcela, rand, numar, suprafata, sector FROM locuri_de_veci WHERE cod_loc = ?",
                (cod_loc_val,)
            ).fetchone()
            if loc_row:
                id_loc = loc_row["id"]
                if not data.get("cemetery_parcel"):
                    data["cemetery_parcel"] = loc_row["parcela"] or ""
                if not data.get("cemetery_row"):
                    data["cemetery_row"] = loc_row["rand"] or ""
                if not data.get("cemetery_tomb"):
                    data["cemetery_tomb"] = loc_row["numar"] or ""
                if not data.get("tomb_surface_m2"):
                    data["tomb_surface_m2"] = loc_row["suprafata"] if loc_row["suprafata"] is not None else ""
                if not data.get("sector"):
                    data["sector"] = loc_row["sector"] or ""

        concesionar_id = data.get("concesionar_id")
        if concesionar_id:
            r = conn.execute("SELECT id FROM concesionari WHERE id = ?", (concesionar_id,)).fetchone()
            if not r:
                print(f"[DB WARN save_contract] concesionar_id invalid: {concesionar_id}")
                return None

        def norm(x): return "" if x is None else x
        values = [
            norm(data.get("numar_contract")), norm(data.get("data_contract")),
            norm(data.get("data_inceput")), norm(data.get("data_sfarsit")),
            concesionar_id, id_loc, norm(cod_loc_val),
            norm(data.get("valabilitate")), norm(data.get("observatii")),
            json.dumps(data.get("extra_data")) if data.get("extra_data") is not None else "",
            norm(data.get("owner_fullname")), norm(data.get("owner_address")),
            norm(data.get("owner_id_card_serie_number")), norm(data.get("owner_cnp")),
            norm(data.get("owner_phone")), norm(data.get("owner_email")),
            norm(data.get("cemetery_parcel")), norm(data.get("cemetery_row")),
            norm(data.get("cemetery_tomb")),
            data.get("tomb_surface_m2") if data.get("tomb_surface_m2") is not None else "",
            norm(data.get("owner_fullname_2")), norm(data.get("sector")),
        ]

        if contract_id:
            set_clause = ", ".join([f"{f}=?" for f in _CONTRACT_FIELDS])
            conn.execute(f"UPDATE contracte SET {set_clause} WHERE id=?", (*values, contract_id))
            return contract_id
        cur = conn.execute(
            f"INSERT INTO contracte ({', '.join(_CONTRACT_FIELDS)}) VALUES ({', '.join(['?']*len(_CONTRACT_FIELDS))})",
            tuple(values)
        )
        return cur.lastrowid
    except Exception as e:
        print(f"[DB ERROR save_contract] {e}")
        return None
    finally:
        conn.close()


def load_contracte(extra_where: str = "") -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        sql = f"SELECT id, {', '.join(_CONTRACT_FIELDS)} FROM contracte {extra_where}"
        rows = conn.execute(sql).fetchall()
        results = []
        for r in rows:
            d = _row_to_dict(r)
            if d.get("extra_data"):
                try:
                    d["extra"] = json.loads(d["extra_data"])
                except Exception:
                    d["extra"] = {}
            else:
                d["extra"] = {}
            results.append(d)
        return results
    finally:
        conn.close()


def get_contract(contract_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM contracte WHERE id = ?", (contract_id,)).fetchone()
        if not row:
            return None
        d = _row_to_dict(row)
        if d.get("extra_data"):
            try:
                d["extra_data"] = json.loads(d["extra_data"])
            except Exception:
                d["extra_data"] = {}
        return d
    finally:
        conn.close()


def delete_contract(contract_id: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM contracte WHERE id = ?", (contract_id,))
        return cur.rowcount > 0
    except Exception as e:
        print(f"[DB ERROR delete_contract] {e}")
        return False
    finally:
        conn.close()


# =============================================================================
# INHUMATI  (funcțiile care lipseau!)
# =============================================================================
_INHUMAT_FIELDS = [
    "nume", "prenume", "cnp", "data_nasterii", "data_deces",
    "data_inhumare", "data_deshumare", "sex", "cauza_deces",
    "profesie", "religie", "ultima_adresa", "observatii",
    "nr_certificat_deces", "nr_adeverinta_inhumare",
]


def save_inhumat(cod_loc: str, data: Dict[str, Any],
                 inhumat_id: Optional[int] = None) -> Optional[int]:
    """
    Salvează un înhumat.
    - Dacă `inhumat_id` este furnizat → UPDATE.
    - Altfel → INSERT (permite mai mulți înhumați pentru același loc).
    """
    id_loc = get_loc_id_by_cod(cod_loc)
    if id_loc is None:
        print(f"[DB WARN save_inhumat] Locul '{cod_loc}' nu există în DB.")
        return None

    values = [data.get(k, "") or "" for k in _INHUMAT_FIELDS]
    conn = get_connection()
    try:
        if inhumat_id:
            set_clause = ", ".join([f"{k}=?" for k in _INHUMAT_FIELDS] + ["id_loc=?"])
            conn.execute(f"UPDATE inhumati SET {set_clause} WHERE id=?",
                         (*values, id_loc, inhumat_id))
            return inhumat_id
        cols = ", ".join(_INHUMAT_FIELDS + ["id_loc"])
        placeholders = ", ".join(["?"] * (len(_INHUMAT_FIELDS) + 1))
        cur = conn.execute(f"INSERT INTO inhumati ({cols}) VALUES ({placeholders})",
                           (*values, id_loc))
        return cur.lastrowid
    except Exception as e:
        print(f"[DB ERROR save_inhumat] {e}")
        return None
    finally:
        conn.close()


def load_inhumati_for_loc(cod_loc: str) -> List[Dict[str, Any]]:
    """Returnează toți înhumații asociați unui cod_loc."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT i.id, i.nume, i.prenume, i.cnp, i.data_nasterii, i.data_deces,
                   i.data_inhumare, i.data_deshumare, i.sex, i.cauza_deces,
                   i.profesie, i.religie, i.ultima_adresa, i.observatii,
                   i.nr_certificat_deces, i.nr_adeverinta_inhumare,
                   i.id_loc, i.contract_id, l.cod_loc
            FROM inhumati i
            LEFT JOIN locuri_de_veci l ON i.id_loc = l.id
            WHERE l.cod_loc = ?
            ORDER BY i.data_inhumare DESC, i.id DESC
        """, (cod_loc,)).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def load_all_inhumati(filters: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """
    Returnează toți înhumații cu cod_loc atașat (pentru fereastra Înhumați).
    filters opțional: {"nume": "...", "prenume": "...", "cnp": "..."}.
    """
    filters = filters or {}
    where_clauses = []
    params: List[Any] = []
    if filters.get("nume"):
        where_clauses.append("i.nume LIKE ?")
        params.append(f"%{filters['nume']}%")
    if filters.get("prenume"):
        where_clauses.append("i.prenume LIKE ?")
        params.append(f"%{filters['prenume']}%")
    if filters.get("cnp"):
        where_clauses.append("i.cnp LIKE ?")
        params.append(f"%{filters['cnp']}%")
    if filters.get("cod_loc"):
        where_clauses.append("l.cod_loc LIKE ?")
        params.append(f"%{filters['cod_loc']}%")

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    conn = get_connection()
    try:
        rows = conn.execute(f"""
            SELECT i.id, i.nume, i.prenume, i.cnp, i.data_nasterii, i.data_deces,
                   i.data_inhumare, i.data_deshumare, i.sex, i.cauza_deces,
                   i.profesie, i.religie, i.ultima_adresa, i.observatii,
                   i.nr_certificat_deces, i.nr_adeverinta_inhumare,
                   i.id_loc, i.contract_id, l.cod_loc
            FROM inhumati i
            LEFT JOIN locuri_de_veci l ON i.id_loc = l.id
            {where_sql}
            ORDER BY l.cod_loc ASC, i.data_inhumare DESC, i.id DESC
        """, tuple(params)).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_inhumat(inhumat_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT i.*, l.cod_loc
            FROM inhumati i
            LEFT JOIN locuri_de_veci l ON i.id_loc = l.id
            WHERE i.id = ?
        """, (inhumat_id,)).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def delete_inhumat(inhumat_id: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM inhumati WHERE id = ?", (inhumat_id,))
        return cur.rowcount > 0
    except Exception as e:
        print(f"[DB ERROR delete_inhumat] {e}")
        return False
    finally:
        conn.close()


# =============================================================================
# SECTOARE
# =============================================================================
def save_sector_to_db(sector_id: str, points: List[tuple], color: str) -> bool:
    conn = get_connection()
    try:
        conn.execute("INSERT OR REPLACE INTO sectoare (id, points, color) VALUES (?, ?, ?)",
                     (sector_id, json.dumps(points), color))
        return True
    except Exception as e:
        print(f"[DB ERROR save_sector_to_db] {e}")
        return False
    finally:
        conn.close()


def delete_sector_from_db(sector_id: str) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM sectoare WHERE id = ?", (sector_id,))
        return cur.rowcount > 0
    except Exception as e:
        print(f"[DB ERROR delete_sector_from_db] {e}")
        return False
    finally:
        conn.close()


def load_all_sectors() -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT id, points, color FROM sectoare").fetchall()
        results = []
        for r in rows:
            try:
                pts = json.loads(r["points"]) if r["points"] else []
            except Exception:
                pts = []
            results.append({"id": r["id"], "points": pts, "color": r["color"]})
        return results
    finally:
        conn.close()


# =============================================================================
# PHOTOS
# =============================================================================
def list_photos_for_loc(cod_loc: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, file_path, caption, uploader, date_added, is_cover, media_id "
            "FROM photos WHERE cod_loc = ? ORDER BY is_cover DESC, id",
            (cod_loc,)
        ).fetchall()
        return [{
            "id": r["id"], "full_path": _local_media_path(r["media_id"], r["file_path"], r["caption"] or "") if r["media_id"] else r["file_path"],
            "caption": r["caption"] or "",
            "uploader": r["uploader"] or "", "date_added": r["date_added"] or "",
            "is_cover": bool(r["is_cover"]),
        } for r in rows]
    finally:
        conn.close()


def save_photo(cod_loc: str, file_path: str,
               metadata: Optional[Dict[str, Any]] = None) -> Optional[int]:
    metadata = metadata or {}
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO photos (cod_loc, file_path, caption, uploader, date_added, is_cover) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (cod_loc, file_path,
             metadata.get("caption", ""),
             metadata.get("uploader", ""),
             metadata.get("date_added", datetime.now().strftime("%d.%m.%Y %H:%M")))
        )
        return cur.lastrowid
    except Exception as e:
        print(f"[DB ERROR save_photo] {e}")
        return None
    finally:
        conn.close()


def delete_photo(photo_id: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
        return cur.rowcount > 0
    except Exception as e:
        print(f"[DB ERROR delete_photo] {e}")
        return False
    finally:
        conn.close()


def set_cover_photo(cod_loc: str, photo_id: int) -> bool:
    conn = get_connection()
    try:
        conn.execute("UPDATE photos SET is_cover = 0 WHERE cod_loc = ?", (cod_loc,))
        cur = conn.execute("UPDATE photos SET is_cover = 1 WHERE id = ?", (photo_id,))
        return cur.rowcount > 0
    except Exception as e:
        print(f"[DB ERROR set_cover_photo] {e}")
        return False
    finally:
        conn.close()


# =============================================================================
# AMENAJARI / AVIZE
# =============================================================================
def list_avize_for_loc(cod_loc: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM amenajari_avize WHERE cod_loc = ? ORDER BY id", (cod_loc,)).fetchall()
        return [_aviz_dict(r) for r in rows]
    finally:
        conn.close()


def _aviz_dict(r) -> Dict[str, Any]:
    k = r.keys()
    g = lambda c: (r[c] if c in k else "") or ""  # noqa: E731
    return {"id": r["id"], "cod_loc": r["cod_loc"], "nr": r["nr_aviz"], "data": r["data_aviz"], "lucrare": g("lucrare"),
            "stare": g("stare"), "observatii": g("observatii"), "data_finalizare": g("data_finalizare"),
            "created_at": r["created_at"]}


def get_aviz(aviz_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        r = conn.execute("SELECT * FROM amenajari_avize WHERE id = ?", (aviz_id,)).fetchone()
        return _aviz_dict(r) if r else None
    finally:
        conn.close()


_AVIZ_COLS = {"nr": "nr_aviz", "data": "data_aviz", "lucrare": "lucrare", "stare": "stare", "observatii": "observatii",
              "data_finalizare": "data_finalizare"}


def update_aviz(aviz_id: int, fields: Dict[str, Any]) -> bool:
    """Actualizează doar câmpurile primite (nr, data, lucrare, stare, observatii, data_finalizare)."""
    sets = [(_AVIZ_COLS[k], v if v is not None else "") for k, v in fields.items() if k in _AVIZ_COLS]
    if not sets:
        return False
    conn = get_connection()
    try:
        cur = conn.execute(f"UPDATE amenajari_avize SET {', '.join(c + '=?' for c, _ in sets)} WHERE id = ?",
                           (*[v for _, v in sets], aviz_id))
        return cur.rowcount > 0
    except Exception as e:
        print(f"[DB ERROR update_aviz] {e}")
        return False
    finally:
        conn.close()


def add_aviz(cod_loc: str, nr: str, data_aviz: Optional[str], lucrare: str = "") -> Optional[int]:
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO amenajari_avize (cod_loc, nr_aviz, data_aviz, lucrare) VALUES (?, ?, ?, ?)",
            (cod_loc, nr, data_aviz, lucrare or "")
        )
        return cur.lastrowid
    except Exception as e:
        print(f"[DB ERROR add_aviz] {e}")
        return None
    finally:
        conn.close()


def delete_aviz(aviz_id: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM amenajari_avize WHERE id = ?", (aviz_id,))
        return cur.rowcount > 0
    except Exception as e:
        print(f"[DB ERROR delete_aviz] {e}")
        return False
    finally:
        conn.close()


# =============================================================================
# LOC EXTRA (fallback JSON per cod_loc)
# =============================================================================
def load_extra_data(cod_loc: str) -> Dict[str, Any]:
    conn = get_connection()
    try:
        row = conn.execute("SELECT extra_json FROM loc_extra WHERE cod_loc = ?", (cod_loc,)).fetchone()
        if not row or not row["extra_json"]:
            return {}
        try:
            return json.loads(row["extra_json"])
        except Exception:
            return {}
    finally:
        conn.close()


def save_extra_data(cod_loc: str, extra: Dict[str, Any]) -> bool:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO loc_extra (cod_loc, extra_json) VALUES (?, ?)",
            (cod_loc, json.dumps(extra, ensure_ascii=False))
        )
        return True
    except Exception as e:
        print(f"[DB ERROR save_extra_data] {e}")
        return False
    finally:
        conn.close()


# =============================================================================
# GUIDES (linii de ghidare)
# =============================================================================
def add_guide(kind: str, points: List[tuple], color: str = "#00AAFF") -> Optional[int]:
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO guides (kind, points, color) VALUES (?, ?, ?)",
            (kind, json.dumps(points), color),
        )
        return cur.lastrowid
    except Exception as e:
        print(f"[DB ERROR add_guide] {e}")
        return None
    finally:
        conn.close()


def list_guides() -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT id, kind, points, color FROM guides").fetchall()
        out = []
        for r in rows:
            try:
                pts = json.loads(r["points"]) if r["points"] else []
            except Exception:
                pts = []
            out.append({"id": r["id"], "kind": r["kind"], "points": pts, "color": r["color"]})
        return out
    finally:
        conn.close()


def delete_guide(guide_id: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM guides WHERE id = ?", (guide_id,))
        return cur.rowcount > 0
    finally:
        conn.close()


def clear_guides() -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM guides")
    finally:
        conn.close()


# =============================================================================
# ROWS (rânduri de ghidare)
# =============================================================================
def add_row(sector_label: str, name: str, order_index: int,
            baseline: List[tuple], width: float, depth: float) -> Optional[int]:
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO rows_ghid (sector_label, name, order_index, baseline, width, depth) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sector_label, name, order_index, json.dumps(baseline), width, depth),
        )
        return cur.lastrowid
    except Exception as e:
        print(f"[DB ERROR add_row] {e}")
        return None
    finally:
        conn.close()


def list_rows(sector_label: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        if sector_label:
            rows = conn.execute(
                "SELECT * FROM rows_ghid WHERE sector_label = ? ORDER BY order_index",
                (sector_label,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM rows_ghid ORDER BY order_index").fetchall()
        out = []
        for r in rows:
            try:
                bl = json.loads(r["baseline"]) if r["baseline"] else []
            except Exception:
                bl = []
            out.append({
                "id": r["id"], "sector_label": r["sector_label"], "name": r["name"],
                "order_index": r["order_index"], "baseline": bl,
                "width": r["width"], "depth": r["depth"],
            })
        return out
    finally:
        conn.close()


def update_row(row_id: int, **fields) -> bool:
    if not fields:
        return False
    allowed = ("sector_label", "name", "order_index", "baseline", "width", "depth")
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "baseline":
            v = json.dumps(v)
        sets.append(f"{k} = ?")
        vals.append(v)
    if not sets:
        return False
    vals.append(row_id)
    conn = get_connection()
    try:
        cur = conn.execute(f"UPDATE rows_ghid SET {', '.join(sets)} WHERE id = ?", vals)
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_row(row_id: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM rows_ghid WHERE id = ?", (row_id,))
        return cur.rowcount > 0
    finally:
        conn.close()


def shift_rows_order(sector_label: str, from_index: int, delta: int = 1) -> None:
    """Împinge order_index cu `delta` pentru rândurile >= from_index (insert între rânduri)."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE rows_ghid SET order_index = order_index + ? "
            "WHERE sector_label = ? AND order_index >= ?",
            (delta, sector_label, from_index),
        )
    finally:
        conn.close()


# =============================================================================
# PLATI
# =============================================================================
def add_plata(cod_loc, suma, data_plata, tip="", chitanta_nr="", observatii="", contract_id=None):
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO plati (cod_loc, contract_id, suma, data_plata, tip, chitanta_nr, observatii) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (cod_loc, contract_id, float(suma or 0), data_plata, tip, chitanta_nr, observatii),
        )
        return cur.lastrowid
    except Exception as e:
        print(f"[DB ERROR add_plata] {e}")
        return None
    finally:
        conn.close()


def list_plati(cod_loc: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        if cod_loc:
            rows = conn.execute(
                "SELECT * FROM plati WHERE cod_loc = ? ORDER BY data_plata DESC, id DESC",
                (cod_loc,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM plati ORDER BY data_plata DESC, id DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_plata(plata_id: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM plati WHERE id = ?", (plata_id,))
        return cur.rowcount > 0
    finally:
        conn.close()


def total_plati(cod_loc: Optional[str] = None) -> float:
    conn = get_connection()
    try:
        if cod_loc:
            row = conn.execute("SELECT COALESCE(SUM(suma),0) AS t FROM plati WHERE cod_loc = ?",
                               (cod_loc,)).fetchone()
        else:
            row = conn.execute("SELECT COALESCE(SUM(suma),0) AS t FROM plati").fetchone()
        return float(row["t"] or 0)
    finally:
        conn.close()


def expiring_contracts(days: int = 30) -> List[Dict[str, Any]]:
    """Contracte a căror dată de sfârșit e în următoarele `days` zile sau deja expirate."""
    conn = get_connection()
    try:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(contracte)").fetchall()]
        date_col = None
        for cand in ("data_sfarsit", "data_expirare", "valabil_pana", "data_final"):
            if cand in cols:
                date_col = cand
                break
        if not date_col:
            return []
        rows = conn.execute(
            f"SELECT * FROM contracte WHERE {date_col} IS NOT NULL AND {date_col} != '' "
            f"AND date({date_col}) <= date('now', '+{int(days)} days') "
            f"ORDER BY {date_col} ASC"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["_date_col"] = date_col
            d["_expira"] = d.get(date_col)
            out.append(d)
        return out
    except Exception as e:
        print(f"[DB ERROR expiring_contracts] {e}")
        return []
    finally:
        conn.close()


# =============================================================================
# DOCUMENTS / MEDIA (poze & documente: loc, inhumat, aviz)
# =============================================================================
# Etapa 7: fișierele pot fi doar în storage-ul online. Un „resolver" (înregistrat de cloud_sync/media_sync în MAIN) aduce
# fișierul în cache-ul local la prima deschidere. Fără resolver (server SQLite, teste) comportamentul rămâne cel vechi.
_media_resolver = None


def set_media_resolver(fn) -> None:
    global _media_resolver
    _media_resolver = fn


def _local_media_path(media_id: Optional[str], path: Optional[str], filename: str = "") -> str:
    """Calea locală a fișierului: cea din DB dacă există pe disc, altfel (online) copia adusă din storage; altfel calea veche."""
    if path and os.path.exists(path):
        return path
    if media_id and _media_resolver is not None:
        try:
            got = _media_resolver(media_id, filename or os.path.basename(path or ""))
            if got:
                return got
        except Exception as e:
            print(f"[media_resolver] {e}")
    return path or ""


def _media_dir(subdir: str = "") -> str:
    base = media_root()
    if subdir:
        base = os.path.join(base, subdir)
    os.makedirs(base, exist_ok=True)
    return base


def import_media_file(src_path: str, subdir: str = "") -> Optional[str]:
    """Copiază fișierul într-un folder media gestionat și întoarce calea absolută."""
    if not src_path or not os.path.exists(src_path):
        return None
    try:
        dest_dir = _media_dir(subdir)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        base = os.path.basename(src_path)
        dest = os.path.join(dest_dir, f"{ts}_{base}")
        shutil.copy2(src_path, dest)
        return dest
    except Exception as e:
        print(f"[import_media_file] {e}")
        return None


def add_document(entity_type: str, entity_id: Any, doc_type: str, src_path: str,
                 caption: str = "", nr_evidenta: str = "", copy: bool = True) -> Optional[int]:
    path = import_media_file(src_path, entity_type) if copy else src_path
    if not path:
        return None
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO documents (entity_type, entity_id, doc_type, file_path, caption, nr_evidenta, date_added) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (entity_type, str(entity_id), doc_type or "", path, caption or "", nr_evidenta or "",
             datetime.now().strftime("%d.%m.%Y %H:%M"))
        )
        return cur.lastrowid
    except Exception as e:
        print(f"[DB ERROR add_document] {e}")
        return None
    finally:
        conn.close()


def list_documents(entity_type: str, entity_id: Any, doc_type: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        if doc_type:
            rows = conn.execute(
                "SELECT * FROM documents WHERE entity_type=? AND entity_id=? AND doc_type=? ORDER BY id",
                (entity_type, str(entity_id), doc_type)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM documents WHERE entity_type=? AND entity_id=? ORDER BY id",
                (entity_type, str(entity_id))).fetchall()
        out = [dict(r) for r in rows]
        for d in out:
            if d.get("media_id"):
                d["file_path"] = _local_media_path(d["media_id"], d.get("file_path"), d.get("caption") or "")
        return out
    finally:
        conn.close()


def _is_managed_media(path: str) -> bool:
    """True doar pentru fișiere din <DATA_DIR>/media — o copie de lucru a DB nu șterge fișierele originalului."""
    try:
        root = os.path.realpath(media_root())
        return os.path.commonpath([root, os.path.realpath(path)]) == root
    except ValueError:
        return False


def delete_document(doc_id: int) -> bool:
    conn = get_connection()
    try:
        row = conn.execute("SELECT file_path FROM documents WHERE id=?", (doc_id,)).fetchone()
        cur = conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        if row and row["file_path"] and _is_managed_media(row["file_path"]) and os.path.exists(row["file_path"]):
            try:
                os.remove(row["file_path"])
            except Exception:
                pass
        return cur.rowcount > 0
    except Exception as e:
        print(f"[DB ERROR delete_document] {e}")
        return False
    finally:
        conn.close()


# =============================================================================
# SETTINGS (key/value store)
# =============================================================================
def get_setting(key: str) -> Optional[str]:
    conn = get_connection()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    conn = get_connection()
    try:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    finally:
        conn.close()


# =============================================================================
# CERERI DE CONCESIONARE (primite din portalul public web)
# =============================================================================
_CERERE_FIELDS = ["cod_loc", "nume", "prenume", "cnp", "bi_ci", "adresa",
                  "telefon", "email", "grad_rudenie", "observatii"]


def add_cerere_concesionare(data: Dict[str, Any], cod_urmarire: Optional[str] = None) -> Optional[int]:
    """Salvează o cerere de concesionare venită din portalul public. Status inițial 'nou'."""
    values = [(data.get(k) or "").strip() if isinstance(data.get(k), str) else (data.get(k) or "")
              for k in _CERERE_FIELDS]
    conn = get_connection()
    try:
        cur = conn.execute(
            f"INSERT INTO cereri_concesionare "
            f"(created_at, status, cod_urmarire, {', '.join(_CERERE_FIELDS)}) "
            f"VALUES (?, 'nou', ?, {', '.join(['?'] * len(_CERERE_FIELDS))})",
            (datetime.now().strftime("%d.%m.%Y %H:%M"), cod_urmarire or _new_tracking_code(conn), *values)
        )
        return cur.lastrowid
    except Exception as e:
        print(f"[DB ERROR add_cerere_concesionare] {e}")
        return None
    finally:
        conn.close()


def _new_tracking_code(conn: sqlite3.Connection) -> str:
    import secrets
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "CR-" + "".join(secrets.choice(alphabet) for _ in range(8))
        if not conn.execute("SELECT 1 FROM cereri_concesionare WHERE cod_urmarire=?", (code,)).fetchone():
            return code


def get_cerere_tracking(cod_urmarire: str) -> Optional[Dict[str, Any]]:
    """Doar câmpurile nepersonale ale unei cereri, găsită după codul de urmărire."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT cod_urmarire, status, created_at, reviewed_at, cod_loc "
            "FROM cereri_concesionare WHERE cod_urmarire = ?",
            ((cod_urmarire or "").strip().upper(),)).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def list_cereri_concesionare(status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Listează cererile. status=None → toate; altfel filtrează (ex. 'nou')."""
    conn = get_connection()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM cereri_concesionare WHERE status = ? ORDER BY id DESC",
                (status,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM cereri_concesionare ORDER BY id DESC").fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_cerere_concesionare(cerere_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM cereri_concesionare WHERE id = ?", (cerere_id,)).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def set_cerere_status(cerere_id: int, status: str, review_note: str = "",
                      concesionar_id: Optional[int] = None) -> bool:
    """Actualizează statusul unei cereri ('validat' / 'respins') + notă + legătură concesionar."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE cereri_concesionare SET status=?, review_note=?, concesionar_id=?, reviewed_at=? "
            "WHERE id=?",
            (status, review_note or "", concesionar_id,
             datetime.now().strftime("%d.%m.%Y %H:%M"), cerere_id)
        )
        return cur.rowcount > 0
    except Exception as e:
        print(f"[DB ERROR set_cerere_status] {e}")
        return False
    finally:
        conn.close()


def count_cereri_noi() -> int:
    """Numărul de cereri cu status 'nou' (pentru badge în UI desktop)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM cereri_concesionare WHERE status = 'nou'").fetchone()
        return int(row["n"]) if row else 0
    finally:
        conn.close()


# =============================================================================
# HELPERS pentru facade-ul `store` (folosite de server / portal public)
# =============================================================================
def map_rows() -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT cod_loc, points, color, sector, parcela, rand, numar, "
            "stare, disponibilitate, ocupat, tip, capacitate, suprafata "
            "FROM locuri_de_veci").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def sector_rows() -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT id, points, color FROM sectoare").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_document_path(doc_id: int) -> Optional[str]:
    conn = get_connection()
    try:
        row = conn.execute("SELECT file_path, media_id, caption FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not row:
            return None
        return _local_media_path(row["media_id"], row["file_path"], row["caption"] or "") if row["media_id"] else row["file_path"]
    finally:
        conn.close()


# ---- users (folosite de auth.py prin store) --------------------------------
def user_get(username: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, username, password_hash, role FROM users WHERE username=?",
            (username.strip(),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def user_touch_login(user_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute("UPDATE users SET last_login=? WHERE id=?",
                     (datetime.now().isoformat(timespec="seconds"), user_id))
    finally:
        conn.close()


def user_set_hash(user_id: int, password_hash: str) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))
        return cur.rowcount > 0
    finally:
        conn.close()


def users_list() -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, username, role, created_at, last_login FROM users ORDER BY username").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def users_count() -> int:
    conn = get_connection()
    try:
        return int(conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"])
    finally:
        conn.close()


def user_insert(username: str, password_hash: str, role: str, created_at: str) -> Optional[int]:
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
            (username.strip(), password_hash, role, created_at))
        return cur.lastrowid
    except Exception as e:
        print(f"[user_insert] {e}")
        return None
    finally:
        conn.close()


def user_delete(user_id: int) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        return cur.rowcount > 0
    finally:
        conn.close()


# -----------------------------------------------------------------------------
if __name__ == "__main__":
    print(f"DB path: {DB_PATH}")
    print("Inițializare / migrare schemă...")
    init_db()
    print("GATA — baza de date este pregătită.")
