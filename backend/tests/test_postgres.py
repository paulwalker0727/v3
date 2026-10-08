"""ETAPA 1 — PostgreSQL: schemă/migrații, strat de compatibilitate, paritate cu SQLite, facade `store`, HTTP + sincronizare.

Rulare (necesită un PostgreSQL de TEST, DESCHIS DOAR PENTRU TESTE — schema `public` se șterge la fiecare test):

    scripts/dev_postgres.sh start                 # cluster local de dezvoltare (port 55432)
    export CIMITIR_TEST_DATABASE_URL=postgresql://cimitir@127.0.0.1:55432/cimitir_test
    cd backend && python -m pytest tests/test_postgres.py -q

Fără CIMITIR_TEST_DATABASE_URL testele care au nevoie de server sunt SĂRITE (traducerea SQL se testează oricum).
Toate testele stau într-un singur modul: `--dist loadscope` le rulează pe un singur worker (o singură bază comună).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "shared", ROOT / "backend" / "cimitir", ROOT / "backend"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pg_compat  # noqa: E402

TEST_URL = os.environ.get("CIMITIR_TEST_DATABASE_URL", "").strip()
TENANT_COLS = ("organization_uid", "cemetery_uid")   # coloane de tenant (migrările 008–013): ignorate la compararea cu schema SQLite
TENANT_TABLES = {"organizations", "cemeteries", "identities", "organization_members", "cemetery_permissions", "devices", "device_cemetery_bindings", "device_pairing_codes"}
needs_pg = pytest.mark.skipif(not TEST_URL, reason="CIMITIR_TEST_DATABASE_URL nu este setat")

EXPECTED_TABLES = {
    "locuri_de_veci", "concesionari", "contracte", "inhumati", "sectoare", "settings", "photos", "amenajari_avize",
    "loc_extra", "guides", "rows_ghid", "plati", "documents", "citizens", "citizen_resets", "cereri_concesionare",
    "users", "audit_log", "loc_holders", "sync_ops", "email_outbox", "schema_migrations",
    "ai_scan_history", "qr_generations", "cloud_sync_map", "cloud_sync_outbox", "cloud_sync_docs",
    "sync_tombstones", "sync_conflicts", "media_objects",
}
SYNC_ENTITIES = ["locuri_de_veci", "concesionari", "contracte", "inhumati", "sectoare", "photos", "amenajari_avize",
                 "guides", "rows_ghid", "plati", "documents", "citizens", "cereri_concesionare", "users"]
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


SCHEMA_BASE = os.environ.get("CIMITIR_TEST_SCHEMA", "")   # schemă izolată (ex. e2_diff) pe proiectul Supabase TEST; gol = baza locală de test


def _schema() -> str:
    return (SCHEMA_BASE + "_" + os.environ.get("PYTEST_XDIST_WORKER", "main")) if SCHEMA_BASE else ""


def _wipe_test_db() -> None:
    import psycopg
    with psycopg.connect(TEST_URL, autocommit=True) as c:
        SCHEMA = _schema()
        if SCHEMA:
            assert SCHEMA.startswith("e2_") and "nhhczdmcocbobptogvsw" in TEST_URL, "schemă izolată doar e2_* pe proiectul TEST"
            c.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
            c.execute(f'CREATE SCHEMA "{SCHEMA}"')
            return
        name = c.execute("SELECT current_database()").fetchone()[0]
        assert "test" in name, f"refuz să șterg baza '{name}': numele trebuie să conțină 'test'"
        c.execute("DROP SCHEMA public CASCADE")
        c.execute("CREATE SCHEMA public")


@pytest.fixture()
def pg(monkeypatch):
    """postgres_store pe o bază goală, cu schema creată de migrații."""
    if not TEST_URL:
        pytest.skip("CIMITIR_TEST_DATABASE_URL nu este setat")
    import postgres_store as ps
    pg_compat.close_pool()
    _wipe_test_db()
    SCHEMA = _schema()
    if SCHEMA:
        orig = pg_compat._configure

        def cfg(raw):
            orig(raw)
            raw.execute(f'SET search_path TO "{SCHEMA}"')
        monkeypatch.setattr(pg_compat, "_configure", cfg)
        monkeypatch.setenv("PGOPTIONS", f"-c search_path={SCHEMA}")   # procesele pornite de teste folosesc aceeași schemă izolată
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    monkeypatch.setenv("CIMITIR_ALLOW_TENANT_MIGRATIONS", "1")
    ps._initialized.clear()
    ps.init_db()
    yield ps
    pg_compat.close_pool()
    if SCHEMA:
        import psycopg
        with psycopg.connect(TEST_URL, autocommit=True) as c:
            c.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')


@pytest.fixture()
def sq(tmp_path):
    """Același API pe nucleul SQLite, într-o bază temporară (referința pentru testele de paritate)."""
    import cimitir_database as sqlite_db
    sqlite_db.set_active_db_path(str(tmp_path / "ref.db"))
    sqlite_db.init_db()
    yield sqlite_db
    sqlite_db.set_active_db_path("")


# =============================================================================
# 1. Traducerea SQL (fără server)
# =============================================================================
def test_translate_placeholders_like_and_percent():
    sql, rid = pg_compat.translate("SELECT * FROM locuri_de_veci WHERE cod_loc LIKE ? AND tip='100%' AND sector=?")
    assert sql == "SELECT * FROM locuri_de_veci WHERE cod_loc ILIKE %s AND tip='100%%' AND sector=%s" and not rid


def test_translate_insert_variants():
    sql, rid = pg_compat.translate("INSERT INTO concesionari (nume) VALUES (?)")
    assert sql.endswith("RETURNING id") and rid
    sql, rid = pg_compat.translate("INSERT INTO settings (key, value) VALUES (?, ?)")
    assert "RETURNING" not in sql and not rid  # tabele fără coloană id
    sql, _ = pg_compat.translate("INSERT OR IGNORE INTO sync_ops (op_id, kind) VALUES (?,?)")
    assert sql.startswith("INSERT INTO sync_ops") and sql.endswith("ON CONFLICT DO NOTHING")
    sql, _ = pg_compat.translate("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)")
    assert "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value" in sql
    sql, _ = pg_compat.translate("INSERT OR IGNORE INTO email_outbox (cod_urmarire) VALUES (?)")
    assert "ON CONFLICT DO NOTHING RETURNING id" in sql


def test_translate_ddl_and_pragma():
    sql, _ = pg_compat.translate("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY AUTOINCREMENT, x REAL, "
                                 "c TEXT DEFAULT (datetime('now')))")
    assert "GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY" in sql and "DOUBLE PRECISION" in sql and "to_char(" in sql


def test_row_behaves_like_sqlite_row():
    row = pg_compat.Row((1, "A-1"), {"id": 0, "cod_loc": 1})
    assert row["id"] == 1 and row[1] == "A-1" and row.keys() == ["id", "cod_loc"] and dict(row) == {"id": 1, "cod_loc": "A-1"}
    (a, b) = row
    assert (a, b) == (1, "A-1")


def test_database_url_comes_only_from_environment(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("CIMITIR_ENV_FILE", "/nonexistent.env")
    monkeypatch.setattr("cimitir_config._env_loaded", True)  # nu citi .env din proiect
    with pytest.raises(pg_compat.PgConfigError):
        pg_compat.database_url()
    monkeypatch.setenv("DATABASE_URL", "postgres://u:secret@h:5432/d")
    assert pg_compat.database_url().startswith("postgresql://")
    assert "secret" not in pg_compat.mask_url(pg_compat.database_url())
    for src in ("postgres_store.py", "pg_compat.py"):  # nicio credențială în cod
        text = (ROOT / "backend" / "cimitir" / src).read_text(encoding="utf-8")
        assert not re.search(r"postgres(ql)?://\w+:\w+@", text)


# =============================================================================
# 2. Schema și migrațiile
# =============================================================================
@needs_pg
def test_migrations_create_all_tables_and_are_idempotent(pg):
    with pg._db() as c:
        tables = {r[0] for r in c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()")}
        assert EXPECTED_TABLES <= tables, EXPECTED_TABLES - tables
        assert pg.schema_version() == 13  # 001 + 002 + 003 (Etapa 6: sincronizare pe entități)
    assert pg.migrate() == []  # a doua rulare nu aplică nimic
    pg.init_db(force=True)
    assert pg.schema_version() == 13


@needs_pg
def test_stable_uid_and_modified_at_on_sync_entities(pg):
    with pg._db() as c:
        for table in SYNC_ENTITIES:
            cols = {r[0] for r in c.execute("SELECT column_name FROM information_schema.columns "
                                            "WHERE table_name=? AND table_schema=current_schema()", (table,))}
            assert {"uid", "modified_at"} <= cols, table
    pg.save_plot_to_db("A-1", [(0, 0), (1, 1)], "#111")
    pg.save_concesionar({"nume": "Pop"})
    with pg._db() as c:
        uid = c.execute("SELECT uid FROM locuri_de_veci WHERE cod_loc='A-1'").fetchone()["uid"]
        assert UUID_RE.match(uid)
        before = c.execute("SELECT modified_at FROM locuri_de_veci WHERE cod_loc='A-1'").fetchone()[0]
    pg.save_loc_metadata("A-1", {"sector": "S1"})
    with pg._db() as c:
        row = c.execute("SELECT uid, modified_at FROM locuri_de_veci WHERE cod_loc='A-1'").fetchone()
        assert row["uid"] == uid and row["modified_at"] > before  # uid stabil, modified_at actualizat de trigger


@needs_pg
def test_map_polygons_are_jsonb(pg):
    pg.save_plot_to_db("A-1", [(0, 0), (4, 0), (4, 3)], "#111")
    pg.save_sector_to_db("S1", [(0, 0), (9, 9)], "#222")
    with pg._db() as c:
        assert c.execute("SELECT jsonb_typeof(points) AS t FROM locuri_de_veci").fetchone()["t"] == "array"
        assert c.execute("SELECT jsonb_array_length(points) AS n FROM sectoare").fetchone()["n"] == 2
    assert pg.load_loc_by_cod("A-1")["points"] == [[0, 0], [4, 0], [4, 3]]
    assert pg.load_all_sectors() == [{"id": "S1", "points": [[0, 0], [9, 9]], "color": "#222"}]


@needs_pg
def test_foreign_keys_cascade_and_set_null(pg):
    pg.save_plot_to_db("A-1", [], "#111")
    pg.save_photo("A-1", "/x.jpg")
    pg.add_aviz("A-1", "12", "2024-01-01", "gard")
    cid = pg.save_concesionar({"nume": "Pop"})
    k = pg.save_contract({"cod_loc": "A-1", "concesionar_id": cid})
    pg.save_inhumat("A-1", {"nume": "X"})
    assert pg.delete_concesionar(cid) and pg.get_contract(k)["concesionar_id"] == ""  # SET NULL
    assert pg.delete_plot("A-1")
    assert pg.list_photos_for_loc("A-1") == [] and pg.list_avize_for_loc("A-1") == []  # CASCADE
    assert pg.load_all_inhumati()[0]["id_loc"] == ""  # SET NULL


# =============================================================================
# 3. Paritate funcțională PostgreSQL <-> SQLite (aceeași interfață, aceleași rezultate)
# =============================================================================
VOLATILE = {"reviewed_at", "uid", "modified_at", "created_ts", "created_at", "date_added", "applied_at", "version", "change_seq", "media_id",
            "description", "original_filename", "mime_type", "uploaded_by", "source"}   # ultimele 5: atașamente (005), doar PostgreSQL


def _norm(value):
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, (list, tuple)):
        return [_norm(v) for v in value]
    if value is None:
        return ""
    if isinstance(value, float) and value == int(value):
        return int(value)
    if isinstance(value, str):
        value = re.sub(r"\d{8}_\d{6}_\d{6}_", "TS_", value).replace("sq_data", "DATA").replace("pg_data", "DATA")
    if isinstance(value, str) and value[:1] in "[{" and value[-1:] in "]}":
        try:
            return _norm(json.loads(value))
        except Exception:
            return value
    return value


def _scenario(m, tmp_path):
    out = {}
    out["plot_ids"] = [m.save_plot_to_db("A-1", [(0, 0), (4, 0), (4, 3)], "#111"),
                       m.save_plot_to_db("A-2", [(5, 0), (9, 0)], "#222"),
                       m.save_plot_to_db("A-1", [(0, 0), (1, 1)], "#333")]  # upsert
    m.save_loc_metadata("A-1", {"sector": "S1", "parcela": "P1", "rand": "1", "numar": "1", "suprafata": "6.5",
                                "disponibilitate": "Liber", "tip": "Simplu", "extra_data": {"nota": "ok", "n": 2}})
    m.save_loc_metadata("A-2", {"sector": "S2", "parcela": "P1", "rand": "2", "numar": "7", "disponibilitate": "Ocupat",
                                "ocupat": 1})
    m.save_loc_metadata("B-9", {"sector": "S1"})  # creează rândul dacă lipsește
    out["loc"] = m.load_loc_by_cod("A-1")
    out["locuri"] = m.list_locuri()
    out["search_like"] = [r["cod_loc"] for r in m.search_locuri({"cod_loc": "a-"})]
    out["search_precise"] = [r["cod_loc"] for r in m.search_locuri({"sector": "S1", "_precise": True})]
    out["search_exact"] = [r["cod_loc"] for r in m.search_locuri({"disponibilitate": "Ocupat"})]
    out["map_rows"] = sorted(m.map_rows(), key=lambda r: r["cod_loc"])
    out["loc_id"] = m.get_loc_id_by_cod("A-2")

    cid = m.save_concesionar({"nume": "Pop", "prenume": "Ion", "cnp": "1800101123456", "telefon": "0700"})
    cid2 = m.save_concesionar({"nume": "Ana", "prenume": "Ene"})
    m.save_concesionar({"nume": "Ana", "prenume": "Eneas"}, cid2)
    out["conc"] = [m.get_concesionar(cid), m.load_concesionari()]
    k = m.save_contract({"cod_loc": "A-1", "concesionar_id": cid, "numar_contract": "7/2024",
                         "data_sfarsit": "2020-05-01", "extra_data": {"x": 1}})
    k2 = m.save_contract({"cod_loc": "A-2", "numar_contract": "8/2024", "data_sfarsit": "2999-01-01"})
    m.save_contract({"cod_loc": "A-2", "numar_contract": "8b/2024", "data_sfarsit": "2999-01-01"}, k2)
    out["bad_conc"] = m.save_contract({"concesionar_id": 9999})
    out["contracts"] = [m.get_contract(k), m.get_contract(k2), m.load_contracte(), m.load_contracte("WHERE cod_loc='A-1'")]
    out["expiring0"] = [c["numar_contract"] for c in m.expiring_contracts(0)]
    out["expiring_far"] = [c["numar_contract"] for c in m.expiring_contracts(100000)]

    i1 = m.save_inhumat("A-1", {"nume": "Vasile", "prenume": "P", "data_inhumare": "2020-01-02", "cnp": "1"})
    i2 = m.save_inhumat("A-1", {"nume": "Maria", "data_inhumare": "2021-03-04"})
    m.save_inhumat("A-1", {"nume": "Maria", "prenume": "Z", "data_inhumare": "2021-03-04"}, i2)
    out["bad_inhumat"] = m.save_inhumat("NU-EXISTA", {"nume": "X"})
    out["inhumati"] = [m.load_inhumati_for_loc("A-1"), m.load_all_inhumati({"nume": "mar"}), m.get_inhumat(i1)]
    out["delete_inhumat"] = [m.delete_inhumat(i1), m.delete_inhumat(i1)]

    m.save_sector_to_db("S1", [(0, 0), (10, 10)], "#aaa")
    m.save_sector_to_db("S1", [(0, 0), (11, 11)], "#bbb")
    m.save_sector_to_db("S2", [(1, 1)], "#ccc")
    out["sectors"] = [sorted(m.load_all_sectors(), key=lambda s: s["id"]), m.delete_sector_from_db("S2"),
                      [s["id"] for s in m.load_all_sectors()]]

    p1, p2 = m.save_photo("A-1", "/p1.jpg", {"caption": "c1"}), m.save_photo("A-1", "/p2.jpg")
    m.set_cover_photo("A-1", p2)
    out["photos"] = [m.list_photos_for_loc("A-1"), m.delete_photo(p1), m.delete_photo(p1)]
    a1 = m.add_aviz("A-1", "12", "2024-05-05", "gard")
    out["avize"] = [m.list_avize_for_loc("A-1"), m.delete_aviz(a1), m.list_avize_for_loc("A-1")]
    m.save_extra_data("A-1", {"a": [1, 2], "b": "ț"})
    out["extra"] = [m.load_extra_data("A-1"), m.load_extra_data("nu-exista")]

    g1 = m.add_guide("line", [(0, 0), (5, 5)], "#0af")
    m.add_guide("line", [(1, 1)], "#0af")
    out["guides"] = [m.list_guides(), m.delete_guide(g1), len(m.list_guides())]
    m.clear_guides()
    out["guides_cleared"] = m.list_guides()
    r1 = m.add_row("S1", "R1", 0, [(0, 0), (9, 0)], 2.0, 1.5)
    m.add_row("S1", "R2", 1, [(0, 1)], 2.0, 1.5)
    m.shift_rows_order("S1", 1, 2)
    m.update_row(r1, name="R1b", width=3.5, baseline=[(0, 0), (1, 1)])
    out["rows"] = [m.list_rows("S1"), m.list_rows(), m.update_row(r1), m.update_row(r1, bogus=1), m.delete_row(r1)]

    m.add_plata("A-1", "100.5", "2024-01-01", "taxa", "C1", "o", k)
    m.add_plata("A-1", 50, "2024-02-01")
    m.add_plata("A-2", 5, "2024-03-01")
    out["plati"] = [m.list_plati("A-1"), len(m.list_plati()), m.total_plati("A-1"), m.total_plati(), m.total_plati("x")]
    out["delete_plata"] = m.delete_plata(m.list_plati("A-2")[0]["id"])

    src = tmp_path / "act.pdf"
    src.write_bytes(b"%PDF-1.4 test")
    d1 = m.add_document("loc", "A-1", "act", str(src), "Act", "N1")
    out["docs"] = [m.list_documents("loc", "A-1"), m.list_documents("loc", "A-1", "act"), m.list_documents("loc", "A-1", "x"),
                   os.path.exists(m.get_document_path(d1)), m.add_document("loc", "A-1", "x", str(tmp_path / "nu.pdf"))]
    out["delete_document"] = [m.delete_document(d1), m.get_document_path(d1)]

    m.set_setting("px_per_meter", "10")
    m.set_setting("px_per_meter", "12")
    out["settings"] = [m.get_setting("px_per_meter"), m.get_setting("nu_exista")]

    c1 = m.add_cerere_concesionare({"cod_loc": "A-1", "nume": " Popa ", "prenume": "M", "cnp": "1", "email": "a@b.ro"}, "CR-TEST0001")
    c2 = m.add_cerere_concesionare({"cod_loc": "A-2", "nume": "Ene"})
    out["cereri"] = [m.get_cerere_tracking(" cr-test0001 "), m.count_cereri_noi(), m.set_cerere_status(c1, "validat", "ok", cid),
                     m.count_cereri_noi(), m.get_cerere_concesionare(c1), [r["id"] for r in m.list_cereri_concesionare()],
                     [r["id"] for r in m.list_cereri_concesionare("nou")], m.get_cerere_tracking("nu-exista"),
                     m.set_cerere_status(9999, "validat")]
    assert c2

    u = m.user_insert("ion ", "hash1", "admin", "2024-01-01")
    out["users"] = [m.user_get("ion"), m.user_insert("ion", "x", "user", "2024"), m.user_set_hash(u, "hash2"),
                    m.users_list(), m.users_count(), m.delete_plot("A-2"), m.user_delete(u), m.users_count()]
    return out


@needs_pg
def test_postgres_store_has_the_same_interface_as_sqlite_core(pg, sq):
    import inspect
    public = [n for n, f in vars(sq).items() if inspect.isfunction(f) and not n.startswith("_") and f.__module__ == sq.__name__]
    missing = [n for n in public if not hasattr(pg, n)]
    assert not missing, f"lipsesc din postgres_store: {missing}"
    for name in public:
        if hasattr(pg, name) and name not in ("get_connection", "set_active_db_path", "current_db_path"):
            a, b = inspect.signature(getattr(sq, name)), inspect.signature(getattr(pg, name))
            sq_params = list(a.parameters)  # PostgreSQL poate adăuga doar parametri opționali la final
            assert list(b.parameters)[:len(sq_params)] == sq_params, (name, a, b)


@needs_pg
def test_parity_with_sqlite_for_every_store_function(pg, sq, tmp_path, monkeypatch):
    monkeypatch.setattr(sq, "DATA_DIR", str(tmp_path / "sq_data"))
    monkeypatch.setattr(pg, "_media_dir", lambda sub="": (lambda p: (os.makedirs(p, exist_ok=True), p)[1])(
        os.path.join(str(tmp_path / "pg_data"), sub)))
    monkeypatch.setattr(pg, "import_media_file", lambda src, sub="": sq.import_media_file(src, sub))
    expected = _norm(_scenario(sq, tmp_path))
    actual = _norm(_scenario(pg, tmp_path))
    for key in expected:
        assert actual[key] == expected[key], f"diferență la '{key}':\n  sqlite   = {expected[key]}\n  postgres = {actual[key]}"
    assert actual == expected


# =============================================================================
# 4. Compatibilitate SQL direct (cum îl folosesc sync_api, citizen_api, cimitir_auth, ...)
# =============================================================================
@needs_pg
def test_raw_sql_compat_lastrowid_rows_upsert_and_transactions(pg):
    conn = pg.get_connection()
    try:
        cur = conn.execute("INSERT INTO citizens (uid, email, password_hash, full_name, created_at, active) "
                           "VALUES (?,?,?,?,?,1)", ("00000000-0000-4000-8000-000000000001", "a@b.ro", "h", "A", "t"))
        assert cur.lastrowid and cur.rowcount == 1
        row = conn.execute("SELECT id, email FROM citizens WHERE email LIKE ?", ("%A@B%",)).fetchone()  # LIKE insensibil la majuscule
        assert row["email"] == "a@b.ro" and row[0] == row["id"] and dict(row)["email"] == "a@b.ro"
        assert conn.execute("SELECT COUNT(*) FROM citizens").fetchone()[0] == 1
        conn.execute("INSERT OR IGNORE INTO sync_ops (op_id, kind, applied_at, result) VALUES (?,?,?,?)", ("o1", "k", "t", "r"))
        conn.execute("INSERT OR IGNORE INTO sync_ops (op_id, kind, applied_at, result) VALUES (?,?,?,?)", ("o1", "k2", "t", "r2"))
        assert [tuple(r) for r in conn.execute("SELECT kind FROM sync_ops")] == [("k",)]
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", ("k", "1"))
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", ("k", "2"))
        assert conn.execute("SELECT value FROM settings WHERE key='k'").fetchone()["value"] == "2"
        for (rid,) in conn.execute("SELECT id FROM citizens").fetchall():  # despachetare ca la sqlite3.Row
            assert rid
        # tranzacție cu rollback (ca în cimitir_requests.approve)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM citizens")
        conn.execute("ROLLBACK")
        assert conn.execute("SELECT COUNT(*) FROM citizens").fetchone()[0] == 1
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM citizens")
        conn.execute("COMMIT")
        assert conn.execute("SELECT COUNT(*) FROM citizens").fetchone()[0] == 0
        conn.execute("CREATE TABLE IF NOT EXISTS citizen_resets (id INTEGER PRIMARY KEY AUTOINCREMENT)")  # no-op (există)
        conn.execute("PRAGMA foreign_keys = ON")
    finally:
        conn.close()


@needs_pg
def test_open_transaction_is_rolled_back_when_connection_is_released(pg):
    conn = pg.get_connection()
    conn.execute("BEGIN")
    conn.execute("INSERT INTO settings (key, value) VALUES ('tmp','1')")
    conn.close()
    assert pg.get_setting("tmp") is None


@needs_pg
def test_shared_modules_route_to_postgres_when_engine_is_postgres(pg, monkeypatch):
    monkeypatch.setenv("CIMITIR_DB_ENGINE", "postgres")
    import cimitir_auth, cimitir_db_router
    assert cimitir_db_router.get_connection.__module__ == "postgres_store"
    cimitir_auth.init_auth()
    assert pg.users_count() == 2  # admin + user implicite, create în PostgreSQL
    user, _ = cimitir_auth.authenticate_ex("admin", "admin123")
    assert user and user["role"] == "admin"
    monkeypatch.setenv("CIMITIR_DB_ENGINE", "sqlite")
    import cimitir_database
    assert cimitir_db_router.get_connection.__module__ == cimitir_database.__name__  # SQLite rămâne implicit


# =============================================================================
# 5. Facade `store`
# =============================================================================
@needs_pg
def test_store_facade_selects_engine(pg, monkeypatch, tmp_path):
    import store
    monkeypatch.setenv("CIMITIR_DB_ENGINE", "postgres")
    src = store.list_sources()
    assert [s["engine"] for s in src] == ["postgres"] and src[0]["id"] == "cimitir"
    store.ensure_initialized(src[0])
    assert store.active_engine() == "postgres"
    store.set_setting("nume_test", "PG")
    assert store.get_setting("nume_test") == "PG" and pg.get_setting("nume_test") == "PG"
    info = store.describe_source(src[0])
    assert info["available"] and info["engine"] == "postgres"
    with pytest.raises(RuntimeError):
        store.create_sqlite_source("x")
    monkeypatch.setenv("CIMITIR_DB_ENGINE", "sqlite")  # implicit: SQLite
    monkeypatch.setenv("CIMITIR_DATA_DIR", str(tmp_path))
    store._engine_ctx.set(None)
    assert store.process_engine() == "sqlite" and store.active_engine() == "sqlite"


def test_sqlite_remains_the_default_engine(monkeypatch):
    import cimitir_config
    monkeypatch.delenv("CIMITIR_DB_ENGINE", raising=False)
    assert cimitir_config.db_engine() == "sqlite"
    monkeypatch.setenv("CIMITIR_DB_ENGINE", "PostgreSQL")
    assert cimitir_config.db_engine() == "postgres"
    monkeypatch.setenv("CIMITIR_DB_ENGINE", "oricum")
    assert cimitir_config.db_engine() == "sqlite"


@needs_pg
def test_health_reports_connection_and_hides_secrets(pg, monkeypatch):
    h = pg.health()
    assert h["engine"] == "postgres" and h["connected"] is True and h["schema_version"] == 13 and h["latency_ms"] >= 0
    monkeypatch.setenv("CIMITIR_PG_CONNECT_TIMEOUT", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:topsecret@127.0.0.1:1/nope")
    bad = pg.health()
    assert bad["connected"] is False and "topsecret" not in json.dumps(bad)


# =============================================================================
# 6. Server real (uvicorn) pe PostgreSQL: /api/public/status, portal, personal, sincronizare MAIN
# =============================================================================
import test_etapa2_sync as t2  # noqa: E402


@pytest.fixture()
def pg_server_env(monkeypatch, tmp_path):
    """Serverele pornite de testele existente (CIMITIR_MODE setat) primesc CIMITIR_DB_ENGINE=postgres + baza de test.
    Procesele MAIN (desktop, SQLite) NU primesc variabilele — desktopul nu se schimbă în Etapa 1."""
    if not TEST_URL:
        pytest.skip("CIMITIR_TEST_DATABASE_URL nu este setat")
    pg_compat.close_pool()
    _wipe_test_db()
    SCHEMA = _schema()
    if SCHEMA:
        monkeypatch.setenv("PGOPTIONS", f"-c search_path={SCHEMA}")   # procesul de test și serverele pornite folosesc schema izolată
    monkeypatch.setenv("CIMITIR_ALLOW_TENANT_MIGRATIONS", "1")
    original = t2._env

    def pg_env(data_dir, **extra):
        env = original(data_dir, **extra)
        if "CIMITIR_MODE" in extra:
            env.update(CIMITIR_DB_ENGINE="postgres", DATABASE_URL=TEST_URL, CIMITIR_ALLOW_TENANT_MIGRATIONS="1")
            if SCHEMA:
                env["PGOPTIONS"] = f"-c search_path={SCHEMA}"
        else:
            # MAIN (SQLite): valori explicite, ca backend/.env (server de preview pe PostgreSQL) să nu le suprascrie
            env["CIMITIR_DB_ENGINE"] = "sqlite"
            env["DATABASE_URL"] = ""
        return env
    monkeypatch.setattr(t2, "_env", pg_env)

    # Testele existente deschid direct fișierul SQLite al serverului (<dir>/server/cimitir.db) pentru verificări;
    # aici aceleași verificări merg în PostgreSQL. Bazele MAIN (desktop) rămân SQLite reale.
    import sqlite3 as real_sqlite3
    monkeypatch.setenv("DATABASE_URL", TEST_URL)

    class Sqlite3Shim:
        def __getattr__(self, name):
            return getattr(real_sqlite3, name)

        def connect(self, path, *a, **k):
            if Path(str(path)).parent.name == "server":
                return pg_compat.connect()
            return real_sqlite3.connect(path, *a, **k)
    shim = Sqlite3Shim()
    monkeypatch.setattr(t2, "sqlite3", shim)
    monkeypatch.setattr("test_etapa3_requests.sqlite3", shim)
    yield
    pg_compat.close_pool()
    if SCHEMA:
        import psycopg
        with psycopg.connect(TEST_URL, autocommit=True) as c:
            c.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')


def test_status_endpoint_for_sqlite_default(tmp_path):
    server = t2.Server(tmp_path / "s", mode="local").start()
    try:
        body = server.get("/api/public/status")
        assert body["server"] == "online" and body["status"] == "ok"
        assert body["database"] == {"engine": "sqlite", "connected": True}
    finally:
        server.stop()


def test_server_on_postgres_reports_status_and_stores_data_in_postgres(pg_server_env, tmp_path):
    server = t2.Server(tmp_path / "s").start()
    try:
        body = server.get("/api/public/status")
        assert body["server"] == "online" and body["status"] == "ok" and body["mode"] == "online"
        db = body["database"]
        assert db["engine"] == "postgres" and db["connected"] is True and db["schema_version"] == 13
        assert "url" not in json.dumps(body).lower() and "password" not in json.dumps(body).lower()
        assert not (tmp_path / "s" / "cimitir.db").exists()  # nu s-a creat nicio bază SQLite

        # cont cetățean + cerere prin API public -> rând în PostgreSQL
        import test_etapa3_requests as t3
        key = t2._server_py(server.data_dir, "import sync_api; print(sync_api.issue_sync_key('cimitir'))")
        assert key.startswith("cimitir.")
        c = t3.Client(server).register("pg@example.com")
        assert c.call("GET", "/api/public/account/me")[1]["email"] == "pg@example.com"
        # personal: login admin implicit, creat în PostgreSQL
        staff = t3.Client(server)
        assert staff.call("POST", "/admin/access/login", fields={"username": "admin", "password": "admin123",
                                                                "source_id": "cimitir"})[0] == 303
        assert staff.call("GET", "/admin/")[0] == 303  # parola implicită trebuie schimbată
        staff.call("POST", "/admin/schimba-parola", fields={"old_password": "admin123", "new_password": "Parola-test-987",
                                                            "confirm_password": "Parola-test-987"})
        assert staff.call("GET", "/admin/")[0] == 200
        import psycopg
        with psycopg.connect(TEST_URL) as conn:
            assert conn.execute("SELECT count(*) FROM citizens WHERE email='pg@example.com'").fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM users WHERE username='admin'").fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM audit_log").fetchone()[0] >= 1
    finally:
        server.stop()


def test_status_reports_degraded_when_postgres_is_unreachable(tmp_path, monkeypatch):
    monkeypatch.setenv("CIMITIR_PG_CONNECT_TIMEOUT", "1")
    original = t2._env

    def bad_env(data_dir, **extra):
        env = original(data_dir, **extra)
        env.update(CIMITIR_DB_ENGINE="postgres", DATABASE_URL="postgresql://u:topsecret@127.0.0.1:1/nope",
                   CIMITIR_PG_CONNECT_TIMEOUT="1")
        return env
    monkeypatch.setattr(t2, "_env", bad_env)

    class DownServer(t2.Server):
        def get(self, path, headers=None):  # /api/public/health dă 500 cu baza oprită: serverul tot ascultă
            if path == "/api/public/health":
                return self.request("GET", path)
            return super().get(path, headers)
    server = DownServer(tmp_path / "s").start()
    try:
        status, body = server.request("GET", "/api/public/status")
        assert status == 503 and body["server"] == "online" and body["status"] == "degraded"
        assert body["database"]["connected"] is False and "topsecret" not in json.dumps(body)
    finally:
        server.stop()


# --- scenariile existente (portal cetățeni + sincronizare MAIN SQLite <-> server PostgreSQL), rulate pe PostgreSQL ---
import test_etapa3_requests as t3  # noqa: E402


@pytest.fixture()
def env(pg_server_env, tmp_path):  # suprascrie fixture-ul din etapa 2: același mediu, dar serverul rulează pe PostgreSQL
    server = t2.Server(tmp_path / "server")
    server.data_dir.mkdir()
    key = t2._server_py(server.data_dir, "import sync_api; print(sync_api.issue_sync_key('cimitir'))")
    server.start()
    main_dir = tmp_path / "main"
    main_dir.mkdir()
    yield server, key, main_dir, tmp_path
    server.stop()


@pytest.fixture()
def env3(pg_server_env, tmp_path, monkeypatch):  # idem, pentru etapa 3 (cont obligatoriu pentru cereri)
    monkeypatch.setenv("CIMITIR_PORTAL_REQUIRE_ACCOUNT", "1")
    server = t2.Server(tmp_path / "server")
    server.data_dir.mkdir()
    key = t2._server_py(server.data_dir, "import sync_api; print(sync_api.issue_sync_key('cimitir'))")
    server.start()
    main_dir = tmp_path / "main"
    main_dir.mkdir()
    t2.sync(main_dir, server, key, t2.SEED.replace('"Ocupat"', '"Liber"'))
    yield server, key, main_dir, tmp_path
    server.stop()


# Se re-rulează TOATE scenariile existente care au sens pe PostgreSQL (sincronizare, hartă publică, cereri, personal,
# drepturi, aprobare/rollback, concurență, retenție/GDPR). Excluse: backup/restore (copie de fișier SQLite).
# Excluse și cele două scenarii cu MAI MULTE baze/cimiteri pe același server (`create_sqlite_source`): în Etapa 1 serverul
# PostgreSQL are o singură bază; izolarea pe cimitir (coloană cemetery_id / scheme) rămâne pentru o etapă ulterioară.
_SQLITE_ONLY = {"test_backup_restore_roundtrip_and_tamper_detection", "test_sync_endpoints_isolation_and_auth",
                "test_main_refuses_key_of_another_cemetery_and_insecure_urls"}
for _mod, _prefix in ((t2, "test_pg_sync_"), (t3, "test_pg_req_")):
    for _name, _fn in list(vars(_mod).items()):
        if _name.startswith("test_") and callable(_fn) and _name not in _SQLITE_ONLY and _fn.__module__ == _mod.__name__:
            globals()[_prefix + _name[5:]] = _fn


# =============================================================================
# 7. scripts/check_postgres.py + „PostgreSQL nu se activează automat”
# =============================================================================
import subprocess  # noqa: E402

CHECK = ROOT / "scripts" / "check_postgres.py"


def _clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in ("CIMITIR_DB_ENGINE", "DATABASE_URL", "CIMITIR_ENV_FILE")}
    # valori goale (nu lipsă): backend/.env al serverului de preview (PostgreSQL) nu are voie să le completeze
    env.update(CIMITIR_DB_ENGINE="", DATABASE_URL="")
    env.update(extra)
    return env


def _check(*args, **env):
    p = subprocess.run([sys.executable, str(CHECK), *args], env=_clean_env(**env), capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout, p.stderr


@needs_pg
def test_check_script_reports_connection_version_latency_and_schema(pg):
    code, out, err = _check("--json", DATABASE_URL=TEST_URL)
    rep = json.loads(out)
    assert code == 0 and rep["ok"] is True
    assert rep["connection"]["connected"] is True and rep["connection"]["connect_ms"] >= 0
    assert rep["version"]["supported"] is True and re.match(r"^\d+", rep["version"]["server_version"])
    assert rep["latency_ms"]["samples"] == 5 and rep["latency_ms"]["query_avg"] >= 0
    sc = rep["schema"]
    assert sc["state"] == "complete" and sc["tables_missing"] == [] and sc["migrations_pending"] == []
    assert sc["migrations_applied"] == list(range(1, 14)) and sc["tables_present"] == sc["tables_expected"] == 39 and sc["can_create"]
    assert rep["engine_configured"] == "sqlite"  # scriptul nu activează PostgreSQL
    assert TEST_URL not in out + err
    code, out, _ = _check("--strict", DATABASE_URL=TEST_URL)  # varianta text
    assert code == 0 and "Versiune PostgreSQL" in out and "Latență interogare" in out and "Schemă: completă" in out


@needs_pg
def test_check_script_is_read_only_and_reports_empty_or_incomplete_schema(pg):
    _wipe_test_db()  # bază goală
    code, out, _ = _check("--json", DATABASE_URL=TEST_URL)
    rep = json.loads(out)
    assert code == 0 and rep["schema"]["state"] == "empty" and rep["schema"]["migrations_pending"] == sorted(x.name for x in (ROOT / "backend" / "cimitir" / "pg_migrations").glob("[0-9][0-9][0-9]_*.sql"))
    assert _check("--strict", DATABASE_URL=TEST_URL)[0] == 2  # --strict: schema nu e completă
    import psycopg
    with psycopg.connect(TEST_URL) as c:  # scriptul n-a creat nimic
        assert c.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema=current_schema()").fetchone()[0] == 0
        c.execute("CREATE TABLE settings (key TEXT)")  # schemă parțială
        c.commit()
    rep = json.loads(_check("--json", DATABASE_URL=TEST_URL)[1])
    assert rep["schema"]["state"] == "incomplete" and "locuri_de_veci" in rep["schema"]["tables_missing"]


@pytest.mark.parametrize("url, secret", [
    ("postgresql://admin:SuperSecret123@127.0.0.1:1/dbname_x", "SuperSecret123"),
    ("host=127.0.0.1 port=1 dbname=dbname_x user=admin password=SuperSecret123", "SuperSecret123"),
    ("postgresql://127.0.0.1:1/dbname_x?user=admin&password=SuperSecret123", "SuperSecret123"),
])
def test_check_script_failure_never_prints_password_or_url(url, secret):
    for extra in ((), ("--json",)):
        code, out, err = _check("--timeout", "1", *extra, DATABASE_URL=url)
        text = out + err
        assert code == 1 and ("Conexiune" in out or '"connected": false' in out)
        for leaked in (secret, url, "dbname_x", "127.0.0.1", "admin"):
            assert leaked not in text, leaked


def test_check_script_without_database_url_fails_cleanly(tmp_path):
    code, out, err = _check(CIMITIR_ENV_FILE=str(tmp_path / "nu-exista.env"))
    assert code == 1 and "DATABASE_URL nu este setat" in out and "Traceback" not in out + err


def test_redact_removes_url_password_host_and_user():
    url = "postgresql://svc_user:p%40ss-w0rd@db.example.internal:5432/prod"
    msg = f'connection to server at "db.example.internal" failed for user "svc_user" ({url}) password=p%40ss-w0rd'
    clean = pg_compat.redact(msg, url)
    for leaked in ("p%40ss-w0rd", "db.example.internal", "svc_user", url):
        assert leaked not in clean
    assert "***" in pg_compat.mask_url("host=h password='un secret' user=u")
    assert "un secret" not in pg_compat.mask_url("host=h password='un secret' user=u")


def test_postgres_is_never_activated_automatically(tmp_path):
    """DATABASE_URL setat dar fără CIMITIR_DB_ENGINE=postgres: aplicația rămâne pe SQLite, fără să atingă PostgreSQL."""
    code = ("import sys; sys.path[:0]=[%r, %r]\n"
            "import store, cimitir_config\n"
            "assert cimitir_config.db_engine()=='sqlite' and store.process_engine()=='sqlite'\n"
            "src=store.create_sqlite_source('cimitir'); assert src['engine']=='sqlite'\n"
            "assert {s['engine'] for s in store.list_sources()}=={'sqlite'}, store.list_sources()\n"
            "store.ensure_initialized(src); store.set_setting('x','1'); assert store.get_setting('x')=='1'\n"
            "assert store.active_engine()=='sqlite'\n"
            "assert 'postgres_store' not in sys.modules and 'psycopg' not in sys.modules and 'psycopg_pool' not in sys.modules\n"
            "print('SQLITE_OK')\n") % (str(ROOT / "shared"), str(ROOT / "backend" / "cimitir"))
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                       env=_clean_env(CIMITIR_DATA_DIR=str(tmp_path), CIMITIR_ENV_FILE=str(tmp_path / "x.env"),
                                      DATABASE_URL="postgresql://u:p@127.0.0.1:1/neatins"))
    assert "SQLITE_OK" in p.stdout, p.stdout + p.stderr


def test_env_examples_keep_sqlite_as_default():
    for name in ("cimitir-local.env.example", "cimitir-online-server.env.example"):
        text = (ROOT / "config" / name).read_text(encoding="utf-8")
        assert re.search(r"^CIMITIR_DB_ENGINE=sqlite$", text, re.M) and re.search(r"^DATABASE_URL=$", text, re.M), name


# =============================================================================
# 8. ETAPA 3 — schema PostgreSQL echivalentă cu schema SQLite (tabele, coloane, tipuri, chei, FK, indexuri)
# =============================================================================
BUILD_SQLITE_SCHEMA = r'''
import sys, os
for p in ("{root}/desktop", "{root}/shared", "{root}/backend", "{root}/backend/cimitir"):
    sys.path.insert(0, p)
import cimitir_database as db
db.set_active_db_path(sys.argv[1]); db.init_db()
import cimitir_auth; cimitir_auth.init_auth()
import cimitir_notify; cimitir_notify.ensure_table()
import citizen_api; citizen_api._ensure_resets()
import qr_tracking; qr_tracking._ensure_table()
from ai_scanare import db_scans; db_scans.init_scan_history()
import cloud_sync; cloud_sync.ensure_tables()
'''


def build_sqlite_schema(path: Path) -> Path:
    """Schema SQLite COMPLETĂ, creată de codul real (inclusiv tabelele „lazy” din backend/desktop)."""
    code = BUILD_SQLITE_SCHEMA.replace("{root}", str(ROOT))
    p = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, timeout=90,
                       env=_clean_env(CIMITIR_DATA_DIR=str(path.parent), CIMITIR_ENV_FILE=str(path.parent / "x.env")))
    assert p.returncode == 0, p.stderr[-1500:]
    return path


@needs_pg
def test_postgres_schema_covers_every_sqlite_table_column_key_fk_and_index(pg, tmp_path):
    import sqlite3
    import psycopg
    sq_path = build_sqlite_schema(tmp_path / "full.db")
    s = sqlite3.connect(sq_path)
    pgc = psycopg.connect(TEST_URL, autocommit=True)
    pg_tables = {r[0] for r in pgc.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()")}
    sq_tables = [r[0] for r in s.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    assert len(sq_tables) >= 25, sq_tables
    assert set(sq_tables) <= pg_tables, set(sq_tables) - pg_tables
    ok_types = {"INTEGER": {"smallint", "integer", "bigint"}, "REAL": {"double precision"}, "TEXT": {"text", "jsonb"}}
    on_delete = {"SET NULL": "n", "CASCADE": "c", "NO ACTION": "a", "RESTRICT": "r"}
    problems = []
    for t in sq_tables:
        pg_cols = {r[0]: r for r in pgc.execute("SELECT column_name, data_type, is_nullable, column_default, is_identity "
                                                "FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s", (t,))}
        assert {"created_ts"} <= set(pg_cols), f"{t}: lipsește created_ts"
        for _, name, typ, notnull, _d, pk in s.execute(f'PRAGMA table_info("{t}")'):
            if name not in pg_cols:
                problems.append(f"{t}.{name}: coloană lipsă")
                continue
            data_type, nullable, default, ident = pg_cols[name][1:]
            if (typ or "TEXT").upper() in ok_types and data_type not in ok_types[(typ or "TEXT").upper()]:
                problems.append(f"{t}.{name}: tip {typ} -> {data_type}")
            if notnull and nullable == "YES" and not pk:
                problems.append(f"{t}.{name}: NOT NULL în SQLite, nullable în PostgreSQL")
        sq_pk = [r[1] for r in s.execute(f'PRAGMA table_info("{t}")') if r[5]]
        pg_pk = [r[0] for r in pgc.execute(
            "SELECT kcu.column_name FROM information_schema.table_constraints tc JOIN information_schema.key_column_usage kcu "
            "USING (constraint_name, table_schema, table_name) WHERE tc.table_schema=current_schema() AND tc.table_name=%s "
            "AND tc.constraint_type='PRIMARY KEY'", (t,))]
        pg_pk = [c for c in pg_pk if c not in TENANT_COLS]
        if set(sq_pk) != set(pg_pk):
            problems.append(f"{t}: cheie primară {sq_pk} != {pg_pk}")
        if sq_pk == ["id"] and pg_cols["id"][1] in ("bigint", "integer") and pg_cols["id"][4] != "YES":
            problems.append(f"{t}.id: nu este IDENTITY (ID-uri explicite + secvență)")
        # FK
        for _id, _seq, parent, frm, to, _upd, dele, _m in s.execute(f'PRAGMA foreign_key_list("{t}")'):
            row = pgc.execute(
                "SELECT c.confdeltype FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid JOIN pg_class p ON p.oid=c.confrelid "
                "JOIN pg_attribute a ON a.attrelid=r.oid AND a.attnum=ANY(c.conkey) JOIN pg_attribute b ON b.attrelid=p.oid AND b.attnum=ANY(c.confkey) "
                "WHERE c.contype='f' AND r.relname=%s AND p.relname=%s AND a.attname=%s AND b.attname=%s", (t, parent, frm, to or "id")).fetchone()
            if not row:
                problems.append(f"{t}.{frm} -> {parent}: FK lipsă")
            elif row[0] != on_delete.get(dele.upper(), "a") and not (dele.upper() == "NO ACTION"):
                problems.append(f"{t}.{frm}: ON DELETE {dele} != {row[0]}")
        # indexuri (unice și neunice): PostgreSQL trebuie să aibă un index cu aceleași coloane de început
        pg_idx = [(list(map(str, r[0])), r[1]) for r in pgc.execute(
            "SELECT array_agg(a.attname::text ORDER BY k.ord), i.indisunique FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid "
            "JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) ON true JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.attnum "
            "WHERE c.relname=%s AND i.indexprs IS NULL GROUP BY i.indexrelid, i.indisunique", (t,))]
        for _n, name, unique, _o, _p in s.execute(f'PRAGMA index_list("{t}")'):
            cols = [r[2] for r in s.execute(f'PRAGMA index_info("{name}")')]
            if unique:
                tenant_unique_lower = (cols == ["email"] and pgc.execute(    # 011: e-mail unic per organizație, ignorând majusculele
                    "SELECT count(*) FROM pg_indexes WHERE tablename=%s AND indexdef ILIKE '%%UNIQUE%%lower(%%email%%'", (t,)).fetchone()[0] > 0)
                if not tenant_unique_lower and not any(u and [x for x in c if x not in TENANT_COLS] == cols for c, u in pg_idx):
                    problems.append(f"{t}: lipsește UNIQUE pe {cols}")
            elif not any([x for x in c if x not in TENANT_COLS][:len(cols)] == cols for c, _u in pg_idx):
                problems.append(f"{t}: lipsește indexul pe {cols}")
    assert not problems, "\n".join(problems)
    # uid stabil unic pe toate entitățile sincronizabile
    for t in SYNC_ENTITIES + ["ai_scan_history", "qr_generations"]:
        assert pgc.execute("SELECT count(*) FROM pg_indexes WHERE tablename=%s AND indexdef ILIKE '%%UNIQUE%%(uid)%%'", (t,)).fetchone()[0] >= 1, t


@needs_pg
def test_migration_002_is_repeatable_and_adds_keys_and_indexes(pg):
    assert pg.schema_version() == 13 and pg.migrate() == []
    sql = (ROOT / "backend" / "cimitir" / "pg_migrations" / "002_extra_tables_fks_indexes.sql").read_text(encoding="utf-8")
    with pg._db() as c:  # rulat a doua oară direct: nu eșuează și nu duplică nimic
        c.raw.execute(sql)   # rularea manuală a unei migrări vechi peste schema finală poate re-adăuga FK-uri simple; a doua rulare nu are voie să adauge altele
        before = c.execute("SELECT count(*) FROM pg_constraint WHERE contype='f'").fetchone()[0]
        c.raw.execute(sql)
        assert c.execute("SELECT count(*) FROM pg_constraint WHERE contype='f'").fetchone()[0] == before
        assert c.execute("SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'trg_%_modified'").fetchone()[0] >= 18
    pg.save_plot_to_db("A-1", [], "#111")
    k = pg.save_contract({"cod_loc": "A-1"})
    p = pg.add_plata("A-1", 10, "2024-01-01", contract_id=k)
    assert p and pg.delete_contract(k)
    assert pg.list_plati("A-1")[0]["contract_id"] is None  # FK plati.contract_id: ON DELETE SET NULL
    assert pg.add_plata("A-1", 5, "2024-01-02", contract_id=99999) is None  # FK respinge contract inexistent


# =============================================================================
# 9. ETAPA 4 — scripts/migrate_sqlite_to_postgres.py (dry-run / execute)
# =============================================================================
MIGRATE = ROOT / "scripts" / "migrate_sqlite_to_postgres.py"
HASH_ADMIN = "pbkdf2_sha256$600000$saltSALT$AbCdEf0123456789+/=="
HASH_USER = "pbkdf2_sha256$600000$other$ZzYyXx9876543210"
WEB_SECRET = "jwt-secret-VALUE-must-not-leak"


def _sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_legacy_sqlite(tmp_path: Path, name="legacy.db") -> Path:
    """Bază SQLite „veche” cu date în toate tabelele, ID-uri neconsecutive, UID existente/lipsă, parole hash, audit, media."""
    import sqlite3
    path = build_sqlite_schema(tmp_path / name)
    media = tmp_path / "media"
    media.mkdir(exist_ok=True)
    (media / "poza.jpg").write_bytes(b"\xff\xd8JPEGDATA")
    s = sqlite3.connect(path)
    s.execute("PRAGMA foreign_keys=ON")
    ex = s.execute
    for seeded in ("users", "settings", "audit_log"):   # init_db/init_auth au pus rânduri implicite
        ex(f"DELETE FROM {seeded}")
    ex("INSERT INTO settings (key, value) VALUES ('cemetery_id','cem-test-1'), ('web_jwt_secret', ?), ('cimitir_nume','Test')", (WEB_SECRET,))
    ex("INSERT INTO concesionari (id, nume, prenume, cnp, email) VALUES (7,'Pop','Ion','1800101123456','ion@example.com'), (3,'Ene','Ana','2900202123456',NULL)")
    ex("INSERT INTO locuri_de_veci (id, cod_loc, points, color, sector, suprafata, uid) VALUES "
       "(10,'A-1','[[0,0],[4,0],[4,3]]','#111','S1',6.5,'11111111-1111-4111-8111-111111111111'), "
       "(25,'A-2','[[5,0],[9,0],[9,4]]','#222','S1',NULL,'u1'), (7,'B-1','[[1,1],[2,2],[3,1]]','#333','S2',3,NULL)")
    ex("INSERT INTO sectoare (id, points, color) VALUES ('S1','[[0,0],[10,0],[10,10]]','#aaa')")
    ex("INSERT INTO guides (id, kind, points, color) VALUES (4,'line','[[0,0],[5,5]]','#0af')")
    ex("INSERT INTO rows_ghid (id, sector_label, name, order_index, baseline, width, depth) VALUES (2,'S1','R1',0,'[[0,0],[9,0]]',2.0,1.5)")
    ex("INSERT INTO loc_extra (cod_loc, extra_json) VALUES ('A-1','{\"a\":[1,2]}')")
    ex("INSERT INTO loc_holders (cod_loc, holder_hash) VALUES ('A-1','h1'), ('A-1','h2')")
    ex("INSERT INTO contracte (id, numar_contract, data_contract, concesionar_id, id_loc, cod_loc, tomb_surface_m2, extra_data) "
       "VALUES (5,'7/2024','01.02.2024',7,10,'A-1','', '{\"x\":1}'), (9,'8/2024','03.04.2024',3,25,'A-2',4.5,'')")
    ex("INSERT INTO inhumati (id, nume, prenume, data_inhumare, id_loc, contract_id) VALUES (12,'Vasile','P','2020-01-02',10,5)")
    ex("INSERT INTO photos (id, cod_loc, file_path, caption, date_added, is_cover) VALUES (3,'A-1',?, 'c','01.01.2024 10:00',1)", (str(media / "poza.jpg"),))
    ex("INSERT INTO amenajari_avize (id, cod_loc, nr_aviz, data_aviz, lucrare, created_at) VALUES (2,'A-1','12','2024-05-05','gard','2024-05-05 10:11:12')")
    ex("INSERT INTO plati (id, cod_loc, contract_id, suma, data_plata, tip, created_at) VALUES (8,'A-1',5,100.5,'2024-01-01','taxa','2024-01-01 09:00:00')")
    ex("INSERT INTO documents (id, entity_type, entity_id, doc_type, file_path, date_added) VALUES (6,'loc','A-1','poza_loc',?,'01.01.2024 10:00'), "
       "(11,'inhumat','12','certificat',?,'02.01.2024 10:00')", (str(media / "poza.jpg"), str(media / "poza.jpg")))
    ex("INSERT INTO citizens (id, email, password_hash, full_name, created_at, active, uid) VALUES (4,'cet@example.com',?, 'Cet', '2024-03-03 10:00:00',1,NULL)", (HASH_USER,))
    ex("INSERT INTO cereri_concesionare (id, created_at, status, cod_urmarire, cod_loc, nume, citizen_id, concesionar_id, contract_id, payload) "
       "VALUES (15,'03.03.2024 10:00','nou','CR-TEST0001','A-1','Cet',4,7,5,'')")
    ex("INSERT INTO citizen_resets (id, citizen_id, token_hash, expires_at, created_at) VALUES (1,4,'th',1700000000.5,'2024-03-03 10:00:00')")
    ex("INSERT INTO users (id, username, password_hash, role, created_at, last_login, active, uid) VALUES "
       "(2,'admin',?,'admin','2024-01-01T10:00:00','2024-02-02T11:00:00',1,'22222222-2222-4222-8222-222222222222'), (9,'op',?, 'operator','2024-01-02T10:00:00',NULL,1,NULL)",
       (HASH_ADMIN, HASH_USER))
    ex("INSERT INTO audit_log (id, ts, actor_type, actor, action, target, detail) VALUES "
       "(100,'2024-01-01T10:00:00','staff','admin','login','-','ok'), (105,'2024-01-02T10:00:00','staff','admin','edit','A-1','ț mlaștină'), (101,NULL,'sys','-','x','-','')")
    ex("INSERT INTO sync_ops (op_id, kind, applied_at, result) VALUES ('op1','map','2024-01-01','ok')")
    ex("INSERT INTO email_outbox (id, cod_urmarire, template, to_email, status, created_at) VALUES (3,'CR-TEST0001','primita','cet@example.com','sent','2024-03-03')")
    ex("INSERT INTO ai_scan_history (id, created_at, document_type, status, entity_type, entity_id, extracted_json) VALUES (2,'2024-04-04 10:00:00','buletin','processed','inhumat',12,'{}')")
    ex("INSERT INTO qr_generations (id, loc_id, cod_loc, generated_at, format) VALUES (5,10,'A-1','2024-04-05 10:00:00','png')")
    ex("INSERT INTO cloud_sync_map (tracking_code, cerere_id, pushed_status) VALUES ('CR-TEST0001',15,'nou')")
    ex("INSERT INTO cloud_sync_outbox (id, tracking_code, status, created_at, op_id, kind, payload) VALUES (2,'CR-TEST0001','nou','2024-03-03','o1','status','{}')")
    ex("INSERT INTO cloud_sync_docs (tracking_code, doc_id) VALUES ('CR-TEST0001', 6)")
    s.commit()
    s.close()
    return path


def default_tenant() -> tuple:
    """(organization_uid, cemetery_uid) al tenantului creat de migrarea 009 în baza de test curentă."""
    import psycopg
    with psycopg.connect(TEST_URL) as c:
        return c.execute("SELECT organization_uid::text, uid::text FROM cemeteries ORDER BY created_at LIMIT 1").fetchone()


def new_tenant(org=None, name="Cimitir B") -> tuple:
    """Creează (în baza de test) un cimitir în organizația dată (sau într-o organizație nouă); întoarce (org, cem)."""
    import psycopg
    import uuid
    with psycopg.connect(TEST_URL, autocommit=True) as c:
        if org is None:
            org = c.execute("INSERT INTO organizations (organization_type, official_name, display_name) VALUES ('OTHER', %s, %s) RETURNING uid::text",
                            (name, name)).fetchone()[0]
        cem = str(uuid.uuid4())
        c.execute("INSERT INTO cemeteries (uid, organization_uid, name, internal_code) VALUES (%s, %s, %s, %s)", (cem, org, name, "C" + cem[:8]))
    return org, cem


def run_migrate(*args, url=None, report=None, tenant=None):
    args = list(args)
    if "--organization-uid" not in args:
        org, cem = tenant or default_tenant()
        args += ["--organization-uid", org, "--cemetery-uid", cem]
    env = _clean_env(DATABASE_URL=url or TEST_URL, CIMITIR_ENV_FILE="/nonexistent.env")
    cmd = [sys.executable, str(MIGRATE), *args] + (["--report", str(report)] if report else [])
    p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout, p.stderr


def pg_count(table: str) -> int:
    import psycopg
    with psycopg.connect(TEST_URL) as c:
        return c.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]


def pg_db_name() -> str:
    import psycopg
    with psycopg.connect(TEST_URL) as c:
        return c.execute("SELECT current_database()").fetchone()[0]


@needs_pg
def test_dry_run_reports_everything_writes_nothing_and_leaks_nothing(pg, tmp_path):
    src = make_legacy_sqlite(tmp_path)
    before, report = _sha(src), tmp_path / "rep.md"
    code, out, err = run_migrate("--source", str(src), "--dry-run", report=report)
    assert code == 0, out + err
    text = report.read_text(encoding="utf-8")
    for heading in ("DRY-RUN", "Inventar tabele", "ID-uri", "UID-uri", "Chei străine", "Duplicate", "Relații lipsă", "Poligoane",
                    "Utilizatori și parole", "audit_log", "Fișiere media", "Neschimbat după rulare:** DA"):
        assert heading in text, heading
    assert "| locuri_de_veci | 3 | 3 | 0 | 3 (estimat) |" in text and "| audit_log | 3 | 3 | 0 | 3 (estimat) |" in text and "ID max" in text and "Tenant țintă" in text
    assert "pbkdf2: 2" in text or "'pbkdf2': 2" in text
    for secret in (HASH_ADMIN, HASH_USER, WEB_SECRET, "1800101123456", "ion@example.com", "cet@example.com", "Vasile", TEST_URL):
        assert secret not in text + out + err, "scurgere în raport/ieșire"
    assert _sha(src) == before                                  # SQLite neatins
    assert all(pg_count(t) == 0 for t in ("locuri_de_veci", "users", "audit_log"))
    assert pg_count("settings") == 1                           # doar cemetery_id auto-generat; nimic scris de migrare


def test_dry_run_works_without_postgres_url(tmp_path):
    src = make_legacy_sqlite(tmp_path) if TEST_URL else None
    if src is None:
        pytest.skip("nu pot construi schema SQLite completă fără mediu complet")
    code, out, err = subprocess.run([sys.executable, str(MIGRATE), "--source", str(src), "--dry-run", "--report", str(tmp_path / "r.md"),
                                     "--organization-uid", "11111111-1111-4111-8111-aaaaaaaaaaaa", "--cemetery-uid", "22222222-2222-4222-8222-bbbbbbbbbbbb"],
                                    env=_clean_env(CIMITIR_ENV_FILE="/nonexistent.env"), capture_output=True, text=True, timeout=60).returncode, "", ""
    assert code == 0 and "omise" in (tmp_path / "r.md").read_text(encoding="utf-8")


@needs_pg
def test_modes_are_mutually_exclusive_and_required(pg, tmp_path):
    src = make_legacy_sqlite(tmp_path)
    assert run_migrate("--source", str(src), report=tmp_path / "implicit.md")[0] == 0     # fără mod = dry-run
    assert "DRY-RUN" in (tmp_path / "implicit.md").read_text(encoding="utf-8") and pg_count("locuri_de_veci") == 0
    assert run_migrate("--source", str(src), "--dry-run", "--apply")[0] == 2    # ambele
    code = subprocess.run([sys.executable, str(MIGRATE), "--source", str(src)], env=_clean_env(CIMITIR_ENV_FILE="/nonexistent.env"),
                          capture_output=True, text=True, timeout=60).returncode
    assert code == 2                                                              # lipsesc --organization-uid / --cemetery-uid
    assert run_migrate("--source", str(tmp_path / "nu-exista.db"), "--dry-run")[0] == 1
    assert run_migrate("--source", str(src), "--apply", "--yes")[0] == 2        # --yes cere --confirm-db


@needs_pg
def test_dry_run_detects_orphans_duplicates_invalid_polygons_and_missing_links(pg, tmp_path):
    import sqlite3
    src = make_legacy_sqlite(tmp_path)
    s = sqlite3.connect(src)
    s.execute("INSERT INTO contracte (id, numar_contract, concesionar_id, id_loc, cod_loc) VALUES (20,'x/1',999,10,'A-1')")         # concesionar orfan
    s.execute("INSERT INTO contracte (id, numar_contract, concesionar_id, id_loc, cod_loc) VALUES (21,'x/2',NULL,NULL,'A-1')")      # fără concesionar și loc
    s.execute("INSERT INTO inhumati (id, nume, id_loc) VALUES (30,'Fara',NULL), (31,'Orfan',888)")                                 # fără loc / loc orfan
    s.execute("INSERT INTO documents (id, entity_type, entity_id, file_path) VALUES (40,'inhumat','777','/x'), (41,'ciudat','1','/x')")
    s.execute("INSERT INTO locuri_de_veci (id, cod_loc, points, uid) VALUES (50,'C-1','[[0,0],[1]]',NULL), (51,'C-2','nu e json',NULL), (52,'C-3','',NULL)")
    s.execute("INSERT INTO users (id, username, password_hash, role, created_at, uid) VALUES (70,'dup1','h','user','2024','33333333-3333-4333-8333-333333333333'), "
              "(71,'dup2','h','user','2024','33333333-3333-4333-8333-333333333333')")      # același UID la doi utilizatori
    s.execute("INSERT INTO photos (id, cod_loc, file_path) VALUES (60,'NU-EXISTA','/x')")
    s.commit()
    s.close()
    code, out, _ = run_migrate("--source", str(src), "--dry-run", report=tmp_path / "rep.md")
    text = (tmp_path / "rep.md").read_text(encoding="utf-8")
    assert code == 3 and "BLOCAT" in text
    for needle in ("orphan_fk", "duplicate", "invalid_polygon", "contract_no_concesionar", "contract_no_loc", "burial_no_loc",
                   "document_no_entity", "document_unknown_type", "no_polygon", "ID: 20", "ID: 30", "ID: 50"):
        assert needle in text, needle
    assert "contracte.concesionar_id -> concesionari.id | 6 | 1 | 1 |" in text.replace("  ", " ") or "| 1 |" in text
    assert pg_count("locuri_de_veci") == 0


@needs_pg
def test_execute_preserves_ids_uids_hashes_relations_dates_audit_and_never_touches_source_or_media(pg, tmp_path):
    import psycopg
    import uuid
    src = make_legacy_sqlite(tmp_path)
    media = tmp_path / "media" / "poza.jpg"
    before, media_before = _sha(src), (media.read_bytes(), media.stat().st_mtime_ns)
    code, out, err = run_migrate("--source", str(src), "--apply", "--yes", "--confirm-db", pg_db_name(), report=tmp_path / "rep.md")
    assert code == 0, out + err
    text = (tmp_path / "rep.md").read_text(encoding="utf-8")
    assert "MIGRARE REUȘITĂ" in text and "COMMIT" in text and "Checksum parole personal: **OK**" in text and "Checksum audit: **OK**" in text
    with psycopg.connect(TEST_URL) as c:
        q = lambda sql, *a: c.execute(sql, a).fetchall()  # noqa: E731
        assert q("SELECT id FROM locuri_de_veci ORDER BY id") == [(7,), (10,), (25,)]           # ID-uri păstrate (neconsecutive)
        assert q("SELECT id FROM concesionari ORDER BY id") == [(3,), (7,)] and q("SELECT id FROM audit_log ORDER BY id") == [(100,), (101,), (105,)]
        uids = dict(q("SELECT cod_loc, uid FROM locuri_de_veci"))
        assert uids["A-1"] == "11111111-1111-4111-8111-111111111111" and uids["A-2"] == "u1"      # UID existente păstrate (inclusiv ne-UUID)
        assert uids["B-1"] == str(uuid.uuid5(uuid.UUID("6f1c0c3e-5d36-4c1e-9a55-2d5b8a0c7e11"), f"{default_tenant()[1]}:locuri_de_veci:7"))  # derivat determinist din cimitirul țintă
        assert dict(q("SELECT username, password_hash FROM users")) == {"admin": HASH_ADMIN, "op": HASH_USER}   # hash-uri identice
        assert q("SELECT password_hash FROM citizens") == [(HASH_USER,)]
        assert q("SELECT ts, actor, detail FROM audit_log ORDER BY id") == [("2024-01-01T10:00:00", "admin", "ok"), (None, "-", ""), ("2024-01-02T10:00:00", "admin", "ț mlaștină")]
        assert q("SELECT created_at, last_login FROM users WHERE id=2") == [("2024-01-01T10:00:00", "2024-02-02T11:00:00")]  # data/ora text original
        assert q("SELECT date_added FROM photos") == [("01.01.2024 10:00",)] and q("SELECT created_at FROM cereri_concesionare") == [("03.03.2024 10:00",)]
        # relații
        assert q("SELECT c.id, k.nume, l.cod_loc FROM contracte c JOIN concesionari k ON k.id=c.concesionar_id JOIN locuri_de_veci l ON l.id=c.id_loc ORDER BY c.id") \
            == [(5, "Pop", "A-1"), (9, "Ene", "A-2")]
        assert q("SELECT i.id FROM inhumati i JOIN contracte c ON c.id=i.contract_id JOIN locuri_de_veci l ON l.id=i.id_loc") == [(12,)]
        assert q("SELECT p.id FROM plati p JOIN contracte c ON c.id=p.contract_id") == [(8,)] and q("SELECT count(*) FROM cloud_sync_docs") == [(0,)]   # starea de sincronizare MAIN nu se importă
        # JSONB și conversii
        assert q("SELECT points FROM locuri_de_veci WHERE id=10") == [([[0, 0], [4, 0], [4, 3]],)] and q("SELECT jsonb_typeof(points) FROM sectoare") == [("array",)]
        assert q("SELECT tomb_surface_m2 FROM contracte ORDER BY id") == [(None,), (4.5,)]    # '' -> NULL
        assert q("SELECT value FROM settings WHERE key='web_jwt_secret'") == [(WEB_SECRET,)]  # setări păstrate (nu apar în raport)
        org, cem = default_tenant()
        assert q("SELECT value FROM settings WHERE key='cemetery_id'") == [(cem,)]             # settings.cemetery_id aliniat cu cemeteries.uid
        for t in ("locuri_de_veci", "concesionari", "contracte", "photos", "audit_log"):
            assert q(f"SELECT DISTINCT organization_uid::text, cemetery_uid::text FROM {t}") == [(org, cem)], t   # tenant pe fiecare rând
        assert q("SELECT DISTINCT organization_uid::text FROM citizens") == [(org,)]
        # secvențe: următorul ID continuă după MAX
        assert q("SELECT nextval(pg_get_serial_sequence('locuri_de_veci','id'))") == [(26,)]
        assert q("SELECT nextval(pg_get_serial_sequence('audit_log','id'))") == [(106,)]
    import postgres_store
    pg_compat.close_pool()
    postgres_store._initialized.clear()
    postgres_store.init_db()                                                                  # repornirea aplicației: tenantul activ = settings.cemetery_id (aliniat de import)
    assert postgres_store.save_concesionar({"nume": "Nou"}) == 8                              # aplicația continuă fără coliziuni
    assert _sha(src) == before and (media.read_bytes(), media.stat().st_mtime_ns) == media_before  # SQLite și media neatinse
    assert "Neschimbat după rulare:** DA" in text


@needs_pg
def test_execute_refuses_non_empty_target_and_truncate_target_replaces(pg, tmp_path):
    import psycopg
    src = make_legacy_sqlite(tmp_path)
    with psycopg.connect(TEST_URL, autocommit=True) as c:
        org, cem = default_tenant()
        c.execute("INSERT INTO concesionari (nume, organization_uid, cemetery_uid) VALUES ('Existent', %s, %s)", (org, cem))
    args = ("--source", str(src), "--apply", "--yes", "--confirm-db", pg_db_name())
    code, _, err = run_migrate(*args)
    assert code == 1 and "are deja date" in err and pg_count("concesionari") == 1 and pg_count("locuri_de_veci") == 0
    assert run_migrate(*args, "--truncate-target")[0] == 0
    assert pg_count("concesionari") == 2 and pg_count("locuri_de_veci") == 3
    assert run_migrate("--source", str(src), "--apply", "--yes", "--confirm-db", "alta_baza", "--truncate-target")[0] == 1  # confirmare greșită


@needs_pg
def test_execute_blocked_by_problems_writes_nothing_but_fix_mode_migrates_and_logs_every_fix(pg, tmp_path):
    import sqlite3
    import psycopg
    src = make_legacy_sqlite(tmp_path)
    s = sqlite3.connect(src)
    s.execute("INSERT INTO contracte (id, numar_contract, concesionar_id, id_loc, cod_loc) VALUES (20,'x/1',999,10,'A-1')")
    s.execute("INSERT INTO photos (id, cod_loc, file_path) VALUES (60,'NU-EXISTA','/x')")
    s.execute("INSERT INTO locuri_de_veci (id, cod_loc, points) VALUES (51,'C-2','nu e json')")
    s.commit()
    s.close()
    args = ("--source", str(src), "--apply", "--yes", "--confirm-db", pg_db_name())
    assert run_migrate(*args, report=tmp_path / "r1.md")[0] == 3 and pg_count("contracte") == 0 and pg_count("users") == 0
    assert "nu s-a scris nimic" in (tmp_path / "r1.md").read_text(encoding="utf-8")
    assert run_migrate(*args, "--on-problem", "fix", report=tmp_path / "r2.md")[0] == 0
    text = (tmp_path / "r2.md").read_text(encoding="utf-8")
    for needle in ("referință orfană către concesionari -> NULL", "rând sărit: părinte inexistent în locuri_de_veci", "poligon invalid -> '[]'"):
        assert needle in text, needle
    with psycopg.connect(TEST_URL) as c:
        assert c.execute("SELECT concesionar_id FROM contracte WHERE id=20").fetchone() == (None,)
        assert c.execute("SELECT count(*) FROM photos WHERE id=60").fetchone() == (0,) and c.execute("SELECT count(*) FROM photos").fetchone() == (1,)
        assert c.execute("SELECT points FROM locuri_de_veci WHERE id=51").fetchone() == ([],)


@needs_pg
def test_failed_verification_rolls_back_everything(pg, tmp_path, monkeypatch):
    import importlib.util
    src = make_legacy_sqlite(tmp_path)
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    monkeypatch.setenv("CIMITIR_ENV_FILE", "/nonexistent.env")
    spec = importlib.util.spec_from_file_location("migrate_mod", MIGRATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    calls = {"n": 0}
    real = mod.table_checksum

    def flaky(rows, cols, order):  # a doua tabelă verificată dă alt checksum => diferență simulată
        calls["n"] += 1
        return real(rows, cols, order) + ("x" if calls["n"] == 6 else "")
    monkeypatch.setattr(mod, "table_checksum", flaky)
    before = _sha(src)
    org, cem = default_tenant()
    code = mod.main(["--source", str(src), "--apply", "--yes", "--confirm-db", pg_db_name(), "--report", str(tmp_path / "rep.md"),
                     "--organization-uid", org, "--cemetery-uid", cem])
    assert code == 4 and "ROLLBACK" in (tmp_path / "rep.md").read_text(encoding="utf-8")
    assert all(pg_count(t) == 0 for t in ("locuri_de_veci", "users", "audit_log", "concesionari"))
    assert pg_count("settings") == 1                                          # cemetery_id auto-generat a rămas (ROLLBACK)
    assert _sha(src) == before


@needs_pg
def test_execute_twice_is_deterministic_for_derived_uids(pg, tmp_path):
    import psycopg
    src = make_legacy_sqlite(tmp_path)
    args = ("--source", str(src), "--apply", "--yes", "--confirm-db", pg_db_name(), "--truncate-target")
    snapshots = []
    for _ in range(2):
        code, out, err = run_migrate(*args)
        assert code == 0, out + err
        with psycopg.connect(TEST_URL) as c:
            snapshots.append((c.execute("SELECT uid FROM locuri_de_veci ORDER BY id").fetchall(),
                              c.execute("SELECT uid FROM concesionari ORDER BY id").fetchall(),
                              c.execute("SELECT uid FROM users ORDER BY id").fetchall()))
    assert snapshots[0] == snapshots[1]


@needs_pg
def test_migration_script_tables_fks_and_uid_tables_match_the_real_schema(pg):
    """Dacă schema PostgreSQL se schimbă, scriptul de migrare trebuie actualizat: această verificare îl ține sincronizat."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("migrate_mod_sync", MIGRATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with pg._db() as c:
        tables = {r[0] for r in c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()")} - {"schema_migrations"}
        tables -= TENANT_TABLES | {"mobile_upload_tokens"}   # tokenii de upload sunt temporari, doar pe server (nu există în SQLite); scriptul one-off SQLite->PostgreSQL nu copiază tabelele multi-tenant (nu există în SQLite)
        assert set(mod.TABLE_ORDER) == tables and len(mod.TABLE_ORDER) == len(tables)
        with_uid = {r[0] for r in c.execute("SELECT table_name FROM information_schema.columns WHERE table_schema=current_schema() AND column_name='uid'")}
        assert mod.UID_TABLES == with_uid - TENANT_TABLES
        fks = set()
        for child, parent, ccols, pcols in c.raw.execute(
                "SELECT r.relname, p.relname, "
                "(SELECT array_agg(a.attname ORDER BY u.ord) FROM unnest(k.conkey) WITH ORDINALITY u(attnum, ord) JOIN pg_attribute a ON a.attrelid=r.oid AND a.attnum=u.attnum), "
                "(SELECT array_agg(b.attname ORDER BY u.ord) FROM unnest(k.confkey) WITH ORDINALITY u(attnum, ord) JOIN pg_attribute b ON b.attrelid=p.oid AND b.attnum=u.attnum) "
                "FROM pg_constraint k JOIN pg_class r ON r.oid=k.conrelid JOIN pg_class p ON p.oid=k.confrelid "
                "WHERE k.contype='f' AND r.relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = current_schema())"):
            if child in TENANT_TABLES | {"mobile_upload_tokens"} or parent in TENANT_TABLES:
                continue
            pairs = [(cc, pc) for cc, pc in zip(ccols, pcols) if cc not in TENANT_COLS]      # 011/012: cheile sunt compuse (cemetery_uid, id) — logic rămâne FK-ul simplu
            if len(pairs) == 1:
                fks.add((child, pairs[0][0], parent, pairs[0][1]))
        assert fks == {(ch, col, par, pc) for ch, col, par, pc, _n in mod.LOGICAL_FKS}
        order = {t: i for i, t in enumerate(mod.TABLE_ORDER)}
        assert all(order[par] < order[ch] for ch, _c, par, _p, _n in mod.LOGICAL_FKS)   # părinții se încarcă primii
        json_cols = {(r[0], r[1]) for r in c.execute("SELECT table_name, column_name FROM information_schema.columns "
                                                     "WHERE table_schema=current_schema() AND data_type='jsonb'")}
        assert {x for x in json_cols if x[0] not in TENANT_TABLES} == mod.JSON_COLUMNS


@needs_pg
def test_import_is_limited_to_one_tenant_and_never_touches_another_cemetery(pg, tmp_path):
    import psycopg
    src = make_legacy_sqlite(tmp_path)
    org, cem_a = default_tenant()
    _, cem_b = new_tenant(org, "Cimitir B")
    with psycopg.connect(TEST_URL, autocommit=True) as c:
        c.execute("INSERT INTO concesionari (id, nume, organization_uid, cemetery_uid) VALUES (900, 'DinB', %s, %s)", (org, cem_b))
        c.execute("INSERT INTO locuri_de_veci (id, cod_loc, points, color, organization_uid, cemetery_uid) VALUES (901, 'B-9', '[[0,0],[1,0],[1,1]]', '#000', %s, %s)", (org, cem_b))
        snap_b = (c.execute("SELECT id, nume FROM concesionari WHERE cemetery_uid=%s", (cem_b,)).fetchall(),
                  c.execute("SELECT id, cod_loc FROM locuri_de_veci WHERE cemetery_uid=%s", (cem_b,)).fetchall())
    args = ("--source", str(src), "--apply", "--yes", "--confirm-db", pg_db_name())
    code, out, err = run_migrate(*args, "--truncate-target", report=tmp_path / "rep.md", tenant=(org, cem_a))
    assert code == 0, out + err
    text = (tmp_path / "rep.md").read_text(encoding="utf-8")
    assert "Alte cimitiruri neatinse: DA" in text and "Înainte" in text
    with psycopg.connect(TEST_URL) as c:
        assert (c.execute("SELECT id, nume FROM concesionari WHERE cemetery_uid=%s", (cem_b,)).fetchall(),
                c.execute("SELECT id, cod_loc FROM locuri_de_veci WHERE cemetery_uid=%s", (cem_b,)).fetchall()) == snap_b   # B neatins
        assert c.execute("SELECT count(*) FROM locuri_de_veci WHERE cemetery_uid=%s", (cem_a,)).fetchone()[0] == 3
        assert c.execute("SELECT count(*) FROM locuri_de_veci WHERE cemetery_uid IS DISTINCT FROM %s AND cemetery_uid IS DISTINCT FROM %s", (cem_a, cem_b)).fetchone()[0] == 0
    # re-import cu --truncate-target în A nu șterge B
    code, out, err = run_migrate(*args, "--truncate-target", tenant=(org, cem_a))
    assert code == 0, out + err
    with psycopg.connect(TEST_URL) as c:
        assert c.execute("SELECT count(*) FROM concesionari WHERE cemetery_uid=%s", (cem_b,)).fetchone()[0] == 1


@needs_pg
def test_import_refuses_unknown_wrong_org_or_non_empty_tenants_and_active_tenant_shift(pg, tmp_path):
    import uuid
    import psycopg
    src = make_legacy_sqlite(tmp_path)
    org, cem_a = default_tenant()
    args = ("--source", str(src), "--apply", "--yes", "--confirm-db", pg_db_name())
    assert run_migrate(*args, tenant=(org, str(uuid.uuid4())))[0] == 1                      # cimitir inexistent
    assert run_migrate(*args, tenant=(str(uuid.uuid4()), cem_a))[0] == 1                    # organizație inexistentă
    other_org, other_cem = new_tenant(None, "Altă organizație")
    code, _, err = run_migrate(*args, tenant=(org, other_cem))                              # cimitirul aparține altei organizații
    assert code == 1 and "altei organizații" in err
    assert run_migrate("--source", str(src), "--dry-run", tenant=(org, other_cem), report=tmp_path / "d.md")[0] == 3   # dry-run: BLOCAT, nimic scris
    assert pg_count("locuri_de_veci") == 0 and pg_count("users") == 0
    assert run_migrate("--source", str(src), "--organization-uid", "nu-uuid", "--cemetery-uid", cem_a)[0] == 2
    # settings.cemetery_id indică A, A are date, importul în B ar muta tenantul activ -> refuzat
    assert run_migrate(*args, tenant=(org, cem_a))[0] == 0
    org_b, cem_b = new_tenant(org, "B")
    code, _, err = run_migrate(*args, tenant=(org, cem_b))
    assert code == 1 and ("tenantul activ" in err or "deja pline" in err), err
    with psycopg.connect(TEST_URL) as c:
        assert c.execute("SELECT value FROM settings WHERE key='cemetery_id'").fetchone()[0] == cem_a
        assert c.execute("SELECT count(*) FROM locuri_de_veci WHERE cemetery_uid=%s", (cem_b,)).fetchone()[0] == 0


def test_migrate_script_blocks_production_project(tmp_path):
    prod = "postgresql://postgres.txuxkjgftopznvmooexq:x@aws-0-eu-west-1.pooler.supabase.com:5432/postgres?sslmode=require"
    src = tmp_path / "x.db"
    import sqlite3
    sqlite3.connect(src).close()
    for mode in ("--dry-run", "--apply"):
        p = subprocess.run([sys.executable, str(MIGRATE), "--source", str(src), mode, "--organization-uid", "11111111-1111-4111-8111-111111111111",
                            "--cemetery-uid", "22222222-2222-4222-8222-222222222222", "--report", str(tmp_path / "r.md")],
                           env=_clean_env(DATABASE_URL=prod, CIMITIR_ENV_FILE="/nonexistent.env"), capture_output=True, text=True, timeout=60)
        assert p.returncode == 1 and "producție" in p.stderr and "txuxkjgftopznvmooexq" not in p.stdout + p.stderr
