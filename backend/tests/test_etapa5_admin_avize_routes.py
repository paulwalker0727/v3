# ruff: noqa: F811
"""Etapa 5 — rutele Cloud admin pentru avize/lucrări: câmpuri complete când schema le suportă, mod compatibil când nu (fără 500, fără pierderi)."""
from __future__ import annotations

from test_etapa1_audit import _login, app_env  # noqa: F401

FORM = {"nr": "AV-7", "data": "2026-03-01", "lucrare": "bordură", "executant": "Popa SRL", "data_inceput": "2026-03-05",
        "stare": "in_lucru", "observatii": "ok"}


def test_create_and_edit_full_fields_when_schema_supports_them(app_env):
    server, client, db = app_env
    _login(client)
    assert client.post("/admin/ui/loc/T-01/aviz", data=FORM, follow_redirects=False).status_code in (200, 303)
    rows = db.list_avize_for_loc("T-01")
    assert len(rows) == 1 and rows[0]["executant"] == "Popa SRL" and rows[0]["stare"] == "in_lucru" and rows[0]["data_inceput"] == "2026-03-05"
    aid = rows[0]["id"]
    assert client.post(f"/admin/ui/aviz/{aid}/edit", data={**FORM, "stare": "finalizata", "observatii": "recepționat"}).status_code in (200, 303)
    row = db.get_aviz(aid)
    assert row["stare"] == "finalizata" and row["observatii"] == "recepționat" and row["data_finalizare"]


def test_compat_mode_when_schema_lacks_028_keeps_base_fields_only(app_env, monkeypatch):
    server, client, db = app_env
    _login(client)
    monkeypatch.setattr(db, "avize_extended", lambda: False)
    assert client.post("/admin/ui/loc/T-01/aviz", data=FORM, follow_redirects=False).status_code in (200, 303)
    row = db.list_avize_for_loc("T-01")[0]
    assert row["nr"] == "AV-7" and row["lucrare"] == "bordură" and row["stare"] == "" and row["executant"] == ""
    resp = client.post(f"/admin/ui/aviz/{row['id']}/edit", data={**FORM, "lucrare": "gard", "stare": "finalizata"})
    assert resp.status_code in (200, 303)
    row = db.get_aviz(row["id"])
    assert row["lucrare"] == "gard" and row["stare"] == ""


def test_anonymous_cannot_edit_avize(app_env):
    server, client, db = app_env
    r = client.post("/admin/ui/loc/T-01/aviz", data=FORM, follow_redirects=False)
    assert r.status_code in (401, 403, 303, 307)
    assert db.list_avize_for_loc("T-01") == []
