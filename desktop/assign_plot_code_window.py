# assign_plot_code_window.py
"""
Dialog complet de „Atribuire loc de veci" — se deschide după finalizarea unui
poligon pe hartă. Conține TOATE câmpurile disponibile și în dialogul de editare
(sector, parcela, rând, număr, capacitate, disponibilitate, tip, stare,
categorie, suprafață, nr. vechi, număr CF, observații).

Suprafața este calculată automat din poligon (formula Shoelace) — utilizatorul
o poate suprascrie manual dacă dorește.
"""

from typing import List, Tuple, Optional

import os

from cimitir_loc_options import options_for
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QFont, QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLineEdit, QComboBox, QPushButton, QMessageBox, QTextEdit, QLabel,
    QDialogButtonBox, QFileDialog, QFrame, QScrollArea, QWidget,
)


def _polygon_area(points: List[Tuple[float, float]]) -> float:
    """Calculează aria unui poligon prin formula Shoelace. Returnează |aria|."""
    if not points or len(points) < 3:
        return 0.0
    s = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        s += (x1 * y2) - (x2 * y1)
    return abs(s) / 2.0


def _auto_area(points: List[Tuple[float, float]]):
    """Aria poligonului: m² dacă scara e calibrată (px_per_meter), altfel px²."""
    area_px = _polygon_area(points)
    try:
        import database as _db
        v = _db.get_setting("px_per_meter")
        ppm = float(v) if v else None
    except Exception:
        ppm = None
    if ppm and ppm > 0:
        return area_px / (ppm * ppm), "m²"
    return area_px, "px²"


class AssignPlotCodeWindow(QDialog):
    def __init__(self, points: List[Tuple[float, float]], parent=None):
        super().__init__(parent)
        self.points = points
        self._parent = parent  # referință la CemeteryUI

        self.setWindowTitle("Atribuire loc de veci")
        self.setMinimumSize(560, 720)

        # -----------------------------------------------------------------
        # HEADER
        # -----------------------------------------------------------------
        header = QLabel("Completează detaliile locului de veci")
        header_font = QFont()
        header_font.setPointSize(12)
        header_font.setBold(True)
        header.setFont(header_font)
        header.setStyleSheet("color: #0057b0; padding: 6px 0;")

        # -----------------------------------------------------------------
        # SECȚIUNE 1: IDENTIFICARE (cod auto-generat)
        # -----------------------------------------------------------------
        gb_id = QGroupBox("Identificare (cod loc)")
        form_id = QFormLayout()

        self.cmb_sector = QComboBox()
        self.cmb_sector.addItems([chr(c) for c in range(ord("A"), ord("Z") + 1)])
        self.cmb_sector.setCurrentText("A")

        self.txt_parcela = QLineEdit()
        self.txt_parcela.setPlaceholderText("(implicit = sector)")

        self.txt_rand = QLineEdit()
        self.txt_rand.setPlaceholderText("ex: 1 sau 01")
        self.txt_rand.setMaxLength(3)

        self.txt_numar = QLineEdit()
        self.txt_numar.setPlaceholderText("ex: 1 sau 01")
        self.txt_numar.setMaxLength(4)

        self.txt_cod = QLineEdit()
        self.txt_cod.setReadOnly(True)
        cod_font = QFont()
        cod_font.setPointSize(11)
        cod_font.setBold(True)
        self.txt_cod.setFont(cod_font)
        self.txt_cod.setStyleSheet("background:#eaf3ff; color:#0057b0; padding:6px;")

        form_id.addRow("Sector:", self.cmb_sector)
        form_id.addRow("Parcela:", self.txt_parcela)
        form_id.addRow("Rând:", self.txt_rand)
        form_id.addRow("Număr:", self.txt_numar)
        form_id.addRow("Cod loc (generat):", self.txt_cod)
        gb_id.setLayout(form_id)

        # -----------------------------------------------------------------
        # SECȚIUNE 2: CARACTERISTICI
        # -----------------------------------------------------------------
        gb_char = QGroupBox("Caracteristici")
        form_char = QFormLayout()

        self.cmb_capacitate = QComboBox()
        self.cmb_capacitate.addItems(options_for("capacitate"))

        self.cmb_disponibilitate = QComboBox()
        self.cmb_disponibilitate.addItems(options_for("disponibilitate"))

        self.cmb_tip = QComboBox()
        self.cmb_tip.addItems(options_for("tip"))

        self.cmb_stare = QComboBox()
        self.cmb_stare.addItems(options_for("stare"))

        self.cmb_categorie = QComboBox()
        self.cmb_categorie.addItems(options_for("categorie"))

        form_char.addRow("Capacitate:", self.cmb_capacitate)
        form_char.addRow("Disponibilitate:", self.cmb_disponibilitate)
        form_char.addRow("Tip:", self.cmb_tip)
        form_char.addRow("Stare:", self.cmb_stare)
        form_char.addRow("Categorie:", self.cmb_categorie)
        gb_char.setLayout(form_char)

        # -----------------------------------------------------------------
        # SECȚIUNE 3: TOPOGRAFIE / DOCUMENTE
        # -----------------------------------------------------------------
        gb_topo = QGroupBox("Topografie & Documente")
        form_topo = QFormLayout()

        # calculăm aria implicită (în m² dacă scara e calibrată, altfel px²)
        area, area_unit = _auto_area(points)
        self.txt_suprafata = QLineEdit(f"{area:.2f}" if area else "")
        self.txt_suprafata.setPlaceholderText(f"{area_unit} (auto-calculat din poligon)")
        area_hint = QLabel(f"Auto-calculat din {len(points)} puncte: {area:.2f} {area_unit}")
        area_hint.setStyleSheet("color:#666; font-size:11px;")

        self.txt_nr_vechi = QLineEdit()
        self.txt_nr_vechi.setPlaceholderText("Număr vechi (dacă există)")

        self.txt_numar_cf = QLineEdit()
        self.txt_numar_cf.setPlaceholderText("Număr Carte Funciară")

        form_topo.addRow("Suprafață (m²):", self.txt_suprafata)
        form_topo.addRow("", area_hint)
        form_topo.addRow("Nr. vechi:", self.txt_nr_vechi)
        form_topo.addRow("Număr CF:", self.txt_numar_cf)
        gb_topo.setLayout(form_topo)

        # -----------------------------------------------------------------
        # SECȚIUNE 4: OBSERVAȚII
        # -----------------------------------------------------------------
        gb_obs = QGroupBox("Observații")
        form_obs = QVBoxLayout()
        self.txt_observatii = QTextEdit()
        self.txt_observatii.setPlaceholderText("Note interne, particularități...")
        self.txt_observatii.setMaximumHeight(80)
        form_obs.addWidget(self.txt_observatii)
        gb_obs.setLayout(form_obs)

        # -----------------------------------------------------------------
        # SECȚIUNE 5: FOTOGRAFII / DOCUMENTE LOC DE VECI
        # -----------------------------------------------------------------
        gb_photos = QGroupBox("Fotografii / Documente loc de veci (imagine / PDF)")
        vp = QVBoxLayout()
        info = QLabel("Poți atașa oricâte fotografii (poze cu locul de veci) sau documente PDF.\n"
                      "Ele vor fi salvate după crearea locului.")
        info.setStyleSheet("color:#666; font-size:11px;")
        info.setWordWrap(True)
        vp.addWidget(info)

        # container listă fișiere noi
        self._photo_files: list = []  # listă cu {"path": ..., "widget": ...}
        self._photo_list_layout = QVBoxLayout()
        self._photo_list_layout.setSpacing(3)
        vp.addLayout(self._photo_list_layout)

        btn_add_photos = QPushButton("➕  Adaugă fotografii / PDF-uri")
        btn_add_photos.setStyleSheet("QPushButton { text-align:left; padding:6px; }")
        btn_add_photos.clicked.connect(self._pick_photos)
        vp.addWidget(btn_add_photos)
        gb_photos.setLayout(vp)

        # -----------------------------------------------------------------
        # BUTOANE
        # -----------------------------------------------------------------
        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Save).setText("💾 Salvează locul")
        btns.button(QDialogButtonBox.Cancel).setText("Anulează")
        btns.accepted.connect(self.save)
        btns.rejected.connect(self.reject)

        # -----------------------------------------------------------------
        # LAYOUT FINAL
        # -----------------------------------------------------------------
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.addWidget(header)
        content_layout.addWidget(gb_id)
        content_layout.addWidget(gb_char)
        content_layout.addWidget(gb_topo)
        content_layout.addWidget(gb_obs)
        content_layout.addWidget(gb_photos)
        content_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(content)

        layout = QVBoxLayout()
        layout.addWidget(scroll)
        layout.addWidget(btns)
        self.setLayout(layout)

        # -----------------------------------------------------------------
        # SEMNALE
        # -----------------------------------------------------------------
        self.cmb_sector.currentTextChanged.connect(self._on_sector_changed)
        self.txt_rand.textChanged.connect(self._update_code)
        self.txt_numar.textChanged.connect(self._update_code)
        self.txt_parcela.textChanged.connect(self._update_code)

        # inițial parcela = sector
        self.txt_parcela.setText(self.cmb_sector.currentText())
        self._update_code()

    # =====================================================================
    # HELPERS
    # =====================================================================
    @staticmethod
    def _format_two(s: str) -> str:
        s = (s or "").strip()
        digits = "".join(ch for ch in s if ch.isdigit())
        if not digits:
            return ""
        try:
            return f"{int(digits):02d}"
        except Exception:
            return ""

    def _on_sector_changed(self, txt: str):
        # dacă parcela e goală sau egală cu sectorul anterior → o actualizăm
        current = self.txt_parcela.text().strip().upper()
        if not current or len(current) <= 1:
            self.txt_parcela.setText(txt)
        self._update_code()

    def _update_code(self):
        sector = (self.cmb_sector.currentText() or "A").strip().upper()
        r = self._format_two(self.txt_rand.text())
        n = self._format_two(self.txt_numar.text())
        if r and n:
            self.txt_cod.setText(f"{sector}-{r}-{n}")
        elif r:
            self.txt_cod.setText(f"{sector}-{r}-__")
        elif n:
            self.txt_cod.setText(f"{sector}-__-{n}")
        else:
            self.txt_cod.setText(f"{sector}-__-__")

    # =====================================================================
    # FOTOGRAFII / DOCUMENTE (helpers)
    # =====================================================================
    def _pick_photos(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Alege fotografii / PDF-uri pentru locul de veci",
            "", "Documente (*.png *.jpg *.jpeg *.pdf)")
        if not files:
            return
        for f in files:
            if not os.path.exists(f):
                continue
            row = self._mk_photo_row(f)
            self._photo_list_layout.addWidget(row)
            self._photo_files.append({"path": f, "widget": row})

    def _mk_photo_row(self, path: str) -> QWidget:
        w = QFrame()
        w.setStyleSheet("QFrame { background:#eaf3ff; border:1px solid #7fb3ff; border-radius:4px; padding:2px; }")
        h = QHBoxLayout(w)
        h.setContentsMargins(6, 3, 6, 3)
        h.setSpacing(6)
        ext = os.path.splitext(path)[1].lower()
        ic = QLabel("📄" if ext == ".pdf" else "🖼")
        lbl = QLabel(os.path.basename(path))
        lbl.setStyleSheet("color:#0057b0; font-size:9pt;")
        lbl.setToolTip(path)
        btn_del = QPushButton("✕")
        btn_del.setFixedSize(22, 22)
        btn_del.setStyleSheet("QPushButton { color:#dc2626; border:none; background:transparent; }"
                              "QPushButton:hover { color:#991b1b; }")
        btn_del.clicked.connect(lambda: self._remove_photo(w, path))
        h.addWidget(ic)
        h.addWidget(lbl, 1)
        h.addWidget(btn_del)
        return w

    def _remove_photo(self, widget: QWidget, path: str):
        self._photo_files = [f for f in self._photo_files if not (f["path"] == path and f["widget"] is widget)]
        widget.setParent(None)

    # =====================================================================
    # SALVARE
    # =====================================================================
    def save(self):
        sector = (self.cmb_sector.currentText() or "A").strip().upper()
        parcela = self.txt_parcela.text().strip().upper() or sector
        rand = self._format_two(self.txt_rand.text())
        numar = self._format_two(self.txt_numar.text())

        if not rand or not numar:
            QMessageBox.warning(
                self, "Câmpuri incomplete",
                "Completează Rândul și Numărul (cel puțin o cifră fiecare)."
            )
            return

        cod = f"{sector}-{rand}-{numar}"

        actions = getattr(self._parent, "actions", None)
        signals = getattr(self._parent, "signals", None)

        # verificare duplicat
        if actions:
            existing = actions.load_loc_by_cod(cod)
            if existing:
                QMessageBox.warning(self, "Cod duplicat",
                                    f"Există deja un loc cu codul {cod}.")
                return

        # parsare suprafață
        suprafata_val: Optional[float] = None
        supra_txt = self.txt_suprafata.text().strip().replace(",", ".")
        if supra_txt:
            try:
                suprafata_val = float(supra_txt)
            except ValueError:
                QMessageBox.warning(self, "Suprafață invalidă",
                                    "Suprafața trebuie să fie un număr (ex: 3.5).")
                return

        metadata = {
            "sector": sector,
            "parcela": parcela,
            "rand": rand,
            "numar": numar,
            "capacitate": self.cmb_capacitate.currentText().strip(),
            "disponibilitate": self.cmb_disponibilitate.currentText().strip(),
            "tip": self.cmb_tip.currentText().strip(),
            "stare": self.cmb_stare.currentText().strip(),
            "categorie": self.cmb_categorie.currentText().strip(),
            "nr_vechi": self.txt_nr_vechi.text().strip(),
            "numar_cf": self.txt_numar_cf.text().strip(),
        }
        if suprafata_val is not None:
            metadata["suprafata"] = suprafata_val

        obs = self.txt_observatii.toPlainText().strip()

        # -----------------------------------------------------------------
        # SALVARE (prin actions dacă e disponibil, altfel fallback direct DB)
        # -----------------------------------------------------------------
        try:
            if actions:
                # 1. geometria
                actions.save_plot_to_db(cod, self.points, "#0078FF")
                # 2. metadata
                actions.save_loc_metadata(cod, metadata)
                # 3. observații în extra_data
                if obs:
                    extra = actions.load_extra_data(cod) or {}
                    extra["observatii"] = obs
                    actions.save_extra_data(cod, extra)
            else:
                import database as _db
                _db.save_plot_to_db(cod, self.points, "#0078FF")
                _db.save_loc_metadata(cod, metadata)
                if obs:
                    _db.save_extra_data(cod, {"observatii": obs})

            if signals and hasattr(signals, "data_changed"):
                signals.data_changed.emit("loc", cod)

            # -----------------------------------------------------------------
            # SALVARE FOTOGRAFII / DOCUMENTE atașate în dialog
            # -----------------------------------------------------------------
            failed = []
            for pf in self._photo_files:
                try:
                    import database as _db
                    did = _db.add_document("loc", cod, "poza_loc", pf["path"])
                    if not did:
                        failed.append(pf["path"])
                except Exception:
                    failed.append(pf["path"])
            if failed:
                QMessageBox.warning(self, "Documente nesalvate",
                                    "Locul a fost creat, dar aceste fișiere nu au putut fi salvate:\n\n"
                                    + "\n".join(failed))

            # setăm codul pentru caller (finalize_polygon citește)
            self.txt_cod.setText(cod)
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Eroare la salvare", f"Nu am putut salva locul: {e}")
