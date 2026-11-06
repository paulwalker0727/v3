"""Interconectare Locuri de veci <-> Concesionari <-> Contracte: navigare, precompletare, cardinalități, istoric.
Rulează izolat (subproces, folder de date temporar, date fictive; nu atinge nicio bază reală)."""
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
from PySide6.QtWidgets import QApplication, QMessageBox, QDialog
app = QApplication([])
QMessageBox.information = staticmethod(lambda *a, **k: 0)
QMessageBox.warning = staticmethod(lambda *a, **k: 0)
QMessageBox.critical = staticmethod(lambda *a, **k: 0)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
import database as db
db.init_db()
assert str(db.DB_PATH).startswith({data!r}), db.DB_PATH
out = {{}}

def count(table):
    c = db.get_connection()
    try:
        return c.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
    finally:
        c.close()

def dump(table):
    c = db.get_connection()
    try:
        return [dict(r) for r in c.execute("SELECT * FROM " + table + " ORDER BY id").fetchall()]
    finally:
        c.close()

SQ = [(0, 0), (10, 0), (10, 10), (0, 10)]
def mk_loc(cod, sector, parcela, rand, numar):
    db.save_plot_to_db(cod, SQ, "#22c55e")
    db.save_loc_metadata(cod, {{"sector": sector, "parcela": parcela, "rand": rand, "numar": numar, "suprafata": 6}})

def mk_contract(nr, cod, cid, inceput="2020-01-01"):
    return db.save_contract({{"numar_contract": nr, "cod_loc": cod, "concesionar_id": cid, "data_inceput": inceput}})

def auto_save_contract(numar):
    import contracts_window as cw
    def fake_exec(self):
        self.txt_numar.setText(numar)
        self.btn_save.click()
        return self.result()
    cw.AddContractDialog.exec = fake_exec

mk_loc("L-1", "A", "1", "1", "1")
mk_loc("L-2", "A", "1", "1", "2")
ana = db.save_concesionar({{"nume": "Ana", "prenume": "Fictiv", "cnp": "2000000000001"}})
bob = db.save_concesionar({{"nume": "Bob", "prenume": "Fictiv", "cnp": "1000000000002"}})
c1 = mk_contract("C-1", "L-1", ana, "2010-01-01")
c2 = mk_contract("C-2", "L-1", bob, "2020-01-01")
c3 = mk_contract("C-3", "L-1", bob, "2022-01-01")
c4 = mk_contract("C-4", "L-2", ana, "2021-01-01")
'''
POST = "\nprint('RESULT ' + json.dumps(out, default=str))\n"


def run(tmp_path, body):
    data = str(tmp_path / "data")
    code = PRE.format(desktop=str(DESKTOP), shared=str(SHARED), data=data) + body + POST
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_") and k not in ("DATABASE_URL", "SUPABASE_URL")}
    env.update(CIMITIR_DATA_DIR=data, CIMITIR_ENV_FILE=str(tmp_path / "nu.env"), CIMITIR_LOG_DIR=str(tmp_path / "logs"),
               CIMITIR_DB_ENGINE="sqlite", CIMITIR_MODE="local", DATABASE_URL="", QT_QPA_PLATFORM="offscreen")
    p = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(DESKTOP), capture_output=True, text=True, timeout=150)
    assert p.returncode == 0, p.stderr[-4000:]
    return json.loads([ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:])


def test_cardinality_loc_has_many_concesionari_and_many_contracts(tmp_path):
    out = run(tmp_path, r'''
import entity_links as el
out["loc1_conc"] = {c["nume"]: c["contracte"] for c in el.concesionari_for_loc("L-1")}
out["loc1_history"] = [r["numar_contract"] for r in el.contracts_for_loc("L-1")]
out["ana_locs"] = {l["cod_loc"]: l["contracte"] for l in el.locuri_for_concesionar(ana)}
out["bob_locs"] = {l["cod_loc"]: l["contracte"] for l in el.locuri_for_concesionar(bob)}
out["ana_id"], out["bob_id"] = ana, bob
''')
    assert out["loc1_conc"] == {"Ana": 1, "Bob": 2}
    assert out["loc1_history"] == ["C-1", "C-2", "C-3"]
    assert out["ana_locs"] == {"L-1": 1, "L-2": 1}
    assert out["bob_locs"] == {"L-1": 2}


def test_new_contract_from_loc_prefills_loc_and_does_not_touch_old_contracts(tmp_path):
    out = run(tmp_path, r'''
from contracts_window import AddContractDialog
import entity_links_ui as ui
before = dump("contracte")
auto_save_contract("C-NOU")
dlg_holder = {}
orig = AddContractDialog.__init__
def spy(self, *a, **k):
    orig(self, *a, **k); dlg_holder["d"] = self
AddContractDialog.__init__ = spy
import contracts_window as cw
def fake_exec(self):
    out["prefill_loc"] = self.cmb_cod_loc.currentText()
    out["prefill_parcela"] = self.txt_parcela.text(); out["prefill_sector"] = self.txt_sector.text()
    out["prefill_conc"] = self.cmb_concesionar.currentData()
    self.txt_numar.setText("C-NOU"); self.cmb_concesionar.setCurrentIndex(
        [self.cmb_concesionar.itemData(i) for i in range(self.cmb_concesionar.count())].index(bob))
    self.btn_save.click(); return self.result()
cw.AddContractDialog.exec = fake_exec
out["accepted"] = ui.open_contract_dialog(None, None, cod_loc="L-2")
after = dump("contracte")
out["old_unchanged"] = after[:len(before)] == before
out["new"] = after[len(before):]
out["loc2_id"] = db.load_loc_by_cod("L-2")["id"]
out["bob"] = bob
out["n_loc"] = count("locuri_de_veci"); out["n_conc"] = count("concesionari")
''')
    assert out["accepted"] and out["prefill_loc"] == "L-2" and out["prefill_parcela"] == "1" and out["prefill_sector"] == "A"
    assert out["prefill_conc"] is None
    assert out["old_unchanged"], "un contract vechi a fost modificat"
    assert len(out["new"]) == 1
    n = out["new"][0]
    assert n["concesionar_id"] == out["bob"] and n["id_loc"] == out["loc2_id"] and n["cod_loc"] == "L-2"
    assert out["n_loc"] == 2 and out["n_conc"] == 2


def test_new_contract_from_concesionar_prefills_person_and_reuses_id(tmp_path):
    out = run(tmp_path, r'''
import contracts_window as cw, entity_links_ui as ui
before = dump("contracte")
def fake_exec(self):
    out["prefill_conc"] = self.cmb_concesionar.currentData()
    out["prefill_nume"] = self.txt_nume.text(); out["prefill_cnp"] = self.txt_cnp.text()
    self.txt_numar.setText("C-ANA2"); self.cmb_cod_loc.setCurrentText("L-2"); self.btn_save.click(); return self.result()
cw.AddContractDialog.exec = fake_exec
ui.open_contract_dialog(None, None, concesionar_id=ana)
after = dump("contracte")
out["old_unchanged"] = after[:len(before)] == before
out["new"] = after[len(before):]
out["ana"] = ana; out["n_conc"] = count("concesionari")
''')
    assert out["prefill_conc"] == out["ana"] and out["prefill_nume"] == "Ana" and out["prefill_cnp"] == "2000000000001"
    assert out["old_unchanged"] and len(out["new"]) == 1 and out["new"][0]["concesionar_id"] == out["ana"]
    assert out["n_conc"] == 2


def test_contracts_window_filtered_history_by_loc_and_by_concesionar(tmp_path):
    out = run(tmp_path, r'''
import entity_links_ui as ui
from contracts_window import ContractsWindow
def rows(win):
    return [win.table.item(i, 1).text() for i in range(win.table.rowCount())]
w = ContractsWindow(None, None, filter_cod_loc="L-1")
out["by_loc"] = sorted(rows(w))
w.reload(); w.reload()
out["by_loc_after_reload"] = sorted(rows(w))
w2 = ContractsWindow(None, None, filter_concesionar_id=ana)
out["by_ana"] = sorted(rows(w2))
w3 = ContractsWindow(None, None)
out["all"] = sorted(rows(w3))
''')
    assert out["by_loc"] == ["C-1", "C-2", "C-3"] == out["by_loc_after_reload"]
    assert out["by_ana"] == ["C-1", "C-4"]
    assert out["all"] == ["C-1", "C-2", "C-3", "C-4"]


def test_linked_dialog_from_loc_pick_existing_creates_one_contract_with_real_ids(tmp_path):
    out = run(tmp_path, r'''
import contracts_window as cw, entity_links_ui as ui
d = ui.LinkedRecordsDialog(None, None, "loc", "L-2", "L-2")
out["initial"] = [d.table.item(i, 1).text() for i in range(d.table.rowCount())]
def pick_exec(self):
    for i, r in enumerate(self._shown):
        if r["id"] == bob: self.table.selectRow(i)
    self._accept(); return self.result()
ui.PickDialog.exec = pick_exec
def fake_exec(self):
    out["prefill_conc"] = self.cmb_concesionar.currentData(); out["prefill_loc"] = self.cmb_cod_loc.currentText()
    self.txt_numar.setText("C-5"); self.btn_save.click(); return self.result()
cw.AddContractDialog.exec = fake_exec
n0 = count("contracte")
d.pick_existing()
out["contracts_added"] = count("contracte") - n0
out["after"] = sorted(d.table.item(i, 1).text() for i in range(d.table.rowCount()))
out["bob"] = bob; out["n_conc"] = count("concesionari")
''')
    assert out["initial"] == ["Ana"]
    assert out["prefill_conc"] == out["bob"] and out["prefill_loc"] == "L-2"
    assert out["contracts_added"] == 1 and out["after"] == ["Ana", "Bob"] and out["n_conc"] == 2


def test_linked_dialog_from_loc_add_new_concesionar_no_duplicates(tmp_path):
    out = run(tmp_path, r'''
import contracts_window as cw, concesionari_window as cwin, entity_links_ui as ui
d = ui.LinkedRecordsDialog(None, None, "loc", "L-2", "L-2")
def add_exec(self):
    self.txt_nume.setText("Nou"); self.txt_prenume.setText("Venit"); self.btn_save.click(); return self.result()
cwin.AddConcesionarDialog.exec = add_exec
def fake_exec(self):
    out["prefill_conc"] = self.cmb_concesionar.currentData(); out["prefill_nume"] = self.txt_nume.text()
    self.txt_numar.setText("C-6"); self.btn_save.click(); return self.result()
cw.AddContractDialog.exec = fake_exec
d.add_new_concesionar()
out["n_conc"] = count("concesionari"); out["n_contr"] = count("contracte")
new = [c for c in dump("concesionari") if c["nume"] == "Nou"]
out["new_ids"] = [c["id"] for c in new]
last = dump("contracte")[-1]
out["last_conc"] = last["concesionar_id"]; out["last_loc"] = last["cod_loc"]
''')
    assert out["n_conc"] == 3 and len(out["new_ids"]) == 1 and out["prefill_conc"] == out["new_ids"][0]
    assert out["last_conc"] == out["new_ids"][0] and out["last_loc"] == "L-2" and out["n_contr"] == 5


def test_linked_dialog_from_concesionar_lists_locs_and_opens_loc_in_locuri_window(tmp_path):
    out = run(tmp_path, r'''
import entity_links_ui as ui
d = ui.LinkedRecordsDialog(None, None, "concesionar", ana, "Ana")
out["locs"] = sorted(d.table.item(i, 0).text() for i in range(d.table.rowCount()))
from locuri_window import LocuriWindow
from PySide6.QtWidgets import QWidget
class Main(QWidget):
    actions = db
w = LocuriWindow(Main(), initial_cod_loc="L-2")
for b in ("btn_concesionari", "btn_contract_nou", "btn_contracte"):
    out[b] = hasattr(w, b)
out["selected"] = w.selected_cod_loc()
out["not_found"] = w.select_cod_loc("NU-EXISTA")
''')
    assert out["locs"] == ["L-1", "L-2"] and out["selected"] == "L-2" and out["not_found"] is False
    assert out["btn_concesionari"] and out["btn_contract_nou"] and out["btn_contracte"]


def test_concesionari_window_shows_loc_info_and_filters_by_cod_loc_and_selects_initial(tmp_path):
    out = run(tmp_path, r'''
from concesionari_window import ConcesionariWindow
w = ConcesionariWindow(None, None, initial_id=bob)
out["selected"] = w._selected_row_data()["nume"]
out["cod_col"] = {w.table.item(i, 1).text(): w.table.item(i, 8).text() for i in range(w.table.rowCount())}
w.filter_cod_loc.setText("L-2")
out["filtered"] = [w.table.item(i, 1).text() for i in range(w.table.rowCount())]
w.reload(); w.reload()
out["rows_after_reloads"] = w.table.rowCount()
for b in ("btn_locuri", "btn_contract_nou", "btn_contracte"):
    out[b] = hasattr(w, b)
''')
    assert out["selected"] == "Bob"
    assert out["cod_col"] == {"Ana": "L-1, L-2", "Bob": "L-1"}
    assert out["filtered"] == ["Ana"] and out["rows_after_reloads"] == 1
    assert out["btn_locuri"] and out["btn_contract_nou"] and out["btn_contracte"]


def test_contract_with_unknown_loc_asks_confirmation_and_never_creates_a_loc(tmp_path):
    out = run(tmp_path, r'''
from contracts_window import AddContractDialog
asked = []
QMessageBox.question = staticmethod(lambda *a, **k: (asked.append(1), QMessageBox.No)[1])
d = AddContractDialog(None, None, selected_concesionar_id=ana)
d.txt_numar.setText("C-X"); d.cmb_cod_loc.setCurrentText("FICTIV-999")
n0 = count("contracte"); l0 = count("locuri_de_veci")
d.btn_save.click()
out["asked"] = len(asked); out["contracts_added"] = count("contracte") - n0; out["locs_added"] = count("locuri_de_veci") - l0
''')
    assert out["asked"] == 1 and out["contracts_added"] == 0 and out["locs_added"] == 0


def test_edit_old_contract_updates_only_that_contract(tmp_path):
    out = run(tmp_path, r'''
from contracts_window import AddContractDialog
before = dump("contracte")
d = AddContractDialog(None, None, existing=db.get_contract(c1))
d.txt_observatii.setPlainText("nota")
d.btn_save.click()
after = dump("contracte")
out["changed_ids"] = [a["id"] for a, b in zip(after, before) if a != b]
out["n"] = (len(before), len(after)); out["c1"] = c1
''')
    assert out["changed_ids"] == [out["c1"]] and out["n"] == [4, 4]
