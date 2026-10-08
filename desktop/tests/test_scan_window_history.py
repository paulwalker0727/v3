"""Teste pentru PUNCTUL 4 — istoric scanări:
  (a) etichete corecte în coloana Tip + filtru pentru adeverință adresă
  (b) buton „👁" cu ScanDetailsDialog
  (c) buton „➜" cu _open_entity spre concesionar/inhumat
  (d) export CSV/Excel

+ regresie: fereastra completă se instanțiază fără excepție, iar _find_main_actions
  și _save_from_data(BULETIN) încă funcționează după refactor.

Aplicație DESKTOP PySide6, headless: QT_QPA_PLATFORM=offscreen. DB izolat în tmp.
"""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ---- Qt bootstrap ---------------------------------------------------------
@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    return app


# ---- DB izolat ------------------------------------------------------------
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


@pytest.fixture()
def no_health_check(monkeypatch):
    """Blochează verificarea automată a cheii la construcția ferestrei."""
    from ai_scanare import scan_window as sw
    monkeypatch.setattr(sw.AIScanareWindow, "_start_key_health_check", lambda self: None)


@pytest.fixture()
def full_window(qapp, isolated_db, silent_msgbox, no_health_check):
    """Instanțiază AIScanareWindow complet (cu __init__)."""
    from ai_scanare import scan_window as sw
    win = sw.AIScanareWindow(parent=None, actions=None)
    yield win
    win.close()


# =========================================================================
# 1. doc_label — mapping simplu
# =========================================================================
class TestDocLabel:
    def test_labels_for_known_types(self):
        from ai_scanare.scan_window import doc_label
        assert doc_label("buletin") == "Buletin CI"
        assert doc_label("certificat_deces") == "Cert. deces"
        assert doc_label("adeverinta_adresa") == "Adeverință adresă"

    def test_label_for_none_or_unknown(self):
        from ai_scanare.scan_window import doc_label
        assert doc_label(None) == "—"
        assert doc_label("") == "—"
        # tip necunoscut => îl întoarce așa cum e (nu 'Cert. deces')
        assert doc_label("something_else") == "something_else"


# =========================================================================
# 2. Coloana Tip afișează corect pentru toate cele 3 tipuri (BUG a)
# =========================================================================
class TestHistoryTypeColumn:
    def test_type_column_shows_correct_label_per_type(self, full_window, isolated_db):
        from ai_scanare.db_scans import save_scan

        save_scan("buletin", "b.jpg", None,
                  {"fields": {"nume": "ION"}}, "processed")
        save_scan("certificat_deces", "c.jpg", None,
                  {"fields": {"nume": "MARIA"}}, "processed")
        save_scan("adeverinta_adresa", "a.jpg", None,
                  {"fields": {"nume": "GEORGE"}}, "processed")

        full_window.reload_history()
        labels = {}
        for row in range(full_window.table.rowCount()):
            filename = full_window.table.item(row, 2).text()
            tip = full_window.table.item(row, 1).text()
            labels[filename] = tip

        assert labels["b.jpg"] == "Buletin CI"
        assert labels["c.jpg"] == "Cert. deces"
        # BUG PRINCIPAL: adeverința NU mai apare ca 'Cert. deces'
        assert labels["a.jpg"] == "Adeverință adresă"
        assert labels["a.jpg"] != "Cert. deces"

    def test_filter_type_has_four_options(self, full_window):
        combo = full_window.filter_type
        assert combo.count() == 4
        expected = [
            ("Toate tipurile", ""),
            ("Buletin CI", "buletin"),
            ("Cert. deces", "certificat_deces"),
            ("Adeverință adresă", "adeverinta_adresa"),
        ]
        for i, (text, data) in enumerate(expected):
            assert combo.itemText(i) == text, f"index {i}"
            assert combo.itemData(i) == data, f"index {i} data"

    def test_filter_by_adeverinta_isolates_rows(self, full_window):
        from ai_scanare.db_scans import save_scan
        save_scan("buletin", "b.jpg", None, {"fields": {}}, "processed")
        save_scan("adeverinta_adresa", "a1.jpg", None, {"fields": {}}, "processed")
        save_scan("adeverinta_adresa", "a2.jpg", None, {"fields": {}}, "processed")

        # selectăm 'Adeverință adresă' după data
        idx = full_window.filter_type.findData("adeverinta_adresa")
        assert idx >= 0
        full_window.filter_type.setCurrentIndex(idx)  # triggers reload_history

        assert full_window.table.rowCount() == 2
        filenames = {full_window.table.item(r, 2).text()
                     for r in range(full_window.table.rowCount())}
        assert filenames == {"a1.jpg", "a2.jpg"}


# =========================================================================
# 3. ScanDetailsDialog — populează câmpurile
# =========================================================================
class TestScanDetailsDialog:
    def test_populates_all_fields_with_labels_and_confidence(self, qapp):
        from ai_scanare.scan_window import ScanDetailsDialog

        scan = {
            "document_type": "buletin",
            "created_at": "01.01.2025",
            "filename": "test.jpg",
            "status": "processed",
            "extracted": {
                "fields": {
                    "nume": {"value": "POPESCU", "confidence": 0.95},
                    "cnp": {"value": "1900101123456", "confidence": 0.4},
                    "adresa": "Str. Test 1",
                },
                "raw_text": "text ocr complet",
            },
        }
        dlg = ScanDetailsDialog(None, scan)
        assert dlg.table.rowCount() == 3
        # sortăm rândurile după eticheta câmp
        rows = {}
        for r in range(dlg.table.rowCount()):
            rows[dlg.table.item(r, 0).text()] = (
                dlg.table.item(r, 1).text(),
                dlg.table.item(r, 2).text(),
            )
        assert rows["Nume"] == ("POPESCU", "95%")
        assert rows["CNP"] == ("1900101123456", "40%")
        assert rows["Adresă"] == ("Str. Test 1", "—")

        # raw text există și conține textul
        raw = dlg.findChild(type(dlg), "scan-details-raw-text") or dlg.findChildren(
            __import__("PySide6.QtWidgets", fromlist=["QPlainTextEdit"]).QPlainTextEdit
        )
        # mai simplu: findChildren by objectName
        from PySide6.QtWidgets import QPlainTextEdit
        boxes = [w for w in dlg.findChildren(QPlainTextEdit)
                 if w.objectName() == "scan-details-raw-text"]
        assert len(boxes) == 1
        assert "text ocr complet" in boxes[0].toPlainText()
        dlg.close()

    def test_empty_extracted_shows_message(self, qapp):
        from ai_scanare.scan_window import ScanDetailsDialog

        dlg = ScanDetailsDialog(None, {"document_type": "buletin",
                                        "extracted": {}, "raw_text": ""})
        assert dlg.table.rowCount() == 0
        # există label-ul de gol
        from PySide6.QtWidgets import QLabel
        labels = [w.text() for w in dlg.findChildren(QLabel)]
        assert any("Nu există câmpuri" in t for t in labels)
        dlg.close()


# =========================================================================
# 4. _show_scan_details — cu id invalid și cu id valid
# =========================================================================
class TestShowScanDetails:
    def test_invalid_id_warns_without_exception(self, full_window):
        # nu trebuie să crape; QMessageBox.warning e deja stubbed
        full_window._show_scan_details(99999)

    def test_valid_id_opens_dialog(self, full_window, monkeypatch):
        from ai_scanare.db_scans import save_scan
        from ai_scanare import scan_window as sw

        sid = save_scan("buletin", "x.jpg", None,
                        {"fields": {"nume": "TEST"}}, "processed")

        opened = {"count": 0}
        real_init = sw.ScanDetailsDialog.__init__

        def spy_init(self, parent, scan):
            opened["count"] += 1
            opened["scan_id"] = scan.get("id")
            real_init(self, parent, scan)

        monkeypatch.setattr(sw.ScanDetailsDialog, "__init__", spy_init)
        monkeypatch.setattr(sw.ScanDetailsDialog, "exec", lambda self: 0)

        full_window._show_scan_details(sid)
        assert opened["count"] == 1
        assert opened["scan_id"] == sid


# =========================================================================
# 5. _open_entity — concesionar / inhumat / edge cases
# =========================================================================
class TestOpenEntity:
    def test_none_or_empty_does_nothing(self, full_window):
        # nu trebuie să crape
        full_window._open_entity("", None)
        full_window._open_entity(None, None)
        full_window._open_entity("concesionar", None)
        full_window._open_entity("", 1)

    def test_missing_concesionar_warns(self, full_window):
        # id inexistent => warning, fără excepție
        full_window._open_entity("concesionar", 99999)

    def test_existing_concesionar_opens_dialog(self, full_window, monkeypatch, isolated_db):
        import database as db
        cid = db.save_concesionar({
            "nume": "POPESCU", "prenume": "ION", "cnp": "1900101123456",
            "bi_ci": "AB 123", "adresa": "Str.", "telefon": "", "email": "",
            "observatii": "",
        })
        assert cid

        import concesionari_window as cw
        opened = {}
        real_init = cw.AddConcesionarDialog.__init__

        def spy_init(self, parent, existing=None, actions=None):
            opened["existing"] = existing
            real_init(self, parent, existing=existing, actions=actions)

        monkeypatch.setattr(cw.AddConcesionarDialog, "__init__", spy_init)
        monkeypatch.setattr(cw.AddConcesionarDialog, "exec", lambda self: 0)

        full_window._open_entity("concesionar", cid)
        assert opened.get("existing") is not None
        assert opened["existing"].get("cnp") == "1900101123456"

    def test_existing_inhumat_opens_dialog(self, full_window, monkeypatch, isolated_db):
        import database as db

        # Trebuie mai întâi un loc de veci
        db.save_loc_metadata("LOC001", {"sector": "A", "rand": "1", "numar": "1"})
        iid = db.save_inhumat("LOC001", {
            "nume": "ION", "prenume": "MARIA", "cnp": "2900101123456",
            "data_nasterii": "", "data_deces": "", "sex": "", "cauza_deces": "",
            "profesie": "", "religie": "", "ultima_adresa": "", "observatii": "",
        })
        assert iid

        import add_inhumat_dialog as aid
        opened = {}
        real_init = aid.AddInhumatDialog.__init__

        def spy_init(self, parent, cod_loc, existing=None):
            opened["cod_loc"] = cod_loc
            opened["existing"] = existing
            real_init(self, parent, cod_loc, existing=existing)

        monkeypatch.setattr(aid.AddInhumatDialog, "__init__", spy_init)
        monkeypatch.setattr(aid.AddInhumatDialog, "exec", lambda self: 0)

        full_window._open_entity("inhumat", iid)
        assert opened.get("existing") is not None
        assert opened["cod_loc"] == "LOC001"

    def test_unknown_entity_type_shows_info(self, full_window):
        # nu trebuie să crape
        full_window._open_entity("something_else", 1)


class TestGotoEntityButtonState:
    def test_button_disabled_when_no_entity(self, full_window):
        from PySide6.QtWidgets import QPushButton
        from ai_scanare.db_scans import save_scan

        save_scan("buletin", "orphan.jpg", None, {"fields": {}}, "processed",
                  entity_type=None, entity_id=None)
        full_window.reload_history()

        # coloana 6 conține widget-ul cu acțiuni
        widget = full_window.table.cellWidget(0, 6)
        assert widget is not None
        goto = [b for b in widget.findChildren(QPushButton)
                if b.objectName() == "history-goto-entity"]
        assert len(goto) == 1
        assert goto[0].isEnabled() is False

    def test_button_enabled_when_entity_present(self, full_window):
        from PySide6.QtWidgets import QPushButton
        from ai_scanare.db_scans import save_scan

        save_scan("buletin", "with_entity.jpg", None, {"fields": {}}, "processed",
                  entity_type="concesionar", entity_id=42)
        full_window.reload_history()

        widget = full_window.table.cellWidget(0, 6)
        goto = [b for b in widget.findChildren(QPushButton)
                if b.objectName() == "history-goto-entity"]
        assert len(goto) == 1
        assert goto[0].isEnabled() is True


# =========================================================================
# 6. _export_history — CSV valid
# =========================================================================
class TestExportHistory:
    def test_empty_shows_info_and_no_file(self, full_window, tmp_path, monkeypatch):
        called = {"save": 0}
        from PySide6.QtWidgets import QFileDialog

        def fake_save(*a, **k):
            called["save"] += 1
            return (str(tmp_path / "x.csv"), "")

        monkeypatch.setattr(QFileDialog, "getSaveFileName", fake_save)

        # Fără scanări în DB => _history_rows va fi gol după reload
        full_window.reload_history()
        full_window._export_history()
        assert called["save"] == 0  # nici nu a ajuns la file dialog

    def test_export_writes_valid_csv(self, full_window, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from ai_scanare.db_scans import save_scan

        save_scan("adeverinta_adresa", "adev.jpg", "/path/adev.jpg",
                  {"fields": {"nume": "IONEȘCU", "prenume": "MĂRIA",
                              "cnp": "2900101123456", "adresa": "Str. Ștefan"}},
                  "processed", entity_type="concesionar", entity_id=7)
        save_scan("buletin", "bul.jpg", None,
                  {"fields": {"nume": "POPESCU", "prenume": "ION",
                              "cnp": "1900101123456"}},
                  "processed", entity_type="concesionar", entity_id=3)

        out_path = tmp_path / "export_test"  # fără .csv, să testăm auto-append
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            lambda *a, **k: (str(out_path), ""))
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)

        full_window.reload_history()
        full_window._export_history()

        final_path = Path(str(out_path) + ".csv")
        assert final_path.exists(), "Fișierul CSV nu a fost creat cu extensia .csv"

        # utf-8-sig: primul byte trebuie să fie BOM
        raw = final_path.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf"), "Lipsește BOM-ul UTF-8 pentru Excel"

        content = final_path.read_text(encoding="utf-8-sig")
        reader = list(csv.reader(content.splitlines(), delimiter=";"))
        headers = reader[0]
        expected_headers = ["Data", "Tip document", "Fișier", "Nume", "Prenume", "CNP",
                            "Status", "Entitate", "ID entitate", "Cale fișier"]
        assert headers == expected_headers

        # 2 rânduri de date
        assert len(reader) == 3

        # Găsim rândul cu adeverința și verificăm eticheta corectă
        adev_row = next((row for row in reader[1:] if row[2] == "adev.jpg"), None)
        assert adev_row is not None
        assert adev_row[1] == "Adeverință adresă"  # NU 'Cert. deces'
        assert adev_row[3] == "IONEȘCU"
        assert adev_row[4] == "MĂRIA"
        assert adev_row[5] == "2900101123456"
        # diacriticele intacte (fără mojibake)
        assert "Ș" in adev_row[3]
        assert "Ă" in adev_row[4]

        bul_row = next((row for row in reader[1:] if row[2] == "bul.jpg"), None)
        assert bul_row is not None
        assert bul_row[1] == "Buletin CI"


# =========================================================================
# 7. Regresie — fereastra completă se construiește cu toate butoanele noi
# =========================================================================
class TestFullWindowRegression:
    def test_window_instantiates_without_error(self, full_window):
        assert full_window is not None
        assert full_window.windowTitle().strip() != ""

    def test_all_new_buttons_present(self, full_window):
        from PySide6.QtWidgets import QPushButton
        expected_ids = {
            "history-export",
            "history-refresh",
            "btn-ai-fix",
            "btn-ai-settings",
            "ai-status-indicator",  # QLabel
            "history-filter-type",
            "history-filter-status",
            "history-filter-search",
        }
        found = {w.objectName() for w in full_window.findChildren(object)
                 if hasattr(w, "objectName") and w.objectName()}
        missing = expected_ids - found
        assert not missing, f"Lipsesc widget-urile: {missing}"

    def test_row_action_buttons_created(self, full_window):
        from PySide6.QtWidgets import QPushButton
        from ai_scanare.db_scans import save_scan

        save_scan("buletin", "row.jpg", None,
                  {"fields": {"nume": "X"}}, "processed",
                  entity_type="concesionar", entity_id=1)
        full_window.reload_history()
        widget = full_window.table.cellWidget(0, 6)
        assert widget is not None
        ids = {b.objectName() for b in widget.findChildren(QPushButton)}
        assert {"history-open-file", "history-details", "history-goto-entity",
                "history-rescan", "history-delete"} <= ids

    def test_find_main_actions_still_works(self, full_window):
        # regresie iterația anterioară
        assert hasattr(full_window, "_find_main_actions")
        assert full_window._find_main_actions() is None  # actions=None în fixture

    def test_save_from_data_buletin_still_persists(self, full_window, isolated_db):
        from ai_scanare.ai_client import DocumentType
        full_window._save_from_data(
            DocumentType.BULETIN,
            {"nume": "REGRESION", "prenume": "TEST", "cnp": "1990101999999",
             "serie": "ZZ", "numar": "111", "adresa": "Str. Regresie"},
            source_file="", file_action="delete",
            full_extracted={"fields": {}, "raw_text": ""},
        )
        conn = isolated_db.get_connection()
        try:
            rows = conn.execute(
                "SELECT nume FROM concesionari WHERE cnp = ?",
                ("1990101999999",),
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 1
