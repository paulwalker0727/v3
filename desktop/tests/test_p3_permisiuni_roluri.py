"""P3-A §1: permisiunile pe roluri sunt aplicate în punctele care execută operațiunile (stratul DB), nu doar în butoane.

Politică confirmată de proprietar: user = consultare + adăugare; operator = + modificare/ștergere; admin = tot.
Date fictive, SQLite în tmp_path.
"""
import sys
from pathlib import Path

import pytest

DESKTOP = Path(__file__).resolve().parents[1]
SHARED = DESKTOP.parent / "shared"
for p in (str(DESKTOP), str(SHARED)):
    if p not in sys.path:
        sys.path.insert(0, p)

import database as db  # noqa: E402
import cimitir_auth as auth  # noqa: E402
import access_session  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_audit_actor", "")
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "cimitir.db"))
    db.init_db()
    auth.init_auth()
    ids = {
        "admin": auth.create_user("adm_t", "Parola-test-123", auth.ROLE_ADMIN),
        "operator": auth.create_user("op_t", "Parola-test-123", auth.ROLE_OPERATOR),
        "user": auth.create_user("usr_t", "Parola-test-123", auth.ROLE_USER),
    }
    db.save_plot_to_db("A-1", [(0, 0), (1, 0), (1, 1)], "#fff")
    cid = db.save_concesionar({"nume": "Popescu", "prenume": "Ana"})
    kid = db.save_contract({"numar_contract": "C-1", "cod_loc": "A-1", "concesionar_id": cid})
    iid = db.save_inhumat("A-1", {"nume": "Test", "prenume": "Unu"})
    pid = db.add_plata("A-1", 10, "2024-01-01", contract_id=kid)
    db.set_access_guard(access_session.can)

    def login(role):
        access_session.set_user(auth.get_session_user(ids[role]))

    yield type("E", (), {"login": staticmethod(login), "cid": cid, "kid": kid, "iid": iid, "pid": pid})
    db.set_access_guard(None)
    access_session.set_user(None)


def test_rolurile_au_drepturile_asteptate():
    assert auth.ROLE_RIGHTS[auth.ROLE_USER] == ("view", "add")
    assert "edit" not in auth.ROLE_RIGHTS[auth.ROLE_USER]
    assert set(auth.ROLE_RIGHTS[auth.ROLE_OPERATOR]) == {"view", "add", "edit", "approve"}
    assert "manage_users" not in auth.ROLE_RIGHTS[auth.ROLE_OPERATOR]
    assert "edit" in auth.effective_rights(auth.ROLE_USER, "edit") and "add" in auth.effective_rights(auth.ROLE_USER, "edit")


def test_user_poate_adauga(env):
    env.login("user")
    assert db.save_inhumat("A-1", {"nume": "Nou", "prenume": "Doi"})
    assert db.save_concesionar({"nume": "Nou", "prenume": "Concesionar"})
    assert db.save_contract({"numar_contract": "C-2", "cod_loc": "A-1", "concesionar_id": env.cid})
    assert db.add_plata("A-1", 20, "2024-02-01", contract_id=env.kid, chitanta_nr="R-1")


def test_user_nu_poate_modifica(env):
    env.login("user")
    assert db.save_inhumat("A-1", {"nume": "Alt"}, inhumat_id=env.iid) is None
    assert db.save_concesionar({"nume": "Alt"}, concesionar_id=env.cid) is None
    assert db.save_contract({"numar_contract": "ALT", "cod_loc": "A-1", "concesionar_id": env.cid}, contract_id=env.kid) is None
    assert "Drepturi insuficiente" in db.last_refusal()
    assert db.get_inhumat(env.iid)["nume"] == "Test"
    assert db.get_concesionar(env.cid)["nume"] == "Popescu"
    assert db.get_contract(env.kid)["numar_contract"] == "C-1"


def test_user_nu_poate_sterge(env):
    env.login("user")
    assert db.delete_plata(env.pid) is False
    assert db.delete_inhumat(env.iid) is False
    assert db.delete_contract(env.kid) is False
    assert db.delete_concesionar(env.cid) is False
    assert db.delete_plot("A-1") is False
    assert len(db.list_plati("A-1")) == 1 and db.get_inhumat(env.iid) and db.get_contract(env.kid)


def test_operator_poate_modifica_si_sterge_plata(env):
    env.login("operator")
    assert db.save_inhumat("A-1", {"nume": "Alt"}, inhumat_id=env.iid) == env.iid
    assert db.save_contract({"numar_contract": "C-1b", "cod_loc": "A-1", "concesionar_id": env.cid}, contract_id=env.kid) == env.kid
    assert db.delete_plata(env.pid) is True


def test_admin_poate_tot(env):
    env.login("admin")
    assert db.delete_plata(env.pid) is True
    assert db.delete_inhumat(env.iid) is True


def test_acces_revocat_in_sesiune_deschisa_este_refuzat(env):
    env.login("operator")
    assert db.save_inhumat("A-1", {"nume": "X"}, inhumat_id=env.iid)
    auth.update_user(auth.get_session_user(1)["id"] if False else access_session.current_user()["id"], active=False)
    assert db.save_inhumat("A-1", {"nume": "Y"}, inhumat_id=env.iid) is None
    assert db.add_plata("A-1", 5, "2024-03-01") is None


def test_fara_utilizator_autentificat_garda_instalata_refuza(env):
    access_session.set_user(None)
    assert db.add_plata("A-1", 5, "2024-03-01") is None
    assert db.save_inhumat("A-1", {"nume": "X"}) is None


def test_eroare_in_garda_refuza_operatiunea(env):
    def broken(_right):
        raise RuntimeError("boom")
    db.set_access_guard(broken)
    assert db.add_plata("A-1", 5, "2024-03-01") is None


def test_fara_garda_scripturile_si_sincronizarea_nu_sunt_afectate(env):
    db.set_access_guard(None)
    assert db.add_plata("A-1", 5, "2024-03-01")


def test_documente_user_adauga_dar_nu_sterge(env, tmp_path, monkeypatch):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(db, "import_media_file", lambda src, sub="": str(src))
    env.login("user")
    did = db.add_document("inhumat", env.iid, "certificat", str(f))
    assert did
    assert db.delete_document(did) is False
    env.login("operator")
    assert db.delete_document(did) is True


def test_butoanele_ferestrelor_sunt_dezactivate_pentru_user(env, qtbot=None):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    env.login("user")
    from plati_window import PlatiWindow
    w = PlatiWindow(None, cod_loc="A-1")
    assert not w.btn_del.isEnabled() and w.btn_add.isEnabled()
    from contracts_window import ContractsWindow
    c = ContractsWindow(None)
    assert c.btn_add.isEnabled() and not c.btn_edit.isEnabled() and not c.btn_delete.isEnabled()
    env.login("operator")
    assert PlatiWindow(None, cod_loc="A-1").btn_del.isEnabled()
