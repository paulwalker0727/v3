"""Regresie: apăsări repetate pe „Salvează” în dialogul de contract nu trebuie să creeze două contracte.
Subproces offscreen, folder temporar, date fictive."""
import json
import os
import subprocess
import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]
SHARED = DESKTOP.parent / "shared"

CODE = r'''
import json, os, sys
sys.path.insert(0, {desktop!r}); sys.path.insert(0, {shared!r})
os.chdir({desktop!r})
from PySide6.QtWidgets import QApplication, QMessageBox
app = QApplication([])
for n in ("information", "warning", "critical"):
    setattr(QMessageBox, n, staticmethod(lambda *a, **k: 0))
import database as db
db.init_db()
assert str(db.DB_PATH).startswith({data!r}), db.DB_PATH
db.save_plot_to_db("L-1", [(0, 0), (1, 0), (1, 1)], "#fff")
cid = db.save_concesionar({{"nume": "Ana", "prenume": "Fictiv", "cnp": "2000000000001"}})
from contracts_window import AddContractDialog
d = AddContractDialog(None, None, existing=None, selected_cod_loc="L-1", selected_concesionar_id=cid)
d.txt_numar.setText("C-1")
d.btn_save.click(); d.btn_save.click(); d.btn_save.click()
c = db.get_connection()
rows = [dict(r) for r in c.execute("SELECT id, numar_contract, concesionar_id FROM contracte")]
c.close()
print("RESULT " + json.dumps({{"rows": rows, "saved_id": d.get_saved_id()}}))
'''


def test_repeated_save_creates_one_contract(tmp_path):
    data = str(tmp_path / "data")
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_") and k not in ("DATABASE_URL", "SUPABASE_URL")}
    env.update(CIMITIR_DATA_DIR=data, CIMITIR_ENV_FILE=str(tmp_path / "nu.env"), CIMITIR_LOG_DIR=str(tmp_path / "logs"),
               CIMITIR_DB_ENGINE="sqlite", CIMITIR_MODE="local", DATABASE_URL="", QT_QPA_PLATFORM="offscreen")
    p = subprocess.run([sys.executable, "-c", CODE.format(desktop=str(DESKTOP), shared=str(SHARED), data=data)], env=env, cwd=str(DESKTOP),
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr[-3000:]
    out = json.loads([ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:])
    assert len(out["rows"]) == 1, out
    assert out["saved_id"] == out["rows"][0]["id"]
