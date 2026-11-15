"""MAIN — regresie editor hartă: desenare → salvare reală (dialog) → selectare → Overview → ștergere → SQLite → alt poligon.

Rulează offscreen, în subproces, cu bază SQLite temporară (nicio dată reală, niciun Cloud). Dialogul „Atribuire loc de veci" e cel REAL
(completat programatic), nu un dublu de test.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]

PRELUDE = r'''
import json, os, sys
sys.path.insert(0, {desktop!r})
os.chdir({desktop!r})
from PySide6.QtWidgets import QApplication, QMessageBox, QDialog, QMenu
from PySide6.QtCore import Qt, QPointF, QTimer
from PySide6.QtTest import QTest
app = QApplication([])
import database as db
db.init_db()
import ui_mainwindow, map_editor_diag
win = ui_mainwindow.CemeteryUI()
win.resize(1500, 900); win.show(); app.processEvents()
mv = win.map_view
mv.resetTransform(); mv.scene().setSceneRect(0, 0, 2000, 2000)
vp = mv.viewport()
out = dict()
boxes = []

def vpt(x, y): return mv.mapFromScene(QPointF(x, y))
def click(x, y, btn=Qt.LeftButton):
    QTest.mouseClick(vp, btn, Qt.NoModifier, vpt(x, y)); app.processEvents()
def key(k):
    QTest.keyClick(mv, k); app.processEvents()
def _typed(t):
    return sorted(i.data(0)["cod_loc" if t != "sector_grafic" else "sector_id"] for i in mv.scene().items()
                  if isinstance(i.data(0), dict) and i.data(0).get("type") == t)
def scene_plots(): return _typed("loc_de_veci")
def scene_labels(): return _typed("cod_loc_text")
def scene_sectors(): return _typed("sector_grafic")
def db_plots():
    c = db.get_connection()
    try: return sorted(r[0] for r in c.execute("SELECT cod_loc FROM locuri_de_veci").fetchall())
    finally: c.close()
def db_sectors():
    c = db.get_connection()
    try: return sorted(r[0] for r in c.execute("SELECT id FROM sectoare").fetchall())
    finally: c.close()
def state():
    return dict(mode=win.modes.current_mode, points=len(win.current_points), drag=mv.dragMode().name,
                pan=win.act_pan.isChecked(), sel=win.act_select.isChecked(), draw=win.act_draw.isChecked(),
                dlg=QApplication.activeModalWidget() is not None)
def select_click(x, y):
    win.selected_cod_loc = None
    click(x, y)
    return win.selected_cod_loc

answers = dict(delete=QMessageBox.Yes, question=QMessageBox.No)
QMessageBox.question = staticmethod(lambda *a, **k: answers["question"])
QMessageBox.exec = lambda self: (boxes.append(("confirm", self.text())), answers["delete"])[1]
QMessageBox.warning = staticmethod(lambda *a, **k: (boxes.append(("warning", str(a[1]) + ": " + str(a[2]))), QMessageBox.Ok)[1])
QMessageBox.critical = staticmethod(lambda *a, **k: (boxes.append(("critical", str(a[2]))), QMessageBox.Ok)[1])

def dlg_save(rand, num):
    def _go():
        d = QApplication.activeModalWidget()
        d.txt_rand.setText(rand); d.txt_numar.setText(num); d.save()
    QTimer.singleShot(150, _go)

def dlg_cancel():
    QTimer.singleShot(150, lambda: QApplication.activeModalWidget().reject())

def draw(x0, y0, x1, y1, finish="enter"):
    win.set_tool_mode("draw")
    for px, py in [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]:
        click(px, py)
    if finish == "enter": key(Qt.Key_Return)
    elif finish == "snap": click(x0, y0)
    elif finish == "dbl": QTest.mouseDClick(vp, Qt.LeftButton, Qt.NoModifier, vpt(x0, y1)); app.processEvents()
    elif finish == "rclick": click(x0, y1, Qt.RightButton)
'''

FULL_FLOW = PRELUDE + r'''
out["initial"] = state()
for finish, rand, num, x0 in (("enter", "1", "1", 100), ("snap", "1", "2", 400), ("dbl", "1", "3", 700), ("rclick", "1", "4", 1000)):
    dlg_save(rand, num)
    draw(x0, 100, x0 + 200, 300, finish)
    out["after_" + finish] = state()
    out["selected_" + finish] = win.selected_cod_loc
out["scene"] = scene_plots(); out["labels"] = scene_labels(); out["db"] = db_plots()
rp = win.right_panel
out["overview"] = [rp.ov_cod_loc.text(), rp.ov_sector.text(), rp.ov_rand.text(), rp.ov_numar.text()]

out["select_A"] = select_click(200, 200); out["select_B"] = select_click(500, 200)
out["overview_B"] = rp.ov_cod_loc.text()
out["select_A_again"] = select_click(200, 200)
out["overview_A"] = rp.ov_cod_loc.text()

win.set_tool_mode("delete")
answers["delete"] = QMessageBox.No
click(200, 200)
out["delete_declined"] = [scene_plots(), db_plots(), [b[0] for b in boxes]]
answers["delete"] = QMessageBox.Yes
boxes.clear()
click(200, 200)
out["delete_confirmed"] = [scene_plots(), scene_labels(), db_plots(), [b[0] for b in boxes]]
out["mode_after_delete"] = win.modes.current_mode
out["overview_cleared"] = [rp.ov_cod_loc.text(), win.selected_cod_loc]

win.set_tool_mode("select")
dlg_save("2", "1")
draw(100, 500, 300, 700)
out["new_after_delete"] = [state(), scene_plots(), db_plots()]
out["select_new"] = select_click(200, 600)
out["select_other"] = select_click(500, 200)
print("RESULT " + json.dumps(out))
'''

CANCEL_AND_ERRORS = PRELUDE + r'''
# anularea dialogului: editorul rămâne utilizabil, nimic nu e salvat
dlg_cancel(); draw(100, 100, 300, 300)
out["cancel"] = [state(), scene_plots(), db_plots()]
win.set_tool_mode("draw"); click(1000, 1000); click(1100, 1000); key(Qt.Key_Escape)
out["esc"] = [state(), scene_plots(), db_plots()]
dlg_save("1", "1"); draw(100, 100, 300, 300)
out["ok_after_cancel"] = [state()["mode"], scene_plots(), db_plots(), select_click(200, 200)]

# drepturi insuficiente la creare: fără poligon fantomă, fără mesaj fals de succes
db.set_access_guard(lambda r: r != "add")
boxes.clear()
def _close_dlg_refused():
    d = QApplication.activeModalWidget()
    d.txt_rand.setText("5"); d.txt_numar.setText("5"); d.save()
    QTimer.singleShot(50, lambda: QApplication.activeModalWidget().reject())
QTimer.singleShot(150, _close_dlg_refused)
draw(400, 100, 600, 300)
out["add_refused"] = [state(), scene_plots(), scene_labels(), db_plots(), [b[0] for b in boxes], win.statusBar().currentMessage()]
db.set_access_guard(None)

# drepturi insuficiente la ștergere: poligonul rămâne în hartă și în SQLite
db.set_access_guard(lambda r: r != "delete")
boxes.clear(); win.set_tool_mode("delete"); click(200, 200)
out["delete_refused"] = [scene_plots(), scene_labels(), db_plots(), [b[0] for b in boxes], win.statusBar().currentMessage(), win.selected_cod_loc]
db.set_access_guard(None)

# loc cu istoric: ștergerea e blocată, nimic nu dispare
win.actions.save_inhumat("A-01-01", {{"nume": "Test", "prenume": "Fictiv"}})
boxes.clear(); click(200, 200)
out["history_blocked"] = [scene_plots(), db_plots(), [b[0] for b in boxes], boxes[0][1][:40] if boxes else ""]
win.set_tool_mode("select")
dlg_save("1", "2"); draw(400, 100, 600, 300)
out["draw_after_blocked"] = [scene_plots(), db_plots()]
print("RESULT " + json.dumps(out))
'''

SECTORS_AND_DIAG = PRELUDE + r'''
db.set_access_guard(lambda r: r != "add")
boxes.clear(); win.set_tool_mode("draw_sector")
for px, py in [(100, 100), (300, 100), (300, 300), (100, 300)]: click(px, py)
key(Qt.Key_Return)
out["sector_add_refused"] = [state()["mode"], scene_sectors(), db_sectors(), [b[0] for b in boxes]]
db.set_access_guard(None)
win.set_tool_mode("draw_sector")
for px, py in [(100, 100), (300, 100), (300, 300), (100, 300)]: click(px, py)
key(Qt.Key_Return)
out["sector_ok"] = [state()["mode"], scene_sectors(), db_sectors()]
db.set_access_guard(lambda r: r != "delete")
boxes.clear(); win.set_tool_mode("delete_sector"); answers["delete"] = QMessageBox.Yes; click(200, 200)
out["sector_delete_refused"] = [scene_sectors(), db_sectors(), [b[0] for b in boxes]]
db.set_access_guard(None)
click(200, 200)
out["sector_deleted"] = [scene_sectors(), db_sectors()]

# right-click pe loc cât timp NU există fereastră activă (cazul de după dialog): fără excepție, „Șterge" funcționează
dlg_save("1", "1"); draw(500, 100, 700, 300)
win.set_tool_mode("select")
out["active_window_is_none"] = QApplication.activeWindow() is None
def pick_delete():
    m = QApplication.activePopupWidget()
    act = [x for x in m.actions() if "Șterge" in x.text()][0]
    m.setActiveAction(act)
    QTest.keyClick(m, Qt.Key_Return)
QTimer.singleShot(200, pick_delete)
raised = []
sys_hook = sys.excepthook
sys.excepthook = lambda *a: raised.append(a[0].__name__)
click(600, 200, Qt.RightButton)
sys.excepthook = sys_hook
out["rightclick_no_active_window"] = [raised, scene_plots(), db_plots()]

# diagnosticare: o eroare în handler e raportată (jurnal + bară de stare), nu înghițită, iar editorul rămâne utilizabil
import logging
records = []
class H(logging.Handler):
    def emit(self, r): records.append(r.getMessage())
logging.getLogger("map_editor").addHandler(H())
win.set_tool_mode("draw")
orig = win.tools.handle_event
def boom(*a, **k): raise ValueError("date personale NU trebuie logate")
win.tools.handle_event = boom
map_editor_diag.reset_for_tests()
click(100, 800)
win.tools.handle_event = orig
out["diag"] = [len(records) >= 1, any("ValueError" in r for r in records), any("personale" in r for r in records),
               "Eroare internă" in win.statusBar().currentMessage()]
win.set_tool_mode("select")
out["after_diag_select"] = [state()["mode"], state()["drag"]]
win.set_tool_mode("nu_exista")
out["invalid_mode_kept"] = win.modes.current_mode

# focusul revine pe hartă după finalizare
calls = []
orig_focus = mv.setFocus
mv.setFocus = lambda *a, **k: (calls.append(1), orig_focus(*a, **k))[1]
dlg_save("3", "3"); draw(800, 500, 1000, 700)
app.processEvents(); QTest.qWait(20)
out["focus_restored"] = len(calls) >= 1
print("RESULT " + json.dumps(out))
'''


def _run(tmp_path: Path, code: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_")}
    env.update(CIMITIR_DATA_DIR=str(tmp_path / "data"), CIMITIR_ENV_FILE=str(tmp_path / "nu.env"),
               CIMITIR_LOG_DIR=str(tmp_path / "logs"), CIMITIR_DB_ENGINE="sqlite", CIMITIR_MODE="local",
               DATABASE_URL="", QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-c", code.format(desktop=str(DESKTOP))],
                          env=env, cwd=str(DESKTOP), capture_output=True, text=True, timeout=170)
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads([ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:])


def _interactive_select(st):
    return (st["mode"] == "select" and st["points"] == 0 and st["pan"] and st["sel"] and not st["draw"]
            and st["drag"] == "ScrollHandDrag" and not st["dlg"])


def test_draw_save_select_overview_delete_sqlite_draw_again(tmp_path):
    o = _run(tmp_path, FULL_FLOW)
    assert _interactive_select(o["initial"])
    for finish, cod in (("enter", "A-01-01"), ("snap", "A-01-02"), ("dbl", "A-01-03"), ("rclick", "A-01-04")):
        assert _interactive_select(o["after_" + finish]), (finish, o["after_" + finish])
        assert o["selected_" + finish] == cod
    assert o["scene"] == o["labels"] == o["db"] == ["A-01-01", "A-01-02", "A-01-03", "A-01-04"]
    assert o["overview"] == ["A-01-04", "A", "01", "04"]
    assert (o["select_A"], o["select_B"], o["select_A_again"]) == ("A-01-01", "A-01-02", "A-01-01")
    assert (o["overview_B"], o["overview_A"]) == ("A-01-02", "A-01-01")
    left = ["A-01-02", "A-01-03", "A-01-04"]
    assert o["delete_declined"][:2] == [["A-01-01"] + left, ["A-01-01"] + left]
    assert o["delete_confirmed"][:3] == [left, left, left] and "confirm" in o["delete_confirmed"][3]
    assert o["mode_after_delete"] == "delete"
    assert o["overview_cleared"] == ["", None]
    st, scene, dbp = o["new_after_delete"]
    assert _interactive_select(st) and scene == dbp == sorted(left + ["A-02-01"])
    assert (o["select_new"], o["select_other"]) == ("A-02-01", "A-01-02")


def test_cancel_errors_history_and_permissions(tmp_path):
    o = _run(tmp_path, CANCEL_AND_ERRORS)
    assert _interactive_select(o["cancel"][0]) and o["cancel"][1:] == [[], []]
    assert _interactive_select(o["esc"][0]) and o["esc"][1:] == [[], []]
    assert o["ok_after_cancel"] == ["select", ["A-01-01"], ["A-01-01"], "A-01-01"]
    st, scene, labels, dbp, boxes, status = o["add_refused"]
    assert _interactive_select(st) and scene == labels == dbp == ["A-01-01"], "niciun poligon fantomă"
    assert "warning" in boxes and "creat cu succes" not in status
    scene, labels, dbp, boxes, status, sel = o["delete_refused"]
    assert scene == labels == dbp == ["A-01-01"] and "warning" in boxes and "șters complet" not in status
    scene, dbp, boxes, text = o["history_blocked"]
    assert scene == dbp == ["A-01-01"] and boxes == ["warning"] and "istoric" in text
    assert o["draw_after_blocked"] == [["A-01-01", "A-01-02"], ["A-01-01", "A-01-02"]]


def test_sectors_rightclick_without_active_window_diagnostics_and_focus(tmp_path):
    o = _run(tmp_path, SECTORS_AND_DIAG)
    mode, scene, dbs, boxes = o["sector_add_refused"]
    assert mode == "select" and scene == [] and dbs == [] and "warning" in boxes
    assert o["sector_ok"][0] == "select" and len(o["sector_ok"][1]) == 1 and o["sector_ok"][1] == o["sector_ok"][2]
    assert o["sector_delete_refused"][0] == o["sector_delete_refused"][1] == o["sector_ok"][1]
    assert "warning" in o["sector_delete_refused"][2]
    assert o["sector_deleted"] == [[], []]
    assert o["active_window_is_none"] is True
    raised, scene, dbp = o["rightclick_no_active_window"]
    assert raised == [] and scene == [] and dbp == []
    assert o["diag"] == [True, True, False, True]
    assert o["after_diag_select"] == ["select", "ScrollHandDrag"]
    assert o["invalid_mode_kept"] == "select"
    assert o["focus_restored"] is True
