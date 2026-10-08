# advanced_dialogs.py
"""Dialoguri pentru editorul avansat: gestionare rânduri + generare locuri."""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QSpinBox, QDoubleSpinBox, QComboBox,
    QLineEdit, QMessageBox, QGroupBox, QColorDialog,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

import database as db
import geometry_utils as geo


class ManageRowsDialog(QDialog):
    """Listă rânduri: reordonare, inserare rând între altele, ștergere, generare."""
    def __init__(self, parent, adv):
        super().__init__(parent)
        self.adv = adv
        self.setWindowTitle("Gestionează rânduri")
        self.resize(460, 420)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Rânduri definite (ordonate). Poți insera un rând între altele două."))

        self.list = QListWidget()
        self.list.setObjectName("rows-list")
        layout.addWidget(self.list)

        row_btns = QHBoxLayout()
        self.btn_up = QPushButton("↑ Sus")
        self.btn_down = QPushButton("↓ Jos")
        self.btn_insert = QPushButton("➕ Inserează rând între")
        self.btn_delete = QPushButton("🗑 Șterge")
        self.btn_generate = QPushButton("▦ Generează locuri")
        for b in (self.btn_up, self.btn_down, self.btn_insert, self.btn_delete, self.btn_generate):
            row_btns.addWidget(b)
        layout.addLayout(row_btns)

        close = QPushButton("Închide")
        layout.addWidget(close)
        self.setLayout(layout)

        self.btn_up.clicked.connect(lambda: self._move(-1))
        self.btn_down.clicked.connect(lambda: self._move(1))
        self.btn_insert.clicked.connect(self._insert_between)
        self.btn_delete.clicked.connect(self._delete)
        self.btn_generate.clicked.connect(self._generate)
        close.clicked.connect(self.accept)

        self._reload()

    def _reload(self):
        self.list.clear()
        self.rows = db.list_rows()
        for r in self.rows:
            n = len(r.get("baseline") or [])
            it = QListWidgetItem(
                f"[{r['order_index']}] {r['sector_label']} · {r['name']}  "
                f"(l={r['width']:.0f} a={r['depth']:.0f}, {n} pct)")
            it.setData(Qt.UserRole, r["id"])
            self.list.addItem(it)

    def _selected_row(self):
        it = self.list.currentItem()
        if not it:
            return None
        rid = it.data(Qt.UserRole)
        return next((r for r in self.rows if r["id"] == rid), None)

    def _move(self, delta):
        row = self._selected_row()
        if not row:
            return
        same = [r for r in self.rows if r["sector_label"] == row["sector_label"]]
        same.sort(key=lambda r: r["order_index"])
        idx = next((i for i, r in enumerate(same) if r["id"] == row["id"]), -1)
        j = idx + delta
        if idx < 0 or j < 0 or j >= len(same):
            return
        a, b = same[idx], same[j]
        db.update_row(a["id"], order_index=b["order_index"])
        db.update_row(b["id"], order_index=a["order_index"])
        self.adv.load_rows()
        self._reload()

    def _insert_between(self):
        row = self._selected_row()
        order = (row["order_index"] + 1) if row else 1
        self.accept()
        # pornește desenarea unui rând nou care se inserează după rândul selectat
        self.adv.start_draw_row(insert_order=order)

    def _delete(self):
        row = self._selected_row()
        if not row:
            return
        if QMessageBox.question(self, "Ștergere", f"Ștergi {row['name']} ({row['sector_label']})?",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        db.delete_row(row["id"])
        self.adv.load_rows()
        self._reload()

    def _generate(self):
        row = self._selected_row()
        if not row:
            QMessageBox.information(self, "Rând", "Selectează un rând.")
            return
        self.accept()
        self.adv.generate_plots_dialog(row=row)


class GeneratePlotsDialog(QDialog):
    """Generează locuri de-a lungul unui rând, cu previzualizare cod (A-01-01)."""
    def __init__(self, parent, adv, rows, preselect=None):
        super().__init__(parent)
        self.adv = adv
        self.rows = rows
        self.setWindowTitle("Generează locuri pe rând")
        self.resize(440, 420)

        unit = adv.unit_label()
        ppm = adv.get_ppm()

        layout = QVBoxLayout()

        form = QFormLayout()
        self.cmb_row = QComboBox()
        for r in rows:
            self.cmb_row.addItem(f"{r['sector_label']} · {r['name']} (poz {r['order_index']})", r["id"])
        if preselect:
            i = self.cmb_row.findData(preselect["id"])
            if i >= 0:
                self.cmb_row.setCurrentIndex(i)
        form.addRow("Rând:", self.cmb_row)

        self.spin_count = QSpinBox()
        self.spin_count.setRange(1, 1000)
        self.spin_count.setValue(10)
        form.addRow("Număr locuri:", self.spin_count)

        self.spin_width = QDoubleSpinBox()
        self.spin_width.setRange(0.1, 100000.0)
        self.spin_width.setDecimals(2)
        self.spin_width.setValue(2.0 if unit == "m" else 30.0)
        form.addRow(f"Lățime ({unit}):", self.spin_width)

        self.spin_depth = QDoubleSpinBox()
        self.spin_depth.setRange(0.1, 100000.0)
        self.spin_depth.setDecimals(2)
        self.spin_depth.setValue(2.5 if unit == "m" else 40.0)
        form.addRow(f"Adâncime ({unit}):", self.spin_depth)

        self.spin_gap = QDoubleSpinBox()
        self.spin_gap.setRange(0.0, 100000.0)
        self.spin_gap.setDecimals(2)
        self.spin_gap.setValue(0.2 if unit == "m" else 4.0)
        form.addRow(f"Spațiu între ({unit}):", self.spin_gap)

        self.cmb_side = QComboBox()
        self.cmb_side.addItems(["center", "left", "right"])
        form.addRow("Partea față de linie:", self.cmb_side)

        self.spin_start = QSpinBox()
        self.spin_start.setRange(1, 100000)
        self.spin_start.setValue(1)
        form.addRow("Primul număr loc:", self.spin_start)

        self.txt_template = QLineEdit(geo.DEFAULT_CODE_TEMPLATE)
        form.addRow("Șablon cod ({S},{R},{N}):", self.txt_template)

        layout.addLayout(form)

        prev = QGroupBox("Previzualizare cod")
        pv = QVBoxLayout()
        self.lbl_preview = QLabel()
        pv.addWidget(self.lbl_preview)
        prev.setLayout(pv)
        layout.addWidget(prev)

        if not ppm:
            layout.addWidget(QLabel("ℹ Scara nu e calibrată — valorile sunt în pixeli."))

        btns = QHBoxLayout()
        self.btn_ok = QPushButton("Generează")
        self.btn_cancel = QPushButton("Anulează")
        btns.addWidget(self.btn_ok)
        btns.addWidget(self.btn_cancel)
        layout.addLayout(btns)
        self.setLayout(layout)

        self.btn_ok.clicked.connect(self._generate)
        self.btn_cancel.clicked.connect(self.reject)
        for w in (self.cmb_row, self.spin_count, self.spin_start, self.txt_template):
            if hasattr(w, "currentIndexChanged"):
                w.currentIndexChanged.connect(self._update_preview)
            if hasattr(w, "valueChanged"):
                w.valueChanged.connect(self._update_preview)
            if hasattr(w, "textChanged"):
                w.textChanged.connect(self._update_preview)
        self._update_preview()

    def _current_row(self):
        rid = self.cmb_row.currentData()
        return next((r for r in self.rows if r["id"] == rid), None)

    def _update_preview(self):
        row = self._current_row()
        if not row:
            self.lbl_preview.setText("—")
            return
        tpl = self.txt_template.text()
        start = self.spin_start.value()
        cnt = self.spin_count.value()
        first = geo.format_plot_code(tpl, row["sector_label"], row["order_index"], start)
        last = geo.format_plot_code(tpl, row["sector_label"], row["order_index"], start + cnt - 1)
        self.lbl_preview.setText(f"{first}  …  {last}   ({cnt} locuri)")

    def _generate(self):
        row = self._current_row()
        if not row:
            return
        w = self.adv.to_px(self.spin_width.value())
        d = self.adv.to_px(self.spin_depth.value())
        gap = self.adv.to_px(self.spin_gap.value())
        created = self.adv.generate_plots_for_row(
            row, self.spin_count.value(), w, d, gap,
            self.cmb_side.currentText(), self.txt_template.text(), self.spin_start.value())
        QMessageBox.information(self, "Generare", f"{created} locuri create.")
        self.accept()


class _ColorButton(QPushButton):
    def __init__(self, color_hex):
        super().__init__()
        self.color = QColor(color_hex)
        self.setFixedWidth(90)
        self._refresh()
        self.clicked.connect(self._pick)

    def _refresh(self):
        self.setText(self.color.name())
        self.setStyleSheet(
            f"background-color: {self.color.name()}; color: white; font-weight: 600;")

    def _pick(self):
        c = QColorDialog.getColor(self.color, self, "Alege culoare")
        if c.isValid():
            self.color = c
            self._refresh()


class LineStyleDialog(QDialog):
    """Configurare grosime / culoare / stil pentru rânduri, ghidaje și grid."""
    STYLES = ["solid", "dash", "dot", "dashdot"]

    def __init__(self, parent, adv):
        super().__init__(parent)
        self.adv = adv
        self.setWindowTitle("Stil linii (grosime / culoare / format)")
        self.resize(430, 320)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Personalizează cum arată liniile pe hartă."))

        self.rows_widgets = {}
        for kind, title in (("row", "Rânduri"), ("guide", "Ghidaje"), ("grid", "Grid")):
            st = adv.line_style(kind)
            box = QGroupBox(title)
            form = QFormLayout()

            color_btn = _ColorButton(st["color"])
            form.addRow("Culoare:", color_btn)

            width = QSpinBox()
            width.setRange(1, 20)
            width.setValue(st["width"])
            form.addRow("Grosime (px):", width)

            style = QComboBox()
            style.addItems(self.STYLES)
            idx = style.findText(st["style"])
            if idx >= 0:
                style.setCurrentIndex(idx)
            form.addRow("Format:", style)

            box.setLayout(form)
            layout.addWidget(box)
            self.rows_widgets[kind] = (color_btn, width, style)

        btns = QHBoxLayout()
        ok = QPushButton("Aplică")
        cancel = QPushButton("Anulează")
        btns.addWidget(ok)
        btns.addWidget(cancel)
        layout.addLayout(btns)
        self.setLayout(layout)

        ok.clicked.connect(self._apply)
        cancel.clicked.connect(self.reject)

    def _apply(self):
        for kind, (color_btn, width, style) in self.rows_widgets.items():
            self.adv.set_line_style(kind, color_btn.color.name(),
                                    width.value(), style.currentText())
        self.accept()
