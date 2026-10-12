"""MAIN: fluxuri existente ale uneltelor de hartă (rând, ghidaje, calibrare, right-click) rămân; ESC/✋ le opresc curat. Offscreen."""
from test_map_selection_reset import WORKER, _run

PRELUDE = WORKER.split("NO_DRAG =")[0]

BODY = r'''
from PySide6.QtWidgets import QInputDialog
QInputDialog.getText = staticmethod(lambda *a, **k: ("A", True))
QInputDialog.getDouble = staticmethod(lambda *a, **k: (2.0, True))

def sel(x, y):
    win.selected_cod_loc = None
    click(x, y)
    return win.selected_cod_loc

def mode():
    return win.modes.current_mode

draw(100, 100, 300, 300)
out["status_after_finalize"] = win.statusBar().currentMessage()
out["a_select"] = sel(200, 200)

# draw_row: mod repetitiv intenționat — rămâne draw_row după finalizare, ✋ iese
win.adv.start_draw_row(); click(100, 800); click(300, 820); key(Qt.Key_Return)
out["row_mode_after_finish"] = mode()
click(100, 900); click(300, 920); key(Qt.Key_Return)
out["row_mode_second"] = mode()
out["row_status"] = win.statusBar().currentMessage()
win.act_pan.trigger(); out["row_hand"] = [mode(), sel(200, 200)]

# right-click cu prea puține puncte: rămâne în desenare, puncte șterse, mesaj
win.set_tool_mode("draw"); click(1000, 1000); click(1100, 1000)
QTest.mouseClick(vp, Qt.RightButton, Qt.NoModifier, vpt(1100, 1100)); app.processEvents()
out["rc"] = [mode(), len(win.current_points), win.statusBar().currentMessage()]
win.act_pan.trigger()

# indicator pentru unelte auxiliare
ind = dict()
for m in ("draw_guide_h", "draw_guide_v", "draw_guide_line", "ruler", "calibrate", "draw_row"):
    win.set_tool_mode(m)
    ind[m] = [vp.cursor().shape() == Qt.CrossCursor or mv.cursor().shape() == Qt.CrossCursor]
out["cross_cursor"] = ind
win.set_tool_mode("draw_guide_line"); out["guide_status"] = win.statusBar().currentMessage()

# ESC iese din uneltele auxiliare și șterge punctele temporare
res = dict()
for m in ("draw_guide_h", "draw_guide_line", "ruler", "calibrate"):
    win.set_tool_mode(m); click(100, 100)
    win.adv._calib_pts = [(1.0, 1.0)]
    key(Qt.Key_Escape)
    res[m] = [mode(), list(win.adv._calib_pts), sel(200, 200)]
out["esc_aux"] = res

# ✋ curăță punctul de calibrare rămas
win.adv.start_calibrate(); click(100, 100); win.act_pan.trigger()
out["calib_after_hand"] = [mode(), list(win.adv._calib_pts)]
# ESC în delete rămâne neschimbat (nu e unealtă de desen)
win.set_tool_mode("delete"); key(Qt.Key_Escape); out["esc_delete"] = mode()
print("RESULT " + json.dumps(out))
'''


def test_existing_tool_flows_and_aux_tool_exit(tmp_path):
    o = _run(tmp_path, PRELUDE + BODY)
    assert "creat" in o["status_after_finalize"] or "anulat" in o["status_after_finalize"], o["status_after_finalize"]
    assert o["a_select"] == "T1"
    assert o["row_mode_after_finish"] == "draw_row" and o["row_mode_second"] == "draw_row"
    assert "Rând 2" in o["row_status"]
    assert o["row_hand"] == ["select", "T1"]
    assert o["rc"][0] == "draw" and o["rc"][1] == 0 and "anulat" in o["rc"][2]
    assert all(v[0] for v in o["cross_cursor"].values()), o["cross_cursor"]
    assert "Ghidaj" in o["guide_status"]
    for m, v in o["esc_aux"].items():
        assert v == ["select", [], "T1"], (m, v)
    assert o["calib_after_hand"] == ["select", []]
    assert o["esc_delete"] == "delete"
