"""Profile de conexiune Cloud/Server (MAIN): CRUD, un singur profil activ, confirmări, secrete doar în SecretStore, migrarea cheii vechi,
Test Connection read-only (mock/fixture pentru PostgreSQL/Storage; LIVE nerulat), mascare UI."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="cimitir_test_cloud_profiles_")
os.environ["CIMITIR_DATA_DIR"] = _TMP
os.environ["CIMITIR_LOG_DIR"] = os.path.join(_TMP, "logs")
os.environ.setdefault("CIMITIR_DB_ENGINE", "sqlite")
os.environ.setdefault("CIMITIR_MODE", "local")
os.environ.setdefault("DATABASE_URL", "")

DESKTOP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DESKTOP))
sys.path.insert(0, str(DESKTOP.parent / "shared"))

import pytest  # noqa: E402
import requests  # noqa: E402

import cimitir_secretstore as ss  # noqa: E402
import cloud_profiles as cp  # noqa: E402
import cloud_sync  # noqa: E402
import database as db  # noqa: E402

KEY = "cimitir." + "a" * 43
KEY2 = "cimitir." + "b" * 43
PW = "S3cr3t-Parola-DB-9981"
SK = "sb_secret_" + "Z" * 30
REF = "abcdefghijklmnopqrst"
SUPA = f"https://{REF}.supabase.co"
HOST = "aws-0-eu.pooler.supabase.com"
USER = f"postgres.{REF}"
ALL_SECRETS = (KEY, KEY2, PW, SK)


def _fake_protect(b: bytes) -> bytes:
    return b"DPAPI-FAKE:" + bytes(reversed(b))


def _fake_unprotect(b: bytes) -> bytes:
    assert b.startswith(b"DPAPI-FAKE:")
    return bytes(reversed(b[len(b"DPAPI-FAKE:"):]))


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "cimitir.db"))
    db.init_db()
    st = ss.DpapiSecretStore(tmp_path / "secrets", _fake_protect, _fake_unprotect)
    monkeypatch.setattr(cp, "get_store", lambda: st)
    return st


def fields(**kw):
    base = {"name": "Server test", "environment": "TEST", "api_base_url": "https://test.example.ro", "project_ref": "",
            "supabase_url": "", "db_host": "", "db_port": "5432", "db_name": "", "db_user": "", "sslmode": "require",
            "storage_bucket": ""}
    base.update(kw)
    return base


def adv_fields(**kw):
    base = dict(name="Advanced", environment="CUSTOM", project_ref=REF, supabase_url=SUPA, db_host=HOST, db_name="postgres",
                db_user=USER, storage_bucket="cimitir-private")
    base.update(kw)
    return fields(**base)


def raw_bytes(path) -> bytes:
    out = b""
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(path) + suffix)
        if p.exists():
            out += p.read_bytes()
    return out


def assert_no_plaintext(tmp_path, caplog=None, capsys=None, secrets=ALL_SECRETS):
    blob = raw_bytes(tmp_path / "cimitir.db")
    conn = db.get_connection()
    try:
        dump = repr([tuple(r) for r in conn.execute("SELECT * FROM cloud_connection_profiles")]) + \
            repr([tuple(r) for r in conn.execute("SELECT * FROM settings")]) + \
            repr([tuple(r) for r in conn.execute("SELECT * FROM audit_log")])
    finally:
        conn.close()
    for s in secrets:
        assert s.encode() not in blob, "plaintext în fișierele SQLite"
        assert s not in dump
        if caplog is not None:
            assert s not in caplog.text
        if capsys is not None:
            out = capsys.readouterr()
            assert s not in out.out + out.err


# ------------------------------------------------------------------ CRUD / activare
def test_create_edit_activate_delete(store):
    p = cp.create_profile(fields(), {"sync_key": KEY}, store)
    assert p.sync_key_ref and not p.is_active and p.environment == "TEST"
    p2 = cp.update_profile(p.id, fields(name="Server test 2", api_base_url="https://test2.example.ro"), None, store)
    assert p2.name == "Server test 2" and p2.api_base_url == "https://test2.example.ro" and p2.sync_key_ref == p.sync_key_ref
    assert store.get(p.sync_key_ref) == KEY
    act = cp.activate_profile(p.id)
    assert act.is_active and cp.active_profile().id == p.id
    cp.deactivate_profile(p.id)
    assert cp.active_profile() is None
    cp.delete_profile(p.id, store=store)
    assert cp.get_profile(p.id) is None and not store.exists(p.sync_key_ref)


def test_single_active_profile(store):
    a = cp.create_profile(fields(name="A"), None, store)
    b = cp.create_profile(fields(name="B", environment="CUSTOM"), None, store)
    cp.activate_profile(a.id)
    cp.activate_profile(b.id)
    assert [p.name for p in cp.list_profiles() if p.is_active] == ["B"]
    conn = db.get_connection()
    try:
        with pytest.raises(Exception):  # indexul unic parțial refuză două active
            conn.execute("UPDATE cloud_connection_profiles SET is_active=1 WHERE id=?", (a.id,))
    finally:
        conn.close()


def test_duplicate_name_and_validation(store):
    cp.create_profile(fields(name="Dup"), None, store)
    with pytest.raises(cp.ValidationFailed):
        cp.create_profile(fields(name="dup"), None, store)
    with pytest.raises(cp.ValidationFailed):
        cp.create_profile(fields(name="X", environment="STAGING"), None, store)
    with pytest.raises(cp.ValidationFailed):
        cp.create_profile(fields(name="X", api_base_url="http://evil.example.ro"), None, store)
    with pytest.raises(cp.ValidationFailed):
        cp.create_profile(fields(name="X", project_ref="zzzzzzzzzzzzzzzzzzzz", supabase_url=SUPA), None, store)
    with pytest.raises(cp.ValidationFailed):
        cp.create_profile(fields(name="Y"), {"sync_key": "invalid"}, store)
    assert [p.name for p in cp.list_profiles()] == ["Dup"]


def test_test_and_custom_activate_without_confirmation(store):
    for env in ("TEST", "CUSTOM"):
        p = cp.create_profile(fields(name=env, environment=env), None, store)
        assert cp.activate_profile(p.id).is_active


def test_production_requires_explicit_confirmation(store):
    p = cp.create_profile(fields(name="Prod", environment="PRODUCTION"), None, store)
    with pytest.raises(cp.ConfirmationRequired):
        cp.activate_profile(p.id)
    assert cp.active_profile() is None
    assert cp.activate_profile(p.id, confirm_production=True).is_active


def test_delete_active_requires_confirmation(store):
    p = cp.create_profile(fields(), {"sync_key": KEY}, store)
    cp.activate_profile(p.id)
    with pytest.raises(cp.ConfirmationRequired):
        cp.delete_profile(p.id, store=store)
    assert cp.get_profile(p.id) is not None and store.get(p.sync_key_ref) == KEY
    cp.delete_profile(p.id, confirm_active=True, store=store)
    assert cp.get_profile(p.id) is None and cp.active_profile() is None


# ------------------------------------------------------------------ secrete
def test_secrets_only_in_secretstore_zero_plaintext(store, tmp_path, caplog, capsys):
    p = cp.create_profile(adv_fields(), {"db_password": PW, "supabase_secret_key": SK, "sync_key": KEY}, store)
    assert (p.db_password_ref, p.supabase_secret_ref, p.sync_key_ref) == tuple(
        f"cimitir/cloud/profiles/{p.id}/{k}" for k in ("db_password", "supabase_secret_key", "sync_key"))
    assert store.get(p.db_password_ref) == PW and store.get(p.supabase_secret_ref) == SK
    for f in (tmp_path / "secrets").iterdir():  # protejate (nu plaintext) pe disc
        assert not any(s.encode() in f.read_bytes() for s in ALL_SECRETS)
    cp.update_profile(p.id, adv_fields(), {"db_password": PW + "x"}, store)
    cp.activate_profile(p.id)
    cp.test_connection(p, "advanced", store, pg_connect=lambda **kw: (_ for _ in ()).throw(RuntimeError(f"password {kw['password']} bad")),
                       storage_factory=lambda *a: (_ for _ in ()).throw(RuntimeError(a[1])))
    assert_no_plaintext(tmp_path, caplog, capsys, ALL_SECRETS + (PW + "x",))
    conn = db.get_connection()
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(cloud_connection_profiles)")]
    conn.close()
    assert "db_password" not in cols and "supabase_secret_key" not in cols


def test_secret_replace_failure_keeps_old_value_and_no_false_save(store):
    p = cp.create_profile(adv_fields(), {"db_password": PW}, store)
    real_set = store.set

    def bad_set(name, value):
        real_set(name, "corrupt")
        raise ss.SecretStoreError("boom")

    store.set = bad_set
    try:
        with pytest.raises(cp.ProfileError):
            cp.update_profile(p.id, adv_fields(name="renamed"), {"db_password": "N3w-Parola-0000000"}, store)
    finally:
        store.set = real_set
    assert cp.get_profile(p.id).name == "Advanced"


def test_create_rolls_back_secrets_when_row_insert_fails(store):
    cp.create_profile(fields(name="Same"), None, store)
    with pytest.raises(cp.ValidationFailed):
        cp.create_profile(fields(name="Same"), {"sync_key": KEY}, store)
    assert not list((store.root).glob("*")) if store.root.exists() else True


def test_delete_removes_all_secrets(store):
    p = cp.create_profile(adv_fields(), {"db_password": PW, "supabase_secret_key": SK, "sync_key": KEY}, store)
    refs = [p.db_password_ref, p.supabase_secret_ref, p.sync_key_ref]
    cp.delete_profile(p.id, store=store)
    assert not any(store.exists(r) for r in refs) and cp.get_profile(p.id) is None


def test_delete_secret_failure_does_not_report_profile_deleted(store):
    p = cp.create_profile(adv_fields(), {"db_password": PW, "supabase_secret_key": SK}, store)
    real_delete = store.delete

    def flaky(name):
        if name.endswith("supabase_secret_key"):
            raise OSError("disk locked")
        real_delete(name)

    store.delete = flaky
    with pytest.raises(cp.SecretDeleteError):
        cp.delete_profile(p.id, store=store)
    still = cp.get_profile(p.id)
    assert still is not None and still.supabase_secret_ref and store.get(p.supabase_secret_ref) == SK
    store.delete = real_delete
    cp.delete_profile(p.id, store=store)  # reluabil
    assert cp.get_profile(p.id) is None and not store.exists(p.supabase_secret_ref)


def test_delete_when_secretstore_unavailable_keeps_profile(store, monkeypatch):
    p = cp.create_profile(fields(), {"sync_key": KEY}, store)

    def unavailable():
        raise ss.SecretStoreUnavailable("indisponibil")

    monkeypatch.setattr(cp, "get_store", unavailable)
    with pytest.raises(cp.SecretDeleteError):
        cp.delete_profile(p.id)
    assert cp.get_profile(p.id) is not None


# ------------------------------------------------------------------ migrare cheie veche
def test_legacy_sync_key_migration(store, tmp_path):
    db.set_setting("cloud_api_url", "https://old.example.ro")
    db.set_setting("cloud_sync_key", KEY)
    assert cp.migrate_legacy_sync_key(store) == "migrated"
    assert store.get(cp.LEGACY_REF) == KEY
    assert db.get_setting("cloud_sync_key_ref") == cp.LEGACY_REF and db.get_setting("cloud_sync_key") is None
    assert KEY.encode() not in raw_bytes(tmp_path / "cimitir.db")
    assert cp.legacy_connection(store) == ("https://old.example.ro", KEY)
    assert cp.migrate_legacy_sync_key(store) == "nothing"


def test_legacy_migration_store_failure_keeps_plaintext_and_is_resumable(store, tmp_path):
    db.set_setting("cloud_sync_key", KEY)
    real_set = store.set
    store.set = lambda n, v: (_ for _ in ()).throw(OSError("dpapi down"))
    with pytest.raises(cp.ProfileError):
        cp.migrate_legacy_sync_key(store)
    store.set = real_set
    assert db.get_setting("cloud_sync_key") == KEY and not store.exists(cp.LEGACY_REF) and not db.get_setting("cloud_sync_key_ref")
    assert cp.legacy_connection(store)[1] == KEY  # sync-ul continuă cu valoarea veche
    assert cp.migrate_legacy_sync_key(store) == "migrated"
    assert db.get_setting("cloud_sync_key") is None


def test_legacy_migration_readback_mismatch_rolls_back(store):
    db.set_setting("cloud_sync_key", KEY)
    real_get = store.get
    store.get = lambda n: "altceva" if n == cp.LEGACY_REF and real_get(n) is not None and getattr(store, "_bad", True) else real_get(n)
    with pytest.raises(cp.ProfileError):
        cp.migrate_legacy_sync_key(store)
    store.get = real_get
    assert db.get_setting("cloud_sync_key") == KEY and not store.exists(cp.LEGACY_REF) and not db.get_setting("cloud_sync_key_ref")
    assert cp.migrate_legacy_sync_key(store) == "migrated"


def test_legacy_migration_failure_after_ref_keeps_plaintext(store, monkeypatch):
    db.set_setting("cloud_sync_key", KEY)
    real = cp._purge_setting
    monkeypatch.setattr(cp, "_purge_setting", lambda k: (_ for _ in ()).throw(OSError("db locked")))
    with pytest.raises(cp.ProfileError):
        cp.migrate_legacy_sync_key(store)
    assert db.get_setting("cloud_sync_key") == KEY
    monkeypatch.setattr(cp, "_purge_setting", real)
    assert cp.migrate_legacy_sync_key(store) == "migrated" and db.get_setting("cloud_sync_key") is None


def test_save_config_never_writes_plaintext_key(store, tmp_path):
    cloud_sync.save_config("https://old.example.ro", KEY, True)
    assert db.get_setting("cloud_sync_key") is None and store.get(cp.LEGACY_REF) == KEY
    assert KEY.encode() not in raw_bytes(tmp_path / "cimitir.db")
    cfg = cloud_sync.get_config()
    assert cfg["url"] == "https://old.example.ro" and cfg["key"] == KEY


def test_cloud_sync_uses_active_profile_and_deactivation(store):
    cloud_sync.save_config("https://old.example.ro", KEY, False)
    p = cp.create_profile(fields(api_base_url="https://prof.example.ro"), {"sync_key": KEY2}, store)
    assert cloud_sync.get_config()["url"] == "https://old.example.ro"
    cp.activate_profile(p.id)
    cfg = cloud_sync.get_config()
    assert (cfg["url"], cfg["key"]) == ("https://prof.example.ro", KEY2)
    cloud_sync.save_config("https://ignored.example.ro", "", True)  # profilul gestionează conexiunea
    assert cloud_sync.get_config()["url"] == "https://prof.example.ro"
    cp.deactivate_profile(p.id)
    assert (cloud_sync.get_config()["url"], cloud_sync.get_config()["key"]) == ("", "")


# ------------------------------------------------------------------ Test Connection
class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code, self._b = status, body if body is not None else {}

    def json(self):
        return self._b


class FakeHttp:
    def __init__(self, health=200, info=None, info_status=200):
        self.calls, self.health, self.info, self.info_status = [], health, info if info is not None else {"cemetery": "cimitir"}, info_status

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, dict(headers or {})))
        return FakeResp(self.health) if url.endswith("/api/health") else FakeResp(self.info_status, self.info)

    post = put = delete = request = patch = lambda self, *a, **k: (_ for _ in ()).throw(AssertionError("test-ul trebuie să fie read-only"))


def test_api_profile_normal_without_db_password_or_secret_key(store):
    p = cp.create_profile(fields(), {"sync_key": KEY}, store)
    assert not p.db_password_ref and not p.supabase_secret_ref and not p.advanced
    http = FakeHttp()
    rep = cp.test_connection(p, "api", store, http=http)
    assert rep.ok, rep.text()
    assert [c.name for c in rep.checks] == ["API reachable", "Tenant/device binding", "Cemetery binding"]
    for _, headers in http.calls:
        assert set(headers) <= {"X-Sync-Key", "X-Sync-Client"}
    assert all(u.startswith("https://test.example.ro/api/") for u, _ in http.calls)


def test_api_test_failures(store):
    p = cp.create_profile(fields(), {"sync_key": KEY}, store)
    assert not cp.test_connection(p, "api", store, http=FakeHttp(health=503)).ok
    assert not cp.test_connection(p, "api", store, http=FakeHttp(info_status=401)).ok
    rep = cp.test_connection(p, "api", store, http=FakeHttp(info={"cemetery": "alt-cimitir"}))
    assert not rep.ok and rep.checks[-1].name == "Cemetery binding" and rep.checks[-1].status == "FAIL"
    db.set_setting("cloud_bound_cemetery", "alt")
    assert not cp.test_connection(p, "api", store, http=FakeHttp()).ok  # legat local de alt cimitir
    no_key = cp.create_profile(fields(name="fără cheie"), None, store)
    assert not cp.test_connection(no_key, "api", store, http=FakeHttp()).ok

    class Down(FakeHttp):
        def get(self, *a, **k):
            raise requests.ConnectionError(f"x {KEY}")

    rep = cp.test_connection(p, "api", store, http=Down())
    assert not rep.ok and KEY not in rep.text()


class FakePg:
    def __init__(self, version=19, ssl=True, fail=None):
        self.version, self.ssl, self.fail = version, ssl, fail
        self.statements, self.read_only, self.closed, self.rolled_back, self.kwargs = [], False, False, False, {}

    def __call__(self, **kw):
        if self.fail:
            raise self.fail
        self.kwargs = kw
        return self

    def execute(self, sql, *a):
        self.statements.append(sql)
        assert self.read_only, "interogările trebuie să ruleze în tranzacție read-only"
        rows = {"SELECT 1": (1,), "schema_migrations": (self.version,), "pg_stat_ssl": (self.ssl,)}
        for k, v in rows.items():
            if k in sql:
                return type("C", (), {"fetchone": lambda s, v=v: v})()
        raise AssertionError(f"interogare neașteptată: {sql}")

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class FakeStorage:
    def __init__(self, info):
        self.info = info

    def __call__(self, url, key, bucket):
        assert (url, key, bucket) == (SUPA, SK, "cimitir-private")
        return self

    def bucket_info(self):
        return self.info


def _adv_profile(store, **kw):
    return cp.create_profile(adv_fields(**kw), {"db_password": PW, "supabase_secret_key": SK}, store)


def test_advanced_profile_pass_with_mocks_read_only(store):
    p = _adv_profile(store)
    pg = FakePg()
    rep = cp.test_connection(p, "advanced", store, pg_connect=pg, storage_factory=FakeStorage({"public": False}))
    assert rep.ok, rep.text()
    names = {c.name: c.status for c in rep.checks}
    assert names == {"sslmode": "OK", "PostgreSQL reachable": "OK", "schema_migrations max version": "OK", "TLS activ pe conexiune": "OK",
                     "project ref": "OK", "Storage bucket exists": "OK", "Bucket public=false": "OK"}
    assert pg.read_only and pg.rolled_back and pg.closed
    assert all(s.lstrip().upper().startswith("SELECT") for s in pg.statements)
    assert pg.kwargs["sslmode"] == "require" and pg.kwargs["password"] == PW and not pg.kwargs["autocommit"]
    assert PW not in rep.text() and SK not in rep.text()


@pytest.mark.parametrize("pg_kw,storage,fail_name", [
    ({"version": 17}, {"public": False}, "schema_migrations max version"),
    ({}, {"public": True}, "Bucket public=false"),
    ({}, None, "Storage bucket exists"),
    ({"fail": RuntimeError("password authentication failed for user")}, {"public": False}, "PostgreSQL reachable"),
    ({"ssl": False}, {"public": False}, "TLS activ pe conexiune"),
])
def test_advanced_failures(store, pg_kw, storage, fail_name):
    p = _adv_profile(store)
    rep = cp.test_connection(p, "advanced", store, pg_connect=FakePg(**pg_kw), storage_factory=FakeStorage(storage))
    assert not rep.ok and [c for c in rep.checks if c.name == fail_name][0].status == "FAIL"


def test_advanced_insecure_sslmode_and_project_ref_mismatch(store):
    p = _adv_profile(store, sslmode="disable")
    rep = cp.test_connection(p, "advanced", store, pg_connect=FakePg(), storage_factory=FakeStorage({"public": False}))
    assert {c.name: c.status for c in rep.checks}["sslmode"] == "FAIL"
    conn = db.get_connection()
    conn.execute("UPDATE cloud_connection_profiles SET project_ref='zzzzzzzzzzzzzzzzzzzz' WHERE id=?", (p.id,))
    conn.close()
    rep = cp.test_connection(cp.get_profile(p.id), "advanced", store, pg_connect=FakePg(), storage_factory=FakeStorage({"public": False}))
    assert {c.name: c.status for c in rep.checks}["project ref"] == "FAIL"


def test_advanced_without_secrets_fails_cleanly_and_never_logs_values(store):
    p = cp.create_profile(adv_fields(), None, store)
    rep = cp.test_connection(p, "advanced", store, pg_connect=FakePg(), storage_factory=FakeStorage({"public": False}))
    assert not rep.ok
    leak = cp.test_connection(cp.get_profile(p.id), "advanced", store,
                              pg_connect=FakePg(fail=RuntimeError("x")), storage_factory=FakeStorage(None))
    assert PW not in leak.text()


# ------------------------------------------------------------------ UI
@pytest.fixture()
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_ui_masking_and_replace_buttons(store, qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QInputDialog, QMessageBox
    import cloud_profiles_dialog as d
    p = cp.create_profile(adv_fields(), {"db_password": PW, "supabase_secret_key": SK, "sync_key": KEY}, store)
    dlg = d.CloudProfilesDialog()
    assert dlg._current == p.id
    for w in (dlg.f_pw, dlg.f_sk, dlg.f_sync):
        assert w.text() == "********" and w.isReadOnly()
    assert dlg.btn_pw.text() == "Înlocuiește parola" and dlg.btn_sk.text() == "Înlocuiește Secret Key"
    assert PW not in dlg.log.toPlainText() and SK not in dlg.f_sk.text()
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Noua-Parola-123456", True)))
    dlg.replace_secret("db_password")
    assert dlg.f_pw.text().startswith("********") and "Noua-Parola" not in dlg.f_pw.text()
    assert store.get(p.db_password_ref) == PW  # încă nesalvat
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Yes))
    assert dlg.save_profile()
    assert store.get(p.db_password_ref) == "Noua-Parola-123456" and dlg.f_pw.text() == "********"
    assert_no_plaintext(tmp_path, secrets=(PW, SK, KEY, "Noua-Parola-123456"))


def test_ui_create_activate_production_confirmation_delete(store, qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    import cloud_profiles_dialog as d
    dlg = d.CloudProfilesDialog()
    dlg.f_name.setText("Producție")
    dlg.f_env.setCurrentText("PRODUCTION")
    dlg.f_api.setText("https://prod.example.ro")
    assert dlg.save_profile()
    pid = dlg._current
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.No))
    dlg.activate_profile()
    assert cp.active_profile() is None  # fără confirmare explicită: nu se activează
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Yes))
    dlg.activate_profile()
    assert cp.active_profile().id == pid
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    dlg.delete_profile()
    assert cp.get_profile(pid) is not None  # profilul activ cere confirmare
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    dlg.delete_profile()
    assert cp.get_profile(pid) is None


def test_ui_delete_failure_shows_profile_still_present(store, qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    import cloud_profiles_dialog as d
    p = cp.create_profile(fields(), {"sync_key": KEY}, store)
    store.delete = lambda n: (_ for _ in ()).throw(OSError("locked"))
    shown = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: shown.append(a[1])))
    dlg = d.CloudProfilesDialog()
    dlg.delete_profile()
    assert cp.get_profile(p.id) is not None and shown == ["Profilul NU a fost șters"]
    assert "Profil șters" not in dlg.log.toPlainText()


def test_cloud_sync_window_masks_key(store, qapp):
    from cloud_sync_window import CloudSyncWindow
    cloud_sync.save_config("https://old.example.ro", KEY, False)
    w = CloudSyncWindow()
    assert w.txt_key.text() == "********" and KEY not in w.txt_key.text()
    assert w._key_value() == "" and w._effective_key() == KEY
    p = cp.create_profile(fields(api_base_url="https://prof.example.ro"), {"sync_key": KEY2}, store)
    cp.activate_profile(p.id)
    w2 = CloudSyncWindow()
    assert w2.txt_url.isReadOnly() and w2.txt_url.text() == "https://prof.example.ro"


def test_unreadable_secret_surfaces_visible_error_not_silent_stop(store):
    p = cp.create_profile(fields(), {"sync_key": KEY}, store)
    cp.activate_profile(p.id)
    store.get = lambda n: (_ for _ in ()).throw(ss.SecretStoreError("decrypt failed"))
    cfg = cloud_sync.get_config()
    assert cfg["key"] == "" and "SecretStore" in cfg["last_error"] and KEY not in cfg["last_error"]
