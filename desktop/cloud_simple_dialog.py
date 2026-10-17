"""cloud_simple_dialog.py — „Configurare Cloud" simplificată: doar nume profil, adresă Cloud, mediu + stările (conexiune, asociere, cimitir).

Fără câmpuri pentru PostgreSQL, Supabase Secret Key sau cheia brută de sincronizare; acestea rămân în configurarea serverului / SecretStore.
Profilul avansat (`cloud_profiles_dialog`) se deschide doar dacă administratorul pornește MAIN cu CIMITIR_CLOUD_ADVANCED_UI=1.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout,
)

import cloud_profiles as cp
import cloud_simple as simple
from field_worker import run_task

_COLORS = {simple.CONN_OK: "#15803d", simple.CONN_ERROR: "#b91c1c", simple.CONN_UNCHECKED: "#a16207", simple.CONN_NONE: "#64748b",
           simple.PAIR_OK: "#15803d", simple.PAIR_UNVERIFIED: "#a16207", simple.PAIR_INCOMPLETE: "#a16207", simple.PAIR_NONE: "#64748b"}


def open_cloud_config(parent=None) -> None:
    CloudSimpleDialog(parent).exec()


class CloudSimpleDialog(QDialog):
    def __init__(self, parent=None, http=None, store=None):
        super().__init__(parent)
        self.setWindowTitle("Configurare Cloud")
        self.setMinimumWidth(560)
        self._http, self._store = http, store
        self._busy = False

        root = QVBoxLayout(self)
        form = QFormLayout()
        self.ed_name = QLineEdit()
        self.ed_name.setObjectName("simple_name")
        self.ed_name.setPlaceholderText(simple.DEFAULT_NAME)
        self.ed_url = QLineEdit()
        self.ed_url.setObjectName("simple_url")
        self.ed_url.setPlaceholderText("https://adresa-cloud.exemplu.ro")
        self.cb_env = QComboBox()
        self.cb_env.setObjectName("simple_env")
        self.cb_env.addItems([label for label, _ in simple.ENV_CHOICES])
        form.addRow("Nume profil:", self.ed_name)
        form.addRow("Adresă Cloud:", self.ed_url)
        form.addRow("Mediu:", self.cb_env)
        self.lbl_conn = QLabel()
        self.lbl_conn.setObjectName("simple_conn_state")
        self.lbl_pair = QLabel()
        self.lbl_pair.setObjectName("simple_pair_state")
        self.lbl_cem = QLabel()
        self.lbl_cem.setObjectName("simple_cemetery")
        form.addRow("Stare conexiune:", self.lbl_conn)
        form.addRow("Stare asociere:", self.lbl_pair)
        form.addRow("Cimitir asociat:", self.lbl_cem)
        root.addLayout(form)

        row = QHBoxLayout()
        self.btn_save = QPushButton("Salvează")
        self.btn_save.setObjectName("simple_save")
        self.btn_test = QPushButton("Verifică conexiunea")
        self.btn_test.setObjectName("simple_test")
        self.btn_assoc = QPushButton("Asociază acest calculator…")
        self.btn_assoc.setObjectName("simple_associate")
        for b in (self.btn_save, self.btn_test, self.btn_assoc):
            row.addWidget(b)
        self.btn_adv = None
        if simple.advanced_ui_enabled():
            self.btn_adv = QPushButton("Avansat (administrator server)…")
            self.btn_adv.setObjectName("simple_advanced")
            row.addWidget(self.btn_adv)
            self.btn_adv.clicked.connect(self._advanced)
        row.addStretch(1)
        root.addLayout(row)

        self.log = QPlainTextEdit()
        self.log.setObjectName("simple_log")
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(110)
        root.addWidget(self.log)
        note = QLabel("Datele bazei de date și cheile de acces sunt gestionate de server și de stocarea securizată; nu trebuie introduse aici.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #64748b; font-size: 11px;")
        root.addWidget(note)

        self.btn_save.clicked.connect(self.save)
        self.btn_test.clicked.connect(self.check)
        self.btn_assoc.clicked.connect(self._associate)
        self.refresh()

    # ------------------------------------------------------------------ stare
    def refresh(self, check: bool = False) -> None:
        st = simple.status(check=check, store=self._store, http=self._http)
        self.ed_name.setText(st.profile_name)
        self.ed_url.setText(st.cloud_url)
        self.cb_env.setCurrentText(st.environment)
        self._show(st)

    def _show(self, st: simple.SimpleStatus) -> None:
        self.lbl_conn.setText(st.connection_text)
        self.lbl_conn.setStyleSheet(f"color: {_COLORS[st.connection]}; font-weight: 600;")
        self.lbl_pair.setText(st.pairing_text)
        self.lbl_pair.setStyleSheet(f"color: {_COLORS[st.pairing]}; font-weight: 600;")
        self.lbl_cem.setText(st.cemetery_name or "—")

    # ------------------------------------------------------------------ acțiuni
    def save(self) -> bool:
        label = self.cb_env.currentText()
        confirm = False
        if simple.env_value(label) == "PRODUCTION":
            current = simple.current_profile()
            if not (current and current.is_active and current.environment == "PRODUCTION"):
                if QMessageBox.warning(self, "ATENȚIE — PROD", "Mediul PROD sincronizează date REALE cu serverul de producție.\n\nConfirmi?",
                                       QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                    return False
            confirm = True
        try:
            simple.save(self.ed_name.text(), self.ed_url.text(), label, confirm, self._store)
        except cp.ValidationFailed as exc:
            QMessageBox.warning(self, "Date invalide", "\n".join(exc.errors))
            return False
        except cp.ProfileError as exc:
            QMessageBox.warning(self, "Eroare", str(exc))
            return False
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Eroare", f"Configurarea nu a fost salvată ({type(exc).__name__}).")
            return False
        self.log.appendPlainText("Configurare salvată.")
        self.refresh()
        return True

    def check(self) -> None:
        if self._busy or not self.save():
            return
        prof = simple.current_profile()
        self._busy = True
        self.btn_test.setEnabled(False)
        self.log.appendPlainText("Verific conexiunea…")
        self._task = run_task(self, lambda: simple.test_connection(prof, self._store, self._http), self._on_checked,
                              lambda msg, _nc: self._on_checked((False, msg)))

    def _on_checked(self, result) -> None:
        ok, text = result
        self._busy = False
        self.btn_test.setEnabled(True)
        self.log.appendPlainText(text)
        st = simple.status(check=False, store=self._store)
        st.connection = simple.CONN_OK if ok else simple.CONN_ERROR
        st.connection_text = simple._CONN_TEXT[st.connection]
        self._show(st)

    def _associate(self) -> None:
        try:
            from cloud_associate_window import CloudAssociateDialog
            CloudAssociateDialog(self).exec()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Eroare", f"Nu am putut deschide asocierea ({type(exc).__name__}).")
        self.refresh()

    def _advanced(self) -> None:
        from cloud_profiles_dialog import CloudProfilesDialog
        CloudProfilesDialog(self).exec()
        self.refresh()
