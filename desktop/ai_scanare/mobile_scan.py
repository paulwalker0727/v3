"""Dialog QR pentru scanare cu telefonul mobil (prin web_server.py LAN).

Flow:
  1. Desktop deschide acest dialog → creează o sesiune unică
  2. QR code afișat conține URL public LAN: http://<ip>:<port>/mobile-scan?session=XXX
  3. Telefonul citește QR → deschide pagina → face poza → upload
  4. Serverul rulează Gemini, salvează rezultatul sub session_id
  5. Desktop face polling la /api/ai-scan-result/<session_id>
  6. Când datele apar → completează formularul

Dialog-ul returnează dict-ul cu câmpuri extrase (același format ca ScanDialog).
"""
from __future__ import annotations

import io
import os
import secrets
import socket
import tempfile
from typing import Optional

import requests
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from .ai_client import DocumentType


def _get_lan_ip() -> str:
    """Determină IP-ul LAN al mașinii curente."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _make_qr_pixmap(text: str, size: int = 260) -> Optional[QPixmap]:
    try:
        import qrcode
        img = qrcode.make(text, box_size=8, border=2)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        pix = QPixmap()
        pix.loadFromData(buf.read())
        return pix.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    except Exception as e:
        print(f"[qr] {e}")
        return None


class MobileScanDialog(QDialog):
    """Dialog cu QR pentru scanare de pe telefon.

    Necesită ca web_server.py să ruleze pe același calculator (via launcher.py).
    """

    def __init__(self, parent=None, doc_type: str = DocumentType.BULETIN,
                 host: str = None, port: int = 8000):
        super().__init__(parent)
        self.setWindowTitle("📱  Scanare de pe telefon")
        self.setMinimumSize(480, 620)
        self.setStyleSheet("QDialog { background:#020617; color:#e2e8f0; }")

        self.doc_type = doc_type
        self.host = host or _get_lan_ip()
        self.port = port
        self.session_id = secrets.token_urlsafe(16)
        self._extracted: Optional[dict] = None
        self._source_file: Optional[str] = None

        self._build_ui()
        self._start_polling()

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 24, 24, 24)
        v.setSpacing(12)
        v.setAlignment(Qt.AlignHCenter)

        title = QLabel("Scanare de pe telefon")
        title.setStyleSheet("color:#f1f5f9; font-size:16pt; font-weight:700;")
        title.setAlignment(Qt.AlignCenter)
        v.addWidget(title)

        sub = QLabel(
            "1. Deschide camera pe telefon și scanează codul QR de mai jos\n"
            "2. Fă poza documentului (buletin sau certificat deces)\n"
            "3. Așteaptă câteva secunde — datele apar aici automat"
        )
        sub.setStyleSheet("color:#94a3b8; font-size:9pt;")
        sub.setAlignment(Qt.AlignCenter)
        sub.setWordWrap(True)
        v.addWidget(sub)

        # Type + port
        opts = QHBoxLayout()
        opts.setAlignment(Qt.AlignCenter)
        opts.addWidget(QLabel("Tip:"))
        self.cmb_type = QComboBox()
        self.cmb_type.addItem("Buletin CI", DocumentType.BULETIN)
        self.cmb_type.addItem("Certificat deces", DocumentType.CERTIFICAT_DECES)
        if self.doc_type == DocumentType.CERTIFICAT_DECES:
            self.cmb_type.setCurrentIndex(1)
        self.cmb_type.currentIndexChanged.connect(self._rebuild_qr)
        opts.addWidget(self.cmb_type)
        opts.addSpacing(12)
        opts.addWidget(QLabel("Port:"))
        self.spn_port = QSpinBox()
        self.spn_port.setRange(1024, 65535)
        self.spn_port.setValue(self.port)
        self.spn_port.valueChanged.connect(self._rebuild_qr)
        opts.addWidget(self.spn_port)
        v.addLayout(opts)

        # QR
        self.qr_lbl = QLabel()
        self.qr_lbl.setAlignment(Qt.AlignCenter)
        self.qr_lbl.setStyleSheet(
            "background:white; padding:12px; border-radius:8px; border:1px solid #334155;"
        )
        self.qr_lbl.setFixedSize(300, 300)
        v.addWidget(self.qr_lbl, alignment=Qt.AlignCenter)

        # URL text
        self.url_lbl = QLabel()
        self.url_lbl.setStyleSheet(
            "color:#0ea5e9; font-family:monospace; font-size:9pt; padding:6px; "
            "background:#0f172a; border-radius:4px;"
        )
        self.url_lbl.setAlignment(Qt.AlignCenter)
        self.url_lbl.setWordWrap(True)
        self.url_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.url_lbl)

        # Status
        self.status_lbl = QLabel("⏳  Aștept poza de pe telefon…")
        self.status_lbl.setStyleSheet("color:#94a3b8; font-size:10pt; padding:8px;")
        self.status_lbl.setAlignment(Qt.AlignCenter)
        v.addWidget(self.status_lbl)

        v.addStretch()

        btn_row = QHBoxLayout()
        self.btn_cancel = QPushButton("Anulează")
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_cancel)
        v.addLayout(btn_row)

        self._rebuild_qr()

    def _rebuild_qr(self):
        self.port = self.spn_port.value()
        self.doc_type = self.cmb_type.currentData()
        url = f"http://{self.host}:{self.port}/mobile-scan?session={self.session_id}&type={self.doc_type}"
        self.url_lbl.setText(url)
        pix = _make_qr_pixmap(url, size=280)
        if pix:
            self.qr_lbl.setPixmap(pix)
        else:
            self.qr_lbl.setText("(instalează qrcode: pip install qrcode)")

    def _start_polling(self):
        self._timer = QTimer(self)
        self._timer.setInterval(1500)  # 1.5s
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    def _poll(self):
        try:
            url = f"http://{self.host}:{self.port}/api/ai-scan-result/{self.session_id}"
            r = requests.get(url, timeout=2)
            if r.status_code == 200:
                data = r.json()
                if data.get("status") == "done":
                    self._on_result(data)
                elif data.get("status") == "processing":
                    self.status_lbl.setText("🔍  Gemini analizează documentul…")
        except Exception:
            # server offline sau tocmai a pornit; continuă polling silențios
            pass

    def _on_result(self, data: dict):
        self._timer.stop()
        self._extracted = data.get("extracted", {})
        self._source_file = data.get("file_path")
        self.status_lbl.setText("✅  Date primite! Se închide dialogul…")
        QTimer.singleShot(600, self.accept)

    def get_extracted(self) -> Optional[dict]:
        """Returnează dict-ul cu fields (același format ca extract_document_sync)."""
        return self._extracted

    def get_source_file(self) -> Optional[str]:
        return self._source_file

    def get_document_type(self) -> str:
        return self.doc_type

    def closeEvent(self, event):
        try:
            self._timer.stop()
        except Exception:
            pass
        # spunem serverului să șteargă sesiunea (best-effort)
        try:
            requests.delete(
                f"http://{self.host}:{self.port}/api/ai-scan-result/{self.session_id}",
                timeout=1,
            )
        except Exception:
            pass
        event.accept()
