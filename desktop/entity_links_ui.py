"""Navigare unitară Locuri de veci <-> Concesionari <-> Contracte (UI). Reutilizează dialogurile existente:
AddConcesionarDialog, AddContractDialog / ContractsWindow, LocuriWindow, ConcesionariWindow. Nu creează date proprii:
asocierea rezultă exclusiv din contractele salvate (id-uri reale)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

import database as db
import entity_links as links


def _call(actions, name: str, *args):
    fn = getattr(actions, name, None) if actions else None
    if not callable(fn):
        fn = getattr(db, name)
    return fn(*args)


def open_contract_dialog(parent, actions, cod_loc: Optional[str] = None, concesionar_id: Optional[int] = None) -> bool:
    """Contract NOU, cu locul și/sau concesionarul precompletate (nu modifică contracte existente)."""
    from contracts_window import AddContractDialog
    dlg = AddContractDialog(parent, actions, existing=None, selected_cod_loc=cod_loc, selected_concesionar_id=concesionar_id)
    return dlg.exec() == QDialog.Accepted


def open_contracts_window(parent, actions, cod_loc: Optional[str] = None, concesionar_id: Optional[int] = None):
    """Lista contractelor (istoric) filtrată după loc și/sau concesionar."""
    from contracts_window import ContractsWindow
    win = ContractsWindow(parent, actions=actions, selected_cod_loc=cod_loc, selected_concesionar_id=concesionar_id,
                          filter_cod_loc=cod_loc, filter_concesionar_id=concesionar_id)
    win.exec()
    return win


def _parent_of(w):
    p = getattr(w, "parent", None)
    return p() if callable(p) else p


def _ancestor(widget, cls_name: str):
    """Fereastra deja deschisă de tipul cerut în lanțul de părinți (evită ferestre duplicate); None dacă nu există."""
    chain, w = [], widget
    while w is not None:
        if type(w).__name__ == cls_name:
            return w, chain
        chain.append(w)
        w = _parent_of(w)
    return None, []


def _close_chain(chain) -> None:
    for w in chain:
        if isinstance(w, QDialog):
            w.accept()


def _clear_line_filters(win) -> None:
    for name in dir(win):
        if name.startswith("filter_"):
            w = getattr(win, name, None)
            if isinstance(w, QLineEdit):
                w.clear()


def open_loc_window(parent, cod_loc: str):
    win, chain = _ancestor(parent, "LocuriWindow")
    if win is not None:
        if not win.select_cod_loc(cod_loc):
            try:
                win.clear_filters()
            except Exception:
                pass
            win.select_cod_loc(cod_loc)
        _close_chain(chain)
        return win
    from locuri_window import LocuriWindow
    win = LocuriWindow(parent, initial_cod_loc=cod_loc)
    win.exec()
    return win


def open_concesionar_window(parent, actions, concesionar_id: int):
    win, chain = _ancestor(parent, "ConcesionariWindow")
    if win is not None:
        if not win.select_concesionar(concesionar_id):
            _clear_line_filters(win)
            win.reload()
            win.select_concesionar(concesionar_id)
        _close_chain(chain)
        return win
    from concesionari_window import ConcesionariWindow
    win = ConcesionariWindow(parent, actions=actions, initial_id=concesionar_id)
    win.exec()
    return win


def open_plati_window(parent, cod_loc: str, contract_id: Optional[int] = None):
    """Plățile locului, cu filtrul pe contractul ales (doar citire/filtrare; nu modifică nimic la deschidere)."""
    from plati_window import PlatiWindow
    win = PlatiWindow(parent, cod_loc=cod_loc or "")
    if contract_id:
        for i in range(win.cmb_filter_contract.count()):
            if win.cmb_filter_contract.itemData(i) == contract_id:
                win.cmb_filter_contract.setCurrentIndex(i)
                break
    win.exec()
    return win


def show_on_map(widget, cod_loc: str) -> bool:
    """Evidențiază locul pe harta ferestrei principale (aceeași selecție ca la clic pe hartă) și închide dialogurile care o acoperă."""
    chain, w = [], widget
    while w is not None and not callable(getattr(w, "highlight_loc_by_cod", None)):
        chain.append(w)
        w = _parent_of(w)
    if w is None:
        return False
    ok = bool(w.highlight_loc_by_cod(cod_loc))
    if ok:
        _close_chain(chain)
    return ok


class PickDialog(QDialog):
    """Căutare + selecție a unei înregistrări existente (concesionar sau loc)."""

    def __init__(self, parent, title: str, rows: List[Dict[str, Any]], columns: List[tuple]):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(640, 420)
        self._rows, self._columns = rows, columns
        self._shown: List[Dict[str, Any]] = []
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Caută...")
        self.table = QTableWidget(0, len(columns))
        self.table.setHorizontalHeaderLabels([c[0] for c in columns])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.btn_ok = QPushButton("Selectează")
        self.btn_cancel = QPushButton("Anulează")
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.btn_ok)
        row.addWidget(self.btn_cancel)
        lay = QVBoxLayout(self)
        lay.addWidget(self.txt_search)
        lay.addWidget(self.table)
        lay.addLayout(row)
        self.txt_search.textChanged.connect(self._fill)
        self.table.doubleClicked.connect(lambda *_: self._accept())
        self.btn_ok.clicked.connect(self._accept)
        self.btn_cancel.clicked.connect(self.reject)
        self._fill()

    def _fill(self):
        needle = self.txt_search.text().strip().casefold()
        self.table.setRowCount(0)
        self._shown = []
        for r in self._rows:
            vals = [str(r.get(k) if r.get(k) is not None else "") for _, k in self._columns]
            if needle and needle not in " ".join(vals).casefold():
                continue
            self._shown.append(r)
            i = self.table.rowCount()
            self.table.insertRow(i)
            for j, v in enumerate(vals):
                self.table.setItem(i, j, QTableWidgetItem(v))
        if self._shown:
            self.table.selectRow(0)

    def _accept(self):
        if self.selected_row() is not None:
            self.accept()

    def selected_row(self) -> Optional[Dict[str, Any]]:
        i = self.table.currentRow()
        return self._shown[i] if 0 <= i < len(self._shown) else None


class LinkedRecordsDialog(QDialog):
    """mode='loc': concesionarii asociați unui loc; mode='concesionar': locurile asociate unui concesionar."""

    def __init__(self, parent, actions, mode: str, key, label: str = ""):
        super().__init__(parent)
        assert mode in ("loc", "concesionar")
        self.actions, self.mode, self.key = actions, mode, key
        self.setWindowTitle(("Concesionari asociați — loc " if mode == "loc" else "Locuri asociate — ") + (label or str(key)))
        self.setMinimumSize(760, 440)
        self._rows: List[Dict[str, Any]] = []
        if mode == "loc":
            self._columns = [("ID", "id"), ("Nume", "nume"), ("Prenume", "prenume"), ("CNP", "cnp"), ("Contracte", "contracte")]
        else:
            self._columns = [("Cod loc", "cod_loc"), ("Sector", "sector"), ("Parcela", "parcela"), ("Rând", "rand"),
                             ("Număr", "numar"), ("Contracte", "contracte")]
        self.lbl_empty = QLabel("")
        self.table = QTableWidget(0, len(self._columns))
        self.table.setHorizontalHeaderLabels([c[0] for c in self._columns])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        self.btn_pick = QPushButton("Selectează concesionar existent" if mode == "loc" else "Alege loc existent")
        self.btn_add = QPushButton("Adaugă concesionar nou")
        self.btn_add.setVisible(mode == "loc")
        self.btn_open = QPushButton("Deschide concesionar" if mode == "loc" else "Deschide loc")
        self.btn_contract = QPushButton("Contract nou")
        self.btn_history = QPushButton("Contracte / istoric")
        self.btn_fisa = QPushButton("Fișa locului")
        self.btn_fisa.setVisible(mode == "loc")
        self.btn_close = QPushButton("Închide")
        btns = QHBoxLayout()
        for b in (self.btn_pick, self.btn_add, self.btn_open, self.btn_contract, self.btn_history, self.btn_fisa):
            btns.addWidget(b)
        btns.addStretch()
        btns.addWidget(self.btn_close)
        lay = QVBoxLayout(self)
        lay.addWidget(self.table)
        lay.addWidget(self.lbl_empty)
        lay.addLayout(btns)

        self.btn_pick.clicked.connect(self.pick_existing)
        self.btn_add.clicked.connect(self.add_new_concesionar)
        self.btn_open.clicked.connect(self.open_selected)
        self.btn_contract.clicked.connect(self.new_contract)
        self.btn_history.clicked.connect(self.open_history)
        self.btn_fisa.clicked.connect(self.open_fisa_loc)
        self.btn_close.clicked.connect(self.accept)
        self.reload()

    def selected_row(self) -> Optional[Dict[str, Any]]:
        i = self.table.currentRow()
        return self._rows[i] if 0 <= i < len(self._rows) else None

    def reload(self):
        self._rows = (links.concesionari_for_loc(self.key) if self.mode == "loc" else links.locuri_for_concesionar(self.key))
        self.table.setRowCount(0)
        for r in self._rows:
            i = self.table.rowCount()
            self.table.insertRow(i)
            for j, (_, k) in enumerate(self._columns):
                self.table.setItem(i, j, QTableWidgetItem("" if r.get(k) is None else str(r.get(k))))
        self.lbl_empty.setText("" if self._rows else "Nicio asociere (nu există contracte pentru această înregistrare).")
        if self._rows:
            self.table.selectRow(0)

    def _new_contract_for(self, cod_loc, concesionar_id):
        open_contract_dialog(self, self.actions, cod_loc=cod_loc, concesionar_id=concesionar_id)
        self.reload()

    def pick_existing(self):
        if self.mode == "loc":
            rows = _call(self.actions, "load_concesionari")
            cols = [("ID", "id"), ("Nume", "nume"), ("Prenume", "prenume"), ("CNP", "cnp")]
            dlg = PickDialog(self, "Selectează concesionar", rows or [], cols)
            if dlg.exec() == QDialog.Accepted:
                self._new_contract_for(self.key, dlg.selected_row()["id"])
        else:
            rows = _call(self.actions, "search_locuri", {})
            cols = [("Cod loc", "cod_loc"), ("Sector", "sector"), ("Parcela", "parcela"), ("Rând", "rand"), ("Număr", "numar")]
            dlg = PickDialog(self, "Alege loc de veci", rows or [], cols)
            if dlg.exec() == QDialog.Accepted:
                self._new_contract_for(dlg.selected_row()["cod_loc"], self.key)

    def add_new_concesionar(self):
        from concesionari_window import AddConcesionarDialog
        dlg = AddConcesionarDialog(self, existing=None, actions=self.actions)
        if dlg.exec() == QDialog.Accepted and dlg.get_saved_id():
            self._new_contract_for(self.key, dlg.get_saved_id())

    def new_contract(self):
        row = self.selected_row()
        if self.mode == "loc":
            self._new_contract_for(self.key, (row or {}).get("id"))
        else:
            self._new_contract_for((row or {}).get("cod_loc"), self.key)

    def open_history(self):
        if self.mode == "loc":
            open_contracts_window(self, self.actions, cod_loc=self.key)
        else:
            open_contracts_window(self, self.actions, concesionar_id=self.key)
        self.reload()

    def open_fisa_loc(self):
        open_loc_window(self.parent(), self.key)
        self.reload()

    def open_selected(self):
        row = self.selected_row()
        if not row:
            QMessageBox.information(self, "Info", "Selectați o înregistrare din listă.")
            return
        main = self.parent()
        if self.mode == "loc":
            open_concesionar_window(main, self.actions, row["id"])
        else:
            open_loc_window(main, row["cod_loc"])
        self.reload()
