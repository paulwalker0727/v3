"""Dialog scanare batch — multiple documente procesate secvențial cu progres."""
from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .ai_client import DocumentType, extract_document_sync


class BatchWorker(QObject):
    file_started = Signal(int, str)       # index, filename
    file_done = Signal(int, dict)         # index, result
    file_failed = Signal(int, str)        # index, error
    all_finished = Signal()

    def __init__(self, files: list[str], doc_type: str):
        super().__init__()
        self.files = files
        self.doc_type = doc_type
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        for idx, path in enumerate(self.files):
            if self._stop:
                break
            self.file_started.emit(idx, os.path.basename(path))
            try:
                result = extract_document_sync(path, self.doc_type)
                self.file_done.emit(idx, result)
            except Exception as e:
                self.file_failed.emit(idx, str(e))
        self.all_finished.emit()


class BatchScanDialog(QDialog):
    """Scanare batch: user alege mai multe fișiere, toate procesate cu Gemini."""

    def __init__(self, parent=None, document_type: str = DocumentType.BULETIN):
        super().__init__(parent)
        self.document_type = document_type
        self.setWindowTitle("🤖  Scanare batch — mai multe documente")
        self.setMinimumSize(1000, 620)
        self.setStyleSheet("QDialog { background:#020617; color:#e2e8f0; }")

        self._files: list[str] = []
        self._results: dict[int, dict] = {}  # index -> extracted result
        self._worker: Optional[BatchWorker] = None
        self._thread: Optional[QThread] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(10)

        # Header
        title = QLabel("Scanare batch documente")
        title.setStyleSheet("color:#f1f5f9; font-size:15pt; font-weight:700;")
        root.addWidget(title)

        # Type selector
        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("Tip document pentru toate fișierele:"))
        self.cmb_type = QComboBox()
        self.cmb_type.addItem("Buletin CI", DocumentType.BULETIN)
        self.cmb_type.addItem("Certificat deces", DocumentType.CERTIFICAT_DECES)
        if document_type == DocumentType.CERTIFICAT_DECES:
            self.cmb_type.setCurrentIndex(1)
        type_row.addWidget(self.cmb_type)
        type_row.addStretch()
        self.btn_pick = QPushButton("📁  Alege fișiere")
        self.btn_pick.clicked.connect(self._pick_files)
        type_row.addWidget(self.btn_pick)
        root.addLayout(type_row)

        # Table
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["#", "Fișier", "Status", "Nume extras", "CNP"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.setStyleSheet(
            "QTableWidget { background:#0f172a; color:#e2e8f0; border:1px solid #1e293b; }"
            "QHeaderView::section { background:#1e293b; color:#94a3b8; padding:6px; border:none; }"
        )
        root.addWidget(self.table, 1)

        # Progress
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setStyleSheet(
            "QProgressBar { border:1px solid #1e293b; background:#0f172a; text-align:center; }"
            "QProgressBar::chunk { background:#0ea5e9; }"
        )
        root.addWidget(self.progress)

        # Footer buttons
        footer = QHBoxLayout()
        self.status_lbl = QLabel("Alege fișiere pentru a începe.")
        self.status_lbl.setStyleSheet("color:#94a3b8;")
        self.btn_start = QPushButton("🚀  Începe scanarea")
        self.btn_start.setStyleSheet(
            "QPushButton { background:#22c55e; color:white; padding:8px 20px; "
            "border:none; border-radius:6px; font-weight:700; }"
            "QPushButton:hover { background:#16a34a; }"
            "QPushButton:disabled { background:#334155; color:#64748b; }"
        )
        self.btn_start.setEnabled(False)
        self.btn_start.clicked.connect(self._start_batch)

        self.btn_close = QPushButton("Închide")
        self.btn_close.clicked.connect(self.accept)

        footer.addWidget(self.status_lbl, 1)
        footer.addWidget(self.btn_start)
        footer.addWidget(self.btn_close)
        root.addLayout(footer)

    # ---------------------------------------------------------------
    def _pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Alege documente pentru scanare batch",
            "", "Imagini (*.png *.jpg *.jpeg *.webp *.pdf)"
        )
        if not files:
            return
        self._files = files
        self._results.clear()
        self.table.setRowCount(0)
        for idx, path in enumerate(files):
            self.table.insertRow(idx)
            self.table.setItem(idx, 0, QTableWidgetItem(str(idx + 1)))
            self.table.setItem(idx, 1, QTableWidgetItem(os.path.basename(path)))
            self.table.setItem(idx, 2, QTableWidgetItem("Așteaptă"))
            self.table.setItem(idx, 3, QTableWidgetItem("—"))
            self.table.setItem(idx, 4, QTableWidgetItem("—"))
        self.table.resizeColumnsToContents()
        self.status_lbl.setText(f"{len(files)} fișiere gata de scanare.")
        self.btn_start.setEnabled(True)

    def _start_batch(self):
        if not self._files:
            return
        self.btn_start.setEnabled(False)
        self.btn_pick.setEnabled(False)
        self.cmb_type.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setMaximum(len(self._files))
        self.progress.setValue(0)
        doc_type = self.cmb_type.currentData()
        self.status_lbl.setText(f"Se scanează {len(self._files)} documente cu Gemini 3 Flash…")

        self._thread = QThread(self)
        self._worker = BatchWorker(self._files, doc_type)
        self._worker.moveToThread(self._thread)
        self._worker.file_started.connect(self._on_started)
        self._worker.file_done.connect(self._on_done)
        self._worker.file_failed.connect(self._on_failed)
        self._worker.all_finished.connect(self._on_all_finished)
        self._thread.started.connect(self._worker.run)
        self._worker.all_finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_started(self, idx: int, filename: str):
        self.table.item(idx, 2).setText("🔄  Procesare…")
        self.status_lbl.setText(f"[{idx+1}/{len(self._files)}] {filename}")

    def _on_done(self, idx: int, result: dict):
        fields = result.get("fields", {}) or {}
        def _val(k):
            v = fields.get(k, {})
            return v.get("value", "") if isinstance(v, dict) else str(v or "")
        nume = f"{_val('nume')} {_val('prenume')}".strip() or "—"
        cnp = _val("cnp") or "—"
        self.table.item(idx, 2).setText("✅  OK")
        self.table.item(idx, 3).setText(nume)
        self.table.item(idx, 4).setText(cnp)
        self._results[idx] = result
        self.progress.setValue(self.progress.value() + 1)

    def _on_failed(self, idx: int, msg: str):
        self.table.item(idx, 2).setText(f"❌  {msg[:40]}")
        self.progress.setValue(self.progress.value() + 1)

    def _on_all_finished(self):
        self.progress.setVisible(False)
        self.status_lbl.setText(
            f"✅  Terminat: {len(self._results)} din {len(self._files)} extrase cu succes."
        )
        self.btn_start.setEnabled(False)
        self.btn_pick.setEnabled(True)
        self.cmb_type.setEnabled(True)

    # ---------------------------------------------------------------
    def get_results(self) -> list[dict]:
        """Returnează listă cu {file_path, extracted, document_type} pentru toate scanările reușite."""
        out = []
        doc_type = self.cmb_type.currentData()
        for idx, path in enumerate(self._files):
            if idx in self._results:
                out.append({
                    "file_path": path,
                    "extracted": self._results[idx],
                    "document_type": doc_type,
                })
        return out
