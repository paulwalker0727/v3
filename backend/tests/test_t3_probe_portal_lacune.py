"""T3 (iteration_3): probe pentru lacune NOI in tokenul mobil (date sintetice, folder temporar)."""
import pytest

from test_etapa1_audit import _load_server  # noqa: F401
from fastapi.testclient import TestClient

PW = "Parola-test-987"
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f"
       b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


@pytest.fixture()
def web(tmp_path):
    server = _load_server(tmp_path / "data")
    import database as db
    import cimitir_auth as auth
    db.init_db()
    db.save_plot_to_db("T-01", [(0, 0), (40, 0), (40, 30), (0, 30)], "#22c55e")
    db.save_inhumat("T-01", {"nume": "Ionescu", "prenume": "Vasile"})
    n = {"i": 0}

    def login(name, role, rights):
        uid = auth.create_user(name, PW, role, rights=rights)
        assert uid, f"create_user({name}) failed"
        c = TestClient(server.app)
        r = c.post("/admin/access/login", data={"username": name, "password": PW, "source_id": "cimitir"}, follow_redirects=False)
        assert r.status_code == 303, r.text[:200]
        return c, uid

    def client_for(role, rights):
        n["i"] += 1
        return login(f"tz{n['i']}", role, rights)
    return type("W", (), {"client_for": staticmethod(client_for), "login": staticmethod(login), "db": db, "auth": auth,
                          "server": server})


def _issue(c):
    r = c.post("/admin/mobile/token", data={"etype": "inhumat", "eid": "1"}, follow_redirects=False)
    assert r.status_code == 200, r.text[:200]
    return "/api/m/" + r.json()["url"].split("/api/m/")[1]


def _main_token(web, issued_by):
    """Token emis ca din MAIN (issue_for_main) - apelam direct issue() cu created_by-ul pe care l-ar scrie issue_for_main."""
    import mobile_upload
    from starlette.requests import Request
    scope = {"type": "http", "method": "POST", "path": "/", "headers": [(b"host", b"testserver")], "scheme": "http",
             "server": ("testserver", 80), "query_string": b""}
    out = mobile_upload.issue(Request(scope), "cimitir", "inhumat", "1", issued_by)
    return "/api/m/" + out["url"].split("/api/m/")[1]


def _up(web, path):
    return TestClient(web.server.app).post(path + "/upload", files={"file": ("a.png", PNG, "image/png")})


# (a) web account without add cannot obtain a token
@pytest.mark.parametrize("rights", [["cfg"], ["cfg", "edit"], ["cfg", "delete"], ["approve"], ["cfg", "approve"]])
def test_fara_add_nu_obtine_token(web, rights):
    c, _ = web.client_for("operator", rights)
    r = c.post("/admin/mobile/token", data={"etype": "inhumat", "eid": "1"}, follow_redirects=False)
    print(rights, "->", r.status_code)
    assert r.status_code in (401, 403)


# (a) the /api/m surface needs a valid token; a web session alone is not enough
def test_api_m_fara_token_valid(web):
    c, _ = web.client_for("operator", ["cfg"])
    for p in ("/api/m/cimitir.xxxxxxxxxxxxxxxxxxxxxxxxx/upload", "/api/m/cimitir/upload"):
        r = c.post(p, files={"file": ("a.png", PNG, "image/png")})
        assert r.status_code in (404, 422, 429), (p, r.status_code)


# (b) legitimate MAIN flow: 'main' and 'main:<user>' tokens upload fine
@pytest.mark.parametrize("issued_by", ["main", "main:operator1"])
def test_token_main_legitim(web, issued_by):
    path = _main_token(web, issued_by)
    up = _up(web, path)
    assert up.status_code == 200, up.text[:200]


# (b) issue_for_main prefixes the issuer
@pytest.mark.parametrize("sent,stored", [(None, "main"), ("", "main"), ("main", "main"), ("ana", "main:ana")])
def test_issue_for_main_prefix(web, monkeypatch, sent, stored):
    import mobile_upload
    import sync_api
    monkeypatch.setattr(sync_api, "_auth", lambda request: "cimitir")
    payload = {"entity_type": "inhumat", "entity_id": "1"}
    if sent is not None:
        payload["issued_by"] = sent
    r = TestClient(web.server.app).post("/api/sync/mobile/tokens", json=payload)
    assert r.status_code == 200, r.text[:200]
    tid = r.json()["tid"]
    row = mobile_upload._q("SELECT created_by FROM mobile_upload_tokens WHERE tid = ?", (tid,), one=True)
    print(sent, "->", row)
    assert row["created_by"] == stored
    assert _up(web, "/api/m/" + r.json()["url"].split("/api/m/")[1]).status_code == 200


# NEW GAP probe: a WEB account literally named 'main' is treated as a MAIN token -> revocation bypass
def test_cont_web_numit_main_dezactivat(web):
    c, uid = web.login("main", "operator", ["cfg", "add"])
    path = _issue(c)
    web.auth.update_user(uid, active=False)
    up = _up(web, path)
    print("web account 'main' deactivated -> upload:", up.status_code, up.text[:120])
    assert up.status_code == 403, "web account named 'main' bypasses _require_issuer_still_allowed"


# Can the portal admin create such a username at all? (no username validation in create_user / POST /admin/ui/user)
@pytest.mark.parametrize("name", ["main", "main:x", "a b<script>"])
def test_portal_creare_utilizator_nume_rezervat(web, name):
    adm, _ = web.client_for("admin", [])
    adm.post("/admin/ui/user", data={"username": name, "password": PW, "role": "operator"}, follow_redirects=False)
    created = any(u["username"] == name for u in web.auth.list_users())
    print(f"portal created username {name!r}:", created)
    assert not created, f"portal allowed reserved/invalid username {name!r}"
