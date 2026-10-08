"""Teste pentru pachetul de build Windows (spec PyInstaller, script bat/iss/yml,
iconița nouă și integrarea app_icon în theme.py / launcher.py).

Nu construiește un .exe — validează doar corectitudinea configurațiilor și
faptul că funcționalitatea existentă nu s-a rupt.
"""
from __future__ import annotations

import os
import re
import sys
import struct
import types
import pytest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SPEC = PROJECT_ROOT / "cimitir.spec"
ICON = PROJECT_ROOT / "icon.ico"
LAUNCHER = PROJECT_ROOT / "launcher.py"
INSTALLER_DIR = PROJECT_ROOT / "installer"
BAT = PROJECT_ROOT / "build_exe.bat"
ISS = INSTALLER_DIR / "cimitir_setup.iss"
VER_FILE = INSTALLER_DIR / "file_version_info.txt"
WORKFLOW = Path("/app/.github/workflows/build-windows.yml")


# ============= FIXTURES =============

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    return app


# ============= 1. THEME.APP_ICON =============

class TestAppIcon:
    def test_returns_non_null_when_icon_exists(self, qapp):
        import theme
        icon = theme.app_icon()
        assert icon is not None
        assert not icon.isNull(), "app_icon() ar trebui să întoarcă un QIcon nevid"

    def test_returns_null_when_icon_missing(self, qapp, monkeypatch):
        import theme
        # Forțează os.path.exists să întoarcă mereu False în app_icon
        real_exists = os.path.exists
        monkeypatch.setattr(theme.os.path if hasattr(theme, "os") else os.path,
                            "exists", lambda p: False)
        # Alternativă: monkeypatch la os.path.exists global folosit înauntru
        import os as _os
        monkeypatch.setattr(_os.path, "exists", lambda p: False)
        icon = theme.app_icon()
        assert icon is not None
        assert icon.isNull(), "fără fișier, app_icon() ar trebui să întoarcă QIcon gol"

    def test_respects_meipass(self, qapp, tmp_path, monkeypatch):
        import shutil
        import theme
        # Copiază iconița reală într-un tmp_path și simulează sys._MEIPASS
        dest = tmp_path / "icon.ico"
        shutil.copy(ICON, dest)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        icon = theme.app_icon()
        assert not icon.isNull()


# ============= 2. APPLY_THEME =============

class TestApplyTheme:
    def test_sets_window_icon(self, qapp):
        import theme
        # reset icon
        from PySide6.QtGui import QIcon
        qapp.setWindowIcon(QIcon())
        assert qapp.windowIcon().isNull()

        theme.apply_theme(qapp, "dark")
        assert not qapp.windowIcon().isNull()

        expected_sizes = [(16, 16), (24, 24), (32, 32), (48, 48),
                          (64, 64), (128, 128), (256, 256)]
        available = qapp.windowIcon().availableSizes()
        avail_tuples = {(s.width(), s.height()) for s in available}
        for sz in expected_sizes:
            assert sz in avail_tuples, f"Lipsește dimensiunea {sz} în iconiță; are {avail_tuples}"

    def test_keeps_font_and_stylesheet(self, qapp):
        import theme
        theme.apply_theme(qapp, "dark")
        assert qapp.font().family().lower().startswith(("segoe", "sans", "dejavu", "liberation"))
        qss = qapp.styleSheet()
        assert "QPushButton" in qss
        assert "QLineEdit" in qss


# ============= 3. ICON.ICO VALIDATION =============

class TestIconFile:
    def test_size_over_10kb(self):
        assert ICON.exists()
        assert ICON.stat().st_size > 10_000, f"icon.ico prea mic: {ICON.stat().st_size} bytes"

    def test_ico_multisize(self):
        from PIL import Image
        with Image.open(ICON) as im:
            assert im.format == "ICO"
            sizes = im.info.get("sizes", set())
            for expected in [(16, 16), (32, 32), (48, 48), (256, 256)]:
                assert expected in sizes, f"Lipsește dimensiunea {expected} în {sizes}"


# ============= 4. LAUNCHER MAIN — nu crapă din cauza iconiței =============

class TestLauncherMain:
    def test_launcher_imports(self):
        import launcher
        assert hasattr(launcher, "main")
        assert hasattr(launcher, "LauncherWindow")

    def test_main_survives_icon_exception(self, qapp, monkeypatch):
        import launcher
        import theme

        # Face ca app_icon să arunce excepție
        def _boom():
            raise RuntimeError("icon boom")

        monkeypatch.setattr(theme, "app_icon", _boom)

        # Fake db to avoid touching real state
        monkeypatch.setattr(launcher.db, "init_db", lambda: None)
        monkeypatch.setattr(launcher.db, "get_setting", lambda k: None)
        monkeypatch.setattr(launcher.db, "set_setting", lambda k, v: None)

        # Prevent event loop & window show
        monkeypatch.setattr(launcher.LauncherWindow, "show", lambda self: None)
        monkeypatch.setattr(sys, "exit", lambda *a, **k: None)
        monkeypatch.setattr(launcher.QApplication, "exec", lambda self: 0)
        # Reuse existing QApplication instance instead of creating a new one
        monkeypatch.setattr(launcher, "QApplication",
                            lambda argv: qapp)

        # Trebuie să nu propage excepția
        launcher.main()


# ============= 5. CIMITIR.SPEC EVALUATION =============

def _eval_spec(onefile: str, slim: str, monkeypatch=None):
    """Evaluate cimitir.spec with stubbed PyInstaller primitives.
    Returns dict with keys: analysis_kwargs, exe_kwargs, has_collect, script.
    """
    captured = {"has_collect": False}

    class _Analysis:
        def __init__(self, scripts, **kwargs):
            captured["scripts"] = scripts
            captured["analysis_kwargs"] = kwargs
            self.pure = "PURE"
            self.scripts = "SCRIPTS"
            self.binaries = "BINARIES"
            self.datas = "DATAS"

    class _PYZ:
        def __init__(self, pure): self.pure = pure

    class _EXE:
        def __init__(self, *args, **kwargs):
            captured["exe_args"] = args
            captured["exe_kwargs"] = kwargs

    class _COLLECT:
        def __init__(self, *args, **kwargs):
            captured["has_collect"] = True
            captured["collect_kwargs"] = kwargs

    env = os.environ.copy()
    os.environ["CIMITIR_ONEFILE"] = onefile
    os.environ["CIMITIR_SLIM"] = slim
    try:
        code = SPEC.read_text(encoding="utf-8")
        ns = {
            "__name__": "__main__",
            "SPECPATH": str(PROJECT_ROOT),
            "Analysis": _Analysis,
            "PYZ": _PYZ,
            "EXE": _EXE,
            "COLLECT": _COLLECT,
        }
        exec(compile(code, str(SPEC), "exec"), ns)
        captured["ns"] = ns
        return captured
    finally:
        os.environ.clear()
        os.environ.update(env)


class TestSpec:
    @pytest.mark.parametrize("onefile,slim", [
        ("0", "0"), ("1", "0"), ("0", "1"), ("1", "1"),
    ])
    def test_all_env_combinations(self, onefile, slim):
        cap = _eval_spec(onefile, slim)
        akw = cap["analysis_kwargs"]
        # entry script
        scripts = cap["scripts"]
        assert any(str(s).endswith("launcher.py") for s in scripts)

        # datas: fișierele referite există efectiv
        datas = akw["datas"]
        srcs = [d[0] for d in datas]
        assert any(s.endswith("icon.ico") for s in srcs)
        assert any(s.endswith("LICENSE.txt") for s in srcs)
        assert any(s.endswith("fonts") for s in srcs)
        assert any(s.endswith("templates") for s in srcs)
        assert not any(s.endswith("web_build") for s in srcs)
        assert any(s.endswith("docs") for s in srcs)
        for src, _ in datas:
            assert os.path.exists(src), f"Data source missing: {src}"

        # hiddenimports
        hi = akw["hiddenimports"]
        assert "PySide6.QtMultimedia" in hi
        assert "main" in hi
        assert "web_server" in hi
        assert "uvicorn.protocols.http.auto" in hi
        assert any(x.startswith("google.genai") for x in hi)
        assert any(x.startswith("ai_scanare") for x in hi)
        assert any(x.startswith("license") for x in hi)
        assert any(x.startswith("security") for x in hi)

        # excludes
        exc = akw["excludes"]
        assert "emergentintegrations" in exc
        assert "litellm" in exc
        assert "PySide6.QtWebEngineCore" in exc
        assert "PySide6.QtQml" in exc
        assert "PySide6.QtQuick" in exc

        # SLIM behavior
        if slim == "1":
            assert "cv2" not in hi
            assert "cv2" in exc
        else:
            assert "cv2" in hi

        # ONEFILE behavior
        if onefile == "1":
            assert cap["has_collect"] is False
        else:
            assert cap["has_collect"] is True


# ============= 6. OFFLINE OCR — graceful fără cv2 =============

class TestOfflineOcrGracefulDegradation:
    def test_ocr_uses_original_when_preprocess_fails(self, monkeypatch, tmp_path):
        """Simulează eșecul preprocess_for_ocr — trebuie prins de try/except."""
        from ai_scanare import offline_ocr
        from ai_scanare import image_preprocessing

        def boom(*a, **kw):
            raise ImportError("cv2 indisponibil")

        monkeypatch.setattr(image_preprocessing, "preprocess_for_ocr", boom)

        # Creează o imagine mică validă pe disc
        from PIL import Image
        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 50), "white").save(img_path)

        # Mock tesseract wrappers, ca să nu depindem de tesseract instalat
        monkeypatch.setattr(offline_ocr, "has_romanian", lambda: False)

        called = {"n": 0}
        def fake_run(img, lang, psm):
            called["n"] += 1
            assert img is not None, "OCR trebuie chemat cu o imagine (originala)"
            return ("Nume: TEST\n", 0.5)

        monkeypatch.setattr(offline_ocr, "_run_tesseract", fake_run)

        text, conf = offline_ocr._ocr_image(str(img_path))
        assert called["n"] > 0
        assert "TEST" in text


# ============= 7. BUILD FILES SYNTAX =============

class TestWorkflowYaml:
    def test_valid_yaml(self):
        import yaml
        data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        assert "jobs" in data
        assert "build-windows" in data["jobs"]
        assert data["jobs"]["build-windows"]["runs-on"] == "windows-latest"

    def test_triggers_are_tags_and_dispatch_only(self):
        import yaml
        data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        # YAML loads "on" as boolean True — normalize
        on = data.get(True, data.get("on"))
        assert on is not None
        assert "push" in on
        assert "workflow_dispatch" in on
        # push doar pe tag-uri, NU pe branch
        push = on["push"]
        assert "tags" in push
        assert "branches" not in push

    def test_has_build_and_upload_steps(self):
        content = WORKFLOW.read_text(encoding="utf-8")
        assert "pyinstaller" in content.lower()
        assert "CIMITIR_ONEFILE" in content
        assert "ISCC.exe" in content
        assert "upload-artifact" in content


class TestInnoSetupScript:
    def test_appid_guid_valid(self):
        text = ISS.read_text(encoding="utf-8")
        m = re.search(r"AppId=\{\{([0-9A-Fa-f\-]+)", text)
        assert m, "AppId nu a fost găsit"
        guid = m.group(1)
        # 8-4-4-4-12
        parts = guid.split("-")
        assert len(parts) == 5
        assert [len(p) for p in parts] == [8, 4, 4, 4, 12]
        for p in parts:
            int(p, 16)  # validate hex

    def test_required_sections(self):
        text = ISS.read_text(encoding="utf-8")
        for section in ["[Setup]", "[Files]", "[Icons]", "[Run]"]:
            assert section in text

    def test_references_exist(self):
        text = ISS.read_text(encoding="utf-8")
        # Referințele ..\icon.ico și ..\LICENSE.txt
        assert "..\\icon.ico" in text
        assert "..\\LICENSE.txt" in text
        assert (INSTALLER_DIR / ".." / "icon.ico").resolve().exists()
        assert (INSTALLER_DIR / ".." / "LICENSE.txt").resolve().exists()
        # SourceDir ..\dist\Cimitir - dist e generat la build, dar referința să existe
        assert "..\\dist\\Cimitir" in text or "SourceDir" in text


class TestBuildBat:
    def test_crlf_line_endings(self):
        data = BAT.read_bytes()
        assert b"\r\n" in data
        # Trebuie ca majoritatea liniilor să fie CRLF
        lf_only = data.count(b"\n") - data.count(b"\r\n")
        assert lf_only < 5, f"prea multe linii LF-only ({lf_only})"

    def test_all_call_labels_exist(self):
        text = BAT.read_text(encoding="utf-8", errors="replace")
        called = set(re.findall(r"call\s+:(\w+)", text, re.IGNORECASE))
        defined = set(re.findall(r"^:(\w+)", text, re.MULTILINE))
        missing = called - defined
        assert not missing, f"Etichete apelate dar nedefinite: {missing}"
        # Expected labels
        for lbl in ["read_version", "find_signtool", "sign_files",
                    "find_iscc", "sign_pfx", "sign_store", "sign_no_tool"]:
            assert lbl in defined, f"Lipsește eticheta :{lbl}"

    def test_signing_support(self):
        text = BAT.read_text(encoding="utf-8", errors="replace")
        assert "CIMITIR_CERT" in text
        assert "CIMITIR_CERT_SUBJECT" in text


class TestFileVersionInfo:
    def test_valid_vsversioninfo(self):
        # Modulul original din PyInstaller depinde de win32api (doar Windows),
        # așa că folosim stub-uri care păstrează validarea sintactică + structurală.
        content = VER_FILE.read_text(encoding="utf-8")

        captured = {}

        class _Node:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs
                captured.setdefault(type(self).__name__, []).append(self)

        class VSVersionInfo(_Node): pass
        class FixedFileInfo(_Node): pass
        class StringFileInfo(_Node): pass
        class StringTable(_Node): pass
        class StringStruct(_Node): pass
        class VarFileInfo(_Node): pass
        class VarStruct(_Node): pass

        ns = {
            "VSVersionInfo": VSVersionInfo,
            "FixedFileInfo": FixedFileInfo,
            "StringFileInfo": StringFileInfo,
            "StringTable": StringTable,
            "StringStruct": StringStruct,
            "VarFileInfo": VarFileInfo,
            "VarStruct": VarStruct,
        }
        result = eval(content, ns)
        assert isinstance(result, VSVersionInfo)
        assert "FixedFileInfo" in captured
        assert "StringFileInfo" in captured
        assert "VarFileInfo" in captured
        # verifică că FileVersion e printre strings
        ss_values = [s.args for s in captured.get("StringStruct", [])]
        keys = [v[0] for v in ss_values if len(v) >= 1]
        assert "FileVersion" in keys
        assert "ProductName" in keys


# ============= 8. REGRESIE — AIScanareWindow smoke =============

class TestAIScanareRegression:
    def test_ai_scan_window_still_works(self, qapp):
        from ai_scanare.scan_window import AIScanareWindow, doc_label
        # doc_label returnează etichete
        lbl = doc_label("buletin")
        assert isinstance(lbl, str) and len(lbl) > 0
        # instanțiere headless
        w = AIScanareWindow()
        assert w is not None
        # butoane de istoric — smoke
        w.close()
