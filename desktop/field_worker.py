"""field_worker.py — rulează apeluri HTTP în afara thread-ului UI (QThread); erorile ajung ca text fix, fără secrete."""
from __future__ import annotations

import atexit
import logging
from typing import Any, Callable

from PySide6.QtCore import QCoreApplication, QThread, Signal

import field_import
import field_inbox_client as fic

log = logging.getLogger("cimitir.field_worker")


def error_text(exc: BaseException) -> str:
    if isinstance(exc, (fic.FieldCloudError, field_import.FieldImportError)):
        return str(exc)
    log.warning("eroare Field Inbox: %s", type(exc).__name__)
    return fic.UNAVAILABLE


class ApiTask(QThread):
    ok = Signal(object)
    failed = Signal(str, bool)  # (mesaj, cloud neconfigurat)

    def __init__(self, fn: Callable[[], Any], parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            result = self._fn()
        except fic.NotConfigured as exc:
            self.failed.emit(str(exc), True)
            return
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(error_text(exc), False)
            return
        self.ok.emit(result)


_ACTIVE: set = set()  # referințe la thread-urile în curs: un QThread în execuție nu trebuie distrus odată cu fereastra


_HOOKED = False


def wait_for_tasks(timeout_ms: int = 15000) -> bool:
    """Așteaptă thread-urile în curs (cererile HTTP au timeout propriu). Un QThread nu are voie să fie distrus cât rulează:
    la ieșirea procesului Qt face abort („Destroyed while thread is still running”). True dacă toate s-au încheiat."""
    done = True
    for task in list(_ACTIVE):
        try:
            if task.isRunning() and not task.wait(timeout_ms):
                done = False
        except RuntimeError:  # obiectul C++ nu mai există
            continue
    app = QCoreApplication.instance()
    if app is not None:
        app.processEvents()
    return done


def _hook_shutdown() -> None:
    global _HOOKED
    if _HOOKED:
        return
    _HOOKED = True
    atexit.register(wait_for_tasks)
    app = QCoreApplication.instance()
    if app is not None:
        app.aboutToQuit.connect(wait_for_tasks)


def run_task(owner, fn: Callable[[], Any], on_ok: Callable[[Any], None], on_fail: Callable[[str, bool], None]) -> ApiTask:
    """Pornește `fn` într-un thread; rezultatul ajunge în UI doar dacă fereastra încă există."""
    _hook_shutdown()
    task = ApiTask(fn)
    _ACTIVE.add(task)

    def _safe(cb):
        def call(*args):
            try:
                cb(*args)
            except RuntimeError:  # widget-ul C++ a fost distrus între timp (fereastra închisă)
                log.debug("rezultat ignorat: fereastra a fost închisă")
        return call

    def _done():
        _ACTIVE.discard(task)
        task.deleteLater()

    task.ok.connect(_safe(on_ok))
    task.failed.connect(_safe(on_fail))
    task.finished.connect(_done)
    task.start()
    return task
