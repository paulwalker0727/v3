"""cimitir_platform.py — căile aplicației pe macOS (instalare autonomă în ~/CIMITIR-MAC).

Folosit DOAR pe ramurile `sys.platform == "darwin"`; pe Windows nu este importat și nu schimbă nicio cale.
Funcțiile sunt pure (nu verifică sistemul de operare), ca să poată fi testate și pe Windows/Linux.

Structura (rădăcina se poate muta cu CIMITIR_MAC_HOME):
    ~/CIMITIR-MAC/date/cimitir-data/   baza SQLite, registre, documente, fotografii, outbox, secrete (= CIMITIR_DATA_DIR)
    ~/CIMITIR-MAC/date/cimitir-data-backups/   copii de siguranță (frate al folderului de date, ca pe Windows)
    ~/CIMITIR-MAC/date/config/         licență, cache licență, configurare AI
    ~/CIMITIR-MAC/date/logs/           jurnale aplicație și erori backup
    ~/CIMITIR-MAC/date/audit/          jurnal de audit criptat
    ~/CIMITIR-MAC/cache/               fișiere temporare (TMPDIR)
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_MAC_HOME = "CIMITIR_MAC_HOME"
DEFAULT_HOME_NAME = "CIMITIR-MAC"


def mac_home() -> Path:
    raw = os.environ.get(ENV_MAC_HOME, "").strip()
    if raw:
        return Path(os.path.expandvars(os.path.expanduser(raw)))
    return Path.home() / DEFAULT_HOME_NAME


def mac_data_root() -> Path:
    return mac_home() / "date"


def mac_app_data_dir() -> Path:
    return mac_data_root() / "cimitir-data"


def mac_cache_dir() -> Path:
    return mac_home() / "cache"


def mac_subdir(name: str) -> Path:
    """`config` | `logs` | `audit` | `backups`; creat la cerere."""
    path = mac_data_root() / name
    path.mkdir(parents=True, exist_ok=True)
    return path
