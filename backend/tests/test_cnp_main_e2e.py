# ruff: noqa: F811
"""E2E CNP: MAIN real (SQLite, desktop/sync_v2.py, proces separat) <-> HTTP real <-> Cloud (PostgreSQL local, rol cimitir_cp_app) <-> Mobile (HTTP).
Verifică valorile EFECTIV persistate în ambele baze (SQLite MAIN și PostgreSQL), nu doar răspunsurile API. Rulează doar cu CIMITIR_TEST_PG_ADMIN_URL. NU atinge Supabase."""
from __future__ import annotations

import os
import uuid

import pytest
import requests

from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_cleanup_race_pg import adm, pgdb  # noqa: F401
from test_etapa6b_field_storage import sup  # noqa: F401
from test_etapa6c_field_inbox_pg import fw  # noqa: F401
from test_sync_v2_main_e2e import ADD, H, main_run, pg, world  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PostgreSQL local de test indisponibil")
API = "/api/v1/sync-v2"
CNP = "0123456789012"
CNP2 = "2750505123456"
ALL = {c: {"view": True, "add": True, "edit": True, "delete": True, "fields": None} for c in
       ("locuri", "harta", "concesionari", "inhumati", "contracte", "plati", "avize", "fisiere")}


def grant(w, view_pii, delete=True):
    m = {k: {**v, "delete": delete} for k, v in ALL.items()}
    r = requests.put(f"{w['url']}{API}/devices/{w['T']['A']['phone_b']}/matrix", json={"matrix": m, "view_pii": view_pii}, headers=H(w["T"]["A"]["main"]), timeout=10)
    assert r.status_code == 200, r.text


def mpush(w, ops, key=None):
    r = requests.post(f"{w['url']}{API}/push", json={"ops": ops}, headers=H(key or w["T"]["A"]["phone"]), timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["result"]["results"]


def mop(entity, uid, data, base=0, action="upsert"):
    return {"op_id": "m-" + uuid.uuid4().hex, "entity": entity, "uid": uid, "action": action, "base_version": base, "data": data}


def mpull(w, entity, key=None):
    return requests.get(f"{w['url']}{API}/pull?entity={entity}", headers=H(key or w["T"]["A"]["phone"]), timeout=10).json()["result"]["changes"]


def test_main_to_cloud_to_mobile_cnp_exact_with_leading_zero(world):
    w = world
    out = main_run(w, ADD + """
cid = q("INSERT INTO concesionari (nume, prenume, cnp, bi_ci, telefon) VALUES (?,?,?,?,?)", ("Popescu", "Ion", "0123456789012", "XB 123456", "0700"))
rep = cloud_sync.sync_all()
out["ok"] = rep.get("ok")
out["cuid"] = q("SELECT uid FROM concesionari WHERE id=?", (cid,))[0]["uid"]
out["sqlite"] = q("SELECT cnp, typeof(cnp) AS t, bi_ci FROM concesionari WHERE id=?", (cid,))[0]
""")
    assert out["ok"] and out["sqlite"] == {"cnp": CNP, "t": "text", "bi_ci": "XB 123456"}
    assert pg(w, "SELECT cnp, bi_ci FROM concesionari WHERE uid=%s", (out["cuid"],))[0] == (CNP, "XB 123456")      # valoarea efectiv persistată în PostgreSQL
    grant(w, view_pii=True)
    assert [c for c in mpull(w, "concesionari") if c["uid"] == out["cuid"]][0]["data"]["cnp"] == CNP
    grant(w, view_pii=False)
    d = [c for c in mpull(w, "concesionari") if c["uid"] == out["cuid"]][0]["data"]
    assert "cnp" not in d and "bi_ci" not in d and d["nume"] == "Popescu"


def test_mobile_with_pii_creates_and_edits_cnp_and_main_receives_exact(world):
    w = world
    out = main_run(w, ADD)
    grant(w, view_pii=True)
    loc = out["loc_uid"]
    cuid, kuid = uuid.uuid4().hex, uuid.uuid4().hex
    res = mpush(w, [mop("concesionari", cuid, {"nume": "Mobil", "prenume": "Nou", "cnp": CNP, "bi_ci": "CJ 000111", "telefon": "0711"}),
                    mop("contracte", kuid, {"numar_contract": "25", "concesionar_uid": cuid, "loc_uid": loc, "cod_loc": "E2E-1", "owner_cnp": CNP})])
    assert [r["status"] for r in res] == ["applied", "applied"], res
    assert pg(w, "SELECT cnp, bi_ci, telefon FROM concesionari WHERE uid=%s", (cuid,))[0] == (CNP, "CJ 000111", "0711")
    back = main_run(w, """
rep = cloud_sync.sync_all()
out["ok"] = rep.get("ok")
out["conc"] = q("SELECT nume, cnp, typeof(cnp) AS t, bi_ci FROM concesionari WHERE nume='Mobil'")
out["contract"] = q("SELECT numar_contract, owner_cnp, concesionar_id, id_loc FROM contracte")
out["conc_id"] = q("SELECT id FROM concesionari WHERE nume='Mobil'")[0]["id"]
out["loc_id"] = q("SELECT id FROM locuri_de_veci WHERE cod_loc='E2E-1'")[0]["id"]
out["pending"] = len(entity_sync.pending_entity_ops(("pending","conflict","error")))
""")
    assert back["ok"] and back["pending"] == 0
    assert back["conc"] == [{"nume": "Mobil", "cnp": CNP, "t": "text", "bi_ci": "CJ 000111"}]            # MAIN SQLite: text, zerou inițial păstrat
    assert back["contract"][0]["owner_cnp"] == CNP and back["contract"][0]["concesionar_id"] == back["conc_id"] and back["contract"][0]["id_loc"] == back["loc_id"]
    # modificare ulterioară de pe telefon -> MAIN
    cur = [c for c in mpull(w, "concesionari") if c["uid"] == cuid][0]
    r = mpush(w, [mop("concesionari", cuid, {**cur["data"], "cnp": CNP2}, base=cur["version"])])[0]
    assert r["status"] == "applied"
    again = main_run(w, "cloud_sync.sync_all(); out['cnp'] = q(\"SELECT cnp FROM concesionari WHERE nume='Mobil'\")[0]['cnp']")
    assert again["cnp"] == CNP2 and pg(w, "SELECT cnp FROM concesionari WHERE uid=%s", (cuid,))[0][0] == CNP2


def test_main_edit_cnp_reaches_cloud_and_mobile_without_loss_of_other_fields(world):
    w = world
    main_run(w, ADD + """
q("INSERT INTO concesionari (nume, prenume, cnp, adresa, email) VALUES (?,?,?,?,?)", ("Ionescu", "Ana", "", "Str. X 1", "a@b.ro"))
cloud_sync.sync_all()
""")
    main_run(w, "q(\"UPDATE concesionari SET cnp='0000000000001' WHERE nume='Ionescu'\"); out['ok'] = cloud_sync.sync_all().get('ok')")
    assert pg(w, "SELECT cnp, adresa, email FROM concesionari WHERE nume='Ionescu'")[0] == ("0000000000001", "Str. X 1", "a@b.ro")
    grant(w, view_pii=True)
    c = [c for c in mpull(w, "concesionari") if c["data"]["nume"] == "Ionescu"][0]["data"]
    assert (c["cnp"], c["adresa"], c["email"]) == ("0000000000001", "Str. X 1", "a@b.ro")


def test_mobile_without_pii_cannot_write_cnp_and_main_value_stays(world):
    w = world
    main_run(w, ADD + """
q("INSERT INTO concesionari (nume, prenume, cnp) VALUES (?,?,?)", ("Protejat", "Ion", "0123456789012"))
cloud_sync.sync_all()
""")
    grant(w, view_pii=False)
    c = [c for c in mpull(w, "concesionari") if c["data"]["nume"] == "Protejat"][0]
    bad = mpush(w, [mop("concesionari", c["uid"], {**c["data"], "cnp": "9999999999999"}, base=c["version"])])[0]
    assert bad["status"] == "applied" and bad["ignored_fields"] == ["cnp"]          # nu se stochează, dar nu e tăcut
    ok = mpush(w, [mop("concesionari", c["uid"], {**c["data"], "telefon": "0722"}, base=c["version"])])[0]
    assert ok["status"] == "applied"
    row = main_run(w, "cloud_sync.sync_all(); out['r'] = q(\"SELECT cnp, telefon FROM concesionari WHERE nume='Protejat'\")[0]")["r"]
    assert row == {"cnp": CNP, "telefon": "0722"} and pg(w, "SELECT cnp FROM concesionari WHERE uid=%s", (c["uid"],))[0][0] == CNP


def test_conflict_stale_mobile_cnp_is_not_applied_main_wins(world):
    w = world
    main_run(w, ADD + "q(\"INSERT INTO concesionari (nume, cnp) VALUES ('Conf', '0123456789012')\"); cloud_sync.sync_all()")
    grant(w, view_pii=True)
    c = [c for c in mpull(w, "concesionari") if c["data"]["nume"] == "Conf"][0]
    main_run(w, "q(\"UPDATE concesionari SET cnp='1111111111113' WHERE nume='Conf'\"); cloud_sync.sync_all()")
    r = mpush(w, [mop("concesionari", c["uid"], {**c["data"], "cnp": CNP2}, base=c["version"])])[0]
    assert r["status"] == "conflict"
    assert pg(w, "SELECT cnp FROM concesionari WHERE uid=%s", (c["uid"],))[0][0] == "1111111111113"


def test_logical_delete_from_main_and_revocation_blocks_offline_changes(world):
    w = world
    main_run(w, ADD + "q(\"INSERT INTO concesionari (nume, cnp) VALUES ('Sters', '0123456789012')\"); cloud_sync.sync_all()")
    grant(w, view_pii=True)
    c = [c for c in mpull(w, "concesionari") if c["data"]["nume"] == "Sters"][0]
    offline = mop("concesionari", c["uid"], {**c["data"], "cnp": CNP2}, base=c["version"])           # pregătită offline, trimisă după revocare
    grant(w, view_pii=False)
    r = requests.put(f"{w['url']}{API}/devices/{w['T']['A']['phone_b']}/matrix", json={"profile": "doar_consultare"}, headers=H(w["T"]["A"]["main"]), timeout=10)
    assert r.status_code == 200
    res = mpush(w, [offline])[0]
    assert res["status"] == "rejected" or res.get("ignored_fields") == ["cnp"]          # fără drept de editare/PII: nimic nu se persistă (verificat în PG mai jos)
    res2 = mpush(w, [mop("concesionari", c["uid"], {**c["data"], "telefon": "0799"}, base=c["version"])])[0]
    assert res2["status"] == "rejected" and res2["reason"] == "not_authorized"
    assert pg(w, "SELECT cnp FROM concesionari WHERE uid=%s", (c["uid"],))[0][0] == CNP
    main_run(w, "q(\"DELETE FROM concesionari WHERE nume='Sters'\"); cloud_sync.sync_all()")
    assert pg(w, "SELECT count(*) FROM concesionari WHERE nume='Sters'")[0][0] == 0
    ch = mpull(w, "concesionari")
    assert any(x["uid"] == c["uid"] and x["deleted"] for x in ch)
