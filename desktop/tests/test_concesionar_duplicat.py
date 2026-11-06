"""Regresie: „Adaugă concesionar” din contract crea DOUĂ înregistrări în SQLite.

Cauza: AddConcesionarDialog._save() salvează deja în baza de date; AddContractDialog._open_add_concesionar()
apela apoi încă o dată save_concesionar(dlg.get_data()) (fără id) => al doilea INSERT.
Rulează izolat (subproces, folder de date temporar, date fictive; nu atinge nicio bază reală).
"""
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
import database as db
db.init_db()
assert str(db.DB_PATH).startswith({data!r}), db.DB_PATH
out = {{}}

def count(table="concesionari"):
    c = db.get_connection()
    try:
        return c.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
    finally:
        c.close()

def fill_and_click_save(values):
    import concesionari_window as cw
    def fake_exec(self):
        self.txt_nume.setText(values.get("nume", ""))
        self.txt_prenume.setText(values.get("prenume", ""))
        self.txt_cnp.setText(values.get("cnp", ""))
        self.btn_save.click()
        return self.result()
    cw.AddConcesionarDialog.exec = fake_exec
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


def test_add_concesionar_from_contract_creates_exactly_one_row_and_selects_it(tmp_path):
    out = run(tmp_path, r'''
from contracts_window import AddContractDialog
fill_and_click_save({"nume": "Ionescu", "prenume": "Fictiv"})
dlg = AddContractDialog(None, actions=None)
before = count()
dlg._open_add_concesionar()
rows = db.load_concesionari()
out["before"] = before
out["after"] = count()
out["names"] = [(r["id"], r["nume"], r["prenume"]) for r in rows]
out["combo_ids"] = [dlg.cmb_concesionar.itemData(i) for i in range(dlg.cmb_concesionar.count())]
out["selected_id"] = dlg.cmb_concesionar.currentData()
''')
    assert out["before"] == 0
    assert out["after"] == 1, f"concesionar duplicat: {out['names']}"
    new_id = out["names"][0][0]
    assert out["selected_id"] == new_id
    assert out["combo_ids"].count(new_id) == 1


def test_add_concesionar_via_actions_adapter_creates_one_row(tmp_path):
    out = run(tmp_path, r'''
from contracts_window import AddContractDialog
class Actions:
    def __init__(self): self.calls = 0
    def load_concesionari(self, f=None): return db.load_concesionari()
    def save_concesionar(self, data, concesionar_id=None):
        self.calls += 1
        return db.save_concesionar(data, concesionar_id)
a = Actions()
fill_and_click_save({"nume": "Popescu", "prenume": "Fictiv"})
dlg = AddContractDialog(None, actions=a)
dlg._open_add_concesionar()
out["calls"] = a.calls
out["rows"] = count()
out["selected_id"] = dlg.cmb_concesionar.currentData()
out["ids"] = [r["id"] for r in db.load_concesionari()]
''')
    assert out["calls"] == 1 and out["rows"] == 1
    assert out["selected_id"] == out["ids"][0]


def test_double_click_on_save_does_not_insert_twice(tmp_path):
    out = run(tmp_path, r'''
import concesionari_window as cw
dlg = cw.AddConcesionarDialog(None, existing=None)
dlg.txt_nume.setText("Dublu"); dlg.txt_prenume.setText("Click")
dlg.btn_save.click(); dlg.btn_save.click()
out["rows"] = count()
out["saved_id"] = dlg.get_saved_id()
''')
    assert out["rows"] == 1


def test_existing_person_can_be_reused_instead_of_creating_new(tmp_path):
    out = run(tmp_path, r'''
import concesionari_window as cw
from contracts_window import AddContractDialog
existing_id = db.save_concesionar({"nume": "Georgescu", "prenume": "Fictiv", "cnp": "1234567890123"})
dlg = AddContractDialog(None, actions=None)
cw.AddConcesionarDialog._choose_existing = lambda self, candidates: candidates[0]["id"]
fill_and_click_save({"nume": "Georgescu", "prenume": "Fictiv", "cnp": "1234567890123"})
dlg._open_add_concesionar()
out["rows"] = count()
out["existing_id"] = existing_id
out["selected_id"] = dlg.cmb_concesionar.currentData()
''')
    assert out["rows"] == 1
    assert out["selected_id"] == out["existing_id"]


def test_same_name_without_cnp_is_offered_but_never_merged_automatically(tmp_path):
    out = run(tmp_path, r'''
import concesionari_window as cw
db.save_concesionar({"nume": "Vasilescu", "prenume": "Fictiv"})
asked = []
def fake_choose(self, candidates):
    asked.append([c["id"] for c in candidates]); return "new"
cw.AddConcesionarDialog._choose_existing = fake_choose
fill_and_click_save({"nume": "Vasilescu", "prenume": "Fictiv"})
d = cw.AddConcesionarDialog(None, existing=None)
d.exec()
out["asked"] = asked
out["rows"] = count()
''')
    assert out["asked"], "utilizatorul trebuia întrebat despre omonim"
    assert out["rows"] == 2


def test_editing_existing_concesionar_updates_same_row(tmp_path):
    out = run(tmp_path, r'''
import concesionari_window as cw
cid = db.save_concesionar({"nume": "Vechi", "prenume": "Nume"})
d = cw.AddConcesionarDialog(None, existing=db.get_concesionar(cid))
d.txt_nume.setText("Nou"); d.btn_save.click()
out["rows"] = count(); out["nume"] = db.get_concesionar(cid)["nume"]; out["saved"] = d.get_saved_id(); out["cid"] = cid
''')
    assert out["rows"] == 1 and out["nume"] == "Nou" and out["saved"] == out["cid"]
