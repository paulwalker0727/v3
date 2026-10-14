# ruff: noqa: F811
"""LIVE (sărit fără CIMITIR_LIVE_TESTS + CIMITIR_TEST_DATABASE_URL + SUPABASE_*). Import local din Field Inbox pe Supabase TEST REAL (ref nhhczdmcocbobptogvsw):
PostgreSQL (cimitir_cp_app, RLS) + Storage privat `cimitir-private-test` + URL-uri semnate reale; SQLite și media sunt locale (tmp). Producția (txuxkjgftopznvmooexq) este blocată
de fixture-ul `live` și de gărzile din FieldInboxService. Creează și curăță DOAR datele create de suită. Presupune migrările 018/019 deja aplicate pe TEST (nu aplică nimic)."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from starlette.testclient import TestClient

import cimitir_bootstrap as bs
import cimitir_field_inbox as fi
import cimitir_field_inbox_http as fih
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_field_storage_live import _jpeg, _pdf, _png, aconn, live, mk as mk_stores, penv, sha  # noqa: F401
from test_etapa6c_field_inbox_pg import PAY, seed_binding

DESKTOP = Path(__file__).resolve().parents[2] / "desktop"
sys.path.insert(0, str(DESKTOP))
pytestmark = pytest.mark.skipif(not all(os.environ.get(k) for k in ("CIMITIR_LIVE_TESTS", "CIMITIR_TEST_DATABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_BUCKET")),
                                reason="Supabase TEST (DB + Storage) nu este configurat")
BASE = "https://cloud-test.example.ro"
T: dict = {}
LIVE_DB = {}


class LiveHttp:
    """`requests`-like: API-ul Field Inbox REAL (routere + FieldInboxService pe PostgreSQL TEST) in-process; URL-urile semnate Supabase se descarcă REAL (fără autentificare)."""

    def __init__(self, svc):
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(fih.create_router(svc))
        self.client = TestClient(app, base_url=BASE)
        self.calls, self.drop = [], None

    def request(self, method, url, headers=None, params=None, json=None, timeout=None, allow_redirects=True):
        self.calls.append((method, url.split("?")[0]))
        if self.drop and self.drop(method, url):
            raise requests.ConnectionError("offline")
        if url.startswith(BASE):
            return self.client.request(method, url, headers=headers, params=params, json=json)
        assert url.startswith("https://nhhczdmcocbobptogvsw.supabase.co/") and not headers
        return requests.get(url, timeout=30, allow_redirects=False)


@pytest.fixture()
def svc(live):
    cp, media = mk_stores(live)
    return fi.FieldInboxService(cp, media)


@pytest.fixture()
def local(tmp_path, monkeypatch):
    import cimitir_registry
    import database as db
    import field_import as fim
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "cimitir.db"))
    monkeypatch.setattr(db, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cimitir_registry, "current_binding", lambda: None)
    db.init_db()
    fim.ensure_tables()
    db.set_setting("cloud_bound_cemetery", T["A"]["cem"])
    gid = str(uuid.uuid4())
    conn = db.get_connection()
    conn.execute("INSERT INTO locuri_de_veci (cod_loc, uid) VALUES ('L-1', ?)", (gid,))
    conn.close()
    return SimpleNamespace(db=db, fim=fim, grave=gid, root=tmp_path)


def idem():
    return "live-imp-" + uuid.uuid4().hex


def pg_status(live, uid):
    with aconn(live) as c:
        return c.execute("SELECT status FROM cp_field_inbox_items WHERE cemetery_uid=%s AND uid=%s", (T["A"]["cem"], uid)).fetchone()[0]


def rows(db, sql, *a):
    conn = db.get_connection()
    try:
        return [dict(r) for r in conn.execute(sql, a).fetchall()]
    finally:
        conn.close()


def mkclient(svc, http=None):
    import field_inbox_client as fic
    h = http or LiveHttp(svc)
    return fic.FieldCloudClient(BASE, T["A"]["main"], http=h), h


def make_item(svc, typ, stage=None, grave=None, target=None, files=1, accept=True):
    t = T["A"]
    it = svc.create_item(t["phone"], t["cem"], idem(), typ, dict(PAY[typ]), grave_uid=grave, target_uid=target, work_stage=stage)["item"]
    for _ in range(files):
        data, mime = (_png(), "image/png") if typ in fi.PHOTO_TYPES else (_pdf(), "application/pdf")
        svc.attach_file(t["phone"], t["cem"], it["uid"], str(uuid.uuid4()), "f", mime, data)
    if accept:
        svc.accept(t["main"], t["cem"], it["uid"])
    return it["uid"]


def test_00_setup(penv, live):
    assert os.environ["CIMITIR_TEST_PROJECT_REF"] == "nhhczdmcocbobptogvsw" and "txuxkjgftopznvmooexq" not in live["admin"]
    w = World(penv)
    b = w.local(w.org1, name="IMPORT-TEST A")
    code = penv.cp.issue_code(w.owner1, w.org1)
    plan = bs.plan_flow_a(b, penv.cp, code, penv.inst, cemetery_name="IMPORT-TEST A")
    bs.execute_flow_a(b, penv.cp, penv.store, plan, plan["plan_hash"], code, penv.inst)
    main = penv.store.get(bs.LocalRegistry(b).get("cloud_secret_ref"))
    T["A"] = {"org": w.org1, "cem": b.cemetery_uid, "main": main}
    T["A"]["phone"], _ = seed_binding({"admin": live["admin"]}, w.org1, b.cemetery_uid, "MOBILE")


def test_01_all_types_import_and_cloud_imported(svc, live, local):
    db, fim = local.db, local.fim
    wuid = str(uuid.uuid4())
    conn = db.get_connection()
    fim.ensure_tables()
    conn.execute("INSERT INTO amenajari_avize (cod_loc, nr_aviz, uid) VALUES ('L-1','W-1',?)", (wuid,))
    conn.close()
    c, http = mkclient(svc)
    cases = [("DECEASED", None, None, 0), ("WORK", None, None, 0), ("PERMIT", None, None, 1), ("DOCUMENT", None, None, 1), ("GRAVE_PHOTO", None, None, 1),
             ("WORK_PHOTO", "BEFORE", wuid, 1), ("WORK_PHOTO", "DURING", wuid, 1), ("WORK_PHOTO", "AFTER", wuid, 1)]
    for typ, stage, target, nfiles in cases:
        uid = make_item(svc, typ, stage, grave=local.grave, target=target, files=nfiles)
        res = fim.import_item(c, uid)
        assert res.state == fim.COMPLETE and res.cloud_final, (typ, stage, res.message)
        assert pg_status(live, uid) == "IMPORTED"
        after = svc.get_item(T["A"]["main"], T["A"]["cem"], uid)                                          # item și fișiere rămân în cloud (fără ștergere)
        assert after["status"] == "IMPORTED" and len(after["files"]) == nfiles and all(f["status"] in ("READY", "IMPORTED") for f in after["files"])
    assert len(rows(db, "SELECT * FROM inhumati")) == 1 and len(rows(db, "SELECT * FROM amenajari_avize")) == 3          # W-1 + WORK + PERMIT
    docs = rows(db, "SELECT entity_type, doc_type FROM documents ORDER BY id")
    assert sorted(d["doc_type"] for d in docs if d["entity_type"] == "aviz") == sorted(["lucrare_inainte", "lucrare_in_timpul", "lucrare_dupa", "alt_document"])
    media = [p for p in Path(db.media_root()).rglob("*") if p.is_file()]
    assert len(media) == len(docs) == 6
    blob = b"".join(p.read_bytes() for p in local.root.rglob("*") if p.is_file())
    assert b"/object/sign/" not in blob and b"token=" not in blob and os.environ["SUPABASE_SECRET_KEY"].encode() not in blob


def test_02_pending_blocked_then_accept_and_import(svc, live, local):
    db, fim = local.db, local.fim
    c, http = mkclient(svc)
    uid = make_item(svc, "GRAVE_PHOTO", grave=local.grave, accept=False)
    with pytest.raises(fim.ImportBlocked):
        fim.import_item(c, uid)
    assert pg_status(live, uid) == "PENDING" and rows(db, "SELECT * FROM documents") == []
    res = fim.accept_and_import(c, uid)
    assert res.cloud_final and pg_status(live, uid) == "IMPORTED" and len(rows(db, "SELECT * FROM documents")) == 1


def test_03_offline_after_local_commit_then_retry_finalizes(svc, live, local):
    db, fim = local.db, local.fim
    c, http = mkclient(svc)
    uid = make_item(svc, "DOCUMENT", grave=local.grave)
    http.drop = lambda m, u: u.endswith("/imported")
    res = fim.import_item(c, uid)
    assert res.state == fim.LOCAL_COMMITTED and not res.cloud_final and pg_status(live, uid) == "ACCEPTED"
    assert len(rows(db, "SELECT * FROM documents")) == 1
    http.drop = None
    n_dl = len([x for x in http.calls if "supabase" in x[1]])
    res2 = fim.import_item(c, uid)
    assert res2.state == fim.COMPLETE and pg_status(live, uid) == "IMPORTED"
    assert len(rows(db, "SELECT * FROM documents")) == 1 and len([p for p in Path(db.media_root()).rglob("*") if p.is_file()]) == 1
    assert len([x for x in http.calls if "supabase" in x[1]]) == n_dl            # fără a doua descărcare


def test_04_wrong_cemetery_and_missing_grave_blocked(svc, live, local):
    db, fim = local.db, local.fim
    c, http = mkclient(svc)
    uid = make_item(svc, "DECEASED", grave=local.grave, files=0)
    db.set_setting("cloud_bound_cemetery", str(uuid.uuid4()))
    with pytest.raises(fim.WrongRegistry):
        fim.import_item(c, uid)
    db.set_setting("cloud_bound_cemetery", T["A"]["cem"])
    uid2 = make_item(svc, "DECEASED", grave=str(uuid.uuid4()), files=0)
    with pytest.raises(fim.ImportBlocked) as e:
        fim.import_item(c, uid2)
    assert e.value.code == "MISSING_GRAVE" and rows(db, "SELECT * FROM inhumati") == []
    assert pg_status(live, uid) == "ACCEPTED" and pg_status(live, uid2) == "ACCEPTED"


def test_99_cleanup(live):
    t = T.get("A")
    if not t:
        return
    with aconn(live) as c:                                   # doar datele tenantului creat de acest run
        c.execute("DELETE FROM cp_field_inbox_attachments WHERE cemetery_uid=%s", (t["cem"],))
        c.execute("DELETE FROM cp_field_inbox_items WHERE cemetery_uid=%s", (t["cem"],))
        for o in live["storage"].list_prefix(f"field/{t['org']}/{t['cem']}"):
            live["storage"].delete(f"field/{t['org']}/{t['cem']}/{o['name']}")
        c.execute("DELETE FROM cp_storage_objects WHERE cemetery_uid=%s", (t["cem"],))
