"""Lucrările scrise din telefon (portal) sunt vizibile complet în MAIN admin și MAIN operator, cu pozele pe etape.

Portalul scrie în baza SQLite + media; MAIN (proces separat, aceeași bază) citește același folder.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_mobile_local_portal import JPG, PDF, _login, _up, phone  # noqa: F401

pytest.importorskip("PySide6")

DESKTOP = Path(__file__).resolve().parents[2] / "desktop"

WORKER = r'''
import json, os, sys
sys.path.insert(0, {desktop!r})
os.chdir({desktop!r})
from PySide6.QtWidgets import QApplication, QLabel, QWidget
app = QApplication([])
import database as db
db.init_db()
import theme

import re
def base(p): return re.sub(r"^(\d+_)+", "", os.path.basename(p))

out = {{}}
# ---- MAIN admin ----
from panels import RightPanel
class Stub(QWidget):
    actions = None
rp = RightPanel(Stub())
rp.update_loc_details("T-10", {{}})
dp = rp.doc_strip_aviz
out["admin"] = {{
    "rows": [[rp.tbl_avize.item(r, c).text() for c in range(rp.tbl_avize.columnCount())] for r in range(rp.tbl_avize.rowCount())],
    "exec": dp.lbl_exec.text(), "start": dp.lbl_start.text(), "stare": dp.lbl_stare.text(), "final": dp.lbl_final.text(), "obs": dp.lbl_obs.text(),
    "stages": {{k: sorted(base(l.toolTip()) for l in s.findChildren(QLabel) if l.toolTip()) for k, s in dp.strips.items()}},
    "other": sorted(base(l.toolTip()) for l in dp.other.findChildren(QLabel) if l.toolTip()),
}}
# ---- MAIN operator ----
import operator_detail_panel as odp
class W:
    _pal = theme.get_palette("dark")
panel = odp.OperatorDetailPanel(W())
panel.show_loc("T-10")
app.processEvents()
card = {{}}
for w in panel.findChildren(QWidget):
    n = w.objectName()
    if n.startswith("aviz-stage-"):
        card.setdefault(n[11:], []).extend(sorted(base(t.doc["file_path"]) for t in w.findChildren(odp._Thumb)))
        card.setdefault(n[11:] + "_title", []).extend([l.text() for l in w.findChildren(QLabel) if "(" in l.text() and ")" in l.text()][:1])
info = {{}}
for n in ("executant", "data-inceput", "stare", "finalizare", "observatii"):
    info[n] = [l.text() for w in panel.findChildren(QWidget, "aviz-" + n) for l in w.findChildren(QLabel)]
out["operator"] = {{"stages": card, "info": info,
                    "titles": [b.text() for b in panel.findChildren(odp.QPushButton) if "Aviz" in b.text()]}}
print("RESULT " + json.dumps(out))
'''


def _main_view(data_dir):
    env = dict(os.environ, CIMITIR_DATA_DIR=str(data_dir), QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-c", WORKER.format(desktop=str(DESKTOP))], env=env, cwd=str(DESKTOP),
                          capture_output=True, text=True, timeout=150)
    assert proc.returncode == 0, proc.stderr[-2500:]
    return json.loads([ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:])


def test_phone_work_details_and_photo_stages_visible_in_main_admin_and_operator(phone):  # noqa: F811
    server, db, tmp = phone
    c, _ = _login(server)
    assert c.post("/admin/ui/loc/T-10/aviz", data={"lucrare": "Reparație monument", "nr": "A-7", "data": "10.03.2025",
                                                    "stare": "in_lucru", "observatii": "ciment", "executant": "SC Marmura SRL",
                                                    "data_inceput": "01.03.2025"}).status_code == 200
    aid = db.list_avize_for_loc("T-10")[0]["id"]
    assert c.post(f"/admin/ui/aviz/{aid}/edit", data={"stare": "finalizata", "observatii": "Test observație"}).status_code == 200
    assert (db.get_aviz(aid)["executant"], db.get_aviz(aid)["data_inceput"]) == ("SC Marmura SRL", "01.03.2025")
    drawer = c.get("/admin/ui/loc/T-10").text
    assert "Executant: SC Marmura SRL" in drawer and "Început: 01.03.2025" in drawer
    for dt, name in (("lucrare_inainte", "inainte.jpg"), ("lucrare_in_timpul", "in_timpul.jpg"), ("lucrare_dupa", "dupa.jpg")):
        assert _up(c, "aviz", aid, dt, JPG, name).status_code == 200
    db.update_aviz(aid, {"data_finalizare": "15.04.2025"})  # dată cunoscută (portalul pune implicit data de azi)
    legacy = tmp / "legacy.jpg"
    legacy.write_bytes(JPG)
    other_pdf = tmp / "autorizatie.pdf"
    other_pdf.write_bytes(PDF)
    db.add_document("aviz", aid, "foto_lucrare", str(legacy))
    db.add_document("aviz", aid, "", str(other_pdf))
    db.add_aviz("T-10", "M-1", "01.01.2025", "Lucrare MAIN")

    out = _main_view(tmp / "data")

    a = out["admin"]
    assert a["stare"] == "Finalizată"
    assert a["obs"] == "Test observație"
    assert a["final"] == "15.04.2025"
    assert a["exec"] == "SC Marmura SRL" and a["start"] == "01.03.2025"
    assert any(r[2] == "Reparație monument" and r[6] == "SC Marmura SRL" and r[7] == "01.03.2025" for r in a["rows"])
    assert any(r[2] == "Lucrare MAIN" and r[6] == "" and r[7] == "" for r in a["rows"]), "lucrările fără executant/dată rămân goale"
    assert any(r[2] == "Reparație monument" and r[4] == "Finalizată" and r[5] == "15.04.2025" for r in a["rows"])
    assert any(r[2] == "Lucrare MAIN" and r[4] == "Nespecificată" and r[5] == "" for r in a["rows"])
    assert a["stages"] == {"lucrare_inainte": ["inainte.jpg"], "lucrare_in_timpul": ["in_timpul.jpg"], "lucrare_dupa": ["dupa.jpg"]}
    assert a["other"] == ["autorizatie.pdf", "legacy.jpg"], "pozele vechi / necategorizate rămân vizibile"

    o = out["operator"]
    assert o["stages"]["lucrare_inainte"] == ["inainte.jpg"]
    assert o["stages"]["lucrare_in_timpul"] == ["in_timpul.jpg"]
    assert o["stages"]["lucrare_dupa"] == ["dupa.jpg"]
    assert o["stages"]["other"] == ["autorizatie.pdf", "legacy.jpg"]
    assert o["stages"]["lucrare_inainte_title"] == ["ÎNAINTE (1)", "ÎNAINTE (0)"]
    assert o["stages"]["lucrare_in_timpul_title"] == ["ÎN TIMPUL LUCRĂRII (1)", "ÎN TIMPUL LUCRĂRII (0)"]
    assert o["stages"]["lucrare_dupa_title"] == ["DUPĂ (1)", "DUPĂ (0)"]
    assert any("SC Marmura SRL" in t for t in o["info"]["executant"])
    assert any("01.03.2025" in t for t in o["info"]["data-inceput"])
    assert any("Finalizată" in t for t in o["info"]["stare"])
    assert any("15.04.2025" in t for t in o["info"]["finalizare"])
    assert any("Test observație" in t for t in o["info"]["observatii"])
    assert any("Reparație monument" in t and "Finalizată" in t for t in o["titles"])
