"""F-01 — MAIN (SQLite izolat, date sintetice): dialogul contractelor nu inventează/pierde date, iar conversiile MAIN<->fir păstrează istoricul.

Fiecare caz rulează într-un proces separat cu folder de date propriu (nu atinge nicio bază reală)."""
import json
import os
import subprocess
import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]
SHARED = DESKTOP.parent / "shared"

PRE = r'''
import json, os, sys
sys.path.insert(0, {desktop!r}); sys.path.insert(0, {shared!r})
os.chdir({desktop!r})
import database as db
db.init_db()
out = {{}}
'''
POST = "\nprint('RESULT ' + json.dumps(out, default=str))\n"


def run(tmp_path, body, qt=False):
    code = PRE.format(desktop=str(DESKTOP), shared=str(SHARED)) + (
        "from PySide6.QtWidgets import QApplication, QMessageBox\napp = QApplication([])\n" if qt else "") + body + POST
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_")}
    env.update(CIMITIR_DATA_DIR=str(tmp_path / "data"), CIMITIR_ENV_FILE=str(tmp_path / "nu.env"), CIMITIR_LOG_DIR=str(tmp_path / "logs"),
               CIMITIR_DB_ENGINE="sqlite", CIMITIR_MODE="local", DATABASE_URL="", QT_QPA_PLATFORM="offscreen")
    p = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(DESKTOP), capture_output=True, text=True, timeout=150)
    assert p.returncode == 0, p.stderr[-3000:]
    return json.loads([ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:])


SEED_CONTRACTS = r'''
db.save_plot_to_db("T-1", [(0, 0), (40, 0), (40, 30), (0, 30)], "#111")
cid = db.save_concesionar({"nume": "Test", "prenume": "Sintetic"})
def mk(nr, c, i, s):
    return db.save_contract({"numar_contract": nr, "data_contract": c, "data_inceput": i, "data_sfarsit": s, "cod_loc": "T-1",
                             "concesionar_id": cid, "observatii": "", "extra_data": None, "owner_fullname": "Test Sintetic"})
'''

DIALOG = r'''
import contracts_window as cw
def reopen_save(contract_id):
    dlg = cw.AddContractDialog(None, existing=db.get_connection().execute("SELECT * FROM contracte WHERE id=?", (contract_id,)).fetchone() and dict(db.get_connection().execute("SELECT * FROM contracte WHERE id=?", (contract_id,)).fetchone()))
    QMessageBox.information = staticmethod(lambda *a, **k: None); QMessageBox.warning = staticmethod(lambda *a, **k: None)
    data = dlg._collect()
    db.save_contract(data, contract_id=contract_id)
    row = dict(db.get_connection().execute("SELECT data_contract, data_inceput, data_sfarsit FROM contracte WHERE id=?", (contract_id,)).fetchone())
    return row
'''


def test_contract_dialog_never_replaces_dates_with_today_or_blank(tmp_path):
    body = SEED_CONTRACTS + DIALOG + r'''
ids = {
  "ro": mk("C-RO", "05.03.2026", "06.03.2026", "07.03.2036"),
  "iso": mk("C-ISO", "2026-03-05", "2026-03-06", "2036-03-07"),
  "blank_end": mk("C-BLANK", "05.03.2026", "", ""),
  "invalid": mk("C-BAD", "31.02.2026", "circa 2020", "nu e data"),
  "one_digit": mk("C-1D", "5.3.2026", "6.3.2026", "7.3.2036"),
  "compact": mk("C-CMP", "05032026", "06032026", "07032036"),
}
for k, i in ids.items():
    out[k] = reopen_save(i)
'''
    o = run(tmp_path, body, qt=True)
    assert o["ro"] == {"data_contract": "05.03.2026", "data_inceput": "06.03.2026", "data_sfarsit": "07.03.2036"}
    assert o["iso"] == {"data_contract": "2026-03-05", "data_inceput": "2026-03-06", "data_sfarsit": "2036-03-07"}, \
        "datele ISO existente nu se ignoră și nu devin data curentă"
    assert o["blank_end"] == {"data_contract": "05.03.2026", "data_inceput": "", "data_sfarsit": ""}, "gol rămâne gol (nu 01.01.2000 / azi)"
    assert o["invalid"] == {"data_contract": "31.02.2026", "data_inceput": "circa 2020", "data_sfarsit": "nu e data"}
    assert o["one_digit"] == {"data_contract": "5.3.2026", "data_inceput": "6.3.2026", "data_sfarsit": "7.3.2036"}
    assert o["compact"] == {"data_contract": "05032026", "data_inceput": "06032026", "data_sfarsit": "07032036"}


def test_contract_dialog_user_change_is_saved_in_main_format_and_new_contract_has_defaults(tmp_path):
    body = SEED_CONTRACTS + DIALOG + r'''
from PySide6.QtCore import QDate
i = mk("C-ISO", "2026-03-05", "2026-03-06", "2036-03-07")
dlg = cw.AddContractDialog(None, existing=dict(db.get_connection().execute("SELECT * FROM contracte WHERE id=?", (i,)).fetchone()))
dlg.date_sfarsit.setDate(QDate(2040, 1, 2))
out["changed"] = {k: dlg._collect()[k] for k in ("data_contract", "data_inceput", "data_sfarsit")}
dlg.date_inceput.set_blank()
out["cleared"] = dlg._collect()["data_inceput"]
new = cw.AddContractDialog(None)
out["new_is_today"] = [new.date_contract.date() == QDate.currentDate()] * 1
'''
    o = run(tmp_path, body, qt=True)
    assert o["changed"] == {"data_contract": "2026-03-05", "data_inceput": "2026-03-06", "data_sfarsit": "02.01.2040"}
    assert o["cleared"] == "", "ștergerea explicită de către utilizator este permisă"
    assert o["new_is_today"] == [True]


WIRE = r'''
import entity_sync as es
assert es.ensure_tracking()
def uid_of(table, rid):
    return db.get_connection().execute(f"SELECT uid FROM {table} WHERE id=?", (rid,)).fetchone()[0]
def wire(table, uid):
    conn = db.get_connection()
    try:
        return es._read_wire(conn, table, uid)["data"]
    finally:
        conn.close()
def local(table, uid, cols):
    return dict(db.get_connection().execute(f"SELECT {', '.join(cols)} FROM {table} WHERE uid=?", (uid,)).fetchone())
def apply(table, uid, data, version=9):
    with es._Applying() as conn:
        res = es._apply_remote(conn, table, {"uid": uid, "version": version, "updated_at": "2026-01-01T00:00:00", "data": data}, respect_pending=False)
    return res
'''


def test_main_to_wire_is_iso_and_incoming_iso_is_stored_in_main_format_without_losses(tmp_path):
    body = SEED_CONTRACTS + WIRE + r'''
ro = uid_of("contracte", mk("C-RO", "05.03.2026", "", "07.03.2036"))
iso = uid_of("contracte", mk("C-ISO", "2026-03-05", "2026-03-06", "2036-03-07"))
bad = uid_of("contracte", mk("C-BAD", "31.02.2026", "circa 2020", ""))
cols = ("data_contract", "data_inceput", "data_sfarsit")
out["wire_ro"] = {c: wire("contracte", ro)[c] for c in cols}
out["wire_iso"] = {c: wire("contracte", iso)[c] for c in cols}
out["wire_bad"] = {c: wire("contracte", bad)[c] for c in cols}
out["local_untouched_after_read"] = local("contracte", ro, cols)
# Cloud trimite ISO pentru aceleași zile: nimic nu se schimbă local (nici formatul vechi)
apply("contracte", ro, {**wire("contracte", ro)})
apply("contracte", iso, {**wire("contracte", iso)})
out["after_same_day_ro"] = local("contracte", ro, cols)
out["after_same_day_iso"] = local("contracte", iso, cols)
# Cloud schimbă ziua: se scrie în formatul MAIN
apply("contracte", ro, {**wire("contracte", ro), "data_sfarsit": "2040-01-02", "data_inceput": "2026-04-01"}, version=10)
out["after_change"] = local("contracte", ro, cols)
# Cloud golește deliberat o dată: devine gol (nu azi); o valoare nerecunoscută sosită rămâne exact
apply("contracte", ro, {**wire("contracte", ro), "data_inceput": "", "data_sfarsit": "nu e data"}, version=11)
out["after_blank_and_junk"] = local("contracte", ro, cols)
# înregistrare nouă din Cloud
new_uid = "ff" * 16
apply("contracte", new_uid, {"numar_contract": "C-NEW", "data_contract": "2026-07-01", "data_inceput": "2026-07-02", "data_sfarsit": "2036-07-01"})
out["inserted"] = local("contracte", new_uid, cols)
'''
    o = run(tmp_path, body)
    assert o["wire_ro"] == {"data_contract": "2026-03-05", "data_inceput": "", "data_sfarsit": "2036-03-07"}
    assert o["wire_iso"] == {"data_contract": "2026-03-05", "data_inceput": "2026-03-06", "data_sfarsit": "2036-03-07"}
    assert o["wire_bad"] == {"data_contract": "31.02.2026", "data_inceput": "circa 2020", "data_sfarsit": ""}
    assert o["local_untouched_after_read"] == {"data_contract": "05.03.2026", "data_inceput": "", "data_sfarsit": "07.03.2036"}
    assert o["after_same_day_ro"] == o["local_untouched_after_read"]
    assert o["after_same_day_iso"] == {"data_contract": "2026-03-05", "data_inceput": "2026-03-06", "data_sfarsit": "2036-03-07"}
    assert o["after_change"] == {"data_contract": "05.03.2026", "data_inceput": "01.04.2026", "data_sfarsit": "02.01.2040"}
    assert o["after_blank_and_junk"] == {"data_contract": "05.03.2026", "data_inceput": "", "data_sfarsit": "nu e data"}
    assert o["inserted"] == {"data_contract": "01.07.2026", "data_inceput": "02.07.2026", "data_sfarsit": "01.07.2036"}


def test_every_entity_with_dates_roundtrips_main_wire_main(tmp_path):
    body = SEED_CONTRACTS + WIRE + r'''
cs = db.get_connection()
cs.execute("INSERT INTO inhumati (nume, prenume, id_loc, data_nasterii, data_deces, data_inhumare, data_deshumare) VALUES ('I','H',(SELECT id FROM locuri_de_veci LIMIT 1),'01.02.1950','2024-02-01','04.02.2024','')")
cs.execute("INSERT INTO plati (cod_loc, suma, data_plata, tip) VALUES ('T-1', 10, '05.03.2026', 'Taxă')")
cs.execute("INSERT INTO plati (cod_loc, suma, data_plata, tip) VALUES ('T-1', 11, '2026-03-06', 'Taxă')")
cs.execute("INSERT INTO amenajari_avize (cod_loc, nr_aviz, data_aviz, lucrare, data_inceput, data_finalizare) VALUES ('T-1', 'A1', '05.03.2026', 'x', '06.03.2026', '2026-03-20')")
cs.close()
conn = db.get_connection()
res = {}
for t, cols in (("inhumati", ("data_nasterii", "data_deces", "data_inhumare", "data_deshumare")), ("plati", ("data_plata",)),
                ("amenajari_avize", ("data_aviz", "data_inceput", "data_finalizare"))):
    for r in conn.execute(f"SELECT uid FROM {t}").fetchall():
        before = local(t, r[0], cols)
        w = wire(t, r[0])
        apply(t, r[0], w)
        res.setdefault(t, []).append({"before": before, "wire": {c: w[c] for c in cols}, "after": local(t, r[0], cols)})
out["res"] = res
'''
    o = run(tmp_path, body)["res"]
    for t, rows in o.items():
        for r in rows:
            assert r["before"] == r["after"], (t, r)       # nicio dată nu se pierde/modifică la dus-întors
            for v in r["wire"].values():
                assert v == "" or (len(v) == 10 and v[4] == "-"), (t, r)
    inh = o["inhumati"][0]["wire"]
    assert inh == {"data_nasterii": "1950-02-01", "data_deces": "2024-02-01", "data_inhumare": "2024-02-04", "data_deshumare": ""}
    assert sorted(r["wire"]["data_plata"] for r in o["plati"]) == ["2026-03-05", "2026-03-06"]
    assert o["amenajari_avize"][0]["wire"] == {"data_aviz": "2026-03-05", "data_inceput": "2026-03-06", "data_finalizare": "2026-03-20"}


def test_avize_dialog_existing_accepts_iso_and_ro(tmp_path):
    body = r'''
import panels
from PySide6.QtCore import QDate
for key, ex in (("ro", {"nr": "1", "data": "05.03.2026", "data_inceput": "06.03.2026"}),
                ("iso", {"nr": "1", "data": "2026-03-05", "data_inceput": "2026-03-06"}),
                ("bad", {"nr": "1", "data": "nu e data", "data_inceput": ""})):
    dlg = panels.AddAvizDialog(None, existing=ex)
    dlg._on_ok()
    out[key] = dlg.get_result()
'''
    o = run(tmp_path, body, qt=True)
    assert (o["ro"]["data"], o["ro"]["data_inceput"]) == ("05.03.2026", "06.03.2026")
    assert (o["iso"]["data"], o["iso"]["data_inceput"]) == ("05.03.2026", "06.03.2026")
    assert o["bad"]["data_inceput"] == ""
