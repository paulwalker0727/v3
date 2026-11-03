"""Etapa 1 — editarea avizelor existente în MAIN (AddAvizDialog în mod editare + db.update_aviz).
Rulează izolat, într-un subproces cu folder de date propriu (nu atinge nicio bază reală)."""
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
from PySide6.QtWidgets import QApplication
app = QApplication([])
import database as db
db.init_db()
out = {{}}
'''
POST = "\nprint('RESULT ' + json.dumps(out, default=str))\n"


def run(tmp_path, body):
    code = PRE.format(desktop=str(DESKTOP), shared=str(SHARED)) + body + POST
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_")}
    env.update(CIMITIR_DATA_DIR=str(tmp_path / "data"), CIMITIR_ENV_FILE=str(tmp_path / "nu.env"), CIMITIR_LOG_DIR=str(tmp_path / "logs"),
               CIMITIR_DB_ENGINE="sqlite", CIMITIR_MODE="local", DATABASE_URL="", QT_QPA_PLATFORM="offscreen")
    p = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(DESKTOP), capture_output=True, text=True, timeout=150)
    assert p.returncode == 0, p.stderr[-4000:]
    return json.loads([ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:])


def test_update_aviz_edits_same_row_without_duplicating(tmp_path):
    out = run(tmp_path, r'''
db.save_plot_to_db("T-1", [(0, 0), (40, 0), (40, 30), (0, 30)], "#111")
aid = db.add_aviz("T-1", "AV-1", "01.01.2026", "bordura", "Executant X", "02.01.2026")
before = db.list_avize_for_loc("T-1")
ok = db.update_aviz(aid, {"stare": "finalizata", "executant": "Executant Y",
                           "data_finalizare": "10.01.2026", "observatii": "lucrare incheiata"})
after = db.list_avize_for_loc("T-1")
out["ok"] = ok
out["rows_before"] = len(before)
out["rows_after"] = len(after)
out["row"] = after[0]
''')
    assert out["ok"] is True
    assert out["rows_before"] == 1
    assert out["rows_after"] == 1, "editarea nu trebuie să creeze o înregistrare nouă (fără duplicate)"
    row = out["row"]
    assert row["stare"] == "finalizata"
    assert row["executant"] == "Executant Y"
    assert row["data_finalizare"] == "10.01.2026"
    assert row["observatii"] == "lucrare incheiata"
    # câmpurile neatinse de update rămân neschimbate
    assert row["nr"] == "AV-1"
    assert row["lucrare"] == "bordura"


def test_update_aviz_partial_fields_only(tmp_path):
    out = run(tmp_path, r'''
db.save_plot_to_db("T-2", [(0, 0), (40, 0), (40, 30), (0, 30)], "#111")
aid = db.add_aviz("T-2", "AV-2", "05.01.2026", "zugravit")
db.update_aviz(aid, {"stare": "in_lucru"})
mid = db.get_aviz(aid)
db.update_aviz(aid, {"observatii": "doar observatii"})
after = db.get_aviz(aid)
out["mid_stare"] = mid["stare"]
out["after_stare"] = after["stare"]
out["after_observatii"] = after["observatii"]
''')
    assert out["mid_stare"] == "in_lucru"
    # a doua actualizare (doar observatii) nu trebuie sa resetez campul "stare" deja salvat
    assert out["after_stare"] == "in_lucru"
    assert out["after_observatii"] == "doar observatii"


def test_add_aviz_dialog_edit_mode_prefills_all_028_fields(tmp_path):
    out = run(tmp_path, r'''
from panels import AddAvizDialog
existing = {"nr": "AV-9", "data": "03.03.2026", "lucrare": "reparatie gard", "executant": "Firma Z",
            "data_inceput": "04.03.2026", "stare": "in_lucru", "data_finalizare": "", "observatii": "fara probleme"}
dlg = AddAvizDialog(existing=existing)
out["title"] = dlg.windowTitle()
out["btn_text"] = dlg.btn_ok.text()
out["nr"] = dlg.txt_nr.text()
out["executant"] = dlg.txt_executant.text()
out["stare_data"] = dlg.cmb_stare.currentData()
out["observatii"] = dlg.txt_observatii.toPlainText()
''')
    assert out["title"] == "Editează aviz"
    assert out["btn_text"] == "Salvează"
    assert out["nr"] == "AV-9"
    assert out["executant"] == "Firma Z"
    assert out["stare_data"] == "in_lucru"
    assert out["observatii"] == "fara probleme"


def test_add_aviz_dialog_rejects_finalizata_without_finish_date(tmp_path):
    out = run(tmp_path, r'''
import panels
panels.show_message = lambda *a, **k: None   # evita blocarea pe QMessageBox.exec() in modul offscreen
from panels import AddAvizDialog
dlg = AddAvizDialog()
dlg.txt_nr.setText("AV-10")
idx = dlg.cmb_stare.findData("finalizata")
dlg.cmb_stare.setCurrentIndex(idx)
dlg._on_ok()
out["result_is_none"] = dlg.get_result() is None
''')
    assert out["result_is_none"] is True, "nu ar trebui sa accepte 'finalizata' fara data de finalizare"
