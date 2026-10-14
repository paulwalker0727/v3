# ruff: noqa: F811
"""Phone Capture v1 — pagina „Trimite din teren” a portalului local: autentificare, cimitir/loc/grave_uid, fără secrete în pagină."""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from test_etapa1_audit import _load_server
from test_etapa3_registries import env, make_world  # noqa: F401

PW = "Parola-op-12345"
PHONE = {"user-agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Mobile Safari/537.36"}
SECRETS = {"SUPABASE_SECRET_KEY": "sb_secret_SENTINEL_9f3", "DATABASE_URL": "postgresql://u:PGSENTINELPW@h/db", "SUPABASE_SERVICE_ROLE_KEY": "service_role_SENTINEL"}


@pytest.fixture()
def phone(tmp_path, monkeypatch):
    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("CIMITIR_FIELD_INBOX_URL", "https://cloud.example.ro/")
    server = _load_server(tmp_path / "data")
    import cimitir_auth
    import database as db
    db.init_db()
    cimitir_auth.init_auth()
    cimitir_auth.create_user("op", PW, "operator")
    db.save_plot_to_db("T-10", [(0, 0), (40, 0), (40, 30), (0, 30)], "#22c55e")
    db.save_loc_metadata("T-10", {"sector": "A", "parcela": "1", "rand": "1", "numar": "10", "disponibilitate": "Liber"})
    c = TestClient(server.app, headers=PHONE)
    r = c.post("/admin/access/login", data={"username": "op", "password": PW, "source_id": "cimitir"}, follow_redirects=False)
    assert r.status_code == 303
    return server, c, db


def test_requires_login(phone):
    server, _c, _db = phone
    anon = TestClient(server.app, headers=PHONE)
    r = anon.get("/admin/teren", follow_redirects=False)
    assert r.status_code in (303, 401) and "teren-page" not in r.text
    assert anon.get("/admin/teren/loc/T-10").status_code == 401


def test_page_menu_and_camera_inputs(phone):
    _s, c, _d = phone
    h = c.get("/admin/teren").text
    for t in ("tile-deceased", "tile-work", "tile-permit", "tile-document", "tile-grave-photo", "tile-work-photo", "f-executant", "f-data-inceput", "f-stage", "teren-send-btn"):
        assert f'data-testid="{t}"' in h
    assert h.count('accept="image/*" capture="environment"') == 2
    assert 'accept="application/pdf,image/jpeg,image/png"' in h
    assert '"cloud_url": "https://cloud.example.ro"' in h
    assert "carte_identitate" not in h and "no-store" in c.get("/admin/teren").headers["cache-control"]


def test_selected_grave_shows_cemetery_code_and_uid(phone):
    _s, c, db = phone
    conn = db.get_connection()
    uid = conn.execute("SELECT uid FROM locuri_de_veci WHERE cod_loc='T-10'").fetchone()["uid"]
    conn.close()
    assert uid
    h = c.get("/admin/teren?cod_loc=T-10").text
    m = re.search(r"window\.TEREN_CFG = (\{.*?\});</script>", h, re.S)
    import json
    cfg = json.loads(m.group(1))
    assert cfg["loc"]["cod_loc"] == "T-10" and cfg["loc"]["grave_uid"] == uid and "cemetery_label" in cfg and set(cfg) == {"cloud_url", "cemetery_uid", "cemetery_label", "loc"}
    j = c.get("/admin/teren/loc/T-10").json()
    assert j["grave_uid"] == uid and j["avize"] == []
    assert c.get("/admin/teren/loc/NU-EXISTA").status_code == 404
    assert json.loads(re.search(r"window\.TEREN_CFG = (\{.*?\});</script>", c.get("/admin/teren?cod_loc=NU").text, re.S).group(1))["loc"] is None


def test_work_targets_listed_only_with_uid(phone):
    _s, c, db = phone
    conn = db.get_connection()
    conn.execute("INSERT INTO amenajari_avize (cod_loc, nr_aviz, lucrare) VALUES ('T-10','A-1','Placare')")
    conn.close()
    assert c.get("/admin/teren/loc/T-10").json()["avize"] == []
    conn = db.get_connection()
    db._add_column_if_missing(conn, "amenajari_avize", "uid TEXT")
    conn.execute("UPDATE amenajari_avize SET uid='11111111-1111-4111-8111-111111111111'")
    conn.close()
    av = c.get("/admin/teren/loc/T-10").json()["avize"]
    assert av and av[0]["uid"] == "11111111-1111-4111-8111-111111111111" and "Placare" in av[0]["label"]


def test_no_server_secret_in_any_phone_response(phone):
    _s, c, _d = phone
    blob = "".join(c.get(u).text for u in ("/admin/teren", "/admin/teren?cod_loc=T-10", "/admin/teren/loc/T-10", "/static/teren.js", "/static/teren_ui.js", "/admin/ui/loc/T-10"))
    for v in SECRETS.values():
        assert v not in blob
    for w in ("service_role", "cimitir_cp_app", "PGSENTINELPW"):
        assert w not in blob


def test_drawer_has_send_from_field_button(phone):
    _s, c, _d = phone
    assert 'data-testid="send-from-field-btn"' in c.get("/admin/ui/loc/T-10").text


def test_cloud_not_configured_gives_empty_url(phone, monkeypatch):
    _s, c, _d = phone
    monkeypatch.delenv("CIMITIR_FIELD_INBOX_URL")
    assert '"cloud_url": ""' in c.get("/admin/teren").text
