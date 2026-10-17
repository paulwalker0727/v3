# ruff: noqa: F811
"""Etapele 7-9: filtrarea datelor personale pe server, documente MAIN <-> Cloud <-> Mobile, izolare între organizații — PostgreSQL LOCAL REAL (rol cimitir_cp_app, RLS), HTTP real prin TestClient,
Storage = server Supabase SIMULAT local (nu e LIVE). Rulează doar cu CIMITIR_TEST_PG_ADMIN_URL (altfel SKIP)."""
from __future__ import annotations

import hashlib
import json
import os
import uuid

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import cimitir_mobile_registry as mr
import cimitir_sync_v2 as sv
import cimitir_sync_v2_docs as sd
import cimitir_sync_v2_http as svh
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_cleanup_race_pg import adm, pgdb  # noqa: F401
from test_etapa6b_field_storage import sup  # noqa: F401
from test_etapa6c_field_inbox_pg import fw  # noqa: F401
from test_sync_v2_pg import H, hx, op, pull, push, put_caps

pytestmark = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PostgreSQL local de test indisponibil")

CNP, CI, OCNP, OCI, ICNP = "1800101123456", "XB123456", "2750505654321", "XC654321", "1450303111222"


def png(n=64, salt=b""):
    return b"\x89PNG\r\n\x1a\n" + salt + b"\x00" * n


def pdf(salt=b""):
    return b"%PDF-1.4\n" + salt + b"%%EOF"


@pytest.fixture()
def dx(fw):
    with adm(fw["pg"]) as c:
        c.execute("TRUNCATE sync_v2_documents, sync_v2_ops, sync_v2_conflicts, sync_v2_permissions, sync_v2_runs, sync_tombstones, inhumati, plati, contracte, concesionari, "
                  "amenajari_avize, locuri_de_veci CASCADE")
    sync = sv.SyncV2Service(fw["svc"])
    docs = sd.DocumentService(sync, fw["sup"])
    app = FastAPI()
    app.include_router(svh.create_router(sync, docs))
    return {**fw, "client": TestClient(app), "mob": mr.MobileRegistryService(fw["svc"]), "docs": docs}


def seed(dx, tag="A", code="D-1"):
    key = dx["T"][tag]["main"]
    loc, ben, ctr, inh = hx(), hx(), hx(), hx()
    res = push(dx, key, [
        op("locuri_de_veci", loc, {"cod_loc": code, "points": [[0, 0], [9, 0], [9, 9]], "sector": "S1"}),
        op("concesionari", ben, {"nume": "Ionescu", "prenume": "Maria", "cnp": CNP, "bi_ci": CI, "telefon": "0700"}),
        op("contracte", ctr, {"numar_contract": "C-9", "owner_cnp": OCNP, "owner_id_card_serie_number": OCI, "loc_uid": loc, "concesionar_uid": ben}),
        op("inhumati", inh, {"nume": "Popescu", "prenume": "Ion", "cnp": ICNP, "loc_uid": loc}),
    ])
    assert [r["status"] for r in res] == ["applied"] * 4, res
    return {"main": key, "loc": loc, "ben": ben, "ctr": ctr, "inh": inh}


def grant(dx, role_caps, tag="A", who="phone_b"):
    return put_caps(dx, dx["T"][tag]["main"], dx["T"][tag][who], capabilities=role_caps)


def dec(dx, key, **kw):
    r = dx["client"].post("/api/v1/sync-v2/documents/declare", json=kw, headers=H(key))
    return r


def meta(data, entity, entity_uid, uid=None, mime="image/png", **kw):
    return dict(uid=uid or hx(), entity=entity, entity_uid=entity_uid, doc_type=kw.pop("doc_type", "fotografie_loc"), kind=kw.pop("kind", "photo"),
                filename=kw.pop("filename", "foto.png"), mime=mime, size=len(data), sha256=hashlib.sha256(data).hexdigest(), **kw)


def put(dx, key, uid, data):
    return dx["client"].put(f"/api/v1/sync-v2/documents/{uid}/content", content=data, headers=H(key))


def doc_list(dx, key, since=0, **q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    r = dx["client"].get(f"/api/v1/sync-v2/documents?since={since}&{qs}", headers=H(key))
    assert r.status_code == 200, r.text
    return r.json()["result"]


# ===================================================================== A. date personale filtrate pe SERVER
SENS = (CNP, CI, OCNP, OCI, ICNP)


def test_phone_never_receives_cnp_or_id_by_default(dx):
    s = seed(dx)
    phone = dx["T"]["A"]["phone"]
    for entity in ("concesionari", "contracte", "inhumati"):
        blob = json.dumps(pull(dx, phone, entity))
        assert not any(x in blob for x in SENS), entity
        for ch in pull(dx, phone, entity)["changes"]:
            assert not (set(ch["data"]) & {"cnp", "bi_ci", "owner_cnp", "owner_id_card_serie_number"})
    for ent, uid in (("concesionari", s["ben"]), ("contracte", s["ctr"]), ("inhumati", s["inh"])):
        r = dx["client"].get(f"/api/v1/sync-v2/row?entity={ent}&uid={uid}", headers=H(phone))
        assert r.status_code == 200 and not any(x in r.text for x in SENS)
    detail = json.dumps(dx["mob"].grave_detail(phone, s["loc"]), default=str)
    assert not any(x in detail for x in SENS)
    found = json.dumps(dx["mob"].search(phone, "Ionescu"), default=str)
    assert "Ionescu" in found and not any(x in found for x in SENS)
    # căutarea după CNP nu găsește nimic (nu se poate deduce existența unui CNP)
    assert dx["mob"].search(phone, CNP[:8])["concessionaires"] == [] and dx["mob"].search(phone, ICNP[:8])["burials"] == []
    # MAIN primește tot
    assert pull(dx, s["main"], "concesionari")["changes"][0]["data"]["cnp"] == CNP
    assert pull(dx, s["main"], "contracte")["changes"][0]["data"]["owner_id_card_serie_number"] == OCI


def test_phone_edit_cannot_read_overwrite_or_erase_sensitive_fields(dx):
    s = seed(dx)
    phone = dx["T"]["A"]["phone"]
    assert grant(dx, ["view_map", "read_registers", "manage_contracts", "register_burials"]).status_code == 200
    ch = pull(dx, phone, "concesionari")["changes"][0]
    res = push(dx, phone, [op("concesionari", s["ben"], {**ch["data"], "telefon": "0799", "cnp": "9999999999999", "bi_ci": "ZZ000000"}, base=ch["version"])])
    assert res[0]["status"] == "applied"
    row = pull(dx, s["main"], "concesionari")["changes"][0]["data"]
    assert row["telefon"] == "0799" and row["cnp"] == CNP and row["bi_ci"] == CI          # valorile sensibile rămân neatinse
    ich = pull(dx, phone, "inhumati")["changes"][0]
    res = push(dx, phone, [op("inhumati", s["inh"], {**ich["data"], "observatii": "x"}, base=ich["version"])])
    assert res[0]["status"] == "applied" and pull(dx, s["main"], "inhumati")["changes"][0]["data"]["cnp"] == ICNP
    # înregistrare nouă de pe telefon: câmpurile sensibile sunt ignorate
    new = hx()
    res = push(dx, phone, [op("inhumati", new, {"nume": "Nou", "cnp": "1111111111111", "loc_uid": s["loc"]})])
    assert res[0]["status"] == "applied"
    assert [c for c in pull(dx, s["main"], "inhumati")["changes"] if c["uid"] == new][0]["data"]["cnp"] in (None, "")


def test_conflict_and_push_responses_and_logs_are_redacted_for_phone(dx):
    s = seed(dx)
    phone = dx["T"]["A"]["phone"]
    grant(dx, ["view_map", "read_registers", "manage_contracts"])
    ch = pull(dx, phone, "concesionari")["changes"][0]
    push(dx, s["main"], [op("concesionari", s["ben"], {**pull(dx, s["main"], "concesionari")["changes"][0]["data"], "telefon": "0711"}, base=ch["version"])])   # MAIN avansează versiunea
    res = push(dx, phone, [op("concesionari", s["ben"], {**ch["data"], "telefon": "0722"}, base=ch["version"])])
    assert res[0]["status"] == "conflict" and not any(x in json.dumps(res) for x in SENS)
    cf = dx["client"].get("/api/v1/sync-v2/conflicts?all=1", headers=H(phone))
    assert cf.status_code == 200 and not any(x in cf.text for x in SENS) and cf.json()["result"]["conflicts"]
    mc = dx["client"].get("/api/v1/sync-v2/conflicts?all=1", headers=H(s["main"]))
    assert CNP in mc.text                                       # MAIN vede conflictul complet
    with adm(dx["pg"]) as c:                                    # jurnalul din cloud (răspunsurile stocate pentru telefon) nu conține date sensibile
        logged = json.dumps([r[0] for r in c.execute("SELECT response FROM sync_v2_ops WHERE device_type='MOBILE'").fetchall()])
    assert not any(x in logged for x in SENS)


def test_view_pii_is_explicit_and_administrator_template_excludes_it(dx):
    s = seed(dx)
    phone = dx["T"]["A"]["phone"]
    assert "view_pii" not in sv.ROLE_PRESETS["administrator"]["caps"] and all("view_pii" not in v["caps"] for v in sv.ROLE_PRESETS.values())
    assert put_caps(dx, s["main"], dx["T"]["A"]["phone_b"], role="administrator").status_code == 200
    assert CNP not in json.dumps(pull(dx, phone, "concesionari"))
    assert grant(dx, ["view_map", "read_registers", "view_pii"]).status_code == 200
    assert pull(dx, phone, "concesionari")["changes"][0]["data"]["cnp"] == CNP
    assert dx["mob"].search(phone, CNP[:8])["concessionaires"]
    # alt telefon al aceluiași cimitir rămâne filtrat
    assert CNP not in json.dumps(pull(dx, dx["T"]["A"]["phone2"], "concesionari"))
    # org B nu vede nimic din org A
    assert pull(dx, dx["T"]["B"]["phone"], "concesionari")["changes"] == []


# ===================================================================== B. documente MAIN -> Cloud -> Mobile
def test_main_document_reaches_phone_with_integrity_and_dedupe(dx):
    s = seed(dx)
    main, phone = s["main"], dx["T"]["A"]["phone"]
    data = png(200, b"unic")
    m = meta(data, "locuri_de_veci", s["loc"])
    r = dec(dx, main, **m)
    assert r.status_code == 200 and r.json()["result"]["status"] == "upload"
    assert doc_list(dx, phone)["documents"] == []                         # PENDING nu se vede pe telefon
    assert put(dx, main, m["uid"], data).status_code == 200
    assert dec(dx, main, **m).json()["result"]["status"] == "ready"      # idempotent
    assert put(dx, main, m["uid"], data).status_code == 200              # reluare = același rezultat
    got = doc_list(dx, phone, entity="locuri_de_veci", entity_uid=s["loc"])["documents"]
    assert len(got) == 1 and got[0]["uid"] == m["uid"] and got[0]["sha256"] == m["sha256"] and "storage_path" not in got[0]
    f = dx["client"].get(f"/api/v1/sync-v2/documents/{m['uid']}/file", headers=H(phone))
    assert f.status_code == 200 and f.content == data and f.headers["x-content-sha256"] == m["sha256"] and f.headers["content-type"] == "image/png"
    # duplicate: alt uid, același conținut, aceeași entitate -> nu se creează
    d2 = dec(dx, main, **meta(data, "locuri_de_veci", s["loc"])).json()["result"]
    assert d2["status"] == "duplicate" and d2["duplicate_of"] == m["uid"]
    with adm(dx["pg"]) as c:
        assert c.execute("SELECT count(*) FROM sync_v2_documents").fetchone()[0] == 1
    # același conținut la o ALTĂ entitate este permis (atașament logic distinct)
    assert dec(dx, main, **meta(data, "concesionari", s["ben"], doc_type="alt_document", kind="document")).json()["result"]["status"] == "upload"
    # calea din Storage e construită pe server și nu apare în API
    with adm(dx["pg"]) as c:
        path = c.execute("SELECT storage_path FROM sync_v2_documents WHERE uid=%s", (m["uid"],)).fetchone()[0]
    assert path.startswith(f"docs/{dx['T']['A']['org']}/{dx['T']['A']['cem']}/")


def test_upload_validation_hash_magic_type_and_identity_documents(dx):
    s = seed(dx)
    main = s["main"]
    good = png(50, b"v")
    m = meta(good, "locuri_de_veci", s["loc"])
    dec(dx, main, **m)
    assert put(dx, main, m["uid"], good + b"x").status_code == 400                  # hash/mărime greșite: nimic în Storage
    assert dx["sup"].stat(f"docs/{dx['T']['A']['org']}/{dx['T']['A']['cem']}/{m['uid']}") is None
    fake = b"MZ\x90\x00 executabil deghizat"
    m2 = meta(fake, "locuri_de_veci", s["loc"])
    dec(dx, main, **m2)
    assert put(dx, main, m2["uid"], fake).status_code == 415                       # semnătură necorespunzătoare
    assert dec(dx, main, **meta(pdf(), "locuri_de_veci", s["loc"], mime="text/html", doc_type="x")).status_code == 415
    assert dec(dx, main, **meta(pdf(b"i"), "concesionari", s["ben"], mime="application/pdf", doc_type="carte_identitate", kind="document")).status_code == 415
    big = meta(good, "locuri_de_veci", s["loc"])
    big["size"] = 26 * 1024 * 1024
    assert dec(dx, main, **big).status_code == 415
    missing_parent = dec(dx, main, **meta(good, "locuri_de_veci", hx())).json()["result"]
    assert missing_parent == {"status": "retry", "reason": "missing_parent"}
    assert put(dx, main, hx(), good).status_code == 409                            # nedeclarat
    ok = put(dx, main, m["uid"], good)
    assert ok.status_code == 200 and ok.json()["result"]["status"] == "READY"       # după eșec, încărcarea corectă reușește


def test_interrupted_upload_resumes_without_loss(dx):
    s = seed(dx)
    data = pdf(b"resume")
    m = meta(data, "contracte", s["ctr"], mime="application/pdf", kind="document", doc_type="contract_concesiune", filename="c.pdf")
    dec(dx, s["main"], **m)
    path = f"docs/{dx['T']['A']['org']}/{dx['T']['A']['cem']}/{m['uid']}"
    dx["sup"].put_bytes(path, data, "application/pdf")                              # obiect scris, PostgreSQL încă PENDING (crash)
    r = put(dx, s["main"], m["uid"], data)
    assert r.status_code == 200 and r.json()["result"]["status"] == "READY"
    # obiect existent cu alt conținut: refuzat, nu se suprascrie
    d2 = pdf(b"altceva")
    m2 = meta(d2, "contracte", s["ctr"], mime="application/pdf", kind="document", doc_type="contract_concesiune")
    dec(dx, s["main"], **m2)
    dx["sup"].put_bytes(f"docs/{dx['T']['A']['org']}/{dx['T']['A']['cem']}/{m2['uid']}", b"%PDF-1 corupt", "application/pdf")
    assert put(dx, s["main"], m2["uid"], d2).status_code == 409


def test_phone_permissions_delete_and_soft_delete_from_main(dx):
    s = seed(dx)
    main, phone = s["main"], dx["T"]["A"]["phone"]
    data = png(40, b"tel")
    m = meta(data, "locuri_de_veci", s["loc"])
    assert dec(dx, phone, **m).status_code == 403                                   # implicit telefonul nu poate încărca
    assert grant(dx, ["view_map", "read_registers", "upload_documents"]).status_code == 200
    assert dec(dx, phone, **m).status_code == 200 and put(dx, phone, m["uid"], data).status_code == 200
    assert put(dx, dx["T"]["A"]["phone2"], m["uid"], data).status_code in (403, 404, 409)    # alt telefon nu atinge încărcarea altuia
    assert dx["client"].delete(f"/api/v1/sync-v2/documents/{m['uid']}", headers=H(phone)).status_code == 403
    main_docs = doc_list(dx, main)["documents"]
    assert main_docs[0]["origin"] == "MOBILE" and main_docs[0]["status"] == "READY"
    cur = main_docs[-1]["seq"]
    assert dx["client"].delete(f"/api/v1/sync-v2/documents/{m['uid']}", headers=H(main)).status_code == 200
    gone = doc_list(dx, phone, since=cur)["documents"]
    assert gone and gone[0]["deleted"] is True
    assert dx["client"].get(f"/api/v1/sync-v2/documents/{m['uid']}/file", headers=H(phone)).status_code == 409
    path = f"docs/{dx['T']['A']['org']}/{dx['T']['A']['cem']}/{m['uid']}"
    assert dx["sup"].stat(path) is not None                                         # octeții nu se pierd la ștergere logică
    assert dec(dx, main, **m).status_code == 409                                    # un document șters nu reapare silențios


def test_document_read_follows_register_permissions_and_tenant_isolation(dx):
    s = seed(dx)
    main, phone = s["main"], dx["T"]["A"]["phone"]
    data = pdf(b"plata")
    m = meta(data, "plati", hx(), mime="application/pdf", kind="document", doc_type="chitanta")
    res = push(dx, main, [op("plati", m["entity_uid"], {"cod_loc": "D-1", "suma": 10.0, "contract_uid": s["ctr"]})])
    assert res[0]["status"] == "applied"
    dec(dx, main, **m)
    put(dx, main, m["uid"], data)
    assert doc_list(dx, phone)["documents"] == []                                   # „limitat" nu citește plăți => nici documentele lor
    assert dx["client"].get(f"/api/v1/sync-v2/documents/{m['uid']}/file", headers=H(phone)).status_code == 404
    grant(dx, ["view_map", "read_registers", "read_finance"])
    assert len(doc_list(dx, phone)["documents"]) == 1
    for who in (dx["T"]["B"]["phone"], dx["T"]["B"]["main"], dx["T"]["A2"]["phone"], dx["T"]["A2"]["main"]):          # alt cimitir / altă organizație
        assert doc_list(dx, who)["documents"] == []
        assert dx["client"].get(f"/api/v1/sync-v2/documents/{m['uid']}/file", headers=H(who)).status_code == 404
        assert dx["client"].delete(f"/api/v1/sync-v2/documents/{m['uid']}", headers=H(who)).status_code in (403, 404)
        assert put(dx, who, m["uid"], data).status_code in (403, 404, 409)
    # revocarea dispozitivului oprește accesul la documente
    with adm(dx["pg"]) as c:
        c.execute("UPDATE device_cemetery_bindings SET active=false, revoked_at=now() WHERE binding_uid=%s", (dx["T"]["A"]["phone_b"],))
    assert dx["client"].get(f"/api/v1/sync-v2/documents/{m['uid']}/file", headers=H(phone)).status_code in (401, 403, 404)
    assert dx["client"].get("/api/v1/sync-v2/documents", headers=H("nu.e.valid")).status_code == 401


def test_rls_blocks_cross_tenant_document_access_at_database_level(dx):
    s = seed(dx)
    data = png(30, b"rls")
    m = meta(data, "locuri_de_veci", s["loc"])
    dec(dx, s["main"], **m)
    put(dx, s["main"], m["uid"], data)
    cp = dx["cp"]
    cp.authenticate(dx["T"]["B"]["main"], dx["T"]["B"]["cem"])                      # contextul RLS = organizația B
    c = cp._c()
    try:
        assert c.execute("SELECT count(*) AS n FROM sync_v2_documents").fetchone()["n"] == 0
        with pytest.raises(Exception):
            c.execute("INSERT INTO sync_v2_documents (organization_uid,cemetery_uid,uid,entity,entity_uid,kind,filename,mime,size,sha256,storage_path,status,origin) "
                      "VALUES (CAST(? AS uuid),CAST(? AS uuid),?,?,?,?,?,?,?,?,?,?,?)",
                      (dx["T"]["A"]["org"], dx["T"]["A"]["cem"], hx(), "locuri_de_veci", s["loc"], "photo", "x.png", "image/png", 5, "0" * 64, "docs/x/" + hx(), "READY", "MAIN"))
    finally:
        c.close()


# ===================================================================== C. circuit complet MAIN -> PostgreSQL -> Mobile -> PostgreSQL -> MAIN
def test_full_circuit_main_cloud_mobile_cloud_main(dx):
    s = seed(dx)
    main, phone = s["main"], dx["T"]["A"]["phone"]
    assert grant(dx, ["view_map", "read_registers", "register_burials", "upload_documents"]).status_code == 200
    # MAIN -> Cloud: document al locului
    d_main = png(90, b"main")
    m1 = meta(d_main, "locuri_de_veci", s["loc"])
    dec(dx, main, **m1)
    put(dx, main, m1["uid"], d_main)
    # Cloud -> Mobile: telefonul vede locul, persoana (fără CNP) și documentul
    g = dx["mob"].grave_detail(phone, s["loc"])
    assert g["burials"][0]["nume"] == "Popescu"
    docs = doc_list(dx, phone, entity="locuri_de_veci", entity_uid=s["loc"])["documents"]
    assert [d["uid"] for d in docs] == [m1["uid"]]
    # Mobile -> Cloud: persoană nouă + fotografie nouă (coadă offline reluată)
    new_inh = hx()
    assert push(dx, phone, [op("inhumati", new_inh, {"nume": "Georgescu", "prenume": "Ana", "loc_uid": s["loc"]})])[0]["status"] == "applied"
    d_ph = png(120, b"phone")
    m2 = meta(d_ph, "locuri_de_veci", s["loc"], filename="telefon.png")
    for _ in range(2):                                                               # reluare idempotentă a cozii
        assert dec(dx, phone, **m2).status_code == 200 and put(dx, phone, m2["uid"], d_ph).status_code == 200
    # Cloud -> MAIN: MAIN primește persoana și fotografia, cu conținut identic
    names = {c["data"]["nume"] for c in pull(dx, main, "inhumati")["changes"]}
    assert {"Popescu", "Georgescu"} <= names
    mdocs = {d["uid"]: d for d in doc_list(dx, main)["documents"]}
    assert set(mdocs) == {m1["uid"], m2["uid"]} and mdocs[m2["uid"]]["origin"] == "MOBILE"
    f = dx["client"].get(f"/api/v1/sync-v2/documents/{m2['uid']}/file", headers=H(main))
    assert f.content == d_ph and hashlib.sha256(f.content).hexdigest() == m2["sha256"]
    with adm(dx["pg"]) as c:
        assert c.execute("SELECT count(*) FROM sync_v2_documents").fetchone()[0] == 2


# ===================================================================== D. regresie: bootstrap administrator cu organizație NOUĂ și fără cimitir (RLS pe jurnalul de audit)
def test_admin_bootstrap_new_org_without_cemetery_is_not_half_applied(dx):
    import secrets
    import time

    import cimitir_cloud_admin as ad
    import cimitir_cloud_control_pg as ccp
    with adm(dx["pg"]) as c:
        c.execute("TRUNCATE cp_admin_sessions, cp_admin_recovery, cp_admin_credentials CASCADE")
        c.execute("UPDATE cp_admin_state SET bootstrap_completed_at=NULL, bootstrap_identity_uid=NULL")
    token = secrets.token_urlsafe(32)
    env = dict(os.environ, BOOTSTRAP_TOKEN=token, BOOTSTRAP_TOKEN_EXPIRES_AT=str(time.time() + 600))
    svc = ad.AdminService(ccp.PostgresControlPlane(), environ=env)
    out = svc.bootstrap(token, f"owner{uuid.uuid4().hex[:6]}@example.invalid", "Parola-Lunga-9!x", "Owner", organization_name="Parohia Nouă (test)")
    assert out["organization_uid"] and out["cemetery_uid"] is None and svc.status()["initialized"]
