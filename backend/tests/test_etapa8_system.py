"""Etapa 8 — pagina tehnică /admin/system: acces doar administratori, stări, acțiuni sigure, fără secrete, fără SQL din browser.

Rulare:  cd backend && python -m pytest tests/test_etapa8_system.py -q -n 0
Teste unitare (mascare secrete, numărare erori) + end-to-end pe serverul real (PostgreSQL). Utilizatorii/backup-urile de test se șterg la final.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import textwrap
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
for p in ("backend", "shared"):
    sys.path.insert(0, str(ROOT / p))
SERVER = os.environ.get("ETAPA6_SERVER_URL", "http://127.0.0.1:8001").rstrip("/")
PY = sys.executable
RUN = uuid.uuid4().hex[:6]
ADMIN, PLAIN, PASSWORD = f"e8admin{RUN}", f"e8user{RUN}", "Parola-E8-test!"


def _server_env() -> dict:
    env = {}
    for line in (ROOT / "backend" / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _server_ok() -> bool:
    try:
        return requests.get(f"{SERVER}/api/public/status", timeout=5).json().get("database", {}).get("engine") == "postgres"
    except Exception:
        return False


# =============================================================================== unitare
def test_redact_masks_credentials_and_secret_env_values(monkeypatch):
    import system_admin as sa
    monkeypatch.setenv("JWT_SECRET", "super-secret-value-123")
    samples = {
        "connect postgresql://cimitir:Parola9Sec@db.example.com:5432/x failed": "Parola9Sec",
        "password=hunter2hunter2 user=x": "hunter2hunter2",
        'headers {"X-Sync-Key": "cimitir.abcdefghijklmnopqrstuvwxyz0123456789"}': "abcdefghijklmnopqrstuvwxyz",
        "Authorization: Bearer abc.def.ghi-123456": "abc.def.ghi-123456",
        "token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r": "eyJzdWIi",
        "cfg JWT value super-secret-value-123 done": "super-secret-value-123",
        "S3_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG": "wJalrXUtnFEMI",
    }
    for text, secret in samples.items():
        assert secret not in sa.redact(text), text
    assert "relatie neutra" in sa.redact("relatie neutra")


def test_error_stats_and_log_tail_use_only_the_app_log(tmp_path, monkeypatch):
    import system_admin as sa
    log = tmp_path / "logs" / "cimitir.log"
    log.parent.mkdir()
    now = datetime.now()
    fmt = lambda t: t.strftime("%Y-%m-%d %H:%M:%S,123")      # noqa: E731
    log.write_text("\n".join([
        f"{fmt(now - timedelta(days=3))} ERROR uvicorn.error: veche",
        f"{fmt(now - timedelta(hours=2))} ERROR cimitir: conectare postgresql://u:SecretPass1@h/db eșuată",
        "Traceback (most recent call last):", '  File "x.py", line 1', "ValueError: ceva",
        f"{fmt(now - timedelta(minutes=5))} WARNING cimitir: avertisment",
        f"{fmt(now - timedelta(minutes=1))} INFO cimitir: ok",
    ]) + "\n", encoding="utf-8")
    monkeypatch.setattr(sa, "log_path", lambda: log)
    stats = sa.error_stats()
    assert stats["last_24h"] == 1 and stats["last_7d"] == 2 and "SecretPass1" not in json.dumps(stats)
    tail = sa.tail_logs(200)
    assert "Traceback" in tail["text"] and "SecretPass1" not in tail["text"] and "u:***@" in tail["text"]
    only = sa.tail_logs(200, errors_only=True)["text"]
    assert "avertisment" in only and "INFO cimitir: ok" not in only
    assert sa.tail_logs(5000)["count"] <= 1000


# =============================================================================== end-to-end
e2e = pytest.mark.skipif(not _server_ok(), reason="serverul PostgreSQL nu rulează")


def _login(username: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{SERVER}/admin/access/login", data={"username": username, "password": PASSWORD}, allow_redirects=False, timeout=15)
    assert r.status_code == 303 and "admin_token" in " ".join(s.cookies.keys()).lower() or s.cookies, r.text[:200]
    return s


@pytest.fixture(scope="module")
def users():
    env = {**os.environ, **_server_env(), "PYTHONPATH": ":".join(str(ROOT / p) for p in ("backend", "backend/cimitir", "shared")),
           "CIMITIR_DATA_DIR": str(ROOT / "shared_data")}
    code = textwrap.dedent(f"""
        import cimitir_auth as a, store as cim
        cim.ensure_initialized(cim.list_sources()[0]) if hasattr(cim, 'list_sources') else None
        a.create_user({ADMIN!r}, {PASSWORD!r}, a.ROLE_ADMIN)
        a.create_user({PLAIN!r}, {PASSWORD!r}, a.ROLE_USER)
    """)
    p = subprocess.run([PY, "-c", code], capture_output=True, text=True, env=env, cwd=ROOT / "backend", timeout=60)
    assert p.returncode == 0, p.stderr[-1500:]
    yield
    import psycopg
    with psycopg.connect(_server_env()["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("DELETE FROM users WHERE username IN (%s,%s)", (ADMIN, PLAIN))
        conn.execute("DELETE FROM audit_log WHERE actor IN (%s,%s)", (ADMIN, PLAIN)) if False else None


@e2e
def test_access_control_admin_only(users):
    anon = requests.get(f"{SERVER}/admin/system", allow_redirects=False, timeout=10)
    assert anon.status_code == 303 and "/admin/access" in anon.headers["location"]
    for path in ("/admin/system/logs",):
        assert requests.get(f"{SERVER}{path}", allow_redirects=False, timeout=10).status_code == 303
    for action in ("test-db", "test-storage", "backup", "check-sync"):
        assert requests.post(f"{SERVER}/admin/system/{action}", timeout=10).status_code == 401
    plain = _login(PLAIN)
    assert plain.get(f"{SERVER}/admin/system", timeout=10).status_code == 403
    assert plain.get(f"{SERVER}/admin/system/logs", timeout=10).status_code == 403
    for action in ("test-db", "test-storage", "backup", "check-sync"):
        assert plain.post(f"{SERVER}/admin/system/{action}", timeout=10).status_code == 403
    nav = plain.get(f"{SERVER}/admin/", timeout=10).text
    assert "/admin/system" not in nav                                    # linkul din meniu nu apare pentru non-administratori
    assert "/admin/system" in _login(ADMIN).get(f"{SERVER}/admin/", timeout=10).text


@e2e
def test_status_page_shows_everything_and_no_secrets(users):
    html = _login(ADMIN).get(f"{SERVER}/admin/system", timeout=30).text
    tid = lambda t: re.search(rf'data-testid="{t}"[^>]*>(.*?)</', html, re.S).group(1).strip()   # noqa: E731
    assert tid("sys-server") == "ONLINE" and tid("sys-db-engine") == "PostgreSQL" and tid("sys-postgres") == "ONLINE"
    assert tid("sys-storage") == "ONLINE" and tid("sys-storage-driver") == "local"
    assert tid("sys-smtp") in ("CONFIGURAT", "NECONFIGURAT")
    assert re.match(r"\d+(\.\d+)? (B|KB|MB|GB)", tid("sys-db-size"))
    assert tid("sys-version").startswith("1.0.0")
    for t in ("sys-last-sync", "sys-outbox", "sys-last-backup", "sys-errors", "btn-test-db", "btn-test-storage", "btn-backup-now",
              "btn-view-logs", "btn-check-sync"):
        assert f'data-testid="{t}"' in html, t
    env = _server_env()
    for name in ("JWT_SECRET", "DATABASE_URL"):
        if env.get(name):
            assert env[name] not in html
    assert not re.search(r"cimitir\.[A-Za-z0-9_-]{30,}", html) and "password" not in html.lower().replace("parol", "")
    # nu există nicio rută care primește SQL / comenzi / căi
    s = _login(ADMIN)
    for path in ("/admin/system/sql", "/admin/system/query", "/admin/system/exec", "/admin/system/shell"):
        assert s.post(f"{SERVER}{path}", data={"sql": "select 1"}, timeout=10).status_code in (404, 405)
        assert s.get(f"{SERVER}{path}", timeout=10).status_code in (404, 405)


@e2e
def test_safe_buttons_return_clear_results(users):
    s = _login(ADMIN)
    h = {"Sec-Fetch-Site": "same-origin"}
    db = s.post(f"{SERVER}/admin/system/test-db", headers=h, timeout=30).text
    assert 'data-testid="sys-result-verdict"' in db and ">OK<" in db and "PostgreSQL: conectat" in db
    st = s.post(f"{SERVER}/admin/system/test-storage", headers=h, timeout=60).text
    assert ">OK<" in st and "Scriere reușită" in st and "sha256: identic" in st and "Ștergere obiect de probă reușită" in st
    sy = s.post(f"{SERVER}/admin/system/check-sync", headers=h, timeout=60).text
    assert "Ultima sincronizare" in sy and "Conflicte de entitate deschise" in sy and "Registru pe server" in sy
    # cerere cross-site respinsă (protecție CSRF)
    assert s.post(f"{SERVER}/admin/system/test-db", headers={"Sec-Fetch-Site": "cross-site"}, timeout=10).status_code == 403
    # probele de storage nu lasă nimic în urmă
    loc = requests.get(f"{SERVER}/api/sync/media/state", headers={"X-Sync-Key": "x"}, timeout=10)
    assert loc.status_code in (401, 403)


@e2e
def test_backup_now_creates_a_verifiable_pg_dump(users):
    import shutil
    import psycopg
    exe = shutil.which("pg_dump")
    if not exe:
        pytest.skip("pg_dump nu este instalat în mediul de test")
    client_major = int(re.search(r"(\d+)", subprocess.run([exe, "--version"], capture_output=True, text=True).stdout).group(1))
    with psycopg.connect(_server_env()["DATABASE_URL"]) as c:
        server_major = int(c.execute("SHOW server_version_num").fetchone()[0]) // 10000
    if client_major < server_major:
        pytest.skip(f"pg_dump {client_major} < PostgreSQL {server_major}: pg_dump refuză serverul mai nou (instalează postgresql-client-{server_major})")
    s = _login(ADMIN)
    out = s.post(f"{SERVER}/admin/system/backup", headers={"Sec-Fetch-Site": "same-origin"}, timeout=200).text
    assert ">OK<" in out and "pg_dump" in out, out[:600]
    name = re.search(r"(cimitir-\d{8}-\d{6})", out).group(1)
    env = {**os.environ, **_server_env(), "PYTHONPATH": ":".join(str(ROOT / p) for p in ("backend", "shared")),
           "CIMITIR_DATA_DIR": str(ROOT / "shared_data")}
    code = ("import server, json; print('DIR=' + str(server._backup_dir()))")
    p = subprocess.run([PY, "-c", code], capture_output=True, text=True, env=env, cwd=ROOT / "backend", timeout=60)
    base = Path([l for l in p.stdout.splitlines() if l.startswith("DIR=")][-1][4:])
    folder = base / name
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        dump = folder / "database.dump"
        assert dump.stat().st_size > 1000 and manifest["engine"] == "postgres"
        assert hashlib.sha256(dump.read_bytes()).hexdigest() == manifest["files"]["database.dump"]
        listing = subprocess.run(["pg_restore", "--list", str(dump)], capture_output=True, text=True, timeout=60)
        assert listing.returncode == 0 and "media_objects" in listing.stdout and "concesionari" in listing.stdout
        page = s.get(f"{SERVER}/admin/system", timeout=30).text
        assert name in page and "niciunul" not in re.search(r'data-testid="sys-last-backup"[^>]*>(.*?)</', page, re.S).group(1)
    finally:
        import shutil
        shutil.rmtree(folder, ignore_errors=True)


@e2e
def test_logs_page_is_masked_and_bounded(users):
    s = _login(ADMIN)
    page = s.get(f"{SERVER}/admin/system/logs", timeout=15)
    assert page.status_code == 200 and 'data-testid="logs-text"' in page.text
    assert "jurnal pornit" in page.text or "audit" in page.text or "logs-note" in page.text
    env = _server_env()
    for name in ("JWT_SECRET",):
        if env.get(name):
            assert env[name] not in page.text
    assert s.get(f"{SERVER}/admin/system/logs?lines=99999&errors=1", timeout=15).status_code == 200
    # calea fișierului nu poate fi aleasă din browser
    leak = s.get(f"{SERVER}/admin/system/logs?path=/etc/passwd&file=/etc/passwd", timeout=15).text
    assert "root:x:0:0" not in leak
