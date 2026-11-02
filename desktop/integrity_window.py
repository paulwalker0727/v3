# integrity_window.py — Etapa 3: fereastră PySide6 pentru verificarea integrității evidențelor
# (Pasul B, shared/cimitir_integrity). STRICT CITIRE: nu modifică și nu corectează automat nimic.
import os
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QProgressBar,
    QTreeWidget, QTreeWidgetItem, QFileDialog, QCheckBox, QMessageBox, QHeaderView
)
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt, QThread, Signal

import database as db

SHARED_DIR = Path(__file__).resolve().parent.parent / "shared"
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))
import cimitir_integrity as ci  # noqa: E402

_SEV_COLORS = {"critical": "#b00020", "error": "#d84315", "warning": "#9a7d0a", "info": "#2e7d32"}


class IntegrityWorker(QThread):
    """Rulează verificarea pe un thread separat ca să nu blocheze interfața."""
    progress = Signal(int, int, str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, db_path: str, include_info: bool = True):
        super().__init__()
        self.db_path = db_path
        self.include_info = include_info
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            report = ci.run_on_file(
                self.db_path,
                include_info=self.include_info,
                progress=lambda done, total, label: self.progress.emit(done, total, label),
                cancel=lambda: self._cancelled,
            )
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"Verificarea a eșuat: {e}")
            return
        if self._cancelled:
            self.failed.emit("Verificare anulată de utilizator.")
            return
        self.finished_ok.emit(report)


class IntegrityWindow(QDialog):
    """Etapa 3 — verificare integritate evidențe: progres, anulare, grupare pe categorii, export, fără scriere în DB."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Verificare integritate evidențe (doar citire)")
        self.resize(940, 660)
        self._worker: Optional[IntegrityWorker] = None
        self._report = None

        layout = QVBoxLayout()

        info = QLabel("Verificarea este STRICT read-only: nu modifică, nu șterge și nu corectează automat nimic. "
                      "CNP-urile și seriile actelor de identitate sunt omise din orice raport generat.")
        info.setWordWrap(True)
        info.setStyleSheet("color:#555;")
        layout.addWidget(info)

        top = QHBoxLayout()
        self.btn_run = QPushButton("Verifică integritatea datelor")
        self.btn_run.setObjectName("btn_integrity_run")
        self.btn_cancel = QPushButton("Anulează")
        self.btn_cancel.setObjectName("btn_integrity_cancel")
        self.btn_cancel.setEnabled(False)
        self.chk_info = QCheckBox("Include și verificările informative (nu doar avertismente/erori/critice)")
        self.chk_info.setChecked(True)
        top.addWidget(self.btn_run)
        top.addWidget(self.btn_cancel)
        top.addWidget(self.chk_info)
        top.addStretch()
        layout.addLayout(top)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.lbl_status = QLabel("Apăsați „Verifică integritatea datelor” pentru a începe.")
        layout.addWidget(self.lbl_status)

        self.lbl_summary = QLabel("")
        self.lbl_summary.setObjectName("lbl_integrity_summary")
        self.lbl_summary.setStyleSheet("font-weight:600;")
        self.lbl_summary.setWordWrap(True)
        layout.addWidget(self.lbl_summary)

        self.tree = QTreeWidget()
        self.tree.setObjectName("tree_integrity_issues")
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["Categorie / Cod", "Severitate", "Înregistrare", "Problemă", "Recomandare"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(4, QHeaderView.Stretch)
        layout.addWidget(self.tree, 1)

        export_row = QHBoxLayout()
        self.btn_export_json = QPushButton("Exportă JSON")
        self.btn_export_csv = QPushButton("Exportă CSV")
        self.btn_export_md = QPushButton("Exportă Markdown")
        for b, obj in ((self.btn_export_json, "btn_integrity_export_json"), (self.btn_export_csv, "btn_integrity_export_csv"),
                       (self.btn_export_md, "btn_integrity_export_md")):
            b.setObjectName(obj)
            b.setEnabled(False)
            export_row.addWidget(b)
        export_row.addStretch()
        close = QPushButton("Închide")
        close.setObjectName("btn_integrity_close")
        close.clicked.connect(self.reject)
        export_row.addWidget(close)
        layout.addLayout(export_row)

        self.setLayout(layout)

        self.btn_run.clicked.connect(self._start_check)
        self.btn_cancel.clicked.connect(self._cancel_check)
        self.btn_export_json.clicked.connect(lambda: self._export("json"))
        self.btn_export_csv.clicked.connect(lambda: self._export("csv"))
        self.btn_export_md.clicked.connect(lambda: self._export("md"))

    # ------------------------------------------------------------------
    def _start_check(self):
        db_path = db.current_db_path()
        if not db_path or not os.path.isfile(db_path):
            QMessageBox.warning(self, "Verificare integritate", "Nu am găsit baza de date activă.")
            return
        self.tree.clear()
        self._report = None
        for b in (self.btn_export_json, self.btn_export_csv, self.btn_export_md):
            b.setEnabled(False)
        self.progress.setValue(0)
        self.lbl_status.setText("Se verifică...")
        self.lbl_summary.setText("")
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self._worker = IntegrityWorker(db_path, include_info=self.chk_info.isChecked())
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_thread_finished)
        self._worker.start()

    def _cancel_check(self):
        if self._worker:
            self._worker.cancel()
        self.btn_cancel.setEnabled(False)
        self.lbl_status.setText("Se anulează...")

    def _on_thread_finished(self):
        self.btn_run.setEnabled(True)
        self.btn_cancel.setEnabled(False)

    def _on_progress(self, done: int, total: int, label: str):
        pct = int(done * 100 / total) if total else 0
        self.progress.setValue(pct)
        self.lbl_status.setText(f"Se verifică: {label} ({done}/{total})")

    def _on_failed(self, message: str):
        self.lbl_status.setText(message)

    def _on_finished(self, report):
        self._report = report
        self.progress.setValue(100)
        self._populate_tree(report)
        for b in (self.btn_export_json, self.btn_export_csv, self.btn_export_md):
            b.setEnabled(True)

    # ------------------------------------------------------------------
    def _populate_tree(self, report):
        d = report.to_dict()
        s, m = d["summary"], d["meta"]
        unchanged = m.get("db_unchanged")
        unchanged_txt = "da" if unchanged else ("necontrolat" if unchanged is None else "NU — verificați manual")
        self.lbl_status.setText(f"Verificare finalizată în {m.get('duration_s')} s. Bază nemodificată: {unchanged_txt}.")
        self.lbl_summary.setText(
            "Total probleme: {total} — critice: {c}, erori: {e}, avertismente: {w}, informații: {i}".format(
                total=s["total"], c=s["by_severity"].get("critical", 0), e=s["by_severity"].get("error", 0),
                w=s["by_severity"].get("warning", 0), i=s["by_severity"].get("info", 0)))
        by_cat = {}
        for issue in d["issues"]:
            by_cat.setdefault(issue["category_ro"], []).append(issue)
        self.tree.clear()
        for cat, issues in sorted(by_cat.items()):
            cat_item = QTreeWidgetItem([f"{cat} ({len(issues)})"])
            self.tree.addTopLevelItem(cat_item)
            for issue in issues:
                loc = f" (loc {issue['cod_loc']})" if issue.get("cod_loc") else ""
                row = QTreeWidgetItem([
                    issue["code"],
                    issue["severity_ro"],
                    f"{issue['entity']} #{issue.get('record_id') or ''}{loc}",
                    issue["description"],
                    issue["recommendation"],
                ])
                color = _SEV_COLORS.get(issue["severity"])
                if color:
                    row.setForeground(1, QColor(color))
                cat_item.addChild(row)
            cat_item.setExpanded(True)
        if m.get("check_errors"):
            err_item = QTreeWidgetItem([f"Erori interne de verificare ({len(m['check_errors'])})"])
            self.tree.addTopLevelItem(err_item)
            for e in m["check_errors"]:
                err_item.addChild(QTreeWidgetItem(["", "", "", e, ""]))
            err_item.setExpanded(True)

    def _export(self, fmt: str):
        if not self._report:
            return
        ext = {"json": "JSON (*.json)", "csv": "CSV (*.csv)", "md": "Markdown (*.md)"}[fmt]
        default_name = {"json": "verificare_evidente.json", "csv": "verificare_evidente.csv", "md": "verificare_evidente.md"}[fmt]
        path, _ = QFileDialog.getSaveFileName(self, "Exportă raport", default_name, ext)
        if not path:
            return
        try:
            if fmt == "json":
                content = ci.to_json(self._report)
            elif fmt == "csv":
                content = ci.to_csv(self._report)
            else:
                content = ci.to_markdown(self._report)
            encoding = "utf-8-sig" if fmt == "csv" else "utf-8"
            with open(path, "w", encoding=encoding, newline="" if fmt == "csv" else None) as f:
                f.write(content)
            QMessageBox.information(self, "Export raport", f"Raportul a fost salvat:\n{path}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Export raport", f"Eroare la export: {e}")
