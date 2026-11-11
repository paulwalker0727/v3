"""P4: interconectare completă Locuri de veci <-> Concesionari <-> Contracte (fluxuri complete, UI offscreen + DB).

Date exclusiv sintetice, SQLite în tmp_path (nu atinge nicio bază reală și niciun Supabase). Ferestrele modale (`exec`) sunt
înlocuite cu acțiuni programatice; mesajele (QMessageBox) sunt interceptate.
"""
import sys
from pathlib import Path

import pytest

DESKTOP = Path(__file__).resolve().parents[1]
SHARED = DESKTOP.parent / "shared"
for _p in (str(DESKTOP), str(SHARED)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import database as db  # noqa: E402
import cimitir_auth as auth  # noqa: E402
import access_session  # noqa: E402
import entity_links as links  # noqa: E402

SQ = [(0, 0), (10, 0), (10, 10), (0, 10)]


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def dump(table):
    c = db.get_connection()
    try:
        return [dict(r) for r in c.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()]
    finally:
        c.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    _app()
    from PySide6.QtWidgets import QMessageBox, QInputDialog
    monkeypatch.setattr(db, "_audit_actor", "tester")
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "cimitir.db"))
    db.init_db()
    auth.init_auth()
    db.set_access_guard(None)
    access_session.set_user(None)
    msgs = {"warning": [], "information": [], "question": []}
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: msgs["warning"].append(a[2] if len(a) > 2 else "")))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: msgs["information"].append(a[2] if len(a) > 2 else "")))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: 0))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: (msgs["question"].append(a[1] if len(a) > 1 else ""), QMessageBox.Yes)[1]))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("motiv de test", True)))
    for cod, num in (("A-01", "1"), ("A-02", "2"), ("A-03", "3")):
        db.save_plot_to_db(cod, SQ, "#22c55e")
        db.save_loc_metadata(cod, {"sector": "S1", "parcela": "P1", "rand": "R1", "numar": num, "suprafata": 6})
    ion = db.save_concesionar({"nume": "Popescu", "prenume": "Ion", "cnp": "1000000000001", "adresa": "Str. Veche 1"})
    ion2 = db.save_concesionar({"nume": "Popescu", "prenume": "Ion", "cnp": "1000000000999", "adresa": "Alta strada 9"})
    maria = db.save_concesionar({"nume": "Ionescu", "prenume": "Maria", "cnp": "2000000000002"})

    def login(role, rights_list=None):
        uid = auth.create_user(f"u{len(auth.list_users())}", "Parola-test-123", role, rights=rights_list)
        access_session.set_user(auth.get_session_user(uid))
        db.set_access_guard(access_session.can)

    yield type("E", (), {"ion": ion, "ion2": ion2, "maria": maria, "msgs": msgs, "login": staticmethod(login),
                         "mp": monkeypatch})
    db.set_access_guard(None)
    access_session.set_user(None)


def loc_id(cod):
    return db.load_loc_by_cod(cod)["id"]


def patch_contract_dialog(env, numar, person_id=None, cod_loc=None, record=None):
    import contracts_window as cw

    def fake_exec(self):
        if record is not None:
            record.update(loc=self.cmb_cod_loc.currentText(), person=self.cmb_concesionar.currentData(),
                          sector=self.txt_sector.text(), parcela=self.txt_parcela.text())
        self.txt_numar.setText(numar)
        if cod_loc:
            self.cmb_cod_loc.setCurrentText(cod_loc)
        if person_id:
            for i in range(self.cmb_concesionar.count()):
                if self.cmb_concesionar.itemData(i) == person_id:
                    self.cmb_concesionar.setCurrentIndex(i)
        self.btn_save.click()
        return self.result()
    env.mp.setattr(cw.AddContractDialog, "exec", fake_exec)


def pick(env, key, value):
    import entity_links_ui as ui

    def fake_exec(self):
        for i, r in enumerate(self._shown):
            if r.get(key) == value:
                self.table.selectRow(i)
        self._accept()
        return self.result()
    env.mp.setattr(ui.PickDialog, "exec", fake_exec)


def contracts_table(win):
    return {win.table.item(i, 1).text(): [win.table.item(i, j).text() for j in range(win.table.columnCount())]
            for i in range(win.table.rowCount())}


# ------------------------------------------------------------------ Scenariul 1: din Locuri de veci
def test_s1_din_loc_alege_concesionar_existent_creeaza_contract_vizibil_in_toate_modulele(env):
    import entity_links_ui as ui
    from contracts_window import ContractsWindow
    from concesionari_window import ConcesionariWindow
    before_people = len(dump("concesionari"))
    d = ui.LinkedRecordsDialog(None, None, "loc", "A-01", "A-01")
    pick(env, "id", env.ion)
    rec = {}
    patch_contract_dialog(env, "125", record=rec)
    d.pick_existing()
    assert rec["loc"] == "A-01" and rec["person"] == env.ion and rec["sector"] == "S1" and rec["parcela"] == "P1"
    rows = [r for r in dump("contracte") if r["numar_contract"] == "125"]
    assert len(rows) == 1 and rows[0]["concesionar_id"] == env.ion and rows[0]["id_loc"] == loc_id("A-01") and rows[0]["cod_loc"] == "A-01"
    assert len(dump("concesionari")) == before_people, "concesionarul existent nu se duplică"
    # același identificator în cele trei module
    assert [c["id"] for c in links.concesionari_for_loc("A-01")] == [env.ion]
    assert [l["cod_loc"] for l in links.locuri_for_concesionar(env.ion)] == ["A-01"]
    t = contracts_table(ContractsWindow(None))
    assert t["125"][3].startswith("Popescu Ion") and t["125"][4] == "A-01" and t["125"][5] == "S1"
    assert ConcesionariWindow(None, None, initial_id=env.ion)._selected_row_data()["id"] == env.ion
    assert "L-" not in str(rows[0]) and rows[0]["owner_fullname"] == "Popescu Ion" and rows[0]["owner_cnp"] == "1000000000001"


# ------------------------------------------------------------------ Scenariul 2: din Concesionari
def test_s2_din_concesionar_alege_loc_nou_fara_duplicare_persoana(env):
    import entity_links_ui as ui
    db.save_contract({"numar_contract": "100", "cod_loc": "A-01", "concesionar_id": env.ion})
    d = ui.LinkedRecordsDialog(None, None, "concesionar", env.ion, "Popescu Ion")
    pick(env, "cod_loc", "A-02")
    rec = {}
    patch_contract_dialog(env, "101", record=rec)
    d.pick_existing()
    assert rec["person"] == env.ion and rec["loc"] == "A-02"
    assert len(dump("concesionari")) == 3
    assert sorted(l["cod_loc"] for l in links.locuri_for_concesionar(env.ion)) == ["A-01", "A-02"]
    assert sorted(d.table.item(i, 0).text() for i in range(d.table.rowCount())) == ["A-01", "A-02"]


# ------------------------------------------------------------------ Scenariul 3: din Contracte
def test_s3_din_contracte_alege_persoana_si_locul_existente(env):
    from contracts_window import ContractsWindow
    w = ContractsWindow(None)
    patch_contract_dialog(env, "126", person_id=env.maria, cod_loc="A-03")
    w.add_contract()
    k = [r for r in dump("contracte") if r["numar_contract"] == "126"][0]
    assert (k["concesionar_id"], k["id_loc"], k["cod_loc"], k["sector"]) == (env.maria, loc_id("A-03"), "A-03", "S1")
    assert w.table.rowCount() == 1 and w._selected_id() == k["id"], "noul contract este selectat după reîncărcare"
    assert [c["id"] for c in links.concesionari_for_loc("A-03")] == [env.maria]
    assert [l["cod_loc"] for l in links.locuri_for_concesionar(env.maria)] == ["A-03"]


# ------------------------------------------------------------------ Scenariul 4: istoric
def test_s4_loc_cu_doua_contracte_succesive_pentru_persoane_diferite(env):
    k1 = db.save_contract({"numar_contract": "H-1", "cod_loc": "A-01", "concesionar_id": env.ion, "data_inceput": "2001-01-01",
                           "owner_fullname": "Popescu Ion", "observatii": "vechi"})
    k2 = db.save_contract({"numar_contract": "H-2", "cod_loc": "A-01", "concesionar_id": env.maria, "data_inceput": "2015-01-01",
                           "owner_fullname": "Ionescu Maria"})
    assert sorted(c["id"] for c in links.concesionari_for_loc("A-01")) == sorted([env.ion, env.maria])
    before2 = db.get_contract(k2)
    assert db.save_contract({"numar_contract": "H-1", "observatii": "actualizat"}, contract_id=k1) == k1
    assert db.get_contract(k2) == before2
    k1r = db.get_contract(k1)
    assert k1r["observatii"] == "actualizat" and k1r["owner_fullname"] == "Popescu Ion" and k1r["concesionar_id"] == env.ion


# ------------------------------------------------------------------ Scenariul 5: protecția datelor
def test_s5_actualizarea_persoanei_nu_atinge_instantaneul_contractului_nici_documentul(env):
    from contract_generator import _build_mapping
    patch_contract_dialog(env, "S5", person_id=env.ion, cod_loc="A-01")
    from contracts_window import ContractsWindow
    ContractsWindow(None).add_contract()
    k = [r for r in dump("contracte") if r["numar_contract"] == "S5"][0]
    assert k["owner_cnp"] == "1000000000001" and k["owner_address"] == "Str. Veche 1"
    db.save_concesionar({"nume": "Schimbat", "prenume": "Nume", "adresa": "Noua 5", "cnp": "9"}, concesionar_id=env.ion)
    after = db.get_contract(k["id"])
    assert all(after[f] == k[f] for f in ("owner_fullname", "owner_address", "owner_cnp", "owner_phone", "owner_email"))
    m = _build_mapping(after)
    assert m["owner_fullname"] == "Popescu Ion" and m["owner_cnp"] == "1000000000001"


# ------------------------------------------------------------------ Scenariul 6: tabul Juridic
def _full_contract(env):
    k = db.save_contract({"numar_contract": "J-1", "data_contract": "2010-01-01", "data_inceput": "2009-05-05",
                          "data_sfarsit": "2030-01-01", "cod_loc": "A-01", "concesionar_id": env.ion, "observatii": "important",
                          "cemetery_parcel": "PX", "cemetery_row": "RX", "tomb_surface_m2": 4.5, "extra_data": {"a": 1},
                          "owner_fullname": "Popescu Ion", "owner_cnp": "1000000000001", "owner_phone": "0700"})
    db.add_plata("A-01", 50, "2020-01-01", contract_id=k, chitanta_nr="R-J1")
    db.add_document("contract", k, "scan", "/tmp/nu-exista.pdf", caption="scan") if hasattr(db, "add_document") else None
    return k


def test_s6_defectul_juridic_demonstrat_cu_payloadul_vechi(env):
    """„Înainte”: payloadul trimis de vechiul tab Juridic (observatii goale, data_inceput = data_contract, fără câmpurile de loc) gola date."""
    k = _full_contract(env)
    legacy = {"numar_contract": "J-1", "data_contract": "2010-01-01", "data_inceput": "2010-01-01", "data_sfarsit": "2031-01-01",
              "cod_loc": "A-01", "concesionar_id": env.ion, "observatii": ""}
    db.save_contract(legacy, contract_id=k)
    r = db.get_contract(k)
    assert r["observatii"] == "" and r["data_inceput"] == "2010-01-01", "payloadul vechi suprascria observatii și data_inceput"
    assert r["cemetery_parcel"] == "PX" and r["tomb_surface_m2"] == 4.5 and r["owner_phone"] == "0700", "cheile absente nu se mai golesc"


def _right_panel(env):
    from PySide6.QtWidgets import QWidget
    import panels
    env.mp.setattr(panels, "show_message", lambda *a, **k: 0)

    class Main(QWidget):
        actions = db
    w = Main()
    rp = panels.RightPanel(w)
    rp._keep = w
    return rp


def test_s6_tabul_juridic_foloseste_dialogul_comun_si_nu_pierde_nimic(env):
    import contracts_window as cw
    k = _full_contract(env)
    before = db.get_contract(k)
    n_contracts, n_plati = len(dump("contracte")), len(dump("plati"))
    rp = _right_panel(env)
    rp.update_loc_details("A-01", {})
    assert rp._ju_selected_contract()["id"] == k

    def fake_exec(self):
        self.txt_observatii.setPlainText("doar asta s-a schimbat")
        self.btn_save.click()
        return self.result()
    env.mp.setattr(cw.AddContractDialog, "exec", fake_exec)
    rp._save_contract_from_juridic()
    after = db.get_contract(k)
    changed = {f for f in after if after[f] != before[f]}
    assert changed == {"observatii"}, changed
    assert (len(dump("contracte")), len(dump("plati"))) == (n_contracts, n_plati)
    assert after["concesionar_id"] == env.ion and after["id_loc"] == loc_id("A-01") and after["cod_loc"] == "A-01"


def test_s6_tabul_juridic_contract_nou_pentru_loc_fara_contract(env):
    import contracts_window as cw
    rp = _right_panel(env)
    rp.update_loc_details("A-02", {})
    assert rp._ju_selected_contract() is None
    patch_contract_dialog(env, "J-NOU", person_id=env.maria)
    rp._save_contract_from_juridic()
    r = [x for x in dump("contracte") if x["numar_contract"] == "J-NOU"]
    assert len(r) == 1 and r[0]["cod_loc"] == "A-02" and r[0]["concesionar_id"] == env.maria
    assert rp._ju_selected_contract()["id"] == r[0]["id"]


# ------------------------------------------------------------------ Scenariul 7: permisiuni
def test_s7_fara_edit_sau_delete_nimic_nu_se_modifica_nici_prin_ferestre_secundare(env):
    from contracts_window import ContractsWindow, AddContractDialog
    k = db.save_contract({"numar_contract": "P-1", "cod_loc": "A-01", "concesionar_id": env.ion, "owner_fullname": "Popescu Ion"})
    snap = (dump("contracte"), dump("concesionari"), dump("locuri_de_veci"))
    env.login("user", ["cfg", "add"])
    assert db.save_contract({"numar_contract": "x"}, contract_id=k) is None
    assert db.reassign_contract_concesionar(k, env.maria, True) is False
    assert db.associate_contract_loc(k, "A-02") is False
    assert db.delete_contract(k) is False
    w = ContractsWindow(None)
    assert w.btn_add.isEnabled() and not w.btn_edit.isEnabled() and not w.btn_delete.isEnabled()
    dlg = AddContractDialog(None, None, existing=db.get_contract(k))
    assert not dlg.btn_change_conc.isEnabled() and not dlg.btn_assoc_loc.isEnabled()
    pick(env, "id", env.maria)
    dlg._change_concesionar()
    assert (dump("contracte"), dump("concesionari"), dump("locuri_de_veci")) == snap
    env.login("operator", ["cfg", "edit"])
    assert db.save_contract({"numar_contract": "nou", "cod_loc": "A-02", "concesionar_id": env.ion}) is None, "fără drept add"
    assert db.save_concesionar({"nume": "Nou"}) is None
    assert len(dump("contracte")) == 1 and len(dump("concesionari")) == 3


# ------------------------------------------------------------------ Scenariul 8: navigare
def test_s8_navigare_contract_concesionar_loc_istoric_si_inapoi_cu_filtre_active(env):
    import entity_links_ui as ui
    from contracts_window import ContractsWindow
    from concesionari_window import ConcesionariWindow
    from locuri_window import LocuriWindow
    k1 = db.save_contract({"numar_contract": "N-1", "cod_loc": "A-01", "concesionar_id": env.ion, "data_inceput": "2000-01-01"})
    k2 = db.save_contract({"numar_contract": "N-2", "cod_loc": "A-01", "concesionar_id": env.maria, "data_inceput": "2010-01-01"})
    db.save_contract({"numar_contract": "N-3", "cod_loc": "A-02", "concesionar_id": env.ion2})
    seen = {}
    env.mp.setattr(ConcesionariWindow, "exec", lambda self: seen.update(conc=self._selected_row_data()["id"]) or 0)
    env.mp.setattr(LocuriWindow, "exec", lambda self: seen.update(loc=self.selected_cod_loc()) or 0)
    w = ContractsWindow(None)
    w.filter_concesionar.setText("ionescu")
    assert list(contracts_table(w)) == ["N-2"]
    w.select_contract(k2)
    w.open_concesionar()
    assert seen["conc"] == env.maria, "se deschide persoana contractului (nu o omonimă)"
    w.open_loc()
    assert seen["loc"] == "A-01"
    hist = {}
    env.mp.setattr(ContractsWindow, "exec", lambda self: hist.update(rows=sorted(contracts_table(self))) or 0)
    w.open_loc_history()
    assert hist["rows"] == ["N-1", "N-2"], "istoricul locului conține ambii titulari, indiferent de filtrul ferestrei-sursă"
    assert w.filter_concesionar.text() == "ionescu" and w._selected_id() == k2 and list(contracts_table(w)) == ["N-2"]


def test_navigare_nu_modifica_contractul_si_gestioneaza_inregistrari_sterse(env):
    from contracts_window import ContractsWindow
    k = db.save_contract({"numar_contract": "D-1", "cod_loc": "A-01", "concesionar_id": env.ion})
    snap = dump("contracte")
    w = ContractsWindow(None)
    w.select_contract(k)
    import entity_links_ui as ui
    env.mp.setattr(ui, "open_concesionar_window", lambda *a, **k2: None)
    env.mp.setattr(ui, "open_loc_window", lambda *a, **k2: None)
    w.open_concesionar()
    w.open_loc()
    assert dump("contracte") == snap
    assert db.delete_contract(k)
    w.open_concesionar()
    w.open_loc()
    w.edit_contract()
    assert any("nu mai există" in m for m in env.msgs["warning"]) and w.table.rowCount() == 0


def test_contract_istoric_fara_loc_sau_persoana_este_afisat_si_asociat_explicit(env):
    from contracts_window import ContractsWindow, AddContractDialog
    k = db.save_contract({"numar_contract": "X-1", "cod_loc": "INEXISTENT", "owner_fullname": "Titular Istoric"})
    kk = db.get_contract(k)
    assert kk["id_loc"] in (None, "", 0) and kk["concesionar_id"] in (None, "", 0)
    t = contracts_table(ContractsWindow(None))
    assert t["X-1"][3] == "" and t["X-1"][4] == "INEXISTENT"
    dlg = AddContractDialog(None, None, existing=kk)
    dlg.txt_observatii.setPlainText("editare fara persoana")
    dlg._on_save()
    assert db.get_contract(k)["observatii"] == "editare fara persoana", "contractele istorice fără persoană rămân editabile"
    pick(env, "cod_loc", "A-01")
    dlg2 = AddContractDialog(None, None, existing=db.get_contract(k))
    dlg2._associate_loc()
    r = db.get_contract(k)
    assert r["cod_loc"] == "A-01" and r["id_loc"] == loc_id("A-01") and r["owner_fullname"] == "Titular Istoric"
    audit = [a for a in dump("audit_log") if a["action"] == "contract.associate_loc"]
    assert len(audit) == 1 and audit[0]["target"] == f"contract:{k}"
    from PySide6.QtWidgets import QMessageBox
    env.mp.setattr(QMessageBox, "question", staticmethod(lambda *a, **kw: QMessageBox.No if "Datele titularului" in a[1] else QMessageBox.Yes))
    pick(env, "id", env.ion)
    dlg3 = AddContractDialog(None, None, existing=db.get_contract(k))
    dlg3._change_concesionar()
    r = db.get_contract(k)
    assert r["concesionar_id"] == env.ion and r["owner_fullname"] == "Titular Istoric", "instantaneul se păstrează dacă utilizatorul nu cere rescrierea"


def test_schimbarea_titularului_nu_se_face_prin_editarea_obisnuita_si_este_jurnalizata(env):
    k = db.save_contract({"numar_contract": "T-1", "cod_loc": "A-01", "concesionar_id": env.ion, "owner_fullname": "Popescu Ion",
                          "owner_cnp": "1000000000001"})
    before = db.get_contract(k)
    assert db.save_contract({"numar_contract": "T-1", "concesionar_id": env.maria}, contract_id=k) is None
    assert "Schimbă concesionarul" in db.last_refusal()
    assert db.save_contract({"numar_contract": "T-1", "cod_loc": "A-02"}, contract_id=k) is None
    assert db.get_contract(k) == before
    assert db.reassign_contract_concesionar(k, 99999) is False and db.get_contract(k) == before
    assert db.associate_contract_loc(k, "NU-EXISTA") is False and len(dump("locuri_de_veci")) == 3
    assert db.reassign_contract_concesionar(k, env.maria, update_owner_snapshot=True, reason="corectură")
    r = db.get_contract(k)
    assert r["concesionar_id"] == env.maria and r["owner_fullname"] == "Ionescu Maria" and r["owner_cnp"] == "2000000000002"
    log = [a for a in dump("audit_log") if a["action"] == "contract.reassign_concesionar"]
    assert len(log) == 1 and log[0]["target"] == f"contract:{k}" and "corectură" in log[0]["detail"]
    assert "1000000000001" not in str(log) and "2000000000002" not in str(log), "jurnalul nu conține date personale"


def test_anularea_dialogului_cere_confirmare_doar_cu_modificari_nesalvate(env):
    from contracts_window import AddContractDialog
    d = AddContractDialog(None, None, selected_cod_loc="A-01", selected_concesionar_id=env.ion)
    d.reject()
    assert env.msgs["question"] == [], "fără modificări nu se întreabă nimic"
    d2 = AddContractDialog(None, None, selected_cod_loc="A-01", selected_concesionar_id=env.ion)
    d2.txt_numar.setText("neterminat")
    from PySide6.QtWidgets import QMessageBox
    env.mp.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: (env.msgs["question"].append(1), QMessageBox.No)[1]))
    d2.reject()
    assert env.msgs["question"] == [1] and d2.result() == 0 and d2.isVisible() is False
    assert len(dump("contracte")) == 0


def test_dublu_click_pe_salvare_creeaza_un_singur_contract(env):
    from contracts_window import AddContractDialog
    d = AddContractDialog(None, None, selected_cod_loc="A-01", selected_concesionar_id=env.ion)
    d.txt_numar.setText("DBL")
    d.btn_save.click()
    d.btn_save.click()
    assert len([r for r in dump("contracte") if r["numar_contract"] == "DBL"]) == 1


def test_doua_persoane_cu_acelasi_nume_raman_separate(env):
    a = db.save_contract({"numar_contract": "O-1", "cod_loc": "A-01", "concesionar_id": env.ion})
    b = db.save_contract({"numar_contract": "O-2", "cod_loc": "A-02", "concesionar_id": env.ion2})
    assert [l["cod_loc"] for l in links.locuri_for_concesionar(env.ion)] == ["A-01"]
    assert [l["cod_loc"] for l in links.locuri_for_concesionar(env.ion2)] == ["A-02"]
    cands = links.find_concesionar_candidates({"nume": "Popescu", "prenume": "Ion"}, db.load_concesionari())
    assert {c["id"] for c in cands} == {env.ion, env.ion2}, "doar propune; nu unifică"
    assert len(dump("concesionari")) == 3 and (a, b)


def test_tabelul_contracte_filtre_si_stare(env):
    from contracts_window import ContractsWindow
    db.save_contract({"numar_contract": "F-1", "cod_loc": "A-01", "concesionar_id": env.ion, "data_inceput": "2000-01-01", "data_sfarsit": "2001-01-01"})
    db.save_contract({"numar_contract": "F-2", "cod_loc": "A-02", "concesionar_id": env.maria, "data_inceput": "2020-01-01", "data_sfarsit": "2999-01-01"})
    db.save_contract({"numar_contract": "F-3", "cod_loc": "A-03", "concesionar_id": env.maria, "data_sfarsit": "text neinterpretabil"})
    w = ContractsWindow(None)
    t = contracts_table(w)
    assert t["F-1"][9] == "Expirat" and t["F-2"][9] == "În vigoare" and t["F-3"][9] == ""
    assert t["F-2"][8] == "2020-01-01 – 2999-01-01" and t["F-2"][6] == "P1" and t["F-2"][7] == "R1"
    w.filter_concesionar.setText("maria")
    assert sorted(contracts_table(w)) == ["F-2", "F-3"]
    w.filter_cod_loc.setText("a-03")
    assert list(contracts_table(w)) == ["F-3"]
    w.filter_numar.setText("F-9")
    assert contracts_table(w) == {}
    w.clear_filters()
    assert len(contracts_table(w)) == 3


# ------------------------------------------------------------------ hartă / sincronizare
def test_selectia_de_pe_harta_si_din_tabel_duce_la_aceleasi_relatii(env):
    k = db.save_contract({"numar_contract": "M-1", "cod_loc": "A-01", "concesionar_id": env.ion})
    from PySide6.QtWidgets import QWidget
    import entity_links_ui as ui
    calls = []

    class FakeMain(QWidget):
        actions = db

        def highlight_loc_by_cod(self, cod):
            calls.append(cod)
            return True
    main = FakeMain()
    from contracts_window import ContractsWindow
    w = ContractsWindow(main)
    w.select_contract(k)
    w.show_on_map()
    assert calls == ["A-01"]
    assert [c["id"] for c in links.concesionari_for_loc(calls[0])] == [env.ion]


def test_sync_pastreaza_legaturile_contractului_dupa_asociere_explicita(env):
    import entity_sync as es
    assert es.ensure_tracking(force=True)
    k = db.save_contract({"numar_contract": "SY-1", "cod_loc": "A-01", "concesionar_id": env.ion, "owner_fullname": "Popescu Ion"})
    conn = db.get_connection()
    uid = conn.execute("SELECT uid FROM contracte WHERE id=?", (k,)).fetchone()[0]
    ion_uid = conn.execute("SELECT uid FROM concesionari WHERE id=?", (env.ion,)).fetchone()[0]
    maria_uid = conn.execute("SELECT uid FROM concesionari WHERE id=?", (env.maria,)).fetchone()[0]
    loc_uid = conn.execute("SELECT uid FROM locuri_de_veci WHERE id=?", (loc_id("A-01"),)).fetchone()[0]
    w = es._read_wire(conn, "contracte", uid)["data"]
    assert (w["concesionar_uid"], w["loc_uid"]) == (ion_uid, loc_uid)
    conn.execute("DELETE FROM cloud_sync_outbox")
    conn.close()
    assert db.reassign_contract_concesionar(k, env.maria, False, "test sync")
    conn = db.get_connection()
    pending = conn.execute("SELECT action FROM cloud_sync_outbox WHERE entity='contracte' AND entity_uid=? AND state='pending'", (uid,)).fetchall()
    w2 = es._read_wire(conn, "contracte", uid)["data"]
    conn.close()
    assert len(pending) == 1 and pending[0][0] == "upsert", "schimbarea explicită intră în coada de sincronizare"
    assert (w2["concesionar_uid"], w2["loc_uid"]) == (maria_uid, loc_uid) and w2["owner_fullname"] == "Popescu Ion"
