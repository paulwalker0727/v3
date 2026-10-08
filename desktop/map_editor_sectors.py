# map_editor_sectors.py
from PySide6.QtWidgets import QColorDialog
from PySide6.QtGui import QPen, QColor


class SectorManager:
    def __init__(self, window):
        self.w = window

    # ----------------------------------------------------
    # CREARE SECTOR NOU  → pornește modul de desenare sector
    # ----------------------------------------------------
    def create_sector(self):
        """„Sector nou" = intră în modul desenare sector.

        După ce utilizatorul desenează poligonul (dublu-click / click pe primul
        punct), sectorul este creat automat de `finalize_sector`.
        """
        try:
            self.w.set_tool_mode("draw_sector")
            self.w.statusBar().showMessage(
                "Desenează SECTOR — click pentru puncte, dublu-click pentru finalizare"
            )
        except Exception:
            pass

    # ----------------------------------------------------
    # HELPER: găsește item-ul grafic al unui sector
    # ----------------------------------------------------
    def _find_sector_item(self, sid):
        try:
            for item in self.w.map_view._scene.items():
                data = item.data(0)
                if isinstance(data, dict) and data.get("type") == "sector_grafic" \
                        and data.get("sector_id") == sid:
                    return item
        except Exception:
            pass
        return None

    def _persist(self, sid, color: QColor):
        """Salvează culoarea/opacitatea curentă a sectorului în DB."""
        sector = self.w.sectors.get(sid)
        if not sector:
            return
        points = sector.get("points")
        if not points:
            item = sector.get("item") or self._find_sector_item(sid)
            if item is not None:
                poly = item.polygon()
                points = [(poly.at(i).x(), poly.at(i).y()) for i in range(poly.count())]
                sector["points"] = points
        if not points:
            return
        try:
            self.w.actions.save_sector_to_db(sid, points, color.name(QColor.HexArgb))
        except Exception:
            pass

    # ----------------------------------------------------
    # SCHIMBARE OPACITATE SECTOR
    # ----------------------------------------------------
    def set_sector_opacity(self, value):
        """Slider → schimbă opacitatea sectorului activ."""
        sid = self.w.active_sector_id
        if not sid or sid not in self.w.sectors:
            return

        sector = self.w.sectors[sid]

        color = QColor(sector["sector_color"])
        color.setAlpha(value)
        sector["sector_color"] = color

        item = sector.get("item") or self._find_sector_item(sid)
        if item is not None:
            item.setBrush(color)
            item.setPen(QPen(color.darker(), 2))
            data = item.data(0)
            if isinstance(data, dict):
                data["sector_color"] = QColor(color)
                item.setData(0, data)

        self._persist(sid, color)

    # ----------------------------------------------------
    # SCHIMBARE CULOARE SECTOR
    # ----------------------------------------------------
    def change_sector_color(self):
        """Color picker pentru schimbarea culorii sectorului activ."""
        sid = self.w.active_sector_id
        if not sid or sid not in self.w.sectors:
            try:
                self.w.statusBar().showMessage("Niciun sector selectat (click pe un sector)")
            except Exception:
                pass
            return

        current = QColor(self.w.sectors[sid]["sector_color"])
        new_color = QColorDialog.getColor(current, self.w, "Alege noua culoare a sectorului")
        if not new_color.isValid():
            return

        # păstrăm opacitatea existentă
        new_color.setAlpha(current.alpha())
        self.w.sectors[sid]["sector_color"] = new_color

        item = self.w.sectors[sid].get("item") or self._find_sector_item(sid)
        if item is not None:
            item.setBrush(new_color)
            item.setPen(QPen(new_color.darker(), 2))
            data = item.data(0)
            if isinstance(data, dict):
                data["sector_color"] = QColor(new_color)
                item.setData(0, data)

        self._persist(sid, new_color)

        try:
            self.w.statusBar().showMessage("Culoarea sectorului a fost actualizată")
        except Exception:
            pass
