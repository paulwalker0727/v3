"""
cloud_conflicts_window.py — Etapa 6: conflicte de sincronizare (același obiect modificat pe două calculatoare).

Serverul nu suprascrie nimic în tăcere: modificarea ta rămâne în coadă ca „conflict" până alegi explicit
versiunea serverului sau pe a ta.
"""
from __future__ import annotations

from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QAbstractItemView)

import cimitir_entities as ents
import entity_sync

REASONS = {
    "version_mismatch": "Modificat în altă parte (versiune mai nouă pe server)",
    "already_exists": "Există deja pe server, cu alt conținut",
    "deleted_on_server": "Șters pe server",
    "natural_key": "Același cod există deja pe server (alt obiect)",
}


class CloudConflictsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Conflicte de sincronizare")
        self.setMinimumSize(760, 380)
        root = QVBoxLayout(self)
        info = QLabel("Același obiect a fost modificat pe alt calculator (sau din panoul web) înainte ca modificarea ta să "
                      "ajungă pe server. Nimic nu a fost suprascris. Alege ce versiune rămâne.")
        info.setWordWrap(True)
        root.addWidget(info)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Obiect", "Motiv", "Câmpuri diferite", "Versiune server", "Din"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.btn_server = QPushButton("Păstrează versiunea serverului")
        self.btn_mine = QPushButton("Păstrează versiunea mea")
        self.btn_close = QPushButton("Închide")
        for b in (self.btn_server, self.btn_mine):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.btn_close)
        root.addLayout(row)
        self.btn_server.clicked.connect(lambda: self._resolve("server"))
        self.btn_mine.clicked.connect(lambda: self._resolve("mine"))
        self.btn_close.clicked.connect(self.accept)
        self._items = []
        self.reload()

    def reload(self):
        self._items = entity_sync.list_conflicts()
        self.table.setRowCount(len(self._items))
        for i, c in enumerate(self._items):
            fields = ""
            if c["local"] is not None and c["server"] is not None:
                fields = ", ".join(ents.changed_fields(c["entity"], c["local"], c["server"])) or "(identice)"
            label = (c["local"] or c["server"] or {})
            name = label.get("cod_loc") or label.get("numar_contract") or " ".join(
                str(label.get(k) or "") for k in ("nume", "prenume")).strip() or c["uid"][:8]
            vals = [f"{c['entity']}: {name}", REASONS.get(c["reason"], c["reason"] or ""), fields,
                    str(c["server_version"] if c["server_version"] is not None else "—"), str(c["created_at"] or "")]
            for j, v in enumerate(vals):
                self.table.setItem(i, j, QTableWidgetItem(v))
        self.btn_server.setEnabled(bool(self._items))
        self.btn_mine.setEnabled(bool(self._items))

    def _resolve(self, keep: str):
        r = self.table.currentRow()
        if r < 0 or r >= len(self._items):
            QMessageBox.information(self, "Conflicte", "Selectează un conflict.")
            return
        text = ("Versiunea serverului înlocuiește modificarea ta locală (se pierde)." if keep == "server"
                else "Modificarea ta se trimite peste versiunea curentă a serverului (cea de pe server se înlocuiește).")
        if QMessageBox.question(self, "Confirmare", text + "\n\nContinui?", QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) != QMessageBox.Yes:
            return
        entity_sync.resolve_conflict(self._items[r]["op_id"], keep)
        self.reload()
