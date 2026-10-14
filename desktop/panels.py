# panels.py
"""
Right-side panel for Cemetery UI.
Provides Overview, Photos, Inhumati, Juridic (contracts/concesionari),
Financiar, Amenajari (avize) and Istoric tabs.

This file is a complete, defensive and functional implementation intended
to integrate with a backend 'actions' object provided by the main window.

Expected actions API (examples):
 - load_inhumati_for_loc(cod_loc) -> List[Dict]
 - list_photos_for_loc(cod_loc) -> List[Dict]
 - save_photo(cod_loc, path, metadata) -> bool
 - delete_photo(photo_id) -> bool
 - set_cover_photo(cod_loc, photo_id) -> bool
 - load_concesionari() -> List[Dict]
 - load_contracte() -> List[Dict]
 - load_contracte_for_loc(cod_loc) -> List[Dict]
 - save_contract(data, contract_id=None) -> int|None
 - save_extra_data(cod_loc, extra: Dict) -> bool
 - load_extra_data(cod_loc) -> Dict
"""

import os
from typing import List, Dict, Optional, Any
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QStackedWidget, QFormLayout,
    QLineEdit, QTextEdit, QFileDialog, QScrollArea, QGridLayout,
    QDialog, QSizePolicy, QDateEdit, QTableWidget, QTableWidgetItem,
    QComboBox, QHeaderView, QTextBrowser
)
from PySide6.QtCore import Qt, QThreadPool, QRunnable, Signal, QObject, QDate
from PySide6.QtGui import QPixmap

from ui_utils import show_message, ask_confirmation, ask_text_input
import database as db
import work_details as wd

# -------------------------
# Worker signals
# -------------------------
class WorkerSignals(QObject):
    finished = Signal(object)


class ThumbnailLoader(QRunnable):
    """
    Loads a thumbnail in background and emits {"photo": photo_dict, "pix": QPixmap|None}
    """
    def __init__(self, photo: Dict, thumb_size: int = 240):
        super().__init__()
        self.photo = photo
        self.thumb_size = thumb_size
        self.signals = WorkerSignals()

    def run(self):
        try:
            full = self.photo.get("full_path") or self.photo.get("path") or self.photo.get("photo")
            if not full or not os.path.exists(full):
                self.signals.finished.emit({"photo": self.photo, "pix": None})
                return
            pix = QPixmap(full)
            if pix.isNull():
                self.signals.finished.emit({"photo": self.photo, "pix": None})
                return
            pix = pix.scaled(self.thumb_size, self.thumb_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.signals.finished.emit({"photo": self.photo, "pix": pix})
        except Exception:
            self.signals.finished.emit({"photo": self.photo, "pix": None})


# -------------------------
# Photo lightbox & gallery
# -------------------------
class PhotoLightbox(QDialog):
    def __init__(self, parent=None, photos: List[Dict] = None, start_index: int = 0):
        super().__init__(parent)
        self.setWindowTitle("Vizualizare fotografie")
        self.setModal(True)
        self.photos = photos or []
        self.index = start_index if 0 <= start_index < len(self.photos) else 0
        self._build_ui()
        self._show_photo()

    def _build_ui(self):
        self.v = QVBoxLayout()
        self.lbl_image = QLabel()
        self.lbl_image.setAlignment(Qt.AlignCenter)
        self.lbl_image.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.v.addWidget(self.lbl_image, 1)

        self.lbl_meta = QLabel()
        self.v.addWidget(self.lbl_meta)

        btns = QHBoxLayout()
        self.btn_prev = QPushButton("◀")
        self.btn_next = QPushButton("▶")
        self.btn_close = QPushButton("Închide")
        btns.addWidget(self.btn_prev)
        btns.addStretch()
        btns.addWidget(self.btn_next)
        btns.addWidget(self.btn_close)
        self.v.addLayout(btns)

        self.setLayout(self.v)
        self.btn_prev.clicked.connect(self._prev)
        self.btn_next.clicked.connect(self._next)
        self.btn_close.clicked.connect(self.accept)
        self.resize(900, 600)

    def _show_photo(self):
        if not self.photos:
            self.lbl_image.setText("Nicio fotografie")
            self.lbl_meta.setText("")
            return
        p = self.photos[self.index]
        path = p.get("full_path") or p.get("path") or p.get("photo") or p.get("photo_path")
        pix = QPixmap(path) if path and os.path.exists(path) else QPixmap()
        if pix.isNull():
            self.lbl_image.setText("Imagine indisponibilă")
        else:
            scaled = pix.scaled(self.width() - 80, self.height() - 160, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.lbl_image.setPixmap(scaled)
        meta_parts = []
        if p.get("lucrare"):
            meta_parts.append(str(p.get("lucrare")))
        if p.get("caption"):
            meta_parts.append(str(p.get("caption")))
        if p.get("uploader"):
            meta_parts.append(str(p.get("uploader")))
        if p.get("date_added"):
            meta_parts.append(str(p.get("date_added")))
        self.lbl_meta.setText(" — ".join([m for m in meta_parts if m]))

    def _prev(self):
        if not self.photos:
            return
        self.index = (self.index - 1) % len(self.photos)
        self._show_photo()

    def _next(self):
        if not self.photos:
            return
        self.index = (self.index + 1) % len(self.photos)
        self._show_photo()


class PhotoGallery(QWidget):
    def __init__(self, parent=None, photos: List[Dict] = None, thread_pool: QThreadPool = None):
        super().__init__(parent)
        self.photos = photos or []
        self.pool = thread_pool or QThreadPool.globalInstance()
        self.grid_widget = QWidget()
        self.grid = QGridLayout()
        self.grid.setSpacing(8)
        self.grid_widget.setLayout(self.grid)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.grid_widget)

        layout = QVBoxLayout()
        layout.addWidget(self.scroll)
        self.setLayout(layout)

        self.thumb_widgets = {}
        self._render_grid()

    def _render_grid(self):
        # clear existing widgets
        for i in reversed(range(self.grid.count())):
            w = self.grid.itemAt(i).widget()
            if w:
                w.setParent(None)
        self.thumb_widgets.clear()

        cols = 4
        for idx, p in enumerate(self.photos):
            r = idx // cols
            c = idx % cols
            lbl = QLabel("Încărcare...")
            lbl.setFixedSize(200, 140)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("background:#1f1f1f;color:#ddd;border-radius:4px;")
            lbl.setProperty("photo_id", p.get("id"))
            lbl.mousePressEvent = (lambda i: (lambda ev: self._open_lightbox(i)))(idx)
            self.grid.addWidget(lbl, r, c)
            self.thumb_widgets[p.get("id")] = lbl

            loader = ThumbnailLoader(p, thumb_size=320)
            loader.signals.finished.connect(self._on_thumb_loaded)
            self.pool.start(loader)

    def _on_thumb_loaded(self, payload):
        photo = payload.get("photo")
        pix = payload.get("pix")
        pid = photo.get("id")
        lbl = self.thumb_widgets.get(pid)
        if not lbl:
            return
        if pix:
            lbl.setPixmap(pix.scaled(lbl.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            lbl.setText("")
        else:
            lbl.setText("N/A")

    def _open_lightbox(self, index: int):
        dlg = PhotoLightbox(self, photos=self.photos, start_index=index)
        dlg.exec()

    def update_photos(self, photos: List[Dict]):
        self.photos = photos or []
        self._render_grid()


# -------------------------
# Dialog pentru adăugare aviz (amenajări) — extins cu lucrare + fotografie
# -------------------------
class AddAvizDialog(QDialog):
    def __init__(self, parent=None, existing: Optional[Dict] = None):
        super().__init__(parent)
        self.setWindowTitle("Adaugă aviz construcție")
        self.setMinimumSize(420, 180)
        layout = QVBoxLayout()
        form = QFormLayout()

        self.txt_nr = QLineEdit()
        self.date_aviz = QDateEdit()
        self.date_aviz.setDisplayFormat("dd.MM.yyyy")
        self.date_aviz.setCalendarPopup(True)
        self.date_aviz.setDate(QDate.currentDate())

        # numele lucrării
        self.txt_lucrare = QLineEdit()
        self.txt_executant = QLineEdit()
        self.date_inceput = QDateEdit()
        self.date_inceput.setDisplayFormat("dd.MM.yyyy")
        self.date_inceput.setCalendarPopup(True)
        self.date_inceput.setMinimumDate(QDate(1900, 1, 1))
        self.date_inceput.setSpecialValueText("—")
        self.date_inceput.setDate(self.date_inceput.minimumDate())

        # selectare fotografie pentru aviz
        photo_row = QHBoxLayout()
        self.lbl_photo_selected = QLabel("Nicio fotografie selectată")
        self.btn_choose_photo = QPushButton("Alege fotografie")
        photo_row.addWidget(self.lbl_photo_selected)
        photo_row.addWidget(self.btn_choose_photo)

        form.addRow("Număr aviz:", self.txt_nr)
        form.addRow("Data aviz:", self.date_aviz)
        form.addRow("Lucrare efectuată:", self.txt_lucrare)
        form.addRow("Executant:", self.txt_executant)
        form.addRow("Data început:", self.date_inceput)
        form.addRow("Fotografie aviz:", photo_row)

        layout.addLayout(form)

        btns = QHBoxLayout()
        self.btn_ok = QPushButton("Adaugă")
        self.btn_cancel = QPushButton("Anulează")
        btns.addStretch()
        btns.addWidget(self.btn_ok)
        btns.addWidget(self.btn_cancel)
        layout.addLayout(btns)

        self.setLayout(layout)
        self.btn_ok.clicked.connect(self._on_ok)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_choose_photo.clicked.connect(self._choose_photo)

        self._result = None
        self._photo_path: Optional[str] = None

        # populate if existing
        if existing:
            self.txt_nr.setText(str(existing.get("nr", "") or ""))
            d = existing.get("data", "")
            if d:
                qd = QDate.fromString(d, "dd.MM.yyyy")
                if qd.isValid():
                    self.date_aviz.setDate(qd)
            self.txt_lucrare.setText(str(existing.get("lucrare", "") or ""))
            self.txt_executant.setText(str(existing.get("executant", "") or ""))
            qi = QDate.fromString(str(existing.get("data_inceput", "") or ""), "dd.MM.yyyy")
            if qi.isValid():
                self.date_inceput.setDate(qi)
            p = existing.get("photo", "") or existing.get("photo_path", "")
            if p:
                self._photo_path = p
                self.lbl_photo_selected.setText(os.path.basename(p))

    def _choose_photo(self):
        file, _ = QFileDialog.getOpenFileName(self, "Alege fotografie aviz", "", "Imagini (*.png *.jpg *.jpeg)")
        if not file:
            return
        self._photo_path = file
        self.lbl_photo_selected.setText(os.path.basename(file))

    def _on_ok(self):
        nr = self.txt_nr.text().strip()
        d = self.date_aviz.date().toString("dd.MM.yyyy")
        lucrare = self.txt_lucrare.text().strip()
        if not nr:
            show_message(self, "Eroare", "Completați numărul avizului.", level="warning")
            return
        start = "" if self.date_inceput.date() == self.date_inceput.minimumDate() else self.date_inceput.date().toString("dd.MM.yyyy")
        self._result = {"nr": nr, "data": d, "lucrare": lucrare, "photo": self._photo_path,
                        "executant": self.txt_executant.text().strip(), "data_inceput": start}
        self.accept()

    def get_result(self) -> Optional[Dict]:
        return self._result


class AddPlataDialog(QDialog):
    """Dialog pentru adăugarea unei plăți (chitanță)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Adaugă plată")
        self.setMinimumSize(420, 220)
        layout = QVBoxLayout()
        form = QFormLayout()

        self.txt_suma = QLineEdit()
        self.txt_suma.setPlaceholderText("ex: 150.00")

        self.date_plata = QDateEdit()
        self.date_plata.setDisplayFormat("dd.MM.yyyy")
        self.date_plata.setCalendarPopup(True)
        self.date_plata.setDate(QDate.currentDate())

        self.cmb_tip = QComboBox()
        self.cmb_tip.setEditable(True)
        self.cmb_tip.addItems(["Concesiune", "Întreținere", "Taxă", "Reînnoire", "Altele"])

        self.txt_chitanta = QLineEdit()
        self.txt_obs = QLineEdit()

        # atașare foto/PDF chitanță
        chit_row = QHBoxLayout()
        self._chitanta_path: Optional[str] = None
        self.lbl_chit_file = QLabel("nicio chitanță atașată")
        self.lbl_chit_file.setStyleSheet("color:#94a3b8; font-size:11px;")
        self.btn_chit_file = QPushButton("Alege foto/PDF…")
        self.btn_chit_file.clicked.connect(self._choose_chitanta)
        chit_row.addWidget(self.lbl_chit_file, 1)
        chit_row.addWidget(self.btn_chit_file)

        form.addRow("Sumă (RON):", self.txt_suma)
        form.addRow("Data plății:", self.date_plata)
        form.addRow("Tip plată:", self.cmb_tip)
        form.addRow("Nr. chitanță:", self.txt_chitanta)
        form.addRow("Chitanță scanată:", chit_row)
        form.addRow("Observații:", self.txt_obs)

        layout.addLayout(form)

        btns = QHBoxLayout()
        self.btn_ok = QPushButton("Adaugă")
        self.btn_cancel = QPushButton("Anulează")
        btns.addStretch()
        btns.addWidget(self.btn_ok)
        btns.addWidget(self.btn_cancel)
        layout.addLayout(btns)

        self.setLayout(layout)
        self.btn_ok.clicked.connect(self._on_ok)
        self.btn_cancel.clicked.connect(self.reject)
        self._result = None

    def _choose_chitanta(self):
        file, _ = QFileDialog.getOpenFileName(
            self, "Alege chitanță (foto/PDF)", "", "Documente (*.png *.jpg *.jpeg *.pdf)")
        if not file:
            return
        self._chitanta_path = file
        self.lbl_chit_file.setText(os.path.basename(file))
        self.lbl_chit_file.setStyleSheet("color:#22c55e; font-size:11px;")

    def _on_ok(self):
        raw = self.txt_suma.text().strip().replace(",", ".")
        try:
            suma = float(raw)
        except (ValueError, TypeError):
            show_message(self, "Eroare", "Introduceți o sumă validă (ex: 150.00).", level="warning")
            return
        if suma <= 0:
            show_message(self, "Eroare", "Suma trebuie să fie mai mare decât 0.", level="warning")
            return
        self._result = {
            "suma": suma,
            "data_plata": self.date_plata.date().toString("dd.MM.yyyy"),
            "tip": self.cmb_tip.currentText().strip(),
            "chitanta_nr": self.txt_chitanta.text().strip(),
            "observatii": self.txt_obs.text().strip(),
            "chitanta_file": self._chitanta_path,
        }
        self.accept()

    def get_result(self) -> Optional[Dict]:
        return self._result



# -------------------------
# DocumentStrip — bandă reutilizabilă pentru foto/PDF pe entitate (loc/contract/inhumat/aviz/plata)
# -------------------------
class DocumentStrip(QWidget):
    """
    Afișează documentele (foto/PDF) pentru o entitate, cu buton „Adaugă"
    și „Șterge" pe fiecare rând. Reîncarcă din DB la fiecare refresh().

    entity_type: "loc" | "contract" | "inhumat" | "aviz" | "plata"
    entity_id: str/int
    doc_type: filtru optional (ex: "poza_loc")
    """
    def __init__(self, entity_type: str, entity_id=None, doc_type: str = "",
                 title: str = "Documente (foto/PDF)", parent=None, exclude_types=()):
        super().__init__(parent)
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.doc_type = doc_type or ""
        self.exclude_types = tuple(exclude_types)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        self.title_lbl = QLabel(title)
        self.title_lbl.setStyleSheet("font-weight:600; color:#4fc3f7; font-size:12px;")
        v.addWidget(self.title_lbl)

        # container listă
        self._list_container = QVBoxLayout()
        self._list_container.setSpacing(2)
        v.addLayout(self._list_container)

        # buton adaugă
        self.btn_add = QPushButton("➕  Adaugă foto / PDF")
        self.btn_add.setStyleSheet(
            "QPushButton { background:transparent; color:#4fc3f7; border:1px dashed #4fc3f7;"
            " border-radius:6px; padding:5px; font-size:11px; }"
            "QPushButton:hover { background:#1e2a30; }")
        self.btn_add.clicked.connect(self._on_add)
        v.addWidget(self.btn_add)

        self.refresh()

    def set_entity(self, entity_type: str, entity_id, doc_type: str = ""):
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.doc_type = doc_type or ""
        self.refresh()

    def refresh(self):
        # curățăm lista
        while self._list_container.count():
            it = self._list_container.takeAt(0)
            w = it.widget()
            if w:
                w.setParent(None)

        if self.entity_id is None or self.entity_id == "":
            empty = QLabel("(selectează întâi o intrare)")
            empty.setStyleSheet("color:#888; font-size:11px; padding:2px;")
            self._list_container.addWidget(empty)
            self.btn_add.setEnabled(False)
            return
        self.btn_add.setEnabled(True)

        try:
            docs = db.list_documents(self.entity_type, self.entity_id,
                                     self.doc_type or None)
        except Exception:
            docs = []
        if self.exclude_types:
            docs = [d for d in docs if d.get("doc_type") not in self.exclude_types]
        if not docs:
            empty = QLabel("(niciun document)")
            empty.setStyleSheet("color:#888; font-size:11px; padding:2px;")
            self._list_container.addWidget(empty)
            return
        for d in docs:
            self._list_container.addWidget(self._mk_row(d))

    def _mk_row(self, doc: Dict) -> QWidget:
        w = QWidget()
        w.setStyleSheet("QWidget { background:#1e293b; border:1px solid #334155; border-radius:4px; }")
        h = QHBoxLayout(w)
        h.setContentsMargins(6, 3, 6, 3)
        h.setSpacing(6)
        path = doc.get("file_path", "")
        ext = os.path.splitext(path)[1].lower()
        ic = QLabel("📄" if ext == ".pdf" else "🖼")
        name = os.path.basename(path) or "(fișier)"
        cap = doc.get("caption") or ""
        nr = doc.get("nr_evidenta") or ""
        extra = ""
        if nr:
            extra += f"  nr. {nr}"
        if cap:
            extra += f"  — {cap}"
        lbl = QLabel(name + extra)
        lbl.setStyleSheet("color:#e2e8f0; font-size:11px;")
        lbl.setToolTip(path)

        btn_open = QPushButton("Deschide")
        btn_open.setStyleSheet("QPushButton { padding:2px 8px; font-size:10px; }")
        btn_open.clicked.connect(lambda: self._open(path))

        btn_del = QPushButton("✕")
        btn_del.setFixedSize(22, 22)
        btn_del.setStyleSheet("QPushButton { color:#f87171; border:none; background:transparent; }"
                              "QPushButton:hover { color:#ef4444; }")
        btn_del.clicked.connect(lambda: self._delete(doc.get("id")))

        h.addWidget(ic)
        h.addWidget(lbl, 1)
        h.addWidget(btn_open)
        h.addWidget(btn_del)
        return w

    def _open(self, path: str):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        if not path or not os.path.exists(path):
            show_message(self, "Info", "Fișierul nu a fost găsit pe disc.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _delete(self, doc_id):
        if not doc_id:
            return
        if not ask_confirmation(self, "Ștergere", "Ștergi acest document?", default_yes=False):
            return
        try:
            ok = db.delete_document(int(doc_id))
        except Exception as e:
            show_message(self, "Eroare", f"Ștergere eșuată: {e}", level="warning")
            return
        if ok:
            self.refresh()

    def _on_add(self):
        if self.entity_id is None:
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, "Alege foto / PDF", "", "Documente (*.png *.jpg *.jpeg *.pdf)")
        if not files:
            return
        failed = []
        for f in files:
            try:
                did = db.add_document(self.entity_type, self.entity_id,
                                      self.doc_type or "", f)
                if not did:
                    failed.append(f)
            except Exception:
                failed.append(f)
        if failed:
            show_message(self, "Documente nesalvate",
                         "Următoarele fișiere nu au putut fi salvate:\n\n" + "\n".join(failed),
                         level="warning")
        self.refresh()
# -------------------------
# AvizDocsPanel — detalii lucrare (din telefon/MAIN) + documente grupate pe etape
# -------------------------
class AvizDocsPanel(QWidget):
    """Stare, dată finalizare, observații + ÎNAINTE / ÎN TIMPUL LUCRĂRII / DUPĂ + necategorizate (aceleași `documents`)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        form = QFormLayout()
        self.lbl_exec = QLabel("—")
        self.lbl_exec.setObjectName("aviz-executant")
        self.lbl_start = QLabel("—")
        self.lbl_start.setObjectName("aviz-data-inceput")
        self.lbl_stare = QLabel("—")
        self.lbl_stare.setObjectName("aviz-stare")
        self.lbl_final = QLabel("—")
        self.lbl_final.setObjectName("aviz-finalizare")
        self.lbl_obs = QLabel("—")
        self.lbl_obs.setObjectName("aviz-observatii")
        self.lbl_obs.setWordWrap(True)
        self.lbl_obs.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow("Executant:", self.lbl_exec)
        form.addRow("Data început:", self.lbl_start)
        form.addRow("Stare:", self.lbl_stare)
        form.addRow("Data finalizării:", self.lbl_final)
        form.addRow("Observații:", self.lbl_obs)
        v.addLayout(form)
        self.strips = {}
        for key, label in wd.STAGES:
            st = DocumentStrip("aviz", None, doc_type=key, title=label)
            st.setObjectName("aviz-stage-" + key)
            self.strips[key] = st
            v.addWidget(st)
        self.other = DocumentStrip("aviz", None, doc_type="", title=wd.OTHER_LABEL, exclude_types=wd.STAGE_TYPES)
        self.other.setObjectName("aviz-stage-other")
        v.addWidget(self.other)
        v.addStretch()

    def set_entity(self, entity_type: str, entity_id, doc_type: str = ""):
        av = None
        if entity_id not in (None, ""):
            try:
                av = db.get_aviz(int(entity_id))
            except Exception:
                av = None
        if av:
            self.lbl_exec.setText(str(av.get("executant") or "") or "—")
            self.lbl_start.setText(str(av.get("data_inceput") or "") or "—")
            self.lbl_stare.setText(wd.stare_label(av))
            self.lbl_final.setText(wd.finalizare_label(av) or "—")
            self.lbl_obs.setText(str(av.get("observatii") or "") or "—")
        else:
            for lbl in (self.lbl_exec, self.lbl_start, self.lbl_stare, self.lbl_final, self.lbl_obs):
                lbl.setText("—")
        for st in list(self.strips.values()) + [self.other]:
            st.set_entity(entity_type, entity_id, st.doc_type)


# -------------------------
# RightPanel
# -------------------------
class RightPanel(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.w = main_window
        self.current_cod_loc: Optional[str] = None
        self.thread_pool = QThreadPool.globalInstance()

        # concesionari map (label -> id) - initialize early to avoid AttributeError
        self._conces_map: Dict[str, Optional[int]] = {}

        # menu
        self.menu = QListWidget()
        self.menu.setFixedWidth(200)
        self.menu.setStyleSheet("""
            QListWidget { background: #222; color: #eee; border: none; }
            QListWidget::item { padding: 12px; margin: 2px; font-size: 13px; }
            QListWidget::item:selected { background: #2f3a40; border-left: 4px solid #4fc3f7; }
        """)
        items = [
            ("📄  Overview", "overview"),
            ("🖼  Fotografii", "foto"),
            ("⚰  Înhumați", "inhumati"),
            ("⚖  Date juridice", "juridic"),
            ("💰  Financiar", "financiar"),
            ("🧱  Amenajări", "amenajari"),
            ("🕓  Istoric", "istoric"),
        ]
        for text, key in items:
            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, key)
            self.menu.addItem(it)
        self.menu.currentRowChanged.connect(self._change_tab)

        # header
        self.header = QWidget()
        self._build_header()

        # stack
        self.stack = QStackedWidget()
        self.tab_overview = self._build_tab_overview()
        self.tab_foto = self._build_tab_foto()
        self.tab_inhumati = self._build_tab_inhumati()
        self.tab_juridic = self._build_tab_juridic()
        self.tab_financiar = self._build_tab_financiar()
        self.tab_amenajari = self._build_tab_amenajari()
        self.tab_istoric = self._build_tab_istoric()

        for t in (self.tab_overview, self.tab_foto, self.tab_inhumati,
                  self.tab_juridic, self.tab_financiar, self.tab_amenajari, self.tab_istoric):
            self.stack.addWidget(t)

        # layout
        main_layout = QVBoxLayout()
        top = QHBoxLayout()
        top.addWidget(self.menu)
        right_col = QVBoxLayout()
        right_col.addWidget(self.header)
        right_col.addWidget(self.stack)
        top.addLayout(right_col, 1)
        main_layout.addLayout(top)
        self.setLayout(main_layout)

        self.menu.setCurrentRow(0)

        # editable state
        self._editing_enabled = False

    # -------------------------
    # Header
    # -------------------------
    def _build_header(self):
        self.header.setStyleSheet("background:#1b1b1b; color:#fff; padding:8px;")
        h = QHBoxLayout()
        left = QVBoxLayout()
        self.lbl_cod = QLabel("Cod loc: —")
        self.lbl_cod.setStyleSheet("font-weight:600; font-size:16px;")
        self.lbl_sub = QLabel("Stare: — | Capacitate: —")
        left.addWidget(self.lbl_cod)
        left.addWidget(self.lbl_sub)
        h.addLayout(left)

        h.addStretch()

        btns = QHBoxLayout()
        self.btn_edit = QPushButton("Edit")
        self.btn_save = QPushButton("Salvează")
        self.btn_pdf = QPushButton("PDF")
        for b in (self.btn_edit, self.btn_save, self.btn_pdf):
            b.setFixedHeight(30)
            b.setStyleSheet("padding:6px;")
            btns.addWidget(b)
        h.addLayout(btns)
        self.header.setLayout(h)

        self.btn_save.clicked.connect(self._save_all)
        self.btn_edit.clicked.connect(self._toggle_editing)
        self.btn_pdf.clicked.connect(lambda: show_message(self, "PDF", "Funcție generare PDF (neimplementată)."))

    # -------------------------
    # Tabs builders
    # -------------------------
    def _build_tab_overview(self):
        w = QWidget()
        form = QFormLayout()
        self.ov_cod_loc = QLineEdit(); self.ov_cod_loc.setReadOnly(True)
        self.ov_sector = QLineEdit(); self.ov_sector.setReadOnly(True)
        self.ov_parcela = QLineEdit(); self.ov_parcela.setReadOnly(True)
        self.ov_rand = QLineEdit(); self.ov_rand.setReadOnly(True)
        self.ov_numar = QLineEdit(); self.ov_numar.setReadOnly(True)
        self.ov_stare = QLineEdit(); self.ov_stare.setReadOnly(True)
        self.ov_capacitate = QLineEdit(); self.ov_capacitate.setReadOnly(True)
        self.ov_observatii = QTextEdit(); self.ov_observatii.setReadOnly(True); self.ov_observatii.setFixedHeight(80)

        form.addRow("Cod loc:", self.ov_cod_loc)
        form.addRow("Sector:", self.ov_sector)
        form.addRow("Parcela:", self.ov_parcela)
        form.addRow("Rând:", self.ov_rand)
        form.addRow("Număr:", self.ov_numar)
        form.addRow("Stare:", self.ov_stare)
        form.addRow("Capacitate:", self.ov_capacitate)
        form.addRow("Observații:", self.ov_observatii)

        # strip documente loc (foto + PDF)
        self.doc_strip_overview = DocumentStrip(
            "loc", entity_id=None, doc_type="poza_loc",
            title="Fotografii / documente loc (foto / PDF)"
        )

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.addLayout(form)
        v.addSpacing(8)
        v.addWidget(self.doc_strip_overview)
        v.addStretch()
        scroll.setWidget(inner)

        outer = QVBoxLayout()
        outer.addWidget(scroll)
        w.setLayout(outer)
        return w

    def _build_tab_foto(self):
        w = QWidget()
        v = QVBoxLayout()
        top = QHBoxLayout()
        self.btn_upload = QPushButton("Încarcă fotografie")
        self.btn_set_cover = QPushButton("Setează cover")
        self.btn_delete_photo = QPushButton("Șterge fotografie")
        top.addWidget(self.btn_upload)
        top.addWidget(self.btn_set_cover)
        top.addWidget(self.btn_delete_photo)
        top.addStretch()
        v.addLayout(top)

        self.photo_gallery = PhotoGallery(self, photos=[], thread_pool=self.thread_pool)
        v.addWidget(self.photo_gallery, 1)
        w.setLayout(v)

        self.btn_upload.clicked.connect(self._upload_photo)
        self.btn_delete_photo.clicked.connect(self._delete_selected_photo)
        self.btn_set_cover.clicked.connect(self._set_cover_photo)
        return w

    def _build_tab_inhumati(self):
        w = QWidget()
        v = QVBoxLayout()
        info = QLabel('Persoane inhumate — foloseste sectiunea "Documente" din fiecare card pentru certificate deces / adeverinte inhumare (foto/PDF).')
        info.setStyleSheet("color:#aaa; font-size:11px; padding:2px 0;")
        info.setWordWrap(True)
        v.addWidget(info)

        # container cu carduri per înhumat
        self.inhumati_scroll = QScrollArea()
        self.inhumati_scroll.setWidgetResizable(True)
        self.inhumati_container = QWidget()
        self.inhumati_layout = QVBoxLayout(self.inhumati_container)
        self.inhumati_layout.setSpacing(8)
        self.inhumati_layout.addStretch()
        self.inhumati_scroll.setWidget(self.inhumati_container)
        v.addWidget(self.inhumati_scroll, 1)

        w.setLayout(v)
        return w

    def _mk_inhumat_card(self, inh: Dict) -> QWidget:
        card = QWidget()
        card.setStyleSheet("QWidget { background:#1a1a1a; border:1px solid #333; border-radius:6px; }")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(10, 8, 10, 10)
        cv.setSpacing(4)
        title = QLabel(f"⚰  <b>{inh.get('nume','')} {inh.get('prenume','')}</b>")
        title.setStyleSheet("color:#e8e8e8; font-size:13px;")
        cv.addWidget(title)
        meta = QLabel(
            f"CNP: {inh.get('cnp','') or '—'}   |   Deces: {inh.get('data_deces','') or '—'}   |   "
            f"Înhumare: {inh.get('data_inhumare','') or '—'}"
        )
        meta.setStyleSheet("color:#bbb; font-size:11px;")
        cv.addWidget(meta)
        nrc = inh.get("nr_certificat_deces") or ""
        nra = inh.get("nr_adeverinta_inhumare") or ""
        if nrc or nra:
            nrs = QLabel(f"Nr. certificat deces: <b>{nrc or '—'}</b>   |   Nr. adeverință înhumare: <b>{nra or '—'}</b>")
            nrs.setStyleSheet("color:#9ccc65; font-size:11px;")
            cv.addWidget(nrs)
        strip = DocumentStrip(
            "inhumat", entity_id=inh.get("id"), doc_type="",
            title="Documente înhumat (certificat deces, adeverință înhumare — foto/PDF)"
        )
        cv.addWidget(strip)
        return card

    def _build_tab_juridic(self):
        w = QWidget()
        form = QFormLayout()

        # contracte combo (populat from DB for cod_loc)
        self.cmb_contracte = QComboBox()
        self.cmb_contracte.setEditable(False)
        self.cmb_contracte.addItem("")  # empty default
        self.cmb_contracte.currentIndexChanged.connect(self._on_contract_selected)

        # concesionar combo (editable for search/typing)
        self.cmb_concesionar = QComboBox()
        self.cmb_concesionar.setEditable(True)
        if not hasattr(self, "_conces_map"):
            self._conces_map = {}

        self.ju_contract_nr = QLineEdit()
        self.ju_contract_data = QLineEdit()
        self.ju_valabilitate = QLineEdit()

        form.addRow("Contract asociat:", self.cmb_contracte)
        form.addRow("Concesionar:", self.cmb_concesionar)
        form.addRow("Contract nr.:", self.ju_contract_nr)
        form.addRow("Data contract:", self.ju_contract_data)
        form.addRow("Valabilitate:", self.ju_valabilitate)

        btn_row = QHBoxLayout()
        self.btn_save_contract = QPushButton("Salvează contract")
        btn_row.addStretch()
        btn_row.addWidget(self.btn_save_contract)

        # strip documente contract (scan contract + acte)
        self.doc_strip_contract = DocumentStrip(
            "contract", entity_id=None, doc_type="",
            title="Documente contract (scan / anexe — foto / PDF)"
        )

        v = QVBoxLayout()
        v.addLayout(form)
        v.addLayout(btn_row)
        v.addSpacing(6)
        v.addWidget(self.doc_strip_contract)
        v.addStretch()
        w.setLayout(v)

        self.btn_save_contract.clicked.connect(self._save_contract_from_juridic)
        return w

    def _build_tab_financiar(self):
        w = QWidget()
        v = QVBoxLayout()

        top = QHBoxLayout()
        self.btn_add_plata = QPushButton("Adaugă plată")
        self.btn_remove_plata = QPushButton("Șterge plată selectată")
        top.addWidget(self.btn_add_plata)
        top.addWidget(self.btn_remove_plata)
        top.addStretch()
        self.lbl_fin_total = QLabel("Total încasat: 0.00 RON")
        self.lbl_fin_total.setStyleSheet("font-weight:600; font-size:14px; color:#4fc3f7;")
        top.addWidget(self.lbl_fin_total)
        v.addLayout(top)

        # tabel plăți: Data | Sumă | Tip | Chitanță | Observații
        self.tbl_plati = QTableWidget(0, 5)
        self.tbl_plati.setHorizontalHeaderLabels(
            ["Data", "Sumă (RON)", "Tip", "Nr. chitanță", "Observații"])
        header = self.tbl_plati.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        self.tbl_plati.setSelectionBehavior(QTableWidget.SelectRows)
        self.tbl_plati.setSelectionMode(QTableWidget.SingleSelection)
        self.tbl_plati.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(self.tbl_plati)

        # documente / chitanțe atașate la plata selectată
        self.doc_strip_plata = DocumentStrip(
            "plata", entity_id=None, doc_type="",
            title="Chitanțe / documente atașate plății selectate (foto / PDF)"
        )
        v.addWidget(self.doc_strip_plata)

        w.setLayout(v)

        self.btn_add_plata.clicked.connect(self._add_plata)
        self.btn_remove_plata.clicked.connect(self._remove_selected_plata)
        self.btn_remove_plata.setEnabled(False)
        self.tbl_plati.itemSelectionChanged.connect(self._on_plata_selection_changed)
        return w

    def _on_plata_selection_changed(self):
        self.btn_remove_plata.setEnabled(
            bool(self.tbl_plati.selectedItems()) and self._editing_enabled)
        # actualizează doc strip pentru plata selectată
        try:
            row = self.tbl_plati.currentRow()
            if row < 0:
                self.doc_strip_plata.set_entity("plata", None)
                return
            item = self.tbl_plati.item(row, 0)
            plata_id = item.data(Qt.UserRole) if item else None
            self.doc_strip_plata.set_entity("plata", plata_id)
        except Exception:
            pass

    def _load_plati(self):
        self.tbl_plati.setRowCount(0)
        if not self.current_cod_loc:
            self.lbl_fin_total.setText("Total încasat: 0.00 RON")
            return
        try:
            plati = db.list_plati(self.current_cod_loc) or []
        except Exception:
            plati = []
        for p in plati:
            r = self.tbl_plati.rowCount()
            self.tbl_plati.insertRow(r)
            data_item = QTableWidgetItem(str(p.get("data_plata", "") or ""))
            data_item.setData(Qt.UserRole, p.get("id"))
            self.tbl_plati.setItem(r, 0, data_item)
            self.tbl_plati.setItem(r, 1, QTableWidgetItem(f"{float(p.get('suma') or 0):.2f}"))
            self.tbl_plati.setItem(r, 2, QTableWidgetItem(str(p.get("tip", "") or "")))
            self.tbl_plati.setItem(r, 3, QTableWidgetItem(str(p.get("chitanta_nr", "") or "")))
            self.tbl_plati.setItem(r, 4, QTableWidgetItem(str(p.get("observatii", "") or "")))
        try:
            total = db.total_plati(self.current_cod_loc)
        except Exception:
            total = 0.0
        self.lbl_fin_total.setText(f"Total încasat: {total:.2f} RON")

    def _add_plata(self):
        if not self.current_cod_loc:
            show_message(self, "Eroare", "Selectați un loc înainte de a adăuga o plată.", level="warning")
            return
        dlg = AddPlataDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        res = dlg.get_result()
        if not res:
            return
        try:
            new_id = db.add_plata(
                self.current_cod_loc, res["suma"], res["data_plata"],
                tip=res["tip"], chitanta_nr=res["chitanta_nr"], observatii=res["observatii"])
            if new_id:
                # dacă utilizatorul a atașat o chitanță (foto/PDF), o salvăm
                chit_path = res.get("chitanta_file")
                if chit_path:
                    try:
                        db.add_document("plata", new_id, "chitanta",
                                        chit_path,
                                        nr_evidenta=res.get("chitanta_nr", ""))
                    except Exception as e:
                        show_message(self, "Atenție",
                                     f"Plata s-a salvat, dar nu am putut salva chitanța: {e}",
                                     level="warning")
                self._load_plati()
                self._load_istoric()
                show_message(self, "OK", "Plata a fost înregistrată.")
            else:
                show_message(self, "Eroare", "Nu am putut salva plata.", level="warning")
        except Exception as e:
            show_message(self, "Eroare", f"Eroare la salvarea plății: {e}", level="error")

    def _remove_selected_plata(self):
        row = self.tbl_plati.currentRow()
        if row < 0:
            show_message(self, "Info", "Selectați o plată pentru ștergere.")
            return
        item = self.tbl_plati.item(row, 0)
        plata_id = item.data(Qt.UserRole) if item else None
        if not plata_id:
            self.tbl_plati.removeRow(row)
            return
        if not ask_confirmation(self, "Șterge plată", "Sigur doriți să ștergeți plata selectată?", default_yes=False):
            return
        try:
            if db.delete_plata(int(plata_id)):
                self._load_plati()
                self._load_istoric()
                show_message(self, "OK", "Plata a fost ștearsă.")
            else:
                show_message(self, "Eroare", "Ștergerea a eșuat.", level="warning")
        except Exception as e:
            show_message(self, "Eroare", f"Eroare la ștergere: {e}", level="error")

    def _build_tab_amenajari(self):
        w = QWidget()
        v = QVBoxLayout()
        top = QHBoxLayout()
        self.btn_add_aviz = QPushButton("Adaugă aviz")
        self.btn_remove_aviz = QPushButton("Șterge aviz selectat")
        top.addWidget(self.btn_add_aviz)
        top.addWidget(self.btn_remove_aviz)
        top.addStretch()
        v.addLayout(top)

        # tabel avize: Număr | Data | Lucrare | Fotografie
        self.tbl_avize = QTableWidget(0, 8)
        self.tbl_avize.setHorizontalHeaderLabels(["Număr aviz", "Data aviz", "Lucrare", "Fotografie", "Stare", "Finalizată la", "Executant", "Data început"])
        header = self.tbl_avize.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)
        self.tbl_avize.setMaximumHeight(190)
        self.tbl_avize.setSelectionBehavior(QTableWidget.SelectRows)
        self.tbl_avize.setSelectionMode(QTableWidget.SingleSelection)
        v.addWidget(self.tbl_avize)

        # documente atașate la avizul selectat (foto lucrare + acte)
        self.doc_strip_aviz = AvizDocsPanel()
        aviz_scroll = QScrollArea()
        aviz_scroll.setWidgetResizable(True)
        aviz_scroll.setWidget(self.doc_strip_aviz)
        v.addWidget(aviz_scroll, 1)
        w.setLayout(v)

        # Connect actions
        self.btn_add_aviz.clicked.connect(self._add_aviz)
        self.btn_remove_aviz.clicked.connect(self._remove_selected_aviz)

        # initial state
        self.btn_add_aviz.setEnabled(True)
        self.btn_remove_aviz.setEnabled(False)

        # enable remove button when a row is selected (and editing enabled)
        self.tbl_avize.itemSelectionChanged.connect(self._on_aviz_selection_changed)
        # double click on photo cell opens image
        self.tbl_avize.cellDoubleClicked.connect(self._on_aviz_cell_double_clicked)

        return w

    def _on_aviz_selection_changed(self):
        sel = self.tbl_avize.selectedItems()
        self.btn_remove_aviz.setEnabled(bool(sel) and self._editing_enabled)
        # actualizează doc strip pentru avizul selectat
        try:
            row = self.tbl_avize.currentRow()
            if row < 0:
                self.doc_strip_aviz.set_entity("aviz", None)
                return
            nr_item = self.tbl_avize.item(row, 0)
            aviz_id = nr_item.data(Qt.UserRole) if nr_item else None
            self.doc_strip_aviz.set_entity("aviz", aviz_id)
        except Exception:
            pass

    def _on_aviz_cell_double_clicked(self, row: int, column: int):
        # if clicked column is fotografia (index 3), try to open image in lightbox
        if column != 3:
            return
        item = self.tbl_avize.item(row, column)
        if not item:
            return
        photo_path = item.data(Qt.UserRole)
        if not photo_path:
            show_message(self, "Info", "Nu există fotografie asociată acestui aviz.")
            return
        if not os.path.exists(photo_path):
            show_message(self, "Info", "Fișierul fotografiei nu a fost găsit.")
            return
        photo_entry = {"photo_path": photo_path, "lucrare": self.tbl_avize.item(row, 2).text() if self.tbl_avize.item(row, 2) else ""}
        dlg = PhotoLightbox(self, photos=[photo_entry], start_index=0)
        dlg.exec()

    def _build_tab_istoric(self):
        w = QWidget()
        v = QVBoxLayout()
        title = QLabel("Istoric loc de veci")
        title.setStyleSheet("font-weight:600; font-size:15px; padding:4px 0;")
        v.addWidget(title)
        self.hist_browser = QTextBrowser()
        self.hist_browser.setOpenExternalLinks(False)
        self.hist_browser.setStyleSheet(
            "QTextBrowser { background:#1e1e1e; color:#e8e8e8; border:1px solid #333; padding:8px; }")
        v.addWidget(self.hist_browser, 1)
        w.setLayout(v)
        return w

    def _load_istoric(self):
        """Construiește o cronologie din evenimentele reale ale locului."""
        if not hasattr(self, "hist_browser"):
            return
        if not self.current_cod_loc:
            self.hist_browser.setHtml("<p style='color:#888'>Niciun loc selectat.</p>")
            return

        events = []  # (sort_key, date_str, icon, color, title, detail)

        def _sk(date_str):
            for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
                try:
                    return datetime.strptime(str(date_str).strip()[:10], fmt)
                except Exception:
                    continue
            return datetime.min

        cod = self.current_cod_loc
        # Contracte
        try:
            contracts = [c for c in (db.load_contracte() or []) if c.get("cod_loc") == cod]
        except Exception:
            contracts = []
        for c in contracts:
            d = c.get("data_contract") or c.get("data_inceput") or ""
            events.append((_sk(d), d, "⚖", "#9ccc65",
                           f"Contract nr. {c.get('numar_contract','—')}",
                           f"Valabilitate: {c.get('data_sfarsit') or c.get('valabilitate') or '—'}"))
        # Înhumări
        try:
            inhumati = db.load_inhumati_for_loc(cod) or []
        except Exception:
            inhumati = []
        for p in inhumati:
            d = p.get("data_inhumare") or ""
            nume = f"{p.get('nume','')} {p.get('prenume','')}".strip()
            events.append((_sk(d), d, "⚰", "#b0bec5",
                           f"Înhumare: {nume or '—'}",
                           f"Deces: {p.get('data_deces') or '—'}"))
        # Avize / lucrări
        try:
            avize = db.list_avize_for_loc(cod) or []
        except Exception:
            avize = []
        for a in avize:
            d = a.get("data_aviz") or ""
            events.append((_sk(d), d, "🧱", "#ffb74d",
                           f"Aviz nr. {a.get('nr_aviz','—')}",
                           f"Lucrare: {a.get('lucrare') or '—'}"))
        # Plăți
        try:
            plati = db.list_plati(cod) or []
        except Exception:
            plati = []
        for p in plati:
            d = p.get("data_plata") or ""
            events.append((_sk(d), d, "💰", "#4fc3f7",
                           f"Plată: {float(p.get('suma') or 0):.2f} RON",
                           f"{p.get('tip') or ''} • chitanță {p.get('chitanta_nr') or '—'}"))

        events.sort(key=lambda e: e[0], reverse=True)

        if not events:
            self.hist_browser.setHtml(
                "<p style='color:#888'>Niciun eveniment înregistrat pentru acest loc.</p>")
            return

        rows = []
        for _, d, icon, color, ttl, detail in events:
            rows.append(
                f"<div style='margin:0 0 10px 0; padding:8px 10px; border-left:3px solid {color}; background:#262626;'>"
                f"<div style='font-size:11px; color:#888;'>{d or 'dată necunoscută'}</div>"
                f"<div style='font-size:13px; font-weight:600;'>{icon} {ttl}</div>"
                f"<div style='font-size:12px; color:#bbb;'>{detail}</div>"
                f"</div>")
        self.hist_browser.setHtml("".join(rows))

    # -------------------------
    # Tab switching
    # -------------------------
    def _change_tab(self, index: int):
        self.stack.setCurrentIndex(index)

    # -------------------------
    # Update details (populate UI)
    # -------------------------
    def update_loc_details(self, cod_loc: str, data: Dict):
        """
        Populate the panel with data for cod_loc.
        `data` may contain fields from locuri_de_veci and extra_data.
        """
        self.current_cod_loc = cod_loc
        self.lbl_cod.setText(f"Cod loc: {cod_loc}")
        sector = data.get("sector", "")
        numar = data.get("numar", "")
        stare = data.get("stare", "")
        capacitate = data.get("capacitate", "")
        self.lbl_sub.setText(f"Stare: {stare} | Capacitate: {capacitate}")

        # overview
        self.ov_cod_loc.setText(cod_loc)
        self.ov_sector.setText(str(sector))
        self.ov_parcela.setText(str(data.get("parcela", "")))
        self.ov_rand.setText(str(data.get("rand", "")))
        self.ov_numar.setText(str(numar))
        self.ov_stare.setText(str(stare))
        self.ov_capacitate.setText(str(capacitate))
        self.ov_observatii.setPlainText(
            str(data.get("observatii", "")) if isinstance(data.get("observatii", ""), str)
            else str(data.get("extra", {}).get("observatii", ""))
        )

        # actualizează doc strip pentru overview (documente loc)
        try:
            self.doc_strip_overview.set_entity("loc", cod_loc, doc_type="poza_loc")
        except Exception:
            pass

        # juridic: load extra and then sync with contracts
        extra = data.get("extra", {}) if isinstance(data.get("extra", {}), dict) else {}
        conces = extra.get("concesionar") or data.get("concesionar") or ""
        self.ju_contract_nr.setText(str(extra.get("contract_nr", "")))
        self.ju_contract_data.setText(str(extra.get("contract_data", "")))
        self.ju_valabilitate.setText(str(extra.get("valabilitate", "")))

        # load concesionari and contracte for this cod_loc
        try:
            self._load_juridic_lists()
        except Exception:
            pass

        # if extra contains concesionar, try to set it in combo
        if conces:
            for label, _id in self._conces_map.items():
                if str(conces).strip() in label:
                    self.cmb_concesionar.setCurrentText(label)
                    break
            else:
                self.cmb_concesionar.setCurrentText(str(conces))

        # inhumati - construim carduri cu strip de documente per persoană
        try:
            inhumati = db.load_inhumati_for_loc(cod_loc) or []
        except Exception:
            inhumati = []
        self._populate_inhumati_cards(inhumati)

        # photos
        photos = []
        try:
            if self.w and hasattr(self.w, "actions") and callable(getattr(self.w.actions, "list_photos_for_loc", None)):
                photos = self.w.actions.list_photos_for_loc(cod_loc) or []
        except Exception:
            photos = []
        self.photo_gallery.update_photos(photos)

        # amenajari: încărcăm avizele REALE din tabela amenajari_avize
        try:
            avize_db = db.list_avize_for_loc(cod_loc) or []
        except Exception:
            avize_db = []
        self._populate_avize_table(avize_db)
        # reset doc strip aviz, apoi selectăm prima lucrare ca detaliile să fie vizibile
        try:
            self.doc_strip_aviz.set_entity("aviz", None)
            if self.tbl_avize.rowCount() > 0:
                self.tbl_avize.selectRow(0)
        except Exception:
            pass

        # financiar / istoric — încărcate din baza de date
        self._load_plati()
        try:
            self.doc_strip_plata.set_entity("plata", None)
        except Exception:
            pass
        self._load_istoric()

        # disable editing by default
        self._editing_enabled = False
        self._set_editable(False)

    # -------------------------
    # Inhumati cards (per person, cu strip documente)
    # -------------------------
    def _populate_inhumati_cards(self, inhumati: List[Dict]):
        # șterge cardurile vechi (păstrăm doar stretch-ul de la final)
        while self.inhumati_layout.count():
            it = self.inhumati_layout.takeAt(0)
            w = it.widget()
            if w:
                w.setParent(None)
        if not inhumati:
            empty = QLabel("Niciun înhumat pentru acest loc.")
            empty.setStyleSheet("color:#888; padding:12px; font-size:12px;")
            self.inhumati_layout.addWidget(empty)
        else:
            for inh in inhumati:
                self.inhumati_layout.addWidget(self._mk_inhumat_card(inh))
        self.inhumati_layout.addStretch()

    # -------------------------
    # Juridic helpers (sync with contracte & concesionari)
    # -------------------------
    def _load_juridic_lists(self):
        # load concesionari
        self.cmb_concesionar.blockSignals(True)
        self.cmb_concesionar.clear()
        self.cmb_concesionar.addItem("")
        self._conces_map.clear()
        try:
            if self.w and hasattr(self.w, "actions") and callable(getattr(self.w.actions, "load_concesionari", None)):
                cons = self.w.actions.load_concesionari() or []
            else:
                cons = []
            for c in cons:
                label = f"{c.get('nume','')} {c.get('prenume','')} ({c.get('id')})"
                self.cmb_concesionar.addItem(label)
                self._conces_map[label] = c.get("id")
        except Exception:
            pass
        self.cmb_concesionar.blockSignals(False)

        # load contracte for current cod_loc
        self.cmb_contracte.blockSignals(True)
        self.cmb_contracte.clear()
        self.cmb_contracte.addItem("")
        try:
            if self.current_cod_loc and self.w and hasattr(self.w, "actions") and callable(getattr(self.w.actions, "load_contracte_for_loc", None)):
                contracts = self.w.actions.load_contracte_for_loc(self.current_cod_loc) or []
            elif self.w and hasattr(self.w, "actions") and callable(getattr(self.w.actions, "load_contracte", None)):
                allc = self.w.actions.load_contracte() or []
                contracts = [c for c in allc if c.get("cod_loc") == self.current_cod_loc]
            else:
                contracts = []
            for c in contracts:
                label = f"{c.get('numar_contract','')} — {c.get('data_contract','')} (id:{c.get('id')})"
                self.cmb_contracte.addItem(label, c)
        except Exception:
            pass
        self.cmb_contracte.blockSignals(False)

        # if there is at least one contract, select the first (prefer active)
        if self.cmb_contracte.count() > 1:
            self.cmb_contracte.setCurrentIndex(1)

    def _on_contract_selected(self, index: int):
        data = None
        try:
            data = self.cmb_contracte.itemData(index)
        except Exception:
            data = None
        if not data or not isinstance(data, dict):
            self.ju_contract_nr.setText("")
            self.ju_contract_data.setText("")
            self.ju_valabilitate.setText("")
            try:
                self.doc_strip_contract.set_entity("contract", None)
            except Exception:
                pass
            return

        self.ju_contract_nr.setText(str(data.get("numar_contract", "")))
        self.ju_contract_data.setText(str(data.get("data_contract", "")))
        self.ju_valabilitate.setText(str(data.get("valabilitate", "") or data.get("data_sfarsit", "")))

        cid = data.get("concesionar_id")
        if cid:
            for label, _id in self._conces_map.items():
                if _id == cid:
                    self.cmb_concesionar.setCurrentText(label)
                    break

        # actualizează doc strip pentru contract
        try:
            self.doc_strip_contract.set_entity("contract", data.get("id"))
        except Exception:
            pass

    def _save_contract_from_juridic(self):
        if not self.current_cod_loc:
            show_message(self, "Eroare", "Selectați un loc înainte de a salva contractul.", level="warning")
            return

        conces_label = self.cmb_concesionar.currentText()
        conces_id = self._conces_map.get(conces_label)

        contract_data = {
            "numar_contract": self.ju_contract_nr.text().strip(),
            "data_contract": self.ju_contract_data.text().strip(),
            "data_inceput": self.ju_contract_data.text().strip(),
            "data_sfarsit": self.ju_valabilitate.text().strip(),
            "cod_loc": self.current_cod_loc,
            "concesionar_id": conces_id,
            "observatii": ""
        }

        idx = self.cmb_contracte.currentIndex()
        selected = self.cmb_contracte.itemData(idx) if idx >= 0 else None
        contract_id = selected.get("id") if isinstance(selected, dict) else None

        try:
            if self.w and hasattr(self.w, "actions") and callable(getattr(self.w.actions, "save_contract", None)):
                saved = self.w.actions.save_contract(contract_data, contract_id=contract_id)
                if saved:
                    show_message(self, "OK", "Contract salvat.")
                    # reload contracts and select saved one
                    self._load_juridic_lists()
                    for i in range(self.cmb_contracte.count()):
                        d = self.cmb_contracte.itemData(i)
                        if isinstance(d, dict) and d.get("id") == saved:
                            self.cmb_contracte.setCurrentIndex(i)
                            break
                    # actualizează doc strip pentru contract
                    try:
                        self.doc_strip_contract.set_entity("contract", saved)
                    except Exception:
                        pass
                else:
                    show_message(self, "Eroare", "Nu am putut salva contractul.", level="warning")
            else:
                show_message(self, "Eroare", "Funcția de salvare contract nu este disponibilă.", level="warning")
        except Exception as e:
            show_message(self, "Eroare", f"Nu am putut salva contractul: {e}", level="error")

    # -------------------------
    # Editing helpers
    # -------------------------
    def _toggle_editing(self):
        self._editing_enabled = not self._editing_enabled
        self._set_editable(self._editing_enabled)
        if self._editing_enabled:
            show_message(self, "Editare", "Puteți edita câmpurile acum. Apăsați Salvează când terminați.")
        else:
            show_message(self, "Editare", "Editare dezactivată.")

    def _set_editable(self, enabled: bool):
        # Overview fields
        for w in (self.ov_observatii,):
            w.setReadOnly(not enabled)
        # juridic fields
        for w in (self.ju_contract_nr, self.ju_contract_data, self.ju_valabilitate):
            w.setReadOnly(not enabled)
        # concesionar combo: enable only in edit mode
        self.cmb_concesionar.setEnabled(enabled)
        # Avize: allow adding always, deletion only in edit mode
        self.btn_add_aviz.setEnabled(True)
        self.btn_remove_aviz.setEnabled(enabled and self.tbl_avize.rowCount() > 0)
        # Plăți: adăugare mereu permisă, ștergere doar în mod editare
        try:
            self.btn_add_plata.setEnabled(True)
            self.btn_remove_plata.setEnabled(enabled and self.tbl_plati.rowCount() > 0)
        except Exception:
            pass
        # contract save button only in edit mode
        try:
            self.btn_save_contract.setEnabled(enabled)
        except Exception:
            pass

    # -------------------------
    # Save all (overview + amenajari)
    # -------------------------
    def _save_all(self):
        if not self.current_cod_loc:
            show_message(self, "Eroare", "Niciun loc selectat.", level="warning")
            return

        # avizele sunt persistate direct în DB (tabela amenajari_avize)
        # aici salvăm doar câmpurile din Overview/Juridic

        extra = {
            "observatii": self.ov_observatii.toPlainText(),
            "concesionar": self.cmb_concesionar.currentText(),
            "contract_nr": self.ju_contract_nr.text(),
            "contract_data": self.ju_contract_data.text(),
            "valabilitate": self.ju_valabilitate.text(),
        }

        try:
            if self.w and hasattr(self.w, "actions") and callable(getattr(self.w.actions, "save_extra_data", None)):
                self.w.actions.save_extra_data(self.current_cod_loc, extra)
                # persistăm și „observatii" în coloana proprie a tabelei locuri_de_veci
                try:
                    db.save_loc_metadata(self.current_cod_loc, {"observatii": extra["observatii"]})
                except Exception:
                    pass
                show_message(self, "Salvat", "Datele au fost salvate.")
                self._editing_enabled = False
                self._set_editable(False)
                try:
                    self._load_juridic_lists()
                except Exception:
                    pass
            else:
                show_message(self, "Eroare", "Funcția de salvare nu este disponibilă.", level="warning")
        except Exception as e:
            show_message(self, "Eroare", f"Nu am putut salva: {e}", level="error")

    # -------------------------
    # Amenajari / Avize
    # -------------------------
    def _populate_avize_table(self, avize: List[Dict]):
        self.tbl_avize.setRowCount(0)
        for a in avize:
            r = self.tbl_avize.rowCount()
            self.tbl_avize.insertRow(r)
            # suportăm ambele scheme (DB: nr_aviz/data_aviz vs. legacy: nr/data)
            nr_val = a.get("nr_aviz") or a.get("nr", "")
            data_val = a.get("data_aviz") or a.get("data", "")
            lucrare = a.get("lucrare", "")
            aid = a.get("id")
            nr_item = QTableWidgetItem(str(nr_val))
            nr_item.setData(Qt.UserRole, aid)
            self.tbl_avize.setItem(r, 0, nr_item)
            self.tbl_avize.setItem(r, 1, QTableWidgetItem(str(data_val)))
            self.tbl_avize.setItem(r, 2, QTableWidgetItem(str(lucrare)))
            # coloana Fotografie: dacă există documente pe aviz, arată nr; păstrăm compat legacy
            photo_ref = ""
            try:
                if aid is not None:
                    docs = db.list_documents("aviz", aid) or []
                    if docs:
                        photo_ref = f"{len(docs)} doc(s)"
            except Exception:
                pass
            if not photo_ref and a.get("photo"):
                photo_ref = os.path.basename(a.get("photo"))
            photo_item = QTableWidgetItem(photo_ref)
            photo_item.setData(Qt.UserRole, a.get("photo"))
            self.tbl_avize.setItem(r, 3, photo_item)
            self.tbl_avize.setItem(r, 4, QTableWidgetItem(wd.stare_label(a)))
            self.tbl_avize.setItem(r, 5, QTableWidgetItem(wd.finalizare_label(a)))
            self.tbl_avize.setItem(r, 6, QTableWidgetItem(str(a.get("executant") or "")))
            self.tbl_avize.setItem(r, 7, QTableWidgetItem(str(a.get("data_inceput") or "")))
        # update remove button state
        self.btn_remove_aviz.setEnabled(self._editing_enabled and self.tbl_avize.rowCount() > 0)

    def _add_aviz(self):
        if not self.current_cod_loc:
            show_message(self, "Eroare", "Selectați un loc înainte de a adăuga un aviz.", level="warning")
            return
        dlg = AddAvizDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        res = dlg.get_result()
        if not res:
            return
        # persistăm în DB
        try:
            aid = db.add_aviz(self.current_cod_loc, res.get("nr", ""),
                              res.get("data", ""), res.get("lucrare", ""),
                              res.get("executant", ""), res.get("data_inceput", ""))
        except Exception as e:
            show_message(self, "Eroare", f"Nu am putut salva avizul: {e}", level="error")
            return
        if not aid:
            show_message(self, "Eroare", "Salvarea avizului a eșuat.", level="warning")
            return
        # dacă utilizatorul a atașat o fotografie a lucrării, o salvăm ca document
        photo = res.get("photo")
        if photo:
            try:
                db.add_document("aviz", aid, "foto_lucrare", photo,
                                caption=res.get("lucrare", ""))
            except Exception as e:
                show_message(self, "Atenție",
                             f"Avizul s-a salvat, dar nu am putut salva fotografia: {e}",
                             level="warning")
        # reîncarcă tabelul din DB
        try:
            avize = db.list_avize_for_loc(self.current_cod_loc) or []
        except Exception:
            avize = []
        self._populate_avize_table(avize)
        self._load_istoric()

    def _remove_selected_aviz(self):
        row = self.tbl_avize.currentRow()
        if row < 0:
            show_message(self, "Info", "Selectați un aviz pentru ștergere.")
            return
        nr_item = self.tbl_avize.item(row, 0)
        aid = nr_item.data(Qt.UserRole) if nr_item else None
        ok = ask_confirmation(self, "Șterge aviz", "Sigur doriți să ștergeți avizul selectat?", default_yes=False)
        if not ok:
            return
        if aid is not None:
            try:
                db.delete_aviz(int(aid))
            except Exception as e:
                show_message(self, "Eroare", f"Ștergere eșuată: {e}", level="error")
                return
        # reîncarcă din DB
        try:
            avize = db.list_avize_for_loc(self.current_cod_loc) or []
        except Exception:
            avize = []
        self._populate_avize_table(avize)
        self.doc_strip_aviz.set_entity("aviz", None)
        self._load_istoric()
        # update remove button state
        self.btn_remove_aviz.setEnabled(self._editing_enabled and self.tbl_avize.rowCount() > 0)

    # -------------------------
    # Photo actions
    # -------------------------
    def _upload_photo(self):
        if not self.current_cod_loc:
            show_message(self, "Eroare", "Selectați un loc înainte de a încărca fotografie.", level="warning")
            return
        file, _ = QFileDialog.getOpenFileName(self, "Alege fotografie", "", "Imagini (*.png *.jpg *.jpeg)")
        if not file:
            return
        try:
            if self.w and hasattr(self.w, "actions") and callable(getattr(self.w.actions, "save_photo", None)):
                saved = self.w.actions.save_photo(self.current_cod_loc, file, metadata={})
                if saved:
                    photos = self.w.actions.list_photos_for_loc(self.current_cod_loc) or []
                    self.photo_gallery.update_photos(photos)
                    show_message(self, "OK", "Fotografie încărcată.")
                else:
                    show_message(self, "Eroare", "Încărcarea a eșuat.", level="warning")
            else:
                # fallback: save to extra_data
                extra = {}
                try:
                    if self.w and hasattr(self.w, "actions") and callable(getattr(self.w.actions, "load_extra_data", None)):
                        extra = self.w.actions.load_extra_data(self.current_cod_loc) or {}
                except Exception:
                    extra = {}
                extra_photos = extra.get("photos", [])
                extra_photos.append({
                    "id": f"local-{len(extra_photos)+1}",
                    "full_path": file,
                    "caption": "",
                    "uploader": "local",
                    "date_added": datetime.now().strftime("%d.%m.%Y")
                })
                extra["photos"] = extra_photos
                if self.w and hasattr(self.w, "actions") and callable(getattr(self.w.actions, "save_extra_data", None)):
                    self.w.actions.save_extra_data(self.current_cod_loc, extra)
                    self.photo_gallery.update_photos(extra_photos)
                    show_message(self, "OK", "Fotografie salvată local (extra_data).")
                else:
                    show_message(self, "Eroare", "Nu există backend pentru salvare.", level="warning")
        except Exception as e:
            show_message(self, "Eroare", f"Eroare la upload: {e}", level="error")

    def _delete_selected_photo(self):
        photos = self.photo_gallery.photos
        if not photos:
            show_message(self, "Info", "Nu există fotografii de șters.")
            return
        # default: delete last photo (UI can be extended to select)
        p = photos[-1]
        try:
            if self.w and hasattr(self.w, "actions") and callable(getattr(self.w.actions, "delete_photo", None)):
                ok = self.w.actions.delete_photo(p.get("id"))
                if ok:
                    photos = self.w.actions.list_photos_for_loc(self.current_cod_loc) or []
                    self.photo_gallery.update_photos(photos)
                    show_message(self, "OK", "Fotografia a fost ștearsă.")
                else:
                    show_message(self, "Eroare", "Ștergerea a eșuat.", level="warning")
            else:
                show_message(self, "Eroare", "Funcția de ștergere nu este disponibilă.", level="warning")
        except Exception as e:
            show_message(self, "Eroare", f"Eroare la ștergere: {e}", level="error")

    def _set_cover_photo(self):
        photos = self.photo_gallery.photos
        if not photos:
            show_message(self, "Info", "Nu există fotografii.")
            return
        p = photos[0]
        try:
            if self.w and hasattr(self.w, "actions") and callable(getattr(self.w.actions, "set_cover_photo", None)):
                ok = self.w.actions.set_cover_photo(self.current_cod_loc, p.get("id"))
                if ok:
                    show_message(self, "OK", "Fotografia setată ca cover.")
                else:
                    show_message(self, "Eroare", "Setare cover eșuată.", level="warning")
            else:
                show_message(self, "Info", "Funcția set_cover_photo nu este disponibilă.")
        except Exception as e:
            show_message(self, "Eroare", f"Eroare la setare cover: {e}", level="error")


def create_right_panel(main_window):
    return RightPanel(main_window)
