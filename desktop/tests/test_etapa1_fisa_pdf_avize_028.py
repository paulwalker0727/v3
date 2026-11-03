"""Etapa 1/4 — fisa PDF (MAIN) include campurile extinse ale avizelor (migrarea 028): stare, executant,
inceput, finalizare, observatii. Regenerarea PDF dupa editare trebuie sa reflecte valorile noi.
Ruleaza izolat, intr-un subproces cu folder de date propriu (nu atinge nicio baza reala)."""
import json
import os
import subprocess
import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]
SHARED = DESKTOP.parent / "shared"

PRE = r'''
import json, os, sys, base64
sys.path.insert(0, {desktop!r}); sys.path.insert(0, {shared!r})
os.chdir({desktop!r})
import database as db
db.init_db()
out = {{}}
'''
POST = "\nprint('RESULT ' + json.dumps(out, default=str))\n"


def run(tmp_path, body):
    code = PRE.format(desktop=str(DESKTOP), shared=str(SHARED)) + body + POST
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_")}
    env.update(CIMITIR_DATA_DIR=str(tmp_path / "data"), CIMITIR_ENV_FILE=str(tmp_path / "nu.env"), CIMITIR_LOG_DIR=str(tmp_path / "logs"),
               CIMITIR_DB_ENGINE="sqlite", CIMITIR_MODE="local", DATABASE_URL="", QT_QPA_PLATFORM="offscreen")
    p = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(DESKTOP), capture_output=True, text=True, timeout=150)
    assert p.returncode == 0, p.stderr[-4000:]
    return json.loads([ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:])


def test_fisa_pdf_includes_extended_aviz_fields_after_edit(tmp_path):
    out = run(tmp_path, r'''
import fisa_pdf
db.save_plot_to_db("T-1", [(0, 0), (40, 0), (40, 30), (0, 30)], "#111")
aid = db.add_aviz("T-1", "AV-77", "01.01.2026", "zugravit gard", "Firma Alfa", "02.01.2026")
db.update_aviz(aid, {"stare": "finalizata", "data_finalizare": "15.01.2026", "observatii": "fara obiectii"})
pdf_bytes = fisa_pdf.build_fisa_pdf("T-1")
out["pdf_len"] = len(pdf_bytes)
out["pdf_b64"] = base64.b64encode(pdf_bytes).decode()
''')
    assert out["pdf_len"] > 1000
    import base64
    import fitz  # PyMuPDF
    pdf_bytes = base64.b64decode(out["pdf_b64"])
    text = "".join(page.get_text() for page in fitz.open(stream=pdf_bytes, filetype="pdf"))
    assert "Finalizat" in text or "Finalizată" in text
    assert "Firma Alfa" in text
    assert "fara obiectii" in text or "fără obiecții" in text or "obiectii" in text
