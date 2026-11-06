import os

# Izolare de backend/.env (în pod rulează PostgreSQL): testele desktop folosesc implicit SQLite local.
os.environ.setdefault("CIMITIR_DB_ENGINE", "sqlite")
os.environ.setdefault("CIMITIR_MODE", "local")
os.environ.setdefault("DATABASE_URL", "")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CIMITIR_ALLOW_DEFAULT_USERS", "1")

import sys  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _wait_field_worker_threads(monkeypatch):
    """Un ApiTask (QThread) rămas în execuție după test se termină înaintea restaurării mediului (DB_PATH etc.) și a ieșirii procesului."""
    yield
    fw = sys.modules.get("field_worker")
    if fw is not None:
        assert fw.wait_for_tasks(15000), "ApiTask încă în execuție la finalul testului"
