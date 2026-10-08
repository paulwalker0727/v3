"""
qr_management_widget.py — Pagina completă de gestionare QR pentru locurile de veci.

Reutilizabil (QWidget):
    - în user mode: ca tab în sidebar
    - în admin: ca fereastră dedicată (deschisă din toolbar)

Layout:
    ┌─────────────────────────────────────────┬──────────────────────┐
    │  Toolbar cu statistici                  │                      │
    │  ┌─Filtre─────────────────────────────┐  │  📱 Preview QR       │
    │  │ 🔍 text | sector | status QR      │  │  (imagine live)      │
    │  └──────────────────────────────────┘  │                      │
    │                                         │  Cod: A-01-05        │
    │  ┌─Tabel locuri──────────────────────┐  │  Sector A, Parc. 1   │
    │  │ ☑  cod       sector  QR    stare │  │                      │
    │  │ ☑  A-01-05   A       ✅    OK    │  │  URL:                │
    │  │ ☐  A-01-06   A       ❌    OK    │  │  https://.../loc/... │
    │  │ ...                              │  │                      │
    │  └──────────────────────────────────┘  │  [ Marchează printat]│
    │                                         │                      │
    │  ┌─Export──────────────────────────────────────────────────┐  │
    │  │ [PNG single] [Batch PNG] [PDF etichete A4]              │  │
    │  │ Preset: 3x8 (24/pag)   ☑ Include text  ☑ Cadru          │  │
    │  │ [🚀 Generează pentru cele bifate]                       │  │
    │  └─────────────────────────────────────────────────────────┘  │
    └─────────────────────────────────────────┴──────────────────────┘
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
from typing import List, Optional

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont, QPixmap, QImage, QIcon
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox,
    QCheckBox, QSpinBox, QFileDialog, QMessageBox, QGroupBox, QRadioButton,
    QButtonGroup, QFormLayout, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QSplitter, QFrame, QSizePolicy, QToolButton,
)

import database as db
import pdf_reports as pr
import qr_labels as ql
import qr_tracking as qt
from app_logging import get_logger

logger = get_logger(__name__)


# Coloane tabel
COL_CHECK, COL_COD, COL_SECTOR, COL_PARCELA, COL_RAND, COL_NUMAR, \
    COL_STARE, COL_QR_STATUS, COL_QR_DATE = range(9)


class QrManagementWidget(QWidget):
    """Pagina completă de management QR — reutilizabilă ca tab sau fereastră."""

    def __init__(self, parent=None, current_user: Optional[str] = None):
        super().__init__(parent)
        self._current_user = current_user or "operator"
        self._all_locuri: List[dict] = []
        self._status_map: dict = {}
        self._preview_cod: Optional[str] = None

        # asigurăm tabela DB
        qt._ensure_table()

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # === HEADER cu titlu + statistici =================================
        header = QHBoxLayout()
        title = QLabel("🔲  Management QR — Locuri de veci")
        f = QFont(); f.setPointSize(16); f.setBold(True); title.setFont(f)
        header.addWidget(title)
        header.addStretch()

        self.lbl_stats = QLabel("")
        self.lbl_stats.setStyleSheet(
            "background-color: rgba(14,90,167,0.08); color: #0e5aa7; "
            "padding: 6px 14px; border-radius: 6px; font-weight: 600;")
        header.addWidget(self.lbl_stats)

        btn_refresh = QToolButton()
        btn_refresh.setText("🔄")
        btn_refresh.setToolTip("Reîncarcă lista locurilor")
        btn_refresh.clicked.connect(self.reload)
        header.addWidget(btn_refresh)

        btn_url = QPushButton("⚙  Setări URL...")
        btn_url.setToolTip("Configurează URL-ul portalului public al cimitirului")
        btn_url.clicked.connect(self._open_url_settings)
        header.addWidget(btn_url)

        btn_content = QPushButton("🎯  Setări conținut...")
        btn_content.setToolTip("Alege exact ce date se afișează pe pagina publică QR")
        btn_content.clicked.connect(self._open_content_settings)
        header.addWidget(btn_content)

        root.addLayout(header)

        # === SPLITTER: stânga listă / dreapta preview =====================
        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)

        # ---- LEFT: filtre + tabel + acțiuni export ----
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(10)

        # Filtre
        filt_box = QGroupBox("Filtrare")
        fl = QHBoxLayout(filt_box)
        self.txt_filter = QLineEdit()
        self.txt_filter.setPlaceholderText("🔍  cod, sector, parcelă...")
        self.txt_filter.textChanged.connect(self._apply_filter)
        fl.addWidget(self.txt_filter, 2)

        self.cmb_sector = QComboBox()
        self.cmb_sector.currentIndexChanged.connect(self._apply_filter)
        fl.addWidget(self.cmb_sector, 1)

        self.cmb_status = QComboBox()
        self.cmb_status.addItem("Toate", "all")
        self.cmb_status.addItem("✅  Cu QR generat", "with_qr")
        self.cmb_status.addItem("❌  Fără QR", "without_qr")
        self.cmb_status.addItem("🖨  QR generat + printat", "printed")
        self.cmb_status.addItem("⚠  QR generat, dar nu e printat", "unprinted")
        self.cmb_status.currentIndexChanged.connect(self._apply_filter)
        fl.addWidget(self.cmb_status, 1)

        left_lay.addWidget(filt_box)

        # Butoane rapide selecție
        sel_row = QHBoxLayout()
        b_all = QPushButton("Bifează tot filtrul")
        b_all.clicked.connect(lambda: self._toggle_visible(True))
        b_none = QPushButton("Debifează tot")
        b_none.clicked.connect(lambda: self._toggle_visible(False))
        b_invert = QPushButton("Inversează")
        b_invert.clicked.connect(self._invert_selection)
        self.lbl_count = QLabel("0 bifate")
        self.lbl_count.setStyleSheet("color: #0e5aa7; font-weight: 600;")
        sel_row.addWidget(b_all)
        sel_row.addWidget(b_none)
        sel_row.addWidget(b_invert)
        sel_row.addStretch()
        sel_row.addWidget(self.lbl_count)
        left_lay.addLayout(sel_row)

        # Tabel
        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels([
            "✓", "Cod loc", "Sector", "Parcelă", "Rând", "Nr.",
            "Stare", "QR", "Ultima generare"
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(COL_COD, QHeaderView.Interactive)
        self.table.setColumnWidth(COL_CHECK, 40)
        self.table.setColumnWidth(COL_COD, 120)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.table.itemChanged.connect(self._on_item_changed)
        left_lay.addWidget(self.table, 1)

        # Export box
        exp_box = QGroupBox("Export QR")
        exp_lay = QVBoxLayout(exp_box)

        mode_row = QHBoxLayout()
        self.rb_png = QRadioButton("🖼  PNG (1 loc)")
        self.rb_batch = QRadioButton("📁  Batch PNG-uri")
        self.rb_pdf = QRadioButton("📄  PDF etichete A4")
        self.rb_pdf.setChecked(True)
        self.grp_mode = QButtonGroup(self)
        for i, rb in enumerate([self.rb_png, self.rb_batch, self.rb_pdf]):
            self.grp_mode.addButton(rb, i)
            mode_row.addWidget(rb)
        self.grp_mode.idClicked.connect(self._on_mode_change)
        exp_lay.addLayout(mode_row)

        opts_row = QHBoxLayout()
        self.cmb_preset = QComboBox()
        for k in ql.LABEL_PRESETS:
            self.cmb_preset.addItem(k)
        idx = self.cmb_preset.findText("3x8 (24/pag, 70×36)")
        if idx >= 0:
            self.cmb_preset.setCurrentIndex(idx)
        opts_row.addWidget(QLabel("Preset:"))
        opts_row.addWidget(self.cmb_preset, 1)

        self.chk_text = QCheckBox("Include text")
        self.chk_text.setChecked(True)
        self.chk_border = QCheckBox("Cadru")
        self.chk_border.setChecked(True)
        self.chk_only_qr = QCheckBox("Doar QR (fără text)")
        opts_row.addWidget(self.chk_text)
        opts_row.addWidget(self.chk_border)
        opts_row.addWidget(self.chk_only_qr)
        exp_lay.addLayout(opts_row)

        self.btn_generate = QPushButton("🚀  Generează pentru cele bifate")
        self.btn_generate.setMinimumHeight(40)
        self.btn_generate.setStyleSheet(
            "background-color: #0e5aa7; color: white; font-weight: 600; "
            "padding: 0 20px; border-radius: 6px; font-size: 11pt;")
        self.btn_generate.clicked.connect(self._generate)
        exp_lay.addWidget(self.btn_generate)

        left_lay.addWidget(exp_box)

        splitter.addWidget(left)

        # ---- RIGHT: preview ----
        right = QFrame()
        right.setFrameShape(QFrame.StyledPanel)
        right.setMaximumWidth(340)
        right.setMinimumWidth(280)
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(14, 14, 14, 14)
        right_lay.setSpacing(10)

        prev_title = QLabel("📱 Preview QR")
        f = QFont(); f.setPointSize(13); f.setBold(True); prev_title.setFont(f)
        right_lay.addWidget(prev_title)

        self.lbl_preview_img = QLabel()
        self.lbl_preview_img.setFixedSize(260, 260)
        self.lbl_preview_img.setAlignment(Qt.AlignCenter)
        self.lbl_preview_img.setStyleSheet(
            "background-color: #f5f7fa; border: 1px dashed #c8ccd4; "
            "border-radius: 8px; color: #667085;")
        self.lbl_preview_img.setText("Selectează un loc\ndin tabel")
        right_lay.addWidget(self.lbl_preview_img, alignment=Qt.AlignCenter)

        self.lbl_preview_info = QLabel("")
        self.lbl_preview_info.setWordWrap(True)
        self.lbl_preview_info.setStyleSheet("color: #333; font-size: 10pt;")
        right_lay.addWidget(self.lbl_preview_info)

        self.lbl_preview_url = QLabel("")
        self.lbl_preview_url.setWordWrap(True)
        self.lbl_preview_url.setStyleSheet(
            "background-color: rgba(14,90,167,0.08); color: #0e5aa7; "
            "padding: 6px 10px; border-radius: 4px; "
            "font-family: monospace; font-size: 9pt;")
        right_lay.addWidget(self.lbl_preview_url)

        self.btn_print_toggle = QPushButton("🖨  Marchează ca printat")
        self.btn_print_toggle.setEnabled(False)
        self.btn_print_toggle.clicked.connect(self._toggle_printed)
        right_lay.addWidget(self.btn_print_toggle)

        self.btn_quick_png = QPushButton("💾  Salvează PNG rapid")
        self.btn_quick_png.setEnabled(False)
        self.btn_quick_png.clicked.connect(self._quick_save_png)
        right_lay.addWidget(self.btn_quick_png)

        right_lay.addStretch()

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 0)

        self._on_mode_change(2)  # default PDF etichete
        self.reload()

    # ------------------------------------------------------------------
    def reload(self):
        """Reîncarcă locurile din DB + status QR + tabel."""
        try:
            self._all_locuri = db.list_locuri() or []
        except Exception:
            logger.exception("Nu am putut încărca locurile")
            self._all_locuri = []
        try:
            self._status_map = qt.get_status_map()
        except Exception:
            logger.exception("Nu am putut încărca status_map QR")
            self._status_map = {}

        sectors = sorted({(l.get("sector") or "").strip()
                          for l in self._all_locuri if (l.get("sector") or "").strip()})
        cur_sect = self.cmb_sector.currentData()
        self.cmb_sector.blockSignals(True)
        self.cmb_sector.clear()
        self.cmb_sector.addItem("Toate sectoarele", "")
        for s in sectors:
            self.cmb_sector.addItem(s, s)
        # restore
        idx = self.cmb_sector.findData(cur_sect or "")
        if idx >= 0:
            self.cmb_sector.setCurrentIndex(idx)
        self.cmb_sector.blockSignals(False)

        self._render_table()
        self._update_stats()

    def _render_table(self):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for loc in self._all_locuri:
            row = self.table.rowCount()
            self.table.insertRow(row)

            cod = loc.get("cod_loc", "")
            status = self._status_map.get(cod, {})
            has_qr = bool(status.get("last_generated_at"))
            is_printed = bool(status.get("last_printed_at"))

            # ✓ bifă
            chk = QTableWidgetItem("")
            chk.setFlags(chk.flags() | Qt.ItemIsUserCheckable)
            chk.setFlags(chk.flags() & ~Qt.ItemIsEditable)
            chk.setCheckState(Qt.Unchecked)
            chk.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, COL_CHECK, chk)

            self.table.setItem(row, COL_COD, QTableWidgetItem(cod))
            self.table.setItem(row, COL_SECTOR, QTableWidgetItem(loc.get("sector") or "—"))
            self.table.setItem(row, COL_PARCELA, QTableWidgetItem(loc.get("parcela") or "—"))
            self.table.setItem(row, COL_RAND, QTableWidgetItem(loc.get("rand") or "—"))
            self.table.setItem(row, COL_NUMAR, QTableWidgetItem(loc.get("numar") or "—"))
            self.table.setItem(row, COL_STARE, QTableWidgetItem(loc.get("stare") or "—"))

            # QR icon + text
            if is_printed:
                qr_txt = "🖨  Printat"
                qr_col = "#0f9d58"
            elif has_qr:
                qr_txt = "✅  Generat"
                qr_col = "#0e5aa7"
            else:
                qr_txt = "❌  —"
                qr_col = "#c0392b"
            qr_item = QTableWidgetItem(qr_txt)
            qr_item.setForeground(Qt.GlobalColor.black)
            qr_item.setData(Qt.UserRole, {"has_qr": has_qr, "printed": is_printed})
            from PySide6.QtGui import QColor
            qr_item.setForeground(QColor(qr_col))
            f2 = QFont(); f2.setBold(True); qr_item.setFont(f2)
            self.table.setItem(row, COL_QR_STATUS, qr_item)

            date_txt = status.get("last_generated_at") or "—"
            if status.get("last_printed_at"):
                date_txt = f"{status['last_generated_at']}\n🖨  {status['last_printed_at']}"
            self.table.setItem(row, COL_QR_DATE, QTableWidgetItem(date_txt))
        self.table.blockSignals(False)
        self._apply_filter()

    def _update_stats(self):
        try:
            s = qt.stats()
        except Exception:
            logger.exception("Nu am putut calcula statistici QR")
            s = {"total_locuri": 0, "with_qr_generated": 0,
                 "with_qr_printed": 0, "without_qr": 0}
        self.lbl_stats.setText(
            f"📊  Total: {s['total_locuri']}  ·  "
            f"✅ QR generat: {s['with_qr_generated']}  ·  "
            f"🖨 Printate: {s['with_qr_printed']}  ·  "
            f"❌ Fără: {s['without_qr']}"
        )

    def _apply_filter(self):
        text = (self.txt_filter.text() or "").strip().lower()
        sector = self.cmb_sector.currentData() or ""
        status = self.cmb_status.currentData() or "all"

        visible = 0
        for row in range(self.table.rowCount()):
            cod = self.table.item(row, COL_COD).text()
            loc = next((l for l in self._all_locuri
                        if l.get("cod_loc") == cod), {})
            qr_info = self.table.item(row, COL_QR_STATUS).data(Qt.UserRole) or {}
            match = True
            if sector and (loc.get("sector") or "") != sector:
                match = False
            if match and text:
                blob = " ".join(str(loc.get(k, "") or "") for k in
                                ("cod_loc", "sector", "parcela", "rand", "numar", "stare"))
                if text not in blob.lower():
                    match = False
            if match and status != "all":
                has = qr_info.get("has_qr")
                printed = qr_info.get("printed")
                if status == "with_qr" and not has:
                    match = False
                elif status == "without_qr" and has:
                    match = False
                elif status == "printed" and not printed:
                    match = False
                elif status == "unprinted" and (not has or printed):
                    match = False
            self.table.setRowHidden(row, not match)
            if match:
                visible += 1
        self._update_count(visible_count=visible)

    def _toggle_visible(self, checked: bool):
        state = Qt.Checked if checked else Qt.Unchecked
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            if not self.table.isRowHidden(row):
                self.table.item(row, COL_CHECK).setCheckState(state)
        self.table.blockSignals(False)
        self._update_count()

    def _invert_selection(self):
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            if not self.table.isRowHidden(row):
                it = self.table.item(row, COL_CHECK)
                it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked)
        self.table.blockSignals(False)
        self._update_count()

    def _on_item_changed(self, item: QTableWidgetItem):
        if item.column() == COL_CHECK:
            self._update_count()

    def _update_count(self, visible_count: Optional[int] = None):
        checked = 0
        for row in range(self.table.rowCount()):
            if self.table.item(row, COL_CHECK).checkState() == Qt.Checked:
                checked += 1
        if visible_count is None:
            visible_count = sum(1 for r in range(self.table.rowCount())
                                if not self.table.isRowHidden(r))
        self.lbl_count.setText(f"{checked} bifate  ·  {visible_count} vizibile")

    def _checked_codes(self) -> List[str]:
        return [self.table.item(r, COL_COD).text()
                for r in range(self.table.rowCount())
                if self.table.item(r, COL_CHECK).checkState() == Qt.Checked]

    # ------------------------------------------------------------------
    # Preview panel
    # ------------------------------------------------------------------
    def _on_row_selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        cod = self.table.item(row, COL_COD).text()
        self._preview_cod = cod
        self._update_preview(cod)

    def _update_preview(self, cod: str):
        try:
            img = ql.make_qr_label_image(cod, label_size_px=(260, 260),
                                         include_text=False)
            # convertim PIL → QPixmap
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            qimg = QImage.fromData(buf.getvalue())
            pix = QPixmap.fromImage(qimg).scaled(
                240, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.lbl_preview_img.setPixmap(pix)
        except Exception:
            logger.exception("Nu am putut face preview QR pentru %s", cod)
            self.lbl_preview_img.setText("Eroare la preview")

        loc = next((l for l in self._all_locuri if l.get("cod_loc") == cod), {})
        details = [f"<b>Cod:</b> {cod}"]
        for k, label in [("sector", "Sector"), ("parcela", "Parcelă"),
                         ("rand", "Rând"), ("numar", "Nr."), ("stare", "Stare")]:
            if loc.get(k):
                details.append(f"<b>{label}:</b> {loc[k]}")
        self.lbl_preview_info.setText("<br>".join(details))

        url = pr._build_qr_url("loc", cod)
        self.lbl_preview_url.setText(url)

        status = self._status_map.get(cod, {})
        is_printed = bool(status.get("last_printed_at"))
        if is_printed:
            self.btn_print_toggle.setText("↩  Anulează marcaj printat")
        else:
            self.btn_print_toggle.setText("🖨  Marchează ca printat")
        self.btn_print_toggle.setEnabled(True)
        self.btn_quick_png.setEnabled(True)

    def _toggle_printed(self):
        if not self._preview_cod:
            return
        status = self._status_map.get(self._preview_cod, {})
        try:
            if status.get("last_printed_at"):
                qt.unmark_printed(self._preview_cod)
            else:
                qt.mark_printed(self._preview_cod, printed_by=self._current_user)
        except Exception:
            logger.exception("Nu am putut schimba starea printat pentru %s", self._preview_cod)
            QMessageBox.warning(self, "Eroare", "Nu am putut salva marcajul.")
            return
        self._status_map = qt.get_status_map()
        self._render_table()
        self._update_stats()
        self._update_preview(self._preview_cod)

    def _quick_save_png(self):
        if not self._preview_cod:
            return
        default = f"qr_{self._preview_cod.replace('/', '_')}.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "Salvează PNG rapid", default, "PNG (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        try:
            ql.generate_qr_png(self._preview_cod, path,
                               include_text=True, only_qr=False)
            qt.log_generation(self._preview_cod, "png", path=path,
                              generated_by=self._current_user)
            self._status_map = qt.get_status_map()
            self._render_table()
            self._update_stats()
            self._update_preview(self._preview_cod)
            QMessageBox.information(self, "Gata", f"PNG salvat:\n{path}")
        except Exception as e:
            logger.exception("Eroare quick_save_png")
            QMessageBox.critical(self, "Eroare", str(e))

    # ------------------------------------------------------------------
    def _on_mode_change(self, idx: int):
        # activează opțiunile relevante
        is_pdf = (idx == 2)
        self.cmb_preset.setEnabled(is_pdf)
        self.chk_border.setEnabled(is_pdf)
        # only_qr merge doar pe PNG (single/batch)
        self.chk_only_qr.setEnabled(not is_pdf)
        self.chk_text.setEnabled(is_pdf)

    def _open_url_settings(self):
        from rapoarte_dialog import QrSettingsDialog
        dlg = QrSettingsDialog(self)
        if dlg.exec():
            if self._preview_cod:
                self._update_preview(self._preview_cod)

    def _open_content_settings(self):
        from qr_content_settings_dialog import QrContentSettingsDialog
        dlg = QrContentSettingsDialog(self, preview_cod_loc=self._preview_cod or "")
        dlg.exec()

    # ------------------------------------------------------------------
    def _generate(self):
        codes = self._checked_codes()
        if not codes:
            QMessageBox.warning(self, "Selecție goală",
                                "Bifează cel puțin un loc din tabel.")
            return

        mode = self.grp_mode.checkedId()

        try:
            if mode == 0:  # PNG single
                if len(codes) > 1:
                    QMessageBox.warning(
                        self, "Prea multe locuri",
                        f"Modul „PNG (1 loc)\" acceptă un singur loc bifat.\n"
                        f"Ai bifat {len(codes)}. Folosește „Batch PNG-uri\" sau „PDF etichete\".")
                    return
                default = f"qr_{codes[0].replace('/', '_')}.png"
                path, _ = QFileDialog.getSaveFileName(
                    self, "Salvează PNG", default, "PNG (*.png)")
                if not path:
                    return
                if not path.lower().endswith(".png"):
                    path += ".png"
                only = self.chk_only_qr.isChecked()
                ql.generate_qr_png(
                    codes[0], path,
                    label_size_px=(500, 500 if only else 620),
                    include_text=not only,
                    only_qr=only,
                )
                qt.log_generation(codes[0], "png", path=path,
                                  generated_by=self._current_user)
                self._after_generate(f"PNG salvat:\n{path}", open_path=path)

            elif mode == 1:  # batch PNG
                out_dir = QFileDialog.getExistingDirectory(
                    self, "Alege folder pentru PNG-uri")
                if not out_dir:
                    return
                only = self.chk_only_qr.isChecked()
                paths = ql.generate_qr_batch_pngs(
                    codes, out_dir,
                    label_size_px=(500, 500 if only else 620),
                    include_text=not only,
                    only_qr=only,
                )
                for cod, p in zip(codes, paths):
                    qt.log_generation(cod, "batch_png", path=p,
                                      generated_by=self._current_user)
                self._after_generate(
                    f"Am generat {len(paths)} PNG-uri în:\n{out_dir}",
                    open_path=out_dir)

            else:  # PDF etichete
                default = f"etichete_qr_{len(codes)}locuri.pdf"
                path, _ = QFileDialog.getSaveFileName(
                    self, "Salvează PDF etichete", default, "PDF (*.pdf)")
                if not path:
                    return
                if not path.lower().endswith(".pdf"):
                    path += ".pdf"
                ql.generate_qr_labels_pdf(
                    codes, path,
                    preset=self.cmb_preset.currentText(),
                    include_text=self.chk_text.isChecked(),
                    border=self.chk_border.isChecked(),
                )
                for cod in codes:
                    qt.log_generation(cod, "pdf_labels", path=path,
                                      generated_by=self._current_user)
                self._after_generate(
                    f"PDF cu {len(codes)} etichete salvat:\n{path}",
                    open_path=path)

        except Exception as e:
            logger.exception("Eroare la generarea QR")
            QMessageBox.critical(self, "Eroare", f"Nu am putut genera:\n{e}")

    def _after_generate(self, msg: str, open_path: Optional[str] = None):
        # refresh status
        self._status_map = qt.get_status_map()
        self._render_table()
        self._update_stats()
        if self._preview_cod:
            self._update_preview(self._preview_cod)
        # notificare
        if open_path:
            reply = QMessageBox.question(
                self, "Gata", f"{msg}\n\nDeschide acum?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                self._open_path(open_path)
        else:
            QMessageBox.information(self, "Gata", msg)

    def _open_path(self, path: str):
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            logger.exception("Nu am putut deschide %s", path)
