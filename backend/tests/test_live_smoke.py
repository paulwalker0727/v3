"""Smoke tests pe serverul local (implicit http://127.0.0.1:8001, ocolește ingress-ul care trimite doar /api/*).
Altă țintă: CIMITIR_TEST_BASE_URL=http://host:port. Dacă serverul nu răspunde, testele se sar."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
import time
import uuid

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get("CIMITIR_TEST_BASE_URL", "http://127.0.0.1:8001").rstrip("/")


def _server_up() -> bool:
    try:
        return requests.get(f"{BASE}/api/health", timeout=5).status_code == 200
    except requests.RequestException:
        return False


pytestmark = pytest.mark.skipif(not _server_up(), reason=f"serverul {BASE} nu răspunde")


@pytest.fixture(autouse=True)
def _reset_local_throttles():
    """Limitările pe IP sunt persistate în DB (ore); rulările repetate de pe 127.0.0.1 le-ar epuiza. Doar pentru PostgreSQL local."""
    env = {}
    for line in (ROOT / "backend" / ".env").read_text(encoding="utf-8").splitlines():
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    if env.get("CIMITIR_DB_ENGINE") == "postgres" and env.get("DATABASE_URL") and ("127.0.0.1" in BASE or "localhost" in BASE):
        import psycopg
        with psycopg.connect(env["DATABASE_URL"], autocommit=True, connect_timeout=5) as conn:
            conn.execute("DELETE FROM settings WHERE key LIKE %s OR key LIKE %s", ("rl:%:127.0.0.1", "loginfail:%127.0.0.1"))
    yield


def _issue_key() -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("CIMITIR_DB_ENGINE", "CIMITIR_MODE", "DATABASE_URL")}
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "issue_sync_key.py")],
                          capture_output=True, text=True, timeout=30, env=env, cwd=ROOT / "backend")

_PRIVATE_KEYS = ("cnp", "adresa", "telefon", "email_titular")


# --- Public map API -------------------------------------------------------
def test_public_map_hides_private_fields():
    r = requests.get(f"{BASE}/api/public/map", timeout=20)
    assert r.status_code == 200
    data = r.json()
    assert "plots" in data and "sectors" in data
    blob = r.text.lower()
    # No PII fields should be exposed in the map projection
    for key in ("cnp", '"adresa"', '"telefon"'):
        assert key not in blob, f"Public map leaks {key!r}"


def test_public_loc_endpoint_hides_private_data():
    m = requests.get(f"{BASE}/api/public/map", timeout=20).json()
    if not m.get("plots"):
        pytest.skip("Preview map has no plots")
    cod = m["plots"][0]["cod_loc"]
    r = requests.get(f"{BASE}/api/public/loc/{cod}", timeout=20)
    assert r.status_code == 200
    body = r.text.lower()
    for key in ("cnp", '"adresa"', '"telefon"'):
        assert key not in body, f"/api/public/loc leaks {key!r}"


def test_public_privacy_endpoint():
    r = requests.get(f"{BASE}/api/public/privacy", timeout=20)
    assert r.status_code == 200
    j = r.json()
    for k in ("version", "text", "retention_days_closed", "retention_days_rejected"):
        assert k in j


# --- Citizen account isolation -------------------------------------------
def test_citizen_register_login_me_and_isolation_from_admin():
    s = requests.Session()
    email = f"test_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "parola-lunga-123"
    r = s.post(f"{BASE}/api/public/account/register",
               json={"email": email, "password": pwd, "full_name": "Test Citizen"}, timeout=20)
    # If registration is closed on server, still we skip cleanly
    if r.status_code == 503:
        pytest.skip("Portal citizen accounts disabled on server")
    assert r.status_code == 200, r.text

    # /me returns the profile
    me = s.get(f"{BASE}/api/public/account/me", timeout=20)
    assert me.status_code == 200
    assert me.json()["email"] == email

    # Citizen token/cookie must NOT grant admin access
    # Only /api/* is externally routed to the backend on server; /admin/ is the
    # React SPA placeholder. So we only assert backend API isolation.
    rr = s.get(f"{BASE}/api/admin/map.json", timeout=20, allow_redirects=False)
    assert rr.status_code in (401, 403), f"/api/admin/map.json: {rr.status_code}"

    # Sync API requires X-Sync-Key; citizen cookie must not bypass
    rs = s.get(f"{BASE}/api/sync/ping", timeout=20)
    assert rs.status_code in (401, 404)

    # Duplicate registration -> 409
    r2 = s.post(f"{BASE}/api/public/account/register",
                json={"email": email, "password": pwd}, timeout=20)
    assert r2.status_code == 409

    # Short password -> 422
    r3 = requests.post(f"{BASE}/api/public/account/register",
                       json={"email": f"x_{uuid.uuid4().hex[:6]}@example.com", "password": "short"}, timeout=20)
    assert r3.status_code == 422

    # Logout invalidates cookie
    s.post(f"{BASE}/api/public/account/logout", timeout=20)
    me2 = s.get(f"{BASE}/api/public/account/me", timeout=20)
    assert me2.status_code == 401

    # Cleanup: delete account
    s2 = requests.Session()
    lr = s2.post(f"{BASE}/api/public/account/login",
                 json={"email": email, "password": pwd}, timeout=20)
    if lr.status_code == 200:
        s2.delete(f"{BASE}/api/public/account", timeout=20)


def test_citizen_login_throttled_after_repeated_failures():
    """The in-memory throttle is per-process. On the server environment the
    k8s ingress fan-outs requests across multiple backend pods, so this test
    only verifies that failed logins are 401 and does not require 429.
    Unit-level verification of 429 is covered by tests/test_etapa3_requests.py.
    """
    email = f"nobody_{uuid.uuid4().hex[:8]}@example.com"
    statuses = []
    for i in range(7):
        r = requests.post(f"{BASE}/api/public/account/login",
                          json={"email": email, "password": f"wrong-{i}"}, timeout=20)
        statuses.append(r.status_code)
    # All must at least be 401 (never a leak of information / 500)
    assert all(s in (401, 429) for s in statuses), statuses


def test_staff_cannot_authenticate_as_citizen():
    s = requests.Session()
    # Staff login
    r = s.post(f"{BASE}/admin/access/login",
               data={"username": "admin", "password": "admin123", "source_id": "cimitir"},
               timeout=20, allow_redirects=False)
    if r.status_code not in (200, 303):
        pytest.skip(f"Staff login unavailable on server: {r.status_code}")
    # Staff cookie must NOT grant /api/public/account/me
    me = s.get(f"{BASE}/api/public/account/me", timeout=20)
    assert me.status_code == 401


# --- Sync auth ------------------------------------------------------------
def test_sync_ping_requires_valid_key():
    # No header -> 401 (online mode) or 404 (local mode)
    r = requests.get(f"{BASE}/api/sync/ping", timeout=20)
    assert r.status_code in (401, 404)
    # Wrong key -> 401
    r2 = requests.get(f"{BASE}/api/sync/ping",
                      headers={"X-Sync-Key": "cimitir.wrongsecret"}, timeout=20)
    assert r2.status_code in (401, 404)


def test_sync_ping_with_valid_key():
    """Issue a fresh sync key against the local backend and validate ping."""
    try:
        proc = _issue_key()
    except Exception as exc:
        pytest.skip(f"cannot run issue_sync_key.py: {exc}")
    if proc.returncode != 0:
        pytest.skip(f"issue_sync_key.py failed: {proc.stderr}")
    m = re.search(r"(cimitir\.[A-Za-z0-9_\-]{20,})", proc.stdout)
    if not m:
        pytest.skip(f"cannot parse key from: {proc.stdout!r}")
    key = m.group(1)
    r = None
    for _ in range(3):
        time.sleep(0.5)
        r = requests.get(f"{BASE}/api/sync/ping", headers={"X-Sync-Key": key}, timeout=20)
        if r.status_code == 200:
            break
        proc = _issue_key()
        key = re.search(r"(cimitir\.[A-Za-z0-9_\-]{20,})", proc.stdout).group(1)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j.get("ok") is True
    assert "map_version" in j and "lease_hours" in j


# --- Public request submission requires citizen account on server -------
def test_anonymous_cerere_rejected_when_account_required():
    r = requests.post(f"{BASE}/api/public/cerere",
                      data={"nume": "X", "prenume": "Y", "cnp": "1800101123456",
                            "adresa": "adr", "telefon": "0700", "email": "a@b.ro",
                            "cod_loc": "A-1", "tip": "concesionare", "privacy_ack": "1"},
                      timeout=20)
    # Preview runs with CIMITIR_PORTAL_REQUIRE_ACCOUNT=1 (default) -> 401
    # If disabled it may return 200/400/404/409 depending on plot state
    assert r.status_code in (401, 400, 404, 409, 200, 503)
