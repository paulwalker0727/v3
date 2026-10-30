"""mobile_pairing_panel.py — MAIN → Telefoane Mobile → „Asociere telefon”: cod/QR direct din MAIN, expirare, revocare, reînnoire, telefoane asociate.

Fiecare acțiune afișează un rezultat vizibil (stare + mesaj în română); niciun buton nu „nu face nimic”. Codul brut nu se salvează și nu se loghează.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

import cimitir_qr as qrlink
import field_inbox_client as fic
import mobile_pairing as mpair
from field_worker import run_task


def _bg(owner, fn: Callable[[], Any], ok, fail) -> None:
    def wrapped():
        try:
            return fn()
        except mpair.PairingProblem as exc:
            raise fic.FieldCloudError(str(exc)) from None
        except Exception as exc:  # noqa: BLE001
            raise fic.FieldCloudError(mpair.explain(exc)) from None
    run_task(owner, wrapped, ok, lambda m, _nc: fail(m))


class MobilePairingPanel(QWidget):
    permissions_requested = Signal(str)       # ref (binding_uid) al telefonului pentru care se cer drepturi

    def __init__(self, parent=None):
        super().__init__(parent)
        self._issued: Optional[Dict[str, Any]] = None
        self._phones: List[Dict[str, Any]] = []
        lay = QVBoxLayout(self)
        intro = QLabel("Asociază un telefon Mobile direct din MAIN: generează un cod (sau QR), introdu-l pe telefon în aplicația Mobile. Codul se folosește o singură dată și expiră. "
                       "Un telefon nou pornește doar cu drepturi de citire; drepturile de editare le acorzi din „Permisiuni Mobile”.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.lbl_server = QLabel("Backend: se verifică…")
        self.lbl_server.setObjectName("pair_server")
        self.lbl_server.setWordWrap(True)
        lay.addWidget(self.lbl_server)
        row = QHBoxLayout()
        self.btn_issue = QPushButton("Generează cod pentru telefon")
        self.btn_issue.setObjectName("pair_issue")
        self.btn_renew = QPushButton("Reînnoiește codul")
        self.btn_renew.setObjectName("pair_renew")
        self.btn_copy = QPushButton("Copiază codul")
        self.btn_copy.setObjectName("pair_copy")
        self.btn_revoke = QPushButton("Revocă codul")
        self.btn_revoke.setObjectName("pair_revoke")
        for b in (self.btn_issue, self.btn_renew, self.btn_copy, self.btn_revoke):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.lbl_code = QLabel("—")
        self.lbl_code.setObjectName("pair_code")
        self.lbl_code.setStyleSheet("font-size: 26px; font-weight: bold; letter-spacing: 3px;")
        self.lbl_code.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_left = QLabel("")
        self.lbl_left.setObjectName("pair_left")
        self.lbl_qr = QLabel()
        self.lbl_qr.setObjectName("pair_qr")
        self.lbl_qr.setVisible(False)
        for w in (self.lbl_code, self.lbl_left, self.lbl_qr):
            lay.addWidget(w)
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("pair_status")
        self.lbl_status.setWordWrap(True)
        lay.addWidget(self.lbl_status)
        self.table = QTableWidget(0, 5)
        self.table.setObjectName("pair_phones")
        self.table.setHorizontalHeaderLabels(["Telefon", "Asociat la", "Ultima activitate", "Drepturi", "Stare"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        lay.addWidget(self.table, 1)
        r2 = QHBoxLayout()
        self.btn_refresh = QPushButton("Reîmprospătează")
        self.btn_refresh.setObjectName("pair_refresh")
        self.btn_rights = QPushButton("Acordă drepturi telefonului selectat…")
        self.btn_rights.setObjectName("pair_rights")
        self.btn_rev_phone = QPushButton("Revocă telefonul selectat")
        self.btn_rev_phone.setObjectName("pair_revoke_phone")
        for b in (self.btn_refresh, self.btn_rights, self.btn_rev_phone):
            r2.addWidget(b)
        r2.addStretch(1)
        lay.addLayout(r2)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self.btn_issue.clicked.connect(self._issue)
        self.btn_renew.clicked.connect(self._renew)
        self.btn_copy.clicked.connect(self._copy)
        self.btn_revoke.clicked.connect(self._revoke_code)
        self.btn_refresh.clicked.connect(self.reload)
        self.btn_rights.clicked.connect(self._rights)
        self.btn_rev_phone.clicked.connect(self._revoke_phone)
        self._sync_buttons()

    # ------------------------------------------------------------------ stare
    def _say(self, text: str, error: bool = False) -> None:
        self.lbl_status.setStyleSheet("color: #b91c1c;" if error else "color: #166534;")
        self.lbl_status.setText(text)

    def _busy(self, on: bool, text: str = "") -> None:
        for b in (self.btn_issue, self.btn_renew, self.btn_revoke, self.btn_rights, self.btn_rev_phone, self.btn_refresh):
            b.setEnabled(not on)
        if on:
            self.lbl_status.setStyleSheet("color: #1d4ed8;")
            self.lbl_status.setText(text)
        else:
            self._sync_buttons()

    def _sync_buttons(self) -> None:
        has = self._issued is not None
        self.btn_copy.setEnabled(has)
        self.btn_revoke.setEnabled(has)
        self.btn_renew.setEnabled(has)

    def reload(self) -> None:
        self._busy(True, "Se verifică backendul și se încarcă telefoanele…")

        def work():
            info = mpair.check_instance()
            return info, mpair.phones()

        def ok(res):
            info, phones = res
            self.lbl_server.setStyleSheet("color: #166534;")
            self.lbl_server.setText(info["message"])
            self._fill(phones)
            self._busy(False)
            self._say(f"{len(phones)} telefon(e) asociat(e).")

        def fail(msg):
            self.lbl_server.setStyleSheet("color: #b91c1c;")
            self.lbl_server.setText("Backend: " + msg)
            self._busy(False)
            self._say(msg, True)
        _bg(self, work, ok, fail)

    def _fill(self, phones: List[Dict[str, Any]]) -> None:
        self._phones = phones
        self.table.setRowCount(len(phones))
        for r, p in enumerate(phones):
            vals = (p["name"], (p.get("created_at") or "—")[:16].replace("T", " "), (p.get("last_seen_at") or "nicio încă")[:16].replace("T", " "), p["rights"], "Activ")
            for c, v in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(str(v)))

    def _selected(self) -> Optional[Dict[str, Any]]:
        r = self.table.currentRow()
        return self._phones[r] if 0 <= r < len(self._phones) else None

    # ------------------------------------------------------------------ cod
    def _issue(self) -> None:
        self._busy(True, "Se generează codul…")

        def ok(res):
            self._issued = res
            self.lbl_code.setText(res["code"])
            self._show_qr(res)
            self._timer.start()
            self._busy(False)
            self._tick()
            self._say("Cod generat. Introdu-l pe telefon înainte să expire; se poate folosi o singură dată.")

        def fail(msg):
            self._busy(False)
            self._say("Codul nu a putut fi generat: " + msg, True)
        _bg(self, mpair.issue_code, ok, fail)

    def _renew(self) -> None:
        old = self._issued
        self._clear_code()
        if old:
            try:
                mpair.revoke_code(old["code_id"])
            except Exception:  # noqa: BLE001  (codul vechi poate fi deja folosit/expirat; se emite oricum unul nou)
                pass
        self._issue()

    def _show_qr(self, res: Dict[str, Any]) -> None:
        self.lbl_qr.setVisible(False)
        base = mpair.current_url()
        try:
            link = qrlink.pair_url(base, res["code"].replace("-", ""))
            pm = QPixmap()
            if link and pm.loadFromData(qrlink.png_bytes(link)):
                self.lbl_qr.setPixmap(pm)
                self.lbl_qr.setVisible(True)
        except Exception:  # noqa: BLE001  (fără QR: codul manual rămâne)
            pass

    def _clear_code(self) -> None:
        self._issued = None
        self._timer.stop()
        self.lbl_code.setText("—")
        self.lbl_qr.setVisible(False)
        self._sync_buttons()

    def _tick(self) -> None:
        if not self._issued:
            return
        left = int(self._issued["deadline"] - time.time())
        if left <= 0:
            self._clear_code()
            self.lbl_left.setText("Codul a expirat. Generează unul nou.")
            return
        self.lbl_left.setText(f"Valabil încă {left // 60}:{left % 60:02d}")

    def _copy(self) -> None:
        if self._issued:
            QGuiApplication.clipboard().setText(self._issued["code"])
            self._say("Cod copiat în clipboard.")

    def _revoke_code(self) -> None:
        if not self._issued:
            return
        cid = self._issued["code_id"]
        self._busy(True, "Se revocă codul…")

        def ok(_r):
            self._clear_code()
            self.lbl_left.setText("Cod revocat.")
            self._busy(False)
            self._say("Codul a fost revocat; nu mai poate fi folosit.")

        def fail(msg):
            self._busy(False)
            self._say("Revocarea a eșuat: " + msg, True)
        _bg(self, lambda: mpair.revoke_code(cid), ok, fail)

    # ------------------------------------------------------------------ telefoane
    def _rights(self) -> None:
        p = self._selected()
        if not p:
            self._say("Selectează mai întâi un telefon din listă.", True)
            return
        self.permissions_requested.emit(p["ref"])

    def _revoke_phone(self) -> None:
        p = self._selected()
        if not p:
            self._say("Selectează mai întâi un telefon din listă.", True)
            return
        if QMessageBox.question(self, "Revocă telefonul", f"„{p['name']}” nu va mai putea citi sau trimite date. Revocarea este definitivă. Continui?") != QMessageBox.Yes:
            return
        self._busy(True, "Se revocă telefonul…")

        def ok(_r):
            self._busy(False)
            self.reload()

        def fail(msg):
            self._busy(False)
            self._say("Revocarea telefonului a eșuat: " + msg, True)
        _bg(self, lambda: mpair.revoke_phone(p["ref"]), ok, fail)

    def done_cleanup(self) -> None:
        self._clear_code()
