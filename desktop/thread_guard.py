"""thread_guard.py — un QThread în execuție nu trebuie distrus la ieșirea procesului (Qt face abort).
`track(thread)` îl ține referit până se termină; `wait_all()` rulează la `aboutToQuit`/`atexit`."""
from __future__ import annotations

import atexit

from PySide6.QtCore import QCoreApplication

_TRACKED: set = set()
_HOOKED = False


def wait_all(timeout_ms: int = 15000) -> bool:
    """True dacă toate thread-urile urmărite s-au încheiat în termen."""
    done = True
    for t in list(_TRACKED):
        try:
            if t.isRunning() and not t.wait(timeout_ms):
                done = False
        except RuntimeError:  # obiectul C++ nu mai există
            _TRACKED.discard(t)
    app = QCoreApplication.instance()
    if app is not None:
        app.processEvents()
    return done


def _hook() -> None:
    global _HOOKED
    app = QCoreApplication.instance()
    if not _HOOKED:
        _HOOKED = True
        atexit.register(wait_all)
    if app is not None and not getattr(app, "_cimitir_thread_guard", False):
        app._cimitir_thread_guard = True
        app.aboutToQuit.connect(wait_all)


def track(thread):
    """Urmărește `thread` până la `finished`; întoarce același obiect (se poate folosi inline)."""
    _hook()
    _TRACKED.add(thread)
    thread.finished.connect(lambda t=thread: _TRACKED.discard(t))
    return thread
