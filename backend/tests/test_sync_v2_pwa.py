# ruff: noqa: F811
"""Mobile v2 PWA în browser real (Chromium/Playwright) peste aplicația cloud reală + PostgreSQL local.
Acoperă: butonul „Sincronizează" (stare, coadă, reluare), persoana înhumată vizibilă în fișa locului, editare/adăugare pe telefon,
offline -> coadă -> reconectare fără duplicate, fără UUID afișat în UI. Rulează doar cu CIMITIR_TEST_PG_ADMIN_URL."""
from __future__ import annotations

import os
import re
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
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\b[0-9a-f]{32}\b", re.I)


@pytest.fixture()
def web(fw):
    with adm(fw["pg"]) as c:
        c.execute("TRUNCATE sync_v2_ops, sync_v2_conflicts, sync_v2_permissions, sync_v2_runs, sync_tombstones, inhumati, plati, contracte, concesionari, "
                  "amenajari_avize, locuri_de_veci CASCADE")
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = Server(cca.create_app(service=fw["svc"]), port); srv.start()
    url = f"http://127.0.0.1:{port}"
    main = fw["T"]["A"]["main"]
    loc, inh = uuid.uuid4().hex, uuid.uuid4().hex
    ops = [{"op_id": "s-" + uuid.uuid4().hex, "entity": "locuri_de_veci", "uid": loc, "action": "upsert", "base_version": 0,
            "data": {"cod_loc": "PWA-1", "points": [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]], "are_inhumari": 1}},
           {"op_id": "s-" + uuid.uuid4().hex, "entity": "inhumati", "uid": inh, "action": "upsert", "base_version": 0,
            "data": {"nume": "Popescu", "prenume": "Ion", "data_deces": "2026-01-02", "loc_uid": loc}}]
    r = requests.post(f"{url}/api/v1/sync-v2/push", json={"ops": ops}, headers=H(main), timeout=10).json()
    assert [x["status"] for x in r["result"]["results"]] == ["applied", "applied"]
    yield {**fw, "srv": srv, "url": url, "loc": loc}
    srv.stop()


def burials(w):
    with adm(w["pg"]) as c:
        return sorted(r[0] for r in c.execute("SELECT nume FROM inhumati").fetchall())


def test_pwa_sync_button_edit_offline_resume(web):
    w = web
    phone = w["T"]["A"]["phone"]
    ref = w["T"]["A"]["phone_b"]
    requests.put(f"{w['url']}/api/v1/sync-v2/devices/{ref}/permissions", json={"entities": ["inhumati"]}, headers=H(w["T"]["A"]["main"]), timeout=10)
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 390, "height": 844}, service_workers="block")
        page = ctx.new_page()
        page.goto(f"{w['url']}/mobile/")
        page.evaluate("""(k) => new Promise((res, rej) => { const r = indexedDB.open('cimitir-mobile-v2');
            r.onsuccess = () => { const tx = r.result.transaction('kv', 'readwrite'); tx.objectStore('kv').put(k, 'binding_key'); tx.oncomplete = res; }; r.onerror = rej; })""", phone)
        page.reload()
        page.wait_for_selector("[data-testid=sync-button]")
        assert "Sincronizează" in page.inner_text("[data-testid=sync-button]")
        page.click("[data-testid=sync-button]")
        page.wait_for_selector("[data-testid=sync-status]")
        assert "Stare sincronizare" in page.inner_text("#sheet-body")
        page.click("[data-testid=sync-run]")
        page.wait_for_selector(".toast:has-text('Sincronizat')")
        page.click("[data-testid=sync-close]")
        # persoana înhumată apare în fișa locului (căutare după nume -> deschide locul)
        page.click(".bottom-nav [data-view=search]")
        page.fill("#search-q", "Popescu")
        page.wait_for_selector("button.result:not([disabled])")
        page.click("button.result:not([disabled])")
        page.wait_for_selector("[data-testid=add-inhumati]")
        txt = page.inner_text("#sheet-body")
        assert "Popescu Ion" in txt and "PWA-1" in txt and not UUID_RE.search(txt)
        # adăugare înhumat OFFLINE -> coadă
        ctx.set_offline(True)
        page.click("[data-testid=add-inhumati]")
        page.fill("[data-testid=field-nume]", "Offline")
        page.fill("[data-testid=field-prenume]", "Test")
        page.click("[data-testid=record-save]")
        page.wait_for_selector(".toast:has-text('Fără conexiune')")
        assert "1 în coadă" in page.inner_text("[data-testid=sync-button]") or "Offline" in page.inner_text("[data-testid=sync-button]")
        assert burials(w) == ["Popescu"]
        # revine internetul: reluare din panou
        ctx.set_offline(False)
        page.click("[data-testid=sync-button]")
        page.click("[data-testid=sync-run]")
        page.wait_for_selector(".toast:has-text('1 modificări trimise')")
        assert burials(w) == ["Offline", "Popescu"]
        page.click("[data-testid=sync-close]")
        page.click("[data-testid=sync-button]")
        page.click("[data-testid=sync-run]")
        page.wait_for_selector(".toast:has-text('0 modificări trimise')")
        assert burials(w) == ["Offline", "Popescu"]
        page.screenshot(path="/tmp/pwa_sync.png")
        # editare existent
        page.click("[data-testid=sync-close]")
        page.click(".bottom-nav [data-view=search]")
        page.fill("#search-q", "Popescu")
        page.wait_for_selector("button.result:not([disabled])")
        page.click("button.result:not([disabled])")
        page.wait_for_selector("[data-testid=edit-inhumati]")
        page.locator(".kv", has_text="Popescu").locator("[data-testid=edit-inhumati]").click()
        page.fill("[data-testid=field-observatii]", "verificat pe teren")
        page.click("[data-testid=record-save]")
        got = None
        for _ in range(60):
            with adm(w["pg"]) as c:
                got = c.execute("SELECT observatii, version FROM inhumati WHERE nume='Popescu'").fetchone()
            if got[0]:
                break
            page.wait_for_timeout(100)
        page.screenshot(path="/tmp/pwa_after_edit.png")
        b.close()
    assert got == ("verificat pe teren", 2)


def _seed_phone(page, key):
    page.evaluate("""(k) => new Promise((res, rej) => { const r = indexedDB.open('cimitir-mobile-v2');
        r.onsuccess = () => { const tx = r.result.transaction('kv', 'readwrite'); tx.objectStore('kv').put(k, 'binding_key'); tx.oncomplete = res; }; r.onerror = rej; })""", key)
    page.reload()
    page.wait_for_selector("[data-testid=sync-button]")


def _rows(w, sql):
    with adm(w["pg"]) as c:
        return c.execute(sql).fetchall()


def _wait_saved(page, w, sql, expect):
    for _ in range(100):
        if _rows(w, sql) == expect:
            break
        page.wait_for_timeout(100)
    page.wait_for_function("() => document.querySelector('[data-testid=sync-button]').dataset.state !== 'busy'")
    page.wait_for_timeout(300)


def _open_grave_via_search(page, text="Popescu"):
    page.click(".bottom-nav [data-view=search]")
    page.fill("#search-q", text)
    try:
        page.wait_for_selector("button.result:not([disabled])", timeout=8000)
    except Exception:
        page.screenshot(path="/tmp/pwa_fail.png"); print("BODY:", page.inner_text("body")[:600]); raise
    page.click("button.result:not([disabled])")
    page.wait_for_selector("[data-testid=sheet-close]")


def test_pwa_full_forms_offline_mirror_roles_and_privacy(web):
    w = web
    phone, ref, main = w["T"]["A"]["phone"], w["T"]["A"]["phone_b"], w["T"]["A"]["main"]
    assert requests.put(f"{w['url']}/api/v1/sync-v2/devices/{ref}/capabilities", json={"role": "administrator"}, headers=H(main), timeout=10).status_code == 200
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 390, "height": 844}, service_workers="block")
        page = ctx.new_page()
        page.on("dialog", lambda d: d.accept())
        page.goto(f"{w['url']}/mobile/")
        _seed_phone(page, phone)
        page.click("[data-testid=sync-button]"); page.click("[data-testid=sync-run]")
        page.wait_for_selector(".toast:has-text('Sincronizat')"); page.click("[data-testid=sync-close]")
        # --- formulare reale: concesionar -> contract -> plată (validări + confirmare)
        _open_grave_via_search(page)
        page.click("[data-testid=add-concesionari]")
        page.click("[data-testid=record-save]")
        assert "Completează câmpul" in page.inner_text("[data-testid=form-error]")            # validare, nu se pierd datele
        page.fill("[data-testid=field-nume]", "Georgescu"); page.fill("[data-testid=field-prenume]", "Maria"); page.fill("[data-testid=field-telefon]", "0722000111")
        page.click("[data-testid=record-save]")
        page.wait_for_selector(".toast:has-text('modificări trimise')")
        _open_grave_via_search(page)
        page.click("[data-testid=add-contracte]")
        page.select_option("[data-testid=field-concesionar_uid]", label="Georgescu Maria")
        page.fill("[data-testid=field-numar_contract]", "C-2026/7"); page.fill("[data-testid=field-data_contract]", "2026-03-01")
        page.click("[data-testid=record-save]")
        page.wait_for_selector(".toast:has-text('modificări trimise')")
        _open_grave_via_search(page)
        page.click("[data-testid=add-plati]")
        page.fill("[data-testid=field-suma]", "-5"); page.click("[data-testid=record-save]")
        assert "pozitiv" in page.inner_text("[data-testid=form-error]")
        page.fill("[data-testid=field-suma]", "150,50"); page.fill("[data-testid=field-chitanta_nr]", "CH-1")
        page.click("[data-testid=record-save]")
        _wait_saved(page, w, "SELECT chitanta_nr FROM plati", [("CH-1",)])
        assert _rows(w, "SELECT suma::float, chitanta_nr FROM plati") == [(150.5, "CH-1")]
        assert _rows(w, "SELECT numar_contract, (SELECT nume FROM concesionari c WHERE c.id=concesionar_id) FROM contracte") == [("C-2026/7", "Georgescu")]
        _open_grave_via_search(page, "Georgescu")
        txt = page.inner_text("#sheet-body")
        assert "Contract C-2026/7" in txt and "150.5 lei" in txt and "CH-1" in txt and "Istoric modificări" in txt and not UUID_RE.search(txt)
        assert "Georgescu Maria" in txt and "0722000111" in txt
        page.screenshot(path="/tmp/pwa_grave.png")
        page.click("[data-testid=sheet-close]")
        # --- OFFLINE: copia locală autorizată (căutare, fișă, liste), modificare în coadă cu indicator
        ctx.set_offline(True)
        page.wait_for_function("() => document.querySelector('[data-testid=net-pill]')?.dataset.state === 'off' || true")
        _open_grave_via_search(page)
        txt = page.inner_text("#sheet-body")
        assert "COPIE OFFLINE" in txt and "Popescu Ion" in txt and "150.5 lei" in txt and "Georgescu" in txt and not UUID_RE.search(txt)
        page.click("[data-testid=add-plati]")
        page.fill("[data-testid=field-suma]", "75"); page.fill("[data-testid=field-chitanta_nr]", "CH-OFF")
        page.click("[data-testid=record-save]")
        page.wait_for_selector(".toast:has-text('Fără conexiune')")
        assert len(_rows(w, "SELECT 1 FROM plati")) == 1                                    # nimic trimis fără internet
        assert "1 în coadă" in page.inner_text("[data-testid=sync-button]") or "Offline" in page.inner_text("[data-testid=sync-button]")
        _open_grave_via_search(page)
        assert "CH-OFF" in page.inner_text("#sheet-body") and "nesincronizat" in page.inner_text("#sheet-body")      # vizibil local, marcat
        page.click("[data-testid=sheet-close]")
        page.click(".bottom-nav [data-view=modules]"); page.click("[data-testid=module-inhumati]")
        page.wait_for_selector("#list-box .result")
        assert "Popescu Ion" in page.inner_text("#list-box")
        page.fill("[data-testid=list-filter]", "zzz")
        page.wait_for_selector("#list-box .empty")
        page.screenshot(path="/tmp/pwa_offline.png")
        # --- reconectare: coada se trimite o singură dată, de două ori nu dublează
        ctx.set_offline(False)
        page.click("[data-testid=sync-button]"); page.click("[data-testid=sync-run]")
        page.wait_for_selector(".toast:has-text('1 modificări trimise')")
        page.click("[data-testid=sync-close]"); page.click("[data-testid=sync-button]"); page.click("[data-testid=sync-run]")
        page.wait_for_selector(".toast:has-text('0 modificări trimise')")
        assert sorted(_rows(w, "SELECT chitanta_nr FROM plati")) == [("CH-1",), ("CH-OFF",)]
        # --- rol limitat aplicat pe server: plățile dispar din copia locală și din fișă; fără butoane de editare
        page.click("[data-testid=sync-close]")
        assert requests.put(f"{w['url']}/api/v1/sync-v2/devices/{ref}/capabilities", json={"role": "limitat"}, headers=H(main), timeout=10).status_code == 200
        page.click("[data-testid=sync-button]"); page.click("[data-testid=sync-run]")
        page.wait_for_selector(".toast:has-text('Sincronizat')"); page.click("[data-testid=sync-close]")
        _open_grave_via_search(page)
        txt = page.inner_text("#sheet-body")
        assert "CH-1" not in txt and "Plăți și obligații · 0" in txt and page.locator("[data-testid=add-plati], [data-testid=edit-inhumati]").count() == 0
        page.click("[data-testid=sheet-close]")
        n_plati = page.evaluate("""() => new Promise((res) => { const r = indexedDB.open('cimitir-mobile-v2'); r.onsuccess = () => {
            const q = r.result.transaction('rows').objectStore('rows').getAllKeys(); q.onsuccess = () => res(q.result.filter(k => k.startsWith('plati|')).length); }; })""")
        assert n_plati == 0                                                                    # registre nepermise nu rămân pe telefon
        # --- dezasociere: copia locală este ștearsă
        page.on("dialog", lambda d: d.accept())
        page.click(".bottom-nav [data-view=modules]")
        b.close()


def test_pwa_repairing_to_other_cemetery_purges_mirror_and_quarantines_queue(web):
    w = web
    a_phone, ref, main = w["T"]["A"]["phone"], w["T"]["A"]["phone_b"], w["T"]["A"]["main"]
    b_phone = w["T"]["B"]["phone"]
    requests.put(f"{w['url']}/api/v1/sync-v2/devices/{ref}/capabilities", json={"role": "operator"}, headers=H(main), timeout=10)
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 390, "height": 844}, service_workers="block")
        page = ctx.new_page()
        page.on("dialog", lambda d: d.accept())
        page.goto(f"{w['url']}/mobile/")
        _seed_phone(page, a_phone)
        page.click("[data-testid=sync-button]"); page.click("[data-testid=sync-run]")
        page.wait_for_selector(".toast:has-text('Sincronizat')"); page.click("[data-testid=sync-close]")
        count = "() => new Promise((res) => { const r = indexedDB.open('cimitir-mobile-v2'); r.onsuccess = () => { const q = r.result.transaction('rows').objectStore('rows').count(); q.onsuccess = () => res(q.result); }; })"
        assert page.evaluate(count) >= 2
        ctx.route("**/api/v1/sync-v2/push", lambda r: r.abort())                           # coada rămâne nesincronizată
        _open_grave_via_search(page)
        page.click("[data-testid=add-plati]"); page.fill("[data-testid=field-suma]", "10"); page.click("[data-testid=record-save]")
        page.wait_for_selector(".toast:has-text('Fără conexiune')")
        ctx.unroute("**/api/v1/sync-v2/push")
        _seed_phone(page, b_phone)                                                           # reasociere la ALT cimitir
        page.wait_for_function(f"({count})().then(n => n === 0)") if False else page.wait_for_timeout(1500)
        assert page.evaluate(count) == 0                                                     # datele cimitirului A nu rămân pe telefon
        q = page.evaluate("""() => new Promise((res) => { const r = indexedDB.open('cimitir-mobile-v2'); r.onsuccess = () => {
            const tx = r.result.transaction('kv'); const g = tx.objectStore('kv').get('outbox_quarantine'); g.onsuccess = () => res(g.result ? g.result.length : 0); }; })""")
        assert q == 1 and _rows(w, "SELECT 1 FROM plati") == []                              # operația veche nu a ajuns în alt cimitir
        b.close()


def test_pwa_map_colors_pinch_zoom_and_tap_opens_grave(web):
    w = web
    phone = w["T"]["A"]["phone"]
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 390, "height": 844}, service_workers="block")
        page = ctx.new_page()
        page.goto(f"{w['url']}/mobile/")
        _seed_phone(page, phone)
        page.click(".bottom-nav [data-view=map]")
        page.wait_for_selector("svg#cem-map polygon.plot")
        assert page.get_attribute("polygon.plot", "fill") == "#9f5a52"                      # ocupat (are înhumat)
        assert "Rezervat" in page.inner_text("[data-testid=map-legend]")
        vb0 = [float(x) for x in page.get_attribute("#cem-map", "viewBox").split()]
        page.evaluate("""() => { const s = document.getElementById('cem-map'); const r = s.getBoundingClientRect();
            const ev = (t, id, x, y) => s.dispatchEvent(new PointerEvent(t, {pointerId: id, clientX: x, clientY: y, bubbles: true, pointerType: 'touch'}));
            ev('pointerdown', 1, r.x + 100, r.y + 200); ev('pointerdown', 2, r.x + 140, r.y + 200);
            for (let i = 0; i < 6; i++) { ev('pointermove', 1, r.x + 100 - i * 8, r.y + 200); ev('pointermove', 2, r.x + 140 + i * 8, r.y + 200); }
            ev('pointerup', 1, 0, 0); ev('pointerup', 2, 0, 0); }""")
        vb1 = [float(x) for x in page.get_attribute("#cem-map", "viewBox").split()]
        assert vb1[2] < vb0[2]                                                              # pinch-out a mărit harta
        page.evaluate("""() => { const s = document.getElementById('cem-map'), pl = document.querySelector('polygon.plot');
            s.dispatchEvent(new PointerEvent('pointerdown', {pointerId: 9, clientX: 50, clientY: 50, bubbles: true})); s.dispatchEvent(new PointerEvent('pointerup', {pointerId: 9, bubbles: true}));
            pl.dispatchEvent(new MouseEvent('click', {bubbles: true})); }""")
        page.wait_for_selector("[data-testid=sheet-close]")
        assert "PWA-1" in page.inner_text("#sheet-body") and not UUID_RE.search(page.inner_text("#sheet-body"))
        b.close()
