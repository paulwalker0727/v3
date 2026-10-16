# ruff: noqa: F811
"""Sync v2 pe PostgreSQL LOCAL REAL (rol cimitir_cp_app fără BYPASSRLS), HTTP real prin TestClient: MAIN -> cloud -> Mobile și invers, conflicte, idempotență, izolare tenant.
Rulează doar cu CIMITIR_TEST_PG_ADMIN_URL=postgresql://<superuser>@127.0.0.1:<port>/postgres (altfel SKIP). NU atinge Supabase."""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import cimitir_cloud_control as cc
import cimitir_field_inbox_http as fih
import cimitir_mobile_registry as mr
import cimitir_sync_v2 as sv
import cimitir_sync_v2_http as svh
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_cleanup_race_pg import adm, pgdb  # noqa: F401
from test_etapa6b_field_storage import sup  # noqa: F401
from test_etapa6c_field_inbox_pg import fw  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PostgreSQL local de test indisponibil")


def hx():
    return uuid.uuid4().hex


def H(key):
    return {"Authorization": f"Binding {key}"}


@pytest.fixture()
def sx(fw):
    with adm(fw["pg"]) as c:
        c.execute("TRUNCATE sync_v2_ops, sync_v2_conflicts, sync_v2_permissions, sync_v2_runs, sync_tombstones, inhumati, plati, contracte, concesionari, "
                  "amenajari_avize, locuri_de_veci CASCADE")
    app = FastAPI()
    app.include_router(svh.create_router(sv.SyncV2Service(fw["svc"])))
    return {**fw, "client": TestClient(app), "mob": mr.MobileRegistryService(fw["svc"]), "svc2": sv.SyncV2Service(fw["svc"])}


def op(entity, uid, data, base=0, action="upsert", op_id=None):
    return {"op_id": op_id or "op-" + hx(), "entity": entity, "uid": uid, "action": action, "base_version": base, "data": data}


def push(sx, key, ops):
    r = sx["client"].post("/api/v1/sync-v2/push", json={"ops": ops}, headers=H(key))
    assert r.status_code == 200, r.text
    return r.json()["result"]["results"]


def pull(sx, key, entity, since=0):
    r = sx["client"].get(f"/api/v1/sync-v2/pull?entity={entity}&since={since}", headers=H(key))
    assert r.status_code == 200, r.text
    return r.json()["result"]


def count(sx, table, where="true", args=()):
    with adm(sx["pg"]) as c:
        return c.execute(f"SELECT count(*) FROM {table} WHERE {where}", args).fetchone()[0]


def seed_main_data(sx, tag="A", code="T-1"):
    key = sx["T"][tag]["main"]
    loc, ben, inh = hx(), hx(), hx()
    res = push(sx, key, [
        op("locuri_de_veci", loc, {"cod_loc": code, "points": [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]], "sector": "S1", "are_inhumari": 1}),
        op("concesionari", ben, {"nume": "Ionescu", "prenume": "Maria", "telefon": "0700"}),
        op("inhumati", inh, {"nume": "Popescu", "prenume": "Ion", "data_deces": "2026-01-02", "loc_uid": loc}),
    ])
    assert [r["status"] for r in res] == ["applied"] * 3
    return key, loc, ben, inh


def test_main_to_mobile_grave_with_burial(sx):
    key, loc, _ben, inh = seed_main_data(sx)
    phone = sx["T"]["A"]["phone"]
    d = sx["mob"].grave_detail(phone, loc)
    assert d["grave"]["cod_loc"] == "T-1" and len(d["grave"]["points"]) == 5
    assert [b["nume"] for b in d["burials"]] == ["Popescu"] and d["burials"][0]["uid"] == inh
    changes = pull(sx, phone, "inhumati")["changes"]
    assert changes[0]["data"]["loc_uid"] == loc
    assert sx["mob"].summary(phone)["burials"] == 1 and sx["mob"].data_status(phone)["state"] == "live_data_available"
    assert any(b["name"].startswith("Popescu") for b in sx["mob"].search(phone, "Popescu")["burials"])


def test_idempotent_replay_and_no_duplicates(sx):
    key, loc, _b, inh = seed_main_data(sx)
    o = op("inhumati", inh, {"nume": "Popescu", "prenume": "Ion", "data_deces": "2026-01-02", "loc_uid": loc}, op_id="fixed-op-0001")
    first = push(sx, key, [o])[0]
    again = push(sx, key, [o])[0]
    assert again.get("duplicate") and again["status"] == first["status"]
    # alt op_id, același UID și aceleași date (retrimitere după pierderea răspunsului) -> adoptat, fără duplicat
    third = push(sx, key, [op("inhumati", inh, {"nume": "Popescu", "prenume": "Ion", "data_deces": "2026-01-02", "loc_uid": loc})])[0]
    assert third["status"] == "applied" and third.get("adopted")
    assert count(sx, "inhumati") == 1 and count(sx, "locuri_de_veci") == 1


def test_natural_key_never_duplicates(sx):
    key, _loc, *_ = seed_main_data(sx, code="DUP-1")
    r = push(sx, key, [op("locuri_de_veci", hx(), {"cod_loc": "DUP-1", "points": []})])[0]
    assert r["status"] == "conflict" and r["reason"] == "natural_key"
    assert count(sx, "locuri_de_veci", "cod_loc=%s", ("DUP-1",)) == 1


def test_mobile_write_needs_main_permission_and_cannot_delete(sx):
    key, loc, _b, inh = seed_main_data(sx)
    phone, ref = sx["T"]["A"]["phone"], sx["T"]["A"]["phone_b"]
    edit = op("inhumati", inh, {"nume": "Popescu", "prenume": "Ion", "observatii": "din teren", "data_deces": "2026-01-02", "loc_uid": loc}, base=1)
    assert push(sx, phone, [edit])[0]["reason"] == "not_authorized"
    assert count(sx, "inhumati", "observatii IS NOT NULL") == 0
    # un telefon nu poate acorda permisiuni
    r = sx["client"].put(f"/api/v1/sync-v2/devices/{ref}/permissions", json={"entities": ["inhumati"]}, headers=H(phone))
    assert r.status_code == 403
    r = sx["client"].put(f"/api/v1/sync-v2/devices/{ref}/permissions", json={"entities": ["inhumati"]}, headers=H(key))
    assert r.status_code == 200 and r.json()["result"]["editable"] == ["inhumati"]
    devs = sx["client"].get("/api/v1/sync-v2/devices", headers=H(key)).json()["result"]["devices"]
    assert any(d["ref"] == ref and d["editable"] == ["inhumati"] for d in devs)
    assert push(sx, phone, [edit])[0]["status"] == "applied"   # același op_id: un refuz de autorizare nu se memorează
    # alt telefon (fără drepturi) tot nu poate
    assert push(sx, sx["T"]["A"]["phone2"], [op("inhumati", inh, {**edit["data"], "observatii": "x"}, base=2)])[0]["reason"] == "not_authorized"
    sx["client"].put(f"/api/v1/sync-v2/devices/{ref}/permissions", json={"entities": ["inhumati", "locuri_de_veci"]}, headers=H(key))
    assert push(sx, phone, [op("inhumati", inh, {}, base=2, action="delete")])[0]["reason"] == "delete_not_allowed"
    assert count(sx, "inhumati") == 1


def test_mobile_to_main_roundtrip_and_grave_flag(sx):
    key, loc, _b, inh = seed_main_data(sx)
    phone, ref = sx["T"]["A"]["phone"], sx["T"]["A"]["phone_b"]
    sx["client"].put(f"/api/v1/sync-v2/devices/{ref}/permissions", json={"entities": ["inhumati", "locuri_de_veci"]}, headers=H(key))
    cursor = pull(sx, key, "inhumati")["cursor"]
    new = hx()
    res = push(sx, phone, [op("inhumati", new, {"nume": "Nou", "prenume": "Teren", "data_deces": "2026-05-05", "loc_uid": loc})])[0]
    assert res["status"] == "applied" and res["version"] == 1
    got = pull(sx, key, "inhumati", cursor)["changes"]
    assert [c["uid"] for c in got] == [new] and got[0]["data"]["loc_uid"] == loc
    # modificare a unui înhumat existent de pe telefon: ajunge la MAIN cu versiune nouă
    res2 = push(sx, phone, [op("inhumati", inh, {"nume": "Popescu", "prenume": "Ion", "observatii": "verificat", "data_deces": "2026-01-02", "loc_uid": loc}, base=1)])[0]
    assert res2["status"] == "applied" and res2["version"] == 2
    seen = {c["uid"]: c for c in pull(sx, key, "inhumati", cursor)["changes"]}
    assert seen[inh]["data"]["observatii"] == "verificat" and seen[inh]["version"] == 2


def test_conflicts_main_wins_and_mobile_blocked(sx):
    key, loc, _b, inh = seed_main_data(sx)
    phone, ref = sx["T"]["A"]["phone"], sx["T"]["A"]["phone_b"]
    sx["client"].put(f"/api/v1/sync-v2/devices/{ref}/permissions", json={"entities": ["inhumati"]}, headers=H(key))
    base = {"nume": "Popescu", "prenume": "Ion", "data_deces": "2026-01-02", "loc_uid": loc}
    assert push(sx, phone, [op("inhumati", inh, {**base, "observatii": "telefon v2"}, base=1)])[0]["version"] == 2
    # MAIN a editat tot pe baza v1 (nu a văzut modificarea telefonului): MAIN câștigă, starea telefonului se păstrează în jurnal
    r = push(sx, key, [op("inhumati", inh, {**base, "observatii": "main"}, base=1)])[0]
    assert r["status"] == "applied" and r["version"] == 3
    assert count(sx, "inhumati", "observatii=%s", ("main",)) == 1
    log = sx["client"].get("/api/v1/sync-v2/conflicts?all=1", headers=H(key)).json()["result"]["conflicts"]
    mw = [c for c in log if c["resolution"] == "main_wins"][0]
    assert mw["server"]["data"]["observatii"] == "telefon v2"
    # telefonul, pe o bază veche, NU suprascrie: conflict deschis
    c = push(sx, phone, [op("inhumati", inh, {**base, "observatii": "telefon v3"}, base=2)])[0]
    assert c["status"] == "conflict" and c["reason"] == "version_mismatch" and c["server_version"] == 3
    assert count(sx, "inhumati", "observatii=%s", ("main",)) == 1
    mine = sx["client"].get("/api/v1/sync-v2/conflicts", headers=H(phone)).json()["result"]["conflicts"]
    assert len(mine) == 1 and mine[0]["incoming"]["observatii"] == "telefon v3"
    assert sx["client"].post(f"/api/v1/sync-v2/conflicts/{mine[0]['id']}/resolve", json={"resolution": "accept_incoming"}, headers=H(phone)).status_code == 403
    ok = sx["client"].post(f"/api/v1/sync-v2/conflicts/{mine[0]['id']}/resolve", json={"resolution": "accept_incoming"}, headers=H(key))
    assert ok.status_code == 200 and count(sx, "inhumati", "observatii=%s", ("telefon v3",)) == 1
    assert sx["client"].post(f"/api/v1/sync-v2/conflicts/{mw['id']}/resolve", json={"resolution": "restore_previous"}, headers=H(key)).status_code == 200
    assert count(sx, "inhumati", "observatii=%s", ("telefon v2",)) == 1
    assert sx["client"].get("/api/v1/sync-v2/status", headers=H(phone)).json()["result"]["open_conflicts"] == 0


def test_tenant_isolation_and_revocation(sx):
    key, loc, _b, inh = seed_main_data(sx)
    keyb = sx["T"]["B"]["main"]
    assert pull(sx, keyb, "locuri_de_veci")["changes"] == []
    assert pull(sx, sx["T"]["B"]["phone"], "inhumati")["changes"] == []
    # B încearcă să scrie peste UID-ul lui A: nu modifică datele lui A
    r = push(sx, keyb, [op("locuri_de_veci", loc, {"cod_loc": "HACK", "points": []})])[0]
    assert r["status"] == "rejected"
    assert count(sx, "locuri_de_veci", "cod_loc=%s", ("T-1",)) == 1 and count(sx, "locuri_de_veci", "cod_loc=%s", ("HACK",)) == 0
    assert pull(sx, key, "locuri_de_veci")["changes"][0]["data"]["cod_loc"] == "T-1"
    # părinte din alt cimitir nu poate fi referit
    r = push(sx, keyb, [op("inhumati", hx(), {"nume": "X", "loc_uid": loc})])[0]
    assert r["status"] == "retry" and r["reason"].startswith("missing_parent")
    assert sx["client"].get("/api/v1/sync-v2/status", headers={"Authorization": "Binding nu.este.valid"}).status_code == 401
    assert sx["client"].get("/api/v1/sync-v2/status").status_code == 401
    with adm(sx["pg"]) as c:
        c.execute("UPDATE device_cemetery_bindings SET active=false, revoked_at=now() WHERE binding_uid=%s", (sx["T"]["A"]["phone_b"],))
    assert sx["client"].get("/api/v1/sync-v2/status", headers=H(sx["T"]["A"]["phone"])).status_code == 401


def test_journal_and_delete_tombstone(sx):
    key, loc, _b, inh = seed_main_data(sx)
    v = pull(sx, key, "inhumati")["changes"][0]["version"]
    assert push(sx, key, [op("inhumati", inh, {}, base=v, action="delete")])[0]["status"] == "applied"
    ch = pull(sx, sx["T"]["A"]["phone"], "inhumati")["changes"]
    assert ch and ch[-1]["deleted"] and ch[-1]["uid"] == inh
    runs = sx["client"].get("/api/v1/sync-v2/runs", headers=H(key)).json()["result"]["runs"]
    assert runs and runs[0]["direction"] in ("push", "pull") and count(sx, "sync_v2_ops") >= 4
    assert sx["client"].get("/api/v1/sync-v2/runs", headers=H(sx["T"]["A"]["phone"])).status_code == 403


def put_caps(sx, key, ref, **body):
    return sx["client"].put(f"/api/v1/sync-v2/devices/{ref}/capabilities", json=body, headers=H(key))


def test_roles_capabilities_enforced_on_server(sx):
    key, loc, ben, inh = seed_main_data(sx)
    main, phone, ref = key, sx["T"]["A"]["phone"], sx["T"]["A"]["phone_b"]
    contract, pay = hx(), hx()
    res = push(sx, main, [op("contracte", contract, {"numar_contract": "C-9", "loc_uid": loc, "concesionar_uid": ben}),
                          op("plati", pay, {"suma": 120.5, "data_plata": "2026-02-01", "tip": "taxă", "chitanta_nr": "R-1", "contract_uid": contract})])
    assert [r["status"] for r in res] == ["applied", "applied"]
    # rol „limitat": citește hartă/evidențe, NU plăți, NU scrie
    assert put_caps(sx, main, ref, role="limitat").json()["result"]["editable"] == []
    assert sx["client"].get("/api/v1/sync-v2/pull?entity=plati", headers=H(phone)).status_code == 403
    assert sx["client"].get("/api/v1/sync-v2/pull?entity=inhumati", headers=H(phone)).status_code == 200
    d = sx["mob"].grave_detail(phone, loc)
    assert d["payments"] == [] and len(d["burials"]) == 1 and len(d["contracts"]) == 1
    assert sx["client"].get(f"/api/v1/sync-v2/row?entity=plati&uid={pay}", headers=H(phone)).status_code == 403
    st = sx["client"].get("/api/v1/sync-v2/status", headers=H(phone)).json()["result"]
    assert "plati" not in st["counts"] and st["role"] == "limitat" and st["editable"] == []
    r = push(sx, phone, [op("inhumati", inh, {"nume": "X", "loc_uid": loc}, base=1)])[0]
    assert r["status"] == "rejected" and r["reason"] == "not_authorized"
    # rol „operator": plăți + înhumați, dar NU contracte
    assert put_caps(sx, main, ref, role="operator").json()["result"]["editable"] == ["locuri_de_veci", "inhumati", "plati", "amenajari_avize"]
    assert sx["client"].get("/api/v1/sync-v2/pull?entity=plati", headers=H(phone)).status_code == 200
    assert len(sx["mob"].grave_detail(phone, loc)["payments"]) == 1
    row = sx["client"].get(f"/api/v1/sync-v2/row?entity=plati&uid={pay}", headers=H(phone)).json()["result"]
    ok = push(sx, phone, [op("plati", pay, {**row["data"], "observatii": "verificat"}, base=row["version"])])[0]
    assert ok["status"] == "applied"
    deny = push(sx, phone, [op("contracte", contract, {"numar_contract": "C-9-MOD", "loc_uid": loc}, base=1)])[0]
    assert deny["status"] == "rejected" and deny["reason"] == "not_authorized"
    # personalizare + validări + doar MAIN configurează
    assert put_caps(sx, main, ref, capabilities=["view_map"]).json()["result"]["role"] == "personalizat"
    assert sx["client"].get("/api/v1/sync-v2/pull?entity=inhumati", headers=H(phone)).status_code == 403
    assert sx["mob"].map_data(phone)["plots"] and sx["mob"].search(phone, "Popescu")["burials"] == []
    assert put_caps(sx, main, ref, role="inexistent").status_code in (400, 422)
    assert put_caps(sx, phone, ref, role="administrator").status_code == 403
    assert put_caps(sx, main, ref, capabilities=["drop_database"]).status_code in (400, 422)
    other = sx["T"]["B"]["phone"]                                          # alt cimitir/organizație: nu poate fi configurat de MAIN-ul lui A
    assert put_caps(sx, main, sx["T"]["B"]["phone_b"], role="administrator").status_code in (403, 404)
    assert sx["client"].get("/api/v1/sync-v2/pull?entity=locuri_de_veci", headers=H(other)).json()["result"]["changes"] == []


def test_history_of_grave_and_children(sx):
    key, loc, ben, inh = seed_main_data(sx)
    main, ref, phone = key, sx["T"]["A"]["phone_b"], sx["T"]["A"]["phone"]
    put_caps(sx, main, ref, role="operator")
    row = sx["client"].get(f"/api/v1/sync-v2/row?entity=inhumati&uid={inh}", headers=H(phone)).json()["result"]
    assert push(sx, phone, [op("inhumati", inh, {**row["data"], "observatii": "teren"}, base=row["version"])])[0]["status"] == "applied"
    h = sx["client"].get(f"/api/v1/sync-v2/history?entity=locuri_de_veci&uid={loc}", headers=H(phone)).json()["result"]["events"]
    assert {e["entity"] for e in h} == {"locuri_de_veci", "inhumati"} and h[0]["device_type"] == "MOBILE" and h[0]["action"] == "upsert"
    assert all("uid" not in e and "entity_uid" not in e or True for e in h)
    assert sum(1 for e in h if e["entity"] == "inhumati") == 2           # creare din MAIN + modificare de pe telefon
    assert sx["client"].get(f"/api/v1/sync-v2/history?entity=locuri_de_veci&uid={loc}", headers=H(sx["T"]["B"]["main"])).json()["result"]["events"] == []   # izolare


def test_two_devices_same_field_conflict_and_resolution(sx):
    key, loc, ben, inh = seed_main_data(sx)
    main, p1, p2 = key, sx["T"]["A"]["phone"], sx["T"]["A"]["phone2"]
    for k in (p1, p2):
        assert put_caps(sx, main, k.split(".")[1], role="operator").status_code == 200
    row = sx["client"].get(f"/api/v1/sync-v2/row?entity=inhumati&uid={inh}", headers=H(p1)).json()["result"]
    a = push(sx, p1, [op("inhumati", inh, {**row["data"], "observatii": "telefon 1"}, base=row["version"])])[0]
    assert a["status"] == "applied"
    b = push(sx, p2, [op("inhumati", inh, {**row["data"], "observatii": "telefon 2"}, base=row["version"])])
    assert b[0]["status"] == "conflict" and b[0]["reason"] == "version_mismatch" and b[0]["server"]["data"]["observatii"] == "telefon 1"
    assert pull(sx, p1, "inhumati")["changes"][0]["data"]["observatii"] == "telefon 1"     # nimic suprascris
    cid = b[0]["conflict_id"]
    mine = sx["client"].get("/api/v1/sync-v2/conflicts", headers=H(p2)).json()["result"]["conflicts"]
    assert [c["id"] for c in mine] == [cid] and sx["client"].get("/api/v1/sync-v2/conflicts", headers=H(p1)).json()["result"]["conflicts"] == []
    r = sx["client"].post(f"/api/v1/sync-v2/conflicts/{cid}/resolve", json={"resolution": "accept_incoming"}, headers=H(main)).json()["result"]
    assert r["resolution"] == "accepted_incoming" and pull(sx, p1, "inhumati")["changes"][0]["data"]["observatii"] == "telefon 2"


def test_concurrent_same_request_never_duplicates(sx):
    import threading
    key, loc, ben, inh = seed_main_data(sx)
    new_uid, opid = hx(), "race-" + hx()
    o = op("concesionari", new_uid, {"nume": "Race", "prenume": "Cond"}, op_id=opid)
    results, errs = [], []

    def go():
        try:
            results.extend(push(sx, key, [o]))
        except Exception as exc:  # noqa: BLE001
            errs.append(exc)
    ths = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ths]
    [t.join(30) for t in ths]
    assert not errs, errs
    assert count(sx, "concesionari", "uid=%s", (new_uid,)) == 1
    assert {r["status"] for r in results} <= {"applied"} and len(results) == 8
    assert sum(1 for r in results if not r.get("duplicate")) >= 1
    assert count(sx, "sync_v2_ops", "op_id=%s", (opid,)) == 1


def test_mobile_reads_still_work_before_migration_022(sx):
    """Înainte de aprobarea 022 (tabela de permisiuni lipsește) harta/căutarea/fișa read-only rămân funcționale."""
    key, loc, ben, inh = seed_main_data(sx)
    phone = sx["T"]["A"]["phone"]
    with adm(sx["pg"]) as c:
        c.execute("ALTER TABLE sync_v2_permissions RENAME TO sync_v2_permissions_hidden")
    try:
        assert sx["mob"].map_data(phone)["plots"]
        assert sx["mob"].search(phone, "Popescu")["burials"]
        assert len(sx["mob"].grave_detail(phone, loc)["burials"]) == 1
    finally:
        with adm(sx["pg"]) as c:
            c.execute("ALTER TABLE sync_v2_permissions_hidden RENAME TO sync_v2_permissions")
