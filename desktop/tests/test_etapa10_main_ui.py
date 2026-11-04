"""Etapa 10 — curățarea interfeței MAIN: un singur buton de backup, ferestrele noi (avize, integritate, duplicate, backup) accesibile din bara MAIN
și fără erori de import. Rulează izolat (subproces, folder de date temporar, Qt offscreen)."""
import json
import os
import subprocess
import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]

WORKER = r'''
import json, os, sys
sys.path.insert(0, {desktop!r}); os.chdir({desktop!r})
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QToolBar
app = QApplication([])
import database as db
db.init_db()
import access_session
access_session.can = lambda right: True
import ui_mainwindow
w = ui_mainwindow.CemeteryUI()
actions = {{a.objectName() or a.text(): a for tb in w.findChildren(QToolBar) for a in tb.actions() if a.text()}}
out = {{"texts": [a.text() for a in actions.values()]}}
QDialog.exec = lambda self: (out.setdefault("opened", []).append(type(self).__name__) or 0)
for key in ("act-backup", "act_integrity_check", "act_duplicate_people"):
    actions[key].trigger()
shown = []
QMessageBox.information = staticmethod(lambda *a, **k: shown.append(a[2]))
w.selected_cod_loc = None
actions["Amenajări"].trigger()
out["amenajari_no_plot"] = bool(shown) and "Selectează" in shown[0]
w.selected_cod_loc = "T-1"
actions["Amenajări"].trigger()
out["amenajari_tab"] = w.right_panel.menu.currentRow()
out["has_backup_now"] = hasattr(w, "backup_now")
out["status_label"] = hasattr(w, "_backup_status_label") and w._backup_status_label is not None
print("RESULT " + json.dumps(out))
'''


def test_main_has_single_backup_entry_and_new_windows_open(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_")}
    env.update(CIMITIR_DATA_DIR=str(tmp_path / "data"), CIMITIR_ENV_FILE=str(tmp_path / "nu.env"), CIMITIR_LOG_DIR=str(tmp_path / "logs"),
               CIMITIR_DB_ENGINE="sqlite", CIMITIR_MODE="local", DATABASE_URL="", QT_QPA_PLATFORM="offscreen", CIMITIR_ALLOW_DEFAULT_USERS="1")
    proc = subprocess.run([sys.executable, "-c", WORKER.format(desktop=str(DESKTOP))], env=env, cwd=str(DESKTOP), capture_output=True, text=True, timeout=150)
    assert proc.returncode == 0, proc.stderr[-3000:]
    out = json.loads([ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:])
    backups = [t for t in out["texts"] if "backup" in t.lower() or "rezerv" in t.lower()]
    assert backups == ["💾 Copii de rezervă"], backups
    assert out["has_backup_now"] is False
    assert out["opened"] == ["BackupWindow", "IntegrityWindow", "DuplicatePeopleWindow"]
    assert out["amenajari_no_plot"] is True and out["amenajari_tab"] == 5
    assert out["status_label"] is True
