"""Fereastra „Administrare online -> Configurare Cloud” (/admin/cloud): doar admin, scriere doar în .env, secrete niciodată afișate.

Rulare:  cd backend && python -m pytest tests/test_cloud_config_ui.py -q -n 0
Nu se conectează la Supabase real: URL-urile de test sunt nerutabile (.invalid) sau invalide.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_etapa1_audit import _load_server

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
import cloud_config as cc  # noqa: E402

FORM = {"source_id": "cimitir"}
SECRET_KEY = "sb_secret_TESTKEYDONOTLEAK1234567890"
PG_PASSWORD = "PgPassDoNotLeak98765"
BAD_URL = f"postgresql://cimitir:{PG_PASSWORD}@gazda-inexistenta.invalid:5432/postgres?sslmode=require"
ORIGINAL = ('MONGO_URL="mongodb://localhost:27017"\nDB_NAME="test_database"\nCORS_ORIGINS="*"\n\n'
            "# ==================================================\n# CONFIGURARE CLOUD - SUPABASE\n"
            "# ==================================================\n\nCIMITIR_MODE=online\nCIMITIR_DB_ENGINE=postgres\n\n"
            "DATABASE_URL=\n\nSUPABASE_URL=\nSUPABASE_SECRET_KEY=\nSUPABASE_BUCKET=cimitir-private\n\nCIMITIR_STORAGE_DRIVER=supabase\n")


@pytest.fixture()
def env_file(tmp_path, monkeypatch):
    f = tmp_path / "test.env"
    f.write_text(ORIGINAL, encoding="utf-8")
    monkeypatch.setenv("CIMITIR_CLOUD_ENV_FILE", str(f))
    monkeypatch.delenv("CIMITIR_CLOUD_CONFIG_READONLY", raising=False)
    for name in (*cc.FIELDS, "SUPABASE_SERVICE_ROLE_KEY"):  # monkeypatch restaurează mediul după test (apply_runtime scrie în os.environ)
        monkeypatch.setenv(name, "")
    return f


@pytest.fixture()
def web(tmp_path, env_file):
    server = _load_server(tmp_path / "data")
    import cimitir_auth
    import database as db
    db.init_db()
    cimitir_auth.init_auth()
    cimitir_auth.create_user("op", "Parola-op-12345", "operator")
    cimitir_auth.create_user("boss", "Parola-boss-123", "admin")
    return TestClient(server.app)


def _login(client, user, pw):
    return client.post("/admin/access/login", data={"username": user, "password": pw, **FORM}, follow_redirects=False)


@pytest.fixture()
def admin(web):
    assert _login(web, "boss", "Parola-boss-123").headers["location"] == "/admin/"
    return web


# ----------------------------------------------------------------------------- unitare
def test_validate_rejects_bad_values_without_echoing_them():
    clean, errors, warnings = cc.validate({"DATABASE_URL": "mysql://LEAKA", "SUPABASE_URL": "http://LEAKB", "SUPABASE_SECRET_KEY": "LEAKC",
                                           "SUPABASE_BUCKET": "Bad Bucket", "CIMITIR_STORAGE_DRIVER": "ftp"})
    assert clean == {} and len(errors) == 5
    assert not any(t in " ".join(errors) for t in ("LEAKA", "LEAKB", "LEAKC"))
    clean, errors, warnings = cc.validate({"DATABASE_URL": "postgresql://u:p@db.example.com:6543/postgres", "SUPABASE_URL": "https://abc.supabase.co/"})
    assert not errors and clean["SUPABASE_URL"] == "https://abc.supabase.co" and len(warnings) == 2


def test_write_values_changes_only_given_keys_and_keeps_rest(env_file):
    changed = cc.write_values({"SUPABASE_URL": "https://abc.supabase.co", "SUPABASE_SECRET_KEY": SECRET_KEY})
    text = env_file.read_text(encoding="utf-8")
    assert set(changed) == {"SUPABASE_URL", "SUPABASE_SECRET_KEY"}
    assert text.startswith('MONGO_URL="mongodb://localhost:27017"\nDB_NAME="test_database"\nCORS_ORIGINS="*"\n')
    assert "CIMITIR_DB_ENGINE=postgres" in text and "SUPABASE_BUCKET=cimitir-private" in text and "DATABASE_URL=\n" in text
    assert text.count("CONFIGURARE CLOUD - SUPABASE") == 1 and text.count("SUPABASE_URL=") == 1
    assert cc.write_values({"SUPABASE_URL": "https://abc.supabase.co"}) == []  # idempotent


def test_write_values_appends_missing_section_and_keeps_crlf(tmp_path, monkeypatch):
    f = tmp_path / "crlf.env"
    f.write_bytes(b"A=1\r\nB=2\r\n")
    monkeypatch.setenv("CIMITIR_CLOUD_ENV_FILE", str(f))
    cc.write_values({"SUPABASE_BUCKET": "cimitir-private"})
    raw = f.read_bytes()
    assert b"\n" not in raw.replace(b"\r\n", b"") and b"CONFIGURARE CLOUD - SUPABASE" in raw and raw.startswith(b"A=1\r\nB=2\r\n")


def test_field_states_never_contain_secret_values(env_file, monkeypatch):
    cc.write_values({"DATABASE_URL": BAD_URL, "SUPABASE_SECRET_KEY": SECRET_KEY})
    cc.apply_runtime({"DATABASE_URL": BAD_URL, "SUPABASE_SECRET_KEY": SECRET_KEY})
    states = cc.field_states()
    assert states["DATABASE_URL"]["configured"] and states["SUPABASE_SECRET_KEY"]["configured"]
    dumped = repr(states)
    assert SECRET_KEY not in dumped and PG_PASSWORD not in dumped
    assert states["DATABASE_URL"]["source"] == "file"


def test_postgres_test_error_has_no_host_or_password(env_file):
    res = cc.test_postgres({"DATABASE_URL": BAD_URL})
    text = repr(res)
    assert res["state"] == "OFFLINE" and PG_PASSWORD not in text and "gazda-inexistenta" not in text


def test_storage_test_unconfigured_and_driver_local(env_file):
    assert cc.test_storage({})["state"] == "NECONFIGURAT"
    assert cc.test_storage({"CIMITIR_STORAGE_DRIVER": "local"})["state"] == "NEAPLICABIL"


# ----------------------------------------------------------------------------- web
def test_access_control(web):
    assert web.get("/admin/cloud", follow_redirects=False).headers["location"] == "/admin/access"
    assert web.post("/admin/cloud/save", data={}).status_code == 401
    assert _login(web, "op", "Parola-op-12345").status_code == 303
    assert web.get("/admin/cloud").status_code == 403
    assert web.post("/admin/cloud/save", data={"SUPABASE_BUCKET": "x-bucket"}).status_code == 403
    assert web.post("/admin/cloud/test-postgres", data={}).status_code == 403


def test_page_renders_masked_and_post_rejects_foreign_origin(admin, env_file):
    page = admin.get("/admin/cloud")
    assert page.status_code == 200 and 'type="password"' in page.text and "Administrare online" in page.text
    assert 'name="SUPABASE_SECRET_KEY"' in page.text
    assert admin.post("/admin/cloud/save", data={"SUPABASE_BUCKET": "x-bucket"}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_save_writes_env_only_and_never_echoes_secret(admin, env_file, monkeypatch):
    live_cc = sys.modules["cloud_config"]  # modulul folosit de server (poate fi reîncărcat de alte teste)
    monkeypatch.setattr(live_cc, "test_storage", lambda values=None, create_bucket=False: {"ok": True, "state": "ONLINE", "title": "Storage", "lines": [("ok", "Supabase Storage: ONLINE")]})
    monkeypatch.setattr(live_cc, "test_postgres", lambda values=None: {"ok": True, "state": "ONLINE", "title": "PostgreSQL", "lines": [("ok", "PostgreSQL: ONLINE")]})
    resp = admin.post("/admin/cloud/save", data={"DATABASE_URL": "postgresql://cimitir:" + PG_PASSWORD + "@db.example.com:5432/postgres?sslmode=require",
                                                 "SUPABASE_URL": "https://abc.supabase.co", "SUPABASE_SECRET_KEY": SECRET_KEY,
                                                 "SUPABASE_BUCKET": "cimitir-private", "CIMITIR_STORAGE_DRIVER": "supabase"})
    assert resp.status_code == 200 and resp.headers.get("hx-trigger") == "cloud-saved" and resp.headers["cache-control"] == "no-store"
    assert SECRET_KEY not in resp.text and PG_PASSWORD not in resp.text and "backend/.env" in resp.text
    text = env_file.read_text(encoding="utf-8")
    assert f"SUPABASE_SECRET_KEY={SECRET_KEY}" in text and PG_PASSWORD in text and 'MONGO_URL="mongodb://localhost:27017"' in text
    for url in ("/admin/cloud", "/admin/cloud/status"):
        body = admin.get(url).text
        assert SECRET_KEY not in body and PG_PASSWORD not in body and "db.example.com" not in body
    assert "CONFIGURAT" in admin.get("/admin/cloud/status").text
    import database as db
    for row in db.get_connection().execute("SELECT key, value FROM settings").fetchall():  # nimic în DB
        assert SECRET_KEY not in str(dict(row)) and PG_PASSWORD not in str(dict(row))
    audit = " ".join(str(dict(r)) for r in db.get_connection().execute("SELECT * FROM audit_log").fetchall())
    assert SECRET_KEY not in audit and PG_PASSWORD not in audit and "cloud_config_save" in audit
    # câmp secret gol = păstrează valoarea existentă
    again = admin.post("/admin/cloud/save", data={"DATABASE_URL": "", "SUPABASE_SECRET_KEY": "", "SUPABASE_BUCKET": "alt-bucket"})
    assert again.status_code == 200 and "SUPABASE_BUCKET" in again.text
    after = env_file.read_text(encoding="utf-8")
    assert f"SUPABASE_SECRET_KEY={SECRET_KEY}" in after and "SUPABASE_BUCKET=alt-bucket" in after


def test_unreachable_database_url_is_refused_and_not_leaked(admin, env_file):
    resp = admin.post("/admin/cloud/save", data={"DATABASE_URL": BAD_URL})
    assert resp.status_code == 200 and "Nu am salvat" in resp.text
    assert PG_PASSWORD not in resp.text and "gazda-inexistenta" not in resp.text
    assert env_file.read_text(encoding="utf-8") == ORIGINAL


def test_tests_endpoints_do_not_leak(admin, env_file):
    pg = admin.post("/admin/cloud/test-postgres", data={"DATABASE_URL": BAD_URL})
    assert pg.status_code == 200 and "OFFLINE" in pg.text and PG_PASSWORD not in pg.text and "gazda-inexistenta" not in pg.text
    st = admin.post("/admin/cloud/test-storage", data={"SUPABASE_URL": "https://gazda-inexistenta.invalid", "SUPABASE_SECRET_KEY": SECRET_KEY,
                                                       "SUPABASE_BUCKET": "cimitir-private", "CIMITIR_STORAGE_DRIVER": "supabase"})
    assert st.status_code == 200 and "OFFLINE" in st.text and SECRET_KEY not in st.text
    assert env_file.read_text(encoding="utf-8") == ORIGINAL  # testele nu scriu nimic


def test_status_fragment_unconfigured(admin, env_file):
    body = admin.get("/admin/cloud/status").text
    assert "NECONFIGURAT" in body and "INCOMPLET" in body


def test_readonly_server_shows_notice_and_refuses_save(admin, env_file, monkeypatch):
    monkeypatch.setenv("CIMITIR_CLOUD_CONFIG_READONLY", "1")
    page = admin.get("/admin/cloud").text
    assert "cloud-readonly" in page and "configurate direct pe server" in page and "disabled" in page
    resp = admin.post("/admin/cloud/save", data={"SUPABASE_BUCKET": "alt-bucket"})
    assert "Secretele trebuie configurate pe server" in resp.text
    assert env_file.read_text(encoding="utf-8") == ORIGINAL


# ----------------------------------------------------------------------------- rotirea cheii
class _FakeStore:
    """Simulează Supabase: cheia bună autentifică; cheia falsă primește 400 (care la GET s-ar citi greșit ca „inexistent”)."""
    GOOD = "sb_secret_GOODKEY000000000000000000"

    def __init__(self, url, key, bucket, prefix=""):
        self.key, self.bucket = key, bucket

    def auth_ok(self):
        return self.key == self.GOOD

    def bucket_info(self):
        return {"public": False} if self.key == self.GOOD else None

    def ensure_bucket(self):
        return "created"


def test_storage_test_distinguishes_rejected_key_from_missing_bucket(env_file, monkeypatch):
    import cimitir_storage
    monkeypatch.setattr(cimitir_storage, "SupabaseStorage", _FakeStore)
    base = {"SUPABASE_URL": "https://abc.supabase.co", "SUPABASE_BUCKET": "cimitir-private"}
    bad = cc.test_storage({**base, "SUPABASE_SECRET_KEY": "sb_secret_BADKEY0000000000000000000"})
    assert bad["state"] == "OFFLINE" and bad["auth_ok"] is False and "respinse" in repr(bad["lines"])
    good = cc.test_storage({**base, "SUPABASE_SECRET_KEY": _FakeStore.GOOD})
    assert good["ok"] and good["state"] == "ONLINE"


def test_rotation_with_rejected_key_is_refused_and_env_unchanged(admin, env_file, monkeypatch):
    import cimitir_storage
    monkeypatch.setattr(cimitir_storage, "SupabaseStorage", _FakeStore)
    admin.post("/admin/cloud/save", data={"SUPABASE_URL": "https://abc.supabase.co", "SUPABASE_SECRET_KEY": _FakeStore.GOOD,
                                          "CIMITIR_STORAGE_DRIVER": "supabase"})
    before = env_file.read_text(encoding="utf-8")
    assert f"SUPABASE_SECRET_KEY={_FakeStore.GOOD}" in before
    bad = admin.post("/admin/cloud/save", data={"SUPABASE_SECRET_KEY": "sb_secret_BADKEY0000000000000000000"})
    assert "Nu am salvat" in bad.text and "sb_secret_BADKEY" not in bad.text
    assert env_file.read_text(encoding="utf-8") == before
    ok = admin.post("/admin/cloud/save", data={"SUPABASE_SECRET_KEY": "sb_secret_GOODKEY000000000000000000"})
    assert "Salvat în backend/.env" not in bad.text and ok.status_code == 200
