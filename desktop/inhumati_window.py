# inhumati_window.py
"""
Fereastra pentru gestionarea persoanelor înhumate.

Îmbunătățiri față de versiunea anterioară:
    - Folosește sqlite3 (via `database.py`) — aceeași conexiune ca restul
      aplicației. Elimină problemele de sincronizare cauzate de QSqlDatabase.
    - Folosește `QStandardItemModel` cu `QSortFilterProxyModel` pentru
      filtrare rapidă și fără interogări repetate.
    - Implementează `on_data_changed(entity, key)` — se conectează la
      semnalul global `signals.data_changed` din MainWindow și face refresh
      automat când datele se schimbă din altă parte a aplicației.
    - Ștergere cu confirmare, refresh instant după fiecare operație.
"""

from typing import Optional, Dict, List

from PySide6.QtCore import Qt, QSortFilterProxyModel, QRegularExpression
from PySide6.QtGui import QStandardItemModel, QStandardItem
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit, QFormLayout,
    QTableView, QGroupBox, QMessageBox, QInputDialog, QAbstractItemView,
    QHeaderView,
)

import database as db
from add_inhumat_dialog import AddInhumatDialog


# Coloanele afișate în tabel (în ordine)
COLUMNS = [
    ("cod_loc",         "Cod loc"),
    ("nume",            "Nume"),
    ("prenume",         "Prenume"),
    ("cnp",             "CNP"),
    ("data_nasterii",   "Data nașterii"),
    ("data_deces",      "Data deces"),
    ("data_inhumare",   "Data înhumare"),
    ("data_deshumare",  "Data deshumare"),
    ("sex",             "Sex"),
    ("cauza_deces",     "Cauza deces"),
    ("profesie",        "Profesie"),
    ("religie",         "Religie"),
    ("ultima_adresa",   "Ultima adresă"),
    ("observatii",      "Observații"),
]
COL_ID_ROLE = Qt.UserRole + 1  # rol pentru stocarea id-ului intern al înhumatului


class InhumatiWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Înhumați")
        self.setMinimumSize(1200, 700)

        # ---- model ----
        self.model = QStandardItemModel(self)
        self.model.setHorizontalHeaderLabels([label for _, label in COLUMNS])

        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)  # filtrare pe toate coloanele

        # ---- tabel ----
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.doubleClicked.connect(self.edit_record_dialog)

        # ---- filtre ----
        self.filter_generic = QLineEdit()
        self.filter_generic.setPlaceholderText("Caută în toate coloanele (nume, cod, CNP...)")
        self.filter_generic.textChanged.connect(self._apply_filter)

        self.filter_cod_loc = QLineEdit()
        self.filter_cod_loc.setPlaceholderText("Ex: A-1-23")

        self.filter_nume = QLineEdit()
        self.filter_prenume = QLineEdit()

        filter_box = QGroupBox("Filtrare")
        form = QFormLayout()
        form.addRow("Căutare rapidă:", self.filter_generic)
        form.addRow("Cod loc:", self.filter_cod_loc)
        form.addRow("Nume:", self.filter_nume)
        form.addRow("Prenume:", self.filter_prenume)
        filter_box.setLayout(form)

        for w in (self.filter_cod_loc, self.filter_nume, self.filter_prenume):
            w.textChanged.connect(self._apply_filter)

        # ---- butoane ----
        self.btn_add = QPushButton("➕ Adaugă")
        self.btn_edit = QPushButton("✏️ Modifică")
        self.btn_delete = QPushButton("🗑️ Șterge")
        self.btn_refresh = QPushButton("🔄 Reîncarcă")

        self.btn_add.clicked.connect(self.add_record_dialog)
        self.btn_edit.clicked.connect(self.edit_record_dialog)
        self.btn_delete.clicked.connect(self.delete_record)
        self.btn_refresh.clicked.connect(self.reload_data)

        btns = QHBoxLayout()
        btns.addWidget(self.btn_add)
        btns.addWidget(self.btn_edit)
        btns.addWidget(self.btn_delete)
        btns.addWidget(self.btn_refresh)
        btns.addStretch()

        # ---- layout ----
        main = QVBoxLayout()
        main.addWidget(self.table)
        main.addWidget(filter_box)
        main.addLayout(btns)
        self.setLayout(main)

        # ---- încărcare inițială ----
        self.reload_data()

    # -------------------------------------------------------------------------
    # Helpers UI
    # -------------------------------------------------------------------------
    def _main_window(self):
        """Găsește main window (cel cu `.actions`) urcând în lanțul de părinți."""
        w = self.parent()
        while w is not None and not hasattr(w, "actions"):
            w = w.parent()
        return w

    def _selected_inhumat_id(self) -> Optional[int]:
        idx = self.table.currentIndex()
        if not idx.isValid():
            return None
        src_idx = self.proxy.mapToSource(idx)
        item = self.model.item(src_idx.row(), 0)
        if item is None:
            return None
        val = item.data(COL_ID_ROLE)
        return int(val) if val is not None else None

    def _selected_cod_loc(self) -> Optional[str]:
        idx = self.table.currentIndex()
        if not idx.isValid():
            return None
        src_idx = self.proxy.mapToSource(idx)
        item = self.model.item(src_idx.row(), 0)
        return item.text() if item else None

    # -------------------------------------------------------------------------
    # Încărcare date
    # -------------------------------------------------------------------------
    def reload_data(self):
        """Reîncarcă toți înhumații din DB și repopulează modelul."""
        try:
            records = db.load_all_inhumati()
        except Exception as e:
            QMessageBox.critical(self, "Eroare DB", f"Nu am putut citi înhumații: {e}")
            return

        self.model.removeRows(0, self.model.rowCount())
        for rec in records:
            row_items: List[QStandardItem] = []
            for key, _ in COLUMNS:
                val = rec.get(key, "")
                item = QStandardItem("" if val is None else str(val))
                row_items.append(item)
            # stocăm id-ul intern pe primul item (invizibil)
            row_items[0].setData(rec.get("id"), COL_ID_ROLE)
            self.model.appendRow(row_items)

        self.table.resizeColumnsToContents()

    def _apply_filter(self):
        """Aplică filtrele: dacă filter_generic e completat -> global search,
           altfel filtrele individuale (nume/prenume/cod_loc)."""
        generic = self.filter_generic.text().strip()
        if generic:
            self.proxy.setFilterKeyColumn(-1)
            self.proxy.setFilterRegularExpression(QRegularExpression(
                QRegularExpression.escape(generic), QRegularExpression.CaseInsensitiveOption
            ))
            return

        # combinăm filtrele individuale într-un regex care merge pe toate coloanele
        cod = self.filter_cod_loc.text().strip()
        nume = self.filter_nume.text().strip()
        prenume = self.filter_prenume.text().strip()

        # aplicăm filtrare custom prin subclasarea comportamentului implicit:
        # folosim un filter care verifică coloanele individuale
        def _custom_accept(source_row: int, _source_parent) -> bool:
            model = self.model
            cod_v = (model.item(source_row, 0).text() if model.item(source_row, 0) else "").lower()
            nume_v = (model.item(source_row, 1).text() if model.item(source_row, 1) else "").lower()
            prenume_v = (model.item(source_row, 2).text() if model.item(source_row, 2) else "").lower()
            if cod and cod.lower() not in cod_v:
                return False
            if nume and nume.lower() not in nume_v:
                return False
            if prenume and prenume.lower() not in prenume_v:
                return False
            return True

        # atașăm dinamic un filterAcceptsRow
        self.proxy.filterAcceptsRow = _custom_accept  # type: ignore
        self.proxy.invalidateFilter()

    # -------------------------------------------------------------------------
    # CRUD
    # -------------------------------------------------------------------------
    def add_record_dialog(self):
        """Adaugă unul sau mai mulți înhumați pentru un cod_loc."""
        main = self._main_window()
        cod_loc = getattr(main, "selected_cod_loc", None) if main else None

        if not cod_loc:
            cod_loc, ok = QInputDialog.getText(self, "Cod loc", "Introduceți codul locului:")
            if not ok or not cod_loc.strip():
                return
            cod_loc = cod_loc.strip()

        # verificăm existența locului
        if db.get_loc_id_by_cod(cod_loc) is None:
            QMessageBox.warning(
                self, "Loc inexistent",
                f"Nu există niciun loc de veci cu codul '{cod_loc}'.\n"
                f"Adăugați mai întâi locul."
            )
            return

        # buclă: după fiecare adăugare întrebăm dacă mai vrem unul
        while True:
            dlg = AddInhumatDialog(self, cod_loc, existing=None)
            if dlg.exec() != QDialog.Accepted:
                break

            data = dlg.get_data()
            try:
                if main and hasattr(main, "actions"):
                    saved_id = main.actions.save_inhumat(cod_loc, data)
                else:
                    saved_id = db.save_inhumat(cod_loc, data)
                if not saved_id:
                    QMessageBox.warning(self, "Eroare", "Salvarea a eșuat (rezultat nul).")
                    break
                failed_docs = []
                for _doc in dlg.get_documents():
                    did = db.add_document("inhumat", saved_id, _doc["doc_type"],
                                          _doc["path"], nr_evidenta=_doc.get("nr_evidenta", ""))
                    if not did:
                        failed_docs.append(_doc["path"])
                if failed_docs:
                    QMessageBox.warning(self, "Documente nesalvate",
                                        "Aceste fișiere NU au putut fi salvate:\n\n"
                                        + "\n".join(failed_docs))
            except Exception as e:
                QMessageBox.warning(self, "Eroare", f"Nu am putut salva: {e}")
                break

            self.reload_data()

            reply = QMessageBox.question(
                self, "Adaugă încă unul?",
                "Doriți să adăugați încă un înhumat pentru același loc?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                break

    def edit_record_dialog(self, *_args):
        inhumat_id = self._selected_inhumat_id()
        cod_loc = self._selected_cod_loc()
        if not inhumat_id or not cod_loc:
            QMessageBox.information(self, "Info", "Selectați o înregistrare pentru modificare.")
            return

        existing = db.get_inhumat(inhumat_id)
        if not existing:
            QMessageBox.warning(self, "Eroare", "Înregistrarea nu mai există în DB.")
            self.reload_data()
            return

        dlg = AddInhumatDialog(self, cod_loc, existing=existing)
        if dlg.exec() != QDialog.Accepted:
            return

        data = dlg.get_data()
        try:
            main = self._main_window()
            if main and hasattr(main, "actions"):
                main.actions.save_inhumat(cod_loc, data, inhumat_id=inhumat_id)
            else:
                db.save_inhumat(cod_loc, data, inhumat_id=inhumat_id)
            failed_docs = []
            for _doc in dlg.get_documents():
                did = db.add_document("inhumat", inhumat_id, _doc["doc_type"],
                                      _doc["path"], nr_evidenta=_doc.get("nr_evidenta", ""))
                if not did:
                    failed_docs.append(_doc["path"])
            if failed_docs:
                QMessageBox.warning(self, "Documente nesalvate",
                                    "Aceste fișiere NU au putut fi salvate:\n\n"
                                    + "\n".join(failed_docs))
            self.reload_data()
        except Exception as e:
            QMessageBox.warning(self, "Eroare", f"Nu am putut salva modificările: {e}")

    def delete_record(self):
        inhumat_id = self._selected_inhumat_id()
        if not inhumat_id:
            QMessageBox.information(self, "Info", "Selectați o înregistrare pentru ștergere.")
            return

        info = db.get_inhumat(inhumat_id) or {}
        nume_complet = f"{info.get('nume','')} {info.get('prenume','')}".strip() or f"ID {inhumat_id}"

        reply = QMessageBox.question(
            self, "Confirmare ștergere",
            f"Ștergeți definitiv înregistrarea pentru <b>{nume_complet}</b>?\n\nAcțiunea NU poate fi anulată.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        try:
            main = self._main_window()
            if main and hasattr(main, "actions"):
                ok = main.actions.delete_inhumat(inhumat_id)
            else:
                ok = db.delete_inhumat(inhumat_id)
            if ok:
                self.reload_data()
            else:
                QMessageBox.warning(self, "Eroare", "Ștergerea a eșuat.")
        except Exception as e:
            QMessageBox.warning(self, "Eroare", f"Nu am putut șterge: {e}")

    # -------------------------------------------------------------------------
    # Sincronizare cu semnalul global
    # -------------------------------------------------------------------------
    def on_data_changed(self, entity: str, _key: str):
        """
        Slot conectat la `signals.data_changed` din MainWindow.
        Se auto-refreshuim doar când e relevant (inhumat / loc).
        """
        if entity in ("inhumat", "loc"):
            self.reload_data()
