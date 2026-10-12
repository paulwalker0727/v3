# add_inhumat_dialog.py
"""
Dialog pentru adăugarea/modificarea unui înhumat.
Dacă `existing` este furnizat, câmpurile sunt populate și dialogul intră
în mod editare (butonul „Salvează și adaugă încă unul" este ascuns).

Documente:
    - Se pot atașa oricâte fotografii / PDF-uri pentru certificatul de deces
      și pentru adeverința de înhumare (butoane „Adaugă…").
    - În modul editare se afișează și documentele deja salvate (din tabela
      documents), cu buton „Deschide" și „Șterge".
    - Fișierele noi selectate se salvează după acceptarea dialogului
      (`get_documents()` returnează lista de fișiere noi de importat).
"""

from typing import Optional, Dict, List
import os

from PySide6.QtCore import QDate, QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QTextEdit, QPushButton, QComboBox, QMessageBox, QDateEdit,
    QFileDialog, QLabel, QWidget, QGroupBox, QScrollArea, QFrame,
)

import database as db


BLANK_DATE = QDate(1800, 1, 1)  # valoare internă „fără dată”; NU se salvează niciodată
_DATE_FORMATS = ("dd.MM.yyyy", "ddMMyyyy", "yyyy-MM-dd")


class OptionalDateEdit(QDateEdit):
    """QDateEdit care poate reprezenta „fără dată”. `value_str()` întoarce "" când nu există dată (niciodată sentinelul)."""

    def __init__(self):
        super().__init__()
        self.setDisplayFormat("dd.MM.yyyy")
        self.setCalendarPopup(True)
        self.setMinimumDate(BLANK_DATE)
        self.setSpecialValueText("fără dată")
        self._raw = ""
        self.set_blank()
        self.dateChanged.connect(self._on_user_change)

    def _on_user_change(self, _d):
        self._raw = ""
        self.setSpecialValueText("fără dată")

    def is_blank(self) -> bool:
        return self.date() == BLANK_DATE

    def set_blank(self):
        self._raw = ""
        self.setSpecialValueText("fără dată")
        self.setDate(BLANK_DATE)

    def set_from_text(self, value):
        """Setează din textul din DB; un text nerecunoscut e păstrat neschimbat (nu se pierde la Save)."""
        value = str(value or "").strip()
        if not value:
            self.set_blank()
            return
        for fmt in _DATE_FORMATS:
            d = QDate.fromString(value, fmt)
            if d.isValid() and d > BLANK_DATE:
                self.setDate(d)
                return
        self.set_blank()
        self._raw = value
        self.setSpecialValueText(value)

    def value_str(self) -> str:
        if self.is_blank():
            return self._raw
        return self.date().toString("dd.MM.yyyy")

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and (event.modifiers() & Qt.ControlModifier or self.is_blank()):
            self.set_blank()
            return
        super().keyPressEvent(event)


def _date_row(edit: OptionalDateEdit) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(edit, 1)
    btn = QPushButton("Fără dată")
    btn.setToolTip("Șterge data (câmpul rămâne gol)")
    btn.clicked.connect(edit.set_blank)
    h.addWidget(btn)
    return w


DOC_TYPES = [
    ("certificat_deces", "Certificat de deces", "nr_certificat_deces"),
    ("adeverinta_inhumare", "Adeverință de înhumare", "nr_adeverinta_inhumare"),
]


class AddInhumatDialog(QDialog):
    def __init__(self, parent, cod_loc: str, existing: Optional[Dict] = None):
        super().__init__(parent)
        self.setWindowTitle(f"Decedat pentru loc {cod_loc}")
        self.setMinimumSize(620, 760)

        self.cod_loc = cod_loc
        self._existing = existing or {}
        self._inhumat_id = self._existing.get("id")
        self._data: Dict = {}

        # listă de fișiere NOI selectate (nu salvate încă). Fiecare: dict
        # {"doc_type": ..., "path": ..., "nr_evidenta": ..., "label": QLabel}
        self._new_files: List[Dict] = []

        # ---- câmpuri text ----
        self.txt_nume = QLineEdit()
        self.txt_prenume = QLineEdit()
        self.txt_cnp = QLineEdit()

        self.txt_data_nasterii = self._mk_date_edit()
        self.txt_data_deces = self._mk_date_edit()
        self.txt_data_inhumare = self._mk_date_edit()
        self.txt_data_deshumare = self._mk_date_edit()

        self.cmb_sex = QComboBox()
        self.cmb_sex.addItems(["", "M", "F"])

        self.txt_cauza_deces = QLineEdit()
        self.txt_profesie = QLineEdit()
        self.txt_religie = QLineEdit()
        self.txt_ultima_adresa = QLineEdit()
        self.txt_observatii = QTextEdit()
        self.txt_observatii.setFixedHeight(70)

        # numere de evidență documente
        self.txt_nr_certificat_deces = QLineEdit()
        self.txt_nr_certificat_deces.setPlaceholderText("nr. certificat de deces")
        self.txt_nr_adeverinta_inhumare = QLineEdit()
        self.txt_nr_adeverinta_inhumare.setPlaceholderText("nr. adeverință de înhumare")

        # ---- formular date ----
        form = QFormLayout()
        form.addRow("Nume:", self.txt_nume)
        form.addRow("Prenume:", self.txt_prenume)
        form.addRow("CNP:", self.txt_cnp)
        form.addRow("Data nașterii:", _date_row(self.txt_data_nasterii))
        form.addRow("Data deces:", _date_row(self.txt_data_deces))
        form.addRow("Data înhumare:", _date_row(self.txt_data_inhumare))
        form.addRow("Data deshumare:", _date_row(self.txt_data_deshumare))
        form.addRow("Sex:", self.cmb_sex)
        form.addRow("Cauza deces:", self.txt_cauza_deces)
        form.addRow("Profesie:", self.txt_profesie)
        form.addRow("Religie:", self.txt_religie)
        form.addRow("Ultima adresă:", self.txt_ultima_adresa)
        form.addRow("Observații:", self.txt_observatii)
        form.addRow("Nr. certificat deces:", self.txt_nr_certificat_deces)
        form.addRow("Nr. adeverință înhumare:", self.txt_nr_adeverinta_inhumare)

        # ---- secțiune documente (foto/PDF) ----
        docs_box = QGroupBox("Documente atașate (foto / PDF)")
        docs_v = QVBoxLayout()
        docs_v.setSpacing(8)

        # container pentru fiecare tip de document
        self._doc_containers: Dict[str, QVBoxLayout] = {}
        for key, label, _ in DOC_TYPES:
            group = QFrame()
            group.setStyleSheet("QFrame { border:1px solid #333; border-radius:6px; padding:6px; }")
            gl = QVBoxLayout(group)
            gl.setContentsMargins(8, 6, 8, 8)
            gl.setSpacing(4)

            title = QLabel(f"<b>{label}</b>")
            gl.addWidget(title)

            # listă documente existente (populată din DB dacă e editare)
            list_container = QVBoxLayout()
            list_container.setSpacing(2)
            gl.addLayout(list_container)
            self._doc_containers[key] = list_container

            # linie „adaugă"
            add_row = QHBoxLayout()
            btn_add = QPushButton(f"➕  Adaugă {label.lower()} (imagine/PDF)")
            btn_add.setStyleSheet("QPushButton { text-align:left; padding:6px; }")
            btn_add.clicked.connect(lambda _=None, k=key, lbl=label, lc=list_container: self._add_new_file(k, lbl, lc))
            add_row.addWidget(btn_add)
            gl.addLayout(add_row)

            docs_v.addWidget(group)

        docs_box.setLayout(docs_v)

        # scroll pentru documente (util dacă sunt multe)
        scroll_docs = QScrollArea()
        scroll_docs.setWidgetResizable(True)
        scroll_docs.setFrameShape(QFrame.NoFrame)
        scroll_docs.setMinimumHeight(180)
        scroll_docs.setMaximumHeight(280)
        scroll_docs.setWidget(docs_box)

        # ---- butoane ----
        self.btn_ai_scan = QPushButton("🤖  Scanare AI certificat")
        self.btn_ai_scan.setToolTip(
            "Fotografiază sau încarcă certificatul de deces / adeverința de înhumare — "
            "AI-ul completează automat toate câmpurile de mai sus."
        )
        self.btn_ai_scan.setStyleSheet(
            "QPushButton { background:#0ea5e9; color:white; padding:6px 14px; "
            "border:none; border-radius:5px; font-weight:600; }"
            "QPushButton:hover { background:#0284c7; }"
        )
        self.btn_ai_scan.clicked.connect(self._open_ai_scan)

        self.btn_save = QPushButton("💾  Salvează")
        self.btn_save_and_add = QPushButton("💾  Salvează și adaugă încă unul")
        self.btn_cancel = QPushButton("Anulează")
        self.btn_save.clicked.connect(self._save)
        self.btn_save_and_add.clicked.connect(self._save_and_add)
        self.btn_cancel.clicked.connect(self.reject)

        btns = QHBoxLayout()
        btns.addWidget(self.btn_ai_scan)
        btns.addWidget(self.btn_save)
        btns.addWidget(self.btn_save_and_add)
        btns.addStretch()
        btns.addWidget(self.btn_cancel)

        # ---- layout principal cu scroll ----
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.addLayout(form)
        content_layout.addWidget(scroll_docs)

        outer_scroll = QScrollArea()
        outer_scroll.setWidgetResizable(True)
        outer_scroll.setFrameShape(QFrame.NoFrame)
        outer_scroll.setWidget(content)

        root = QVBoxLayout()
        root.addWidget(outer_scroll)
        root.addLayout(btns)
        self.setLayout(root)

        # ---- populate ----
        if self._existing:
            self.populate(self._existing)

        # ascundem butonul „save and add" în modul editare
        if self._inhumat_id:
            self.btn_save_and_add.setVisible(False)
            self._load_existing_documents()

    # ---------------------------------------------------------------
    @staticmethod
    def _mk_date_edit() -> OptionalDateEdit:
        return OptionalDateEdit()

    # ---------------------------------------------------------------
    # DOCUMENTE
    # ---------------------------------------------------------------
    def _add_new_file(self, doc_type: str, doc_label: str, list_container: QVBoxLayout):
        """Deschide dialog fișier și adaugă în lista de fișiere noi."""
        files, _ = QFileDialog.getOpenFileNames(
            self, f"Alege fișier(e) pentru {doc_label}",
            "", "Documente (*.png *.jpg *.jpeg *.pdf)")
        if not files:
            return
        for f in files:
            if not os.path.exists(f):
                QMessageBox.warning(self, "Eroare", f"Fișierul nu există:\n{f}")
                continue
            row = self._mk_new_file_row(doc_type, doc_label, f)
            list_container.addWidget(row)
            self._new_files.append({"doc_type": doc_type, "path": f, "widget": row})

    def _mk_new_file_row(self, doc_type: str, doc_label: str, path: str) -> QWidget:
        w = QFrame()
        w.setStyleSheet("QFrame { background:#0f2a1a; border:1px solid #22c55e; border-radius:4px; padding:2px; }")
        h = QHBoxLayout(w)
        h.setContentsMargins(6, 3, 6, 3)
        h.setSpacing(6)
        ic = QLabel("📎")
        lbl = QLabel(os.path.basename(path))
        lbl.setStyleSheet("color:#22c55e; font-size:9pt;")
        lbl.setToolTip(path)
        btn_del = QPushButton("✕")
        btn_del.setFixedSize(22, 22)
        btn_del.setStyleSheet("QPushButton { color:#f87171; border:none; background:transparent; }"
                              "QPushButton:hover { color:#ef4444; }")
        btn_del.clicked.connect(lambda: self._remove_new_file(w, path))
        h.addWidget(ic)
        h.addWidget(lbl, 1)
        h.addWidget(btn_del)
        return w

    def _remove_new_file(self, widget: QWidget, path: str):
        self._new_files = [f for f in self._new_files if not (f["path"] == path and f["widget"] is widget)]
        widget.setParent(None)

    def _load_existing_documents(self):
        """Populează lista cu documentele deja salvate în DB (mod editare)."""
        if not self._inhumat_id:
            return
        for key, label, _ in DOC_TYPES:
            try:
                docs = db.list_documents("inhumat", self._inhumat_id, key)
            except Exception:
                docs = []
            container = self._doc_containers[key]
            for d in docs:
                row = self._mk_existing_doc_row(d)
                container.addWidget(row)

    def _mk_existing_doc_row(self, doc: Dict) -> QWidget:
        w = QFrame()
        w.setStyleSheet("QFrame { background:#1e293b; border:1px solid #334155; border-radius:4px; padding:2px; }")
        h = QHBoxLayout(w)
        h.setContentsMargins(6, 3, 6, 3)
        h.setSpacing(6)
        path = doc.get("file_path", "")
        ext = os.path.splitext(path)[1].lower()
        ic = QLabel("📄" if ext == ".pdf" else "🖼")
        name = os.path.basename(path) or "(fișier necunoscut)"
        nr = doc.get("nr_evidenta", "") or ""
        if nr:
            name = f"{name}  —  nr. {nr}"
        lbl = QLabel(name)
        lbl.setStyleSheet("color:#e2e8f0; font-size:9pt;")
        lbl.setToolTip(path)

        btn_open = QPushButton("Deschide")
        btn_open.setStyleSheet("QPushButton { padding:2px 8px; }")
        btn_open.clicked.connect(lambda: self._open_file(path))

        btn_del = QPushButton("Șterge")
        btn_del.setStyleSheet("QPushButton { color:#f87171; padding:2px 8px; }")
        btn_del.clicked.connect(lambda: self._delete_existing_doc(doc.get("id"), w))

        h.addWidget(ic)
        h.addWidget(lbl, 1)
        h.addWidget(btn_open)
        h.addWidget(btn_del)
        return w

    def _open_file(self, path: str):
        if not path or not os.path.exists(path):
            QMessageBox.information(self, "Info", "Fișierul nu a fost găsit pe disc.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _delete_existing_doc(self, doc_id, widget: QWidget):
        if doc_id is None:
            return
        if QMessageBox.question(self, "Confirmare",
                                "Ștergi definitiv acest document?",
                                QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            ok = db.delete_document(int(doc_id))
        except Exception as e:
            QMessageBox.warning(self, "Eroare", f"Ștergere eșuată: {e}")
            return
        if ok:
            widget.setParent(None)
        else:
            QMessageBox.warning(self, "Eroare", "Ștergerea a eșuat.")

    # ---------------------------------------------------------------
    def get_documents(self) -> List[Dict]:
        """Documentele NOI atașate (se salvează după crearea/editarea înhumatului)."""
        nr_map = {
            "certificat_deces": self.txt_nr_certificat_deces,
            "adeverinta_inhumare": self.txt_nr_adeverinta_inhumare,
        }
        out = []
        for f in self._new_files:
            nr_field = nr_map.get(f["doc_type"])
            out.append({
                "doc_type": f["doc_type"],
                "path": f["path"],
                "nr_evidenta": nr_field.text().strip() if nr_field else "",
            })
        return out

    def populate(self, data: Dict):
        self.txt_nume.setText(str(data.get("nume", "") or ""))
        self.txt_prenume.setText(str(data.get("prenume", "") or ""))
        self.txt_cnp.setText(str(data.get("cnp", "") or ""))

        def set_date(field: OptionalDateEdit, value: Optional[str]):
            if value:
                field.set_from_text(value)

        set_date(self.txt_data_nasterii, data.get("data_nasterii"))
        set_date(self.txt_data_deces, data.get("data_deces"))
        set_date(self.txt_data_inhumare, data.get("data_inhumare"))
        set_date(self.txt_data_deshumare, data.get("data_deshumare"))

        sex = str(data.get("sex", "") or "")
        self.cmb_sex.setCurrentText(sex if sex in ("M", "F") else "")

        self.txt_cauza_deces.setText(str(data.get("cauza_deces", "") or ""))
        self.txt_profesie.setText(str(data.get("profesie", "") or ""))
        self.txt_religie.setText(str(data.get("religie", "") or ""))
        self.txt_ultima_adresa.setText(str(data.get("ultima_adresa", "") or ""))
        self.txt_observatii.setPlainText(str(data.get("observatii", "") or ""))
        self.txt_nr_certificat_deces.setText(str(data.get("nr_certificat_deces", "") or ""))
        self.txt_nr_adeverinta_inhumare.setText(str(data.get("nr_adeverinta_inhumare", "") or ""))

    def _collect_data(self) -> Dict:
        return {
            "nume": self.txt_nume.text().strip(),
            "prenume": self.txt_prenume.text().strip(),
            "cnp": self.txt_cnp.text().strip(),
            "data_nasterii": self.txt_data_nasterii.value_str(),
            "data_deces": self.txt_data_deces.value_str(),
            "data_inhumare": self.txt_data_inhumare.value_str(),
            "data_deshumare": self.txt_data_deshumare.value_str(),
            "sex": self.cmb_sex.currentText().strip(),
            "cauza_deces": self.txt_cauza_deces.text().strip(),
            "profesie": self.txt_profesie.text().strip(),
            "religie": self.txt_religie.text().strip(),
            "ultima_adresa": self.txt_ultima_adresa.text().strip(),
            "observatii": self.txt_observatii.toPlainText().strip(),
            "nr_certificat_deces": self.txt_nr_certificat_deces.text().strip(),
            "nr_adeverinta_inhumare": self.txt_nr_adeverinta_inhumare.text().strip(),
        }

    def _validate_minimal(self, data: Dict) -> bool:
        if not data["nume"] or not data["prenume"]:
            QMessageBox.warning(self, "Eroare", "Numele și prenumele sunt obligatorii.")
            return False
        return True

    def _clear_form(self):
        self.txt_nume.clear()
        self.txt_prenume.clear()
        self.txt_cnp.clear()
        for f in (self.txt_data_nasterii, self.txt_data_deces,
                  self.txt_data_inhumare, self.txt_data_deshumare):
            f.set_blank()
        self.cmb_sex.setCurrentIndex(0)
        self.txt_cauza_deces.clear()
        self.txt_profesie.clear()
        self.txt_religie.clear()
        self.txt_ultima_adresa.clear()
        self.txt_observatii.clear()
        self.txt_nr_certificat_deces.clear()
        self.txt_nr_adeverinta_inhumare.clear()
        # golim și fișierele noi
        for f in list(self._new_files):
            try:
                f["widget"].setParent(None)
            except Exception:
                pass
        self._new_files.clear()
        self._inhumat_id = None

    # ---------------------------------------------------------------
    def _find_actions(self):
        """Urcă în lanțul de părinți până găsește un widget cu `.actions`."""
        w = self.parent()
        while w is not None and not hasattr(w, "actions"):
            w = w.parent()
        return getattr(w, "actions", None) if w else None

    def _save(self):
        data = self._collect_data()
        if not self._validate_minimal(data):
            return
        if self._inhumat_id:
            data["id"] = self._inhumat_id
        self._data = data
        self.accept()

    def _save_and_add(self):
        """
        Salvează direct în DB (incl. documente atașate) și resetează formularul.
        Nu este disponibil în modul editare.
        """
        if self._inhumat_id:
            QMessageBox.information(self, "Info",
                                    "Funcția 'Salvează și adaugă' nu este disponibilă la editare.")
            return

        data = self._collect_data()
        if not self._validate_minimal(data):
            return

        actions = self._find_actions()
        saved_id = None
        try:
            if actions and callable(getattr(actions, "save_inhumat", None)):
                saved_id = actions.save_inhumat(self.cod_loc, data, inhumat_id=None)
            else:
                saved_id = db.save_inhumat(self.cod_loc, data, inhumat_id=None)
        except Exception as e:
            QMessageBox.warning(self, "Eroare", f"Nu am putut salva: {e}")
            return

        if not saved_id:
            QMessageBox.warning(self, "Eroare",
                                "Salvarea a eșuat. Verificați că locul de veci există în baza de date.")
            return

        # salvăm documentele atașate
        failed = []
        for doc in self.get_documents():
            did = db.add_document("inhumat", saved_id, doc["doc_type"],
                                  doc["path"], nr_evidenta=doc.get("nr_evidenta", ""))
            if not did:
                failed.append(doc["path"])
        if failed:
            QMessageBox.warning(self, "Documente nesalvate",
                                "Următoarele fișiere NU au putut fi salvate:\n\n" + "\n".join(failed))

        QMessageBox.information(self, "OK",
                                "Înregistrare salvată. Puteți adăuga următorul înhumat.")
        self._clear_form()

    # ---------------------------------------------------------------
    def _open_ai_scan(self):
        """Deschide dialogul AI Scanare pentru certificat deces și populează formularul."""
        try:
            from ai_scanare.scan_dialog import ScanDialog
            from ai_scanare.ai_client import DocumentType
            from ai_scanare.db_scans import save_scan
        except Exception as e:
            QMessageBox.warning(
                self, "Modul AI indisponibil",
                f"Modulul AI Scanare nu se poate încărca: {e}",
            )
            return

        dlg = ScanDialog(self, DocumentType.CERTIFICAT_DECES, allow_type_change=False)
        if dlg.exec() != QDialog.Accepted:
            return
        data = dlg.get_extracted_data()

        # populăm formularul cu datele extrase (mapare 1:1)
        self.populate({
            "nume": data.get("nume", ""),
            "prenume": data.get("prenume", ""),
            "cnp": data.get("cnp", ""),
            "data_nasterii": data.get("data_nasterii", ""),
            "data_deces": data.get("data_deces", ""),
            "sex": data.get("sex", ""),
            "cauza_deces": data.get("cauza_deces", ""),
            "ultima_adresa": data.get("ultima_adresa", ""),
            "nr_certificat_deces": data.get("nr_certificat_deces", ""),
            "nr_adeverinta_inhumare": data.get("nr_adeverinta_inhumare", ""),
            "observatii": (
                f"Locul decesului: {data.get('locul_decesului', '')} · "
                f"Emitent: {data.get('emitent', '')} · "
                f"Data eliberării: {data.get('data_eliberare', '')}"
            ),
        })

        # dacă a ales „attach", atașăm fișierul ca document nou la înhumat (se salvează după _save)
        src = dlg.get_source_file()
        action = dlg.get_file_action()
        if src and os.path.exists(src) and action != "delete":
            row = self._mk_new_file_row("certificat_deces", "Certificat de deces (AI)", src)
            self._doc_containers["certificat_deces"].addWidget(row)
            self._new_files.append({"doc_type": "certificat_deces", "path": src, "widget": row})

        # salvăm în istoric AI (fără entity — se leagă când se apasă „Salvează")
        try:
            save_scan(
                document_type=DocumentType.CERTIFICAT_DECES,
                filename=os.path.basename(src or ""),
                file_path=src if action != "delete" else None,
                extracted={"fields": data},
                status="processed",
            )
        except Exception:
            pass

        QMessageBox.information(
            self, "Câmpuri completate",
            "Datele au fost extrase și completate în formular. Verifică și salvează."
        )

    def get_data(self) -> Dict:
        return self._data
