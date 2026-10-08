# alerts_window.py — alerte expirare concesiuni/contracte
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
)
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt
import datetime

import database as db


def _pick(d: dict, *keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return ""


class AlertsWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Alerte expirare concesiuni / contracte")
        self.resize(720, 500)

        layout = QVBoxLayout()

        top = QHBoxLayout()
        top.addWidget(QLabel("Arată contractele care expiră în următoarele"))
        self.spin_days = QSpinBox()
        self.spin_days.setRange(0, 3650)
        self.spin_days.setValue(30)
        self.spin_days.valueChanged.connect(self.reload)
        top.addWidget(self.spin_days)
        top.addWidget(QLabel("zile (inclusiv deja expirate)"))
        top.addStretch()
        layout.addLayout(top)

        self.lbl_count = QLabel()
        self.lbl_count.setStyleSheet("font-weight:600;")
        layout.addWidget(self.lbl_count)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(
            ["Cod loc", "Concesionar", "Nr. contract", "Expiră la", "Stare"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setObjectName("alerts-table")
        layout.addWidget(self.table)

        close = QPushButton("Închide")
        close.clicked.connect(self.accept)
        layout.addWidget(close)
        self.setLayout(layout)

        self.reload()

    def reload(self):
        days = self.spin_days.value()
        rows = db.expiring_contracts(days)
        self.table.setRowCount(len(rows))
        today = datetime.date.today()
        expired_n = 0
        for i, r in enumerate(rows):
            cod = _pick(r, "cod_loc", "cod")
            conc = _pick(r, "concesionar", "nume_concesionar", "titular", "nume")
            nrc = _pick(r, "numar_contract", "nr_contract", "numar", "id")
            exp = _pick(r, r.get("_date_col", ""), "data_sfarsit", "data_expirare")
            stare = "activ"
            try:
                d = datetime.date.fromisoformat(str(exp)[:10])
                if d < today:
                    stare = "EXPIRAT"
                    expired_n += 1
                elif (d - today).days <= 7:
                    stare = "expiră curând"
            except Exception:
                pass
            vals = [cod, conc, nrc, exp, stare]
            for j, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if stare == "EXPIRAT":
                    item.setForeground(QColor("#dc2626"))
                elif stare == "expiră curând":
                    item.setForeground(QColor("#d97706"))
                self.table.setItem(i, j, item)
        self.lbl_count.setText(
            f"{len(rows)} contracte de urmărit  |  {expired_n} deja expirate")
