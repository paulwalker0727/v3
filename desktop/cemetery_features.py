# cemetery_features.py — rezumat, colorare status + legendă, căutare pe hartă
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QBrush, QPen, QFont
from PySide6.QtWidgets import (
    QLabel, QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QHeaderView, QMessageBox,
)

import database as db


STATUS_COLORS = {
    "liber":    QColor(34, 197, 94, 130),    # verde
    "ocupat":   QColor(148, 163, 184, 160),  # gri
    "rezervat": QColor(251, 191, 36, 150),   # galben
    "expirat":  QColor(239, 68, 68, 160),    # roșu
}
STATUS_LABEL = {
    "liber": "Liber", "ocupat": "Ocupat", "rezervat": "Rezervat", "expirat": "Concesiune expirată",
}


def plot_status(loc: dict, expired_cods: set) -> str:
    cod = (loc.get("cod_loc") or "")
    if cod in expired_cods:
        return "expirat"
    disp = (str(loc.get("disponibilitate") or "")).lower()
    stare = (str(loc.get("stare") or "")).lower()
    ocupat = loc.get("ocupat")
    if "rezerv" in disp or "rezerv" in stare:
        return "rezervat"
    if ocupat in (1, "1", True) or "ocupat" in disp or "ocupat" in stare or "indisponibil" in disp:
        return "ocupat"
    return "liber"


class CemeteryFeatures:
    def __init__(self, window):
        self.w = window
        self.coloring_active = False
        self.legend = None
        self._orig_brushes = {}

    @property
    def scene(self):
        return self.w.map_view._scene

    # =====================================================================
    # COLORARE DUPĂ STATUS + LEGENDĂ
    # =====================================================================
    def _expired_cods(self) -> set:
        cods = set()
        try:
            for c in db.expiring_contracts(0):  # deja expirate
                cod = c.get("cod_loc")
                if cod:
                    cods.add(cod)
        except Exception:
            pass
        return cods

    def toggle_status_coloring(self):
        if self.coloring_active:
            self._restore_colors()
            self.coloring_active = False
            self._hide_legend()
            self.w.statusBar().showMessage("Colorare status dezactivată")
        else:
            self.apply_status_coloring()
            self.coloring_active = True
            self._show_legend()
            self.w.statusBar().showMessage("Colorare după status activată")

    def apply_status_coloring(self):
        expired = self._expired_cods()
        loc_map = {l["cod_loc"]: l for l in db.list_locuri()}
        for it in self.scene.items():
            d = it.data(0)
            if not (isinstance(d, dict) and d.get("type") == "loc_de_veci"):
                continue
            cod = d.get("cod_loc")
            if cod not in self._orig_brushes:
                self._orig_brushes[cod] = it.brush().color()
            loc = loc_map.get(cod, {"cod_loc": cod})
            status = plot_status(loc, expired)
            col = STATUS_COLORS.get(status, STATUS_COLORS["liber"])
            it.setBrush(QBrush(col))
            it.setPen(QPen(col.darker(), 2))

    def _restore_colors(self):
        for it in self.scene.items():
            d = it.data(0)
            if not (isinstance(d, dict) and d.get("type") == "loc_de_veci"):
                continue
            cod = d.get("cod_loc")
            col = self._orig_brushes.get(cod) or QColor(0, 120, 255, 80)
            it.setBrush(QBrush(col))
            it.setPen(QPen(col.darker(), 2))

    def refresh_if_active(self):
        if self.coloring_active:
            self.apply_status_coloring()

    def _show_legend(self):
        if self.legend is None:
            self.legend = QLabel(self.w.map_view)
            self.legend.setStyleSheet(
                "background: rgba(255,255,255,0.92); border:1px solid #cbd5e1;"
                "border-radius:6px; padding:8px; font-size:12px;")
        rows = []
        for key, lbl in STATUS_LABEL.items():
            c = STATUS_COLORS[key]
            rows.append(
                f"<div style='margin:2px 0;'>"
                f"<span style='display:inline-block;width:12px;height:12px;"
                f"background:{c.name()};border:1px solid #555;'></span> {lbl}</div>")
        self.legend.setText("<b>Legendă status</b>" + "".join(rows))
        self.legend.adjustSize()
        self.legend.move(12, 12)
        self.legend.show()
        self.legend.raise_()

    def _hide_legend(self):
        if self.legend is not None:
            self.legend.hide()

    # =====================================================================
    # CĂUTARE LOC PE HARTĂ
    # =====================================================================
    def search_plot(self, text: str):
        text = (text or "").strip().lower()
        if not text:
            return
        target = None
        for it in self.scene.items():
            d = it.data(0)
            if isinstance(d, dict) and d.get("type") == "loc_de_veci":
                cod = str(d.get("cod_loc") or "").lower()
                if cod == text or text in cod:
                    target = it
                    if cod == text:
                        break
        if target is None:
            self.w.statusBar().showMessage(f"Locul '{text}' nu a fost găsit pe hartă")
            QMessageBox.information(self.w, "Căutare", f"Nu am găsit locul '{text}'.")
            return
        # centrează + zoom + highlight temporar
        self.w.map_view.centerOn(target)
        old_pen = target.pen()
        target.setPen(QPen(QColor("#DC2626"), 4))
        target.setSelected(True)
        cod = target.data(0).get("cod_loc")
        self.w.statusBar().showMessage(f"Loc găsit: {cod}")
        QTimer.singleShot(2000, lambda: self._reset_highlight(target, old_pen))

    def _reset_highlight(self, item, pen):
        try:
            if not self.coloring_active:
                item.setPen(pen)
            item.setSelected(False)
        except Exception:
            pass

    # =====================================================================
    # REZUMAT CIMITIR
    # =====================================================================
    def show_summary(self):
        locuri = db.list_locuri()
        expired = self._expired_cods()
        total = len(locuri)
        counts = {"liber": 0, "ocupat": 0, "rezervat": 0, "expirat": 0}
        area_total = 0.0
        per_sector = {}
        for l in locuri:
            st = plot_status(l, expired)
            counts[st] = counts.get(st, 0) + 1
            try:
                area_total += float(l.get("suprafata") or 0)
            except Exception:
                pass
            sec = l.get("sector") or "—"
            ps = per_sector.setdefault(sec, {"total": 0, "liber": 0, "ocupat": 0,
                                             "rezervat": 0, "expirat": 0, "supr": 0.0})
            ps["total"] += 1
            ps[st] += 1
            try:
                ps["supr"] += float(l.get("suprafata") or 0)
            except Exception:
                pass

        dlg = QDialog(self.w)
        dlg.setWindowTitle("Rezumat cimitir")
        dlg.resize(640, 480)
        lay = QVBoxLayout()

        head = QLabel(
            f"<h3>Rezumat cimitir</h3>"
            f"Total locuri: <b>{total}</b>  |  "
            f"<span style='color:#16a34a'>Libere: {counts['liber']}</span>  |  "
            f"<span style='color:#64748b'>Ocupate: {counts['ocupat']}</span>  |  "
            f"<span style='color:#d97706'>Rezervate: {counts['rezervat']}</span>  |  "
            f"<span style='color:#dc2626'>Expirate: {counts['expirat']}</span><br>"
            f"Suprafață totală: <b>{area_total:.2f}</b> (m²/px² în funcție de calibrare)")
        lay.addWidget(head)

        table = QTableWidget()
        table.setColumnCount(7)
        table.setHorizontalHeaderLabels(
            ["Sector", "Total", "Libere", "Ocupate", "Rezervate", "Expirate", "Suprafață"])
        table.setRowCount(len(per_sector))
        for i, (sec, ps) in enumerate(sorted(per_sector.items())):
            vals = [sec, ps["total"], ps["liber"], ps["ocupat"],
                    ps["rezervat"], ps["expirat"], f"{ps['supr']:.2f}"]
            for j, v in enumerate(vals):
                table.setItem(i, j, QTableWidgetItem(str(v)))
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setObjectName("summary-table")
        lay.addWidget(table)

        btn = QPushButton("Închide")
        btn.clicked.connect(dlg.accept)
        lay.addWidget(btn)
        dlg.setLayout(lay)
        dlg.exec()
