"""T4 (iteration_4): probes for remaining rights gaps in MAIN (synthetic SQLite in tmp_path; no network)."""
import json

import pytest

from test_p3_desktop_lacune2_regresii import env  # noqa: F401
import access_session  # noqa: E402
import database as db  # noqa: E402


# QR fixes: buttons gated on edit in QrManagementWidget
@pytest.mark.parametrize("rights,enabled", [(["cfg"], False), (["cfg", "add"], False), (["cfg", "edit"], True)])
def test_qr_buttons_gated(env, rights, enabled):
    env.login(rights)
    from PySide6.QtWidgets import QPushButton
    import qr_management_widget
    w = qr_management_widget.QrManagementWidget(None)
    btns = {b.text(): b.isEnabled() for b in w.findChildren(QPushButton)}
    url = [v for k, v in btns.items() if "URL" in k or "url" in k.lower()]
    content = [v for k, v in btns.items() if "conținut" in k]
    print(rights, btns)
    assert url and content
    assert url[0] == enabled and content[0] == enabled


# QR save allowed for edit
def test_qr_url_save_with_edit(env):
    db.set_setting("qr_base_url", "https://a.example.ro")
    env.login(["cfg", "edit"])
    from rapoarte_dialog import QrSettingsDialog
    d = QrSettingsDialog(None)
    d.txt_url.setText("https://b.example.ro")
    d._save()
    assert db.get_setting("qr_base_url") == "https://b.example.ro"


# Probe: rapoarte_dialog btn_qr_settings gating (UI consistency)
def test_rapoarte_btn_qr_settings_gated(env):
    env.login(["cfg"])
    import rapoarte_dialog
    try:
        d = rapoarte_dialog.RapoarteDialog(None)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"cannot build RapoarteDialog: {exc}")
    print("btn_qr_settings enabled for view-only:", d.btn_qr_settings.isEnabled())


# NEW GAP probe: operator without manage_config (edit/add) changes global AI settings (Gemini key) via toolbar / ScanDialog
def test_ai_settings_view_only(env, tmp_path, monkeypatch):
    from ai_scanare import config as ai_config
    monkeypatch.setattr(ai_config, "CONFIG_FILE", tmp_path / "ai_config.json")
    ai_config.save_config({"api_key": "ORIG-KEY", "mode": ai_config.MODE_AI, "model": "m1", "provider": "google"})
    env.login(["cfg", "add", "edit"])
    assert not access_session.can("manage_config")
    from ai_scanare.ai_settings_dialog import AISettingsDialog
    d = AISettingsDialog(None)
    d.key_edit.setText("ATTACKER-KEY")
    d.mode_combo.setCurrentIndex(d.mode_combo.findData(ai_config.MODE_AI))
    d.model_combo.setCurrentIndex(max(0, d.model_combo.findData("m1")))
    if d.model_combo.currentData() == ai_config.MODEL_AUTO:
        d.model_combo.addItem("m1", "m1")
        d.model_combo.setCurrentIndex(d.model_combo.findData("m1"))
    d._save()
    print("effective AI key after save:", ai_config.get_api_key())
    assert ai_config.get_api_key() == "ORIG-KEY", "operator without manage_config changed the Gemini API key"
