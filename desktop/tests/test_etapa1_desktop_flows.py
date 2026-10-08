"""Etapa 1 — fluxuri desktop pe date fictive, în proces separat, pe aceeași bază ca portalul.

Fiecare test lucrează într-un folder temporar (nu atinge baza reală). Părțile PySide6 (ferestre)
sunt acoperite de celelalte teste din acest folder (offscreen); aici verificăm datele comune.
"""
import os
os.environ.setdefault("CIMITIR_PORTAL_REQUIRE_ACCOUNT", "0")
import importlib.util
import json
import os
import subprocess
import sys
import textwrap
import urllib.parse
import urllib.request
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parents[1]

WORKER = r'''
import json, os, sys
sys.path.insert(0, {desktop!r})
import database as db
import reports_export, pdf_reports

db.init_db()
out = {{"db": db.DB_PATH}}

# hartă + locuri
db.save_plot_to_db("F-01", [(0, 0), (30, 0), (30, 20), (0, 20)], "#22c55e")
db.save_plot_to_db("F-02", [(40, 0), (70, 0), (70, 20), (40, 20)], "#ef4444")
db.save_loc_metadata("F-01", {{"sector": "A", "parcela": "1", "rand": "1", "numar": "1", "disponibilitate": "Liber", "suprafata": 6}})
db.save_loc_metadata("F-02", {{"sector": "A", "parcela": "1", "rand": "1", "numar": "2", "disponibilitate": "Liber"}})
out["locuri"] = len(db.list_locuri())
out["map_points"] = len(json.loads(db.load_loc_by_cod("F-01")["points"])) if isinstance(db.load_loc_by_cod("F-01")["points"], str) else len(db.load_loc_by_cod("F-01")["points"])

# concesionar + contract + înhumat + document
cid = db.save_concesionar({{"nume": "Test", "prenume": "Fictiv", "cnp": "0000000000000", "adresa": "Str. Fictiva 1"}})
contract_id = db.save_contract({{"numar_contract": "F-1", "cod_loc": "F-01", "concesionar_id": cid, "data_sfarsit": "2099-12-31"}})
inh_id = db.save_inhumat("F-01", {{"nume": "Decedat", "prenume": "Fictiv", "data_deces": "01.01.2020"}})
src = os.path.join({tmp!r}, "act.txt")
open(src, "w").write("act fictiv")
doc_id = db.add_document("contract", contract_id, "contract_scan", src)
out.update(concesionari=len(db.load_concesionari()), contracte=len(db.load_contracte()),
           inhumati=len(db.load_all_inhumati()), docs=len(db.list_documents("contract", contract_id)),
           doc_exists=os.path.exists(db.get_document_path(doc_id)))

# rapoarte / export
xlsx = reports_export.export_contracte(os.path.join({tmp!r}, "contracte.xlsx"))
out["xlsx_bytes"] = os.path.getsize(xlsx)
pdf = pdf_reports.render_contract_pdf(contract_id)
out["pdf_header"] = pdf[:4].decode("latin1")
out["cereri_noi"] = db.count_cereri_noi()
print("RESULT " + json.dumps(out))
'''

READ_WORKER = r'''
import json, sys
sys.path.insert(0, {desktop!r})
import database as db
rows = db.list_cereri_concesionare("nou")
print("RESULT " + json.dumps({{"db": db.DB_PATH, "noi": db.count_cereri_noi(),
      "rows": [[r["id"], r["nume"], r["cod_loc"], r["status"], r["cod_urmarire"]] for r in rows]}}))
'''


def _run(code, data_dir, tmp):
    env = {k: v for k, v in os.environ.items() if k not in ("CIMITIR_DATA_DIR", "JWT_SECRET")}
    env.update(CIMITIR_DATA_DIR=str(data_dir), CIMITIR_ENV_FILE=str(tmp / "nu.env"), QT_QPA_PLATFORM="offscreen",
               CIMITIR_LOG_DIR=str(tmp / "logs"))
    proc = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(DESKTOP_DIR), capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")][-1]
    return json.loads(line[len("RESULT "):])


def test_desktop_flows_on_fictitious_data(tmp_path):
    data = tmp_path / "desktop-data"
    res = _run(WORKER.format(desktop=str(DESKTOP_DIR), tmp=str(tmp_path)), data, tmp_path)
    assert Path(res["db"]) == (data / "cimitir.db").resolve()
    assert (res["locuri"], res["map_points"]) == (2, 4)
    assert (res["concesionari"], res["contracte"], res["inhumati"], res["docs"]) == (1, 1, 1, 1)
    assert res["doc_exists"] is True
    assert res["xlsx_bytes"] > 1000 and res["pdf_header"] == "%PDF"


def test_portal_request_is_seen_by_desktop_process(tmp_path):
    spec = importlib.util.spec_from_file_location("start_local_flow", DESKTOP_DIR / "start_local.py")
    sl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sl)
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    data = tmp_path / "shared"
    data.mkdir()
    proc = sl.start_portal(sl.child_environment(data, "127.0.0.1", port), "127.0.0.1", port, data / "logs" / "p.log")
    try:
        assert sl.wait_for_health("127.0.0.1", port, 40, proc)
        body = urllib.parse.urlencode({"nume": "Solicitant", "prenume": "Fictiv", "cod_loc": "",
                                       "cnp": "0000000000000"}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/public/cerere", data=body, method="POST")
        created = json.loads(urllib.request.urlopen(req, timeout=10).read())
        seen = _run(READ_WORKER.format(desktop=str(DESKTOP_DIR)), data, tmp_path)
    finally:
        sl.stop_portal(proc)
    assert Path(seen["db"]) == (data / "cimitir.db").resolve()
    assert seen["noi"] == 1
    assert seen["rows"][0][:2] == [created["id"], "Solicitant"]
    assert seen["rows"][0][4] == created["cod_urmarire"]
