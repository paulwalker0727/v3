"""MAIN: loc selectat pe hartă -> concesionari asociați / contracte / istoric / fișa locului, după ID-ul real (cod_loc din datele poligonului, nu din text).
Subproces offscreen, folder de date temporar, date fictive; nu atinge nicio bază reală."""
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
for n in ("information", "warning", "critical"):
    setattr(QMessageBox, n, staticmethod(lambda *a, **k: 0))
import database as db
db.init_db()
assert str(db.DB_PATH).startswith({data!r}), db.DB_PATH
out = {{}}
SQ = [(0, 0), (10, 0), (10, 10), (0, 10)]
for cod, par in (("L-1", "1"), ("L-10", "1"), ("L-2", "2")):
    db.save_plot_to_db(cod, SQ, "#22c55e")
    db.save_loc_metadata(cod, {{"sector": "A", "parcela": par, "rand": "1", "numar": cod[2:], "suprafata": 6}})
ana = db.save_concesionar({{"nume": "Ana", "prenume": "Fictiv", "cnp": "2000000000001"}})
bob = db.save_concesionar({{"nume": "Bob", "prenume": "Fictiv", "cnp": "1000000000002"}})
db.save_contract({{"numar_contract": "C-1", "cod_loc": "L-1", "concesionar_id": ana, "data_inceput": "2010-01-01"}})
db.save_contract({{"numar_contract": "C-2", "cod_loc": "L-1", "concesionar_id": bob, "data_inceput": "2020-01-01"}})
db.save_contract({{"numar_contract": "C-3", "cod_loc": "L-10", "concesionar_id": bob, "data_inceput": "2021-01-01"}})
import ui_mainwindow
messages = []
ui_mainwindow.show_message = lambda parent, title, text, level="info": messages.append((title, level))
win = ui_mainwindow.CemeteryUI()
import entity_links_ui as eui
captured = []
def fake_exec(self):
    captured.append(dict(mode=self.mode, key=self.key, people=sorted(r["nume"] for r in self._rows), fisa_visible=not self.btn_fisa.isHidden() or self.mode == "loc"))
    return 0
eui.LinkedRecordsDialog.exec = fake_exec
class FakeItem:
    def __init__(self, d): self._d = d
    def data(self, role): return self._d
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


def test_selected_plot_opens_linked_records_by_real_cod_loc(tmp_path):
    out = run(tmp_path, r'''
win.on_plot_clicked(FakeItem({"cod_loc": "L-1", "label": "L-10"}))   # textul afișat nu decide; contează cod_loc din date
win.open_selected_loc_links()
win.on_plot_clicked(FakeItem({"cod_loc": "L-10"}))
win.open_selected_loc_links()
out["captured"] = captured
out["has_action"] = hasattr(win, "act_loc_links")
''')
    assert out["has_action"]
    first, second = out["captured"]
    assert first["mode"] == "loc" and first["key"] == "L-1" and first["people"] == ["Ana", "Bob"]
    assert second["key"] == "L-10" and second["people"] == ["Bob"], "L-10 nu trebuie confundat cu L-1 (potrivire parțială/pe text)"


def test_no_selection_or_unknown_loc_opens_nothing_and_creates_nothing(tmp_path):
    out = run(tmp_path, r'''
win.open_selected_loc_links()
win.on_plot_clicked(FakeItem({"cod_loc": "NU-EXISTA"}))
win.open_selected_loc_links()
c = db.get_connection()
out["locuri"] = c.execute("SELECT COUNT(*) FROM locuri_de_veci").fetchone()[0]
c.close()
out["captured"] = captured
out["messages"] = messages
''')
    assert out["captured"] == [] and out["locuri"] == 3
    assert [m[0] for m in out["messages"]] == ["Selectare", "Loc negăsit"]


def test_fisa_button_reuses_existing_locuri_window_with_same_cod(tmp_path):
    out = run(tmp_path, r'''
import locuri_window
opened = []
orig = locuri_window.LocuriWindow.__init__
def spy(self, *a, **k):
    opened.append(k.get("initial_cod_loc")); orig(self, *a, **k)
locuri_window.LocuriWindow.__init__ = spy
locuri_window.LocuriWindow.exec = lambda self: 0
d = eui.LinkedRecordsDialog(win, win.actions, "loc", "L-10", "L-10")
d.btn_fisa.click()
out["opened"] = opened
out["history_btn"] = d.btn_history.text()
c = db.get_connection()
out["locuri"] = c.execute("SELECT COUNT(*) FROM locuri_de_veci").fetchone()[0]
c.close()
''')
    assert out["opened"] == ["L-10"] and out["locuri"] == 3
