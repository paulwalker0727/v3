# ruff: noqa: F811
"""LIVE (sărit fără CIMITIR_LIVE_TESTS + CIMITIR_TEST_DATABASE_URL + SUPABASE_*). Field Inbox v1 pe Supabase TEST REAL (ref nhhczdmcocbobptogvsw), ca `cimitir_cp_app` (fără BYPASSRLS)
+ Storage privat `cimitir-private-test`. Producția (txuxkjgftopznvmooexq) e blocată de fixture-ul `live` și de gărzile din cimitir_cloud_control_pg / FieldInboxService.
Migrarea 018 se aplică DOAR dacă CIMITIR_APPLY_MIGRATION_018=1 (după ce testele locale au trecut); altfel testele cer ca ea să fie deja aplicată."""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest
import requests
from starlette.testclient import TestClient

import cimitir_bootstrap as bs
import cimitir_cloud_control as cc
import cimitir_field_inbox as fi
import cimitir_field_inbox_http as fih
import cimitir_field_storage as fs
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_field_storage_live import _jpeg, _pdf, _png, aconn, live, mk as mk_stores, penv, sha  # noqa: F401
from test_etapa6c_field_inbox_pg import PAY, seed_binding

MIG = Path(__file__).resolve().parents[1] / "cimitir" / "pg_migrations" / "018_field_inbox.sql"
pytestmark = pytest.mark.skipif(not all(os.environ.get(k) for k in ("CIMITIR_LIVE_TESTS", "CIMITIR_TEST_DATABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_BUCKET")),
                                reason="Supabase TEST (DB + Storage) nu este configurat")
T: dict = {}
RES: dict = {}


class _PG:           # seed_binding/adm folosesc dict-ul cu cheia "admin"
    pass


@pytest.fixture(scope="module", autouse=True)
def _migration(live):
    assert os.environ["CIMITIR_TEST_PROJECT_REF"] == "nhhczdmcocbobptogvsw" and "txuxkjgftopznvmooexq" not in live["admin"]       # confirmare ref ÎNAINTE de orice write
    with aconn(live) as c:
        if os.environ.get("CIMITIR_APPLY_MIGRATION_018") == "1":
            c.execute(MIG.read_text(encoding="utf-8"))
        assert c.execute("SELECT to_regclass('cp_field_inbox_items') IS NOT NULL").fetchone()[0], "migrarea 018 nu este aplicată pe TEST"
    yield
    with aconn(live) as c:                                   # curăță doar datele tenantelor create de acest run
        for t in T.values():
            c.execute("DELETE FROM cp_field_inbox_attachments WHERE cemetery_uid=%s", (t["cem"],))
            c.execute("DELETE FROM cp_field_inbox_items WHERE cemetery_uid=%s", (t["cem"],))
    if os.environ.get("LIVE_REPORT_6C"):
        Path(os.environ["LIVE_REPORT_6C"]).write_text(json.dumps(RES, indent=1, default=str))


@pytest.fixture()
def svc(live):
    cp, media = mk_stores(live)
    return fi.FieldInboxService(cp, media)


def idem():
    return "live-" + uuid.uuid4().hex


def test_00_setup(penv, live):
    w = World(penv)
    for tag, org, owner in (("A", w.org1, w.owner1), ("B", w.org2, w.owner2)):
        b = w.local(org, name=f"6C-TEST {tag}")
        code = penv.cp.issue_code(owner, org)
        plan = bs.plan_flow_a(b, penv.cp, code, penv.inst, cemetery_name=f"6C-TEST {tag}")
        bs.execute_flow_a(b, penv.cp, penv.store, plan, plan["plan_hash"], code, penv.inst)
        main = penv.store.get(bs.LocalRegistry(b).get("cloud_secret_ref"))
        T[tag] = {"org": org, "cem": b.cemetery_uid, "main": main, "main_b": main.split(".")[1]}
        T[tag]["phone"], T[tag]["phone_b"] = seed_binding({"admin": live["admin"]}, org, b.cemetery_uid, "MOBILE")
    assert live["storage"].bucket_info()["public"] is False


CASES = [("DECEASED", None, 0), ("WORK", None, 0), ("PERMIT", None, 1), ("DOCUMENT", None, 1), ("GRAVE_PHOTO", None, 1), ("WORK_PHOTO", "BEFORE", 1), ("WORK_PHOTO", "DURING", 1), ("WORK_PHOTO", "AFTER", 1)]


@pytest.mark.parametrize("typ,stage,files", CASES, ids=["deceased", "work", "permit", "document", "grave_photo", "work_photo_before", "work_photo_during", "work_photo_after"])
def test_01_items_files_and_status(svc, typ, stage, files):
    t = T["A"]
    it = svc.create_item(t["phone"], t["cem"], idem(), typ, dict(PAY[typ]), grave_uid="G-L", work_stage=stage)["item"]
    photo = typ in fi.PHOTO_TYPES
    for _ in range(files):
        data, mime = (_png(), "image/png") if photo else (_pdf(), "application/pdf")
        f = svc.attach_file(t["phone"], t["cem"], it["uid"], str(uuid.uuid4()), "f", mime, data)["file"]
        assert f["status"] == "READY" and f["sha256"] == sha(data)
        got = requests.get(svc.file_url(t["main"], t["cem"], it["uid"], f["uid"], 300)["url"], timeout=30)       # URL semnat, fără header de autentificare
        assert got.status_code == 200 and got.content == data
    assert svc.accept(t["main"], t["cem"], it["uid"])["item"]["status"] == "ACCEPTED"
    assert svc.mark_imported(t["main"], t["cem"], it["uid"])["item"]["status"] == "IMPORTED"
    RES[f"{typ}{'/' + stage if stage else ''}"] = "PASS"


def test_02_jpeg_pdf_png_and_reject(svc):
    t = T["A"]
    it = svc.create_item(t["phone"], t["cem"], idem(), "DOCUMENT", PAY["DOCUMENT"], grave_uid="G-L")["item"]
    for mime, data in (("application/pdf", _pdf()), ("image/jpeg", _jpeg()), ("image/png", _png())):
        assert svc.attach_file(t["phone"], t["cem"], it["uid"], str(uuid.uuid4()), "f", mime, data)["file"]["sha256"] == sha(data)
    assert svc.reject(t["main"], t["cem"], it["uid"], "test")["item"]["status"] == "REJECTED"
    with pytest.raises(fi.InvalidTransition):
        svc.mark_imported(t["main"], t["cem"], it["uid"])
    RES["PENDING->REJECTED"] = "PASS"


def test_03_idempotency_response_loss_concurrency(svc):
    t, key = T["A"], idem()
    first = svc.create_item(t["phone"], t["cem"], key, "WORK", PAY["WORK"], grave_uid="G-L")
    assert svc.create_item(t["phone"], t["cem"], key, "WORK", PAY["WORK"], grave_uid="G-L")["item"]["uid"] == first["item"]["uid"]
    k2, bar = idem(), threading.Barrier(6)

    def go(_):
        bar.wait()
        return svc.create_item(t["phone"], t["cem"], k2, "WORK", PAY["WORK"], grave_uid="G-L")["item"]["uid"]
    with ThreadPoolExecutor(6) as ex:
        assert len(set(ex.map(go, range(6)))) == 1
    with aconn({"admin": os.environ["CIMITIR_TEST_DATABASE_URL"]}) as c:
        assert c.execute("SELECT count(*) FROM cp_field_inbox_items WHERE idempotency_key IN (%s,%s)", (key, k2)).fetchone()[0] == 2
    RES["IDEMPOTENT"] = "PASS"


def test_04_invalid_transitions_and_phone_cannot_review(svc):
    t = T["A"]
    it = svc.create_item(t["phone"], t["cem"], idem(), "WORK", PAY["WORK"], grave_uid="G-L")["item"]
    with pytest.raises(cc.PermissionDenied):
        svc.accept(t["phone"], t["cem"], it["uid"])
    with pytest.raises(fi.InvalidTransition):
        svc.mark_imported(t["main"], t["cem"], it["uid"])
    svc.reject(t["main"], t["cem"], it["uid"], "x")
    for fn in (lambda: svc.accept(t["main"], t["cem"], it["uid"]), lambda: svc.mark_imported(t["main"], t["cem"], it["uid"])):
        with pytest.raises(fi.InvalidTransition):
            fn()
    RES["INVALID TRANSITIONS"] = "PASS"


def test_05_cross_tenant_zero_leakage(svc, live):
    A, B = T["A"], T["B"]
    ia = svc.create_item(A["phone"], A["cem"], idem(), "DOCUMENT", PAY["DOCUMENT"], grave_uid="G-L")["item"]
    fa = svc.attach_file(A["phone"], A["cem"], ia["uid"], str(uuid.uuid4()), "f", "application/pdf", _pdf())["file"]["uid"]
    leaks = 0
    for key, cem in ((B["main"], B["cem"]), (B["phone"], B["cem"]), (B["main"], A["cem"])):
        for call in (lambda: svc.get_item(key, cem, ia["uid"]), lambda: svc.file_url(key, cem, ia["uid"], fa), lambda: svc.accept(key, cem, ia["uid"]),
                     lambda: svc.reject(key, cem, ia["uid"], "x"), lambda: svc.mark_imported(key, cem, ia["uid"]),
                     lambda: svc.attach_file(key, cem, ia["uid"], str(uuid.uuid4()), "f", "application/pdf", _pdf())):
            try:
                call()
                leaks += 1
            except (cc.ScopeError, cc.PermissionDenied, fi.FieldInboxError):
                pass
    assert leaks == 0 and svc.get_item(A["main"], A["cem"], ia["uid"])["status"] == "PENDING"
    c = psycopg.connect(live["app"], autocommit=True)                        # RLS direct, ca cimitir_cp_app
    c.execute("SELECT set_config('cp.org', %s, false), set_config('cp.cems', %s, false)", (B["org"], B["cem"]))
    assert c.execute("SELECT count(*) FROM cp_field_inbox_items WHERE organization_uid=%s", (A["org"],)).fetchone()[0] == 0
    assert c.execute("UPDATE cp_field_inbox_items SET status='REJECTED', reviewed_at=now(), reviewed_by=%s, rejection_reason='x' WHERE organization_uid=%s", (B["main_b"], A["org"])).rowcount == 0
    c.close()
    RES["CROSS TENANT"] = "0 leaks"


def test_06_http_no_secret_leak_and_private_storage(svc, live):
    A = T["A"]
    cl, seen = TestClient(fih.create_app(svc)), []
    hd = {"Authorization": f"Binding {A['phone']}"}
    r = cl.post(fih.PREFIX + "/items", headers=hd, json={"idempotency_key": idem(), "item_type": "GRAVE_PHOTO", "grave_uid": "G-L", "payload": {}})
    uid, fu, data = r.json()["result"]["item"]["uid"], str(uuid.uuid4()), _jpeg()
    seen.append(r.text)
    r = cl.post(fih.PREFIX + f"/items/{uid}/files", headers={**hd, "Content-Type": "image/jpeg", "X-File-Uid": fu, "X-File-Name": "p.jpg", "X-File-Sha256": sha(data)}, content=data)
    assert r.status_code == 201
    seen.append(r.text)
    u = cl.get(fih.PREFIX + f"/items/{uid}/files/{fu}/url?ttl=900", headers={"Authorization": f"Binding {A['main']}"}).json()["result"]
    seen.append(json.dumps(u))
    assert u["expires_in"] <= 300 and requests.get(u["url"], timeout=30).content == data
    path = fs.storage_path(A["org"], A["cem"], fu)
    base = live["storage"].base
    assert all(requests.get(x, timeout=20).status_code >= 400 for x in (f"{base}/object/public/{os.environ['SUPABASE_BUCKET']}/{path}", f"{base}/object/authenticated/{os.environ['SUPABASE_BUCKET']}/{path}"))
    blob = "\n".join(seen)
    for secret in (os.environ["SUPABASE_SECRET_KEY"], live["app"], live["admin"], live["cleanup"], "service_role", "cimitir_cp_app"):
        assert secret not in blob
    assert re.search(r"postgres(ql)?://", blob) is None
    RES["PUBLIC STORAGE ACCESS"], RES["SECRET EXPOSED"] = "NO", "NO"
