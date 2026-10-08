"""
app_logging.py — configurare centralizată de logging pentru Cimitir Desktop.

- Fișier de log rotativ: ~appdata/Cimitir/logs/cimitir.log (5 MB × 5 fișiere)
- Consolă (stderr) pentru dev-mode
- Format cu timestamp + nivel + modul + linie + mesaj
- Excepțiile se pot loga cu `logger.exception(...)` — stack-trace complet

Utilizare în restul aplicației:
    from app_logging import get_logger
    logger = get_logger(__name__)
    try:
        ...
    except Exception:
        logger.exception("Descriere clară a operațiunii care a eșuat")
"""
import logging
import logging.handlers
import os
import sys
from typing import Optional


_INITIALIZED = False


def _resolve_log_dir() -> str:
    """Determină folderul de log în funcție de OS (același root ca database.py)."""
    env = os.environ.get("CIMITIR_LOG_DIR", "").strip()
    if env:
        os.makedirs(env, exist_ok=True)
        return env

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        path = os.path.join(base, "Cimitir", "logs")
    elif sys.platform == "darwin":
        path = os.path.join(os.path.expanduser("~"),
                            "Library", "Application Support", "Cimitir", "logs")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share")
        path = os.path.join(base, "Cimitir", "logs")

    os.makedirs(path, exist_ok=True)
    return path


def setup_logging(level: int = logging.INFO,
                  log_file: Optional[str] = None,
                  console: bool = True) -> None:
    """Configurează logging-ul global. Idempotent — poate fi apelat de mai multe ori."""
    global _INITIALIZED
    if _INITIALIZED:
        return

    log_file = log_file or os.path.join(_resolve_log_dir(), "cimitir.log")

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(name)s:%(lineno)d — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)
    # curățăm handlerele existente (în caz de rulare repetată în teste)
    for h in list(root.handlers):
        root.removeHandler(h)

    # 1) fișier rotativ: max 5 MB × 5 fișiere
    try:
        fh = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        fh.setLevel(level)
        fh.setFormatter(formatter)
        root.addHandler(fh)
    except Exception as e:
        # ne mulțumim cu consola dacă fișierul nu poate fi scris
        sys.stderr.write(f"[logging] cannot open log file {log_file}: {e}\n")

    # 2) consolă (util în dev + supervisor)
    if console:
        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(level)
        ch.setFormatter(formatter)
        root.addHandler(ch)

    # reduce zgomotul unor librării
    logging.getLogger("PIL").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("matplotlib").setLevel(logging.WARNING)

    _INITIALIZED = True
    logging.getLogger(__name__).info("Logging inițializat — fișier: %s", log_file)


def get_logger(name: str) -> logging.Logger:
    """Returnează un logger. Se asigură că setup-ul a fost făcut."""
    if not _INITIALIZED:
        setup_logging()
    return logging.getLogger(name)
