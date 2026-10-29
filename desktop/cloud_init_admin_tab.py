"""cloud_init_admin_tab.py — fila „Inițializare administrator Cloud” (fără BOOTSTRAP_TOKEN manual, fără recrearea containerului).
Autorizarea este cheia proprietarului instanței (SecretStore), nu licența. Parola nu se păstrează; grantul nu se afișează și nu se loghează."""
from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtWidgets import QCheckBox, QFormLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

import cimitir_cloud_init_admin as cia
import field_inbox_client as fic
from field_worker import run_task


def _wrap(fn):
    def w():
        try:
            return fn()
        except cia.InitError as exc:
            raise fic.FieldCloudError(str(exc)) from None
    return w


class InitAdminTab(QWidget):
    def __init__(self, store, url_getter: Callable[[], str], can: Callable[[], bool], parent=None, client=None):
        super().__init__(parent)
        self._store, self._url, self._can, self._client = store, url_getter, can, client
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Creează primul administrator al instanței Cloud direct din MAIN. Se folosește cheia proprietarului instanței (nu licența)."))
        self.btn_status = QPushButton("Verifică instanța")
        self.btn_status.setObjectName("init_status")
        self.btn_key = QPushButton("Generează cheia proprietarului (o singură dată)")
        self.btn_key.setObjectName("init_key")
        form = QFormLayout()
        self.ed_email, self.ed_pw, self.ed_org, self.ed_cem = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        self.ed_org.setObjectName("init_org_name")
        self.ed_cem.setObjectName("init_cem_name")
        self.ed_email.setObjectName("init_email")
        self.ed_pw.setObjectName("init_password")
        self.ed_pw.setEchoMode(QLineEdit.Password)
        form.addRow("Denumirea organizației (parohie/primărie)", self.ed_org)
        form.addRow("Denumirea cimitirului (opțional)", self.ed_cem)
        form.addRow("Email administrator", self.ed_email)
        form.addRow("Parolă (min. 12 caractere)", self.ed_pw)
        self.chk = QCheckBox("Confirm: inițializez primul administrator al acestei instanțe Cloud")
        self.chk.setObjectName("init_confirm")
        self.btn_go = QPushButton("Inițializează administratorul")
        self.btn_go.setObjectName("init_go")
        self.btn_go.setEnabled(False)
        self.chk.toggled.connect(self.btn_go.setEnabled)
        self.txt = QPlainTextEdit()
        self.txt.setObjectName("init_out")
        self.txt.setReadOnly(True)
        for w in (self.btn_status, self.btn_key):
            lay.addWidget(w)
        lay.addLayout(form)
        for w in (self.chk, self.btn_go, self.txt):
            lay.addWidget(w)
        self.btn_status.clicked.connect(self._status)
        self.btn_key.clicked.connect(self._key)
        self.btn_go.clicked.connect(self._go)

    def _ia(self) -> cia.InitAdmin:
        return cia.InitAdmin(self._url(), self._store, self._client)

    def _bg(self, fn: Callable[[], Any], ok: Callable[[Any], None]) -> None:
        if not self._can():
            return
        try:
            ia = self._ia()
        except Exception as exc:  # noqa: BLE001
            self.txt.setPlainText(str(exc))
            return
        run_task(self, _wrap(lambda: fn(ia)), ok, lambda m, _nc: self.txt.setPlainText(m))

    def _status(self) -> None:
        def ok(st: dict) -> None:
            self.txt.setPlainText(
                f"Instanță: {st.get('instance_id') or '—'}\nAdministrator existent: {'da' if st['initialized'] else 'nu'}\n"
                f"Autorizare prin grant semnat: {'acceptată' if st['grant_supported'] else 'neconfigurată pe server'}\n"
                f"Cheie proprietar pe acest calculator: {'da' if st['owner_key_present'] else 'nu'}"
                + (f" ({'corespunde serverului' if st['owner_key_matches_server'] else 'NU corespunde serverului'})" if st["owner_key_present"] else ""))
        self._bg(lambda ia: ia.status(), ok)

    def _key(self) -> None:
        def ok(r: dict) -> None:
            self.txt.setPlainText(f"Cheie creată. Pune această cheie PUBLICĂ în CIMITIR_INSTANCE_ADMIN_PUBKEY la provisionarea instanței "
                                  f"(împreună cu CIMITIR_INSTANCE_ID={r['instance_id']}); amprenta: {r['fingerprint']}\n\n{r['public_key']}\n\nCheia privată rămâne doar în SecretStore.")
        def work(ia):
            iid = ia.status()["instance_id"]
            if not iid:
                raise cia.InitError("Serverul nu anunță un identificator de instanță (CIMITIR_INSTANCE_ID).")
            return ia.create_owner_key(iid)
        self._bg(work, ok)

    def _go(self) -> None:
        email, pw, confirmed = self.ed_email.text().strip(), self.ed_pw.text(), self.chk.isChecked()
        org, cem = self.ed_org.text().strip(), self.ed_cem.text().strip()
        self.btn_go.setEnabled(False)

        def ok(r: dict) -> None:
            self.ed_pw.clear()
            self.chk.setChecked(False)
            self.txt.setPlainText(r["message"])
        self._bg(lambda ia: ia.initialize(email, pw, confirmed, None, org, None, cem), ok)
