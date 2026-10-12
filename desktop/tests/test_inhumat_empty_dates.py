"""MAIN: dialogul „Decedat" nu inventează date. Datele lipsă rămân goale la deschidere + Salvare; adeverința nu tipărește date false."""
import json
import os
import subprocess
import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]

WORKER = r'''
import json, os, sys
sys.path.insert(0, {desktop!r})
os.chdir({desktop!r})
from PySide6.QtWidgets import QApplication, QMessageBox
app = QApplication([])
import database as db
db.init_db()
db.save_plot_to_db("T-1", [(0, 0), (40, 0), (40, 30), (0, 30)], "#111")
import add_inhumat_dialog as aid

DATES = ("data_nasterii", "data_deces", "data_inhumare", "data_deshumare")
out = {{}}

def reopen_and_save(iid):
    dlg = aid.AddInhumatDialog(None, "T-1", existing=db.get_inhumat(iid))
    dlg._save()
    data = dlg.get_data()
    db.save_inhumat("T-1", data, inhumat_id=iid)
    return db.get_inhumat(iid)

def dates(row):
    return {{k: row.get(k) for k in DATES}}

# 1) creat din telefon cu toate datele lipsă -> open + Save fără modificări
a = db.save_inhumat("T-1", {{"nume": "Fara", "prenume": "Date"}})
out["all_empty"] = dates(reopen_and_save(a))

# 2) doar data nașterii -> edit + save (se schimbă doar numele)
b = db.save_inhumat("T-1", {{"nume": "Doar", "prenume": "Nastere", "data_nasterii": "01.02.1950"}})
dlg = aid.AddInhumatDialog(None, "T-1", existing=db.get_inhumat(b))
dlg.txt_prenume.setText("Nastere2")
dlg._save()
db.save_inhumat("T-1", dlg.get_data(), inhumat_id=b)
out["only_birth"] = dates(db.get_inhumat(b))

# 3) înhumat nou din MAIN, fără nicio dată completată: nu apare data de azi
dlg = aid.AddInhumatDialog(None, "T-1")
out["new_blank_flags"] = [getattr(dlg, "txt_" + k).is_blank() for k in DATES]
dlg.txt_nume.setText("Nou"); dlg.txt_prenume.setText("Main")
dlg._save()
c = db.save_inhumat("T-1", dlg.get_data())
out["new_empty"] = dates(db.get_inhumat(c))

# 4) data completată în dialog + apoi „Fără dată"
dlg = aid.AddInhumatDialog(None, "T-1")
dlg.txt_nume.setText("Cu"); dlg.txt_prenume.setText("Data")
from PySide6.QtCore import QDate
dlg.txt_data_deces.setDate(QDate(2024, 2, 1))
dlg.txt_data_inhumare.setDate(QDate(2024, 2, 4))
dlg.txt_data_inhumare.set_blank()
dlg._save()
d = db.save_inhumat("T-1", dlg.get_data())
out["set_then_cleared"] = dates(db.get_inhumat(d))

# 5) valoare nerecunoscută în DB: păstrată, nu ștearsă
e = db.save_inhumat("T-1", {{"nume": "Text", "prenume": "Liber", "data_nasterii": "circa 1940"}})
out["unparseable"] = dates(reopen_and_save(e))

# 6) persoană cu date reale: round-trip identic
f = db.save_inhumat("T-1", {{"nume": "Cu", "prenume": "Toate", "data_nasterii": "05.06.1940", "data_deces": "01.02.2024",
                          "data_inhumare": "04.02.2024", "data_deshumare": "10.10.2030"}})
out["real_dates"] = dates(reopen_and_save(f))

# 7) adeverință: nu tipărește date false pentru datele lipsă
import pdf_reports as pr
texts = []
class P(pr.Paragraph):
    def __init__(self, text, *a, **k):
        texts.append(str(text)); super().__init__(text, *a, **k)
pr.Paragraph = P
pdf = pr.render_burial_certificate_pdf(a)
out["pdf_ok"] = pdf[:5] == b"%PDF-"
out["pdf_text"] = " ".join(texts)
print("RESULT " + json.dumps(out))
'''


def _run(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_")}
    env.update(CIMITIR_DATA_DIR=str(tmp_path / "data"), CIMITIR_ENV_FILE=str(tmp_path / "nu.env"),
               CIMITIR_LOG_DIR=str(tmp_path / "logs"), CIMITIR_DB_ENGINE="sqlite", CIMITIR_MODE="local",
               DATABASE_URL="", QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-c", WORKER.format(desktop=str(DESKTOP))],
                          env=env, cwd=str(DESKTOP), capture_output=True, text=True, timeout=150)
    assert proc.returncode == 0, proc.stderr[-2500:]
    return json.loads([ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:])


EMPTY = {"data_nasterii": "", "data_deces": "", "data_inhumare": "", "data_deshumare": ""}


def test_empty_dates_are_never_invented(tmp_path):
    o = _run(tmp_path)
    assert o["all_empty"] == EMPTY, "open + Save fără modificări nu scrie 01.01.2000 / data de azi"
    assert o["only_birth"] == {**EMPTY, "data_nasterii": "01.02.1950"}
    assert o["new_blank_flags"] == [True] * 4
    assert o["new_empty"] == EMPTY, "înhumat nou: nicio dată inventată (nici deshumare, nici data de azi)"
    assert o["set_then_cleared"] == {**EMPTY, "data_deces": "01.02.2024"}
    assert o["unparseable"] == {**EMPTY, "data_nasterii": "circa 1940"}
    assert o["real_dates"] == {"data_nasterii": "05.06.1940", "data_deces": "01.02.2024",
                               "data_inhumare": "04.02.2024", "data_deshumare": "10.10.2030"}


def test_certificate_does_not_print_false_dates(tmp_path):
    o = _run(tmp_path)
    assert o["pdf_ok"]
    txt = o["pdf_text"]
    assert "născut(ă) la data de <b>—</b>" in txt and "decedat(ă) la data de <b>—</b>" in txt
    assert "înhumat(ă) la data de <b>—</b>" in txt
    assert "01.01.2000" not in txt and "01.01.1800" not in txt
