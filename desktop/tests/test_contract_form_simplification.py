"""Compact contract form: isolated Qt processes and fictional temporary SQLite only."""
import pytest

from test_concesionar_duplicat import run


def test_compact_form_selection_and_save(tmp_path):
    run(tmp_path, r'''
from PySide6.QtWidgets import QLabel
from contracts_window import AddContractDialog
first = db.save_concesionar({"nume": "Alpha", "prenume": "Fictiv"})
second = db.save_concesionar({"nume": "Beta", "prenume": "Fictiv", "adresa": "Adresa fictiva", "cnp": "2000000000002"})
d = AddContractDialog(None, selected_concesionar_id=first)
d.show(); app.processEvents()
labels = {w.text() for w in d.findChildren(QLabel)}
assert not labels.intersection({"Nume:", "Prenume:", "Adresă:", "BI/CI:", "CNP:", "Telefon:", "Email:", "Sector:", "Parcela:", "Rând:", "Loc:", "Suprafață (m2):"})
assert {"Număr contract:", "Data contract:", "Data început:", "Data sfârșit:", "Cod loc:", "Concesionar:", "Observații:"} <= labels
for w in (d.txt_nume, d.txt_prenume, d.txt_adresa, d.txt_bi_ci, d.txt_cnp, d.txt_telefon, d.txt_email, d.txt_sector, d.txt_parcela, d.txt_rand, d.txt_loc, d.txt_suprafata):
    assert w.parent() is d and not w.isVisible()
for w in (d.btn_open_loc, d.btn_add_concesionar, d.btn_open_concesionar, d.btn_save, d.btn_cancel):
    assert w.isVisible()
assert d.cmb_concesionar.currentData() == first
# Typed text must not silently save the previously selected person's ID.
d.cmb_concesionar.setEditText("Unknown")
d.txt_numar.setText("FICTIV-1")
assert d._collect()["concesionar_id"] is None
assert not d._collect()["owner_fullname"]
d.btn_save.click()
assert count("contracte") == 0
# Exact completion resolves by label even if currentData still refers to Alpha.
d.cmb_concesionar.setEditText(d.cmb_concesionar.itemText(d.cmb_concesionar.findData(second)))
assert d._collect()["concesionar_id"] == second
d.btn_save.click()
stored = db.get_contract(d.get_saved_id())
assert stored["concesionar_id"] == second
assert stored["owner_fullname"] == "Beta Fictiv"
assert stored["owner_address"] == "Adresa fictiva"
assert count() == 2 and count("contracte") == 1
''')


@pytest.mark.parametrize("empty_snapshot", [False, True])
def test_edit_preserves_all_historical_fields_and_documents(tmp_path, empty_snapshot):
    run(tmp_path, 'empty_snapshot = ' + repr(empty_snapshot) + r'''
from pathlib import Path
from contracts_window import AddContractDialog
import contract_generator as cg
import pdf_reports
from docx import Document
import fitz
cid = db.save_concesionar({"nume": "Istoric", "prenume": "Fictiv", "adresa": "Adresa veche", "bi_ci": "XX 123456", "cnp": "2000000000001", "telefon": "0700000000", "email": "fictiv@example.invalid"})
db.save_plot_to_db("TEST-1", [(0,0), (1,0), (1,1)], "#fff")
db.save_loc_metadata("TEST-1", {"sector": "A", "parcela": "P1", "rand": "R1", "numar": "1", "suprafata": 4.5})
d = AddContractDialog(None, selected_cod_loc="TEST-1", selected_concesionar_id=cid)
d.txt_numar.setText("ISTORIC-1"); d.btn_save.click()
k = d.get_saved_id()
assert k
if empty_snapshot:
    c = db.get_connection()
    c.execute("UPDATE contracte SET owner_fullname=NULL, owner_address='', cemetery_parcel=NULL, cemetery_row='', cemetery_tomb='', tomb_surface_m2=0, sector='' WHERE id=?", (k,))
    c.commit(); c.close()
before = db.get_contract(k)
# The person's and plot's current files may diverge from the contract snapshot.
db.save_concesionar({"nume": "Actual", "prenume": "Fictiv", "adresa": "Adresa noua"}, cid)
db.save_loc_metadata("TEST-1", {"sector": "Z", "parcela": "P9", "rand": "R9", "numar": "9", "suprafata": 9.0})
edit = AddContractDialog(None, existing=before, selected_cod_loc="OTHER", selected_concesionar_id=999)
assert edit.cmb_concesionar.currentData() == cid
assert edit.cmb_cod_loc.currentText() == "TEST-1"
assert not edit.cmb_concesionar.isEnabled() and not edit.cmb_cod_loc.isEnabled()
assert not edit.btn_add_concesionar.isEnabled()
edit._refresh_and_select_concesionar(cid)
assert edit.cmb_concesionar.currentData() == cid
edit.txt_observatii.setPlainText("Nota fictiva")
edit.btn_save.click()
after = db.get_contract(k)
assert {key: value for key, value in after.items() if key != "observatii"} == {key: value for key, value in before.items() if key != "observatii"}
assert after["observatii"] == "Nota fictiva"
assert count() == 1 and count("contracte") == 1
# Generate actual DOCX and PDF, without external Word/LibreOffice or network.
output = Path(db.DB_PATH).parent / "contract.docx"
result = cg.generate_contract(k, "templates/contract_template.docx", str(output))
assert result and output.is_file()
doc = Document(output)
text = "\n".join([p.text for p in doc.paragraphs] + [c.text for t in doc.tables for r in t.rows for c in r.cells])
assert "{{" not in text and "ISTORIC-1" in text
pdf = pdf_reports.render_contract_pdf(k)
with fitz.open(stream=pdf, filetype="pdf") as document:
    pdf_text = "\n".join(page.get_text() for page in document)
assert "ISTORIC-1" in pdf_text
if not empty_snapshot:
    assert "Istoric Fictiv" in text and "Actual Fictiv" not in text
    assert "Istoric Fictiv" in pdf_text and "Actual Fictiv" not in pdf_text
assert db.get_contract(k) == after
''')


def test_cancel_does_not_write_contract_or_person(tmp_path):
    run(tmp_path, r'''
from contracts_window import AddContractDialog
cid = db.save_concesionar({"nume": "Anulare", "prenume": "Fictiv"})
d = AddContractDialog(None, selected_concesionar_id=cid)
d.txt_numar.setText("NU-SALVA")
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
d.btn_cancel.click()
assert d.result() == QDialog.Rejected
assert count("contracte") == 0 and count() == 1
''')
