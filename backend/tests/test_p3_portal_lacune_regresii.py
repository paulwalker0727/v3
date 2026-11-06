"""T2 (iteration_2): probe pentru lacune NOI de drepturi în portal (date sintetice, folder temporar)."""
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

    def client_for(role, rights):
        n["i"] += 1
        name = f"tq{n['i']}"
        uid = auth.create_user(name, PW, role, rights=rights)
        c = TestClient(server.app)
        r = c.post("/admin/access/login", data={"username": name, "password": PW, "source_id": "cimitir"}, follow_redirects=False)
        assert r.status_code == 303
        return c, uid, name
    return type("W", (), {"client_for": staticmethod(client_for), "db": db, "auth": auth, "server": server})


def _count(db, sql):
    c = db.get_connection()
    try:
        return c.execute(sql).fetchone()[0]
    finally:
        c.close()


def _issue(c):
    r = c.post("/admin/mobile/token", data={"etype": "inhumat", "eid": "1"}, follow_redirects=False)
    if r.status_code != 200:
        pytest.skip(f"token issue not available: {r.status_code}")
    return "/api/m/" + r.json()["url"].split("/api/m/")[1]


# Mobile token: author DEACTIVATED (still has add in rights column) -> upload must be refused
def test_token_mobil_autor_dezactivat(web):
    c, uid, _ = web.client_for("operator", ["cfg", "add"])
    path = _issue(c)
    web.auth.update_user(uid, active=False)
    up = TestClient(c.app).post(path + "/upload", files={"file": ("a.png", PNG, "image/png")})
    print("deactivated author upload:", up.status_code, up.text[:160])
    assert up.status_code == 403


# Mobile token: author DELETED -> upload must be refused
def test_token_mobil_autor_sters(web):
    c, uid, _ = web.client_for("operator", ["cfg", "add"])
    path = _issue(c)
    web.auth.delete_user(uid)
    up = TestClient(c.app).post(path + "/upload", files={"file": ("a.png", PNG, "image/png")})
    print("deleted author upload:", up.status_code, up.text[:160])
    assert up.status_code == 403


# Mobile token: legacy 'edit' account (implies add) keeps working
def test_token_mobil_legacy_edit_ok(web):
    c, _, _ = web.client_for("operator", ["edit"])
    path = _issue(c)
    up = TestClient(c.app).post(path + "/upload", files={"file": ("a.png", PNG, "image/png")})
    assert up.status_code == 200, up.text[:200]


# Portal: inhumat on a non-existent loc must not create an orphan record / phantom loc
def test_inhumat_pe_loc_inexistent(web):
    c, _, _ = web.client_for("operator", ["cfg", "add"])
    before = _count(web.db, "SELECT COUNT(*) FROM inhumati")
    r = c.post("/admin/ui/loc/LIPSA-1/inhumat", data={"nume": "X", "prenume": "Y"}, follow_redirects=False)
    after = _count(web.db, "SELECT COUNT(*) FROM inhumati")
    print("inhumat missing loc:", r.status_code, "rows +", after - before, "loc:", bool(web.db.load_loc_by_cod("LIPSA-1")))
    assert web.db.load_loc_by_cod("LIPSA-1") is None
    assert after == before


# Portal: plata on a non-existent loc must not create orphan record
def test_plata_pe_loc_inexistent(web):
    c, _, _ = web.client_for("operator", ["cfg", "add"])
    before = _count(web.db, "SELECT COUNT(*) FROM plati")
    r = c.post("/admin/ui/loc/LIPSA-2/plata", data={"suma": "10", "data_plata": "2024-01-01"}, follow_redirects=False)
    after = _count(web.db, "SELECT COUNT(*) FROM plati")
    print("plata missing loc:", r.status_code, "rows +", after - before, "loc:", bool(web.db.load_loc_by_cod("LIPSA-2")))
    assert web.db.load_loc_by_cod("LIPSA-2") is None
    assert after == before


# Legacy formats: 'approve' only => no add/edit/delete; '' => role defaults
@pytest.mark.parametrize("rights,add,edit,delete,approve", [
    (["edit", "approve"], True, True, True, True),
    (["approve"], False, False, False, True),
    (["edit"], True, True, True, False),
])
def test_format_vechi_effective_rights(web, rights, add, edit, delete, approve):
    eff = web.auth.effective_rights("operator", ",".join(rights))
    assert ("add" in eff, "edit" in eff, "delete" in eff, "approve" in eff) == (add, edit, delete, approve)


def test_format_vechi_gol_foloseste_rolul(web):
    eff = web.auth.effective_rights("operator", "")
    print("operator '' ->", eff, "| ROLE_RIGHTS:", web.auth.ROLE_RIGHTS.get("operator"))
    assert tuple(eff) == tuple(web.auth.ROLE_RIGHTS.get("operator", ("view",)))


# Legacy 'approve'-only account: portal approves cerere, but cannot add plata
def test_legacy_approve_only_portal(web):
    c, _, _ = web.client_for("operator", ["approve"])
    assert c.post("/admin/ui/loc/T-01/plata", data={"suma": "1", "data_plata": "2024-01-01"}, follow_redirects=False).status_code == 403
    assert c.post("/admin/ui/loc/T-01/edit", data={"sector": "Q"}, follow_redirects=False).status_code == 403
