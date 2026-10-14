# -*- mode: python ; coding: utf-8 -*-
"""Spec PyInstaller pentru Cimitir Desktop.

Rulează din folderul `cimitir-desktop`:

    pyinstaller --noconfirm cimitir.spec                 # folder (onedir) — pornire instant
    set CIMITIR_ONEFILE=1 && pyinstaller --noconfirm cimitir.spec   # un singur .exe portabil

Punctul de intrare este `launcher.py` (alege între mod local și server LAN) —
este singurul modul deja pregătit pentru `sys.frozen`.

Portalul + administrarea web (backend/server.py) sunt incluse sub `portal/` și pornesc ca proces
propriu: `Cimitir.exe --portal --host H --port P` (vezi start_local.py).
"""
import os
import sys

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

ONEFILE = os.environ.get("CIMITIR_ONEFILE", "0") == "1"
# CIMITIR_SLIM=1 scoate OpenCV (~85 MB) — folosit doar la preprocesarea pentru
# OCR-ul offline, care oricum are nevoie de Tesseract instalat separat.
SLIM = os.environ.get("CIMITIR_SLIM", "0") == "1"
HERE = os.path.abspath(SPECPATH)  # noqa: F821  (injectat de PyInstaller)
SHARED = os.path.join(os.path.dirname(HERE), "shared")  # nucleul SQLite + config comun
BACKEND = os.path.join(os.path.dirname(HERE), "backend")  # portal + administrare web (server:app)

APP_NAME = "Cimitir"
ICON = os.path.join(HERE, "icon.ico")
VERSION_FILE = os.path.join(HERE, "installer", "file_version_info.txt")


def _data(rel_path, dest=None):
    """(sursă, destinație) doar dacă sursa există — evită erori de build."""
    src = os.path.join(HERE, rel_path)
    if not os.path.exists(src):
        return None
    return (src, dest if dest is not None else rel_path)


def _backend_data(name):
    src = os.path.join(BACKEND, name)
    return (src, "portal/" + name) if os.path.exists(src) else None


# --- fișiere de date incluse în pachet ---
datas = [
    d for d in (
        _data("fonts"),                 # LiberationSans pentru PDF/etichete
        _data("templates"),             # contract_template.docx
        _data("icon.ico", "."),
        _data("LICENSE.txt", "."),
        _data("docs"),
        _backend_data("templates"),     # șabloane Jinja2 ale administrării web
        _backend_data("static"),
        _backend_data("fonts"),
        _backend_data("doc_templates"),
    ) if d
]

binaries = collect_dynamic_libs("psycopg_binary")  # libpq pentru portalul înghețat

hiddenimports = [
    # Qt: module încărcate dinamic
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    # Google Gemini (cheia proprie din ⚙️ Setări AI)
    *collect_submodules("google.genai"),
    # server LAN
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    # documente / rapoarte
    "docx",
    "openpyxl",
    "reportlab.graphics.barcode",
    "qrcode.image.pil",
    # imagini / PDF / OCR
    "PIL._tkinter_finder",
    "cv2",
    "fitz",
    "pytesseract",
    # licențiere Ed25519
    "cryptography.hazmat.primitives.asymmetric.ed25519",
    "cryptography.hazmat.backends.openssl",
    # nucleul comun (shared/): importat dinamic din desktop/database.py
    "cimitir_database",
    "cimitir_config",
    "cimitir_auth",
    "cimitir_public",
    "cimitir_requests",
    "cloud_sync",
    "cloud_sync_auto",
    "cloud_sync_window",
    "cloud_conflicts_window",
    "cloud_profiles",
    "cloud_profiles_dialog",
    "field_inbox_client",
    "field_inbox_window",
    "field_import",
    "field_import_history",
    "field_import_history_window",
    "field_devices_window",
    "field_worker",
    "entity_sync",
    "cimitir_entities",
    "access_session",
    "cimitir_notify",
    "start_local",
    # portal + administrare web (backend/), pornite cu `Cimitir.exe --portal`
    *collect_submodules("psycopg"),
    "psycopg_binary",
    "psycopg_pool",
    "server",
    "store",
    "sync_api",
    "entity_sync_api",
    "citizen_api",
    "portal_ratelimit",
    "portal_jobs",
    "cimitir_backup",
    "backup_window",
    "web_public",
    "jinja2",
    "multipart",
    "python_multipart",
    "jwt",
    # module proprii încărcate prin import târziu
    "main",
    "web_server",
    "ui_mainwindow",
    "user_mode_window",
    "login_window",
    *collect_submodules("ai_scanare"),
    *collect_submodules("license"),
    *collect_submodules("security"),
]

excludes = [
    # rezerva Emergent LLM Key nu se livrează clientului (litellm e uriaș)
    "emergentintegrations",
    "litellm",
    # unelte de dezvoltare
    "pytest",
    "_pytest",
    "tkinter",
    "matplotlib",
    "notebook",
    "IPython",
    # module Qt nefolosite — scad mult dimensiunea
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtDesigner",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtSerialPort",
    "PySide6.QtTest",
    "PySide6.QtSql",
    "PySide6.QtHelp",
    "PySide6.QtSpatialAudio",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
]

if SLIM:
    hiddenimports = [h for h in hiddenimports if h != "cv2"]
    excludes += ["cv2", "opencv-python-headless"]

a = Analysis(
    [os.path.join(HERE, "launcher.py")],
    pathex=[HERE, SHARED, BACKEND, os.path.join(BACKEND, "cimitir")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

_exe_kwargs = dict(
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # aplicație GUI, fără fereastră de consolă
    icon=ICON if os.path.exists(ICON) else None,
    version=VERSION_FILE if os.path.exists(VERSION_FILE) else None,
)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        upx_exclude=[],
        runtime_tmpdir=None,
        **_exe_kwargs,
    )
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **_exe_kwargs)
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name=APP_NAME,
    )
