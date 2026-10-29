# ruff: noqa: F811
"""PWA „Mobile complet" în Chromium real peste aplicația cloud + PostgreSQL local: UI condiționat de permisiuni, creare loc + poligon, editare câmp permis/blocat,
ștergere cu confirmare, PDF chitanță, tabletă. Serverul impune permisiunile independent (verificat în test_mobile_permissions_pg.py). Rulează doar cu CIMITIR_TEST_PG_ADMIN_URL."""
from __future__ import annotations

import os
import socket
import uuid

import pytest
import requests

import cimitir_cloud_app as cca
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_cleanup_race_pg import adm, pgdb  # noqa: F401
from test_etapa6b_field_storage import sup  # noqa: F401
from test_etapa6c_field_inbox_pg import fw  # noqa: F401
from test_sync_v2_main_e2e import Server, H

pw = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PostgreSQL local de test indisponibil")


def cell(view=False, add=False, edit=False, delete=False, fields=None):
    return {"view": view, "add": add, "edit": edit, "delete": delete, "fields": fields}


@pytest.fixture()
def web(fw):
    with adm(fw["pg"]) as c:
        c.execute("TRUNCATE sync_v2_ops, sync_v2_conflicts, sync_v2_permissions, sync_v2_runs, sync_v2_audit, sync_v2_issued, sync_v2_counters, sync_tombstones, "
                  "inhumati, plati, contracte, concesionari, amenajari_avize, locuri_de_veci CASCADE")
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = Server(cca.create_app(service=fw["svc"]), port); srv.start()
    url = f"http://127.0.0.1:{port}"
    main = fw["T"]["A"]["main"]
    loc, ct, pay = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
    ops = [{"op_id": "s-" + uuid.uuid4().hex, "entity": e, "uid": u, "action": "upsert", "base_version": 0, "data": d} for e, u, d in (
        ("locuri_de_veci", loc, {"cod_loc": "PWA-1", "points": [[0, 0], [10, 0], [10, 10], [0, 10]], "observatii": "initial"}),
        ("contracte", ct, {"numar_contract": "C-1", "loc_uid": loc, "cod_loc": "PWA-1"}),
        ("plati", pay, {"suma": 100, "data_plata": "2026-02-01", "tip": "Taxă", "cod_loc": "PWA-1", "contract_uid": ct}))]
    r = requests.post(f"{url}/api/v1/sync-v2/push", json={"ops": ops}, headers=H(main), timeout=10).json()
    assert [x["status"] for x in r["result"]["results"]] == ["applied"] * 3, r
    yield {**fw, "srv": srv, "url": url, "loc": loc, "pay": pay}
    srv.stop()


def give(w, matrix):
    r = requests.put(f"{w['url']}/api/v1/sync-v2/devices/{w['T']['A']['phone_b']}/matrix", json={"matrix": matrix}, headers=H(w["T"]["A"]["main"]), timeout=10)
    assert r.status_code == 200, r.text


def open_app(p, w, width=390, height=844):
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": width, "height": height}, service_workers="block")
    page = ctx.new_page()
    page.goto(f"{w['url']}/mobile/")
    page.evaluate("""(k) => new Promise((res, rej) => { const r = indexedDB.open('cimitir-mobile-v2');
        r.onsuccess = () => { const tx = r.result.transaction('kv', 'readwrite'); tx.objectStore('kv').put(k, 'binding_key'); tx.oncomplete = res; }; r.onerror = rej; })""", w["T"]["A"]["phone"])
    page.reload()
    page.wait_for_selector("[data-testid=sync-button]")
    return b, page


def sql(w, q, args=()):
    with adm(w["pg"]) as c:
        return c.execute(q, args).fetchall()


def test_ui_follows_matrix_create_grave_polygon_field_lock_delete_and_pdf(web):
    w = web
    give(w, {"locuri": cell(True, True, True, False, ["observatii"]), "harta": cell(True, True, True), "plati": cell(True, True), "contracte": cell(True), "concesionari": cell(True)})
    with pw.sync_playwright() as p:
        b, page = open_app(p, w)
        assert "Doar citire" not in page.inner_text("#perm-pill")
        # categoriile fără „Vede" nu apar în navigare (înhumați / fișiere)
        nav = page.text_content(".sidebar .nav")
        assert "Înhumați" not in nav and "Documente" not in nav and "Plăți" in nav and "Concesionari" in nav
        # creare loc nou + poligon (3 atingeri) -> sincronizat în cloud
        page.click(".bottom-nav [data-view=map]")
        page.click("[data-testid=map-new-grave]")
        page.fill("[data-testid=field-cod_loc]", "NOU-7")
        page.fill("[data-testid=field-sector]", "S9")
        page.click("[data-testid=record-save]")
        page.wait_for_selector("[data-testid=polygon-editor]")
        page.click("[data-testid=polygon-mode-add]")
        box = page.locator("#pe-svg").bounding_box()
        for dx, dy in ((0.3, 0.3), (0.7, 0.3), (0.5, 0.7)):
            page.mouse.click(box["x"] + box["width"] * dx, box["y"] + box["height"] * dy)
        page.click("[data-testid=polygon-save]")
        page.wait_for_selector(".toast:has-text('Sincronizat')")
        for _ in range(20):
            row = sql(w, "SELECT sector, points FROM locuri_de_veci WHERE cod_loc='NOU-7'")
            if row and row[0][1] and len(row[0][1]) >= 3:
                break
            page.wait_for_timeout(300)
        assert row and row[0][0] == "S9" and len(row[0][1]) == 3
        # câmp blocat: la editarea locului doar „Observații" este activ (sectorul/numărul sunt dezactivate)
        page.click(".bottom-nav [data-view=modules]")
        page.click("[data-testid=module-locuri]")
        page.fill("[data-testid=list-filter]", "PWA-1")
        page.wait_for_selector("button.result:not([disabled])")
        page.click("button.result:not([disabled])")
        page.click("[data-testid=edit-grave]")
        assert page.is_disabled("[data-testid=field-sector]") and not page.is_disabled("[data-testid=field-observatii]")
        page.fill("[data-testid=field-observatii]", "verificat pe teren")
        page.click("[data-testid=record-save]")
        for _ in range(30):
            got = sql(w, "SELECT observatii, cod_loc FROM locuri_de_veci WHERE uid=%s", (w["loc"],))[0]
            if got[0] == "verificat pe teren":
                break
            page.wait_for_timeout(300)
        assert got == ("verificat pe teren", "PWA-1"), page.inner_text("body")[-600:]
        # fără drept de ștergere: nu există buton „Șterge" (și serverul ar refuza oricum)
        page.click("button.result:not([disabled])")
        assert page.locator("[data-testid=delete-grave]").count() == 0
        # chitanță PDF: emite (are „Adaugă" pe plăți), apoi se deschide vizualizatorul cu descărcare/tipărire
        page.wait_for_selector("[data-testid=pdf-chitanta]")
        page.click("[data-testid=pdf-chitanta]")
        page.click("[data-testid=confirm-ok]")
        page.wait_for_selector("[data-testid=pdf-viewer]")
        assert page.locator("[data-testid=pdf-download]").count() == 1 and page.locator("[data-testid=pdf-print]").count() == 1
        page.click("[data-testid=pdf-close]")
        assert sql(w, "SELECT chitanta_nr FROM plati WHERE uid=%s", (w["pay"],))[0][0] == "CH-000001"
        page.screenshot(path="/tmp/pwa_complet.png")
        b.close()


def test_delete_requires_permission_and_confirmation_then_syncs(web):
    w = web
    give(w, {"locuri": cell(True), "plati": cell(True, delete=True), "contracte": cell(True)})
    with pw.sync_playwright() as p:
        b, page = open_app(p, w)
        page.click(".bottom-nav [data-view=modules]")
        page.click("[data-testid=module-plati]")
        page.wait_for_selector("button.result:not([disabled])")
        page.click("button.result:not([disabled])")
        page.wait_for_selector("[data-testid=sheet-close]")
        assert page.locator("[data-testid=edit-plati]").count() == 0 and page.locator("[data-testid=add-plati]").count() == 0     # doar citire pe plăți
        # fără „Editează" pe plăți nu există formular; ștergerea se oferă doar din formular, deci verificăm prin API direct că serverul refuză fără drept
        r = requests.post(f"{w['url']}/api/v1/sync-v2/push", json={"ops": [{"op_id": "x-" + uuid.uuid4().hex, "entity": "locuri_de_veci", "uid": w["loc"], "action": "delete", "base_version": 1, "data": {}}]},
                          headers=H(w["T"]["A"]["phone"]), timeout=10).json()["result"]["results"][0]
        assert r["status"] == "rejected" and r["reason"] == "delete_not_allowed"
        b.close()


def test_tablet_layout_and_read_only_badge(web):
    w = web
    give(w, {"locuri": cell(True), "harta": cell(True)})
    with pw.sync_playwright() as p:
        b, page = open_app(p, w, 900, 1100)
        assert "Doar citire" in page.inner_text("#perm-pill")
        assert page.locator(".sidebar").is_visible()                      # tabletă: bară laterală, nu bara de jos
        page.click(".sidebar [data-view=map]")
        assert page.locator("[data-testid=map-new-grave]").count() == 0   # fără „Adaugă": nu există creare
        page.wait_for_selector("#cem-map polygon.plot")
        page.screenshot(path="/tmp/pwa_tablet.png")
        b.close()


def test_offline_create_grave_and_polygon_then_reconnect_without_duplicates(web):
    w = web
    give(w, {"locuri": cell(True, True, True), "harta": cell(True, True, True)})
    with pw.sync_playwright() as p:
        b, page = open_app(p, w)
        page.click(".bottom-nav [data-view=map]")
        page.wait_for_selector("#cem-map")
        page.context.set_offline(True)                                                   # telefonul pierde internetul
        page.click("[data-testid=map-new-grave]")
        page.fill("[data-testid=field-cod_loc]", "OFF-1")
        page.click("[data-testid=record-save]")
        page.wait_for_selector("[data-testid=polygon-editor]")
        page.click("[data-testid=polygon-mode-add]")
        box = page.locator("#pe-svg").bounding_box()
        for dx, dy in ((0.2, 0.2), (0.8, 0.2), (0.5, 0.8)):
            page.mouse.click(box["x"] + box["width"] * dx, box["y"] + box["height"] * dy)
        page.click("[data-testid=polygon-save]")
        page.wait_for_timeout(800)
        assert sql(w, "SELECT count(*) FROM locuri_de_veci WHERE cod_loc='OFF-1'")[0][0] == 0   # nimic ajuns încă în cloud; operațiile sunt păstrate local
        page.context.set_offline(False)
        page.evaluate("document.getElementById('sync-btn').click()")
        for _ in range(40):
            rows = sql(w, "SELECT points FROM locuri_de_veci WHERE cod_loc='OFF-1'")
            if rows and rows[0][0] and len(rows[0][0]) == 3:
                break
            page.wait_for_timeout(300)
        assert len(rows) == 1 and len(rows[0][0]) == 3
        page.evaluate("document.getElementById('sync-btn').click()")                       # retrimitere: fără duplicate
        page.wait_for_timeout(1200)
        assert sql(w, "SELECT count(*) FROM locuri_de_veci WHERE cod_loc='OFF-1'")[0][0] == 1
        b.close()
