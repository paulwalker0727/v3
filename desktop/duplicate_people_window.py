# duplicate_people_window.py — Etapa 4: fereastră PySide6 pentru persoane duplicate
# Reutilizează verificările CON004-CON007 (concesionari) și INH008/INH009 (înhumați) din
# shared/cimitir_integrity/. Doar prezentare + examinare manuală; FĂRĂ ștergere sau fuziune automată.
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QTreeWidget, QTreeWidgetItem,
    QMessageBox, QHeaderView
)
from PySide6.QtGui import QColor
from PySide6.QtCore import QThread, Signal

import database as db

SHARED_DIR = Path(__file__).resolve().parent.parent / "shared"
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))
import cimitir_integrity as ci  # noqa: E402

# cod -> (categorie, etichetă de încredere afișată utilizatorului)
_DUP_CODES = {
    "CON004": ("concesionari", "Sigur — CNP identic, nume identic"),
    "CON007": ("concesionari", "Verificați — CNP identic, nume diferit"),
    "CON006": ("concesionari", "Probabil — nume și date de contact identice"),
    "CON005": ("concesionari", "Incert — doar numele este identic"),
    "INH008": ("inhumati", "Sigur — CNP identic"),
    "INH009": ("inhumati", "Probabil — nume și dată a decesului identice"),
}
# prioritate la afișare dacă același grup e semnalat de mai multe coduri (cel mai „tare” câștigă)
_RANK = {"CON004": 0, "INH008": 0, "CON007": 1, "INH009": 1, "CON006": 2, "CON005": 3}
_CONFIDENCE_COLOR = {"Sigur": "#b00020", "Verificați": "#b00020", "Probabil": "#9a7d0a", "Incert": "#555555"}


class DuplicateScanWorker(QThread):
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, db_path: str):
        super().__init__()
        self.db_path = db_path

    def run(self):
        try:
            report = ci.run_on_file(self.db_path, categories=["concesionari", "inhumati"], include_info=True)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"Verificarea a eșuat: {e}")
            return
        self.finished_ok.emit(report)


class DuplicatePeopleWindow(QDialog):
    """Etapa 4 — persoane duplicate: afișează grupuri suspecte (concesionari/înhumați), cu contractele
    și locurile asociate, pentru examinare manuală. Nu șterge și nu fuzionează nimic automat."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Persoane duplicate (concesionari / înhumați)")
        self.resize(1000, 660)
        self._worker = None

        layout = QVBoxLayout()
        info = QLabel("Identifică posibile persoane duplicate (CNP identic sau nume/contact asemănător, cu marcarea "
                      "rezultatelor incerte). Nu se șterge și nu se fuzionează nimic automat — fiecare caz se "
                      "examinează manual.")
        info.setWordWrap(True)
        info.setStyleSheet("color:#555;")
        layout.addWidget(info)

        top = QHBoxLayout()
        self.btn_run = QPushButton("Caută persoane duplicate")
        self.btn_run.setObjectName("btn_duplicate_scan")
        top.addWidget(self.btn_run)
        top.addStretch()
        layout.addLayout(top)

        self.lbl_status = QLabel("Apăsați „Caută persoane duplicate” pentru a începe.")
        self.lbl_status.setObjectName("lbl_duplicate_status")
        self.lbl_status.setWordWrap(True)
        layout.addWidget(self.lbl_status)

        self.tree = QTreeWidget()
        self.tree.setObjectName("tree_duplicate_people")
        self.tree.setColumnCount(6)
        self.tree.setHeaderLabels(["Grup / Persoană", "Încredere", "Nume", "Contact", "Contracte / Locuri", "Observații"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(4, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(5, QHeaderView.Stretch)
        layout.addWidget(self.tree, 1)

        close = QPushButton("Închide")
        close.setObjectName("btn_duplicate_close")
        close.clicked.connect(self.reject)
        layout.addWidget(close)

        self.setLayout(layout)
        self.btn_run.clicked.connect(self._start_scan)

    # ------------------------------------------------------------------
    def _start_scan(self):
        db_path = db.current_db_path()
        if not db_path:
            QMessageBox.warning(self, "Persoane duplicate", "Nu am găsit baza de date activă.")
            return
        self.tree.clear()
        self.lbl_status.setText("Se caută...")
        self.btn_run.setEnabled(False)
        self._worker = DuplicateScanWorker(db_path)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(lambda: self.btn_run.setEnabled(True))
        self._worker.start()

    def _on_failed(self, message: str):
        self.lbl_status.setText(message)

    def _on_finished(self, report):
        d = report.to_dict()
        groups = {}  # (categorie, tuple(id-uri)) -> (cod, etichetă)
        for issue in d["issues"]:
            code = issue["code"]
            if code not in _DUP_CODES:
                continue
            ids = tuple(issue["evidence"].get("id_uri_grup") or [])
            if not ids:
                continue
            key = (_DUP_CODES[code][0], ids)
            if key not in groups or _RANK[code] < _RANK[groups[key][0]]:
                groups[key] = (code, _DUP_CODES[code][1])
        self.tree.clear()
        if not groups:
            self.lbl_status.setText("Nicio persoană suspectată de duplicare.")
            return
        self.lbl_status.setText(f"{len(groups)} grup(uri) de posibile duplicate găsite. Examinați manual fiecare caz "
                                f"— nimic nu a fost șters sau fuzionat automat.")
        for (cat, ids), (code, label) in sorted(groups.items(), key=lambda kv: (_RANK[kv[1][0]], kv[0])):
            confidence, detail = label.split(" — ", 1)
            cat_ro = "Concesionari" if cat == "concesionari" else "Înhumați"
            group_item = QTreeWidgetItem([f"{cat_ro} — grup de {len(ids)} ({code})", confidence, "", "", "", detail])
            color = _CONFIDENCE_COLOR.get(confidence)
            if color:
                group_item.setForeground(1, QColor(color))
            self.tree.addTopLevelItem(group_item)
            for pid in ids:
                group_item.addChild(self._person_row(cat, pid))
            group_item.setExpanded(True)

    # ------------------------------------------------------------------
    def _person_row(self, category: str, person_id: str) -> QTreeWidgetItem:
        conn = db.get_connection()
        try:
            if category == "concesionari":
                r = conn.execute("SELECT * FROM concesionari WHERE id = ?", (person_id,)).fetchone()
                if not r:
                    return QTreeWidgetItem([f"#{person_id}", "", "", "", "", "Înregistrare negăsită (posibil ștearsă)."])
                nume = f"{r['nume'] or ''} {r['prenume'] or ''}".strip()
                contact = " / ".join(v for v in (r["telefon"], r["email"], r["adresa"]) if v) or "—"
                contracts = conn.execute("SELECT numar_contract AS nr, cod_loc FROM contracte WHERE concesionar_id = ?",
                                         (person_id,)).fetchall()
                links = ", ".join(f"contract {c['nr'] or '?'} (loc {c['cod_loc'] or '?'})" for c in contracts) or "fără contracte"
                return QTreeWidgetItem([f"#{person_id}", "", nume, contact, links, ""])
            r = conn.execute("SELECT * FROM inhumati WHERE id = ?", (person_id,)).fetchone()
            if not r:
                return QTreeWidgetItem([f"#{person_id}", "", "", "", "", "Înregistrare negăsită (posibil ștearsă)."])
            nume = f"{r['nume'] or ''} {r['prenume'] or ''}".strip()
            loc = conn.execute("SELECT cod_loc FROM locuri_de_veci WHERE id = ?", (r["id_loc"],)).fetchone() if r["id_loc"] else None
            return QTreeWidgetItem([f"#{person_id}", "", nume, f"data deces: {r['data_deces'] or '—'}",
                                    f"loc {loc['cod_loc'] if loc else '?'}", ""])
        except Exception as e:  # noqa: BLE001
            return QTreeWidgetItem([f"#{person_id}", "", "", "", "", f"Eroare la citire: {e}"])
        finally:
            conn.close()
