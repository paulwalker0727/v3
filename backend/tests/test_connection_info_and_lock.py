"""Cheie MAIN + connection-info (admin și /api/sync) și logica scripts/lock_accounts.py. Server real în mod online (SQLite temporar)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from test_etapa2_sync import Server, _server_py  # noqa: E402
from test_etapa3_requests import Client  # noqa: E402
from test_etapa5_main_authority import KEY_CMD, _admin  # noqa: E402

PUBLIC = "https://cimitir-test.onrender.com"


@pytest.fixture()
def online_pub(tmp_path, monkeypatch):
    monkeypatch.setenv("CIMITIR_PUBLIC_URL", PUBLIC)
    server = Server(tmp_path / "server")
    server.data_dir.mkdir()
    key = _server_py(server.data_dir, KEY_CMD)
    server.start()
    yield server, key
    server.stop()


def _secret(key: str) -> str:
    return key.partition(".")[2]


def test_admin_connection_info_json_has_url_no_secret_and_requires_login(online_pub):
    server, key = online_pub
    assert Client(server).call("GET", "/admin/ui/connection-info")[0] in (401, 303, 307)
    admin = _admin(server)
    status, info = admin.call("GET", "/admin/ui/connection-info")
    assert status == 200 and isinstance(info, dict)
    assert info["server_url"] == PUBLIC and info["url_configured"] and info["https"]
    assert info["health_url"] == PUBLIC + "/api/health" and info["ping_url"] == PUBLIC + "/api/sync/ping"
    assert info["cemetery"] == "cimitir" and info["mode"] == "online" and info["key_configured"] is True
    assert info["key_header"] == "X-Sync-Key" and info["warnings"] == []
    assert _secret(key) not in json.dumps(info) and "sync_key_hash" not in json.dumps(info)


def test_setari_page_shows_panel_and_generate_main_key_rotates_key(online_pub):
    server, old_key = online_pub
    admin = _admin(server)
    page = admin.call("GET", "/admin/setari")[1].decode()
    assert 'data-testid="connection-info"' in page and "Generează cheie MAIN" in page and PUBLIC in page
    assert 'data-testid="sync-key-value"' not in page

    status, body = admin.call("POST", "/admin/ui/sync-key")
    page2 = body.decode()
    assert status == 200 and 'data-testid="sync-key-value"' in page2
    new_key = page2.split('data-testid="sync-key-value" class="mono">')[1].split("<")[0].strip()
    assert new_key.startswith("cimitir.") and new_key != old_key

    # cheia veche nu mai merge, cea nouă da; secretul nu mai apare la următoarea afișare a paginii
    assert server.request("GET", "/api/sync/ping", headers={"X-Sync-Key": old_key})[0] == 401
    assert server.request("GET", "/api/sync/ping", headers={"X-Sync-Key": new_key})[0] == 200
    again = admin.call("GET", "/admin/setari")[1].decode()
    assert _secret(new_key) not in again
    audit = admin.call("GET", "/admin/monitorizare")[1].decode()
    assert "sync_key_issued" in audit or "cheie MAIN generată" in audit


def test_sync_connection_info_needs_key_and_never_returns_it(online_pub):
    server, key = online_pub
    assert server.request("GET", "/api/sync/connection-info")[0] == 401
    status, info = server.request("GET", "/api/sync/connection-info", headers={"X-Sync-Key": key})
    assert status == 200 and info["server_url"] == PUBLIC and info["cemetery"] == "cimitir"
    assert _secret(key) not in json.dumps(info)


def test_connection_info_warns_without_public_url_and_non_https(tmp_path, monkeypatch):
    monkeypatch.delenv("CIMITIR_PUBLIC_URL", raising=False)
    server = Server(tmp_path / "server")
    server.data_dir.mkdir()
    key = _server_py(server.data_dir, KEY_CMD)
    server.start()
    try:
        info = server.request("GET", "/api/sync/connection-info", headers={"X-Sync-Key": key})[1]
        assert info["url_configured"] is False and info["server_url"].startswith("http://127.0.0.1")
        text = " ".join(info["warnings"])
        assert "CIMITIR_PUBLIC_URL" in text
        info2 = server.request("GET", "/api/sync/connection-info", headers={"X-Sync-Key": key, "X-Forwarded-Proto": "https", "X-Forwarded-Host": "x.onrender.com"})[1]
        assert info2["server_url"] == "https://x.onrender.com" and info2["https"]
    finally:
        server.stop()


# ---------------------------------------------------------------- scripts/lock_accounts.py
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import lock_accounts  # noqa: E402


def _u(name, role="user", active=1, uid=0):
    return {"id": uid, "username": name, "role": role, "active": active}


def test_lock_accounts_targets_defaults_and_tst_but_keeps_alin():
    keep = {"alin"}
    assert [lock_accounts.is_target(n, keep) for n in ("admin", "User", "tst_a", "TST_b", "Alin", "maria", "administrator", "tester")] == \
        [True, True, True, True, False, False, False, False]


def test_lock_accounts_plan_never_leaves_zero_admins():
    users = [_u("admin", "admin", 1, 1), _u("user", "user", 1, 2), _u("tst_x", "operator", 1, 3), _u("Alin", "admin", 1, 4)]
    p = lock_accounts.plan(users, {"alin"})
    assert [u["username"] for u in p["to_disable"]] == ["admin", "user", "tst_x"] and [u["username"] for u in p["remaining_admins"]] == ["Alin"]
    only_defaults = [_u("admin", "admin", 1, 1), _u("user", "user", 1, 2)]
    assert lock_accounts.plan(only_defaults, {"alin"})["remaining_admins"] == []
    already = [_u("admin", "admin", 0, 1), _u("Alin", "admin", 1, 2)]
    assert lock_accounts.plan(already, {"alin"})["to_disable"] == []
