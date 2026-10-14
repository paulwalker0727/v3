"""WORK: `executant` și `data_inceput` sunt câmpuri native în amenajari_avize (SQLite local)."""
from __future__ import annotations

import os
import sqlite3

from test_field_import import add, loc, rows  # noqa: F401,I001
from test_field_inbox_main import client, env, make_profile, qapp  # noqa: F401,I001
import database as db  # noqa: E402
import field_import as fim  # noqa: E402

LEGACY_DDL = """
CREATE TABLE locuri_de_veci (id INTEGER PRIMARY KEY AUTOINCREMENT, cod_loc TEXT UNIQUE NOT NULL);
CREATE TABLE amenajari_avize (id INTEGER PRIMARY KEY AUTOINCREMENT, cod_loc TEXT NOT NULL, nr_aviz TEXT NOT NULL, data_aviz TEXT,
    created_at TEXT DEFAULT (datetime('now')), lucrare TEXT, stare TEXT, observatii TEXT, data_finalizare TEXT);
"""


def _cols(table="amenajari_avize"):
    conn = db.get_connection()
    try:
        return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    finally:
        conn.close()


def _make_loc(cod="L-1"):
    conn = db.get_connection()
    conn.execute("INSERT INTO locuri_de_veci (cod_loc) VALUES (?)", (cod,))
    conn.close()
    return cod


# ------------------------------------------------------------------ migrare SQLite
def test_existing_db_gets_columns_without_data_loss(env):
    path = db.current_db_path()
    conn = sqlite3.connect(path)
    conn.executescript("DROP TABLE amenajari_avize; DROP TABLE locuri_de_veci;" + LEGACY_DDL)
    conn.execute("INSERT INTO locuri_de_veci (cod_loc) VALUES ('OLD-1')")
    conn.execute("INSERT INTO amenajari_avize (cod_loc, nr_aviz, data_aviz, lucrare, stare, observatii, data_finalizare) "
                 "VALUES ('OLD-1','7/2020','01.02.2020','Cavou','finalizata','Executant: SC Vechi','05.02.2020')")
    conn.commit()
    conn.close()
    assert "executant" not in _cols()
    db.init_db()
    db.init_db()                                                                    # idempotent
    assert "executant" in _cols() and "data_inceput" in _cols()
    r = rows("SELECT * FROM amenajari_avize")
    assert len(r) == 1
    assert (r[0]["nr_aviz"], r[0]["data_aviz"], r[0]["lucrare"], r[0]["stare"], r[0]["observatii"], r[0]["data_finalizare"]) == (
        "7/2020", "01.02.2020", "Cavou", "finalizata", "Executant: SC Vechi", "05.02.2020")
    assert r[0]["executant"] is None and r[0]["data_inceput"] is None               # nu se extrage nimic din observații
    old = db.list_avize_for_loc("OLD-1")[0]
    assert old["executant"] == "" and old["data_inceput"] == "" and old["observatii"] == "Executant: SC Vechi" and old["data"] == "01.02.2020"


def test_fresh_db_has_native_columns(env):
    assert {"executant", "data_inceput"} <= set(_cols())


# ------------------------------------------------------------------ CRUD
def test_create_edit_and_read_after_restart(env):
    cod = _make_loc()
    aid = db.add_aviz(cod, "1/2026", "02.03.2026", "Placare", "SC Marmura", "05.03.2026")
    a = db.get_aviz(aid)
    assert (a["executant"], a["data_inceput"], a["data"], a["observatii"]) == ("SC Marmura", "05.03.2026", "02.03.2026", "")
    assert db.update_aviz(aid, {"executant": "SC Nou", "data_inceput": "10.03.2026"})
    db.init_db()                                                                    # „restart": re-deschidere + migrare
    a = db.list_avize_for_loc(cod)[0]
    assert (a["executant"], a["data_inceput"], a["data"]) == ("SC Nou", "10.03.2026", "02.03.2026")


def test_empty_values_stay_empty(env):
    cod = _make_loc()
    aid = db.add_aviz(cod, "2/2026", "", "Curățare")
    r = rows("SELECT executant, data_inceput FROM amenajari_avize WHERE id=?", aid)[0]
    assert r["executant"] is None and r["data_inceput"] is None
    a = db.get_aviz(aid)
    assert a["executant"] == "" and a["data_inceput"] == ""
    aid2 = db.add_aviz(cod, "3/2026", "", "x", "  ", "")
    assert rows("SELECT executant, data_inceput FROM amenajari_avize WHERE id=?", aid2)[0] == {"executant": None, "data_inceput": None}
    db.update_aviz(aid, {"executant": "X", "data_inceput": "01.01.2026"})
    db.update_aviz(aid, {"executant": "", "data_inceput": None})
    a = db.get_aviz(aid)
    assert a["executant"] == "" and a["data_inceput"] == ""


def test_update_other_fields_does_not_touch_native_fields(env):
    cod = _make_loc()
    aid = db.add_aviz(cod, "4/2026", "", "L", "SC A", "01.01.2026")
    db.update_aviz(aid, {"observatii": "nota", "stare": "in_lucru"})
    a = db.get_aviz(aid)
    assert (a["executant"], a["data_inceput"], a["observatii"]) == ("SC A", "01.01.2026", "nota")


# ------------------------------------------------------------------ Field Inbox WORK
def test_work_import_uses_native_fields_not_observatii_or_data_aviz(env, loc):
    uid = add(env, loc, "WORK", payload={"descriere": "Placare", "data_inceput": "01.05.2026", "executant": "SC Marmura"})
    fim.import_item(client(env), uid)
    r = rows("SELECT * FROM amenajari_avize WHERE uid=?", uid)[0]
    assert (r["executant"], r["data_inceput"]) == ("SC Marmura", "01.05.2026")
    assert r["observatii"] == "" and r["data_aviz"] == ""
    assert "Executant" not in r["observatii"] and "SC Marmura" not in r["observatii"]
    a = db.get_aviz(r["id"])
    assert a["executant"] == "SC Marmura" and a["data_inceput"] == "01.05.2026" and a["data"] == ""


def test_work_import_keeps_real_observatii_and_data_aviz_untouched(env, loc):
    uid = add(env, loc, "WORK", payload={"descriere": "L", "observatii": "doar nota", "data_inceput": "01.05.2026", "executant": "E"})
    fim.import_item(client(env), uid)
    r = rows("SELECT * FROM amenajari_avize WHERE uid=?", uid)[0]
    assert r["observatii"] == "doar nota" and r["data_aviz"] == ""


def test_work_import_without_native_fields_leaves_them_empty(env, loc):
    uid = add(env, loc, "WORK", payload={"descriere": "Curățare", "observatii": "n"})
    fim.import_item(client(env), uid)
    r = rows("SELECT * FROM amenajari_avize WHERE uid=?", uid)[0]
    assert r["executant"] == "" and r["data_inceput"] == "" and r["observatii"] == "n"


def test_permit_import_does_not_fill_native_work_fields(env, loc):
    uid = add(env, loc, "PERMIT", payload={"nr_aviz": "9/2026", "data_aviz": "02.01.2026", "lucrare": "Cavou"})
    fim.import_item(client(env), uid)
    r = rows("SELECT * FROM amenajari_avize WHERE uid=?", uid)[0]
    assert r["data_aviz"] == "02.01.2026" and not r["executant"] and not r["data_inceput"]


def test_work_import_retry_creates_no_duplicates(env, loc):
    uid = add(env, loc, "WORK", payload={"descriere": "P", "data_inceput": "01.05.2026", "executant": "SC M"})
    c = client(env)
    r1, r2, r3 = fim.import_item(c, uid), fim.import_item(c, uid), fim.import_item(c, uid)
    assert r1.created and not r2.created and not r3.created
    rr = rows("SELECT * FROM amenajari_avize WHERE uid=?", uid)
    assert len(rr) == 1 and rr[0]["executant"] == "SC M" and rr[0]["data_inceput"] == "01.05.2026"


def test_existing_identical_work_stays_idempotent_and_different_conflicts(env, loc):
    uid = add(env, loc, "WORK", payload={"descriere": "P", "data_inceput": "01.05.2026", "executant": "SC M"})
    conn = db.get_connection()
    fim.ensure_tables()
    conn.execute("INSERT INTO amenajari_avize (cod_loc, nr_aviz, data_aviz, lucrare, stare, observatii, data_finalizare, executant, data_inceput, uid) "
                 "VALUES ('G-12','','','P','','','','SC M','01.05.2026',?)", (uid,))
    conn.close()
    res = fim.import_item(client(env), uid)
    assert not res.created and res.state == fim.COMPLETE
    assert len(rows("SELECT * FROM amenajari_avize WHERE uid=?", uid)) == 1

    uid2 = add(env, loc, "WORK", payload={"descriere": "P", "data_inceput": "01.05.2026", "executant": "ALTCINEVA"})
    conn = db.get_connection()
    conn.execute("INSERT INTO amenajari_avize (cod_loc, nr_aviz, data_aviz, lucrare, stare, observatii, data_finalizare, executant, data_inceput, uid) "
                 "VALUES ('G-12','','','P','','','','SC M','01.05.2026',?)", (uid2,))
    conn.close()
    try:
        fim.import_item(client(env), uid2)
        raise AssertionError("trebuia conflict")
    except fim.ImportConflict as exc:
        assert "executant" in exc.fields if hasattr(exc, "fields") else "executant" in str(exc)
    assert rows("SELECT executant FROM amenajari_avize WHERE uid=?", uid2)[0]["executant"] == "SC M"


def test_cloud_migrations_untouched():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    mig = sorted(os.listdir(os.path.join(root, "backend", "cimitir", "pg_migrations")))
    assert not any(m.startswith("020") for m in mig)


# ------------------------------------------------------------------ MAIN UI
def test_add_dialog_empty_start_date_stays_empty_and_values_roundtrip(qapp):
    from panels import AddAvizDialog
    d = AddAvizDialog()
    d.txt_nr.setText("5/2026")
    d._on_ok()
    r = d.get_result()
    assert r["executant"] == "" and r["data_inceput"] == ""
    d2 = AddAvizDialog(existing={"nr": "5/2026", "executant": "SC X", "data_inceput": "03.04.2026"})
    d2._on_ok()
    r2 = d2.get_result()
    assert r2["executant"] == "SC X" and r2["data_inceput"] == "03.04.2026"


def test_history_details_show_native_work_fields(env, loc):
    import field_import_history as fih
    uid = add(env, loc, "WORK", payload={"descriere": "P", "data_inceput": "01.05.2026", "executant": "SC M"})
    fim.import_item(client(env), uid)
    d = fih.local_details(uid)
    flat = str(d)
    assert "SC M" in flat and "01.05.2026" in flat
