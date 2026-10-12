# operator_detail_panel.py
"""
Panou de detalii bogat și organizat pentru modul Operator (user).

Afișează, pentru locul de veci selectat pe hartă, secțiuni pliabile:
    - Loc de veci (info + fotografii ale locului)
    - Concesionar (click → se extinde cu detalii + acces contracte)
    - Înhumați (fiecare cu numere de evidență + poze documente:
      certificat de deces, adeverință de înhumare)
    - Lucrări efectuate / Avize (avize eliberate + fotografii cu lucrările)
    - Plăți

Toate documentele/pozele sunt gestionate prin tabela `documents` din database.py
(copiate într-un folder `media/` gestionat).
"""

import os
from typing import List, Dict, Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QFont, QPixmap, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QFrame, QFormLayout, QFileDialog, QMessageBox, QInputDialog, QSizePolicy,
)

import database as db
import work_details as wd

try:
    from panels import PhotoLightbox
except Exception:
    PhotoLightbox = None


# =============================================================================
# SECȚIUNE PLIABILĂ
# =============================================================================
class CollapsibleSection(QWidget):
    def __init__(self, title: str, palette: dict, expanded: bool = False, accent: Optional[str] = None):
        super().__init__()
        self._pal = palette
        self._title = title
        accent = accent or palette["primary"]

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.header = QPushButton()
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.setStyleSheet(
            f"""
            QPushButton {{ text-align: left; padding: 10px 12px; border: none;
                background-color: {palette['bg_hover']}; color: {palette['text']};
                border-left: 3px solid {accent}; border-radius: 6px; font-weight: 600; font-size: 10.5pt; }}
            QPushButton:hover {{ background-color: {accent}; color: white; }}
            QPushButton:checked {{ background-color: {palette['bg_hover']}; color: {palette['text']};
                border-left: 3px solid {accent}; }}
            """
        )
        self.header.toggled.connect(self._on_toggle)
        lay.addWidget(self.header)

        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(10, 10, 10, 12)
        self.body_lay.setSpacing(8)
        lay.addWidget(self.body)

        self.body.setVisible(expanded)
        self._update_header()

    def _on_toggle(self, checked: bool):
        self.body.setVisible(checked)
        self._update_header()

    def _update_header(self):
        arrow = "▾" if self.header.isChecked() else "▸"
        self.header.setText(f"  {arrow}   {self._title}")

    def set_title(self, title: str):
        self._title = title
        self._update_header()

    def add(self, widget: QWidget):
        self.body_lay.addWidget(widget)

    def add_layout(self, layout):
        self.body_lay.addLayout(layout)


# =============================================================================
# THUMBNAIL DOCUMENT
# =============================================================================
class _Thumb(QFrame):
    def __init__(self, doc: Dict, palette: dict, on_delete, on_view):
        super().__init__()
        self._pal = palette
        self.doc = doc
        self.setFixedSize(96, 116)
        self.setStyleSheet(
            f"background-color: {palette['bg']}; border: 1px solid {palette['border']}; border-radius: 8px;"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)

        img = QLabel()
        img.setFixedSize(88, 78)
        img.setAlignment(Qt.AlignCenter)
        img.setCursor(Qt.PointingHandCursor)
        path = doc.get("file_path", "")
        ext = os.path.splitext(path)[1].lower()
        if ext == ".pdf":
            img.setText("📄 PDF")
            img.setStyleSheet("color:#fff; background:#b91c1c; border:none; border-radius:5px; font-weight:700;")
            img.mousePressEvent = lambda ev: QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            pix = QPixmap(path) if path and os.path.exists(path) else QPixmap()
            if pix.isNull():
                img.setText("N/A")
                img.setStyleSheet(f"color: {palette['text_muted']}; background: transparent; border: none;")
            else:
                img.setPixmap(pix.scaled(88, 78, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                img.setStyleSheet("background: transparent; border: none;")
            img.mousePressEvent = lambda ev: on_view(doc)
        lay.addWidget(img)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        cap = doc.get("nr_evidenta") or doc.get("caption") or ""
        lbl = QLabel(cap[:12] if cap else "")
        lbl.setStyleSheet(f"color: {palette['text_muted']}; background: transparent; border: none; font-size: 8pt;")
        bottom.addWidget(lbl, 1)
        btn_del = QPushButton("✕")
        btn_del.setFixedSize(18, 18)
        btn_del.setCursor(Qt.PointingHandCursor)
        btn_del.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {palette['text_muted']}; border: none; font-size: 9pt; }}"
            f"QPushButton:hover {{ color: {palette['danger']}; }}"
        )
        btn_del.clicked.connect(lambda: on_delete(doc))
        bottom.addWidget(btn_del)
        lay.addLayout(bottom)


# =============================================================================
# BANDĂ FOTOGRAFII
# =============================================================================
class PhotoStrip(QWidget):
    def __init__(self, palette: dict, on_add, on_delete, add_text: str = "➕ Adaugă"):
        super().__init__()
        self._pal = palette
        self._on_add = on_add
        self._on_delete = on_delete
        self._add_text = add_text

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self.row = QHBoxLayout()
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(6)
        holder = QWidget()
        holder.setLayout(self.row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(132)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(holder)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        root.addWidget(scroll)

        btn = QPushButton(add_text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {palette['primary']};"
            f" border: 1px dashed {palette['primary']}; border-radius: 6px; padding: 5px; font-size: 9.5pt; }}"
            f"QPushButton:hover {{ background-color: {palette['bg_hover']}; }}"
        )
        btn.clicked.connect(lambda: self._on_add())
        root.addWidget(btn)

    def set_docs(self, docs: List[Dict]):
        while self.row.count():
            it = self.row.takeAt(0)
            w = it.widget()
            if w:
                w.setParent(None)
        if not docs:
            empty = QLabel("niciun document")
            empty.setStyleSheet(f"color: {self._pal['text_muted']}; background: transparent; border: none; font-size: 9pt;")
            self.row.addWidget(empty)
        else:
            for d in docs:
                self.row.addWidget(_Thumb(d, self._pal, self._on_delete, self._view))
        self.row.addStretch()

    def _view(self, doc: Dict):
        path = doc.get("file_path", "")
        if not path or not os.path.exists(path):
            QMessageBox.information(self, "Info", "Fișierul imaginii nu a fost găsit.")
            return
        if PhotoLightbox:
            dlg = PhotoLightbox(self, photos=[{"full_path": path, "caption": doc.get("caption", "")}], start_index=0)
            dlg.exec()


# =============================================================================
# PANOU PRINCIPAL OPERATOR
# =============================================================================
class OperatorDetailPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self._pal = window._pal
        self.current_cod_loc: Optional[str] = None

        self.setFixedWidth(440)
        self.setStyleSheet(
            f"background-color: {self._pal['bg_panel']}; border-left: 1px solid {self._pal['border']};"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # header fix
        head = QWidget()
        head.setStyleSheet(f"background-color: {self._pal['bg_panel']}; border-bottom: 1px solid {self._pal['border']};")
        hl = QVBoxLayout(head)
        hl.setContentsMargins(18, 16, 18, 12)
        hl.setSpacing(2)
        self.lbl_cod = QLabel("Selectează un loc pe hartă")
        hf = QFont(); hf.setPointSize(16); hf.setBold(True); self.lbl_cod.setFont(hf)
        self.lbl_cod.setStyleSheet(f"color: {self._pal['text']}; background: transparent; border: none;")
        self.lbl_cod.setWordWrap(True)
        self.lbl_sub = QLabel("")
        self.lbl_sub.setStyleSheet(f"color: {self._pal['text_muted']}; background: transparent; border: none; font-size: 10pt;")
        hl.addWidget(self.lbl_cod)
        hl.addWidget(self.lbl_sub)
        self.btn_fisa = QPushButton("📄  Generează fișă PDF")
        self.btn_fisa.setCursor(Qt.PointingHandCursor)
        self.btn_fisa.setMinimumHeight(34)
        self.btn_fisa.setStyleSheet(
            "QPushButton { background:#b91c1c; color:#fff; border:none; border-radius:7px;"
            " padding:6px; font-weight:600; margin-top:6px; }"
            "QPushButton:disabled { background:#3a2626; color:#8a6b6b; }")
        self.btn_fisa.setEnabled(False)
        self.btn_fisa.clicked.connect(self._generate_fisa)
        hl.addWidget(self.btn_fisa)
        root.addWidget(head)

        # zonă scrollabilă cu secțiuni
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.container = QWidget()
        self.cl = QVBoxLayout(self.container)
        self.cl.setContentsMargins(14, 14, 14, 18)
        self.cl.setSpacing(10)
        self.scroll.setWidget(self.container)
        root.addWidget(self.scroll, 1)

        self._show_placeholder()

    # -------------------------------------------------------------------------
    def _generate_fisa(self):
        if not self.current_cod_loc:
            return
        try:
            import fisa_pdf
            import tempfile
            pdf = fisa_pdf.build_fisa_pdf(self.current_cod_loc)
            path = os.path.join(tempfile.gettempdir(), f"fisa_{self.current_cod_loc}.pdf")
            with open(path, "wb") as fh:
                fh.write(pdf)
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except Exception as e:
            QMessageBox.warning(self, "Eroare", f"Nu am putut genera fișa PDF:\n{e}")

    def _clear_container(self):
        while self.cl.count():
            it = self.cl.takeAt(0)
            w = it.widget()
            if w:
                w.setParent(None)

    def _show_placeholder(self):
        self._clear_container()
        ph = QLabel("Fă click pe un loc de veci de pe hartă pentru a vedea fișa completă:\n\n"
                    "• informații & fotografii loc\n• concesionar\n• înhumați & documente\n"
                    "• lucrări efectuate / avize\n• plăți")
        ph.setWordWrap(True)
        ph.setStyleSheet(f"color: {self._pal['text_muted']}; background: transparent; border: none; font-size: 10pt;")
        self.cl.addWidget(ph)
        self.cl.addStretch()

    def _btn(self, text: str, accent: str, cb) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.setMinimumHeight(36)
        b.setStyleSheet(
            f"QPushButton {{ background-color: {accent}; color: white; border: none;"
            f" border-radius: 7px; font-weight: 600; font-size: 10pt; padding: 0 12px; text-align: left; }}"
        )
        b.clicked.connect(cb)
        return b

    def _info_row(self, label: str, value: str) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
        lb = QLabel(label)
        lb.setFixedWidth(120)
        lb.setStyleSheet(f"color: {self._pal['text_muted']}; background: transparent; border: none; font-size: 9.5pt;")
        vl = QLabel(value or "—")
        vl.setWordWrap(True)
        vl.setStyleSheet(f"color: {self._pal['text']}; background: transparent; border: none; font-size: 9.5pt; font-weight: 600;")
        h.addWidget(lb); h.addWidget(vl, 1)
        return w

    # =========================================================================
    # RANDARE FIȘĂ
    # =========================================================================
    def show_loc(self, cod_loc: str):
        self.current_cod_loc = cod_loc
        self.btn_fisa.setEnabled(True)
        loc = db.load_loc_by_cod(cod_loc) or {}
        self.lbl_cod.setText(f"📍  {cod_loc}")
        self.lbl_sub.setText(
            f"Stare: {loc.get('stare','') or '—'}   |   Capacitate: {loc.get('capacitate','') or '—'}"
        )
        self._clear_container()
        self._build_loc_section(cod_loc, loc)
        self._build_concesionar_section(cod_loc)
        self._build_inhumati_section(cod_loc)
        self._build_lucrari_section(cod_loc)
        self._build_plati_section(cod_loc)
        self.cl.addStretch()

    # ---- LOC ----
    def _build_loc_section(self, cod_loc: str, loc: Dict):
        sec = CollapsibleSection("Loc de veci", self._pal, expanded=True, accent=self._pal["primary"])
        for lbl, key in [("Sector", "sector"), ("Parcela", "parcela"), ("Rând", "rand"),
                         ("Număr", "numar"), ("Tip", "tip"), ("Categorie", "categorie"),
                         ("Disponibilitate", "disponibilitate")]:
            sec.add(self._info_row(lbl, str(loc.get(key, "") or "")))
        sec.add(self._info_row("Suprafață", f"{loc.get('suprafata','') or '—'} m²"))

        strip = PhotoStrip(
            self._pal,
            on_add=lambda: self._add_loc_photos(cod_loc),
            on_delete=lambda d: self._delete_doc(d, cod_loc),
            add_text="➕  Adaugă foto / PDF loc",
        )
        strip.set_docs(self._loc_photos(cod_loc))
        lbl_ph = QLabel("Fotografii / documente loc (imagine/PDF)")
        lbl_ph.setStyleSheet(f"color: {self._pal['accent']}; background: transparent; border: none; font-weight: 600; margin-top: 4px;")
        sec.add(lbl_ph)
        sec.add(strip)
        sec.add(self._phone_btn("loc", cod_loc, f"Loc {cod_loc}"))
        self.cl.addWidget(sec)

    def _phone_btn(self, etype: str, eid, label: str, refresh=None):
        """Pasul 6: „Continuă pe telefon” (QR cu token temporar, prin internet)."""
        def _open():
            from mobile_upload_dialog import open_phone_upload
            open_phone_upload(self, etype, eid, label, on_done=refresh or (lambda: self.current_cod_loc and self.show_loc(self.current_cod_loc)))
        return self._btn("\U0001F4F1  Continuă pe telefon (încarcă document)", self._pal["accent"], _open)

    def _loc_photos(self, cod_loc: str) -> List[Dict]:
        docs = db.list_documents("loc", cod_loc, "poza_loc")
        # includem și pozele legacy din tabela photos
        try:
            for p in db.list_photos_for_loc(cod_loc):
                docs.append({"id": f"legacy-{p['id']}", "file_path": p.get("full_path", ""),
                             "caption": p.get("caption", ""), "nr_evidenta": "", "_legacy": True})
        except Exception:
            pass
        return docs

    def _add_loc_photos(self, cod_loc: str):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Alege fotografii / PDF pentru loc", "", "Documente (*.png *.jpg *.jpeg *.pdf)")
        if not files:
            return
        for f in files:
            db.add_document("loc", cod_loc, "poza_loc", f)
        self.show_loc(cod_loc)

    # ---- CONCESIONAR ----
    def _build_concesionar_section(self, cod_loc: str):
        try:
            contracte = [c for c in db.load_contracte() if c.get("cod_loc") == cod_loc]
        except Exception:
            contracte = []
        conces = None
        contract = contracte[-1] if contracte else None
        if contract and contract.get("concesionar_id"):
            try:
                conces = db.get_concesionar(int(contract["concesionar_id"]))
            except Exception:
                conces = None

        if conces:
            title = f"Concesionar — {conces.get('nume','')} {conces.get('prenume','')}"
        elif contract and contract.get("owner_fullname"):
            title = f"Concesionar — {contract.get('owner_fullname')}"
        else:
            title = "Concesionar — (neasociat)"

        sec = CollapsibleSection(title, self._pal, expanded=False, accent=self._pal["success"])
        if conces:
            for lbl, key in [("Nume", "nume"), ("Prenume", "prenume"), ("CNP", "cnp"),
                             ("BI/CI", "bi_ci"), ("Adresă", "adresa"), ("Telefon", "telefon"),
                             ("Email", "email")]:
                sec.add(self._info_row(lbl, str(conces.get(key, "") or "")))
        elif contract:
            sec.add(self._info_row("Titular", str(contract.get("owner_fullname", "") or "")))
            sec.add(self._info_row("CNP", str(contract.get("owner_cnp", "") or "")))
            sec.add(self._info_row("Adresă", str(contract.get("owner_address", "") or "")))
            sec.add(self._info_row("Telefon", str(contract.get("owner_phone", "") or "")))
        else:
            info = QLabel("Niciun concesionar asociat acestui loc.")
            info.setStyleSheet(f"color: {self._pal['text_muted']}; background: transparent; border: none;")
            sec.add(info)
        sec.add(self._info_row("Contracte", str(len(contracte))))
        for c in contracte:
            lbl = QLabel(f"📄 Contract {c.get('numar_contract') or '?'} — documente (imagine/PDF)")
            lbl.setStyleSheet(f"color: {self._pal['accent']}; background: transparent; border: none; font-weight: 600; margin-top: 4px;")
            sec.add(lbl)
            strip = PhotoStrip(
                self._pal,
                on_add=lambda cid=c.get("id"): self._add_contract_doc(cid),
                on_delete=lambda d, cc=cod_loc: self._delete_doc(d, cc),
                add_text="➕  Adaugă contract scanat (imagine/PDF)",
            )
            strip.set_docs(db.list_documents("contract", c.get("id")))
            sec.add(strip)
            sec.add(self._phone_btn("contract", c.get("id"), f"Contract {c.get('numar_contract') or c.get('id')}"))
        sec.add(self._btn("👤  Gestionează concesionar & contracte", self._pal["success"],
                          lambda: self.window.op_manage_contracts()))
        self.cl.addWidget(sec)

    def _add_contract_doc(self, contract_id):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Alege contract scanat (imagine/PDF)", "", "Documente (*.png *.jpg *.jpeg *.pdf)")
        if not files:
            return
        for f in files:
            db.add_document("contract", contract_id, "contract_scan", f)
        if self.current_cod_loc:
            self.show_loc(self.current_cod_loc)

    # ---- ÎNHUMAȚI ----
    def _build_inhumati_section(self, cod_loc: str):
        inhumati = db.load_inhumati_for_loc(cod_loc)
        sec = CollapsibleSection(f"Înhumați ({len(inhumati)})", self._pal, expanded=True, accent=self._pal["accent"])
        sec.add(self._btn("➕  Adaugă înhumat", self._pal["primary"],
                          lambda: self.window.op_add_inhumat()))
        if not inhumati:
            info = QLabel("Nicio persoană înhumată.")
            info.setStyleSheet(f"color: {self._pal['text_muted']}; background: transparent; border: none;")
            sec.add(info)
        for inh in inhumati:
            sec.add(self._inhumat_card(inh))
        self.cl.addWidget(sec)

    def _inhumat_card(self, inh: Dict) -> QWidget:
        iid = inh.get("id")
        name = f"{inh.get('nume','')} {inh.get('prenume','')}".strip() or "Fără nume"
        sub = CollapsibleSection(name, self._pal, expanded=False, accent=self._pal["accent"])
        for lbl, key in [("CNP", "cnp"), ("Data nașterii", "data_nasterii"),
                         ("Data deces", "data_deces"), ("Data înhumare", "data_inhumare"),
                         ("Cauza deces", "cauza_deces"), ("Nr. certificat deces", "nr_certificat_deces"),
                         ("Nr. adev. înhumare", "nr_adeverinta_inhumare")]:
            val = str(inh.get(key, "") or "")
            if val:
                sub.add(self._info_row(lbl, val))

        # documente înhumat
        docs_lbl = QLabel("Documente: certificat deces, adeverință înhumare (imagine/PDF)")
        docs_lbl.setStyleSheet(f"color: {self._pal['accent']}; background: transparent; border: none; font-weight: 600; margin-top: 4px;")
        sub.add(docs_lbl)
        strip = PhotoStrip(
            self._pal,
            on_add=lambda i=iid: self._add_inhumat_doc(i),
            on_delete=lambda d, i=iid: self._delete_doc(d, self.current_cod_loc),
            add_text="➕  Adaugă document (imagine/PDF)",
        )
        strip.set_docs(db.list_documents("inhumat", iid))
        sub.add(strip)
        sub.add(self._phone_btn("inhumat", iid, f"Înhumat #{iid}"))

        sub.add(self._btn("✎  Editează înhumat", self._pal["warning"],
                          lambda i=iid: self.window.op_edit_inhumat(i)))
        return sub

    def _add_inhumat_doc(self, inhumat_id):
        types = ["Certificat de deces", "Adeverință de înhumare", "Alt document"]
        choice, ok = QInputDialog.getItem(self, "Tip document", "Ce document adaugi?", types, 0, False)
        if not ok:
            return
        doc_type = {"Certificat de deces": "certificat_deces",
                    "Adeverință de înhumare": "adeverinta_inhumare",
                    "Alt document": "alt"}[choice]
        file, _ = QFileDialog.getOpenFileName(self, "Alege fișier document (imagine/PDF)", "", "Documente (*.png *.jpg *.jpeg *.pdf)")
        if not file:
            return
        nr, _ = QInputDialog.getText(self, "Număr evidență", "Număr de evidență (opțional):")
        db.add_document("inhumat", inhumat_id, doc_type, file, nr_evidenta=(nr or "").strip())
        if self.current_cod_loc:
            self.show_loc(self.current_cod_loc)

    # ---- LUCRĂRI / AVIZE ----
    def _build_lucrari_section(self, cod_loc: str):
        avize = db.list_avize_for_loc(cod_loc)
        sec = CollapsibleSection(f"Lucrări efectuate / Avize ({len(avize)})", self._pal,
                                 expanded=False, accent=self._pal["warning"])
        sec.add(self._btn("➕  Adaugă lucrare / aviz", self._pal["warning"],
                          lambda: self._add_aviz(cod_loc)))
        if not avize:
            info = QLabel("Nicio lucrare / aviz înregistrat.")
            info.setStyleSheet(f"color: {self._pal['text_muted']}; background: transparent; border: none;")
            sec.add(info)
        for av in avize:
            sec.add(self._aviz_card(cod_loc, av))
        self.cl.addWidget(sec)

    def _aviz_card(self, cod_loc: str, av: Dict) -> QWidget:
        aid = av.get("id")
        title = f"Aviz {av.get('nr','') or '?'}"
        if av.get("lucrare"):
            title += f" — {av.get('lucrare')}"
        if av.get("stare"):
            title += f" · {wd.stare_label(av)}"
        sub = CollapsibleSection(title, self._pal, expanded=False, accent=self._pal["warning"])
        sub.add(self._info_row("Nr. aviz", str(av.get("nr", "") or "")))
        sub.add(self._info_row("Data aviz", str(av.get("data", "") or "")))
        sub.add(self._info_row("Lucrare", str(av.get("lucrare", "") or "")))
        for name, label, value in (("stare", "Stare", wd.stare_label(av)),
                                   ("finalizare", "Data finalizării", wd.finalizare_label(av)),
                                   ("observatii", "Observații", str(av.get("observatii", "") or ""))):
            row = self._info_row(label, value)
            row.setObjectName(f"aviz-{name}")
            sub.add(row)
        groups = wd.split_docs(db.list_documents("aviz", aid))
        for key, label in wd.STAGES:
            sub.add(self._aviz_stage_strip(cod_loc, aid, key, label, groups[key],
                                           lambda a=aid, k=key: self._add_aviz_photo(a, k)))
        sub.add(self._aviz_stage_strip(cod_loc, aid, "other", wd.OTHER_LABEL, groups["other"],
                                       lambda a=aid: self._add_aviz_photo(a)))
        sub.add(self._phone_btn("aviz", aid, f"Aviz #{aid}"))
        return sub

    def _aviz_stage_strip(self, cod_loc: str, aid, key: str, label: str, docs: List[Dict], on_add) -> QWidget:
        box = QWidget()
        box.setObjectName(f"aviz-stage-{key}")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lbl = QLabel(f"{label} ({len(docs)})")
        lbl.setStyleSheet(f"color: {self._pal['accent']}; background: transparent; border: none; font-weight: 600; margin-top: 4px;")
        lay.addWidget(lbl)
        strip = PhotoStrip(
            self._pal,
            on_add=on_add,
            on_delete=lambda d, c=cod_loc: self._delete_doc(d, c),
            add_text="➕  Adaugă (imagine/PDF)",
        )
        strip.set_docs(docs)
        lay.addWidget(strip)
        return box

    def _add_aviz(self, cod_loc: str):
        try:
            from panels import AddAvizDialog
        except Exception as e:
            QMessageBox.warning(self, "Eroare", f"Nu am putut deschide dialogul: {e}")
            return
        from PySide6.QtWidgets import QDialog
        dlg = AddAvizDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        res = dlg.get_result()
        if not res:
            return
        aid = db.add_aviz(cod_loc, res.get("nr", ""), res.get("data", ""), res.get("lucrare", ""))
        if aid and res.get("photo"):
            db.add_document("aviz", aid, "foto_lucrare", res["photo"], caption=res.get("lucrare", ""))
        self.show_loc(cod_loc)

    def _add_aviz_photo(self, aviz_id, doc_type: str = "foto_lucrare"):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Alege fotografii lucrare / autorizație (imagine/PDF)", "", "Documente (*.png *.jpg *.jpeg *.pdf)")
        if not files:
            return
        for f in files:
            db.add_document("aviz", aviz_id, doc_type, f)
        if self.current_cod_loc:
            self.show_loc(self.current_cod_loc)

    # ---- PLĂȚI ----
    def _build_plati_section(self, cod_loc: str):
        try:
            total = db.total_plati(cod_loc)
            n = len(db.list_plati(cod_loc))
        except Exception:
            total, n = 0.0, 0
        sec = CollapsibleSection(f"Plăți ({n})", self._pal, expanded=False, accent=self._pal["primary"])
        sec.add(self._info_row("Total încasat", f"{total:.2f} lei"))
        sec.add(self._btn("💰  Gestionează plăți", self._pal["warning"],
                          lambda: self.window.op_payments()))
        self.cl.addWidget(sec)

    # -------------------------------------------------------------------------
    def _delete_doc(self, doc: Dict, cod_loc: Optional[str]):
        if QMessageBox.question(self, "Ștergere", "Ștergi această fotografie/document?",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        did = doc.get("id")
        if isinstance(did, str) and did.startswith("legacy-"):
            try:
                db.delete_photo(int(did.split("-", 1)[1]))
            except Exception:
                pass
        else:
            db.delete_document(did)
        if cod_loc:
            self.show_loc(cod_loc)
