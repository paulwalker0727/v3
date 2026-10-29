"""portable_tab.py — fila „Backup portabil”: export registru → arhivă verificată; import cu previzualizare, confirmare explicită și raport. Nu sincronizează nimic automat."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtWidgets import QCheckBox, QComboBox, QFileDialog, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout, QWidget

import cimitir_portable as pt
import cimitir_registry as reg
import field_inbox_client as fic
from field_worker import run_task


def _wrap(fn):
    def w():
        try:
            return fn()
        except pt.PortableError as exc:
            raise fic.FieldCloudError(str(exc)) from None
        except reg.RegistryError as exc:
            raise fic.FieldCloudError(str(exc)) from None
    return w


class PortableTab(QWidget):
    def __init__(self, catalog: reg.RegistryCatalog, can: Callable[[], bool], parent=None, pick_save: Optional[Callable[[], str]] = None, pick_open: Optional[Callable[[], str]] = None):
        super().__init__(parent)
        self._cat, self._can = catalog, can
        self._pick_save = pick_save or (lambda: QFileDialog.getSaveFileName(self, "Salvează arhiva portabilă", "", f"Arhivă portabilă (*{pt.EXT})")[0])
        self._pick_open = pick_open or (lambda: QFileDialog.getOpenFileName(self, "Alege arhiva portabilă", "", f"Arhivă portabilă (*{pt.EXT});;Toate (*)")[0])
        self._archive: Optional[str] = None
        self._preview: Optional[dict] = None
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Export: registrul selectat → arhivă verificată (fără parole/chei/legătură Cloud). Import: previzualizare → confirmare → restaurare cu rollback."))
        self.cmb = QComboBox()
        self.cmb.setObjectName("portable_registry")
        for c in self._cat.list_cemeteries():
            self.cmb.addItem(f"{(self._cat.get_organization(c['organization_uid']) or {}).get('display_name', '?')} — {c['name']}", c["uid"])
        self.btn_export = QPushButton("Exportă registrul selectat…")
        self.btn_export.setObjectName("portable_export")
        self.btn_open = QPushButton("Alege arhivă pentru import și previzualizează…")
        self.btn_open.setObjectName("portable_open")
        self.chk_dest = QCheckBox("Confirm organizația și cimitirul destinație afișate mai sus")
        self.chk_dest.setObjectName("portable_confirm_dest")
        self.chk_warn = QCheckBox("Accept avertismentele din previzualizare")
        self.chk_warn.setObjectName("portable_accept_warnings")
        self.btn_import = QPushButton("Importă (nu suprascrie nimic)")
        self.btn_import.setObjectName("portable_import")
        self.btn_import.setEnabled(False)
        self.chk_dest.toggled.connect(self._sync)
        self.chk_warn.toggled.connect(self._sync)
        self.bar = QProgressBar()
        self.bar.setObjectName("portable_progress")
        self.out = QPlainTextEdit()
        self.out.setObjectName("portable_out")
        self.out.setReadOnly(True)
        for w in (self.cmb, self.btn_export, self.btn_open, self.chk_dest, self.chk_warn, self.btn_import, self.bar, self.out):
            lay.addWidget(w)
        self.btn_export.clicked.connect(self._export)
        self.btn_open.clicked.connect(self._open)
        self.btn_import.clicked.connect(self._import)

    def _sync(self, *_a) -> None:
        pv = self._preview
        ok = bool(pv and pv["can_import"] and self.chk_dest.isChecked() and (self.chk_warn.isChecked() or not pv["warnings"]))
        self.btn_import.setEnabled(ok)

    def _bg(self, fn: Callable[[], Any], ok: Callable[[Any], None]) -> None:
        if not self._can():
            return
        run_task(self, _wrap(fn), ok, lambda m, _nc: self.out.setPlainText(m))

    def _export(self) -> None:
        uid, dest = self.cmb.currentData(), self._pick_save()
        if not uid or not dest:
            return
        if not dest.endswith(pt.EXT):
            dest += pt.EXT

        def work():
            b = self._cat.binding_for(uid)
            return pt.export_registry(b, dest, self._cat.get_organization(b.organization_uid) or {}, self._cat.get_cemetery(uid) or {})

        def ok(r: dict) -> None:
            self.out.setPlainText(f"Arhivă creată și verificată: {r['path']}\n{r['bytes']} octeți · sha256 {r['sha256'][:16]}…\nÎnregistrări: {json.dumps(r['entities'], ensure_ascii=False)}\n"
                                  f"Fișiere media: {r['media']}\nAvertismente: {len(r['warnings'])}" + "".join(f"\n - {w}" for w in r["warnings"]))
        self._bg(work, ok)

    def _open(self) -> None:
        path = self._pick_open()
        if not path:
            return
        self._archive, self._preview = path, None
        self.chk_dest.setChecked(False)
        self.chk_warn.setChecked(False)

        def ok(pv: dict) -> None:
            self._preview = pv
            o, c = pv["organization"], pv["cemetery"]
            self.out.setPlainText(f"Destinație: organizația „{o['official_name']}” ({o['uid']}) · cimitirul „{c['name']}” ({c['uid']})\nArhivă din {pv['created_at']}\n"
                                  f"Înregistrări: {json.dumps(pv['entities'], ensure_ascii=False)}\nFișiere media: {pv['media']['count']}\n"
                                  + ("".join(f"\nBLOCAT: {b}" for b in pv["blockers"]) or "\nNiciun blocaj.") + "".join(f"\nAvertisment: {w}" for w in pv["warnings"])
                                  + "\n\nRegistrul rămâne neasociat cu Cloud-ul; încărcarea se face separat, din „Asociază acest calculator”.")
            self._sync()
        self._bg(lambda: pt.preview(path, self._cat), ok)

    def _import(self) -> None:
        pv, path = self._preview, self._archive
        if not (pv and path):
            return
        self.btn_import.setEnabled(False)
        warn = self.chk_warn.isChecked()

        def ok(r: dict) -> None:
            self.out.setPlainText("Import finalizat și verificat.\n" + json.dumps({k: r[k] for k in ("organization_uid", "cemetery_uid", "entities", "media_files", "media_problems", "sync_state")},
                                                                                   ensure_ascii=False, indent=1) + "\n\n" + r["next_step"])
        self._bg(lambda: pt.import_archive(path, self._cat, pv["organization"]["uid"], pv["cemetery"]["uid"], accept_warnings=warn,
                                           progress=lambda s, i, n: self.bar.setValue(int(100 * i / max(n, 1)))), ok)
