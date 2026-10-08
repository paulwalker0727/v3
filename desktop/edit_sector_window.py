from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QColorDialog, QSlider
)
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QPolygonF, QPen, QBrush, QColor

from point_handle_item import PointHandleItem


class EditSectorWindow(QDialog):
    def __init__(self, parent, sector_item, sector_data: dict):
        super().__init__(parent)
        self.setWindowTitle("Editare sector")
        self.resize(360, 210)

        # NON-MODAL – trebuie să poți muta punctele pe hartă
        self.setModal(False)

        self.sector_item = sector_item
        self.view = parent.map_view
        self.scene = self.view._scene
        self.sector_data = sector_data if isinstance(sector_data, dict) else {}

        # --- normalizăm datele sectorului (defensiv) ---
        sc = self.sector_data.get("sector_color")
        if not isinstance(sc, QColor) or not sc.isValid():
            sc = self.sector_item.brush().color()
        self.sector_data["sector_color"] = QColor(sc)

        if "points" not in self.sector_data or not self.sector_data["points"]:
            poly = self.sector_item.polygon()
            self.sector_data["points"] = [
                (poly.at(i).x(), poly.at(i).y()) for i in range(poly.count())
            ]

        if "id" not in self.sector_data:
            data0 = self.sector_item.data(0)
            self.sector_data["id"] = data0.get("sector_id") if isinstance(data0, dict) else "sector"

        # salvăm starea inițială pentru anulare
        self.original_polygon: QPolygonF = QPolygonF(self.sector_item.polygon())
        self.original_color = QColor(self.sector_data["sector_color"])

        self.handles: list[PointHandleItem] = []

        layout = QVBoxLayout()
        layout.addWidget(QLabel(
            "Mută punctele direct pe hartă.\nPoți schimba și culoarea / opacitatea."
        ))

        # Buton schimbare culoare
        btn_color = QPushButton("Schimbă culoarea")
        btn_color.clicked.connect(self.change_color)
        layout.addWidget(btn_color)

        # Slider opacitate
        layout.addWidget(QLabel("Opacitate:"))
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(0, 255)
        self.opacity_slider.setValue(self.original_color.alpha())
        self.opacity_slider.valueChanged.connect(self.change_opacity)
        layout.addWidget(self.opacity_slider)

        # Butoane salvare / anulare
        btn_row = QHBoxLayout()
        self.btn_save = QPushButton("Salvează")
        self.btn_cancel = QPushButton("Anulează")
        btn_row.addWidget(self.btn_save)
        btn_row.addWidget(self.btn_cancel)
        layout.addLayout(btn_row)

        self.setLayout(layout)

        self.btn_save.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)

        # aplicăm culoarea curentă (asigură vizual consistent) + handle-uri
        self._apply_color()
        self._create_handles()

    # ----------------------------------------------------
    # CREARE HANDLERE PENTRU PUNCTE
    # ----------------------------------------------------
    def _create_handles(self):
        poly = self.sector_item.polygon()
        for i in range(poly.count()):
            p = poly.at(i)
            handle = PointHandleItem(i, self.sector_item.mapToScene(p), self.sector_item)
            handle.moved.connect(self._handle_moved)
            self.handles.append(handle)

    # ----------------------------------------------------
    # MUTAREA UNUI PUNCT
    # ----------------------------------------------------
    def _handle_moved(self, index: int, scene_pos: QPointF):
        adv = getattr(self.parent(), "adv", None)
        if adv is not None:
            try:
                scene_pos = adv.snap_qpoint(scene_pos, exclude_item=self.sector_item)
            except Exception:
                pass
        local_pos = self.sector_item.mapFromScene(scene_pos)
        poly = self.sector_item.polygon()

        if 0 <= index < poly.count():
            poly[index] = local_pos
            self.sector_item.setPolygon(poly)
            self.sector_data["points"] = [
                (poly.at(i).x(), poly.at(i).y()) for i in range(poly.count())
            ]

    # ----------------------------------------------------
    # SCHIMBARE CULOARE
    # ----------------------------------------------------
    def change_color(self):
        new_color = QColorDialog.getColor(self.sector_data["sector_color"], self, "Culoare sector")
        if not new_color.isValid():
            return

        # păstrăm opacitatea existentă
        alpha = self.sector_data["sector_color"].alpha()
        new_color.setAlpha(alpha)

        self.sector_data["sector_color"] = new_color
        self._apply_color()

    # ----------------------------------------------------
    # SCHIMBARE OPACITATE
    # ----------------------------------------------------
    def change_opacity(self, value: int):
        c = QColor(self.sector_data["sector_color"])
        c.setAlpha(value)
        self.sector_data["sector_color"] = c
        self._apply_color()

    # ----------------------------------------------------
    # APLICĂ CULOAREA PE SECTOR
    # ----------------------------------------------------
    def _apply_color(self):
        c = self.sector_data["sector_color"]
        self.sector_item.setBrush(QBrush(c))
        self.sector_item.setPen(QPen(c.darker(), 3))

        # sincronizăm și data(0) a item-ului
        data0 = self.sector_item.data(0)
        if isinstance(data0, dict):
            data0["sector_color"] = QColor(c)
            self.sector_item.setData(0, data0)

    # ----------------------------------------------------
    # SALVARE MODIFICĂRI
    # ----------------------------------------------------
    def accept(self):
        sector_id = self.sector_data["id"]
        points = self.sector_data["points"]
        color = self.sector_data["sector_color"].name(QColor.HexArgb)

        # sincronizăm data(0) a item-ului (formă + culoare)
        data0 = self.sector_item.data(0)
        if isinstance(data0, dict):
            data0["points"] = points
            data0["sector_color"] = QColor(self.sector_data["sector_color"])
            self.sector_item.setData(0, data0)

        # sincronizăm registrul de sectoare al ferestrei principale
        parent = self.parent()
        if hasattr(parent, "sectors") and sector_id in parent.sectors:
            parent.sectors[sector_id]["points"] = points
            parent.sectors[sector_id]["sector_color"] = QColor(self.sector_data["sector_color"])
            parent.sectors[sector_id]["item"] = self.sector_item

        try:
            parent.actions.save_sector_to_db(sector_id, points, color)
            parent.statusBar().showMessage(f"Sector {sector_id} salvat")
        except Exception as e:
            print("[EditSectorWindow] EROARE salvare:", e)

        self._cleanup()
        super().accept()

    # ----------------------------------------------------
    # ANULARE MODIFICĂRI
    # ----------------------------------------------------
    def reject(self):
        # revenim la forma inițială
        self.sector_item.setPolygon(self.original_polygon)

        # revenim la culoarea inițială
        self.sector_data["sector_color"] = QColor(self.original_color)
        self._apply_color()

        self._cleanup()
        super().reject()

    # ----------------------------------------------------
    # CURĂȚARE HANDLERE
    # ----------------------------------------------------
    def _cleanup(self):
        for h in self.handles:
            try:
                self.scene.removeItem(h)
            except Exception:
                pass
        self.handles.clear()
