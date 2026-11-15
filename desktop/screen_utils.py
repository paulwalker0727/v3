"""Dimensiuni adaptive la ecran (QScreen.availableGeometry, coordonate LOGICE — fără factori de scalare proprii).

- Windows/Linux: dimensiunile existente rămân neschimbate (fereastra principală minim 1500×850).
- macOS: minim adaptiv (~1000×640), limitat la suprafața disponibilă a ecranului (fără bara de meniu/Dock).
- `ScreenGuard` (doar macOS, sau `force=True`): la fiecare afișare a unei ferestre/dialog îi reduce minimul/dimensiunea la ce încape pe
  ecran și o aduce complet în zona vizibilă; reacționează la schimbarea ecranului/rezoluției.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QObject, QRect, QSize, QTimer, Qt
from PySide6.QtGui import QGuiApplication, QScreen
from PySide6.QtWidgets import QApplication, QLabel, QWidget

WINDOWS_MAIN_MIN = (1500, 850)
MAC_MAIN_MIN = (1000, 640)
MAC_MAIN_PREFERRED = (1440, 900)
MAC_MAIN_SCREEN_FRACTION = 0.92
_QWIDGETSIZE_MAX = (1 << 24) - 1
_WINDOW_TYPES = (Qt.Window, Qt.Dialog, Qt.Sheet, Qt.Tool)
_GUARD = None


def is_macos(platform: str | None = None) -> bool:
    return (platform or sys.platform) == "darwin"


def screen_for(widget: QWidget | None = None) -> QScreen | None:
    if widget is not None:
        try:
            wh = widget.windowHandle()
            if wh is not None and wh.screen() is not None:
                return wh.screen()
            s = widget.screen()
            if s is not None:
                return s
        except Exception:
            pass
    return QGuiApplication.primaryScreen()


def available_rect(widget: QWidget | None = None) -> QRect:
    s = screen_for(widget)
    return s.availableGeometry() if s is not None else QRect(0, 0, 1280, 800)


def _frame_extra(widget: QWidget) -> QSize:
    fg, g = widget.frameGeometry(), widget.geometry()
    return QSize(max(0, fg.width() - g.width()), max(0, fg.height() - g.height()))


def max_client_size(widget: QWidget, avail: QRect | None = None) -> QSize:
    """Dimensiunea maximă a zonei client care încape pe ecran (scade cadrul/bara de titlu)."""
    avail = avail or available_rect(widget)
    extra = _frame_extra(widget)
    return QSize(max(200, avail.width() - extra.width()), max(150, avail.height() - extra.height()))


def main_window_min_size(widget: QWidget | None = None, platform: str | None = None) -> QSize:
    """Windows/Linux: 1500×850 (neschimbat). macOS: ~1000×640, fără a depăși ecranul disponibil."""
    if not is_macos(platform):
        return QSize(*WINDOWS_MAIN_MIN)
    avail = available_rect(widget)
    return QSize(min(MAC_MAIN_MIN[0], avail.width()), min(MAC_MAIN_MIN[1], avail.height()))


def main_window_initial_size(widget: QWidget | None = None, platform: str | None = None) -> QSize | None:
    """macOS: 92% din ecranul disponibil (max 1440×900). Alte sisteme: None = comportamentul existent (fără resize)."""
    if not is_macos(platform):
        return None
    avail = available_rect(widget)
    w = min(MAC_MAIN_PREFERRED[0], int(avail.width() * MAC_MAIN_SCREEN_FRACTION))
    h = min(MAC_MAIN_PREFERRED[1], int(avail.height() * MAC_MAIN_SCREEN_FRACTION))
    mn = main_window_min_size(widget, platform)
    return QSize(max(w, mn.width()), max(h, mn.height()))


LONG_LABEL_CHARS = 70


def wrap_long_labels(window: QWidget) -> int:
    """Etichetele lungi fără word-wrap impun lățime minimă enormă ferestrei (text tăiat/ieșit din ecran): le activează word-wrap."""
    n = 0
    for lbl in window.findChildren(QLabel):
        try:
            if (not lbl.wordWrap() and len(lbl.text()) >= LONG_LABEL_CHARS
                    and lbl.pixmap().isNull() and lbl.movie() is None):
                lbl.setWordWrap(True)
                n += 1
        except Exception:
            pass
    return n


def fit_window_to_screen(window: QWidget) -> bool:
    """Reduce minimul/maximul/dimensiunea ferestrei la ecranul disponibil și o aduce în zona vizibilă. True = a modificat ceva."""
    if window is None or window.isMaximized() or window.isFullScreen():
        return False
    avail = available_rect(window)
    mx = max_client_size(window, avail)
    changed = wrap_long_labels(window) > 0

    mn, hint = window.minimumSize(), window.minimumSizeHint()
    eff_w = mn.width() or hint.width()
    eff_h = mn.height() or hint.height()
    if eff_w > mx.width() or eff_h > mx.height():
        window.setMinimumSize(min(eff_w, mx.width()), min(eff_h, mx.height()))
        changed = True
    if window.maximumWidth() != _QWIDGETSIZE_MAX and window.maximumWidth() > mx.width():
        window.setMaximumWidth(mx.width()); changed = True
    if window.maximumHeight() != _QWIDGETSIZE_MAX and window.maximumHeight() > mx.height():
        window.setMaximumHeight(mx.height()); changed = True

    w, h = min(window.width(), mx.width()), min(window.height(), mx.height())
    if (w, h) != (window.width(), window.height()):
        window.resize(w, h)
        changed = True

    fg = window.frameGeometry()
    x = min(max(fg.x(), avail.left()), max(avail.left(), avail.right() + 1 - fg.width()))
    y = min(max(fg.y(), avail.top()), max(avail.top(), avail.bottom() + 1 - fg.height()))
    if (x, y) != (fg.x(), fg.y()):
        window.move(window.x() + x - fg.x(), window.y() + y - fg.y())
        changed = True
    return changed


class ScreenGuard(QObject):
    """Filtru la nivel de aplicație: la `Show` încadrează ferestrele top-level pe ecran; la schimbarea ecranului/rezoluției le reîncadrează."""

    def __init__(self, app: QApplication):
        super().__init__(app)
        self._app = app
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self.refit_all)
        self._watched: set = set()
        app.installEventFilter(self)
        app.screenAdded.connect(self._on_screens)
        app.screenRemoved.connect(self._on_screens)
        for s in app.screens():
            self._watch(s)

    def _watch(self, screen: QScreen):
        if screen is None or id(screen) in self._watched:
            return
        self._watched.add(id(screen))
        screen.availableGeometryChanged.connect(lambda *_: self._timer.start())
        screen.geometryChanged.connect(lambda *_: self._timer.start())

    def _on_screens(self, *_):
        for s in self._app.screens():
            self._watch(s)
        self._timer.start()

    def refit_all(self):
        for w in self._app.topLevelWidgets():
            if w.isVisible() and w.windowType() in _WINDOW_TYPES:
                fit_window_to_screen(w)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow() \
                and obj.windowType() in _WINDOW_TYPES and not obj.property("screen_guard_skip"):
            try:
                fit_window_to_screen(obj)
                wh = obj.windowHandle()
                if wh is not None and not obj.property("_sg_connected"):
                    obj.setProperty("_sg_connected", True)
                    wh.screenChanged.connect(lambda *_: self._timer.start())
            except Exception:
                pass
        return False


def install_screen_guard(app: QApplication | None = None, force: bool = False, platform: str | None = None):
    """Instalează garda pe macOS (sau cu `force=True`). Windows/Linux: nu face nimic."""
    app = app or QApplication.instance()
    if app is None or not (force or is_macos(platform)):
        return None
    global _GUARD
    if _GUARD is None or _GUARD.parent() is not app:
        _GUARD = ScreenGuard(app)
    return _GUARD
