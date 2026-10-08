# user_mode_window.py
"""
Mod USER / OPERATOR — interfață modernă cu sidebar de navigare.

Operatorul poate:
    - Vizualiza HARTA FINALĂ a cimitirului (desenată în modul admin) — interactivă:
      pan/zoom, căutare loc, colorare pe status, click pe loc → fișă completă.
    - Adăuga înhumați pe locuri deja salvate.
    - Asocia concesionari și genera contracte de concesiune pe locuri existente.
    - Înregistra plăți.
    - Căuta locuri / înhumați, vedea contractele.

NU poate (geometrie — doar în admin):
    - Desena / edita / șterge poligoane, sectoare, ghidaje.
    - Calibra scara sau modifica structura hărții.

Blocarea editării structurale se face prin flagul `self.read_only = True`, verificat
de `GravePlotItem` (meniurile contextuale de editare/ștergere sunt dezactivate).
"""

from typing import Optional, Dict

from PySide6.QtCore import Qt, Signal, QObject, QTimer
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QStackedWidget, QLabel, QLineEdit, QPushButton, QTableView, QAbstractItemView,
    QHeaderView, QFrame, QMessageBox, QComboBox, QDialog, QScrollArea, QFormLayout,
    QTextBrowser, QGraphicsDropShadowEffect,
)
from PySide6.QtGui import QStandardItemModel, QStandardItem

import database as db
from theme import get_palette
from map_view import CemeteryMapView
from map_editor_actions import EditorActions
from operator_detail_panel import OperatorDetailPanel


class _AppSignals(QObject):
    data_changed = Signal(str, str)   # entity, key


# =============================================================================
# CARDURI STATISTICĂ
# =============================================================================
class _StatCard(QFrame):
    def __init__(self, icon: str, label: str, value: str, palette: dict, accent: str):
        super().__init__()
        self.setProperty("class", "card")
        self.setStyleSheet(
            f"QFrame[class='card'] {{ background-color: {palette['bg_panel']}; "
            f"border: 1px solid {palette['border']}; border-radius: 12px; }} "
            f"QFrame[class='card']:hover {{ border-color: {accent}; }}"
        )
        self.setMinimumHeight(110)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(12)
        shadow.setColor(QColor(0, 0, 0, 50))
        shadow.setOffset(0, 2)
        self.setGraphicsEffect(shadow)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(4)

        top = QHBoxLayout()
        icon_lbl = QLabel(icon)
        f = QFont(); f.setPointSize(22); icon_lbl.setFont(f)
        icon_lbl.setStyleSheet(f"color: {accent}; background: transparent; border: none;")

        lbl = QLabel(label)
        lbl.setStyleSheet(
            f"color: {palette['text_muted']}; background: transparent; "
            f"font-size: 10pt; font-weight: 600; letter-spacing: 1px;"
        )
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        top.addWidget(icon_lbl)
        top.addStretch()
        top.addWidget(lbl)

        val = QLabel(value)
        vf = QFont(); vf.setPointSize(24); vf.setBold(True); val.setFont(vf)
        val.setStyleSheet(f"color: {palette['text']}; background: transparent; border: none;")

        lay.addLayout(top)
        lay.addWidget(val)


# =============================================================================
# PANOU DETALII LOC (operator) — afișat lângă hartă
# =============================================================================
class _OperatorDetailPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self._pal = window._pal
        self.setFixedWidth(400)
        self.setStyleSheet(
            f"background-color: {self._pal['bg_panel']}; "
            f"border-left: 1px solid {self._pal['border']};"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        # header
        self.lbl_cod = QLabel("Selectează un loc pe hartă")
        hf = QFont(); hf.setPointSize(16); hf.setBold(True); self.lbl_cod.setFont(hf)
        self.lbl_cod.setStyleSheet(f"color: {self._pal['text']}; background: transparent; border: none;")
        self.lbl_cod.setWordWrap(True)
        root.addWidget(self.lbl_cod)

        self.lbl_sub = QLabel("")
        self.lbl_sub.setStyleSheet(f"color: {self._pal['text_muted']}; background: transparent; border: none; font-size: 10pt;")
        root.addWidget(self.lbl_sub)

        # detalii (scroll)
        self.detail = QTextBrowser()
        self.detail.setObjectName("operator-loc-detail")
        self.detail.setStyleSheet(
            f"QTextBrowser {{ background-color: {self._pal['bg']}; "
            f"border: 1px solid {self._pal['border']}; border-radius: 8px; padding: 12px; }}"
        )
        root.addWidget(self.detail, 1)

        # butoane operațiuni
        self.btn_inhumat = self._mk_btn("➕  Adaugă înhumat", self._pal['primary'])
        self.btn_contract = self._mk_btn("👤  Concesionar & Contracte", self._pal['success'])
        self.btn_plati = self._mk_btn("💰  Plăți", self._pal['warning'])

        self.btn_inhumat.setObjectName("op-add-inhumat")
        self.btn_contract.setObjectName("op-manage-contracts")
        self.btn_plati.setObjectName("op-payments")

        self.btn_inhumat.clicked.connect(lambda: window.op_add_inhumat())
        self.btn_contract.clicked.connect(lambda: window.op_manage_contracts())
        self.btn_plati.clicked.connect(lambda: window.op_payments())

        root.addWidget(self.btn_inhumat)
        root.addWidget(self.btn_contract)
        root.addWidget(self.btn_plati)

        self._set_buttons_enabled(False)

    def _mk_btn(self, text: str, accent: str) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.setMinimumHeight(42)
        b.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {accent}; color: white; border: none;
                border-radius: 8px; font-weight: 600; font-size: 10.5pt;
                text-align: left; padding-left: 16px;
            }}
            QPushButton:hover {{ background-color: {accent}; opacity: 0.9; }}
            QPushButton:disabled {{ background-color: {self._pal['bg_hover']}; color: {self._pal['text_muted']}; }}
            """
        )
        return b

    def _set_buttons_enabled(self, enabled: bool):
        for b in (self.btn_inhumat, self.btn_contract, self.btn_plati):
            b.setEnabled(enabled)

    def show_loc(self, cod_loc: str):
        loc = db.load_loc_by_cod(cod_loc) or {}
        inhumati = db.load_inhumati_for_loc(cod_loc)
        try:
            contracte = [c for c in db.load_contracte() if c.get("cod_loc") == cod_loc]
        except Exception:
            contracte = []

        self.lbl_cod.setText(f"📍  {cod_loc}")
        self.lbl_sub.setText(
            f"Stare: {loc.get('stare','') or '—'}  |  Capacitate: {loc.get('capacitate','') or '—'}"
        )

        p = self._pal
        html = f"""
        <div style="font-family:Segoe UI; color:{p['text']};">
          <table cellspacing="6">
            <tr><td><b>Sector:</b> {loc.get('sector','') or '—'}</td>
                <td><b>Parcela:</b> {loc.get('parcela','') or '—'}</td></tr>
            <tr><td><b>Rând:</b> {loc.get('rand','') or '—'}</td>
                <td><b>Număr:</b> {loc.get('numar','') or '—'}</td></tr>
            <tr><td><b>Tip:</b> {loc.get('tip','') or '—'}</td>
                <td><b>Categorie:</b> {loc.get('categorie','') or '—'}</td></tr>
            <tr><td colspan="2"><b>Suprafață:</b> {loc.get('suprafata','') or '—'} m²
                &nbsp;|&nbsp; <b>Disp.:</b> {loc.get('disponibilitate','') or '—'}</td></tr>
          </table>
          <hr style="border:0;border-top:1px solid {p['border']};margin:10px 0;">
          <b style="color:{p['accent']};">⚰ Înhumați ({len(inhumati)})</b>
        """
        if inhumati:
            html += "<ul style='margin:6px 0;'>"
            for i in inhumati:
                html += (f"<li>{i.get('nume','')} {i.get('prenume','')}"
                         f" — deces {i.get('data_deces','') or '?'}</li>")
            html += "</ul>"
        else:
            html += f"<p style='color:{p['text_muted']};'>Nicio persoană înhumată.</p>"

        html += f"""<hr style="border:0;border-top:1px solid {p['border']};margin:10px 0;">
          <b style="color:{p['success']};">📄 Contracte ({len(contracte)})</b>"""
        if contracte:
            html += "<ul style='margin:6px 0;'>"
            for c in contracte:
                owner = c.get("owner_fullname", "") or ""
                html += (f"<li>Nr. {c.get('numar_contract','') or '?'}"
                         f" — {owner or 'fără titular'}"
                         f" (valabil: {c.get('data_sfarsit','') or '—'})</li>")
            html += "</ul>"
        else:
            html += f"<p style='color:{p['text_muted']};'>Niciun contract asociat.</p>"
        html += "</div>"

        self.detail.setHtml(html)
        self._set_buttons_enabled(True)


# =============================================================================
# PAGINA HARTĂ (interactivă, read-only geometrie)
# =============================================================================
class _MapPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        pal = window._pal

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(0)

        # bară sus: titlu + căutare + colorare status
        bar = QFrame()
        bar.setStyleSheet(f"background-color: {pal['bg_panel']}; border-bottom: 1px solid {pal['border']};")
        bar_l = QHBoxLayout(bar)
        bar_l.setContentsMargins(20, 12, 20, 12)
        bar_l.setSpacing(12)

        title = QLabel("Harta cimitirului")
        tf = QFont(); tf.setPointSize(15); tf.setBold(True); title.setFont(tf)
        title.setStyleSheet(f"color: {pal['text']}; background: transparent; border: none;")
        bar_l.addWidget(title)
        bar_l.addStretch()

        self.search = QLineEdit()
        self.search.setObjectName("map-search")
        self.search.setPlaceholderText("🔎  caută loc pe hartă (cod)…")
        self.search.setFixedWidth(240)
        self.search.setMinimumHeight(34)
        self.search.returnPressed.connect(
            lambda: window.features.search_plot(self.search.text()))
        bar_l.addWidget(self.search)

        self.btn_color = QPushButton("🎨  Colorare status")
        self.btn_color.setObjectName("map-toggle-color")
        self.btn_color.setCheckable(True)
        self.btn_color.setCursor(Qt.PointingHandCursor)
        self.btn_color.setMinimumHeight(34)
        self.btn_color.setStyleSheet(
            f"""
            QPushButton {{ background-color: transparent; color: {pal['text']};
                border: 1px solid {pal['border']}; border-radius: 8px; padding: 0 14px; }}
            QPushButton:checked {{ background-color: {pal['primary']}; color: white; border-color: {pal['primary']}; }}
            """
        )
        self.btn_color.clicked.connect(lambda: window.features.toggle_status_coloring())
        bar_l.addWidget(self.btn_color)

        left.addWidget(bar)
        left.addWidget(window.map_view, 1)

        root.addLayout(left, 1)
        root.addWidget(window.detail_panel)


# =============================================================================
# PAGINI TABELARE (căutare)
# =============================================================================
class _SearchLocuriPage(QWidget):
    def __init__(self, palette: dict):
        super().__init__()
        self._pal = palette

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(16)

        h1 = QLabel("Căutare locuri de veci")
        f = QFont(); f.setPointSize(20); f.setBold(True); h1.setFont(f)
        h1.setStyleSheet(f"color: {palette['text']}; background: transparent; border: none;")
        layout.addWidget(h1)

        search_row = QHBoxLayout()
        self.input_search = QLineEdit()
        self.input_search.setPlaceholderText("🔍  Caută după cod, sector, parcelă, rând, număr…")
        self.input_search.setMinimumHeight(42)
        search_row.addWidget(self.input_search)

        self.cmb_sector = QComboBox()
        self.cmb_sector.addItem("Toate sectoarele", "")
        for c in range(ord("A"), ord("Z") + 1):
            self.cmb_sector.addItem(chr(c), chr(c))
        self.cmb_sector.setMinimumHeight(42)
        self.cmb_sector.setMinimumWidth(180)
        search_row.addWidget(self.cmb_sector)
        layout.addLayout(search_row)

        self.model = QStandardItemModel(self)
        self.model.setHorizontalHeaderLabels(
            ["Cod loc", "Sector", "Parcela", "Rând", "Număr", "Tip", "Stare", "Disponibilitate", "Suprafață"]
        )
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.doubleClicked.connect(self._show_details)
        layout.addWidget(self.table)

        self.detail = QTextBrowser()
        self.detail.setMaximumHeight(200)
        self.detail.setPlaceholderText("Selectează un loc pentru a vedea detaliile...")
        self.detail.setStyleSheet(
            f"QTextBrowser {{ background-color: {palette['bg_panel']}; "
            f"border: 1px solid {palette['border']}; border-radius: 8px; padding: 12px; }}"
        )
        layout.addWidget(self.detail)

        self.input_search.textChanged.connect(self._reload)
        self.cmb_sector.currentIndexChanged.connect(self._reload)
        self.table.selectionModel().currentRowChanged.connect(self._show_details)
        self._reload()

    def _reload(self):
        term = self.input_search.text().strip()
        sector = self.cmb_sector.currentData()
        try:
            filters = {}
            if sector:
                filters["sector"] = sector
            all_rows = db.search_locuri(filters)
        except Exception:
            all_rows = []
        if term:
            t = term.lower()
            all_rows = [
                r for r in all_rows
                if t in str(r.get("cod_loc", "")).lower()
                or t in str(r.get("parcela", "")).lower()
                or t in str(r.get("rand", "")).lower()
                or t in str(r.get("numar", "")).lower()
                or t in str(r.get("tip", "")).lower()
            ]
        self.model.removeRows(0, self.model.rowCount())
        for r in all_rows:
            self.model.appendRow([
                QStandardItem(str(r.get("cod_loc", "") or "")),
                QStandardItem(str(r.get("sector", "") or "")),
                QStandardItem(str(r.get("parcela", "") or "")),
                QStandardItem(str(r.get("rand", "") or "")),
                QStandardItem(str(r.get("numar", "") or "")),
                QStandardItem(str(r.get("tip", "") or "")),
                QStandardItem(str(r.get("stare", "") or "")),
                QStandardItem(str(r.get("disponibilitate", "") or "")),
                QStandardItem(str(r.get("suprafata", "") or "")),
            ])
        self.table.resizeColumnsToContents()

    def _show_details(self, *_):
        idx = self.table.currentIndex()
        if not idx.isValid():
            return
        cod = self.model.item(idx.row(), 0).text()
        try:
            loc = db.load_loc_by_cod(cod)
            inhumati = db.load_inhumati_for_loc(cod)
        except Exception:
            loc, inhumati = None, []
        if not loc:
            self.detail.setHtml("<i>Nu s-au găsit detalii.</i>")
            return
        p = self._pal
        html = f"""
        <div style="font-family:Segoe UI;">
          <h2 style="color:{p['primary']}; margin:0 0 8px 0;">📍 {loc.get('cod_loc','')}</h2>
          <table cellspacing="6" style="color:{p['text']};">
            <tr><td><b>Sector:</b> {loc.get('sector','')}</td>
                <td><b>Parcela:</b> {loc.get('parcela','')}</td>
                <td><b>Rând:</b> {loc.get('rand','')}</td>
                <td><b>Număr:</b> {loc.get('numar','')}</td></tr>
            <tr><td><b>Tip:</b> {loc.get('tip','')}</td>
                <td><b>Stare:</b> {loc.get('stare','')}</td>
                <td><b>Capacitate:</b> {loc.get('capacitate','')}</td>
                <td><b>Disponibilitate:</b> {loc.get('disponibilitate','')}</td></tr>
          </table>
          <hr style="border:0;border-top:1px solid {p['border']};margin:10px 0;">
          <b style="color:{p['accent']};">Înhumați ({len(inhumati)})</b>
        """
        if inhumati:
            html += "<ul>"
            for i in inhumati:
                html += f"<li>{i.get('nume','')} {i.get('prenume','')} — deces {i.get('data_deces','')}</li>"
            html += "</ul>"
        else:
            html += f"<p style='color:{p['text_muted']};'>Nu există înregistrări.</p>"
        html += "</div>"
        self.detail.setHtml(html)


class _SearchInhumatiPage(QWidget):
    def __init__(self, palette: dict):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(16)

        h1 = QLabel("Căutare înhumați")
        f = QFont(); f.setPointSize(20); f.setBold(True); h1.setFont(f)
        h1.setStyleSheet(f"color: {palette['text']}; background: transparent; border: none;")
        layout.addWidget(h1)

        row = QHBoxLayout()
        self.input_nume = QLineEdit(); self.input_nume.setPlaceholderText("Nume"); self.input_nume.setMinimumHeight(38)
        self.input_prenume = QLineEdit(); self.input_prenume.setPlaceholderText("Prenume"); self.input_prenume.setMinimumHeight(38)
        self.input_cnp = QLineEdit(); self.input_cnp.setPlaceholderText("CNP"); self.input_cnp.setMinimumHeight(38)
        row.addWidget(self.input_nume); row.addWidget(self.input_prenume); row.addWidget(self.input_cnp)
        layout.addLayout(row)

        self.model = QStandardItemModel(self)
        self.model.setHorizontalHeaderLabels(
            ["Nume", "Prenume", "CNP", "Data nașterii", "Data deces", "Data înhumare", "Loc"]
        )
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        for w in (self.input_nume, self.input_prenume, self.input_cnp):
            w.textChanged.connect(self._reload)
        self._reload()

    def _reload(self):
        try:
            rows = db.load_all_inhumati({
                "nume": self.input_nume.text().strip(),
                "prenume": self.input_prenume.text().strip(),
                "cnp": self.input_cnp.text().strip(),
            })
        except Exception:
            rows = []
        self.model.removeRows(0, self.model.rowCount())
        for r in rows:
            self.model.appendRow([
                QStandardItem(str(r.get("nume", ""))),
                QStandardItem(str(r.get("prenume", ""))),
                QStandardItem(str(r.get("cnp", ""))),
                QStandardItem(str(r.get("data_nasterii", ""))),
                QStandardItem(str(r.get("data_deces", ""))),
                QStandardItem(str(r.get("data_inhumare", ""))),
                QStandardItem(str(r.get("cod_loc", ""))),
            ])
        self.table.resizeColumnsToContents()


class _ContractePage(QWidget):
    def __init__(self, palette: dict):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(16)

        h1 = QLabel("Contracte")
        f = QFont(); f.setPointSize(20); f.setBold(True); h1.setFont(f)
        h1.setStyleSheet(f"color: {palette['text']}; background: transparent; border: none;")
        layout.addWidget(h1)

        self.input_search = QLineEdit()
        self.input_search.setPlaceholderText("🔍  Caută după număr contract, cod loc, sector…")
        self.input_search.setMinimumHeight(42)
        self.input_search.textChanged.connect(self._reload)
        layout.addWidget(self.input_search)

        self.model = QStandardItemModel(self)
        self.model.setHorizontalHeaderLabels(
            ["Nr. contract", "Data contract", "Data început", "Data sfârșit",
             "Cod loc", "Sector", "Titular", "Valabilitate"]
        )
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        self._reload()

    def _reload(self):
        try:
            rows = db.load_contracte()
        except Exception:
            rows = []
        term = self.input_search.text().strip().lower()
        if term:
            rows = [
                r for r in rows
                if term in str(r.get("numar_contract", "")).lower()
                or term in str(r.get("cod_loc", "")).lower()
                or term in str(r.get("sector", "")).lower()
                or term in str(r.get("owner_fullname", "")).lower()
            ]
        self.model.removeRows(0, self.model.rowCount())
        for r in rows:
            self.model.appendRow([
                QStandardItem(str(r.get("numar_contract", ""))),
                QStandardItem(str(r.get("data_contract", ""))),
                QStandardItem(str(r.get("data_inceput", ""))),
                QStandardItem(str(r.get("data_sfarsit", ""))),
                QStandardItem(str(r.get("cod_loc", ""))),
                QStandardItem(str(r.get("sector", ""))),
                QStandardItem(str(r.get("owner_fullname", ""))),
                QStandardItem(str(r.get("valabilitate", ""))),
            ])
        self.table.resizeColumnsToContents()


# =============================================================================
# MAIN WINDOW USER MODE
# =============================================================================
class UserModeWindow(QMainWindow):
    logout_requested = Signal()
    registry_switch_requested = Signal()

    def __init__(self, user: Dict):
        super().__init__()
        self.user = user
        self._pal = get_palette("dark")

        # --- flag critic: blochează editarea structurală (verificat de GravePlotItem) ---
        self.read_only = True

        # --- host state pentru randarea hărții (folosit de EditorActions/GravePlotItem) ---
        self.sectors: Dict[str, Dict] = {}
        self.signals = _AppSignals()
        self.selected_cod_loc: Optional[str] = None

        self.map_view = CemeteryMapView()
        try:
            self.map_view.set_pan_mode(True)
        except Exception:
            pass
        self.actions = EditorActions(self)

        from cemetery_features import CemeteryFeatures
        self.features = CemeteryFeatures(self)

        self.detail_panel = OperatorDetailPanel(self)

        self.setWindowTitle(f"Cimitir — Operator (utilizator: {user['username']})")
        self.setMinimumSize(1300, 780)

        container = QWidget()
        self.setCentralWidget(container)
        root = QHBoxLayout(container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- SIDEBAR ----
        sidebar_wrap = QFrame()
        sidebar_wrap.setFixedWidth(260)
        sidebar_wrap.setStyleSheet(
            f"background-color: {self._pal['bg_panel']}; "
            f"border-right: 1px solid {self._pal['border']};"
        )
        sb_lay = QVBoxLayout(sidebar_wrap)
        sb_lay.setContentsMargins(16, 20, 16, 16)
        sb_lay.setSpacing(12)

        brand_row = QHBoxLayout()
        icon = QLabel("⚱")
        icf = QFont(); icf.setPointSize(20); icon.setFont(icf)
        icon.setStyleSheet(f"color: {self._pal['primary']}; background: transparent; border: none;")
        brand = QLabel("Cimitir")
        bf = QFont(); bf.setPointSize(14); bf.setBold(True); brand.setFont(bf)
        brand.setStyleSheet(f"color: {self._pal['text']}; background: transparent; border: none;")
        brand_row.addWidget(icon); brand_row.addWidget(brand); brand_row.addStretch()
        sb_lay.addLayout(brand_row)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background-color: {self._pal['border']}; max-height: 1px; border: none;")
        sb_lay.addWidget(sep)

        self.nav = QListWidget()
        self.nav.setObjectName("sidebar")
        self.nav.setStyleSheet(
            f"""
            QListWidget#sidebar {{ background: transparent; border: none; padding: 4px 0; font-size: 10.5pt; }}
            QListWidget#sidebar::item {{ padding: 12px 14px; margin: 3px 0; border-radius: 8px; color: {self._pal['text']}; }}
            QListWidget#sidebar::item:hover {{ background-color: {self._pal['bg_hover']}; }}
            QListWidget#sidebar::item:selected {{ background-color: {self._pal['primary']}; color: white; font-weight: 600; }}
            """
        )
        for icon_txt, label in [
            ("🗺", "Hartă"),
            ("📊", "Panou principal"),
            ("🗺", "Căutare locuri"),
            ("🕯", "Căutare înhumați"),
            ("📄", "Contracte"),
            ("📄", "Rapoarte PDF"),
            ("🔲", "QR / Etichete"),
        ]:
            self.nav.addItem(QListWidgetItem(f"  {icon_txt}    {label}"))
        self.nav.setCurrentRow(0)
        sb_lay.addWidget(self.nav)
        sb_lay.addStretch()

        # user card + logout
        user_card = QFrame()
        user_card.setStyleSheet(
            f"background-color: {self._pal['bg']}; "
            f"border: 1px solid {self._pal['border']}; border-radius: 10px;"
        )
        uc_lay = QVBoxLayout(user_card)
        uc_lay.setContentsMargins(12, 10, 12, 10)
        user_row = QHBoxLayout()
        avatar = QLabel(user['username'][:1].upper())
        avf = QFont(); avf.setPointSize(14); avf.setBold(True); avatar.setFont(avf)
        avatar.setFixedSize(36, 36); avatar.setAlignment(Qt.AlignCenter)
        avatar.setStyleSheet(f"background-color: {self._pal['primary']}; color: white; border-radius: 18px;")
        info = QVBoxLayout(); info.setSpacing(0)
        name_lbl = QLabel(user['username'])
        name_lbl.setStyleSheet(f"color: {self._pal['text']}; background: transparent; border: none; font-weight: 600;")
        role_lbl = QLabel(f"role: {user['role']}")
        role_lbl.setStyleSheet(f"color: {self._pal['text_muted']}; background: transparent; border: none; font-size: 9pt;")
        info.addWidget(name_lbl); info.addWidget(role_lbl)
        user_row.addWidget(avatar); user_row.addLayout(info); user_row.addStretch()
        uc_lay.addLayout(user_row)

        btn_logout = QPushButton("🚪  Deconectare")
        btn_logout.setObjectName("user-logout-btn")
        btn_logout.setStyleSheet(
            f"""
            QPushButton {{ background-color: transparent; color: {self._pal['text_muted']};
                border: 1px solid {self._pal['border']}; border-radius: 6px; padding: 6px 10px; margin-top: 8px; }}
            QPushButton:hover {{ background-color: {self._pal['danger']}; color: white; border-color: {self._pal['danger']}; }}
            """
        )
        btn_logout.setCursor(Qt.PointingHandCursor)
        btn_logout.clicked.connect(self._logout)
        uc_lay.addWidget(btn_logout)
        btn_registry = QPushButton("🗂  Schimbă registrul")
        btn_registry.setObjectName("user-switch-registry-btn")
        btn_registry.setCursor(Qt.PointingHandCursor)
        btn_registry.clicked.connect(self._switch_registry)
        uc_lay.addWidget(btn_registry)
        sb_lay.addWidget(user_card)

        root.addWidget(sidebar_wrap)

        # ---- CONTENT STACK ----
        self.stack = QStackedWidget()
        self.stack.addWidget(_MapPage(self))
        self.stack.addWidget(_DashboardPage(self._pal))
        self.stack.addWidget(_SearchLocuriPage(self._pal))
        self.stack.addWidget(_SearchInhumatiPage(self._pal))
        self.stack.addWidget(_ContractePage(self._pal))
        self.stack.addWidget(_RapoartePage(self._pal, self))
        self.stack.addWidget(_QrLabelsPage(self._pal, self))
        root.addWidget(self.stack, stretch=1)

        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)

        self.setStatusBar(self.statusBar())
        self.statusBar().showMessage(f"Autentificat ca {user['username']} (mod operator)")

        # încărcăm harta finală (imagine + locuri + sectoare) din DB
        self._load_map()

    # ---------------------------------------------------------------
    # ÎNCĂRCARE HARTĂ
    # ---------------------------------------------------------------
    def _load_map(self):
        try:
            self.actions.load_saved_map()
        except Exception:
            pass
        try:
            self.actions.load_all_sectors()
        except Exception:
            pass
        try:
            self.actions.load_all_plots()
        except Exception:
            pass
        QTimer.singleShot(60, self._fit_map)

    def _fit_map(self):
        try:
            scene = self.map_view._scene
            rect = scene.itemsBoundingRect()
            if not rect.isEmpty():
                self.map_view.fitInView(rect, Qt.KeepAspectRatio)
        except Exception:
            pass

    # ---------------------------------------------------------------
    # CLICK PE LOC → fișă în panoul lateral
    # ---------------------------------------------------------------
    def on_plot_clicked(self, item):
        data = item.data(0)
        if not isinstance(data, dict):
            return
        cod_loc = data.get("cod_loc")
        if not cod_loc:
            return
        self.selected_cod_loc = cod_loc
        try:
            self.statusBar().showMessage(f"Loc selectat: {cod_loc}")
        except Exception:
            pass
        try:
            self.detail_panel.show_loc(cod_loc)
        except Exception:
            pass
        # ne asigurăm că pagina Hartă e vizibilă
        if self.stack.currentIndex() != 0:
            self.nav.setCurrentRow(0)

    def _refresh_detail(self):
        if self.selected_cod_loc:
            try:
                self.detail_panel.show_loc(self.selected_cod_loc)
            except Exception:
                pass
        try:
            self.features.refresh_if_active()
        except Exception:
            pass

    # ---------------------------------------------------------------
    # OPERAȚIUNI (pe locuri existente)
    # ---------------------------------------------------------------
    def _require_selection(self) -> bool:
        if not self.selected_cod_loc:
            QMessageBox.information(self, "Selectează un loc",
                                    "Selectează întâi un loc pe hartă (click pe poligon).")
            return False
        return True

    def op_add_inhumat(self):
        if not self._require_selection():
            return
        try:
            from add_inhumat_dialog import AddInhumatDialog
        except Exception as e:
            QMessageBox.warning(self, "Eroare", f"Nu am putut deschide dialogul: {e}")
            return
        dlg = AddInhumatDialog(self, self.selected_cod_loc)
        if dlg.exec() == QDialog.Accepted:
            data = dlg.get_data()
            if data:
                try:
                    saved = self.actions.save_inhumat(self.selected_cod_loc, data)
                    if saved:
                        failed_docs = []
                        for doc in dlg.get_documents():
                            did = db.add_document("inhumat", saved, doc["doc_type"],
                                                  doc["path"], nr_evidenta=doc.get("nr_evidenta", ""))
                            if not did:
                                failed_docs.append(doc["path"])
                        if failed_docs:
                            QMessageBox.warning(self, "Documente nesalvate",
                                                "Aceste fișiere NU au putut fi salvate:\n\n"
                                                + "\n".join(failed_docs))
                        self.statusBar().showMessage(
                            f"Înhumat salvat pentru locul {self.selected_cod_loc}")
                    else:
                        QMessageBox.warning(self, "Eroare", "Salvarea a eșuat.")
                except Exception as e:
                    QMessageBox.warning(self, "Eroare", f"Nu am putut salva: {e}")
        self._refresh_detail()

    def op_edit_inhumat(self, inhumat_id):
        try:
            from add_inhumat_dialog import AddInhumatDialog
        except Exception as e:
            QMessageBox.warning(self, "Eroare", f"Nu am putut deschide dialogul: {e}")
            return
        existing = db.get_inhumat(inhumat_id)
        if not existing:
            QMessageBox.warning(self, "Eroare", "Înhumatul nu a fost găsit.")
            return
        cod = existing.get("cod_loc") or self.selected_cod_loc
        dlg = AddInhumatDialog(self, cod, existing=existing)
        if dlg.exec() == QDialog.Accepted:
            data = dlg.get_data()
            if data:
                try:
                    self.actions.save_inhumat(cod, data, inhumat_id=inhumat_id)
                    failed_docs = []
                    for doc in dlg.get_documents():
                        did = db.add_document("inhumat", inhumat_id, doc["doc_type"],
                                              doc["path"], nr_evidenta=doc.get("nr_evidenta", ""))
                        if not did:
                            failed_docs.append(doc["path"])
                    if failed_docs:
                        QMessageBox.warning(self, "Documente nesalvate",
                                            "Aceste fișiere NU au putut fi salvate:\n\n"
                                            + "\n".join(failed_docs))
                    self.statusBar().showMessage(f"Înhumat actualizat ({cod})")
                except Exception as e:
                    QMessageBox.warning(self, "Eroare", f"Nu am putut salva: {e}")
        self._refresh_detail()

    def op_manage_contracts(self):
        if not self._require_selection():
            return
        try:
            from contracts_window import ContractsWindow
        except Exception as e:
            QMessageBox.warning(self, "Eroare", f"Nu am putut deschide Contracte: {e}")
            return
        try:
            dlg = ContractsWindow(self, actions=self.actions,
                                  selected_cod_loc=self.selected_cod_loc)
            dlg.exec()
        except Exception as e:
            QMessageBox.warning(self, "Eroare", f"Eroare la Contracte: {e}")
        self._refresh_detail()

    def op_payments(self):
        if not self._require_selection():
            return
        try:
            from plati_window import PlatiWindow
        except Exception as e:
            QMessageBox.warning(self, "Eroare", f"Nu am putut deschide Plăți: {e}")
            return
        try:
            dlg = PlatiWindow(self, cod_loc=self.selected_cod_loc)
            dlg.exec()
        except Exception as e:
            QMessageBox.warning(self, "Eroare", f"Eroare la Plăți: {e}")
        self._refresh_detail()

    # ---------------------------------------------------------------
    def _switch_registry(self):
        self.registry_switch_requested.emit()
        self.close()

    def _logout(self):
        reply = QMessageBox.question(
            self, "Deconectare",
            "Ești sigur că vrei să te deconectezi?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.logout_requested.emit()
            self.close()


# =============================================================================
# DASHBOARD (păstrat)
# =============================================================================
class _RapoartePage(QWidget):
    """Pagină cu shortcut-uri către rapoartele PDF (deschide RapoarteDialog)."""

    def __init__(self, palette: dict, window):
        super().__init__()
        self._pal = palette
        self._window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(16)

        h1 = QLabel("Rapoarte PDF")
        f = QFont(); f.setPointSize(20); f.setBold(True); h1.setFont(f)
        h1.setStyleSheet(f"color: {palette['text']}; background: transparent; border: none;")
        layout.addWidget(h1)

        h2 = QLabel("Generează rapoarte PDF gata de tipărit")
        h2.setStyleSheet(f"color: {palette['text_muted']}; background: transparent; border: none; font-size: 11pt;")
        layout.addWidget(h2)

        btn = QPushButton("📄  Deschide dialog rapoarte")
        btn.setMinimumHeight(48)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton {{ background-color: {palette['primary']}; color: white; "
            f"border: none; border-radius: 8px; font-weight: 600; font-size: 11pt; padding: 0 20px; }}"
            f"QPushButton:hover {{ background-color: {palette['accent']}; }}"
        )
        btn.clicked.connect(self._open_dialog)
        layout.addWidget(btn)

        info = QLabel(
            "Rapoarte disponibile:\n\n"
            "• Contract de concesiune (PDF gata de semnat)\n"
            "• Chitanță plată (dovadă plată)\n"
            "• Adeverință de înhumare\n"
            "• Situație contracte expirate / care expiră\n"
            "• Raport financiar pe perioadă\n"
            "• Registru înhumați pe perioadă"
        )
        info.setStyleSheet(f"color: {palette['text_muted']}; background: transparent; border: none; font-size: 10.5pt;")
        info.setWordWrap(True)
        layout.addWidget(info)
        layout.addStretch()

    def _open_dialog(self):
        try:
            from rapoarte_dialog import RapoarteDialog
            dlg = RapoarteDialog(self._window)
            dlg.exec()
        except Exception as e:
            QMessageBox.warning(self._window, "Rapoarte", f"Eroare: {e}")


class _QrLabelsPage(QWidget):
    """Pagină full-screen de management QR — embedded direct în user mode."""

    def __init__(self, palette: dict, window):
        super().__init__()
        self._window = window
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        try:
            from qr_management_widget import QrManagementWidget
            self.mgmt = QrManagementWidget(self, current_user="operator")
            lay.addWidget(self.mgmt)
        except Exception as e:
            err = QLabel(f"Eroare la încărcarea managementului QR:\n{e}")
            err.setStyleSheet(f"color: {palette.get('text', '#000')}; padding: 20px;")
            lay.addWidget(err)

    def _open_dialog(self):
        # menținem compatibilitatea cu apeluri vechi (nu se mai folosește)
        pass


# =============================================================================
# DASHBOARD (păstrat)
# =============================================================================
class _DashboardPage(QWidget):
    def __init__(self, palette: dict):
        super().__init__()
        self._pal = palette
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        h1 = QLabel("Panou principal")
        f = QFont(); f.setPointSize(20); f.setBold(True); h1.setFont(f)
        h1.setStyleSheet(f"color: {palette['text']}; background: transparent; border: none;")
        h2 = QLabel("Prezentare generală a cimitirului")
        h2.setStyleSheet(f"color: {palette['text_muted']}; background: transparent; border: none; font-size: 11pt;")
        layout.addWidget(h1); layout.addWidget(h2); layout.addSpacing(10)

        stats_row = QHBoxLayout(); stats_row.setSpacing(16)
        counts = self._get_counts()
        stats_row.addWidget(_StatCard("🗺", "LOCURI DE VECI", str(counts["locuri"]), palette, palette["primary"]))
        stats_row.addWidget(_StatCard("🕯", "ÎNHUMAȚI", str(counts["inhumati"]), palette, palette["accent"]))
        stats_row.addWidget(_StatCard("📄", "CONTRACTE", str(counts["contracte"]), palette, palette["success"]))
        stats_row.addWidget(_StatCard("🏷", "SECTOARE", str(counts["sectoare"]), palette, palette["warning"]))
        layout.addLayout(stats_row)

        info = QFrame()
        info.setStyleSheet(f"background-color: {palette['bg_panel']}; border: 1px solid {palette['border']}; border-radius: 12px;")
        info_lay = QVBoxLayout(info); info_lay.setContentsMargins(24, 20, 24, 20)
        info_title = QLabel("Bun venit în modul operator")
        it_f = QFont(); it_f.setPointSize(14); it_f.setBold(True); info_title.setFont(it_f)
        info_title.setStyleSheet(f"color: {palette['text']}; background: transparent; border: none;")
        info_txt = QLabel(
            "Folosește secțiunea Hartă pentru a lucra direct pe planul cimitirului:\n\n"
            "• Click pe un loc → vezi fișa (înhumați, contracte)\n"
            "• Adaugă înhumați pe locuri existente\n"
            "• Asociază concesionari și generează contracte de concesiune\n"
            "• Înregistrează plăți\n\n"
            "Editarea structurii hărții (desenare poligoane/sectoare) este disponibilă doar în modul administrator."
        )
        info_txt.setWordWrap(True)
        info_txt.setStyleSheet(f"color: {palette['text_muted']}; background: transparent; border: none; font-size: 10.5pt;")
        info_lay.addWidget(info_title); info_lay.addSpacing(8); info_lay.addWidget(info_txt)
        layout.addWidget(info)
        layout.addStretch()

    def _get_counts(self) -> Dict[str, int]:
        result = {"locuri": 0, "inhumati": 0, "contracte": 0, "sectoare": 0}
        try:
            conn = db.get_connection()
            try:
                for k, table in [("locuri", "locuri_de_veci"), ("inhumati", "inhumati"),
                                 ("contracte", "contracte"), ("sectoare", "sectoare")]:
                    r = conn.execute(f"SELECT COUNT(*) as c FROM {table}").fetchone()
                    result[k] = r["c"] if r else 0
            finally:
                conn.close()
        except Exception:
            pass
        return result
