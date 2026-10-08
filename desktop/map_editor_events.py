# map_editor_events.py
"""
EditorEvents — event filter pentru viewport-ul map_view.

Procesează:
    - Click stânga/dreapta / Dublu click / Mouse move → deleagă către EditorTools
    - Tastele ESC / Backspace / Enter → deleagă către EditorTools.handle_key
"""

from PySide6.QtCore import QEvent, Qt


class EditorEvents:
    def __init__(self, window):
        self.w = window

    def eventFilter(self, source, event):
        # ---- eveniment KEYBOARD (pe viewport sau pe view) ----
        if event.type() == QEvent.KeyPress:
            try:
                if self.w.tools.handle_key(event):
                    return True
            except Exception:
                pass
            return False

        # ---- filtrăm restul doar pe viewport-ul hărții ----
        try:
            if source is not self.w.map_view.viewport():
                return False
        except Exception:
            return False

        if event.type() not in (
            QEvent.MouseButtonPress,
            QEvent.MouseMove,
            QEvent.MouseButtonDblClick,
            QEvent.MouseButtonRelease,
        ):
            return False

        # ---- PAN cu butonul din mijloc → lăsăm view-ul să gestioneze (orice mod) ----
        try:
            if event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
                if event.button() == Qt.MiddleButton:
                    return False
            if event.type() == QEvent.MouseMove and (event.buttons() & Qt.MiddleButton):
                return False
        except Exception:
            pass

        pos = self.w.map_view.mapToScene(event.pos())
        mode = self.w.modes.current_mode

        # ---- SELECT MODE → nu interceptăm nimic (pan lucrează normal) ----
        if mode == "select":
            return False

        # ---- toate celelalte moduri → deleagă către EditorTools ----
        if self.w.tools.handle_event(event, pos):
            return True

        return False
