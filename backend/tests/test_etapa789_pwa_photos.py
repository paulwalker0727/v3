# ruff: noqa: F811
"""PWA în Chromium real: fotografii OFFLINE -> coadă (IndexedDB) -> reluare automată -> fără duplicate; indicator de nesincronizat; documente MAIN vizibile pe telefon.
Camera reală NU e testată (file chooser simulat) — vezi raportul. Rulează doar cu CIMITIR_TEST_PG_ADMIN_URL."""
from __future__ import annotations

import hashlib
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
from test_sync_v2_pwa import _seed_phone

pw = pytest.importorskip("playwright.sync_api")
TMO = float(os.environ.get("CIMITIR_TEST_HTTP_TIMEOUT", "10"))   # mai mare pe Supabase LIVE (latență rețea)
pytestmark = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PostgreSQL local de test indisponibil")
PHOTO = b"\x89PNG\r\n\x1a\n" + b"teren-offline" * 60
MAIN_PNG = b"\x89PNG\r\n\x1a\n" + b"din-main" * 30


@pytest.fixture()
def web(fw, monkeypatch):
    monkeypatch.setattr(cca, "probe_storage", lambda s: True)
    with adm(fw["pg"]) as c:
        c.execute("TRUNCATE sync_v2_documents, sync_v2_ops, sync_v2_conflicts, sync_v2_permissions, sync_v2_runs, sync_tombstones, inhumati, plati, contracte, concesionari, "
                  "amenajari_avize, locuri_de_veci CASCADE")
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = Server(cca.create_app(service=fw["svc"]), port); srv.start()
    url, main = f"http://127.0.0.1:{port}", fw["T"]["A"]["main"]
    loc = uuid.uuid4().hex
    r = requests.post(f"{url}/api/v1/sync-v2/push", headers=H(main), timeout=TMO, json={"ops": [
        {"op_id": "s-" + uuid.uuid4().hex, "entity": "locuri_de_veci", "uid": loc, "action": "upsert", "base_version": 0, "data": {"cod_loc": "PH-1", "points": [[0, 0], [9, 0], [9, 9]]}}]}).json()
    assert r["result"]["results"][0]["status"] == "applied"
    meta = {"uid": uuid.uuid4().hex, "entity": "locuri_de_veci", "entity_uid": loc, "doc_type": "fotografie_loc", "kind": "photo", "filename": "din-main.png", "mime": "image/png",
            "size": len(MAIN_PNG), "sha256": hashlib.sha256(MAIN_PNG).hexdigest()}
    assert requests.post(f"{url}/api/v1/sync-v2/documents/declare", json=meta, headers=H(main), timeout=TMO).status_code == 200
    assert requests.put(f"{url}/api/v1/sync-v2/documents/{meta['uid']}/content", data=MAIN_PNG, headers=H(main), timeout=TMO).status_code == 200
    requests.put(f"{url}/api/v1/sync-v2/devices/{fw['T']['A']['phone_b']}/capabilities", headers=H(main), timeout=TMO,
                 json={"capabilities": ["view_map", "read_registers", "upload_documents"]}).raise_for_status()
    yield {**fw, "srv": srv, "url": url}
    srv.stop()


def cloud_docs(w):
    with adm(w["pg"]) as c:
        return c.execute("SELECT filename, origin, status FROM sync_v2_documents WHERE status='READY' ORDER BY created_at").fetchall()


def test_offline_photo_queue_resumes_without_duplicates_and_main_doc_visible(web, tmp_path):
    w = web
    f1 = tmp_path / "poza1.png"; f1.write_bytes(PHOTO)
    f2 = tmp_path / "poza1_copie.png"; f2.write_bytes(PHOTO)
    bad = tmp_path / "fals.png"; bad.write_bytes(b"MZ nu e imagine")
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 390, "height": 844}, service_workers="block")
        page = ctx.new_page()
        page.goto(f"{w['url']}/mobile/")
        _seed_phone(page, w["T"]["A"]["phone"])
        page.click(".bottom-nav [data-view=search]")
        page.fill("#search-q", "PH-1")
        page.wait_for_selector("button.result:not([disabled])")
        page.click("button.result:not([disabled])")
        page.wait_for_selector("[data-testid=grave-documents] [data-testid=grave-doc-row]")      # documentul din MAIN apare pe telefon
        assert "din-main.png" in page.inner_text("[data-testid=grave-documents]")
        page.click("[data-testid=grave-doc-row]")
        page.wait_for_selector("[data-testid=photo-viewer] img")                                  # se deschide (octeți verificați de server)
        page.click("[data-testid=photo-viewer]")
        # --- OFFLINE: fotografie în coadă, indicator, fără trimitere
        ctx.set_offline(True)
        for name, fp in (("poza", f1), ("copie identică", f2), ("falsă", bad)):
            with page.expect_file_chooser() as fc:
                page.click("[data-testid=add-grave-gallery]")
            fc.value.set_files(str(fp))
            page.wait_for_selector(".toast")
            page.wait_for_timeout(300)
        txt = page.inner_text("[data-testid=grave-documents]")
        assert txt.count("Nesincronizată") == 1, txt                                              # copia identică și fișierul fals nu intră în coadă
        assert "(1 foto)" in page.inner_text("[data-testid=sync-button]")
        assert len(cloud_docs(w)) == 1
        ctx.route("**/api/**", lambda r: r.abort())
        ctx.set_offline(False)                                                # pagina se încarcă, API-ul nu răspunde (Cloud căzut)
        page.reload()                                                                             # coada supraviețuiește repornirii aplicației
        page.wait_for_selector("[data-testid=sync-button]")
        page.wait_for_timeout(800)
        assert "(1 foto)" in page.inner_text("[data-testid=sync-button]")
        assert len(cloud_docs(w)) == 1
        # --- ONLINE: reluare automată
        ctx.unroute("**/api/**")
        page.evaluate("window.dispatchEvent(new Event('online'))")
        for _ in range(80):
            if len(cloud_docs(w)) == 2:
                break
            page.wait_for_timeout(150)
        docs = cloud_docs(w)
        assert [d[1] for d in docs] == ["MAIN", "MOBILE"] and docs[1][2] == "READY", docs
        page.wait_for_function("!document.querySelector('[data-testid=sync-button]').textContent.includes('foto')")
        page.screenshot(path="/tmp/pwa_photos.png")
        page.reload()
        page.wait_for_selector("[data-testid=sync-button]")
        page.click(".bottom-nav [data-view=search]")
        page.fill("#search-q", "PH-1")
        page.wait_for_selector("button.result:not([disabled])")
        page.click("button.result:not([disabled])")
        page.wait_for_selector("[data-testid=add-grave-gallery]")
        # retrimitere a aceleiași fotografii: serverul răspunde duplicat, nu creează al doilea rând
        ctx.set_offline(True)
        with page.expect_file_chooser() as fc:
            page.click("[data-testid=add-grave-gallery]")
        fc.value.set_files(str(f1))
        page.wait_for_timeout(500)
        ctx.set_offline(False)
        page.evaluate("window.dispatchEvent(new Event('online'))")
        page.wait_for_function("!document.querySelector('[data-testid=sync-button]').textContent.includes('foto')")
        b.close()
    assert len(cloud_docs(w)) == 2


def test_old_offline_copy_with_personal_data_is_purged_on_upgrade(web):
    w = web
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_context(service_workers="block").new_page()
        page.goto(f"{w['url']}/mobile-assets/mobile_offline.js")
        page.evaluate("""() => new Promise((res, rej) => { const r = indexedDB.open('cimitir-mobile-v2', 2);
            r.onupgradeneeded = () => { for (const s of ['kv', 'rows']) r.result.createObjectStore(s); };
            r.onsuccess = () => { const tx = r.result.transaction('rows', 'readwrite'); tx.objectStore('rows').put({uid: 'u1', data: {nume: 'Ionescu', cnp: '1800101123456', bi_ci: 'XB123456'}, nested: [{owner_cnp: '2750505654321'}]}, 'concesionari:u1');
                tx.oncomplete = () => { r.result.close(); res(); }; tx.onerror = () => rej(tx.error); }; })""")
        page.goto(f"{w['url']}/mobile/")
        page.wait_for_selector("body")
        dump = page.evaluate("""() => new Promise((res, rej) => { const r = indexedDB.open('cimitir-mobile-v2');
            r.onsuccess = () => { const q = r.result.transaction('rows').objectStore('rows').getAll(); q.onsuccess = () => res(JSON.stringify(q.result)); q.onerror = () => rej(q.error); }; })""")
        b.close()
    assert "Ionescu" in dump and not any(x in dump for x in ("1800101123456", "XB123456", "2750505654321", "cnp", "bi_ci"))
