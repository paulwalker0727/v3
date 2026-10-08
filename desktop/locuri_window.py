# locuri_window.py
from typing import Dict, Any, Optional, List
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLineEdit, QComboBox, QPushButton, QTableWidget, QTableWidgetItem,
    QMessageBox, QInputDialog, QCheckBox, QAbstractItemView, QTextEdit
)
from PySide6.QtCore import Qt


# ============================================================
# DIALOG EDITARE LOC
# ============================================================
class EditLocDialog(QDialog):
    """
    Dialog pentru editarea unui loc de veci.
    Folosește EditorActions.load_loc_by_cod / save_loc_metadata / save_extra_data.
    """
    def __init__(self, parent, cod_loc: str, open_inhumati_after_save: bool = False):
        super().__init__(parent)
        self.setWindowTitle(f"Editează loc {cod_loc}")
        self.setMinimumSize(520, 520)

        self.cod_loc = cod_loc
        self.actions = getattr(parent, "actions", None)
        self.signals = getattr(parent, "signals", None)
        self._open_inhumati_after_save = open_inhumati_after_save
        self._parent_window = parent

        main_layout = QVBoxLayout()
        form = QFormLayout()

        # Topologie / identificare
        self.txt_sector = QLineEdit()
        self.txt_parcela = QLineEdit()
        self.txt_rand = QLineEdit()
        self.txt_numar = QLineEdit()

        self.txt_parcela.setReadOnly(True)
        self.txt_rand.setReadOnly(True)
        self.txt_numar.setReadOnly(True)

        self.chk_allow_edit = QCheckBox("Permite editare Parcela / Rând / Număr")
        self.chk_allow_edit.stateChanged.connect(self._on_allow_edit_changed)

        # Combo-uri
        self.cmb_capacitate = QComboBox()
        self.cmb_capacitate.addItems(["", "Simplu", "Dublu", "Cvadrupul"])

        self.cmb_disponibilitate = QComboBox()
        self.cmb_disponibilitate.addItems(["", "Ocupat", "Liber", "Posibil liber"])

        self.cmb_tip = QComboBox()
        self.cmb_tip.addItems(["", "Cavou", "Cruce", "Împrejmuire", "Mormânt"])

        self.cmb_stare = QComboBox()
        self.cmb_stare.addItems(["", "Foarte bună", "Bună", "Mai puțin bună"])

        self.cmb_categorie = QComboBox()
        self.cmb_categorie.addItems(["", "Categoria I", "Categoria II", "Categoria III"])

        # Alte câmpuri
        self.txt_suprafata = QLineEdit()
        self.txt_nr_vechi = QLineEdit()
        self.txt_numar_cf = QLineEdit()
        self.txt_observatii = QTextEdit()

        form.addRow("Sector:", self.txt_sector)
        form.addRow("Parcela:", self.txt_parcela)
        form.addRow("Rând:", self.txt_rand)
        form.addRow("Număr:", self.txt_numar)
        form.addRow(self.chk_allow_edit)
        form.addRow("Capacitate:", self.cmb_capacitate)
        form.addRow("Disponibilitate:", self.cmb_disponibilitate)
        form.addRow("Tip:", self.cmb_tip)
        form.addRow("Stare:", self.cmb_stare)
        form.addRow("Categorie:", self.cmb_categorie)
        form.addRow("Suprafață:", self.txt_suprafata)
        form.addRow("Nr. vechi:", self.txt_nr_vechi)
        form.addRow("Număr CF:", self.txt_numar_cf)
        form.addRow("Observații:", self.txt_observatii)

        btns = QHBoxLayout()
        self.btn_save = QPushButton("Salvează")
        self.btn_cancel = QPushButton("Anulează")
        btns.addWidget(self.btn_save)
        btns.addWidget(self.btn_cancel)

        main_layout.addLayout(form)
        main_layout.addLayout(btns)
        self.setLayout(main_layout)

        self.btn_save.clicked.connect(self._on_save)
        self.btn_cancel.clicked.connect(self.reject)

        self._load_or_parse()

    def _on_allow_edit_changed(self, state: int):
        allow = state == Qt.Checked
        self.txt_parcela.setReadOnly(not allow)
        self.txt_rand.setReadOnly(not allow)
        self.txt_numar.setReadOnly(not allow)

    def _parse_cod_loc(self):
        cod = (self.cod_loc or "").strip()
        parts = cod.split("-")
        parcela = ""
        rand = ""
        numar = ""
        if len(parts) >= 1 and parts[0]:
            parcela = parts[0][0].upper()
        if len(parts) >= 2 and parts[1]:
            digits = "".join(ch for ch in parts[1] if ch.isdigit())
            rand = digits[:2].rjust(2, "0") if digits else ""
        if len(parts) >= 3 and parts[2]:
            digits = "".join(ch for ch in parts[2] if ch.isdigit())
            numar = digits[:2].rjust(2, "0") if digits else ""
        return parcela, rand, numar

    def _load_or_parse(self):
        data: Dict[str, Any] = {}
        if self.actions:
            try:
                loaded = self.actions.load_loc_by_cod(self.cod_loc)
                if loaded:
                    data = loaded
            except Exception:
                data = {}

        if data:
            self.txt_sector.setText(str(data.get("sector", "") or ""))
            self.txt_parcela.setText(str(data.get("parcela", "") or ""))
            self.txt_rand.setText(str(data.get("rand", "") or ""))
            self.txt_numar.setText(str(data.get("numar", "") or ""))

            cap = data.get("capacitate", "") or ""
            self.cmb_capacitate.setCurrentText(cap)

            disp = data.get("disponibilitate", "") or ""
            self.cmb_disponibilitate.setCurrentText(disp)

            tip = data.get("tip", "") or ""
            self.cmb_tip.setCurrentText(tip)

            stare = data.get("stare", "") or ""
            self.cmb_stare.setCurrentText(stare)

            cat = data.get("categorie", "") or ""
            self.cmb_categorie.setCurrentText(cat)

            self.txt_suprafata.setText(str(data.get("suprafata", "") or ""))
            self.txt_nr_vechi.setText(str(data.get("nr_vechi", "") or ""))
            self.txt_numar_cf.setText(str(data.get("numar_cf", "") or ""))

            extra = data.get("extra_data", {}) or {}
            self.txt_observatii.setPlainText(str(extra.get("observatii", "") or ""))
            return

        parcela, rand, numar = self._parse_cod_loc()
        self.txt_parcela.setText(parcela)
        self.txt_sector.setText(parcela)
        self.txt_rand.setText(rand)
        self.txt_numar.setText(numar)

    def _on_save(self):
        if not self.actions:
            QMessageBox.warning(self, "Eroare", "Nu există conexiune la acțiuni.")
            return

        metadata = {
            "sector": self.txt_sector.text().strip(),
            "parcela": self.txt_parcela.text().strip(),
            "rand": self.txt_rand.text().strip(),
            "numar": self.txt_numar.text().strip(),
            "capacitate": self.cmb_capacitate.currentText().strip(),
            "disponibilitate": self.cmb_disponibilitate.currentText().strip(),
            "tip": self.cmb_tip.currentText().strip(),
            "stare": self.cmb_stare.currentText().strip(),
            "categorie": self.cmb_categorie.currentText().strip(),
            "suprafata": self.txt_suprafata.text().strip(),
            "nr_vechi": self.txt_nr_vechi.text().strip(),
            "numar_cf": self.txt_numar_cf.text().strip(),
        }
        extra = {"observatii": self.txt_observatii.toPlainText().strip()}

        try:
            self.actions.save_loc_metadata(self.cod_loc, metadata)
            self.actions.save_extra_data(self.cod_loc, extra)
            if self.signals and hasattr(self.signals, "data_changed"):
                self.signals.data_changed.emit("loc", self.cod_loc)
        except Exception as e:
            QMessageBox.warning(self, "Eroare", f"Nu am putut salva: {e}")
            return

        self.accept()

        if self._open_inhumati_after_save and self._parent_window is not None:
            try:
                from inhumati_window import InhumatiWindow
                inh = InhumatiWindow(self._parent_window)
                if hasattr(inh, "open_for_cod"):
                    inh.open_for_cod(self.cod_loc)
                inh.exec()
            except Exception:
                pass


# ============================================================
# FEREASTRĂ PRINCIPALĂ LOCURI DE VECI
# ============================================================
class LocuriWindow(QDialog):
    """
    Fereastra principală pentru administrarea locurilor de veci.
    Folosește EditorActions.search_locuri + load_loc_by_cod.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Locuri de veci")
        self.setMinimumSize(1200, 700)

        self.parent = parent
        self.actions = getattr(parent, "actions", None)
        self.signals = getattr(parent, "signals", None)

        # filtre
        self.filter_capacitate = QComboBox()
        self.filter_capacitate.addItems(["", "Simplu", "Dublu", "Cvadrupul"])

        self.filter_disponibilitate = QComboBox()
        self.filter_disponibilitate.addItems(["", "Ocupat", "Liber", "Posibil liber"])

        self.filter_tip = QComboBox()
        self.filter_tip.addItems(["", "Cavou", "Cruce", "Împrejmuire", "Mormânt"])

        self.filter_stare = QComboBox()
        self.filter_stare.addItems(["", "Foarte bună", "Bună", "Mai puțin bună"])

        self.filter_categorie = QComboBox()
        self.filter_categorie.addItems(["", "Categoria I", "Categoria II", "Categoria III"])

        self.filter_cod_unic = QLineEdit()
        self.filter_nr_vechi = QLineEdit()
        self.filter_numar_cf = QLineEdit()

        self.filter_sector = QLineEdit()
        self.filter_parcela = QLineEdit()
        self.filter_rand = QLineEdit()
        self.filter_loc = QLineEdit()

        self.precise_search = QCheckBox("Căutare precisă")
        self.precise_search.setChecked(False)

        # butoane filtre
        self.btn_filtreaza = QPushButton("Filtrează")
        self.btn_sterge_filtre = QPushButton("Șterge filtre")
        self.btn_filtreaza.clicked.connect(self.apply_filters)
        self.btn_sterge_filtre.clicked.connect(self.clear_filters)

        # grupuri filtre
        char_box = QGroupBox("Caracteristici")
        char_layout = QFormLayout()
        char_layout.addRow("Capacitate:", self.filter_capacitate)
        char_layout.addRow("Disponibilitate:", self.filter_disponibilitate)
        char_layout.addRow("Tip:", self.filter_tip)
        char_layout.addRow("Stare:", self.filter_stare)
        char_layout.addRow("Categorie:", self.filter_categorie)
        char_box.setLayout(char_layout)

        gen_box = QGroupBox("General")
        gen_layout = QFormLayout()
        gen_layout.addRow("Cod unic:", self.filter_cod_unic)
        gen_layout.addRow("Nr. vechi:", self.filter_nr_vechi)
        gen_layout.addRow("Număr CF:", self.filter_numar_cf)
        gen_box.setLayout(gen_layout)

        topo_box = QGroupBox("Topologie")
        topo_layout = QFormLayout()
        topo_layout.addRow("Sector:", self.filter_sector)
        topo_layout.addRow("Parcela:", self.filter_parcela)
        topo_layout.addRow("Rand:", self.filter_rand)
        topo_layout.addRow("Loc:", self.filter_loc)
        topo_box.setLayout(topo_layout)

        # tabel
        self.table = QTableWidget()
        headers = [
            "ID", "Cod unic", "Sector", "Parcela", "Rand", "Număr",
            "Capacitate", "Disponibilitate", "Tip", "Stare", "Categorie",
            "Suprafață", "Nr. vechi", "Număr CF", "Observații / Extra"
        ]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(self.on_table_double_click)

        # butoane CRUD
        self.btn_add = QPushButton("Adaugă")
        self.btn_edit = QPushButton("Modifică")
        self.btn_delete = QPushButton("Șterge")
        self.btn_dosare = QPushButton("Dosare")
        self.btn_reload = QPushButton("Reîncarcă locuri")

        self.btn_add.clicked.connect(self.add_record)
        self.btn_edit.clicked.connect(self.edit_record)
        self.btn_delete.clicked.connect(self.delete_record)
        self.btn_dosare.clicked.connect(self.open_dosare)
        self.btn_reload.clicked.connect(self.load_data)

        # layout principal
        left_layout = QVBoxLayout()
        left_layout.addWidget(char_box)
        left_layout.addWidget(gen_box)
        left_layout.addWidget(topo_box)
        left_layout.addWidget(self.precise_search)
        left_layout.addWidget(self.btn_filtreaza)
        left_layout.addWidget(self.btn_sterge_filtre)
        left_layout.addStretch()

        right_layout = QVBoxLayout()
        right_layout.addWidget(self.table)
        btns = QHBoxLayout()
        btns.addWidget(self.btn_add)
        btns.addWidget(self.btn_edit)
        btns.addWidget(self.btn_delete)
        btns.addWidget(self.btn_dosare)
        btns.addStretch()
        btns.addWidget(self.btn_reload)
        right_layout.addLayout(btns)

        main_layout = QHBoxLayout()
        main_layout.addLayout(right_layout, 3)
        main_layout.addLayout(left_layout, 1)
        self.setLayout(main_layout)

        self.current_filters: Dict[str, Any] = {}

        if self.signals is not None:
            try:
                self.signals.data_changed.connect(self.on_data_changed)
            except Exception:
                pass

        self.load_data()

    # FILTRE
    def build_filters(self) -> Dict[str, Any]:
        f: Dict[str, Any] = {}
        if self.filter_capacitate.currentText():
            f["capacitate"] = self.filter_capacitate.currentText()
        if self.filter_disponibilitate.currentText():
            f["disponibilitate"] = self.filter_disponibilitate.currentText()
        if self.filter_tip.currentText():
            f["tip"] = self.filter_tip.currentText()
        if self.filter_stare.currentText():
            f["stare"] = self.filter_stare.currentText()
        if self.filter_categorie.currentText():
            f["categorie"] = self.filter_categorie.currentText()

        if self.filter_cod_unic.text().strip():
            f["cod_loc"] = self.filter_cod_unic.text().strip()
        if self.filter_nr_vechi.text().strip():
            f["nr_vechi"] = self.filter_nr_vechi.text().strip()
        if self.filter_numar_cf.text().strip():
            f["numar_cf"] = self.filter_numar_cf.text().strip()

        if self.filter_sector.text().strip():
            f["sector"] = self.filter_sector.text().strip()
        if self.filter_parcela.text().strip():
            f["parcela"] = self.filter_parcela.text().strip()
        if self.filter_rand.text().strip():
            f["rand"] = self.filter_rand.text().strip()
        if self.filter_loc.text().strip():
            f["numar"] = self.filter_loc.text().strip()

        f["_precise"] = self.precise_search.isChecked()


        return f

    def apply_filters(self):
        self.current_filters = self.build_filters()
        self.load_data()

    def clear_filters(self):
        self.filter_capacitate.setCurrentIndex(0)
        self.filter_disponibilitate.setCurrentIndex(0)
        self.filter_tip.setCurrentIndex(0)
        self.filter_stare.setCurrentIndex(0)
        self.filter_categorie.setCurrentIndex(0)
        self.filter_cod_unic.clear()
        self.filter_nr_vechi.clear()
        self.filter_numar_cf.clear()
        self.filter_sector.clear()
        self.filter_parcela.clear()
        self.filter_rand.clear()
        self.filter_loc.clear()
        self.precise_search.setChecked(False)
        self.current_filters = {}
        self.load_data()

    # POPULARE TABEL
    def load_data(self):
        self.table.setRowCount(0)
        if not self.actions:
            return

        try:
            rows = self.actions.search_locuri(self.current_filters)
        except Exception as e:
            print("[LocuriWindow] search_locuri error:", e)
            return

        for r in rows:
            row_idx = self.table.rowCount()
            self.table.insertRow(row_idx)

            cod = r["cod_loc"]
            full = {}
            try:
                loaded = self.actions.load_loc_by_cod(cod)
                if loaded:
                    full = loaded
            except Exception as e:
                print("[LocuriWindow] load_loc_by_cod error:", e)
                full = {}

            def val_full(key: str, fallback: Any = ""):
                if key in full:
                    return full[key] if full[key] is not None else fallback
                return fallback

            self.table.setItem(row_idx, 0, QTableWidgetItem(str(val_full("id", r["id"]))))
            self.table.setItem(row_idx, 1, QTableWidgetItem(str(val_full("cod_loc", r["cod_loc"]))))
            self.table.setItem(row_idx, 2, QTableWidgetItem(str(val_full("sector", r["sector"]))))
            self.table.setItem(row_idx, 3, QTableWidgetItem(str(val_full("parcela", r["parcela"]))))
            self.table.setItem(row_idx, 4, QTableWidgetItem(str(val_full("rand", r["rand"]))))
            self.table.setItem(row_idx, 5, QTableWidgetItem(str(val_full("numar", r["numar"]))))
            self.table.setItem(row_idx, 6, QTableWidgetItem(str(val_full("capacitate", r["capacitate"]))))
            self.table.setItem(row_idx, 7, QTableWidgetItem(str(val_full("disponibilitate", r["disponibilitate"]))))
            self.table.setItem(row_idx, 8, QTableWidgetItem(str(val_full("tip", r["tip"]))))
            self.table.setItem(row_idx, 9, QTableWidgetItem(str(val_full("stare", r["stare"]))))
            self.table.setItem(row_idx, 10, QTableWidgetItem(str(val_full("categorie", r["categorie"]))))
            self.table.setItem(row_idx, 11, QTableWidgetItem(str(val_full("suprafata", r["suprafata"]))))
            self.table.setItem(row_idx, 12, QTableWidgetItem(str(val_full("nr_vechi", r["nr_vechi"]))))
            self.table.setItem(row_idx, 13, QTableWidgetItem(str(val_full("numar_cf", r["numar_cf"]))))

            extra_dict = full.get("extra_data", {}) if isinstance(full.get("extra_data", {}), dict) else {}
            preview = extra_dict.get("observatii") or extra_dict.get("nume") or ""
            self.table.setItem(row_idx, 14, QTableWidgetItem(str(preview)))

        self.table.resizeColumnsToContents()

    # SELECȚIE
    def selected_cod_loc(self) -> Optional[str]:
        idx = self.table.currentRow()
        if idx < 0:
            return None
        item = self.table.item(idx, 1)
        return item.text() if item else None

    # CRUD
    def add_record(self):
        if not self.actions:
            return
        cod, ok = QInputDialog.getText(self, "Adaugă loc", "Cod unic loc:")
        if not ok or not cod.strip():
            return
        cod = cod.strip()

        # creează rând minim
        self.actions.save_loc_metadata(cod, {"sector": "", "parcela": "", "rand": "", "numar": ""})
        dlg = EditLocDialog(self.parent or self, cod, open_inhumati_after_save=True)
        if dlg.exec() == QDialog.Accepted:
            if self.signals:
                try:
                    self.signals.data_changed.emit("loc", cod)
                except Exception:
                    pass
            QMessageBox.information(self, "Adăugat", f"Loc {cod} creat și salvat.")
        self.load_data()

    def edit_record(self):
        cod = self.selected_cod_loc()
        if not cod:
            QMessageBox.warning(self, "Selectare", "Selectează un rând mai întâi.")
            return
        dlg = EditLocDialog(self.parent or self, cod, open_inhumati_after_save=False)
        if dlg.exec() == QDialog.Accepted:
            if self.signals:
                try:
                    self.signals.data_changed.emit("loc", cod)
                except Exception:
                    pass
            QMessageBox.information(self, "Salvat", "Modificările au fost salvate.")
        self.load_data()

    def delete_record(self):
        cod = self.selected_cod_loc()
        if not cod:
            QMessageBox.warning(self, "Selectare", "Selectează un rând mai întâi.")
            return
        reply = QMessageBox.question(
            self, "Șterge", f"Ștergi locul {cod} din baza de date?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            try:
                self.actions.delete_plot_from_db(cod)
            except Exception as e:
                QMessageBox.warning(self, "Eroare", f"Nu am putut șterge: {e}")
                return
            if self.signals:
                try:
                    self.signals.data_changed.emit("loc", cod)
                except Exception:
                    pass
            QMessageBox.information(self, "Șters", f"Loc {cod} șters.")
            self.load_data()

    def open_dosare(self):
        cod = self.selected_cod_loc()
        if not cod:
            QMessageBox.warning(self, "Selectare", "Selectează un rând mai întâi.")
            return
        QMessageBox.information(self, "Dosare", f"Deschidere dosare pentru {cod} (neimplementat).")

    # DOUBLE CLICK
    def on_table_double_click(self, index):
        cod = self.selected_cod_loc()
        if not cod:
            return
        try:
            if hasattr(self.parent, "map_view") and hasattr(self.parent, "on_plot_clicked"):
                for item in self.parent.map_view._scene.items():
                    data = item.data(0)
                    if isinstance(data, dict) and data.get("cod_loc") == cod:
                        try:
                            self.parent.on_plot_clicked(item)
                        except Exception:
                            pass
                        break
        except Exception:
            pass

    # SEMNAL
    def on_data_changed(self, entity: str, key: str):
        if entity in ("loc", "loc_extra", "inhumat"):
            self.load_data()

    # Notă: nu deconectăm semnalul în closeEvent — MainWindow face asta după
    # ce dlg.exec() se termină. Deconectarea dublă ar cauza RuntimeWarning.
    def closeEvent(self, event):
        super().closeEvent(event)
