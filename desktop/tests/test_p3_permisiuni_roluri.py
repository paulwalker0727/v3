"""P3 §1: drepturile individuale configurate de administrator (view/add/edit/delete/approve) sunt respectate în UI și în funcțiile care scriu.

Fără politică fixă pe rol: rolul dă doar valorile implicite (identice cu main), `users.rights` le suprascrie explicit (marcaj `cfg`),
iar conturile existente (format vechi `edit,approve`) își păstrează comportamentul. Date fictive, SQLite în tmp_path.
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


def rights(role, csv=None):
    return set(auth.effective_rights(role, csv))


# ------------------------------------------------------------------ model de drepturi
def test_valorile_implicite_ale_rolurilor_raman_cele_din_main():
    assert rights("user") == {"view"}
    assert rights("operator") == {"view", "add", "edit", "delete", "approve"}
    assert rights("admin") == set(auth.RIGHTS)


def test_conturile_vechi_cu_edit_pastreaza_adaugarea_si_stergerea():
    assert rights("operator", "edit,approve") == {"view", "add", "edit", "delete", "approve"}
    assert rights("user", "edit") == {"view", "add", "edit", "delete"}
    assert rights("user", "approve") == {"view", "approve"}


def test_configurarea_explicita_a_administratorului_nu_are_implicatii_ascunse():
    assert rights("operator", "cfg,add") == {"view", "add"}
    assert rights("operator", "cfg,edit") == {"view", "edit"}
    assert rights("operator", "cfg,delete") == {"view", "delete"}
    assert rights("user", "cfg,add,edit") == {"view", "add", "edit"}
    assert rights("operator", "cfg") == {"view"}, "administratorul poate retrage tot, chiar unui operator"
    assert rights("user", "cfg,add,edit,delete,approve") == {"view", "add", "edit", "delete", "approve"}


def test_utilizatorul_nu_poate_primi_drepturi_de_administrare_prin_configurare():
    assert rights("operator", "cfg,manage_users,manage_config,add") == {"view", "add"}


def test_administratorul_are_mereu_toate_drepturile():
    assert rights("admin", "cfg") == set(auth.RIGHTS)


def test_clientii_vechi_ignora_marcajul_si_nu_pierd_view():
    legacy_view = {r for r in "cfg,add".split(",")} & {"view", "edit", "approve", "manage_users", "manage_config"}
    assert legacy_view == set(), "un MAIN vechi vede doar `view` pentru configurarea nouă (mai restrictiv, niciodată mai larg)"


# ------------------------------------------------------------------ aplicare în funcțiile care scriu
@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_audit_actor", "")
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "cimitir.db"))
    db.init_db()
    auth.init_auth()
    db.save_plot_to_db("A-1", [(0, 0), (1, 0), (1, 1)], "#fff")
    db.set_access_guard(None)
    cid = db.save_concesionar({"nume": "Popescu", "prenume": "Ana"})
    state = {}

    def seed():
        state["kid"] = db.save_contract({"numar_contract": "C-1", "cod_loc": "A-1", "concesionar_id": cid})
        state["iid"] = db.save_inhumat("A-1", {"nume": "Test", "prenume": "Unu"})
        state["pid"] = db.add_plata("A-1", 10, "2024-01-01", contract_id=state["kid"])
        return state
    seed()

    def login(role, rights_list=None, name=None):
        uid = auth.create_user(name or f"u_{role}_{len(auth.list_users())}", "Parola-test-123", role, rights=rights_list)
        access_session.set_user(auth.get_session_user(uid))
        db.set_access_guard(access_session.can)
        return uid

    yield type("E", (), {"login": staticmethod(login), "cid": cid, **state})
    db.set_access_guard(None)
    access_session.set_user(None)


def probe(e):
    """Ce poate face utilizatorul curent: {operațiune: bool}. Operațiunile distructive rulează la final pe înregistrări noi/seed."""
    out = {}
    out["add_inhumat"] = bool(db.save_inhumat("A-1", {"nume": "N"}))
    out["add_plata"] = bool(db.add_plata("A-1", 5, "2024-02-02", chitanta_nr=f"R-{id(out)}"))
    out["add_contract"] = bool(db.save_contract({"numar_contract": "C-9", "cod_loc": "A-1", "concesionar_id": e.cid}))
    out["edit_inhumat"] = bool(db.save_inhumat("A-1", {"nume": "Edit"}, inhumat_id=e.iid))
    out["edit_contract"] = bool(db.save_contract({"numar_contract": "C-1", "cod_loc": "A-1", "concesionar_id": e.cid, "observatii": "e"}, contract_id=e.kid))
    out["delete_plata"] = bool(db.delete_plata(e.pid))
    out["delete_inhumat"] = bool(db.delete_inhumat(e.iid))
    return out


CONFIGS = [
    ("user", None, set()),
    ("operator", None, {"add_inhumat", "add_plata", "add_contract", "edit_inhumat", "edit_contract", "delete_plata", "delete_inhumat"}),
    ("operator", ["edit", "approve"], {"add_inhumat", "add_plata", "add_contract", "edit_inhumat", "edit_contract", "delete_plata", "delete_inhumat"}),
    ("user", ["cfg", "add"], {"add_inhumat", "add_plata", "add_contract"}),
    ("user", ["cfg", "edit"], {"edit_inhumat", "edit_contract"}),
    ("user", ["cfg", "delete"], {"delete_plata", "delete_inhumat"}),
    ("operator", ["cfg", "add", "edit"], {"add_inhumat", "add_plata", "add_contract", "edit_inhumat", "edit_contract"}),
    ("operator", ["cfg", "add", "delete"], {"add_inhumat", "add_plata", "add_contract", "delete_plata", "delete_inhumat"}),
    ("operator", ["cfg", "edit", "delete"], {"edit_inhumat", "edit_contract", "delete_plata", "delete_inhumat"}),
    ("operator", ["cfg"], set()),
    ("admin", None, {"add_inhumat", "add_plata", "add_contract", "edit_inhumat", "edit_contract", "delete_plata", "delete_inhumat"}),
]


@pytest.mark.parametrize("role,rights_list,allowed", CONFIGS, ids=[f"{r}-{'+'.join(x) if x else 'implicit'}" for r, x, _ in CONFIGS])
def test_matricea_de_configuratii_este_aplicata_in_functiile_care_scriu(env, role, rights_list, allowed):
    env.login(role, rights_list)
    got = {k for k, v in probe(env).items() if v}
    assert got == allowed


def test_revocarea_unui_drept_se_aplica_in_sesiunea_deschisa(env):
    uid = env.login("operator", ["cfg", "add", "edit", "delete"])
    assert db.save_inhumat("A-1", {"nume": "X"}, inhumat_id=env.iid)
    auth.update_user(uid, rights=["cfg", "add"])
    assert db.save_inhumat("A-1", {"nume": "Y"}, inhumat_id=env.iid) is None
    assert db.add_plata("A-1", 5, "2024-03-03", chitanta_nr="R-77")
    assert db.delete_plata(env.pid) is False
    assert "Drepturi insuficiente" in db.last_refusal()


def test_acordarea_unui_drept_se_aplica_in_sesiunea_deschisa(env):
    uid = env.login("user")
    assert db.add_plata("A-1", 5, "2024-03-03", chitanta_nr="R-1") is None
    auth.update_user(uid, rights=["cfg", "add"])
    assert db.add_plata("A-1", 5, "2024-03-03", chitanta_nr="R-1")


def test_fara_utilizator_autentificat_garda_instalata_refuza(env):
    db.set_access_guard(access_session.can)
    access_session.set_user(None)
    assert db.add_plata("A-1", 5, "2024-03-03") is None


def test_eroare_in_garda_refuza_operatiunea(env):
    def broken(_right):
        raise RuntimeError("boom")
    db.set_access_guard(broken)
    assert db.add_plata("A-1", 5, "2024-03-03") is None


def test_fara_garda_scripturile_si_sincronizarea_nu_sunt_afectate(env):
    db.set_access_guard(None)
    assert db.add_plata("A-1", 5, "2024-03-03")


def test_documente_adaugare_si_stergere_sunt_drepturi_separate(env, tmp_path, monkeypatch):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(db, "import_media_file", lambda src, sub="": str(src))
    env.login("user", ["cfg", "add"])
    did = db.add_document("inhumat", env.iid, "certificat", str(f))
    assert did and db.delete_document(did) is False
    env.login("user", ["cfg", "delete"])
    assert db.add_document("inhumat", env.iid, "certificat", str(f)) is None
    assert db.delete_document(did) is True


# ------------------------------------------------------------------ UI
def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_butoanele_ferestrelor_urmeaza_drepturile_individuale(env):
    _qapp()
    from plati_window import PlatiWindow
    from contracts_window import ContractsWindow
    env.login("user", ["cfg", "add"])
    w = PlatiWindow(None, cod_loc="A-1")
    assert w.btn_add.isEnabled() and not w.btn_del.isEnabled()
    c = ContractsWindow(None)
    assert c.btn_add.isEnabled() and not c.btn_edit.isEnabled() and not c.btn_delete.isEnabled()
    env.login("operator", ["cfg", "edit", "delete"])
    w = PlatiWindow(None, cod_loc="A-1")
    assert not w.btn_add.isEnabled() and w.btn_del.isEnabled()
    c = ContractsWindow(None)
    assert not c.btn_add.isEnabled() and c.btn_edit.isEnabled() and c.btn_delete.isEnabled()
    env.login("operator")
    w = PlatiWindow(None, cod_loc="A-1")
    assert w.btn_add.isEnabled() and w.btn_del.isEnabled()


def test_fara_utilizator_autentificat_butoanele_nu_sunt_blocate_de_gate(env):
    _qapp()
    from plati_window import PlatiWindow
    db.set_access_guard(None)
    access_session.set_user(None)
    w = PlatiWindow(None, cod_loc="A-1")
    assert w.btn_add.isEnabled() and w.btn_del.isEnabled()
