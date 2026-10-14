"""field_inbox_window.py — „Elemente din teren" (Field Inbox) în MAIN: listă, filtre, previzualizare, Acceptă / Respinge.

Doar HTTPS spre API (field_inbox_client); cererile rulează în thread (field_worker), UI-ul nu se blochează. Cloud nedisponibil = mesaj, fără crash.
ACCEPTED = aprobat în cloud; „Importă în MAIN” (field_import.py) îl scrie în registrul SQLite activ, apoi îl marchează IMPORTED. Doar la acțiunea operatorului.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

import access_session
import field_import
import field_inbox_client as fic
from field_worker import run_task

log = logging.getLogger("cimitir.field_inbox_window")
COLUMNS = ("Tip", "Cimitir", "Loc / grave_uid", "Data", "Dispozitiv / telefon", "Status")
KEYS = ("type", "cemetery", "place", "date", "device", "status")


def local_cemetery_name() -> str:
    try:
        import database as db
        return (db.get_setting("cimitir_nume") or "").strip()
    except Exception:
        return ""


class FieldInboxWindow(QDialog):
    changed = Signal()

    def __init__(self, parent=None, client_factory: Callable[[], Any] = None, can_review: Optional[Callable[[], bool]] = None):
        super().__init__(parent)
        self.setWindowTitle("Elemente din teren (Field Inbox)")
        self.setMinimumSize(1100, 640)
        self._factory = client_factory or (lambda: fic.connect()[0])
        self._can_review = can_review or (lambda: access_session.require("approve", self, "aprobarea elementelor din teren"))
        self._items: List[Dict[str, Any]] = []
        self._devices: Dict[str, str] = {}
        self._detail: Optional[Dict[str, Any]] = None
        self._seq = 0

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        self.cmb_status = QComboBox()
        self.cmb_status.setObjectName("field_status_filter")
        for s in fic.STATUSES:
            self.cmb_status.addItem(fic.STATUS_LABELS[s], s)
        self.cmb_status.setCurrentIndex(0)
        self.btn_refresh = QPushButton("Reîmprospătează")
        self.btn_config = QPushButton("Configurează Cloud/Server")
        self.btn_config.setVisible(False)
        self.lbl_state = QLabel("")
        self.lbl_state.setObjectName("field_state")
        top.addWidget(QLabel("Status:"))
        top.addWidget(self.cmb_status)
        top.addWidget(self.btn_refresh)
        top.addWidget(self.btn_config)
        top.addWidget(self.lbl_state, 1)
        root.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setObjectName("field_table")
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        split.addWidget(self.table)

        right = QWidget()
        rl = QVBoxLayout(right)
        self.txt_detail = QPlainTextEdit()
        self.txt_detail.setObjectName("field_detail")
        self.txt_detail.setReadOnly(True)
        self.lst_files = QListWidget()
        self.lst_files.setObjectName("field_files")
        self.lst_files.setMaximumHeight(110)
        self.lbl_stage = QLabel("")
        self.lbl_stage.setStyleSheet("font-weight: bold;")
        self.lbl_preview = QLabel("Selectează un element.")
        self.lbl_preview.setObjectName("field_preview")
        self.lbl_preview.setAlignment(Qt.AlignCenter)
        self.lbl_preview.setMinimumHeight(220)
        self.lbl_preview.setWordWrap(True)
        rl.addWidget(self.txt_detail, 2)
        rl.addWidget(self.lbl_stage)
        rl.addWidget(self.lst_files)
        rl.addWidget(self.lbl_preview, 3)
        btns = QHBoxLayout()
        self.btn_accept = QPushButton("Acceptă")
        self.btn_accept.setObjectName("field_accept")
        self.btn_reject = QPushButton("Respinge")
        self.btn_reject.setObjectName("field_reject")
        self.btn_accept_import = QPushButton("Acceptă și importă")
        self.btn_accept_import.setObjectName("field_accept_import")
        self.btn_import = QPushButton("Importă în MAIN")
        self.btn_import.setObjectName("field_import")
        btns.addStretch(1)
        btns.addWidget(self.btn_import)
        btns.addWidget(self.btn_accept_import)
        btns.addWidget(self.btn_accept)
        btns.addWidget(self.btn_reject)
        rl.addLayout(btns)
        split.addWidget(right)
        split.setSizes([650, 450])
        root.addWidget(split, 1)
        note = QLabel("„Acceptă” = aprobat în cloud (nu modifică registrul local). „Importă în MAIN” scrie în registrul local activ, cu confirmare, apoi marchează IMPORTED.")
        note.setStyleSheet("color: #94a3b8; font-size: 11px;")
        root.addWidget(note)
        self._set_review_enabled(False)

        self.cmb_status.currentIndexChanged.connect(self.reload)
        self.btn_refresh.clicked.connect(self.reload)
        self.btn_config.clicked.connect(self._configure)
        self.table.itemSelectionChanged.connect(self._on_select)
        self.lst_files.currentRowChanged.connect(self._on_file)
        self.btn_accept.clicked.connect(self._accept)
        self.btn_reject.clicked.connect(self._reject)
        self.btn_accept_import.clicked.connect(lambda: self._import(True))
        self.btn_import.clicked.connect(lambda: self._import(False))
        self.reload()

    # ------------------------------------------------------------------ stare
    def current_status(self) -> str:
        return self.cmb_status.currentData()

    def _fail(self, msg: str, not_configured: bool) -> None:
        self.lbl_state.setText(msg)
        self.btn_config.setVisible(not_configured)

    def _set_review_enabled(self, on: bool, status: str = "") -> None:
        self.btn_accept.setEnabled(on)
        self.btn_reject.setEnabled(on)
        self.btn_accept_import.setEnabled(on)
        self.btn_import.setEnabled(status == "ACCEPTED")

    def _configure(self) -> None:
        from cloud_profiles_dialog import CloudProfilesDialog
        CloudProfilesDialog(self).exec()
        self.reload()

    # ------------------------------------------------------------------ listă
    def reload(self, *_args) -> None:
        self._seq += 1
        seq, status = self._seq, self.current_status()
        self.lbl_state.setText("Se încarcă…")

        def work():
            client = self._factory()
            items = client.list_items(status)
            try:
                devices = fic.phone_names(client.list_phones())
            except fic.FieldCloudError:
                devices = {}
            try:
                field_import.finalize_pending(client)       # doar statusul cloud al importurilor deja comise local; nu importă nimic nou
            except Exception as exc:  # noqa: BLE001
                log.debug("finalizare cloud amânată (%s)", type(exc).__name__)
            return items, devices

        def ok(res):
            if seq != self._seq:
                return
            self._items, self._devices = res
            self.btn_config.setVisible(False)
            self._fill()
            self.lbl_state.setText(f"{len(self._items)} elemente" if self._items else "Niciun element.")

        run_task(self, work, ok, lambda m, nc: seq == self._seq and self._fail(m, nc))

    def _fill(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        cem = local_cemetery_name()
        for it in self._items:
            row = fic.item_row(it, self._devices, cem)
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, k in enumerate(KEYS):
                cell = QTableWidgetItem(row[k])
                if c == 0:
                    cell.setData(Qt.UserRole, it["uid"])
                self.table.setItem(r, c, cell)
        self.table.blockSignals(False)
        self._detail = None
        self.txt_detail.setPlainText("")
        self.lst_files.clear()
        self.lbl_stage.setText("")
        self.lbl_preview.setText("Selectează un element.")
        self._set_review_enabled(False)

    def selected_uid(self) -> Optional[str]:
        r = self.table.currentRow()
        it = self.table.item(r, 0) if r >= 0 else None
        return it.data(Qt.UserRole) if it else None

    # ------------------------------------------------------------------ detalii + previzualizare
    def _on_select(self) -> None:
        uid = self.selected_uid()
        if not uid:
            return
        self._set_review_enabled(False)
        self.lbl_preview.setText("Se încarcă…")
        run_task(self, lambda: self._factory().get_item(uid), lambda it: self._show_detail(uid, it),
                 lambda m, nc: self._fail(m, nc))

    def _show_detail(self, uid: str, item: Dict[str, Any]) -> None:
        if uid != self.selected_uid():
            return
        self._detail = item
        pv = fic.item_preview(item, self._devices)
        self.txt_detail.setPlainText(pv["title"] + "\n\n" + "\n".join(f"{k}: {v}" for k, v in pv["lines"]))
        self.lbl_stage.setText(f"Etapa: {pv['stage']}" if pv["stage"] else "")
        self.lst_files.blockSignals(True)
        self.lst_files.clear()
        for f in pv["files"]:
            self.lst_files.addItem(QListWidgetItem(f"{f['filename']} ({f['mime']})"))
        self.lst_files.blockSignals(False)
        self._set_review_enabled(pv["can_review"], item["status"])
        if pv["files"]:
            self.lst_files.setCurrentRow(0)
        else:
            self.lbl_preview.setPixmap(QPixmap())
            self.lbl_preview.setText("Fără fișiere atașate.")

    def _on_file(self, row: int) -> None:
        if self._detail is None or row < 0 or row >= len(self._detail.get("files") or []):
            return
        item_uid, f = self._detail["uid"], self._detail["files"][row]
        if f.get("status") != "READY" or self._detail["status"] not in ("PENDING", "ACCEPTED"):
            self.lbl_preview.setText("Fișier indisponibil pentru previzualizare în acest status.")
            return
        self.lbl_preview.setText("Se descarcă previzualizarea…")
        run_task(self, lambda: self._factory().fetch_attachment(item_uid, f["uid"]), lambda res: self._show_file(item_uid, res),
                 lambda m, nc: self.lbl_preview.setText(m if nc else fic.UNAVAILABLE))

    def _show_file(self, item_uid: str, res) -> None:
        if self._detail is None or self._detail["uid"] != item_uid:
            return
        data, mime = res
        if mime == "application/pdf":
            self._show_pdf(data)
            return
        img = QImage.fromData(data)
        if img.isNull():
            self.lbl_preview.setText("Imaginea nu poate fi afișată.")
            return
        pix = QPixmap.fromImage(img)
        self.lbl_preview.setPixmap(pix.scaled(self.lbl_preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _show_pdf(self, data: bytes) -> None:
        try:
            from PySide6.QtCore import QBuffer, QByteArray, QSize
            from PySide6.QtPdf import QPdfDocument
        except ImportError:
            self.lbl_preview.setText(f"PDF verificat ({len(data) // 1024} KB). Previzualizarea PDF nu este disponibilă în această instalare.")
            return
        ba = QByteArray(data)
        buf = QBuffer(ba, self)
        buf.open(QBuffer.ReadOnly)
        doc = QPdfDocument(self)
        doc.load(buf)
        if doc.pageCount() < 1:
            self.lbl_preview.setText("PDF nevalid.")
            return
        size = doc.pagePointSize(0).toSize().scaled(QSize(520, 520), Qt.KeepAspectRatio)
        self.lbl_preview.setPixmap(QPixmap.fromImage(doc.render(0, size)))
        self._pdf_keepalive = (doc, buf, ba)

    # ------------------------------------------------------------------ Acceptă / Respinge
    def _review(self, call: Callable[[Any, Dict[str, Any]], Any]) -> None:
        item = self._detail
        if item is None or item["status"] != "PENDING" or not self._can_review():
            return
        self._set_review_enabled(False)

        def ok(_res):
            self.changed.emit()
            self.reload()

        def fail(msg, nc):
            self._fail(msg, nc)
            QMessageBox.warning(self, "Field Inbox", msg)
            self.reload()

        run_task(self, lambda: call(self._factory(), item), ok, fail)

    def _import(self, accept_first: bool) -> None:
        item = self._detail
        want = "PENDING" if accept_first else "ACCEPTED"
        if item is None or item["status"] != want or not self._can_review():
            return
        pv = field_import.preview(item, local_cemetery_name())
        if not pv["can_import"]:
            QMessageBox.warning(self, "Importă în MAIN", field_import.preview_text(pv))
            return
        title = "Acceptă și importă" if accept_first else "Importă în MAIN"
        if QMessageBox.question(self, title, field_import.preview_text(pv) + "\n\nConfirmi importul în registrul local activ?") != QMessageBox.Yes:
            return
        self._set_review_enabled(False)
        uid = item["uid"]

        def ok(res):
            self.lbl_state.setText(res.message)
            if not res.cloud_final:
                QMessageBox.information(self, "Field Inbox", res.message)
            self.changed.emit()
            if res.cloud_final:
                self.cmb_status.setCurrentIndex(fic.STATUSES.index("IMPORTED"))
            self.reload()

        def fail(msg, nc):
            self._fail(msg, nc)
            QMessageBox.warning(self, "Field Inbox", msg)
            self.reload()

        run_task(self, lambda: (field_import.accept_and_import if accept_first else field_import.import_item)(self._factory(), uid), ok, fail)

    def _accept(self) -> None:
        self._review(lambda c, it: c.accept(it["uid"], it["status"]))

    def _reject(self) -> None:
        if self._detail is None or self._detail["status"] != "PENDING":
            return
        reason, ok = QInputDialog.getText(self, "Respinge element", "Motiv (opțional):")
        if not ok:
            return
        self._review(lambda c, it: c.reject(it["uid"], reason, it["status"]))
