"""Joburi periodice ale portalului: alerte email pentru MAIN offline, trimiterea mesajelor în așteptare, copia zilnică de rezervă.

Rulează într-un fir de fundal pornit la startup. `CIMITIR_JOBS=0` le oprește (teste); fiecare job își tratează singur erorile.
"""
from __future__ import annotations

import os
import threading
import time

import cimitir_backup
import cimitir_config
import cimitir_notify
import store as cim
import sync_api

INTERVAL_SECONDS = 300
FIRST_RUN_DELAY = 20


def run_once() -> dict:
    """Un ciclu de joburi; întoarce un rezumat (folosit și în teste)."""
    summary = {"alerts": 0, "emails": 0, "backup": None}
    for key, job in (("alerts", sync_api.check_offline_alerts),
                     ("emails", lambda: cimitir_notify.send_pending() if cimitir_notify.enabled() else 0)):
        try:
            summary[key] = job() or 0
        except Exception as exc:
            print(f"[jobs] {key}: {exc}")
    target = None
    if cimitir_config.db_engine() != "postgres":  # copia de rezervă SQLite; PostgreSQL se salvează la furnizor / pg_dump
        target = cimitir_backup.run_scheduled(cimitir_config.working_dir(), cim.get_setting)
    summary["backup"] = target.name if target else None
    return summary


def _bind(source) -> None:
    """Fiecare ciclu pe registrul activ în acel moment (Etapa 3: registre locale)."""
    import cimitir_registry
    if cimitir_registry.catalog_exists(cimitir_config.data_dir()):
        try:
            cimitir_registry.bind_context_from_catalog(cimitir_config.data_dir())
        except Exception as exc:
            print(f"[jobs] registru: {exc}")
    cim.bind_source(source)


def _loop(source) -> None:
    time.sleep(FIRST_RUN_DELAY)
    while True:
        _bind(source)
        run_once()
        time.sleep(INTERVAL_SECONDS)


def start(source) -> bool:
    if os.environ.get("CIMITIR_JOBS", "1").strip() == "0":
        return False
    threading.Thread(target=_loop, args=(source,), name="cimitir-jobs", daemon=True).start()
    return True
