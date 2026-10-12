# ruff: noqa: F811
"""FIX 1: detalii loc identice în MAIN și în portalul local (liste canonice + observații).

Numai foldere temporare. MAIN = dialogul real EditLocDialog (PySide6 offscreen) rulat în subproces pe aceeași bază.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_etapa1_audit import _load_server
from test_etapa3_registries import add_plot, env, make_world  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
DESKTOP = ROOT / "desktop"
sys.path.insert(0, str(ROOT / "shared"))
import cimitir_loc_options as lo  # noqa: E402

PW = "Parola-op-12345"
PHONE = {"user-agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Mobile Safari/537.36"}
FIELDS = lo.LOC_SELECT_FIELDS

WORKER = r'''
import json, os, sys
sys.path.insert(0, {desktop!r}); os.chdir({desktop!r})
from PySide6.QtWidgets import QApplication, QWidget
app = QApplication([])
import database as db
from map_editor_actions import EditorActions
import locuri_window as lw
parent = QWidget(); parent.actions = EditorActions(parent)
dlg = lw.EditLocDialog(parent, {cod!r})
mode = sys.argv[1]
if mode == "set_obs":
    dlg.txt_observatii.setPlainText(sys.argv[2])
if mode in ("save", "set_obs"):
    dlg._on_save()
    dlg = lw.EditLocDialog(parent, {cod!r})
out = {{f: getattr(dlg, "cmb_" + f).currentText() for f in {fields!r}}}
out["items"] = {{f: [getattr(dlg, "cmb_" + f).itemText(i) for i in range(getattr(dlg, "cmb_" + f).count())] for f in {fields!r}}}
out["observatii"] = dlg.txt_observatii.toPlainText()
print("RESULT" + json.dumps(out))
'''


def main_dialog(data_dir: Path, mode="dump", arg="", cod="T-10"):
    env_ = dict(os.environ, CIMITIR_DATA_DIR=str(data_dir), CIMITIR_DB_ENGINE="sqlite", CIMITIR_MODE="local",
                DATABASE_URL="", QT_QPA_PLATFORM="offscreen", CIMITIR_IGNORE_PROJECT_ENV="1")
    p = subprocess.run([sys.executable, "-c", WORKER.format(desktop=str(DESKTOP), cod=cod, fields=list(FIELDS)), mode, arg],
                       capture_output=True, text=True, env=env_, timeout=90)
    line = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT")]
    assert line, p.stdout[-800:] + p.stderr[-1500:]
    return json.loads(line[-1][6:])


def _login(server, user="op", source="cimitir"):
    c = TestClient(server.app, headers=PHONE)
    r = c.post("/admin/access/login", data={"username": user, "password": PW, "source_id": source}, follow_redirects=False)
    assert r.status_code == 303, r.text[:200]
    return c


def _drawer_state(html: str):
    sel = {}
    for f in FIELDS:
        m = re.search(rf'<select name="{f}"[^>]*>(.*?)</select>', html, re.S)
        assert m, f
        opts = re.findall(r'<option value="([^"]*)"\s*(selected)?\s*>', m.group(1))
        sel[f] = ([o for o, _ in opts], next((o for o, s in opts if s), ""))
    m = re.search(r'<textarea name="observatii"[^>]*>(.*?)</textarea>', html, re.S)
    return sel, (m.group(1).strip() if m else None)


@pytest.fixture()
def phone(tmp_path):
    server = _load_server(tmp_path / "data")
    import cimitir_auth
    import database as db
    db.init_db()
    cimitir_auth.init_auth()
    cimitir_auth.create_user("op", PW, "operator")
    add_plot(db, "T-10")
    return server, db, tmp_path / "data"


VALUES = {"capacitate": "Dublu", "tip": "Cavou", "stare": "Bună", "categorie": "Categoria II", "disponibilitate": "Posibil liber"}


# ============================================================ FIX 1
def test_phone_and_main_share_same_option_lists(phone):
    server, db, data = phone
    c = _login(server)
    state, _ = _drawer_state(c.get("/admin/ui/loc/T-10").text)
    main = main_dialog(data)
    for f in FIELDS:
        assert state[f][0] == lo.options_for(f) == main["items"][f], f
    assert [o for o in state["capacitate"][0] if o] == ["Simplu", "Dublu", "Cvadrupul"]


def test_phone_to_main_to_phone_roundtrip_all_fields_and_observatii(phone):
    server, db, data = phone
    c = _login(server)
    obs = "Monument degradat\nde verificat în aprilie"
    r = c.post("/admin/ui/loc/T-10/edit", data={"sector": "A", "parcela": "1", "rand": "1", "numar": "10", **VALUES, "observatii": obs})
    assert r.status_code == 200
    loc = db.load_loc_by_cod("T-10")
    assert loc["extra_data"]["observatii"].replace("\r\n", "\n") == obs and loc["observatii"].replace("\r\n", "\n") == obs
    # phone -> MAIN
    main = main_dialog(data)
    assert {f: main[f] for f in FIELDS} == VALUES
    assert main["observatii"].replace("\r\n", "\n") == obs
    # Save din MAIN fără modificări -> nimic pierdut
    again = main_dialog(data, "save")
    assert {f: again[f] for f in FIELDS} == VALUES and again["observatii"] == main["observatii"]
    state, o = _drawer_state(c.get("/admin/ui/loc/T-10").text)
    assert {f: state[f][1] for f in FIELDS} == VALUES and o.replace("&#10;", "\n").replace("\r\n", "\n") == obs
    # MAIN modifică observația -> telefonul (refresh) o vede
    main_dialog(data, "set_obs", "Observație nouă din MAIN")
    _, o2 = _drawer_state(c.get("/admin/ui/loc/T-10").text)
    assert o2 == "Observație nouă din MAIN"
    assert db.load_loc_by_cod("T-10")["observatii"] == "Observație nouă din MAIN"
    # telefon -> salvează fără modificări: valorile rămân
    st, o3 = _drawer_state(c.get("/admin/ui/loc/T-10").text)
    c.post("/admin/ui/loc/T-10/edit", data={**{f: st[f][1] for f in FIELDS}, "observatii": o3})
    assert {f: db.load_loc_by_cod("T-10")[f] for f in FIELDS} == VALUES


def test_historical_values_survive_both_interfaces_and_other_extra_keys_kept(phone):
    server, db, data = phone
    db.save_loc_metadata("T-10", {"tip": "Piatră veche", "stare": "Excelentă", "capacitate": "Cvadruplu",
                                  "extra_data": {"concesionar": "X", "contract_nr": "7"}})
    c = _login(server)
    state, _ = _drawer_state(c.get("/admin/ui/loc/T-10").text)
    assert state["tip"][1] == "Piatră veche" and "Piatră veche" in state["tip"][0]
    assert state["capacitate"][1] == "Cvadruplu"
    main = main_dialog(data)
    assert (main["tip"], main["stare"], main["capacitate"]) == ("Piatră veche", "Excelentă", "Cvadruplu")
    main_dialog(data, "save")
    loc = db.load_loc_by_cod("T-10")
    assert (loc["tip"], loc["stare"], loc["capacitate"]) == ("Piatră veche", "Excelentă", "Cvadruplu")
    st, _o = _drawer_state(c.get("/admin/ui/loc/T-10").text)
    c.post("/admin/ui/loc/T-10/edit", data={**{f: st[f][1] for f in FIELDS}, "observatii": "ok"})
    loc = db.load_loc_by_cod("T-10")
    assert (loc["tip"], loc["stare"], loc["capacitate"]) == ("Piatră veche", "Excelentă", "Cvadruplu")
    assert loc["extra_data"]["concesionar"] == "X" and loc["extra_data"]["contract_nr"] == "7" and loc["extra_data"]["observatii"] == "ok"


def test_phone_rejects_unknown_option_without_changing_data(phone):
    server, db, _ = phone
    c = _login(server)
    c.post("/admin/ui/loc/T-10/edit", data={"tip": "Cavou"})
    r = c.post("/admin/ui/loc/T-10/edit", data={"tip": "Inventat", "stare": "Bună"})
    assert r.status_code == 400
    loc = db.load_loc_by_cod("T-10")
    assert loc["tip"] == "Cavou" and not loc["stare"]


def test_legacy_observatii_only_in_column_visible_in_both_and_clearing_in_main_sticks(phone):
    server, db, data = phone
    db.save_loc_metadata("T-10", {"observatii": "veche din coloană"})
    c = _login(server)
    assert _drawer_state(c.get("/admin/ui/loc/T-10").text)[1] == "veche din coloană"
    assert main_dialog(data)["observatii"] == "veche din coloană"
    main_dialog(data, "set_obs", "")                       # MAIN șterge observația
    assert _drawer_state(c.get("/admin/ui/loc/T-10").text)[1] == ""
    assert main_dialog(data)["observatii"] == ""
