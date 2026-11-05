"""Ajutor pentru testele de remediere care ating fereastra „Copii de rezervă” (PySide6 offscreen, bază SQLite temporară, date fictive)."""
import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import ROOT

HEAD = r'''
import json, os, sys
sys.path.insert(0, {desktop!r})
sys.path.insert(0, {shared!r})
os.chdir({desktop!r})
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QPlainTextEdit, QTimeEdit
from PySide6.QtCore import QTime
app = QApplication([])
import database as db, cimitir_backup, cimitir_config
db.init_db()
import backup_window
out = {{}}
'''


def run_ui(body: str, tmp_path: Path, **fmt) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_")}
    env.update(CIMITIR_DATA_DIR=str(tmp_path / "data"), CIMITIR_ENV_FILE=str(tmp_path / "nu.env"), CIMITIR_LOG_DIR=str(tmp_path / "logs"),
               QT_QPA_PLATFORM="offscreen", CIMITIR_DB_ENGINE="sqlite", CIMITIR_MODE="local", DATABASE_URL="")
    code = (HEAD + body + "\nprint('RESULT ' + json.dumps(out))\n").format(desktop=str(ROOT / "desktop"), shared=str(ROOT / "shared"), **fmt)
    proc = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(ROOT / "desktop"), capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-2500:]
    return json.loads([ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:])
