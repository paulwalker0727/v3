"""mobile_devices_window.py — MAIN: telefoane Mobile v2 (permisiuni de editare pe registre), conflicte telefon și jurnal de sincronizare.

Toate datele vin prin `sync_v2` (HTTPS + cheia MAIN din SecretStore). Identificatorii tehnici nu sunt afișați: telefoanele apar după nume.
"""
from __future__ import annotations

from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTabWidget,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

import cloud_sync
import sync_v2
from mobile_pairing_panel import MobilePairingPanel
from mobile_permissions_panel import AuditPanel, MobilePermissionsPanel

ENTITY_LABELS = {"locuri_de_veci": "Locuri de veci", "concesionari": "Concesionari", "contracte": "Contracte",
                 "inhumati": "Înhumați", "plati": "Plăți", "amenajari_avize": "Avize amenajare"}
REASONS = {"version_mismatch": "Modificat între timp în MAIN", "deleted_on_server": "Șters între timp", "natural_key": "Cod deja existent"}
STATES = {"open": "De rezolvat", "main_wins": "MAIN a câștigat (modificarea telefonului păstrată în jurnal)"}


class MobileDevicesDialog(QDialog):
    def __init__(self, parent=None, tab: str = "pairing"):
        super().__init__(parent)
        self.setWindowTitle("Telefoane Mobile — asociere, permisiuni, conflicte, jurnal")
        self.setMinimumSize(780, 460)
        self._devices, self._conflicts, self._roles = [], [], {}
        root = QVBoxLayout(self)
        self.lbl = QLabel("")
        self.lbl.setWordWrap(True)
        root.addWidget(self.lbl)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.pair_panel, self.perm_panel, self.audit_panel = MobilePairingPanel(), MobilePermissionsPanel(), AuditPanel()
        self.tabs.addTab(self.pair_panel, "Asociere telefon (cod / QR)")
        self.tabs.addTab(self.perm_panel, "Permisiuni Mobile")
        self.pair_panel.permissions_requested.connect(self._open_rights)
        self._tab_devices()
        self._tab_conflicts()
        self._tab_journal()
        self.tabs.addTab(self.audit_panel, "Audit (permisiuni, ștergeri)")
        self.reload()
        self.pair_panel.reload()
        self.tabs.setCurrentIndex(0 if tab == "pairing" else 1)

    def _open_rights(self, ref: str) -> None:
        i = self.perm_panel.cmb_dev.findData(ref)
        if i < 0:
            self.perm_panel.reload()
            i = self.perm_panel.cmb_dev.findData(ref)
        if i >= 0:
            self.perm_panel.cmb_dev.setCurrentIndex(i)
        self.tabs.setCurrentWidget(self.perm_panel)

    def done(self, result) -> None:
        self.pair_panel.done_cleanup()
        super().done(result)

    def _tab_devices(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Bifează registrele pe care telefonul are voie să le modifice. Implicit telefoanele sunt doar pentru citire. "
                             "Ștergerea nu este permisă de pe telefon."))
        self.dev_table = QTableWidget(0, 2 + len(ENTITY_LABELS))
        self.dev_table.setHorizontalHeaderLabels(["Telefon", "Rol (șablon)"] + list(ENTITY_LABELS.values()))
        self.dev_table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.dev_table, 1)
        row = QHBoxLayout()
        self.btn_save = QPushButton("Salvează permisiunile")
        self.btn_save.clicked.connect(self._save_perms)
        row.addWidget(self.btn_save)
        row.addStretch(1)
        lay.addLayout(row)
        self.tabs.addTab(w, "Permisiuni simple (registre — vechi)")

    def _fill_devices(self):
        self.dev_table.setRowCount(len(self._devices))
        for r, d in enumerate(self._devices):
            self.dev_table.setItem(r, 0, QTableWidgetItem(d["name"]))
            combo = QComboBox()
            combo.addItem("— păstrează setările actuale —", "")
            for key, label in (self._roles or {}).items():
                combo.addItem(label, key)
            combo.setCurrentIndex(max(0, combo.findData(d.get("role") or "")))
            combo.setToolTip("Rolul fixează și drepturile de CITIRE (ex. fără plăți) pe server. Alegerea unui rol înlocuiește bifele rândului.")
            self.dev_table.setCellWidget(r, 1, combo)
            for c, e in enumerate(ENTITY_LABELS, start=2):
                cb = QCheckBox()
                cb.setChecked(e in d["editable"])
                self.dev_table.setCellWidget(r, c, cb)

    def _save_perms(self):
        try:
            for r, d in enumerate(self._devices):
                role = self.dev_table.cellWidget(r, 1).currentData()
                if role and role != (d.get("role") or ""):
                    sync_v2.set_device_role(d["ref"], role)
                    continue
                chosen = [e for c, e in enumerate(ENTITY_LABELS, start=2) if self.dev_table.cellWidget(r, c).isChecked()]
                if set(chosen) != set(d["editable"]):
                    sync_v2.set_device_entities(d["ref"], chosen)
        except (cloud_sync.RetryLater, cloud_sync.Rejected) as exc:
            QMessageBox.warning(self, "Permisiuni", f"Nu s-au salvat: {getattr(exc, 'detail', exc)}")
            return
        QMessageBox.information(self, "Permisiuni", "Permisiunile au fost salvate în cloud.")
        self.reload()

    def _tab_conflicts(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Conflict = telefonul a modificat o înregistrare care între timp s-a schimbat în MAIN. MAIN câștigă; nimic nu se pierde: "
                             "poți păstra varianta MAIN sau accepta modificarea telefonului."))
        self.cf_table = QTableWidget(0, 4)
        self.cf_table.setHorizontalHeaderLabels(["Registru", "Motiv", "Stare", "Data"])
        self.cf_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.cf_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.cf_table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.cf_table, 1)
        row = QHBoxLayout()
        for text, res in (("Păstrează varianta MAIN", "keep_main"), ("Acceptă modificarea telefonului", "accept_incoming"),
                          ("Restaurează varianta telefonului", "restore_previous")):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, r=res: self._resolve(r))
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.tabs.addTab(w, "Conflicte telefon")

    def _fill_conflicts(self):
        self.cf_table.setRowCount(len(self._conflicts))
        for r, c in enumerate(self._conflicts):
            vals = (ENTITY_LABELS.get(c["entity"], c["entity"]), REASONS.get(c["reason"], c["reason"]), STATES.get(c["resolution"], c["resolution"]),
                    str(c["created_at"])[:19].replace("T", " "))
            for i, v in enumerate(vals):
                self.cf_table.setItem(r, i, QTableWidgetItem(v))

    def _resolve(self, resolution: str):
        r = self.cf_table.currentRow()
        if r < 0:
            QMessageBox.information(self, "Conflicte", "Selectează un conflict.")
            return
        try:
            sync_v2.resolve_server_conflict(self._conflicts[r]["id"], resolution)
        except (cloud_sync.RetryLater, cloud_sync.Rejected) as exc:
            QMessageBox.warning(self, "Conflicte", f"Nu se poate aplica: {getattr(exc, 'detail', exc)}")
            return
        self.reload()

    def _tab_journal(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.jr_table = QTableWidget(0, 8)
        self.jr_table.setHorizontalHeaderLabels(["Data", "Dispozitiv", "Sens", "Primite", "Aplicate", "Conflicte", "Respinse", "Duplicate"])
        self.jr_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.jr_table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.jr_table, 1)
        self.tabs.addTab(w, "Jurnal sincronizare")

    def _fill_journal(self, runs):
        self.jr_table.setRowCount(len(runs))
        for r, x in enumerate(runs):
            vals = (str(x["created_at"])[:19].replace("T", " "), x.get("device") or x["device_type"], "trimitere" if x["direction"] == "push" else "descărcare",
                    x["received"] or x["pulled"], x["applied"], x["conflicts"], x["rejected"], x["duplicates"])
            for i, v in enumerate(vals):
                self.jr_table.setItem(r, i, QTableWidgetItem(str(v)))

    def reload(self):
        try:
            info = sync_v2.list_devices()
            self._devices, self._roles = info["devices"], info.get("roles", {})
            self._conflicts = sync_v2.server_conflicts(True)
            runs = sync_v2.journal(100)
        except (cloud_sync.RetryLater, cloud_sync.Rejected) as exc:
            self.lbl.setText(f"Cloud indisponibil, cheie MAIN lipsă sau backend vechi: {getattr(exc, 'detail', exc)}")
            return
        self.lbl.setText(f"{len(self._devices)} telefon(e) asociate · {sum(1 for c in self._conflicts if c['resolution'] == 'open')} conflict(e) de rezolvat")
        self._fill_devices()
        self._fill_conflicts()
        self._fill_journal(runs)
        try:
            self.perm_panel.reload()
            self.audit_panel.reload()
        except (cloud_sync.RetryLater, cloud_sync.Rejected) as exc:
            self.lbl.setText(self.lbl.text() + f" · Permisiuni Mobile indisponibile (backend vechi sau migrarea 027 lipsă?): {getattr(exc, 'detail', exc)}")
