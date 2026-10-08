"""Protecție login personal (blocare 5 × 5 min) + schimbare obligatorie a parolelor implicite."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from test_etapa1_audit import _load_server
import test_etapa3_requests as _t3

FORM = {"source_id": "cimitir"}
env3 = _t3.env3


@pytest.fixture()
def web(tmp_path):
    server = _load_server(tmp_path / "data")
    import cimitir_auth
    import database as db
    db.init_db()
    cimitir_auth.init_auth()
    cimitir_auth.create_user("op", "Parola-op-12345", "operator")
    return TestClient(server.app), cimitir_auth, db


def _login(client, user, pw):
    return client.post("/admin/access/login", data={"username": user, "password": pw, **FORM}, follow_redirects=False)


def test_default_password_forces_change_before_any_admin_page(web):
    client, auth, _ = web
    resp = _login(client, "admin", "admin123")
    assert resp.status_code == 303 and resp.headers["location"] == "/admin/schimba-parola"
    assert client.get("/admin/harta", follow_redirects=False).headers["location"] == "/admin/schimba-parola"
    assert client.get("/api/admin/map.json").status_code == 403
    assert client.post("/admin/ui/settings", data={"cimitir_nume": "X"}, follow_redirects=False).status_code == 303
    assert client.get("/admin/schimba-parola").status_code == 200

    bad = client.post("/admin/schimba-parola", data={"old_password": "admin123", "new_password": "admin123",
                                                     "confirm_password": "admin123"})
    assert bad.status_code == 400
    short = client.post("/admin/schimba-parola", data={"old_password": "admin123", "new_password": "abc",
                                                       "confirm_password": "abc"})
    assert short.status_code == 400
    mismatch = client.post("/admin/schimba-parola", data={"old_password": "admin123", "new_password": "Parola-noua-123",
                                                          "confirm_password": "altceva-123"})
    assert mismatch.status_code == 400
    wrong_old = client.post("/admin/schimba-parola", data={"old_password": "gresit", "new_password": "Parola-noua-123",
                                                           "confirm_password": "Parola-noua-123"})
    assert wrong_old.status_code == 400
    ok = client.post("/admin/schimba-parola", data={"old_password": "admin123", "new_password": "Parola-noua-123",
                                                    "confirm_password": "Parola-noua-123"}, follow_redirects=False)
    assert ok.status_code == 303 and ok.headers["location"] == "/admin/"
    assert client.get("/api/admin/map.json").status_code == 200

    assert _login(client, "admin", "admin123").status_code == 200
    again = _login(client, "admin", "Parola-noua-123")
    assert again.status_code == 303 and again.headers["location"] == "/admin/"


def test_changed_password_not_forced_and_other_user_default_forced(web):
    client, auth, _ = web
    assert _login(client, "op", "Parola-op-12345").headers["location"] == "/admin/"
    assert client.get("/api/admin/map.json").status_code == 200
    other = TestClient(client.app)
    assert _login(other, "user", "user123").headers["location"] == "/admin/schimba-parola"


def test_five_failures_lock_account_for_five_minutes_even_with_correct_password(web):
    client, auth, db = web
    for _ in range(4):
        resp = _login(client, "op", "gresit")
        assert resp.status_code == 200 and "Utilizator sau parolă greșite" in resp.text
    fifth = _login(client, "op", "gresit")
    assert "blocat" in fifth.text
    locked = _login(client, "op", "Parola-op-12345")
    assert locked.status_code == 200 and "blocat" in locked.text
    assert 0 < auth.lock_remaining("op") <= 300
    db.set_setting("loginfail:op", "0,1")
    assert _login(client, "op", "Parola-op-12345").status_code == 303


def test_success_resets_counter_and_lock_survives_new_process_state(web):
    client, auth, db = web
    for _ in range(3):
        _login(client, "op", "gresit")
    assert _login(client, "op", "Parola-op-12345").status_code == 303
    for _ in range(4):
        _login(client, "op", "gresit")
    assert auth.lock_remaining("op") == 0
    _login(client, "op", "gresit")
    assert auth.authenticate_ex("op", "Parola-op-12345")[1] == "locked"


def test_desktop_authenticate_reports_must_change_and_locked(web):
    _, auth, _ = web
    user, reason = auth.authenticate_ex("admin", "admin123")
    assert reason == "ok" and user["must_change_password"] is True
    assert auth.change_own_password(user["id"], "admin123", "Parola-desktop-1") == ""
    user2, _ = auth.authenticate_ex("admin", "Parola-desktop-1")
    assert user2["must_change_password"] is False
    assert auth.change_own_password(user["id"], "Parola-desktop-1", "user123")


def test_sync_key_failures_throttle_but_valid_key_still_works(tmp_path, monkeypatch):
    monkeypatch.setenv("CIMITIR_MODE", "online")
    server = _load_server(tmp_path / "data")
    import sync_api
    import store
    store.ensure_initialized(server.DEFAULT_SOURCE)
    key = sync_api.issue_sync_key("cimitir")
    client = TestClient(server.app)
    codes = [client.get("/api/sync/ping", headers={"X-Sync-Key": "cimitir.gresit"}).status_code for _ in range(7)]
    assert codes[:5] == [401] * 5 and codes[5:] == [429, 429]
    assert client.get("/api/sync/ping", headers={"X-Sync-Key": key}).status_code == 200


def test_portal_map_has_selection_panel_with_measurements_and_loc_page_shows_area(web):
    client, _, db = web
    db.save_plot_to_db("A-1", [(0, 0), (40, 0), (40, 30), (0, 30)], "#111")
    db.set_setting("px_per_meter", "10")
    html = client.get("/portal/harta").text
    assert 'id="plot-panel"' in html and "selectPlot" in html and "plot-scale-note" in html
    loc = client.get("/portal/loc/A-1").text
    assert "12.0 m²" in loc and "4.0 m · 3.0 m" in loc


# ---------------------------------------------------- notificări e-mail (MOCK)
def test_email_notifications_on_approve_and_reject_are_mock_idempotent_and_minimal(env3):
    import sqlite3
    from test_etapa3_requests import Client, sync
    server, key, main_dir, _ = env3
    c1, c2 = Client(server).register("c1@example.com"), Client(server).register("c2@example.com")
    code1 = c1.apply("A-1", files=[])[1]["cod_urmarire"]
    code2 = c2.apply("A-2", files=[])[1]["cod_urmarire"]
    sync(main_dir, server, key)
    sync(main_dir, server, key, "import cimitir_requests\nrows = {c['cod_urmarire']: c for c in db.list_cereri_concesionare()}\n"
                                f"cimitir_requests.approve_cerere(rows['{code1}']['id'])\n"
                                f"db.set_cerere_status(rows['{code2}']['id'], 'respins', 'Acte incomplete', None)")
    sync(main_dir, server, key)
    conn = sqlite3.connect(server.data_dir / "cimitir.db")
    rows = {r[0]: r[1:] for r in conn.execute(
        "SELECT cod_urmarire, template, status, to_email, to_masked, subject, body FROM email_outbox")}
    assert set(rows) == {code1, code2}
    assert rows[code1][:2] == ("validat", "mock_sent") and rows[code2][:2] == ("respins", "mock_sent")
    assert rows[code1][2] == "" and rows[code1][3] == "c***@e***"
    assert "aprobată" in rows[code1][4] and "respinsă" in rows[code2][4]
    assert "Acte incomplete" in rows[code2][5] and code2 in rows[code2][5]
    body_all = " ".join(str(v) for r in rows.values() for v in r)
    assert not any(s in body_all for s in ["1800101123456", "Strada Secreta 99", "0799123456", "Popa"])
    sync(main_dir, server, key)
    assert conn.execute("SELECT COUNT(*) FROM email_outbox").fetchone()[0] == 2
    staff = Client(server)
    staff.call("POST", "/admin/access/login", fields={"username": "admin", "password": "admin123", "source_id": "cimitir"})
    staff.call("POST", "/admin/schimba-parola", fields={"old_password": "admin123", "new_password": "Parola-test-987", "confirm_password": "Parola-test-987"})
    page = staff.call("GET", "/admin/monitorizare", raw=True)[1].decode()
    outbox = page.split('data-testid="email-outbox"')[1].split("Jurnal operațiuni")[0]
    assert "email-mock-banner" in outbox and "mock_sent" in outbox and "c***@e***" in outbox and "c1@example.com" not in outbox


def test_notifications_can_be_disabled_and_unknown_provider_fails_then_retries(web):
    _, auth, db = web
    import cimitir_notify as notify
    db.set_setting("notify_email_enabled", "0")
    assert notify.enqueue_status_email("CR-NONE", "validat") is False
    db.set_setting("notify_email_enabled", "1")
    notify.ensure_table()
    conn = db.get_connection()
    conn.execute("INSERT INTO email_outbox (cod_urmarire, template, to_email, to_masked, subject, body, created_at) "
                 "VALUES ('CR-1','validat','x@y.ro','x***@y***','s','b','2026-01-01T00:00:00')")
    conn.close()
    db.set_setting("email_provider", "necunoscut")
    assert notify.send_pending() == {"sent": 0, "failed": 1}
    assert notify.recent()[0]["status"] == "failed"
    db.set_setting("email_provider", "mock")
    assert notify.send_pending() == {"sent": 1, "failed": 0}
    assert notify.recent()[0]["status"] == "mock_sent"
