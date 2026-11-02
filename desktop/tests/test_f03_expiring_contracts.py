"""F-03 — `expiring_contracts()` pe baza SQLite reală (date sintetice, folder izolat): formate DD.MM.YYYY și YYYY-MM-DD, limite, date invalide.
Ziua curentă este fixată la 2026-03-10 (înlocuind `date` din modulul bazei) ca limitele să fie deterministe."""
import json
import os
import subprocess
import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]
SHARED = DESKTOP.parent / "shared"

PRE = r'''
import datetime, json, os, sys
sys.path.insert(0, {desktop!r}); sys.path.insert(0, {shared!r})
os.chdir({desktop!r})
import database as db
db.init_db()
class FakeDate(datetime.date):
    @classmethod
    def today(cls):
        return cls(2026, 3, 10)
db.date = FakeDate
db.save_plot_to_db("T-1", [(0, 0), (40, 0), (40, 30), (0, 30)], "#111")
cid = db.save_concesionar({{"nume": "Test", "prenume": "Sintetic"}})
def mk(nr, end):
    return db.save_contract({{"numar_contract": nr, "data_contract": "01.01.2020", "data_inceput": "01.01.2020", "data_sfarsit": end, "cod_loc": "T-1",
                              "concesionar_id": cid, "observatii": "", "extra_data": None, "owner_fullname": "Test Sintetic"}})
def raw():
    c = db.get_connection()
    try:
        return {{r["numar_contract"]: r["data_sfarsit"] for r in c.execute("SELECT numar_contract, data_sfarsit FROM contracte").fetchall()}}
    finally:
        c.close()
def nrs(days):
    return [c["numar_contract"] for c in db.expiring_contracts(days)]
out = {{}}
'''
POST = "\nprint('RESULT ' + json.dumps(out, default=str))\n"


def run(tmp_path, body):
    code = PRE.format(desktop=str(DESKTOP), shared=str(SHARED)) + body + POST
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_")}
    env.update(CIMITIR_DATA_DIR=str(tmp_path / "data"), CIMITIR_ENV_FILE=str(tmp_path / "nu.env"), CIMITIR_LOG_DIR=str(tmp_path / "logs"),
               CIMITIR_DB_ENGINE="sqlite", CIMITIR_MODE="local", DATABASE_URL="", QT_QPA_PLATFORM="offscreen")
    p = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(DESKTOP), capture_output=True, text=True, timeout=150)
    assert p.returncode == 0, p.stderr[-3000:]
    return json.loads([ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:])


def test_main_format_dd_mm_yyyy_is_detected_exactly_like_iso(tmp_path):
    o = run(tmp_path, r'''
mk("RO-EXPIRAT", "01.03.2026"); mk("ISO-EXPIRAT", "2026-03-01")
mk("RO-AZI", "10.03.2026");     mk("ISO-AZI", "2026-03-10")
mk("RO-IN-5", "15.03.2026");    mk("ISO-IN-5", "2026-03-15")
mk("RO-LIMITA", "09.04.2026");  mk("ISO-LIMITA", "2026-04-09")        # 10.03 + 30 zile = 09.04: inclus
mk("RO-DUPA", "10.04.2026");    mk("ISO-DUPA", "2026-04-10")          # a 31-a zi: exclus
mk("RO-VIITOR", "10.03.2036");  mk("ISO-VIITOR", "2036-03-10")
out["d0"] = nrs(0); out["d30"] = nrs(30); out["d5"] = nrs(5); out["d4"] = nrs(4); out["d4000"] = nrs(4000)
''')
    assert sorted(o["d0"]) == ["ISO-AZI", "ISO-EXPIRAT", "RO-AZI", "RO-EXPIRAT"], "expirate + cele care expiră azi, ambele formate"
    assert sorted(o["d5"]) == sorted(o["d0"] + ["RO-IN-5", "ISO-IN-5"])
    assert sorted(o["d4"]) == sorted(o["d0"])
    assert sorted(o["d30"]) == sorted(o["d5"] + ["RO-LIMITA", "ISO-LIMITA"])
    assert "RO-DUPA" not in o["d30"] and "ISO-DUPA" not in o["d30"]
    assert {"RO-DUPA", "ISO-DUPA", "RO-VIITOR", "ISO-VIITOR"} <= set(o["d4000"])


def test_results_are_ordered_by_real_date_not_by_text_and_expose_iso_day(tmp_path):
    o = run(tmp_path, r'''
mk("C", "02.03.2026"); mk("A", "2025-12-31"); mk("B", "01.01.2026"); mk("D", "2026-03-05"); mk("E", "05.03.2026")
rows = db.expiring_contracts(30)
out["order"] = [c["numar_contract"] for c in rows]
out["expira"] = {c["numar_contract"]: [c["_expira"], c["_expira_raw"], c["_date_col"]] for c in rows}
''')
    assert o["order"][:3] == ["A", "B", "C"] and set(o["order"][3:]) == {"D", "E"}, o["order"]
    assert o["expira"]["C"] == ["2026-03-02", "02.03.2026", "data_sfarsit"]
    assert o["expira"]["D"] == ["2026-03-05", "2026-03-05", "data_sfarsit"]


def test_invalid_blank_and_unreadable_dates_are_skipped_not_guessed_and_never_rewritten(tmp_path):
    o = run(tmp_path, r'''
mk("BAD-FEB", "31.02.2026"); mk("BAD-TEXT", "nu e data"); mk("BAD-YEAR", "05.03.26"); mk("BAD-MONTH", "2026-13-01"); mk("BAD-ZERO", "00.00.0000")
mk("BLANK", ""); mk("SPACES", "   "); mk("OK-RO", "01.03.2026"); mk("OK-LEAP", "29.02.2024"); mk("BAD-LEAP", "29.02.2025")
before = raw()
out["d0"] = nrs(0); out["d9999"] = nrs(9999)
out["unchanged"] = raw() == before
out["after"] = raw()
''')
    assert sorted(o["d0"]) == ["OK-LEAP", "OK-RO"]
    assert sorted(o["d9999"]) == ["OK-LEAP", "OK-RO"], "nicio dată invalidă nu este ghicită și nu apare ca expirată/viitoare"
    assert o["unchanged"] is True, "datele istorice nu se modifică pentru a face interogarea să funcționeze"
    assert o["after"]["BAD-FEB"] == "31.02.2026" and o["after"]["BAD-TEXT"] == "nu e data" and o["after"]["BLANK"] in ("", None)


def test_iso_with_time_part_and_whitespace_are_understood(tmp_path):
    o = run(tmp_path, r'''
mk("T1", "2026-03-01 08:30:00"); mk("T2", "2026-03-01T08:30:00"); mk("W", " 01.03.2026 "); mk("F", "2026-03-11T00:00:00")
out["d0"] = nrs(0); out["d1"] = nrs(1)
''')
    assert sorted(o["d0"]) == ["T1", "T2", "W"]
    assert sorted(o["d1"]) == ["F", "T1", "T2", "W"]


def test_month_and_year_boundaries(tmp_path):
    o = run(tmp_path, r'''
db.date = type("FD2", (datetime.date,), {"today": classmethod(lambda c: c(2026, 12, 25))})
mk("NOU-AN", "01.01.2027"); mk("NOU-AN-ISO", "2027-01-01"); mk("CRAPA", "26.01.2027"); mk("OK", "25.01.2027"); mk("ULTIMA", "31.12.2026")
out["d7"] = nrs(7); out["d31"] = nrs(31)
''')
    assert sorted(o["d7"]) == ["NOU-AN", "NOU-AN-ISO", "ULTIMA"]
    assert sorted(o["d31"]) == ["NOU-AN", "NOU-AN-ISO", "OK", "ULTIMA"]       # 25.12 + 31 zile = 25.01 inclus, 26.01 exclus


def test_alerts_window_marks_main_format_contracts_as_expired_and_soon(tmp_path):
    o = run(tmp_path, r'''
import datetime as _dt
from PySide6.QtWidgets import QApplication
app = QApplication([])
t = _dt.date.today()
fmt = lambda d: d.strftime("%d.%m.%Y")
mk("EXP-RO", fmt(t - _dt.timedelta(days=3))); mk("EXP-ISO", (t - _dt.timedelta(days=3)).isoformat())
mk("SOON-RO", fmt(t + _dt.timedelta(days=4))); mk("LATER-RO", fmt(t + _dt.timedelta(days=20)))
db.date = _dt.date
import alerts_window
w = alerts_window.AlertsWindow()
rows = {}
for i in range(w.table.rowCount()):
    rows[w.table.item(i, 2).text()] = w.table.item(i, 4).text()
out["rows"] = rows
out["label"] = w.lbl_count.text()
''')
    assert o["rows"] == {"EXP-RO": "EXPIRAT", "EXP-ISO": "EXPIRAT", "SOON-RO": "expiră curând", "LATER-RO": "activ"}, o
    assert "2 deja expirate" in o["label"]


def test_expired_pdf_report_and_map_coloring_see_main_format_contracts(tmp_path):
    o = run(tmp_path, r'''
import datetime as _dt
t = _dt.date.today()
mk("EXP-RO", (t - _dt.timedelta(days=3)).strftime("%d.%m.%Y"))
db.date = _dt.date
out["expired_cods"] = sorted({c["cod_loc"] for c in db.expiring_contracts(0)})
import pdf_reports as pr
texts = []
class P(pr.Paragraph):
    def __init__(self, text, *a, **k):
        texts.append(str(text)); super().__init__(text, *a, **k)
pr.Paragraph = P
pdf = pr.render_expiring_contracts_pdf(days=30)
out["pdf_ok"] = pdf[:5] == b"%PDF-"
out["pdf_text"] = " ".join(texts)
''')
    assert o["expired_cods"] == ["T-1"]
    assert o["pdf_ok"] and "EXPIRAT (3 zile)" in o["pdf_text"], o["pdf_text"][-600:]
