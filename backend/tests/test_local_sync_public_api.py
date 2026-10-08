"""Regression tests for local desktop↔backend SQLite sync and public API privacy."""

from __future__ import annotations

import importlib.util
import os
import py_compile
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
DESKTOP_DIR = ROOT / "desktop"
BACKEND_DIR = ROOT / "backend"
BACKEND_CIMITIR_DIR = BACKEND_DIR / "cimitir"
SHARED_DIR = ROOT / "shared"


def _load_module_from_path(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module from {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _clear_backend_related_modules() -> None:
    for name in [
        "server",
        "store",
        "database",
        "cimitir_config",
        "cimitir_database",
        "cimitir_auth",
        "cimitir_public",
        "sync_api",
        "auth",
        "web_public",
        "mongo_store",
    ]:
        sys.modules.pop(name, None)


def _import_server_with_env(data_dir: Path):
    os.environ["CIMITIR_DATA_DIR"] = str(data_dir)
    _clear_backend_related_modules()
    return _load_module_from_path("server_under_test", BACKEND_DIR / "server.py")


# module compile/syntax checks for requested desktop + backend modules
def test_requested_modules_compile():
    targets = [
        DESKTOP_DIR / "start_local.py",
        DESKTOP_DIR / "ui_mainwindow.py",
        DESKTOP_DIR / "cereri_online_window.py",
        BACKEND_DIR / "server.py",
        BACKEND_CIMITIR_DIR / "database.py",
        BACKEND_CIMITIR_DIR / "web_public.py",
    ]
    for target in targets:
        py_compile.compile(str(target), doraise=True)


# explicit compile checks for changed canonical database modules
def test_changed_modules_compile():
    targets = [
        SHARED_DIR / "cimitir_database.py",
        DESKTOP_DIR / "database.py",
        BACKEND_CIMITIR_DIR / "database.py",
        BACKEND_DIR / "server.py",
    ]
    for target in targets:
        py_compile.compile(str(target), doraise=True)


# adapter files should stay thin wrappers around shared canonical implementation
def test_database_adapters_are_thin_wrappers_to_shared_module():
    desktop_source = (DESKTOP_DIR / "database.py").read_text(encoding="utf-8")
    backend_source = (BACKEND_CIMITIR_DIR / "database.py").read_text(encoding="utf-8")

    for source in (desktop_source, backend_source):
        assert "import cimitir_database as _shared" in source
        assert "from cimitir_database import *" not in source
        assert "CREATE TABLE" not in source
        assert "sqlite3.connect" not in source


# CLI behavior check for local launcher
def test_start_local_help_mentions_data_dir():
    proc = subprocess.run(
        [sys.executable, str(DESKTOP_DIR / "start_local.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    help_text = f"{proc.stdout}\n{proc.stderr}"
    assert "--data-dir" in help_text


# shared SQLite sync between desktop module and backend module
def test_desktop_and_backend_share_same_sqlite_and_plot(tmp_path):
    data_dir = tmp_path / "cimitir-shared"
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["CIMITIR_DATA_DIR"] = str(data_dir)
    _clear_backend_related_modules()

    desktop_db = _load_module_from_path("desktop_database_under_test", DESKTOP_DIR / "database.py")
    backend_db = _load_module_from_path("backend_database_under_test", BACKEND_CIMITIR_DIR / "database.py")

    desktop_db.init_db()
    cod = "TEST-SYNC-001"
    points = [(10, 10), (60, 10), (60, 50), (10, 50)]
    plot_id = desktop_db.save_plot_to_db(cod, points, "#00AAFF")
    assert isinstance(plot_id, int)

    assert Path(desktop_db.DB_PATH) == data_dir / "cimitir.db"
    assert Path(backend_db.DB_PATH) == data_dir / "cimitir.db"

    found = backend_db.load_loc_by_cod(cod)
    assert found is not None
    assert found["cod_loc"] == cod
    assert found["points"] == [list(p) for p in points]


# public request flow writes into cereri_concesionare with status "nou"
def test_public_cerere_is_visible_in_shared_desktop_db(tmp_path):
    data_dir = tmp_path / "cimitir-shared"
    data_dir.mkdir(parents=True, exist_ok=True)

    server_module = _import_server_with_env(data_dir)
    client = TestClient(server_module.app)

    cod = "TEST-CERERE-001"
    import database as backend_db  # resolved from backend/cimitir path injected by server

    backend_db.init_db()
    backend_db.save_plot_to_db(cod, [(0, 0), (50, 0), (50, 30), (0, 30)], "#22c55e")
    backend_db.save_loc_metadata(cod, {"sector": "A", "parcela": "1", "rand": "2", "numar": "7", "disponibilitate": "Liber"})

    payload = {
        "nume": "Popescu",
        "prenume": "Ion",
        "cnp": "1234567890123",
        "bi_ci": "RX123456",
        "adresa": "Str. Test 1",
        "telefon": "0700000000",
        "email": "ion@example.com",
        "grad_rudenie": "fiu",
        "observatii": "test local sync",
        "cod_loc": cod,
    }
    resp = client.post("/api/public/cerere", data=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body.get("id"), int)

    desktop_db = _load_module_from_path("desktop_database_after_cerere", DESKTOP_DIR / "database.py")
    rows = desktop_db.list_cereri_concesionare("nou")
    created = next((r for r in rows if r.get("id") == body["id"]), None)
    assert created is not None
    assert created["status"] == "nou"
    assert created["cod_loc"] == cod
    assert created["nume"] == "Popescu"
    assert created["prenume"] == "Ion"


# privacy checks for public map and public location endpoints
def test_public_endpoints_share_data_and_do_not_expose_sensitive_fields(tmp_path):
    data_dir = tmp_path / "cimitir-shared"
    data_dir.mkdir(parents=True, exist_ok=True)

    server_module = _import_server_with_env(data_dir)
    client = TestClient(server_module.app)

    cod = "TEST-PRIV-001"
    import database as backend_db

    backend_db.init_db()
    backend_db.save_plot_to_db(cod, [(5, 5), (45, 5), (45, 35), (5, 35)], "#2563eb")
    backend_db.save_loc_metadata(
        cod,
        {
            "sector": "B",
            "parcela": "2",
            "rand": "4",
            "numar": "11",
            "capacitate": "2",
            "tip": "familie",
            "categorie": "A",
            "suprafata": 12.5,
            "disponibilitate": "Liber",
        },
    )

    map_resp = client.get("/api/public/map")
    assert map_resp.status_code == 200
    map_data = map_resp.json()
    assert any(p.get("cod_loc") == cod for p in map_data.get("plots", []))

    loc_resp = client.get(f"/api/public/loc/{cod}")
    assert loc_resp.status_code == 200
    loc_data = loc_resp.json()
    assert loc_data["cod_loc"] == cod

    forbidden = {
        "cnp",
        "adresa",
        "telefon",
        "email",
        "documents",
        "password",
        "password_hash",
        "credential",
        "credentials",
        "bi_ci",
    }

    def assert_no_forbidden_fields(value):
        if isinstance(value, dict):
            for k, v in value.items():
                assert k not in forbidden
                assert_no_forbidden_fields(v)
        elif isinstance(value, list):
            for item in value:
                assert_no_forbidden_fields(item)

    assert_no_forbidden_fields(map_data)
    assert_no_forbidden_fields(loc_data)


def test_reports_filter_by_sector_and_paginate_contracts(tmp_path):
    data_dir = tmp_path / "cimitir-reports"
    data_dir.mkdir(parents=True, exist_ok=True)
    server_module = _import_server_with_env(data_dir)
    client = TestClient(server_module.app)
    import database as backend_db

    backend_db.init_db()
    today = date.today()
    for sector, cod in (("A", "REPORT-A-01"), ("B", "REPORT-B-01")):
        backend_db.save_plot_to_db(cod, [(0, 0), (10, 0), (10, 10), (0, 10)], "#00AAFF")
        backend_db.save_loc_metadata(cod, {"sector": sector, "disponibilitate": "Liber"})
    for index in range(12):
        backend_db.save_contract({
            "numar_contract": f"A-{index + 1:02}", "cod_loc": "REPORT-A-01",
            "data_sfarsit": (today + timedelta(days=index + 1)).isoformat(),
        })
    backend_db.save_contract({
        "numar_contract": "A-OUT", "cod_loc": "REPORT-A-01",
        "data_sfarsit": (today + timedelta(days=60)).isoformat(),
    })
    backend_db.save_contract({
        "numar_contract": "B-01", "cod_loc": "REPORT-B-01",
        "data_sfarsit": (today + timedelta(days=3)).isoformat(),
    })

    login = client.post("/admin/access/login", data={
        "username": "admin", "password": "admin123", "source_id": "cimitir",
    }, follow_redirects=False)
    assert login.status_code == 303
    assert client.post("/admin/schimba-parola", data={"old_password": "admin123", "new_password": "Parola-test-987",
                                                      "confirm_password": "Parola-test-987"}, follow_redirects=False).status_code == 303

    response = client.get(
        f"/admin/rapoarte?sector=A&start={today.isoformat()}&end={(today + timedelta(days=30)).isoformat()}&page_size=5&page=2"
    )
    assert response.status_code == 200
    assert "Pagina 2 din 3" in response.text
    assert "A-06" in response.text
    assert "B-01" not in response.text
    assert "A-OUT" not in response.text
    assert 'data-testid="report-pagination"' in response.text
    for testid in [
        'data-testid="report-filter-form"',
        'data-testid="report-sector-filter"',
        'data-testid="report-start-date"',
        'data-testid="report-end-date"',
        'data-testid="report-apply-filters"',
        'data-testid="report-reset-filters"',
        'data-testid="report-expiring-count"',
        'data-testid="report-expiring-table"',
        'data-testid="report-page-previous"',
        'data-testid="report-current-page"',
        'data-testid="report-page-next"',
    ]:
        assert testid in response.text
