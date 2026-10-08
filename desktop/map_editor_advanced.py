# map_editor_advanced.py
"""
AdvancedEditor — grid de referință, ghidaje, calibrare scară, rânduri reale
și generare locuri de-a lungul unui rând. Snap activ când se ține Shift/Ctrl.

Toate datele (grid config, scară, ghidaje, rânduri) sunt persistente în DB.
"""
import json
from typing import List, Optional

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPen, QColor, QFont, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsLineItem, QGraphicsItem, QGraphicsPathItem, QGraphicsSimpleTextItem,
    QInputDialog, QMessageBox, QMenu, QApplication,
)
from PySide6.QtGui import QPainterPath

import database as db
import geometry_utils as geo


GUIDE_COLOR = "#00AAFF"
ROW_COLOR = "#22C55E"
GRID_COLOR = QColor(120, 120, 120, 90)

_PEN_STYLES = {
    "solid": Qt.SolidLine,
    "dash": Qt.DashLine,
    "dot": Qt.DotLine,
    "dashdot": Qt.DashDotLine,
}

_LINE_DEFAULTS = {
    "row":   {"color": "#16A34A", "width": 3, "style": "solid"},
    "guide": {"color": "#00AAFF", "width": 2, "style": "dash"},
    "grid":  {"color": "#7A7A7A", "width": 1, "style": "solid"},
}


def pen_style(name: str):
    return _PEN_STYLES.get(name, Qt.SolidLine)


# ---------------------------------------------------------------------------
# GHIDAJ (linie mutabilă, persistentă)
# ---------------------------------------------------------------------------
class GuideLineItem(QGraphicsLineItem):
    def __init__(self, guide: dict, adv):
        pts = guide["points"]
        super().__init__(pts[0][0], pts[0][1], pts[1][0], pts[1][1])
        self.guide_id = guide["id"]
        self.adv = adv
        st = adv.line_style("guide")
        pen = QPen(QColor(st["color"]), st["width"])
        pen.setCosmetic(True)
        pen.setStyle(pen_style(st["style"]))
        self.setPen(pen)
        self.setZValue(50)
        self.setData(0, {"type": "guide", "guide_id": self.guide_id})
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.SizeAllCursor)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        ln = self.line()
        off = self.pos()
        p1 = (ln.x1() + off.x(), ln.y1() + off.y())
        p2 = (ln.x2() + off.x(), ln.y2() + off.y())
        self.setPos(0, 0)
        self.setLine(p1[0], p1[1], p2[0], p2[1])
        self.adv.update_guide(self.guide_id, [p1, p2])

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            event.accept()
            menu = QMenu()
            act_del = menu.addAction("Șterge ghidajul")
            action = menu.exec(QApplication.activeWindow().cursor().pos())
            if action == act_del:
                self.adv.delete_guide(self.guide_id, self)
            return
        super().mousePressEvent(event)


class AdvancedEditor:
    def __init__(self, window):
        self.w = window
        self.guide_items: List[GuideLineItem] = []
        self.grid_items = []
        self.row_items = {}   # row_id -> (path_item, text_item)
        self._calib_pts = []
        self._pending_row_order: Optional[int] = None

    @property
    def scene(self):
        return self.w.map_view._scene

    # =====================================================================
    # SETTINGS
    # =====================================================================
    def get_ppm(self) -> Optional[float]:
        try:
            v = db.get_setting("px_per_meter")
            return float(v) if v else None
        except Exception:
            return None

    def set_ppm(self, v: float):
        db.set_setting("px_per_meter", str(v))

    def unit_label(self) -> str:
        return "m" if self.get_ppm() else "px"

    # ---- stil linii (rând / ghidaj / grid) ----
    def line_style(self, kind: str) -> dict:
        d = _LINE_DEFAULTS.get(kind, _LINE_DEFAULTS["guide"])
        color = db.get_setting(f"line_{kind}_color") or d["color"]
        try:
            width = int(db.get_setting(f"line_{kind}_width") or d["width"])
        except Exception:
            width = d["width"]
        style = db.get_setting(f"line_{kind}_style") or d["style"]
        return {"color": color, "width": max(1, width), "style": style}

    def set_line_style(self, kind: str, color: str, width: int, style: str):
        db.set_setting(f"line_{kind}_color", color)
        db.set_setting(f"line_{kind}_width", str(int(width)))
        db.set_setting(f"line_{kind}_style", style)

    def configure_line_style(self):
        from advanced_dialogs import LineStyleDialog
        dlg = LineStyleDialog(self.w, self)
        if dlg.exec():
            # reaplicăm pe tot ce e desenat
            self.load_guides()
            self.load_rows()
            if self.grid_cfg()["visible"]:
                self.rebuild_grid()
            self.w.statusBar().showMessage("Stil linii actualizat")

    def to_px(self, value: float) -> float:
        ppm = self.get_ppm()
        return value * ppm if ppm else value

    def grid_cfg(self) -> dict:
        def _get(k, d):
            v = db.get_setting(k)
            return float(v) if v is not None else d
        return {
            "visible": (db.get_setting("grid_visible") or "0") == "1",
            "spacing": _get("grid_spacing", 40.0),
            "angle": _get("grid_angle", 0.0),
            "origin": (_get("grid_origin_x", 0.0), _get("grid_origin_y", 0.0)),
        }

    # =====================================================================
    # ÎNCĂRCARE INIȚIALĂ
    # =====================================================================
    def load_all(self):
        self.load_guides()
        self.load_rows()
        cfg = self.grid_cfg()
        if cfg["visible"]:
            self.rebuild_grid()
        self.refresh_area_labels()

    # =====================================================================
    # GRID DE REFERINȚĂ
    # =====================================================================
    def clear_grid(self):
        for it in self.grid_items:
            try:
                self.scene.removeItem(it)
            except Exception:
                pass
        self.grid_items = []

    def rebuild_grid(self):
        self.clear_grid()
        cfg = self.grid_cfg()
        if not cfg["visible"] or cfg["spacing"] <= 0:
            return
        rect = self.scene.sceneRect()
        if rect.isEmpty():
            rect = QRectF(0, 0, 2000, 2000)
        import math
        spacing = cfg["spacing"]
        angle = math.radians(cfg["angle"])
        ox, oy = cfg["origin"]
        diag = math.hypot(rect.width(), rect.height()) + spacing * 2
        n = int(diag / spacing) + 2
        ca, sa = math.cos(angle), math.sin(angle)

        def g2s(gx, gy):
            return (gx * ca - gy * sa + ox, gx * sa + gy * ca + oy)

        pen = QPen(GRID_COLOR, 0)
        pen.setCosmetic(True)
        st = self.line_style("grid")
        gpen = QPen(QColor(st["color"]), st["width"])
        gpen.setCosmetic(True)
        gpen.setStyle(pen_style(st["style"]))
        pen = gpen
        for k in range(-n, n + 1):
            gx = k * spacing
            a = g2s(gx, -diag)
            b = g2s(gx, diag)
            self._add_grid_line(a, b, pen)
            gy = k * spacing
            a = g2s(-diag, gy)
            b = g2s(diag, gy)
            self._add_grid_line(a, b, pen)

    def _add_grid_line(self, a, b, pen):
        it = QGraphicsLineItem(a[0], a[1], b[0], b[1])
        it.setPen(pen)
        it.setZValue(-800)
        it.setData(0, {"type": "grid"})
        it.setAcceptedMouseButtons(Qt.NoButton)
        self.scene.addItem(it)
        self.grid_items.append(it)

    def toggle_grid(self):
        cfg = self.grid_cfg()
        new = not cfg["visible"]
        db.set_setting("grid_visible", "1" if new else "0")
        if new:
            self.rebuild_grid()
            self.w.statusBar().showMessage("Grid activat")
        else:
            self.clear_grid()
            self.w.statusBar().showMessage("Grid dezactivat")

    def configure_grid(self):
        cfg = self.grid_cfg()
        spacing, ok = QInputDialog.getDouble(
            self.w, "Grid — spațiere",
            f"Spațiere grid ({self.unit_label()}):", cfg["spacing"], 1.0, 100000.0, 2)
        if not ok:
            return
        angle, ok = QInputDialog.getDouble(
            self.w, "Grid — unghi", "Unghi grid (grade):", cfg["angle"], -90.0, 90.0, 1)
        if not ok:
            return
        db.set_setting("grid_spacing", str(self.to_px(spacing)))
        db.set_setting("grid_angle", str(angle))
        db.set_setting("grid_visible", "1")
        self.rebuild_grid()
        self.w.statusBar().showMessage("Grid actualizat")

    # =====================================================================
    # GHIDAJE
    # =====================================================================
    def load_guides(self):
        for it in self.guide_items:
            try:
                self.scene.removeItem(it)
            except Exception:
                pass
        self.guide_items = []
        for g in db.list_guides():
            if len(g.get("points") or []) >= 2:
                self._create_guide_item(g)

    def _create_guide_item(self, guide: dict):
        item = GuideLineItem(guide, self)
        self.scene.addItem(item)
        self.guide_items.append(item)
        return item

    def add_guide(self, kind: str, points):
        gid = db.add_guide(kind, points, GUIDE_COLOR)
        if gid:
            self._create_guide_item({"id": gid, "kind": kind, "points": points, "color": GUIDE_COLOR})

    def update_guide(self, guide_id: int, points):
        conn = db.get_connection()
        try:
            conn.execute("UPDATE guides SET points = ? WHERE id = ?",
                         (json.dumps(points), guide_id))
        finally:
            conn.close()

    def delete_guide(self, guide_id: int, item=None):
        db.delete_guide(guide_id)
        if item is not None:
            try:
                self.scene.removeItem(item)
            except Exception:
                pass
            if item in self.guide_items:
                self.guide_items.remove(item)

    def clear_all_guides(self):
        reply = QMessageBox.question(
            self.w, "Ștergere ghidaje", "Ștergi toate ghidajele?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        db.clear_guides()
        self.load_guides()
        self.w.statusBar().showMessage("Ghidaje șterse")

    def _scene_bounds(self):
        rect = self.scene.sceneRect()
        if rect.isEmpty():
            rect = QRectF(0, 0, 2000, 2000)
        return rect

    # =====================================================================
    # CALIBRARE SCARĂ
    # =====================================================================
    def start_calibrate(self):
        self._calib_pts = []
        self._clear_temp_line()
        self.w.set_tool_mode("calibrate")
        self.w.statusBar().showMessage(
            "Calibrare scară: trasează o linie peste o distanță cunoscută "
            "(click START → click STOP), apoi introdu distanța în metri.")

    # =====================================================================
    # RIGLĂ (măsurare liberă)
    # =====================================================================
    def start_ruler(self):
        self._calib_pts = []
        self._clear_temp_line()
        self.w.set_tool_mode("ruler")
        self.w.statusBar().showMessage(
            "Riglă: click START → click STOP pentru a măsura o distanță pe hartă.")

    def _ruler_text(self, p1, p2) -> str:
        px = geo.dist(p1, p2)
        ppm = self.get_ppm()
        if ppm:
            return f"Distanță: {px / ppm:.2f} m  ({px:.0f} px)"
        return f"Distanță: {px:.0f} px  (scara necalibrată)"

    # =====================================================================
    # RÂNDURI
    # =====================================================================
    def start_draw_row(self, insert_order: Optional[int] = None):
        self._pending_row_order = insert_order
        self.w.set_tool_mode("draw_row")
        msg = "Desenează linia RÂNDULUI (poate fi strâmbă) — dublu-click pentru finalizare"
        if insert_order is not None:
            msg = f"Inserare rând la poziția {insert_order} — desenează linia, dublu-click finalizare"
        self.w.statusBar().showMessage(msg)

    def finalize_row(self, points):
        if not points or len(points) < 2:
            return
        sector_label, ok = QInputDialog.getText(
            self.w, "Rând nou", "Sector + parcelă (ex. A):", text="A")
        if not ok:
            return
        sector_label = (sector_label or "A").strip()

        unit = self.unit_label()
        width, ok = QInputDialog.getDouble(
            self.w, "Rând nou", f"Lățime loc ({unit}):", 2.0 if unit == "m" else 30.0, 0.1, 100000.0, 2)
        if not ok:
            return
        depth, ok = QInputDialog.getDouble(
            self.w, "Rând nou", f"Adâncime loc ({unit}):", 2.5 if unit == "m" else 40.0, 0.1, 100000.0, 2)
        if not ok:
            return

        rows = db.list_rows(sector_label)
        if self._pending_row_order is not None:
            order_index = self._pending_row_order
            db.shift_rows_order(sector_label, order_index, 1)
        else:
            order_index = (max([r["order_index"] for r in rows], default=0) + 1) if rows else 1

        name = f"Rând {order_index}"
        row_id = db.add_row(sector_label, name, order_index, points,
                            self.to_px(width), self.to_px(depth))
        self._pending_row_order = None
        if row_id:
            self._draw_row_item({
                "id": row_id, "sector_label": sector_label, "name": name,
                "order_index": order_index, "baseline": points,
                "width": self.to_px(width), "depth": self.to_px(depth),
            })
            self.w.statusBar().showMessage(f"{name} ({sector_label}) creat. Foloseste 'Genereaza locuri pe rand'.")
        self.w.current_points = []
        try:
            self.w.map_view.clear_preview()
        except Exception:
            pass

    def load_rows(self):
        for path_it, txt_it in self.row_items.values():
            for it in (path_it, txt_it):
                try:
                    self.scene.removeItem(it)
                except Exception:
                    pass
        self.row_items = {}
        for r in db.list_rows():
            if len(r.get("baseline") or []) >= 2:
                self._draw_row_item(r)

    def _draw_row_item(self, row: dict):
        pts = row["baseline"]
        path = QPainterPath(QPointF(pts[0][0], pts[0][1]))
        for x, y in pts[1:]:
            path.lineTo(x, y)
        item = QGraphicsPathItem(path)
        st = self.line_style("row")
        pen = QPen(QColor(st["color"]), st["width"])
        pen.setCosmetic(True)
        pen.setStyle(pen_style(st["style"]))
        item.setPen(pen)
        item.setZValue(40)
        item.setData(0, {"type": "row_baseline", "row_id": row["id"]})
        item.setAcceptedMouseButtons(Qt.NoButton)
        self.scene.addItem(item)

        txt = QGraphicsSimpleTextItem(f"{row['sector_label']}·{row['name']}")
        txt.setFont(QFont("Arial", 10, QFont.Bold))
        txt.setBrush(QColor(st["color"]))
        txt.setZValue(41)
        txt.setPos(pts[0][0], pts[0][1] - 14)
        txt.setData(0, {"type": "row_label", "row_id": row["id"]})
        self.scene.addItem(txt)
        self.row_items[row["id"]] = (item, txt)

    def manage_rows(self):
        from advanced_dialogs import ManageRowsDialog
        dlg = ManageRowsDialog(self.w, self)
        dlg.exec()

    def generate_plots_dialog(self, row: Optional[dict] = None):
        from advanced_dialogs import GeneratePlotsDialog
        rows = db.list_rows()
        if not rows:
            QMessageBox.information(self.w, "Rânduri", "Nu există niciun rând. Creează întâi un rând.")
            return
        dlg = GeneratePlotsDialog(self.w, self, rows, preselect=row)
        dlg.exec()

    def generate_plots_for_row(self, row: dict, count: int, width_px: float, depth_px: float,
                               gap_px: float, side: str, template: str,
                               start_number: int) -> int:
        """Generează + salvează locurile pe rând. Returnează câte au fost create."""
        polys = geo.generate_plots_along_baseline(
            row["baseline"], count, width_px, depth_px, gap_px, side)
        created = 0
        rand_no = row["order_index"]
        from grave_plot import GravePlotItem
        for i, poly in enumerate(polys):
            loc_no = start_number + i
            cod = geo.format_plot_code(template, row["sector_label"], rand_no, loc_no)
            existing = db.load_loc_by_cod(cod)
            if existing:
                continue
            self.w.actions.save_plot_to_db(cod, poly, "#0078FF80")
            db.save_loc_metadata(cod, {
                "sector": row["sector_label"], "rand": str(rand_no), "numar": str(loc_no),
            })
            qpoly = QPolygonF([QPointF(x, y) for x, y in poly])
            data = {"type": "loc_de_veci", "cod_loc": cod, "points": poly,
                    "plot_color": QColor(0, 120, 255, 128)}
            item = GravePlotItem(qpoly, data)
            self.scene.addItem(item)
            try:
                item.clicked.connect(self.w.on_plot_clicked)
            except Exception:
                pass
            # eticheta cod
            cx = sum(p[0] for p in poly) / len(poly)
            cy = sum(p[1] for p in poly) / len(poly)
            txt = QGraphicsSimpleTextItem(cod)
            txt.setFont(QFont("Arial", 8))
            txt.setBrush(QColor(0, 0, 0))
            txt.setZValue(20)
            br = txt.boundingRect()
            txt.setPos(cx - br.width() / 2, cy - br.height() / 2)
            txt.setData(0, {"type": "cod_loc_text", "cod_loc": cod})
            self.scene.addItem(txt)
            created += 1
        self.w.statusBar().showMessage(f"{created} locuri generate pe {row['name']} ({row['sector_label']})")
        self.refresh_area_labels()
        try:
            self.w.features.refresh_if_active()
        except Exception:
            pass
        return created

    # =====================================================================
    # SUPRAFEȚE (calcul automat)
    # =====================================================================
    def recompute_all_areas(self) -> int:
        """Recalculează suprafața fiecărui loc din DB (m² dacă e calibrat)."""
        ppm = self.get_ppm()
        conn = db.get_connection()
        try:
            rows = conn.execute("SELECT cod_loc, points FROM locuri_de_veci").fetchall()
        finally:
            conn.close()
        count = 0
        for r in rows:
            try:
                pts = json.loads(r["points"]) if r["points"] else []
            except Exception:
                pts = []
            if len(pts) < 3:
                continue
            area = geo.area_m2(pts, ppm) if ppm else geo.polygon_area(pts)
            if area is not None:
                db.save_loc_metadata(r["cod_loc"], {"suprafata": round(area, 2)})
                count += 1
        return count

    def refresh_area_labels(self):
        """Afișează suprafața sub fiecare poligon de loc, pe hartă."""
        for it in list(self.scene.items()):
            d = it.data(0)
            if isinstance(d, dict) and d.get("type") == "loc_area_text":
                try:
                    self.scene.removeItem(it)
                except Exception:
                    pass
        ppm = self.get_ppm()
        unit = "m²" if ppm else "px²"
        for it in list(self.scene.items()):
            d = it.data(0)
            if not (isinstance(d, dict) and d.get("type") == "loc_de_veci"):
                continue
            poly = it.polygon()
            pts = [(poly.at(i).x(), poly.at(i).y()) for i in range(poly.count())]
            if len(pts) < 3:
                continue
            area = geo.area_m2(pts, ppm) if ppm else geo.polygon_area(pts)
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            txt = QGraphicsSimpleTextItem(f"{area:.2f} {unit}")
            txt.setFont(QFont("Arial", 7))
            txt.setBrush(QColor(30, 64, 175))
            txt.setZValue(21)
            br = txt.boundingRect()
            txt.setPos(cx - br.width() / 2, cy + 4)
            txt.setData(0, {"type": "loc_area_text"})
            self.scene.addItem(txt)

    def _clear_temp_line(self):
        if getattr(self, "_temp_line", None) is not None:
            try:
                self.scene.removeItem(self._temp_line)
            except Exception:
                pass
            self._temp_line = None

    def _update_temp_line(self, p1, p2, color="#E11D48"):
        from PySide6.QtGui import QPen
        if getattr(self, "_temp_line", None) is None:
            self._temp_line = QGraphicsLineItem(p1[0], p1[1], p2[0], p2[1])
            pen = QPen(QColor(color), 3)
            pen.setCosmetic(True)
            pen.setStyle(Qt.DashLine)
            self._temp_line.setPen(pen)
            self._temp_line.setZValue(60)
            self._temp_line.setData(0, {"type": "temp"})
            self.scene.addItem(self._temp_line)
        else:
            self._temp_line.setLine(p1[0], p1[1], p2[0], p2[1])

    # =====================================================================
    # SNAP (Shift/Ctrl)
    # =====================================================================
    def snap_active(self) -> bool:
        mods = QApplication.keyboardModifiers()
        return bool(mods & (Qt.ShiftModifier | Qt.ControlModifier))

    def _snap_threshold(self) -> float:
        try:
            scale = self.w.map_view.transform().m11() or 1.0
        except Exception:
            scale = 1.0
        return 12.0 / max(abs(scale), 0.001)

    def snap_targets(self, exclude_item=None):
        vertices = []
        segments = []
        for it in self.scene.items():
            data = it.data(0)
            if not isinstance(data, dict):
                continue
            t = data.get("type")
            if t == "loc_de_veci" and it is not exclude_item:
                poly = it.polygon()
                for i in range(poly.count()):
                    vertices.append((poly.at(i).x(), poly.at(i).y()))
            elif t == "guide":
                ln = it.line()
                off = it.pos()
                segments.append(((ln.x1() + off.x(), ln.y1() + off.y()),
                                 (ln.x2() + off.x(), ln.y2() + off.y())))
        return vertices, segments

    def snap_xy(self, x: float, y: float, exclude_item=None):
        if not self.snap_active():
            return (x, y)
        vertices, segments = self.snap_targets(exclude_item)
        cfg = self.grid_cfg()
        grid = {"spacing": cfg["spacing"], "origin": cfg["origin"], "angle": cfg["angle"]} \
            if cfg["visible"] else None
        p, _ = geo.snap_point((x, y), vertices, segments, self._snap_threshold(), grid)
        return p

    def snap_qpoint(self, qpt, exclude_item=None):
        x, y = self.snap_xy(qpt.x(), qpt.y(), exclude_item)
        return QPointF(x, y)

    # =====================================================================
    # EVENIMENTE (moduri avansate)
    # =====================================================================
    def handle_event(self, event, pos) -> bool:
        from PySide6.QtCore import QEvent
        mode = self.w.modes.current_mode

        if mode == "calibrate":
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._calib_pts.append((pos.x(), pos.y()))
                if len(self._calib_pts) == 2:
                    self._finish_calibrate()
                return True
            if event.type() == QEvent.MouseMove and len(self._calib_pts) == 1:
                self._update_temp_line(self._calib_pts[0], (pos.x(), pos.y()), "#E11D48")
                return True
            return False

        if mode in ("draw_guide_h", "draw_guide_v"):
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                rect = self._scene_bounds()
                if mode == "draw_guide_h":
                    pts = [(rect.left(), pos.y()), (rect.right(), pos.y())]
                else:
                    pts = [(pos.x(), rect.top()), (pos.x(), rect.bottom())]
                self.add_guide("h" if mode == "draw_guide_h" else "v", pts)
                self.w.statusBar().showMessage("Ghidaj adăugat")
                return True
            return False

        if mode == "draw_guide_line":
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._calib_pts.append((pos.x(), pos.y()))
                if len(self._calib_pts) == 2:
                    self._clear_temp_line()
                    self.add_guide("line", list(self._calib_pts))
                    self._calib_pts = []
                    self.w.statusBar().showMessage("Ghidaj linie adăugat")
                return True
            if event.type() == QEvent.MouseMove and len(self._calib_pts) == 1:
                self._update_temp_line(self._calib_pts[0], (pos.x(), pos.y()), GUIDE_COLOR)
                return True
            return False

        if mode == "ruler":
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._calib_pts.append((pos.x(), pos.y()))
                if len(self._calib_pts) == 2:
                    txt = self._ruler_text(self._calib_pts[0], self._calib_pts[1])
                    self.w.statusBar().showMessage("📏 " + txt)
                    self._calib_pts = []  # gata de o nouă măsurare
                return True
            if event.type() == QEvent.MouseMove and len(self._calib_pts) == 1:
                self._update_temp_line(self._calib_pts[0], (pos.x(), pos.y()), "#7C3AED")
                self.w.statusBar().showMessage(
                    "📏 " + self._ruler_text(self._calib_pts[0], (pos.x(), pos.y())))
                return True
            return False

        return False

    def _finish_calibrate(self):
        if len(self._calib_pts) < 2:
            self._clear_temp_line()
            self.w.set_tool_mode("select")
            return
        p1, p2 = self._calib_pts[0], self._calib_pts[1]
        self._calib_pts = []
        meters, ok = QInputDialog.getDouble(
            self.w, "Calibrare scară",
            "Distanța reală între cele 2 puncte (metri):", 1.0, 0.001, 100000.0, 3)
        self._clear_temp_line()
        if not ok:
            self.w.set_tool_mode("select")
            return
        ppm = geo.px_per_meter(p1, p2, meters)
        if ppm:
            self.set_ppm(ppm)
            n = self.recompute_all_areas()
            self.refresh_area_labels()
            try:
                self.w.features.refresh_if_active()
            except Exception:
                pass
            self.w.statusBar().showMessage(
                f"Scară calibrată: {ppm:.2f} px/m. Suprafețe recalculate pentru {n} locuri.")
        self.w.set_tool_mode("select")
