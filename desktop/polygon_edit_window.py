from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QGroupBox
)
from PySide6.QtCore import Qt, QPointF, QEvent
from PySide6.QtGui import QPolygonF, QColor

from point_handle_item import PointHandleItem
import geometry_utils as geo


class PolygonEditWindow(QDialog):
    """
    Editor poligon COMPLET pentru loc de veci:
    - mutare puncte (cu snap dacă ții Shift/Ctrl)
    - adăugare punct (mijlocul celei mai lungi muchii)
    - ștergere punct (click dreapta pe punct)
    - rotire / scalare întregul poligon
    - Undo / Redo
    """
    def __init__(self, parent, plot_item):
        super().__init__(parent)
        self.setWindowTitle("Editare poligon loc")
        self.resize(340, 300)
        self.setModal(False)

        self.plot_item = plot_item
        self.view = parent.map_view
        self.scene = self.view._scene

        self.original_polygon = QPolygonF(self.plot_item.polygon())
        self.handles: list[PointHandleItem] = []
        self.undo_stack: list[list] = []
        self.redo_stack: list[list] = []
        self._suppress_undo = False

        layout = QVBoxLayout()
        layout.addWidget(QLabel(
            "Mută punctele pe hartă (ține Shift/Ctrl pentru snap).\n"
            "Click dreapta pe un punct = ștergere."))

        # --- puncte ---
        pts_box = QGroupBox("Puncte")
        pl = QHBoxLayout()
        btn_add = QPushButton("➕ Adaugă (mijloc)")
        btn_add.clicked.connect(self.add_point)
        pl.addWidget(btn_add)
        self.btn_edge = QPushButton("🖈 Pe muchie (click)")
        self.btn_edge.setCheckable(True)
        self.btn_edge.clicked.connect(self.toggle_edge_mode)
        pl.addWidget(self.btn_edge)
        pts_box.setLayout(pl)
        layout.addWidget(pts_box)

        # --- transformare ---
        tr_box = QGroupBox("Transformare")
        tl = QHBoxLayout()
        for text, cb in (
            ("↺", lambda: self.rotate(-5)),
            ("↻", lambda: self.rotate(5)),
            ("➖", lambda: self.scale(0.95)),
            ("➕", lambda: self.scale(1.05)),
        ):
            b = QPushButton(text)
            b.clicked.connect(cb)
            tl.addWidget(b)
        tr_box.setLayout(tl)
        layout.addWidget(tr_box)

        # --- undo / redo ---
        ur = QHBoxLayout()
        self.btn_undo = QPushButton("⟲ Undo")
        self.btn_redo = QPushButton("⟳ Redo")
        self.btn_undo.clicked.connect(self.undo)
        self.btn_redo.clicked.connect(self.redo)
        ur.addWidget(self.btn_undo)
        ur.addWidget(self.btn_redo)
        layout.addLayout(ur)

        # --- salvare / anulare ---
        btn_row = QHBoxLayout()
        btn_save = QPushButton("Salvează")
        btn_cancel = QPushButton("Anulează")
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        self.setLayout(layout)
        btn_save.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)

        self._edge_mode = False
        self._rebuild_handles()

    # ----------------------------------------------------
    # ADĂUGARE PUNCT PRIN CLICK PE MUCHIE (pe hartă)
    # ----------------------------------------------------
    def toggle_edge_mode(self):
        self._edge_mode = self.btn_edge.isChecked()
        if self._edge_mode:
            self.view.viewport().installEventFilter(self)
            self.parent().statusBar().showMessage(
                "Click pe o muchie a poligonului pentru a insera un punct.")
        else:
            self.view.viewport().removeEventFilter(self)

    def eventFilter(self, obj, event):
        if self._edge_mode and event.type() == QEvent.MouseButtonPress:
            if event.button() == Qt.LeftButton:
                scene_pos = self.view.mapToScene(event.pos())
                self._push_undo()
                new_pts, _ = geo.insert_vertex_on_polygon(
                    self._points(), (scene_pos.x(), scene_pos.y()))
                self._suppress_undo = True
                self._set_points(new_pts)
                self._suppress_undo = False
                self.btn_edge.setChecked(False)
                self._edge_mode = False
                self.view.viewport().removeEventFilter(self)
                return True
        return super().eventFilter(obj, event)

    # ----------------------------------------------------
    # HELPERS PUNCTE
    # ----------------------------------------------------
    def _points(self):
        poly = self.plot_item.polygon()
        return [(poly.at(i).x(), poly.at(i).y()) for i in range(poly.count())]

    def _set_points(self, points):
        poly = QPolygonF([QPointF(x, y) for x, y in points])
        self.plot_item.setPolygon(poly)
        self._rebuild_handles()

    def _clear_handles(self):
        for h in self.handles:
            try:
                self.scene.removeItem(h)
            except Exception:
                pass
        self.handles = []

    def _rebuild_handles(self):
        self._clear_handles()
        poly = self.plot_item.polygon()
        for i in range(poly.count()):
            p = poly.at(i)
            handle = PointHandleItem(i, self.plot_item.mapToScene(p), self.plot_item)
            handle.moved.connect(self._handle_moved)
            handle.pressed.connect(self._handle_pressed)
            handle.deleted.connect(self.delete_point)
            self.handles.append(handle)

    # ----------------------------------------------------
    # UNDO / REDO
    # ----------------------------------------------------
    def _push_undo(self):
        if self._suppress_undo:
            return
        self.undo_stack.append(self._points())
        self.redo_stack.clear()

    def undo(self):
        if not self.undo_stack:
            return
        self.redo_stack.append(self._points())
        pts = self.undo_stack.pop()
        self._suppress_undo = True
        self._set_points(pts)
        self._suppress_undo = False

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append(self._points())
        pts = self.redo_stack.pop()
        self._suppress_undo = True
        self._set_points(pts)
        self._suppress_undo = False

    # ----------------------------------------------------
    # MUTARE PUNCT (cu snap)
    # ----------------------------------------------------
    def _handle_pressed(self, index):
        self._push_undo()

    def _handle_moved(self, index: int, scene_pos: QPointF):
        adv = getattr(self.parent(), "adv", None)
        if adv is not None:
            try:
                scene_pos = adv.snap_qpoint(scene_pos, exclude_item=self.plot_item)
            except Exception:
                pass
        local_pos = self.plot_item.mapFromScene(scene_pos)
        poly = self.plot_item.polygon()
        if 0 <= index < poly.count():
            poly[index] = local_pos
            self.plot_item.setPolygon(poly)

    # ----------------------------------------------------
    # ADĂUGARE / ȘTERGERE PUNCT
    # ----------------------------------------------------
    def add_point(self):
        pts = self._points()
        if len(pts) < 2:
            return
        # inserează la mijlocul celei mai lungi muchii
        best_len, best_i = -1.0, 0
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            d = geo.dist(a, b)
            if d > best_len:
                best_len, best_i = d, i
        a, b = pts[best_i], pts[(best_i + 1) % len(pts)]
        mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        self._push_undo()
        new_pts = list(pts)
        new_pts.insert(best_i + 1, mid)
        self._suppress_undo = True
        self._set_points(new_pts)
        self._suppress_undo = False

    def delete_point(self, index):
        pts = self._points()
        if len(pts) <= 3:
            return
        self._push_undo()
        new_pts = geo.delete_vertex(pts, index)
        self._suppress_undo = True
        self._set_points(new_pts)
        self._suppress_undo = False

    # ----------------------------------------------------
    # ROTIRE / SCALARE
    # ----------------------------------------------------
    def rotate(self, deg):
        self._push_undo()
        new_pts = geo.transform_polygon(self._points(), angle_deg=deg)
        self._suppress_undo = True
        self._set_points(new_pts)
        self._suppress_undo = False

    def scale(self, factor):
        self._push_undo()
        new_pts = geo.transform_polygon(self._points(), scale=factor)
        self._suppress_undo = True
        self._set_points(new_pts)
        self._suppress_undo = False

    # ----------------------------------------------------
    # SALVARE
    # ----------------------------------------------------
    def accept(self):
        data0 = self.plot_item.data(0)
        cod_loc = data0.get("cod_loc") if isinstance(data0, dict) else None
        if not cod_loc:
            print("[PolygonEditWindow] EROARE: cod_loc lipsă în data(0)")
            self._cleanup()
            super().accept()
            return

        points = self._points()
        color = self.plot_item.brush().color().name(QColor.HexArgb)
        try:
            self.parent().actions.save_plot_to_db(cod_loc, points, color)
            self.parent().statusBar().showMessage(f"Poligon {cod_loc} salvat")
            try:
                self.parent().adv.refresh_area_labels()
            except Exception:
                pass
        except Exception as e:
            print("[PolygonEditWindow] EROARE salvare:", e)

        self._cleanup()
        super().accept()

    def reject(self):
        self.plot_item.setPolygon(self.original_polygon)
        self._cleanup()
        super().reject()

    def _cleanup(self):
        try:
            if getattr(self, "_edge_mode", False):
                self.view.viewport().removeEventFilter(self)
        except Exception:
            pass
        self._clear_handles()
