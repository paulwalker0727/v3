"""Adaptor desktop pentru nucleul SQLite comun (shared/cimitir_database.py).

Calea bazei vine din shared/cimitir_config.py (CIMITIR_DATA_DIR / .env).
"""
from __future__ import annotations

import sys
from pathlib import Path


SHARED_DIR = Path(__file__).resolve().parent.parent / "shared"
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))

import cimitir_database as _shared  # noqa: E402

globals().update({k: v for k, v in vars(_shared).items() if not k.startswith("__")})

# `import database` întoarce chiar modulul comun: monkeypatch/setări pe DB_PATH ajung la get_connection().
sys.modules[__name__] = _shared
