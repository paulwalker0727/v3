"""P2 §3: documente (Word contract, PDF contract/chitanță): date corecte, diacritice, markup ostil, fișiere lipsă. Date fictive."""
import sqlite3
import sys
from pathlib import Path

import pytest

DESKTOP = Path(__file__).resolve().parents[1]
SHARED = DESKTOP.parent / "shared"
for p in (str(DESKTOP), str(SHARED)):
    if p not in sys.path:
        sys.path.insert(0, p)

import database as db  # noqa: E402
import contract_generator as cg  # noqa: E402
import pdf_reports as pr  # noqa: E402

TEMPLATE = DESKTOP / "templates" / "contract_template.docx"


@pytest.fixture
def d(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "cimitir.db"))
    db.init_db()
    db.save_plot_to_db("A-1", [(0, 0), (1, 0), (1, 1)], "#fff")
    db.save_loc_metadata("A-1", {"parcela": "P3", "rand": "R4", "numar": "12", "suprafata": 6.0, "sector": "B"})
    return db


def docx_text(path):
    from docx import Document
    doc = Document(path)
    parts = [p.text for p in doc.paragraphs]
    for t in doc.tables:
        for row in t.rows:
            parts += [c.text for c in row.cells]
    return "\n".join(parts)


def pdf_text(data):
    fitz = pytest.importorskip("fitz")
    doc = fitz.open(stream=data, filetype="pdf")
    return "\n".join(page.get_text() for page in doc)


def make_contract(d, **over):
    cid = d.save_concesionar({"nume": "Țîrlea", "prenume": "Ștefan", "cnp": "1500101123456", "adresa": "Str. Școlii nr. 5, Șimian",
                              "bi_ci": "MH 123456", "telefon": "0700000000", "email": "a@exemplu.ro"})
    data = {"numar_contract": "12/2020", "data_contract": "05.03.2020", "data_inceput": "05.03.2020", "data_sfarsit": "05.03.2045",
            "cod_loc": "A-1", "concesionar_id": cid}
    data.update(over)
    return cid, d.save_contract(data)


def test_toate_placeholderele_din_sablon_au_valoare_in_mapare(d):
    from docx import Document
    import re
    doc = Document(str(TEMPLATE))
    text = "\n".join(p.text for p in doc.paragraphs) + "\n".join(c.text for t in doc.tables for r in t.rows for c in r.cells)
    needed = set(re.findall(r"\{\{\s*(\w+)\s*\}\}", text))
    _, k = make_contract(d)
    mapping = cg._build_mapping(d.get_contract(k))
    assert needed <= set(mapping), f"șablon cu placeholdere fără mapare: {needed - set(mapping)}"


def test_contract_word_complet_cu_diacritice_si_date_romanesti(d, tmp_path):
    cid, k = make_contract(d)
    out = tmp_path / "ieșire cu spații și diacritice ăîșț" / "Contract.docx"
    res = cg.generate_contract(k, str(TEMPLATE), str(out))
    assert res and Path(res["path"]).is_file() and res["missing"] == []
    text = docx_text(res["path"])
    assert "{{" not in text and "}}" not in text, "placeholdere rămase în document"
    for needle in ("12/2020", "05.03.2020", "Țîrlea Ștefan", "Str. Școlii nr. 5, Șimian", "MH 123456", "1500101123456", "P3", "R4", "12", "6.00"):
        assert needle in text, needle
    assert text.count("Țîrlea Ștefan") >= 2, "titularul apare și la semnătură"


def test_contract_fara_date_raporteaza_campurile_lipsa_dar_nu_inventeaza(d, tmp_path):
    k = d.save_contract({"numar_contract": "9", "data_contract": "01.01.2021"})
    res = cg.generate_contract(k, str(TEMPLATE), str(tmp_path / "c.docx"))
    assert set(res["missing"]) >= {"owner_fullname", "owner_cnp", "cemetery_parcel"}
    assert "01.01.2021" in docx_text(res["path"])


def test_nume_fisier_implicit_este_sigur_pe_windows_si_fara_diacritice_deformate(d):
    _, k = make_contract(d, numar_contract="12/2020 ă")
    name = cg.build_default_filename(d.get_contract(k))
    assert name.endswith(".docx") and not set('<>:"/\\|?*') & set(name)
    assert "12" in name and "2020" in name


def test_generare_repetata_nu_creeaza_contracte_noi_in_baza(d, tmp_path):
    _, k = make_contract(d)
    before = d.get_contract(k)
    for i in range(3):
        assert cg.generate_contract(k, str(TEMPLATE), str(tmp_path / f"c{i}.docx"))
    assert d.get_contract(k) == before and len(d.load_contracte()) == 1


@pytest.mark.parametrize("obs", ["normal ț ș ă î â", "suma < 100 & altele", "<b>neînchis", "a</b>b", "<para>x"])
def test_pdf_contract_si_chitanta_se_genereaza_si_cu_text_ostil(d, obs):
    _, k = make_contract(d, observatii=obs, owner_fullname="Țîrlea <Ștefan> & Fiii", owner_address="Str. <Mare>")
    p = d.add_plata("A-1", 120.5, "2020-03-05", "concesiune", "R<1>&2", obs, contract_id=k)
    c, r = pr.render_contract_pdf(k), pr.render_receipt_pdf(p)
    assert c.startswith(b"%PDF") and r.startswith(b"%PDF")


def test_pdf_contract_si_chitanta_pastreaza_diacriticele_si_datele(d):
    _, k = make_contract(d)
    p = d.add_plata("A-1", 1234.5, "2020-03-05", "concesiune", "CH-77", contract_id=k)
    ct, rt = pdf_text(pr.render_contract_pdf(k)), pdf_text(pr.render_receipt_pdf(p))
    for needle in ("Țîrlea", "Ștefan", "05.03.2020", "05.03.2045", "12/2020", "A-1"):
        assert needle in ct, needle
    for needle in ("CHITANȚĂ", "CH-77", "1 234.50 lei", "Țîrlea Ștefan", "A-1", "12/2020", "05.03.2020"):
        assert needle in rt, needle


def test_chitanta_pentru_plata_fara_contract_nu_afiseaza_un_platitor_gresit(d):
    cid, k = make_contract(d)
    p = d.add_plata("A-1", 10, "2020-03-05", chitanta_nr="X-1")      # fără contract_id (așa creează UI plățile)
    rt = pdf_text(pr.render_receipt_pdf(p))
    assert "Țîrlea" not in rt, "chitanța nu trebuie să ghicească plătitorul"
    assert "____" in rt, "câmpul plătitor rămâne necompletat (de completat manual) — vezi raport: plățile din UI nu se leagă de contract"


def test_document_inregistrat_dar_fisier_lipsa_este_vizibil_si_nu_sters(d, tmp_path):
    f = tmp_path / "act.pdf"
    f.write_bytes(b"%PDF-1.4 fictiv")
    doc_id = d.add_document("loc", "A-1", "act", str(f))
    assert doc_id
    stored = d.get_document_path(doc_id)
    assert stored and Path(stored).is_file()
    Path(stored).unlink()
    rows = d.list_documents("loc", "A-1")
    assert len(rows) == 1, "înregistrarea din baza de date nu se șterge când fișierul lipsește"
    from cimitir_integrity.runner import run_checks
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    report = run_checks(conn, media_root=d.media_root())
    conn.close()
    codes = {f_.code for f_ in report.findings} if hasattr(report, "findings") else str(report)
    assert "DOC004" in codes or "DOC004" in str(codes)
