"""cimitir_db_router.py — alege motorul de date pentru modulele comune (auth, cereri, notificări, public).

Modulele din `shared/` apelau direct `cimitir_database` (SQLite). Importându-l pe acesta în loc (`import
cimitir_db_router as db`), ele folosesc automat PostgreSQL când `CIMITIR_DB_ENGINE=postgres`, iar implicit rămân pe
SQLite — deci aplicația desktop și modul offline nu sunt afectate.
"""
from __future__ import annotations

import importlib

import cimitir_config


def _target():
    # Modulele se rezolvă la fiecare acces (nu la import): dacă `cimitir_database` e reîncărcat (teste, repornire
    # in-process), routerul vede mereu instanța curentă.
    if cimitir_config.db_engine() == "postgres":
        return importlib.import_module("postgres_store")  # backend/cimitir (pe sys.path doar în backend)
    return importlib.import_module("cimitir_database")


def __getattr__(name: str):
    return getattr(_target(), name)
