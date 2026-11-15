from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import (
    QGraphicsPolygonItem, QGraphicsItem, QApplication, QMenu
)
from PySide6.QtGui import QBrush, QPen, QColor, QPolygonF, QCursor

import map_editor_diag as diag


class GravePlotItem(QGraphicsPolygonItem, QObject):
    clicked = Signal(object)

    def _editor_window(self):
        """Fereastra editorului de hartă care conține scena itemului.

        NU QApplication.activeWindow(): acela e None cât timp activarea ferestrei întârzie după un dialog (frecvent pe macOS)
        sau o altă fereastră (editor nemodal, popup) — iar atunci modul curent și acțiunile se pierdeau."""
        try:
            scene = self.scene()
            views = scene.views() if scene is not None else []
            for v in views:
                w = v.window()
                if w is not None and hasattr(w, "modes"):
                    return w
        except RuntimeError:
            pass
        w = QApplication.activeWindow()
        return w if (w is not None and hasattr(w, "modes")) else None

    def __init__(self, polygon: QPolygonF, data: dict):
        QGraphicsPolygonItem.__init__(self, polygon)
        QObject.__init__(self)

        # salvăm data în item
        self.setData(0, data)

        # sector grafic sau loc normal
        if data.get("type") == "sector_grafic":
            self._setup_sector_item(data)
        else:
            self._setup_plot_item(data)

    # ----------------------------------------------------
    # SECTOR GRAFIC
    # ----------------------------------------------------
    def _setup_sector_item(self, data):
        # sectoarele sunt acum interactive: click stânga = selectare (sector activ),
        # click dreapta = meniu contextual (culoare / opacitate / formă / ștergere)
        self.setAcceptedMouseButtons(Qt.LeftButton | Qt.RightButton)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)

        color = data.get("sector_color", QColor(255, 0, 0, 100))
        self.setBrush(QBrush(color))
        self.setPen(QPen(color.darker(), 2))
        # sub locuri (z=10) dar deasupra imaginii hărții (z=-2000)
        self.setZValue(-500)

    # ----------------------------------------------------
    # LOC NORMAL
    # ----------------------------------------------------
    def _setup_plot_item(self, data):
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsFocusable, True)
        self.setZValue(10)
        self.setVisible(True)

        color = data.get("plot_color", QColor(0, 120, 255, 80))
        self.setBrush(QBrush(color))
        self.setPen(QPen(color.darker(), 2))

    # ----------------------------------------------------
    # HOVER
    # ----------------------------------------------------
    def hoverEnterEvent(self, event):
        data0 = self.data(0)
        if isinstance(data0, dict) and data0.get("type") == "sector_grafic":
            # highlight subtil pe sector
            c = self.brush().color()
            self.setPen(QPen(QColor("#ff8800"), 3))
            return

        if not self.isSelected():
            self.setPen(QPen(QColor("#0077cc"), 3))

    def hoverLeaveEvent(self, event):
        data0 = self.data(0)
        if isinstance(data0, dict) and data0.get("type") == "sector_grafic":
            self.setPen(QPen(self.brush().color().darker(), 2))
            return

        if not self.isSelected():
            color = data0.get("plot_color", QColor(0, 120, 255, 80))
            self.setPen(QPen(color.darker(), 2))

    # ----------------------------------------------------
    # CLICK
    # ----------------------------------------------------
    def mousePressEvent(self, event):
        data0 = self.data(0)
        widget = self._editor_window()

        # mod ștergere → lăsăm event-filter-ul să gestioneze (nu interceptăm aici)
        if widget is not None and hasattr(widget, "modes"):
            if widget.modes.current_mode in ("delete", "delete_sector"):
                return

        # ---- SECTOR GRAFIC ----
        if isinstance(data0, dict) and data0.get("type") == "sector_grafic":
            sid = data0.get("sector_id")
            # marcăm sectorul ca activ (folosit de toolbar: culoare / opacitate / editare)
            if widget is not None and sid:
                widget.active_sector_id = sid
                try:
                    widget.statusBar().showMessage(
                        f"Sector activ: {sid}  —  click dreapta pentru editare"
                    )
                except Exception:
                    pass

            if event.button() == Qt.LeftButton:
                event.accept()
                return

            if event.button() == Qt.RightButton:
                event.accept()
                if getattr(widget, "read_only", False):
                    return
                self.open_sector_context_menu(widget)
                return
            return

        # CLICK STÂNGA → selectare
        if event.button() == Qt.LeftButton:
            event.accept()
            self.setSelected(True)
            self.setFocus()
            self.setPen(QPen(QColor("#cc0000"), 3))
            self.clicked.emit(self)
            return

        # CLICK DREAPTA → meniu contextual
        if event.button() == Qt.RightButton:
            event.accept()
            if getattr(widget, "read_only", False):
                return
            self.open_context_menu()
            return

        super().mousePressEvent(event)

    # ----------------------------------------------------
    # MENIU CONTEXTUAL LOC
    # ----------------------------------------------------
    def open_context_menu(self):
        from edit_plot_window import EditPlotWindow
        from polygon_edit_window import PolygonEditWindow

        menu = QMenu()

        act_color = menu.addAction("Schimbă culoarea / opacitatea")
        act_poly = menu.addAction("Editează poligonul")
        act_delete = menu.addAction("Șterge locul")

        widget = self._editor_window()
        if widget is None:
            diag.report("meniu contextual loc", RuntimeError("fereastra editorului nu a fost găsită"), None, notify=False)
            return
        action = menu.exec(QCursor.pos())

        # EDITARE CULOARE
        if action == act_color:
            dlg = EditPlotWindow(widget, self)
            dlg.show()
            widget._active_editor = dlg
            return

        # EDITARE POLIGON
        if action == act_poly:
            dlg = PolygonEditWindow(widget, self)
            dlg.show()
            widget._active_editor = dlg
            return

        # ȘTERGERE LOC
        if action == act_delete and hasattr(widget, "delete_plot"):
            widget.delete_plot(self)
            return

    # ----------------------------------------------------
    # MENIU CONTEXTUAL SECTOR
    # ----------------------------------------------------
    def open_sector_context_menu(self, widget):
        from edit_sector_window import EditSectorWindow

        menu = QMenu()
        act_edit = menu.addAction("Editează sector (culoare / opacitate / formă)")
        act_delete = menu.addAction("Șterge sectorul")

        if widget is None:
            return
        action = menu.exec(QCursor.pos())

        data0 = self.data(0)
        sid = data0.get("sector_id") if isinstance(data0, dict) else None
        if not sid:
            return

        # EDITARE SECTOR (culoare + opacitate + formă într-un singur dialog)
        if action == act_edit:
            sector_data = None
            if hasattr(widget, "sectors"):
                sector_data = widget.sectors.get(sid)

            if not sector_data:
                poly = self.polygon()
                sector_data = {
                    "id": sid,
                    "name": sid,
                    "sector_color": self.brush().color(),
                    "points": [(poly.at(i).x(), poly.at(i).y()) for i in range(poly.count())],
                    "item": self,
                }
                if hasattr(widget, "sectors"):
                    widget.sectors[sid] = sector_data
            else:
                sector_data["item"] = self

            dlg = EditSectorWindow(widget, self, sector_data)
            dlg.show()
            widget._active_editor = dlg
            return

        # ȘTERGERE SECTOR
        if action == act_delete:
            from PySide6.QtWidgets import QMessageBox
            import database as db
            if not widget.actions.delete_sector_from_db(sid):
                QMessageBox.warning(widget, "Ștergere refuzată",
                                    f"{db.last_refusal() or 'Ștergerea din baza de date a eșuat.'}\n\nSectorul {sid} a fost păstrat.")
                return
            try:
                widget.map_view._scene.removeItem(self)
            except RuntimeError:
                pass
            if hasattr(widget, "sectors"):
                widget.sectors.pop(sid, None)
            if getattr(widget, "active_sector_id", None) == sid:
                widget.active_sector_id = None
            try:
                widget.statusBar().showMessage(f"Sector {sid} șters")
            except Exception:  # noqa: BLE001
                pass
            return
