"""FIRST RUN pe o bază PostgreSQL complet goală: schema se creează, NU există conturi implicite, primul admin se creează sigur.

Rulare:  cd backend && python -m pytest tests/test_first_run_bootstrap.py -q -n 0
Necesită PostgreSQL local de dezvoltare:  bash scripts/dev_postgres.sh start   (se sare dacă nu rulează).
Folosește baza separată `cimitir_firstrun` (se golește la fiecare test); nu atinge backend/.env.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_etapa1_audit import _load_server

ROOT = Path(__file__).resolve().parents[2]
PG_ADMIN_URL = os.environ.get("CIMITIR_FIRSTRUN_ADMIN_URL", "postgresql://cimitir@127.0.0.1:55432/postgres")
PG_URL = PG_ADMIN_URL.rsplit("/", 1)[0] + "/cimitir_firstrun"
FORM = {"source_id": "cimitir"}
BOOT_PW = "Bootstrap-Temp-Pw-2026"
NEW_PW = "Parola-Definitiva-2026!"


def _psycopg():
    try:
        import psycopg
        return psycopg
    except ImportError:  # pragma: no cover
        pytest.skip("psycopg lipsește")


def _reset_pg_caches():
    """Un restart real = proces nou; în teste golim cache-ul „schemă deja migrată” al modulului și pool-ul."""
    ps = sys.modules.get("postgres_store")
    if ps is not None:
        ps._initialized.clear()
    pc = sys.modules.get("pg_compat")
    if pc is not None:
        pc.close_pool()
        pc._fail_until = 0.0


@pytest.fixture()
def empty_pg(tmp_path, monkeypatch):
    psycopg = _psycopg()
    try:
        with psycopg.connect(PG_ADMIN_URL, connect_timeout=3, autocommit=True) as c:
            if not c.execute("SELECT 1 FROM pg_database WHERE datname='cimitir_firstrun'").fetchone():
                c.execute("CREATE DATABASE cimitir_firstrun")
    except Exception:
        pytest.skip("PostgreSQL local de dezvoltare nu rulează (bash scripts/dev_postgres.sh start)")
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS public CASCADE")
        c.execute("CREATE SCHEMA public")
    for k, v in {"CIMITIR_MODE": "online", "CIMITIR_DB_ENGINE": "postgres", "DATABASE_URL": PG_URL,
                 "CIMITIR_IGNORE_PROJECT_ENV": "1", "CIMITIR_CLOUD_ENV_FILE": str(tmp_path / "cloud.env"),
                 "CIMITIR_PORTAL_REQUIRE_ACCOUNT": "0"}.items():
        monkeypatch.setenv(k, v)
    for k in ("CIMITIR_BOOTSTRAP_ADMIN_USER", "CIMITIR_BOOTSTRAP_ADMIN_PASSWORD", "CIMITIR_ALLOW_DEFAULT_USERS"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "cloud.env").write_text("SUPABASE_BUCKET=cimitir-private\n", encoding="utf-8")
    _reset_pg_caches()
    yield PG_URL
    _reset_pg_caches()
    for name in ("cimitir_db_router", "postgres_store", "pg_compat", "cimitir_storage", "system_admin", "cloud_config",
                 "portal_ratelimit", "portal_jobs", "entity_sync_api", "media_api", "cimitir_media", "media_service", "cimitir_entities"):
        sys.modules.pop(name, None)  # nu lăsa starea PostgreSQL în testele următoare (SQLite)


def _q(sql, args=()):
    with _psycopg().connect(PG_URL, autocommit=True) as c:
        return c.execute(sql, args).fetchall()


def _login(client, user, pw):
    return client.post("/admin/access/login", data={"username": user, "password": pw, **FORM}, follow_redirects=False)


def test_empty_database_gets_schema_but_no_default_accounts(empty_pg, tmp_path):
    server = _load_server(tmp_path / "data")
    tables = {r[0] for r in _q("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}
    assert {"users", "settings", "locuri_de_veci", "schema_migrations"} <= tables       # schema migrată automat
    assert _q("SELECT COUNT(*) FROM users")[0][0] == 0                                   # NICIUN cont (nici admin/admin123)
    client = TestClient(server.app)
    assert _login(client, "admin", "admin123").status_code == 200                        # 200 = pagina de login cu eroare, nu redirect
    assert _login(client, "user", "user123").status_code == 200
    assert client.get("/admin/cloud", follow_redirects=False).headers["location"] == "/admin/access"
    assert client.get("/api/public/status").json()["database"]["connected"] is True


def test_no_public_endpoint_can_create_an_admin(empty_pg, tmp_path):
    server = _load_server(tmp_path / "data")
    paths = [getattr(r, "path", "") for r in server.app.routes]
    assert not [p for p in paths if any(w in p.lower() for w in ("bootstrap", "first-admin", "first_admin", "setup", "install"))]
    client = TestClient(server.app)
    for url in ("/admin/utilizatori", "/admin/ui/users", "/api/admin/users"):
        assert client.post(url, data={"username": "hacker", "password": "Hacker-Pass-12345", "role": "admin"},
                           follow_redirects=False).status_code in (303, 401, 403, 404, 405)
    assert _q("SELECT COUNT(*) FROM users")[0][0] == 0


def test_cli_wizard_creates_first_admin_once_then_disables(empty_pg, tmp_path, monkeypatch):
    _load_server(tmp_path / "data")
    env = {**os.environ, "FIRST_PW": BOOT_PW, "CIMITIR_DATA_DIR": str(tmp_path / "data2")}
    run = lambda *a: subprocess.run([sys.executable, str(ROOT / "scripts" / "create_first_admin.py"), *a], env=env,
                                    capture_output=True, text=True, timeout=60)
    weak = run("--username", "admin", "--password-env", "FIRST_PW")
    assert weak.returncode == 2 and BOOT_PW not in weak.stdout + weak.stderr
    short = subprocess.run([sys.executable, str(ROOT / "scripts" / "create_first_admin.py"), "--username", "director", "--password-env", "SHORT"],
                           env={**env, "SHORT": "scurta1"}, capture_output=True, text=True, timeout=60)
    assert short.returncode == 2 and "scurta1" not in short.stdout + short.stderr
    first = run("--username", "director", "--password-env", "FIRST_PW")
    assert first.returncode == 0 and BOOT_PW not in first.stdout + first.stderr, first.stdout + first.stderr
    again = run("--username", "altcineva", "--password-env", "FIRST_PW")
    assert again.returncode == 1 and "dezactivat" in again.stdout
    rows = _q("SELECT username, role FROM users")
    assert rows == [("director", "admin")]


def test_first_admin_must_change_password_then_reaches_cloud_page(empty_pg, tmp_path, monkeypatch):
    server = _load_server(tmp_path / "data")
    import cimitir_auth as auth
    ok, _ = auth.create_first_admin("director", BOOT_PW)
    assert ok
    client = TestClient(server.app)
    resp = _login(client, "director", BOOT_PW)
    assert resp.status_code == 303 and resp.headers["location"] == "/admin/schimba-parola"
    assert client.get("/admin/cloud", follow_redirects=False).headers["location"] == "/admin/schimba-parola"   # blocat până schimbă parola
    assert client.get("/admin/system", follow_redirects=False).headers["location"] == "/admin/schimba-parola"
    same = client.post("/admin/schimba-parola", data={"old_password": BOOT_PW, "new_password": BOOT_PW, "confirm_password": BOOT_PW})
    assert same.status_code == 400
    done = client.post("/admin/schimba-parola", data={"old_password": BOOT_PW, "new_password": NEW_PW, "confirm_password": NEW_PW},
                       follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"] == "/admin/"
    assert client.get("/admin/cloud").status_code == 200                                                        # acum are acces
    assert client.post("/admin/cloud/test-postgres", data={}).status_code == 200
    # la un login nou nu mai cere schimbarea
    other = TestClient(server.app)
    assert _login(other, "director", NEW_PW).headers["location"] == "/admin/"
    assert _login(TestClient(server.app), "director", BOOT_PW).status_code == 200                              # parola temporară nu mai merge


def test_env_bootstrap_creates_one_admin_then_deactivates(empty_pg, tmp_path, monkeypatch):
    monkeypatch.setenv("CIMITIR_BOOTSTRAP_ADMIN_USER", "primar.admin")
    monkeypatch.setenv("CIMITIR_BOOTSTRAP_ADMIN_PASSWORD", BOOT_PW)
    server = _load_server(tmp_path / "data")                      # pornirea serverului = migrare + bootstrap
    assert _q("SELECT username, role FROM users") == [("primar.admin", "admin")]
    assert _q("SELECT value FROM settings WHERE key LIKE %s", ("mustchange:%",)) == [("1",)]
    client = TestClient(server.app)
    assert _login(client, "primar.admin", BOOT_PW).headers["location"] == "/admin/schimba-parola"
    assert client.post("/admin/schimba-parola", data={"old_password": BOOT_PW, "new_password": NEW_PW, "confirm_password": NEW_PW},
                       follow_redirects=False).status_code == 303
    # variabilele rămân setate, dar bootstrap-ul e oprit: nu se mai creează nimic, nu se resetează parola
    monkeypatch.setenv("CIMITIR_BOOTSTRAP_ADMIN_USER", "alt.admin")
    server2 = _load_server(tmp_path / "data")
    assert _q("SELECT username FROM users ORDER BY username") == [("primar.admin",)]
    assert _login(TestClient(server2.app), "primar.admin", NEW_PW).headers["location"] == "/admin/"


def test_env_bootstrap_weak_password_creates_nothing(empty_pg, tmp_path, monkeypatch):
    monkeypatch.setenv("CIMITIR_BOOTSTRAP_ADMIN_USER", "admin")
    monkeypatch.setenv("CIMITIR_BOOTSTRAP_ADMIN_PASSWORD", "admin123")
    _load_server(tmp_path / "data")
    assert _q("SELECT COUNT(*) FROM users")[0][0] == 0


def test_bootstrap_never_runs_when_an_admin_already_exists(empty_pg, tmp_path, monkeypatch):
    server = _load_server(tmp_path / "data")
    import cimitir_auth as auth
    assert auth.create_user("existent", "Parola-Existenta-123", auth.ROLE_ADMIN)
    monkeypatch.setenv("CIMITIR_BOOTSTRAP_ADMIN_USER", "intrus")
    monkeypatch.setenv("CIMITIR_BOOTSTRAP_ADMIN_PASSWORD", BOOT_PW)
    assert auth.bootstrap_from_env() == ""
    assert [r[0] for r in _q("SELECT username FROM users")] == ["existent"]
    ok, msg = auth.create_first_admin("intrus", BOOT_PW)
    assert not ok and "dezactivat" in msg


def test_sqlite_local_desktop_mode_creates_no_default_accounts(tmp_path, monkeypatch):
    """Instalările comerciale (și SQLite local) NU mai creează admin/user implicit: primul administrator vine din asistentul de configurare inițială."""
    for k, v in {"CIMITIR_MODE": "local", "CIMITIR_DB_ENGINE": "sqlite", "CIMITIR_IGNORE_PROJECT_ENV": "1"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CIMITIR_ALLOW_DEFAULT_USERS", raising=False)
    server = _load_server(tmp_path / "data")
    import cimitir_auth as auth
    auth.init_auth()
    assert auth.list_users() == [] and auth.first_run_required() and auth.default_users_allowed() is False
    assert auth.setup_first_admin("primar.admin", "Parola-Definitiva-2026!", "Parola-Definitiva-2026!")[0] is True
    assert _login(TestClient(server.app), "primar.admin", "Parola-Definitiva-2026!").headers["location"] != "/admin/schimba-parola"
    monkeypatch.setenv("CIMITIR_MODE", "online")
    assert auth.default_users_allowed() is False
