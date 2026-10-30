"""cloud_center_dialog.py — „Centru de configurare Cimitir Sync” (doar administratorul local): stare, Supabase, Backend Cloud, Administratori, Backup, Mobile.

Reutilizează ferestrele existente (asociere Cloud, backup, telefoane); nu introduce conturi/autentificare/configurare paralele.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
                               QTabWidget, QVBoxLayout, QWidget)

import access_session
import cimitir_cloud_admin_client as adc
import cloud_center as cc
import cloud_profiles as cp
import cloud_simple as simple
from cloud_center_common import run_bg, state_label, unavailable_box
from cloud_center_supabase_tab import SupabaseTab

CONFIDENTIALITY = (
    "<b>Confidențialitate — ce garantează și ce nu garantează această arhitectură</b><br>"
    "• Parohia deține proiectul Supabase; dezvoltatorul NU păstrează credențiale administrative Supabase (parola bazei se folosește o singură dată, din memorie).<br>"
    "• Backendul primește doar credențiale runtime cu privilegii minime, într-o instanță izolată per parohie, cu secrete separate per organizație.<br>"
    "• <b>Atenție:</b> cine operează backendul poate avea, tehnic, acces la datele procesate (memoria procesului, jurnale, variabile de mediu ale instanței). "
    "Lipsa credențialelor administrative NU garantează confidențialitate absolută.<br>"
    "• Reducerea riscului: acces nominal și jurnalizat la gazdă, fără acces SSH permanent, secrete în secret manager, jurnale fără date personale, rotirea cheilor, contract de prelucrare (GDPR).<br>"
    "• Confidențialitate puternică (neimplementată): criptare end-to-end cu chei deținute exclusiv de parohie — dezvoltatorul nu ar putea citi datele, dar ar dispărea căutarea/portalul public pe server."
)


class _NoStore:
    """SecretStore indisponibil (ex. fără DPAPI): fila Provizionare nu poate salva tokenul și o spune explicit."""
    def get(self, name):
        return None

    def set(self, name, value):
        raise RuntimeError("SecretStore indisponibil pe acest sistem: tokenul de operator nu poate fi salvat în siguranță.")


class CloudCenterDialog(QDialog):
    def __init__(self, parent=None, can_manage: Optional[Callable[[], bool]] = None, http: Any = None, store: Any = None,
                 supabase_connect: Any = None, supabase_bucket_connect: Any = None, supabase_url_connect: Any = None, admin_factory: Any = None):
        super().__init__(parent)
        self.setWindowTitle("Centru de configurare Cimitir Sync")
        self.setObjectName("cloud_center")
        self.setMinimumSize(900, 640)
        self._can = can_manage or (lambda: access_session.require("manage_config", self, "Centrul de configurare Cloud"))
        self._http, self._store, self._admin_factory = http, store, admin_factory
        self._busy = False
        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("center_tabs")
        root.addWidget(self.tabs, 1)
        self.supabase = SupabaseTab(self, supabase_connect, supabase_bucket_connect, supabase_url_connect)
        for title, w in (("A. Stare generală", self._build_status()), ("B. Supabase", self.supabase), ("C. Backend Cloud", self._build_backend()),
                         ("D. Administratori", self._build_admin()), ("E. Backup", self._build_backup()), ("F. Mobile", self._build_mobile()),
                         ("G. Provizionare", self._build_provision())):
            self.tabs.addTab(w, title)
        close = QPushButton("Închide")
        close.setObjectName("center_close")
        close.clicked.connect(self.accept)
        root.addWidget(close)
        self.refresh_status()

    def guard(self) -> bool:
        return bool(self._can())

    def done(self, r: int) -> None:  # noqa: D401
        self.supabase.clear_auth()
        self._wipe_admin_fields()
        super().done(r)

    def _build_provision(self) -> QWidget:
        from cloud_provision_tab import ProvisionTab
        import cimitir_secretstore as ss
        try:
            store = self._store or ss.default_store()
        except Exception:  # noqa: BLE001
            store = _NoStore()
        self.provision = ProvisionTab(store, self.guard, self)
        return self.provision

    # ------------------------------------------------------------------ A. stare generală
    def _build_status(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        form = QFormLayout()
        self._status_labels = {}
        for key, label in (("license", "Licență"), ("local_db", "Baza locală"), ("supabase", "Supabase"), ("backend", "Backend Cloud"),
                           ("pairing", "Asociere MAIN"), ("sync", "Sincronizare Mobile")):
            lbl = QLabel("…")
            lbl.setObjectName(f"status_{key}")
            lbl.setWordWrap(True)
            self._status_labels[key] = lbl
            form.addRow(label + ":", lbl)
        lay.addLayout(form)
        row = QHBoxLayout()
        self.btn_refresh = QPushButton("Reîmprospătează")
        self.btn_refresh.setObjectName("status_refresh")
        self.btn_check_backend = QPushButton("Verifică și backendul (rețea)")
        self.btn_check_backend.setObjectName("status_check_backend")
        row.addWidget(self.btn_refresh)
        row.addWidget(self.btn_check_backend)
        row.addStretch(1)
        lay.addLayout(row)
        flow = QLabel("<b>Ordinea recomandată:</b> licență → administrator local → (backup evidență) → B. Supabase (verifică, instalează schema) → "
                      "C. Backend Cloud (adresă + asociere) → D. administrator Cloud → sincronizare → F. telefoane.")
        flow.setWordWrap(True)
        lay.addWidget(flow)
        lay.addStretch(1)
        self.btn_refresh.clicked.connect(lambda: self.refresh_status())
        self.btn_check_backend.clicked.connect(lambda: self.refresh_status(check_backend=True))
        return w

    def refresh_status(self, check_backend: bool = False) -> None:
        def work():
            return cc.general_status(check_backend=check_backend, http=self._http)
        if check_backend:
            self.btn_check_backend.setEnabled(False)
            run_bg(self, work, self._show_status, lambda m: self._show_status(None))
        else:
            self._show_status(work())

    def _show_status(self, items) -> None:
        self.btn_check_backend.setEnabled(True)
        if not items:
            return
        for it in items:
            state_label(self._status_labels[it.key], it.state, it.text)

    # ------------------------------------------------------------------ C. backend
    def _build_backend(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        form = QFormLayout()
        self.be_name = QLineEdit()
        self.be_name.setObjectName("be_name")
        self.be_name.setPlaceholderText(simple.DEFAULT_NAME)
        self.be_url = QLineEdit()
        self.be_url.setObjectName("be_url")
        self.be_url.setPlaceholderText("https://parohia-x.cloud.exemplu.ro")
        self.be_env = QComboBox()
        self.be_env.setObjectName("be_env")
        self.be_env.addItems([label for label, _ in simple.ENV_CHOICES])
        form.addRow("Nume profil:", self.be_name)
        form.addRow("Adresă backend Cloud:", self.be_url)
        form.addRow("Mediu:", self.be_env)
        lay.addLayout(form)
        row = QHBoxLayout()
        self.be_save = QPushButton("Salvează")
        self.be_save.setObjectName("be_save")
        self.be_test = QPushButton("Verifică conexiunea")
        self.be_test.setObjectName("be_test")
        self.be_assoc = QPushButton("Asociază acest calculator…")
        self.be_assoc.setObjectName("be_associate")
        self.be_conf = QPushButton("Confidențialitate…")
        self.be_conf.setObjectName("be_confidentiality")
        for b in (self.be_save, self.be_test, self.be_assoc, self.be_conf):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.be_log = QPlainTextEdit()
        self.be_log.setObjectName("be_log")
        self.be_log.setReadOnly(True)
        self.be_log.setMaximumHeight(120)
        lay.addWidget(self.be_log)
        lay.addWidget(unavailable_box("backend_provisioning"))
        lay.addStretch(1)
        st = simple.status(store=self._store)
        self.be_name.setText(st.profile_name)
        self.be_url.setText(st.cloud_url)
        self.be_env.setCurrentText(st.environment)
        self.be_save.clicked.connect(self.save_backend)
        self.be_test.clicked.connect(self.test_backend)
        self.be_assoc.clicked.connect(self.open_associate)
        self.be_conf.clicked.connect(lambda: QMessageBox.information(self, "Confidențialitate", CONFIDENTIALITY))
        return w

    def save_backend(self) -> bool:
        if not self.guard():
            return False
        label, confirm = self.be_env.currentText(), False
        if simple.env_value(label) == "PRODUCTION":
            cur = simple.current_profile()
            if not (cur and cur.is_active and cur.environment == "PRODUCTION"):
                if QMessageBox.warning(self, "ATENȚIE — PROD", "PROD lucrează cu date REALE.\n\nConfirmi?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                    return False
            confirm = True
        name = self.be_name.text()
        try:
            cur = simple.current_profile()
            if cur is None or cur.environment != simple.env_value(label):
                import cloud_supabase as sb
                tgt = sb.ensure_profile_for_env(simple.env_value(label), confirm, self._store)
                if cur is None or name.strip().lower() == cur.name.lower():
                    name = tgt.name
            simple.save(name, self.be_url.text(), label, confirm, self._store)
        except cp.ValidationFailed as exc:
            QMessageBox.warning(self, "Date invalide", "\n".join(exc.errors))
            return False
        except cp.ProfileError as exc:
            QMessageBox.warning(self, "Eroare", str(exc))
            return False
        self.be_log.appendPlainText("Configurare salvată.")
        self.refresh_status()
        return True

    def test_backend(self) -> None:
        if not self.guard() or self._busy or not self.save_backend():
            return
        prof = simple.current_profile()
        self._busy = True
        self.be_log.appendPlainText("Verific conexiunea…")

        def done(res):
            self._busy = False
            self.be_log.appendPlainText(res[1] if isinstance(res, tuple) else str(res))
            self.refresh_status()
        self._task = run_bg(self, lambda: simple.test_connection(prof, self._store, self._http), done, lambda m: done((False, m)))

    def open_associate(self) -> None:
        if not self.guard():
            return
        try:
            from cloud_associate_window import CloudAssociateDialog
            CloudAssociateDialog(self, store=self._store).exec()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Eroare", f"Nu am putut deschide asocierea ({type(exc).__name__}).")
        self.refresh_status()

    # ------------------------------------------------------------------ D. administratori
    def _build_admin(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        info = QLabel("<b>Administrator local:</b> se creează la prima pornire a MAIN (neschimbat) și are drepturi complete asupra organizației proprii.<br>"
                      "<b>Administrator Cloud:</b> operațiune UNICĂ, protejată de un token tehnic de inițializare; după creare, inițializarea se închide pe server.")
        info.setWordWrap(True)
        lay.addWidget(info)
        self.ad_state = QLabel("Starea Cloud nu a fost verificată.")
        self.ad_state.setObjectName("admin_state")
        self.ad_state.setWordWrap(True)
        self.ad_check = QPushButton("Verifică starea inițializării")
        self.ad_check.setObjectName("admin_check")
        lay.addWidget(self.ad_state)
        lay.addWidget(self.ad_check)
        form = QFormLayout()
        self.ad_email = QLineEdit()
        self.ad_email.setObjectName("admin_email")
        self.ad_name = QLineEdit()
        self.ad_name.setObjectName("admin_display_name")
        self.ad_pw = self._pw_field("admin_password")
        self.ad_pw2 = self._pw_field("admin_password2")
        self.ad_token = self._pw_field("admin_token")
        form.addRow("Email administrator Cloud:", self.ad_email)
        form.addRow("Nume afișat (opțional):", self.ad_name)
        form.addRow("Parolă (min. 12 caractere):", self.ad_pw)
        form.addRow("Repetă parola:", self.ad_pw2)
        form.addRow("Token tehnic de inițializare:", self.ad_token)
        lay.addLayout(form)
        self.ad_confirm = QCheckBox("Confirm: creez primul administrator Cloud (operațiune unică; parola și tokenul nu se salvează pe acest calculator)")
        self.ad_confirm.setObjectName("admin_confirm")
        self.ad_go = QPushButton("Inițializează administratorul Cloud")
        self.ad_go.setObjectName("admin_init")
        self.ad_go.setEnabled(False)
        self.ad_log = QPlainTextEdit()
        self.ad_log.setObjectName("admin_log")
        self.ad_log.setReadOnly(True)
        self.ad_log.setMaximumHeight(90)
        lay.addWidget(self.ad_confirm)
        lay.addWidget(self.ad_go)
        lay.addWidget(self.ad_log)
        row = QHBoxLayout()
        self.ad_manage = QPushButton("Conturi, dispozitive și coduri Cloud…")
        self.ad_manage.setObjectName("admin_manage")
        row.addWidget(self.ad_manage)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(unavailable_box("bootstrap_token_delivery"))
        lay.addStretch(1)
        self.ad_check.clicked.connect(self.check_admin_state)
        self.ad_go.clicked.connect(self.init_admin)
        self.ad_manage.clicked.connect(self.open_associate)
        self.ad_confirm.toggled.connect(lambda on: self.ad_go.setEnabled(on and self._admin_can_init))
        self._admin_can_init = False
        return w

    def _pw_field(self, name: str) -> QLineEdit:
        e = QLineEdit()
        e.setObjectName(name)
        e.setEchoMode(QLineEdit.Password)
        return e

    def _wipe_admin_fields(self) -> None:
        for e in (self.ad_pw, self.ad_pw2, self.ad_token):
            e.clear()

    def _base_url(self) -> str:
        prof = simple.current_profile()
        return (prof.api_base_url if prof else "") or ""

    def check_admin_state(self) -> None:
        if not self.guard():
            return
        url = self._base_url()
        if not url:
            self.ad_state.setText("Configurează mai întâi adresa backendului (fila C).")
            return
        self.ad_state.setText("Verific…")

        def ok(st: cc.BackendAdminState):
            self._admin_can_init = st.can_initialize
            self.ad_go.setEnabled(st.can_initialize and self.ad_confirm.isChecked())
            state_label(self.ad_state, cc.ST_OK if st.can_initialize else cc.ST_WARN if st.reachable else cc.ST_ERROR, st.detail)
        self._task = run_bg(self, lambda: cc.admin_state(url, self._admin_factory), ok, lambda m: self.ad_state.setText(m))

    def init_admin(self) -> None:
        if not self.guard() or self._busy:
            return
        errs = cc.validate_admin_form(self.ad_email.text(), self.ad_pw.text(), self.ad_pw2.text(), self.ad_token.text())
        if errs:
            QMessageBox.warning(self, "Date incomplete", "\n".join(errs))
            return
        url, token, email, pw, name = self._base_url(), self.ad_token.text(), self.ad_email.text(), self.ad_pw.text(), self.ad_name.text()
        self._wipe_admin_fields()
        self._busy = True
        self.ad_go.setEnabled(False)

        def done(res):
            self._busy = False
            self.ad_confirm.setChecked(False)
            self.ad_log.appendPlainText(f"Administratorul Cloud {res['email']} a fost creat. Inițializarea este acum închisă pe server.")
            self._admin_can_init = False
            self.refresh_status()

        def fail(msg):
            self._busy = False
            self.ad_log.appendPlainText("Eroare: " + msg)
        self._task = run_bg(self, lambda: cc.initialize_cloud_admin(url, token, email, pw, name, self._admin_factory), done, fail)

    # ------------------------------------------------------------------ E. backup
    def _build_backup(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        info = QLabel("Copiile de rezervă locale (baza SQLite + fișiere media) sunt cele existente în MAIN. Ele NU sunt un dump PostgreSQL. "
                      "Înainte de orice instalare de schemă Cloud se face automat o copie locală verificată.")
        info.setWordWrap(True)
        lay.addWidget(info)
        row = QHBoxLayout()
        self.bk_open = QPushButton("Copii de rezervă locale (deschide)…")
        self.bk_open.setObjectName("backup_open")
        self.bk_now = QPushButton("Fă o copie acum")
        self.bk_now.setObjectName("backup_now")
        row.addWidget(self.bk_open)
        row.addWidget(self.bk_now)
        row.addStretch(1)
        lay.addLayout(row)
        self.bk_log = QLabel("")
        self.bk_log.setObjectName("backup_log")
        self.bk_log.setWordWrap(True)
        lay.addWidget(self.bk_log)
        lay.addWidget(unavailable_box("portable_backup"))
        lay.addWidget(unavailable_box("initial_sync"))
        lay.addStretch(1)
        self.bk_open.clicked.connect(self.open_backup)
        self.bk_now.clicked.connect(self.backup_now)
        return w

    def open_backup(self) -> None:
        if not self.guard():
            return
        from backup_window import BackupWindow
        BackupWindow(self).exec()

    def backup_now(self) -> None:
        if not self.guard():
            return
        import cloud_supabase as sb
        self.bk_now.setEnabled(False)

        def ok(path):
            self.bk_now.setEnabled(True)
            self.bk_log.setText(f"Copie creată și verificată: {path.name}")

        def fail(msg):
            self.bk_now.setEnabled(True)
            self.bk_log.setText("Eroare: " + msg)
        self._task = run_bg(self, sb.local_backup, ok, fail)

    # ------------------------------------------------------------------ F. mobile
    def _build_mobile(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        info = QLabel("Telefoanele se asociază prin mecanismul existent de pairing (cod / QR emis de administratorul Cloud). "
                      "Administratorul aprobă și revocă dispozitivele, stabilește permisiunile și vede starea sincronizării.")
        info.setWordWrap(True)
        lay.addWidget(info)
        self.mb_sync = QLabel("")
        self.mb_sync.setObjectName("mobile_sync_state")
        lay.addWidget(self.mb_sync)
        for name, text, slot in (("mobile_pair", "Asociază telefoane (cod / QR)…", self.open_associate), ("mobile_field", "Telefoane de teren…", self.open_field),
                                 ("mobile_v2", "Permisiuni Mobile (categorii, câmpuri, profiluri), conflicte, jurnal…", self.open_mobile_v2)):
            b = QPushButton(text)
            b.setObjectName(name)
            b.clicked.connect(slot)
            lay.addWidget(b)
        lay.addStretch(1)
        ind = simple.sync_indicator() if self._safe_indicator() else {"state": "none", "text": "Indisponibilă", "detail": ""}
        self.mb_sync.setText("Stare sincronizare: " + ind["text"])
        return w

    @staticmethod
    def _safe_indicator() -> bool:
        try:
            simple.sync_indicator()
            return True
        except Exception:  # noqa: BLE001
            return False

    def _open(self, module: str, cls: str) -> None:
        if not self.guard():
            return
        try:
            mod = __import__(module)
            getattr(mod, cls)(self).exec()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Eroare", f"Nu am putut deschide fereastra ({type(exc).__name__}).")

    def open_field(self) -> None:
        self._open("field_devices_window", "FieldDevicesDialog")

    def open_mobile_v2(self) -> None:
        self._open("mobile_devices_window", "MobileDevicesDialog")


def open_cloud_center(parent=None) -> None:
    if not access_session.require("manage_config", parent, "Centrul de configurare Cloud"):
        return
    CloudCenterDialog(parent).exec()
