# map_editor_diag.py
"""Diagnosticare editor hartă: excepțiile nu mai sunt ignorate silențios.

Se loghează doar locul, clasa excepției și stiva (fără mesajul excepției, care poate conține date personale),
iar utilizatorul vede o notă în bara de stare. Aceeași eroare repetată (ex. la fiecare mouse-move) se loghează o dată pe interval.
"""
import time
import traceback

from app_logging import get_logger

logger = get_logger("map_editor")

_REPEAT_SECONDS = 30.0
_last_seen: dict = {}


def report(where: str, exc: BaseException, window=None, notify: bool = True) -> None:
    last_frame = repr(traceback.extract_tb(exc.__traceback__)[-1:]) if exc.__traceback__ else ""
    key = (where, type(exc).__name__, last_frame)
    now = time.monotonic()
    prev = _last_seen.get(key)
    if prev is not None and now - prev < _REPEAT_SECONDS:
        return
    _last_seen[key] = now
    stack = "".join(traceback.format_tb(exc.__traceback__)[-6:])
    logger.error("Editor hartă — eroare în %s: %s\n%s", where, type(exc).__name__, stack)
    if notify and window is not None:
        try:
            window.statusBar().showMessage(f"⚠ Eroare internă editor hartă ({where}). Detalii în jurnalul aplicației.")
        except Exception:  # noqa: BLE001 - raportarea nu are voie să arunce
            pass


def reset_for_tests() -> None:
    _last_seen.clear()
