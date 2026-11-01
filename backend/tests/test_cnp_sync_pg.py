# ruff: noqa: F811
"""CNP Mobile <-> cloud (PostgreSQL local real) <-> MAIN: valori efectiv persistate, zerouri inițiale, drepturi pe câmpuri sensibile, jurnal.
Rulează doar cu CIMITIR_TEST_PG_ADMIN_URL=postgresql://<superuser>@127.0.0.1:<port>/postgres (altfel SKIP). NU atinge Supabase."""
from __future__ import annotations

import json
import os

import pytest

from test_mobile_permissions_pg import API, cell, give  # noqa: F401
from test_sync_v2_pg import H, adm, count, hx, op, pgdb, pull, push, seed_main_data, sx  # noqa: F401
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_field_storage import sup  # noqa: F401
from test_etapa6c_field_inbox_pg import fw  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PostgreSQL local de test indisponibil")

CNP = "0123456789012"          # cu zero inițial: trebuie să rămână text
CNP2 = "1800101123456"
FULL = {"locuri": cell(True, True, True), "concesionari": cell(True, True, True), "inhumati": cell(True, True, True), "contracte": cell(True, True, True)}


def pg_value(sx, table, uid, col):
    with adm(sx["pg"]) as c:
        r = c.execute(f"SELECT {col} FROM {table} WHERE uid=%s", (uid,)).fetchone()
    return r[0] if r else "<lipsă>"


def phone_and_ref(sx):
    return sx["T"]["A"]["phone"], sx["T"]["A"]["phone_b"]


def test_phone_with_pii_creates_concesionar_cnp_persisted_and_reaches_main(sx):
    main, _loc, _b, _i = seed_main_data(sx)
    phone, ref = phone_and_ref(sx)
    give(sx, ref, matrix=FULL, view_pii=True)
    uid = hx()
    r = push(sx, phone, [op("concesionari", uid, {"nume": "Test", "prenume": "Cnp", "cnp": CNP, "bi_ci": "XY 123456"})])[0]
    assert r["status"] == "applied"
    assert pg_value(sx, "concesionari", uid, "cnp") == CNP
    assert pg_value(sx, "concesionari", uid, "bi_ci") == "XY 123456"
    got = [c for c in pull(sx, main, "concesionari")["changes"] if c["uid"] == uid][0]
    assert got["data"]["cnp"] == CNP and got["data"]["bi_ci"] == "XY 123456"


def test_phone_with_pii_edits_cnp_and_associated_contract_owner_cnp(sx):
    main, loc, ben, _i = seed_main_data(sx)
    phone, ref = phone_and_ref(sx)
    give(sx, ref, matrix=FULL, view_pii=True)
    ver = pull(sx, main, "concesionari")["changes"][0]["version"]
    cur = pull(sx, phone, "concesionari")["changes"][0]
    assert cur["uid"] == ben
    r = push(sx, phone, [op("concesionari", ben, {**cur["data"], "cnp": CNP2}, base=ver)])[0]
    assert r["status"] == "applied", r
    assert pg_value(sx, "concesionari", ben, "cnp") == CNP2
    kuid = hx()
    r = push(sx, phone, [op("contracte", kuid, {"numar_contract": "7", "concesionar_uid": ben, "loc_uid": loc, "owner_cnp": CNP,
                                                  "owner_id_card_serie_number": "AB 1"})])[0]
    assert r["status"] == "applied", r
    assert pg_value(sx, "contracte", kuid, "owner_cnp") == CNP


def test_phone_without_pii_cnp_is_never_silently_lost(sx):
    """Telefon fără view_pii care trimite CNP/CI: valoarea NU se stochează (contract existent: «applied»), dar răspunsul o raportează explicit în `ignored_fields`."""
    main, _loc, _b, _i = seed_main_data(sx)
    phone, ref = phone_and_ref(sx)
    give(sx, ref, matrix=FULL, view_pii=False)
    uid = hx()
    r = push(sx, phone, [op("concesionari", uid, {"nume": "Fara", "prenume": "Pii", "cnp": CNP, "bi_ci": "AA 1"})])[0]
    assert r["status"] == "applied" and sorted(r["ignored_fields"]) == ["bi_ci", "cnp"], r
    assert pg_value(sx, "concesionari", uid, "cnp") is None
    assert CNP not in json.dumps(r)


def test_phone_without_pii_edit_preserves_main_cnp(sx):
    main, _loc, ben, _i = seed_main_data(sx)
    key_main = main
    ver = pull(sx, main, "concesionari")["changes"][0]["version"]
    r = push(sx, key_main, [op("concesionari", ben, {"nume": "Ionescu", "prenume": "Maria", "telefon": "0700", "cnp": CNP}, base=ver)])[0]
    assert r["status"] == "applied"
    phone, ref = phone_and_ref(sx)
    give(sx, ref, matrix=FULL, view_pii=False)
    cur = pull(sx, phone, "concesionari")["changes"][0]
    assert "cnp" not in cur["data"] and "bi_ci" not in cur["data"]               # redactat pe telefon
    r = push(sx, phone, [op("concesionari", ben, {**cur["data"], "telefon": "0799"}, base=cur["version"])])[0]
    assert r["status"] == "applied"
    assert pg_value(sx, "concesionari", ben, "cnp") == CNP                      # CNP-ul din MAIN nu e șters
    assert pg_value(sx, "concesionari", ben, "telefon") == "0799"
    r = push(sx, phone, [op("concesionari", ben, {**cur["data"], "telefon": "0711", "cnp": ""}, base=r["version"])])[0]
    assert pg_value(sx, "concesionari", ben, "cnp") == CNP                      # nici golirea din formular nu-l șterge


def test_main_zero_leading_cnp_roundtrip_exact(sx):
    main, _loc, ben, inh = seed_main_data(sx)
    v = pull(sx, main, "concesionari")["changes"][0]["version"]
    push(sx, main, [op("concesionari", ben, {"nume": "Ionescu", "prenume": "Maria", "cnp": CNP}, base=v),
                    op("inhumati", inh, {"nume": "Popescu", "prenume": "Ion", "cnp": "0000000000000", "data_deces": "2026-01-02"}, base=1)])
    assert pg_value(sx, "concesionari", ben, "cnp") == CNP
    assert pg_value(sx, "inhumati", inh, "cnp") == "0000000000000"
    phone, ref = phone_and_ref(sx)
    give(sx, ref, matrix=FULL, view_pii=True)
    assert [c for c in pull(sx, phone, "concesionari")["changes"] if c["uid"] == ben][0]["data"]["cnp"] == CNP


def test_cnp_not_in_phone_journal_without_pii_and_conflict_views_redacted(sx):
    main, loc, ben, inh = seed_main_data(sx)
    phone, ref = phone_and_ref(sx)
    give(sx, ref, matrix=FULL, view_pii=False)
    v = pull(sx, main, "inhumati")["changes"][0]["version"]
    push(sx, main, [op("inhumati", inh, {"nume": "Popescu", "prenume": "Ion", "cnp": CNP, "data_deces": "2026-01-02", "loc_uid": loc}, base=v)])
    # versiune depășită pe telefon -> conflict; răspunsul și vizualizarea conflictului nu conțin CNP
    r = push(sx, phone, [op("inhumati", inh, {"nume": "Popescu", "prenume": "Ion", "observatii": "x", "data_deces": "2026-01-02", "loc_uid": loc}, base=v)])[0]
    assert r["status"] == "conflict"
    assert CNP not in json.dumps(r)
    cf = sx["client"].get(f"{API}/conflicts", headers=H(phone)).json()["result"]["conflicts"]
    assert CNP not in json.dumps(cf)


def test_ops_journal_never_stores_cnp_in_clear(sx):
    """Jurnalizarea operațiilor (sync_v2_ops.response, sync_v2_conflicts.incoming/server) nu păstrează CNP/CI în clar."""
    main, loc, ben, inh = seed_main_data(sx)
    phone, ref = phone_and_ref(sx)
    give(sx, ref, matrix=FULL, view_pii=True)
    v = pull(sx, main, "inhumati")["changes"][0]["version"]
    push(sx, main, [op("inhumati", inh, {"nume": "Popescu", "prenume": "Ion", "cnp": CNP, "data_deces": "2026-01-02", "loc_uid": loc}, base=v)])
    push(sx, phone, [op("inhumati", inh, {"nume": "Popescu", "prenume": "Ion", "cnp": CNP2, "data_deces": "2026-01-02", "loc_uid": loc}, base=v)])  # conflict
    new = hx()
    push(sx, phone, [op("concesionari", new, {"nume": "Nou", "cnp": CNP2, "bi_ci": "ZZ 999999"})])
    with adm(sx["pg"]) as c:
        sources = {"sync_v2_ops.response": "SELECT response::text FROM sync_v2_ops",
                   "sync_v2_conflicts": "SELECT incoming::text || coalesce(server::text,'') FROM sync_v2_conflicts",
                   "sync_v2_audit": "SELECT row_to_json(a)::text FROM sync_v2_audit a"}
        found = {}
        for name, sql in sources.items():
            blob = " ".join(str(r[0]) for r in c.execute(sql))
            found[name] = [x for x in (CNP, CNP2, "ZZ 999999") if x in blob]
    print("JURNAL_CNP", found)
    assert found["sync_v2_ops.response"] == [] and found["sync_v2_audit"] == [], found
    # sync_v2_conflicts păstrează intenționat starea completă (accept_incoming/restore_previous o reaplică); accesibil doar MAIN, RLS pe tenant.
    assert found["sync_v2_conflicts"], "se aștepta păstrarea stării pentru rezolvarea conflictului"
