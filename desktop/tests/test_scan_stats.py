"""Teste pentru statistici scanări (iteration 4): scan_stats, monthly_scan_stats,
persistarea motorului în istoric, banda cu 4 carduri și ScanStatsDialog.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    import database as db
    tmp_db = tmp_path / "cimitir_test.db"
    monkeypatch.setattr(db, "DB_PATH", str(tmp_db))
    db.init_db()
    from ai_scanare.db_scans import init_scan_history
    init_scan_history()
    return db


@pytest.fixture()
def silent_msgbox(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)


def _insert_row(db_mod, created_at: str, extracted: dict,
                doc_type: str = "buletin") -> None:
    conn = db_mod.get_connection()
    try:
        conn.execute(
            "INSERT INTO ai_scan_history (created_at, document_type, filename, "
            "file_path, status, extracted_json) VALUES (?, ?, ?, ?, ?, ?)",
            (created_at, doc_type, "f.jpg", "", "processed",
             json.dumps(extracted, ensure_ascii=False)),
        )
    finally:
        conn.close()


# =========================================================================
# 1. scan_stats
# =========================================================================
class TestScanStats:
    def test_mixed_engines(self, isolated_db):
        from ai_scanare.db_scans import scan_stats
        now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        for _ in range(3):
            _insert_row(isolated_db, now, {"engine": "gemini"})
        _insert_row(isolated_db, now, {"engine": "tesseract"})
        _insert_row(isolated_db, now, {"fields": {"nume": "X"}})  # no engine
        _insert_row(isolated_db, now, {})                         # no engine

        s = scan_stats()
        assert s["total"] == 6
        assert s["ai"] == 3
        assert s["offline"] == 1
        assert s["unknown"] == 2
        # procentele DOAR pe cunoscute (3 + 1 = 4)
        assert s["ai_pct"] == 75.0
        assert s["offline_pct"] == 25.0

    def test_this_month_ignores_last_month(self, isolated_db):
        from ai_scanare.db_scans import scan_stats
        now = datetime.now()
        this_month = now.strftime("%d.%m.%Y %H:%M:%S")
        # o dată din luna trecută (asigură decrement corect peste ianuarie)
        prev_year, prev_month = (now.year, now.month - 1)
        if prev_month == 0:
            prev_year, prev_month = prev_year - 1, 12
        prev = f"15.{prev_month:02d}.{prev_year} 10:00:00"

        _insert_row(isolated_db, this_month, {"engine": "gemini"})
        _insert_row(isolated_db, this_month, {"engine": "gemini"})
        _insert_row(isolated_db, prev, {"engine": "gemini"})

        s = scan_stats()
        assert s["total"] == 3
        assert s["this_month"] == 2

    def test_empty_db_no_zero_division(self, isolated_db):
        from ai_scanare.db_scans import scan_stats
        s = scan_stats()
        assert s["total"] == 0
        assert s["this_month"] == 0
        assert s["ai_pct"] is None
        assert s["offline_pct"] is None

    def test_corrupt_json_bucketed_as_unknown(self, isolated_db):
        from ai_scanare.db_scans import scan_stats
        # inject invalid JSON string directly
        conn = isolated_db.get_connection()
        try:
            conn.execute(
                "INSERT INTO ai_scan_history (created_at, document_type, status, "
                "extracted_json) VALUES (?, ?, ?, ?)",
                (datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
                 "buletin", "processed", "{not-json"),
            )
        finally:
            conn.close()
        s = scan_stats()
        assert s["total"] == 1
        assert s["unknown"] == 1


# =========================================================================
# 2. monthly_scan_stats
# =========================================================================
class TestMonthlyScanStats:
    def test_shape_and_order(self, isolated_db):
        from ai_scanare.db_scans import monthly_scan_stats
        months = monthly_scan_stats(12)
        assert len(months) == 12
        now = datetime.now()
        # ultima e luna curentă
        assert (months[-1]["year"], months[-1]["month"]) == (now.year, now.month)
        # de la cea mai veche la cea mai nouă
        for a, b in zip(months, months[1:]):
            assert (a["year"], a["month"]) < (b["year"], b["month"])
        for m in months:
            assert set(m.keys()) >= {"year", "month", "label",
                                     "total", "ai", "offline", "unknown"}
            assert m["label"] == f"{m['month']:02d}.{m['year'] % 100:02d}"

    def test_out_of_range_ignored_and_sum_matches(self, isolated_db):
        from ai_scanare.db_scans import monthly_scan_stats
        now = datetime.now()
        cur = now.strftime("%d.%m.%Y %H:%M:%S")
        _insert_row(isolated_db, cur, {"engine": "gemini"})
        _insert_row(isolated_db, cur, {"engine": "gemini"})
        _insert_row(isolated_db, cur, {"engine": "tesseract"})
        _insert_row(isolated_db, cur, {})
        # unul foarte vechi (5 ani în urmă)
        _insert_row(isolated_db, f"01.01.{now.year - 5} 10:00:00", {"engine": "gemini"})

        months = monthly_scan_stats(12)
        last = months[-1]
        assert last["total"] == 4
        assert last["ai"] == 2
        assert last["offline"] == 1
        assert last["unknown"] == 1
        for m in months:
            assert m["ai"] + m["offline"] + m["unknown"] == m["total"]

    def test_accepts_multiple_date_formats(self, isolated_db):
        from ai_scanare.db_scans import _parse_created_at
        assert _parse_created_at("15.06.2025 10:20:30") is not None
        assert _parse_created_at("15.06.2025 10:20") is not None
        assert _parse_created_at("15.06.2025") is not None
        # corrupt
        assert _parse_created_at("garbage") is None
        assert _parse_created_at("") is None


# =========================================================================
# 3. Motorul se salvează în istoric (raw_text + engine)
# =========================================================================
class TestEnginePersistedInHistory:
    def test_save_from_data_persists_engine_and_raw_text(
        self, qapp, isolated_db, silent_msgbox, monkeypatch,
    ):
        from ai_scanare import scan_window as sw
        from ai_scanare.ai_client import DocumentType
        from ai_scanare.db_scans import list_scans

        win = sw.AIScanareWindow.__new__(sw.AIScanareWindow)
        win.actions = None
        win.reload_history = lambda: None
        win.parent = lambda: None

        data = {"nume": "TEST_ENG", "prenume": "ION",
                "cnp": "1900101987654", "adresa": "Str."}
        full = {"engine": "gemini", "raw_text": "abc", "fields": {}}
        win._save_from_data(DocumentType.BULETIN, data, None, "archive", full)

        rows = list_scans("buletin")
        assert rows, "Nu s-a salvat nimic în istoric"
        extracted = rows[0]["extracted"]
        assert extracted.get("engine") == "gemini"
        assert extracted.get("raw_text") == "abc"

    def test_handle_adeverinta_adresa_persists_engine(
        self, qapp, isolated_db, silent_msgbox, monkeypatch,
    ):
        from ai_scanare import scan_window as sw
        from ai_scanare.ai_client import DocumentType
        from ai_scanare.db_scans import list_scans

        # ensure a concesionar exists with the CNP, to avoid QMessageBox flow
        import database as db
        db.save_concesionar({
            "nume": "EXIST", "prenume": "USER", "cnp": "1800101112233",
            "bi_ci": "AA 111", "adresa": "vechi", "telefon": "",
            "email": "", "observatii": "",
        })

        win = sw.AIScanareWindow.__new__(sw.AIScanareWindow)
        win.actions = None
        win.reload_history = lambda: None
        win.parent = lambda: None

        data = {"nume": "EXIST", "prenume": "USER",
                "cnp": "1800101112233", "adresa": "Adresa Noua 42"}
        win._handle_adeverinta_adresa(data, None, "archive", engine="gemini")

        rows = list_scans(DocumentType.ADEVERINTA_ADRESA)
        assert rows
        assert rows[0]["extracted"].get("engine") == "gemini"


# =========================================================================
# 4. Banda cu 4 carduri + reload nu duplică
# =========================================================================
class TestStatsRow:
    @pytest.fixture()
    def no_health(self, monkeypatch):
        from ai_scanare import scan_window as sw
        monkeypatch.setattr(sw.AIScanareWindow, "_start_key_health_check",
                            lambda self: None)

    @pytest.fixture()
    def window(self, qapp, isolated_db, silent_msgbox, no_health):
        from ai_scanare import scan_window as sw
        w = sw.AIScanareWindow(parent=None, actions=None)
        yield w
        w.close()

    def test_four_cards_with_expected_ids(self, window):
        from PySide6.QtWidgets import QFrame
        ids = {c.objectName() for c in window.findChildren(QFrame)
               if c.objectName().startswith("stat-")}
        assert {"stat-total", "stat-this-month",
                "stat-ai-pct", "stat-offline-pct"} <= ids
        # 4 în stats_row exact
        assert window.stats_row.count() == 4

    def test_reload_history_does_not_duplicate(self, window):
        for _ in range(3):
            window.reload_history()
        assert window.stats_row.count() == 4

    def test_btn_scan_stats_present(self, window):
        assert hasattr(window, "btn_stats")
        assert window.btn_stats.objectName() == "btn-scan-stats"


# =========================================================================
# 5. ScanStatsDialog + MonthlyScanChart (paintEvent nu crapă)
# =========================================================================
class TestScanStatsDialog:
    def test_dialog_renders(self, qapp, isolated_db):
        from ai_scanare.scan_window import ScanStatsDialog, MonthlyScanChart
        from PySide6.QtWidgets import QFrame, QTableWidget
        dlg = ScanStatsDialog(None)
        cards = [c for c in dlg.findChildren(QFrame)
                 if c.objectName().startswith("stat-")]
        assert len(cards) == 4
        table = dlg.findChild(QTableWidget, "monthly-stats-table")
        assert table is not None
        assert table.rowCount() == 12
        # cea mai nouă lună pe primul rând (tabelul e ordonat descendent)
        now = datetime.now()
        first_label = table.item(0, 0).text()
        assert first_label == f"{now.month:02d}.{now.year % 100:02d}"
        # chart există
        chart = dlg.findChild(MonthlyScanChart)
        assert chart is not None
        dlg.close()

    def test_chart_paints_on_empty_db(self, qapp, isolated_db):
        from ai_scanare.scan_window import MonthlyScanChart
        from ai_scanare.db_scans import monthly_scan_stats
        chart = MonthlyScanChart(monthly_scan_stats(12))
        chart.resize(600, 220)
        chart.grab()  # force paintEvent — nu trebuie să crape la /0

    def test_chart_paints_with_single_total(self, qapp, isolated_db):
        from ai_scanare.scan_window import MonthlyScanChart
        from ai_scanare.db_scans import monthly_scan_stats
        _insert_row(isolated_db,
                    datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
                    {"engine": "gemini"})
        chart = MonthlyScanChart(monthly_scan_stats(12))
        chart.resize(600, 220)
        chart.grab()


# =========================================================================
# 6. ScanDetailsDialog acceptă și cheia 'raw' (compat înregistrări vechi)
# =========================================================================
class TestScanDetailsRawCompat:
    def test_shows_raw_key(self, qapp):
        from ai_scanare.scan_window import ScanDetailsDialog
        from PySide6.QtWidgets import QPlainTextEdit
        dlg = ScanDetailsDialog(None, {
            "document_type": "buletin",
            "extracted": {"fields": {"nume": "X"}, "raw": "TEXT VECHI"},
        })
        boxes = [w for w in dlg.findChildren(QPlainTextEdit)
                 if w.objectName() == "scan-details-raw-text"]
        assert boxes and "TEXT VECHI" in boxes[0].toPlainText()
        dlg.close()

    def test_shows_raw_text_key(self, qapp):
        from ai_scanare.scan_window import ScanDetailsDialog
        from PySide6.QtWidgets import QPlainTextEdit
        dlg = ScanDetailsDialog(None, {
            "document_type": "buletin",
            "extracted": {"fields": {"nume": "X"}, "raw_text": "TEXT NOU"},
        })
        boxes = [w for w in dlg.findChildren(QPlainTextEdit)
                 if w.objectName() == "scan-details-raw-text"]
        assert boxes and "TEXT NOU" in boxes[0].toPlainText()
        dlg.close()
