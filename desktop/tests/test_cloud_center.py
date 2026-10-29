"""Centrul de configurare Cloud (MAIN): stare generală, acces doar pentru administrator, inițializarea unică a administratorului Cloud, interfața Qt (offscreen),
licența neschimbată, funcțiile neimplementate marcate explicit."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="cimitir_test_cloud_center_")
os.environ["CIMITIR_DATA_DIR"] = _TMP
os.environ["CIMITIR_LOG_DIR"] = os.path.join(_TMP, "logs")
os.environ.setdefault("CIMITIR_DB_ENGINE", "sqlite")
os.environ.setdefault("CIMITIR_MODE", "local")
os.environ.setdefault("DATABASE_URL", "")

DESKTOP = Path(__file__).resolve().parent.parent
REPO = DESKTOP.parent
sys.path.insert(0, str(DESKTOP))
sys.path.insert(0, str(REPO / "shared"))

import pytest  # noqa: E402

import cimitir_cloud_admin_client as adc  # noqa: E402
import cimitir_registry as reg  # noqa: E402
import cimitir_secretstore as ss  # noqa: E402
import cloud_center as cc  # noqa: E402
import cloud_profiles as cp  # noqa: E402
import cloud_simple as simple  # noqa: E402
import cloud_supabase as sb  # noqa: E402
import database as db  # noqa: E402

REF, HOST = "abcdefghijklmnopqrst", "aws-0-eu-central-1.pooler.supabase.com"
BASE_COMMIT = "60f292c"


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


def wait(qapp, cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end and not cond():
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    assert cond(), "timeout"


class FakeAdmin:
    state = {"initialized": False, "bootstrap_open": True}
    calls: list = []

    def __init__(self, base_url):
        self.base_url = base_url

    def status(self):
        return dict(FakeAdmin.state)

    def bootstrap(self, token, email, password, display_name="", organization_uid=None, organization_name="", cemetery_uid=None, cemetery_name=""):
        FakeAdmin.calls.append(dict(token=token, email=email, org=organization_uid, org_name=organization_name, cem=cemetery_uid))
        if token != "tok-valid-0123456789abcdef":
            raise adc.AdminApiError("token de inițializare invalid", "FORBIDDEN", 403)
        FakeAdmin.state = {"initialized": True, "bootstrap_open": False}
        return {"identity_uid": "i1", "organization_uid": organization_uid or "o1", "cemetery_uid": cemetery_uid, "email": email}

    def close(self):
        pass


@pytest.fixture(autouse=True)
def no_modal_boxes(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.No))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Cancel))


@pytest.fixture(autouse=True)
def reset_admin():
    FakeAdmin.state = {"initialized": False, "bootstrap_open": True}
    FakeAdmin.calls = []


# ------------------------------------------------------------------ licența și autentificarea locală rămân neschimbate
def test_license_and_license_generator_files_unchanged_vs_base():
    try:
        subprocess.run(["git", "cat-file", "-e", BASE_COMMIT], cwd=REPO, check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("istoricul Git de bază nu este disponibil")
    paths = ["desktop/license", "desktop/tools/license_manager.py", "desktop/login_window.py", "desktop/auth.py", "shared/cimitir_auth.py"]
    out = subprocess.run(["git", "diff", "--name-only", BASE_COMMIT, "--"] + paths, cwd=REPO, capture_output=True, text=True, check=True).stdout
    assert out.strip() == "", f"fișiere interzise modificate: {out}"


def test_status_reads_license_without_modifying_it(store, monkeypatch):
    from license import validator

    class R:
        ok, motiv, client, expira = True, "OK", "Parohia Test", None
    monkeypatch.setattr(validator, "verifica_licenta_curenta", lambda: R)
    items = {i.key: i for i in cc.general_status()}
    assert list(items) == ["license", "local_db", "supabase", "backend", "pairing", "sync"]
    assert items["license"].state == cc.ST_OK and "Parohia Test" in items["license"].text
    R.ok, R.motiv = False, "Licență expirată la 2020-01-01"
    assert cc.general_status()[0].state == cc.ST_ERROR


def test_status_follows_supabase_and_backend_state(store):
    items = {i.key: i for i in cc.general_status()}
    assert items["supabase"].state == cc.ST_NONE and items["backend"].state == cc.ST_NONE and items["local_db"].state == cc.ST_OK
    sb.save_to_profile(sb.SupabaseInput(REF, "", HOST, 5432, f"postgres.{REF}"), "TEST", store=store)
    simple.save("P", "https://cloud.example.ro", "TEST", store=store)
    items = {i.key: i for i in cc.general_status()}
    assert items["supabase"].state == cc.ST_WARN and "neverificat" in items["supabase"].text and items["backend"].state == cc.ST_WARN
    cc.remember_supabase_check(REF, {"ok": True, "pending": []})
    assert {i.key: i for i in cc.general_status()}["supabase"].state == cc.ST_OK
    cc.remember_supabase_check(REF, {"ok": True, "pending": ["026_x.sql"]})
    assert {i.key: i for i in cc.general_status()}["supabase"].state == cc.ST_WARN
    cc.remember_supabase_check(REF, {"ok": False})
    assert {i.key: i for i in cc.general_status()}["supabase"].state == cc.ST_ERROR


class Http:
    def __init__(self, code=200, body=None, exc=None):
        self.code, self.body, self.exc = code, body if body is not None else {"ok": True}, exc

    def get(self, url, **kw):
        if self.exc:
            raise self.exc

        class Resp:
            status_code = self.code
            json = staticmethod(lambda: self.body)
        return Resp


def test_backend_health_variants(store):
    assert cc.backend_health("https://c.example.ro", Http())["ready"]
    assert not cc.backend_health("https://c.example.ro", Http(503))["ready"]
    import requests
    assert not cc.backend_health("https://c.example.ro", Http(exc=requests.ConnectionError()))["reachable"]
    assert not cc.backend_health("http://exemplu-nesigur.ro", Http())["reachable"]


# ------------------------------------------------------------------ inițializarea unică a administratorului Cloud
def test_admin_state_variants():
    st = cc.admin_state("https://c.example.ro", FakeAdmin)
    assert st.can_initialize
    FakeAdmin.state = {"initialized": True, "bootstrap_open": False}
    assert not cc.admin_state("https://c.example.ro", FakeAdmin).can_initialize
    FakeAdmin.state = {"initialized": False, "bootstrap_open": False}
    st = cc.admin_state("https://c.example.ro", FakeAdmin)
    assert not st.can_initialize and "token" in st.detail


def test_initialize_admin_once_with_local_identity(store, monkeypatch):
    monkeypatch.setattr(cc, "local_identity", lambda: {"organization_uid": "11111111-1111-4111-8111-111111111111", "organization_name": "Parohia X",
                                                       "cemetery_uid": "22222222-2222-4222-8222-222222222222", "cemetery_name": "Cimitir X"})
    res = cc.initialize_cloud_admin("https://c.example.ro", "tok-valid-0123456789abcdef", "pr@parohie.ro", "Parola-Lunga-123", "Pr", FakeAdmin, lambda: True)
    assert res["email"] == "pr@parohie.ro"
    assert FakeAdmin.calls[0]["org"] == "11111111-1111-4111-8111-111111111111" and FakeAdmin.calls[0]["org_name"] == "Parohia X"
    with pytest.raises(adc.AdminApiError, match="nu mai este permisă"):                    # a doua inițializare: refuzată înainte de orice cerere
        cc.initialize_cloud_admin("https://c.example.ro", "tok-valid-0123456789abcdef", "alt@x.ro", "Parola-Lunga-123", "", FakeAdmin, lambda: True)
    assert len(FakeAdmin.calls) == 1
    assert "tok-valid" not in Path(db.DB_PATH).read_bytes().decode("latin-1") and "Parola-Lunga" not in Path(db.DB_PATH).read_bytes().decode("latin-1")


def test_initialize_admin_refused_without_valid_license_or_with_bad_input(store):
    with pytest.raises(adc.AdminApiError, match="Licența"):
        cc.initialize_cloud_admin("https://c.example.ro", "t", "a@b.ro", "Parola-Lunga-123", "", FakeAdmin, lambda: False)
    with pytest.raises(adc.AdminApiError):
        cc.initialize_cloud_admin("https://c.example.ro", "t", "nu-este-email", "scurta", "", FakeAdmin, lambda: True)
    with pytest.raises(adc.AdminApiError, match="invalid"):
        cc.initialize_cloud_admin("https://c.example.ro", "token-gresit", "a@b.ro", "Parola-Lunga-123", "", FakeAdmin, lambda: True)
    assert cc.validate_admin_form("a@b.ro", "aaaaaaaaaaaa", "aaaaaaaaaaaa", "t") == ["Parola este prea slabă."]


def test_unavailable_features_are_explicit():
    assert set(cc.UNAVAILABLE) == {"backend_provisioning", "bootstrap_token_delivery", "portable_backup", "initial_sync"}
    assert all(v["stage"].startswith("ETAPA") and v["reason"] for v in cc.UNAVAILABLE.values())


# ------------------------------------------------------------------ interfața Qt (offscreen)
def _dialog(qapp, store, allowed=True, **kw):
    from cloud_center_dialog import CloudCenterDialog
    return CloudCenterDialog(None, can_manage=lambda: allowed, store=store, admin_factory=FakeAdmin, **kw)


def test_dialog_structure_and_unavailable_markers(qapp, store):
    from PySide6.QtWidgets import QFrame
    d = _dialog(qapp, store)
    assert [d.tabs.tabText(i) for i in range(d.tabs.count())] == ["A. Stare generală", "B. Supabase", "C. Backend Cloud", "D. Administratori", "E. Backup", "F. Mobile", "G. Provizionare"]
    assert set(d._status_labels) == {"license", "local_db", "supabase", "backend", "pairing", "sync"}
    assert all(lbl.text() not in ("", "…") for lbl in d._status_labels.values())
    boxes = {f.objectName() for f in d.findChildren(QFrame) if f.objectName().startswith("unavailable_")}
    assert boxes == {"unavailable_backend_provisioning", "unavailable_bootstrap_token_delivery", "unavailable_portable_backup", "unavailable_initial_sync"}
    assert "INDISPONIBIL" in d.findChild(QFrame, "unavailable_backend_provisioning").findChildren(__import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel)[0].text()


def test_dialog_blocks_every_action_for_non_admin(qapp, store, monkeypatch):
    called = []
    monkeypatch.setattr(sb, "verify_project", lambda *a, **k: called.append("verify"))
    monkeypatch.setattr(sb, "local_backup", lambda: called.append("backup"))
    d = _dialog(qapp, store, allowed=False)
    d.supabase.ed_pw.setText("x" * 10)
    d.supabase.verify()
    d.supabase.install()
    assert d.save_backend() is False and d.supabase.save_profile() is False
    d.backup_now()
    d.check_admin_state()
    d.init_admin()
    d.open_associate()
    assert called == [] and cp.list_profiles() == []


def test_dialog_backend_save_and_prod_confirmation(qapp, store, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    d = _dialog(qapp, store)
    d.be_name.setText("Parohia Test")
    d.be_url.setText("https://cloud.example.ro")
    assert d.save_backend() is True and simple.current_profile().environment == "TEST"
    d.be_env.setCurrentText("PROD")
    assert d.save_backend() is False and simple.current_profile().environment == "TEST"


def test_dialog_supabase_verify_never_keeps_password_and_status_updates(qapp, store, monkeypatch):
    seen = {}

    def fake_verify(inp, pw, connect=None, mig_dir=None):
        seen["pw"] = pw
        r = sb.VerifyResult(reachable=True, empty_project=True, plan={"steps": [{"name": "001_initial.sql", "action": "apply", "why": "x", "version": 1}],
                                                                      "blocked": [], "executable": True, "confirm_phrase": "APLIC 001"})
        r.add("Proiect Supabase", "OK", "ok")
        return r
    monkeypatch.setattr(sb, "verify_project", fake_verify)
    d = _dialog(qapp, store)
    t = d.supabase
    t.ed_uri.setText(f"postgresql://postgres.{REF}:Pa55w0rd-secret@{HOST}:5432/postgres")
    t.fill_from_uri()
    assert t.ed_ref.text() == REF and t.ed_uri.text() == "" and t.ed_url.text() == f"https://{REF}.supabase.co"
    t.verify()
    wait(qapp, lambda: t.result is not None)
    assert seen["pw"] == "Pa55w0rd-secret" and t.ed_pw.text() == ""
    assert "Pa55w0rd" not in t.log.toPlainText()
    assert "Lipsesc migrările" in t.log.toPlainText() and "001_initial.sql" in t.log.toPlainText()
    t.clear_auth()
    assert not t.session.active
    d.accept()


def test_dialog_install_flow_requires_preview_backup_and_confirmation(qapp, store, tmp_path, monkeypatch):
    calls = {}
    res = sb.VerifyResult(reachable=True, empty_project=True, bucket_state="ok",
                          plan={"steps": [{"name": "001_initial.sql", "action": "apply", "why": "x", "version": 1}], "blocked": [], "executable": True,
                                "confirm_phrase": "APLIC 001"})
    monkeypatch.setattr(sb, "local_backup", lambda: tmp_path)
    monkeypatch.setattr(sb, "apply_schema", lambda *a, **k: calls.setdefault("apply", a[3]) and {"schema": {"executed": True, "log": ["001 aplicată"]}, "bucket": None})
    monkeypatch.setattr(sb, "verify_project", lambda *a, **k: res)
    d = _dialog(qapp, store)
    t = d.supabase
    t.ed_ref.setText(REF)
    t.ed_host.setText(HOST)
    t.ed_user.setText(f"postgres.{REF}")
    t.ed_pw.setText("Pa55w0rd-secret")
    t.install()                                                   # fără verificare prealabilă: nu face nimic
    assert "apply" not in calls
    t.result = res
    t.ask_preview = lambda pv: False                              # preview refuzat -> nimic
    t.install()
    assert "apply" not in calls
    t.ask_preview = lambda pv: True
    t.ask_confirmation = lambda phrase: ""                        # confirmare anulată -> nimic
    t.install()
    wait(qapp, lambda: not t._busy)
    assert "apply" not in calls and "anulată" in t.log.toPlainText()
    t.ask_confirmation = lambda phrase: phrase
    t.install()
    wait(qapp, lambda: "apply" in calls)
    assert calls["apply"] == "APLIC 001"
    wait(qapp, lambda: "Instalare finalizată" in t.log.toPlainText())


def test_dialog_admin_init_flow(qapp, store, monkeypatch):
    monkeypatch.setattr(cc, "_license_item", lambda: cc.Item("license", "Licență", cc.ST_OK, "ok"))
    simple.save("P", "https://cloud.example.ro", "TEST", store=store)
    d = _dialog(qapp, store)
    d.check_admin_state()
    wait(qapp, lambda: "deschisă" in d.ad_state.text())
    d.ad_email.setText("pr@parohie.ro")
    d.ad_pw.setText("Parola-Lunga-123")
    d.ad_pw2.setText("Parola-Lunga-123")
    d.ad_token.setText("tok-valid-0123456789abcdef")
    assert not d.ad_go.isEnabled()
    d.ad_confirm.setChecked(True)
    assert d.ad_go.isEnabled()
    d.init_admin()
    assert d.ad_pw.text() == "" and d.ad_token.text() == ""         # câmpurile secrete se golesc imediat
    wait(qapp, lambda: "a fost creat" in d.ad_log.toPlainText())
    assert not d.ad_go.isEnabled()
    d.check_admin_state()
    wait(qapp, lambda: "nu mai este permisă" in d.ad_state.text())


def test_capture_screenshots_when_requested(qapp, store, monkeypatch):
    out = os.environ.get("CIMITIR_SCREENSHOT_DIR")
    if not out:
        pytest.skip("CIMITIR_SCREENSHOT_DIR nesetat")
    monkeypatch.setattr(cc, "_license_item", lambda: cc.Item("license", "Licență", cc.ST_OK, "Validă — Parohia Demo, expiră: permanent"))
    simple.save("Cloud Parohia Demo", "https://parohia-demo.cloud.exemplu.ro", "TEST", store=store)
    sb.save_to_profile(sb.SupabaseInput(REF, "", HOST, 5432, f"postgres.{REF}"), "TEST", store=store)
    cc.remember_supabase_check(REF, {"ok": True, "pending": ["025_admin_provision_registry.sql", "026_registry_requests.sql"]})
    d = _dialog(qapp, store)
    d.resize(960, 680)
    d.show()
    d.supabase.log.setPlainText("[OK] Coerența datelor proiectului — referință, URL și utilizator se potrivesc\n[OK] Conexiune și autentificare PostgreSQL — conectat (tranzacție doar-citire)\n"
                                "[OK] Conexiune criptată (TLS)\n[OK] Proiect Supabase — schemele auth și storage există\n"
                                "[ATENȚIE] Schema Cimitir — 2 migrări lipsă: 025_admin_provision_registry.sql, 026_registry_requests.sql\n"
                                "[ATENȚIE] Storage — bucket „cimitir-fisiere” — lipsește; va fi creat PRIVAT la instalare")
    Path(out).mkdir(parents=True, exist_ok=True)
    for i, name in enumerate(["stare", "supabase", "backend", "administratori", "backup", "mobile"]):
        d.tabs.setCurrentIndex(i)
        qapp.processEvents()
        d.grab().save(str(Path(out) / f"{i + 1}_{name}.png"))
    d.close()


def test_dialog_switching_env_creates_separate_profile_and_keeps_test(qapp, store, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    d = _dialog(qapp, store)
    d.be_name.setText("Parohia Test")
    d.be_url.setText("https://test.example.ro")
    assert d.save_backend() is True
    test_id = simple.current_profile().id
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Yes))
    d.be_env.setCurrentText("PROD")
    d.be_url.setText("https://prod.example.ro")
    assert d.save_backend() is True
    cur = simple.current_profile()
    assert cur.environment == "PRODUCTION" and cur.id != test_id and cur.api_base_url == "https://prod.example.ro"
    kept = cp.get_profile(test_id)
    assert kept.environment == "TEST" and kept.api_base_url == "https://test.example.ro" and not kept.is_active
