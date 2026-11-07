"""P3 audit final: rutele de scriere ale portalului web respectă individual drepturile add/edit/delete (+ approve) configurate în panoul Admin.

Fără acces suplimentar: un cont primește doar ce i s-a bifat; conturile în format vechi (edit,approve) rămân neschimbate; fluxurile administrative
(utilizatori, setări, aprobare cereri) nu se schimbă. Date fictive, server încărcat în proces cu folder temporar.
"""
import pytest

from test_etapa1_audit import _load_server, _login, SENTINELS  # noqa: F401
from fastapi.testclient import TestClient

PW = "Parola-test-987"

# (metodă, cale, drept necesar, date)
ROUTES = [
    ("POST", "/admin/ui/loc/T-01/edit", "edit", {}),
    ("POST", "/admin/ui/loc/T-01/inhumat", "add", {"nume": "Nou", "prenume": "Unu"}),
    ("POST", "/admin/ui/inhumat/1/edit", "edit", {"nume": "Alt"}),
    ("POST", "/admin/ui/inhumat/1/delete", "delete", {"cod_loc": "T-01"}),
    ("POST", "/admin/ui/contract/1/delete", "delete", {"cod_loc": "T-01"}),
    ("POST", "/admin/ui/loc/T-01/plata", "add", {"suma": "10", "data_plata": "2024-01-01"}),
    ("POST", "/admin/ui/plata/1/delete", "delete", {"cod_loc": "T-01"}),
    ("POST", "/admin/ui/loc/T-01/aviz", "add", {"nr": "1", "data": "2024-01-01", "lucrare": "x"}),
    ("POST", "/admin/ui/aviz/1/edit", "edit", {"lucrare": "y"}),
    ("POST", "/admin/ui/aviz/1/delete", "delete", {"cod_loc": "T-01"}),
    ("POST", "/admin/ui/document/1/delete", "delete", {"cod_loc": "T-01"}),
    ("POST", "/admin/attdel/1", "delete", {}),
    ("POST", "/admin/ui/concesionar", "add", {"nume": "N", "prenume": "P"}),
    ("POST", "/admin/ui/concesionar", "edit", {"id": "1", "nume": "N2", "prenume": "P2"}),
    ("POST", "/admin/ui/concesionar/1/delete", "delete", {}),
    ("POST", "/admin/mobile/token", "add", {"etype": "inhumat", "eid": "1"}),
]
UPLOADS = [
    ("/admin/ui/loc/T-01/document", "add"),
    ("/admin/att/inhumat/1", "add"),
]
ADMIN_ONLY = [("POST", "/admin/ui/user", {"username": "z", "password": "z"}), ("POST", "/admin/ui/settings", {"cimitir_nume": "X"})]


@pytest.fixture()
def web(tmp_path):
    server = _load_server(tmp_path / "data")
    import database as db
    import cimitir_auth as auth
    db.init_db()
    db.save_plot_to_db("T-01", [(0, 0), (40, 0), (40, 30), (0, 30)], "#22c55e")
    db.save_inhumat("T-01", {"nume": "Ionescu", "prenume": "Vasile"})
    cid = db.save_concesionar({"nume": "Popa", "prenume": "Maria"})
    db.save_contract({"numar_contract": "C-1", "cod_loc": "T-01", "concesionar_id": cid})
    db.add_plata("T-01", 5, "2024-01-01")
    n = {"i": 0}

    def client_for(role, rights):
        n["i"] += 1
        name = f"u{n['i']}"
        auth.create_user(name, PW, role, rights=rights)
        c = TestClient(server.app)
        r = c.post("/admin/access/login", data={"username": name, "password": PW, "source_id": "cimitir"}, follow_redirects=False)
        assert r.status_code == 303, r.status_code
        return c
    return client_for


def call(c, method, path, data=None):
    return c.request(method, path, data=data or {}, follow_redirects=False).status_code


def run_matrix(c, granted_rights, approve=False):
    """Întoarce {cale/drept: status} pentru rutele de scriere."""
    out = []
    for method, path, right, data in ROUTES:
        out.append((path, right, call(c, method, path, data)))
    for path, right in UPLOADS:
        st = c.post(path, files={"file": ("a.png", b"x", "image/png"), "files": ("a.png", b"x", "image/png")}, follow_redirects=False).status_code
        out.append((path, right, st))
    return out


@pytest.mark.parametrize("rights", [["cfg", "add"], ["cfg", "edit"], ["cfg", "delete"], ["cfg", "add", "edit"], ["cfg", "edit", "delete"],
                                    ["cfg", "add", "delete"], ["cfg"], ["cfg", "add", "edit", "delete"], ["edit", "approve"], None])
def test_fiecare_ruta_de_scriere_cere_exact_dreptul_ei(web, rights):
    granted = set(rights or [])
    effective_legacy = rights is None or "cfg" not in rights
    if effective_legacy:       # operator implicit sau format vechi: edit => add + delete
        granted = {"add", "edit", "delete"} if (rights is None or "edit" in rights) else granted
    c = web("operator", rights)
    for path, right, status in run_matrix(c, granted):
        if right in granted:
            assert status not in (401, 403), (rights, path, right, status)
        else:
            assert status == 403, (rights, path, right, status)


def test_utilizator_implicit_user_nu_poate_scrie_nicaieri(web):
    c = web("user", None)
    assert all(st == 403 for _, _, st in run_matrix(c, set()))


def test_administratorul_nu_este_restrans_de_drepturile_salvate(web):
    c = web("admin", ["cfg"])
    assert all(st not in (401, 403) for _, _, st in run_matrix(c, {"add", "edit", "delete"}))


def test_fluxurile_administrative_raman_rezervate_administratorului(web):
    c = web("operator", ["cfg", "add", "edit", "delete", "approve", "manage_users", "manage_config"])
    for method, path, data in ADMIN_ONLY:
        assert call(c, method, path, data) == 403


def test_crearea_de_contract_ramane_pe_approve_si_respecta_add_la_conturile_configurate(web):
    path = "/admin/ui/loc/T-01/contract"
    assert call(web("operator", ["approve"]), "POST", path) not in (401, 403)          # format vechi: neschimbat
    assert call(web("operator", ["cfg", "approve"]), "POST", path) == 403              # configurat explicit, fără add
    assert call(web("operator", ["cfg", "add"]), "POST", path) == 403                  # fără approve
    assert call(web("operator", ["cfg", "approve", "add"]), "POST", path) not in (401, 403)


def test_decizia_cererilor_ramane_pe_approve(web):
    assert call(web("operator", ["cfg", "add", "edit", "delete"]), "POST", "/admin/ui/cerere/999/status", {"status": "validat"}) == 403
    assert call(web("operator", ["cfg", "approve"]), "POST", "/admin/ui/cerere/999/status", {"status": "validat"}) == 404
