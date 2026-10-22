"""cloud_center_supabase_tab.py — fila „Supabase” a Centrului de configurare Cloud (ghidat: verificare, previzualizare, instalare cu backup + confirmare)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

import cloud_center as cc
import cloud_profiles as cp
import cloud_simple as simple
import cloud_supabase as sb
from cloud_center_common import run_bg


class SupabaseTab(QWidget):
    def __init__(self, dialog, connect=None, bucket_connect=None, url_connect=None):
        super().__init__()
        self._dlg = dialog
        self._connect, self._bucket_connect, self._url_connect = connect, bucket_connect, url_connect
        self.session = sb.AdminSession()
        self.result: sb.VerifyResult | None = None
        self.local_backup_path = ""
        self._busy = False
        root = QVBoxLayout(self)
        intro = QLabel("Fiecare parohie folosește propriul proiect Supabase. Parola bazei de date este cerută doar pentru verificare/instalare, "
                       "rămâne numai în memorie (expiră după 15 minute) și NU se salvează pe acest calculator.")
        intro.setWordWrap(True)
        root.addWidget(intro)
        form = QFormLayout()
        row = QHBoxLayout()
        self.ed_uri = QLineEdit()
        self.ed_uri.setObjectName("sb_uri")
        self.ed_uri.setEchoMode(QLineEdit.Password)
        self.ed_uri.setPlaceholderText("postgresql://postgres.<ref>:parola@aws-0-…pooler.supabase.com:5432/postgres (opțional)")
        self.btn_uri = QPushButton("Completează din șir")
        self.btn_uri.setObjectName("sb_fill_from_uri")
        row.addWidget(self.ed_uri, 1)
        row.addWidget(self.btn_uri)
        form.addRow("Șir de conexiune (Session pooler):", row)
        self.ed_ref = self._line("sb_ref", "20 de litere mici, din Project Settings → General")
        self.ed_url = self._line("sb_url", "se completează automat: https://<ref>.supabase.co")
        self.ed_host = self._line("sb_host", "ex. aws-0-eu-central-1.pooler.supabase.com")
        self.sp_port = QSpinBox()
        self.sp_port.setObjectName("sb_port")
        self.sp_port.setRange(1, 65535)
        self.sp_port.setValue(5432)
        self.ed_user = self._line("sb_user", "postgres.<ref>")
        self.ed_bucket = self._line("sb_bucket", sb.DEFAULT_BUCKET)
        self.ed_bucket.setText(sb.DEFAULT_BUCKET)
        self.cb_env = QComboBox()
        self.cb_env.setObjectName("sb_env")
        self.cb_env.addItems([label for label, _ in simple.ENV_CHOICES])
        self.ed_pw = QLineEdit()
        self.ed_pw.setObjectName("sb_password")
        self.ed_pw.setEchoMode(QLineEdit.Password)
        self.ed_pw.setPlaceholderText("parola bazei de date (temporară)")
        self.lbl_auth = QLabel("Fără autorizare activă")
        self.lbl_auth.setObjectName("sb_auth_state")
        self.btn_clear = QPushButton("Șterge autorizarea")
        self.btn_clear.setObjectName("sb_clear_auth")
        prow = QHBoxLayout()
        prow.addWidget(self.ed_pw, 1)
        prow.addWidget(self.btn_clear)
        for label, w in (("Project Ref:", self.ed_ref), ("Supabase URL:", self.ed_url), ("Gazdă PostgreSQL:", self.ed_host), ("Port (5432 = Session pooler):", self.sp_port),
                         ("Utilizator PostgreSQL:", self.ed_user), ("Bucket Storage (privat):", self.ed_bucket), ("Profil Cloud:", self.cb_env)):
            form.addRow(label, w)
        form.addRow("Parola PostgreSQL:", prow)
        form.addRow("", self.lbl_auth)
        root.addLayout(form)
        buttons = QHBoxLayout()
        self.btn_verify = self._btn("sb_verify", "1. Verifică proiectul", buttons)
        self.btn_save = self._btn("sb_save", "2. Salvează în profil (fără parolă)", buttons)
        self.btn_install = self._btn("sb_install", "3. Instalează / actualizează schema…", buttons)
        buttons.addStretch(1)
        root.addLayout(buttons)
        self.log = QPlainTextEdit()
        self.log.setObjectName("sb_log")
        self.log.setReadOnly(True)
        root.addWidget(self.log, 1)
        self.btn_uri.clicked.connect(self.fill_from_uri)
        self.btn_clear.clicked.connect(self.clear_auth)
        self.btn_verify.clicked.connect(self.verify)
        self.btn_save.clicked.connect(self.save_profile)
        self.btn_install.clicked.connect(self.install)
        self.ed_ref.textChanged.connect(self._autofill)
        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self._refresh_auth)
        self._timer.start()
        self.load_profile()

    # ---- helperi UI
    def _line(self, name: str, placeholder: str = "") -> QLineEdit:
        w = QLineEdit()
        w.setObjectName(name)
        w.setPlaceholderText(placeholder)
        return w

    def _btn(self, name: str, text: str, lay) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName(name)
        lay.addWidget(b)
        return b

    def say(self, text: str) -> None:
        self.log.appendPlainText(text)

    def _autofill(self) -> None:
        ref = self.ed_ref.text().strip().lower()
        if len(ref) == 20:
            if not self.ed_url.text().strip():
                self.ed_url.setText(f"https://{ref}.supabase.co")
            if not self.ed_user.text().strip() and "pooler" in self.ed_host.text():
                self.ed_user.setText(f"postgres.{ref}")

    def inputs(self) -> sb.SupabaseInput:
        return sb.SupabaseInput(self.ed_ref.text(), self.ed_url.text(), self.ed_host.text(), self.sp_port.value(), self.ed_user.text(), "postgres", self.ed_bucket.text()).normalized()

    def load_profile(self) -> None:
        i = sb.input_from_profile()
        if i.project_ref:
            self.ed_ref.setText(i.project_ref)
            self.ed_url.setText(i.supabase_url)
            self.ed_host.setText(i.db_host)
            self.sp_port.setValue(i.db_port)
            self.ed_user.setText(i.db_user)
            self.ed_bucket.setText(i.storage_bucket)
        prof = simple.current_profile()
        if prof:
            self.cb_env.setCurrentText(simple.env_label(prof.environment))

    # ---- autorizare temporară
    def _authorize(self) -> bool:
        pw = self.ed_pw.text()
        if pw:
            try:
                self.session.authorize(pw)
            except sb.CenterError as exc:
                QMessageBox.warning(self, "Parolă invalidă", str(exc))
                return False
            self.ed_pw.clear()                          # câmpul nu păstrează parola; rămâne doar în sesiunea temporară
        if not self.session.active:
            QMessageBox.information(self, "Autorizare necesară", "Introdu parola bazei de date Supabase (autorizare temporară, nu se salvează).")
            return False
        self._refresh_auth()
        return True

    def _refresh_auth(self) -> None:
        self.lbl_auth.setText("Autorizare temporară ACTIVĂ (în memorie, expiră în max. 15 min)" if self.session.active else "Fără autorizare activă")
        self.lbl_auth.setStyleSheet(f"color:{cc.COLORS[cc.ST_OK if self.session.active else cc.ST_NONE]};")

    def clear_auth(self) -> None:
        self.session.clear()
        self.ed_pw.clear()
        self.ed_uri.clear()
        self._refresh_auth()
        self.say("Autorizarea temporară a fost ștearsă.")

    def fill_from_uri(self) -> None:
        try:
            fields, pw = sb.parse_connection_string(self.ed_uri.text())
        except sb.CenterError as exc:
            QMessageBox.warning(self, "Șir invalid", str(exc))
            return
        self.ed_uri.clear()
        self.ed_ref.setText(fields["project_ref"])
        self.ed_host.setText(fields["db_host"])
        self.sp_port.setValue(int(fields["db_port"]))
        self.ed_user.setText(fields["db_user"])
        if pw:
            self.ed_pw.setText(pw)
        self._autofill()
        self.say("Câmpurile au fost completate din șirul de conexiune; șirul și parola nu sunt afișate.")

    # ---- 1. verificare
    def verify(self) -> None:
        if self._busy or not self._dlg.guard() or not self._authorize():
            return
        inp, pw = self.inputs(), self.session.password
        self._set_busy(True)
        self.say("Verific proiectul Supabase (doar citire)…")
        self._task = run_bg(self, lambda: sb.verify_project(inp, pw, connect=self._connect), self._on_verified, self._on_failed)

    def _on_verified(self, res: "sb.VerifyResult") -> None:
        self._set_busy(False)
        self.result = res
        self.say(res.text())
        cc.remember_supabase_check(self.inputs().project_ref, res.summary())
        if res.ok:
            self.say("Rezultat: proiectul este valid." + (f" Lipsesc migrările: {', '.join(res.pending)}." if res.pending else " Schema este la zi."))
        else:
            self.say("Rezultat: proiect RESPINS sau incomplet. Corectează erorile de mai sus și verifică din nou. Nimic nu a fost modificat.")
        self._dlg.refresh_status()

    def _on_failed(self, msg: str) -> None:
        self._set_busy(False)
        self.say("Eroare: " + msg)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for b in (self.btn_verify, self.btn_save, self.btn_install):
            b.setEnabled(not busy)

    # ---- 2. profil
    def save_profile(self) -> bool:
        if not self._dlg.guard():
            return False
        inp = self.inputs()
        warn = sb.reconfigure_warning(inp)
        if warn and QMessageBox.warning(self, "Reconfigurare proiect", warn + "\n\nContinui?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return False
        label, confirm = self.cb_env.currentText(), False
        if simple.env_value(label) == "PRODUCTION":
            cur = simple.current_profile()
            if not (cur and cur.is_active and cur.environment == "PRODUCTION"):
                if QMessageBox.warning(self, "ATENȚIE — PROD", "Profilul PROD lucrează cu date REALE.\n\nConfirmi?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                    return False
            confirm = True
        try:
            sb.save_to_profile(inp, label, confirm_production=confirm)
        except cp.ValidationFailed as exc:
            QMessageBox.warning(self, "Date invalide", "\n".join(exc.errors))
            return False
        except cp.ProfileError as exc:
            QMessageBox.warning(self, "Eroare", str(exc))
            return False
        self.say("Datele proiectului au fost salvate în profilul Cloud (fără parolă).")
        self._dlg.refresh_status()
        return True

    # ---- 3. instalare / actualizare
    def install(self) -> None:
        if self._busy or not self._dlg.guard():
            return
        if self.result is None or not self.result.ok:
            QMessageBox.information(self, "Verificare necesară", "Rulează mai întâi „Verifică proiectul”; instalarea cere un proiect valid.")
            return
        try:
            pv = sb.preview(self.result, self.inputs())
        except sb.CenterError as exc:
            QMessageBox.warning(self, "Instalare indisponibilă", str(exc))
            return
        if not pv["executable"]:
            QMessageBox.information(self, "Nimic de instalat", "Schema este la zi." if not pv["blocked"] else "Planul este blocat:\n" + "\n".join(pv["blocked"]))
            return
        if not self.ask_preview(pv) or not self._authorize():
            return
        self._set_busy(True)
        self.say("Creez backupul local MAIN înainte de instalare…")
        self._task = run_bg(self, sb.local_backup, self._after_local_backup, self._on_failed)

    def ask_preview(self, pv) -> bool:
        text = ("Se vor executa, într-o singură tranzacție (rollback complet la eroare):\n\n• " + "\n• ".join(pv["steps"])
                + "\n\nÎnainte de instalare se face un backup local MAIN verificat.")
        return QMessageBox.question(self, "Previzualizare instalare", text, QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel) == QMessageBox.Ok

    def ask_db_backup(self) -> str:
        QMessageBox.information(self, "Backup bază Cloud", "Proiectul conține date. Indică un fișier de backup al bazei (pg_dump/.zip/.sql/.gz) făcut din Supabase.")
        path, _ = QFileDialog.getOpenFileName(self, "Backup bază Cloud", "", "Backup (*.zip *.sql *.gz *.tar *.dump *.backup);;Toate (*)")
        return path

    def ask_confirmation(self, phrase: str) -> str:
        text, ok = QInputDialog.getText(self, "Confirmare finală", f"Pentru a continua tastează exact:\n{phrase}")
        return text if ok else ""

    def _after_local_backup(self, path: Path) -> None:
        self.local_backup_path = str(path)
        self.say(f"Backup local verificat: {Path(path).name}")
        pv = sb.preview(self.result, self.inputs())
        db_path = "" if self.result.empty_project else self.ask_db_backup()
        info = sb.db_backup_info(self.result, db_path)
        self.say("Backup bază Cloud: " + (info.get("detail") or "invalid"))
        if not info.get("ok"):
            self._set_busy(False)
            return
        phrase = self.ask_confirmation(pv["confirm_phrase"])
        if not phrase:
            self._set_busy(False)
            self.say("Instalare anulată: nimic nu a fost modificat.")
            return
        inp, pw, res, lb = self.inputs(), self.session.password, self.result, self.local_backup_path
        self.say("Aplic migrările…")
        self._task = run_bg(self, lambda: sb.apply_schema(inp, pw, res, phrase, lb, info, connect=self._url_connect, bucket_connect=self._bucket_connect),
                            self._on_installed, self._on_failed)

    def _on_installed(self, out) -> None:
        self._set_busy(False)
        schema = out.get("schema") or {}
        for line in schema.get("log", []):
            self.say(line)
        if schema and not schema.get("executed"):
            self.say(schema.get("message", "Instalarea nu s-a executat."))
            return
        if out.get("bucket"):
            self.say(out["bucket"])
        self.say("Instalare finalizată. Reverific proiectul…")
        self.verify()
