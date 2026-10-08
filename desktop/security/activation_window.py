"""
security/activation_window.py — Fereastră de activare licență (PySide6).

UX:
  - un input mare pentru cheia CIM-XXXX-XXXX-XXXX-XXXX
  - buton "Activează" → apel la license_client.activate_license()
  - afișează erori clare (invalid, revocată, max devices depășit, offline)
  - la succes → închide cu Accepted și expune license_payload
  - buton "Am nevoie de ajutor" → afișează email de suport
"""
import re

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFrame, QMessageBox,
)

from . import license_client
from .config import APP_NAME, APP_VERSION
from .hardware_id import get_hardware_id, get_device_name


PAL = {
    "bg": "#0A0A0B", "surface": "#121214", "border": "#27272A",
    "text": "#FFFFFF", "muted": "#A1A1AA", "muted2": "#71717A",
    "primary": "#FFFFFF", "danger": "#DC2626", "success": "#34D399",
}


class _ActivateWorker(QThread):
    ok = Signal(dict)
    err = Signal(str)

    def __init__(self, key: str, parent=None):
        super().__init__(parent)
        self.key = key

    def run(self):
        try:
            cache = license_client.activate_license(self.key)
            self.ok.emit(cache)
        except license_client.LicenseError as e:
            self.err.emit(str(e))
        except Exception as e:
            self.err.emit(f"Eroare neașteptată: {e}")


class ActivationWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — Activare Licență")
        self.setFixedSize(560, 480)
        self.setModal(True)
        self.setStyleSheet(f"""
            QDialog {{ background-color: {PAL['bg']}; color: {PAL['text']}; }}
            QLabel {{ color: {PAL['text']}; }}
            QLineEdit {{
                background-color: {PAL['bg']};
                border: 1px solid {PAL['border']};
                border-radius: 6px;
                padding: 10px 12px;
                color: {PAL['text']};
                font-family: 'JetBrains Mono', 'Consolas', monospace;
                font-size: 13pt;
                letter-spacing: 2px;
            }}
            QLineEdit:focus {{ border-color: #52525B; }}
            QPushButton#primary {{
                background-color: {PAL['primary']};
                color: #000000;
                border: none;
                border-radius: 6px;
                padding: 12px 24px;
                font-weight: 700;
                font-size: 11pt;
            }}
            QPushButton#primary:hover {{ background-color: #E4E4E7; }}
            QPushButton#primary:disabled {{ background-color: #3F3F46; color: #71717A; }}
            QPushButton#link {{
                background: transparent;
                border: none;
                color: {PAL['muted']};
                text-decoration: underline;
                padding: 6px;
            }}
            QPushButton#link:hover {{ color: {PAL['text']}; }}
        """)
        self.license_payload = None
        self._worker = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 32, 40, 28)
        root.setSpacing(0)

        # Header
        overline = QLabel("PROIECT CIMITIR · v" + APP_VERSION)
        overline.setStyleSheet(f"color: {PAL['muted2']}; letter-spacing: 4px; font-size: 9pt;")
        root.addWidget(overline)

        title = QLabel("Activare Licență")
        tf = QFont(); tf.setPointSize(24); tf.setBold(True)
        title.setFont(tf)
        title.setStyleSheet("color: #FFFFFF; margin-top: 6px;")
        root.addWidget(title)

        subtitle = QLabel(
            "Introdu cheia de licență primită pe email pentru a activa aplicația "
            "pe acest calculator. Cheia este legată de hardware-ul acestui dispozitiv."
        )
        subtitle.setStyleSheet(f"color: {PAL['muted']}; font-size: 10pt; margin-top: 8px;")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        root.addSpacing(24)

        # Input label
        lbl = QLabel("CHEIE LICENȚĂ")
        lbl.setStyleSheet(f"color: {PAL['muted2']}; letter-spacing: 3px; font-size: 8pt; font-weight: 600;")
        root.addWidget(lbl)
        root.addSpacing(6)

        # Input
        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("CIM-XXXX-XXXX-XXXX-XXXX")
        self.key_input.setMaxLength(23)  # CIM-XXXX-XXXX-XXXX-XXXX = 23 chars
        self.key_input.textChanged.connect(self._on_text_changed)
        self.key_input.returnPressed.connect(self._activate)
        root.addWidget(self.key_input)

        # Status
        self.status = QLabel("")
        self.status.setStyleSheet(f"color: {PAL['muted2']}; font-size: 9pt; margin-top: 8px;")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        root.addStretch()

        # Device info
        info = QFrame()
        info.setStyleSheet(f"background-color: {PAL['surface']}; border: 1px solid {PAL['border']}; border-radius: 6px;")
        info_lay = QVBoxLayout(info)
        info_lay.setContentsMargins(14, 10, 14, 10)
        info_lay.setSpacing(2)
        d1 = QLabel(f"Device: <b>{get_device_name()}</b>")
        d1.setStyleSheet(f"color: {PAL['text']}; font-size: 9pt;")
        d2 = QLabel(f"Hardware ID: {get_hardware_id()[:20]}…")
        d2.setStyleSheet(f"color: {PAL['muted2']}; font-family: monospace; font-size: 8pt;")
        info_lay.addWidget(d1); info_lay.addWidget(d2)
        root.addWidget(info)

        root.addSpacing(20)

        # Buttons
        btns = QHBoxLayout()
        btns.setSpacing(10)

        help_btn = QPushButton("Am nevoie de ajutor")
        help_btn.setObjectName("link")
        help_btn.setCursor(Qt.PointingHandCursor)
        help_btn.clicked.connect(self._help)
        btns.addWidget(help_btn)

        btns.addStretch()

        self.activate_btn = QPushButton("Activează")
        self.activate_btn.setObjectName("primary")
        self.activate_btn.setCursor(Qt.PointingHandCursor)
        self.activate_btn.setEnabled(False)
        self.activate_btn.clicked.connect(self._activate)
        btns.addWidget(self.activate_btn)

        root.addLayout(btns)

    def _on_text_changed(self, txt):
        clean = txt.strip().upper()
        # auto-format: dacă userul lipesc "CIMABCDEFG...", inserează liniuțe
        clean_stripped = clean.replace("-", "").replace(" ", "")
        if len(clean_stripped) >= 4 and not clean_stripped.startswith("CIM"):
            # nu forțăm — poate userul are alt prefix, dar validăm la submit
            pass
        # detectare format complet
        m = re.match(r"^CIM(-?)([A-Z0-9]{4})\1([A-Z0-9]{4})\1([A-Z0-9]{4})\1([A-Z0-9]{4})$", clean)
        self.activate_btn.setEnabled(bool(m))
        if clean and not m:
            self.status.setText("Format: CIM-XXXX-XXXX-XXXX-XXXX (5 blocuri de 4 caractere)")
            self.status.setStyleSheet(f"color: {PAL['muted2']}; font-size: 9pt;")
        else:
            self.status.setText("")

    def _activate(self):
        key = self.key_input.text().strip().upper()
        if not key:
            return
        self.activate_btn.setEnabled(False)
        self.activate_btn.setText("Activez…")
        self.status.setText("Se conectează la serverul de licențe…")
        self.status.setStyleSheet(f"color: {PAL['muted']}; font-size: 9pt;")

        self._worker = _ActivateWorker(key, self)
        self._worker.ok.connect(self._on_ok)
        self._worker.err.connect(self._on_err)
        self._worker.start()

    def _on_ok(self, cache):
        self.license_payload = cache
        self.status.setText("Licență activată cu succes.")
        self.status.setStyleSheet(f"color: {PAL['success']}; font-size: 9pt;")
        self.activate_btn.setText("Deschide Aplicația")
        self.activate_btn.setEnabled(True)
        self.activate_btn.clicked.disconnect()
        self.activate_btn.clicked.connect(self.accept)
        # închide automat după 1.5s
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1500, self.accept)

    def _on_err(self, msg):
        self.status.setText(msg)
        self.status.setStyleSheet(f"color: #F87171; font-size: 9pt;")
        self.activate_btn.setEnabled(True)
        self.activate_btn.setText("Activează")

    def _help(self):
        QMessageBox.information(
            self,
            "Ajutor Activare",
            "Dacă întâmpini probleme la activare:\n\n"
            "1. Verifică că ai copiat cheia corect (nu are spații în plus).\n"
            "2. Asigură-te că ai conexiune la internet activă.\n"
            "3. Dacă cheia funcționa pe alt PC → contactează suportul pentru "
            "transfer de licență (activare pe device nou necesită revocarea vechiului).\n"
            "4. Trimite un email către:\n"
            "   support@cimitir.ro\n"
            "   cu cheia + Hardware ID afișat mai jos."
        )
