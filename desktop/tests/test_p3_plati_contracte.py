"""P3-B: Plăți ↔ Contracte — alegere explicită, istoric pe contract, chitanță, fără dubluri. Date fictive, SQLite în tmp_path."""
import sys
from pathlib import Path

import pytest

DESKTOP = Path(__file__).resolve().parents[1]
SHARED = DESKTOP.parent / "shared"
for p in (str(DESKTOP), str(SHARED)):
    if p not in sys.path:
        sys.path.insert(0, p)

import database as db  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(db, "_audit_actor", "")
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "cimitir.db"))
    db.init_db()
    db.set_access_guard(None)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2] if len(a) > 2 else ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    db.save_plot_to_db("A-1", [(0, 0), (1, 0), (1, 1)], "#fff")
    db.save_plot_to_db("A-2", [(2, 0), (3, 0), (3, 1)], "#fff")
    c1 = db.save_concesionar({"nume": "Popescu", "prenume": "Ana"})
    c2 = db.save_concesionar({"nume": "Ionescu", "prenume": "Dan"})
    k1 = db.save_contract({"numar_contract": "C-1", "data_contract": "2010-01-01", "cod_loc": "A-1", "concesionar_id": c1, "owner_fullname": "Popescu Ana"})
    k2 = db.save_contract({"numar_contract": "C-2", "data_contract": "2020-01-01", "cod_loc": "A-1", "concesionar_id": c2, "owner_fullname": "Ionescu Dan"})
    return type("E", (), {"c1": c1, "c2": c2, "k1": k1, "k2": k2, "warned": warned})


def fill(w, suma=50.0, chit=""):
    w.spin_suma.setValue(suma)
    w.txt_chitanta.setText(chit)


def test_loc_cu_mai_multe_contracte_cere_alegere_explicita(env):
    from plati_window import PlatiWindow
    w = PlatiWindow(None, cod_loc="A-1")
    assert w.picker.choice() is None
    fill(w)
    w.btn_add.click()
    assert db.list_plati("A-1") == [] and env.warned, "nu se înregistrează fără alegerea contractului"


def test_plata_se_leaga_de_contractul_ales_si_arata_concesionarul(env):
    from plati_window import PlatiWindow
    w = PlatiWindow(None, cod_loc="A-1")
    w.picker.select_contract(env.k2)
    assert "Ionescu Dan" in w.picker.lbl_titular.text()
    fill(w, 75.0, "R-1")
    w.btn_add.click()
    rows = db.list_plati("A-1")
    assert len(rows) == 1 and rows[0]["contract_id"] == env.k2
    assert w.table.item(0, 2).text() == "C-2" and w.table.item(0, 3).text() == "Ionescu Dan"


def test_plata_fara_contract_doar_prin_alegere_explicita(env):
    from plati_window import PlatiWindow
    from plata_contracts import NO_CONTRACT
    w = PlatiWindow(None, cod_loc="A-1")
    w.picker.combo.setCurrentIndex(w.picker.combo.findData(NO_CONTRACT))
    fill(w)
    w.btn_add.click()
    assert db.list_plati("A-1")[0]["contract_id"] is None


def test_loc_fara_contracte_nu_cere_alegere(env):
    from plati_window import PlatiWindow
    w = PlatiWindow(None, cod_loc="A-2")
    fill(w)
    w.btn_add.click()
    assert len(db.list_plati("A-2")) == 1 and not env.warned


def test_schimbarea_locului_reface_lista_de_contracte(env):
    from plati_window import PlatiWindow
    w = PlatiWindow(None, cod_loc="A-2")
    w.txt_cod.setText("A-1")
    assert w.picker.combo.count() == 4        # placeholder + 2 contracte + fără contract
    w.txt_cod.setText("A-2")
    assert w.picker.combo.count() == 1


def test_dublu_click_nu_creeaza_doua_plati(env):
    from plati_window import PlatiWindow
    w = PlatiWindow(None, cod_loc="A-1")
    w.picker.select_contract(env.k1)
    fill(w, 40.0)
    w.btn_add.click()
    w.btn_add.click()
    fill(w, 40.0)
    w.btn_add.click()          # butonul e blocat scurt după salvare
    assert len(db.list_plati("A-1")) == 1


def test_retrimiterea_aceleiasi_chitante_nu_dubleaza(env):
    a = db.add_plata("A-1", 10, "2024-01-01", "taxă", "R-9", contract_id=env.k1)
    b = db.add_plata("A-1", 10, "2024-01-01", "taxă", "R-9", contract_id=env.k1)
    assert a == b and len(db.list_plati("A-1")) == 1


def test_istoricul_pe_contract_si_plati_istorice_fara_contract_raman_neatinse(env):
    from plati_window import PlatiWindow
    old = db.add_plata("A-1", 5, "2019-01-01", "taxă", "OLD-1")           # plată istorică, fără contract_id
    p1 = db.add_plata("A-1", 11, "2021-01-01", "taxă", "N-1", contract_id=env.k1)
    p2 = db.add_plata("A-1", 22, "2022-01-01", "taxă", "N-2", contract_id=env.k2)
    w = PlatiWindow(None, cod_loc="A-1")
    assert w.table.rowCount() == 3 and w.lbl_total.text() == "Total: 38.00 lei"
    cmb = w.cmb_filter_contract
    cmb.setCurrentIndex(cmb.findData(env.k2))
    assert w.table.rowCount() == 1 and w.table.item(0, 0).text() == str(p2) and w.lbl_total.text() == "Total: 22.00 lei"
    cmb.setCurrentIndex(cmb.findData("__fara_contract__"))
    assert w.table.rowCount() == 1 and w.table.item(0, 0).text() == str(old)
    assert w.table.item(0, 2).text().startswith("— fără contract")
    assert [r["contract_id"] for r in db.list_plati("A-1") if r["id"] == old] == [None], "plata istorică nu se modifică"


def test_chitanta_foloseste_titularul_din_contractul_ales(env):
    import pdf_reports as pr
    db.save_concesionar({"nume": "Schimbat", "prenume": "Ulterior"}, concesionar_id=env.c2)
    p = db.add_plata("A-1", 120.5, "2024-02-02", "concesiune", "CH-5", contract_id=env.k2)
    import fitz
    d = fitz.open(stream=pr.render_receipt_pdf(p), filetype="pdf")
    t = " ".join(pg.get_text() for pg in d)
    assert "Ionescu Dan" in t and "C-2" in t and "CH-5" in t and "Schimbat" not in t


def test_dialogul_din_panoul_locului_cere_si_transmite_contractul(env, monkeypatch):
    from panels import AddPlataDialog
    shown = []
    monkeypatch.setattr("panels.show_message", lambda *a, **k: shown.append(a[2]))
    dlg = AddPlataDialog(None, "A-1")
    dlg.txt_suma.setText("30")
    dlg._on_ok()
    assert dlg.get_result() is None and shown
    dlg2 = AddPlataDialog(None, "A-1")
    dlg2.picker.select_contract(env.k1)
    dlg2.txt_suma.setText("30")
    dlg2._on_ok()
    assert dlg2.get_result()["contract_id"] == env.k1
