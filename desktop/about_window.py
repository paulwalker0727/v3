"""
about_window.py — Fereastra "Despre" cu detalii licență, versiune și contact.

Se apelează dintr-un meniu Ajutor → Despre în CemeteryUI.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QGridLayout,
)

PAL = {"bg": "#0A0A0B", "surface": "#121214", "border": "#27272A",
       "text": "#FFFFFF", "muted": "#A1A1AA", "muted2": "#71717A"}


class AboutWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cimitir — Despre")
        self.setFixedSize(520, 560)
        self.setModal(True)
        self.setStyleSheet(f"""
            QDialog {{ background-color: {PAL['bg']}; color: {PAL['text']}; }}
            QLabel {{ color: {PAL['text']}; }}
            QPushButton {{
                background-color: {PAL['surface']};
                border: 1px solid {PAL['border']};
                border-radius: 6px;
                padding: 8px 16px;
                color: {PAL['text']};
            }}
            QPushButton:hover {{ background-color: {PAL['border']}; }}
        """)
        self._build_ui()

    def _kv(self, label, value):
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {PAL['muted2']}; font-size: 8pt; letter-spacing: 2px; font-weight: 600;")
        val = QLabel(str(value) if value else "—")
        val.setStyleSheet(f"color: {PAL['text']}; font-family: monospace; font-size: 10pt;")
        val.setTextInteractionFlags(Qt.TextSelectableByMouse)
        return lbl, val

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 26, 32, 22)
        root.setSpacing(6)

        # Overline
        ov = QLabel("PROIECT CIMITIR")
        ov.setStyleSheet(f"color: {PAL['muted2']}; font-size: 9pt; letter-spacing: 4px;")
        root.addWidget(ov)

        # Title
        try:
            from security.config import APP_VERSION
        except Exception:
            APP_VERSION = "1.0.0"

        title = QLabel(f"Cimitir v{APP_VERSION}")
        tf = QFont(); tf.setPointSize(22); tf.setBold(True)
        title.setFont(tf)
        root.addWidget(title)

        sub = QLabel("Sistem profesional de gestiune cimitire · PySide6 · SQLite")
        sub.setStyleSheet(f"color: {PAL['muted']}; font-size: 9pt;")
        root.addWidget(sub)

        root.addSpacing(20)

        # License panel
        lic_frame = QFrame()
        lic_frame.setStyleSheet(f"background-color: {PAL['surface']}; border: 1px solid {PAL['border']}; border-radius: 8px;")
        lic_lay = QGridLayout(lic_frame)
        lic_lay.setContentsMargins(18, 16, 18, 16)
        lic_lay.setHorizontalSpacing(24)
        lic_lay.setVerticalSpacing(10)

        # Fetch license info (din noul modul license/ — Ed25519 offline)
        info = None
        hw_short = ""
        try:
            from license import verifica_licenta_curenta, get_hwid, LICENSE_FILE
            rez = verifica_licenta_curenta()
            hw_short = get_hwid()[:16] + "…"
            if rez.ok:
                info = {
                    "client": rez.client,
                    "expira": rez.expira or "Permanent",
                    "status": "Activă",
                    "file": str(LICENSE_FILE),
                }
            else:
                info = {"status": f"Invalidă: {rez.motiv}"}
        except Exception:
            pass

        rows = [
            ("STATUS", info.get("status") if info else "Necunoscut"),
            ("CLIENT", info.get("client") if info else "—"),
            ("EXPIRĂ", info.get("expira") if info else "—"),
            ("FIȘIER", info.get("file") if info else "—"),
            ("HARDWARE ID", hw_short or "—"),
        ]
        for i, (l, v) in enumerate(rows):
            lbl, val = self._kv(l, v)
            lic_lay.addWidget(lbl, i, 0)
            lic_lay.addWidget(val, i, 1)

        root.addWidget(lic_frame)

        root.addSpacing(16)

        # Buton „Reactivare" — util pentru transfer licență pe alt PC / re-încărcare
        react_row = QHBoxLayout()
        reactivate_btn = QPushButton("🔑  Reactivare / Încarcă altă licență")
        reactivate_btn.setCursor(Qt.PointingHandCursor)
        reactivate_btn.clicked.connect(self._reactivate)
        react_row.addWidget(reactivate_btn)
        react_row.addStretch()
        root.addLayout(react_row)

        root.addStretch()

        # Contact & Company
        support = QLabel(
            "<div style='color: #A1A1AA; font-size: 9pt; line-height: 150%;'>"
            "<b style='color: white;'>Suport</b> · support@cimitir.ro<br>"
            "<b style='color: white;'>Web</b> · https://cimitir.ro<br>"
            "<b style='color: white;'>Copyright</b> · © 2026 Toate drepturile rezervate.<br>"
            "<span style='color: #71717A;'>Această aplicație este protejată prin licențiere. "
            "Distribuirea neautorizată este interzisă.</span>"
            "</div>"
        )
        support.setWordWrap(True)
        support.setTextInteractionFlags(Qt.TextBrowserInteraction)
        root.addWidget(support)

        root.addSpacing(14)

        # Close button
        row = QHBoxLayout()
        row.addStretch()
        close_btn = QPushButton("Închide")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        root.addLayout(row)

    def _reactivate(self):
        """Deschide dialogul de activare pentru încărcarea unei licențe noi."""
        try:
            from license.activation_dialog import ActivationDialog
            from license import verifica_licenta_curenta
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Eroare", f"Nu pot deschide activarea: {e}")
            return
        dlg = ActivationDialog(motiv="Reactivare / schimbare licență", parent=self)
        dlg.exec()
        # închide fereastra „Despre" ca să se redeschidă cu datele noi
        if dlg.activat:
            self.accept()
