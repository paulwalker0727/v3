"""Configurare inițială MAIN: fără conturi implicite în instalările comerciale; primul administrator local se creează securizat (SQLite local, offscreen)."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="cimitir_test_firstrun_")
os.environ["CIMITIR_DATA_DIR"] = _TMP
os.environ["CIMITIR_LOG_DIR"] = os.path.join(_TMP, "logs")
DESKTOP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DESKTOP))
sys.path.insert(0, str(DESKTOP.parent / "shared"))

import pytest  # noqa: E402

import cimitir_auth as auth  # noqa: E402
import database as db  # noqa: E402


@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    monkeypatch.delenv("CIMITIR_ALLOW_DEFAULT_USERS", raising=False)
    monkeypatch.delenv("CIMITIR_BOOTSTRAP_ADMIN_USER", raising=False)
    monkeypatch.delenv("CIMITIR_BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "cimitir.db"))
    db.init_db()
    auth.init_auth()


def test_no_default_accounts_in_local_sqlite_commercial_install(fresh):
    assert not auth.default_users_allowed()
    assert auth.list_users() == [] and auth.first_run_required()
    assert auth.authenticate("admin", "admin123") is None and auth.authenticate("user", "user123") is None


def test_first_admin_rules_and_creation(fresh):
    assert auth.setup_first_admin("admin", "Parola-Buna-2026!", "Parola-Buna-2026!")[0] is False            # nume implicit refuzat
    assert auth.setup_first_admin("director", "scurta", "scurta")[0] is False
    assert auth.setup_first_admin("directorul-meu", "directorul-meu", "directorul-meu")[0] is False          # parolă egală cu numele
    ok, msg = auth.setup_first_admin("director", "Parola-Buna-2026!", "alta-parola-2026!!")
    assert not ok and "nu coincid" in msg and auth.first_run_required()
    ok, _ = auth.setup_first_admin("director", "Parola-Buna-2026!", "Parola-Buna-2026!")
    assert ok and not auth.first_run_required()
    user, why = auth.authenticate_ex("director", "Parola-Buna-2026!")
    assert why == "ok" and user["role"] == auth.ROLE_ADMIN and "manage_config" in user["rights"] and not user["must_change_password"]
    assert auth.setup_first_admin("altul", "Parola-Buna-2026!", "Parola-Buna-2026!")[0] is False            # nu se creează al doilea „prim” administrator
    assert auth.authenticate("admin", "admin123") is None


def test_dev_flag_still_creates_defaults_only_when_explicit(tmp_path, monkeypatch):
    monkeypatch.setenv("CIMITIR_ALLOW_DEFAULT_USERS", "1")
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "dev.db"))
    db.init_db()
    auth.init_auth()
    assert auth.default_password_accounts()                                                                # dezvoltare: conturile implicite există și sunt semnalate


def test_wizard_dialog_creates_admin_and_rejects_bad_input(fresh):
    from PySide6.QtWidgets import QApplication, QDialog
    QApplication.instance() or QApplication([])
    from first_run_wizard import FirstRunWizard
    w = FirstRunWizard()
    w.ed_user.setText("director")
    w.ed_pass.setText("Parola-Buna-2026!")
    w.ed_conf.setText("diferita")
    w.btn.click()
    assert "nu coincid" in w.lbl_err.text() and w.result() != QDialog.Accepted and w.ed_pass.text() == ""
    w.ed_pass.setText("Parola-Buna-2026!")
    w.ed_conf.setText("Parola-Buna-2026!")
    w.btn.click()
    assert w.result() == QDialog.Accepted and not auth.first_run_required()
