"""
cereri_online_window.py — Fereastra „Cereri online" (revizuire + validare).

Afișează cererile de concesionare primite din portalul public web
(tabelul `cereri_concesionare` din aceeași bază `cimitir.db`). Operatorul poate:
    - vedea toate câmpurile completate de solicitant + documentele scanate atașate
    - deschide documentele scanate ÎNAINTE de validare (viewer implicit al OS-ului)
    - VALIDA cererea → creează automat concesionarul (`save_concesionar`) și,
      dacă locul solicitat există, creează și un CONTRACT DRAFT legat de loc
    - RESPINGE cererea (cu motiv opțional)

Nu introduce nimic manual de două ori: datele completate online intră direct în program.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Optional, Dict, Any, List

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QComboBox,
    QTableWidget, QTableWidgetItem, QSplitter, QWidget, QScrollArea,
    QGroupBox, QFormLayout, QMessageBox, QInputDialog, QHeaderView, QFrame,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDesktopServices, QColor
from PySide6.QtCore import QUrl

import database as db

FIELD_LABELS = [
    ("nume", "Nume"),
    ("prenume", "Prenume"),
    ("cnp", "CNP"),
    ("bi_ci", "Serie / nr. CI"),
    ("adresa", "Adresă"),
    ("telefon", "Telefon"),
    ("email", "Email"),
    ("grad_rudenie", "Grad de rudenie"),
    ("cod_loc", "Loc de veci solicitat"),
    ("observatii", "Observații"),
]

STATUS_LABELS = {"nou": "Nou", "completari": "Completări cerute", "validat": "Validat", "respins": "Respins"}
STATUS_COLORS = {"nou": "#f59e0b", "completari": "#2563eb", "validat": "#16a34a", "respins": "#dc2626"}

# statusul locului de veci (disponibilitate)
LOC_LABELS = {"liber": "Liber", "ocupat": "Ocupat", "rezervat": "Rezervat"}
LOC_COLORS = {"liber": "#16a34a", "ocupat": "#dc2626", "rezervat": "#f59e0b"}


def _loc_status(loc: Dict[str, Any]) -> str:
    disp = str(loc.get("disponibilitate") or "").lower()
    stare = str(loc.get("stare") or "").lower()
    ocupat = loc.get("ocupat")
    if "rezerv" in disp or "rezerv" in stare:
        return "rezervat"
    if ocupat in (1, "1", True) or "ocupat" in disp or "ocupat" in stare or "indisponibil" in disp:
        return "ocupat"
    return "liber"


class CereriOnlineWindow(QDialog):
    """Fereastra de revizuire a cererilor de concesionare venite din portalul public."""

    changed = Signal()  # emis când o cerere își schimbă statusul (pentru badge + hartă)

    def __init__(self, parent=None, actions: Optional[Any] = None):
        super().__init__(parent)
        self.actions = actions
        self.setWindowTitle("Cereri de concesionare online")
        self.setMinimumSize(1000, 620)
        self._current_id: Optional[int] = None

        root = QVBoxLayout(self)

        # ---- bară sus: filtru + refresh ----
        top = QHBoxLayout()
        top.addWidget(QLabel("Filtru:"))
        self.cmb_filter = QComboBox()
        self.cmb_filter.addItem("Doar noi", "nou")
        self.cmb_filter.addItem("Toate", "")
        self.cmb_filter.addItem("Validate", "validat")
        self.cmb_filter.addItem("Respinse", "respins")
        self.cmb_filter.currentIndexChanged.connect(self._load_list)
        top.addWidget(self.cmb_filter)

        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet("color:#667085;")
        top.addWidget(self.lbl_count)
        top.addStretch(1)

        btn_refresh = QPushButton("🔄 Reîmprospătează")
        btn_refresh.clicked.connect(self._load_list)
        top.addWidget(btn_refresh)
        root.addLayout(top)

        # ---- splitter: listă | detaliu ----
        splitter = QSplitter(Qt.Horizontal)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["#", "Data", "Solicitant", "Loc", "Status"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._on_select)
        splitter.addWidget(self.table)

        # panoul de detaliu (scroll)
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_host = QWidget()
        self.detail_layout = QVBoxLayout(self.detail_host)
        self.detail_layout.setAlignment(Qt.AlignTop)
        self.detail_scroll.setWidget(self.detail_host)
        splitter.addWidget(self.detail_scroll)
        splitter.setSizes([420, 580])
        root.addWidget(splitter, 1)

        self._show_placeholder()
        self._load_list()

    # ------------------------------------------------------------------ listă
    def _load_list(self):
        status = self.cmb_filter.currentData()
        try:
            rows = db.list_cereri_concesionare(status or None)
        except Exception as e:
            rows = []
            QMessageBox.critical(self, "Eroare", f"Nu am putut citi cererile: {e}")

        self.table.setRowCount(0)
        for r in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            nume = f"{r.get('nume', '')} {r.get('prenume', '')}".strip()
            st = r.get("status") or "nou"
            cells = [str(r.get("id")), r.get("created_at") or "", nume,
                     r.get("cod_loc") or "—", STATUS_LABELS.get(st, st)]
            for col, val in enumerate(cells):
                item = QTableWidgetItem(val)
                if col == 4:
                    item.setForeground(QColor(STATUS_COLORS.get(st, "#334155")))
                self.table.setItem(row, col, item)
            self.table.item(row, 0).setData(Qt.UserRole, r.get("id"))

        try:
            noi = db.count_cereri_noi()
        except Exception:
            noi = 0
        self.lbl_count.setText(f"{self.table.rowCount()} afișate · {noi} noi în total")
        self._show_placeholder()

    # ---------------------------------------------------------------- selecție
    def _selected_id(self) -> Optional[int]:
        items = self.table.selectedItems()
        if not items:
            return None
        return self.table.item(items[0].row(), 0).data(Qt.UserRole)

    def _on_select(self):
        cid = self._selected_id()
        if cid is None:
            return
        self._current_id = cid
        cerere = db.get_cerere_concesionare(cid)
        if cerere:
            self._show_detail(cerere)

    # ---------------------------------------------------------------- detaliu
    def _clear_detail(self):
        while self.detail_layout.count():
            item = self.detail_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

    def _show_placeholder(self):
        self._clear_detail()
        lbl = QLabel("Selectează o cerere din listă pentru a-i vedea detaliile.")
        lbl.setStyleSheet("color:#94a3b8; padding:20px;")
        lbl.setAlignment(Qt.AlignCenter)
        self.detail_layout.addWidget(lbl)

    def _show_detail(self, cerere: Dict[str, Any]):
        self._clear_detail()

        st = cerere.get("status") or "nou"
        header = QLabel(
            f"Cerere #{cerere.get('id')} — "
            f"<span style='color:{STATUS_COLORS.get(st, '#334155')}'>"
            f"{STATUS_LABELS.get(st, st)}</span>"
        )
        header.setTextFormat(Qt.RichText)
        header.setStyleSheet("font-size:18px; font-weight:700; color:#0e5aa7;")
        self.detail_layout.addWidget(header)

        sub = QLabel(f"Primită: {cerere.get('created_at') or '—'}")
        sub.setStyleSheet("color:#667085;")
        self.detail_layout.addWidget(sub)

        # câmpuri
        box = QGroupBox("Date completate de solicitant")
        form = QFormLayout(box)
        for key, label in FIELD_LABELS:
            val = cerere.get(key)
            if val:
                lv = QLabel(str(val))
                lv.setWordWrap(True)
                lv.setTextInteractionFlags(Qt.TextSelectableByMouse)
                form.addRow(QLabel(f"{label}:"), lv)
        self.detail_layout.addWidget(box)

        # disponibilitatea locului solicitat + buton „vezi pe hartă"
        cod_loc = (cerere.get("cod_loc") or "").strip()
        if cod_loc:
            av = self._loc_availability(cod_loc)
            loc_box = QGroupBox(f"Locul solicitat: {cod_loc}")
            loc_layout = QVBoxLayout(loc_box)
            if not av["exists"]:
                warn = QLabel(f"⚠️ Locul „{cod_loc}” NU există în evidența cimitirului.")
                warn.setStyleSheet("color:#b45309; font-weight:600;")
                warn.setWordWrap(True)
                loc_layout.addWidget(warn)
            else:
                pill = QLabel(
                    f"Disponibilitate: <b style='color:{LOC_COLORS.get(av['status'], '#334155')}'>"
                    f"{av['status_label']}</b>"
                )
                pill.setTextFormat(Qt.RichText)
                loc_layout.addWidget(pill)
                bits = []
                if av["disponibilitate"]:
                    bits.append(f"stare declarată: {av['disponibilitate']}")
                if av["stare"]:
                    bits.append(f"stare: {av['stare']}")
                bits.append("are contract" if av["has_contract"] else "fără contract")
                sub = QLabel(" · ".join(bits))
                sub.setStyleSheet("color:#667085;")
                sub.setWordWrap(True)
                loc_layout.addWidget(sub)

                btn_map = QPushButton("🗺 Vezi locul pe hartă")
                btn_map.setToolTip("Centrează și evidențiază locul pe harta din fereastra principală")
                btn_map.clicked.connect(lambda _=False, c=cod_loc: self._show_on_map(c))
                if not (self.parent() and hasattr(self.parent(), "features")):
                    btn_map.setEnabled(False)
                    btn_map.setToolTip("Disponibil doar din fereastra principală a aplicației.")
                loc_layout.addWidget(btn_map)
            self.detail_layout.addWidget(loc_box)

        # documente
        docs = db.list_documents("cerere", cerere.get("id"))
        doc_box = QGroupBox(f"Documente scanate atașate ({len(docs)})")
        doc_layout = QVBoxLayout(doc_box)
        if not docs:
            doc_layout.addWidget(QLabel("Nu au fost atașate documente."))
        else:
            for d in docs:
                path = d.get("file_path") or ""
                name = d.get("caption") or os.path.basename(path) or f"document {d.get('id')}"
                btn = QPushButton(f"📎 {name}")
                exists = bool(path) and os.path.exists(path)
                btn.setEnabled(exists)
                if not exists:
                    btn.setToolTip("Fișierul nu a fost găsit pe disc.")
                btn.clicked.connect(lambda _=False, p=path: self._open_doc(p))
                doc_layout.addWidget(btn)
        self.detail_layout.addWidget(doc_box)

        # separator
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color:#e2e8f0;")
        self.detail_layout.addWidget(line)

        # acțiuni
        actions = QHBoxLayout()
        if st == "nou":
            btn_val = QPushButton("✅ Validează → adaugă în concesionar")
            btn_val.setStyleSheet("background:#16a34a; color:#fff; font-weight:700; padding:8px 14px;")
            btn_val.clicked.connect(lambda: self._validate(cerere.get("id")))
            actions.addWidget(btn_val)

            btn_more = QPushButton("📝 Solicită completări")
            btn_more.setStyleSheet("background:#2563eb; color:#fff; font-weight:700; padding:8px 14px;")
            btn_more.clicked.connect(lambda: self._request_more(cerere.get("id")))
            actions.addWidget(btn_more)

            btn_rej = QPushButton("❌ Respinge")
            btn_rej.setStyleSheet("background:#dc2626; color:#fff; font-weight:700; padding:8px 14px;")
            btn_rej.clicked.connect(lambda: self._reject(cerere.get("id")))
            actions.addWidget(btn_rej)
        else:
            info = QLabel("Această cerere a fost deja procesată.")
            info.setStyleSheet("color:#667085;")
            actions.addWidget(info)
            if cerere.get("review_note"):
                self.detail_layout.addWidget(QLabel(f"Notă: {cerere.get('review_note')}"))
        actions.addStretch(1)
        wrap = QWidget()
        wrap.setLayout(actions)
        self.detail_layout.addWidget(wrap)

    def _open_doc(self, path: str):
        if path and os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            QMessageBox.warning(self, "Fișier lipsă", "Documentul nu a fost găsit pe disc.")

    def _loc_availability(self, cod_loc: str) -> Dict[str, Any]:
        """Info despre disponibilitatea locului solicitat (pentru verificare rapidă)."""
        loc = db.load_loc_by_cod(cod_loc)
        if not loc:
            return {"exists": False}
        st = _loc_status(loc)
        has_contract = False
        try:
            has_contract = any((c.get("cod_loc") == cod_loc) for c in db.load_contracte())
        except Exception:
            has_contract = False
        return {
            "exists": True,
            "status": st,
            "status_label": LOC_LABELS.get(st, st),
            "disponibilitate": loc.get("disponibilitate") or "",
            "stare": loc.get("stare") or "",
            "ocupat": loc.get("ocupat"),
            "has_contract": has_contract,
        }

    def _show_on_map(self, cod_loc: str):
        """Deschide fișa locului pe harta din fereastra principală (centrează + evidențiază)."""
        parent = self.parent()
        if not (parent and hasattr(parent, "features")):
            QMessageBox.information(self, "Hartă indisponibilă",
                                    "Harta este disponibilă doar din fereastra principală.")
            return
        try:
            parent.features.search_plot(cod_loc)
            if hasattr(parent, "raise_"):
                parent.raise_()
                parent.activateWindow()
        except Exception as e:
            QMessageBox.warning(self, "Eroare", f"Nu am putut afișa locul pe hartă: {e}")
            return
        # închide fereastra ca harta evidențiată să fie vizibilă
        self.accept()

    # ---------------------------------------------------------------- validare
    def _validate(self, cerere_id: int):
        import access_session
        if not access_session.require("approve", self, "aprobarea cererilor"):
            return
        cerere = db.get_cerere_concesionare(cerere_id)
        if not cerere:
            return
        nume = f"{cerere.get('nume', '')} {cerere.get('prenume', '')}".strip()
        if not QMessageBox.question(
            self, "Confirmă validarea",
            f"Adaugi în concesionari solicitantul:\n\n{nume}\nCNP: {cerere.get('cnp') or '—'}\n\n"
            f"Loc solicitat: {cerere.get('cod_loc') or '—'}\n\nContinui?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        ) == QMessageBox.Yes:
            return

        # 1) anti-dubluri: verifică dacă persoana există deja ca concesionar
        cnp = (cerere.get("cnp") or "").strip()
        existing = None
        for c in db.load_concesionari():
            if cnp and (c.get("cnp") or "").strip() == cnp:
                existing = c
                break
            if not cnp and (c.get("nume") or "").strip().lower() == (cerere.get("nume") or "").strip().lower() \
                    and (c.get("prenume") or "").strip().lower() == (cerere.get("prenume") or "").strip().lower():
                existing = c
                break

        cid = None
        if existing:
            box = QMessageBox(self)
            box.setWindowTitle("Concesionar existent")
            box.setIcon(QMessageBox.Warning)
            box.setText(
                f"Această persoană există deja în evidență:\n\n"
                f"#{existing['id']} — {existing.get('nume', '')} {existing.get('prenume', '')}\n"
                f"CNP: {existing.get('cnp') or '—'}\n"
                f"Telefon: {existing.get('telefon') or '—'}\n\n"
                "Ce vrei să faci?"
            )
            btn_link = box.addButton("Leagă cererea la persoana existentă", QMessageBox.AcceptRole)
            btn_new = box.addButton("Creează totuși un concesionar nou", QMessageBox.DestructiveRole)
            box.addButton("Anulează", QMessageBox.RejectRole)
            box.setDefaultButton(btn_link)
            box.exec()
            clicked = box.clickedButton()
            if clicked is btn_link:
                cid = existing["id"]
            elif clicked is not btn_new:
                return

        # 2) aprobare tranzacțională: re-verifică disponibilitatea, creează concesionar + contract draft, rezervă locul
        import cimitir_requests
        try:
            result = cimitir_requests.approve_cerere(cerere_id, concesionar_id=cid, actor=(access_session.current_user() or {}).get("username", "MAIN"))
        except cimitir_requests.ApprovalError as exc:
            QMessageBox.warning(self, "Aprobare imposibilă", str(exc))
            return
        cid, contract_id = result["concesionar_id"], result["contract_id"]
        contract_msg = f"\nContract DRAFT #{contract_id} creat și legat de locul {cerere.get('cod_loc')}." if contract_id else ""
        if result["already"]:
            contract_msg = "\n(Cererea era deja aprobată; nu s-a creat nimic nou.)"
        copied = 0
        for d in ([] if result["already"] else db.list_documents("cerere", cerere_id)):
            p = d.get("file_path")
            if not p or not os.path.exists(p):
                continue
            try:
                db.add_document("concesionar", cid, "buletin", p, caption=d.get("caption") or "", copy=True)
                if contract_id:
                    db.add_document("contract", contract_id, "contract_scan", p, caption=d.get("caption") or "", copy=True)
                copied += 1
            except Exception:
                pass
        docs_msg = f"\n{copied} document(e) copiate în dosar." if copied else ""
        self._emit_data_changed()
        self.changed.emit()
        self._load_list()
        QMessageBox.information(
            self, "Cerere validată",
            f"Concesionar #{cid} din cererea online #{cerere_id}.{contract_msg}{docs_msg}"
        )

    def _request_more(self, cerere_id: int):
        import access_session
        if not access_session.require("approve", self, "solicitarea de completări"):
            return
        note, ok = QInputDialog.getText(self, "Solicită completări", "Ce trebuie completat (vizibil cetățeanului):")
        if not ok or not note.strip():
            return
        db.set_cerere_status(cerere_id, "completari", note.strip(), None)
        self._load_list()

    def _reject(self, cerere_id: int):
        import access_session
        if not access_session.require("approve", self, "respingerea cererilor"):
            return
        note, ok = QInputDialog.getText(self, "Respinge cererea", "Motiv (obligatoriu, vizibil cetățeanului):")
        if not ok or not (note or "").strip():
            return
        db.set_cerere_status(cerere_id, "respins", note or "")
        self.changed.emit()
        self._load_list()

    def _emit_data_changed(self):
        try:
            parent = self.parent()
            if parent and hasattr(parent, "signals") and hasattr(parent.signals, "data_changed"):
                parent.signals.data_changed.emit()
        except Exception:
            pass
