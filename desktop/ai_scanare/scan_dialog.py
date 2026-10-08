"""Dialog PySide6 reutilizabil pentru scanare AI documente (Buletin / Certificat Deces).

Utilizare:
    dlg = ScanDialog(parent, DocumentType.BULETIN)
    if dlg.exec() == QDialog.Accepted:
        data = dlg.get_extracted_data()   # dict cu câmpurile finale (editate)
        source_file = dlg.get_source_file()  # calea fișierului scanat
        file_action = dlg.get_file_action()  # "attach" | "archive" | "delete"

Dialog-ul se ocupă doar de scanare + preview + editare + confirmare.
Salvarea în DB (concesionar/inhumat) și acțiunea pe fișier sunt responsabilitatea apelantului.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QImage,
    QPixmap,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from . import config as ai_config
from .ai_client import DocumentType, extract_document_sync
from .offline_ocr import tesseract_available


# ============ CÂMPURI PE TIP DOCUMENT ============

BULETIN_FIELDS = [
    ("nume", "Nume"),
    ("prenume", "Prenume"),
    ("cnp", "CNP"),
    ("serie", "Serie CI"),
    ("numar", "Număr CI"),
    ("data_nasterii", "Data nașterii"),
    ("sex", "Sex"),
    ("nationalitate", "Naționalitate"),
    ("adresa", "Adresă"),
    ("emitent", "Emitent (SPCLEP)"),
    ("data_eliberarii", "Data eliberării"),
    ("valabilitate", "Valabilitate"),
]

DECES_FIELDS = [
    ("nume", "Nume"),
    ("prenume", "Prenume"),
    ("cnp", "CNP"),
    ("data_nasterii", "Data nașterii"),
    ("data_deces", "Data decesului"),
    ("sex", "Sex"),
    ("cauza_deces", "Cauza decesului"),
    ("locul_decesului", "Locul decesului"),
    ("ultima_adresa", "Ultima adresă"),
    ("nr_certificat_deces", "Nr. certificat deces"),
    ("nr_adeverinta_inhumare", "Nr. adeverință înhumare"),
    ("data_eliberare", "Data eliberării"),
    ("emitent", "Emitent"),
]

ADEVERINTA_FIELDS = [
    ("nume", "Nume"),
    ("prenume", "Prenume"),
    ("cnp", "CNP"),
    ("serie", "Serie CI"),
    ("numar", "Număr CI"),
    ("adresa", "Adresa completă"),
    ("localitate", "Localitate"),
    ("judet", "Județ"),
    ("data_eliberarii", "Data eliberării"),
    ("emitent", "Emitent (SPCLEP/DEPABD)"),
    ("nr_adeverinta", "Nr. adeverință"),
]


def _fields_for(doc_type: str) -> list[tuple[str, str]]:
    if doc_type == DocumentType.BULETIN:
        return BULETIN_FIELDS
    if doc_type == DocumentType.ADEVERINTA_ADRESA:
        return ADEVERINTA_FIELDS
    return DECES_FIELDS


def _confidence_style(conf: float) -> tuple[str, str]:
    """Returnează (border_color_hex, label). Verde ≥0.85, galben ≥0.6, roșu <0.6."""
    if conf is None:
        return ("#475569", "—")
    if conf >= 0.85:
        return ("#22c55e", f"OK  {int(conf * 100)}%")
    if conf >= 0.6:
        return ("#eab308", f"Mediu  {int(conf * 100)}%")
    return ("#ef4444", f"Scăzut  {int(conf * 100)}%")


# ============ WORKER THREAD ============

class ExtractWorker(QObject):
    finished = Signal(dict)   # result dict
    failed = Signal(str)      # error message

    def __init__(self, file_path: str, document_type: str, prefer_offline: bool = False):
        super().__init__()
        self._path = file_path
        self._type = document_type
        self._prefer_offline = prefer_offline

    def run(self):
        try:
            result = extract_document_sync(
                self._path, self._type, prefer_offline=self._prefer_offline
            )
            self.finished.emit(result)
        except Exception as e:
            self.failed.emit(str(e))


# ============ UPLOAD ZONE ============

class UploadZone(QFrame):
    """Zonă drag & drop + file picker + cameră (webcam sau upload cu 'capture')."""

    file_selected = Signal(str)  # path

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("upload_zone")
        self._path: Optional[str] = None

        self.setStyleSheet(
            """
            #upload_zone {
                border: 2px dashed #475569;
                border-radius: 10px;
                background: #0f172a;
                min-height: 260px;
            }
            #upload_zone[hovered="true"] {
                border-color: #0ea5e9;
                background: #0c1a2d;
            }
            #upload_zone QLabel#hint_title { font-size: 15pt; font-weight: 600; color: #e2e8f0; }
            #upload_zone QLabel#hint_sub { color: #94a3b8; font-size: 10pt; }
            """
        )

        v = QVBoxLayout(self)
        v.setContentsMargins(24, 24, 24, 24)
        v.setSpacing(10)
        v.setAlignment(Qt.AlignCenter)

        title = QLabel("📎  Trage un fișier aici sau folosește butoanele de mai jos")
        title.setObjectName("hint_title")
        title.setAlignment(Qt.AlignCenter)

        sub = QLabel("Formate acceptate: JPG · PNG · WEBP · PDF (max 15 MB)")
        sub.setObjectName("hint_sub")
        sub.setAlignment(Qt.AlignCenter)

        btn_row = QHBoxLayout()
        btn_row.setAlignment(Qt.AlignCenter)
        btn_row.setSpacing(10)

        btn_pick = QPushButton("📁  Alege fișier")
        btn_pick.setCursor(QCursor(Qt.PointingHandCursor))
        btn_pick.clicked.connect(self._pick_file)

        btn_cam = QPushButton("📷  Fotografiază (cameră)")
        btn_cam.setCursor(QCursor(Qt.PointingHandCursor))
        btn_cam.clicked.connect(self._capture_camera)

        for b in (btn_pick, btn_cam):
            b.setStyleSheet(
                "QPushButton { padding: 8px 18px; background:#0ea5e9; color:white; "
                "border:none; border-radius:6px; font-weight:600; }"
                "QPushButton:hover { background:#0284c7; }"
            )

        btn_row.addWidget(btn_pick)
        btn_row.addWidget(btn_cam)

        v.addStretch()
        v.addWidget(title)
        v.addWidget(sub)
        v.addSpacing(8)
        v.addLayout(btn_row)
        v.addStretch()

    # -- Drag & drop --
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and self._is_supported(urls[0].toLocalFile()):
                self.setProperty("hovered", True)
                self.style().unpolish(self)
                self.style().polish(self)
                event.acceptProposedAction()
                return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self.setProperty("hovered", False)
        self.style().unpolish(self)
        self.style().polish(self)

    def dropEvent(self, event: QDropEvent) -> None:
        self.setProperty("hovered", False)
        self.style().unpolish(self)
        self.style().polish(self)
        urls = event.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if self._is_supported(path):
            self._path = path
            self.file_selected.emit(path)

    @staticmethod
    def _is_supported(path: str) -> bool:
        return os.path.splitext(path.lower())[1] in (".jpg", ".jpeg", ".png", ".webp", ".pdf")

    def _pick_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Alege document (buletin / certificat)",
            "", "Imagini/PDF (*.png *.jpg *.jpeg *.webp *.pdf)"
        )
        if path:
            self._path = path
            self.file_selected.emit(path)

    def _capture_camera(self):
        """Deschide fereastra de captură webcam. Dacă lipsește, revine la file picker."""
        try:
            from .camera_capture import CameraCaptureDialog
            dlg = CameraCaptureDialog(self)
            if dlg.exec() == QDialog.Accepted and dlg.captured_path:
                self._path = dlg.captured_path
                self.file_selected.emit(dlg.captured_path)
        except Exception as e:
            # fallback la file picker cu instrucțiune
            QMessageBox.information(
                self,
                "Cameră indisponibilă",
                "Camera nu este disponibilă pe acest sistem. "
                "Poți încărca poza făcută cu telefonul folosind butonul „Alege fișier"
                "\n\nDetaliu tehnic: " + str(e),
            )


# ============ FIELD ROW CU CONFIDENCE ============

class FieldRow(QWidget):
    """Un rând: label + input + indicator confidence."""

    def __init__(self, key: str, label: str, parent=None):
        super().__init__(parent)
        self.key = key

        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)

        self.lbl = QLabel(label + ":")
        self.lbl.setMinimumWidth(150)
        self.lbl.setStyleSheet("color:#cbd5e1; font-size:9pt;")

        self.input = QLineEdit()
        self.input.setStyleSheet(
            "QLineEdit { border:1px solid #334155; border-left:4px solid #475569; "
            "padding:5px 8px; background:#0f172a; color:#e2e8f0; border-radius:4px; }"
            "QLineEdit:focus { border-color:#0ea5e9; }"
        )

        self.conf_lbl = QLabel("—")
        self.conf_lbl.setFixedWidth(88)
        self.conf_lbl.setAlignment(Qt.AlignCenter)
        self.conf_lbl.setStyleSheet(
            "QLabel { padding:3px 8px; border-radius:8px; font-size:8pt; "
            "background:#1e293b; color:#94a3b8; font-weight:600; }"
        )

        h.addWidget(self.lbl)
        h.addWidget(self.input, 1)
        h.addWidget(self.conf_lbl)

    def set_value(self, value: str, confidence: float):
        self.input.setText(value or "")
        color, text = _confidence_style(confidence)
        self.conf_lbl.setText(text)
        # aplicăm culoarea pe border-left al input-ului + pe background-ul badge-ului
        self.input.setStyleSheet(
            f"QLineEdit {{ border:1px solid #334155; border-left:4px solid {color}; "
            f"padding:5px 8px; background:#0f172a; color:#e2e8f0; border-radius:4px; }}"
            f"QLineEdit:focus {{ border-color:#0ea5e9; }}"
        )
        self.conf_lbl.setStyleSheet(
            f"QLabel {{ padding:3px 8px; border-radius:8px; font-size:8pt; "
            f"background:{color}22; color:{color}; font-weight:600; }}"
        )

    def value(self) -> str:
        return self.input.text().strip()


# ============ MAIN DIALOG ============

class ScanDialog(QDialog):
    """Dialog complet de scanare AI. Reutilizabil pentru buletin sau certificat deces."""

    def __init__(self, parent, document_type: str, allow_type_change: bool = False):
        super().__init__(parent)
        self.document_type = document_type
        self.allow_type_change = allow_type_change

        titles = {
            DocumentType.BULETIN: "🤖  Scanare AI — Buletin de identitate",
            DocumentType.CERTIFICAT_DECES: "🤖  Scanare AI — Certificat deces / Adeverință",
        }
        self.setWindowTitle(titles.get(document_type, "Scanare AI"))
        self.setMinimumSize(1080, 720)
        self.setStyleSheet("QDialog { background:#020617; }")

        self._source_file: Optional[str] = None
        self._extracted: dict = {}
        self._fields: dict[str, FieldRow] = {}
        self._worker_thread: Optional[QThread] = None
        self._file_action: str = "attach"  # attach | archive | delete
        self._prefer_offline: bool = False

        self._build_ui()

    # ---------------------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(10)

        # --- header ---
        header = QHBoxLayout()
        title = QLabel(self.windowTitle())
        title.setStyleSheet("color:#f1f5f9; font-size:16pt; font-weight:700;")
        self.subtitle = QLabel("Extragere automată a câmpurilor din poză sau PDF")
        self.subtitle.setStyleSheet("color:#94a3b8; font-size:9pt;")
        left_head = QVBoxLayout()
        left_head.addWidget(title)
        left_head.addWidget(self.subtitle)
        header.addLayout(left_head)
        header.addStretch()

        self.ai_badge = QLabel()
        self.ai_badge.setObjectName("scan-ai-status")
        header.addWidget(self.ai_badge)

        self.btn_ai_settings = QPushButton("⚙️  Setări AI")
        self.btn_ai_settings.setObjectName("scan-dialog-ai-settings")
        self.btn_ai_settings.setToolTip("Configurează cheia Gemini, modul de lucru și modelul")
        self.btn_ai_settings.setCursor(QCursor(Qt.PointingHandCursor))
        self.btn_ai_settings.setStyleSheet(
            "QPushButton { background:#0ea5e9; color:white; padding:6px 14px; "
            "border:none; border-radius:6px; font-weight:600; }"
            "QPushButton:hover { background:#0284c7; }"
        )
        self.btn_ai_settings.clicked.connect(self._open_ai_settings)
        header.addWidget(self.btn_ai_settings)
        root.addLayout(header)
        self._refresh_ai_badge()

        # --- type switch (dacă e permis) ---
        # Offline checkbox row
        offline_row = QHBoxLayout()
        offline_row.setSpacing(8)
        self.chk_offline = QCheckBox("🔌  Doar offline (fără internet, folosește Tesseract OCR)")
        self.chk_offline.setToolTip(
            "Bifează dacă nu ai internet sau vrei procesare 100% locală.\n"
            "Precizia va fi mai mică decât cu Gemini AI, dar funcționează oriunde."
        )
        self.chk_offline.setStyleSheet("QCheckBox { color:#cbd5e1; }")
        if not tesseract_available():
            self.chk_offline.setEnabled(False)
            self.chk_offline.setToolTip("Tesseract nu e instalat pe acest sistem — offline indisponibil.")
        self.chk_offline.toggled.connect(self._on_offline_toggled)
        offline_row.addWidget(self.chk_offline)
        offline_row.addStretch()

        # engine badge (afișat după scan)
        self.engine_badge = QLabel()
        self.engine_badge.setStyleSheet(
            "QLabel { padding:2px 10px; border-radius:10px; font-size:8pt; font-weight:600; }"
        )
        self.engine_badge.hide()
        offline_row.addWidget(self.engine_badge)
        root.addLayout(offline_row)

        if self.allow_type_change:
            switch_box = QHBoxLayout()
            switch_box.setSpacing(6)
            lbl_type = QLabel("Tip document:")
            lbl_type.setStyleSheet("color:#94a3b8;")
            self.rb_buletin = QRadioButton("Buletin CI")
            self.rb_deces = QRadioButton("Certificat deces / adeverință")
            self.rb_adeverinta = QRadioButton("Adeverință adresă (anexa CI nou)")
            grp = QButtonGroup(self)
            grp.addButton(self.rb_buletin)
            grp.addButton(self.rb_deces)
            grp.addButton(self.rb_adeverinta)
            if self.document_type == DocumentType.BULETIN:
                self.rb_buletin.setChecked(True)
            elif self.document_type == DocumentType.ADEVERINTA_ADRESA:
                self.rb_adeverinta.setChecked(True)
            else:
                self.rb_deces.setChecked(True)
            self.rb_buletin.toggled.connect(
                lambda on: self._change_type(DocumentType.BULETIN) if on else None
            )
            self.rb_deces.toggled.connect(
                lambda on: self._change_type(DocumentType.CERTIFICAT_DECES) if on else None
            )
            self.rb_adeverinta.toggled.connect(
                lambda on: self._change_type(DocumentType.ADEVERINTA_ADRESA) if on else None
            )
            switch_box.addWidget(lbl_type)
            switch_box.addWidget(self.rb_buletin)
            switch_box.addWidget(self.rb_deces)
            switch_box.addWidget(self.rb_adeverinta)
            switch_box.addStretch()
            root.addLayout(switch_box)

        # --- split: preview stânga | formular dreapta ---
        split = QHBoxLayout()
        split.setSpacing(12)

        # Stânga
        left = QVBoxLayout()
        left.setSpacing(8)
        left_title = QLabel("DOCUMENT ORIGINAL")
        left_title.setStyleSheet("color:#94a3b8; font-size:8pt; font-weight:700; letter-spacing:1px;")
        left.addWidget(left_title)

        self.upload_zone = UploadZone(self)
        self.upload_zone.file_selected.connect(self._on_file_selected)

        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(420, 480)
        self.preview_label.setStyleSheet(
            "background:#0f172a; border:1px solid #1e293b; border-radius:8px; color:#64748b;"
        )
        self.preview_label.hide()

        left.addWidget(self.upload_zone, 1)
        left.addWidget(self.preview_label, 1)

        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setMinimumWidth(420)

        # Dreapta
        right = QVBoxLayout()
        right.setSpacing(8)
        right_title = QLabel("DATE EXTRASE (editabile)")
        right_title.setStyleSheet("color:#94a3b8; font-size:8pt; font-weight:700; letter-spacing:1px;")
        right.addWidget(right_title)

        # Progres
        self.progress_frame = QFrame()
        self.progress_frame.setStyleSheet(
            "QFrame { background:#0f172a; border:1px solid #1e293b; border-radius:8px; padding:14px; }"
        )
        pv = QVBoxLayout(self.progress_frame)
        pv.setSpacing(8)
        self.progress_status = QLabel("Așteaptă un document…")
        self.progress_status.setStyleSheet("color:#e2e8f0; font-size:10pt;")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # indeterminate
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setStyleSheet(
            "QProgressBar { border:none; background:#1e293b; border-radius:3px; }"
            "QProgressBar::chunk { background:#0ea5e9; border-radius:3px; }"
        )
        pv.addWidget(self.progress_status)
        pv.addWidget(self.progress_bar)
        right.addWidget(self.progress_frame)

        # Formular scrollabil
        self.form_scroll = QScrollArea()
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setFrameShape(QFrame.NoFrame)
        self.form_container = QWidget()
        self.form_layout = QVBoxLayout(self.form_container)
        self.form_layout.setSpacing(8)
        self.form_layout.setContentsMargins(4, 4, 4, 4)
        self.form_scroll.setWidget(self.form_container)
        self.form_scroll.setVisible(False)
        right.addWidget(self.form_scroll, 1)

        # Placeholder când nu există date
        self.empty_hint = QLabel(
            "📄  Încarcă un document în stânga.\n"
            "AI-ul va extrage automat câmpurile din poză sau PDF."
        )
        self.empty_hint.setAlignment(Qt.AlignCenter)
        self.empty_hint.setStyleSheet(
            "color:#64748b; font-size:10pt; padding:40px; border:1px dashed #1e293b; border-radius:8px;"
        )
        right.addWidget(self.empty_hint, 1)

        right_widget = QWidget()
        right_widget.setLayout(right)

        split.addWidget(left_widget, 1)
        split.addWidget(right_widget, 1)
        root.addLayout(split, 1)

        # --- footer: acțiune fișier + butoane ---
        footer = QVBoxLayout()

        # opțiuni fișier
        file_group = QGroupBox("Ce facem cu fișierul original după confirmare?")
        file_group.setStyleSheet(
            "QGroupBox { color:#cbd5e1; border:1px solid #1e293b; border-radius:6px; margin-top:6px; padding-top:10px; }"
            "QGroupBox::title { subcontrol-origin: margin; left:8px; padding:0 4px; }"
        )
        fg = QHBoxLayout(file_group)
        self.rb_attach = QRadioButton("📎  Atașează la profil (recomandat)")
        self.rb_archive = QRadioButton("🗄  Salvează în arhivă generală")
        self.rb_delete = QRadioButton("🗑  Șterge fișierul după extragere")
        self.rb_attach.setChecked(True)
        for rb in (self.rb_attach, self.rb_archive, self.rb_delete):
            rb.setStyleSheet("QRadioButton { color:#cbd5e1; padding:4px 8px; }")
        self.rb_attach.toggled.connect(lambda on: on and setattr(self, "_file_action", "attach"))
        self.rb_archive.toggled.connect(lambda on: on and setattr(self, "_file_action", "archive"))
        self.rb_delete.toggled.connect(lambda on: on and setattr(self, "_file_action", "delete"))
        fg.addWidget(self.rb_attach)
        fg.addWidget(self.rb_archive)
        fg.addWidget(self.rb_delete)
        fg.addStretch()
        footer.addWidget(file_group)

        # butoane
        btn_row = QHBoxLayout()
        self.btn_rescan = QPushButton("🔄  Reșterge / Alt fișier")
        self.btn_rescan.clicked.connect(self._reset)
        self.btn_rescan.setEnabled(False)

        self.btn_ok = QPushButton("✅  Confirmă și continuă")
        self.btn_ok.setStyleSheet(
            "QPushButton { background:#22c55e; color:white; padding:8px 20px; "
            "border:none; border-radius:6px; font-weight:700; }"
            "QPushButton:hover { background:#16a34a; }"
            "QPushButton:disabled { background:#334155; color:#64748b; }"
        )
        self.btn_ok.setEnabled(False)
        self.btn_ok.clicked.connect(self._on_confirm)

        self.btn_cancel = QPushButton("Anulează")
        self.btn_cancel.clicked.connect(self.reject)

        btn_row.addWidget(self.btn_rescan)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_cancel)
        btn_row.addWidget(self.btn_ok)
        footer.addLayout(btn_row)

        root.addLayout(footer)

    # ---------------------------------------------------------------
    def _refresh_ai_badge(self):
        active = ai_config.is_ai_enabled()
        color = "#22c55e" if active else "#f59e0b"
        bg = "rgba(34,197,94,0.12)" if active else "rgba(245,158,11,0.12)"
        icon = "🟢" if active else "🟡"
        self.ai_badge.setText(f"{icon}  {ai_config.status_text()}")
        self.ai_badge.setStyleSheet(
            f"color:{color}; background:{bg}; padding:4px 10px; "
            f"border-radius:10px; font-weight:600; font-size:9pt;"
        )

    def _open_ai_settings(self):
        try:
            from .ai_settings_dialog import AISettingsDialog
        except Exception as e:
            QMessageBox.critical(self, "Eroare", f"Nu pot deschide Setări AI: {e}")
            return
        dlg = AISettingsDialog(self)
        dlg.settings_saved.connect(self._refresh_ai_badge)
        dlg.exec()
        self._refresh_ai_badge()

    # ---------------------------------------------------------------
    def _change_type(self, new_type: str):
        if new_type == self.document_type:
            return
        self.document_type = new_type
        titles = {
            DocumentType.BULETIN: "🤖  Scanare AI — Buletin de identitate",
            DocumentType.CERTIFICAT_DECES: "🤖  Scanare AI — Certificat deces / Adeverință",
            DocumentType.ADEVERINTA_ADRESA: "🤖  Scanare AI — Adeverință de adresă",
        }
        self.setWindowTitle(titles.get(new_type, "Scanare AI"))
        # dacă am date extrase deja, ștergem formularul (schema e diferită)
        self._clear_form()
        # dacă avem deja fișier, îl re-scanăm pe noul tip
        if self._source_file:
            self._start_extraction()

    def _clear_form(self):
        for i in reversed(range(self.form_layout.count())):
            w = self.form_layout.itemAt(i).widget()
            if w:
                w.setParent(None)
        self._fields.clear()
        self.form_scroll.setVisible(False)
        self.empty_hint.setVisible(True)
        self.btn_ok.setEnabled(False)

    # ---------------------------------------------------------------
    def _on_file_selected(self, path: str):
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "Eroare", f"Fișierul nu există:\n{path}")
            return
        if os.path.getsize(path) > 15 * 1024 * 1024:
            QMessageBox.warning(self, "Eroare", "Fișier prea mare (max 15 MB).")
            return
        self._source_file = path

        # Preview
        self._render_preview(path)

        # Start scan
        self._start_extraction()

    def _render_preview(self, path: str):
        ext = os.path.splitext(path)[1].lower()
        self.upload_zone.hide()
        self.preview_label.show()
        if ext in (".png", ".jpg", ".jpeg", ".webp"):
            img = QImage(path)
            if img.isNull():
                self.preview_label.setText("Preview indisponibil pentru acest fișier.")
                return
            pix = QPixmap.fromImage(img)
            self.preview_label.setPixmap(
                pix.scaled(
                    self.preview_label.width() - 12,
                    self.preview_label.height() - 12,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )
        elif ext == ".pdf":
            # Randează prima pagină din PDF folosind PyMuPDF (dacă e disponibil)
            try:
                import fitz  # PyMuPDF
                doc = fitz.open(path)
                if doc.page_count > 0:
                    page = doc.load_page(0)
                    pix = page.get_pixmap(dpi=120)
                    img = QImage(
                        pix.samples, pix.width, pix.height, pix.stride,
                        QImage.Format_RGB888,
                    ).copy()
                    doc.close()
                    qpix = QPixmap.fromImage(img)
                    self.preview_label.setPixmap(
                        qpix.scaled(
                            self.preview_label.width() - 12,
                            self.preview_label.height() - 12,
                            Qt.KeepAspectRatio,
                            Qt.SmoothTransformation,
                        )
                    )
                    return
                doc.close()
            except Exception as e:
                print(f"[scan_dialog] preview PDF eșuat: {e}")
            name = os.path.basename(path)
            self.preview_label.setText(
                f"📄  {name}\n\n(PDF — preview indisponibil)"
            )
        else:
            name = os.path.basename(path)
            self.preview_label.setText(
                f"📄  {name}\n\n(preview indisponibil pentru acest format)"
            )

    # ---------------------------------------------------------------
    def _on_offline_toggled(self, on: bool):
        self._prefer_offline = on
        # dacă avem deja un fișier și scanăm, re-scanează cu noul engine
        if self._source_file and self._fields:
            self._start_extraction()

    def _start_extraction(self):
        if not self._source_file:
            return
        use_offline = self._prefer_offline or not ai_config.is_ai_enabled()
        engine_label = "🔌 Tesseract (offline)" if use_offline else "🤖 Gemini AI"
        self.progress_status.setText(f"🔍  {engine_label} analizează documentul…")
        self.progress_bar.setVisible(True)
        self.btn_ok.setEnabled(False)
        self.btn_rescan.setEnabled(False)
        self.empty_hint.setVisible(False)
        self.engine_badge.hide()

        # thread worker
        self._worker_thread = QThread(self)
        self._worker = ExtractWorker(self._source_file, self.document_type, prefer_offline=self._prefer_offline)
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_extract_finished)
        self._worker.failed.connect(self._on_extract_failed)
        # cleanup
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker.failed.connect(self._worker_thread.quit)
        self._worker_thread.finished.connect(self._worker.deleteLater)
        self._worker_thread.finished.connect(self._worker_thread.deleteLater)
        self._worker_thread.start()

    def _on_extract_finished(self, result: dict):
        self._extracted = result or {}
        fields = self._extracted.get("fields", {}) or {}
        engine = self._extracted.get("engine", "gemini")
        self.progress_bar.setVisible(False)

        # engine badge
        if engine == "tesseract":
            self.engine_badge.setText("🔌 Tesseract (offline)")
            self.engine_badge.setStyleSheet(
                "QLabel { padding:2px 10px; border-radius:10px; font-size:8pt; "
                "background:#eab30822; color:#eab308; font-weight:600; }"
            )
            note = ""
            if self._extracted.get("_gemini_error"):
                note = " (Gemini a eșuat, folosim OCR local)"
            self.progress_status.setText(f"✅  Extras cu Tesseract{note} — verifică datele.")
        else:
            self.engine_badge.setText("🤖 Gemini 3 Flash")
            self.engine_badge.setStyleSheet(
                "QLabel { padding:2px 10px; border-radius:10px; font-size:8pt; "
                "background:#22c55e22; color:#22c55e; font-weight:600; }"
            )
            self.progress_status.setText("✅  Extras cu Gemini AI — verifică datele.")
        self.engine_badge.show()
        self.progress_status.setStyleSheet("color:#e2e8f0; font-size:10pt; font-weight:600;")

        # Populăm formularul
        self._clear_form()
        for key, label in _fields_for(self.document_type):
            row = FieldRow(key, label)
            data = fields.get(key, {}) if isinstance(fields.get(key), dict) else {}
            row.set_value(str(data.get("value", "") or ""), float(data.get("confidence", 0) or 0))
            self.form_layout.addWidget(row)
            self._fields[key] = row
        self.form_layout.addStretch()
        self.form_scroll.setVisible(True)
        self.empty_hint.setVisible(False)

        self.btn_ok.setEnabled(True)
        self.btn_rescan.setEnabled(True)

    def _on_extract_failed(self, msg: str):
        self.progress_bar.setVisible(False)
        self.progress_status.setText(f"❌  Eroare la extragere: {msg[:220]}")
        self.progress_status.setStyleSheet("color:#ef4444; font-size:10pt; font-weight:600;")
        self.btn_rescan.setEnabled(True)
        QMessageBox.warning(self, "Extragere eșuată", msg)

    # ---------------------------------------------------------------
    def _reset(self):
        self._source_file = None
        self._extracted = {}
        self._clear_form()
        self.preview_label.hide()
        self.preview_label.clear()
        self.upload_zone.show()
        self.progress_status.setText("Așteaptă un document…")
        self.progress_status.setStyleSheet("color:#e2e8f0; font-size:10pt;")
        self.btn_rescan.setEnabled(False)

    # ---------------------------------------------------------------
    def _on_confirm(self):
        # validare minimă
        data = self.get_extracted_data()
        if not data.get("nume") and not data.get("prenume"):
            QMessageBox.warning(self, "Date incomplete", "Numele sau prenumele sunt obligatorii.")
            return
        self.accept()

    # ---------------------------------------------------------------
    def get_extracted_data(self) -> dict:
        """Returnează dict flat cu valorile finale (editate de user)."""
        return {key: row.value() for key, row in self._fields.items()}

    def get_source_file(self) -> Optional[str]:
        return self._source_file

    def get_file_action(self) -> str:
        return self._file_action

    def get_document_type(self) -> str:
        return self.document_type
