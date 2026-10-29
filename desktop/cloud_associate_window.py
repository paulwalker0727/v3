"""cloud_associate_window.py — „Administrare → Cloud → Asociază acest calculator” + „Coduri și dispozitive”. Fereastra doar apelează `cimitir_cloud_associate` / `cimitir_cloud_admin_client`.

Cheia definitivă nu apare niciodată în UI: se salvează direct în SecretStore (DPAPI). Parola de administrator nu se păstrează (doar tokenul de sesiune, în memorie).
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

import access_session
import cimitir_bootstrap as bs
import cimitir_cloud_admin_client as adc
import cimitir_cloud_associate as assoc
import cimitir_qr as qrlink
import cimitir_registry as reg
import cimitir_secretstore as ss
import field_inbox_client as fic
from field_worker import run_task


def _run(owner, fn, ok, fail):
    """Ca run_task, dar mesajele AssociationError/AdminApiError (fără secrete, în română) ajung în UI."""
    def wrapped():
        try:
            return fn()
        except (assoc.AssociationError, adc.AdminApiError) as exc:
            raise fic.FieldCloudError(str(exc)) from None
    return run_task(owner, wrapped, ok, fail)


def default_url() -> str:
    try:
        import cloud_sync
        u = (cloud_sync.get_config().get("url") or "").strip()
        if u:
            return u
    except Exception:  # noqa: BLE001
        pass
    try:
        import database as db
        return (db.get_setting("cloud_url") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def local_choices(catalog=None) -> List[Dict[str, str]]:
    cat = catalog or reg.RegistryCatalog()
    orgs = {o["uid"]: o["display_name"] for o in cat.list_organizations()}
    return [{"cemetery_uid": c["uid"], "label": f"{orgs.get(c['organization_uid'], '?')} — {c['name']}"} for c in cat.list_cemeteries()]


class CloudAssociateDialog(QDialog):
    def __init__(self, parent=None, catalog=None, store=None, can_manage: Optional[Callable[[], bool]] = None, rcp_factory=None):
        super().__init__(parent)
        self.setWindowTitle("Cloud — asociere și coduri")
        self.setMinimumSize(900, 640)
        self._cat = catalog or reg.RegistryCatalog()
        self._store = store or ss.default_store()
        self._can = can_manage or (lambda: access_session.require("manage_config", self, "asocierea cu Cloud-ul"))
        self._factory = rcp_factory
        self._plan: Optional[Dict[str, Any]] = None
        self._code_ok = False
        self._admin: Optional[adc.AdminClient] = None
        self._me: Optional[Dict[str, Any]] = None
        self._issued: Optional[Dict[str, Any]] = None
        self._auto_admin: Optional[adc.AdminClient] = None
        self._auto_state: Optional[Dict[str, Any]] = None
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._build_assoc(), "Asociază acest calculator")
        tabs.addTab(self._build_codes(), "Coduri și dispozitive (administrator)")
        lay.addWidget(tabs)

    # ================================================================== tab 1
    def _build_assoc(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        form = QFormLayout()
        self.ed_url = QLineEdit(default_url())
        self.ed_url.setObjectName("assoc_url")
        self.ed_url.setPlaceholderText("https://adresa-cloud.exemplu.ro")
        self.btn_check = QPushButton("Verifică conexiunea")
        self.btn_check.setObjectName("assoc_check")
        row = QHBoxLayout()
        row.addWidget(self.ed_url, 1)
        row.addWidget(self.btn_check)
        form.addRow("Adresa Cloud (URL):", row)
        self.cmb_local = QComboBox()
        self.cmb_local.setObjectName("assoc_local")
        for c in local_choices(self._cat):
            self.cmb_local.addItem(c["label"], c["cemetery_uid"])
        cur = reg.current_binding()
        if cur:
            i = self.cmb_local.findData(cur.cemetery_uid)
            self.cmb_local.setCurrentIndex(max(i, 0))
        form.addRow("Organizație / cimitir local:", self.cmb_local)
        info = QLabel("<b>Două autentificări diferite:</b> contul LOCAL (administratorul MAIN, deja autentificat) permite acest ecran; "
                      "contul <b>administrator CLOUD</b> (email + parolă primite de la administratorul serverului) autorizează crearea registrului în Cloud și codul de asociere. "
                      "Parolele nu se păstrează și cheia definitivă ajunge direct în SecretStore.")
        info.setWordWrap(True)
        form.addRow(info)
        self.ed_cmail = QLineEdit()
        self.ed_cmail.setObjectName("assoc_cloud_email")
        self.ed_cpass = QLineEdit()
        self.ed_cpass.setEchoMode(QLineEdit.Password)
        self.ed_cpass.setObjectName("assoc_cloud_pass")
        self.btn_auto_check = QPushButton("Autentifică-te în Cloud și verifică registrul")
        self.btn_auto_check.setObjectName("assoc_auto_check")
        form.addRow("Administrator Cloud — email:", self.ed_cmail)
        form.addRow("Administrator Cloud — parolă:", self.ed_cpass)
        form.addRow(self.btn_auto_check)
        self.chk_auto = QCheckBox("Confirm: creez în Cloud (dacă lipsesc) organizația și cimitirul cu UID-urile originale și asociez acest calculator")
        self.chk_auto.setObjectName("assoc_auto_confirm")
        self.chk_auto.setEnabled(False)
        self.btn_auto_go = QPushButton("Provizionează (dacă e nevoie) și asociază automat")
        self.btn_auto_go.setObjectName("assoc_auto_go")
        self.btn_auto_go.setEnabled(False)
        form.addRow(self.chk_auto)
        form.addRow(self.btn_auto_go)
        self.chk_req = QCheckBox("Confirm: trimit către Cloud UID-urile și denumirile registrului local, pentru confirmarea administratorului Cloud (fără parolă pe acest calculator)")
        self.chk_req.setObjectName("assoc_req_confirm")
        self.btn_req = QPushButton("Trimite cererea de înregistrare")
        self.btn_req.setObjectName("assoc_req_send")
        self.btn_req_cancel = QPushButton("Anulează cererea")
        self.btn_req_cancel.setObjectName("assoc_req_cancel")
        self.btn_req_cancel.setEnabled(False)
        form.addRow(QLabel("— varianta fără parolă Cloud aici: cerere confirmată de administrator în /admin/ —"))
        form.addRow(self.chk_req)
        row_r = QHBoxLayout()
        row_r.addWidget(self.btn_req)
        row_r.addWidget(self.btn_req_cancel)
        form.addRow(row_r)
        form.addRow(QLabel("— sau, varianta manuală, cu un cod primit de la administrator —"))
        self.ed_code = QLineEdit()
        self.ed_code.setObjectName("assoc_code")
        self.ed_code.setPlaceholderText("Codul temporar primit de la administrator")
        self.btn_verify = QPushButton("Verifică codul")
        self.btn_verify.setObjectName("assoc_verify")
        row2 = QHBoxLayout()
        row2.addWidget(self.ed_code, 1)
        row2.addWidget(self.btn_verify)
        form.addRow("Cod de asociere:", row2)
        lay.addLayout(form)
        self.txt_summary = QPlainTextEdit()
        self.txt_summary.setReadOnly(True)
        self.txt_summary.setObjectName("assoc_summary")
        lay.addWidget(self.txt_summary, 1)
        self.chk_confirm = QCheckBox("Confirm: asociez acest calculator cu Cloud-ul conform rezumatului de mai sus")
        self.chk_confirm.setObjectName("assoc_confirm")
        self.chk_confirm.setEnabled(False)
        lay.addWidget(self.chk_confirm)
        self.chk_media = QCheckBox("Confirm: unele fișiere media (fotografii/documente) lipsesc de pe disc; continui fără ele")
        self.chk_media.setObjectName("assoc_media_confirm")
        self.chk_media.setVisible(False)
        lay.addWidget(self.chk_media)
        btns = QHBoxLayout()
        self.btn_go = QPushButton("Asociază")
        self.btn_go.setObjectName("assoc_go")
        self.btn_go.setEnabled(False)
        self.btn_resume = QPushButton("Reia asocierea")
        self.btn_abort = QPushButton("Anulează asocierea")
        self.btn_unbind = QPushButton("Dezasociază")
        for b in (self.btn_go, self.btn_resume, self.btn_abort, self.btn_unbind):
            btns.addWidget(b)
        lay.addLayout(btns)
        self.btn_check.clicked.connect(self._check_api)
        self.btn_auto_check.clicked.connect(self._auto_check)
        self.chk_auto.toggled.connect(lambda on: self.btn_auto_go.setEnabled(bool(on and self._auto_state)))
        self.btn_auto_go.clicked.connect(self._auto_go)
        self.btn_req.clicked.connect(self._req_send)
        self.btn_req_cancel.clicked.connect(self._req_cancel)
        self._req_timer = QTimer(self)
        self._req_timer.setInterval(4000)
        self._req_timer.timeout.connect(self._req_poll)
        self.btn_verify.clicked.connect(self._verify_code)
        self.chk_confirm.toggled.connect(lambda on: self.btn_go.setEnabled(bool(on and self._plan)))
        self.btn_go.clicked.connect(self._execute)
        self.btn_resume.clicked.connect(self._resume)
        self.btn_abort.clicked.connect(self._abort)
        self.btn_unbind.clicked.connect(self._unbind)
        QTimer.singleShot(0, self._refresh_state)
        return w

    def _association(self) -> assoc.Association:
        cem = self.cmb_local.currentData()
        if not cem:
            raise assoc.AssociationError("Nu există niciun cimitir local selectat.", "local")
        binding = self._cat.binding_for(cem)
        return assoc.Association(binding, self._store, bs.installation_uid(self._cat), self.ed_url.text(), rcp_factory=self._factory)

    def _say(self, text: str) -> None:
        self.txt_summary.setPlainText(text)

    def _guarded(self, fn: Callable[[], Any], ok: Callable[[Any], None]) -> None:
        if not self._can():
            return
        try:
            a = self._association()
        except assoc.AssociationError as exc:
            self._say(str(exc))
            return

        _run(self, lambda: fn(a), ok, lambda m, _nc: self._say(m))

    def _refresh_state(self) -> None:
        try:
            a = self._association()
            info = a.incomplete()
            self.btn_resume.setEnabled(bool(info))
            self.btn_abort.setEnabled(bool(info))
            self.btn_unbind.setEnabled(a.local.sync_state in bs.SYNC_ACTIVE_STATES)
            st = a.local.sync_state
            self._say(f"Stare locală: {st}" + (f"\nAsociere întreruptă ({info['state']}, faza {info['phase']}): reia sau anulează." if info else ""))
        except Exception:  # noqa: BLE001
            self.btn_resume.setEnabled(False)

    def _names(self, a: assoc.Association):
        org = self._cat.get_organization(a.binding.organization_uid) or {}
        cem = self._cat.get_cemetery(a.binding.cemetery_uid) or {}
        return (org.get("display_name") or org.get("official_name") or "Organizație"), (cem.get("name") or "Cimitir")

    def _auto_check(self) -> None:
        email, pw = self.ed_cmail.text().strip(), self.ed_cpass.text()
        self.ed_cpass.clear()
        self._auto_state = None
        self.chk_auto.setChecked(False)
        self.chk_auto.setEnabled(False)
        self.btn_auto_go.setEnabled(False)

        def work(a):
            a.check_api()
            cl = adc.AdminClient(a.url)
            try:
                cl.login(email, pw)
                return cl, a.registry_in_cloud(cl)
            except Exception:
                cl.close()
                raise

        def ok(res):
            cl, st = res
            if self._auto_admin:
                self._auto_admin.close()
            self._auto_admin, self._auto_state = cl, st
            org_name, cem_name = (self._names(self._association()) if self.cmb_local.currentData() else ("", ""))
            self._say(f"Autentificat în Cloud.\n\nOrganizație locală: {org_name} ({st['organization_uid']})\nCimitir local: {cem_name} ({st['cemetery_uid']})\n\n{st['message']}"
                      + ("\n\nBifează confirmarea pentru a continua." if st["action"] in ("ready", "provision_cemetery", "provision_all") else ""))
            self.chk_auto.setEnabled(st["action"] in ("ready", "provision_cemetery", "provision_all"))
            if st["action"] not in ("ready", "provision_cemetery", "provision_all"):
                self._auto_state = None
        self._guarded(work, ok)

    def _auto_go(self) -> None:
        if not (self._auto_admin and self._auto_state and self.chk_auto.isChecked()):
            return
        self.btn_auto_go.setEnabled(False)
        cl = self._auto_admin

        def work(a):
            org_name, cem_name = self._names(a)
            return a.associate_with_admin(cl, org_name, cem_name, True)

        def ok(res):
            v = res["verification"]
            prov = res.get("provision")
            lines = [v["message"], ""]
            if prov:
                lines.append(f"✔ Provizionat în Cloud: organizație {'creată' if prov['organization_created'] else 'existentă'}, cimitir {'creat' if prov['cemetery_created'] else 'existent'} (UID-uri originale)")
            lines += [f"✔ {k}: {s}" for k, s in v["checks"].items()]
            self._say("\n".join(lines))
            self._auto_state = None
            self.chk_auto.setChecked(False)
            self._refresh_buttons()
        self._guarded(work, ok)

    def _req_send(self) -> None:
        if not self.chk_req.isChecked():
            self._say("Bifează confirmarea pentru a trimite cererea.")
            return

        def work(a):
            org_name, cem_name = self._names(a)
            return a.request_registration(org_name, cem_name, True)

        def ok(res):
            self._say(res["message"])
            self.btn_req.setEnabled(False)
            self.btn_req_cancel.setEnabled(True)
            self._req_timer.start()
        self._guarded(work, ok)

    def _req_poll(self) -> None:
        if getattr(self, "_req_busy", False):
            return
        self._req_busy = True

        def work(a):
            return a.complete_registration(self._names(a)[1])

        def done(res):
            self._req_busy = False
            if res["state"] == "pending":
                return
            self._req_timer.stop()
            v = res["verification"]
            self._say(v["message"] + "\n\n" + "\n".join(f"✔ {k}: {s}" for k, s in v["checks"].items()))
            self.btn_req.setEnabled(True)
            self.btn_req_cancel.setEnabled(False)
            self.chk_req.setChecked(False)
            self._refresh_buttons()

        def fail(msg, _nc):
            self._req_busy = False
            self._req_timer.stop()
            self.btn_req.setEnabled(True)
            self.btn_req_cancel.setEnabled(False)
            self._say(msg)
        if not self._can():
            self._req_busy = False
            return
        try:
            a = self._association()
        except assoc.AssociationError as exc:
            self._req_busy = False
            self._say(str(exc))
            return
        _run(self, lambda: work(a), done, fail)

    def _req_cancel(self) -> None:
        self._req_timer.stop()
        try:
            self._association().cancel_request()
        except Exception:  # noqa: BLE001
            pass
        self.btn_req.setEnabled(True)
        self.btn_req_cancel.setEnabled(False)
        self._say("Cererea a fost anulată local. Administratorul Cloud o poate respinge sau ea expiră în 24 h.")

    def _check_api(self) -> None:
        self._guarded(lambda a: a.check_api(), lambda r: self._say(f"{r['message']}\nHTTPS: {'da' if r['https'] else 'NU (doar localhost)'}\nBază de date: {r['database']} · stocare: {r['storage']}"))

    def _verify_code(self) -> None:
        def ok(plan):
            self._plan = plan["plan"]
            self.chk_confirm.setEnabled(True)
            self.chk_media.setChecked(False)
            self.chk_media.setVisible(bool(plan["plan"].get("media_missing")))
            self._say("Cod valid. Rezumat operațiune:\n\n" + "\n".join(f"• {k}: {v}" for k, v in plan["summary"].items()) + "\n\nBifează confirmarea pentru a continua.")
        code = self.ed_code.text().strip()

        def work(a):
            a.verify_code(code)
            return a.plan(code)
        self._plan = None
        self.chk_confirm.setChecked(False)
        self.chk_confirm.setEnabled(False)
        self.chk_media.setVisible(False)
        self._guarded(work, ok)

    def _execute(self) -> None:
        plan, code = self._plan, self.ed_code.text().strip()
        if not plan or not self.chk_confirm.isChecked():
            return
        if plan.get("media_missing") and not self.chk_media.isChecked():
            self._say("Unele fișiere media lipsesc de pe disc. Bifează confirmarea suplimentară pentru a continua fără ele sau repară fișierele și verifică din nou codul.")
            return
        accept_media = self.chk_media.isChecked()
        self.btn_go.setEnabled(False)
        self._say("Asociere în curs…" + (" Se încarcă registrul în Cloud (poate dura); nu închide aplicația." if plan.get("kind") == "B" else ""))

        def ok(res):
            self._plan = None
            self.chk_media.setVisible(False)
            self.ed_code.clear()
            v = res["verification"]
            self._say(v["message"] + "\n\n" + "\n".join(f"✔ {k}: {s}" for k, s in v["checks"].items()))
            self._refresh_buttons()

        def fail_refresh(a):
            return a.execute(plan, code, True, accept_missing_media=accept_media)
        self._guarded(fail_refresh, ok)

    def _refresh_buttons(self) -> None:
        QTimer.singleShot(0, lambda: (self.btn_unbind.setEnabled(True), self.btn_resume.setEnabled(False), self.btn_abort.setEnabled(False)))

    def _resume(self) -> None:
        self._guarded(lambda a: a.resume(self.ed_code.text().strip()),
                      lambda r: (self._say(r.get("verification", {}).get("message", "Asociere reluată.")), self._refresh_state()))

    def _abort(self) -> None:
        if QMessageBox.question(self, "Anulează asocierea", "Se revocă legătura începută și se șterge cheia. Datele locale rămân. Continui?") != QMessageBox.Yes:
            return
        self._guarded(lambda a: a.abort(), lambda r: (self._say(r["message"]), self._refresh_state()))

    def _unbind(self) -> None:
        if QMessageBox.question(self, "Dezasociază", "Calculatorul nu va mai sincroniza cu Cloud-ul. Continui?") != QMessageBox.Yes:
            return
        self._guarded(lambda a: a.disassociate(), lambda r: (self._say(r["message"]), self._refresh_state()))

    # ================================================================== tab 2
    def _build_codes(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        form = QFormLayout()
        self.ed_mail = QLineEdit()
        self.ed_mail.setObjectName("adm_email")
        self.ed_pass = QLineEdit()
        self.ed_pass.setEchoMode(QLineEdit.Password)
        self.ed_pass.setObjectName("adm_pass")
        self.btn_login = QPushButton("Autentifică-te ca administrator")
        form.addRow("Email administrator:", self.ed_mail)
        form.addRow("Parolă:", self.ed_pass)
        lay.addLayout(form)
        lay.addWidget(self.btn_login)
        self.cmb_org = QComboBox()
        self.cmb_cem = QComboBox()
        row = QHBoxLayout()
        row.addWidget(self.cmb_org)
        row.addWidget(self.cmb_cem)
        lay.addLayout(row)
        r2 = QHBoxLayout()
        self.btn_main = QPushButton("Generează cod pentru MAIN")
        self.btn_main.setObjectName("adm_issue_main")
        self.btn_phone = QPushButton("Generează cod pentru telefon")
        self.btn_phone.setObjectName("adm_issue_phone")
        self.btn_copy = QPushButton("Copiază codul")
        self.btn_revoke_code = QPushButton("Revocă codul")
        for b in (self.btn_main, self.btn_phone, self.btn_copy, self.btn_revoke_code):
            r2.addWidget(b)
            b.setEnabled(False)
        lay.addLayout(r2)
        self.lbl_code = QLabel("—")
        self.lbl_code.setObjectName("adm_code")
        self.lbl_code.setStyleSheet("font-size: 24px; font-weight: bold; letter-spacing: 3px;")
        self.lbl_code.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_left = QLabel("")
        self.lbl_qr = QLabel()
        self.lbl_qr.setObjectName("adm_qr")
        self.lbl_qr.setVisible(False)
        lay.addWidget(self.lbl_code)
        lay.addWidget(self.lbl_qr)
        lay.addWidget(self.lbl_left)
        self.tbl_dev = QTableWidget(0, 5)
        self.tbl_dev.setHorizontalHeaderLabels(("Nume", "Tip", "Cimitir", "Ultima activitate", "Stare"))
        self.tbl_dev.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_dev.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_dev.setEditTriggers(QAbstractItemView.NoEditTriggers)
        lay.addWidget(self.tbl_dev, 1)
        self.btn_rev_dev = QPushButton("Revocă dispozitivul selectat")
        self.btn_rev_dev.setEnabled(False)
        lay.addWidget(self.btn_rev_dev)
        self.lbl_adm = QLabel("")
        lay.addWidget(self.lbl_adm)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self.btn_login.clicked.connect(self._login)
        self.btn_main.clicked.connect(lambda: self._issue("main"))
        self.btn_phone.clicked.connect(lambda: self._issue("phone"))
        self.btn_copy.clicked.connect(self._copy)
        self.btn_revoke_code.clicked.connect(self._revoke_code)
        self.btn_rev_dev.clicked.connect(self._revoke_device)
        self.cmb_org.currentIndexChanged.connect(self._org_changed)
        return w

    def _client(self) -> adc.AdminClient:
        if self._admin is None:
            self._admin = adc.AdminClient(assoc.normalize_url(self.ed_url.text()))
        return self._admin

    def _login(self) -> None:
        email, pw = self.ed_mail.text().strip(), self.ed_pass.text()
        self.ed_pass.clear()

        def work():
            self._admin = None
            return self._client().login(email, pw)

        def ok(me):
            self._me = me
            self.cmb_org.clear()
            for o in me["organizations"]:
                self.cmb_org.addItem(o["name"] or o["organization_uid"], o["organization_uid"])
            for b in (self.btn_main, self.btn_phone):
                b.setEnabled(True)
            self.lbl_adm.setText(f"Autentificat: {me['email']}")
            self._org_changed()
        _run(self, work, ok, lambda m, _nc: self.lbl_adm.setText(m))

    def _org_changed(self) -> None:
        self.cmb_cem.clear()
        org = next((o for o in (self._me or {}).get("organizations", []) if o["organization_uid"] == self.cmb_org.currentData()), None)
        for c in (org or {}).get("cemeteries", []):
            self.cmb_cem.addItem(f"{c['name']} ({c['status']})", c["uid"])
        self._reload_devices()

    def _reload_devices(self) -> None:
        org = self.cmb_org.currentData()
        if not org or not self._admin:
            return

        def ok(devs):
            self.tbl_dev.setRowCount(0)
            for d in devs:
                i = self.tbl_dev.rowCount()
                self.tbl_dev.insertRow(i)
                for c, v in enumerate((d["device_name"], "MAIN" if d["kind"] == "main" else "Telefon", d["cemetery_name"], (d["last_seen_at"] or "—")[:16], d["state"])):
                    cell = QTableWidgetItem(v)
                    if c == 0:
                        cell.setData(Qt.UserRole, (d["binding_uid"], d["state"]))
                    self.tbl_dev.setItem(i, c, cell)
            self.btn_rev_dev.setEnabled(bool(devs))
        _run(self, lambda: self._admin.list_devices(org), ok, lambda m, _nc: self.lbl_adm.setText(m))

    def _issue(self, kind: str) -> None:
        org, cem = self.cmb_org.currentData(), self.cmb_cem.currentData()
        if not (org and cem and self._admin):
            return

        def ok(res):
            self._issued = res
            self.lbl_code.setText(res["display"])
            self._show_qr(res)
            self.btn_copy.setEnabled(True)
            self.btn_revoke_code.setEnabled(True)
            self._timer.start()
            self._tick()
        _run(self, lambda: self._admin.issue_code(org, cem, kind), ok, lambda m, _nc: self.lbl_adm.setText(m))

    def _show_qr(self, res: Dict[str, Any]) -> None:
        """QR doar pentru codul de telefon: link `<cloud>/mobile/#pair=<cod>`; telefonul se asociază automat la scanare."""
        self.lbl_qr.setVisible(False)
        if res.get("kind") != "phone" or not self._admin:
            return
        link = res.get("pair_url") or qrlink.pair_url(self._admin.base[:-len("/api/v1/admin")], res.get("code", ""))
        if not link:
            return
        try:
            from PySide6.QtGui import QPixmap
            pm = QPixmap()
            if pm.loadFromData(qrlink.png_bytes(link)):
                self.lbl_qr.setPixmap(pm)
                self.lbl_qr.setVisible(True)
        except Exception:  # noqa: BLE001  (fără generator QR: codul manual rămâne disponibil)
            pass

    def _tick(self) -> None:
        if not self._issued:
            return
        left = int(self._issued["expires_at"] - time.time())
        if left <= 0:
            self._timer.stop()
            self.lbl_left.setText("Codul a expirat. Generează unul nou (regenerare).")
            self.lbl_code.setText("—")
            self.lbl_qr.setVisible(False)
            self._issued = None
            return
        self.lbl_left.setText(f"Valabil încă {left // 60}:{left % 60:02d}")

    def _copy(self) -> None:
        if self._issued:
            QGuiApplication.clipboard().setText(self._issued["code"])
            self.lbl_adm.setText("Cod copiat în clipboard.")

    def _revoke_code(self) -> None:
        if not (self._issued and self._admin):
            return
        org, cid = self.cmb_org.currentData(), self._issued["code_id"]

        def ok(_r):
            self._issued = None
            self._timer.stop()
            self.lbl_code.setText("—")
            self.lbl_qr.setVisible(False)
            self.lbl_left.setText("Cod revocat.")
        _run(self, lambda: self._admin.revoke_code(org, cid), ok, lambda m, _nc: self.lbl_adm.setText(m))

    def _revoke_device(self) -> None:
        it = self.tbl_dev.item(self.tbl_dev.currentRow(), 0) if self.tbl_dev.currentRow() >= 0 else None
        if not it or not self._admin or it.data(Qt.UserRole)[1] == "revoked":
            return
        if QMessageBox.question(self, "Revocă dispozitivul", f"„{it.text()}” nu va mai putea sincroniza. Continui?") != QMessageBox.Yes:
            return
        org, bu = self.cmb_org.currentData(), it.data(Qt.UserRole)[0]
        _run(self, lambda: self._admin.revoke_device(org, bu), lambda _r: self._reload_devices(), lambda m, _nc: self.lbl_adm.setText(m))

    def closeEvent(self, ev) -> None:  # noqa: N802
        if self._auto_admin:
            try:
                self._auto_admin.close()
            except Exception:  # noqa: BLE001
                pass
        if self._admin:
            try:
                self._admin.close()
            except Exception:  # noqa: BLE001
                pass
        super().closeEvent(ev)
