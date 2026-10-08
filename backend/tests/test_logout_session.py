"""Logout securizat (jti + revocare server-side) și igiena auditului pe entități (fără secrete). Rulează pe SQLite."""
from __future__ import annotations

import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient
from test_etapa1_audit import _load_server

FORM = {"source_id": "cimitir"}
PW = "Parola-op-12345"


@pytest.fixture()
def web(tmp_path):
    server = _load_server(tmp_path / "data")
    import cimitir_auth
    import database as db
    db.init_db()
    cimitir_auth.init_auth()
    cimitir_auth.create_user("op", PW, "operator")
    cimitir_auth.create_user("op2", PW, "operator")
    return server, db, server.app


def _new_client(app):
    return TestClient(app)


def _login(client, user="op"):
    r = client.post("/admin/access/login", data={"username": user, "password": PW, **FORM}, follow_redirects=False)
    assert r.status_code == 303, r.text[:200]
    return r


def _is_rejected(resp):
    return resp.status_code in (401, 403) or (resp.status_code in (302, 303, 307) and "access" in resp.headers.get("location", ""))


def _revoked_keys(db):
    conn = db.get_connection()
    try:
        return [r[0] for r in conn.execute("SELECT key FROM settings WHERE key LIKE 'revoked_session:%'").fetchall()]
    finally:
        conn.close()


def test_old_cookie_rejected_after_logout_and_other_session_survives(web):
    server, db, app = web
    s1, s2 = _new_client(app), _new_client(app)
    _login(s1); _login(s2)
    cookie_name = server.COOKIE
    assert s1.cookies.get(cookie_name) != s2.cookies.get(cookie_name)          # jti diferit => token diferit
    old_cookie = s1.cookies.get(cookie_name)
    assert s1.get("/api/admin/map.json").status_code == 200
    assert s1.get("/admin/", follow_redirects=False).status_code == 200

    lo = s1.post("/admin/logout", follow_redirects=False)
    assert lo.status_code == 303 and lo.headers["location"] == "/admin/access"
    sc = lo.headers.get("set-cookie", "").lower()
    assert "max-age=0" in sc or "expires=" in sc
    assert "path=/" in sc and "httponly" in sc and "samesite=lax" in sc
    assert len(_revoked_keys(db)) == 1

    # cookie-ul vechi, reutilizat de un client nou, e respins pe toate rutele protejate
    thief = _new_client(app)
    thief.cookies.set(cookie_name, old_cookie, path="/")
    for path in ("/admin/", "/admin/system", "/api/admin/map.json"):
        assert _is_rejected(thief.get(path, follow_redirects=False)), path
    # a doua sesiune (alt dispozitiv) rămâne validă
    assert s2.get("/api/admin/map.json").status_code == 200
    # login nou după logout => sesiune validă
    s3 = _new_client(app)
    _login(s3)
    assert s3.get("/api/admin/map.json").status_code == 200
    # logout fără cookie / cu cookie deja revocat nu dă eroare
    assert _new_client(app).post("/admin/logout", follow_redirects=False).status_code == 303
    assert thief.post("/admin/logout", follow_redirects=False).status_code == 303
    assert len(_revoked_keys(db)) == 1                                           # revocarea nu se dublează


def test_revocation_list_is_pruned_by_token_expiry(web):
    server, db, app = web
    now = int(time.time())
    db.set_setting("revoked_session:expired-old", str(now - 3600))               # token deja expirat natural
    db.set_setting("revoked_session:still-valid", str(now + 3600))
    c = _new_client(app); _login(c)
    c.post("/admin/logout", follow_redirects=False)
    keys = _revoked_keys(db)
    assert "revoked_session:expired-old" not in keys                              # curățat
    assert "revoked_session:still-valid" in keys                                  # păstrat până la expirare
    assert len(keys) == 2                                                         # + cea nouă (anterioara expirată a dispărut)


def test_token_without_revocation_check_for_garbage(web):
    server, db, app = web
    assert server.revoke_token("not-a-jwt") is False
    assert server.revoke_token("") is False


def test_audit_entities_never_records_secrets():
    import audit_entities as ae
    row = {"id": 1, "uid": "u1", "nume": "Popa", "password": "p1", "password_hash": "h", "jwt_token": "eyJabc", "cookie": "c=1",
           "DATABASE_URL": "postgresql://u:p@h/db", "SUPABASE_SECRET_KEY": "sb_secret_x", "api_key": "k", "continut": b"\x00\x01",
           "document_bytes": b"bin", "observatii": "ok", "telefon": "0700"}
    out = ae.clean(row)
    assert out == {"nume": "Popa", "observatii": "ok", "telefon": "0700"}

    cn = sqlite3.connect(":memory:")
    cn.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY, ts TEXT, actor_type TEXT, actor TEXT, action TEXT, target TEXT, detail TEXT)")
    old, new = dict(row), dict(row, telefon="0799", password="p2")
    assert ae.record(cn, "staff", "admin", "concesionari", "u1", old, new, 2) is True
    r = cn.execute("SELECT actor_type, actor, action, target, ts, detail FROM audit_log").fetchone()
    assert r[:4] == ("staff", "admin", "concesionari.update", "concesionari:u1") and r[4]
    d = json.loads(r[5])
    assert d["old"] == {"telefon": "0700"} and d["new"] == {"telefon": "0799"}    # doar câmpurile schimbate, valori vechi/noi
    blob = r[5]
    for s in ("p1", "p2", "eyJ", "postgresql://", "sb_secret", "c=1"):
        assert s not in blob
    # nicio schimbare efectivă (doar un câmp secret s-a schimbat) => niciun rând
    assert ae.record(cn, "staff", "admin", "concesionari", "u1", old, dict(old, password="p3")) is False
    # create / delete
    assert ae.record(cn, "sync", "main-1", "plati", "p1", None, {"suma": 50, "token": "t"}) is True
    assert ae.record(cn, "sync", "main-1", "plati", "p1", {"suma": 50}, None) is True
    acts = [x[0] for x in cn.execute("SELECT action FROM audit_log ORDER BY id").fetchall()]
    assert acts == ["concesionari.update", "plati.create", "plati.delete"]


def test_logout_is_post_only_with_csrf_protection(web):
    server, db, app = web
    c = _new_client(app); _login(c)
    # GET nu schimbă starea: sesiunea rămâne validă, nimic revocat, cookie-ul nu e șters
    g = c.get("/admin/logout", follow_redirects=False)
    assert g.status_code == 303 and "set-cookie" not in g.headers
    assert _revoked_keys(db) == [] and c.get("/api/admin/map.json").status_code == 200
    # POST cross-site (Sec-Fetch-Site) și cu Origin străin: 403, sesiunea rămâne validă
    for hdr in ({"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}, {"Origin": "https://evil.example"}):
        r = c.post("/admin/logout", headers=hdr, follow_redirects=False)
        assert r.status_code == 403, hdr
    assert _revoked_keys(db) == [] and c.get("/api/admin/map.json").status_code == 200
    # POST de aceeași origine (ca din browser): revocă
    ok = c.post("/admin/logout", headers={"Sec-Fetch-Site": "same-origin"}, follow_redirects=False)
    assert ok.status_code == 303 and len(_revoked_keys(db)) == 1
