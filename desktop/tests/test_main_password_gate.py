"""MAIN: parola implicită trebuie schimbată înainte de accesul la aplicație (aceeași regulă ca în administrarea web).

Fereastra reală de autentificare (LoginWindow) rulează offscreen, în proces separat, pe o bază temporară.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]

WORKER = r'''
import json, os, sys
sys.path.insert(0, {desktop!r})
os.chdir({desktop!r})
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QLabel
app = QApplication([])
import database as db, auth, cimitir_auth
db.init_db(); auth.init_auth()
from login_window import LoginWindow

out = {{"same_module": auth is cimitir_auth}}

def drive(new, confirm, then="save", expect_message=False, followup=None):
    def act():
        dlg = app.activeModalWidget()
        out.setdefault("dialogs", []).append(dlg.objectName() if dlg else None)
        if dlg is None:
            return
        dlg.findChild(QLineEdit, "force-password-new").setText(new)
        dlg.findChild(QLineEdit, "force-password-confirm").setText(confirm)
        if then == "save":
            dlg.findChild(QPushButton, "force-password-save").click()
            if expect_message:
                out.setdefault("messages", []).append(dlg.findChild(QLabel, "force-password-message").text())
                if followup:
                    followup(dlg)
        else:
            dlg.reject()
    QTimer.singleShot(250, act)

def attempt(username, password):
    win = LoginWindow()
    win.input_username.setText(username)
    win.input_password.setText(password)
    win.try_login()
    return win

uid = {{u["username"]: u["id"] for u in cimitir_auth.list_users()}}
out["required_before"] = {{k: cimitir_auth.password_change_required(v) for k, v in uid.items()}}

# 1) renunțare la dialog => fără acces
drive("", "", then="cancel")
win = attempt("admin", "admin123")
out["cancel_user"] = win.user
out["cancel_error"] = win.lbl_error.text()
out["still_default"] = cimitir_auth.password_change_required(uid["admin"])

# 2) parolă nouă slabă / identică cu cea implicită => dialogul rămâne, apoi parolă bună => acces
state = {{}}
def retry(dlg):
    dlg.findChild(QLineEdit, "force-password-new").setText("Parola-Noua-2026!")
    dlg.findChild(QLineEdit, "force-password-confirm").setText("Parola-Noua-2026!")
    dlg.findChild(QPushButton, "force-password-save").click()
drive("admin123", "admin123", then="save", expect_message=True, followup=retry)
win = attempt("admin", "admin123")
out["after_change_user"] = (win.user or {{}}).get("username")
out["default_login_now_rejected"] = cimitir_auth.authenticate("admin", "admin123") is None
out["new_login_ok"] = (cimitir_auth.authenticate("admin", "Parola-Noua-2026!") or {{}}).get("must_change_password")
out["required_after"] = cimitir_auth.password_change_required(uid["admin"])
out["user_required_after"] = cimitir_auth.password_change_required(uid["user"])

# 3) o parolă diferită de cea implicită nu cere dialog
win = attempt("admin", "Parola-Noua-2026!")
out["normal_login"] = (win.user or {{}}).get("username")
print("RESULT " + json.dumps(out, default=str))
'''


def _run(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("CIMITIR_DATA_DIR", "JWT_SECRET")}
    env.update(CIMITIR_DATA_DIR=str(tmp_path / "data"), CIMITIR_ENV_FILE=str(tmp_path / "nu.env"),
               QT_QPA_PLATFORM="offscreen", CIMITIR_LOG_DIR=str(tmp_path / "logs"))
    code = WORKER.format(desktop=str(DESKTOP))
    proc = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(DESKTOP), capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads([ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:])


def test_login_window_forces_default_password_change(tmp_path):
    out = _run(tmp_path)
    assert out["same_module"] is True
    assert out["required_before"] == {"admin": True, "user": True}

    assert out["cancel_user"] is None and "obligatorie" in out["cancel_error"] and out["still_default"] is True

    assert out["dialogs"][1] == "force-password-dialog"
    assert any("diferită" in m or "parolele implicite" in m for m in out["messages"])
    assert out["after_change_user"] == "admin"
    assert out["default_login_now_rejected"] is True
    assert out["new_login_ok"] is False
    assert out["required_after"] is False and out["user_required_after"] is True
    assert out["normal_login"] == "admin"
    assert len(out["dialogs"]) == 2, "nicio fereastră de schimbare nu mai apare după ce parola a fost schimbată"
