"""Regresii P3 (auditul de testare, runda 3):  lacune NOI de drepturi in MAIN (SQLite sintetic in tmp_path; fara retea)."""
import sys
from pathlib import Path

import pytest

DESKTOP = Path(__file__).resolve().parents[1]
SHARED = DESKTOP.parent / "shared"
for p in (str(DESKTOP), str(SHARED)):
    if p not in sys.path:
        sys.path.insert(0, p)

import database as db  # noqa: E402
import cimitir_auth as auth  # noqa: E402
import access_session  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

POLY = [(0, 0), (10, 0), (10, 10), (0, 10)]


@pytest.fixture
def env(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    for fn in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, fn, staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(db, "_audit_actor", "")
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "cimitir.db"))
    db.init_db()
    auth.init_auth()
    db.set_access_guard(None)
    db.save_plot_to_db("A-1", POLY, "#fff")

    def login(rights_list, role="operator"):
        uid = auth.create_user(f"t{len(auth.list_users())}", "Parola-test-123", role, rights=rights_list)
        access_session.set_user(auth.get_session_user(uid))
        db.set_access_guard(access_session.can)
        return uid
    yield type("E", (), {"login": staticmethod(login), "tmp": tmp_path})
    db.set_access_guard(None)
    access_session.set_user(None)


# Phone buttons gated on add (concesionari / plati)
@pytest.mark.parametrize("rights,enabled", [(["cfg"], False), (["cfg", "edit"], False), (["cfg", "add"], True), (["edit"], True)])
def test_btn_phone_gated(env, rights, enabled):
    env.login(rights)
    import concesionari_window
    import plati_window
    w1 = concesionari_window.ConcesionariWindow(None)
    w2 = plati_window.PlatiWindow(None, "A-1")
    print(rights, "concesionari.btn_phone:", w1.btn_phone.isEnabled(), "plati.btn_phone:", w2.btn_phone.isEnabled())
    assert w1.btn_phone.isEnabled() == enabled
    assert w2.btn_phone.isEnabled() == enabled


# Legacy 'edit' account (implies add) -> phone dialog requests token with the real username
def test_phone_legacy_edit_real_user(env, monkeypatch):
    import mobile_upload
    import mobile_upload_dialog
    calls = []

    def fake(entity_type, entity_id, issued_by="main", *a, **k):
        calls.append(issued_by)
        raise RuntimeError("stop")
    monkeypatch.setattr(mobile_upload, "request_token", fake)
    env.login(["edit"])
    try:
        mobile_upload_dialog.PhoneUploadDialog(None, "concesionar", 1, "x")
    except RuntimeError:
        pass
    assert calls == [access_session.current_user()["username"]]


# view-only account changes QR base URL (target of all printed QR codes) from the QR page in UserModeWindow
def test_qr_base_url_view_only(env):
    db.set_setting("qr_base_url", "https://cimitir.example.ro")
    env.login(["cfg"])
    assert not access_session.can("manage_config") and not access_session.can("edit")
    from rapoarte_dialog import QrSettingsDialog
    d = QrSettingsDialog(None)
    d.txt_url.setText("https://evil.example.com")
    d._save()
    print("qr_base_url after view-only save:", db.get_setting("qr_base_url"))
    assert db.get_setting("qr_base_url") == "https://cimitir.example.ro", "view-only user changed qr_base_url"


# view-only account changes public QR fields
def test_qr_public_fields_view_only(env):
    import qr_public_data as qpd
    db.set_setting("qr_public_fields", "cod_loc")
    env.login(["cfg"])
    from qr_content_settings_dialog import QrContentSettingsDialog
    d = QrContentSettingsDialog(None, preview_cod_loc="A-1")
    for cb in d._checkboxes.values():
        if cb.isEnabled():
            cb.setChecked(True)
    d._save()
    print("qr_public_fields after view-only save:", db.get_setting("qr_public_fields"))
    assert db.get_setting("qr_public_fields") == "cod_loc", "view-only user changed qr_public_fields"
