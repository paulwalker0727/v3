"""P3-B: PDF-urile din backend/cimitir/pdf_reports.py nu pică la text cu markup invalid și nu pierd textul valid.

Rulează într-un subproces (module `database`/`store` colizionează cu cele din desktop). Date fictive, SQLite în tmp.
"""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

SCRIPT = textwrap.dedent(r'''
    import json, os, sys, tempfile, re
    sys.path[:0] = [r"%(root)s/backend/cimitir", r"%(root)s/backend", r"%(root)s/shared"]
    tmp = tempfile.mkdtemp()
    os.environ.update(CIMITIR_DB_ENGINE="sqlite", CIMITIR_MODE="local", DATABASE_URL="", CIMITIR_DATA_DIR=tmp)
    import database as db
    db.DB_PATH = os.path.join(tmp, "t.db"); db.init_db()
    import pdf_reports as pr
    import fitz
    def text(b):
        d = fitz.open(stream=b, filetype="pdf")
        return re.sub(r"\s+", " ", " ".join(p.get_text() for p in d))
    VALUES = json.loads(sys.argv[1])
    db.save_plot_to_db("A-1", [(0,0),(1,0),(1,1)], "#fff")
    res = []
    for n, obs in enumerate(VALUES):
        cid = db.save_concesionar({"nume": "Test", "prenume": "Unu"})
        k = db.save_contract({"numar_contract": f"N-{n}", "cod_loc": "A-1", "concesionar_id": cid, "owner_fullname": "Titular " + obs,
                              "owner_address": "Adr " + obs, "observatii": "Obs " + obs, "data_inceput": "2020-01-01", "data_sfarsit": "2030-01-01"})
        i = db.save_inhumat("A-1", {"nume": "Def " + obs, "prenume": "P", "cauza_deces": "Cauza " + obs, "nr_certificat_deces": "CD" + str(n)})
        p = db.add_plata("A-1", 10 + n, "2020-03-05", "concesiune", f"CH-{n}", "Plata " + obs, contract_id=k)
        row = {"obs": obs}
        for name, fn in [("contract", lambda: pr.render_contract_pdf(k)), ("chitanta", lambda: pr.render_receipt_pdf(p)),
                         ("adeverinta", lambda: pr.render_burial_certificate_pdf(i)), ("expirari", lambda: pr.render_expiring_contracts_pdf(5000)),
                         ("financiar", lambda: pr.render_financial_report_pdf()), ("registru", lambda: pr.render_burials_register_pdf())]:
            try:
                b = fn()
                assert b[:4] == b"%%PDF"
                row[name] = text(b)
            except Exception as e:
                row[name] = "FAIL " + type(e).__name__ + str(e)[:150]
        res.append(row)
    print("JSON::" + json.dumps(res))
''')

VALUES = [
    "Țîrlea Ștefan ț ș ă î â",
    "A & B Fiii",
    "suma < 100 și > 50",
    "<b>neînchis",
    "a</b>b",
    "<para>x",
    "<font color='red'>roșu",
    "&unknown; &amp; &#35;",
    "<",
    ">",
    "",
    "日本語 ✓ 😀",
    "cuvânt lung " * 300,
]

TEXT_CHECK = ["Țîrlea Ștefan ț ș ă î â", "A & B Fiii", "suma < 100 și > 50", "<b>neînchis", "a</b>b"]   # taguri reportlab valide și complete (ex. <para>) rămân interpretate ca formatare
DOC_WITH_TEXT = {"contract": "Titular ", "adeverinta": "Def ", "chitanta": "Titular "}


@pytest.fixture(scope="module")
def results():
    out = subprocess.run([sys.executable, "-c", SCRIPT % {"root": str(ROOT)}, json.dumps(VALUES)],
                         capture_output=True, text=True, timeout=300, cwd=str(ROOT / "backend"))
    line = [ln for ln in out.stdout.splitlines() if ln.startswith("JSON::")]
    assert line, out.stderr[-2000:]
    return json.loads(line[0][6:])


@pytest.mark.parametrize("doc", ["contract", "chitanta", "adeverinta", "expirari", "financiar", "registru"])
def test_toate_tipurile_de_pdf_se_genereaza_pentru_orice_text(results, doc):
    bad = [(r["obs"][:25], r[doc][:120]) for r in results if r[doc].startswith("FAIL")]
    assert not bad, bad


@pytest.mark.parametrize("value", TEXT_CHECK)
def test_textul_utilizatorului_nu_se_pierde_in_contract_si_adeverinta(results, value):
    row = next(r for r in results if r["obs"] == value)
    assert value in row["contract"], row["contract"][:400]
    assert value in row["adeverinta"] or value.replace("  ", " ") in row["adeverinta"]


def test_formatarea_documentelor_ramane_pentru_text_valid(results):
    row = next(r for r in results if r["obs"].startswith("Țîrlea"))
    assert "CONTRACT" in row["contract"].upper() and "PĂRȚILE CONTRACTANTE" in row["contract"]
    assert "CHITANȚĂ" in row["chitanta"] and "10.00 lei".replace(".", ".") in row["chitanta"].replace(" ", " ") or "10" in row["chitanta"]
    assert "<b>" not in row["contract"].replace("<b>neînchis", "")


def test_camp_gol_nu_strica_documentul(results):
    row = next(r for r in results if r["obs"] == "")
    assert all(not v.startswith("FAIL") for k, v in row.items() if k != "obs")
