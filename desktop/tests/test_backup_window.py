"""MAIN: fereastra „Copii de rezervă" (copie acum, verificare, restaurare dintr-un clic) și copia zilnică în fundal. Ruleză offscreen, bază temporară."""
import json
import os
import subprocess
import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]

WORKER = r'''
import json, os, sys, time
sys.path.insert(0, {desktop!r})
os.chdir({desktop!r})
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QTableWidget, QCheckBox
app = QApplication([])
import database as db, cimitir_backup, cimitir_config
db.init_db()
db.set_setting("cimitir_nume", "Original")
import backup_window
data = cimitir_config.data_dir()
out = {{"folder": str(backup_window.backup_folder())}}

win = backup_window.BackupWindow()
win.txt_dir.setText({dest!r})
win.findChild(QPushButton, "backup-now").click()
table = win.findChild(QTableWidget, "backup-table")
out["rows_after_backup"] = table.rowCount()
out["saved_dir"] = db.get_setting("backup_dir")

table.selectRow(0)
win.findChild(QPushButton, "backup-verify").click()
out["verify_status"] = win.lbl_status.text()

db.set_setting("cimitir_nume", "Stricat")
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
QMessageBox.information = staticmethod(lambda *a, **k: None)
QApplication.quit = staticmethod(lambda: out.__setitem__("quit_called", True))
win.findChild(QPushButton, "backup-restore").click()
out["marker"] = cimitir_backup.pending_marker(data).is_file()
out["name_before_restart"] = db.get_setting("cimitir_nume")
print("RESULT " + json.dumps(out))
'''

APPLY = r'''
import json, os, sys
sys.path.insert(0, {desktop!r})
sys.path.insert(0, {shared!r})
import cimitir_backup, cimitir_config
msg = cimitir_backup.apply_pending_restore(cimitir_config.data_dir())
import database as db
print("RESULT " + json.dumps({{"message": msg, "name": db.get_setting("cimitir_nume")}}))
'''

BACKGROUND = r'''
import json, os, sys, time
sys.path.insert(0, {desktop!r})
os.chdir({desktop!r})
from PySide6.QtWidgets import QApplication
app = QApplication([])
import database as db, cimitir_backup, cimitir_config
db.init_db()
import backup_window
db.set_setting("backup_dir", {dest!r})
timer = backup_window.start_scheduler(None)
out = {{"interval_minutes": timer.interval() / 60000}}
backup_window.run_background_backup()
for _ in range(100):
    if cimitir_backup.list_backups({dest!r}):
        break
    time.sleep(0.1)
out["count"] = len(cimitir_backup.list_backups({dest!r}))
backup_window.run_background_backup(); time.sleep(1)
out["count_after_second"] = len(cimitir_backup.list_backups({dest!r}))
db.set_setting("backup_enabled", "0")
print("RESULT " + json.dumps(out))
'''


def _run(code: str, tmp_path: Path, **fmt) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_")}
    env.update(CIMITIR_DATA_DIR=str(tmp_path / "data"), CIMITIR_ENV_FILE=str(tmp_path / "nu.env"), CIMITIR_LOG_DIR=str(tmp_path / "logs"),
               QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-c", code.format(desktop=str(DESKTOP), shared=str(DESKTOP.parent / "shared"), **fmt)],
                          env=env, cwd=str(DESKTOP), capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2500:]
    return json.loads([ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:])


def test_backup_window_backup_verify_and_one_click_restore(tmp_path):
    dest = str(tmp_path / "copii")
    out = _run(WORKER, tmp_path, dest=dest)
    assert out["folder"].endswith("data-backups"), "implicit: folder lângă folderul de date, nu în interiorul lui"
    assert out["rows_after_backup"] == 1 and out["saved_dir"] == dest
    assert "întreagă" in out["verify_status"]
    assert out["marker"] is True and out["quit_called"] is True
    assert out["name_before_restart"] == "Stricat", "restaurarea nu atinge baza cât aplicația rulează"

    restarted = _run(APPLY, tmp_path)
    assert "restaurată" in restarted["message"] and restarted["name"] == "Original"
    assert any(".inainte-de-restaurare-" in p.name for p in tmp_path.iterdir())


def test_background_daily_backup_runs_once_and_respects_disabled_flag(tmp_path):
    out = _run(BACKGROUND, tmp_path, dest=str(tmp_path / "zilnic"))
    assert out["interval_minutes"] == 30 and out["count"] == 1 and out["count_after_second"] == 1


def test_main_applies_pending_restore_before_opening_the_database():
    text = (DESKTOP / "main.py").read_text(encoding="utf-8")
    assert text.index("apply_pending_restore") < text.index("from database import")
    assert "RESTORE_MESSAGE" in text
    assert "act-backup" in (DESKTOP / "ui_mainwindow.py").read_text(encoding="utf-8")
