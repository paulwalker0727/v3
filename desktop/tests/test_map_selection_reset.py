"""MAIN: după finalizarea/anularea desenului, harta revine la „mână" (select); click = selectare, nu desenare. Offscreen, bază temporară."""
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
from PySide6.QtWidgets import QApplication, QMessageBox, QDialog
from PySide6.QtCore import Qt, QPointF
from PySide6.QtTest import QTest
app = QApplication([])
import database as db
db.init_db()
import assign_plot_code_window as apw

class FakeDlg:
    n = 0
    cancel = False
    def __init__(self, points, parent=None):
        FakeDlg.n += 1
        code = "T%d" % FakeDlg.n
        self.txt_cod = type("T", (), {{"text": lambda s, c=code: c}})()
    def exec(self):
        return QDialog.Rejected if FakeDlg.cancel else QDialog.Accepted

apw.AssignPlotCodeWindow = FakeDlg
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
import ui_mainwindow
win = ui_mainwindow.CemeteryUI()
win.resize(1500, 900); win.show(); app.processEvents()
mv = win.map_view
mv.resetTransform(); mv.scene().setSceneRect(0, 0, 2000, 2000)
vp = mv.viewport()
out = dict(log=[])

def vpt(x, y):
    return mv.mapFromScene(QPointF(x, y))

def click(x, y):
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, vpt(x, y)); app.processEvents()

def key(k):
    QTest.keyClick(mv, k); app.processEvents()

def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

def draw(x0, y0, x1, y1, finish="enter"):
    win.set_tool_mode("draw")
    for px, py in rect(x0, y0, x1, y1):
        click(px, py)
    if finish == "enter":
        key(Qt.Key_Return)
    elif finish == "snap":
        click(x0, y0)

def state():
    return dict(mode=win.modes.current_mode, points=len(win.current_points), draw=win.modes.is_draw_mode(),
                pan_checked=win.act_pan.isChecked(), sel_checked=win.act_select.isChecked(),
                draw_checked=win.act_draw.isChecked(),
                drag=int(mv.dragMode().value))

def select_click(x, y):
    win.selected_cod_loc = None
    n = len(win.current_points)
    click(x, y)
    return win.selected_cod_loc, len(win.current_points) - n

NO_DRAG = int(mv.dragMode().NoDrag.value)
HAND = int(mv.dragMode().ScrollHandDrag.value)

draw(100, 100, 300, 300); out["after_A"] = state()
draw(400, 100, 600, 300, "snap"); out["after_B"] = state()
a, b = "T1", "T2"
out["hand_select_A_before_second_draw"] = select_click(200, 200)

win.set_tool_mode("draw"); out["draw_on"] = state()
for px, py in rect(100, 500, 300, 700):
    click(px, py)
key(Qt.Key_Return)
out["after_C"] = state()
out["cycle1_A"] = select_click(200, 200)
out["cycle1_B"] = select_click(500, 200)

draw(400, 500, 600, 700)
out["after_D"] = state()
out["cycle2_A"] = select_click(200, 200)
out["cycle2_B"] = select_click(500, 200)

win.set_tool_mode("draw"); click(1000, 1000); click(1100, 1000); click(1100, 1100)
key(Qt.Key_Escape); out["after_esc"] = state()
out["esc_B"] = select_click(500, 200)
out["esc_empty_click"] = select_click(1500, 1500)

win.set_tool_mode("draw"); click(1000, 1000); click(1100, 1000)
win.act_pan.trigger(); app.processEvents(); out["after_hand"] = state()
out["hand_A"] = select_click(200, 200)
out["hand_empty_click"] = select_click(1500, 1500)

win.set_tool_mode("draw"); click(1000, 1000); click(1100, 1000); click(1100, 1100)
FakeDlg.cancel = True
key(Qt.Key_Return); FakeDlg.cancel = False
out["after_dialog_cancel"] = state()
out["cancel_B"] = select_click(500, 200)

win.set_tool_mode("draw_sector")
for px, py in rect(1200, 1200, 1400, 1400):
    click(px, py)
key(Qt.Key_Return); out["after_sector"] = state()
out["sector_then_A"] = select_click(200, 200)

out["no_drag"] = NO_DRAG; out["hand"] = HAND
out["n_plots"] = sum(1 for i in mv.scene().items() if isinstance(i.data(0), dict) and i.data(0).get("type") == "loc_de_veci")
print("RESULT " + json.dumps(out))
'''


def _run(tmp_path: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_")}
    env.update(CIMITIR_DATA_DIR=str(tmp_path / "data"), CIMITIR_ENV_FILE=str(tmp_path / "nu.env"),
               CIMITIR_LOG_DIR=str(tmp_path / "logs"), CIMITIR_DB_ENGINE="sqlite", CIMITIR_MODE="local",
               DATABASE_URL="", QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-c", WORKER.format(desktop=str(DESKTOP))],
                          env=env, cwd=str(DESKTOP), capture_output=True, text=True, timeout=150)
    assert proc.returncode == 0, proc.stderr[-2500:]
    return json.loads([ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:])


def _is_select(st):
    return (st["mode"] == "select" and st["points"] == 0 and not st["draw"]
            and st["pan_checked"] and st["sel_checked"] and not st["draw_checked"])


def test_hand_returns_to_select_after_every_draw_exit(tmp_path):
    o = _run(tmp_path)
    for k in ("after_A", "after_B", "after_C", "after_D", "after_esc", "after_hand",
              "after_dialog_cancel", "after_sector"):
        assert _is_select(o[k]), (k, o[k])
        assert o[k]["drag"] == o["hand"], (k, "drag mode")
    assert o["draw_on"]["mode"] == "draw" and o["draw_on"]["drag"] == o["no_drag"]

    for k, cod in (("hand_select_A_before_second_draw", "T1"), ("cycle1_A", "T1"), ("cycle1_B", "T2"),
                   ("cycle2_A", "T1"), ("cycle2_B", "T2"), ("esc_B", "T2"), ("hand_A", "T1"),
                   ("cancel_B", "T2"), ("sector_then_A", "T1")):
        assert o[k] == [cod, 0], (k, o[k])
    for k in ("esc_empty_click", "hand_empty_click"):
        assert o[k] == [None, 0], (k, o[k])
    assert o["n_plots"] == 4, "A, B, C, D create; niciun loc accidental"
