import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
import cimitir_test_guard as _guard  # noqa: E402


_ROOT = Path(__file__).resolve().parents[2]


def _modules_in(directory: Path) -> set:
    return {f.stem for f in directory.glob("*.py")}


COLLIDING_MODULES = sorted((_modules_in(_ROOT / "backend") | _modules_in(_ROOT / "backend" / "cimitir")) & _modules_in(_ROOT / "desktop"))   # același nume în backend/ și desktop/ (database, mobile_upload …)


@pytest.fixture(scope="module", autouse=True)
def _isolate_colliding_modules():
    """Testele backend reîncarcă `server`/`database`/… din backend/; la finalul modulului se restaurează modulele și sys.path, ca testele desktop rulate după ele să vadă propriile module."""
    saved_modules = {n: sys.modules[n] for n in COLLIDING_MODULES if n in sys.modules}
    saved_path = list(sys.path)
    try:
        yield
    finally:
        for n in COLLIDING_MODULES:
            if n in saved_modules:
                sys.modules[n] = saved_modules[n]
            else:
                sys.modules.pop(n, None)
        sys.path[:] = saved_path


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
