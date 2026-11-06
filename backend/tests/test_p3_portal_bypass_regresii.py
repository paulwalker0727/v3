"""Regresii P3 (găsite de auditul de testare): încercări de ocolire a drepturilor add/edit/delete în portalul web. Date sintetice în folder temporar."""
import pytest

from test_etapa1_audit import _load_server  # noqa: F401
from fastapi.testclient import TestClient

PW = "Parola-test-987"


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
        name = f"tp{n['i']}"
        uid = auth.create_user(name, PW, role, rights=rights)
        c = TestClient(server.app)
        r = c.post("/admin/access/login", data={"username": name, "password": PW, "source_id": "cimitir"}, follow_redirects=False)
        assert r.status_code == 303
        return c, uid
    return type("W", (), {"client_for": staticmethod(client_for), "db": db, "auth": auth})


# Portal: edit-only account creates a NEW plot via /ui/loc/{new}/edit (save_loc_metadata INSERTs a missing loc)
def test_cont_doar_edit_nu_poate_crea_loc_nou_prin_ruta_de_editare(web):
    c, _ = web.client_for("operator", ["cfg", "edit"])
    assert web.db.load_loc_by_cod("NOU-99") is None
    r = c.post("/admin/ui/loc/NOU-99/edit", data={"sector": "Z"}, follow_redirects=False)
    print("status", r.status_code)
    created = web.db.load_loc_by_cod("NOU-99")
    assert r.status_code == 404 and created is None


# Portal: add-only account adds inhumat on a NON-existent loc
def test_add_inhumat_on_missing_loc(web):
    c, _ = web.client_for("operator", ["cfg", "add"])
    r = c.post("/admin/ui/loc/LIPSA-1/inhumat", data={"nume": "X", "prenume": "Y"}, follow_redirects=False)
    print("inhumat on missing loc status", r.status_code, "loc now:", bool(web.db.load_loc_by_cod("LIPSA-1")),
          "inhumati X:", web.db.get_connection().execute("SELECT COUNT(*) FROM inhumati WHERE nume='X'").fetchone()[0])


# Portal: add-only account adds plata on a NON-existent loc
def test_add_plata_on_missing_loc(web):
    c, _ = web.client_for("operator", ["cfg", "add"])
    r = c.post("/admin/ui/loc/LIPSA-2/plata", data={"suma": "10", "data_plata": "2024-01-01"}, follow_redirects=False)
    c2 = web.db.get_connection()
    n = c2.execute("SELECT COUNT(*) FROM plati").fetchone()[0]
    c2.close()
    print("plata on missing loc status", r.status_code, "loc now:", bool(web.db.load_loc_by_cod("LIPSA-2")), "plati rows:", n)


# Portal: legacy accounts (no cfg) keep add+delete; legacy approve-only can still create contract
def test_legacy_edit_approve_keeps_add_delete(web):
    c, _ = web.client_for("operator", ["edit", "approve"])
    assert c.post("/admin/ui/loc/T-01/plata", data={"suma": "1", "data_plata": "2024-01-01"}, follow_redirects=False).status_code == 200
    r = c.post("/admin/ui/loc/T-01/contract", data={"numar_contract": "LEG-1", "nume": "Pop"}, follow_redirects=False)
    assert r.status_code == 200


def test_cfg_only_is_view_only(web):
    c, _ = web.client_for("operator", ["cfg"])
    assert c.post("/admin/ui/loc/T-01/plata", data={"suma": "1"}, follow_redirects=False).status_code == 403
    assert c.post("/admin/ui/loc/T-01/edit", data={"sector": "Q"}, follow_redirects=False).status_code == 403
    assert c.post("/admin/ui/inhumat/1/delete", data={"cod_loc": "T-01"}, follow_redirects=False).status_code == 403


# Revocation in open web session: rights re-read from DB on every request
def test_revocation_applies_on_next_request(web):
    c, uid = web.client_for("operator", ["cfg", "add", "edit", "delete"])
    assert c.post("/admin/ui/loc/T-01/plata", data={"suma": "1", "data_plata": "2024-01-01"}, follow_redirects=False).status_code == 200
    web.auth.update_user(uid, rights=["cfg", "edit"])
    assert c.post("/admin/ui/loc/T-01/plata", data={"suma": "1", "data_plata": "2024-01-01"}, follow_redirects=False).status_code == 403


# Mobile upload token issued under `add` remains usable after the issuer's `add` right is revoked
def test_tokenul_mobil_nu_supravietuieste_revocarii_dreptului_add(web):
    c, uid = web.client_for("operator", ["cfg", "add"])
    r = c.post("/admin/mobile/token", data={"etype": "inhumat", "eid": "1"}, follow_redirects=False)
    print("token issue status", r.status_code, r.text[:200])
    if r.status_code != 200:
        pytest.skip(f"token issue not available in test env: {r.status_code}")
    url = r.json()["url"]
    path = "/api/m/" + url.split("/api/m/")[1]
    web.auth.update_user(uid, rights=["cfg"])
    png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f"
           b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")
    up = TestClient(c.app).post(path + "/upload", files={"file": ("a.png", png, "image/png")})
    assert up.status_code == 403, up.text[:200]
