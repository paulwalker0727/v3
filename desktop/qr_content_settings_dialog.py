"""
qr_content_settings_dialog.py — Dialog „Setări conținut QR".

Utilizatorul bifează exact ce date vrea să apară pe pagina publică
când cineva scanează QR-ul unui loc. Câmpurile sensibile (CNP, adresă,
telefon) sunt marcate ca 🔒 blocate și nu pot fi bifate niciodată.
"""
from __future__ import annotations

from typing import Dict, List

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QGroupBox, QScrollArea, QWidget, QMessageBox, QFrame, QTextEdit,
)

import database as db
import qr_public_data as qpd
from app_logging import get_logger

logger = get_logger(__name__)


class QrContentSettingsDialog(QDialog):
    """Dialog pentru configurarea câmpurilor publice ce apar pe pagina QR."""

    def __init__(self, parent=None, preview_cod_loc: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Setări conținut QR — Ce afișează pagina publică")
        self.resize(860, 640)

        self._checkboxes: Dict[str, QCheckBox] = {}
        self._preview_cod_loc = preview_cod_loc or self._first_loc_cod()

        # === Root: vertical (conținut + butoane) ===
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(10)

        # === conținut: orizontal (stânga bifă / dreapta preview) ===
        content_layout = QHBoxLayout()

        # -------- LEFT --------
        left = QVBoxLayout()

        title = QLabel("🎯  Ce afișează pagina publică QR")
        f = QFont(); f.setPointSize(14); f.setBold(True); title.setFont(f)
        left.addWidget(title)

        subtitle = QLabel(
            "Bifează exact câmpurile pe care le vezi utile pentru vizitatori.\n"
            "Datele sensibile (CNP, adresă, telefon) sunt <b>blocate hard</b>, "
            "nu pot fi expuse niciodată."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #667085; font-size: 10pt;")
        left.addWidget(subtitle)

        # scroll cu grup-uri
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content_widget = QWidget()
        content_lay = QVBoxLayout(content_widget)
        content_lay.setSpacing(10)

        # grupăm câmpurile pe secțiuni
        by_group: Dict[str, List[str]] = {}
        for key, meta in qpd.AVAILABLE_FIELDS.items():
            by_group.setdefault(meta["group"], []).append(key)

        group_order = ["Localizare", "Detalii loc", "Titular", "Persoane înhumate",
                       "Contract", "Memorial", "🔒 Blocate"]
        current = set(qpd.get_public_fields())

        for group in group_order:
            if group not in by_group:
                continue
            gb = QGroupBox(group)
            gb_lay = QVBoxLayout(gb)
            for key in by_group[group]:
                meta = qpd.AVAILABLE_FIELDS[key]
                cb = QCheckBox(meta["label"])
                cb.setChecked(key in current)
                if not meta["safe"]:
                    cb.setChecked(False)
                    cb.setEnabled(False)
                    cb.setToolTip("Acest câmp e blocat hard — nu poate fi expus public.")
                    cb.setStyleSheet("color: #c0392b;")
                cb.stateChanged.connect(self._update_preview)
                self._checkboxes[key] = cb
                gb_lay.addWidget(cb)
            content_lay.addWidget(gb)

        content_lay.addStretch()
        scroll.setWidget(content_widget)
        left.addWidget(scroll, 1)

        # butoane preseturi
        preset_row = QHBoxLayout()
        btn_min = QPushButton("Minim (doar localizare)")
        btn_min.clicked.connect(lambda: self._apply_preset("min"))
        btn_default = QPushButton("Recomandat (default)")
        btn_default.clicked.connect(lambda: self._apply_preset("default"))
        btn_max = QPushButton("Maxim (fără date personale)")
        btn_max.clicked.connect(lambda: self._apply_preset("max"))
        preset_row.addWidget(btn_min)
        preset_row.addWidget(btn_default)
        preset_row.addWidget(btn_max)
        left.addLayout(preset_row)

        content_layout.addLayout(left, 3)

        # -------- RIGHT: preview live --------
        right = QVBoxLayout()

        prev_title = QLabel("👁  Preview conținut public")
        f2 = QFont(); f2.setPointSize(13); f2.setBold(True); prev_title.setFont(f2)
        right.addWidget(prev_title)

        info = QLabel(
            f"Preview pentru locul <b>{self._preview_cod_loc or '(niciun loc)'}</b><br>"
            f"Așa va vedea vizitatorul când scanează QR-ul:"
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #667085;")
        right.addWidget(info)

        self.txt_preview = QTextEdit()
        self.txt_preview.setReadOnly(True)
        self.txt_preview.setStyleSheet(
            "QTextEdit { background-color: #f8fafc; border: 1px solid #d0d7e2; "
            "border-radius: 8px; padding: 12px; }"
        )
        right.addWidget(self.txt_preview, 1)

        content_layout.addLayout(right, 4)

        outer.addLayout(content_layout, 1)

        # === butoane finale ===
        btn_row = QHBoxLayout()
        btn_cancel = QPushButton("Anulează")
        btn_cancel.clicked.connect(self.reject)
        btn_save = QPushButton("💾  Salvează setările")
        btn_save.setDefault(True)
        btn_save.setMinimumHeight(38)
        btn_save.setStyleSheet(
            "background-color: #0e5aa7; color: white; font-weight: 600; "
            "padding: 0 22px; border-radius: 6px;")
        btn_save.clicked.connect(self._save)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_save)
        outer.addLayout(btn_row)

        # preview initial
        self._update_preview()

    # ------------------------------------------------------------------
    def _first_loc_cod(self) -> str:
        try:
            locs = db.list_locuri()
            if locs:
                return locs[0].get("cod_loc", "")
        except Exception:
            pass
        return ""

    def _apply_preset(self, name: str):
        presets = {
            "min": ["cod_loc", "sector", "parcela", "rand", "numar"],
            "default": list(qpd.DEFAULT_PUBLIC_FIELDS),
            "max": [k for k, m in qpd.AVAILABLE_FIELDS.items() if m["safe"]],
        }
        keys = set(presets.get(name, []))
        for k, cb in self._checkboxes.items():
            if cb.isEnabled():
                cb.setChecked(k in keys)
        self._update_preview()

    def _current_keys(self) -> List[str]:
        return [k for k, cb in self._checkboxes.items()
                if cb.isChecked() and cb.isEnabled()]

    def _update_preview(self):
        """Randează preview-ul folosind câmpurile bifate ACUM (fără persistență)."""
        keys = self._current_keys()
        original = db.get_setting

        def _mock_get(key):
            if key == "qr_public_fields":
                return ",".join(keys)
            return original(key)
        db.get_setting = _mock_get
        try:
            data = (qpd.get_public_data(self._preview_cod_loc)
                    if self._preview_cod_loc
                    else {"cod_loc": "(niciun loc)", "found": False, "sections": []})
        finally:
            db.get_setting = original

        self.txt_preview.setHtml(self._render_preview_html(data))

    def _render_preview_html(self, data: dict) -> str:
        if not data.get("found"):
            return ("<div style='padding:20px;color:#667085'>"
                    "Nu există un loc de veci de previzualizat.<br>"
                    "Adaugă câteva locuri și revino aici.</div>")

        parts = [
            f"<h2 style='color:#0e5aa7;margin:0 0 6px 0'>Locul de veci"
            f" <span style='color:#0f9d58'>{data['cod_loc']}</span></h2>",
            "<p style='color:#667085;margin:0 0 14px 0;font-size:10pt'>"
            "Pagină publică — informații pentru vizitatori</p>",
        ]

        if not data["sections"]:
            parts.append("<div style='padding:20px;color:#c0392b;"
                         "background-color:#fef2f2;border-radius:8px'>"
                         "⚠ Nu ai bifat niciun câmp de afișat.</div>")

        for sec in data["sections"]:
            parts.append(f"<h3 style='color:#0e5aa7;margin:12px 0 4px 0;"
                         f"border-bottom:1px solid #d0d7e2;padding-bottom:2px'>"
                         f"{sec['title']}</h3>")
            if "fields" in sec:
                parts.append("<table style='width:100%;border-collapse:collapse'>")
                for fld in sec["fields"]:
                    parts.append(
                        f"<tr>"
                        f"<td style='padding:3px 6px;color:#667085;width:35%'>{fld['label']}</td>"
                        f"<td style='padding:3px 6px;font-weight:600'>{fld['value']}</td>"
                        f"</tr>"
                    )
                parts.append("</table>")
            if "entries" in sec:
                for entry in sec["entries"]:
                    parts.append("<div style='padding:8px 12px;margin:6px 0;"
                                 "background-color:#f0f5fa;border-left:3px solid #0e5aa7;"
                                 "border-radius:4px'>")
                    if entry.get("nume"):
                        parts.append(f"<b>{entry['nume']}</b>")
                    dets = []
                    if entry.get("data_deces"):
                        dets.append(f"deces: {entry['data_deces']}")
                    if entry.get("data_inhumare"):
                        dets.append(f"înhumat: {entry['data_inhumare']}")
                    if dets:
                        parts.append(f"<br><span style='color:#667085;font-size:9pt'>"
                                     f"{' · '.join(dets)}</span>")
                    parts.append("</div>")

        return "".join(parts)

    def _save(self):
        keys = self._current_keys()
        try:
            qpd.set_public_fields(keys)
        except Exception as e:
            logger.exception("Nu am putut salva setările QR")
            QMessageBox.warning(self, "Eroare", f"Nu am putut salva:\n{e}")
            return
        QMessageBox.information(
            self, "Salvat",
            f"Am salvat {len(keys)} câmpuri publice.\n\n"
            f"De acum, când cineva scanează un QR de loc,\n"
            f"va vedea DOAR aceste câmpuri.")
        self.accept()
