# ruff: noqa: F811
"""Field Inbox v1 (migration 018) pe PostgreSQL LOCAL REAL (rolul aplicației cimitir_cp_app, fără BYPASSRLS; conexiuni/fire reale), Storage = Supabase simulat.
Rulează doar cu CIMITIR_TEST_PG_ADMIN_URL=postgresql://<superuser>@127.0.0.1:<port>/postgres (altfel SKIP). NU atinge Supabase."""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest
from starlette.testclient import TestClient

import cimitir_bootstrap as bs
import cimitir_cloud_control as cc
import cimitir_cloud_control_pg as ccp
import cimitir_field_inbox as fi
import cimitir_field_inbox_http as fih
import cimitir_field_storage as fs
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_cleanup_race_pg import adm, app as appconn, pdf, pgdb, sha  # noqa: F401
from test_etapa6b_field_storage import KEY as STORAGE_KEY, sup  # noqa: F401

ADMIN = os.environ.get("CIMITIR_TEST_PG_ADMIN_URL", "")
MIG = Path(__file__).resolve().parents[1] / "cimitir" / "pg_migrations"
pytestmark = pytest.mark.skipif(not ADMIN, reason="PostgreSQL local de test indisponibil")
LEAKS = {"item": 0, "storage": 0, "status": 0}

PAY = {"DECEASED": {"nume": "Popescu", "prenume": "Ion", "data_deces": "2026-01-02"}, "WORK": {"descriere": "Soclu nou", "executant": "Firma X"},
       "PERMIT": {"nr_aviz": "123/2026", "data_aviz": "2026-02-01"}, "DOCUMENT": {"doc_type": "adeverinta_inhumare"},
       "GRAVE_PHOTO": {"caption": "loc"}, "WORK_PHOTO": {"caption": "lucrare"}}


def png():
    return b"\x89PNG\r\n\x1a\n" + os.urandom(400)


def jpeg():
    return b"\xff\xd8\xff\xe0" + os.urandom(400) + b"\xff\xd9"


def seed_binding(pg, org, cem, dtype):
    dev, bnd = str(uuid.uuid4()), str(uuid.uuid4())
    key = f"cimitir.{bnd}.{secrets.token_urlsafe(32)}"
    with adm(pg) as c:
        c.execute("INSERT INTO devices (device_uid, organization_uid, installation_uid, device_name, device_type) VALUES (%s,%s,%s,%s,%s)", (dev, org, str(uuid.uuid4()), f"t-{dtype}", dtype))
        c.execute("INSERT INTO device_cemetery_bindings (binding_uid, device_uid, organization_uid, cemetery_uid, sync_key_hash, key_prefix, active) VALUES (%s,%s,%s,%s,%s,%s,true)",
                  (bnd, dev, org, cem, cc._h(key), key[:16]))
    return key, bnd


@pytest.fixture()
def fw(renv, pgdb, sup, monkeypatch):
    with adm(pgdb) as c:
        c.execute("TRUNCATE cp_field_inbox_attachments, cp_field_inbox_items, cp_storage_objects, cp_bootstrap_runs, cp_failed_redeems, cp_audit_log, cp_platform_admins, cp_org_roles, "
                  "cp_binding_issuers, device_pairing_codes, device_cemetery_bindings, devices, cemeteries, organizations, identities CASCADE")
    monkeypatch.setenv("CIMITIR_CP_DATABASE_URL", pgdb["app"])
    monkeypatch.setenv("CIMITIR_CP_ADMIN_DATABASE_URL", pgdb["admin"])
    monkeypatch.setenv("CIMITIR_ALLOW_TEST_SECRETSTORE", "1")
    renv.cp = ccp.PostgresControlPlane()
    w, T = World(renv), {}
    for tag, org, owner in (("A", w.org1, w.owner1), ("B", w.org2, w.owner2)):
        b = w.local(org, name=f"6C {tag}")
        code = renv.cp.issue_code(owner, org)
        plan = bs.plan_flow_a(b, renv.cp, code, renv.inst, cemetery_name=f"6C {tag}")
        bs.execute_flow_a(b, renv.cp, renv.store, plan, plan["plan_hash"], code, renv.inst)
        main = renv.store.get(bs.LocalRegistry(b).get("cloud_secret_ref"))
        T[tag] = {"org": org, "cem": b.cemetery_uid, "main": main, "main_b": main.split(".")[1]}
        T[tag]["phone"], T[tag]["phone_b"] = seed_binding(pgdb, org, b.cemetery_uid, "MOBILE")
        T[tag]["phone2"], _ = seed_binding(pgdb, org, b.cemetery_uid, "MOBILE")
    a2 = str(uuid.uuid4())                                   # al doilea cimitir al organizației A
    with adm(pgdb) as c:
        c.execute("INSERT INTO cemeteries (uid, organization_uid, name, internal_code, status, data_epoch) VALUES (%s,%s,'6C A2',%s,'DRAFT',1)", (a2, T["A"]["org"], a2))
    T["A2"] = {"org": T["A"]["org"], "cem": a2}
    T["A2"]["main"], _ = seed_binding(pgdb, T["A"]["org"], a2, "MAIN")
    T["A2"]["phone"], _ = seed_binding(pgdb, T["A"]["org"], a2, "MOBILE")
    media = fs.FieldMediaStore(renv.cp, sup, cleanup_url=pgdb["worker"])
    return {"T": T, "svc": fi.FieldInboxService(renv.cp, media), "media": media, "sup": sup, "pg": pgdb, "cp": renv.cp}


def mk(fw, tag="A", typ="WORK", who="phone", files=0, stage=None, key=None, grave="G-001", **kw):
    t, svc = fw["T"][tag], fw["svc"]
    stage = stage or ("BEFORE" if typ == "WORK_PHOTO" else None)
    res = svc.create_item(t[who], t["cem"], key or "k-" + uuid.uuid4().hex, typ, dict(PAY[typ]), grave_uid=grave, work_stage=stage, **kw)
    for _ in range(files):
        svc.attach_file(t[who], t["cem"], res["item"]["uid"], str(uuid.uuid4()), "f.png" if typ in fi.PHOTO_TYPES else "f.pdf",
                        "image/png" if typ in fi.PHOTO_TYPES else "application/pdf", png() if typ in fi.PHOTO_TYPES else pdf())
    return res["item"]


def row(fw, uid, cols="status"):
    with adm(fw["pg"]) as c:
        return c.execute(f"SELECT {cols} FROM cp_field_inbox_items WHERE uid=%s", (uid,)).fetchone()


# ===================================================================== 1. itemuri pe tipuri
CASES = [("deceased", "DECEASED", None, 0), ("work", "WORK", None, 0), ("permit", "PERMIT", None, 1), ("document", "DOCUMENT", None, 1), ("grave_photo", "GRAVE_PHOTO", None, 1),
         ("work_photo_before", "WORK_PHOTO", "BEFORE", 1), ("work_photo_during", "WORK_PHOTO", "DURING", 1), ("work_photo_after", "WORK_PHOTO", "AFTER", 1)]


@pytest.mark.parametrize("name,typ,stage,files", CASES, ids=[c[0] for c in CASES])
def test_create_item_types(fw, name, typ, stage, files):
    it = mk(fw, typ=typ, stage=stage, files=files)
    t = fw["T"]["A"]
    got = fw["svc"].get_item(t["main"], t["cem"], it["uid"])        # MAIN vede itemul trimis de telefon
    assert got["status"] == "PENDING" and got["item_type"] == typ and got["work_stage"] == stage and got["payload"] == PAY[typ] and got["grave_uid"] == "G-001"
    assert got["created_by"] == t["phone_b"] and len(got["files"]) == files and all(f["status"] == "READY" for f in got["files"])
    assert got["organization_uid"] == t["org"] and got["cemetery_uid"] == t["cem"]


def test_validation_rules(fw):
    t, svc = fw["T"]["A"], fw["svc"]
    k = lambda: "k-" + uuid.uuid4().hex                                       # noqa: E731
    bad = [dict(item_type="NOPE", payload={}, grave_uid="G"), dict(item_type="WORK_PHOTO", payload={}, grave_uid="G"),
           dict(item_type="WORK", payload=PAY["WORK"], grave_uid="G", work_stage="BEFORE"), dict(item_type="WORK_PHOTO", payload={}, grave_uid="G", work_stage="LATER"),
           dict(item_type="DECEASED", payload={"nume": "X", "cnp": "1234567890123"}, grave_uid="G"), dict(item_type="DECEASED", payload={"prenume": "X"}, grave_uid="G"),
           dict(item_type="WORK", payload=PAY["WORK"]), dict(item_type="WORK", payload=PAY["WORK"], grave_uid="../x"), dict(item_type="WORK", payload="x", grave_uid="G"),
           dict(item_type="WORK", payload={"descriere": "x" * 3000}, grave_uid="G")]
    for b in bad:
        with pytest.raises(fi.InvalidItem):
            svc.create_item(t["phone"], t["cem"], k(), **b)
    for k_ in ("short", "bad key with spaces!!", "x" * 200, None):
        with pytest.raises(fi.InvalidItem):
            svc.create_item(t["phone"], t["cem"], k_, "WORK", PAY["WORK"], grave_uid="G")
    with adm(fw["pg"]) as c:
        assert c.execute("SELECT count(*) FROM cp_field_inbox_items").fetchone()[0] == 0


# ===================================================================== 2. idempotență / reziliență
def test_idempotent_retry_and_response_loss(fw):
    t, svc = fw["T"]["A"], fw["svc"]
    key = "retry-" + uuid.uuid4().hex
    first = svc.create_item(t["phone"], t["cem"], key, "WORK", PAY["WORK"], grave_uid="G")
    assert first["replayed"] is False
    lost = svc.create_item(t["phone"], t["cem"], key, "WORK", dict(PAY["WORK"]), grave_uid="G")          # răspunsul s-a pierdut: telefonul retrimite
    again = svc.create_item(t["phone"], t["cem"], key, "WORK", PAY["WORK"], grave_uid="G")              # dublu tap
    assert lost["replayed"] and again["replayed"] and lost["item"]["uid"] == again["item"]["uid"] == first["item"]["uid"]
    with adm(fw["pg"]) as c:
        assert c.execute("SELECT count(*) FROM cp_field_inbox_items WHERE idempotency_key=%s", (key,)).fetchone()[0] == 1
    with pytest.raises(fi.IdempotencyConflict):                                  # aceeași cheie, conținut diferit
        svc.create_item(t["phone"], t["cem"], key, "WORK", {"descriere": "ALTA"}, grave_uid="G")
    other = svc.create_item(fw["T"]["B"]["phone"], fw["T"]["B"]["cem"], key, "WORK", PAY["WORK"], grave_uid="G")   # aceeași cheie în alt tenant = alt item
    assert other["replayed"] is False and other["item"]["uid"] != first["item"]["uid"]
    with pytest.raises(fi.IdempotencyConflict):                                  # alt telefon din același cimitir nu poate prelua cheia
        svc.create_item(t["phone2"], t["cem"], key, "WORK", PAY["WORK"], grave_uid="G")
    assert svc.create_item(t["main"], t["cem"], key, "WORK", PAY["WORK"], grave_uid="G")["replayed"]


def test_concurrent_identical_requests_create_one_item(fw):
    t, svc = fw["T"]["A"], fw["svc"]
    key, bar = "conc-" + uuid.uuid4().hex, threading.Barrier(10)

    def go(_):
        bar.wait()
        return svc.create_item(t["phone"], t["cem"], key, "DECEASED", PAY["DECEASED"], grave_uid="G")
    with ThreadPoolExecutor(10) as ex:
        res = list(ex.map(go, range(10)))
    assert len({r["item"]["uid"] for r in res}) == 1 and sum(not r["replayed"] for r in res) == 1
    with adm(fw["pg"]) as c:
        assert c.execute("SELECT count(*) FROM cp_field_inbox_items WHERE idempotency_key=%s", (key,)).fetchone()[0] == 1


def test_retry_after_upload_and_interrupted_upload(fw):
    t, svc, media = fw["T"]["A"], fw["svc"], fw["media"]
    key = "up-" + uuid.uuid4().hex
    it = svc.create_item(t["phone"], t["cem"], key, "DOCUMENT", PAY["DOCUMENT"], grave_uid="G")["item"]
    fu, data = str(uuid.uuid4()), pdf()
    r1 = svc.attach_file(t["phone"], t["cem"], it["uid"], fu, "adev.pdf", "application/pdf", data)
    r2 = svc.attach_file(t["phone"], t["cem"], it["uid"], fu, "adev.pdf", "application/pdf", data)           # retry după upload
    assert (r1["replayed"], r2["replayed"]) == (False, True) and r1["file"] == r2["file"]
    assert svc.create_item(t["phone"], t["cem"], key, "DOCUMENT", PAY["DOCUMENT"], grave_uid="G")["item"]["uid"] == it["uid"]   # retry la create după upload
    assert len(svc.get_item(t["phone"], t["cem"], it["uid"])["files"]) == 1
    fu2, d2 = str(uuid.uuid4()), pdf()                                      # întrerupere după scrierea în Storage, apoi reluare prin attach
    media.begin_upload(t["phone"], t["cem"], fu2, "b.pdf", "application/pdf", len(d2), sha(d2), "document")
    with pytest.raises(fs.Interrupted):
        media.upload(t["phone"], t["cem"], fu2, d2, _crash_after_storage=True)
    assert svc.attach_file(t["phone"], t["cem"], it["uid"], fu2, "b.pdf", "application/pdf", d2)["file"]["status"] == "READY"
    with pytest.raises(fs.Conflict):                                         # același file_uid, alt conținut
        svc.attach_file(t["phone"], t["cem"], it["uid"], fu2, "b.pdf", "application/pdf", pdf())
    other = svc.create_item(t["phone"], t["cem"], "o-" + uuid.uuid4().hex, "DOCUMENT", PAY["DOCUMENT"], grave_uid="G")["item"]
    with pytest.raises(fs.Conflict):                                         # un obiect aparține unui singur item
        svc.attach_file(t["phone"], t["cem"], other["uid"], fu, "adev.pdf", "application/pdf", data)


# ===================================================================== 3. status
def test_pending_to_accepted_and_imported(fw):
    t, svc = fw["T"]["A"], fw["svc"]
    it = mk(fw, typ="DOCUMENT", files=1)
    acc = svc.accept(t["main"], t["cem"], it["uid"])
    assert acc["changed"] and acc["item"]["status"] == "ACCEPTED" and acc["item"]["reviewed_by"] == t["main_b"] and acc["item"]["reviewed_at"]
    assert svc.accept(t["main"], t["cem"], it["uid"])["changed"] is False                 # replay după răspuns pierdut
    imp = svc.mark_imported(t["main"], t["cem"], it["uid"])
    assert imp["changed"] and imp["item"]["status"] == "IMPORTED" and imp["item"]["imported_at"] and imp["item"]["imported_by"] == t["main_b"]
    assert svc.mark_imported(t["main"], t["cem"], it["uid"])["changed"] is False
    got = svc.get_item(t["main"], t["cem"], it["uid"])
    assert [f["status"] for f in got["files"]] == ["IMPORTED"]


def test_pending_to_rejected(fw):
    t, svc = fw["T"]["A"], fw["svc"]
    it = mk(fw, typ="WORK")
    with pytest.raises(fi.InvalidItem):
        svc.reject(t["main"], t["cem"], it["uid"], "  ")
    rej = svc.reject(t["main"], t["cem"], it["uid"], "date greșite")
    assert rej["item"]["status"] == "REJECTED" and rej["item"]["rejection_reason"] == "date greșite" and rej["item"]["reviewed_by"] == t["main_b"]
    assert svc.reject(t["main"], t["cem"], it["uid"], "alt motiv")["changed"] is False
    assert svc.get_item(t["main"], t["cem"], it["uid"])["rejection_reason"] == "date greșite"


def test_invalid_transitions_blocked(fw):
    t, svc, T = fw["T"]["A"], fw["svc"], fw["T"]["A"]
    pend, acc, rej, imp = (mk(fw, typ="WORK") for _ in range(4))
    svc.accept(t["main"], t["cem"], acc["uid"])
    svc.reject(t["main"], t["cem"], rej["uid"], "nu")
    svc.accept(t["main"], t["cem"], imp["uid"])
    svc.mark_imported(t["main"], t["cem"], imp["uid"])
    for fn, u in ((lambda u: svc.mark_imported(t["main"], t["cem"], u), pend["uid"]),           # PENDING -> IMPORTED
                  (lambda u: svc.mark_imported(t["main"], t["cem"], u), rej["uid"]),            # REJECTED -> IMPORTED
                  (lambda u: svc.reject(t["main"], t["cem"], u, "x"), acc["uid"]),              # ACCEPTED -> REJECTED
                  (lambda u: svc.accept(t["main"], t["cem"], u), rej["uid"]),                   # REJECTED -> ACCEPTED
                  (lambda u: svc.accept(t["main"], t["cem"], u), imp["uid"]),                   # IMPORTED -> ACCEPTED
                  (lambda u: svc.reject(t["main"], t["cem"], u, "x"), imp["uid"])):             # IMPORTED -> REJECTED
        with pytest.raises(fi.InvalidTransition):
            fn(u)
    # aceleași tranziții atacate DIRECT în DB cu rolul aplicației: trigger-ul le respinge (inclusiv regresii spre PENDING)
    with appconn(fw["pg"], T["org"], T["cem"]) as c:
        for u, sql in ((pend["uid"], "status='IMPORTED', imported_at=now(), imported_by=%s"), (rej["uid"], "status='IMPORTED', imported_at=now(), imported_by=%s"),
                       (imp["uid"], "status='PENDING', reviewed_at=NULL, reviewed_by=NULL, imported_at=NULL, imported_by=NULL"),
                       (acc["uid"], "status='REJECTED', rejection_reason='x', reviewed_at=now(), reviewed_by=%s"),
                       (rej["uid"], "status='PENDING', reviewed_at=NULL, reviewed_by=NULL, rejection_reason=NULL")):
            with pytest.raises(psycopg.errors.CheckViolation):
                c.execute(f"UPDATE cp_field_inbox_items SET {sql} WHERE uid=%s", ([T["main_b"]] if "%s" in sql else []) + [u])
        with pytest.raises(psycopg.errors.CheckViolation):          # telefonul (MOBILE) nu se poate auto-aproba nici direct în DB
            c.execute("UPDATE cp_field_inbox_items SET status='ACCEPTED', reviewed_at=now(), reviewed_by=%s WHERE uid=%s", (T["phone_b"], pend["uid"]))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):   # conținutul nu se poate modifica; nu există DELETE/TRUNCATE
            c.execute("UPDATE cp_field_inbox_items SET payload='{}'::jsonb WHERE uid=%s", (pend["uid"],))
        for stmt in ("DELETE FROM cp_field_inbox_items", "DELETE FROM cp_field_inbox_attachments", "TRUNCATE cp_field_inbox_items"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(stmt)
    assert [row(fw, u)[0] for u in (pend["uid"], acc["uid"], rej["uid"], imp["uid"])] == ["PENDING", "ACCEPTED", "REJECTED", "IMPORTED"]


def test_phone_cannot_review_and_attach_blocked_after_accept(fw):
    t, svc = fw["T"]["A"], fw["svc"]
    it = mk(fw, typ="DOCUMENT", files=1)
    for fn in (lambda: svc.accept(t["phone"], t["cem"], it["uid"]), lambda: svc.reject(t["phone"], t["cem"], it["uid"], "x"), lambda: svc.mark_imported(t["phone"], t["cem"], it["uid"])):
        with pytest.raises(cc.PermissionDenied):
            fn()
    svc.accept(t["main"], t["cem"], it["uid"])
    fu = str(uuid.uuid4())
    with pytest.raises(fi.InvalidTransition):
        svc.attach_file(t["phone"], t["cem"], it["uid"], fu, "x.pdf", "application/pdf", pdf())
    empty = mk(fw, typ="GRAVE_PHOTO")
    with pytest.raises(fi.InvalidTransition):                       # itemul foto cere cel puțin un fișier
        svc.accept(t["main"], t["cem"], empty["uid"])


def test_concurrent_accept_vs_reject_has_one_winner(fw):
    t, svc = fw["T"]["A"], fw["svc"]
    for _ in range(3):
        it, bar = mk(fw, typ="WORK"), threading.Barrier(2)

        def go(fn):
            bar.wait()
            try:
                return fn()
            except fi.InvalidTransition:
                return None
        with ThreadPoolExecutor(2) as ex:
            r = list(ex.map(go, [lambda: svc.accept(t["main"], t["cem"], it["uid"]), lambda: svc.reject(t["main"], t["cem"], it["uid"], "nu")]))
        assert sum(x is not None for x in r) == 1
        assert row(fw, it["uid"])[0] in ("ACCEPTED", "REJECTED")
    it, bar = mk(fw, typ="WORK"), threading.Barrier(6)

    def acc(_):
        bar.wait()
        return svc.accept(t["main"], t["cem"], it["uid"])
    with ThreadPoolExecutor(6) as ex:
        r = list(ex.map(acc, range(6)))
    assert sum(x["changed"] for x in r) == 1


# ===================================================================== 4. fișiere
def test_files_pdf_jpeg_png_sha_signed_url_private(fw):
    t, svc, sup = fw["T"]["A"], fw["svc"], fw["sup"]
    it = svc.create_item(t["phone"], t["cem"], "f-" + uuid.uuid4().hex, "DOCUMENT", PAY["DOCUMENT"], grave_uid="G")["item"]
    blobs = {"application/pdf": pdf(), "image/jpeg": jpeg(), "image/png": png()}
    uids = {}
    for mime, data in blobs.items():
        uids[mime] = str(uuid.uuid4())
        f = svc.attach_file(t["phone"], t["cem"], it["uid"], uids[mime], "x", mime, data)["file"]
        assert f["sha256"] == sha(data) and f["size"] == len(data) and f["status"] == "READY" and f["mime"] == mime
        assert sup.stat(fs.storage_path(t["org"], t["cem"], uids[mime]))["size"] == len(data)
    assert {f["kind"] for f in svc.get_item(t["main"], t["cem"], it["uid"])["files"]} == {"document", "photo"}
    u = svc.file_url(t["main"], t["cem"], it["uid"], uids["image/png"], ttl=9999)
    assert u["expires_in"] <= 300 and "token=" in u["url"] and STORAGE_KEY not in u["url"] and u["sha256"] == sha(blobs["image/png"])
    assert svc.file_url(t["phone"], t["cem"], it["uid"], uids["image/png"])["expires_in"] <= 300       # telefonul: doar pe itemul propriu
    for bad in ("../../x", "not-a-uuid"):
        with pytest.raises(fi.InvalidItem):
            svc.file_url(t["main"], t["cem"], it["uid"], bad)
    with pytest.raises(cc.ScopeError):                               # fișier care nu aparține itemului
        svc.file_url(t["main"], t["cem"], it["uid"], str(uuid.uuid4()))
    other = mk(fw, typ="DOCUMENT", files=1)
    foreign = svc.get_item(t["main"], t["cem"], other["uid"])["files"][0]["uid"]
    with pytest.raises(cc.ScopeError):                               # fișierul altui item nu se descarcă prin itemul acesta
        svc.file_url(t["main"], t["cem"], it["uid"], foreign)



def test_file_rules_and_url_status_gate(fw):
    t, svc = fw["T"]["A"], fw["svc"]
    ph = svc.create_item(t["phone"], t["cem"], "p-" + uuid.uuid4().hex, "GRAVE_PHOTO", PAY["GRAVE_PHOTO"], grave_uid="G")["item"]
    with pytest.raises(fs.BadContent):
        svc.attach_file(t["phone"], t["cem"], ph["uid"], str(uuid.uuid4()), "x.pdf", "application/pdf", pdf())          # foto nu acceptă PDF
    with pytest.raises(fs.BadContent):
        svc.attach_file(t["phone"], t["cem"], ph["uid"], str(uuid.uuid4()), "x.png", "image/png", b"not png bytes at all" * 20)   # semnătură greșită
    with pytest.raises(fs.BadContent):
        svc.attach_file(t["phone"], t["cem"], ph["uid"], str(uuid.uuid4()), "x.gif", "image/gif", b"GIF89a" + os.urandom(20))
    d = jpeg()
    fu = str(uuid.uuid4())
    svc.attach_file(t["phone"], t["cem"], ph["uid"], fu, "x.jpg", "image/jpeg", d)
    assert svc.file_url(t["main"], t["cem"], ph["uid"], fu)["url"]
    svc.accept(t["main"], t["cem"], ph["uid"])
    assert svc.file_url(t["main"], t["cem"], ph["uid"], fu)["url"]                       # ACCEPTED: MAIN poate previzualiza/importa
    svc.mark_imported(t["main"], t["cem"], ph["uid"])
    with pytest.raises(fs.NotReady):                                                  # IMPORTED: fără URL nou
        svc.file_url(t["main"], t["cem"], ph["uid"], fu)
    rj = mk(fw, typ="DOCUMENT", files=1)
    rf = svc.get_item(t["main"], t["cem"], rj["uid"])["files"][0]["uid"]
    svc.reject(t["main"], t["cem"], rj["uid"], "nu")
    with pytest.raises(fs.NotReady):
        svc.file_url(t["main"], t["cem"], rj["uid"], rf)
    many = svc.create_item(t["phone"], t["cem"], "m-" + uuid.uuid4().hex, "DOCUMENT", PAY["DOCUMENT"], grave_uid="G")["item"]
    for _ in range(fi.MAX_FILES_PER_ITEM):
        svc.attach_file(t["phone"], t["cem"], many["uid"], str(uuid.uuid4()), "x.pdf", "application/pdf", pdf(40))
    with pytest.raises(fi.InvalidItem):
        svc.attach_file(t["phone"], t["cem"], many["uid"], str(uuid.uuid4()), "x.pdf", "application/pdf", pdf(40))


def test_linked_object_cannot_be_deleted_and_stays_in_6b(fw):
    t, svc, media = fw["T"]["A"], fw["svc"], fw["media"]
    it = mk(fw, typ="DOCUMENT", files=1)
    fu = svc.get_item(t["main"], t["cem"], it["uid"])["files"][0]["uid"]
    with pytest.raises(fs.Conflict):                                 # obiectul e legat de un item activ: delete-ul 6B e respins de DB
        media.delete(t["main"], t["cem"], fu)
    svc.reject(t["main"], t["cem"], it["uid"], "nu")
    assert media.delete(t["main"], t["cem"], fu)["status"] == "DELETED"           # după respingere, ștergerea normală 6B redevine posibilă
    with adm(fw["pg"]) as c:
        assert c.execute("SELECT count(*) FROM information_schema.columns WHERE table_name LIKE 'cp_field_inbox%%' AND data_type IN ('bytea','oid')").fetchone()[0] == 0
    ready = mk(fw, typ="DOCUMENT", files=1)
    rf = svc.get_item(t["main"], t["cem"], ready["uid"])["files"][0]["uid"]
    with adm(fw["pg"]) as c:
        c.execute("UPDATE cp_storage_objects SET updated_at = now() - interval '9 hours' WHERE uid=%s", (rf,))
    assert media.cleanup_orphans(t["main"], t["cem"], older_than_s=60) == {"stale_rows": 0, "orphan_objects": 0}   # cleanup-ul 6B nu atinge obiectele READY legate


# ===================================================================== 5. izolare tenant / securitate
def test_cross_tenant_zero_leakage(fw):
    A, B, svc = fw["T"]["A"], fw["T"]["B"], fw["svc"]
    ia = mk(fw, "A", "DOCUMENT", files=1)
    ib = mk(fw, "B", "DOCUMENT", files=1)
    fb = svc.get_item(B["main"], B["cem"], ib["uid"])["files"][0]["uid"]
    fa = svc.get_item(A["main"], A["cem"], ia["uid"])["files"][0]["uid"]
    for key, other in ((A["main"], B), (A["phone"], B), (B["main"], A), (B["phone"], A)):
        mine = A if key in (A["main"], A["phone"]) else B
        their_item, their_file = (ib, fb) if mine is A else (ia, fa)
        calls = [lambda: svc.get_item(key, mine["cem"], their_item["uid"]), lambda: svc.file_url(key, mine["cem"], their_item["uid"], their_file),
                 lambda: svc.get_item(key, other["cem"], their_item["uid"]), lambda: svc.list_items(key, other["cem"]),
                 lambda: svc.attach_file(key, mine["cem"], their_item["uid"], str(uuid.uuid4()), "x", "application/pdf", pdf()),
                 lambda: svc.accept(key, mine["cem"], their_item["uid"]), lambda: svc.reject(key, mine["cem"], their_item["uid"], "x"),
                 lambda: svc.mark_imported(key, mine["cem"], their_item["uid"]), lambda: fw["media"].download_url(key, mine["cem"], their_file)]
        for call in calls:
            with pytest.raises((cc.ScopeError, cc.PermissionDenied, fi.InvalidTransition)):
                call()
        assert all(i["uid"] != their_item["uid"] for i in svc.list_items(key, mine["cem"]))
    assert [row(fw, u)[0] for u in (ia["uid"], ib["uid"])] == ["PENDING", "PENDING"]                  # nicio schimbare de status cross-tenant
    for tag, mine, theirs in (("A", A, B), ("B", B, A)):                                                    # RLS direct, ca rolul aplicației (NU postgres)
        with appconn(fw["pg"], mine["org"], mine["cem"]) as c:
            assert c.execute("SELECT count(*) FROM cp_field_inbox_items WHERE organization_uid=%s", (theirs["org"],)).fetchone()[0] == 0
            assert c.execute("SELECT count(*) FROM cp_field_inbox_attachments WHERE organization_uid=%s", (theirs["org"],)).fetchone()[0] == 0
            assert c.execute("SELECT count(*) FROM cp_storage_objects WHERE organization_uid=%s", (theirs["org"],)).fetchone()[0] == 0
            assert c.execute("UPDATE cp_field_inbox_items SET status='REJECTED', rejection_reason='x', reviewed_at=now(), reviewed_by=%s WHERE organization_uid=%s",
                             (mine["main_b"], theirs["org"])).rowcount == 0
            with pytest.raises(psycopg.errors.Error):
                c.execute("INSERT INTO cp_field_inbox_items (uid, organization_uid, cemetery_uid, item_type, grave_uid, idempotency_key, request_hash, created_by) "
                          "VALUES (%s,%s,%s,'WORK','G','k-xxxxxxxxxxxxxxxx',%s,%s)", (str(uuid.uuid4()), theirs["org"], theirs["cem"], "a" * 64, theirs["main_b"]))
    with appconn(fw["pg"]) as c:                                     # fără context RLS: nimic vizibil
        assert c.execute("SELECT count(*) FROM cp_field_inbox_items").fetchone()[0] == 0


def test_cross_cemetery_same_organization_and_phone_scope(fw):
    A, A2, svc = fw["T"]["A"], fw["T"]["A2"], fw["svc"]
    i1 = mk(fw, "A", "WORK")
    i2 = svc.create_item(A2["phone"], A2["cem"], "k-" + uuid.uuid4().hex, "WORK", PAY["WORK"], grave_uid="G")["item"]
    for key, cem, uid in ((A["main"], A2["cem"], i2["uid"]), (A["main"], A["cem"], i2["uid"]), (A2["main"], A["cem"], i1["uid"]), (A2["main"], A2["cem"], i1["uid"])):
        with pytest.raises(cc.ScopeError):
            svc.get_item(key, cem, uid)
    with pytest.raises(cc.ScopeError):
        svc.accept(A["main"], A["cem"], i2["uid"])
    with pytest.raises(cc.ScopeError):
        svc.list_items(A["main"], A2["cem"])
    assert [i["uid"] for i in svc.list_items(A["main"], A["cem"])] == [i1["uid"]] and [i["uid"] for i in svc.list_items(A2["main"], A2["cem"])] == [i2["uid"]]
    with appconn(fw["pg"], A["org"], A["cem"]) as c:                    # aceeași organizație, alt cimitir: RLS pe cp.cems
        assert c.execute("SELECT count(*) FROM cp_field_inbox_items WHERE cemetery_uid=%s", (A2["cem"],)).fetchone()[0] == 0
    p2 = mk(fw, "A", "WORK", who="phone2")                           # telefon vs telefon în același cimitir
    assert [i["uid"] for i in svc.list_items(A["phone"], A["cem"])] == [i1["uid"]]
    with pytest.raises(cc.ScopeError):
        svc.get_item(A["phone"], A["cem"], p2["uid"])
    with pytest.raises(cc.ScopeError):
        svc.attach_file(A["phone"], A["cem"], p2["uid"], str(uuid.uuid4()), "x", "application/pdf", pdf())
    assert {i["uid"] for i in svc.list_items(A["main"], A["cem"])} == {i1["uid"], p2["uid"]}
    assert [i["uid"] for i in svc.list_items(A["main"], A["cem"], status="PENDING", item_type="WORK", limit=1)] == [i1["uid"]]
    with pytest.raises(cc.ScopeError):
        svc.list_items("cimitir." + str(uuid.uuid4()) + ".nope", None)
    with adm(fw["pg"]) as c:                                          # legătură revocată = respinsă
        c.execute("UPDATE device_cemetery_bindings SET active=false, revoked_at=now() WHERE binding_uid=%s", (A["phone_b"],))
    with pytest.raises(cc.ScopeError):
        svc.create_item(A["phone"], A["cem"], "k-" + uuid.uuid4().hex, "WORK", PAY["WORK"], grave_uid="G")


def test_rls_and_grants_least_privilege(fw):
    with adm(fw["pg"]) as c:
        for tbl in ("cp_field_inbox_items", "cp_field_inbox_attachments"):
            assert c.execute("SELECT relrowsecurity FROM pg_class WHERE relname=%s", (tbl,)).fetchone()[0]
            for priv in ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
                assert not c.execute("SELECT has_table_privilege('cimitir_cp_app', %s, %s)", (tbl, priv)).fetchone()[0]
            for role in ("public", "cimitir_storage_cleanup", "cimitir_storage_cleanup_exec"):
                assert not c.execute("SELECT has_table_privilege(%s, %s, 'SELECT')", (role, tbl)).fetchone()[0]
            assert c.execute("SELECT rolbypassrls FROM pg_roles WHERE rolname='cimitir_cp_app'").fetchone()[0] is False
        assert not c.execute("SELECT has_table_privilege('cimitir_cp_app','cp_field_inbox_attachments','UPDATE')").fetchone()[0]
        assert not c.execute("SELECT has_column_privilege('cimitir_cp_app','cp_field_inbox_items','payload','UPDATE')").fetchone()[0]
        assert c.execute("SELECT has_column_privilege('cimitir_cp_app','cp_field_inbox_items','status','UPDATE')").fetchone()[0]
        for f in sorted(MIG.glob("018*.sql")):                         # idempotent: a doua rulare nu schimbă nimic și nu eșuează
            c.execute(f.read_text(encoding="utf-8"))
        assert c.execute("SELECT count(*) FROM pg_policies WHERE tablename IN ('cp_field_inbox_items','cp_field_inbox_attachments')").fetchone()[0] == 2


def test_production_is_hard_blocked(fw):
    class _S:
        base = "https://txuxkjgftopznvmooexq.supabase.co/storage/v1"
    with pytest.raises(RuntimeError):
        fi.FieldInboxService(fw["cp"], type("M", (), {"storage": _S()})())
    with pytest.raises(RuntimeError):
        ccp.PostgresControlPlane(url="postgresql://cimitir_cp_app.txuxkjgftopznvmooexq:x@aws-0-eu.pooler.supabase.com:6543/postgres")
    migs = [f.name for f in sorted(MIG.glob("*.sql"))]
    assert migs[-4:] == ["015_field_storage.sql", "016_field_storage_cleanup.sql", "017_revoke_cleanup_role_membership.sql", "018_field_inbox.sql"]


# ===================================================================== 6. API HTTP (aceleași reguli, prin FastAPI)
def test_http_api_flow_security_and_no_secret_leak(fw):
    A, B = fw["T"]["A"], fw["T"]["B"]
    cl = TestClient(fih.create_app(fw["svc"]))
    P = fih.PREFIX
    h = lambda k: {"Authorization": f"Binding {k}"}                  # noqa: E731
    seen = []

    def call(method, path, key=None, **kw):
        hdr = {**(h(key) if key else {}), **kw.pop("headers", {})}
        r = cl.request(method, P + path, headers=hdr, **kw)
        seen.append(r.text + json.dumps(dict(r.headers)))
        return r
    assert call("GET", "/items").status_code == 401 and call("GET", "/items", "cimitir.x.y").status_code == 401
    assert cl.get(P + "/items", headers={"Authorization": f"Bearer {A['main']}"}).status_code == 401
    body = {"idempotency_key": "http-" + uuid.uuid4().hex, "item_type": "WORK_PHOTO", "work_stage": "AFTER", "grave_uid": "G-9", "payload": {"caption": "după"}}
    r1 = call("POST", "/items", A["phone"], json=body)
    r2 = call("POST", "/items", A["phone"], json=body)
    assert (r1.status_code, r2.status_code) == (201, 200) and r1.json()["result"]["item"]["uid"] == r2.json()["result"]["item"]["uid"]
    assert call("POST", "/items", A["phone"], json={**body, "payload": {"caption": "alta"}}).status_code == 409
    assert call("POST", "/items", A["phone"], json={**body, "idempotency_key": "x" * 20, "item_type": "NOPE"}).status_code == 400
    uid, data, fu = r1.json()["result"]["item"]["uid"], png(), str(uuid.uuid4())
    hd = {"Content-Type": "image/png", "X-File-Uid": fu, "X-File-Name": "poza.png", "X-File-Sha256": sha(data)}
    assert call("POST", f"/items/{uid}/files", A["phone"], content=data, headers={**hd, "Authorization": f"Binding {A['phone']}"}).status_code == 201
    assert call("POST", f"/items/{uid}/files", A["phone"], content=data, headers={**hd, "Authorization": f"Binding {A['phone']}"}).status_code == 200
    assert call("POST", f"/items/{uid}/files", A["phone"], content=data, headers={**hd, "X-File-Sha256": "0" * 64, "Authorization": f"Binding {A['phone']}"}).status_code == 400
    assert call("POST", f"/items/{uid}/files", A["phone"], content=b"x" * 50, headers={**hd, "X-File-Uid": str(uuid.uuid4()), "X-File-Sha256": "", "Authorization": f"Binding {A['phone']}"}).status_code == 415
    assert call("POST", f"/items/{uid}/files", A["phone"], content=b"x", headers={**hd, "Content-Length": str(fs.MAX_BYTES + 1), "Authorization": f"Binding {A['phone']}"}).status_code in (400, 415)
    assert call("GET", f"/items/{uid}", A["main"]).json()["result"]["files"][0]["sha256"] == sha(data)
    u = call("GET", f"/items/{uid}/files/{fu}/url?ttl=100000", A["main"]).json()["result"]
    assert u["expires_in"] <= 300 and STORAGE_KEY not in u["url"]
    assert call("POST", f"/items/{uid}/accept", A["phone"]).status_code == 403                    # telefonul nu aprobă
    for k in (B["main"], B["phone"]):                                                            # tenant B
        for m, p, kw in (("GET", f"/items/{uid}", {}), ("GET", f"/items/{uid}/files/{fu}/url", {}), ("POST", f"/items/{uid}/accept", {}),
                         ("POST", f"/items/{uid}/reject", {"json": {"reason": "x"}}), ("POST", f"/items/{uid}/imported", {})):
            rr = call(m, p, k, **kw)
            assert rr.status_code in (403, 404) and uid not in rr.text, (m, p, rr.status_code)
        assert [i for i in call("GET", "/items", k).json()["result"] if i["uid"] == uid] == []
    assert call("POST", f"/items/{uid}/accept", A["main"]).json()["result"]["item"]["status"] == "ACCEPTED"
    assert call("POST", f"/items/{uid}/reject", A["main"], json={"reason": "x"}).status_code == 409
    assert call("POST", f"/items/{uid}/imported", A["main"]).json()["result"]["item"]["status"] == "IMPORTED"
    assert call("GET", f"/items/{uid}/files/{fu}/url", A["main"]).status_code == 409
    assert call("GET", "/items?status=IMPORTED", A["main"]).json()["result"][0]["uid"] == uid and call("GET", "/items?status=BOGUS", A["main"]).status_code == 400
    big = {"idempotency_key": "big-" + uuid.uuid4().hex, "item_type": "WORK", "grave_uid": "G", "payload": {"descriere": "x" * (200 * 1024)}}
    assert call("POST", "/items", A["phone"], json=big).status_code in (400, 415)           # corp JSON limitat (128 KiB)
    assert call("POST", f"/items/{uid}/reject", A["main"], content=b"{" + b" " * (200 * 1024) + b"}").status_code in (400, 415)
    assert "constraint" not in "".join(seen).lower() and "cp_field_inbox" not in "".join(seen)    # fără detalii de schemă în erori
    assert call("DELETE", f"/items/{uid}", A["main"]).status_code in (404, 405)                    # fără DELETE remote
    blob = "\n".join(seen)
    for secret in (STORAGE_KEY, fw["pg"]["app"], fw["pg"]["admin"], fw["pg"]["worker"], "cimitir_cp_app", "service_role", "SUPABASE_SECRET", B["main"], B["phone"], A["main"], A["phone"]):
        assert secret not in blob, "secret în răspuns"
    assert re.search(r"postgres(ql)?://", blob) is None
    assert all("no-store" in s for s in seen if "Cache-Control" in s)
