# plati_window.py — modul Plăți (chitanțe / scadențe / istoric)
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QComboBox,
    QDoubleSpinBox, QPushButton, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QDateEdit,
)
from PySide6.QtCore import Qt, QDate

import database as db


class PlatiWindow(QDialog):
    def __init__(self, parent=None, cod_loc: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Plăți")
        self.resize(720, 520)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("<h3>Evidență plăți</h3>"))

        # --- formular adăugare ---
        form_box = QHBoxLayout()
        form = QFormLayout()

        self.txt_cod = QLineEdit(cod_loc)
        self.txt_cod.setPlaceholderText("cod loc (ex. A-01-01)")
        form.addRow("Cod loc:", self.txt_cod)

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

        # --- filtru + total ---
        filt = QHBoxLayout()
        self.txt_filter = QLineEdit()
        self.txt_filter.setPlaceholderText("Filtrează după cod loc… (gol = toate)")
        self.txt_filter.textChanged.connect(self.reload)
        filt.addWidget(self.txt_filter)
        self.lbl_total = QLabel("Total: 0.00 lei")
        self.lbl_total.setStyleSheet("font-weight:600;")
        filt.addWidget(self.lbl_total)
        layout.addLayout(filt)

        # --- tabel ---
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["ID", "Cod loc", "Sumă", "Data", "Tip", "Chitanță", "Observații"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setObjectName("plati-table")
        layout.addWidget(self.table)

        btns = QHBoxLayout()
        self.btn_del = QPushButton("🗑 Șterge plata selectată")
        self.btn_del.clicked.connect(self.delete_selected)
        btns.addWidget(self.btn_del)
        self.btn_phone = QPushButton("📱 Continuă pe telefon")
        self.btn_phone.setToolTip("Cod QR temporar: încarci chitanța/dovada plății de pe telefon la plata selectată")
        self.btn_phone.clicked.connect(self.phone_upload)
        btns.addWidget(self.btn_phone)
        close = QPushButton("Închide")
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        layout.addLayout(btns)

        self.setLayout(layout)
        self.reload()

    def add_plata(self):
        cod = self.txt_cod.text().strip()
        if not cod:
            QMessageBox.warning(self, "Plată", "Introdu codul locului.")
            return
        if self.spin_suma.value() <= 0:
            QMessageBox.warning(self, "Plată", "Suma trebuie să fie mai mare ca 0.")
            return
        db.add_plata(cod, self.spin_suma.value(), self.date.date().toString("yyyy-MM-dd"),
                     self.cmb_tip.currentText(), self.txt_chitanta.text().strip(),
                     self.txt_obs.text().strip())
        self.spin_suma.setValue(0)
        self.txt_chitanta.clear()
        self.txt_obs.clear()
        self.reload()

    def phone_upload(self):
        """Pasul 6: QR/token temporar pentru plata selectată (documentul ajunge prin FastAPI în Storage)."""
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Plată", "Selectează o plată din tabel.")
            return
        from mobile_upload_dialog import open_phone_upload
        pid = int(self.table.item(row, 0).text())
        chit = self.table.item(row, 5).text() if self.table.item(row, 5) else ""
        open_phone_upload(self, "plata", pid, f"Plată {chit or '#' + str(pid)}", on_done=self.reload)

    def delete_selected(self):
        row = self.table.currentRow()
        if row < 0:
            return
        pid = int(self.table.item(row, 0).text())
        if QMessageBox.question(self, "Ștergere", "Ștergi această plată?",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        db.delete_plata(pid)
        self.reload()

    def reload(self):
        cod = self.txt_filter.text().strip() or None
        rows = db.list_plati(cod)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [r.get("id"), r.get("cod_loc"), f"{float(r.get('suma') or 0):.2f}",
                    r.get("data_plata"), r.get("tip"), r.get("chitanta_nr"), r.get("observatii")]
            for j, v in enumerate(vals):
                self.table.setItem(i, j, QTableWidgetItem(str(v if v is not None else "")))
        total = db.total_plati(cod)
        self.lbl_total.setText(f"Total: {total:.2f} lei")
