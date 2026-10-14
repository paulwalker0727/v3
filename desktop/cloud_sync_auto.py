"""
cloud_sync_auto.py — Sincronizare automată în fundal.

Rulează la pornirea aplicației (după 5 secunde, ca UI-ul să apară instant) și apoi
periodic (implicit la fiecare 5 minute), DOAR dacă sincronizarea e configurată și bifată
„Sincronizează automat” în fereastra ☁ Sincronizare cloud.

Totul rulează pe un thread separat → interfața nu îngheață niciodată, iar lipsa
internetului nu produce erori vizibile (operațiile rămân în coada locală).
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QThread, QTimer, Signal

import cloud_sync

START_DELAY_MS = 5000
INTERVAL_MS = 5 * 60 * 1000
FAST_INTERVAL_MS = 30 * 1000   # Etapa 6: modificările locale pleacă (și cele ale serverului vin) în ~30 s, nu în 5 min


def _pinned():
    """Un ciclu de sincronizare rămâne pe registrul pe care a început; comutarea îl așteaptă (Etapa 3)."""
    import contextlib
    import cimitir_registry
    return cimitir_registry.pinned() if cimitir_registry.current_binding() is not None else contextlib.nullcontext()


class _Worker(QThread):
    done = Signal(dict)

    def run(self):
        try:
            with _pinned():
                self.done.emit(cloud_sync.sync_all())
        except Exception as e:
            self.done.emit({"ok": False, "lines": [f"Eroare sincronizare: {e}"], "cereri_noi": 0})


class _EntityWorker(QThread):
    """Ciclu ușor pe entități: outbox -> API, apoi API -> cache SQLite. Offline: nu face nimic vizibil."""
    done = Signal(dict)

    def run(self):
        try:
            with _pinned():
                import entity_sync
                ok, msg = entity_sync.sync_entities()
                import media_sync
                ok_m, msg_m = media_sync.sync_media()
                ok, msg = ok and ok_m, "; ".join(m for m in (msg, msg_m) if m)
            self.done.emit({"ok": ok, "lines": [msg], "cereri_noi": 0, "entities_only": True})
        except Exception as e:
            self.done.emit({"ok": False, "lines": [f"Eroare sincronizare entități: {e}"], "cereri_noi": 0,
                            "entities_only": True})


class AutoSync(QObject):
    """Controller de sincronizare automată. `finished` trimite raportul fiecărei rulări."""

    finished = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: _Worker | None = None

        self._timer = QTimer(self)
        self._timer.setInterval(INTERVAL_MS)
        self._timer.timeout.connect(self.run_once)

        self._fast = QTimer(self)
        self._fast.setInterval(FAST_INTERVAL_MS)
        self._fast.timeout.connect(self.run_entities)

    def start(self):
        try:  # captarea modificărilor locale pornește imediat (și offline), înainte de prima sincronizare
            import cloud_sync
            if cloud_sync.is_configured():
                import entity_sync
                entity_sync.ensure_tracking()
                import media_sync
                media_sync.ensure_tracking()
        except Exception:
            pass
        QTimer.singleShot(START_DELAY_MS, self.run_once)
        self._timer.start()
        self._fast.start()

    def stop(self):
        self._timer.stop()
        self._fast.stop()

    def run_entities(self):
        """Ciclul rapid: doar dacă sincronizarea automată e activă și nu rulează deja alt ciclu."""
        if (self._worker is not None and self._worker.isRunning()) or \
                (getattr(self, "_ew", None) is not None and self._ew.isRunning()):
            return
        try:
            cfg = cloud_sync.get_config()
        except Exception:
            return
        if not (cfg["url"] and cfg["key"] and cfg["auto"]):
            return
        self._ew = _EntityWorker(self)
        self._ew.done.connect(self._on_entities_done)
        self._ew.start()

    def _on_entities_done(self, report: dict):
        self._ew = None
        self.finished.emit(report)

    def run_once(self, force: bool = False):
        if (self._worker is not None and self._worker.isRunning()) or \
                (getattr(self, "_ew", None) is not None and self._ew.isRunning()):
            return
        try:
            import cloud_profiles
            cloud_profiles.migrate_legacy_sync_key()
        except Exception as exc:
            print(f"[cloud_sync_auto] migrare cheie: {type(exc).__name__}")
        try:
            cfg = cloud_sync.get_config()
        except Exception:
            return
        if not (cfg["url"] and cfg["key"]):
            return
        if not cfg["auto"] and not force:
            return
        self._worker = _Worker(self)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_done(self, report: dict):
        self._worker = None
        self.finished.emit(report)
