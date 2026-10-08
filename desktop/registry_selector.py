"""Selector de registre MAIN (Etapa 3): Organizație -> Cimitir. Local-first, fără internet.

Dialogul este subțire: toată logica (catalog, activare, adoptare) este în shared/cimitir_registry*.py.
Rezultat: `RegistrySelector.exec()` == Accepted și `.binding` setat (registrul activ), sau `.legacy_chosen` pentru baza veche.
"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QDialogButtonBox)

_SHARED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shared")
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)

import cimitir_config  # noqa: E402
import cimitir_registry as reg  # noqa: E402
import cimitir_registry_adopt as adopt  # noqa: E402


class OrganizationDialog(QDialog):
    def __init__(self, parent=None, org=None):
        super().__init__(parent)
        self.setWindowTitle("Editează organizația" if org else "Adaugă organizație")
        self.setMinimumWidth(460)
        org = org or {}
        form = QFormLayout(self)
        self.type = QComboBox()
        self.type.setObjectName("org-type")
        for key, label in reg.ORG_TYPES.items():
            self.type.addItem(label, key)
        if org.get("org_type"):
            self.type.setCurrentIndex(self.type.findData(org["org_type"]))
        self.fields = {}
        for key, label in (("official_name", "Denumire oficială *"), ("display_name", "Denumire afișată"), ("cui", "CUI / CIF"),
                           ("county", "Județ"), ("locality", "Localitate"), ("address", "Adresă"), ("phone", "Telefon"),
                           ("email", "Email"), ("logo_path", "Logo (fișier)")):
            edit = QLineEdit(str(org.get(key) or ""))
            edit.setObjectName(f"org-{key}")
            self.fields[key] = edit
            if key == "logo_path":
                row = QHBoxLayout()
                row.addWidget(edit)
                btn = QPushButton("…")
                btn.clicked.connect(self._browse_logo)
                row.addWidget(btn)
                form.addRow(label, row)
            else:
                form.addRow(label, edit)
            if key == "official_name":
                form.insertRow(0, "Tip organizație", self.type)
        self.active = QCheckBox("Activă")
        self.active.setChecked(bool(org.get("active", 1)))
        form.addRow("", self.active)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse_logo(self):
        path, _ = QFileDialog.getOpenFileName(self, "Logo", "", "Imagini (*.png *.jpg *.jpeg *.svg)")
        if path:
            self.fields["logo_path"].setText(path)

    def values(self) -> dict:
        out = {k: e.text().strip() for k, e in self.fields.items()}
        out["org_type"] = self.type.currentData()
        return out


class CemeteryDialog(QDialog):
    def __init__(self, parent=None, cem=None):
        super().__init__(parent)
        self.setWindowTitle("Editează cimitirul" if cem else "Adaugă cimitir")
        self.setMinimumWidth(420)
        cem = cem or {}
        form = QFormLayout(self)
        self.fields = {}
        for key, label in (("name", "Denumire cimitir *"), ("county", "Județ"), ("locality", "Localitate"), ("address", "Adresă")):
            edit = QLineEdit(str(cem.get(key) or ""))
            edit.setObjectName(f"cem-{key}")
            self.fields[key] = edit
            form.addRow(label, edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> dict:
        return {k: e.text().strip() for k, e in self.fields.items()}


class RegistrySelector(QDialog):
    def __init__(self, parent=None, data_dir=None):
        super().__init__(parent)
        self.setWindowTitle("Cimitir — Alege registrul")
        self.setMinimumSize(820, 480)
        self.data_dir = data_dir or cimitir_config.data_dir()
        self.catalog = reg.RegistryCatalog(self.data_dir)
        self.binding = None
        self.legacy_chosen = False
        self.show_archived = False

        root = QVBoxLayout(self)
        title = QLabel("<h2>Registre locale</h2>Alege organizația și cimitirul. Datele rămân pe acest calculator.")
        root.addWidget(title)

        self.legacy_box = QLabel()
        self.legacy_box.setObjectName("legacy-banner")
        self.legacy_box.setWordWrap(True)
        root.addWidget(self.legacy_box)
        legacy_row = QHBoxLayout()
        self.btn_adopt = QPushButton("Adoptă instalația existentă…")
        self.btn_adopt.clicked.connect(self._adopt_legacy)
        self.btn_legacy = QPushButton("Continuă cu baza veche (fără registre)")
        self.btn_legacy.clicked.connect(self._continue_legacy)
        legacy_row.addWidget(self.btn_adopt)
        legacy_row.addWidget(self.btn_legacy)
        legacy_row.addStretch(1)
        self.legacy_row_widgets = (self.btn_adopt, self.btn_legacy)
        root.addLayout(legacy_row)

        lists = QHBoxLayout()
        left, right = QVBoxLayout(), QVBoxLayout()
        left.addWidget(QLabel("<b>Organizație</b>"))
        self.orgs = QListWidget()
        self.orgs.setObjectName("org-list")
        self.orgs.currentRowChanged.connect(self._load_cemeteries)
        left.addWidget(self.orgs)
        right.addWidget(QLabel("<b>Cimitir</b>"))
        self.cems = QListWidget()
        self.cems.setObjectName("cemetery-list")
        self.cems.itemDoubleClicked.connect(lambda _i: self._open())
        right.addWidget(self.cems)
        lists.addLayout(left, 1)
        lists.addLayout(right, 1)
        root.addLayout(lists, 1)

        actions = QHBoxLayout()
        for text, slot, name in (("Adaugă organizație", self._add_org, "btn-add-org"),
                                 ("Adaugă cimitir", self._add_cem, "btn-add-cem"),
                                 ("Deschide registru", self._open, "btn-open"),
                                 ("Editează", self._edit, "btn-edit"),
                                 ("Arhivează", self._archive, "btn-archive"),
                                 ("Configurare cloud", self._cloud, "btn-cloud")):
            btn = QPushButton(text)
            btn.setObjectName(name)
            btn.clicked.connect(slot)
            actions.addWidget(btn)
        root.addLayout(actions)

        self.auto = QCheckBox("Deschide automat data viitoare (dacă există un singur registru)")
        self.auto.setChecked(self.catalog.get_state("auto_open") == "1")
        root.addWidget(self.auto)
        self._refresh()

    # -- date
    def _selected_org(self):
        item = self.orgs.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _selected_cem(self):
        item = self.cems.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _refresh(self):
        legacy = adopt.detect_legacy(self.data_dir)
        self.legacy_box.setVisible(bool(legacy))
        for w in self.legacy_row_widgets:
            w.setVisible(bool(legacy))
        if legacy:
            self.legacy_box.setText(f"Am găsit registru existent: {legacy['records']} înregistrări, {legacy['media_files']} fișiere media "
                                    f"(cimitir.db). Nu se mută nimic fără confirmare.")
        current = self._selected_org()
        self.orgs.clear()
        for org in self.catalog.list_organizations():
            item = QListWidgetItem(f"{org['display_name']}  ·  {reg.ORG_TYPES.get(org['org_type'], org['org_type'])}")
            item.setData(Qt.UserRole, org["uid"])
            self.orgs.addItem(item)
            if org["uid"] == current:
                self.orgs.setCurrentItem(item)
        if self.orgs.count() and self.orgs.currentRow() < 0:
            self.orgs.setCurrentRow(0)
        self._load_cemeteries()

    def _load_cemeteries(self, *_):
        self.cems.clear()
        org = self._selected_org()
        if not org:
            return
        for cem in self.catalog.list_cemeteries(org):
            item = QListWidgetItem(f"{cem['name']}  ({cem['locality'] or '—'})")
            item.setData(Qt.UserRole, cem["uid"])
            self.cems.addItem(item)
        if self.cems.count():
            self.cems.setCurrentRow(0)

    def _error(self, exc):
        QMessageBox.warning(self, "Registre", str(exc))

    # -- acțiuni
    def _add_org(self):
        dlg = OrganizationDialog(self)
        if dlg.exec() == QDialog.Accepted:
            v = dlg.values()
            try:
                self.catalog.create_organization(v.pop("org_type"), v.pop("official_name"), v.pop("display_name"), **v)
            except reg.RegistryError as exc:
                return self._error(exc)
            self._refresh()

    def _add_cem(self):
        org = self._selected_org()
        if not org:
            return self._error("Alege sau adaugă mai întâi o organizație.")
        dlg = CemeteryDialog(self)
        if dlg.exec() == QDialog.Accepted:
            v = dlg.values()
            try:
                self.catalog.create_cemetery(org, v.pop("name"), **v)
            except reg.RegistryError as exc:
                return self._error(exc)
            self._refresh()

    def _edit(self):
        cem_uid = self._selected_cem()
        if self.cems.hasFocus() and cem_uid:
            dlg = CemeteryDialog(self, self.catalog.get_cemetery(cem_uid))
            if dlg.exec() == QDialog.Accepted:
                try:
                    self.catalog.update_cemetery(cem_uid, **dlg.values())
                except reg.RegistryError as exc:
                    return self._error(exc)
        else:
            org_uid = self._selected_org()
            if not org_uid:
                return
            dlg = OrganizationDialog(self, self.catalog.get_organization(org_uid))
            if dlg.exec() == QDialog.Accepted:
                v = dlg.values()
                try:
                    self.catalog.update_organization(org_uid, **v)
                    self.catalog.set_organization_active(org_uid, dlg.active.isChecked())
                except reg.RegistryError as exc:
                    return self._error(exc)
        self._refresh()

    def _archive(self):
        cem_uid = self._selected_cem()
        if not cem_uid:
            return
        if QMessageBox.question(self, "Arhivează", "Cimitirul dispare din selector și nu mai poate fi deschis. Datele rămân pe disc. Continui?",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.catalog.archive_cemetery(cem_uid)
            self._refresh()

    def _open(self) -> bool:
        cem_uid = self._selected_cem()
        if not cem_uid:
            self._error("Alege un cimitir.")
            return False
        try:
            self.binding = reg.activate(cem_uid, self.data_dir, catalog=self.catalog)
        except reg.RegistryError as exc:
            self._error(exc)
            return False
        reg.set_auto_open(self.auto.isChecked(), self.data_dir)
        self.accept()
        return True

    def _cloud(self):
        if not self._selected_cem():
            return self._error("Alege un cimitir.")
        try:
            self.binding = reg.activate(self._selected_cem(), self.data_dir, catalog=self.catalog)
        except reg.RegistryError as exc:
            return self._error(exc)
        from cloud_sync_window import CloudSyncWindow
        CloudSyncWindow(self).exec()
        reg.set_auto_open(self.auto.isChecked(), self.data_dir)
        self.accept()

    def _continue_legacy(self):
        reg.set_legacy_mode(True, self.data_dir)
        self.legacy_chosen = True
        self.accept()

    def _adopt_legacy(self):
        cem_uid = self._selected_cem()
        if not cem_uid:
            return self._error("Creează (sau alege) întâi organizația și cimitirul în care va fi mutată instalația veche.")
        try:
            plan = adopt.plan_adoption(cem_uid, self.data_dir, self.catalog)
        except reg.RegistryError as exc:
            return self._error(exc)
        text = "\n".join(plan["lines"]) + "\n\nOriginalele se păstrează în folderul legacy_adopted_<uid>. Confirmi mutarea?"
        if QMessageBox.question(self, "Adoptare registru existent", text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            adopt.adopt_legacy(cem_uid, confirm=True, data_dir=self.data_dir, catalog=self.catalog)
        except reg.RegistryError as exc:
            return self._error(exc)
        QMessageBox.information(self, "Adoptare", "Instalația veche a fost mutată în registrul nou.")
        self._refresh()
