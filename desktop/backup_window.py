"""Copii de rezervă MAIN (interfață unică): programare, copie imediată, verificare, retenție, jurnal și restaurare manuală confirmată."""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QTime, QTimer
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QTimeEdit,
                               QVBoxLayout, QWidget)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shared"))
import cimitir_backup  # noqa: E402
import cimitir_config  # noqa: E402
import database as db  # noqa: E402

CHECK_INTERVAL_MS = 30 * 60 * 1000
STATUS_INTERVAL_MS = 60 * 1000
LEVEL_STYLE = {"error": "background:#fde8e8;color:#9b1c1c;border:1px solid #f5b5b5;padding:6px;font-weight:bold",
               "warning": "background:#fff4d6;color:#7a5200;border:1px solid #f0d58a;padding:6px",
               "off": "background:#eef;color:#445;border:1px solid #ccd;padding:6px",
               "ok": "background:#e6f4ea;color:#1e5a2e;border:1px solid #b9dcc3;padding:6px"}


def backup_folder() -> Path:
    return cimitir_backup.backup_destination(Path(cimitir_config.working_dir()), db.get_setting)


def backup_health() -> dict:
    return cimitir_backup.health(Path(cimitir_config.working_dir()), db.get_setting)


def run_background_backup() -> None:
    """Copie programată în fundal (nu blochează interfața); sare peste dacă există una recentă sau dacă e dezactivată."""
    threading.Thread(target=lambda: cimitir_backup.run_scheduled(Path(cimitir_config.working_dir()), db.get_setting), daemon=True,
                     name="cimitir-backup").start()


def scheduler_run_command() -> str:
    """Comanda executată de activitatea programată (copie verificată, jurnalizată; nu restaurează)."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --backup-run'
    script = Path(__file__).resolve().parent.parent / "scripts" / "backup_scheduled.py"
    return f'"{sys.executable}" "{script}"'


def scheduler_command(at: str | None = None) -> str:
    """Comanda COMPLETĂ de înregistrare în Planificatorul de activități Windows, cu ora din setarea `backup_time` (BUG-06):
    activitatea rulează zilnic la acea oră, independent de aplicația deschisă. Ora se alege din „Ora de pornire (Planificator)”."""
    at = (at or db.get_setting("backup_time") or cimitir_backup.DEFAULTS["backup_time"]).strip()
    run = scheduler_run_command().replace('"', '\\"')
    return f'schtasks /Create /TN "Cimitir - Copie de rezerva" /SC DAILY /ST {at} /TR "{run}" /F'


def attach_status_label(main_window) -> QLabel | None:
    """Avertizare vizibilă permanent în bara de stare MAIN când ultima copie a eșuat sau este prea veche."""
    try:
        bar = main_window.statusBar()
    except Exception:
        return None
    label = QLabel("")
    label.setObjectName("backup-status-label")
    label.setVisible(False)
    bar.addPermanentWidget(label)

    def refresh():
        try:
            state = backup_health()
        except Exception as exc:
            state = {"level": "warning", "message": f"Starea copiilor nu a putut fi citită: {exc}"}
        label.setVisible(state["level"] in ("error", "warning"))
        label.setText(("⛔ Backup EȘUAT" if state["level"] == "error" else "⚠ Backup") + " — vezi „💾 Copii de rezervă”")
        label.setToolTip(state["message"])
        label.setStyleSheet(LEVEL_STYLE.get(state["level"], ""))
    timer = QTimer(label)
    timer.setInterval(STATUS_INTERVAL_MS)
    timer.timeout.connect(refresh)
    timer.start()
    label._refresh = refresh
    refresh()
    return label


def start_scheduler(parent) -> QTimer:
    timer = QTimer(parent)
    timer.setInterval(CHECK_INTERVAL_MS)
    timer.timeout.connect(run_background_backup)
    timer.start()
    QTimer.singleShot(60_000, run_background_backup)
    if parent is not None:
        parent._backup_status_label = attach_status_label(parent)
    return timer


class BackupWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("backup-window")
        self.setWindowTitle("Copii de rezervă")
        self.resize(820, 600)
        lay = QVBoxLayout(self)
        self.lbl_warning = QLabel("")
        self.lbl_warning.setObjectName("backup-warning")
        self.lbl_warning.setWordWrap(True)
        lay.addWidget(self.lbl_warning)
        tabs = QTabWidget()
        tabs.setObjectName("backup-tabs")
        lay.addWidget(tabs, 1)
        tabs.addTab(self._build_copies_tab(), "Copii")
        tabs.addTab(self._build_schedule_tab(), "Programare și retenție")
        tabs.addTab(self._build_log_tab(), "Jurnal")
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("backup-status")
        self.lbl_status.setWordWrap(True)
        lay.addWidget(self.lbl_status)
        buttons = QHBoxLayout()
        for name, text, slot in (("backup-now", "Fă copie acum", self.backup_now), ("backup-verify", "Verifică selecția", self.verify_selected),
                                 ("backup-protect", "Protejează / deprotejează selecția", self.toggle_protect),
                                 ("backup-restore", "Restaurează selecția…", self.restore_selected), ("backup-close", "Închide", self.accept)):
            button = QPushButton(text)
            button.setObjectName(name)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        lay.addLayout(buttons)
        self.refresh()

    # ------------------------------------------------------------ taburi
    def _build_copies_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.table = QTableWidget(0, 4)
        self.table.setObjectName("backup-table")
        self.table.setHorizontalHeaderLabels(["Copie", "Data", "Mărime", "Protejată"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table)
        hint = QLabel("Restaurarea este întotdeauna manuală: alegi o copie, confirmi explicit, iar baza se înlocuiește abia la următoarea pornire. "
                      "Nicio programare nu restaurează singură.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#667")
        lay.addWidget(hint)
        return w

    def _build_schedule_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        form = QFormLayout()
        self.chk_enabled = QCheckBox("Copie automată activă (în aplicație și prin Planificatorul Windows)")
        self.chk_enabled.setObjectName("backup-enabled")
        self.chk_enabled.setChecked((db.get_setting("backup_enabled") or "1") != "0")
        form.addRow(self.chk_enabled)
        row = QHBoxLayout()
        self.txt_dir = QLineEdit(str(backup_folder()))
        self.txt_dir.setObjectName("backup-dir")
        btn_dir = QPushButton("Alege folder…")
        btn_dir.clicked.connect(self._choose_dir)
        row.addWidget(self.txt_dir, 1)
        row.addWidget(btn_dir)
        form.addRow("Folder copii:", row)
        self.spin_interval = QDoubleSpinBox()
        self.spin_interval.setObjectName("backup-interval-hours")
        self.spin_interval.setRange(1, 168)
        self.spin_interval.setDecimals(0)
        self.spin_interval.setValue(float(db.get_setting("backup_interval_hours") or cimitir_backup.DEFAULTS["backup_interval_hours"]))
        form.addRow("La fiecare (ore):", self.spin_interval)
        self.time_start = QTimeEdit()
        self.time_start.setObjectName("backup-time")
        self.time_start.setDisplayFormat("HH:mm")
        self.time_start.setTime(QTime.fromString(db.get_setting("backup_time") or cimitir_backup.DEFAULTS["backup_time"], "HH:mm"))
        form.addRow("Ora de pornire (Planificator):", self.time_start)
        self.spin_days = QSpinBox()
        self.spin_days.setObjectName("backup-keep-days")
        self.spin_days.setRange(1, 365)
        self.spin_days.setValue(int(db.get_setting("backup_keep_days") or 14))
        form.addRow("Păstrează (zile):", self.spin_days)
        self.spin_min = QSpinBox()
        self.spin_min.setObjectName("backup-keep-min")
        self.spin_min.setRange(cimitir_backup.MIN_KEEP_FLOOR, 100)
        self.spin_min.setValue(max(cimitir_backup.MIN_KEEP_FLOOR, int(db.get_setting("backup_keep_min") or cimitir_backup.MIN_KEEP_FLOOR)))
        form.addRow("Păstrează mereu cel puțin (copii):", self.spin_min)
        lay.addLayout(form)
        guard = QLabel(f"Protecții la ștergere: nu se șterg niciodată cele mai noi {cimitir_backup.MIN_KEEP_FLOOR}+ copii, nimic nu se șterge dacă "
                       "acestea nu sunt întregi, iar copiile marcate „protejate” rămân. Recomandat: folder pe un disc extern sau în alt loc decât calculatorul MAIN.")
        guard.setWordWrap(True)
        guard.setStyleSheet("color:#667")
        lay.addWidget(guard)
        btn_save = QPushButton("Salvează setările")
        btn_save.setObjectName("backup-save-settings")
        btn_save.clicked.connect(self.save_settings)
        lay.addWidget(btn_save)
        lay.addWidget(QLabel("Programare Windows (rulează și când MAIN este închis) — comanda de înregistrare (ora = „Ora de pornire”, rulată o dată, ca administrator dacă e cerut):"))
        self.txt_command = QPlainTextEdit(scheduler_command(self.time_start.time().toString("HH:mm")))
        self.txt_command.setObjectName("backup-scheduler-command")
        self.txt_command.setReadOnly(True)
        self.txt_command.setMaximumHeight(80)
        self.time_start.timeChanged.connect(lambda t: self.txt_command.setPlainText(scheduler_command(t.toString("HH:mm"))))
        lay.addWidget(self.txt_command)
        lay.addStretch(1)
        return w

    def _build_log_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.log_table = QTableWidget(0, 5)
        self.log_table.setObjectName("backup-log-table")
        self.log_table.setHorizontalHeaderLabels(["Data", "Rezultat", "Declanșare", "Locație", "Detalii"])
        self.log_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.log_table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.log_table)
        return w

    # ------------------------------------------------------------ acțiuni
    def _choose_dir(self):
        chosen = QFileDialog.getExistingDirectory(self, "Folder pentru copii de rezervă", self.txt_dir.text())
        if chosen:
            self.txt_dir.setText(chosen)

    def _save_settings(self):
        db.set_setting("backup_enabled", "1" if self.chk_enabled.isChecked() else "0")
        db.set_setting("backup_dir", self.txt_dir.text().strip())
        db.set_setting("backup_keep_days", str(self.spin_days.value()))
        db.set_setting("backup_keep_min", str(self.spin_min.value()))
        db.set_setting("backup_interval_hours", str(int(self.spin_interval.value())))
        db.set_setting("backup_time", self.time_start.time().toString("HH:mm"))

    def save_settings(self):
        self._save_settings()
        self.lbl_status.setText("Setările au fost salvate.")
        self.refresh()

    def refresh(self):
        folder = Path(self.txt_dir.text().strip() or backup_folder())
        rows = cimitir_backup.list_backups(folder)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            protected = "da" if cimitir_backup.is_protected(Path(r["path"])) else ""
            for col, value in enumerate((r["name"], r["created"].replace("T", " "), f"{r['size'] / 1048576:.1f} MB", protected)):
                self.table.setItem(i, col, QTableWidgetItem(value))
        self.lbl_status.setText(f"{len(rows)} copii în {folder}")
        self._refresh_log()
        self._refresh_warning()

    def _refresh_warning(self):
        state = backup_health()
        self.lbl_warning.setText(("⛔ " if state["level"] == "error" else "⚠ " if state["level"] in ("warning", "off") else "✔ ") + state["message"])
        self.lbl_warning.setStyleSheet(LEVEL_STYLE.get(state["level"], ""))
        self.lbl_warning.setProperty("level", state["level"])

    def _refresh_log(self):
        rows = cimitir_backup.read_log(Path(cimitir_config.working_dir()), 100)
        self.log_table.setRowCount(len(rows))
        for i, e in enumerate(rows):
            detail = e.get("error") or f"{e.get('files', '?')} fișiere, {e.get('size', 0) / 1048576:.1f} MB, verificată" + (f", șterse: {e['pruned']}" if e.get("pruned") else "")
            values = (str(e.get("time", "")).replace("T", " "), {"ok": "REUȘIT", "busy": "OCUPAT"}.get(e.get("result"), "EȘEC"), e.get("trigger", ""), e.get("location", ""), detail)
            for col, value in enumerate(values):
                self.log_table.setItem(i, col, QTableWidgetItem(value))

    def _selected(self):
        rows = self.table.selectionModel().selectedRows()
        return Path(self.txt_dir.text().strip()) / self.table.item(rows[0].row(), 0).text() if rows else None

    def backup_now(self):
        self._save_settings()
        result = cimitir_backup.run_backup_job(Path(cimitir_config.working_dir()), db.get_setting, force=True, trigger="manual")
        self.refresh()
        if result["result"] == "ok":
            self.lbl_status.setText(f"Copie creată și verificată: {result['backup']}")
        else:
            self.lbl_status.setText("Copia a EȘUAT: " + str(result.get("error") or result["result"]))

    def verify_selected(self):
        chosen = self._selected()
        if not chosen:
            self.lbl_status.setText("Selectează o copie.")
            return
        problems = cimitir_backup.verify(chosen)
        self.lbl_status.setText("Copia este întreagă (sume de control verificate)." if not problems else "Copie coruptă: " + "; ".join(problems[:3]))

    def toggle_protect(self):
        chosen = self._selected()
        if not chosen:
            self.lbl_status.setText("Selectează o copie.")
            return
        cimitir_backup.set_protected(chosen, not cimitir_backup.is_protected(chosen))
        self.refresh()
        self.lbl_status.setText("Copie protejată de ștergere." if cimitir_backup.is_protected(chosen) else "Protecția a fost scoasă.")

    def restore_selected(self):
        chosen = self._selected()
        if not chosen:
            self.lbl_status.setText("Selectează o copie.")
            return
        answer = QMessageBox.question(self, "Restaurare", f"Restaurezi baza din „{chosen.name}”?\n\nAplicația se va închide; restaurarea se aplică la următoarea pornire. "
                                      "Starea curentă este păstrată într-un folder „.inainte-de-restaurare-…” lângă folderul de date.",
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        try:
            cimitir_backup.schedule_restore(chosen, Path(cimitir_config.working_dir()))
        except SystemExit as exc:
            QMessageBox.critical(self, "Copie invalidă", str(exc))
            return
        QMessageBox.information(self, "Restaurare programată", "Închid aplicația. Pornește-o din nou pentru a aplica restaurarea.")
        QApplication.quit()
