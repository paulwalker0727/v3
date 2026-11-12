"""P3-A §2: modificarea datelor actuale ale concesionarului / editarea unui contract vechi nu alterează instantaneul `owner_*`.

Date fictive, SQLite în tmp_path. Nu se rescriu contracte existente și nu se rulează migrări.
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

OWNER = {
    "owner_fullname": "Popescu Ana", "owner_address": "Str. Veche 1", "owner_id_card_serie_number": "XX 111111",
    "owner_cnp": "2000000000001", "owner_phone": "0700000001", "owner_email": "ana@example.test",
    "owner_fullname_2": "Popescu Ana",
}


@pytest.fixture
def d(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_audit_actor", "")
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "cimitir.db"))
    db.init_db()
    db.save_plot_to_db("A-1", [(0, 0), (1, 0), (1, 1)], "#fff")
    return db


def mk_contract(d):
    cid = d.save_concesionar({"nume": "Popescu", "prenume": "Ana", "adresa": "Str. Veche 1", "cnp": "2000000000001"})
    kid = d.save_contract({"numar_contract": "C-1", "cod_loc": "A-1", "concesionar_id": cid, **OWNER})
    return cid, kid


def test_modificarea_persoanei_nu_schimba_snapshotul_contractului(d):
    cid, kid = mk_contract(d)
    d.save_concesionar({"nume": "Ionescu", "prenume": "Maria", "adresa": "Str. Noua 9", "cnp": "2999999999999"}, concesionar_id=cid)
    k = d.get_contract(kid)
    for key, val in OWNER.items():
        assert k[key] == val


def test_editarea_altor_campuri_fara_owner_pastreaza_snapshotul(d):
    cid, kid = mk_contract(d)
    d.save_concesionar({"adresa": "Str. Noua 9"}, concesionar_id=cid)
    assert d.save_contract({"numar_contract": "C-1", "observatii": "prelungit verbal", "cod_loc": "A-1", "concesionar_id": cid},
                           contract_id=kid) == kid
    k = d.get_contract(kid)
    assert k["observatii"] == "prelungit verbal"
    for key, val in OWNER.items():
        assert k[key] == val


def test_owner_trimis_explicit_trimis_explicit_se_scrie_iar_restul_se_pastreaza(d):
    cid, kid = mk_contract(d)
    d.save_contract({"numar_contract": "C-1", "cod_loc": "A-1", "concesionar_id": cid, "owner_phone": "0711111111"}, contract_id=kid)
    k = d.get_contract(kid)
    assert k["owner_phone"] == "0711111111" and k["owner_cnp"] == "2000000000001"


def test_contracte_succesive_pe_acelasi_loc_isi_pastreaza_titularii(d):
    c1, k1 = mk_contract(d)
    c2 = d.save_concesionar({"nume": "Ionescu", "prenume": "Dan"})
    k2 = d.save_contract({"numar_contract": "C-2", "cod_loc": "A-1", "concesionar_id": c2, "owner_fullname": "Ionescu Dan"})
    d.save_concesionar({"nume": "Altul"}, concesionar_id=c1)
    d.save_contract({"numar_contract": "C-2", "cod_loc": "A-1", "concesionar_id": c2, "observatii": "x"}, contract_id=k2)
    assert d.get_contract(k1)["owner_fullname"] == "Popescu Ana"
    assert d.get_contract(k2)["owner_fullname"] == "Ionescu Dan"


def test_documentul_word_foloseste_titularul_din_contract(d):
    from contract_generator import _build_mapping
    cid, kid = mk_contract(d)
    d.save_concesionar({"nume": "Ionescu", "prenume": "Maria", "adresa": "Str. Noua 9", "cnp": "2999999999999",
                        "telefon": "0799999999"}, concesionar_id=cid)
    m = _build_mapping(d.get_contract(kid))
    assert m["owner_fullname"] == "Popescu Ana" and m["owner_cnp"] == "2000000000001"
    assert m["owner_address"] == "Str. Veche 1" and m["owner_phone"] == "0700000001"


def test_pdf_contract_foloseste_titularul_din_contract(d):
    import pdf_reports
    cid, kid = mk_contract(d)
    d.save_concesionar({"nume": "Ionescu", "prenume": "Maria", "cnp": "2999999999999"}, concesionar_id=cid)
    pdf = pdf_reports.render_contract_pdf(kid)
    assert pdf[:4] == b"%PDF"
    assert d.get_contract(kid)["owner_cnp"] == "2000000000001"


def _dialog_cls():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from contracts_window import AddContractDialog
    return AddContractDialog


def test_dialogul_de_editare_nu_rescrie_owner_cu_datele_curente(d, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    cid, kid = mk_contract(d)
    d.save_concesionar({"nume": "Ionescu", "prenume": "Maria", "adresa": "Str. Noua 9", "cnp": "2999999999999"}, concesionar_id=cid)
    dlg = _dialog_cls()(None, None, existing=d.get_contract(kid))
    assert dlg.txt_cnp.text() == "2000000000001" and dlg.txt_nume.text() == "Popescu Ana"
    assert dlg.txt_cnp.isReadOnly()
    payload = dlg._collect()
    assert not any(k.startswith("owner_") for k in payload)
    dlg.txt_observatii.setPlainText("nota noua")
    dlg._on_save()
    k = d.get_contract(kid)
    assert k["observatii"] == "nota noua"
    for key, val in OWNER.items():
        assert k[key] == val


def test_dialogul_de_contract_nou_completeaza_snapshotul_din_concesionar(d):
    from PySide6.QtWidgets import QMessageBox
    cid = d.save_concesionar({"nume": "Popescu", "prenume": "Ana", "cnp": "2000000000001"})
    dlg = _dialog_cls()(None, None, existing=None, selected_cod_loc="A-1", selected_concesionar_id=cid)
    dlg.txt_numar.setText("C-9")
    assert dlg._collect()["owner_cnp"] == "2000000000001"


def test_schimbarea_concesionarului_nu_actualizeaza_automat_snapshotul(d, monkeypatch):
    """P4: editarea obișnuită nu mai poate schimba titularul; schimbarea explicită păstrează instantaneul owner_*."""
    from PySide6.QtWidgets import QMessageBox
    cid, kid = mk_contract(d)
    c2 = d.save_concesionar({"nume": "Ionescu", "prenume": "Dan", "cnp": "1000000000002"})
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    dlg = _dialog_cls()(None, None, existing=d.get_contract(kid))
    for i in range(dlg.cmb_concesionar.count()):
        if dlg.cmb_concesionar.itemData(i) == c2:
            dlg.cmb_concesionar.setCurrentIndex(i)
    dlg._on_save()
    assert d.get_contract(kid)["concesionar_id"] == cid
    assert d.reassign_contract_concesionar(kid, c2, update_owner_snapshot=False, reason="corectura asocierii")
    k = d.get_contract(kid)
    assert k["concesionar_id"] == c2 and k["owner_cnp"] == "2000000000001"
