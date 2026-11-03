"""Etapa 2 — fereastra PySide6 de verificare a integrității (Pasul B). Strict read-only: nicio scriere.
Rulează izolat, într-un subproces cu folder de date propriu (nu atinge nicio bază reală)."""
import json
import os
import subprocess
import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]
SHARED = DESKTOP.parent / "shared"

PRE = r'''
import json, os, sys
sys.path.insert(0, {desktop!r}); sys.path.insert(0, {shared!r})
os.chdir({desktop!r})
from PySide6.QtWidgets import QApplication
app = QApplication([])
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


def test_integrity_window_runs_checks_and_reports_db_unchanged(tmp_path):
    out = run(tmp_path, r'''
from integrity_window import IntegrityWindow, IntegrityWorker

db.save_plot_to_db("T-1", [(0, 0), (40, 0), (40, 30), (0, 30)], "#111")
db.save_concesionar({"nume": "", "prenume": ""})   # CON001: nume gol -> trebuie semnalat

w = IntegrityWindow()
events = {"progress": 0, "finished": False, "report": None}
worker = IntegrityWorker(db.current_db_path())
worker.progress.connect(lambda done, total, label: events.__setitem__("progress", events["progress"] + 1))
worker.finished_ok.connect(lambda rep: (events.__setitem__("finished", True), events.__setitem__("report", rep)))
worker.failed.connect(lambda msg: events.__setitem__("error", msg))
worker.run()   # sincron, fara thread real, pentru test determinist

out["finished"] = events["finished"]
out["progress_calls"] = events["progress"]
rep = events["report"]
d = rep.to_dict() if rep else None
out["db_unchanged"] = d["meta"]["db_unchanged"] if d else None
out["has_con001"] = any(i["code"] == "CON001" for i in d["issues"]) if d else False

w._on_finished(rep)
out["tree_top_level_count"] = w.tree.topLevelItemCount()
out["export_buttons_enabled"] = w.btn_export_json.isEnabled() and w.btn_export_csv.isEnabled() and w.btn_export_md.isEnabled()
''')
    assert out["finished"] is True
    assert out["progress_calls"] > 0
    assert out["db_unchanged"] is True, "verificarea nu trebuie sa modifice baza de date"
    assert out["has_con001"] is True
    assert out["tree_top_level_count"] >= 1
    assert out["export_buttons_enabled"] is True


def test_integrity_worker_cancel_stops_without_report(tmp_path):
    out = run(tmp_path, r'''
from integrity_window import IntegrityWorker
db.save_plot_to_db("T-1", [(0, 0), (40, 0), (40, 30), (0, 30)], "#111")
worker = IntegrityWorker(db.current_db_path())
worker.cancel()  # anulat inainte de a porni
events = {"failed": None, "finished": False}
worker.failed.connect(lambda msg: events.__setitem__("failed", msg))
worker.finished_ok.connect(lambda rep: events.__setitem__("finished", True))
worker.run()
out["failed"] = events["failed"]
out["finished"] = events["finished"]
''')
    assert out["finished"] is False
    assert out["failed"] is not None and "anulat" in out["failed"].lower()


def test_integrity_export_json_writes_redacted_file(tmp_path):
    out_dir = tmp_path / "export"
    out_dir.mkdir()
    out = run(tmp_path, r'''
import cimitir_integrity as ci
db.save_plot_to_db("T-1", [(0, 0), (40, 0), (40, 30), (0, 30)], "#111")
cid = db.save_concesionar({"nume": "Test", "prenume": "Om", "cnp": "1800101123456"})
rep = ci.run_on_file(db.current_db_path(), categories=["concesionari"], include_info=True)
content = ci.to_json(rep)
out["has_cnp_literal"] = "1800101123456" in content
out["json_len"] = len(content)
''')
    assert out["has_cnp_literal"] is False, "CNP-ul nu trebuie sa apara niciodata in raportul exportat"
    assert out["json_len"] > 0
