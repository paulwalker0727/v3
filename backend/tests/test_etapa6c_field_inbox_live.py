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
            for o in live["storage"].list_prefix(f"field/{t['org']}/{t['cem']}"):         # doar obiectele create de acest run
                live["storage"].delete(f"field/{t['org']}/{t['cem']}/{o['name']}")
            c.execute("DELETE FROM cp_storage_objects WHERE cemetery_uid=%s", (t["cem"],))
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
    a2 = str(uuid.uuid4())                                           # al doilea cimitir al organizației A
    with aconn(live) as c:
        c.execute("INSERT INTO cemeteries (uid, organization_uid, name, internal_code, status, data_epoch) VALUES (%s,%s,'6C-TEST A2',%s,'DRAFT',1)", (a2, T["A"]["org"], a2))
    T["A2"] = {"org": T["A"]["org"], "cem": a2}
    T["A2"]["main"], T["A2"]["main_b"] = seed_binding({"admin": live["admin"]}, T["A"]["org"], a2, "MAIN")
    T["A2"]["phone"], T["A2"]["phone_b"] = seed_binding({"admin": live["admin"]}, T["A"]["org"], a2, "MOBILE")
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


def test_07_cross_cemetery_same_org_zero_leakage(svc, live):
    A, A2 = T["A"], T["A2"]
    i1 = svc.create_item(A["phone"], A["cem"], idem(), "DOCUMENT", PAY["DOCUMENT"], grave_uid="G-L")["item"]
    f1 = svc.attach_file(A["phone"], A["cem"], i1["uid"], str(uuid.uuid4()), "f", "application/pdf", _pdf())["file"]["uid"]
    i2 = svc.create_item(A2["phone"], A2["cem"], idem(), "WORK", PAY["WORK"], grave_uid="G-L")["item"]
    leaks = 0
    for key, cem, uid in ((A["main"], A2["cem"], i2["uid"]), (A["main"], A["cem"], i2["uid"]), (A2["main"], A["cem"], i1["uid"]), (A2["main"], A2["cem"], i1["uid"]), (A2["phone"], A2["cem"], i1["uid"])):
        for call in (lambda: svc.get_item(key, cem, uid), lambda: svc.accept(key, cem, uid), lambda: svc.reject(key, cem, uid, "x"), lambda: svc.mark_imported(key, cem, uid),
                     lambda: svc.file_url(key, cem, uid, f1), lambda: svc.list_items(key, cem)):
            try:
                r = call()
                leaks += 1 if (isinstance(r, dict) or (isinstance(r, list) and any(i["cemetery_uid"] != cem for i in r))) else 0
            except (cc.ScopeError, cc.PermissionDenied, fi.FieldInboxError):
                pass
    assert leaks == 0
    assert {i["uid"] for i in svc.list_items(A["main"], A["cem"])}.isdisjoint({i2["uid"]}) and {i["uid"] for i in svc.list_items(A2["main"], A2["cem"])}.isdisjoint({i1["uid"]})
    c = psycopg.connect(live["app"], autocommit=True)                    # RLS direct (cimitir_cp_app): contextul cimitirului A nu vede A2, deși organizația e aceeași
    c.execute("SELECT set_config('cp.org', %s, false), set_config('cp.cems', %s, false)", (A["org"], A["cem"]))
    assert c.execute("SELECT count(*) FROM cp_field_inbox_items WHERE cemetery_uid=%s", (A2["cem"],)).fetchone()[0] == 0
    assert c.execute("SELECT count(*) FROM cp_storage_objects WHERE cemetery_uid=%s", (A2["cem"],)).fetchone()[0] == 0
    assert c.execute("UPDATE cp_field_inbox_items SET status='REJECTED', reviewed_at=now(), reviewed_by=%s, rejection_reason='x' WHERE cemetery_uid=%s", (A["main_b"], A2["cem"])).rowcount == 0
    c.close()
    RES["CROSS CEMETERY"] = "0 leaks"


def test_08_sha256_signed_url_and_cross_tenant_storage(svc, live):
    A, B = T["A"], T["B"]
    it = svc.create_item(A["phone"], A["cem"], idem(), "DOCUMENT", PAY["DOCUMENT"], grave_uid="G-L")["item"]
    data = _pdf()
    cl = TestClient(fih.create_app(svc))
    hd = {"Authorization": f"Binding {A['phone']}", "Content-Type": "application/pdf", "X-File-Name": "a.pdf"}
    bad = cl.post(fih.PREFIX + f"/items/{it['uid']}/files", headers={**hd, "X-File-Uid": str(uuid.uuid4()), "X-File-Sha256": "0" * 64}, content=data)
    assert bad.status_code == 400 and bad.json()["code"] == "HASH_MISMATCH"                         # SHA-256 greșit: nimic scris
    fu = str(uuid.uuid4())
    ok = cl.post(fih.PREFIX + f"/items/{it['uid']}/files", headers={**hd, "X-File-Uid": fu, "X-File-Sha256": sha(data)}, content=data)
    assert ok.status_code == 201 and ok.json()["result"]["file"]["sha256"] == sha(data)
    path = fs.storage_path(A["org"], A["cem"], fu)
    assert live["storage"].stat(path)["size"] == len(data)
    assert live["storage"].stat(fs.storage_path(A["org"], A["cem"], str(uuid.UUID(int=0)))) is None
    hit = 0
    for key, cem in ((B["main"], B["cem"]), (B["phone"], B["cem"]), (B["main"], A["cem"])):
        for call in (lambda: svc.media.download_url(key, cem, fu), lambda: svc.file_url(key, cem, it["uid"], fu), lambda: svc.media.status(key, cem, fu)):
            try:
                r = call()
                hit += 1 if r else 0
            except (cc.ScopeError, cc.PermissionDenied, fi.FieldInboxError, fs.FieldStorageError):
                pass
    assert hit == 0                                                                                   # niciun signed URL / status cross-tenant
    u = svc.file_url(A["main"], A["cem"], it["uid"], fu, ttl=100000)
    assert u["expires_in"] == 300 and requests.get(u["url"], timeout=30).content == data
    token_url = u["url"]
    assert requests.get(token_url.split("?")[0], timeout=20).status_code >= 400                  # fără token: refuz
    RES["SIGNED URL MAX TTL"] = u["expires_in"]
