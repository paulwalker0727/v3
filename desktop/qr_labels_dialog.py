"""
qr_labels_dialog.py — Dialog pentru generare QR code / etichete pentru locurile de veci.

Trei moduri de export:
    1. PNG individual (1 loc → 1 imagine PNG)
    2. Batch PNG (mai multe locuri → mai multe PNG-uri într-un folder)
    3. PDF etichete pe A4 (grid tipăribil, gata pentru coli adezive)

Selecție locuri:
    - Manual (checkbox per loc din listă filtrabilă)
    - După sector / stare
    - Toate locurile
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPixmap, QImage
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox,
    QCheckBox, QSpinBox, QFileDialog, QMessageBox, QGroupBox, QRadioButton,
    QButtonGroup, QListWidget, QListWidgetItem, QStackedWidget, QWidget, QFormLayout,
    QAbstractItemView, QSplitter,
)

import database as db
import pdf_reports as pr
import qr_labels as ql
from app_logging import get_logger

logger = get_logger(__name__)


class QrLabelsDialog(QDialog):
    """Dialog dedicat pentru QR-uri de locuri de veci — fără PDF de raport."""

    def __init__(self, parent=None, preselected_cod: Optional[str] = None):
        super().__init__(parent)
        self.setWindowTitle("QR code / Etichete locuri de veci")
        self.resize(920, 620)

        root = QHBoxLayout(self)

        # =================== LEFT: SELECȚIE LOCURI ===================
        left = QVBoxLayout()
        left_box = QGroupBox("1. Alege locurile")
        lb_lay = QVBoxLayout(left_box)

        # filtre
        filter_row = QHBoxLayout()
        self.txt_filter = QLineEdit()
        self.txt_filter.setPlaceholderText("🔍  Filtrează după cod, sector, parcelă...")
        self.txt_filter.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.txt_filter)
        self.cmb_sector = QComboBox()
        self.cmb_sector.setMinimumWidth(120)
        self.cmb_sector.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self.cmb_sector)
        lb_lay.addLayout(filter_row)

        # listă cu checkboxes
        self.list_locuri = QListWidget()
        self.list_locuri.setSelectionMode(QAbstractItemView.NoSelection)
        lb_lay.addWidget(self.list_locuri, 1)

        # butoane selecție
        sel_row = QHBoxLayout()
        btn_all = QPushButton("Bifează tot filtrul")
        btn_all.clicked.connect(lambda: self._toggle_visible(True))
        btn_none = QPushButton("Debifează tot")
        btn_none.clicked.connect(lambda: self._toggle_visible(False))
        self.lbl_count = QLabel("0 bifate")
        self.lbl_count.setStyleSheet("color: #0e5aa7; font-weight: 600;")
        sel_row.addWidget(btn_all)
        sel_row.addWidget(btn_none)
        sel_row.addStretch()
        sel_row.addWidget(self.lbl_count)
        lb_lay.addLayout(sel_row)

        left.addWidget(left_box, 1)
        root.addLayout(left, 2)

        # =================== RIGHT: OPȚIUNI OUTPUT ===================
        right = QVBoxLayout()
        right.setSpacing(12)

        # tip export
        out_box = QGroupBox("2. Format export")
        out_lay = QVBoxLayout(out_box)
        self.rb_png_single = QRadioButton("🖼  PNG individual (doar 1 loc bifat)")
        self.rb_png_batch = QRadioButton("📁  Batch PNG (câte 1 PNG per loc, într-un folder)")
        self.rb_pdf_labels = QRadioButton("📄  PDF etichete A4 (grid tipăribil, ideal pentru coli adezive)")
        self.rb_pdf_labels.setChecked(True)
        self.grp_mode = QButtonGroup(self)
        for i, rb in enumerate([self.rb_png_single, self.rb_png_batch, self.rb_pdf_labels]):
            self.grp_mode.addButton(rb, i)
            out_lay.addWidget(rb)
        self.grp_mode.idClicked.connect(self._on_mode_change)
        right.addWidget(out_box)

        # opțiuni per mode
        self.opt_stack = QStackedWidget()

        # --- opțiuni PNG single ---
        w1 = QWidget(); lay1 = QFormLayout(w1)
        self.chk_only_qr = QCheckBox("Doar QR (fără text, fără cadru)")
        self.chk_only_qr.setToolTip(
            "Bifat: doar codul QR ca imagine pătrată.\n"
            "Nebifat: QR + codul locului + sector/parcelă dedesubt."
        )
        self.spin_size = QSpinBox()
        self.spin_size.setRange(200, 2000)
        self.spin_size.setValue(500)
        self.spin_size.setSuffix(" px")
        lay1.addRow(self.chk_only_qr)
        lay1.addRow("Dimensiune imagine:", self.spin_size)
        self.opt_stack.addWidget(w1)

        # --- opțiuni PNG batch ---
        w2 = QWidget(); lay2 = QFormLayout(w2)
        self.chk_only_qr_b = QCheckBox("Doar QR (fără text)")
        self.spin_size_b = QSpinBox()
        self.spin_size_b.setRange(200, 2000)
        self.spin_size_b.setValue(500)
        self.spin_size_b.setSuffix(" px")
        lay2.addRow(self.chk_only_qr_b)
        lay2.addRow("Dimensiune per PNG:", self.spin_size_b)
        self.opt_stack.addWidget(w2)

        # --- opțiuni PDF etichete ---
        w3 = QWidget(); lay3 = QFormLayout(w3)
        self.cmb_preset = QComboBox()
        for k in ql.LABEL_PRESETS:
            self.cmb_preset.addItem(k)
        # default: 24 etichete / pagină
        idx = self.cmb_preset.findText("3x8 (24/pag, 70×36)")
        if idx >= 0:
            self.cmb_preset.setCurrentIndex(idx)
        self.chk_include_text = QCheckBox("Include text (cod loc + sector/parcelă)")
        self.chk_include_text.setChecked(True)
        self.chk_border = QCheckBox("Cadru în jurul fiecărei etichete (pentru tăiat manual)")
        self.chk_border.setChecked(True)
        lay3.addRow("Aspect etichete:", self.cmb_preset)
        lay3.addRow(self.chk_include_text)
        lay3.addRow(self.chk_border)
        self.opt_stack.addWidget(w3)

        right.addWidget(self.opt_stack)

        # URL info
        self.lbl_url = QLabel()
        self.lbl_url.setStyleSheet(
            "background-color: rgba(14,90,167,0.08); color: #0e5aa7; "
            "padding: 8px 12px; border-radius: 6px; font-family: monospace; font-size: 9pt;")
        self.lbl_url.setWordWrap(True)
        self._update_url_hint()

        url_row = QHBoxLayout()
        url_row.addWidget(self.lbl_url, 1)
        btn_url_cfg = QPushButton("⚙  Setări URL...")
        btn_url_cfg.clicked.connect(self._open_url_settings)
        url_row.addWidget(btn_url_cfg)
        right.addLayout(url_row)

        right.addStretch()

        # butoane finale
        btn_row = QHBoxLayout()
        btn_close = QPushButton("Închide")
        btn_close.clicked.connect(self.reject)
        self.btn_go = QPushButton("🚀  Generează")
        self.btn_go.setMinimumHeight(38)
        self.btn_go.setDefault(True)
        self.btn_go.setStyleSheet(
            "background-color: #0e5aa7; color: white; font-weight: 600; "
            "padding: 0 24px; border-radius: 6px;")
        self.btn_go.clicked.connect(self._generate)
        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        btn_row.addWidget(self.btn_go)
        right.addLayout(btn_row)

        root.addLayout(right, 3)

        # populare inițială
        self._all_locuri: List[dict] = []
        self._load_locuri()
        self._on_mode_change(2)  # default pe PDF etichete

        # preselecție (dacă e apelat cu un loc anume)
        if preselected_cod:
            for i in range(self.list_locuri.count()):
                it = self.list_locuri.item(i)
                if it.data(Qt.UserRole) == preselected_cod:
                    it.setCheckState(Qt.Checked)
                    self.list_locuri.scrollToItem(it)
                    break
            self._update_count()

    # ------------------------------------------------------------------
    def _load_locuri(self):
        try:
            self._all_locuri = db.list_locuri() or []
        except Exception:
            logger.exception("Nu am putut încărca locurile")
            self._all_locuri = []

        sectors = sorted({(l.get("sector") or "").strip()
                          for l in self._all_locuri if (l.get("sector") or "").strip()})
        self.cmb_sector.clear()
        self.cmb_sector.addItem("Toate sectoarele", "")
        for s in sectors:
            self.cmb_sector.addItem(s, s)

        self._render_list()

    def _render_list(self):
        self.list_locuri.blockSignals(True)
        self.list_locuri.clear()
        for loc in self._all_locuri:
            cod = loc.get("cod_loc", "")
            sect = loc.get("sector", "") or "-"
            parc = loc.get("parcela", "") or "-"
            stare = loc.get("stare", "") or "-"
            text = f"  {cod}   ·   Sect. {sect}, Parc. {parc}   ·   {stare}"
            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, cod)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked)
            self.list_locuri.addItem(it)
        self.list_locuri.blockSignals(False)
        self.list_locuri.itemChanged.connect(lambda _: self._update_count())
        self._apply_filter()

    def _apply_filter(self):
        text = self.txt_filter.text().strip().lower()
        sector = self.cmb_sector.currentData() or ""
        visible = 0
        for i in range(self.list_locuri.count()):
            it = self.list_locuri.item(i)
            loc = next((l for l in self._all_locuri
                        if l.get("cod_loc") == it.data(Qt.UserRole)), {})
            match = True
            if sector and (loc.get("sector") or "") != sector:
                match = False
            if match and text:
                blob = " ".join(str(loc.get(k, "") or "") for k in
                                ("cod_loc", "sector", "parcela", "rand", "numar", "stare"))
                if text not in blob.lower():
                    match = False
            it.setHidden(not match)
            if match:
                visible += 1
        self._update_count(visible_count=visible)

    def _toggle_visible(self, checked: bool):
        state = Qt.Checked if checked else Qt.Unchecked
        for i in range(self.list_locuri.count()):
            it = self.list_locuri.item(i)
            if not it.isHidden():
                it.setCheckState(state)
        self._update_count()

    def _update_count(self, visible_count: Optional[int] = None):
        checked = sum(1 for i in range(self.list_locuri.count())
                      if self.list_locuri.item(i).checkState() == Qt.Checked)
        if visible_count is None:
            visible_count = sum(1 for i in range(self.list_locuri.count())
                                if not self.list_locuri.item(i).isHidden())
        self.lbl_count.setText(f"{checked} bifate  ·  {visible_count} vizibile")

    def _checked_codes(self) -> List[str]:
        return [self.list_locuri.item(i).data(Qt.UserRole)
                for i in range(self.list_locuri.count())
                if self.list_locuri.item(i).checkState() == Qt.Checked]

    # ------------------------------------------------------------------
    def _on_mode_change(self, idx: int):
        self.opt_stack.setCurrentIndex(idx)

    def _update_url_hint(self):
        base = pr.get_qr_base_url()
        self.lbl_url.setText(
            f"URL QR (setat în settings):\n"
            f"{base}/loc/<cod_loc>\n\n"
            f"Exemplu: {base}/loc/A-01-05"
        )

    def _open_url_settings(self):
        from rapoarte_dialog import QrSettingsDialog
        dlg = QrSettingsDialog(self)
        if dlg.exec():
            self._update_url_hint()

    # ------------------------------------------------------------------
    def _generate(self):
        codes = self._checked_codes()
        if not codes:
            QMessageBox.warning(self, "Selecție goală",
                                "Bifează cel puțin un loc de veci.")
            return

        mode = self.grp_mode.checkedId()

        try:
            if mode == 0:  # PNG single
                if len(codes) > 1:
                    QMessageBox.warning(
                        self, "Prea multe locuri",
                        f"Modul „PNG individual\" acceptă un singur loc.\n"
                        f"Ai bifat {len(codes)}. Debifează sau alege „Batch PNG\".")
                    return
                default_name = f"qr_{codes[0].replace('/', '_')}.png"
                path, _ = QFileDialog.getSaveFileName(
                    self, "Salvează PNG", default_name, "PNG (*.png)")
                if not path:
                    return
                if not path.lower().endswith(".png"):
                    path += ".png"
                side = self.spin_size.value()
                ql.generate_qr_png(
                    codes[0], path,
                    label_size_px=(side, side if self.chk_only_qr.isChecked() else int(side * 1.25)),
                    include_text=not self.chk_only_qr.isChecked(),
                    only_qr=self.chk_only_qr.isChecked(),
                )
                self._notify_ok(path, one_file=True)

            elif mode == 1:  # batch PNG
                out_dir = QFileDialog.getExistingDirectory(
                    self, "Alege folder pentru PNG-uri")
                if not out_dir:
                    return
                side = self.spin_size_b.value()
                only = self.chk_only_qr_b.isChecked()
                paths = ql.generate_qr_batch_pngs(
                    codes, out_dir,
                    label_size_px=(side, side if only else int(side * 1.25)),
                    include_text=not only,
                    only_qr=only,
                )
                QMessageBox.information(
                    self, "Gata",
                    f"Am generat {len(paths)} PNG-uri în:\n{out_dir}")
                self._open_folder(out_dir)

            else:  # PDF etichete
                default_name = f"etichete_qr_{len(codes)}locuri.pdf"
                path, _ = QFileDialog.getSaveFileName(
                    self, "Salvează PDF etichete", default_name, "PDF (*.pdf)")
                if not path:
                    return
                if not path.lower().endswith(".pdf"):
                    path += ".pdf"
                ql.generate_qr_labels_pdf(
                    codes, path,
                    preset=self.cmb_preset.currentText(),
                    include_text=self.chk_include_text.isChecked(),
                    border=self.chk_border.isChecked(),
                )
                self._notify_ok(path, one_file=True)

        except Exception as e:
            logger.exception("Eroare la generarea QR")
            QMessageBox.critical(self, "Eroare", f"Nu am putut genera:\n{e}")

    # ------------------------------------------------------------------
    def _notify_ok(self, path: str, one_file: bool = True):
        reply = QMessageBox.question(
            self, "Gata",
            f"Fișier salvat:\n{path}\n\nDeschide acum?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if reply == QMessageBox.Yes:
            self._open_path(path)

    def _open_path(self, path: str):
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            logger.exception("Nu am putut deschide %s", path)

    def _open_folder(self, path: str):
        self._open_path(path)
