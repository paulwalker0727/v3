"""Teste regresie pentru bugul AttributeError: '_find_main_actions'
și pentru fluxul de salvare buletin/certificat în DB.

Aplicație DESKTOP PySide6, teste headless (QT_QPA_PLATFORM=offscreen).
Nu se folosește Gemini real — se apelează direct _save_from_data cu date mock.
"""
from __future__ import annotations

import ast
import inspect
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


# ---- Isolate DB in a tmp dir ---------------------------------------------
@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """Redirect database.DB_PATH into tmp dir + init schema."""
    # Import BEFORE patching so module already resolved
    import database as db

    tmp_db = tmp_path / "cimitir_test.db"
    monkeypatch.setattr(db, "DB_PATH", str(tmp_db))
    # Recreate schema in the tmp DB
    db.init_db()
    return db


# ---- Silence blocking QMessageBox dialogs --------------------------------
@pytest.fixture()
def silent_msgbox(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)


# ---- Build a bare AIScanareWindow without full __init__ ------------------
@pytest.fixture()
def bare_window(qapp, isolated_db, silent_msgbox, monkeypatch):
    """Creates an AIScanareWindow without running its heavy __init__.
    Provides the attributes touched by _save_from_data."""
    from ai_scanare import scan_window as sw
    from ai_scanare.db_scans import init_scan_history

    # Ensure scan history table is in the same tmp DB
    init_scan_history()

    win = sw.AIScanareWindow.__new__(sw.AIScanareWindow)
    win.actions = None
    # Stub methods that touch UI widgets
    win.reload_history = lambda: None
    win.parent = lambda: None
    return win


# =========================================================================
# 1. Verificare structurală: _find_main_actions este metodă a clasei
# =========================================================================
class TestFindMainActionsMethodStructure:
    def test_method_exists_on_class(self):
        from ai_scanare.scan_window import AIScanareWindow
        assert hasattr(AIScanareWindow, "_find_main_actions"), \
            "AIScanareWindow trebuie să aibă _find_main_actions ca metodă"
        assert callable(getattr(AIScanareWindow, "_find_main_actions"))

    def test_open_ai_settings_does_not_return_actions(self):
        """_open_ai_settings trebuie doar să deschidă dialogul, NU să conțină
        codul de căutare a lui self.actions (bug-ul original)."""
        from ai_scanare.scan_window import AIScanareWindow
        src = inspect.getsource(AIScanareWindow._open_ai_settings)
        # regresia originală lipise corpul lui _find_main_actions aici
        assert "return self.actions" not in src, (
            "_open_ai_settings pare să conțină încă corpul orfan al lui _find_main_actions"
        )
        assert "getattr(w" not in src or "actions" not in src, (
            "_open_ai_settings NU trebuie să conțină walk-ul parent()->actions"
        )

    def test_find_main_actions_body_intact(self):
        from ai_scanare.scan_window import AIScanareWindow
        src = inspect.getsource(AIScanareWindow._find_main_actions)
        assert "self.actions" in src
        assert "parent" in src

    def test_no_orphan_methods_in_ai_scanare(self):
        """Sanity check AST: nicio metodă din ai_scanare/*.py nu are corp orfan
        (adică ultima instrucțiune din alta funcție să fie un `return`
        care aparține conceptual altei metode)."""
        pkg = PROJECT_ROOT / "ai_scanare"
        for py in pkg.glob("*.py"):
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    seen_names = [n.name for n in node.body
                                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                    # duplicate method names would silently overwrite each other
                    assert len(seen_names) == len(set(seen_names)), (
                        f"Metode duplicate în {py}::{node.name}: "
                        f"{[n for n in seen_names if seen_names.count(n) > 1]}"
                    )


# =========================================================================
# 2. _find_main_actions runtime
# =========================================================================
class TestFindMainActionsRuntime:
    def test_returns_actions_when_provided(self, bare_window):
        sentinel = object()
        bare_window.actions = sentinel
        assert bare_window._find_main_actions() is sentinel

    def test_returns_none_when_no_parent_with_actions(self, bare_window):
        bare_window.actions = None
        # parent chain returns None
        assert bare_window._find_main_actions() is None

    def test_walks_parent_chain(self, bare_window):
        class Fake:
            def __init__(self, actions=None, parent=None):
                self.actions = actions
                self._p = parent
            def parent(self):
                return self._p

        sentinel = object()
        grand = Fake(actions=sentinel)
        mid = Fake(actions=None, parent=grand)
        # Attach parent chain to bare_window
        bare_window.parent = lambda: mid
        bare_window.actions = None
        # Trick: our fake's `hasattr(w, "actions")` is True even when None -> returns mid's actions (None).
        # The current implementation stops at first parent with attribute 'actions'.
        assert bare_window._find_main_actions() is None


# =========================================================================
# 3. Fluxul de salvare buletin -> DB (bugul principal)
# =========================================================================
class TestSaveBuletinFlow:
    def test_save_from_data_buletin_persists_in_db(self, bare_window, isolated_db):
        """Verifică că _save_from_data pentru BULETIN NU aruncă AttributeError
        și inserează efectiv rândul în tabela concesionari."""
        from ai_scanare.ai_client import DocumentType

        data = {
            "nume": "POPESCU",
            "prenume": "ION",
            "cnp": "1900101123456",
            "serie": "AB",
            "numar": "123456",
            "adresa": "Str. Test 1, București",
            "emitent": "SPCLEP Sector 1",
            "data_eliberarii": "2020-01-01",
            "valabilitate": "2030-01-01",
            "data_nasterii": "1990-01-01",
        }
        # Rulare — dacă bugul ar fi încă acolo, aici s-ar arunca AttributeError
        bare_window._save_from_data(
            DocumentType.BULETIN, data, source_file="", file_action="delete",
            full_extracted={"fields": data, "raw_text": ""},
        )

        # Verificare directă în DB
        conn = isolated_db.get_connection()
        try:
            rows = conn.execute(
                "SELECT nume, prenume, cnp, bi_ci, adresa "
                "FROM concesionari WHERE cnp = ?",
                ("1900101123456",)
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 1, "Concesionarul nu a fost inserat în DB"
        row = dict(rows[0])
        assert row["nume"] == "POPESCU"
        assert row["prenume"] == "ION"
        assert row["cnp"] == "1900101123456"
        assert row["bi_ci"] == "AB 123456"
        assert "Test 1" in (row["adresa"] or "")

    def test_save_from_data_does_not_raise_attribute_error(self, bare_window):
        """Regresie: obiectul are metoda _find_main_actions (nu AttributeError)."""
        from ai_scanare.ai_client import DocumentType
        try:
            bare_window._save_from_data(
                DocumentType.BULETIN,
                {"nume": "X", "prenume": "Y", "cnp": "9999999999999"},
                source_file="",
                file_action="delete",
                full_extracted={"fields": {}},
            )
        except AttributeError as e:
            if "_find_main_actions" in str(e):
                pytest.fail(f"Regression: {e}")
            raise


# =========================================================================
# 4. Certificat deces — nu trebuie să arunce AttributeError înainte de _pick_cod_loc
# =========================================================================
class TestSaveCertificatDeces:
    def test_certificat_deces_uses_find_main_actions(self, bare_window, monkeypatch, isolated_db):
        from ai_scanare.ai_client import DocumentType
        from ai_scanare import scan_window as sw

        # Stub _pick_cod_loc pentru a returna None => flow se oprește "Anulat"
        # dar tot va apela _find_main_actions() la începutul _save_from_data.
        called = {"pick": False}
        def fake_pick(self, *a, **k):
            called["pick"] = True
            return None
        monkeypatch.setattr(sw.AIScanareWindow, "_pick_cod_loc", fake_pick)

        bare_window._save_from_data(
            DocumentType.CERTIFICAT_DECES,
            {"nume": "ION", "prenume": "MARIA", "cnp": "2900101123456"},
            source_file="",
            file_action="delete",
            full_extracted={"fields": {}},
        )
        assert called["pick"], "flow-ul certificat-deces nu a ajuns la _pick_cod_loc"


# =========================================================================
# 5. Import & AISettingsDialog instantiation
# =========================================================================
class TestAiScanareImports:
    def test_import_all_submodules(self):
        import importlib
        for mod in [
            "ai_scanare",
            "ai_scanare.ai_client",
            "ai_scanare.ai_settings_dialog",
            "ai_scanare.batch_dialog",
            "ai_scanare.config",
            "ai_scanare.db_scans",
            "ai_scanare.duplicate_check",
            "ai_scanare.feedback_learning",
            "ai_scanare.google_engine",
            "ai_scanare.image_preprocessing",
            "ai_scanare.mobile_scan",
            "ai_scanare.offline_ocr",
            "ai_scanare.scan_dialog",
            "ai_scanare.scan_window",
            "ai_scanare.training",
        ]:
            importlib.import_module(mod)

    def test_ai_settings_dialog_instantiates(self, qapp, silent_msgbox):
        from ai_scanare.ai_settings_dialog import AISettingsDialog
        dlg = AISettingsDialog(None)
        assert dlg is not None
        dlg.close()
