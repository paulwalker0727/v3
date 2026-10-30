"""field_devices_window.py — „Telefoane / Dispozitive teren" în MAIN: cod de pairing MOBILE, telefoane asociate, revocare (Phone Pairing v1 peste API HTTPS).

Codul brut există doar în memoria ferestrei (afișat o singură dată, șters la expirare/închidere); nu se loghează și nu se salvează.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

import access_session
import field_inbox_client as fic
from field_inbox_window import local_cemetery_name
from field_worker import run_task

COLUMNS = ("Telefon", "Cimitir asociat", "Asociat la", "Ultima activitate", "Stare")
KEYS = ("name", "cemetery", "paired", "last_seen", "state")


class FieldDevicesDialog(QDialog):
    def __init__(self, parent=None, client_factory: Callable[[], Any] = None, can_manage: Optional[Callable[[], bool]] = None):
        super().__init__(parent)
        self.setWindowTitle("Telefoane de teren — Field Inbox v1 (vechi)")
        self.setMinimumSize(820, 520)
        self._factory = client_factory or (lambda: fic.connect()[0])
        self._can_manage = can_manage or (lambda: access_session.require("manage_config", self, "administrarea telefoanelor de teren"))
        self._deadline: Optional[float] = None
        self._cem_uid = ""
        self._phones: List[Dict[str, Any]] = []

        root = QVBoxLayout(self)
        self.lbl_state = QLabel("")
        self.lbl_state.setObjectName("devices_state")
        self.btn_config = QPushButton("Configurează Cloud/Server")
        self.btn_config.setVisible(False)
        root.addWidget(self.lbl_state)
        root.addWidget(self.btn_config)

        row = QHBoxLayout()
        self.btn_code = QPushButton("Generează cod pentru Field Inbox (vechi)")
        self.btn_code.setObjectName("devices_gen_code")
        self.lbl_code = QLabel("—")
        self.lbl_code.setObjectName("devices_code")
        self.lbl_code.setStyleSheet("font-size: 22px; font-weight: bold; letter-spacing: 2px;")
        self.lbl_code.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_expiry = QLabel("")
        self.lbl_expiry.setObjectName("devices_expiry")
        row.addWidget(self.btn_code)
        row.addWidget(self.lbl_code)
        row.addWidget(self.lbl_expiry)
        row.addStretch(1)
        root.addLayout(row)
        hint = QLabel("Acest cod este numai pentru aplicația de teren Field Inbox v1, NU pentru Mobile v2. "
                      "Pentru Mobile v2 folosește Centrul Cloud → F. Mobile → «Asociază telefoane (cod / QR)». "
                      "Codul se folosește o singură dată și expiră.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #94a3b8; font-size: 11px;")
        root.addWidget(hint)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setObjectName("devices_table")
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        root.addWidget(self.table, 1)
        btns = QHBoxLayout()
        self.btn_refresh = QPushButton("Reîmprospătează")
        self.btn_revoke = QPushButton("Revocă telefonul")
        self.btn_revoke.setObjectName("devices_revoke")
        self.btn_revoke.setEnabled(False)
        btns.addWidget(self.btn_refresh)
        btns.addStretch(1)
        btns.addWidget(self.btn_revoke)
        root.addLayout(btns)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self.btn_code.clicked.connect(self._generate)
        self.btn_refresh.clicked.connect(self.reload)
        self.btn_revoke.clicked.connect(self._revoke)
        self.btn_config.clicked.connect(self._configure)
        self.table.itemSelectionChanged.connect(self._on_select)
        self.reload()

    def _fail(self, msg: str, not_configured: bool) -> None:
        self.lbl_state.setText(msg)
        self.btn_config.setVisible(not_configured)

    def _configure(self) -> None:
        from cloud_simple_dialog import open_cloud_config
        open_cloud_config(self)
        self.reload()

    # ------------------------------------------------------------------ telefoane
    def reload(self) -> None:
        self.lbl_state.setText("Se încarcă…")

        def ok(phones):
            self._phones = phones
            self.btn_config.setVisible(False)
            self.lbl_state.setText(f"{len(phones)} telefoane" if phones else "Niciun telefon asociat.")
            cem = local_cemetery_name() or self._cem_uid[:8]
            self.table.setRowCount(0)
            for p in phones:
                r = fic.phone_row(p, cem)
                i = self.table.rowCount()
                self.table.insertRow(i)
                for c, k in enumerate(KEYS):
                    cell = QTableWidgetItem(r[k])
                    if c == 0:
                        cell.setData(Qt.UserRole, (p["binding_uid"], r["revocable"]))
                    self.table.setItem(i, c, cell)
            self._on_select()

        run_task(self, lambda: self._factory().list_phones(), ok, self._fail)

    def _selected(self):
        r = self.table.currentRow()
        it = self.table.item(r, 0) if r >= 0 else None
        return it.data(Qt.UserRole) if it else None

    def _on_select(self) -> None:
        sel = self._selected()
        self.btn_revoke.setEnabled(bool(sel and sel[1]))

    def _revoke(self) -> None:
        sel = self._selected()
        if not sel or not sel[1] or not self._can_manage():
            return
        if QMessageBox.question(self, "Revocă telefonul", "Telefonul nu va mai putea trimite elemente din teren. Revocarea este definitivă. Continui?") != QMessageBox.Yes:
            return
        self.btn_revoke.setEnabled(False)
        run_task(self, lambda: self._factory().revoke_phone(sel[0]), lambda _r: self.reload(),
                 lambda m, nc: (self._fail(m, nc), QMessageBox.warning(self, "Telefoane teren", m)))

    # ------------------------------------------------------------------ cod pairing
    def _generate(self) -> None:
        if not self._can_manage():
            return
        self.btn_code.setEnabled(False)

        def ok(res):
            self.btn_code.setEnabled(True)
            self._cem_uid = res.get("cemetery_uid") or ""
            self._deadline = time.time() + int(res.get("ttl") or 0)
            self.lbl_code.setText(str(res.get("code") or "—"))
            self._timer.start()
            self._tick()
            self.reload()

        def fail(m, nc):
            self.btn_code.setEnabled(True)
            self._fail(m, nc)

        run_task(self, lambda: self._factory().issue_pairing_code(), ok, fail)

    def _tick(self) -> None:
        if self._deadline is None:
            return
        self.lbl_expiry.setText(fic.format_expiry(self._deadline))
        if time.time() >= self._deadline:
            self._timer.stop()
            self._deadline = None
            self.lbl_code.setText("—")

    def done(self, result) -> None:
        self.lbl_code.setText("—")
        self._deadline = None
        self._timer.stop()
        super().done(result)
