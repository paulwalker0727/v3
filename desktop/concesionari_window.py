# concesionari_window.py
import os
from typing import Optional, Dict, List, Any
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QPushButton,
    QLineEdit, QTableWidget, QTableWidgetItem, QMessageBox, QGroupBox,
    QLabel, QWidget, QSizePolicy, QHeaderView
)
from PySide6.QtCore import Qt

# Try to use the application's actions adapter first (preferred).
# If not available, fall back to a simple database module if present.
try:
    import database as db
except Exception:
    db = None


class AddConcesionarDialog(QDialog):
    """
    Dialog Add / Edit concesionar.
    If `actions` is provided it will be used to save/load; otherwise fallback to `database` module.
    """

    def __init__(self, parent, existing: Optional[Dict] = None, actions: Optional[Any] = None):
        super().__init__(parent)
        self.setWindowTitle("Concesionar")
        self.setMinimumSize(480, 320)
        self._existing = existing or {}
        self._id = self._existing.get("id")
        self.actions = actions

        layout = QVBoxLayout()
        form = QFormLayout()

        self.txt_nume = QLineEdit()
        self.txt_prenume = QLineEdit()
        self.txt_cnp = QLineEdit()
        self.txt_bi_ci = QLineEdit()
        self.txt_adresa = QLineEdit()
        self.txt_telefon = QLineEdit()
        self.txt_email = QLineEdit()
        self.txt_observatii = QLineEdit()

        form.addRow("Nume:", self.txt_nume)
        form.addRow("Prenume:", self.txt_prenume)
        form.addRow("CNP:", self.txt_cnp)
        form.addRow("BI/CI:", self.txt_bi_ci)
        form.addRow("Adresă:", self.txt_adresa)
        form.addRow("Telefon:", self.txt_telefon)
        form.addRow("Email:", self.txt_email)
        form.addRow("Observații:", self.txt_observatii)

        layout.addLayout(form)

        btns = QHBoxLayout()
        self.btn_ai_scan = QPushButton("🤖  Scanare AI Buletin")
        self.btn_ai_scan.setToolTip("Fotografiază sau încarcă buletinul — AI-ul completează automat câmpurile")
        self.btn_ai_scan.setStyleSheet(
            "QPushButton { background:#0ea5e9; color:white; padding:6px 14px; "
            "border:none; border-radius:5px; font-weight:600; }"
            "QPushButton:hover { background:#0284c7; }"
        )
        self.btn_ai_scan.clicked.connect(self._open_ai_scan)
        self.btn_save = QPushButton("Salvează")
        self.btn_cancel = QPushButton("Anulează")
        btns.addWidget(self.btn_ai_scan)
        btns.addStretch()
        btns.addWidget(self.btn_save)
        btns.addWidget(self.btn_cancel)
        layout.addLayout(btns)
        self.setLayout(layout)

        self.btn_save.clicked.connect(self._save)
        self.btn_cancel.clicked.connect(self.reject)

        if self._existing:
            self.populate(self._existing)

        self._saved_id: Optional[int] = None
        self._ai_scan_source: Optional[str] = None
        self._ai_scan_action: str = "attach"

    def _open_ai_scan(self):
        """Deschide dialog AI Scanare buletin și populează formularul."""
        try:
            from ai_scanare.scan_dialog import ScanDialog
            from ai_scanare.ai_client import DocumentType
            from ai_scanare.db_scans import save_scan
        except Exception as e:
            QMessageBox.warning(self, "Modul AI indisponibil", str(e))
            return
        import os as _os
        dlg = ScanDialog(self, DocumentType.BULETIN, allow_type_change=False)
        if dlg.exec() != QDialog.Accepted:
            return
        data = dlg.get_extracted_data()
        bi_ci = f"{data.get('serie', '')} {data.get('numar', '')}".strip()
        self.txt_nume.setText(data.get("nume", ""))
        self.txt_prenume.setText(data.get("prenume", ""))
        self.txt_cnp.setText(data.get("cnp", ""))
        self.txt_bi_ci.setText(bi_ci)
        self.txt_adresa.setText(data.get("adresa", ""))
        obs = (f"Data nașterii: {data.get('data_nasterii', '')} · "
               f"Emitent: {data.get('emitent', '')} · "
               f"Elib: {data.get('data_eliberarii', '')} · "
               f"Valab: {data.get('valabilitate', '')} · "
               f"Sex: {data.get('sex', '')}")
        prev = self.txt_observatii.text().strip()
        self.txt_observatii.setText(f"{prev} | {obs}" if prev else obs)
        self._ai_scan_source = dlg.get_source_file()
        self._ai_scan_action = dlg.get_file_action()
        try:
            save_scan(
                document_type=DocumentType.BULETIN,
                filename=_os.path.basename(self._ai_scan_source or ""),
                file_path=self._ai_scan_source if self._ai_scan_action != "delete" else None,
                extracted={"fields": data},
                status="processed",
            )
        except Exception:
            pass
        QMessageBox.information(self, "Câmpuri completate",
                                "Datele au fost extrase. Verifică și apasă Salvează.")

    def _attach_ai_file_if_any(self, concesionar_id):
        """Atașează fișierul scanat AI la concesionar, arhivă sau șterge."""
        import os as _os
        src = getattr(self, "_ai_scan_source", None)
        action = getattr(self, "_ai_scan_action", "attach")
        if not src or not _os.path.exists(src):
            return
        try:
            if action == "delete":
                try: _os.remove(src)
                except Exception: pass
                return
            if db is None:
                return
            if action == "attach" and concesionar_id:
                db.add_document("concesionar", concesionar_id, "buletin", src, copy=True)
            elif action == "archive":
                db.import_media_file(src, subdir="arhiva")
        except Exception as e:
            print(f"[_attach_ai_file_if_any] {e}")

    def populate(self, data: Dict):
        self.txt_nume.setText(str(data.get("nume", "") or ""))
        self.txt_prenume.setText(str(data.get("prenume", "") or ""))
        self.txt_cnp.setText(str(data.get("cnp", "") or ""))
        self.txt_bi_ci.setText(str(data.get("bi_ci", "") or ""))
        self.txt_adresa.setText(str(data.get("adresa", "") or ""))
        self.txt_telefon.setText(str(data.get("telefon", "") or ""))
        self.txt_email.setText(str(data.get("email", "") or ""))
        self.txt_observatii.setText(str(data.get("observatii", "") or ""))

    def _gather(self) -> Dict[str, Any]:
        data = {
            "nume": self.txt_nume.text().strip(),
            "prenume": self.txt_prenume.text().strip(),
            "cnp": self.txt_cnp.text().strip(),
            "bi_ci": self.txt_bi_ci.text().strip(),
            "adresa": self.txt_adresa.text().strip(),
            "telefon": self.txt_telefon.text().strip(),
            "email": self.txt_email.text().strip(),
            "observatii": self.txt_observatii.text().strip(),
        }
        if self._id:
            data["id"] = self._id
        return data

    def _save(self):
        if not (self.txt_nume.text().strip() or self.txt_prenume.text().strip()):
            QMessageBox.warning(self, "Eroare", "Nume sau Prenume obligatoriu.")
            return

        data = self._gather()

        # Prefer actions.save_concesionar if available
        try:
            if self.actions and hasattr(self.actions, "save_concesionar"):
                saved_id = self.actions.save_concesionar(data, concesionar_id=data.get("id"))
            elif db and hasattr(db, "save_concesionar"):
                saved_id = db.save_concesionar(data, concesionar_id=data.get("id"))
            else:
                saved_id = None
        except Exception as e:
            QMessageBox.warning(self, "Eroare", f"Nu am putut salva concesionarul: {e}")
            return

        if saved_id:
            self._saved_id = saved_id
            self._attach_ai_file_if_any(saved_id)
            QMessageBox.information(self, "OK", "Concesionar salvat.")
            self.accept()
        else:
            self._saved_id = data.get("id")
            self._attach_ai_file_if_any(self._saved_id)
            self.accept()

    def get_saved_id(self) -> Optional[int]:
        return self._saved_id

    def get_data(self) -> Dict[str, Any]:
        return self._gather()


class ConcesionariWindow(QDialog):
    """
    Concesionari window implemented without QSql to avoid QSql circular issues.
    Uses `actions` (preferred) or `database` fallback to load/save/delete concesionari.
    """

    def __init__(self, parent=None, actions: Optional[Any] = None):
        super().__init__(parent)
        self.setWindowTitle("Concesionari")
        self.setMinimumSize(1100, 700)
        self.actions = actions

        main_layout = QVBoxLayout()

        # Filters group
        filter_box = QGroupBox("Filtre generale")
        filter_layout = QFormLayout()
        self.filter_nume = QLineEdit()
        self.filter_prenume = QLineEdit()
        self.filter_cnp = QLineEdit()
        self.filter_adresa = QLineEdit()
        self.filter_telefon = QLineEdit()
        self.filter_email = QLineEdit()
        filter_layout.addRow("Nume:", self.filter_nume)
        filter_layout.addRow("Prenume:", self.filter_prenume)
        filter_layout.addRow("CNP:", self.filter_cnp)
        filter_layout.addRow("Adresă:", self.filter_adresa)
        filter_layout.addRow("Telefon:", self.filter_telefon)
        filter_layout.addRow("Email:", self.filter_email)
        filter_box.setLayout(filter_layout)

        # Topology filters
        topo_box = QGroupBox("Topologie (opțional)")
        topo_layout = QFormLayout()
        self.filter_sector = QLineEdit()
        self.filter_parcela = QLineEdit()
        self.filter_rand = QLineEdit()
        self.filter_cod_loc = QLineEdit()
        topo_layout.addRow("Sector:", self.filter_sector)
        topo_layout.addRow("Parcela:", self.filter_parcela)
        topo_layout.addRow("Rând:", self.filter_rand)
        topo_layout.addRow("Cod loc:", self.filter_cod_loc)
        topo_box.setLayout(topo_layout)

        # Table (left)
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels([
            "ID", "Nume", "Prenume", "CNP", "BI/CI", "Adresă", "Telefon", "Email", "Cod loc"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)

        # Buttons
        btn_row = QHBoxLayout()
        self.btn_evid = QPushButton("Evidentiere pe Harta")
        self.btn_rapoarte = QPushButton("Rapoarte")
        self.btn_add = QPushButton("Adaugă")
        self.btn_edit = QPushButton("Modifică")
        self.btn_phone = QPushButton("Continuă pe telefon")
        self.btn_phone.setToolTip("Generează un cod QR temporar: încarci un document/fotografie de pe telefon la concesionarul selectat")
        self.btn_delete = QPushButton("Șterge")
        self.btn_reload = QPushButton("Reîncarcă")
        btn_row.addWidget(self.btn_evid)
        btn_row.addWidget(self.btn_rapoarte)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_add)
        btn_row.addWidget(self.btn_edit)
        btn_row.addWidget(self.btn_phone)
        btn_row.addWidget(self.btn_delete)
        btn_row.addWidget(self.btn_reload)

        # Layout assembly
        top_panel = QHBoxLayout()
        left_widget = QWidget()
        left_layout = QVBoxLayout()
        left_layout.addWidget(self.table)
        left_widget.setLayout(left_layout)
        left_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        right_widget = QWidget()
        right_layout = QVBoxLayout()
        right_layout.addWidget(filter_box)
        right_layout.addWidget(topo_box)
        right_layout.addStretch()
        right_widget.setLayout(right_layout)
        right_widget.setFixedWidth(360)

        top_panel.addWidget(left_widget)
        top_panel.addWidget(right_widget)

        main_layout.addLayout(top_panel)
        main_layout.addLayout(btn_row)
        self.setLayout(main_layout)

        # Connect signals
        self.btn_add.clicked.connect(self.add_dialog)
        self.btn_edit.clicked.connect(self.edit_dialog)
        self.btn_phone.clicked.connect(self.phone_upload)
        self.btn_delete.clicked.connect(self.delete_record)
        self.btn_reload.clicked.connect(self.reload)
        self.btn_evid.clicked.connect(self.highlight_on_map)
        self.btn_rapoarte.clicked.connect(self.open_reports)

        # Filters
        self.filter_nume.textChanged.connect(self.reload)
        self.filter_prenume.textChanged.connect(self.reload)
        self.filter_cnp.textChanged.connect(self.reload)
        self.filter_adresa.textChanged.connect(self.reload)
        self.filter_telefon.textChanged.connect(self.reload)
        self.filter_email.textChanged.connect(self.reload)
        self.filter_sector.textChanged.connect(self.reload)
        self.filter_parcela.textChanged.connect(self.reload)
        self.filter_rand.textChanged.connect(self.reload)
        self.filter_cod_loc.textChanged.connect(self.reload)

        # internal cache of rows
        self._rows: List[Dict[str, Any]] = []

        # initial load
        self.reload()

    # -------------------------
    # Data loading / rendering
    # -------------------------
    def _load_all(self) -> List[Dict[str, Any]]:
        """
        Load concesionari records using actions or db fallback.
        Expected return: list of dicts with keys: id, nume, prenume, cnp, bi_ci, adresa, telefon, email, observatii, cod_loc (optional)
        """
        try:
            if self.actions and hasattr(self.actions, "load_concesionari"):
                return self.actions.load_concesionari()
            elif db and hasattr(db, "load_concesionari"):
                return db.load_concesionari()
        except Exception:
            pass
        return []

    def reload(self):
        self.table.setRowCount(0)
        rows = self._load_all()
        self._rows = rows or []

        # apply filters in Python for simplicity
        def match(r: Dict[str, Any]) -> bool:
            def contains(field, value):
                if not value:
                    return True
                v = str(r.get(field, "") or "").lower()
                return value.lower() in v

            if not contains("nume", self.filter_nume.text()):
                return False
            if not contains("prenume", self.filter_prenume.text()):
                return False
            if not contains("cnp", self.filter_cnp.text()):
                return False
            if not contains("adresa", self.filter_adresa.text()):
                return False
            if not contains("telefon", self.filter_telefon.text()):
                return False
            if not contains("email", self.filter_email.text()):
                return False

            # topology filters: try direct fields first, then check extra_data if present
            topo_sector = self.filter_sector.text().strip().lower()
            topo_parcela = self.filter_parcela.text().strip().lower()
            topo_rand = self.filter_rand.text().strip().lower()
            topo_cod = self.filter_cod_loc.text().strip().lower()

            # helper to check both top-level and extra_data
            def topo_check(key, val):
                if not val:
                    return True
                # direct field
                if key in r and r.get(key):
                    if val in str(r.get(key)).lower():
                        return True
                # extra_data
                extra = r.get("extra", {}) or {}
                if extra and key in extra and extra.get(key):
                    if val in str(extra.get(key)).lower():
                        return True
                # also check cod_loc top-level
                if key == "cod_loc":
                    if val in str(r.get("cod_loc", "")).lower():
                        return True
                return False

            if not topo_check("sector", topo_sector):
                return False
            if not topo_check("parcela", topo_parcela):
                return False
            if not topo_check("rand", topo_rand):
                return False
            if not topo_check("cod_loc", topo_cod):
                return False

            return True

        filtered = [r for r in self._rows if match(r)]

        for r in filtered:
            row_idx = self.table.rowCount()
            self.table.insertRow(row_idx)
            self.table.setItem(row_idx, 0, QTableWidgetItem(str(r.get("id") or "")))
            self.table.setItem(row_idx, 1, QTableWidgetItem(str(r.get("nume") or "")))
            self.table.setItem(row_idx, 2, QTableWidgetItem(str(r.get("prenume") or "")))
            self.table.setItem(row_idx, 3, QTableWidgetItem(str(r.get("cnp") or "")))
            self.table.setItem(row_idx, 4, QTableWidgetItem(str(r.get("bi_ci") or "")))
            self.table.setItem(row_idx, 5, QTableWidgetItem(str(r.get("adresa") or "")))
            self.table.setItem(row_idx, 6, QTableWidgetItem(str(r.get("telefon") or "")))
            self.table.setItem(row_idx, 7, QTableWidgetItem(str(r.get("email") or "")))
            # cod_loc may be top-level or inside extra
            cod_loc = r.get("cod_loc") or (r.get("extra") or {}).get("cod_loc", "")
            self.table.setItem(row_idx, 8, QTableWidgetItem(str(cod_loc)))

        self.table.resizeColumnsToContents()

    # -------------------------
    # CRUD operations
    # -------------------------
    def _selected_row_data(self) -> Optional[Dict[str, Any]]:
        idx = self.table.currentRow()
        if idx < 0:
            return None
        try:
            id_item = self.table.item(idx, 0)
            if not id_item:
                return None
            cid = id_item.text()
            for r in self._rows:
                if str(r.get("id")) == str(cid):
                    return r
        except Exception:
            return None
        return None

    def add_dialog(self):
        dlg = AddConcesionarDialog(self, existing=None, actions=self.actions)
        if dlg.exec() == QDialog.Accepted:
            saved_id = dlg.get_saved_id()
            # If actions handled saving, reload; otherwise try fallback insert via actions/db
            if saved_id:
                self.reload()
                return
            # fallback: try to save via actions or db if AddConcesionarDialog didn't call actions
            data = dlg.get_data()
            try:
                if self.actions and hasattr(self.actions, "save_concesionar"):
                    self.actions.save_concesionar(data)
                elif db and hasattr(db, "save_concesionar"):
                    db.save_concesionar(data)
                self.reload()
            except Exception as e:
                QMessageBox.warning(self, "Eroare", f"Nu am putut salva concesionarul: {e}")

    def edit_dialog(self):
        row = self._selected_row_data()
        if not row:
            QMessageBox.information(self, "Info", "Selectați o înregistrare pentru modificare.")
            return
        dlg = AddConcesionarDialog(self, existing=row, actions=self.actions)
        if dlg.exec() == QDialog.Accepted:
            data = dlg.get_data()
            try:
                if self.actions and hasattr(self.actions, "save_concesionar"):
                    self.actions.save_concesionar(data, concesionar_id=data.get("id"))
                elif db and hasattr(db, "save_concesionar"):
                    db.save_concesionar(data, concesionar_id=data.get("id"))
                self.reload()
            except Exception as e:
                QMessageBox.warning(self, "Eroare", f"Nu am putut salva modificările: {e}")

    def phone_upload(self):
        """Pasul 6: QR/token temporar pentru concesionarul selectat; documentul de pe telefon ajunge prin FastAPI în Storage."""
        row = self._selected_row_data()
        if not row or not row.get("id"):
            QMessageBox.information(self, "Info", "Selectați un concesionar.")
            return
        from mobile_upload_dialog import open_phone_upload
        nume = f"{row.get('nume') or ''} {row.get('prenume') or ''}".strip()
        open_phone_upload(self, "concesionar", row["id"], f"Concesionar {nume}", on_done=self.reload)

    def delete_record(self):
        row = self._selected_row_data()
        if not row:
            QMessageBox.information(self, "Info", "Selectați o înregistrare pentru ștergere.")
            return
        cid = row.get("id")
        if not cid:
            QMessageBox.warning(self, "Eroare", "Nu am putut determina id-ul.")
            return
        reply = QMessageBox.question(self, "Șterge", "Sigur doriți să ștergeți concesionarul?", QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            if self.actions and hasattr(self.actions, "delete_concesionar"):
                ok = self.actions.delete_concesionar(cid)
            elif db and hasattr(db, "delete_concesionar"):
                ok = db.delete_concesionar(cid)
            else:
                ok = False
            if ok:
                QMessageBox.information(self, "OK", "Șters.")
                self.reload()
            else:
                QMessageBox.warning(self, "Eroare", "Ștergere eșuată.")
        except Exception as e:
            QMessageBox.warning(self, "Eroare", f"Ștergere eșuată: {e}")

    # -------------------------
    # Actions / hooks
    # -------------------------
    def highlight_on_map(self):
        row = self._selected_row_data()
        if not row:
            QMessageBox.information(self, "Info", "Selectați o înregistrare pentru evidențiere.")
            return
        # cod_loc may be top-level or inside extra
        cod_loc = row.get("cod_loc") or (row.get("extra") or {}).get("cod_loc")
        if cod_loc and hasattr(self.parent(), "highlight_loc_by_cod"):
            try:
                self.parent().highlight_loc_by_cod(cod_loc)
            except Exception:
                QMessageBox.information(self, "Info", "Funcție evidențiere indisponibilă.")
        else:
            QMessageBox.information(self, "Info", "Nu există cod loc asociat sau funcția nu este disponibilă.")

    def open_reports(self):
        QMessageBox.information(self, "Rapoarte", "Funcționalitate Rapoarte — în curs de implementare.")
