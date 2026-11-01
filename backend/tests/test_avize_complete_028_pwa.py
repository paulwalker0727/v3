# ruff: noqa: F811
"""Pasul A — Mobile PWA în Chromium real: formular aviz complet, detalii în fișa locului, validare, offline->coadă->reconectare, avertizare server fără 028."""
from __future__ import annotations

import os
import uuid

import pytest
import requests

from test_avize_complete_028 import FULL, MIG, ROLL, NEW  # noqa: F401
from test_etapa6b_cleanup_race_pg import adm  # noqa: F401
from test_sync_v2_main_e2e import H
from test_sync_v2_pwa import _open_grave_via_search, _seed_phone, _wait_saved, web  # noqa: F401
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_cleanup_race_pg import pgdb  # noqa: F401
from test_etapa6b_field_storage import sup  # noqa: F401
from test_etapa6c_field_inbox_pg import fw  # noqa: F401

pw = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PostgreSQL local de test indisponibil")
API = "/api/v1/sync-v2"


def _rows(w, sql, args=()):
    with adm(w["pg"]) as c:
        return c.execute(sql, args).fetchall()


def test_pwa_aviz_form_details_validation_offline_and_incompat_banner(web):
    w = web
    main, phone, ref = w["T"]["A"]["main"], w["T"]["A"]["phone"], w["T"]["A"]["phone_b"]
    uid = uuid.uuid4().hex
    r = requests.post(f"{w['url']}{API}/push", headers=H(main), timeout=10, json={"ops": [
        {"op_id": "a-" + uuid.uuid4().hex, "entity": "amenajari_avize", "uid": uid, "action": "upsert", "base_version": 0, "data": {**FULL, "cod_loc": "PWA-1"}}]})
    assert r.json()["result"]["results"][0]["status"] == "applied"
    assert requests.put(f"{w['url']}{API}/devices/{ref}/capabilities", json={"role": "operator"}, headers=H(main), timeout=10).status_code == 200
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 390, "height": 844}, service_workers="block")
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(f"{w['url']}/mobile/")
        _seed_phone(page, phone)
        _open_grave_via_search(page)
        txt = page.inner_text("#sheet-body")
        for needle in ("Aviz AV-1", "bordură", "Stare: În lucru", "Executant: Popa SRL", "Început: 2026-03-05", "Finalizat: 2026-03-20", "Observații: fără probleme"):
            assert needle in txt, (needle, txt[:900])
        page.screenshot(path="/tmp/pwa_aviz_detail.png")
        # validare: finalizare înaintea începerii
        page.locator(".kv", has_text="AV-1").locator("[data-testid=edit-amenajari_avize]").click()
        assert page.is_enabled("[data-testid=field-stare]") and not page.is_visible("[data-testid=aviz-ext-incompat]")
        page.fill("[data-testid=field-data_finalizare]", "2026-03-01")
        page.click("[data-testid=record-save]")
        assert "nu poate fi înaintea" in page.inner_text("#sheet-body")
        assert _rows(w, "SELECT data_finalizare FROM amenajari_avize WHERE uid=%s", (uid,))[0][0] == "2026-03-20"
        # modificare validă
        page.select_option("[data-testid=field-stare]", "finalizata")
        page.fill("[data-testid=field-executant]", "Ionescu SRL")
        page.fill("[data-testid=field-data_finalizare]", "2026-03-25")
        page.fill("[data-testid=field-observatii]", "recepționat pe teren")
        page.click("[data-testid=record-save]")
        _wait_saved(page, w, "SELECT stare, executant, data_finalizare, observatii FROM amenajari_avize WHERE uid='" + uid + "'",
                    [("finalizata", "Ionescu SRL", "2026-03-25", "recepționat pe teren")])
        assert _rows(w, "SELECT stare, executant, data_inceput, data_finalizare, observatii, version FROM amenajari_avize WHERE uid=%s", (uid,))[0] == \
            ("finalizata", "Ionescu SRL", "2026-03-05", "2026-03-25", "recepționat pe teren", 2)
        # aviz nou OFFLINE -> coadă -> reconectare, fără duplicate
        _open_grave_via_search(page)
        ctx.set_offline(True)
        page.click("[data-testid=add-amenajari_avize]")
        page.fill("[data-testid=field-nr_aviz]", "AV-OFF")
        page.fill("[data-testid=field-lucrare]", "soclu nou")
        page.select_option("[data-testid=field-stare]", "planificata")
        page.fill("[data-testid=field-executant]", "Offline SRL")
        page.click("[data-testid=record-save]")
        page.wait_for_selector(".toast:has-text('Fără conexiune')")
        assert _rows(w, "SELECT count(*) FROM amenajari_avize")[0][0] == 1
        ctx.set_offline(False)
        page.click("[data-testid=sync-button]")
        page.click("[data-testid=sync-run]")
        page.wait_for_selector(".toast:has-text('modificări trimise')")
        got = _rows(w, "SELECT nr_aviz, stare, executant FROM amenajari_avize ORDER BY nr_aviz")
        assert got == [("AV-1", "finalizata", "Ionescu SRL"), ("AV-OFF", "planificata", "Offline SRL")], got
        page.click("[data-testid=sync-run]")
        assert _rows(w, "SELECT count(*) FROM amenajari_avize")[0][0] == 2
        page.click("[data-testid=sync-close]")
        # server fără 028: formularul avertizează explicit și dezactivează câmpurile noi
        with adm(w["pg"]) as c:
            c.execute((ROLL / "028_avize_complete_ROLLBACK.sql").read_text(encoding="utf-8"))
        try:
            assert requests.get(f"{w['url']}{API}/status", headers=H(phone), timeout=10).json()["result"]["features"] == {"avize_extended": False}
            page.reload()
            page.wait_for_selector("[data-testid=sync-button]")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1500)
            _open_grave_via_search(page)
            page.locator(".kv", has_text="AV-1").locator("[data-testid=edit-amenajari_avize]").click()
            page.wait_for_selector("[data-testid=field-lucrare]")
            page.wait_for_selector("[data-testid=aviz-ext-incompat]", timeout=8000)
            assert "migrarea 028" in page.inner_text("[data-testid=aviz-ext-incompat]")
            assert page.is_disabled("[data-testid=field-stare]") and page.is_disabled("[data-testid=field-executant]") and page.is_enabled("[data-testid=field-lucrare]")
            page.screenshot(path="/tmp/pwa_aviz_incompat.png")
        finally:
            with adm(w["pg"]) as c:
                c.execute((MIG / "028_avize_complete.sql").read_text(encoding="utf-8"))
        b.close()
    assert errs == []
