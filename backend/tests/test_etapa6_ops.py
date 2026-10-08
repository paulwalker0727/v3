"""Etapa 6 — resetare parolă cetățean, alertă email MAIN offline, copii de rezervă și restaurare. Date fictive; email MOCK (jurnal local)."""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from test_etapa2_sync import BACKEND, Server, _server_py
from test_etapa3_requests import Client

sys.path.insert(0, str(Path(BACKEND).parent / "shared"))
import cimitir_backup as backups  # noqa: E402

OLD_PW, NEW_PW = "Parola-veche-12345", "Parola-noua-67890"


def mails(server: Server) -> list:
    path = server.data_dir / "logs" / "mock_mail.log"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def reset_token(mail: dict) -> str:
    return re.search(r"reset=([\w\-]+)", mail["body"]).group(1)


@pytest.fixture()
def portal(tmp_path):
    server = Server(tmp_path / "srv", mode="local")
    server.data_dir.mkdir()
    server.start()
    yield server
    server.stop()


def post(server, path, body, headers=None):
    return server.request("POST", path, body, headers=headers)


# ------------------------------------------------------------------ resetare parolă cetățean
def test_password_reset_flow_is_single_use_generic_and_invalidates_old_sessions(portal):
    citizen = Client(portal)
    status, _ = citizen.call("POST", "/api/public/account/register", json_body={"email": "ana@example.com", "password": OLD_PW})
    assert status == 200 and citizen.call("GET", "/api/public/account/me")[0] == 200

    unknown = post(portal, "/api/public/account/forgot", {"email": "nimeni@example.com"})
    known = post(portal, "/api/public/account/forgot", {"email": "ana@example.com"})
    assert unknown[0] == known[0] == 200 and unknown[1] == known[1], "răspunsul nu dezvăluie dacă emailul are cont"
    sent = mails(portal)
    assert len(sent) == 1 and sent[0]["to"] == "ana@example.com" and "/portal/cont?reset=" in sent[0]["body"]
    token = reset_token(sent[0])

    assert post(portal, "/api/public/account/reset", {"token": token, "password": "scurta"})[0] == 422
    assert post(portal, "/api/public/account/reset", {"token": "invalid", "password": NEW_PW})[0] == 400
    assert post(portal, "/api/public/account/reset", {"token": token, "password": NEW_PW})[0] == 200
    assert post(portal, "/api/public/account/reset", {"token": token, "password": "Alta-parola-123456"})[0] == 400, "linkul e de unică folosință"

    assert citizen.call("GET", "/api/public/account/me")[0] == 401, "sesiunea veche nu mai e valabilă după resetare"
    assert post(portal, "/api/public/account/login", {"email": "ana@example.com", "password": OLD_PW})[0] == 401
    fresh = Client(portal)
    assert fresh.call("POST", "/api/public/account/login", json_body={"email": "ana@example.com", "password": NEW_PW})[0] == 200
    assert fresh.call("GET", "/api/public/account/me")[0] == 200


def test_reset_link_expires_and_new_request_supersedes_the_old_one(portal):
    post(portal, "/api/public/account/register", {"email": "bob@example.com", "password": OLD_PW})
    post(portal, "/api/public/account/forgot", {"email": "bob@example.com"})
    post(portal, "/api/public/account/forgot", {"email": "bob@example.com"})
    first, second = (reset_token(m) for m in mails(portal))
    assert post(portal, "/api/public/account/reset", {"token": first, "password": NEW_PW})[0] == 400, "cererea nouă o anulează pe cea veche"
    _server_py(portal.data_dir, "conn = store.get_connection(); conn.execute('UPDATE citizen_resets SET expires_at=1'); conn.close(); print('ok')")
    assert post(portal, "/api/public/account/reset", {"token": second, "password": NEW_PW})[0] == 400, "linkul expirat este refuzat"


def test_forgot_password_is_rate_limited_per_email_and_per_ip(portal):
    codes = [post(portal, "/api/public/account/forgot", {"email": "cineva@example.com"})[0] for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    other_email = [post(portal, "/api/public/account/forgot", {"email": f"x{i}@example.com"})[0] for i in range(8)]
    assert other_email[-1] == 429, "limita per IP (10/oră) se aplică indiferent de email"


def test_reset_page_exposes_forgot_link_and_reset_form(portal):
    page = portal.get("/portal/cont")
    html = page.decode() if isinstance(page, bytes) else str(page)
    assert "forgot-link" in html and "resetToken" in html and "/forgot" in html and "/reset" in html


# ------------------------------------------------------------------ alertă email MAIN offline
def test_offline_alert_email_is_sent_once_per_episode_to_configured_recipients(tmp_path):
    server = Server(tmp_path / "online")
    server.data_dir.mkdir()
    key = _server_py(server.data_dir, "import sync_api; print(sync_api.issue_sync_key('cimitir'))")
    server.start()
    try:
        hdr = {"X-Sync-Key": key, "X-Sync-Client": "main-alert1"}
        assert server.request("POST", "/api/sync/status", {"pending": 3, "host": "PC-CIMITIR"}, headers=hdr)[0] == 200
        age = ("import json, datetime\nstore.bind_source(server.DEFAULT_SOURCE)\nc = json.loads(store.get_setting('sync_clients'))\n"
               "old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=3)).isoformat(timespec='seconds')\n"
               "for v in c.values():\n    v['last_seen'] = v['reported_at'] = old\nstore.set_setting('sync_clients', json.dumps(c))\nprint('ok')")
        run = ("import json, portal_jobs\nstore.bind_source(server.DEFAULT_SOURCE)\nprint(json.dumps(portal_jobs.run_once()))")

        no_recipients = json.loads(_server_py(server.data_dir, age + "\n" + run))
        assert no_recipients["alerts"] == 0 and mails(server) == [], "fără destinatari configurați nu pleacă nimic"

        admin = Client(server)
        assert admin.call("POST", "/admin/access/login", fields={"username": "admin", "password": "admin123"})[0] == 303
        assert admin.call("POST", "/admin/schimba-parola", fields={"old_password": "admin123", "new_password": "Parola-test-987",
                                                                     "confirm_password": "Parola-test-987"})[0] == 303
        assert admin.call("POST", "/admin/ui/settings", fields={"alert_emails": "sef@example.com; fara-arond ,al2@example.com",
                                                                 "public_base_url": "https://portal.exemplu.ro/"})[0] == 303
        first = json.loads(_server_py(server.data_dir, run))
        again = json.loads(_server_py(server.data_dir, run))
        assert first["alerts"] == 1 and again["alerts"] == 0
        sent = mails(server)
        assert sorted(m["to"] for m in sent) == ["al2@example.com", "sef@example.com"]
        assert "main-alert1" in sent[0]["subject"] and "PC-CIMITIR" in sent[0]["body"] and "Operațiuni locale restante raportate: 3" in sent[0]["body"]
        assert "alertă pe email trimisă" in admin.call("GET", "/admin/monitorizare")[1].decode()

        assert server.request("GET", "/api/sync/ping", headers=hdr)[0] == 200
        assert json.loads(_server_py(server.data_dir, run))["alerts"] == 0, "clientul e din nou online"
        _server_py(server.data_dir, age)
        assert json.loads(_server_py(server.data_dir, run))["alerts"] == 1, "un episod nou de offline trimite o alertă nouă"
    finally:
        server.stop()


# ------------------------------------------------------------------ copii de rezervă și restaurare
def make_data(tmp_path, name="data") -> Path:
    import sqlite3
    data = tmp_path / name
    (data / "media" / "cerere").mkdir(parents=True)
    (data / "media" / "cerere" / "act.pdf").write_bytes(b"%PDF-1.4 test")
    (data / ".jwt_secret").write_text("secret-local", encoding="utf-8")
    conn = sqlite3.connect(data / "cimitir.db")
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO settings VALUES ('cimitir_nume', 'Original')")
    conn.commit()
    conn.close()
    return data


def read_name(data: Path) -> str:
    import sqlite3
    conn = sqlite3.connect(data / "cimitir.db")
    try:
        return conn.execute("SELECT value FROM settings WHERE key='cimitir_nume'").fetchone()[0]
    finally:
        conn.close()


def test_scheduled_backup_is_daily_pruned_and_respects_settings(tmp_path):
    data = make_data(tmp_path)
    dest = tmp_path / "copii"
    settings = {"backup_dir": str(dest), "backup_keep_days": "3"}
    first = backups.run_scheduled(data, settings.get)
    assert first and first.parent == dest and not backups.verify(first)
    assert backups.run_scheduled(data, settings.get) is None, "a doua rulare în aceeași zi nu face altă copie"
    for age in (10, 20, 30):
        old = dest / f"cimitir-{(datetime.now() - timedelta(days=age)):%Y%m%d-%H%M%S}"
        old.mkdir()
        (old / "manifest.json").write_text("{}", encoding="utf-8")
    assert backups.prune(dest, 3, keep_min=1) == 3 and [b["name"] for b in backups.list_backups(dest)] == [first.name]
    assert backups.run_scheduled(data, {**settings, "backup_enabled": "0"}.get) is None
    forced = backups.run_scheduled(data, {**settings, "backup_enabled": "0"}.get, force=True)
    assert forced is not None
    assert backups.default_backup_dir(data).name == "data-backups"
    assert backups.run_scheduled(tmp_path / "inexistent", settings.get, force=True) is None, "eșecul nu ridică excepții"


def test_one_click_restore_is_applied_on_next_start_keeps_safety_copy_and_rejects_tampering(tmp_path):
    import sqlite3
    data = make_data(tmp_path)
    copy = backups.backup(data, tmp_path / "copii")
    conn = sqlite3.connect(data / "cimitir.db")
    conn.execute("UPDATE settings SET value='Stricat'")
    conn.commit()
    conn.close()
    (data / "media" / "cerere" / "act.pdf").write_bytes(b"corupt")
    assert backups.apply_pending_restore(data) is None

    marker = backups.schedule_restore(copy, data)
    assert marker.is_file() and read_name(data) == "Stricat", "programarea nu atinge datele curente"
    message = backups.apply_pending_restore(data)
    assert message and "restaurată" in message and not marker.exists()
    assert read_name(data) == "Original" and (data / "media" / "cerere" / "act.pdf").read_bytes() == b"%PDF-1.4 test"
    assert (data / ".jwt_secret").read_text(encoding="utf-8") == "secret-local"
    safety = [p for p in tmp_path.iterdir() if ".inainte-de-restaurare-" in p.name]
    assert len(safety) == 1 and read_name(safety[0]) == "Stricat"

    (copy / "media" / "cerere" / "act.pdf").write_bytes(b"manipulat")
    with pytest.raises(SystemExit):
        backups.schedule_restore(copy, data)
    assert not backups.pending_marker(data).exists()


def test_admin_can_backup_and_schedule_restore_from_monitoring_page(tmp_path):
    server = Server(tmp_path / "srv", mode="local")
    server.data_dir.mkdir()
    server.start()
    try:
        admin = Client(server)
        admin.call("POST", "/admin/access/login", fields={"username": "admin", "password": "admin123"})
        admin.call("POST", "/admin/schimba-parola", fields={"old_password": "admin123", "new_password": "Parola-test-987", "confirm_password": "Parola-test-987"})
        page = admin.call("GET", "/admin/monitorizare")[1].decode()
        assert "backup-empty" in page and "backup-srv-backups" not in page and "srv-backups" in page
        assert admin.call("POST", "/admin/ui/backup")[0] == 303
        folder = tmp_path / "srv-backups"
        name = backups.list_backups(folder)[0]["name"]
        page = admin.call("GET", "/admin/monitorizare")[1].decode()
        assert f'data-testid="backup-row-{name}"' in page and "backup-empty" not in page
        assert admin.call("POST", "/admin/ui/backup/restore", fields={"name": "../../etc"})[0] == 404
        assert admin.call("POST", "/admin/ui/backup/restore", fields={"name": name})[0] == 303
        assert backups.pending_marker(server.data_dir).is_file()
        assert "restore-pending" in admin.call("GET", "/admin/monitorizare")[1].decode()
        settings = admin.call("POST", "/admin/ui/settings", fields={"backup_keep_days": "30", "backup_enabled": "0"})
        assert settings[0] == 303
    finally:
        server.stop()
    restarted = Server(tmp_path / "srv", mode="local")
    restarted.start()
    try:
        assert not backups.pending_marker(restarted.data_dir).exists(), "restaurarea s-a aplicat la repornirea portalului"
        assert any(".inainte-de-restaurare-" in p.name for p in tmp_path.iterdir())
        assert restarted.get("/api/public/health")["ok"] is True
    finally:
        restarted.stop()
