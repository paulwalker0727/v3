"""field_import_history_window.py — fereastra MAIN „Istoric importuri din teren” (jurnalul local field_import_journal).

Lista și detaliile sunt citite local (merg fără internet). Starea cloud și acțiunile cloud rulează în thread (field_worker.run_task); UI-ul nu se blochează.
Acțiuni: „Finalizează în cloud” (LOCAL_COMMITTED), „Reia importul” (PREPARING, doar după verificări), „Deschide în MAIN” (mecanismul existent features.search_plot).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import access_session
import field_import as fim
import field_import_history as fih
import field_inbox_client as fic
from field_inbox_window import local_cemetery_name
from field_worker import run_task
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger("cimitir.field_import_history_window")
COLUMNS = ("Data / ora", "inbox_item_uid", "Tip", "Cimitir / registru", "grave_uid", "local_entity_uid", "Stare import", "Fișiere", "Ultima eroare", "Ultima încercare")
KEYS = ("created_at", "inbox_item_uid", "type", "registry", "grave_uid", "local_entity_uid", "state", "files", "last_error", "last_attempt")


class FieldImportHistoryWindow(QDialog):
    changed = Signal()

    def __init__(self, parent=None, client_factory: Callable[[], Any] | None = None, can_review: Callable[[], bool] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Istoric importuri din teren")
        self.setMinimumSize(1200, 640)
        self._factory = client_factory or (lambda: fic.connect()[0])
        self._can_review = can_review or (lambda: access_session.require("approve", self, "acțiuni asupra importurilor din teren"))
        self._rows: list[dict[str, Any]] = []
        self._detail: dict[str, Any] | None = None
        self._cloud: dict[str, Any] = {}
        self._verdict: fih.ResumeVerdict | None = None
        self._seq = 0
        self._busy = False

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        self.cmb_filter = QComboBox()
        self.cmb_filter.setObjectName("history_filter")
        for f in fih.FILTERS:
            self.cmb_filter.addItem(fih.FILTER_LABELS[f], f)
        self.btn_refresh = QPushButton("Reîmprospătează")
        self.btn_refresh.setObjectName("history_refresh")
        self.lbl_state = QLabel("")
        self.lbl_state.setObjectName("history_state")
        top.addWidget(QLabel("Filtru:"))
        top.addWidget(self.cmb_filter)
        top.addWidget(self.btn_refresh)
        top.addWidget(self.lbl_state, 1)
        root.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setObjectName("history_table")
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        split.addWidget(self.table)
        right = QWidget()
        rl = QVBoxLayout(right)
        self.txt_detail = QPlainTextEdit()
        self.txt_detail.setObjectName("history_detail")
        self.txt_detail.setReadOnly(True)
        rl.addWidget(self.txt_detail, 1)
        btns = QHBoxLayout()
        self.btn_finalize = QPushButton("Finalizează în cloud")
        self.btn_finalize.setObjectName("history_finalize")
        self.btn_resume = QPushButton("Reia importul")
        self.btn_resume.setObjectName("history_resume")
        self.btn_open = QPushButton("Deschide în MAIN")
        self.btn_open.setObjectName("history_open")
        btns.addStretch(1)
        for b in (self.btn_open, self.btn_resume, self.btn_finalize):
            btns.addWidget(b)
        rl.addLayout(btns)
        split.addWidget(right)
        split.setSizes([760, 440])
        root.addWidget(split, 1)
        note = QLabel("Jurnal local. „Finalizează în cloud” face doar ACCEPTED → IMPORTED (nu reimportă și nu copiază media). Starea cloud poate fi „indisponibil” fără internet.")
        note.setStyleSheet("color: #94a3b8; font-size: 11px;")
        root.addWidget(note)
        self._update_buttons()

        self.cmb_filter.currentIndexChanged.connect(self.reload)
        self.btn_refresh.clicked.connect(self.reload)
        self.table.itemSelectionChanged.connect(self._on_select)
        self.btn_finalize.clicked.connect(self._finalize)
        self.btn_resume.clicked.connect(self._resume)
        self.btn_open.clicked.connect(self._open_in_main)
        self.reload()

    # ------------------------------------------------------------------ listă (locală)
    def current_filter(self) -> str:
        return self.cmb_filter.currentData()

    def selected_uid(self) -> str | None:
        r = self.table.currentRow()
        it = self.table.item(r, 0) if r >= 0 else None
        return it.data(Qt.UserRole) if it else None

    def reload(self, *_args, keep: str | None = None) -> None:
        if not isinstance(keep, str):
            keep = self.selected_uid()
        try:
            self._rows = fih.list_history(self.current_filter(), local_cemetery_name())
        except Exception as exc:  # noqa: BLE001
            log.warning("istoric indisponibil (%s)", type(exc).__name__)
            self._rows = []
            self.lbl_state.setText("Jurnalul local nu poate fi citit.")
        else:
            self.lbl_state.setText(f"{len(self._rows)} importuri" if self._rows else "Niciun import.")
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for row in self._rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, k in enumerate(KEYS):
                cell = QTableWidgetItem(str(row[k]))
                if c == 0:
                    cell.setData(Qt.UserRole, row["inbox_item_uid"])
                self.table.setItem(r, c, cell)
        self.table.blockSignals(False)
        self._detail, self._cloud, self._verdict = None, {}, None
        self.txt_detail.setPlainText("")
        self._update_buttons()
        for r, row in enumerate(self._rows):
            if keep and row["inbox_item_uid"] == keep:
                self.table.selectRow(r)
                return

    # ------------------------------------------------------------------ detalii (local imediat, cloud în thread)
    def _on_select(self) -> None:
        uid = self.selected_uid()
        if not uid:
            return
        self._detail = fih.local_details(uid, local_cemetery_name())
        self._cloud, self._verdict = {}, None
        self._render()
        if self._detail is None:
            return
        self._seq += 1
        seq = self._seq

        def ok(snap):
            if seq != self._seq or uid != self.selected_uid():
                return
            self._cloud, self._verdict = snap, snap.get("verdict")
            self._render()

        run_task(self, lambda: fih.cloud_snapshot(self._factory, uid), ok, lambda m, nc: None)

    def _render(self) -> None:
        d = self._detail
        if d is None:
            self.txt_detail.setPlainText("Elementul nu mai există în jurnal.")
            self._update_buttons()
            return
        lines = [f"{d['type']} — {d['state']}", "", f"inbox_item_uid: {d['inbox_item_uid']}", f"Cimitir / registru: {d['registry']}", f"Destinație locală: {d['destination'] or '—'}",
                 f"Entitate locală (local_entity_uid): {d['local_entity_uid'] or '—'}", f"Loc de veci: {d['cod_loc'] or '—'} ({d['grave_uid'] or '—'})",
                 f"Stare locală: {d['local_state']}", f"Data / ora: {d['created_at']}", f"Ultima încercare: {d['last_attempt']}", f"Ultima eroare: {d['last_error'] or '—'}", "",
                 "Payload relevant (local):"]
        lines += [f"  {k}: {v}" for k, v in d["payload"]] or ["  —"]
        lines += ["", "Fișiere / media locale:"]
        lines += [f"  #{f['document_id']} {f['path']} | SHA256: {f['sha256'] or '—'} | {'prezent' if f['exists'] else 'LIPSĂ'}" for f in d["files_detail"]] or ["  —"]
        c = self._cloud
        lines += ["", f"Stare cloud: {c['status'] if c else 'se verifică…'}"]
        if c.get("item") and c["item"]["payload"]:
            lines += ["Payload (cloud):"] + [f"  {k}: {v}" for k, v in c["item"]["payload"]]
        if self._verdict is not None:
            lines += ["", "Reluare import: " + ("posibilă (verificări OK)" if self._verdict.can_resume else "NU este sigură")]
            lines += [f"  - {r}" for r in self._verdict.reasons]
        self.txt_detail.setPlainText("\n".join(lines))
        self._update_buttons()

    def _update_buttons(self) -> None:
        d = self._detail
        state = d["state"] if d else ""
        idle = not self._busy
        self.btn_finalize.setEnabled(idle and state == fim.LOCAL_COMMITTED)
        self.btn_resume.setEnabled(idle and state == fim.PREPARING and bool(self._verdict and self._verdict.can_resume))
        self.btn_open.setEnabled(idle and bool(d and d.get("navigation")))

    # ------------------------------------------------------------------ acțiuni
    def _run(self, call: Callable[[Any, str], Any], uid: str) -> None:
        self._busy = True
        self._update_buttons()

        def done():
            self._busy = False

        def ok(res):
            done()
            self.lbl_state.setText(res.message)
            if res.state != fim.COMPLETE:
                QMessageBox.information(self, "Istoric importuri", res.message)
            self.changed.emit()
            self.reload(keep=uid)

        def fail(msg, _nc):
            done()
            self.lbl_state.setText(msg)
            QMessageBox.warning(self, "Istoric importuri", msg)
            self.reload(keep=uid)

        run_task(self, lambda: call(self._factory(), uid), ok, fail)

    def _finalize(self) -> None:
        uid = self.selected_uid()
        if self._busy or not uid or not self._detail or self._detail["state"] != fim.LOCAL_COMMITTED or not self._can_review():
            return
        self._run(fih.finalize_cloud, uid)

    def _resume(self) -> None:
        uid = self.selected_uid()
        if self._busy or not uid or not self._detail or self._detail["state"] != fim.PREPARING or not (self._verdict and self._verdict.can_resume) or not self._can_review():
            return
        if QMessageBox.question(self, "Reia importul", f"Reiau importul elementului {uid} în registrul local activ?\nVerificările de siguranță au trecut.") != QMessageBox.Yes:
            return
        self._run(fih.resume_import, uid)

    def _open_in_main(self) -> None:
        uid = self.selected_uid()
        nav = fih.navigation_target(uid) if uid else None
        parent = self.parent()
        features = getattr(parent, "features", None)
        if not nav or features is None or not hasattr(features, "search_plot"):
            QMessageBox.information(self, "Deschide în MAIN", "Harta este disponibilă doar din fereastra principală, iar entitatea locală trebuie să existe.")
            return
        try:
            features.search_plot(nav["cod_loc"])
            if hasattr(parent, "raise_"):
                parent.raise_()
                parent.activateWindow()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Deschide în MAIN", f"Nu am putut afișa locul pe hartă: {type(exc).__name__}")
            return
        self.accept()
