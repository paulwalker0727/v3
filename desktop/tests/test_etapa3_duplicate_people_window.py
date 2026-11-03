"""Etapa 3 (numerotare sesiune curentă) — fereastra de persoane duplicate (concesionari/înhumați).
Reutilizează verificările CON00x/INH00x din shared/cimitir_integrity/. Nu șterge, nu fuzionează automat.
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


def test_duplicate_window_groups_concesionari_with_same_cnp(tmp_path):
    out = run(tmp_path, r'''
from duplicate_people_window import DuplicatePeopleWindow
import cimitir_integrity as ci

db.save_plot_to_db("T-1", [(0, 0), (40, 0), (40, 30), (0, 30)], "#111")
cid1 = db.save_concesionar({"nume": "Popescu", "prenume": "Ion", "cnp": "1800101123456", "telefon": "0722000000"})
cid2 = db.save_concesionar({"nume": "Popescu", "prenume": "Ion", "cnp": "1800101123456", "telefon": "0722000001"})
db.save_concesionar({"nume": "Georgescu", "prenume": "Maria", "cnp": "2850505123457"})

w = DuplicatePeopleWindow()
rep = ci.run_on_file(db.current_db_path(), categories=["concesionari", "inhumati"], include_info=True)
w._on_finished(rep)

out["top_level_count"] = w.tree.topLevelItemCount()
first = w.tree.topLevelItem(0)
out["first_group_label"] = first.text(0)
out["first_group_confidence"] = first.text(1)
out["children_count"] = first.childCount()
out["cid1"] = cid1
out["cid2"] = cid2
''')
    assert out["top_level_count"] >= 1
    assert "CON004" in out["first_group_label"]
    assert out["first_group_confidence"] == "Sigur"
    assert out["children_count"] == 2


def test_duplicate_window_no_groups_when_no_duplicates(tmp_path):
    out = run(tmp_path, r'''
from duplicate_people_window import DuplicatePeopleWindow
import cimitir_integrity as ci

db.save_plot_to_db("T-1", [(0, 0), (40, 0), (40, 30), (0, 30)], "#111")
db.save_concesionar({"nume": "Unic", "prenume": "Fararudenii", "cnp": "1800101123458"})

w = DuplicatePeopleWindow()
rep = ci.run_on_file(db.current_db_path(), categories=["concesionari", "inhumati"], include_info=True)
w._on_finished(rep)
out["top_level_count"] = w.tree.topLevelItemCount()
out["status_text"] = w.lbl_status.text()
''')
    assert out["top_level_count"] == 0
    assert "Nicio" in out["status_text"]


def test_duplicate_window_does_not_modify_database(tmp_path):
    out = run(tmp_path, r'''
from duplicate_people_window import DuplicatePeopleWindow
import cimitir_integrity as ci

db.save_plot_to_db("T-1", [(0, 0), (40, 0), (40, 30), (0, 30)], "#111")
db.save_concesionar({"nume": "Popescu", "prenume": "Ion", "cnp": "1800101123456"})
db.save_concesionar({"nume": "Popescu", "prenume": "Ion", "cnp": "1800101123456"})
before = len(db.load_concesionari()) if hasattr(db, "load_concesionari") else None

w = DuplicatePeopleWindow()
rep = ci.run_on_file(db.current_db_path(), categories=["concesionari", "inhumati"], include_info=True)
w._on_finished(rep)

out["db_unchanged"] = rep.to_dict()["meta"]["db_unchanged"]
out["before"] = before
out["after"] = len(db.load_concesionari()) if hasattr(db, "load_concesionari") else None
''')
    assert out["db_unchanged"] is True
    if out["before"] is not None:
        assert out["before"] == out["after"] == 2, "fereastra de examinare nu trebuie sa stearga sau sa fuzioneze nimic"
