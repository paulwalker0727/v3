import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
import cimitir_test_guard as _guard  # noqa: E402


def pytest_configure(config):
    _guard.enforce_or_exit()


def pytest_collection_modifyitems(config, items):
    _guard.skip_live_items(items)


# Izolare de backend/.env (serverul de preview rulează pe PostgreSQL): testele folosesc implicit SQLite local.
# Variabilele deja setate în mediu (ex. CIMITIR_DB_ENGINE=postgres) au prioritate.
os.environ.setdefault("CIMITIR_DB_ENGINE", "sqlite")
os.environ.setdefault("CIMITIR_MODE", "local")
os.environ.setdefault("DATABASE_URL", "")


@pytest.fixture(autouse=True)
def _legacy_anonymous_requests(monkeypatch):
    """Testele Etapelor 1-2 depun cereri anonime; cele din Etapa 3 activează explicit contul obligatoriu."""
    monkeypatch.setenv("CIMITIR_PORTAL_REQUIRE_ACCOUNT", "0")


@pytest.fixture(autouse=True)
def _legacy_default_accounts(monkeypatch):
    """Testele vechi se loghează cu conturile implicite chiar și în modul online/SQLite. Producția NU le creează
    (vezi tests/test_first_run_bootstrap.py, care șterge această variabilă)."""
    monkeypatch.setenv("CIMITIR_ALLOW_DEFAULT_USERS", "1")


@pytest.fixture(scope="module", autouse=True)
def _serialize_modules_sharing_the_preview_server(request):
    """Modulele care rotesc X-Sync-Key pe serverul comun (:8001) nu rulează niciodată simultan pe workerii xdist (altfel: 401 între ele)."""
    try:
        text = Path(str(request.module.__file__)).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        text = ""
    if not any(m in text for m in ("ETAPA6_SERVER_URL", "127.0.0.1:8001", "localhost:8001")):
        yield
        return
    import fcntl
    import tempfile
    with open(Path(tempfile.gettempdir()) / "cimitir_preview_server.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
