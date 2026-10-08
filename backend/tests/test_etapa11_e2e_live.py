"""Live e2e for /admin/system Verificare Producție card and persistence buttons.
Runs against local backend 127.0.0.1:8001 (ingress only exposes /api/*).
Does NOT restart backend (that is tested in-process by test_etapa11_prod_check.py).
"""
import os
import re
import requests
import pytest

BASE = "http://127.0.0.1:8001"
USER = "e11view"
PW = "Parola-E11-view!"


@pytest.fixture(scope="module")
def admin():
    s = requests.Session()
    r = s.get(f"{BASE}/admin/access", timeout=10)
    assert r.status_code == 200
    # CSRF/csrf-free jinja+htmx form; just post credentials
    r = s.post(f"{BASE}/admin/access/login", data={"source_id": "cimitir", "username": USER, "password": PW},
               allow_redirects=False, timeout=10)
    # Expect redirect on success
    if r.status_code not in (302, 303, 307):
        pytest.skip("contul de test e11view nu există (vezi memory/test_credentials.md pentru recrearea lui)")
    # Fetch /admin/system
    r2 = s.get(f"{BASE}/admin/system", timeout=10)
    assert r2.status_code == 200, r2.text[:300]
    return s


def test_system_page_shows_pg_local_and_not_ready(admin):
    r = admin.get(f"{BASE}/admin/system", timeout=10)
    html = r.text
    assert 'data-testid="sys-prod-check"' in html
    assert 'data-testid="sys-prod-level"' in html
    assert 'data-level="pg_local"' in html
    assert "POSTGRESQL LOCAL" in html.upper()
    assert "NU ESTE GATA PENTRU PRODUC" in html.upper()
    # Checklist mentions CIMITIR_DB_ENGINE caveat
    assert "CIMITIR_DB_ENGINE=postgres" in html
    assert "nu dovede" in html.lower()


def test_no_secrets_leaked_in_system_page(admin):
    html = admin.get(f"{BASE}/admin/system", timeout=10).text
    # Must not leak actual URL value, host:port, credentials, or JWT secrets.
    # Note: the string "DATABASE_URL" (variable name) is intentionally mentioned
    # in the user-facing explanation and is NOT a secret.
    forbidden = ["postgresql://cimitir@", "127.0.0.1:55432", "cimitir_dev"]
    for s in forbidden:
        assert s not in html, f"secret leaked: {s}"
    # JWT pattern
    assert not re.search(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.", html)


def _get_system_html(sess):
    return sess.get(f"{BASE}/admin/system", timeout=10).text


def test_persistence_create_duplicate_verify_pending_and_cleanup(admin):
    # Make sure clean state
    admin.post(f"{BASE}/admin/system/persistence/cleanup", timeout=10)
    # Create
    r1 = admin.post(f"{BASE}/admin/system/persistence/create", timeout=10)
    assert r1.status_code == 200, r1.text[:300]
    assert "creat" in r1.text.lower()
    # Page should now show pending probe
    html = _get_system_html(admin)
    assert "sys-prod-probe-pending" in html
    # Duplicate create refused
    r2 = admin.post(f"{BASE}/admin/system/persistence/create", timeout=10)
    assert "deja" in r2.text.lower() or "exist" in r2.text.lower()
    # Verify before restart => pending (not confirmed)
    r3 = admin.post(f"{BASE}/admin/system/persistence/verify", timeout=10)
    body = r3.text.lower()
    assert "a\u0219teptare" in body or "nu a fost repornit" in body or "atenție" in body or "atentie" in body
    # Probe still exists
    html2 = _get_system_html(admin)
    assert "sys-prod-probe-pending" in html2
    # Cleanup
    r4 = admin.post(f"{BASE}/admin/system/persistence/cleanup", timeout=10)
    assert r4.status_code == 200
    html3 = _get_system_html(admin)
    assert "sys-prod-probe-pending" not in html3


def test_unauth_persistence_endpoints_blocked():
    for path in ("create", "verify", "cleanup"):
        r = requests.post(f"{BASE}/admin/system/persistence/{path}", timeout=10, allow_redirects=False)
        # Expect 401 or 302/303 to login (treat redirect-to-login as blocked)
        assert r.status_code in (401, 302, 303, 307, 403), f"{path}: {r.status_code}"


def test_cross_origin_post_rejected(admin):
    r = admin.post(f"{BASE}/admin/system/persistence/create",
                   headers={"Origin": "http://evil.example.com", "Sec-Fetch-Site": "cross-site"},
                   timeout=10, allow_redirects=False)
    assert r.status_code in (401, 403), f"cross-origin not rejected: {r.status_code} {r.text[:200]}"
    # Clean up any probe that might have been created (shouldn't be)
    admin.post(f"{BASE}/admin/system/persistence/cleanup", timeout=10)


def test_real_tables_untouched(admin):
    # Simple check: counts for key tables unchanged across create+cleanup cycle
    import psycopg
    from urllib.parse import urlparse
    url = None
    with open("/app/backend/.env") as fh:
        for line in fh:
            if line.startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    assert url, "DATABASE_URL missing"

    def counts():
        with psycopg.connect(url) as cx, cx.cursor() as cur:
            out = {}
            for t in ("locuri_de_veci", "contracte", "inhumati"):
                cur.execute(f"SELECT COUNT(*) FROM {t}")
                out[t] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM settings WHERE key='_probe:persistence:active'")
            out["active_probe"] = cur.fetchone()[0]
            return out

    before = counts()
    admin.post(f"{BASE}/admin/system/persistence/create", timeout=10)
    admin.post(f"{BASE}/admin/system/persistence/cleanup", timeout=10)
    after = counts()
    for t in ("locuri_de_veci", "contracte", "inhumati"):
        assert before[t] == after[t], f"{t} count changed: {before[t]} -> {after[t]}"
    assert after["active_probe"] == 0
