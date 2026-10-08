"""Teste pentru PUNCTUL 3 — verificare silențioasă a cheii AI (Gemini).

Nu se folosește niciun apel real de rețea: google_engine.test_key este mockuit.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ---- Qt bootstrap ---------------------------------------------------------
@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture(autouse=True)
def clear_cache():
    """Golește cache-ul înainte de fiecare test ca să nu polueze."""
    from ai_scanare import scan_window as sw
    sw._key_health_cache.clear()
    yield
    sw._key_health_cache.clear()


# =========================================================================
# 1. Cache: cached_key_health / remember_key_health / forget_key_health / TTL
# =========================================================================
class TestKeyHealthCache:
    def test_missing_key_returns_none(self):
        from ai_scanare.scan_window import cached_key_health
        assert cached_key_health("AQ.some-random-key") is None

    def test_remember_and_read_back(self):
        from ai_scanare.scan_window import cached_key_health, remember_key_health
        remember_key_health("AQ.abc", False, "Cheie invalidă sau revocată.")
        assert cached_key_health("AQ.abc") == (False, "Cheie invalidă sau revocată.")

    def test_different_key_isolated(self):
        from ai_scanare.scan_window import cached_key_health, remember_key_health
        remember_key_health("AQ.abc", True, "OK")
        assert cached_key_health("AQ.def") is None

    def test_forget_clears_all(self):
        from ai_scanare.scan_window import (cached_key_health, forget_key_health,
                                             remember_key_health)
        remember_key_health("AQ.abc", True, "OK")
        remember_key_health("AQ.other", False, "bad")
        forget_key_health()
        assert cached_key_health("AQ.abc") is None
        assert cached_key_health("AQ.other") is None

    def test_ttl_expiry(self):
        from ai_scanare import scan_window as sw

        sw.remember_key_health("AQ.exp", True, "OK")
        # manipulăm timestamp-ul pentru a-l face vechi
        fp = sw._key_fingerprint("AQ.exp")
        ok, msg, _ts = sw._key_health_cache[fp]
        sw._key_health_cache[fp] = (ok, msg, time.time() - sw._KEY_HEALTH_TTL - 5)
        assert sw.cached_key_health("AQ.exp") is None

    def test_fingerprint_stable_and_key_specific(self):
        from ai_scanare.scan_window import _key_fingerprint
        assert _key_fingerprint("AQ.abc") == _key_fingerprint("AQ.abc")
        assert _key_fingerprint("AQ.abc") != _key_fingerprint("AQ.abd")
        # nu conține direct cheia
        assert "abc" not in _key_fingerprint("AQ.abc") or len(_key_fingerprint("AQ.abc")) == 16


# =========================================================================
# 2. _KeyHealthWorker — verifică apelul spre google_engine.test_key
# =========================================================================
class TestKeyHealthWorker:
    def test_worker_emits_ok_true(self, qapp, monkeypatch):
        from ai_scanare import google_engine, scan_window as sw

        monkeypatch.setattr(google_engine, "test_key",
                            lambda k: {"ok": True, "message": "OK", "model": "gemini-flash"})
        worker = sw._KeyHealthWorker("AQ.valid")
        results = []
        worker.done.connect(lambda ok, msg: results.append((ok, msg)))
        worker.run()
        assert results == [(True, "OK")]

    def test_worker_emits_ok_false(self, qapp, monkeypatch):
        from ai_scanare import google_engine, scan_window as sw

        monkeypatch.setattr(google_engine, "test_key",
                            lambda k: {"ok": False, "message": "Cheie invalidă sau revocată."})
        worker = sw._KeyHealthWorker("AQ.bad")
        results = []
        worker.done.connect(lambda ok, msg: results.append((ok, msg)))
        worker.run()
        assert results == [(False, "Cheie invalidă sau revocată.")]

    def test_worker_handles_exception(self, qapp, monkeypatch):
        from ai_scanare import google_engine, scan_window as sw

        def boom(_k):
            raise RuntimeError("network down")

        monkeypatch.setattr(google_engine, "test_key", boom)
        worker = sw._KeyHealthWorker("AQ.x")
        results = []
        worker.done.connect(lambda ok, msg: results.append((ok, msg)))
        worker.run()
        assert results[0][0] is False
        assert "network down" in results[0][1]


# =========================================================================
# 3. _on_key_health — indicator + buton Repară
# =========================================================================
@pytest.fixture()
def window_stub(qapp, monkeypatch):
    """Instanțiază bare AIScanareWindow cu widget-urile UI necesare pentru _on_key_health."""
    from PySide6.QtWidgets import QLabel, QPushButton
    from ai_scanare import scan_window as sw

    # nu vrem să pornească health-check-ul automat sau să acceseze DB
    monkeypatch.setattr(sw.AIScanareWindow, "_start_key_health_check", lambda self: None)

    win = sw.AIScanareWindow.__new__(sw.AIScanareWindow)
    win.ai_status_lbl = QLabel()
    win.btn_ai_fix = QPushButton()
    win.btn_ai_fix.hide()
    # _refresh_ai_status apelează active_engine_status; îl stubuim ca să nu depindă de config
    def fake_refresh(self):
        self.ai_status_lbl.setText("🟢  AI activ")
        self.ai_status_lbl.setToolTip("")
        self.btn_ai_fix.hide()
    monkeypatch.setattr(sw.AIScanareWindow, "_refresh_ai_status", fake_refresh)
    return win


class TestOnKeyHealth:
    def test_invalid_key_sets_red_indicator_and_shows_fix_button(self, window_stub):
        window_stub._on_key_health(False, "Cheie invalidă sau revocată.")
        assert "🔴" in window_stub.ai_status_lbl.text()
        assert "Cheie AI invalidă" in window_stub.ai_status_lbl.text()
        assert "Cheie invalidă sau revocată." in window_stub.ai_status_lbl.toolTip()
        assert window_stub.btn_ai_fix.isHidden() is False

    def test_valid_key_hides_fix_button(self, window_stub):
        # forțează întâi starea invalidă
        window_stub._on_key_health(False, "bad")
        assert window_stub.btn_ai_fix.isHidden() is False
        # apoi verifică cheie ok => revine la refresh normal (buton ascuns)
        window_stub._on_key_health(True, "OK")
        assert window_stub.btn_ai_fix.isHidden() is True
        assert "🟢" in window_stub.ai_status_lbl.text()

    def test_on_key_health_checked_populates_cache(self, window_stub):
        from ai_scanare import scan_window as sw

        window_stub._health_key = "AQ.cachekey"
        window_stub._on_key_health_checked(False, "expired")
        assert sw.cached_key_health("AQ.cachekey") == (False, "expired")


# =========================================================================
# 4. _start_key_health_check — respectă modul offline / cheie lipsă
# =========================================================================
class TestStartKeyHealthCheck:
    def test_skips_when_offline(self, qapp, monkeypatch):
        from ai_scanare import config as ai_config
        from ai_scanare import scan_window as sw

        monkeypatch.setattr(ai_config, "get_mode", lambda: ai_config.MODE_OFFLINE)
        # dacă ar continua, ar apela get_api_key => îl facem să crape ca să detectăm
        monkeypatch.setattr(ai_config, "get_api_key", lambda: (_ for _ in ()).throw(
            AssertionError("nu ar trebui apelat în modul offline")))

        win = sw.AIScanareWindow.__new__(sw.AIScanareWindow)
        # nu trebuie să crape
        win._start_key_health_check()

    def test_skips_when_no_key(self, qapp, monkeypatch):
        from ai_scanare import config as ai_config
        from ai_scanare import scan_window as sw

        monkeypatch.setattr(ai_config, "get_mode", lambda: ai_config.MODE_AI)
        monkeypatch.setattr(ai_config, "get_api_key", lambda: "")

        win = sw.AIScanareWindow.__new__(sw.AIScanareWindow)
        # nu trebuie să pornească niciun thread
        win._start_key_health_check()
        assert not hasattr(win, "_health_thread")

    def test_uses_cache_without_starting_thread(self, qapp, monkeypatch):
        from ai_scanare import config as ai_config
        from ai_scanare import scan_window as sw
        from PySide6.QtWidgets import QLabel, QPushButton

        monkeypatch.setattr(ai_config, "get_mode", lambda: ai_config.MODE_AI)
        monkeypatch.setattr(ai_config, "get_api_key", lambda: "AQ.cached")
        sw.remember_key_health("AQ.cached", False, "cached error")

        win = sw.AIScanareWindow.__new__(sw.AIScanareWindow)
        win.ai_status_lbl = QLabel()
        win.btn_ai_fix = QPushButton()
        monkeypatch.setattr(sw.AIScanareWindow, "_refresh_ai_status", lambda self: None)

        win._start_key_health_check()
        # a folosit cache-ul => a apelat _on_key_health cu False
        assert "🔴" in win.ai_status_lbl.text()
        assert win.btn_ai_fix.isHidden() is False
        assert not hasattr(win, "_health_thread")
