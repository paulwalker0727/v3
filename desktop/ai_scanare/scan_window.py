"""Fereastră principală „AI Scanare" — accesibilă din toolbar-ul aplicației.

Include:
  - 2 card-uri mari pentru alegere: Buletin CI / Certificat Deces
  - Buton scanare pentru fiecare (deschide ScanDialog)
  - Tabel istoric scanări cu filtre + status + acțiuni
  - După confirmare scanare → salvează în DB (concesionar sau inhumat)
    + salvează fișierul conform preferinței (attach / archive / delete)
"""
from __future__ import annotations

import csv
import hashlib
import os
import time
from datetime import datetime
from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import database as db

from .ai_client import DocumentType
from .batch_dialog import BatchScanDialog
from .db_scans import (
    delete_scan,
    get_scan,
    init_scan_history,
    list_scans,
    monthly_scan_stats,
    save_scan,
    scan_stats,
    update_scan_status,
)
from .duplicate_check import (
    find_concesionar_by_cnp,
    find_inhumat_by_cnp,
    update_concesionar,
    update_inhumat,
)
from .mobile_scan import MobileScanDialog
from .scan_dialog import ScanDialog


# ============ ETICHETE TIP DOCUMENT ============

DOC_LABELS: Dict[str, str] = {
    DocumentType.BULETIN: "Buletin CI",
    DocumentType.CERTIFICAT_DECES: "Cert. deces",
    DocumentType.ADEVERINTA_ADRESA: "Adeverință adresă",
}

FIELD_LABELS: Dict[str, str] = {
    "nume": "Nume", "prenume": "Prenume", "cnp": "CNP",
    "serie": "Serie", "numar": "Număr", "data_nasterii": "Data nașterii",
    "sex": "Sex", "nationalitate": "Naționalitate", "adresa": "Adresă",
    "emitent": "Emitent", "data_eliberarii": "Data eliberării",
    "valabilitate": "Valabilitate", "data_deces": "Data decesului",
    "cauza_deces": "Cauza decesului", "locul_decesului": "Locul decesului",
    "ultima_adresa": "Ultima adresă", "nr_certificat_deces": "Nr. certificat deces",
    "nr_adeverinta_inhumare": "Nr. adeverință înhumare",
    "data_eliberare": "Data eliberării", "localitate": "Localitate",
    "judet": "Județ", "nr_adeverinta": "Nr. adeverință",
}


def doc_label(doc_type: Optional[str]) -> str:
    """Eticheta prietenoasă pentru un tip de document (fără fallback greșit)."""
    return DOC_LABELS.get(doc_type or "", doc_type or "—")


def _plain_fields(extracted: Dict[str, Any]) -> Dict[str, str]:
    """Aplatizează {"nume": {"value": x}} → {"nume": x}."""
    fields = (extracted or {}).get("fields", extracted) or {}
    out: Dict[str, str] = {}
    for k, v in fields.items():
        if isinstance(v, dict):
            out[k] = str(v.get("value", "") or "")
        elif not isinstance(v, (list, dict)):
            out[k] = str(v or "")
    return out


# ============ VERIFICARE CHEIE AI (silențioasă, în fundal) ============

_KEY_HEALTH_TTL = 30 * 60  # nu re-verificăm cheia mai des de 30 minute
_key_health_cache: Dict[str, tuple] = {}  # hash cheie -> (ok, mesaj, timestamp)


def _key_fingerprint(key: str) -> str:
    return hashlib.sha256((key or "").encode("utf-8")).hexdigest()[:16]


def cached_key_health(key: str) -> Optional[tuple]:
    """(ok, mesaj) dacă avem o verificare recentă pentru cheia asta, altfel None."""
    entry = _key_health_cache.get(_key_fingerprint(key))
    if not entry:
        return None
    ok, msg, ts = entry
    if time.time() - ts > _KEY_HEALTH_TTL:
        return None
    return ok, msg


def remember_key_health(key: str, ok: bool, msg: str) -> None:
    _key_health_cache[_key_fingerprint(key)] = (ok, msg, time.time())


def forget_key_health() -> None:
    """Invalidează cache-ul (după salvarea unei chei noi din Setări AI)."""
    _key_health_cache.clear()


class _KeyHealthWorker(QObject):
    """Verifică în fundal dacă cheia Gemini mai e valabilă."""

    done = Signal(bool, str)

    def __init__(self, api_key: str):
        super().__init__()
        self.api_key = api_key

    def run(self):
        try:
            from . import google_engine
            res = google_engine.test_key(self.api_key)
            self.done.emit(bool(res.get("ok")), str(res.get("message", "")))
        except Exception as exc:  # noqa: BLE001
            self.done.emit(False, f"Verificare eșuată: {exc}")


# ============ DIALOG DETALII SCANARE ============

class ScanDetailsDialog(QDialog):
    """Afișează toate câmpurile extrase pentru o scanare din istoric."""

    def __init__(self, parent, scan: Dict[str, Any]):
        super().__init__(parent)
        self.setWindowTitle("Detalii scanare")
        self.setMinimumSize(680, 560)
        self.setStyleSheet("QDialog { background:#020617; color:#e2e8f0; }")

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        title = QLabel(doc_label(scan.get("document_type")))
        title.setStyleSheet("color:#f1f5f9; font-size:16pt; font-weight:800;")
        root.addWidget(title)

        extracted = scan.get("extracted") or {}
        fields = extracted.get("fields", extracted) or {}

        meta_bits = [
            f"Data: {scan.get('created_at', '—')}",
            f"Fișier: {scan.get('filename') or '—'}",
            f"Status: {scan.get('status', '—')}",
            f"Motor: {extracted.get('engine') or '—'}",
        ]
        if scan.get("entity_type"):
            meta_bits.append(f"Entitate: {scan['entity_type']} #{scan.get('entity_id', '')}")
        meta = QLabel("  ·  ".join(meta_bits))
        meta.setWordWrap(True)
        meta.setStyleSheet("color:#94a3b8; font-size:9pt;")
        root.addWidget(meta)

        self.table = QTableWidget(0, 3)
        self.table.setObjectName("scan-details-table")
        self.table.setHorizontalHeaderLabels(["Câmp", "Valoare extrasă", "Încredere"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setStyleSheet(
            "QTableWidget { background:#0f172a; color:#e2e8f0; border:1px solid #1e293b; }"
            "QHeaderView::section { background:#1e293b; color:#94a3b8; padding:6px; border:none; }"
        )
        for key, raw in fields.items():
            if key.startswith("_"):
                continue
            if isinstance(raw, dict):
                value = str(raw.get("value", "") or "")
                conf = raw.get("confidence")
            else:
                value = str(raw or "")
                conf = None
            idx = self.table.rowCount()
            self.table.insertRow(idx)
            self.table.setItem(idx, 0, QTableWidgetItem(FIELD_LABELS.get(key, key)))
            self.table.setItem(idx, 1, QTableWidgetItem(value or "—"))
            if conf is None:
                conf_item = QTableWidgetItem("—")
            else:
                conf_item = QTableWidgetItem(f"{float(conf) * 100:.0f}%")
                if float(conf) < 0.6:
                    conf_item.setForeground(Qt.red)
            self.table.setItem(idx, 2, conf_item)

        if self.table.rowCount() == 0:
            empty = QLabel("Nu există câmpuri salvate pentru această scanare.")
            empty.setStyleSheet("color:#f59e0b;")
            root.addWidget(empty)
        root.addWidget(self.table, 1)

        raw_text = (extracted.get("raw_text") or extracted.get("raw") or "").strip()
        if raw_text:
            lbl = QLabel("Text brut recunoscut (OCR/AI):")
            lbl.setStyleSheet("color:#cbd5e1; font-weight:600;")
            root.addWidget(lbl)
            box = QPlainTextEdit(raw_text)
            box.setObjectName("scan-details-raw-text")
            box.setReadOnly(True)
            box.setMaximumHeight(140)
            box.setStyleSheet(
                "QPlainTextEdit { background:#0f172a; color:#94a3b8; "
                "border:1px solid #1e293b; font-family:Consolas,monospace; font-size:9pt; }"
            )
            root.addWidget(box)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.setObjectName("scan-details-close")
        buttons.button(QDialogButtonBox.Close).setText("Închide")
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)


# ============ STATISTICI SCANĂRI ============

STAT_COLORS = {"ai": "#22c55e", "offline": "#f59e0b", "unknown": "#475569"}


class MonthlyScanChart(QWidget):
    """Grafic cu bare pe ultimele luni: AI (verde), offline (galben), necunoscut (gri)."""

    def __init__(self, months: list, parent=None):
        super().__init__(parent)
        self.setObjectName("monthly-scan-chart")
        self.months = months
        self.setMinimumHeight(220)

    def paintEvent(self, event):  # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0f172a"))

        if not self.months:
            return

        pad_left, pad_right, pad_top, pad_bottom = 34, 12, 14, 26
        width = max(1, self.width() - pad_left - pad_right)
        height = max(1, self.height() - pad_top - pad_bottom)
        peak = max([m["total"] for m in self.months] + [1])

        ticks = min(4, peak)
        for i in range(ticks + 1):
            value = round(peak * i / ticks)
            y = pad_top + height - int(height * value / peak)
            painter.setPen(QColor("#1e293b"))
            painter.drawLine(pad_left, y, pad_left + width, y)
            painter.setPen(QColor("#64748b"))
            painter.drawText(2, y + 4, str(value))

        slot = width / len(self.months)
        bar_w = max(6, int(slot * 0.56))
        for idx, month in enumerate(self.months):
            x = int(pad_left + slot * idx + (slot - bar_w) / 2)
            y_bottom = pad_top + height
            for key in ("ai", "offline", "unknown"):
                count = month.get(key, 0)
                if not count:
                    continue
                bar_h = max(2, int(height * count / peak))
                painter.setBrush(QColor(STAT_COLORS[key]))
                painter.setPen(Qt.NoPen)
                painter.drawRect(x, y_bottom - bar_h, bar_w, bar_h)
                y_bottom -= bar_h
            painter.setPen(QColor("#94a3b8"))
            painter.drawText(x - 6, pad_top + height + 16, month["label"])
            if month["total"]:
                painter.setPen(QColor("#e2e8f0"))
                painter.drawText(x - 2, y_bottom - 4, str(month["total"]))
        painter.end()


class ScanStatsDialog(QDialog):
    """Fereastră cu statistici pe ultimele 12 luni."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📊  Statistici scanări")
        self.setMinimumSize(760, 560)
        self.setStyleSheet("QDialog { background:#020617; color:#e2e8f0; }")

        stats = scan_stats()
        months = monthly_scan_stats(12)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        title = QLabel("Statistici scanări")
        title.setStyleSheet("color:#f1f5f9; font-size:18pt; font-weight:800;")
        root.addWidget(title)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(10)
        for widget in stat_cards(stats):
            cards_row.addWidget(widget)
        root.addLayout(cards_row)

        chart_lbl = QLabel("Scanări pe lună (ultimele 12 luni)")
        chart_lbl.setStyleSheet("color:#cbd5e1; font-weight:600;")
        root.addWidget(chart_lbl)
        root.addWidget(MonthlyScanChart(months, self), 1)

        legend = QLabel(
            f"<span style='color:{STAT_COLORS['ai']}'>■</span> AI (Gemini) &nbsp;&nbsp;"
            f"<span style='color:{STAT_COLORS['offline']}'>■</span> OCR offline &nbsp;&nbsp;"
            f"<span style='color:{STAT_COLORS['unknown']}'>■</span> necunoscut "
            "(scanări făcute înainte de a înregistra motorul)"
        )
        legend.setStyleSheet("color:#94a3b8; font-size:9pt;")
        root.addWidget(legend)

        self.table = QTableWidget(0, 5)
        self.table.setObjectName("monthly-stats-table")
        self.table.setHorizontalHeaderLabels(["Luna", "Total", "AI", "Offline", "Necunoscut"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMaximumHeight(180)
        self.table.setStyleSheet(
            "QTableWidget { background:#0f172a; color:#e2e8f0; border:1px solid #1e293b; }"
            "QHeaderView::section { background:#1e293b; color:#94a3b8; padding:6px; border:none; }"
        )
        for month in reversed(months):
            idx = self.table.rowCount()
            self.table.insertRow(idx)
            self.table.setItem(idx, 0, QTableWidgetItem(month["label"]))
            for col, key in enumerate(("total", "ai", "offline", "unknown"), start=1):
                self.table.setItem(idx, col, QTableWidgetItem(str(month.get(key, 0))))
        root.addWidget(self.table)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.setObjectName("scan-stats-close")
        buttons.button(QDialogButtonBox.Close).setText("Închide")
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)


def stat_cards(stats: Dict[str, Any]) -> list:
    """Cele 4 cifre: total, luna curentă, % AI, % offline (listă de widget-uri)."""
    def card(value: str, caption: str, color: str, testid: str) -> QWidget:
        box = QFrame()
        box.setObjectName(testid)
        box.setStyleSheet(
            "QFrame { background:#0f172a; border:1px solid #1e293b; border-radius:10px; }"
        )
        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        big = QLabel(value)
        big.setStyleSheet(f"color:{color}; font-size:17pt; font-weight:800; "
                          "border:none; background:transparent;")
        small = QLabel(caption)
        small.setStyleSheet("color:#94a3b8; font-size:9pt; border:none; background:transparent;")
        lay.addWidget(big)
        lay.addWidget(small)
        return box

    ai_pct = stats.get("ai_pct")
    off_pct = stats.get("offline_pct")
    return [
        card(str(stats.get("total", 0)), "documente scanate", "#f1f5f9", "stat-total"),
        card(str(stats.get("this_month", 0)), "luna aceasta", "#0ea5e9", "stat-this-month"),
        card("—" if ai_pct is None else f"{ai_pct:g}%",
             f"reușite cu AI ({stats.get('ai', 0)})", STAT_COLORS["ai"], "stat-ai-pct"),
        card("—" if off_pct is None else f"{off_pct:g}%",
             f"OCR offline ({stats.get('offline', 0)})", STAT_COLORS["offline"],
             "stat-offline-pct"),
    ]


# ============ CARD ============

class ScanCard(QFrame):
    """Card interactiv pentru inițierea unei scanări."""

    def __init__(self, icon: str, title: str, subtitle: str, badges: list[str], parent=None):
        super().__init__(parent)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setStyleSheet(
            """
            ScanCard {
                background: #0f172a;
                border: 1px solid #1e293b;
                border-radius: 10px;
                padding: 4px;
            }
            ScanCard:hover {
                border-color: #0ea5e9;
                background: #0c1a2d;
            }
            """
        )

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(8)

        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet("font-size:32pt;")
        v.addWidget(icon_lbl)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("color:#f1f5f9; font-size:14pt; font-weight:700;")
        v.addWidget(title_lbl)

        sub_lbl = QLabel(subtitle)
        sub_lbl.setStyleSheet("color:#94a3b8; font-size:9pt;")
        sub_lbl.setWordWrap(True)
        v.addWidget(sub_lbl)

        v.addSpacing(8)
        badges_row = QHBoxLayout()
        badges_row.setSpacing(4)
        for b in badges:
            lb = QLabel(b)
            lb.setStyleSheet(
                "QLabel { background:#1e293b; color:#cbd5e1; padding:3px 8px; "
                "border-radius:4px; font-size:8pt; }"
            )
            badges_row.addWidget(lb)
        badges_row.addStretch()
        v.addLayout(badges_row)

        v.addStretch()

        self.btn = QPushButton(f"🔍  Începe scanare")
        self.btn.setStyleSheet(
            "QPushButton { background:#0ea5e9; color:white; padding:10px; "
            "border:none; border-radius:6px; font-weight:700; }"
            "QPushButton:hover { background:#0284c7; }"
        )
        v.addWidget(self.btn)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.btn.click()
        super().mousePressEvent(event)


# ============ HELPERS SAVE ============

def _save_concesionar_from_scan(actions, data: dict) -> Optional[int]:
    """Din datele extrase din buletin, creează un concesionar."""
    # Construim BI/CI din serie + număr
    bi_ci = ""
    if data.get("serie") or data.get("numar"):
        bi_ci = f"{data.get('serie', '')} {data.get('numar', '')}".strip()

    payload = {
        "nume": data.get("nume", ""),
        "prenume": data.get("prenume", ""),
        "cnp": data.get("cnp", ""),
        "bi_ci": bi_ci,
        "adresa": data.get("adresa", ""),
        "telefon": "",
        "email": "",
        "observatii": (
            f"Emitent: {data.get('emitent', '')} · "
            f"Data eliberării: {data.get('data_eliberarii', '')} · "
            f"Valabilitate: {data.get('valabilitate', '')} · "
            f"Data nașterii: {data.get('data_nasterii', '')}"
        ),
    }
    try:
        if actions and hasattr(actions, "save_concesionar"):
            return actions.save_concesionar(payload)
        return db.save_concesionar(payload)
    except Exception as e:
        print(f"[save_concesionar_from_scan] {e}")
        return None


def _save_inhumat_from_scan(actions, cod_loc: str, data: dict) -> Optional[int]:
    """Din datele extrase din certificat, creează un inhumat."""
    payload = {
        "nume": data.get("nume", ""),
        "prenume": data.get("prenume", ""),
        "cnp": data.get("cnp", ""),
        "data_nasterii": data.get("data_nasterii", ""),
        "data_deces": data.get("data_deces", ""),
        "data_inhumare": "",
        "data_deshumare": "",
        "sex": data.get("sex", ""),
        "cauza_deces": data.get("cauza_deces", ""),
        "profesie": "",
        "religie": "",
        "ultima_adresa": data.get("ultima_adresa", ""),
        "observatii": (
            f"Locul decesului: {data.get('locul_decesului', '')} · "
            f"Emitent: {data.get('emitent', '')} · "
            f"Data eliberării: {data.get('data_eliberare', '')}"
        ),
        "nr_certificat_deces": data.get("nr_certificat_deces", ""),
        "nr_adeverinta_inhumare": data.get("nr_adeverinta_inhumare", ""),
    }
    try:
        if actions and hasattr(actions, "save_inhumat"):
            return actions.save_inhumat(cod_loc, payload)
        return db.save_inhumat(cod_loc, payload)
    except Exception as e:
        print(f"[save_inhumat_from_scan] {e}")
        return None


def _handle_file_action(
    source_file: str,
    file_action: str,
    entity_type: Optional[str],
    entity_id: Optional[Any],
    doc_type: str,
) -> Optional[str]:
    """Aplică acțiunea aleasă pentru fișierul sursă. Returnează calea finală salvată (sau None)."""
    if not source_file or not os.path.exists(source_file):
        return None

    if file_action == "delete":
        try:
            os.remove(source_file)
        except Exception:
            pass
        return None

    subdir = entity_type if entity_type else "arhiva"
    kept_path = db.import_media_file(source_file, subdir=subdir)

    if file_action == "attach" and kept_path and entity_type and entity_id:
        db.add_document(
            entity_type=entity_type,
            entity_id=entity_id,
            doc_type=doc_type,
            src_path=kept_path,
            copy=False,  # deja copiat de import_media_file
        )
    # dacă e "archive" sau fallback: rămâne doar copia în /media/{subdir}
    return kept_path


# ============ SCAN WINDOW ============

class AIScanareWindow(QDialog):
    """Fereastra principală AI Scanare."""

    def __init__(self, parent=None, actions=None):
        super().__init__(parent)
        self.actions = actions
        self.setWindowTitle("🤖  AI Scanare Documente")
        self.setMinimumSize(1240, 800)
        self.setStyleSheet("QDialog { background:#020617; color:#e2e8f0; }")

        init_scan_history()

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        # --- header ---
        head = QVBoxLayout()
        title = QLabel("AI Scanare Documente")
        title.setStyleSheet("color:#f1f5f9; font-size:22pt; font-weight:800; letter-spacing:-0.5px;")
        subtitle = QLabel(
            "Fotografiază sau încarcă un buletin/certificat — Gemini 3 Flash "
            "extrage automat toate câmpurile și creează concesionarul sau inhumatul."
        )
        subtitle.setStyleSheet("color:#94a3b8; font-size:10pt;")
        head.addWidget(title)
        head.addWidget(subtitle)

        # indicator stare AI (AI activ / Mod offline / cheie invalidă)
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.ai_status_lbl = QLabel()
        self.ai_status_lbl.setObjectName("ai-status-indicator")

        self.btn_ai_fix = QPushButton("Repară")
        self.btn_ai_fix.setObjectName("btn-ai-fix")
        self.btn_ai_fix.setToolTip("Deschide Setări AI ca să înlocuiești cheia")
        self.btn_ai_fix.setCursor(QCursor(Qt.PointingHandCursor))
        self.btn_ai_fix.setStyleSheet(
            "QPushButton { background:#ef4444; color:white; padding:3px 12px; "
            "border:none; border-radius:10px; font-weight:700; font-size:9pt; }"
            "QPushButton:hover { background:#dc2626; }"
        )
        self.btn_ai_fix.clicked.connect(self._open_ai_settings)
        self.btn_ai_fix.hide()

        status_row.addWidget(self.ai_status_lbl)
        status_row.addWidget(self.btn_ai_fix)
        status_row.addStretch()
        head.addLayout(status_row)
        root.addLayout(head)

        # --- cards ---
        cards = QHBoxLayout()
        cards.setSpacing(16)

        self.card_buletin = ScanCard(
            "🆔",
            "Scanare Buletin CI",
            "Extrage Nume, CNP, Serie/Nr, Data nașterii, Adresă și restul câmpurilor. "
            "La final creează un concesionar.",
            ["Nume", "CNP", "Serie/Nr", "Adresă", "Emitent"],
        )
        self.card_buletin.btn.clicked.connect(lambda: self._start_scan(DocumentType.BULETIN))

        self.card_deces = ScanCard(
            "📜",
            "Scanare Certificat Deces / Adeverință Inhumare",
            "Extrage datele decedatului și creează un inhumat legat de un loc de veci pe care îl alegi.",
            ["Nume", "CNP", "Data deces", "Cauza", "Nr. cert."],
        )
        self.card_deces.btn.clicked.connect(lambda: self._start_scan(DocumentType.CERTIFICAT_DECES))

        self.card_adeverinta = ScanCard(
            "📍",
            "Scanare Adeverință Adresă (CI nou)",
            "Pentru noile CI electronice unde adresa e pe un document separat. "
            "Actualizează adresa unui concesionar existent sau completează un buletin scanat.",
            ["Adresa", "Localitate", "Județ", "CNP", "SPCLEP"],
        )
        self.card_adeverinta.btn.clicked.connect(lambda: self._start_scan(DocumentType.ADEVERINTA_ADRESA))

        cards.addWidget(self.card_buletin, 1)
        cards.addWidget(self.card_deces, 1)
        cards.addWidget(self.card_adeverinta, 1)
        root.addLayout(cards)

        # --- toolbar batch + mobile scan ---
        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)

        self.btn_batch = QPushButton("📁  Scanare batch (mai multe deodată)")
        self.btn_batch.setStyleSheet(
            "QPushButton { background:#334155; color:white; padding:8px 14px; "
            "border:none; border-radius:6px; font-weight:600; }"
            "QPushButton:hover { background:#475569; }"
        )
        self.btn_batch.clicked.connect(self._start_batch)

        self.btn_mobile = QPushButton("📱  Scanare de pe telefon (QR code)")
        self.btn_mobile.setStyleSheet(
            "QPushButton { background:#334155; color:white; padding:8px 14px; "
            "border:none; border-radius:6px; font-weight:600; }"
            "QPushButton:hover { background:#475569; }"
        )
        self.btn_mobile.clicked.connect(self._start_mobile_scan)

        self.btn_ai_settings = QPushButton("⚙️  Setări AI")
        self.btn_ai_settings.setObjectName("btn-ai-settings")
        self.btn_ai_settings.setToolTip("Configurează cheia Gemini, modul de lucru și modelul")
        self.btn_ai_settings.setStyleSheet(
            "QPushButton { background:#0ea5e9; color:white; padding:8px 14px; "
            "border:none; border-radius:6px; font-weight:600; }"
            "QPushButton:hover { background:#0284c7; }"
        )
        self.btn_ai_settings.clicked.connect(self._open_ai_settings)

        self.btn_stats = QPushButton("📊  Statistici")
        self.btn_stats.setObjectName("btn-scan-stats")
        self.btn_stats.setToolTip("Scanări pe lună și rata de reușită cu AI")
        self.btn_stats.setStyleSheet(
            "QPushButton { background:#334155; color:white; padding:8px 14px; "
            "border:none; border-radius:6px; font-weight:600; }"
            "QPushButton:hover { background:#475569; }"
        )
        self.btn_stats.clicked.connect(self._open_stats)

        actions_row.addWidget(self.btn_batch)
        actions_row.addWidget(self.btn_mobile)
        actions_row.addWidget(self.btn_stats)
        actions_row.addWidget(self.btn_ai_settings)
        actions_row.addStretch()
        root.addLayout(actions_row)

        # --- banda de statistici ---
        self.stats_row = QHBoxLayout()
        self.stats_row.setSpacing(10)
        root.addLayout(self.stats_row)

        # --- istoric ---
        hist_box = QGroupBox("📋  Istoric scanări")
        hist_box.setStyleSheet(
            "QGroupBox { color:#cbd5e1; font-weight:600; border:1px solid #1e293b; "
            "border-radius:8px; margin-top:8px; padding-top:14px; }"
            "QGroupBox::title { subcontrol-origin: margin; left:10px; padding:0 6px; }"
        )
        hv = QVBoxLayout(hist_box)

        # filtre
        filt = QHBoxLayout()
        self.filter_type = QComboBox()
        self.filter_type.setObjectName("history-filter-type")
        self.filter_type.addItem("Toate tipurile", "")
        self.filter_type.addItem(DOC_LABELS[DocumentType.BULETIN], DocumentType.BULETIN)
        self.filter_type.addItem(DOC_LABELS[DocumentType.CERTIFICAT_DECES],
                                 DocumentType.CERTIFICAT_DECES)
        self.filter_type.addItem(DOC_LABELS[DocumentType.ADEVERINTA_ADRESA],
                                 DocumentType.ADEVERINTA_ADRESA)
        self.filter_type.currentIndexChanged.connect(self.reload_history)

        self.filter_status = QComboBox()
        self.filter_status.setObjectName("history-filter-status")
        self.filter_status.addItem("Toate stările", "")
        self.filter_status.addItem("Procesat", "processed")
        self.filter_status.addItem("Atașat", "attached")
        self.filter_status.addItem("Arhivat", "archived")
        self.filter_status.currentIndexChanged.connect(self.reload_history)

        self.filter_search = QLineEdit()
        self.filter_search.setObjectName("history-filter-search")
        self.filter_search.setPlaceholderText("Caută după nume fișier sau nume persoană…")
        self.filter_search.textChanged.connect(self.reload_history)

        filt.addWidget(QLabel("Tip:"))
        filt.addWidget(self.filter_type)
        filt.addSpacing(8)
        filt.addWidget(QLabel("Status:"))
        filt.addWidget(self.filter_status)
        filt.addSpacing(8)
        filt.addWidget(self.filter_search, 1)

        self.btn_refresh = QPushButton("🔄  Reîncarcă")
        self.btn_refresh.setObjectName("history-refresh")
        self.btn_refresh.clicked.connect(self.reload_history)
        filt.addWidget(self.btn_refresh)

        self.btn_export = QPushButton("⬇  Export CSV/Excel")
        self.btn_export.setObjectName("history-export")
        self.btn_export.setToolTip("Exportă istoricul filtrat într-un fișier CSV "
                                   "care se deschide direct în Excel")
        self.btn_export.clicked.connect(self._export_history)
        filt.addWidget(self.btn_export)

        hv.addLayout(filt)

        # tabel
        self.table = QTableWidget(0, 7)
        self.table.setObjectName("scan-history-table")
        self.table.setHorizontalHeaderLabels(
            ["Data", "Tip", "Fișier", "Persoană", "Status", "Entitate", "Acțiuni"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setStyleSheet(
            "QTableWidget { background:#0f172a; color:#e2e8f0; border:1px solid #1e293b; }"
            "QHeaderView::section { background:#1e293b; color:#94a3b8; padding:6px; border:none; }"
        )
        hv.addWidget(self.table, 1)

        root.addWidget(hist_box, 1)

        # --- footer close ---
        btn_close = QPushButton("Închide")
        btn_close.clicked.connect(self.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        root.addLayout(btn_row)

        self.reload_history()
        self._refresh_ai_status()
        # verificare silențioasă a cheii, după ce fereastra s-a afișat
        QTimer.singleShot(300, self._start_key_health_check)

    # ---------------------------------------------------------------
    def _refresh_ai_status(self):
        """Actualizează indicatorul „AI activ (Gemini)" / „Mod offline"."""
        try:
            from .ai_client import active_engine_status
            st = active_engine_status()
        except Exception:
            st = {"engine": "offline", "label": "Mod offline"}
        active = st.get("engine") in ("google", "emergent")
        color = "#22c55e" if active else "#f59e0b"
        icon = "🟢" if active else "🟡"
        bg = "rgba(34,197,94,0.12)" if active else "rgba(245,158,11,0.12)"
        self.ai_status_lbl.setText(f"{icon}  {st.get('label', 'Mod offline')}")
        self.ai_status_lbl.setToolTip("")
        self.ai_status_lbl.setStyleSheet(
            f"color:{color}; background:{bg}; padding:4px 10px; "
            f"border-radius:10px; font-weight:600; font-size:9pt;"
        )
        self.btn_ai_fix.hide()

    # ---------------------------------------------------------------
    # VERIFICARE SILENȚIOASĂ A CHEII AI
    # ---------------------------------------------------------------
    def _start_key_health_check(self):
        """Verifică în fundal dacă cheia Gemini mai e valabilă (fără pop-up-uri)."""
        try:
            from . import config as ai_config
            if ai_config.get_mode() == ai_config.MODE_OFFLINE:
                return
            key = ai_config.get_api_key()
        except Exception:
            return
        if not key:
            return

        cached = cached_key_health(key)
        if cached is not None:
            ok, msg = cached
            self._on_key_health(ok, msg)
            return

        self._health_key = key
        self._health_thread = QThread(self)
        self._health_worker = _KeyHealthWorker(key)
        self._health_worker.moveToThread(self._health_thread)
        self._health_thread.started.connect(self._health_worker.run)
        self._health_worker.done.connect(self._on_key_health_checked)
        self._health_worker.done.connect(self._health_thread.quit)
        self._health_thread.start()

    def _on_key_health_checked(self, ok: bool, message: str):
        remember_key_health(getattr(self, "_health_key", ""), ok, message)
        self._on_key_health(ok, message)

    def _on_key_health(self, ok: bool, message: str):
        """Aplică rezultatul verificării pe indicator (roșu + buton Repară la eșec)."""
        if ok:
            self._refresh_ai_status()
            return
        self.ai_status_lbl.setText("🔴  Cheie AI invalidă")
        self.ai_status_lbl.setToolTip(
            f"{message}\n\nScanarea va folosi OCR-ul offline până înlocuiești cheia."
        )
        self.ai_status_lbl.setStyleSheet(
            "color:#ef4444; background:rgba(239,68,68,0.12); padding:4px 10px; "
            "border-radius:10px; font-weight:700; font-size:9pt;"
        )
        self.btn_ai_fix.show()

    def _open_ai_settings(self):
        """Deschide ecranul de Setări AI și reîmprospătează indicatorul."""
        try:
            from .ai_settings_dialog import AISettingsDialog
        except Exception as e:
            QMessageBox.critical(self, "Eroare",
                                 f"Nu pot deschide Setări AI: {e}")
            return
        dlg = AISettingsDialog(self)
        dlg.settings_saved.connect(self._refresh_ai_status)
        dlg.exec()
        forget_key_health()  # cheia poate fi alta => re-verificăm
        self._refresh_ai_status()
        QTimer.singleShot(200, self._start_key_health_check)

    # ---------------------------------------------------------------
    def _find_main_actions(self):
        """Găsește obiectul `actions` (din fereastra principală) pentru salvare în DB."""
        if self.actions:
            return self.actions
        w = self.parent()
        while w is not None and not hasattr(w, "actions"):
            w = w.parent()
        return getattr(w, "actions", None) if w else None

    # ---------------------------------------------------------------
    def _start_scan(self, doc_type: str):
        """Deschide dialogul de scanare pentru un tip anume."""
        dlg = ScanDialog(self, doc_type, allow_type_change=True)
        if dlg.exec() != QDialog.Accepted:
            return
        # Salvarea + dedup se face în _save_after_scan (comun cu re-scan / mobile)
        self._save_after_scan(dlg)
        return

        # LEGACY (nu mai e folosit — păstrat pentru referință dacă e nevoie)
        data = dlg.get_extracted_data()
        source_file = dlg.get_source_file()
        file_action = dlg.get_file_action()
        real_doc_type = dlg.get_document_type()

        actions = self._find_main_actions()
        entity_type: Optional[str] = None
        entity_id: Optional[int] = None

        if real_doc_type == DocumentType.BULETIN:
            entity_id = _save_concesionar_from_scan(actions, data)
            if not entity_id:
                QMessageBox.warning(
                    self, "Eroare",
                    "Nu am putut salva concesionarul. Verifică datele obligatorii (nume/prenume)."
                )
                return
            entity_type = "concesionar"

            # Întreabă dacă vrea să atribuie un loc de veci
            reply = QMessageBox.question(
                self,
                "Atribuire loc de veci",
                f"Concesionarul <b>{data.get('nume','')} {data.get('prenume','')}</b> "
                f"a fost creat cu succes.<br><br>"
                f"Doriți să îi atribuiți acum un loc de veci?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self._link_loc_to_concesionar(entity_id, data)

        else:  # certificat_deces
            # Cere obligatoriu locul de veci
            cod_loc = self._pick_cod_loc(
                "Selectează locul de veci pentru inhumat",
                "Introduceți codul locului (obligatoriu) pentru inhumat:"
            )
            if not cod_loc:
                QMessageBox.information(
                    self, "Anulat",
                    "Fără un loc de veci nu se poate crea inhumatul."
                )
                return
            if db.get_loc_id_by_cod(cod_loc) is None:
                QMessageBox.warning(
                    self, "Loc inexistent",
                    f"Locul cu codul '{cod_loc}' nu există în DB."
                )
                return
            entity_id = _save_inhumat_from_scan(actions, cod_loc, data)
            if not entity_id:
                QMessageBox.warning(
                    self, "Eroare",
                    "Nu am putut salva inhumatul."
                )
                return
            entity_type = "inhumat"

        # File action
        try:
            _handle_file_action(source_file, file_action, entity_type, entity_id, real_doc_type)
        except Exception as e:
            print(f"[file action] {e}")

        # Save history
        try:
            save_scan(
                document_type=real_doc_type,
                filename=os.path.basename(source_file or ""),
                file_path=source_file if file_action != "delete" else None,
                extracted={"fields": data,
                          "raw_text": dlg._extracted.get("raw_text", ""),
                          "engine": dlg._extracted.get("engine", "")},
                status=("attached" if file_action == "attach"
                        else "archived" if file_action == "archive"
                        else "processed"),
                entity_type=entity_type,
                entity_id=entity_id,
            )
        except Exception as e:
            print(f"[save_scan history] {e}")

        # notify main window signals if available
        try:
            main = self.parent()
            while main is not None and not hasattr(main, "signals"):
                main = main.parent()
            if main and hasattr(main, "signals"):
                main.signals.data_changed.emit(entity_type or "loc", str(entity_id or ""))
        except Exception:
            pass

        QMessageBox.information(
            self, "Succes",
            f"Am creat {entity_type} cu ID={entity_id} din documentul scanat."
        )
        self.reload_history()

    # ---------------------------------------------------------------
    def _link_loc_to_concesionar(self, concesionar_id: int, data: dict) -> None:
        """Cere cod_loc și scrie contractul minimal + marchează locul ca ocupat."""
        cod_loc = self._pick_cod_loc(
            "Atribuire loc de veci",
            "Introduceți codul locului pe care îl atribui concesionarului:",
        )
        if not cod_loc:
            return
        if db.get_loc_id_by_cod(cod_loc) is None:
            QMessageBox.warning(self, "Loc inexistent", f"Locul '{cod_loc}' nu există.")
            return
        # Creăm un contract minimal ce leagă concesionarul de loc
        try:
            db.save_contract({
                "concesionar_id": concesionar_id,
                "cod_loc": cod_loc,
                "owner_fullname": f"{data.get('nume','')} {data.get('prenume','')}".strip(),
                "owner_cnp": data.get("cnp", ""),
                "owner_id_card_serie_number": f"{data.get('serie','')} {data.get('numar','')}".strip(),
                "owner_address": data.get("adresa", ""),
                "observatii": "Creat automat prin scanare AI a buletinului",
            })
            # marchează locul ca ocupat
            db.save_loc_metadata(cod_loc, {"ocupat": 1, "stare": "concesionat"})
        except Exception as e:
            QMessageBox.warning(self, "Eroare", f"Nu am putut lega locul: {e}")

    # ---------------------------------------------------------------
    def _pick_cod_loc(self, title: str, prompt: str) -> Optional[str]:
        """Popup simplu pentru codul locului. Sugerează primele 30 de coduri libere."""
        try:
            all_locuri = db.list_locuri()
            libere = [l for l in all_locuri if not l.get("ocupat")]
            if libere:
                items = [f"{l.get('cod_loc','')}  ({l.get('sector','')}/{l.get('rand','')}/{l.get('numar','')})"
                         for l in libere[:60]]
                items.append("(altul — introduc manual)")
                choice, ok = QInputDialog.getItem(self, title, prompt, items, 0, True)
                if not ok or not choice:
                    return None
                if choice.startswith("(altul"):
                    txt, ok2 = QInputDialog.getText(self, title, "Cod loc:")
                    return txt.strip() if ok2 and txt.strip() else None
                return choice.split()[0].strip()
        except Exception:
            pass
        # fallback: text input simplu
        txt, ok = QInputDialog.getText(self, title, prompt)
        return txt.strip() if ok and txt and txt.strip() else None

    # ---------------------------------------------------------------
    def reload_history(self):
        doc_type = self.filter_type.currentData()
        status = self.filter_status.currentData()
        query = self.filter_search.text().strip().lower()

        rows = list_scans(document_type=doc_type or None, status=status or None)

        def match(r):
            if not query:
                return True
            hay = (r.get("filename", "") or "").lower()
            extracted = r.get("extracted", {}) or {}
            fields = extracted.get("fields", extracted) or {}
            for v in fields.values():
                if isinstance(v, dict):
                    hay += " " + str(v.get("value", "") or "").lower()
                else:
                    hay += " " + str(v or "").lower()
            return query in hay

        rows = [r for r in rows if match(r)]

        self.table.setRowCount(0)
        for r in rows:
            idx = self.table.rowCount()
            self.table.insertRow(idx)

            self.table.setItem(idx, 0, QTableWidgetItem(r.get("created_at", "")))

            tp = r.get("document_type", "")
            self.table.setItem(idx, 1, QTableWidgetItem(doc_label(tp)))

            filename = r.get("filename", "") or "—"
            self.table.setItem(idx, 2, QTableWidgetItem(filename))

            extracted = r.get("extracted", {}) or {}
            fields = extracted.get("fields", extracted) or {}
            nume = ""
            prenume = ""
            if isinstance(fields.get("nume"), dict):
                nume = fields["nume"].get("value", "")
                prenume = fields.get("prenume", {}).get("value", "") if isinstance(fields.get("prenume"), dict) else ""
            else:
                nume = fields.get("nume", "") or ""
                prenume = fields.get("prenume", "") or ""
            self.table.setItem(idx, 3, QTableWidgetItem(f"{nume} {prenume}".strip() or "—"))

            self.table.setItem(idx, 4, QTableWidgetItem(r.get("status", "")))

            ent = ""
            if r.get("entity_type"):
                ent = f"{r['entity_type']} #{r.get('entity_id', '')}"
            self.table.setItem(idx, 5, QTableWidgetItem(ent))

            # Acțiuni
            action_widget = QWidget()
            aw = QHBoxLayout(action_widget)
            aw.setContentsMargins(4, 2, 4, 2)
            aw.setSpacing(4)

            btn_open = QPushButton("Deschide")
            btn_open.setObjectName("history-open-file")
            btn_open.setStyleSheet("QPushButton { padding:2px 8px; }")
            file_path = r.get("file_path", "") or ""
            btn_open.setEnabled(bool(file_path and os.path.exists(file_path)))
            btn_open.clicked.connect(lambda _=None, p=file_path: QDesktopServices.openUrl(QUrl.fromLocalFile(p)))

            btn_details = QPushButton("👁")
            btn_details.setObjectName("history-details")
            btn_details.setToolTip("Vezi toate câmpurile extrase")
            btn_details.setStyleSheet("QPushButton { padding:2px 6px; color:#a5b4fc; }")
            btn_details.clicked.connect(
                lambda _=None, sid=r["id"]: self._show_scan_details(sid)
            )

            btn_goto = QPushButton("➜")
            btn_goto.setObjectName("history-goto-entity")
            ent_type = r.get("entity_type") or ""
            ent_id = r.get("entity_id")
            btn_goto.setToolTip(
                f"Deschide fișa: {ent_type} #{ent_id}" if ent_type and ent_id
                else "Această scanare nu a creat nicio fișă"
            )
            btn_goto.setStyleSheet("QPushButton { padding:2px 6px; color:#22c55e; }")
            btn_goto.setEnabled(bool(ent_type and ent_id))
            btn_goto.clicked.connect(
                lambda _=None, et=ent_type, ei=ent_id: self._open_entity(et, ei)
            )

            btn_rescan = QPushButton("🔄")
            btn_rescan.setObjectName("history-rescan")
            btn_rescan.setToolTip("Re-rulează extragerea AI pe același fișier")
            btn_rescan.setStyleSheet("QPushButton { padding:2px 6px; color:#0ea5e9; }")
            btn_rescan.setEnabled(bool(file_path and os.path.exists(file_path)))
            btn_rescan.clicked.connect(
                lambda _=None, p=file_path, dt=r.get("document_type"): self._rescan(p, dt)
            )

            btn_del = QPushButton("🗑")
            btn_del.setObjectName("history-delete")
            btn_del.setStyleSheet("QPushButton { color:#f87171; padding:2px 6px; }")
            btn_del.clicked.connect(lambda _=None, sid=r["id"]: self._delete_scan(sid))

            aw.addWidget(btn_open)
            aw.addWidget(btn_details)
            aw.addWidget(btn_goto)
            aw.addWidget(btn_rescan)
            aw.addWidget(btn_del)
            aw.addStretch()
            self.table.setCellWidget(idx, 6, action_widget)

        self.table.resizeColumnsToContents()
        self._history_rows = rows
        self._refresh_stats()

    # ---------------------------------------------------------------
    def _refresh_stats(self):
        """Reconstruiește banda cu cele 4 cifre din capul istoricului."""
        while self.stats_row.count():
            item = self.stats_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for widget in stat_cards(scan_stats()):
            self.stats_row.addWidget(widget)

    def _open_stats(self):
        """Deschide fereastra cu statistici pe ultimele 12 luni."""
        ScanStatsDialog(self).exec()

    # ---------------------------------------------------------------
    def _show_scan_details(self, scan_id: int):
        """Deschide dialogul cu toate câmpurile extrase pentru o scanare."""
        scan = get_scan(scan_id)
        if not scan:
            QMessageBox.warning(self, "Lipsă", "Înregistrarea nu mai există în istoric.")
            self.reload_history()
            return
        ScanDetailsDialog(self, scan).exec()

    def _open_entity(self, entity_type: str, entity_id: Optional[int]):
        """Deschide fișa concesionarului sau inhumatului creat din scanare."""
        if not entity_type or not entity_id:
            return
        actions = self._find_main_actions()
        try:
            if entity_type == "concesionar":
                existing = db.get_concesionar(int(entity_id))
                if not existing:
                    QMessageBox.warning(self, "Lipsă",
                                        f"Concesionarul #{entity_id} nu mai există în baza de date.")
                    return
                from concesionari_window import AddConcesionarDialog
                AddConcesionarDialog(self, existing=existing, actions=actions).exec()
            elif entity_type == "inhumat":
                existing = db.get_inhumat(int(entity_id))
                if not existing:
                    QMessageBox.warning(self, "Lipsă",
                                        f"Inhumatul #{entity_id} nu mai există în baza de date.")
                    return
                from add_inhumat_dialog import AddInhumatDialog
                AddInhumatDialog(self, existing.get("cod_loc", "") or "",
                                 existing=existing).exec()
            else:
                QMessageBox.information(self, "Nedisponibil",
                                        f"Nu pot deschide o fișă de tip '{entity_type}'.")
                return
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Eroare",
                                 f"Nu pot deschide fișa {entity_type} #{entity_id}:\n{exc}")
            return
        self.reload_history()

    def _export_history(self):
        """Exportă istoricul filtrat într-un CSV compatibil Excel (UTF-8 BOM, `;`)."""
        rows = getattr(self, "_history_rows", None)
        if not rows:
            QMessageBox.information(self, "Nimic de exportat",
                                    "Istoricul filtrat nu conține nicio scanare.")
            return
        default_name = f"istoric_scanari_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "Salvează exportul", default_name,
            "Fișiere CSV pentru Excel (*.csv)"
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"

        headers = ["Data", "Tip document", "Fișier", "Nume", "Prenume", "CNP",
                   "Status", "Entitate", "ID entitate", "Cale fișier"]
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as fh:
                writer = csv.writer(fh, delimiter=";")
                writer.writerow(headers)
                for r in rows:
                    f = _plain_fields(r.get("extracted") or {})
                    writer.writerow([
                        r.get("created_at", ""),
                        doc_label(r.get("document_type")),
                        r.get("filename", "") or "",
                        f.get("nume", ""),
                        f.get("prenume", ""),
                        f.get("cnp", ""),
                        r.get("status", ""),
                        r.get("entity_type", "") or "",
                        r.get("entity_id", "") or "",
                        r.get("file_path", "") or "",
                    ])
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Eroare export",
                                 f"Nu am putut scrie fișierul:\n{exc}")
            return

        if QMessageBox.question(
            self, "Export reușit",
            f"Am exportat {len(rows)} scanări în:\n{path}\n\nDeschizi fișierul acum?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        ) == QMessageBox.Yes:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _delete_scan(self, scan_id: int):
        if QMessageBox.question(
            self, "Ștergere",
            "Ștergi această înregistrare din istoric?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        delete_scan(scan_id)
        self.reload_history()

    # ---------------------------------------------------------------
    # RE-SCAN din istoric
    # ---------------------------------------------------------------
    def _rescan(self, file_path: str, doc_type: str):
        """Re-rulează extragerea AI pe un fișier deja scanat."""
        if not file_path or not os.path.exists(file_path):
            QMessageBox.warning(self, "Fișier lipsă", "Fișierul original nu mai există pe disc.")
            return
        # deschidem ScanDialog cu fișierul preîncărcat
        dlg = ScanDialog(self, doc_type or DocumentType.BULETIN, allow_type_change=True)
        # simulăm selecția fișierului pentru a porni imediat scanarea
        try:
            dlg.upload_zone.file_selected.emit(file_path)
        except Exception:
            pass
        if dlg.exec() != QDialog.Accepted:
            return
        # continuăm cu fluxul normal de save
        self._save_after_scan(dlg)

    # ---------------------------------------------------------------
    # BATCH scanare
    # ---------------------------------------------------------------
    def _start_batch(self):
        dlg = BatchScanDialog(self, document_type=DocumentType.BULETIN)
        if dlg.exec() != QDialog.Accepted:
            return
        results = dlg.get_results()
        if not results:
            return
        actions = self._find_main_actions()

        summary = {"concesionari": 0, "inhumati": 0, "skipped": 0, "duplicates": 0}
        for r in results:
            doc_type = r["document_type"]
            extracted = r["extracted"]
            fields_raw = extracted.get("fields", {}) or {}
            data = {}
            for k, v in fields_raw.items():
                data[k] = v.get("value", "") if isinstance(v, dict) else str(v or "")
            if not data.get("nume") and not data.get("prenume"):
                summary["skipped"] += 1
                continue

            # dedup check
            cnp = data.get("cnp", "")
            if doc_type == DocumentType.BULETIN:
                existing = find_concesionar_by_cnp(cnp)
                if existing:
                    summary["duplicates"] += 1
                    continue  # în batch, sărim peste duplicate (mesaj la sfârșit)
                cid = _save_concesionar_from_scan(actions, data)
                if cid:
                    summary["concesionari"] += 1
                    save_scan(doc_type, os.path.basename(r["file_path"]),
                              r["file_path"], extracted, "attached", "concesionar", cid)
                    try:
                        db.add_document("concesionar", cid, "buletin", r["file_path"], copy=True)
                    except Exception:
                        pass
            else:
                # Certificat deces necesită loc_de_veci → în batch cerem o singură dată
                if "batch_cod_loc" not in summary:
                    cod_loc = self._pick_cod_loc(
                        "Loc de veci pentru batch",
                        "Toate certificatele scanate vor fi legate de acest loc:",
                    )
                    summary["batch_cod_loc"] = cod_loc
                cod_loc = summary.get("batch_cod_loc")
                if not cod_loc:
                    summary["skipped"] += 1
                    continue
                existing = find_inhumat_by_cnp(cnp)
                if existing:
                    summary["duplicates"] += 1
                    continue
                iid = _save_inhumat_from_scan(actions, cod_loc, data)
                if iid:
                    summary["inhumati"] += 1
                    save_scan(doc_type, os.path.basename(r["file_path"]),
                              r["file_path"], extracted, "attached", "inhumat", iid)
                    try:
                        db.add_document("inhumat", iid, "certificat_deces", r["file_path"], copy=True)
                    except Exception:
                        pass

        QMessageBox.information(
            self, "Batch terminat",
            f"Rezultate:\n"
            f"  ✅ {summary['concesionari']} concesionari creați\n"
            f"  ✅ {summary['inhumati']} inhumați creați\n"
            f"  ⚠ {summary['duplicates']} duplicate ignorate (CNP deja există)\n"
            f"  ⚠ {summary['skipped']} sărite (date incomplete)"
        )
        self.reload_history()

    # ---------------------------------------------------------------
    # MOBILE scan (QR)
    # ---------------------------------------------------------------
    def _start_mobile_scan(self):
        # verificăm dacă server-ul web e pornit
        port = 8000
        try:
            import database as _db
            saved = _db.get_setting("startup_port")
            if saved:
                port = int(saved) + 1  # serverul de scanare rulează lângă portal (launcher: MOBILE_SCAN_PORT_OFFSET)
        except Exception:
            pass

        # test simplu de conectivitate
        try:
            import requests
            r = requests.get(f"http://127.0.0.1:{port}/api/ai-scan-result/__test__", timeout=1)
            server_ok = r.status_code in (200, 404, 405)
        except Exception:
            server_ok = False

        if not server_ok:
            QMessageBox.warning(
                self, "Server web oprit",
                f"Serverul web local nu răspunde pe portul {port}.\n\n"
                "Pornește-l din launcher.py (Server local) "
                "sau alege alt port din dialog după deschidere."
            )
            # continuăm oricum — utilizatorul poate schimba portul în dialog

        dlg = MobileScanDialog(self, doc_type=DocumentType.BULETIN, port=port)
        if dlg.exec() != QDialog.Accepted:
            return
        extracted_raw = dlg.get_extracted() or {}
        source_file = dlg.get_source_file()
        doc_type = dlg.get_document_type()

        fields_raw = extracted_raw.get("fields", {}) or {}
        data = {}
        for k, v in fields_raw.items():
            data[k] = v.get("value", "") if isinstance(v, dict) else str(v or "")

        if not data.get("nume") and not data.get("prenume"):
            QMessageBox.warning(self, "Date incomplete", "Extragerea nu a returnat un nume valid.")
            return

        # Deschidem ScanDialog cu datele preîncărcate pentru user să verifice
        # (versiune ușoară: sărim direct la save)
        self._save_from_data(doc_type, data, source_file, "attach", extracted_raw)

    # ---------------------------------------------------------------
    # Utilitare comune pentru save (folosite din re-scan / mobile / batch)
    # ---------------------------------------------------------------
    def _save_after_scan(self, dlg: ScanDialog):
        """Salvează după ce user-ul a confirmat în ScanDialog.
        Înregistrează și în feedback_learning ce a corectat manual utilizatorul.
        """
        data = dlg.get_extracted_data()
        source_file = dlg.get_source_file()
        file_action = dlg.get_file_action()
        doc_type = dlg.get_document_type()
        # Snapshot al extragerii automate (înainte de edit-uri)
        original = {}
        for k, v in (dlg._extracted.get("fields", {}) or {}).items():
            original[k] = v.get("value", "") if isinstance(v, dict) else str(v or "")
        # Feedback learning — colectăm ce a corectat user-ul
        try:
            from .feedback_learning import record_correction
            record_correction(
                raw_ocr_text=dlg._extracted.get("raw_text", "") if dlg._extracted else "",
                extracted_fields=original,
                corrected_fields=data,
                document_type=doc_type,
            )
        except Exception as e:
            print(f"[feedback_learning] {e}")

        # Redirect pentru adeverință: nu creează concesionar nou, ci actualizează unul existent
        if doc_type == DocumentType.ADEVERINTA_ADRESA:
            self._handle_adeverinta_adresa(data, source_file, file_action,
                                          engine=(dlg._extracted or {}).get("engine", ""))
            return
        self._save_from_data(doc_type, data, source_file, file_action, dlg._extracted)

    def _handle_adeverinta_adresa(self, data: dict, source_file, file_action: str,
                                  engine: str = ""):
        """Actualizează un concesionar existent cu adresa din adeverință.

        Caută întâi după CNP; dacă nu găsește, întreabă utilizatorul.
        """
        from .duplicate_check import find_concesionar_by_cnp, update_concesionar

        cnp = data.get("cnp", "")
        existing = find_concesionar_by_cnp(cnp) if cnp else None
        if not existing:
            # Ofertă: creăm concesionar nou doar din adeverință
            reply = QMessageBox.question(
                self,
                "Concesionar inexistent",
                f"Nu găsesc niciun concesionar cu CNP {cnp or '(lipsă)'}.\n\n"
                "Vrei să creezi un concesionar NOU folosind datele din adeverință?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            payload = {
                "nume": data.get("nume", ""),
                "prenume": data.get("prenume", ""),
                "cnp": cnp,
                "bi_ci": f"{data.get('serie', '')} {data.get('numar', '')}".strip(),
                "adresa": data.get("adresa", ""),
                "observatii": (
                    f"[Adeverință CI nou] Emitent: {data.get('emitent', '')} · "
                    f"Elib: {data.get('data_eliberarii', '')} · "
                    f"Localitate: {data.get('localitate', '')} · "
                    f"Județ: {data.get('judet', '')}"
                ),
            }
            actions = self._find_main_actions()
            new_id = _save_concesionar_from_scan(actions, payload)
            if new_id:
                _handle_file_action(source_file, file_action, "concesionar", new_id, "adeverinta_adresa")
                save_scan(
                    document_type=DocumentType.ADEVERINTA_ADRESA,
                    filename=os.path.basename(source_file or ""),
                    file_path=source_file if file_action != "delete" else None,
                    extracted={"fields": data, "engine": engine},
                    status=("attached" if file_action == "attach" else "processed"),
                    entity_type="concesionar",
                    entity_id=new_id,
                )
                QMessageBox.information(self, "Creat", f"Concesionar #{new_id} creat din adeverință.")
                self.reload_history()
            return

        # Actualizează concesionarul existent
        updated = {**existing}
        for src_k, dst_k in [
            ("adresa", "adresa"),
            ("serie", None),  # merged in bi_ci
            ("numar", None),
        ]:
            pass
        updated["adresa"] = data.get("adresa", existing.get("adresa", "")) or existing.get("adresa", "")
        bi_ci = f"{data.get('serie', '')} {data.get('numar', '')}".strip()
        if bi_ci:
            updated["bi_ci"] = bi_ci
        obs = existing.get("observatii", "")
        note = (
            f"[Adeverință CI nou] Emitent: {data.get('emitent', '')} · "
            f"Elib: {data.get('data_eliberarii', '')} · "
            f"Localitate: {data.get('localitate', '')} · "
            f"Județ: {data.get('judet', '')}"
        )
        updated["observatii"] = (obs + " | " + note).strip(" |") if obs else note
        update_concesionar(existing["id"], updated)

        _handle_file_action(source_file, file_action, "concesionar", existing["id"], "adeverinta_adresa")
        save_scan(
            document_type=DocumentType.ADEVERINTA_ADRESA,
            filename=os.path.basename(source_file or ""),
            file_path=source_file if file_action != "delete" else None,
            extracted={"fields": data, "engine": engine},
            status=("attached" if file_action == "attach" else "processed"),
            entity_type="concesionar",
            entity_id=existing["id"],
        )
        QMessageBox.information(
            self, "Actualizat",
            f"Concesionar #{existing['id']} ({existing.get('nume','')} {existing.get('prenume','')}) "
            f"actualizat cu adresa nouă din adeverință."
        )
        self.reload_history()

    def _save_from_data(self, doc_type: str, data: dict, source_file: Optional[str],
                        file_action: str, full_extracted: dict):
        actions = self._find_main_actions()
        entity_type: Optional[str] = None
        entity_id: Optional[int] = None

        # ---- DEDUP CHECK ----
        cnp = data.get("cnp", "")
        if doc_type == DocumentType.BULETIN and cnp:
            existing = find_concesionar_by_cnp(cnp)
            if existing:
                reply = QMessageBox.question(
                    self,
                    "CNP deja existent",
                    f"Există deja un concesionar cu CNP {cnp}:\n\n"
                    f"  {existing.get('nume', '')} {existing.get('prenume', '')}\n"
                    f"  ID: {existing.get('id')}\n\n"
                    "Actualizezi datele existente sau creezi unul nou?",
                    QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                )
                if reply == QMessageBox.Cancel:
                    return
                if reply == QMessageBox.Yes:
                    # UPDATE
                    updated_data = {
                        **existing,
                        "nume": data.get("nume", existing.get("nume", "")),
                        "prenume": data.get("prenume", existing.get("prenume", "")),
                        "bi_ci": f"{data.get('serie', '')} {data.get('numar', '')}".strip() or existing.get("bi_ci", ""),
                        "adresa": data.get("adresa", existing.get("adresa", "")),
                        "observatii": (existing.get("observatii", "") + " | Actualizat din scanare AI").strip(" |"),
                    }
                    update_concesionar(existing["id"], updated_data)
                    entity_id = existing["id"]
                    entity_type = "concesionar"
                    QMessageBox.information(self, "Actualizat", f"Concesionar #{entity_id} actualizat.")

        if doc_type == DocumentType.CERTIFICAT_DECES and cnp:
            existing = find_inhumat_by_cnp(cnp)
            if existing and not entity_id:
                reply = QMessageBox.question(
                    self,
                    "CNP deja existent",
                    f"Există deja un inhumat cu CNP {cnp}:\n\n"
                    f"  {existing.get('nume', '')} {existing.get('prenume', '')}\n"
                    f"  Loc: {existing.get('cod_loc', '—')}\n\n"
                    "Actualizezi datele existente sau creezi unul nou?",
                    QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                )
                if reply == QMessageBox.Cancel:
                    return
                if reply == QMessageBox.Yes:
                    updated_data = {
                        "nume": data.get("nume", existing.get("nume", "")),
                        "prenume": data.get("prenume", existing.get("prenume", "")),
                        "cnp": cnp,
                        "data_nasterii": data.get("data_nasterii", ""),
                        "data_deces": data.get("data_deces", ""),
                        "sex": data.get("sex", ""),
                        "cauza_deces": data.get("cauza_deces", ""),
                        "ultima_adresa": data.get("ultima_adresa", ""),
                        "nr_certificat_deces": data.get("nr_certificat_deces", ""),
                        "observatii": "Actualizat din scanare AI",
                    }
                    update_inhumat(existing["id"], existing.get("cod_loc", ""), updated_data)
                    entity_id = existing["id"]
                    entity_type = "inhumat"
                    QMessageBox.information(self, "Actualizat", f"Inhumat #{entity_id} actualizat.")

        # ---- CREATE NEW dacă nu s-a făcut update ----
        if not entity_id:
            if doc_type == DocumentType.BULETIN:
                entity_id = _save_concesionar_from_scan(actions, data)
                if not entity_id:
                    QMessageBox.warning(self, "Eroare", "Salvare eșuată.")
                    return
                entity_type = "concesionar"
            else:
                cod_loc = self._pick_cod_loc(
                    "Loc de veci pentru inhumat",
                    "Cod loc:",
                )
                if not cod_loc or db.get_loc_id_by_cod(cod_loc) is None:
                    QMessageBox.warning(self, "Anulat", "Fără un loc valid nu se poate crea inhumatul.")
                    return
                entity_id = _save_inhumat_from_scan(actions, cod_loc, data)
                if not entity_id:
                    QMessageBox.warning(self, "Eroare", "Salvare eșuată.")
                    return
                entity_type = "inhumat"

        # ---- FILE action ----
        try:
            _handle_file_action(source_file, file_action, entity_type, entity_id, doc_type)
        except Exception:
            pass

        # ---- History ----
        try:
            save_scan(
                document_type=doc_type,
                filename=os.path.basename(source_file or ""),
                file_path=source_file if file_action != "delete" else None,
                extracted={"fields": data,
                          "raw_text": (full_extracted or {}).get("raw_text", ""),
                          "engine": (full_extracted or {}).get("engine", "")},
                status=("attached" if file_action == "attach"
                        else "archived" if file_action == "archive"
                        else "processed"),
                entity_type=entity_type,
                entity_id=entity_id,
            )
        except Exception:
            pass

        # notify main
        try:
            main = self.parent()
            while main is not None and not hasattr(main, "signals"):
                main = main.parent()
            if main and hasattr(main, "signals"):
                main.signals.data_changed.emit(entity_type or "loc", str(entity_id or ""))
        except Exception:
            pass

        self.reload_history()
