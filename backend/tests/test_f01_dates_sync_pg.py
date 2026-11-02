# ruff: noqa: F811
"""F-01 — datele în Cloud (PostgreSQL local real, date sintetice): formatul canonic pe fir/în Cloud este ISO, istoricul nu se pierde.
Rulează doar cu CIMITIR_TEST_PG_ADMIN_URL (altfel SKIP). NU atinge Supabase."""
from __future__ import annotations

import os

import pytest

from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_cleanup_race_pg import adm, pgdb  # noqa: F401
from test_etapa6b_field_storage import sup  # noqa: F401
from test_etapa6c_field_inbox_pg import fw  # noqa: F401
from test_sync_v2_pg import H, hx, op, pull, push, seed_main_data, sx  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PostgreSQL local de test indisponibil")

CONTRACT_COLS = ("data_contract", "data_inceput", "data_sfarsit")


def _row(sx, table, uid, cols):
    with adm(sx["pg"]) as c:
        r = c.execute(f"SELECT {', '.join(cols)}, version FROM {table} WHERE uid=%s", (uid,)).fetchone()
    return tuple(r[:-1]), r[-1]


def _contract(sx, key, loc, ben, uid=None, **dates):
    uid = uid or hx()
    data = {"numar_contract": "C-" + uid[:4], "concesionar_uid": ben, "loc_uid": loc, "cod_loc": "T-1", "observatii": "o", **dates}
    return uid, push(sx, key, [op("contracte", uid, data)])[0]


def test_main_ro_dates_are_stored_and_served_as_iso_for_every_entity(sx):
    key, loc, ben, inh = seed_main_data(sx)
    cu, r = _contract(sx, key, loc, ben, data_contract="05.03.2026", data_inceput="06.03.2026", data_sfarsit="07.03.2036")
    assert r["status"] == "applied"
    iu, pu, au = hx(), hx(), hx()
    res = push(sx, key, [
        op("inhumati", iu, {"nume": "A", "prenume": "B", "loc_uid": loc, "data_nasterii": "01.02.1950", "data_deces": "2024-02-01", "data_inhumare": "4.2.2024", "data_deshumare": ""}),
        op("plati", pu, {"cod_loc": "T-1", "suma": 10, "data_plata": "05.03.2026", "tip": "Taxă", "contract_uid": cu}),
        op("amenajari_avize", au, {"cod_loc": "T-1", "nr_aviz": "A1", "data_aviz": "05.03.2026", "lucrare": "x", "data_inceput": "06.03.2026", "data_finalizare": "20.03.2026"}),
    ])
    assert [x["status"] for x in res] == ["applied"] * 3
    assert _row(sx, "contracte", cu, CONTRACT_COLS)[0] == ("2026-03-05", "2026-03-06", "2036-03-07")
    assert _row(sx, "inhumati", iu, ("data_nasterii", "data_deces", "data_inhumare", "data_deshumare"))[0] == ("1950-02-01", "2024-02-01", "2024-02-04", "")
    assert _row(sx, "plati", pu, ("data_plata",))[0] == ("2026-03-05",)
    assert _row(sx, "amenajari_avize", au, ("data_aviz", "data_inceput", "data_finalizare"))[0] == ("2026-03-05", "2026-03-06", "2026-03-20")
    phone = sx["T"]["A"]["phone"]
    got = pull(sx, phone, "contracte")["changes"][0]["data"]
    assert (got["data_contract"], got["data_inceput"], got["data_sfarsit"]) == ("2026-03-05", "2026-03-06", "2036-03-07")


def test_invalid_and_blank_dates_survive_cloud_unchanged(sx):
    key, loc, ben, _inh = seed_main_data(sx)
    cu, r = _contract(sx, key, loc, ben, data_contract="31.02.2026", data_inceput="circa 2020", data_sfarsit="")
    assert r["status"] == "applied"
    assert _row(sx, "contracte", cu, CONTRACT_COLS)[0] == ("31.02.2026", "circa 2020", "")      # nici gol, nici data de azi
    got = pull(sx, sx["T"]["A"]["phone"], "contracte")["changes"][0]["data"]
    assert (got["data_contract"], got["data_inceput"], got["data_sfarsit"]) == ("31.02.2026", "circa 2020", "")


def test_legacy_ro_rows_in_cloud_are_served_as_iso_without_rewriting_storage_or_bumping_version(sx):
    key, loc, ben, _inh = seed_main_data(sx)
    cu, _ = _contract(sx, key, loc, ben, data_contract="2026-03-05", data_inceput="2026-03-06", data_sfarsit="2036-03-07")
    with adm(sx["pg"]) as c:       # simulează o înregistrare istorică scrisă înainte de F-01 (zz.ll.aaaa în Cloud)
        c.execute("UPDATE contracte SET data_contract='05.03.2026', data_inceput='06.03.2026', data_sfarsit='07.03.2036' WHERE uid=%s", (cu,))
    stored, ver = _row(sx, "contracte", cu, CONTRACT_COLS)
    assert stored == ("05.03.2026", "06.03.2026", "07.03.2036")
    phone = sx["T"]["A"]["phone"]
    ch = [c for c in pull(sx, phone, "contracte")["changes"] if c["uid"] == cu][0]
    assert (ch["data"]["data_contract"], ch["data"]["data_sfarsit"]) == ("2026-03-05", "2036-03-07")
    assert _row(sx, "contracte", cu, CONTRACT_COLS) == (stored, ver)         # citirea nu scrie nimic
    # MAIN retrimite aceleași zile în ISO (alt format, același conținut): nu e conflict și nu schimbă datele
    again = push(sx, key, [op("contracte", cu, {"numar_contract": "C-" + cu[:4], "concesionar_uid": ben, "loc_uid": loc, "cod_loc": "T-1", "observatii": "o",
                                                "data_contract": "2026-03-05", "data_inceput": "2026-03-06", "data_sfarsit": "2036-03-07"}, base=ver)])[0]
    assert again["status"] in ("applied", "noop", "duplicate"), again
    assert _row(sx, "contracte", cu, CONTRACT_COLS)[1] == ver


def _phone_with_contract_rights(sx, key):
    m = {c: {"view": True, "add": True, "edit": True, "delete": False, "fields": None} for c in
         ("locuri", "harta", "concesionari", "inhumati", "contracte", "plati", "avize", "fisiere")}
    r = sx["client"].put(f"/api/v1/sync-v2/devices/{sx['T']['A']['phone_b']}/matrix", json={"matrix": m}, headers=H(key))
    assert r.status_code == 200, r.text
    return sx["T"]["A"]["phone"]


def test_equivalent_formats_never_create_a_conflict_but_a_real_change_does(sx):
    key, loc, ben, _inh = seed_main_data(sx)
    cu, _ = _contract(sx, key, loc, ben, data_contract="05.03.2026", data_inceput="", data_sfarsit="07.03.2036")
    base = {"numar_contract": "C-" + cu[:4], "concesionar_uid": ben, "loc_uid": loc, "cod_loc": "T-1", "observatii": "o"}
    dates = {"data_contract": "05.03.2026", "data_inceput": "", "data_sfarsit": "07.03.2036"}
    assert push(sx, key, [op("contracte", cu, {**base, **dates, "observatii": "v2"}, base=1)])[0]["version"] == 2
    phone = _phone_with_contract_rights(sx, key)
    same_day = {**base, "observatii": "v2", "data_contract": "2026-03-05", "data_inceput": "", "data_sfarsit": "2036-03-07"}
    r = push(sx, phone, [op("contracte", cu, same_day, base=1)])[0]       # bază veche, conținut echivalent ca zi: nu este conflict
    assert r["status"] != "conflict", r
    r2 = push(sx, phone, [op("contracte", cu, {**same_day, "data_sfarsit": "2036-03-08"}, base=1)])[0]
    assert r2["status"] == "conflict", r2


def test_mobile_edit_with_iso_dates_keeps_all_existing_dates(sx):
    key, loc, ben, _inh = seed_main_data(sx)
    cu, _ = _contract(sx, key, loc, ben, data_contract="05.03.2026", data_inceput="06.03.2026", data_sfarsit="07.03.2036")
    phone = _phone_with_contract_rights(sx, key)
    cur = [c for c in pull(sx, phone, "contracte")["changes"] if c["uid"] == cu][0]
    data = {**cur["data"], "observatii": "modificat de pe telefon"}       # exact ce face formularul Mobile: valorile ISO afișate în <input type=date>
    r = push(sx, phone, [op("contracte", cu, data, base=cur["version"])])[0]
    assert r["status"] == "applied", r
    stored, _ = _row(sx, "contracte", cu, CONTRACT_COLS)
    assert stored == ("2026-03-05", "2026-03-06", "2036-03-07")
    # un client vechi care trimite GOL (bug-ul din audit) rămâne o ștergere explicită: dovada că nu „inventăm" valori în Cloud
    blank = push(sx, phone, [op("contracte", cu, {**data, "data_inceput": ""}, base=r["version"])])[0]
    assert blank["status"] == "applied" and _row(sx, "contracte", cu, CONTRACT_COLS)[0][1] == ""
