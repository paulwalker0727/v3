"""Configurare Cloud simplificată (MAIN): doar nume/adresă/mediu + stări; fără câmpuri PostgreSQL / Secret Key / cheie brută; secretele existente rămân neatinse."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="cimitir_test_cloud_simple_")
os.environ["CIMITIR_DATA_DIR"] = _TMP
os.environ["CIMITIR_LOG_DIR"] = os.path.join(_TMP, "logs")
os.environ.setdefault("CIMITIR_DB_ENGINE", "sqlite")
os.environ.setdefault("CIMITIR_MODE", "local")
os.environ.setdefault("DATABASE_URL", "")

DESKTOP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DESKTOP))
sys.path.insert(0, str(DESKTOP.parent / "shared"))

import pytest  # noqa: E402

import cimitir_registry as reg  # noqa: E402
import cimitir_secretstore as ss  # noqa: E402
import cloud_profiles as cp  # noqa: E402
import cloud_simple as simple  # noqa: E402
import database as db  # noqa: E402
from test_cloud_profiles import ALL_SECRETS, KEY, PW, REF, SK, FakeHttp, adv_fields, fields  # noqa: E402,F401


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "cimitir.db"))
    db.init_db()
    st = ss.DpapiSecretStore(tmp_path / "secrets", lambda b: b"F:" + bytes(reversed(b)), lambda b: bytes(reversed(b[2:])))
    monkeypatch.setattr(cp, "get_store", lambda: st)
    monkeypatch.setattr(reg, "current_binding", lambda: None)
    return st


@pytest.fixture()
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_status_not_configured_then_save_creates_active_profile(store):
    st = simple.status()
    assert (st.connection, st.pairing, st.cemetery_name, st.cloud_url) == (simple.CONN_NONE, simple.PAIR_NONE, "", "")
    p = simple.save("Cimitir Craiova", "https://cloud.example.ro/", "TEST", store=store)
    assert p.is_active and p.environment == "TEST" and p.api_base_url == "https://cloud.example.ro"
    assert not (p.db_host or p.db_user or p.db_name or p.project_ref or p.supabase_url) and not (p.db_password_ref or p.supabase_secret_ref or p.sync_key_ref)
    st = simple.status()
    assert (st.profile_name, st.environment, st.connection, st.connection_text) == ("Cimitir Craiova", "TEST", simple.CONN_UNCHECKED, "Neverificat")


def test_prod_needs_explicit_confirmation(store):
    with pytest.raises(cp.ConfirmationRequired):
        simple.save("P", "https://cloud.example.ro", "PROD", store=store)
    assert cp.list_profiles() == []
    p = simple.save("P", "https://cloud.example.ro", "PROD", confirm_production=True, store=store)
    assert p.environment == "PRODUCTION" and simple.status().environment == "PROD"


def test_update_keeps_hidden_fields_and_existing_secrets(store):
    adv = cp.create_profile(adv_fields(name="Admin server", environment="TEST", api_base_url="https://test.example.ro"),
                            {"db_password": PW, "supabase_secret_key": SK, "sync_key": KEY}, store)
    cp.activate_profile(adv.id)
    p = simple.save("Nume nou", "https://test.example.ro", "TEST", store=store)
    assert p.id == adv.id and p.name == "Nume nou"
    assert (p.db_host, p.db_user, p.db_name, p.project_ref, p.supabase_url) == (adv.db_host, adv.db_user, adv.db_name, adv.project_ref, adv.supabase_url)
    assert (p.db_password_ref, p.supabase_secret_ref, p.sync_key_ref) == (adv.db_password_ref, adv.supabase_secret_ref, adv.sync_key_ref)
    assert store.get(p.db_password_ref) == PW and store.get(p.supabase_secret_ref) == SK and store.get(p.sync_key_ref) == KEY


def test_changing_address_drops_stale_project_refs_but_not_secrets(store):
    adv = cp.create_profile(adv_fields(name="Admin server", environment="TEST", api_base_url="https://test.example.ro"), {"sync_key": KEY}, store)
    cp.activate_profile(adv.id)
    p = simple.save("Admin server", "https://alt.example.ro", "TEST", store=store)
    assert p.api_base_url == "https://alt.example.ro" and p.project_ref == "" and p.supabase_url == "" and store.get(p.sync_key_ref) == KEY


def test_invalid_address_rejected(store):
    with pytest.raises(cp.ValidationFailed):
        simple.save("X", "http://insecure.example.ro", "TEST", store=store)


def test_connection_states_without_pairing(store):
    simple.save("X", "https://test.example.ro", "TEST", store=store)
    assert simple.status(check=True, store=store, http=FakeHttp()).connection == simple.CONN_OK
    assert simple.status(check=True, store=store, http=FakeHttp(health=503)).connection == simple.CONN_ERROR
    ok, text = simple.test_connection(simple.current_profile(), store, FakeHttp())
    assert ok and "asociat" in text and "http" not in text


def test_pairing_state_and_cemetery_name_from_local_registry(store, monkeypatch, tmp_path):
    import cimitir_bootstrap as bs
    cat = reg.RegistryCatalog(tmp_path / "data")
    org = cat.create_organization("parohie", "Parohia Test", "Parohia Test")
    cem = cat.create_cemetery(org, "Cimitir Craiova")
    binding = reg.activate(cem, catalog=cat)
    import sqlite3
    with sqlite3.connect(binding.database) as c:
        c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    try:
        monkeypatch.setattr(reg, "current_binding", lambda: binding)
        monkeypatch.setattr(reg, "RegistryCatalog", lambda *a, **k: cat)
        assert simple.pairing_state() == {"pairing": simple.PAIR_NONE, "cemetery_name": ""}
        local = bs.LocalRegistry(binding)
        local.put(cloud_assoc_plan="{}")
        assert simple.pairing_state()["pairing"] == simple.PAIR_INCOMPLETE
        local.clear("cloud_assoc_plan")
        local.put(sync_state="BOUND", cloud_assoc_unverified="1")
        assert simple.pairing_state()["pairing"] == simple.PAIR_UNVERIFIED
        local.clear("cloud_assoc_unverified")
        assert simple.pairing_state() == {"pairing": simple.PAIR_OK, "cemetery_name": "Cimitir Craiova"}
        st = simple.status()
        assert st.pairing_text == "Asociat" and st.cemetery_name == "Cimitir Craiova"
        assert cem not in str(st.as_dict()) and org not in str(st.as_dict())
    finally:
        reg.deactivate()


def test_dialog_shows_only_needed_fields_and_no_secret_inputs(store, qapp):
    from PySide6.QtWidgets import QComboBox, QLineEdit
    import cloud_simple_dialog as d
    dlg = d.CloudSimpleDialog(store=store)
    edits = [w.objectName() for w in dlg.findChildren(QLineEdit)]
    assert sorted(edits) == ["simple_name", "simple_url"]
    assert not any(w.echoMode() == QLineEdit.Password for w in dlg.findChildren(QLineEdit))
    assert [w.objectName() for w in dlg.findChildren(QComboBox)] == ["simple_env"]
    assert [dlg.cb_env.itemText(i) for i in range(dlg.cb_env.count())] == ["TEST", "PROD"]
    from PySide6.QtWidgets import QLabel
    text = " ".join(lbl.text() for lbl in dlg.findChildren(QLabel)).lower()
    for forbidden in ("host", "utilizator postgres", "parola", "secret key", "cheie sincronizare"):
        assert forbidden not in text.replace("cheile de acces", "")
    assert dlg.btn_adv is None and dlg.lbl_conn.text() == "Neconfigurat" and dlg.lbl_pair.text() == "Neasociat" and dlg.lbl_cem.text() == "—"


def test_dialog_save_and_check_updates_states(store, qapp, monkeypatch):
    import cloud_simple_dialog as d
    dlg = d.CloudSimpleDialog(store=store, http=FakeHttp())
    dlg.ed_name.setText("Craiova")
    dlg.ed_url.setText("https://test.example.ro")
    assert dlg.save()
    assert dlg.lbl_conn.text() == "Neverificat" and simple.current_profile().name == "Craiova"
    monkeypatch.setattr(d, "run_task", lambda owner, fn, ok, fail: ok(fn()))
    dlg.check()
    assert dlg.lbl_conn.text() == "Conectat"
    dlg._http = FakeHttp(health=503)
    dlg.check()
    assert dlg.lbl_conn.text() == "Eroare de conexiune"


def test_dialog_prod_declined_changes_nothing(store, qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    import cloud_simple_dialog as d
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.No))
    dlg = d.CloudSimpleDialog(store=store)
    dlg.ed_url.setText("https://cloud.example.ro")
    dlg.cb_env.setCurrentText("PROD")
    assert dlg.save() is False and cp.list_profiles() == []


def test_advanced_button_only_for_server_admin(store, qapp, monkeypatch):
    import cloud_simple_dialog as d
    monkeypatch.setenv(simple.ENV_ADVANCED, "1")
    assert d.CloudSimpleDialog(store=store).btn_adv is not None


def test_sync_window_hides_raw_key_for_normal_user(store, qapp, monkeypatch):
    from cloud_sync_window import CloudSyncWindow
    w = CloudSyncWindow()
    w.show()
    assert not w.txt_key.isVisible() and w.txt_url.isVisible()
    assert w.btn_profiles.text() == "Configurare Cloud…"
    w.close()
    monkeypatch.setenv(simple.ENV_ADVANCED, "1")
    w2 = CloudSyncWindow()
    w2.show()
    assert w2.txt_key.isVisible()
    w2.close()


def test_no_secret_in_status_or_logs(store):
    p = cp.create_profile(adv_fields(name="S", environment="TEST", api_base_url="https://test.example.ro"), {"db_password": PW, "supabase_secret_key": SK, "sync_key": KEY}, store)
    cp.activate_profile(p.id)
    blob = str(simple.status(check=True, store=store, http=FakeHttp()).as_dict()) + simple.test_connection(p, store, FakeHttp())[1]
    assert not any(s in blob for s in ALL_SECRETS)
