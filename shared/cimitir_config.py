"""Configurare unică Cimitir: folderul de date (CIMITIR_DATA_DIR) și fișierul .env.

Toate componentele (desktop, backend, start_local) importă acest modul, deci
deschid același fișier cimitir.db. Fără dependențe externe (merge și în .exe).
"""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path
from typing import Dict, List, Optional

SHARED_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SHARED_DIR.parent
DEFAULT_DEV_DATA_DIR = PROJECT_DIR / "shared_data"

_env_loaded = False
_CLOUD_MARKERS = ("onedrive", "dropbox", "google drive", "googledrive", "icloud", "\\\\")


def _parse_env_line(line: str) -> Optional[tuple]:
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, _, value = line.partition("=")
    key, value = key.strip(), value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return (key, value) if key else None


def env_files() -> List[Path]:
    explicit = os.environ.get("CIMITIR_ENV_FILE", "").strip()
    files = [Path(explicit)] if explicit else []
    if os.environ.get("CIMITIR_IGNORE_PROJECT_ENV", "").strip() != "1":  # izolare pentru teste
        files += [PROJECT_DIR / ".env", PROJECT_DIR / "backend" / ".env"]
    return files


def load_env() -> List[str]:
    """Încarcă .env fără a suprascrie variabilele deja setate. Returnează fișierele citite."""
    global _env_loaded
    loaded: List[str] = []
    if _env_loaded:
        return loaded
    _env_loaded = True
    for path in env_files():
        try:
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                item = _parse_env_line(line)
                if item and item[0] not in os.environ:
                    os.environ[item[0]] = item[1]
            loaded.append(str(path))
        except OSError:
            continue
    return loaded


def _os_user_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / "Cimitir"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Cimitir"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "Cimitir"


def data_dir() -> str:
    """Folderul de date, în ordine: CIMITIR_DATA_DIR -> (.exe) folderul utilizatorului -> ./shared_data."""
    load_env()
    raw = os.environ.get("CIMITIR_DATA_DIR", "").strip()
    if raw:
        path = Path(os.path.expandvars(os.path.expanduser(raw))).resolve()
    elif getattr(sys, "frozen", False):
        path = _os_user_data_dir()
    else:
        path = DEFAULT_DEV_DATA_DIR
    path.mkdir(parents=True, exist_ok=True)
    os.environ["CIMITIR_DATA_DIR"] = str(path)
    return str(path)


def working_dir() -> str:
    """Folderul registrului activ (mod registre locale) sau DATA_DIR (mod clasic): copii de siguranță, restaurare."""
    import cimitir_registry
    return str(cimitir_registry.working_dir(data_dir()))


def db_path() -> str:
    return os.path.join(data_dir(), "cimitir.db")


def sync_folder_warning(path: Optional[str] = None) -> str:
    """Avertisment dacă folderul pare sincronizat în cloud/rețea (SQLite WAL nu e sigur acolo)."""
    low = str(path or data_dir()).lower()
    hit = next((m for m in _CLOUD_MARKERS if m in low), None)
    if hit:
        return (f"Folderul de date pare pe OneDrive/Dropbox/rețea ({hit.strip(chr(92)) or 'UNC'}): "
                "SQLite WAL se poate corupe. Folosește un disc local.")
    return ""


def jwt_secret() -> str:
    """JWT_SECRET din mediu; altfel generat o dată și păstrat în <data>/.jwt_secret (sesiuni stabile la restart)."""
    load_env()
    value = os.environ.get("JWT_SECRET", "").strip()
    if value:
        return value
    secret_file = Path(data_dir()) / ".jwt_secret"
    try:
        if secret_file.is_file():
            value = secret_file.read_text(encoding="utf-8").strip()
        if not value:
            value = secrets.token_hex(32)
            secret_file.write_text(value, encoding="utf-8")
            try:
                os.chmod(secret_file, 0o600)
            except OSError:
                pass
    except OSError:
        value = secrets.token_hex(32)
    os.environ["JWT_SECRET"] = value
    return value


def portal_host() -> str:
    load_env()
    return os.environ.get("CIMITIR_HOST", "127.0.0.1").strip() or "127.0.0.1"


def portal_port() -> int:
    load_env()
    return int(os.environ.get("CIMITIR_PORT") or os.environ.get("PORT") or 8000)


LOCAL_HOST = "127.0.0.1"
LOCAL_PORT = 8000


def desktop_port() -> int:
    """Portul backend-ului local al MAIN: CIMITIR_PORT explicit, altfel 8000. Ignoră variabila generică PORT (a hosting-ului)."""
    load_env()
    raw = os.environ.get("CIMITIR_PORT", "").strip()
    return int(raw) if raw.isdigit() else LOCAL_PORT


def is_loopback_host(host: str) -> bool:
    return (host or "").strip().lower() in ("127.0.0.1", "localhost", "::1")


def instance_id() -> str:
    """Identitate neconfidențială a instanței locale (amprentă a folderului de date); permite reutilizarea propriului backend."""
    import hashlib
    path = os.path.normcase(os.path.realpath(data_dir()))
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]


def mode() -> str:
    """'local' (implicit: desktop + portal local) sau 'online' (server public cu API de sincronizare)."""
    load_env()
    value = os.environ.get("CIMITIR_MODE", "local").strip().lower()
    return value if value in ("local", "online") else "local"


def db_engine() -> str:
    """Motorul de date al serverului: 'sqlite' (implicit, desktop/offline/teste) sau 'postgres' (CIMITIR_DB_ENGINE)."""
    load_env()
    value = os.environ.get("CIMITIR_DB_ENGINE", "sqlite").strip().lower()
    return "postgres" if value in ("postgres", "postgresql", "pg") else "sqlite"


APP_VERSION = "1.0.0-etapa6"


def app_version() -> str:
    """Versiunea aplicației (CIMITIR_VERSION din mediu are prioritate; nu e secret)."""
    load_env()
    return os.environ.get("CIMITIR_VERSION", "").strip() or APP_VERSION


_mongo_warned = False


def mongo_enabled() -> bool:
    """DEPRECATED (Etapa 10): MongoDB e legacy, dezactivat implicit; activarea cu CIMITIR_ENABLE_MONGO=1 se avertizează în jurnal."""
    global _mongo_warned
    load_env()
    on = os.environ.get("CIMITIR_ENABLE_MONGO", "0").strip().lower() in ("1", "true", "yes")
    if on and not _mongo_warned:
        _mongo_warned = True
        import logging
        logging.getLogger("cimitir").warning("CIMITIR_ENABLE_MONGO=1: MongoDB este DEPRECATED și va fi eliminat; folosește PostgreSQL.")
    return on


def describe(component: str) -> Dict[str, str]:
    """Rezumat pentru logul de pornire (cale absolută a bazei deschise de componentă)."""
    database = "PostgreSQL (DATABASE_URL)" if db_engine() == "postgres" else db_path()
    return {"component": component, "data_dir": data_dir(), "db": database,
            "warning": sync_folder_warning()}


def log_startup(component: str) -> Dict[str, str]:
    info = describe(component)
    print(f"[config] {component}: db={info['db']}", flush=True)
    if info["warning"]:
        print(f"[config] AVERTISMENT: {info['warning']}", flush=True)
    return info
