# plati_window.py — modul Plăți (chitanțe / istoric), cu legătură explicită la contract
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QComboBox,
    QDoubleSpinBox, QPushButton, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QDateEdit, QFileDialog,
)
from PySide6.QtCore import QDate, QTimer

import database as db
from plata_contracts import ContractPicker, contract_columns, contract_label, contracts_for_loc

_FILTER_ALL, _FILTER_NONE = "__toate__", "__fara_contract__"


class PlatiWindow(QDialog):
    def __init__(self, parent=None, cod_loc: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Plăți")
        self.resize(980, 640)
        self._contract_cache = {}

        layout = QVBoxLayout()
        layout.addWidget(QLabel("<h3>Evidență plăți</h3>"))

        form_box = QHBoxLayout()
        form = QFormLayout()

        self.txt_cod = QLineEdit(cod_loc)
        self.txt_cod.setPlaceholderText("cod loc (ex. A-01-01)")
        form.addRow("Cod loc:", self.txt_cod)

        self.picker = ContractPicker(cod_loc)
        form.addRow("Contract:", self.picker)
        self.txt_cod.textChanged.connect(lambda t: self.picker.set_loc(t.strip()))

        self.spin_suma = QDoubleSpinBox()
        self.spin_suma.setRange(0, 10_000_000)
        self.spin_suma.setDecimals(2)
        self.spin_suma.setSuffix(" lei")
        form.addRow("Sumă:", self.spin_suma)

        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDate(QDate.currentDate())
        self.date.setDisplayFormat("yyyy-MM-dd")
        form.addRow("Data:", self.date)

        self.cmb_tip = QComboBox()
        self.cmb_tip.addItems(["concesiune", "întreținere", "amenajare", "taxă", "altele"])
        form.addRow("Tip:", self.cmb_tip)

        self.txt_chitanta = QLineEdit()
        self.txt_chitanta.setPlaceholderText("nr. chitanță")
        form.addRow("Chitanță:", self.txt_chitanta)

        self.txt_obs = QLineEdit()
        form.addRow("Observații:", self.txt_obs)

        form_box.addLayout(form)
        add_col = QVBoxLayout()
        self.btn_add = QPushButton("➕ Adaugă plata")
        self.btn_add.setObjectName("plata-add-btn")
        self.btn_add.clicked.connect(self.add_plata)
        add_col.addWidget(self.btn_add)
        add_col.addStretch()
        form_box.addLayout(add_col)
        layout.addLayout(form_box)

        filt = QHBoxLayout()
        self.txt_filter = QLineEdit(cod_loc)
        self.txt_filter.setPlaceholderText("Filtrează după cod loc… (gol = toate)")
        self.txt_filter.textChanged.connect(self._refresh_contract_filter)
        filt.addWidget(self.txt_filter)
        self.cmb_filter_contract = QComboBox()
        self.cmb_filter_contract.setObjectName("plati-filter-contract")
        self.cmb_filter_contract.currentIndexChanged.connect(lambda *_: self.reload())
        filt.addWidget(self.cmb_filter_contract)
        self.lbl_total = QLabel("Total: 0.00 lei")
        self.lbl_total.setStyleSheet("font-weight:600;")
        filt.addWidget(self.lbl_total)
        layout.addLayout(filt)

        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels(
            ["ID", "Cod loc", "Contract", "Concesionar", "Sumă", "Data", "Tip", "Chitanță", "Observații"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setObjectName("plati-table")
        layout.addWidget(self.table)

        btns = QHBoxLayout()
        self.btn_del = QPushButton("🗑 Șterge plata selectată")
        self.btn_del.clicked.connect(self.delete_selected)
        btns.addWidget(self.btn_del)
        self.btn_receipt = QPushButton("🧾 Chitanță PDF")
        self.btn_receipt.setObjectName("plata-receipt-btn")
        self.btn_receipt.clicked.connect(self.receipt_pdf)
        btns.addWidget(self.btn_receipt)
        self.btn_phone = QPushButton("📱 Continuă pe telefon")
        self.btn_phone.setToolTip("Cod QR temporar: încarci chitanța/dovada plății de pe telefon la plata selectată")
        self.btn_phone.clicked.connect(self.phone_upload)
        btns.addWidget(self.btn_phone)
        close = QPushButton("Închide")
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        layout.addLayout(btns)

        self.setLayout(layout)
        try:
            import access_session
            if access_session.current_user() is not None and not access_session.can("edit"):
                self.btn_del.setEnabled(False)
        except Exception:
            pass
        self._refresh_contract_filter()

    # ------------------------------------------------------------------ filtru
    def _refresh_contract_filter(self, *_):
        cod = self.txt_filter.text().strip()
        self.cmb_filter_contract.blockSignals(True)
        self.cmb_filter_contract.clear()
        self.cmb_filter_contract.addItem("Toate contractele", _FILTER_ALL)
        if cod:
            for c in contracts_for_loc(cod):
                self.cmb_filter_contract.addItem(contract_label(c), c["id"])
            self.cmb_filter_contract.addItem("Doar plăți fără contract", _FILTER_NONE)
        self.cmb_filter_contract.blockSignals(False)
        self.reload()

    # ------------------------------------------------------------------ adăugare
    def add_plata(self):
        if not self.btn_add.isEnabled():
            return
        cod = self.txt_cod.text().strip()
        if not cod:
            QMessageBox.warning(self, "Plată", "Introdu codul locului.")
            return
        valid, contract_id = self.picker.contract_id()
        if not valid:
            QMessageBox.warning(self, "Plată",
                                "Locul are contracte: alege contractul pentru care înregistrezi plata "
                                "sau „Plată fără contract”.")
            return
        if self.spin_suma.value() <= 0:
            QMessageBox.warning(self, "Plată", "Suma trebuie să fie mai mare ca 0.")
            return
        self.btn_add.setEnabled(False)
        QTimer.singleShot(800, lambda: self.btn_add.setEnabled(True))
        pid = db.add_plata(cod, self.spin_suma.value(), self.date.date().toString("yyyy-MM-dd"),
                           self.cmb_tip.currentText(), self.txt_chitanta.text().strip(),
                           self.txt_obs.text().strip(), contract_id=contract_id)
        if not pid:
            QMessageBox.warning(self, "Plată", "Plata nu a fost înregistrată. " + db.last_refusal())
            return
        self.spin_suma.setValue(0)
        self.txt_chitanta.clear()
        self.txt_obs.clear()
        self.reload()

    # ------------------------------------------------------------------ acțiuni pe rând
    def _selected_plata_id(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        return int(self.table.item(row, 0).text())

    def phone_upload(self):
        """Pasul 6: QR/token temporar pentru plata selectată (documentul ajunge prin FastAPI în Storage)."""
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Plată", "Selectează o plată din tabel.")
            return
        from mobile_upload_dialog import open_phone_upload
        pid = int(self.table.item(row, 0).text())
        chit = self.table.item(row, 7).text() if self.table.item(row, 7) else ""
        open_phone_upload(self, "plata", pid, f"Plată {chit or '#' + str(pid)}", on_done=self.reload)

    def receipt_pdf(self):
        pid = self._selected_plata_id()
        if pid is None:
            QMessageBox.information(self, "Chitanță", "Selectează o plată din tabel.")
            return
        try:
            import pdf_reports
            data = pdf_reports.render_receipt_pdf(pid)
        except Exception as e:
            QMessageBox.warning(self, "Chitanță", f"Nu am putut genera chitanța: {e}")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Salvează chitanța", f"chitanta_{pid}.pdf", "PDF (*.pdf)")
        if not path:
            return
        try:
            with open(path, "wb") as fh:
                fh.write(data)
        except OSError as e:
            QMessageBox.warning(self, "Chitanță", f"Nu am putut salva fișierul: {e}")

    def delete_selected(self):
        pid = self._selected_plata_id()
        if pid is None:
            return
        if QMessageBox.question(self, "Ștergere", "Ștergi această plată?",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        if not db.delete_plata(pid):
            QMessageBox.warning(self, "Plată", "Plata nu a fost ștearsă. " + db.last_refusal())
        self.reload()

    # ------------------------------------------------------------------ listă
    def reload(self):
        cod = self.txt_filter.text().strip() or None
        rows = db.list_plati(cod)
        flt = self.cmb_filter_contract.currentData()
        if flt == _FILTER_NONE:
            rows = [r for r in rows if not r.get("contract_id")]
        elif flt not in (None, _FILTER_ALL):
            rows = [r for r in rows if r.get("contract_id") == flt]
        self._contract_cache = {}
        self.table.setRowCount(len(rows))
        total = 0.0
        for i, r in enumerate(rows):
            contract, titular = contract_columns(r, self._contract_cache)
            total += float(r.get("suma") or 0)
            vals = [r.get("id"), r.get("cod_loc"), contract, titular, f"{float(r.get('suma') or 0):.2f}",
                    r.get("data_plata"), r.get("tip"), r.get("chitanta_nr"), r.get("observatii")]
            for j, v in enumerate(vals):
                self.table.setItem(i, j, QTableWidgetItem(str(v if v is not None else "")))
        self.lbl_total.setText(f"Total: {total:.2f} lei")
