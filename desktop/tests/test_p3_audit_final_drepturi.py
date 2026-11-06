"""P3 audit final (desktop): garda nu poate eșua silențios; avize/fotografii/hartă respectă add/edit/delete; revocare/acordare în sesiune
deschisă; buton rămas activ nu execută operațiuni interzise; compatibilitate cu conturi existente și sincronizare. Date fictive, SQLite în tmp_path."""
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
    db.set_access_guard(None)
    db.save_plot_to_db("A-1", [(0, 0), (1, 0), (1, 1)], "#fff")
    aid = db.add_aviz("A-1", "1", "2024-01-01", "lucrare")
    cid = db.save_concesionar({"nume": "Popescu", "prenume": "Ana"})
    kid = db.save_contract({"numar_contract": "C-1", "cod_loc": "A-1", "concesionar_id": cid})
    pid = db.add_plata("A-1", 10, "2024-01-01", contract_id=kid)

    def login(role, rights_list=None):
        uid = auth.create_user(f"u{len(auth.list_users())}", "Parola-test-123", role, rights=rights_list)
        access_session.set_user(auth.get_session_user(uid))
        db.set_access_guard(access_session.can)
        return uid
    yield type("E", (), {"login": staticmethod(login), "aid": aid, "kid": kid, "pid": pid})
    db.set_access_guard(None)
    access_session.set_user(None)


# ------------------------------------------------------------------ garda din main.py
def _main_module():
    import importlib
    return importlib.import_module("main")


def test_garda_se_instaleaza_si_se_verifica_la_autentificare(env):
    m = _main_module()
    uid = auth.create_user("op_g", "Parola-test-123", "operator")
    user = auth.get_session_user(uid)
    access_session.set_user(user)
    m.install_access_guard(user)
    assert db.add_plata("A-1", 5, "2024-02-02", chitanta_nr="G-1")


def test_garda_care_nu_poate_fi_verificata_refuza_sesiunea_si_blocheaza_scrierile(env, monkeypatch):
    m = _main_module()
    uid = auth.create_user("op_g2", "Parola-test-123", "operator")
    user = auth.get_session_user(uid)
    access_session.set_user(user)

    def boom(_r):
        raise RuntimeError("DB indisponibil")
    monkeypatch.setattr(access_session, "can", boom)
    with pytest.raises(RuntimeError):
        m.install_access_guard(user)
    assert db.add_plata("A-1", 5, "2024-02-02", chitanta_nr="G-2") is None, "după eșec, orice scriere trebuie refuzată"


def test_fara_utilizator_in_sesiune_garda_ridica_eroare(env):
    m = _main_module()
    access_session.set_user(None)
    with pytest.raises(RuntimeError):
        m.install_access_guard({"username": "x"})
    assert db.add_plata("A-1", 5, "2024-02-02") is None


def test_main_nu_inghite_erorile_garzii():
    src = (DESKTOP / "main.py").read_text(encoding="utf-8")
    block = src[src.index("install_access_guard(user)\n"):src.index("continue", src.index("install_access_guard(user)\n"))]
    assert "QMessageBox.critical" in block and "except Exception as exc" in block and "pass" not in block


# ------------------------------------------------------------------ avize / fotografii / hartă
OPS = {
    "add_aviz": lambda e: db.add_aviz("A-1", "2", "2024-02-02", "x"),
    "edit_aviz": lambda e: db.update_aviz(e.aid, {"lucrare": "y"}),
    "delete_aviz": lambda e: db.delete_aviz(e.aid),
    "save_photo": lambda e: db.save_photo("A-1", __file__),
    "cover_photo": lambda e: db.set_cover_photo("A-1", 999),
    "delete_photo": lambda e: db.delete_photo(999),
    "new_plot": lambda e: db.save_plot_to_db("N-9", [(0, 0), (2, 0), (2, 2)], "#000"),
    "edit_plot": lambda e: db.save_plot_to_db("A-1", [(0, 0), (3, 0), (3, 3)], "#111"),
    "loc_metadata": lambda e: db.save_loc_metadata("A-1", {"sector": "Z"}),
    "extra_data": lambda e: db.save_extra_data("A-1", {"observatii": "z"}),
    "sector_save": lambda e: db.save_sector_to_db("S1", [(0, 0), (1, 0), (1, 1)], "#000"),
    "sector_delete": lambda e: db.delete_sector_from_db("S1"),
    "guide_add": lambda e: db.add_guide("linie", [(0, 0), (1, 1)]),
    "guide_delete": lambda e: db.delete_guide(999),
    "row_add": lambda e: db.add_row("S1", "R1", 1, [(0, 0), (1, 1)], 1.0, 1.0),
    "row_edit": lambda e: db.update_row(999, name="R2"),
    "row_delete": lambda e: db.delete_row(999),
    "delete_plot": lambda e: db.delete_plot("A-1"),
}
NEEDS = {k: "add" if k in ("add_aviz", "save_photo", "new_plot", "guide_add", "row_add") else
         "delete" if "delete" in k else "edit" for k in OPS}


@pytest.mark.parametrize("granted", [["add"], ["edit"], ["delete"], ["add", "edit"], ["edit", "delete"], ["add", "delete"], []],
                         ids=lambda g: "+".join(g) or "doar-view")
def test_operatiunile_pe_avize_fotografii_si_harta_urmeaza_drepturile(env, granted):
    env.login("operator", ["cfg", *granted])
    for name, op in OPS.items():
        db._last_refusal = ""
        try:
            op(env)
        except Exception:      # operațiuni pe date inexistente pot ridica alte erori; contează doar refuzul de drepturi
            pass
        refused = "Drepturi insuficiente" in db.last_refusal()
        assert refused == (NEEDS[name] not in granted), (granted, name, db.last_refusal())


def test_creare_loc_nou_cu_doar_add_permite_metadatele_locului_nou_dar_nu_ale_celor_existente(env):
    env.login("operator", ["cfg", "add"])
    assert db.save_plot_to_db("N-1", [(0, 0), (1, 0), (1, 1)], "#000")
    with db.granted("edit"):
        assert db.save_loc_metadata("N-1", {"sector": "S"})
    assert db.save_loc_metadata("A-1", {"sector": "S"}) is False


def test_operatiuni_initiate_de_sistem_si_aprobarea_cererilor_nu_cer_drepturi_de_operator(env, tmp_path, monkeypatch):
    f = tmp_path / "d.pdf"
    f.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(db, "import_media_file", lambda src, sub="": str(src))
    env.login("operator", ["cfg", "approve"])
    assert db.add_document("cerere", 1, "x", str(f)) is None
    with db.system_operation():
        assert db.add_document("cerere", 1, "x", str(f))
    with db.granted("add"):
        assert db.add_document("concesionar", 1, "buletin", str(f))
    assert db.add_document("concesionar", 1, "buletin", str(f)) is None, "acordarea temporară nu rămâne după ieșirea din context"


# ------------------------------------------------------------------ sesiune deschisă, butoane, ferestre
def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_buton_ramas_activ_dupa_revocare_nu_poate_executa_operatiunea(env, monkeypatch):
    _qapp()
    from PySide6.QtWidgets import QMessageBox
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    uid = env.login("operator", ["cfg", "add", "edit", "delete"])
    from plati_window import PlatiWindow
    w = PlatiWindow(None, cod_loc="A-1")
    w.table.selectRow(0)
    assert w.btn_del.isEnabled()
    auth.update_user(uid, rights=["cfg", "add"])          # revocare cât timp fereastra e deschisă; butonul NU s-a actualizat încă
    assert w.btn_del.isEnabled()
    w.delete_selected()
    assert any("nu a fost ștearsă" in m for m in shown)
    assert len(db.list_plati("A-1")) == 1, "plata trebuia să rămână"


def test_refresh_gates_dezactiveaza_la_revocare_si_reactiveaza_la_acordare(env):
    _qapp()
    from plati_window import PlatiWindow
    uid = env.login("operator", ["cfg", "add", "delete"])
    w = PlatiWindow(None, cod_loc="A-1")
    assert w.btn_del.isEnabled() and w.btn_add.isEnabled()
    auth.update_user(uid, rights=["cfg", "add"])
    access_session.refresh_gates()
    assert not w.btn_del.isEnabled() and w.btn_add.isEnabled()
    auth.update_user(uid, rights=["cfg", "add", "delete"])
    access_session.refresh_gates()
    assert w.btn_del.isEnabled()
    auth.update_user(uid, active=False)                      # cont dezactivat => orice drept retras
    access_session.refresh_gates()
    assert not w.btn_del.isEnabled() and not w.btn_add.isEnabled()


def test_refresh_nu_reactiveaza_butoane_dezactivate_din_alte_motive(env):
    _qapp()
    from plati_window import PlatiWindow
    env.login("operator")
    w = PlatiWindow(None, cod_loc="A-1")
    w.btn_receipt.setEnabled(False)
    w.btn_phone.setEnabled(False)
    access_session.refresh_gates()
    assert not w.btn_receipt.isEnabled() and not w.btn_phone.isEnabled()


def test_acordarea_unui_drept_permite_operatiunea_imediat_chiar_cu_fereastra_veche(env):
    _qapp()
    uid = env.login("user")
    from plati_window import PlatiWindow
    w = PlatiWindow(None, cod_loc="A-1")
    assert not w.btn_add.isEnabled()
    auth.update_user(uid, rights=["cfg", "add"])
    assert db.add_plata("A-1", 5, "2024-05-05", chitanta_nr="NEW-1")
    access_session.refresh_gates()
    assert w.btn_add.isEnabled()


def test_fereastra_panou_loc_deja_deschisa_nu_ocoleste_garda(env):
    uid = env.login("operator", ["cfg", "add", "edit", "delete"])
    assert db.delete_aviz(db.add_aviz("A-1", "7", "2024-01-01", "t"))
    auth.update_user(uid, rights=["cfg", "view"])
    assert db.add_aviz("A-1", "8", "2024-01-01", "t") is None


# ------------------------------------------------------------------ compatibilitate / sincronizare
def test_conturile_existente_isi_pastreaza_comportamentul(env):
    for csv, expect in [("edit,approve", {"view", "add", "edit", "delete", "approve"}), ("edit", {"view", "add", "edit", "delete"}),
                        ("approve", {"view", "approve"}), ("", {"view", "add", "edit", "delete", "approve"})]:
        assert set(auth.effective_rights("operator", csv)) == expect, csv
    assert set(auth.effective_rights("user", "")) == {"view"}


def test_sincronizarea_staff_transporta_configurarea_fara_pierderi(env):
    uid = auth.create_user("sy", "Parola-test-123", "operator", rights=["cfg", "add", "delete"])
    exported = [s for s in auth.export_staff() if s["username"] == "sy"][0]
    assert exported["rights"] == "cfg,add,delete"
    assert set(auth.effective_rights(exported["role"], exported["rights"])) == {"view", "add", "delete"}
    # un MAIN vechi: RIGHTS = view/edit/approve/manage_* -> ignoră cfg/add/delete, nu primește drepturi suplimentare
    old = {"view", "edit", "approve", "manage_users", "manage_config"}
    chosen = {r.strip() for r in exported["rights"].split(",")} & old
    assert chosen == set()
    auth.update_user(uid, rights=["cfg", "add"])
    assert [s for s in auth.export_staff() if s["username"] == "sy"][0]["rights"] == "cfg,add"


def test_drepturile_telefoanelor_mobile_v2_nu_sunt_atinse():
    import mobile_pairing
    src = (DESKTOP / "mobile_pairing.py").read_text(encoding="utf-8")
    assert "RIGHT_ADD" not in src and "cfg" not in src
    assert hasattr(mobile_pairing, "rights_summary")
