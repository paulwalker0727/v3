"""Splitter hartă | panou Overview: panoul se poate trage, micșora, ascunde și restaura fără pierderea selecției sau a datelor."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QSplitter, QSplitterHandle, QWidget

MIN_MAP_WIDTH = 240
DEFAULT_PANEL_WIDTH = 400


class _Handle(QSplitterHandle):
    def mouseDoubleClickEvent(self, event):
        sp = self.splitter()
        if isinstance(sp, OverviewSplitter):
            sp.toggle_panel()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class OverviewSplitter(QSplitter):
    panel_toggled = Signal(bool)

    def __init__(self, map_widget: QWidget, panel: QWidget, panel_width: int = DEFAULT_PANEL_WIDTH,
                 handle_width: int | None = None, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.map_widget = map_widget
        self.panel = panel
        self._open = True
        self._last_width = panel_width
        self.addWidget(map_widget)
        self.addWidget(panel)
        self.setStretchFactor(0, 1)
        self.setStretchFactor(1, 0)
        self.setCollapsible(0, False)
        self.setCollapsible(1, True)
        self.setOpaqueResize(True)
        map_widget.setMinimumWidth(MIN_MAP_WIDTH)
        if handle_width:
            self.setHandleWidth(handle_width)
        self.splitterMoved.connect(self._on_moved)

    def createHandle(self):
        return _Handle(self.orientation(), self)

    def panel_open(self) -> bool:
        return self._open

    def panel_min_width(self) -> int:
        return max(1, self.panel.minimumSizeHint().width(), self.panel.minimumWidth())

    def _clamped_width(self, total: int, want: int) -> int:
        lo = self.panel_min_width()
        hi = max(lo, total - MIN_MAP_WIDTH)
        return max(lo, min(want, hi))

    def set_panel_width(self, width: int):
        total = sum(self.sizes()) or self.width()
        w = self._clamped_width(total, width)
        self._last_width = w
        self.setSizes([max(1, total - w), w])

    def hide_panel(self):
        if not self._open:
            return
        cur = self.sizes()[1] if len(self.sizes()) > 1 else 0
        if cur > 0:
            self._last_width = cur
        self.panel.hide()
        self._set_open(False)

    def show_panel(self):
        if self._open and self.sizes()[1] > 0:
            return
        self.panel.show()
        self._set_open(True)
        self.set_panel_width(self._last_width)

    def toggle_panel(self):
        if self._open:
            self.hide_panel()
        else:
            self.show_panel()

    def _set_open(self, value: bool):
        if self._open != value:
            self._open = value
            self.panel_toggled.emit(value)

    def _on_moved(self, *_):
        sizes = self.sizes()
        if len(sizes) < 2:
            return
        if sizes[1] == 0:
            self._set_open(False)
        else:
            self._last_width = sizes[1]
            self._set_open(True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._open and self.panel.isVisibleTo(self):
            s = self.sizes()
            total = sum(s)
            if len(s) == 2 and s[1] > 0 and total > 0:
                limit = max(self.panel_min_width(), total - MIN_MAP_WIDTH)
                if s[1] > limit:
                    self.setSizes([total - limit, limit])
