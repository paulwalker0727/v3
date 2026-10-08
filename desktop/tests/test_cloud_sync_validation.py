"""Validări MAIN pentru adresa serverului și cheia de sincronizare (fără rețea reală: requests mockuit)."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

DESKTOP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DESKTOP))
sys.path.insert(0, str(DESKTOP.parent / "shared"))

import cloud_sync  # noqa: E402

@pytest.fixture(autouse=True)
def _no_db(monkeypatch):
    monkeypatch.setattr(cloud_sync.db, "get_setting", lambda key: "")


KEY = "cimitir." + "a" * 43


@pytest.mark.parametrize("url,ok", [
    ("https://cimitir.onrender.com", True), ("https://cimitir.onrender.com/", True), ("https://x.ro:8443", True),
    ("http://localhost:8000", True), ("http://cimitir.onrender.com", False), ("", False), ("cimitir.onrender.com", False),
    ("https://cimitir.onrender.com/admin", False), ("https://cimitir.onrender.com/?a=1", False), ("https://exemplu ro", False),
])
def test_validate_url(url, ok):
    assert (cloud_sync.validate_url(url) == "") is ok


@pytest.mark.parametrize("key,ok", [
    (KEY, True), (f"  {KEY}  ", True), ("", False), ("fara-punct", False), ("cimitir.scurt", False), (f'"{KEY}"', False),
    (f"cimitir. {'a' * 40}", False), (".{}".format("a" * 40), False),
])
def test_validate_key(key, ok):
    assert (cloud_sync.validate_key(key) == "") is ok


class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body if body is not None else {}

    def json(self):
        return self._body


def _patch(monkeypatch, health, info):
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        out = health if url.endswith("/api/health") else info
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(cloud_sync.requests, "get", fake_get)
    monkeypatch.setattr(cloud_sync, "client_id", lambda: "main-test")
    return calls


URL = "https://cimitir.onrender.com"
HEALTH_OK = FakeResp(200, {"database": "postgres", "database_status": "online", "server_status": "online"})


def test_check_connection_ok_and_declared_url_mismatch_warns(monkeypatch):
    _patch(monkeypatch, HEALTH_OK, FakeResp(200, {"cemetery": "cimitir", "server_url": URL, "url_configured": True, "warnings": []}))
    assert cloud_sync.check_connection(URL, KEY) == (True, "Conexiune reușită cu serverul.")
    ok, msg = None, None
    _patch(monkeypatch, HEALTH_OK, FakeResp(200, {"cemetery": "cimitir", "server_url": "https://altul.onrender.com", "url_configured": True, "warnings": []}))
    ok, msg = cloud_sync.check_connection(URL, KEY)
    assert ok and "altul.onrender.com" in msg


def test_check_connection_failure_messages(monkeypatch):
    assert cloud_sync.check_connection("http://x.ro", KEY)[0] is False
    _patch(monkeypatch, requests.ConnectionError("x"), FakeResp())
    ok, msg = cloud_sync.check_connection(URL, KEY)
    assert not ok and "adoarme" in msg
    _patch(monkeypatch, FakeResp(404), FakeResp())
    assert "nu pare serverul Cimitir" in cloud_sync.check_connection(URL, KEY)[1]
    _patch(monkeypatch, FakeResp(200, {"database_status": "offline"}), FakeResp())
    assert "baza de date este OFFLINE" in cloud_sync.check_connection(URL, KEY)[1]
    _patch(monkeypatch, HEALTH_OK, FakeResp(401))
    assert "invalidă (401)" in cloud_sync.check_connection(URL, KEY)[1]
    _patch(monkeypatch, HEALTH_OK, FakeResp(429))
    assert "Prea multe" in cloud_sync.check_connection(URL, KEY)[1]
    _patch(monkeypatch, HEALTH_OK, FakeResp(200, {"cemetery": "altcimitir"}))
    assert "altui cimitir" in cloud_sync.check_connection(URL, KEY)[1]


def test_check_connection_falls_back_to_ping_on_old_server_and_detects_local_mode(monkeypatch):
    calls = _patch(monkeypatch, HEALTH_OK, FakeResp(404))
    ok, msg = cloud_sync.check_connection(URL, KEY)
    assert not ok and "mod local" in msg and calls[-1].endswith("/api/sync/ping")
