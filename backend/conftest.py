import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))
import cimitir_test_guard as _guard  # noqa: E402


def pytest_configure(config):
    _guard.enforce_or_exit()


def pytest_collection_modifyitems(config, items):
    _guard.skip_live_items(items)
