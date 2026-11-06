"""T4 (iteration_4): compatibility probes for username validation + web: token prefix (synthetic data, tmp folder)."""
import pytest

from test_p3_portal_lacune2_regresii import web, _issue, _main_token, _up, PW  # noqa: F401
from fastapi.testclient import TestClient


# Normal usernames still created via admin panel POST /admin/ui/user
def test_admin_ui_create_normal_names(web):
    c, _ = web.login("admin_t4", "admin", None)
    for name in ("op1", "maria.popescu", "ab", "x_y-z.9"):
        r = c.post("/admin/ui/user", data={"username": name, "password": PW, "role": "operator"}, follow_redirects=False)
        assert r.status_code in (200, 303), (name, r.status_code)
    names = {u["username"] for u in web.auth.list_users()}
    for name in ("op1", "maria.popescu", "ab", "x_y-z.9"):
        assert name in names, name


# Reserved / invalid names refused via admin panel (no 500)
@pytest.mark.parametrize("name", ["main", "MAIN", "Main", " main ", "main:x", "a b", "a<b", "web:x", ""])
def test_admin_ui_refuses_bad(web, name):
    c, _ = web.login("admin_t4", "admin", None)
    r = c.post("/admin/ui/user", data={"username": name, "password": PW, "role": "operator"}, follow_redirects=False)
    assert r.status_code in (200, 303, 400, 422), r.status_code
    assert name.strip() not in {u["username"] for u in web.auth.list_users()} or name.strip() == ""


# Staff snapshot not affected by validation (legacy names incl. 'main' still sync)
def test_staff_snapshot_unaffected(web):
    snap = [{"uid": "u-main", "username": "main", "password_hash": web.auth.hash_password(PW), "role": "operator",
             "rights": "cfg,add", "active": 1, "version": 1},
            {"uid": "u-sp", "username": "ion pop", "password_hash": web.auth.hash_password(PW), "role": "operator",
             "rights": "cfg", "active": 1, "version": 1}]
    st = web.auth.apply_staff_snapshot(snap, 24)
    assert st["created"] == 2
    names = {u["username"] for u in web.auth.list_users()}
    assert {"main", "ion pop"} <= names


# Pre-existing account with now-invalid name can still log in; token gets web: prefix and is fail-closed
def test_legacy_main_account_login_and_failclosed(web):
    import database as db
    conn = db.get_connection()
    try:
        conn.execute("INSERT INTO users (username, password_hash, role, created_at, uid, rights, active, cemetery_id, version) "
                     "VALUES (?,?,?,?,?,?,1,'',1)", ("main", web.auth.hash_password(PW), "operator", "2025-01-01", "u-legacy",
                                                    "cfg,add"))
        conn.commit()
    finally:
        conn.close()
    c = TestClient(web.server.app)
    r = c.post("/admin/access/login", data={"username": "main", "password": PW, "source_id": "cimitir"}, follow_redirects=False)
    assert r.status_code == 303, r.text[:200]
    path = _issue(c)
    import mobile_upload
    rows = mobile_upload._q("SELECT created_by FROM mobile_upload_tokens", ())
    assert any(x["created_by"] == "web:main" for x in rows), rows
    uid = web.auth._row("username=?", "main")["id"]
    web.auth.update_user(uid, active=False)
    up = _up(web, path)
    assert up.status_code == 403, up.status_code


# Legacy un-prefixed web token (issued before fix) still verified against live account
def test_legacy_unprefixed_web_token(web):
    c, uid = web.client_for("operator", ["cfg", "add"])
    name = web.auth._row("id=?", uid)["username"]
    ok = _main_token(web, name)
    assert _up(web, ok).status_code == 200
    bad = _main_token(web, name)
    web.auth.update_user(uid, rights=["cfg"])
    assert _up(web, bad).status_code == 403


# Unknown web: author => fail-closed
def test_web_prefix_unknown_author(web):
    assert _up(web, _main_token(web, "web:ghost")).status_code == 403


# issue_for_main prefix handling via sync route
@pytest.mark.parametrize("given,expected", [(None, "main"), ("main", "main"), ("ana", "main:ana"), ("main:ana", "main:ana"),
                                            ("web:ana", "main:web:ana")])
def test_issue_for_main_prefix(web, given, expected, monkeypatch):
    import mobile_upload
    import sync_api
    monkeypatch.setattr(sync_api, "_auth", lambda req: "cimitir")
    from starlette.requests import Request
    scope = {"type": "http", "method": "POST", "path": "/", "headers": [(b"host", b"testserver")], "scheme": "http",
             "server": ("testserver", 80), "query_string": b""}
    payload = {"entity_type": "inhumat", "entity_id": "1"}
    if given is not None:
        payload["issued_by"] = given
    mobile_upload.issue_for_main(Request(scope), payload)
    rows = mobile_upload._q("SELECT created_by FROM mobile_upload_tokens ORDER BY rowid DESC LIMIT 1", ())
    assert rows[0]["created_by"] == expected
