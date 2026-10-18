"""first_run_wizard.py — asistentul de configurare inițială MAIN: creează securizat PRIMUL administrator local (fără conturi implicite).

Explică separat autentificarea LOCALĂ (acest calculator) de cea CLOUD (administratorul serverului). Logica e în `cimitir_auth.setup_first_admin` (testabilă fără UI).
"""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QFormLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

import cimitir_auth as auth


class FirstRunWizard(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("first-run-wizard")
        self.setWindowTitle("Cimitir — Configurare inițială")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        intro = QLabel(
            "<b>Bun venit!</b> Acest registru nu are încă niciun administrator.<br><br>"
            "<b>1. Cont LOCAL (acest pas)</b> — administratorul aplicației MAIN de pe acest calculator. Cu el te autentifici în program, "
            "creezi utilizatori și configurezi conexiunea Cloud. Nu există conturi implicite: parola o alegi tu.<br>"
            "<b>2. Cont CLOUD (mai târziu)</b> — administratorul serverului Cloud (email + parolă primite separat). Îl vei folosi doar în "
            "„Asociază cu Cloud”, pentru a crea registrul în Cloud și a asocia calculatorul și telefoanele. Cele două conturi sunt independente.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QFormLayout()
        self.ed_user = QLineEdit()
        self.ed_user.setObjectName("first-run-username")
        self.ed_pass = QLineEdit()
        self.ed_pass.setObjectName("first-run-password")
        self.ed_pass.setEchoMode(QLineEdit.Password)
        self.ed_conf = QLineEdit()
        self.ed_conf.setObjectName("first-run-confirm")
        self.ed_conf.setEchoMode(QLineEdit.Password)
        form.addRow("Nume administrator local:", self.ed_user)
        form.addRow(f"Parolă (min. {auth.MIN_ADMIN_PASSWORD_LEN} caractere):", self.ed_pass)
        form.addRow("Confirmă parola:", self.ed_conf)
        lay.addLayout(form)
        self.lbl_err = QLabel("")
        self.lbl_err.setObjectName("first-run-error")
        self.lbl_err.setWordWrap(True)
        lay.addWidget(self.lbl_err)
        self.btn = QPushButton("Creează administratorul local")
        self.btn.setObjectName("first-run-create")
        self.btn.clicked.connect(self._create)
        lay.addWidget(self.btn)

    def _create(self) -> None:
        ok, msg = auth.setup_first_admin(self.ed_user.text().strip(), self.ed_pass.text(), self.ed_conf.text())
        self.ed_pass.clear()
        self.ed_conf.clear()
        if not ok:
            self.lbl_err.setText("⚠  " + msg)
            return
        self.accept()
