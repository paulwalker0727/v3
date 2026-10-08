"""Etapa 10 — Mongo este legacy/deprecated: dezactivat implicit, neimportat la rulare, păstrat pentru ștergerea ulterioară."""
import logging
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "shared", ROOT / "backend" / "cimitir"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import cimitir_config  # noqa: E402


def test_mongo_is_off_by_default(monkeypatch):
    monkeypatch.delenv("CIMITIR_ENABLE_MONGO", raising=False)
    assert cimitir_config.mongo_enabled() is False
    import store
    assert all(s.get("engine") != "mongo" for s in store.list_sources())


def test_enabling_mongo_logs_a_deprecation_warning(monkeypatch, caplog):
    monkeypatch.setenv("CIMITIR_ENABLE_MONGO", "1")
    monkeypatch.setattr(cimitir_config, "_mongo_warned", False)
    with caplog.at_level(logging.WARNING, logger="cimitir"):
        assert cimitir_config.mongo_enabled() is True
    assert "DEPRECATED" in caplog.text


def test_legacy_module_is_kept_and_marked():
    text = (ROOT / "backend" / "cimitir" / "mongo_store.py").read_text(encoding="utf-8")
    assert "LEGACY / DEPRECATED" in text and "DeprecationWarning" in text


def test_server_does_not_import_mongo_at_runtime():
    code = ("import sys, os; os.environ['CIMITIR_DB_ENGINE']='sqlite'; sys.path[:0]=['backend','backend/cimitir','shared']; "
            "import importlib.util as u; s=u.spec_from_file_location('srv','backend/server.py'); m=u.module_from_spec(s); s.loader.exec_module(m); "
            "print('MONGO_IMPORTED' if ('mongo_store' in sys.modules or 'pymongo' in sys.modules) else 'CLEAN')")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=90,
                         env={**__import__("os").environ, "CIMITIR_ENABLE_MONGO": "0", "CIMITIR_IGNORE_PROJECT_ENV": "1"})
    assert "CLEAN" in out.stdout, out.stdout[-300:] + out.stderr[-500:]


def test_audit_script_reports_disabled_state():
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "audit_mongo.py")], capture_output=True, text=True, timeout=60,
                         env={**__import__("os").environ, "CIMITIR_ENABLE_MONGO": "0"})
    assert out.returncode == 0 and "Mongo dezactivat" in out.stdout
