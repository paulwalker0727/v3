"""
qr_custom_dialog.py — UI Widget: „Generator QR Personalizat".

Utilizatorul:
    1. selectează un loc de veci (dropdown cu căutare)
    2. bifează exact câmpurile ce vor fi salvate ÎN codul QR
       (nu link, nu URL — datele bifate sunt embedate direct în cod)
    3. vede preview live
    4. exportă PNG (sau batch pentru mai multe locuri)

Câmpurile sensibile (CNP, adresă, telefon) sunt permise, dar afișează
avertisment vizual pentru a atrage atenția asupra confidențialității.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QFont, QPixmap, QImage, QIcon
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox,
    QCheckBox, QGroupBox, QScrollArea, QFrame, QFileDialog, QMessageBox,
    QSplitter, QRadioButton, QButtonGroup, QListWidget, QListWidgetItem,
    QTextEdit, QSizePolicy, QDialog,
)

import database as db
import qr_custom_generator as qcg
from app_logging import get_logger

logger = get_logger(__name__)


class QrCustomDialog(QWidget):
    """Widget standalone (poate fi montat în QMainWindow separat)."""

    request_close = Signal()

    def __init__(self, parent=None, current_user: str = "admin"):
        super().__init__(parent)
        self._current_user = current_user
        self._checkboxes: Dict[str, QCheckBox] = {}
        self._all_locuri: List[dict] = []
        self._preview_cod: Optional[str] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # === HEADER =====================================================
        header = QHBoxLayout()
        title = QLabel("🎫  Generator QR Personalizat")
        f = QFont(); f.setPointSize(16); f.setBold(True); title.setFont(f)
        header.addWidget(title)
        header.addStretch()

        info = QLabel(
            "Bifează câmpurile pe care le vrei <b>direct în codul QR</b> "
            "(nu link, ci date embedate).")
        info.setStyleSheet("color: #667085; font-size: 10pt;")
        info.setWordWrap(True)
        header.addWidget(info, 1)
        root.addLayout(header)

        # === SPLITTER: stânga = selecție loc + câmpuri; dreapta = preview
        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)

        # ---- LEFT ----
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(10)

        # Selector loc
        loc_row = QHBoxLayout()
        loc_row.addWidget(QLabel("Loc de veci:"))
        self.cmb_loc = QComboBox()
        self.cmb_loc.setEditable(True)
        self.cmb_loc.setInsertPolicy(QComboBox.NoInsert)
        self.cmb_loc.setMinimumWidth(200)
        self.cmb_loc.setToolTip("Selectează sau tastează codul locului")
        self.cmb_loc.currentTextChanged.connect(self._on_loc_changed)
        loc_row.addWidget(self.cmb_loc, 1)

        btn_reload = QPushButton("🔄")
        btn_reload.setToolTip("Reîncarcă lista locurilor")
        btn_reload.setMaximumWidth(40)
        btn_reload.clicked.connect(self._reload_locuri)
        loc_row.addWidget(btn_reload)
        left_lay.addLayout(loc_row)

        # Preset buttons
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("<b>Presetări rapide:</b>"))
        btn_min = QPushButton("Minim")
        btn_min.setToolTip("Doar cod loc + locație")
        btn_min.clicked.connect(lambda: self._apply_preset("min"))
        btn_default = QPushButton("Recomandat")
        btn_default.setToolTip("Loc + titular + contract de bază")
        btn_default.clicked.connect(lambda: self._apply_preset("default"))
        btn_full = QPushButton("Complet (fără sensitive)")
        btn_full.setToolTip("Toate câmpurile non-sensibile")
        btn_full.clicked.connect(lambda: self._apply_preset("full"))
        btn_all = QPushButton("⚠  Tot (inclusiv CNP)")
        btn_all.setToolTip("ATENȚIE: include date personale sensibile")
        btn_all.setStyleSheet("color: #b45309; font-weight: 600;")
        btn_all.clicked.connect(lambda: self._apply_preset("all"))
        for b in (btn_min, btn_default, btn_full, btn_all):
            preset_row.addWidget(b)
        preset_row.addStretch()
        left_lay.addLayout(preset_row)

        # Selectare toate/nimic
        sel_row = QHBoxLayout()
        b_all = QPushButton("Bifează tot")
        b_all.clicked.connect(lambda: self._set_all(True))
        b_none = QPushButton("Debifează tot")
        b_none.clicked.connect(lambda: self._set_all(False))
        b_save_default = QPushButton("💾 Salvează ca implicit")
        b_save_default.setToolTip("Setează bifările curente ca implicite pentru toate locurile viitoare")
        b_save_default.clicked.connect(self._save_default)
        sel_row.addWidget(b_all)
        sel_row.addWidget(b_none)
        sel_row.addStretch()
        sel_row.addWidget(b_save_default)
        left_lay.addLayout(sel_row)

        # Scroll cu câmpuri
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.StyledPanel)
        content = QWidget()
        content_lay = QVBoxLayout(content)
        content_lay.setSpacing(8)

        by_group: Dict[str, List[str]] = {}
        for key, meta in qcg.FIELDS.items():
            by_group.setdefault(meta["group"], []).append(key)

        default_keys = qcg.get_saved_default_keys()

        for group in qcg.GROUP_ORDER:
            keys = by_group.get(group, [])
            if not keys:
                continue
            gb = QGroupBox(group)
            gb_lay = QVBoxLayout(gb)
            gb_lay.setSpacing(4)
            for k in keys:
                meta = qcg.FIELDS[k]
                label = meta["label"]
                if meta.get("sensitive"):
                    label = f"⚠  {label}  (date personale)"
                cb = QCheckBox(label)
                cb.setChecked(k in default_keys)
                if meta.get("sensitive"):
                    cb.setStyleSheet("color: #b45309;")
                cb.stateChanged.connect(self._refresh_preview)
                self._checkboxes[k] = cb
                gb_lay.addWidget(cb)
            content_lay.addWidget(gb)

        content_lay.addStretch()
        scroll.setWidget(content)
        left_lay.addWidget(scroll, 1)

        # === Options: format + EC level + text
        opts_box = QGroupBox("Opțiuni codificare")
        opts_lay = QHBoxLayout(opts_box)

        opts_lay.addWidget(QLabel("Format:"))
        self.rb_text = QRadioButton("Text (citibil uman)")
        self.rb_text.setChecked(True)
        self.rb_json = QRadioButton("JSON (pentru import)")
        self.grp_format = QButtonGroup(self)
        self.grp_format.addButton(self.rb_text, 0)
        self.grp_format.addButton(self.rb_json, 1)
        self.grp_format.idClicked.connect(lambda _i: self._refresh_preview())
        opts_lay.addWidget(self.rb_text)
        opts_lay.addWidget(self.rb_json)
        opts_lay.addSpacing(20)

        opts_lay.addWidget(QLabel("Toleranță erori:"))
        self.cmb_ec = QComboBox()
        for lv, desc in [("L", "L (7%)"), ("M", "M (15%) — implicit"),
                         ("Q", "Q (25%)"), ("H", "H (30% — cel mai robust)")]:
            self.cmb_ec.addItem(desc, lv)
        self.cmb_ec.setCurrentIndex(1)
        self.cmb_ec.currentIndexChanged.connect(lambda _i: self._refresh_preview())
        opts_lay.addWidget(self.cmb_ec)

        opts_lay.addSpacing(20)
        self.chk_include_text = QCheckBox("Adaugă text sub QR")
        self.chk_include_text.setChecked(True)
        opts_lay.addWidget(self.chk_include_text)

        self.chk_sign = QCheckBox("🔐 Semnează cu HMAC (anti-falsificare)")
        self.chk_sign.setToolTip(
            "Adaugă o semnătură scurtă la sfârșitul datelor. La scanare poți verifica "
            "cu butonul „Verifică QR scanat” dacă datele au fost falsificate.")
        self.chk_sign.setChecked(False)
        self.chk_sign.stateChanged.connect(self._refresh_preview)
        opts_lay.addWidget(self.chk_sign)
        opts_lay.addStretch()

        left_lay.addWidget(opts_box)

        # Butoane export
        btn_row = QHBoxLayout()
        self.btn_export = QPushButton("💾  Exportă PNG…")
        self.btn_export.setMinimumHeight(42)
        self.btn_export.setStyleSheet(
            "background-color: #0e5aa7; color: white; font-weight: 600; "
            "padding: 0 22px; border-radius: 6px; font-size: 11pt;")
        self.btn_export.clicked.connect(self._export_current)
        btn_row.addWidget(self.btn_export, 1)

        self.btn_batch = QPushButton("📁  Export batch (toate locurile)…")
        self.btn_batch.setMinimumHeight(42)
        self.btn_batch.clicked.connect(self._export_batch)
        btn_row.addWidget(self.btn_batch, 1)

        self.btn_verify = QPushButton("🔍  Verifică QR scanat…")
        self.btn_verify.setMinimumHeight(42)
        self.btn_verify.setToolTip("Lipește conținutul unui QR scanat și verifică autenticitatea semnăturii")
        self.btn_verify.clicked.connect(self._open_verifier)
        btn_row.addWidget(self.btn_verify, 1)

        left_lay.addLayout(btn_row)

        splitter.addWidget(left)

        # ---- RIGHT: preview ----
        right = QFrame()
        right.setFrameShape(QFrame.StyledPanel)
        right.setMinimumWidth(340)
        right.setMaximumWidth(420)
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(14, 14, 14, 14)
        right_lay.setSpacing(10)

        prev_title = QLabel("📱  Preview live")
        f2 = QFont(); f2.setPointSize(13); f2.setBold(True); prev_title.setFont(f2)
        right_lay.addWidget(prev_title)

        self.lbl_preview = QLabel()
        self.lbl_preview.setFixedSize(300, 300)
        self.lbl_preview.setAlignment(Qt.AlignCenter)
        self.lbl_preview.setStyleSheet(
            "background-color: #f5f7fa; border: 1px dashed #c8ccd4; "
            "border-radius: 8px; color: #667085;")
        self.lbl_preview.setText("Alege un loc\ndin lista")
        right_lay.addWidget(self.lbl_preview, alignment=Qt.AlignCenter)

        self.lbl_size = QLabel("")
        self.lbl_size.setStyleSheet("color: #667085; font-size: 10pt;")
        self.lbl_size.setAlignment(Qt.AlignCenter)
        right_lay.addWidget(self.lbl_size)

        prev_data_title = QLabel("Conținut embedat:")
        prev_data_title.setStyleSheet("color: #0e5aa7; font-weight: 600; margin-top: 6px;")
        right_lay.addWidget(prev_data_title)

        self.txt_encoded = QTextEdit()
        self.txt_encoded.setReadOnly(True)
        self.txt_encoded.setStyleSheet(
            "QTextEdit { background-color: #f8fafc; border: 1px solid #d0d7e2; "
            "border-radius: 6px; padding: 8px; font-family: monospace; font-size: 9pt; }")
        self.txt_encoded.setMinimumHeight(140)
        right_lay.addWidget(self.txt_encoded, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 0)

        # populate
        self._reload_locuri()

    # ---------------------------------------------------------------
    def _reload_locuri(self):
        try:
            self._all_locuri = db.list_locuri() or []
        except Exception:
            logger.exception("Nu am putut încărca locurile")
            self._all_locuri = []

        cur = self.cmb_loc.currentText()
        self.cmb_loc.blockSignals(True)
        self.cmb_loc.clear()
        for loc in self._all_locuri:
            cod = loc.get("cod_loc") or ""
            if cod:
                self.cmb_loc.addItem(cod)
        # restore
        if cur:
            idx = self.cmb_loc.findText(cur)
            if idx >= 0:
                self.cmb_loc.setCurrentIndex(idx)
        self.cmb_loc.blockSignals(False)
        self._on_loc_changed(self.cmb_loc.currentText())

    def _on_loc_changed(self, text: str):
        cod = (text or "").strip()
        self._preview_cod = cod if cod else None
        self._refresh_preview()

    def _current_keys(self) -> List[str]:
        return [k for k, cb in self._checkboxes.items() if cb.isChecked()]

    def _set_all(self, checked: bool):
        for cb in self._checkboxes.values():
            cb.blockSignals(True)
            cb.setChecked(checked)
            cb.blockSignals(False)
        self._refresh_preview()

    def _apply_preset(self, name: str):
        presets: Dict[str, List[str]] = {
            "min": ["cod_loc", "sector", "parcela", "rand", "numar"],
            "default": list(qcg.DEFAULT_KEYS),
            "full": [k for k, m in qcg.FIELDS.items() if not m.get("sensitive")],
            "all": list(qcg.FIELDS.keys()),
        }
        want = set(presets.get(name, []))
        for k, cb in self._checkboxes.items():
            cb.blockSignals(True)
            cb.setChecked(k in want)
            cb.blockSignals(False)
        self._refresh_preview()

    def _save_default(self):
        keys = self._current_keys()
        qcg.save_default_keys(keys)
        QMessageBox.information(
            self, "Salvat",
            f"Am salvat {len(keys)} câmpuri ca implicite.\n"
            "Data viitoare când deschizi acest generator, aceste bifări vor fi active.")

    def _refresh_preview(self):
        if not self._preview_cod:
            self.lbl_preview.setPixmap(QPixmap())
            self.lbl_preview.setText("Alege un loc\ndin lista")
            self.txt_encoded.setPlainText("")
            self.lbl_size.setText("")
            return
        keys = self._current_keys()
        if not keys:
            self.lbl_preview.setPixmap(QPixmap())
            self.lbl_preview.setText("Nu ai bifat\nniciun câmp")
            self.txt_encoded.setPlainText("")
            self.lbl_size.setText("")
            return
        fmt = "json" if self.rb_json.isChecked() else "text"
        ec = self.cmb_ec.currentData() or "M"
        sign = self.chk_sign.isChecked()
        try:
            png_bytes, data, byte_len = qcg.build_qr_pixmap(
                self._preview_cod, keys, format=fmt, ec_level=ec,
                side_px=290, sign=sign)
            qimg = QImage.fromData(png_bytes)
            pix = QPixmap.fromImage(qimg).scaled(
                290, 290, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.lbl_preview.setPixmap(pix)
            self.lbl_preview.setText("")
            self.txt_encoded.setPlainText(data)
            # avertisment vizual pentru capacitate QR: pt EC=M, versiunea 40 = ~2953 bytes
            warn = ""
            if byte_len > 1800:
                warn = "  ⚠  aproape de limita QR — reduce câmpuri sau EC"
            sig_tag = "  🔐 semnat" if sign else ""
            self.lbl_size.setText(
                f"{byte_len} bytes  ·  format: {fmt.upper()}  ·  EC: {ec}{sig_tag}{warn}")
        except Exception as e:
            logger.exception("Preview QR custom a eșuat")
            self.lbl_preview.setPixmap(QPixmap())
            self.lbl_preview.setText(f"Eroare:\n{e}")
            self.txt_encoded.setPlainText("")
            self.lbl_size.setText("")

    # ---------------------------------------------------------------
    def _export_current(self):
        if not self._preview_cod:
            QMessageBox.warning(self, "Selecție lipsă",
                                "Alege întâi un loc de veci din listă.")
            return
        keys = self._current_keys()
        if not keys:
            QMessageBox.warning(self, "Selecție lipsă",
                                "Bifează cel puțin un câmp pentru QR.")
            return
        default_name = f"qr_custom_{self._preview_cod.replace('/', '_')}.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "Salvează QR personalizat", default_name, "PNG (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        fmt = "json" if self.rb_json.isChecked() else "text"
        ec = self.cmb_ec.currentData() or "M"
        sign = self.chk_sign.isChecked()
        try:
            out, encoded = qcg.generate_and_save_png(
                self._preview_cod, keys, path,
                format=fmt, ec_level=ec,
                include_text_below=self.chk_include_text.isChecked(),
                title=f"Cimitir · {self._preview_cod}",
                sign=sign)
        except Exception as e:
            logger.exception("Export QR custom a eșuat")
            QMessageBox.critical(self, "Eroare", f"Nu am putut salva:\n{e}")
            return
        sig_note = "\n🔐 QR-ul e semnat cu HMAC — poate fi verificat cu „Verifică QR scanat”." if sign else ""
        reply = QMessageBox.question(
            self, "Gata",
            f"QR salvat:\n{out}\n\nDimensiune date embedate: {len(encoded)} bytes.{sig_note}\n\n"
            f"Deschid fișierul acum?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if reply == QMessageBox.Yes:
            self._open_path(out)

    def _export_batch(self):
        keys = self._current_keys()
        if not keys:
            QMessageBox.warning(self, "Selecție lipsă",
                                "Bifează cel puțin un câmp înainte de export batch.")
            return
        out_dir = QFileDialog.getExistingDirectory(
            self, "Alege folder pentru PNG-uri")
        if not out_dir:
            return
        fmt = "json" if self.rb_json.isChecked() else "text"
        ec = self.cmb_ec.currentData() or "M"
        sign = self.chk_sign.isChecked()
        ok, fail = 0, 0
        errors: List[str] = []
        for loc in self._all_locuri:
            cod = loc.get("cod_loc") or ""
            if not cod:
                continue
            safe = cod.replace("/", "_").replace(" ", "_")
            out = os.path.join(out_dir, f"qr_custom_{safe}.png")
            try:
                qcg.generate_and_save_png(
                    cod, keys, out,
                    format=fmt, ec_level=ec,
                    include_text_below=self.chk_include_text.isChecked(),
                    title=f"Cimitir · {cod}",
                    sign=sign)
                ok += 1
            except Exception as e:
                fail += 1
                errors.append(f"{cod}: {e}")
                logger.exception("Batch QR custom: eroare la %s", cod)
        msg = f"Am generat {ok} PNG-uri în:\n{out_dir}"
        if fail:
            msg += f"\n\n⚠  {fail} au eșuat."
            if errors[:5]:
                msg += "\n\n" + "\n".join(errors[:5])
        reply = QMessageBox.question(
            self, "Batch complet",
            msg + "\n\nDeschid folderul?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if reply == QMessageBox.Yes:
            self._open_path(out_dir)

    def _open_verifier(self):
        """Deschide dialogul de verificare — lipești un QR scanat, îți spun dacă e autentic."""
        dlg = QrVerifyDialog(self)
        dlg.exec()

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


# ===========================================================================
# QrVerifyDialog — dialog pentru verificarea autenticității unui QR scanat
# ===========================================================================
class QrVerifyDialog(QDialog):
    """
    Lipește conținutul unui QR (text sau JSON) și primești un răspuns clar:
    ✅ AUTENTIC — datele nu au fost modificate  ·  emis la <data>
    ❌ FALSIFICAT / EROARE — datele au fost modificate SAU alt secret
    ⚠  NESEMNAT — QR-ul nu conține semnătură (nu poate fi verificat)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Verifică QR scanat")
        self.resize(720, 640)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        title = QLabel("🔍  Verificare autenticitate QR")
        f = QFont(); f.setPointSize(14); f.setBold(True); title.setFont(f)
        root.addWidget(title)

        info = QLabel(
            "Scanează un QR generat de aplicație cu orice cititor (telefon, cameră) "
            "și lipește conținutul aici. Îți spun dacă semnătura corespunde "
            "sau dacă datele au fost modificate."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #667085; font-size: 10pt;")
        root.addWidget(info)

        self.txt_input = QTextEdit()
        self.txt_input.setPlaceholderText(
            "Lipește aici conținutul scanat din QR…\n\n"
            "Exemplu:\n"
            "Loc de veci: A-01-05\n"
            "— Loc\n  Cod loc: A-01-05\n  Sector: A\n"
            "…\n"
            "— Semnătură\n  sig: 3f6a…\n  gen: 2026-01-15T10:30")
        self.txt_input.setStyleSheet(
            "QTextEdit { font-family: monospace; font-size: 10pt; "
            "border: 1px solid #d0d7e2; border-radius: 6px; padding: 8px; }")
        root.addWidget(self.txt_input, 1)

        btn_row = QHBoxLayout()
        self.btn_verify = QPushButton("🔍  Verifică")
        self.btn_verify.setMinimumHeight(40)
        self.btn_verify.setStyleSheet(
            "background-color: #0e5aa7; color: white; font-weight: 600; "
            "padding: 0 22px; border-radius: 6px; font-size: 11pt;")
        self.btn_verify.clicked.connect(self._verify)
        btn_paste = QPushButton("📋  Lipește din clipboard")
        btn_paste.clicked.connect(self._paste_from_clipboard)
        btn_clear = QPushButton("Golește")
        btn_clear.clicked.connect(lambda: self.txt_input.clear())
        btn_close = QPushButton("Închide")
        btn_close.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_verify)
        btn_row.addWidget(btn_paste)
        btn_row.addWidget(btn_clear)
        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        root.addLayout(btn_row)

        self.lbl_result = QLabel("")
        self.lbl_result.setWordWrap(True)
        self.lbl_result.setStyleSheet(
            "padding: 12px 16px; border-radius: 8px; font-size: 12pt;")
        self.lbl_result.setVisible(False)
        root.addWidget(self.lbl_result)

        self.txt_details = QTextEdit()
        self.txt_details.setReadOnly(True)
        self.txt_details.setMaximumHeight(150)
        self.txt_details.setStyleSheet(
            "QTextEdit { background-color: #f8fafc; border: 1px solid #d0d7e2; "
            "border-radius: 6px; padding: 8px; font-family: monospace; font-size: 9pt; }")
        self.txt_details.setVisible(False)
        root.addWidget(self.txt_details)

    def _paste_from_clipboard(self):
        from PySide6.QtGui import QGuiApplication
        cb = QGuiApplication.clipboard()
        text = cb.text() if cb else ""
        if text:
            self.txt_input.setPlainText(text)

    def _verify(self):
        content = self.txt_input.toPlainText()
        if not content.strip():
            QMessageBox.warning(self, "Gol", "Lipește întâi un conținut de QR.")
            return
        res = qcg.verify_signed(content)
        self.lbl_result.setVisible(True)
        self.txt_details.setVisible(True)

        if not res["signed"]:
            self.lbl_result.setStyleSheet(
                "padding: 12px 16px; border-radius: 8px; font-size: 12pt; "
                "background-color: #fef3c7; color: #92400e; font-weight: 600;")
            self.lbl_result.setText(
                "⚠  QR NESEMNAT — nu conține o semnătură HMAC.\n"
                "Nu pot certifica autenticitatea datelor.")
        elif res["valid"]:
            self.lbl_result.setStyleSheet(
                "padding: 12px 16px; border-radius: 8px; font-size: 12pt; "
                "background-color: #d1fae5; color: #065f46; font-weight: 600;")
            self.lbl_result.setText(
                f"✅  QR AUTENTIC — semnătura este validă.\n"
                f"Datele NU au fost modificate.\n"
                f"Emis la: {res['gen']} UTC")
        else:
            self.lbl_result.setStyleSheet(
                "padding: 12px 16px; border-radius: 8px; font-size: 12pt; "
                "background-color: #fee2e2; color: #991b1b; font-weight: 600;")
            self.lbl_result.setText(
                f"❌  QR INVALID — semnătura NU se potrivește.\n{res['error']}")

        details = [
            f"Format detectat: {res['format']}",
            f"Data generare:  {res['gen'] or '(lipsă)'}",
            f"Semnătură găsită:    {res['sig_present'] or '(lipsă)'}",
            f"Semnătură așteptată: {res['sig_expected'] or '(nu s-a calculat)'}",
        ]
        self.txt_details.setPlainText("\n".join(details))
