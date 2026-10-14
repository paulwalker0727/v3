# ruff: noqa: F811
"""Cloud app (cimitir_cloud_app) pe PostgreSQL LOCAL REAL (rol cimitir_cp_app, RLS) + Supabase Storage simulat (HTTP local). Fără MAIN: cheia MAIN e folosită doar ca client HTTP
(emite codul și, ulterior, citește itemurile PENDING). Rulează doar cu CIMITIR_TEST_PG_ADMIN_URL=postgresql://<superuser>@127.0.0.1:<port>/postgres (altfel SKIP)."""
from __future__ import annotations

import os
import uuid

import pytest
from starlette.testclient import TestClient

import cimitir_cloud_app as cca
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_cleanup_race_pg import adm, pdf, pgdb, sha  # noqa: F401
from test_etapa6b_field_storage import KEY as STORAGE_KEY, sup  # noqa: F401
from test_etapa6c_field_inbox_pg import fw, png  # noqa: F401

ADMIN = os.environ.get("CIMITIR_TEST_PG_ADMIN_URL", "")
pytestmark = pytest.mark.skipif(not ADMIN, reason="PostgreSQL local de test indisponibil")
GRAVE = str(uuid.uuid4())


def auth(k):
    return {"Authorization": f"Binding {k}"}


@pytest.fixture()
def cloud(fw):
    app = cca.create_app(fw["svc"], [])
    return TestClient(app), fw["T"]["A"], fw


def test_health_real_db_and_storage(cloud):
    c, _t, fw = cloud
    r = c.get("/api/v1/health")
    assert r.status_code == 200 and r.json() == {"ok": True, "api": "up", "database": "up", "storage": "configured"}
    body = r.text + c.get("/healthz").text
    for secret in (STORAGE_KEY, fw["pg"]["app"], fw["pg"]["admin"], fw["pg"]["worker"], "127.0.0.1"):
        assert secret not in body


def test_health_reports_db_down(cloud, monkeypatch):
    c, _t, fw = cloud
    monkeypatch.setattr(fw["cp"], "url", "postgresql://cimitir_cp_app@127.0.0.1:1/none")
    r = c.get("/api/v1/health")
    assert r.status_code == 503 and r.json()["database"] == "down" and r.json()["storage"] == "configured" and "127.0.0.1" not in r.text


def test_phone_end_to_end_without_main(cloud):
    c, t, fw = cloud
    issued = c.post("/api/v1/phone-pairing/codes", json={"ttl": 600}, headers=auth(t["main"]))     # MAIN: doar o cheie HTTP, nu procesul desktop
    assert issued.status_code == 201
    code = issued.json()["result"]["code"]
    page = c.get("/phone")
    assert page.status_code == 200 and "teren-page" in page.text
    redeem = c.post("/api/v1/phone-pairing/redeem", json={"code": code, "device_name": "Telefon teren"})
    assert redeem.status_code == 201
    key = redeem.json()["result"]["binding_key"]
    assert c.post("/api/v1/phone-pairing/redeem", json={"code": code}).status_code == 401
    assert c.get("/api/v1/field-inbox/me", headers=auth(key)).json()["result"] == {"cemetery_uid": t["cem"], "device_type": "MOBILE"}

    created = c.post("/api/v1/field-inbox/items", headers=auth(key), json={"idempotency_key": "cl-" + uuid.uuid4().hex, "item_type": "GRAVE_PHOTO", "payload": {"caption": "intrare"}, "grave_uid": GRAVE})
    assert created.status_code == 201
    uid = created.json()["result"]["item"]["uid"]
    data, fuid = png(), str(uuid.uuid4())
    up = c.post(f"/api/v1/field-inbox/items/{uid}/files", content=data, headers={**auth(key), "Content-Type": "image/png", "X-File-Uid": fuid, "X-File-Name": "IMG_0001.png", "X-File-Sha256": sha(data)})
    assert up.status_code == 201 and up.json()["result"]["file"]["status"] == "READY"
    # MAIN (mai târziu, de pe alt calculator) vede PENDING și poate cere URL semnat temporar; bucket-ul rămâne privat (cheia nu apare nicăieri)
    lst = c.get("/api/v1/field-inbox/items?status=PENDING", headers=auth(t["main"]))
    assert lst.status_code == 200 and [i["uid"] for i in lst.json()["result"]] == [uid]
    url = c.get(f"/api/v1/field-inbox/items/{uid}/files/{fuid}/url?ttl=60", headers=auth(t["main"]))
    assert url.status_code == 200
    body = url.text
    assert STORAGE_KEY not in body and fw["pg"]["app"] not in body
    assert url.json()["result"]["expires_in"] == 60 and url.json()["result"]["url"].startswith("http")
    over = c.get(f"/api/v1/field-inbox/items/{uid}/files/{fuid}/url?ttl=99999", headers=auth(t["main"]))
    assert over.status_code == 200 and over.json()["result"]["expires_in"] == 300           # URL semnat temporar: plafon 300 s
    assert fw["sup"].headers["apikey"] not in over.text


def test_phone_cannot_use_main_operations_or_other_tenant(cloud):
    c, t, fw = cloud
    assert c.post("/api/v1/phone-pairing/codes", json={}, headers=auth(t["phone"])).status_code == 403
    created = c.post("/api/v1/field-inbox/items", headers=auth(t["phone"]), json={"idempotency_key": "x-" + uuid.uuid4().hex, "item_type": "WORK", "payload": {"descriere": "Soclu"}, "grave_uid": GRAVE})
    uid = created.json()["result"]["item"]["uid"]
    assert c.post(f"/api/v1/field-inbox/items/{uid}/accept", headers=auth(t["phone"])).status_code == 403
    other = fw["T"]["B"]["main"]
    assert c.get(f"/api/v1/field-inbox/items/{uid}", headers=auth(other)).status_code in (404, 401)
