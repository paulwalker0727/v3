# login_window.py
"""
Fereastră de autentificare modernă, cu design centrat pe brand.

Layout: split în 2 coloane —
    - Stânga: brand panel (gradient + titlu + descriere + credențiale de test)
    - Dreapta: formular login (utilizator, parolă, buton, mesaj eroare)
"""

from typing import Optional, Dict

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QPixmap, QPainter, QLinearGradient, QColor, QBrush
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFrame, QSizePolicy, QSpacerItem, QGraphicsDropShadowEffect,
)

import auth
from theme import get_palette


class _BrandPanel(QFrame):
    """Panoul din stânga — gradient + branding."""
    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self._pal = palette
        self.setFixedWidth(380)
        self.setAttribute(Qt.WA_StyledBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(16)

        # icon/logo
        logo = QLabel("⚱")
        logo_font = QFont()
        logo_font.setPointSize(48)
        logo.setFont(logo_font)
        logo.setStyleSheet("color: white; background: transparent; border: none;")

        # brand title
        title = QLabel("Cimitir")
        tf = QFont()
        tf.setPointSize(28)
        tf.setBold(True)
        title.setFont(tf)
        title.setStyleSheet("color: white; background: transparent; border: none;")

        subtitle = QLabel("Sistem de administrare\nlocuri de veci")
        sf = QFont()
        sf.setPointSize(11)
        subtitle.setFont(sf)
        subtitle.setStyleSheet("color: rgba(255,255,255,0.85); background: transparent; border: none;")

        # spacer
        layout.addWidget(logo)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacerItem(QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding))

        # features
        features_title = QLabel("CARACTERISTICI")
        ft = QFont()
        ft.setPointSize(9)
        ft.setBold(True)
        features_title.setFont(ft)
        features_title.setStyleSheet("color: rgba(255,255,255,0.6); background: transparent; border: none; letter-spacing: 2px;")
        layout.addWidget(features_title)

        for icon, txt in [
            ("🗺", "Editor hartă interactiv"),
            ("👥", "Gestionare concesionari"),
            ("📄", "Contracte automatizate"),
            ("🕯", "Registrul înhumaților"),
        ]:
            row = QHBoxLayout()
            i = QLabel(icon)
            f = QFont(); f.setPointSize(14); i.setFont(f)
            i.setStyleSheet("color: white; background: transparent; border: none;")
            t = QLabel(txt)
            t.setStyleSheet("color: rgba(255,255,255,0.95); background: transparent; border: none;")
            row.addWidget(i)
            row.addWidget(t)
            row.addStretch()
            wrap = QFrame()
            wrap.setLayout(row)
            wrap.setStyleSheet("background: transparent; border: none;")
            layout.addWidget(wrap)

        # bottom credentials hint
        layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))
        hint = QLabel(
            "<b style='color:white'>Credențiale de test</b><br>"
            "<span style='color:rgba(255,255,255,0.85)'>Developer: <b>admin</b> / admin123<br>"
            "User: <b>user</b> / user123</span>"
        )
        hint.setStyleSheet("background: rgba(255,255,255,0.08); "
                           "padding: 12px; border-radius: 8px;")
        layout.addWidget(hint)

    def paintEvent(self, event):
        # gradient background
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        gradient = QLinearGradient(0, 0, rect.width(), rect.height())
        gradient.setColorAt(0, QColor("#0284c7"))   # sky-600
        gradient.setColorAt(0.5, QColor("#0369a1"))  # sky-700
        gradient.setColorAt(1, QColor("#1e293b"))   # slate-800
        painter.fillRect(rect, QBrush(gradient))
        super().paintEvent(event)


class LoginWindow(QDialog):
    """
    Dialog modal de login. La accept, `self.user` conține dict-ul cu date
    despre utilizator: {id, username, role}.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.user: Optional[Dict] = None
        self._palette = get_palette("dark")

        self.setWindowTitle("Autentificare - Cimitir")
        self.setFixedSize(880, 560)
        self.setWindowFlag(Qt.FramelessWindowHint, False)

        # -----------------------------------------------------------
        # Root layout — 2 coloane
        # -----------------------------------------------------------
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 1) Brand panel (stânga)
        root.addWidget(_BrandPanel(self._palette))

        # 2) Form panel (dreapta)
        form_panel = QFrame()
        form_panel.setStyleSheet(f"background-color: {self._palette['bg']};")
        form_layout = QVBoxLayout(form_panel)
        form_layout.setContentsMargins(60, 60, 60, 60)
        form_layout.setSpacing(0)
        form_layout.addSpacerItem(QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding))

        # Header
        h1 = QLabel("Bun venit!")
        f_h1 = QFont(); f_h1.setPointSize(22); f_h1.setBold(True)
        h1.setFont(f_h1)
        h1.setStyleSheet(f"color: {self._palette['text']}; background: transparent; border: none;")

        h2 = QLabel("Autentifică-te pentru a continua")
        h2.setStyleSheet(f"color: {self._palette['text_muted']}; background: transparent; border: none; font-size: 11pt;")

        form_layout.addWidget(h1)
        form_layout.addSpacing(6)
        form_layout.addWidget(h2)
        form_layout.addSpacing(32)

        # Username
        lbl_user = QLabel("Utilizator")
        lbl_user.setStyleSheet(f"color: {self._palette['text']}; background: transparent; border: none; font-weight: 600;")
        self.input_username = QLineEdit()
        self.input_username.setPlaceholderText("ex: admin")
        self.input_username.setMinimumHeight(42)
        self.input_username.setStyleSheet(self._input_style())

        form_layout.addWidget(lbl_user)
        form_layout.addSpacing(6)
        form_layout.addWidget(self.input_username)
        form_layout.addSpacing(18)

        # Password
        lbl_pass = QLabel("Parolă")
        lbl_pass.setStyleSheet(f"color: {self._palette['text']}; background: transparent; border: none; font-weight: 600;")
        self.input_password = QLineEdit()
        self.input_password.setPlaceholderText("••••••••")
        self.input_password.setEchoMode(QLineEdit.Password)
        self.input_password.setMinimumHeight(42)
        self.input_password.setStyleSheet(self._input_style())

        form_layout.addWidget(lbl_pass)
        form_layout.addSpacing(6)
        form_layout.addWidget(self.input_password)
        form_layout.addSpacing(6)

        # Mesaj eroare (invizibil inițial)
        self.lbl_error = QLabel("")
        self.lbl_error.setStyleSheet(
            f"color: {self._palette['danger']}; background: transparent; "
            f"font-size: 9pt; min-height: 18px;"
        )
        self.lbl_error.setWordWrap(True)
        form_layout.addWidget(self.lbl_error)
        form_layout.addSpacing(14)

        # Login button
        self.btn_login = QPushButton("Autentificare →")
        self.btn_login.setMinimumHeight(46)
        self.btn_login.setCursor(Qt.PointingHandCursor)
        self.btn_login.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {self._palette['primary']};
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 11pt;
                font-weight: 600;
            }}
            QPushButton:hover {{ background-color: {self._palette['primary_hover']}; }}
            QPushButton:pressed {{ background-color: {self._palette['primary_soft']}; color: {self._palette['primary']}; }}
            """
        )
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setColor(QColor(14, 165, 233, 90))
        shadow.setOffset(0, 4)
        self.btn_login.setGraphicsEffect(shadow)

        form_layout.addWidget(self.btn_login)
        form_layout.addSpacing(20)

        # Footer
        footer = QLabel("© 2026 Sistem administrare cimitir — v3.0")
        footer.setAlignment(Qt.AlignCenter)
        footer.setStyleSheet(f"color: {self._palette['text_disabled']}; background: transparent; border: none; font-size: 9pt;")
        form_layout.addWidget(footer)
        form_layout.addSpacerItem(QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding))

        root.addWidget(form_panel, stretch=1)

        # -----------------------------------------------------------
        # Semnale
        # -----------------------------------------------------------
        self.btn_login.clicked.connect(self.try_login)
        self.input_password.returnPressed.connect(self.try_login)
        self.input_username.returnPressed.connect(lambda: self.input_password.setFocus())

        # focus inițial
        self.input_username.setFocus()

    def _input_style(self) -> str:
        p = self._palette
        return f"""
            QLineEdit {{
                background-color: {p['bg_panel']};
                color: {p['text']};
                border: 1.5px solid {p['border']};
                border-radius: 8px;
                padding: 10px 14px;
                font-size: 11pt;
                selection-background-color: {p['primary']};
            }}
            QLineEdit:focus {{ border-color: {p['primary']}; }}
        """

    # ---------------------------------------------------------------
    def try_login(self):
        u = self.input_username.text().strip()
        p = self.input_password.text()
        self.lbl_error.setText("")

        if not u or not p:
            self.lbl_error.setText("⚠  Introdu utilizator și parolă.")
            return

        import database as _db
        import access_session
        result, reason = auth.authenticate_ex(u, p, _db.get_setting("cemetery_id") or None)
        if not result:
            self.lbl_error.setText("❌  " + access_session.DENIED_MESSAGES.get(reason, "Acces refuzat."))
            self.input_password.clear()
            self.input_password.setFocus()
            return

        if result.get("must_change_password") and not self._force_password_change(result, p):
            self.lbl_error.setText("⚠  Schimbarea parolei implicite este obligatorie.")
            self.input_password.clear()
            return

        access_session.set_user(result)
        self.user = result
        self.accept()

    def _force_password_change(self, user: dict, old_password: str) -> bool:
        """Cere o parolă nouă pentru conturile cu parolă implicită. True dacă s-a schimbat."""
        dlg = QDialog(self)
        dlg.setObjectName("force-password-dialog")
        dlg.setWindowTitle("Schimbă parola implicită")
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(f"Contul „{user['username']}” folosește o parolă implicită.\n"
                             f"Alege o parolă nouă (minim {auth.MIN_PASSWORD_LEN} caractere)."))
        fields = []
        for name, placeholder in (("force-password-new", "Parola nouă"), ("force-password-confirm", "Confirmă parola nouă")):
            edit = QLineEdit()
            edit.setObjectName(name)
            edit.setEchoMode(QLineEdit.Password)
            edit.setPlaceholderText(placeholder)
            lay.addWidget(edit)
            fields.append(edit)
        msg = QLabel("")
        msg.setObjectName("force-password-message")
        msg.setStyleSheet("color:#dc2626")
        lay.addWidget(msg)
        btn = QPushButton("Salvează parola")
        btn.setObjectName("force-password-save")
        lay.addWidget(btn)

        def save():
            if fields[0].text() != fields[1].text():
                msg.setText("Parolele nu coincid.")
                return
            error = auth.change_own_password(user["id"], old_password, fields[0].text())
            if error:
                msg.setText(error)
                return
            dlg.accept()

        btn.clicked.connect(save)
        return dlg.exec() == QDialog.Accepted
