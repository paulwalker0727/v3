"""Copii de rezervă ale bazei MAIN: setări, copie imediată, verificare și restaurare dintr-un clic (aplicată la repornire)."""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QApplication)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shared"))
import cimitir_backup  # noqa: E402
import cimitir_config  # noqa: E402
import database as db  # noqa: E402

CHECK_INTERVAL_MS = 30 * 60 * 1000


def backup_folder() -> Path:
    custom = (db.get_setting("backup_dir") or "").strip()
    return Path(custom) if custom else cimitir_backup.default_backup_dir(cimitir_config.working_dir())


def run_background_backup() -> None:
    """Copie zilnică în fundal (nu blochează interfața); sare peste dacă există una recentă sau dacă e dezactivată."""
    threading.Thread(target=lambda: cimitir_backup.run_scheduled(cimitir_config.working_dir(), db.get_setting), daemon=True,
                     name="cimitir-backup").start()


def start_scheduler(parent) -> QTimer:
    timer = QTimer(parent)
    timer.setInterval(CHECK_INTERVAL_MS)
    timer.timeout.connect(run_background_backup)
    timer.start()
    QTimer.singleShot(60_000, run_background_backup)
    return timer


class BackupWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("backup-window")
        self.setWindowTitle("Copii de rezervă")
        self.resize(720, 460)
        lay = QVBoxLayout(self)
        self.chk_enabled = QCheckBox("Copie automată zilnică (în fundal, cât timp aplicația este deschisă)")
        self.chk_enabled.setObjectName("backup-enabled")
        self.chk_enabled.setChecked((db.get_setting("backup_enabled") or "1") != "0")
        lay.addWidget(self.chk_enabled)
        row = QHBoxLayout()
        self.txt_dir = QLineEdit(str(backup_folder()))
        self.txt_dir.setObjectName("backup-dir")
        btn_dir = QPushButton("Alege folder…")
        btn_dir.clicked.connect(self._choose_dir)
        self.spin_days = QSpinBox()
        self.spin_days.setObjectName("backup-keep-days")
        self.spin_days.setRange(1, 365)
        self.spin_days.setValue(int(db.get_setting("backup_keep_days") or 14))
        row.addWidget(QLabel("Folder:"))
        row.addWidget(self.txt_dir, 1)
        row.addWidget(btn_dir)
        row.addWidget(QLabel("Păstrează (zile):"))
        row.addWidget(self.spin_days)
        lay.addLayout(row)
        hint = QLabel("Recomandat: un disc extern sau un folder sincronizat în alt loc decât calculatorul MAIN.")
        hint.setStyleSheet("color:#667")
        lay.addWidget(hint)
        self.table = QTableWidget(0, 3)
        self.table.setObjectName("backup-table")
        self.table.setHorizontalHeaderLabels(["Copie", "Data", "Mărime"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table, 1)
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("backup-status")
        lay.addWidget(self.lbl_status)
        buttons = QHBoxLayout()
        for name, text, slot in (("backup-now", "Fă copie acum", self.backup_now), ("backup-verify", "Verifică selecția", self.verify_selected),
                                 ("backup-restore", "Restaurează selecția…", self.restore_selected), ("backup-close", "Închide", self.accept)):
            button = QPushButton(text)
            button.setObjectName(name)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        lay.addLayout(buttons)
        self.refresh()

    def _choose_dir(self):
        chosen = QFileDialog.getExistingDirectory(self, "Folder pentru copii de rezervă", self.txt_dir.text())
        if chosen:
            self.txt_dir.setText(chosen)

    def _save_settings(self):
        db.set_setting("backup_enabled", "1" if self.chk_enabled.isChecked() else "0")
        db.set_setting("backup_dir", self.txt_dir.text().strip())
        db.set_setting("backup_keep_days", str(self.spin_days.value()))

    def refresh(self):
        rows = cimitir_backup.list_backups(Path(self.txt_dir.text().strip() or backup_folder()))
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for col, value in enumerate((r["name"], r["created"].replace("T", " "), f"{r['size'] / 1048576:.1f} MB")):
                self.table.setItem(i, col, QTableWidgetItem(value))
        self.lbl_status.setText(f"{len(rows)} copii în {self.txt_dir.text().strip()}")

    def _selected(self):
        rows = self.table.selectionModel().selectedRows()
        return Path(self.txt_dir.text().strip()) / self.table.item(rows[0].row(), 0).text() if rows else None

    def backup_now(self):
        self._save_settings()
        target = cimitir_backup.run_scheduled(cimitir_config.working_dir(), db.get_setting, force=True)
        self.lbl_status.setText(f"Copie creată: {target.name}" if target else "Copia a eșuat. Verifică folderul și spațiul liber.")
        self.refresh()

    def verify_selected(self):
        chosen = self._selected()
        if not chosen:
            self.lbl_status.setText("Selectează o copie.")
            return
        problems = cimitir_backup.verify(chosen)
        self.lbl_status.setText("Copia este întreagă (sume de control verificate)." if not problems else "Copie coruptă: " + "; ".join(problems[:3]))

    def restore_selected(self):
        chosen = self._selected()
        if not chosen:
            self.lbl_status.setText("Selectează o copie.")
            return
        answer = QMessageBox.question(self, "Restaurare", f"Restaurezi baza din „{chosen.name}”?\n\nAplicația se va închide; restaurarea se aplică la următoarea pornire. "
                                      "Starea curentă este păstrată într-un folder „.inainte-de-restaurare-…” lângă folderul de date.")
        if answer != QMessageBox.Yes:
            return
        try:
            cimitir_backup.schedule_restore(chosen, cimitir_config.working_dir())
        except SystemExit as exc:
            QMessageBox.critical(self, "Copie invalidă", str(exc))
            return
        QMessageBox.information(self, "Restaurare programată", "Închid aplicația. Pornește-o din nou pentru a aplica restaurarea.")
        QApplication.quit()
