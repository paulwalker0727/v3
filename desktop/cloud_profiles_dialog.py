"""cloud_profiles_dialog.py — „Configurare Cloud/Server": profile de conexiune (TEST / PRODUCTION / CUSTOM).

Secretele nu apar niciodată în UI: se afișează doar `********`; valoarea nouă se cere cu „Înlocuiește …" și ajunge direct în
SecretStore la salvare (vezi cloud_profiles.py).
"""
from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout,
)

import cloud_profiles as cp

SECRET_LABELS = {"db_password": "parola bazei de date", "supabase_secret_key": "Supabase Secret Key", "sync_key": "cheia de sincronizare"}


class _TestThread(QThread):
    done = Signal(object)

    def __init__(self, profile, mode, parent=None):
        super().__init__(parent)
        self._profile, self._mode = profile, mode

    def run(self):
        self.done.emit(cp.test_connection(self._profile, self._mode))


class CloudProfilesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configurare Cloud / Server — profile de conexiune")
        self.setMinimumSize(900, 620)
        self._current: Optional[str] = None
        self._pending: Dict[str, str] = {}
        self._thread = None

        root = QHBoxLayout(self)
        left = QVBoxLayout()
        self.lst = QListWidget()
        self.lst.setObjectName("profile_list")
        self.btn_new = QPushButton("Creează profil")
        left.addWidget(self.lst, 1)
        left.addWidget(self.btn_new)
        root.addLayout(left, 1)

        right = QVBoxLayout()
        form = QFormLayout()
        self.f_name = QLineEdit()
        self.f_env = QComboBox()
        self.f_env.addItems(cp.ENVIRONMENTS)
        self.f_ref = QLineEdit()
        self.f_api = QLineEdit()
        self.f_api.setPlaceholderText("https://server-cimitir.ro")
        self.f_sup = QLineEdit()
        self.f_host = QLineEdit()
        self.f_port = QLineEdit("5432")
        self.f_db = QLineEdit()
        self.f_user = QLineEdit()
        self.f_ssl = QComboBox()
        self.f_ssl.addItems(cp.SSL_MODES)
        self.f_ssl.setCurrentText("require")
        self.f_bucket = QLineEdit()
        self.f_sync = QLineEdit()
        self.f_sync.setReadOnly(True)
        self.btn_sync_key = QPushButton("Înlocuiește cheia de sincronizare")
        for label, w in (("Nume profil:", self.f_name), ("Mediu:", self.f_env), ("project_ref:", self.f_ref),
                         ("api_base_url:", self.f_api), ("supabase_url:", self.f_sup), ("db_host:", self.f_host),
                         ("db_port:", self.f_port), ("db_name:", self.f_db), ("db_user:", self.f_user),
                         ("sslmode:", self.f_ssl), ("storage_bucket:", self.f_bucket)):
            form.addRow(label, w)
        row = QHBoxLayout()
        row.addWidget(self.f_sync, 1)
        row.addWidget(self.btn_sync_key)
        form.addRow("Cheie sincronizare (API):", row)
        right.addLayout(form)

        self.chk_adv = QCheckBox("Advanced / Server mode (parola bazei de date și Supabase Secret Key — opționale)")
        self.box_adv = QGroupBox("Advanced / Server mode")
        adv = QFormLayout(self.box_adv)
        self.f_pw = QLineEdit()
        self.f_pw.setReadOnly(True)
        self.btn_pw = QPushButton("Înlocuiește parola")
        self.f_sk = QLineEdit()
        self.f_sk.setReadOnly(True)
        self.btn_sk = QPushButton("Înlocuiește Secret Key")
        r1, r2 = QHBoxLayout(), QHBoxLayout()
        r1.addWidget(self.f_pw, 1)
        r1.addWidget(self.btn_pw)
        r2.addWidget(self.f_sk, 1)
        r2.addWidget(self.btn_sk)
        adv.addRow("db_password:", r1)
        adv.addRow("supabase_secret_key:", r2)
        note = QLabel("MAIN și telefonul comunică doar prin API HTTPS; aceste două secrete sunt folosite exclusiv la testul Advanced "
                      "și nu sunt trimise nicăieri.")
        note.setWordWrap(True)
        adv.addRow(note)
        right.addWidget(self.chk_adv)
        right.addWidget(self.box_adv)
        self.box_adv.setVisible(False)

        btns = QHBoxLayout()
        self.btn_save = QPushButton("Salvează profil")
        self.btn_test = QPushButton("Testează conexiunea")
        self.btn_activate = QPushButton("Activează profil")
        self.btn_deactivate = QPushButton("Dezactivează profil")
        self.btn_delete = QPushButton("Șterge profil")
        for b in (self.btn_save, self.btn_test, self.btn_activate, self.btn_deactivate, self.btn_delete):
            btns.addWidget(b)
        right.addLayout(btns)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        right.addWidget(self.log, 1)
        root.addLayout(right, 3)

        self.btn_new.clicked.connect(self.new_profile)
        self.btn_save.clicked.connect(self.save_profile)
        self.btn_test.clicked.connect(self.test_profile)
        self.btn_activate.clicked.connect(self.activate_profile)
        self.btn_deactivate.clicked.connect(self.deactivate_profile)
        self.btn_delete.clicked.connect(self.delete_profile)
        self.btn_pw.clicked.connect(lambda: self.replace_secret("db_password"))
        self.btn_sk.clicked.connect(lambda: self.replace_secret("supabase_secret_key"))
        self.btn_sync_key.clicked.connect(lambda: self.replace_secret("sync_key"))
        self.chk_adv.toggled.connect(self.box_adv.setVisible)
        self.lst.currentItemChanged.connect(self._on_select)
        self.reload()

    # ------------------------------------------------------------ date
    def reload(self, select: Optional[str] = None) -> None:
        select = select or self._current
        self.lst.blockSignals(True)
        self.lst.clear()
        row = -1
        for i, p in enumerate(cp.list_profiles()):
            it = QListWidgetItem(f"{'● ' if p.is_active else ''}{p.name}  [{p.environment}]")
            it.setData(256, p.id)
            self.lst.addItem(it)
            if p.id == select:
                row = i
        self.lst.blockSignals(False)
        if row < 0 and self.lst.count():
            row = 0
            select = self.lst.item(0).data(256)
        if row >= 0:
            self.lst.setCurrentRow(row)
            self._load(select)
        else:
            self.new_profile()

    def _on_select(self, item, _prev=None):
        if item is not None:
            self._load(item.data(256))

    def new_profile(self):
        self._current = None
        self._pending = {}
        for w in (self.f_name, self.f_ref, self.f_api, self.f_sup, self.f_host, self.f_db, self.f_user, self.f_bucket):
            w.setText("")
        self.f_port.setText("5432")
        self.f_env.setCurrentText("TEST")
        self.f_ssl.setCurrentText("require")
        self._show_secrets(None)
        self.chk_adv.setChecked(False)
        self.btn_activate.setEnabled(False)
        self.btn_deactivate.setEnabled(False)
        self.btn_delete.setEnabled(False)

    def _load(self, pid: str):
        p = cp.get_profile(pid)
        if p is None:
            return self.new_profile()
        self._current, self._pending = p.id, {}
        self.f_name.setText(p.name)
        self.f_env.setCurrentText(p.environment)
        self.f_ref.setText(p.project_ref)
        self.f_api.setText(p.api_base_url)
        self.f_sup.setText(p.supabase_url)
        self.f_host.setText(p.db_host)
        self.f_port.setText(str(p.db_port))
        self.f_db.setText(p.db_name)
        self.f_user.setText(p.db_user)
        self.f_ssl.setCurrentText(p.sslmode)
        self.f_bucket.setText(p.storage_bucket)
        self._show_secrets(p)
        self.chk_adv.setChecked(p.advanced)
        self.btn_activate.setEnabled(not p.is_active)
        self.btn_deactivate.setEnabled(p.is_active)
        self.btn_delete.setEnabled(True)

    def _show_secrets(self, p) -> None:
        for kind, widget in (("db_password", self.f_pw), ("supabase_secret_key", self.f_sk), ("sync_key", self.f_sync)):
            if kind in self._pending:
                widget.setText(cp.MASK + " (se salvează)")
            else:
                widget.setText(p.masked(kind) if p is not None else "")

    def _fields(self) -> dict:
        return {"name": self.f_name.text(), "environment": self.f_env.currentText(), "project_ref": self.f_ref.text(),
                "api_base_url": self.f_api.text(), "supabase_url": self.f_sup.text(), "db_host": self.f_host.text(),
                "db_port": self.f_port.text(), "db_name": self.f_db.text(), "db_user": self.f_user.text(),
                "sslmode": self.f_ssl.currentText(), "storage_bucket": self.f_bucket.text()}

    # ------------------------------------------------------------ acțiuni
    def replace_secret(self, kind: str) -> None:
        value, ok = QInputDialog.getText(self, "Înlocuiește secret", f"Valoare nouă pentru {SECRET_LABELS[kind]}:", QLineEdit.Password)
        if not ok or not value:
            return
        try:
            cp._validate_secret(kind, value)
        except cp.ValidationFailed as exc:
            QMessageBox.warning(self, "Valoare invalidă", "\n".join(exc.errors))
            return
        self._pending[kind] = value
        self._show_secrets(cp.get_profile(self._current) if self._current else None)

    def save_profile(self) -> bool:
        secrets = dict(self._pending)
        if not self.chk_adv.isChecked():
            secrets.pop("db_password", None)
            secrets.pop("supabase_secret_key", None)
        try:
            if self._current:
                p = cp.update_profile(self._current, self._fields(), secrets)
            else:
                p = cp.create_profile(self._fields(), secrets)
        except cp.ValidationFailed as exc:
            QMessageBox.warning(self, "Profil invalid", "\n".join(exc.errors))
            return False
        except cp.ProfileError as exc:
            QMessageBox.warning(self, "Eroare", str(exc))
            return False
        except Exception as exc:
            QMessageBox.warning(self, "SecretStore indisponibil", f"Profilul nu a fost salvat ({type(exc).__name__}).")
            return False
        self._pending = {}
        self.log.appendPlainText(f"Profil salvat: {p.name}")
        self.reload(p.id)
        return True

    def test_profile(self) -> None:
        if not self._current or self._pending:
            if not self.save_profile():
                return
        p = cp.get_profile(self._current)
        mode = "advanced" if self.chk_adv.isChecked() and p.advanced else "api"
        self.log.appendPlainText(f"Testez ({mode}) …")
        self.btn_test.setEnabled(False)
        self._thread = _TestThread(p, mode, self)
        self._thread.done.connect(self._on_test)
        self._thread.start()

    def _on_test(self, report) -> None:
        self.btn_test.setEnabled(True)
        self.log.appendPlainText(report.text())
        self.log.appendPlainText("Rezultat: " + ("REUȘIT" if report.ok else "EȘUAT"))

    def activate_profile(self) -> None:
        if not self._current:
            return
        p = cp.get_profile(self._current)
        confirm = False
        if p.environment == "PRODUCTION":
            if QMessageBox.warning(self, "ATENȚIE — PRODUCTION",
                                   f"Profilul „{p.name}” este PRODUCTION. După activare, MAIN va sincroniza date REALE cu serverul "
                                   "de producție.\n\nActivezi explicit acest profil?",
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            confirm = True
        try:
            cp.activate_profile(p.id, confirm_production=confirm)
        except cp.ProfileError as exc:
            QMessageBox.warning(self, "Eroare", str(exc))
            return
        self.log.appendPlainText(f"Profil activ: {p.name}")
        self.reload(p.id)

    def deactivate_profile(self) -> None:
        if self._current:
            cp.deactivate_profile(self._current)
            self.log.appendPlainText("Profil dezactivat; sincronizarea nu mai are conexiune configurată.")
            self.reload(self._current)

    def delete_profile(self) -> None:
        if not self._current:
            return
        p = cp.get_profile(self._current)
        confirm = False
        if p.is_active:
            if QMessageBox.question(self, "Șterge profilul ACTIV",
                                    f"„{p.name}” este profilul activ. Ștergerea oprește sincronizarea și elimină secretele din SecretStore.\n\nContinui?",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            confirm = True
        elif QMessageBox.question(self, "Șterge profil", f"Ștergi profilul „{p.name}” și secretele lui?",
                                  QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            cp.delete_profile(p.id, confirm_active=confirm)
        except cp.SecretDeleteError as exc:
            QMessageBox.warning(self, "Profilul NU a fost șters", str(exc))
            self.log.appendPlainText("Ștergere eșuată: " + str(exc))
            self.reload(p.id)
            return
        except cp.ProfileError as exc:
            QMessageBox.warning(self, "Eroare", str(exc))
            return
        self.log.appendPlainText(f"Profil șters: {p.name}")
        self._current = None
        self.reload()
