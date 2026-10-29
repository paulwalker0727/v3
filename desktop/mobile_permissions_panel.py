"""mobile_permissions_panel.py — MAIN → Centrul Cloud → F. Mobile → „Permisiuni Mobile".

Pentru fiecare telefon: tabel de bife pe 8 categorii × (Vede / Adaugă / Editează / Șterge-Anulează), câmpuri editabile per categorie, profiluri predefinite
(Administrator, Secretar, Personal cimitir, Doar consultare — personalizabile) și jurnal de audit. Regulile se IMPUN PE SERVER (cimitir_mobile_perms); panoul doar le configurează
prin Sync v2 (aceeași autentificare Binding MAIN). Nu există alt canal sau sistem paralel de permisiuni.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
from PySide6.QtCore import Qt

import cloud_sync
import sync_v2

ACTIONS = ("view", "add", "edit", "delete")
AUDIT_LABELS = {"permissions_changed": "Permisiuni modificate", "delete": "Ștergere/anulare de pe telefon", "denied": "Operațiune refuzată",
                "issued": "Document emis", "profile_changed": "Profil modificat"}


def slug(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", label.lower().replace("ă", "a").replace("â", "a").replace("î", "i").replace("ș", "s").replace("ş", "s").replace("ț", "t").replace("ţ", "t")).strip("_")
    return (s or "profil")[:40]


class FieldsDialog(QDialog):
    """Alege câmpurile pe care telefonul le poate MODIFICA în categorie (restul rămân doar pentru citire)."""

    def __init__(self, parent, title: str, fields: List[str], selected: Optional[List[str]]):
        super().__init__(parent)
        self.setWindowTitle(f"Câmpuri editabile — {title}")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Bifează câmpurile pe care telefonul le poate modifica. Câmpurile nebifate (ex. numărul sau coordonatele) rămân blocate pe server."))
        self.lst = QListWidget()
        for f in fields:
            it = QListWidgetItem(f)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if (selected is None or f in selected) else Qt.Unchecked)
            self.lst.addItem(it)
        lay.addWidget(self.lst)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def selected(self) -> Optional[List[str]]:
        sel = [self.lst.item(i).text() for i in range(self.lst.count()) if self.lst.item(i).checkState() == Qt.Checked]
        return None if len(sel) == self.lst.count() else sel


class MobilePermissionsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._meta: Dict[str, Any] = {}
        self._devices: List[Dict[str, Any]] = []
        self._fields: Dict[str, Optional[List[str]]] = {}
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Permisiuni Mobile: bifează ce poate face fiecare telefon pe categorie. Fără „Vede”, categoria nu apare pe telefon și nu poate fi accesată prin API. "
                             "Regulile se aplică pe server (și operațiilor offline trimise ulterior); ștergerea/anularea este autorizată separat și auditată."))
        top = QHBoxLayout()
        self.cmb_dev, self.cmb_prof = QComboBox(), QComboBox()
        self.cmb_dev.setToolTip("Telefonul (utilizatorul) configurat")
        self.btn_apply = QPushButton("Aplică profilul în tabel")
        self.chk_pii = QCheckBox("Acces date sensibile (CNP, serie/nr. CI)")
        for w in (QLabel("Telefon:"), self.cmb_dev, QLabel("Profil:"), self.cmb_prof, self.btn_apply, self.chk_pii):
            top.addWidget(w)
        top.addStretch(1)
        lay.addLayout(top)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Categorie", "Vede", "Adaugă", "Editează", "Șterge / Anulează", "Câmpuri editabile"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.btn_save = QPushButton("Salvează permisiunile telefonului")
        self.btn_profile = QPushButton("Salvează tabelul ca profil…")
        row.addWidget(self.btn_save)
        row.addWidget(self.btn_profile)
        row.addStretch(1)
        lay.addLayout(row)
        self.cmb_dev.currentIndexChanged.connect(self._load_device)
        self.btn_apply.clicked.connect(self._apply_profile)
        self.btn_save.clicked.connect(self._save)
        self.btn_profile.clicked.connect(self._save_profile)

    # ------------------------------------------------------------------ date
    def reload(self) -> None:
        self._meta = sync_v2.permission_profiles()
        self._devices = sync_v2.list_devices().get("devices", [])
        self.cmb_dev.blockSignals(True)
        self.cmb_dev.clear()
        for d in self._devices:
            self.cmb_dev.addItem(d["name"], d["ref"])
        self.cmb_dev.blockSignals(False)
        self.cmb_prof.clear()
        for k, p in self._meta["profiles"].items():
            self.cmb_prof.addItem(p["label"] + (" (personalizat)" if p.get("custom") else ""), k)
        self._build_table()
        self._load_device()

    def _build_table(self) -> None:
        cats = self._meta["categories"]
        self.table.setRowCount(len(cats))
        self._boxes: Dict[str, Dict[str, QCheckBox]] = {}
        self._btns: Dict[str, QPushButton] = {}
        for r, (cat, label) in enumerate(cats.items()):
            self.table.setItem(r, 0, QTableWidgetItem(label))
            self._boxes[cat] = {}
            for c, act in enumerate(ACTIONS, start=1):
                cb = QCheckBox()
                cb.setObjectName(f"perm-{cat}-{act}")
                cb.toggled.connect(lambda checked, ct=cat, a=act: self._coupled(ct, a, checked))
                self.table.setCellWidget(r, c, cb)
                self._boxes[cat][act] = cb
            btn = QPushButton("Toate câmpurile")
            btn.clicked.connect(lambda _=False, ct=cat: self._pick_fields(ct))
            self.table.setCellWidget(r, 5, btn)
            self._btns[cat] = btn

    def _coupled(self, cat: str, act: str, checked: bool) -> None:
        """Adaugă/Editează/Șterge cer „Vede"; debifarea lui „Vede" le debifează pe toate."""
        b = self._boxes[cat]
        for x in b.values():
            x.blockSignals(True)
        if act != "view" and checked:
            b["view"].setChecked(True)
        if act == "view" and not checked:
            for k in ("add", "edit", "delete"):
                b[k].setChecked(False)
        for x in b.values():
            x.blockSignals(False)
        self._btns[cat].setEnabled(b["edit"].isChecked())

    def _show(self, matrix: Dict[str, Any]) -> None:
        for cat, boxes in self._boxes.items():
            cell = (matrix or {}).get(cat) or {}
            for act, cb in boxes.items():
                cb.blockSignals(True)
                cb.setChecked(bool(cell.get(act)))
                cb.blockSignals(False)
            self._fields[cat] = cell.get("fields")
            self._refresh_btn(cat)

    def _refresh_btn(self, cat: str) -> None:
        f = self._fields.get(cat)
        self._btns[cat].setText("Toate câmpurile" if f is None else f"{len(f)} câmp(uri)")
        self._btns[cat].setEnabled(self._boxes[cat]["edit"].isChecked())

    def _pick_fields(self, cat: str) -> None:
        dlg = FieldsDialog(self, self._meta["categories"][cat], self._meta["fields"][cat], self._fields.get(cat))
        if dlg.exec():
            self._fields[cat] = dlg.selected()
            self._refresh_btn(cat)

    def matrix(self) -> Dict[str, Any]:
        return {cat: {**{a: cb.isChecked() for a, cb in boxes.items()}, "fields": self._fields.get(cat)} for cat, boxes in self._boxes.items()}

    def _device(self) -> Optional[Dict[str, Any]]:
        i = self.cmb_dev.currentIndex()
        return self._devices[i] if 0 <= i < len(self._devices) else None

    def _load_device(self) -> None:
        d = self._device()
        if not d:
            return
        self.chk_pii.setChecked(bool(d.get("view_pii")))
        if d.get("matrix"):
            self._show(d["matrix"])
            self.cmb_prof.setCurrentIndex(max(0, self.cmb_prof.findData(d.get("profile") or "")))
        else:       # telefon încă pe rol/registre vechi: pornește de la „Doar consultare" (sigur), nu se aplică până la „Salvează"
            self._show(self._meta["profiles"]["doar_consultare"]["matrix"])

    def _apply_profile(self) -> None:
        k = self.cmb_prof.currentData()
        if k:
            self._show(self._meta["profiles"][k]["matrix"])

    def _guard(self, fn) -> bool:
        try:
            fn()
            return True
        except (cloud_sync.RetryLater, cloud_sync.Rejected) as exc:
            QMessageBox.warning(self, "Permisiuni Mobile", f"Nu s-a salvat: {getattr(exc, 'detail', exc)}")
            return False

    def _save(self) -> None:
        d = self._device()
        if not d:
            QMessageBox.information(self, "Permisiuni Mobile", "Nu există telefoane asociate.")
            return
        if self._guard(lambda: sync_v2.set_device_matrix(d["ref"], matrix=self.matrix(), view_pii=self.chk_pii.isChecked(), profile=self.cmb_prof.currentData())):
            QMessageBox.information(self, "Permisiuni Mobile", "Permisiunile au fost salvate în cloud și se aplică imediat pe server.")
            self.reload()

    def _save_profile(self) -> None:
        label, ok = QInputDialog.getText(self, "Profil nou / personalizat", "Denumirea profilului (un nume existent îl personalizează):")
        if ok and label.strip():
            key = next((k for k, p in self._meta["profiles"].items() if p["label"].lower() == label.strip().lower()), slug(label))
            if self._guard(lambda: sync_v2.save_permission_profile(key, label.strip(), self.matrix())):
                self.reload()


class AuditPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Data", "Telefon", "Eveniment", "Registru", "Detalii"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table, 1)

    def reload(self) -> None:
        ev = sync_v2.audit_log(200)
        self.table.setRowCount(len(ev))
        for r, e in enumerate(ev):
            d = e.get("detail") or {}
            det = d.get("reason") or d.get("detail") or d.get("profile") or d.get("number") or ""
            for i, v in enumerate((str(e["created_at"])[:19].replace("T", " "), e.get("device") or e["device_type"], AUDIT_LABELS.get(e["kind"], e["kind"]), e.get("entity") or "", str(det))):
                self.table.setItem(r, i, QTableWidgetItem(v))
