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
        page.evaluate("""(k) => new Promise((res, rej) => { const r = indexedDB.open('cimitir-mobile-v2', 1);
            r.onupgradeneeded = () => r.result.createObjectStore('kv');
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
        page.wait_for_selector("[data-testid=add-burial]")
        txt = page.inner_text("#sheet-body")
        assert "Popescu Ion" in txt and "PWA-1" in txt and not UUID_RE.search(txt)
        # adăugare înhumat OFFLINE -> coadă
        ctx.set_offline(True)
        page.click("[data-testid=add-burial]")
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
