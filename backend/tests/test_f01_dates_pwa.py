# ruff: noqa: F811
"""F-01 — Mobile PWA în Chromium real: datele MAIN (zz.ll.aaaa) apar în <input type=date>, editarea nu șterge nicio dată,
valorile neinterpretabile sunt păstrate (câmp doar-citire), golul rămâne gol. PostgreSQL local real; date sintetice."""
from __future__ import annotations

import os
import uuid

import pytest
import requests

import cimitir_dates as cd

from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_cleanup_race_pg import adm, pgdb  # noqa: F401
from test_etapa6b_field_storage import sup  # noqa: F401
from test_etapa6c_field_inbox_pg import fw  # noqa: F401
from test_sync_v2_main_e2e import H
from test_sync_v2_pwa import _open_grave_via_search, _seed_phone, _wait_saved, web  # noqa: F401

pw = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PostgreSQL local de test indisponibil")
API = "/api/v1/sync-v2"


def _rows(w, sql, args=()):
    with adm(w["pg"]) as c:
        return c.execute(sql, args).fetchall()


def _days(row):
    """Ziua calendaristică a fiecărei valori (Cloud poate păstra textul istoric zz.ll.aaaa pentru aceeași zi); text nerecunoscut / gol rămân ca atare."""
    return tuple((cd.parse_date(v).isoformat() if cd.parse_date(v) else v) for v in row)


def _push(w, key, entity, uid, data):
    r = requests.post(f"{w['url']}{API}/push", headers=H(key), timeout=10, json={"ops": [
        {"op_id": "d-" + uuid.uuid4().hex, "entity": entity, "uid": uid, "action": "upsert", "base_version": 0, "data": data}]})
    assert r.json()["result"]["results"][0]["status"] == "applied", r.text


def test_pwa_edit_never_loses_or_invents_dates(web):
    w = web
    main, phone, ref = w["T"]["A"]["main"], w["T"]["A"]["phone"], w["T"]["A"]["phone_b"]
    assert requests.put(f"{w['url']}{API}/devices/{ref}/capabilities", json={"role": "administrator"}, headers=H(main), timeout=10).status_code == 200
    ben, c_ok, c_bad = (uuid.uuid4().hex for _ in range(3))
    _push(w, main, "concesionari", ben, {"nume": "Ionescu", "prenume": "Maria"})
    base = {"concesionar_uid": ben, "loc_uid": w["loc"], "cod_loc": "PWA-1"}
    _push(w, main, "contracte", c_ok, {**base, "numar_contract": "OK-1", "data_contract": "2026-03-05", "data_inceput": "2026-03-06", "data_sfarsit": ""})
    _push(w, main, "contracte", c_bad, {**base, "numar_contract": "BAD-1", "data_contract": "2026-03-05", "data_inceput": "2026-03-06", "data_sfarsit": "2036-03-07"})
    with adm(w["pg"]) as c:      # istoric: Cloud conține formatul MAIN și o valoare neinterpretabilă (înainte de F-01)
        c.execute("UPDATE contracte SET data_contract='05.03.2026', data_inceput='6.3.2026' WHERE uid=%s", (c_ok,))
        c.execute("UPDATE contracte SET data_contract='05.03.2026', data_inceput='31.02.2026', data_sfarsit='circa 2036' WHERE uid=%s", (c_bad,))
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 390, "height": 844}, service_workers="block")
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        page.goto(f"{w['url']}/mobile/")
        _seed_phone(page, phone)

        # 1) contract MAIN: zz.ll.aaaa în Cloud (istoric) -> câmpurile arată data, gol rămâne gol
        _open_grave_via_search(page)
        page.locator(".kv", has_text="OK-1").locator("[data-testid=edit-contracte]").click()
        page.wait_for_selector("[data-testid=field-data_contract]")
        assert page.input_value("[data-testid=field-data_contract]") == "2026-03-05"
        assert page.input_value("[data-testid=field-data_inceput]") == "2026-03-06"
        assert page.input_value("[data-testid=field-data_sfarsit]") == ""
        page.fill("[data-testid=field-observatii]", "editat pe telefon")
        page.click("[data-testid=record-save]")
        _wait_saved(page, w, "SELECT observatii FROM contracte WHERE uid='" + c_ok + "'", [("editat pe telefon",)])
        assert _days(_rows(w, "SELECT data_contract, data_inceput, COALESCE(data_sfarsit,'') FROM contracte WHERE uid=%s", (c_ok,))[0]) == ("2026-03-05", "2026-03-06", "")

        # 2) valori neinterpretabile: câmp doar-citire, trimise neschimbate (nu goale, nu data de azi)
        _open_grave_via_search(page)
        page.locator(".kv", has_text="BAD-1").locator("[data-testid=edit-contracte]").click()
        page.wait_for_selector("[data-testid=field-data_contract]")
        assert page.input_value("[data-testid=field-data_contract]") == "2026-03-05"
        for k, raw in (("data_inceput", "31.02.2026"), ("data_sfarsit", "circa 2036")):
            assert page.is_disabled(f"[data-testid=field-{k}]") and page.input_value(f"[data-testid=field-{k}]") == raw
            assert page.is_visible(f"[data-testid=date-keep-{k}]")
        page.screenshot(path="/tmp/pwa_dates_keep.png")
        page.fill("[data-testid=field-observatii]", "alt edit")
        page.click("[data-testid=record-save]")
        _wait_saved(page, w, "SELECT observatii FROM contracte WHERE uid='" + c_bad + "'", [("alt edit",)])
        assert _days(_rows(w, "SELECT data_contract, data_inceput, data_sfarsit FROM contracte WHERE uid=%s", (c_bad,))[0]) == ("2026-03-05", "31.02.2026", "circa 2036")

        # 3) ștergerea explicită a unei date valide de către utilizator este permisă (nu se confundă cu pierderea)
        _open_grave_via_search(page)
        page.locator(".kv", has_text="OK-1").locator("[data-testid=edit-contracte]").click()
        page.wait_for_selector("[data-testid=field-data_inceput]")
        page.fill("[data-testid=field-data_inceput]", "")
        page.click("[data-testid=record-save]")
        _wait_saved(page, w, "SELECT COALESCE(data_inceput,'') FROM contracte WHERE uid='" + c_ok + "'", [("",)])
        assert _days(_rows(w, "SELECT data_contract FROM contracte WHERE uid=%s", (c_ok,))[0]) == ("2026-03-05",)

        # 4) înhumat creat din MAIN cu zz.ll.aaaa / ISO: se deschide cu valorile și se salvează fără pierderi
        iu = _rows(w, "SELECT uid FROM inhumati")[0][0]
        with adm(w["pg"]) as c:
            c.execute("UPDATE inhumati SET data_nasterii='01.02.1950', data_deces='02.01.2026', data_inhumare='' WHERE uid=%s", (iu,))
        _open_grave_via_search(page)
        page.locator("[data-testid=edit-inhumati]").first.click()
        page.wait_for_selector("[data-testid=field-data_deces]")
        assert [page.input_value(f"[data-testid=field-{k}]") for k in ("data_nasterii", "data_deces", "data_inhumare")] == ["1950-02-01", "2026-01-02", ""]
        page.fill("[data-testid=field-observatii]", "verificat")
        page.click("[data-testid=record-save]")
        _wait_saved(page, w, "SELECT observatii FROM inhumati WHERE uid='" + iu + "'", [("verificat",)])
        assert _days(_rows(w, "SELECT data_nasterii, data_deces, COALESCE(data_inhumare,'') FROM inhumati WHERE uid=%s", (iu,))[0]) == ("1950-02-01", "2026-01-02", "")

        # 5) editare OFFLINE -> coadă -> reconectare: datele (inclusiv cele neinterpretabile) rămân intacte
        _open_grave_via_search(page)
        ctx.set_offline(True)
        page.locator(".kv", has_text="BAD-1").locator("[data-testid=edit-contracte]").click()
        page.wait_for_selector("[data-testid=field-data_contract]")
        assert page.input_value("[data-testid=field-data_contract]") == "2026-03-05"
        assert page.is_disabled("[data-testid=field-data_inceput]")
        page.fill("[data-testid=field-observatii]", "offline edit")
        page.click("[data-testid=record-save]")
        page.wait_for_selector(".toast:has-text('Fără conexiune')")
        assert _rows(w, "SELECT observatii FROM contracte WHERE uid=%s", (c_bad,))[0][0] == "alt edit"
        ctx.set_offline(False)
        page.click("[data-testid=sync-button]")
        page.click("[data-testid=sync-run]")
        page.wait_for_selector(".toast:has-text('modificări trimise')")
        assert _rows(w, "SELECT observatii FROM contracte WHERE uid=%s", (c_bad,))[0][0] == "offline edit"
        assert _days(_rows(w, "SELECT data_contract, data_inceput, data_sfarsit FROM contracte WHERE uid=%s", (c_bad,))[0]) == ("2026-03-05", "31.02.2026", "circa 2036")
        b.close()
    assert errs == []
