# ruff: noqa: F811
"""Etapa 8 — securitate/stabilitate: tokenul QR nu apare în jurnalul de acces; izolarea modulelor omonime backend/desktop."""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from test_etapa1_audit import app_env  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]


def test_access_log_masks_the_qr_token(app_env):
    server, _, _ = app_env
    secret = "cimitir.AbCdEf0123456789AbCdEf0123456789"
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:5", "GET", f"/api/m/{secret}/info?x=1", "1.1", 200), None)
    assert server._TokenPathLogFilter().filter(rec) is True
    out = rec.getMessage()
    assert secret not in out and "/api/m/[TOKEN]/info?x=1" in out
    other = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "GET /portal/harta 200", (), None)
    server._TokenPathLogFilter().filter(other)
    assert other.getMessage() == "GET /portal/harta 200"
    assert any(isinstance(f, server._TokenPathLogFilter) for f in logging.getLogger("uvicorn.access").filters)


def test_info_endpoint_with_wrong_token_leaks_nothing_and_is_rate_limited(app_env):
    _, client, _ = app_env
    bad = "cimitir." + "z" * 43
    codes = []
    for _ in range(8):
        r = client.get(f"/api/m/{bad}/info")
        codes.append(r.status_code)
        assert "cimitir_test" not in r.text and "Traceback" not in r.text and "entity" not in r.text.lower().replace("entity_missing", "")
    assert codes[0] == 404 and 429 in codes


def test_launcher_portal_process_loads_backend_modules_not_desktop_homonyms(tmp_path):
    code = (
        "import sys; sys.path.insert(0, r'%s'); sys.path.insert(0, r'%s'); import mobile_upload as desktop_one  # simulează MAIN deja încărcat\n"
        "import start_local; start_local._prefer_backend_modules(start_local.WEB_DIR)\n"
        "import mobile_upload; print('SYNC', hasattr(mobile_upload, 'sync_router'))"
    ) % (ROOT / "desktop", ROOT / "shared")
    env = {"PATH": "/usr/bin:/bin", "CIMITIR_DATA_DIR": str(tmp_path), "CIMITIR_IGNORE_PROJECT_ENV": "1", "HOME": str(tmp_path)}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60)
    assert "SYNC True" in out.stdout, out.stderr[-500:]
