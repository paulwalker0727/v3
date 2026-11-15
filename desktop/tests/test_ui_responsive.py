"""UI adaptiv: ecrane compacte (1280×800 logic), panou Overview ascuns/restaurat, splitter, zoom/pan trackpad, ferestre care încap pe ecran.

Offscreen, baze SQLite temporare. Simulează macOS prin monkeypatch pe `screen_utils`/clasa hărții (nu rulează pe un Mac real:
verificarea vizuală pe MacBook rămâne manuală — vezi desktop/docs/MACOS_UI_RESPONSIVENESS.md).
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

DESKTOP = Path(__file__).resolve().parents[1]
if str(DESKTOP) not in sys.path:
    sys.path.insert(0, str(DESKTOP))

COMPACT = (1280, 775)  # 1280×800 logic minus bara de meniu macOS (~25px)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def compact_screen(monkeypatch):
    from PySide6.QtCore import QRect
    import screen_utils
    monkeypatch.setattr(screen_utils, "available_rect", lambda widget=None: QRect(0, 0, *COMPACT))
    return QRect(0, 0, *COMPACT)


# ---------------------------------------------------------------- screen_utils
def test_main_min_size_windows_is_unchanged_and_mac_is_adaptive(qapp, compact_screen):
    import screen_utils as su
    assert (su.main_window_min_size(platform="win32").width(), su.main_window_min_size(platform="win32").height()) == (1500, 850)
    assert (su.main_window_min_size(platform="linux").width(), su.main_window_min_size(platform="linux").height()) == (1500, 850)
    m = su.main_window_min_size(platform="darwin")
    assert (m.width(), m.height()) == (1000, 640)


def test_mac_min_never_exceeds_the_available_screen(qapp, monkeypatch):
    from PySide6.QtCore import QRect
    import screen_utils as su
    monkeypatch.setattr(su, "available_rect", lambda widget=None: QRect(0, 0, 900, 560))
    m = su.main_window_min_size(platform="darwin")
    assert m.width() <= 900 and m.height() <= 560


def test_initial_size_only_on_mac_and_within_screen(qapp, compact_screen):
    import screen_utils as su
    assert su.main_window_initial_size(platform="win32") is None
    s = su.main_window_initial_size(platform="darwin")
    assert s.width() <= COMPACT[0] and s.height() <= COMPACT[1]
    assert s.width() >= 1000 and s.height() >= 640


def test_fit_window_clamps_min_size_size_and_position(qapp, compact_screen):
    from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout
    import screen_utils as su
    d = QDialog()
    QVBoxLayout(d).addWidget(QLabel("x"))
    d.setMinimumSize(1500, 900)
    d.resize(1600, 1000)
    d.move(900, 500)
    d.show()
    qapp.processEvents()
    assert su.fit_window_to_screen(d)
    assert d.minimumWidth() <= COMPACT[0] and d.minimumHeight() <= COMPACT[1]
    assert d.width() <= COMPACT[0] and d.height() <= COMPACT[1]
    fg = d.frameGeometry()
    assert fg.left() >= 0 and fg.top() >= 0 and fg.right() <= COMPACT[0] and fg.bottom() <= COMPACT[1]
    d.close()


def test_fit_window_leaves_windows_that_already_fit_untouched(qapp, compact_screen):
    from PySide6.QtWidgets import QDialog
    import screen_utils as su
    d = QDialog()
    d.setMinimumSize(500, 400)
    d.resize(640, 480)
    d.move(100, 100)
    d.show()
    assert not su.fit_window_to_screen(d)
    assert (d.width(), d.height()) == (640, 480)
    d.close()


def test_screen_guard_is_installed_only_on_macos_unless_forced(qapp, monkeypatch):
    import screen_utils as su
    monkeypatch.setattr(su, "_GUARD", None)
    assert su.install_screen_guard(qapp, platform="win32") is None
    g = su.install_screen_guard(qapp, force=True)
    assert g is not None
    assert su.install_screen_guard(qapp, force=True) is g
    g.deleteLater()
    monkeypatch.setattr(su, "_GUARD", None)


def test_screen_guard_fits_dialogs_when_shown_and_when_screen_changes(qapp, compact_screen, monkeypatch):
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QDialog
    import screen_utils as su
    monkeypatch.setattr(su, "_GUARD", None)
    guard = su.install_screen_guard(qapp, force=True)
    try:
        d = QDialog()
        d.setMinimumSize(1400, 900)
        d.resize(1400, 900)
        d.show()
        qapp.processEvents()
        assert d.width() <= COMPACT[0] and d.height() <= COMPACT[1]
        monkeypatch.setattr(su, "available_rect", lambda widget=None: QRect(0, 0, 1000, 640))
        guard.refit_all()
        assert d.width() <= 1000 and d.height() <= 640
        d.close()
    finally:
        guard.deleteLater()
        monkeypatch.setattr(su, "_GUARD", None)


def test_long_unwrapped_labels_stop_forcing_a_huge_minimum_width(qapp):
    from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout
    import screen_utils as su
    d = QDialog()
    QVBoxLayout(d).addWidget(QLabel("Text foarte lung fără word-wrap care ar impune o lățime minimă enormă ferestrei. " * 3))
    before = d.minimumSizeHint().width()
    assert su.wrap_long_labels(d) == 1
    assert d.minimumSizeHint().width() < before and d.minimumSizeHint().width() < 600


# ---------------------------------------------------------------- FlowLayout
def test_flow_layout_wraps_on_narrow_width_and_keeps_one_row_when_wide(qapp):
    from PySide6.QtWidgets import QPushButton, QWidget
    from flow_layout import FlowLayout
    w = QWidget()
    lay = FlowLayout(w)
    for t in ("Fișa concesionarului", "Concesionari asociați / alege", "Contract nou", "Istoric contracte"):
        lay.addWidget(QPushButton(t))
    one_row = lay.heightForWidth(2000)
    narrow = lay.heightForWidth(300)
    assert narrow > one_row
    assert lay.minimumSize().width() < 300


# ---------------------------------------------------------------- OverviewSplitter
def _splitter(qapp, width=780, panel_w=300):
    from PySide6.QtWidgets import QLabel, QWidget, QVBoxLayout
    from overview_splitter import OverviewSplitter
    m, p = QLabel("map"), QWidget()
    QVBoxLayout(p).addWidget(QLabel("panel"))
    sp = OverviewSplitter(m, p, panel_width=panel_w)
    sp.resize(width, 600)
    sp.setSizes([width - panel_w, panel_w])
    sp.show()
    qapp.processEvents()
    return sp, m, p


def test_overview_splitter_hide_restore_keeps_width_and_emits(qapp):
    sp, m, p = _splitter(qapp)
    events = []
    sp.panel_toggled.connect(events.append)
    w0 = p.width()
    sp.hide_panel()
    qapp.processEvents()
    assert not sp.panel_open() and p.isHidden() and m.width() >= sp.width() - 8
    sp.show_panel()
    qapp.processEvents()
    assert sp.panel_open() and not p.isHidden() and abs(p.width() - w0) <= 8
    assert events == [False, True]
    sp.close()


def test_overview_splitter_drag_collapse_then_toggle_restores(qapp):
    sp, m, p = _splitter(qapp)
    sp.setSizes([sp.width(), 0])
    sp.splitterMoved.emit(sp.width(), 1)
    assert not sp.panel_open()
    sp.toggle_panel()
    qapp.processEvents()
    assert sp.panel_open() and p.width() >= sp.panel_min_width()
    sp.close()


def test_double_click_on_the_handle_toggles_the_panel(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    sp, m, p = _splitter(qapp)
    handle = sp.handle(1)
    QTest.mouseDClick(handle, Qt.LeftButton)
    qapp.processEvents()
    assert not sp.panel_open()
    sp.show_panel()
    sp.close()


def test_overview_splitter_panel_and_map_stay_inside_limits(qapp):
    from overview_splitter import MIN_MAP_WIDTH
    sp, m, p = _splitter(qapp, width=700, panel_w=300)
    sp.set_panel_width(5000)
    qapp.processEvents()
    assert m.width() >= MIN_MAP_WIDTH - 2 and m.width() + p.width() + sp.handleWidth() <= sp.width() + 2
    sp.set_panel_width(1)
    qapp.processEvents()
    assert p.width() >= sp.panel_min_width()
    sp.close()


# ---------------------------------------------------------------- CemeteryMapView
def _map(qapp, vp=(800, 600), img=(4000, 3000)):
    from PySide6.QtGui import QPixmap
    from map_view import CemeteryMapView
    v = CemeteryMapView()
    v.resize(*vp)
    v.show()
    qapp.processEvents()
    pix = QPixmap(*img)
    pix.fill()
    from PySide6.QtWidgets import QGraphicsPixmapItem
    from PySide6.QtCore import QRectF
    it = QGraphicsPixmapItem(pix)
    v.scene().addItem(it)
    v.scene().setSceneRect(QRectF(pix.rect()))
    v._map_item = it
    return v


def _wheel(view, dx=0, dy=0, pixel=True, mods=None, phase=None):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    pos = QPointF(view.viewport().rect().center())
    return QWheelEvent(pos, view.viewport().mapToGlobal(pos), QPoint(dx, dy) if pixel else QPoint(0, 0), QPoint(dx, dy * 4 if pixel else dy),
                       Qt.NoButton, mods or Qt.NoModifier, phase if phase is not None else (Qt.ScrollUpdate if pixel else Qt.NoScrollPhase), False)


def test_fit_fills_a_compact_viewport_with_the_whole_map(qapp):
    v = _map(qapp, vp=(1000, 600))
    v.fit_to_content()
    r = v.mapFromScene(v.sceneRect()).boundingRect()
    vp = v.viewport().rect()
    assert vp.contains(r) or (r.left() >= -1 and r.top() >= -1 and r.right() <= vp.right() + 1 and r.bottom() <= vp.bottom() + 1)
    assert r.width() >= vp.width() * 0.9 or r.height() >= vp.height() * 0.9
    v.close()


def test_refit_follows_a_bigger_viewport_only_while_fitted(qapp):
    v = _map(qapp, vp=(600, 400))
    v.fit_to_content()
    s_small = v.transform().m11()
    v.resize(1200, 800)
    qapp.processEvents()
    v.refit_if_fitted()
    assert v.transform().m11() > s_small * 1.5
    v._zoom_at(1.5, v.viewport().rect().center())
    s_zoomed = v.transform().m11()
    v.resize(900, 700)
    qapp.processEvents()
    v.refit_if_fitted()
    assert v.transform().m11() == pytest.approx(s_zoomed)
    v.fit_to_content()
    assert v.transform().m11() != pytest.approx(s_zoomed)
    v.close()


def test_auto_refit_on_resize_is_mac_only(qapp, monkeypatch):
    from map_view import CemeteryMapView
    v = _map(qapp, vp=(600, 400))
    v.fit_to_content()
    s0 = v.transform().m11()
    monkeypatch.setattr(CemeteryMapView, "AUTO_REFIT_ON_RESIZE", False)
    v.resize(1200, 800)
    qapp.processEvents(); qapp.processEvents()
    assert v.transform().m11() == pytest.approx(s0)
    monkeypatch.setattr(CemeteryMapView, "AUTO_REFIT_ON_RESIZE", True)
    v.resize(1100, 700)
    for _ in range(3):
        qapp.processEvents()
        from PySide6.QtTest import QTest
        QTest.qWait(10)
    assert v.transform().m11() > s0 * 1.4
    v.close()


def test_trackpad_two_finger_scroll_pans_and_wheel_zooms_on_mac(qapp, monkeypatch):
    from PySide6.QtCore import Qt
    from map_view import CemeteryMapView
    monkeypatch.setattr(CemeteryMapView, "TRACKPAD_SCROLL_PANS", True)
    v = _map(qapp)
    v._zoom_at(3.0, v.viewport().rect().center())
    qapp.processEvents()
    s0 = v.transform().m11()
    h0, v0 = v.horizontalScrollBar().value(), v.verticalScrollBar().value()
    v.wheelEvent(_wheel(v, dx=-40, dy=-60))
    assert v.transform().m11() == pytest.approx(s0)
    assert (v.horizontalScrollBar().value(), v.verticalScrollBar().value()) != (h0, v0)
    v.wheelEvent(_wheel(v, dy=120, pixel=False))           # rotiță de mouse clasică (fără pixelDelta/fază) = zoom, ca înainte
    assert v.transform().m11() > s0
    v.close()


def test_cmd_scroll_and_pinch_zoom_on_mac(qapp, monkeypatch):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QNativeGestureEvent, QPointingDevice
    from map_view import CemeteryMapView
    monkeypatch.setattr(CemeteryMapView, "TRACKPAD_SCROLL_PANS", True)
    v = _map(qapp)
    s0 = v.transform().m11()
    v.wheelEvent(_wheel(v, dy=60, mods=Qt.ControlModifier))
    s1 = v.transform().m11()
    assert s1 > s0
    pos = QPointF(v.viewport().rect().center())
    ev = QNativeGestureEvent(Qt.ZoomNativeGesture, QPointingDevice.primaryPointingDevice(), 2, pos, pos, pos, 0.25, QPointF())
    assert v.viewportEvent(ev)
    assert v.transform().m11() == pytest.approx(s1 * 1.25)
    v.close()


def test_windows_wheel_behaviour_is_unchanged(qapp, monkeypatch):
    from map_view import CemeteryMapView
    monkeypatch.setattr(CemeteryMapView, "TRACKPAD_SCROLL_PANS", False)
    v = _map(qapp)
    s0 = v.transform().m11()
    v.wheelEvent(_wheel(v, dy=120, pixel=False))
    assert v.transform().m11() == pytest.approx(s0 * v._zoom_factor)
    v.wheelEvent(_wheel(v, dy=-120, pixel=False))
    assert v.transform().m11() == pytest.approx(s0)
    v.wheelEvent(_wheel(v, dy=30))                              # chiar și cu pixelDelta: tot zoom pe Windows
    assert v.transform().m11() > s0
    v.close()


def test_cmd_ctrl_zero_fits_and_plus_minus_zoom(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    v = _map(qapp)
    v._zoom_at(4.0, v.viewport().rect().center())
    z = v.transform().m11()
    QTest.keyClick(v, Qt.Key_Minus, Qt.ControlModifier)
    assert v.transform().m11() < z
    QTest.keyClick(v, Qt.Key_0, Qt.ControlModifier)
    assert v._fitted
    v.close()


def test_zoom_limits_are_respected(qapp):
    v = _map(qapp)
    for _ in range(80):
        v._zoom_at(1.5, v.viewport().rect().center())
    assert v.transform().m11() <= v._max_scale * 1.5 + 1e-6
    for _ in range(200):
        v._zoom_at(1 / 1.5, v.viewport().rect().center())
    assert v.transform().m11() >= v._min_scale / 1.5 - 1e-9
    v.close()


# ---------------------------------------------------------------- HiDPI (fără dublă scalare)
def test_no_manual_scale_factors_anywhere_in_the_desktop_code():
    bad = re.compile(r"QT_SCALE_FACTOR|QT_AUTO_SCREEN_SCALE_FACTOR|AA_EnableHighDpiScaling|AA_DisableHighDpiScaling|"
                     r"AA_Use96Dpi|QT_FONT_DPI|setHighDpiScaleFactorRoundingPolicy|devicePixelRatio\(\)\s*\*")
    offenders = []
    for p in DESKTOP.rglob("*.py"):
        if "tests" in p.parts or "tools" in p.parts:
            continue
        if bad.search(p.read_text(encoding="utf-8", errors="ignore")):
            offenders.append(str(p.relative_to(DESKTOP)))
    assert offenders == []


# ---------------------------------------------------------------- ferestre reale (subproces, bază SQLite fictivă)
WORKER = r'''
import json, os, sys
sys.path.insert(0, {desktop!r})
os.chdir({desktop!r})
from PySide6.QtCore import QRect, QPointF, Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
app = QApplication([])
import database as db
db.init_db()
import screen_utils as su
MAC = {mac!r}
COMPACT = {compact!r}
su.available_rect = lambda widget=None: QRect(0, 0, COMPACT[0], COMPACT[1])
if MAC:
    su.is_macos = lambda platform=None: True
    su.install_screen_guard(app, force=True)
import ui_mainwindow
out = dict()
win = ui_mainwindow.CemeteryUI()
win.show(); app.processEvents()
out["min"] = [win.minimumWidth(), win.minimumHeight()]
out["size"] = [win.width(), win.height()]
out["panel_min_w"] = win.right_panel.minimumSizeHint().width()
out["hint_w"] = win.minimumSizeHint().width()
out["handle_w"] = win.splitter.handleWidth()
sizes = win.splitter.sizes(); out["sizes"] = sizes

w, h = (1000, 640) if MAC else (win.minimumWidth(), win.minimumHeight())
win.resize(w, h); app.processEvents()
out["after_min_resize"] = [win.width(), win.height()]
mv = win.map_view
pix = QPixmap(5000, 3500); pix.fill()
p = {map_png!r}
pix.save(p)
mv.load_map_file(p)
for _ in range(8):
    app.processEvents(); QTest.qWait(30)
mv.fit_to_content(); app.processEvents()
def fitted():
    r = mv.mapFromScene(mv.sceneRect()).boundingRect(); vp = mv.viewport().rect()
    return dict(inside=(r.left() >= -2 and r.top() >= -2 and r.right() <= vp.right() + 2 and r.bottom() <= vp.bottom() + 2),
                fill=max(r.width() / vp.width(), r.height() / vp.height()), scale=mv.transform().m11(), vp=[vp.width(), vp.height()])
out["fit_with_panel"] = fitted()
# selecție + panou
win.right_panel.current_cod_loc = "A1"
win.selected_cod_loc = "A1"
map_w_open = mv.width()
win.act_overview.trigger(); app.processEvents()
for _ in range(5):
    app.processEvents(); QTest.qWait(20)
out["overview_open_after_hide"] = win.splitter.panel_open()
out["act_checked_after_hide"] = win.act_overview.isChecked()
out["map_w_open"] = map_w_open
out["map_w_hidden"] = mv.width()
out["fit_hidden"] = fitted()
out["sel_after_hide"] = [win.right_panel.current_cod_loc, win.selected_cod_loc]
win.act_overview.trigger(); app.processEvents()
for _ in range(5):
    app.processEvents(); QTest.qWait(20)
out["overview_open_after_show"] = win.splitter.panel_open()
out["map_w_restored"] = mv.width()
out["panel_w_restored"] = win.right_panel.width()
out["sel_after_show"] = [win.right_panel.current_cod_loc, win.selected_cod_loc]
out["fit_restored"] = fitted()
# separator tras cu mouse-ul (simulat prin setSizes + splitterMoved)
tot = sum(win.splitter.sizes())
out["panel_min_width"] = win.splitter.panel_min_width()
want = win.splitter.panel_min_width() + 20
win.splitter.setSizes([tot - want, want]); win.splitter.splitterMoved.emit(tot - want, 1); app.processEvents()
out["want"] = want
out["panel_w_after_drag"] = win.right_panel.width(); out["map_w_after_drag"] = mv.width()
# panou foarte îngust: butoanele din „Date juridice” trec pe mai multe rânduri
juridic = win.right_panel.tab_juridic
out["juridic_min_w"] = juridic.minimumSizeHint().width()
# fereastra mare: panoul rămâne în ecran
win.resize(COMPACT[0], COMPACT[1]); app.processEvents()
su.fit_window_to_screen(win); app.processEvents()
fg = win.frameGeometry()
out["frame"] = [fg.left(), fg.top(), fg.right(), fg.bottom()]
out["frame_inside"] = fg.right() <= COMPACT[0] + 2 and fg.bottom() <= COMPACT[1] + 2
out["splitter_total"] = sum(win.splitter.sizes())
out["panel_right_edge_inside"] = win.right_panel.mapTo(win, win.right_panel.rect().bottomRight()).x() <= win.width()
print("RESULT " + json.dumps(out))
'''


def _run_window(tmp_path: Path, mac: bool, compact=COMPACT) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_")}
    env.update(CIMITIR_DATA_DIR=str(tmp_path / "data"), CIMITIR_ENV_FILE=str(tmp_path / "nu.env"),
               CIMITIR_LOG_DIR=str(tmp_path / "logs"), CIMITIR_DB_ENGINE="sqlite", CIMITIR_MODE="local",
               DATABASE_URL="", QT_QPA_PLATFORM="offscreen")
    code = WORKER.format(desktop=str(DESKTOP), mac=mac, compact=tuple(compact), map_png=str(tmp_path / "map.png"))
    proc = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(DESKTOP), capture_output=True, text=True, timeout=240)
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads([ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:])


def test_main_window_on_compact_macbook_screen(tmp_path):
    r = _run_window(tmp_path, mac=True)
    assert r["min"][0] <= 1000 and r["min"][1] <= 640, r
    assert r["size"][0] <= COMPACT[0] and r["size"][1] <= COMPACT[1], r
    assert r["after_min_resize"] == [1000, 640], r
    assert r["hint_w"] <= 1000, "conținutul nu trebuie să împiedice micșorarea la 1000 px"
    assert r["panel_min_w"] <= 560, r
    assert r["handle_w"] >= 8, "separator lat, ușor de prins cu trackpad-ul"
    assert r["frame_inside"], r
    assert r["panel_right_edge_inside"], r


def test_overview_hide_restore_keeps_selection_and_map_stays_fitted(tmp_path):
    r = _run_window(tmp_path, mac=True)
    assert r["overview_open_after_hide"] is False and r["act_checked_after_hide"] is False, r
    assert r["map_w_hidden"] > r["map_w_open"] + 250, "harta trebuie să preia spațiul panoului ascuns"
    assert r["fit_hidden"]["inside"] and r["fit_hidden"]["fill"] >= 0.9, r["fit_hidden"]
    assert r["fit_hidden"]["scale"] > r["fit_with_panel"]["scale"]
    assert r["sel_after_hide"] == ["A1", "A1"] == r["sel_after_show"]
    assert r["overview_open_after_show"] is True
    assert r["panel_w_restored"] >= 300
    assert r["fit_restored"]["inside"], r["fit_restored"]
    assert r["fit_with_panel"]["inside"] and r["fit_with_panel"]["fill"] >= 0.9
    assert abs(r["panel_w_after_drag"] - r["want"]) <= 12 and r["map_w_after_drag"] >= 240, r


def test_windows_main_window_keeps_1500x850_and_the_same_toggle_works(tmp_path):
    r = _run_window(tmp_path, mac=False, compact=(1920, 1040))
    assert r["min"] == [1500, 850], r
    assert r["after_min_resize"] == [1500, 850], r
    assert r["handle_w"] < 8, "separatorul implicit al platformei (Windows) rămâne neschimbat"
    assert r["overview_open_after_hide"] is False and r["overview_open_after_show"] is True
    assert r["sel_after_hide"] == ["A1", "A1"] == r["sel_after_show"]
    assert r["fit_with_panel"]["inside"]
    assert r["sizes"][0] > r["sizes"][1] and sum(r["sizes"]) > 1000, r


DIALOGS = r'''
import json, os, sys, importlib
sys.path.insert(0, {desktop!r})
os.chdir({desktop!r})
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication
app = QApplication([])
import database as db
db.init_db()
import screen_utils as su
COMPACT = {compact!r}
su.available_rect = lambda widget=None: QRect(0, 0, COMPACT[0], COMPACT[1])
su.install_screen_guard(app, force=True)
TARGETS = {targets!r}
out = {{}}
for mod, name, *rest in TARGETS:
    try:
        cls = getattr(importlib.import_module(mod), name)
        if rest:
            w = cls(*rest[0])
        else:
            try:
                w = cls()
            except TypeError:
                w = cls(None)
        w.show(); app.processEvents()
        fg = w.frameGeometry()
        out[name] = dict(inside=bool(fg.left() >= 0 and fg.top() >= 0 and fg.right() <= COMPACT[0] and fg.bottom() <= COMPACT[1]),
                         hint=[w.minimumSizeHint().width(), w.minimumSizeHint().height()],
                         min=[w.minimumWidth(), w.minimumHeight()], size=[w.width(), w.height()])
        w.close()
    except ImportError as exc:
        out[name] = dict(skip=str(exc))
    except Exception as exc:
        out[name] = dict(error=type(exc).__name__ + ": " + str(exc)[:200])
print("RESULT " + json.dumps(out))
'''

WINDOW_TARGETS = [
    ("locuri_window", "LocuriWindow"), ("inhumati_window", "InhumatiWindow"), ("concesionari_window", "ConcesionariWindow"),
    ("contracts_window", "ContractsWindow"), ("plati_window", "PlatiWindow"), ("backup_window", "BackupWindow"),
    ("alerts_window", "AlertsWindow"), ("integrity_window", "IntegrityWindow"), ("duplicate_people_window", "DuplicatePeopleWindow"),
    ("cloud_center_dialog", "CloudCenterDialog"), ("cloud_profiles_dialog", "CloudProfilesDialog"), ("cloud_sync_window", "CloudSyncWindow"),
    ("cereri_online_window", "CereriOnlineWindow"), ("field_inbox_window", "FieldInboxWindow"),
    ("field_import_history_window", "FieldImportHistoryWindow"), ("field_devices_window", "FieldDevicesDialog"),
    ("mobile_devices_window", "MobileDevicesDialog"), ("qr_custom_dialog", "QrCustomDialog"), ("qr_labels_dialog", "QrLabelsDialog"),
    ("rapoarte_dialog", "RapoarteDialog"), ("registry_selector", "RegistrySelector"), ("login_window", "LoginWindow"),
    ("add_inhumat_dialog", "AddInhumatDialog", (None, "A1")), ("locuri_window", "EditLocDialog", (None, "A1")), ("contracts_window", "AddContractDialog"),
    ("concesionari_window", "AddConcesionarDialog"), ("panels", "AddPlataDialog"), ("panels", "AddAvizDialog"),
    ("ai_scanare.scan_window", "AIScanareWindow"), ("ai_scanare.scan_dialog", "ScanDialog", (None, "buletin")), ("ai_scanare.batch_dialog", "BatchScanDialog"),
    ("ai_scanare.ai_settings_dialog", "AISettingsDialog"), ("user_mode_window", "UserModeWindow", ({"username": "u", "id": 1, "role": "user", "rights": ["view"]},)),
]


@pytest.mark.parametrize("screen", [(1280, 775), (1100, 700)])
def test_every_window_and_dialog_fits_a_compact_screen(tmp_path, screen):
    pytest.importorskip("reportlab")
    pytest.importorskip("qrcode")
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_")}
    env.update(CIMITIR_DATA_DIR=str(tmp_path / "data"), CIMITIR_ENV_FILE=str(tmp_path / "nu.env"),
               CIMITIR_LOG_DIR=str(tmp_path / "logs"), CIMITIR_DB_ENGINE="sqlite", CIMITIR_MODE="local",
               DATABASE_URL="", QT_QPA_PLATFORM="offscreen")
    code = DIALOGS.format(desktop=str(DESKTOP), compact=screen, targets=WINDOW_TARGETS)
    proc = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(DESKTOP), capture_output=True, text=True, timeout=280)
    assert proc.returncode == 0, proc.stderr[-3000:]
    res = json.loads([ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:])
    errors = {k: v for k, v in res.items() if "error" in v}
    assert not errors, errors
    outside = {k: v for k, v in res.items() if "inside" in v and not v["inside"]}
    assert not outside, "ferestre care ies din ecran: %s" % outside
    assert sum(1 for v in res.values() if "inside" in v) >= 25
