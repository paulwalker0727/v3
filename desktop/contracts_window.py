# contracts_window.py
from typing import Optional, Dict, List, Any
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QDateEdit, QTableWidget, QTableWidgetItem,
    QFormLayout, QMessageBox, QGroupBox, QWidget, QTextEdit,
    QComboBox, QCompleter, QAbstractItemView, QFileDialog
)
from PySide6.QtCore import Qt, QDate

# încercăm să folosim adaptorul actions dacă este disponibil, altfel fallback la modul database
try:
    from database import get_connection, load_loc_by_cod, load_concesionari, save_concesionar, save_contract, load_contracte, delete_contract
    DB_AVAILABLE = True
except Exception:
    get_connection = None
    load_loc_by_cod = None
    load_concesionari = None
    save_concesionar = None
    save_contract = None
    load_contracte = None
    delete_contract = None
    DB_AVAILABLE = False


class AddContractDialog(QDialog):
    def __init__(self, parent: QWidget, actions=None, existing: Optional[Dict] = None, selected_cod_loc: Optional[str] = None):
        super().__init__(parent)
        self.setWindowTitle("Contract de concesiune")
        self.setMinimumSize(640, 560)
        self.actions = actions
        self._existing = existing or {}
        self._contract_id = self._existing.get("id")

        layout = QVBoxLayout()
        form = QFormLayout()

        # date contract
        self.txt_numar = QLineEdit()
        self.date_contract = QDateEdit()
        self.date_contract.setDisplayFormat("dd.MM.yyyy")
        self.date_contract.setCalendarPopup(True)

        self.date_inceput = QDateEdit()
        self.date_inceput.setDisplayFormat("dd.MM.yyyy")
        self.date_inceput.setCalendarPopup(True)

        self.date_sfarsit = QDateEdit()
        self.date_sfarsit.setDisplayFormat("dd.MM.yyyy")
        self.date_sfarsit.setCalendarPopup(True)

        # cod loc
        self.cmb_cod_loc = QComboBox()
        self.cmb_cod_loc.setEditable(True)
        self._setup_cod_loc_combo()
        if selected_cod_loc:
            self.cmb_cod_loc.setCurrentText(selected_cod_loc)
        self.cmb_cod_loc.currentTextChanged.connect(self._on_cod_loc_changed)

        # concesionar
        self.cmb_concesionar = QComboBox()
        self.cmb_concesionar.setEditable(True)
        self._conces_map: Dict[str, Optional[int]] = {}
        self._conces_data: Dict[int, Dict[str, Any]] = {}
        self._setup_concesionar_combo()
        self.cmb_concesionar.currentTextChanged.connect(self._on_concesionar_changed)

        self.btn_add_concesionar = QPushButton("Adaugă concesionar")
        conces_layout = QHBoxLayout()
        conces_layout.addWidget(self.cmb_concesionar)
        conces_layout.addWidget(self.btn_add_concesionar)

        # date concesionar (auto-fill)
        self.txt_nume = QLineEdit()
        self.txt_prenume = QLineEdit()
        self.txt_adresa = QLineEdit()
        self.txt_bi_ci = QLineEdit()
        self.txt_cnp = QLineEdit()
        self.txt_telefon = QLineEdit()
        self.txt_email = QLineEdit()

        # date loc (auto-fill) — editabile pentru suprascriere
        self.txt_sector = QLineEdit()
        self.txt_parcela = QLineEdit()
        self.txt_rand = QLineEdit()
        self.txt_loc = QLineEdit()
        self.txt_suprafata = QLineEdit()

        self.txt_observatii = QTextEdit()
        self.txt_observatii.setFixedHeight(80)

        # form layout
        form.addRow("Număr contract:", self.txt_numar)
        form.addRow("Data contract:", self.date_contract)
        form.addRow("Data început:", self.date_inceput)
        form.addRow("Data sfârșit:", self.date_sfarsit)
        form.addRow("Cod loc:", self.cmb_cod_loc)
        form.addRow("Concesionar:", conces_layout)

        form.addRow("Nume:", self.txt_nume)
        form.addRow("Prenume:", self.txt_prenume)
        form.addRow("Adresă:", self.txt_adresa)
        form.addRow("BI/CI:", self.txt_bi_ci)
        form.addRow("CNP:", self.txt_cnp)
        form.addRow("Telefon:", self.txt_telefon)
        form.addRow("Email:", self.txt_email)

        # loc fields (sector/parcela/rand/loc/suprafata)
        form.addRow("Sector:", self.txt_sector)
        form.addRow("Parcela:", self.txt_parcela)
        form.addRow("Rând:", self.txt_rand)
        form.addRow("Loc:", self.txt_loc)
        form.addRow("Suprafață (m2):", self.txt_suprafata)

        form.addRow("Observații:", self.txt_observatii)

        layout.addLayout(form)

        # buttons
        btns = QHBoxLayout()
        self.btn_save = QPushButton("Salvează")
        self.btn_cancel = QPushButton("Anulează")
        btns.addStretch()
        btns.addWidget(self.btn_save)
        btns.addWidget(self.btn_cancel)
        layout.addLayout(btns)

        self.setLayout(layout)

        self.btn_save.clicked.connect(self._on_save)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_add_concesionar.clicked.connect(self._open_add_concesionar)

        if self._existing:
            self._populate(self._existing)
        else:
            today = QDate.currentDate()
            self.date_contract.setDate(today)
            self.date_inceput.setDate(today)
            self.date_sfarsit.setDate(today)

        self._saved_id: Optional[int] = None

    # ---------------------------------------------------------
    # SETUP COMBOBOXES
    # ---------------------------------------------------------
    def _setup_concesionar_combo(self):
        self.cmb_concesionar.clear()
        rows = []
        try:
            if self.actions and hasattr(self.actions, "load_concesionari"):
                rows = self.actions.load_concesionari()
            elif DB_AVAILABLE and load_concesionari:
                rows = load_concesionari()
        except Exception:
            rows = []

        self.cmb_concesionar.addItem("", None)
        self._conces_map.clear()
        self._conces_data.clear()

        for c in rows:
            cid = c.get("id")
            label = f"{c.get('nume','')} {c.get('prenume','')} ({cid})"
            self.cmb_concesionar.addItem(label, cid)
            self._conces_map[label] = cid
            if cid is not None:
                self._conces_data[cid] = c

        # completer pentru căutare rapidă
        completer = QCompleter([self.cmb_concesionar.itemText(i) for i in range(self.cmb_concesionar.count())])
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.cmb_concesionar.setCompleter(completer)

    def _setup_cod_loc_combo(self):
        cods = []
        try:
            # încercăm să folosim actions.search_locuri dacă există
            if self.actions and hasattr(self.actions, "search_locuri"):
                locs = self.actions.search_locuri({})
                for l in locs:
                    if l.get("cod_loc"):
                        cods.append(l["cod_loc"])
            elif DB_AVAILABLE and get_connection:
                conn = get_connection()
                cur = conn.cursor()
                cur.execute("SELECT cod_loc FROM locuri_de_veci WHERE cod_loc IS NOT NULL ORDER BY cod_loc")
                rows = cur.fetchall()
                conn.close()
                for r in rows:
                    try:
                        cods.append(r["cod_loc"])
                    except Exception:
                        try:
                            cods.append(r[0])
                        except Exception:
                            pass
        except Exception:
            pass

        cods = sorted(set(cods))
        self.cmb_cod_loc.clear()
        self.cmb_cod_loc.addItem("")
        for code in cods:
            self.cmb_cod_loc.addItem(code)

        completer = QCompleter(cods)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.cmb_cod_loc.setCompleter(completer)

    # ---------------------------------------------------------
    # AUTO-FILL
    # ---------------------------------------------------------
    def _on_concesionar_changed(self, text: str):
        if not text:
            for w in (self.txt_nume, self.txt_prenume, self.txt_adresa,
                      self.txt_bi_ci, self.txt_cnp, self.txt_telefon, self.txt_email):
                w.clear()
            return

        # încercăm să obținem id-ul din userData sau din map
        cid = self.cmb_concesionar.currentData()
        if cid is None:
            cid = self._conces_map.get(text)

        if not cid:
            return

        data = self._conces_data.get(cid)
        if not data:
            # dacă nu avem date locale, încercăm să le încărcăm din actions/db
            try:
                if self.actions and hasattr(self.actions, "load_concesionar"):
                    data = self.actions.load_concesionar(cid)
                elif DB_AVAILABLE and get_connection:
                    conn = get_connection()
                    cur = conn.cursor()
                    cur.execute("SELECT * FROM concesionari WHERE id = ?", (cid,))
                    row = cur.fetchone()
                    conn.close()
                    if row:
                        data = dict(row)
            except Exception:
                data = None

        if not data:
            return

        self.txt_nume.setText(str(data.get("nume", "") or ""))
        self.txt_prenume.setText(str(data.get("prenume", "") or ""))
        self.txt_adresa.setText(str(data.get("adresa", "") or ""))
        self.txt_bi_ci.setText(str(data.get("bi_ci", "") or ""))
        self.txt_cnp.setText(str(data.get("cnp", "") or ""))
        self.txt_telefon.setText(str(data.get("telefon", "") or ""))
        self.txt_email.setText(str(data.get("email", "") or ""))

    def _on_cod_loc_changed(self, text: str):
        # completează automat sector, parcela, rând, loc și suprafață după cod_loc
        if not text:
            for w in (self.txt_sector, self.txt_parcela, self.txt_rand, self.txt_loc, self.txt_suprafata):
                w.clear()
            return

        try:
            fn = None
            if self.actions and hasattr(self.actions, "load_loc_by_cod"):
                fn = getattr(self.actions, "load_loc_by_cod")
            elif DB_AVAILABLE and load_loc_by_cod:
                fn = load_loc_by_cod

            loc = fn(text) if callable(fn) else None
        except Exception:
            loc = None

        if not loc:
            for w in (self.txt_sector, self.txt_parcela, self.txt_rand, self.txt_loc, self.txt_suprafata):
                w.clear()
            return

        self.txt_sector.setText(str(loc.get("sector", "") or ""))
        self.txt_parcela.setText(str(loc.get("parcela", "") or ""))
        self.txt_rand.setText(str(loc.get("rand", "") or ""))
        self.txt_loc.setText(str(loc.get("numar", "") or ""))
        sup = loc.get("suprafata", "")
        self.txt_suprafata.setText(str(sup) if sup is not None else "")

    # ---------------------------------------------------------
    # POPULATE EXISTING CONTRACT
    # ---------------------------------------------------------
    def _populate(self, data: Dict):
        self.txt_numar.setText(data.get("numar_contract", ""))

        def set_date(field, value):
            if not value:
                return
            d = QDate.fromString(value, "dd.MM.yyyy")
            if d.isValid():
                field.setDate(d)

        set_date(self.date_contract, data.get("data_contract", ""))
        set_date(self.date_inceput, data.get("data_inceput", ""))
        set_date(self.date_sfarsit, data.get("data_sfarsit", ""))

        self.cmb_cod_loc.setCurrentText(data.get("cod_loc", ""))

        cid = data.get("concesionar_id")
        if cid:
            # selectăm concesionarul în combo
            for i in range(self.cmb_concesionar.count()):
                if self.cmb_concesionar.itemData(i) == cid:
                    self.cmb_concesionar.setCurrentIndex(i)
                    break

        # auto-fill concesionar
        self._on_concesionar_changed(self.cmb_concesionar.currentText())

        # auto-fill loc (preferăm valorile din contract)
        if data.get("cemetery_parcel") or data.get("cemetery_row") or data.get("cemetery_tomb") or data.get("tomb_surface_m2"):
            self.txt_parcela.setText(str(data.get("cemetery_parcel", "") or ""))
            self.txt_rand.setText(str(data.get("cemetery_row", "") or ""))
            self.txt_loc.setText(str(data.get("cemetery_tomb", "") or ""))
            self.txt_suprafata.setText(str(data.get("tomb_surface_m2", "") or ""))
            self.txt_sector.setText(str(data.get("sector", "") or ""))
        else:
            # încărcăm din locuri_de_veci
            try:
                fn = None
                if self.actions and hasattr(self.actions, "load_loc_by_cod"):
                    fn = getattr(self.actions, "load_loc_by_cod")
                elif DB_AVAILABLE and load_loc_by_cod:
                    fn = load_loc_by_cod
                loc = fn(self.cmb_cod_loc.currentText()) if callable(fn) else None
            except Exception:
                loc = None
            if loc:
                self.txt_sector.setText(str(loc.get("sector", "") or ""))
                self.txt_parcela.setText(str(loc.get("parcela", "") or ""))
                self.txt_rand.setText(str(loc.get("rand", "") or ""))
                self.txt_loc.setText(str(loc.get("numar", "") or ""))
                self.txt_suprafata.setText(str(loc.get("suprafata", "") or ""))

        self.txt_observatii.setPlainText(data.get("observatii", ""))

    # ---------------------------------------------------------
    # COLLECT & SAVE
    # ---------------------------------------------------------
    def _collect(self) -> Dict:
        # concesionar id din userData
        concesionar_id = self.cmb_concesionar.currentData()
        if concesionar_id is None:
            # încercăm map-ul (compatibilitate)
            concesionar_id = self._conces_map.get(self.cmb_concesionar.currentText())

        cod_loc_val = self.cmb_cod_loc.currentText().strip() or None

        cemetery_parcel = self.txt_parcela.text().strip() or None
        cemetery_row = self.txt_rand.text().strip() or None
        cemetery_tomb = self.txt_loc.text().strip() or None
        tomb_surface = self.txt_suprafata.text().strip() or None
        sector_val = self.txt_sector.text().strip() or None

        return {
            "numar_contract": self.txt_numar.text().strip(),
            "data_contract": self.date_contract.date().toString("dd.MM.yyyy"),
            "data_inceput": self.date_inceput.date().toString("dd.MM.yyyy"),
            "data_sfarsit": self.date_sfarsit.date().toString("dd.MM.yyyy"),
            "cod_loc": cod_loc_val,
            "concesionar_id": concesionar_id,
            "observatii": self.txt_observatii.toPlainText().strip(),
            "extra_data": None,
            "owner_fullname": f"{self.txt_nume.text().strip()} {self.txt_prenume.text().strip()}".strip(),
            "owner_address": self.txt_adresa.text().strip(),
            "owner_id_card_serie_number": self.txt_bi_ci.text().strip(),
            "owner_cnp": self.txt_cnp.text().strip(),
            "owner_phone": self.txt_telefon.text().strip(),
            "owner_email": self.txt_email.text().strip(),
            "cemetery_parcel": cemetery_parcel,
            "cemetery_row": cemetery_row,
            "cemetery_tomb": cemetery_tomb,
            "tomb_surface_m2": tomb_surface,
            "owner_fullname_2": f"{self.txt_nume.text().strip()} {self.txt_prenume.text().strip()}".strip(),
            "sector": sector_val
        }

    def _on_save(self):
        data = self._collect()

        if not data["numar_contract"]:
            QMessageBox.warning(self, "Eroare", "Numărul contractului este obligatoriu.")
            return

        if not data["concesionar_id"]:
            QMessageBox.warning(self, "Eroare", "Selectați un concesionar.")
            return

        try:
            if self._contract_id:
                if self.actions and hasattr(self.actions, "save_contract"):
                    saved = self.actions.save_contract(data, contract_id=self._contract_id)
                elif DB_AVAILABLE and save_contract:
                    saved = save_contract(data, contract_id=self._contract_id)
                else:
                    saved = None
            else:
                if self.actions and hasattr(self.actions, "save_contract"):
                    saved = self.actions.save_contract(data, contract_id=None)
                elif DB_AVAILABLE and save_contract:
                    saved = save_contract(data, contract_id=None)
                else:
                    saved = None

            self._saved_id = saved
            if saved:
                QMessageBox.information(self, "OK", "Contract salvat.")
                self.accept()
            else:
                QMessageBox.warning(self, "Eroare", "Nu am putut salva contractul.")
        except Exception as e:
            QMessageBox.critical(self, "Eroare", f"Eroare la salvare: {e}")

    def get_saved_id(self) -> Optional[int]:
        return self._saved_id

    # ---------------------------------------------------------
    # helper: open add concesionar dialog (lazy import)
    # ---------------------------------------------------------
    def _open_add_concesionar(self):
        try:
            from concesionari_window import AddConcesionarDialog
        except Exception:
            AddConcesionarDialog = None

        if not AddConcesionarDialog:
            QMessageBox.warning(self, "Eroare", "Modulul de adăugare concesionar nu este disponibil.")
            return

        dlg = AddConcesionarDialog(self, existing=None)
        if dlg.exec() == QDialog.Accepted:
            data = dlg.get_data()
            saved_id = None
            try:
                if self.actions and hasattr(self.actions, "save_concesionar"):
                    saved_id = self.actions.save_concesionar(data)
                elif DB_AVAILABLE and save_concesionar:
                    saved_id = save_concesionar(data)
            except Exception:
                saved_id = None

            # refresh concesionar combo
            self._setup_concesionar_combo()
            if saved_id:
                # selectăm noul concesionar
                for i in range(self.cmb_concesionar.count()):
                    if self.cmb_concesionar.itemData(i) == saved_id:
                        self.cmb_concesionar.setCurrentIndex(i)
                        break


# ---------------------------------------------------------
# CONTRACTS WINDOW
# ---------------------------------------------------------
class ContractsWindow(QDialog):
    def __init__(self, parent: QWidget = None, actions=None, selected_cod_loc: Optional[str] = None):
        super().__init__(parent)
        self.setWindowTitle("Contracte de concesiune")
        self.setMinimumSize(980, 560)
        self.actions = actions
        self.selected_cod_loc = selected_cod_loc

        main = QVBoxLayout()

        # filters
        filter_box = QGroupBox("Filtre")
        f_layout = QHBoxLayout()
        self.filter_numar = QLineEdit()
        self.filter_cod_loc = QLineEdit()
        self.filter_numar.setPlaceholderText("Număr contract")
        self.filter_cod_loc.setPlaceholderText("Cod loc")
        f_layout.addWidget(QLabel("Număr:"))
        f_layout.addWidget(self.filter_numar)
        f_layout.addWidget(QLabel("Cod loc:"))
        f_layout.addWidget(self.filter_cod_loc)
        filter_box.setLayout(f_layout)
        main.addWidget(filter_box)

        # table
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "ID", "Număr contract", "Data contract",
            "Data început", "Data sfârșit", "Cod loc"
        ])
        self.table.setColumnHidden(0, True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        main.addWidget(self.table)

        # buttons
        btn_row = QHBoxLayout()
        self.btn_add = QPushButton("Adaugă")
        self.btn_edit = QPushButton("Modifică")
        self.btn_delete = QPushButton("Șterge")
        self.btn_generate = QPushButton("Generează contract")
        self.btn_refresh = QPushButton("Reîncarcă")
        btn_row.addWidget(self.btn_add)
        btn_row.addWidget(self.btn_edit)
        btn_row.addWidget(self.btn_delete)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_generate)
        btn_row.addWidget(self.btn_refresh)
        main.addLayout(btn_row)

        self.setLayout(main)

        # signals
        self.btn_add.clicked.connect(self.add_contract)
        self.btn_edit.clicked.connect(self.edit_contract)
        self.btn_delete.clicked.connect(self.delete_contract)
        self.btn_generate.clicked.connect(self.generate_contract)
        self.btn_refresh.clicked.connect(self.reload)

        self.filter_numar.textChanged.connect(self.reload)
        self.filter_cod_loc.textChanged.connect(self.reload)

        self.reload()

    # ---------------------------------------------------------
    def reload(self):
        self.table.setRowCount(0)
        try:
            if self.actions and hasattr(self.actions, "load_contracte"):
                rows = self.actions.load_contracte()
            elif DB_AVAILABLE and load_contracte:
                rows = load_contracte()
            else:
                rows = []
        except Exception:
            rows = []

        num_filter = self.filter_numar.text().strip().lower()
        cod_filter = self.filter_cod_loc.text().strip().lower()

        for r in rows:
            numar = str(r.get("numar_contract", "") or "")
            cod_loc = str(r.get("cod_loc", "") or "")

            if num_filter and num_filter not in numar.lower():
                continue
            if cod_filter and cod_filter not in cod_loc.lower():
                continue

            row_idx = self.table.rowCount()
            self.table.insertRow(row_idx)
            self.table.setItem(row_idx, 0, QTableWidgetItem(str(r.get("id"))))
            self.table.setItem(row_idx, 1, QTableWidgetItem(numar))
            self.table.setItem(row_idx, 2, QTableWidgetItem(str(r.get("data_contract", ""))))
            self.table.setItem(row_idx, 3, QTableWidgetItem(str(r.get("data_inceput", ""))))
            self.table.setItem(row_idx, 4, QTableWidgetItem(str(r.get("data_sfarsit", ""))))
            self.table.setItem(row_idx, 5, QTableWidgetItem(cod_loc))

        self.table.resizeColumnsToContents()

    # ---------------------------------------------------------
    def _selected_id(self) -> Optional[int]:
        idx = self.table.currentRow()
        if idx < 0:
            return None
        item = self.table.item(idx, 0)
        try:
            return int(item.text())
        except Exception:
            return None

    def _selected_row(self) -> Optional[Dict]:
        cid = self._selected_id()
        if not cid:
            return None
        try:
            if self.actions and hasattr(self.actions, "load_contracte"):
                rows = self.actions.load_contracte()
            elif DB_AVAILABLE and load_contracte:
                rows = load_contracte()
            else:
                rows = []
            for r in rows:
                if r.get("id") == cid:
                    return r
        except Exception:
            return None
        return None

    # ---------------------------------------------------------
    def add_contract(self):
        dlg = AddContractDialog(self, self.actions, existing=None, selected_cod_loc=self.selected_cod_loc)
        if dlg.exec() == QDialog.Accepted:
            self.reload()

    def edit_contract(self):
        cid = self._selected_id()
        if not cid:
            QMessageBox.information(self, "Info", "Selectați un contract.")
            return
        existing = self._selected_row()
        dlg = AddContractDialog(self, self.actions, existing=existing, selected_cod_loc=self.selected_cod_loc)
        if dlg.exec() == QDialog.Accepted:
            self.reload()

    def delete_contract(self):
        cid = self._selected_id()
        if not cid:
            return
        reply = QMessageBox.question(self, "Șterge", "Sigur doriți să ștergeți contractul?", QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            if self.actions and hasattr(self.actions, "delete_contract"):
                ok = self.actions.delete_contract(cid)
            elif DB_AVAILABLE and delete_contract:
                ok = delete_contract(cid)
            else:
                ok = False
            if ok:
                QMessageBox.information(self, "OK", "Contract șters.")
            else:
                QMessageBox.warning(self, "Eroare", "Ștergere eșuată.")
        except Exception as e:
            QMessageBox.critical(self, "Eroare", f"Eroare la ștergere: {e}")
        self.reload()

    # ---------------------------------------------------------
    def generate_contract(self):
        cid = self._selected_id()
        if not cid:
            QMessageBox.information(self, "Info", "Selectați un contract din listă.")
            return

        # 1. Colectăm datele și verificăm ce câmpuri lipsesc
        try:
            from contract_generator import _build_mapping, get_missing_placeholders, build_default_filename
            from database import get_contract as _get_contract
            contract = _get_contract(cid)
            if not contract:
                QMessageBox.warning(self, "Eroare", "Contractul nu a fost găsit în baza de date.")
                return
            mapping = _build_mapping(contract)
            missing = get_missing_placeholders(mapping)
        except Exception as e:
            QMessageBox.critical(self, "Eroare", f"Eroare la pregătirea datelor: {e}")
            return

        # 2. Avertizare pentru câmpurile goale
        if missing:
            msg = ("Următoarele câmpuri lipsesc și vor apărea goale în contract:\n\n• "
                   + "\n• ".join(missing)
                   + "\n\nContinuăm generarea?")
            reply = QMessageBox.question(self, "Câmpuri lipsă", msg,
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if reply != QMessageBox.Yes:
                return

        # 3. Save As dialog
        from PySide6.QtWidgets import QFileDialog
        import os
        default_name = build_default_filename(contract)
        default_dir = os.path.abspath("contracts")
        os.makedirs(default_dir, exist_ok=True)
        default_path = os.path.join(default_dir, default_name)

        out_path, _ = QFileDialog.getSaveFileName(
            self, "Salvează contractul generat",
            default_path,
            "Document Word (*.docx)"
        )
        if not out_path:
            return
        if not out_path.lower().endswith(".docx"):
            out_path += ".docx"

        # 4. Generare
        try:
            actions = self.actions
            if actions and hasattr(actions, "generate_contract_document"):
                result_path = actions.generate_contract_document(
                    cid,
                    template_path="templates/contract_template.docx",
                    out_path=out_path
                )
            else:
                from contract_generator import generate_contract
                r = generate_contract(cid, "templates/contract_template.docx", out_path)
                result_path = r.get("path") if r else None
        except Exception as e:
            QMessageBox.critical(self, "Eroare la generare", f"Nu am putut genera documentul: {e}")
            return

        if not result_path or not os.path.isfile(result_path):
            QMessageBox.warning(self, "Eroare",
                                "Documentul nu a fost creat. Verifică:\n"
                                "• Că templates/contract_template.docx există\n"
                                "• Că ai permisiuni de scriere în folderul destinație")
            return

        # 5. Succes — oferă deschidere automată
        reply = QMessageBox.question(
            self, "Contract generat cu succes",
            f"Contractul a fost salvat în:\n{result_path}\n\nDeschideți documentul acum?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        )
        if reply == QMessageBox.Yes:
            try:
                import sys, subprocess
                if sys.platform.startswith("win"):
                    os.startfile(result_path)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", result_path])
                else:
                    subprocess.Popen(["xdg-open", result_path])
            except Exception as e:
                QMessageBox.information(self, "Info",
                                        f"Documentul a fost salvat, dar nu am putut să-l deschid:\n{e}")
