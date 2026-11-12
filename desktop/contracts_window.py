# contracts_window.py
from typing import Optional, Dict, List, Any
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QTableWidget, QTableWidgetItem,
    QFormLayout, QMessageBox, QGroupBox, QWidget, QTextEdit,
    QComboBox, QCompleter, QAbstractItemView, QFileDialog
)
from PySide6.QtCore import Qt, QDate

from optional_date_edit import OptionalDateEdit

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
    def __init__(self, parent: QWidget, actions=None, existing: Optional[Dict] = None, selected_cod_loc: Optional[str] = None,
                 selected_concesionar_id: Optional[int] = None):
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
        self.date_contract = OptionalDateEdit()
        self.date_inceput = OptionalDateEdit()
        self.date_sfarsit = OptionalDateEdit()

        # cod loc
        self.cmb_cod_loc = QComboBox()
        self.cmb_cod_loc.setEditable(True)
        self._setup_cod_loc_combo()
        if selected_cod_loc:
            self.cmb_cod_loc.setCurrentText(selected_cod_loc)
        self.cmb_cod_loc.currentTextChanged.connect(self._on_cod_loc_changed)
        self.btn_open_loc = QPushButton("Date loc")
        loc_layout = QHBoxLayout()
        loc_layout.addWidget(self.cmb_cod_loc)
        loc_layout.addWidget(self.btn_open_loc)

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
        self.btn_open_concesionar = QPushButton("Date concesionar")
        conces_layout.addWidget(self.btn_open_concesionar)

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
        form.addRow("Cod loc:", loc_layout)
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
        self.btn_change_conc = QPushButton("Schimbă concesionarul…")
        self.btn_assoc_loc = QPushButton("Asociază / schimbă loc…")
        self.btn_change_conc.setToolTip("Schimbarea titularului este o operațiune separată, cu confirmare și jurnal.")
        btns.addWidget(self.btn_change_conc)
        btns.addWidget(self.btn_assoc_loc)
        btns.addStretch()
        btns.addWidget(self.btn_save)
        btns.addWidget(self.btn_cancel)
        layout.addLayout(btns)

        self.setLayout(layout)

        self.btn_save.clicked.connect(self._on_save)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_add_concesionar.clicked.connect(self._open_add_concesionar)
        self.btn_open_concesionar.clicked.connect(self._open_concesionar_data)
        self.btn_open_loc.clicked.connect(self._open_loc_data)
        self.btn_change_conc.clicked.connect(self._change_concesionar)
        self.btn_assoc_loc.clicked.connect(self._associate_loc)
        for b in (self.btn_change_conc, self.btn_assoc_loc):
            b.setVisible(bool(self._existing))
            try:
                import access_session
                access_session.gate(b, "edit")
            except Exception:
                pass

        if self._existing:
            self._populate(self._existing)
            self.cmb_concesionar.setEnabled(False)
            self.cmb_cod_loc.setEnabled(False)
            self.btn_add_concesionar.setEnabled(False)
        else:
            if selected_cod_loc:
                self._on_cod_loc_changed(selected_cod_loc)
            if selected_concesionar_id:
                self._refresh_and_select_concesionar(selected_concesionar_id)
            today = QDate.currentDate()
            self.date_contract.setDate(today)
            self.date_inceput.setDate(today)
            self.date_sfarsit.setDate(today)

        self._saved_id: Optional[int] = None
        self._initial = self._collect()

    def _is_dirty(self) -> bool:
        return self._saved_id is None and self._collect() != self._initial

    def reject(self):
        if self._is_dirty():
            reply = QMessageBox.question(self, "Modificări nesalvate",
                                         "Există modificări nesalvate. Renunțați la ele?",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        super().reject()

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
        if getattr(self, "_snapshot_locked", False):
            return
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

    _OWNER_KEYS = ("owner_fullname", "owner_address", "owner_id_card_serie_number", "owner_cnp", "owner_phone", "owner_email")

    def _show_owner_snapshot(self, data: Dict):
        """Contract existent: afișează datele titularului de la emitere (read-only); ele NU se rescriu la salvare."""
        self._edit_concesionar_id = data.get("concesionar_id") or None
        if not any(data.get(k) for k in self._OWNER_KEYS):
            return
        self.txt_nume.setText(str(data.get("owner_fullname", "") or ""))
        self.txt_prenume.clear()
        self.txt_adresa.setText(str(data.get("owner_address", "") or ""))
        self.txt_bi_ci.setText(str(data.get("owner_id_card_serie_number", "") or ""))
        self.txt_cnp.setText(str(data.get("owner_cnp", "") or ""))
        self.txt_telefon.setText(str(data.get("owner_phone", "") or ""))
        self.txt_email.setText(str(data.get("owner_email", "") or ""))
        self._snapshot_locked = True
        for w in (self.txt_nume, self.txt_prenume, self.txt_adresa, self.txt_bi_ci,
                  self.txt_cnp, self.txt_telefon, self.txt_email):
            w.setReadOnly(True)
            w.setToolTip("Date istorice ale titularului din contract (nu se modifică la editare).")

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

        # F-01: acceptă zz.ll.aaaa, aaaa-ll-zz, zzllaaaa; gol rămâne gol; textul nerecunoscut se păstrează (nu devine data curentă)
        self.date_contract.set_from_text(data.get("data_contract"))
        self.date_inceput.set_from_text(data.get("data_inceput"))
        self.date_sfarsit.set_from_text(data.get("data_sfarsit"))

        self.cmb_cod_loc.setCurrentText(data.get("cod_loc", ""))

        cid = data.get("concesionar_id")
        if cid:
            # selectăm concesionarul în combo
            for i in range(self.cmb_concesionar.count()):
                if self.cmb_concesionar.itemData(i) == cid:
                    self.cmb_concesionar.setCurrentIndex(i)
                    break

        # auto-fill concesionar, apoi instantaneul istoric al titularului din contract are prioritate
        self._on_concesionar_changed(self.cmb_concesionar.currentText())
        self._show_owner_snapshot(data)

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
        if self._contract_id:
            concesionar_id = self._existing.get("concesionar_id") or None
            cod_loc_val = self._existing.get("cod_loc") or None

        cemetery_parcel = self.txt_parcela.text().strip() or None
        cemetery_row = self.txt_rand.text().strip() or None
        cemetery_tomb = self.txt_loc.text().strip() or None
        tomb_surface = self.txt_suprafata.text().strip() or None
        sector_val = self.txt_sector.text().strip() or None

        payload = {
            "numar_contract": self.txt_numar.text().strip(),
            "data_contract": self.date_contract.value_str(),
            "data_inceput": self.date_inceput.value_str(),
            "data_sfarsit": self.date_sfarsit.value_str(),
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
        if self._contract_id:
            for k in (*self._OWNER_KEYS, "owner_fullname_2"):
                payload.pop(k, None)
        return payload

    def _on_save(self):
        if self._saved_id:      # deja salvat în această sesiune (dublu-click / apăsări repetate): nu se mai scrie nimic
            return
        data = self._collect()

        if not data["numar_contract"]:
            QMessageBox.warning(self, "Eroare", "Numărul contractului este obligatoriu.")
            return

        if not data["concesionar_id"] and not self._contract_id:
            QMessageBox.warning(self, "Eroare", "Selectați un concesionar.")
            return

        cod = data.get("cod_loc")
        if not self._contract_id and self._duplicate_exists(data):
            reply = QMessageBox.question(
                self, "Contract posibil duplicat",
                "Există deja un contract cu același număr, concesionar și loc. Salvezi totuși un contract nou?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        if cod and not self._contract_id and not self._loc_exists(cod):
            reply = QMessageBox.question(
                self, "Loc inexistent",
                f"Codul „{cod}” nu există în Locuri de veci (nu se creează locuri noi din contract).\n"
                "Salvezi contractul fără legătură cu un loc existent?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
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
                import database as _db
                QMessageBox.warning(self, "Eroare", "Nu am putut salva contractul. " + _db.last_refusal())
        except Exception as e:
            QMessageBox.critical(self, "Eroare", f"Eroare la salvare: {e}")

    def get_saved_id(self) -> Optional[int]:
        return self._saved_id

    def _duplicate_exists(self, data: Dict) -> bool:
        try:
            rows = self.actions.load_contracte() if self.actions and hasattr(self.actions, "load_contracte") else (load_contracte() if DB_AVAILABLE else [])
        except Exception:
            return False
        return any(str(r.get("numar_contract") or "") == data["numar_contract"]
                   and (r.get("concesionar_id") or None) == (data.get("concesionar_id") or None)
                   and (r.get("cod_loc") or None) == (data.get("cod_loc") or None) for r in rows)

    # ---------------------------------------------------------
    # SCHIMBAREA TITULARULUI / LOCULUI (operațiuni separate, cu confirmare și jurnal)
    # ---------------------------------------------------------
    def _db_call(self, name: str, *args):
        fn = getattr(self.actions, name, None) if self.actions else None
        if not callable(fn):
            import database as _db
            fn = getattr(_db, name)
        return fn(*args)

    def _ask_reason(self) -> Optional[str]:
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, "Motiv (opțional)", "Motivul modificării (se înregistrează în jurnal):")
        return text.strip() if ok else None

    def _refresh_after_link_change(self):
        fresh = self._db_call("get_contract", self._contract_id) or {}
        self._existing = fresh
        self._setup_concesionar_combo()
        for i in range(self.cmb_concesionar.count()):
            if self.cmb_concesionar.itemData(i) == fresh.get("concesionar_id"):
                self.cmb_concesionar.setCurrentIndex(i)
        self.cmb_cod_loc.setCurrentText(fresh.get("cod_loc") or "")
        self._initial = self._collect()

    def _change_concesionar(self):
        if not self._contract_id:
            return
        from entity_links_ui import PickDialog
        rows = self._db_call("load_concesionari") or []
        dlg = PickDialog(self, "Alege noul concesionar al contractului", rows,
                         [("Nume", "nume"), ("Prenume", "prenume"), ("CNP", "cnp"), ("Adresă", "adresa")])
        if dlg.exec() != QDialog.Accepted or not dlg.selected_row():
            return
        new = dlg.selected_row()
        if new["id"] == self._existing.get("concesionar_id"):
            QMessageBox.information(self, "Info", "Concesionarul ales este deja asociat contractului.")
            return
        label = f"{new.get('nume') or ''} {new.get('prenume') or ''}".strip()
        if QMessageBox.question(
                self, "Confirmare schimbare titular",
                f"Contractul {self.txt_numar.text()} va fi asociat concesionarului „{label}”.\n"
                "Operațiunea se aplică imediat și se înregistrează în jurnal. Nu șterge nimic (plăți, documente, istoric).\n\nContinuați?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        snapshot = QMessageBox.question(
            self, "Datele titularului din contract",
            "Datele titularului din contract (instantaneul de la emitere) se păstrează implicit și vor apărea în documentul generat.\n"
            "Le actualizez din fișa noului concesionar? (alegeți Da doar pentru corectarea unei asocieri greșite)",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes
        reason = self._ask_reason()
        if reason is None:
            return
        if self._db_call("reassign_contract_concesionar", self._contract_id, new["id"], snapshot, reason):
            self._refresh_after_link_change()
            self._edit_concesionar_id = new["id"]
            if snapshot:
                for w in (self.txt_nume, self.txt_prenume, self.txt_adresa, self.txt_bi_ci, self.txt_cnp, self.txt_telefon, self.txt_email):
                    w.setReadOnly(False)
                self._snapshot_locked = False
                self._show_owner_snapshot(self._existing)
            QMessageBox.information(self, "OK", "Concesionarul contractului a fost schimbat.")
        else:
            import database as _db
            QMessageBox.warning(self, "Eroare", "Nu am putut schimba concesionarul. " + _db.last_refusal())

    def _associate_loc(self):
        if not self._contract_id:
            return
        from entity_links_ui import PickDialog
        rows = self._db_call("search_locuri", {}) or []
        dlg = PickDialog(self, "Alege locul contractului", rows,
                         [("Cod loc", "cod_loc"), ("Sector", "sector"), ("Parcela", "parcela"), ("Rând", "rand"), ("Număr", "numar")])
        if dlg.exec() != QDialog.Accepted or not dlg.selected_row():
            return
        cod = dlg.selected_row()["cod_loc"]
        if cod == (self._existing.get("cod_loc") or ""):
            QMessageBox.information(self, "Info", "Locul ales este deja asociat contractului.")
            return
        if QMessageBox.question(
                self, "Confirmare asociere loc",
                f"Contractul {self.txt_numar.text()} va fi asociat locului „{cod}”.\n"
                "Operațiunea se aplică imediat și se înregistrează în jurnal.\n\nContinuați?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        reason = self._ask_reason()
        if reason is None:
            return
        if self._db_call("associate_contract_loc", self._contract_id, cod, reason):
            self._refresh_after_link_change()
            self._on_cod_loc_changed(cod)
            QMessageBox.information(self, "OK", "Locul contractului a fost asociat.")
        else:
            import database as _db
            QMessageBox.warning(self, "Eroare", "Nu am putut asocia locul. " + _db.last_refusal())

    def _loc_exists(self, cod: str) -> bool:
        try:
            fn = getattr(self.actions, "load_loc_by_cod", None) if self.actions else None
            fn = fn or (load_loc_by_cod if DB_AVAILABLE else None)
            return bool(fn(cod)) if callable(fn) else True
        except Exception:
            return True

    def _open_concesionar_data(self):
        cid = self.cmb_concesionar.currentData() or self._conces_map.get(self.cmb_concesionar.currentText())
        if not cid:
            QMessageBox.information(self, "Info", "Selectați mai întâi un concesionar.")
            return
        from concesionari_window import AddConcesionarDialog
        data = self._conces_data.get(cid)
        if data is None:
            return
        dlg = AddConcesionarDialog(self, existing=data, actions=self.actions)
        if dlg.exec() == QDialog.Accepted:
            self._refresh_and_select_concesionar(cid)
            self._on_concesionar_changed(self.cmb_concesionar.currentText())

    def _open_loc_data(self):
        cod = self.cmb_cod_loc.currentText().strip()
        if not cod:
            QMessageBox.information(self, "Info", "Selectați mai întâi un loc de veci.")
            return
        from locuri_window import EditLocDialog
        if EditLocDialog(self, cod).exec() == QDialog.Accepted:
            self._on_cod_loc_changed(cod)

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

        dlg = AddConcesionarDialog(self, existing=None, actions=self.actions)
        if dlg.exec() == QDialog.Accepted:
            # dialogul a salvat deja (o singură dată); reutilizăm ID-ul, nu recreăm persoana
            self._refresh_and_select_concesionar(dlg.get_saved_id())

    def _refresh_and_select_concesionar(self, concesionar_id: Optional[int]):
        self._setup_concesionar_combo()
        if not concesionar_id:
            return
        for i in range(self.cmb_concesionar.count()):
            if self.cmb_concesionar.itemData(i) == concesionar_id:
                self.cmb_concesionar.setCurrentIndex(i)
                break


def generate_contract_flow(parent, actions, cid) -> Optional[str]:
    """Previzualizare câmpuri lipsă -> Salvează ca -> generare DOCX din șablonul curent. Nu modifică contractul."""
    # 1. Colectăm datele și verificăm ce câmpuri lipsesc
    try:
        from contract_generator import _build_mapping, get_missing_placeholders, build_default_filename
        from database import get_contract as _get_contract
        contract = _get_contract(cid)
        if not contract:
            QMessageBox.warning(parent, "Eroare", "Contractul nu a fost găsit în baza de date.")
            return None
        mapping = _build_mapping(contract)
        missing = get_missing_placeholders(mapping)
    except Exception as e:
        QMessageBox.critical(parent, "Eroare", f"Eroare la pregătirea datelor: {e}")
        return None

    # 2. Avertizare pentru câmpurile goale
    if missing:
        msg = ("Următoarele câmpuri lipsesc și vor apărea goale în contract:\n\n• "
               + "\n• ".join(missing)
               + "\n\nContinuăm generarea?")
        reply = QMessageBox.question(parent, "Câmpuri lipsă", msg,
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if reply != QMessageBox.Yes:
            return None

    # 3. Save As dialog
    from PySide6.QtWidgets import QFileDialog
    import os
    default_name = build_default_filename(contract)
    default_dir = os.path.abspath("contracts")
    os.makedirs(default_dir, exist_ok=True)
    default_path = os.path.join(default_dir, default_name)

    out_path, _ = QFileDialog.getSaveFileName(
        parent, "Salvează contractul generat",
        default_path,
        "Document Word (*.docx)"
    )
    if not out_path:
        return None
    if not out_path.lower().endswith(".docx"):
        out_path += ".docx"

    # 4. Generare
    try:
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
        QMessageBox.critical(parent, "Eroare la generare", f"Nu am putut genera documentul: {e}")
        return None

    if not result_path or not os.path.isfile(result_path):
        QMessageBox.warning(parent, "Eroare",
                            "Documentul nu a fost creat. Verifică:\n"
                            "• Că templates/contract_template.docx există\n"
                            "• Că ai permisiuni de scriere în folderul destinație")
        return None

    # 5. Succes — oferă deschidere automată
    reply = QMessageBox.question(
        parent, "Contract generat cu succes",
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
            QMessageBox.information(parent, "Info",
                                    f"Documentul a fost salvat, dar nu am putut să-l deschid:\n{e}")
    return result_path



# ---------------------------------------------------------
# CONTRACTS WINDOW
# ---------------------------------------------------------
class ContractsWindow(QDialog):
    COLS = ["ID", "Număr contract", "Data contract", "Concesionar", "Cod loc", "Sector", "Parcela", "Rând", "Perioadă", "Stare"]

    def __init__(self, parent: QWidget = None, actions=None, selected_cod_loc: Optional[str] = None,
                 selected_concesionar_id: Optional[int] = None, filter_cod_loc: Optional[str] = None,
                 filter_concesionar_id: Optional[int] = None):
        super().__init__(parent)
        self.setWindowTitle("Contracte de concesiune")
        self.selected_concesionar_id = selected_concesionar_id
        self._only_concesionar_id = filter_concesionar_id
        self.setMinimumSize(1100, 600)
        self.actions = actions
        self.selected_cod_loc = selected_cod_loc

        main = QVBoxLayout()
        filter_box = QGroupBox("Filtre")
        f_layout = QHBoxLayout()
        self.filter_numar = QLineEdit()
        self.filter_concesionar = QLineEdit()
        self.filter_cod_loc = QLineEdit()
        self.filter_numar.setPlaceholderText("Număr contract")
        self.filter_concesionar.setPlaceholderText("Nume concesionar")
        self.filter_cod_loc.setPlaceholderText("Cod loc")
        for lbl, w in (("Număr:", self.filter_numar), ("Concesionar:", self.filter_concesionar), ("Cod loc:", self.filter_cod_loc)):
            f_layout.addWidget(QLabel(lbl))
            f_layout.addWidget(w)
        self.btn_clear_filters = QPushButton("Șterge filtrele")
        f_layout.addWidget(self.btn_clear_filters)
        filter_box.setLayout(f_layout)
        main.addWidget(filter_box)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setColumnHidden(0, True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        main.addWidget(self.table)

        nav = QHBoxLayout()
        self.btn_open_conc = QPushButton("Fișa concesionarului")
        self.btn_open_loc = QPushButton("Fișa locului")
        self.btn_on_map = QPushButton("Locul pe hartă")
        self.btn_loc_history = QPushButton("Istoricul locului")
        self.btn_plati = QPushButton("Plăți")
        for b in (self.btn_open_conc, self.btn_open_loc, self.btn_on_map, self.btn_loc_history, self.btn_plati):
            nav.addWidget(b)
        nav.addStretch()
        main.addLayout(nav)

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

        try:
            import access_session
            access_session.gate(self.btn_add, "add")
            access_session.gate(self.btn_edit, "edit")
            access_session.gate(self.btn_delete, "delete")
        except Exception:
            pass
        self.btn_add.clicked.connect(self.add_contract)
        self.btn_edit.clicked.connect(self.edit_contract)
        self.btn_delete.clicked.connect(self.delete_contract)
        self.btn_generate.clicked.connect(self.generate_contract)
        self.btn_refresh.clicked.connect(self.reload)
        self.btn_clear_filters.clicked.connect(self.clear_filters)
        self.btn_open_conc.clicked.connect(self.open_concesionar)
        self.btn_open_loc.clicked.connect(self.open_loc)
        self.btn_on_map.clicked.connect(self.show_on_map)
        self.btn_loc_history.clicked.connect(self.open_loc_history)
        self.btn_plati.clicked.connect(self.open_plati)
        self.table.doubleClicked.connect(lambda *_: self.edit_contract())

        if filter_cod_loc:
            self.filter_cod_loc.setText(filter_cod_loc)
            self.setWindowTitle(f"Contracte de concesiune — istoric loc {filter_cod_loc}")
        if filter_concesionar_id:
            self.setWindowTitle(f"Contracte de concesiune — istoric concesionar #{filter_concesionar_id}")

        self.filter_numar.textChanged.connect(self.reload)
        self.filter_concesionar.textChanged.connect(self.reload)
        self.filter_cod_loc.textChanged.connect(self.reload)
        self.reload()

    # ---------------------------------------------------------
    def clear_filters(self):
        self._only_concesionar_id = None
        for w in (self.filter_numar, self.filter_concesionar, self.filter_cod_loc):
            w.blockSignals(True)
            w.clear()
            w.blockSignals(False)
        self.setWindowTitle("Contracte de concesiune")
        self.reload()

    def _load_rows(self) -> List[Dict[str, Any]]:
        try:
            import entity_links as links
            return links.contracts_overview()
        except Exception:
            return []

    def reload(self):
        keep = self._selected_id()
        self.table.setRowCount(0)
        num_f = self.filter_numar.text().strip().lower()
        con_f = self.filter_concesionar.text().strip().lower()
        cod_f = self.filter_cod_loc.text().strip().lower()
        for r in self._load_rows():
            numar = str(r.get("numar_contract") or "")
            cod_loc = str(r.get("cod_loc_afisat") or "")
            nume = r.get("concesionar_nume") or ""
            if num_f and num_f not in numar.lower():
                continue
            if cod_f and cod_f not in cod_loc.lower():
                continue
            if con_f and con_f not in nume.lower():
                continue
            if self._only_concesionar_id and r.get("concesionar_id") != self._only_concesionar_id:
                continue
            perioada = " – ".join(x for x in (str(r.get("data_inceput") or ""), str(r.get("data_sfarsit") or "")) if x)
            vals = [r["id"], numar, r.get("data_contract") or "", nume, cod_loc, r.get("sector") or "",
                    r.get("parcela") or "", r.get("rand") or "", perioada, r.get("stare") or ""]
            i = self.table.rowCount()
            self.table.insertRow(i)
            for j, v in enumerate(vals):
                self.table.setItem(i, j, QTableWidgetItem("" if v is None else str(v)))
        self.table.resizeColumnsToContents()
        if keep:
            self.select_contract(keep)

    def select_contract(self, contract_id: int) -> bool:
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 0)
            if it and it.text() == str(contract_id):
                self.table.selectRow(i)
                return True
        return False

    def on_data_changed(self, entity: str, key: str):
        if entity in ("contract", "concesionar", "loc"):
            self.reload()

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
            if self.actions and hasattr(self.actions, "get_contract"):
                return self.actions.get_contract(cid)
            import database as _db
            return _db.get_contract(cid)
        except Exception:
            return None

    def _selected_or_warn(self) -> Optional[Dict]:
        cid = self._selected_id()
        if not cid:
            QMessageBox.information(self, "Info", "Selectați un contract.")
            return None
        import entity_links as links
        ctx = links.contract_context(cid)
        if not ctx:
            QMessageBox.warning(self, "Contract inexistent", "Contractul selectat nu mai există. Lista se reîncarcă.")
            self.reload()
            return None
        return ctx

    # --- navigare (nu modifică niciun contract) ---
    def open_concesionar(self):
        ctx = self._selected_or_warn()
        if not ctx:
            return
        if not ctx["concesionar_id"]:
            QMessageBox.information(self, "Info", "Contractul nu are un concesionar asociat (date istorice incomplete).")
            return
        import entity_links_ui as ui
        ui.open_concesionar_window(self, self.actions, ctx["concesionar_id"])
        self.reload()

    def open_loc(self):
        ctx = self._selected_or_warn()
        if not ctx:
            return
        if not ctx["cod_loc"] or not ctx["loc_exists"]:
            QMessageBox.information(self, "Info", "Contractul nu este legat de un loc existent. Folosiți Modifică → „Asociază / schimbă loc…”.")
            return
        import entity_links_ui as ui
        ui.open_loc_window(self, ctx["cod_loc"])
        self.reload()

    def show_on_map(self):
        ctx = self._selected_or_warn()
        if not ctx:
            return
        if not ctx["cod_loc"] or not ctx["loc_exists"]:
            QMessageBox.information(self, "Info", "Contractul nu este legat de un loc existent.")
            return
        import entity_links_ui as ui
        if not ui.show_on_map(self, ctx["cod_loc"]):
            QMessageBox.information(self, "Info", "Locul nu a putut fi evidențiat pe hartă (poligon nedesenat sau hartă indisponibilă).")

    def open_loc_history(self):
        ctx = self._selected_or_warn()
        if not ctx:
            return
        if not ctx["cod_loc"]:
            QMessageBox.information(self, "Info", "Contractul nu are loc asociat.")
            return
        import entity_links_ui as ui
        ui.open_contracts_window(self, self.actions, cod_loc=ctx["cod_loc"])
        self.reload()

    def open_plati(self):
        ctx = self._selected_or_warn()
        if not ctx:
            return
        import entity_links_ui as ui
        ui.open_plati_window(self, ctx["cod_loc"] or "", ctx["contract_id"])

    # ---------------------------------------------------------
    def add_contract(self):
        dlg = AddContractDialog(self, self.actions, existing=None, selected_cod_loc=self.selected_cod_loc,
                                selected_concesionar_id=self.selected_concesionar_id or self._only_concesionar_id)
        if dlg.exec() == QDialog.Accepted:
            self.reload()
            if dlg.get_saved_id():
                self.select_contract(dlg.get_saved_id())

    def edit_contract(self):
        cid = self._selected_id()
        if not cid:
            QMessageBox.information(self, "Info", "Selectați un contract.")
            return
        existing = self._selected_row()
        if not existing:
            QMessageBox.warning(self, "Contract inexistent", "Contractul selectat nu mai există. Lista se reîncarcă.")
            self.reload()
            return
        dlg = AddContractDialog(self, self.actions, existing=existing)
        dlg.exec()
        self.reload()
        self.select_contract(cid)

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
                import database as _db
                QMessageBox.warning(self, "Eroare", "Ștergere eșuată. " + _db.last_refusal())
        except Exception as e:
            QMessageBox.critical(self, "Eroare", f"Eroare la ștergere: {e}")
        self.reload()

    def generate_contract(self):
        cid = self._selected_id()
        if not cid:
            QMessageBox.information(self, "Info", "Selectați un contract din listă.")
            return
        generate_contract_flow(self, self.actions, cid)
