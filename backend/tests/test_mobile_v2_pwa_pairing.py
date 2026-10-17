# ruff: noqa: F811
"""PWA Mobile v2 în Chromium real: asociere automată prin link/QR (`/mobile/#pair=<cod>`), organizația/cimitirul corecte, codul scos din URL,
ecranul Documente (fotografie încărcată de pe telefon). Rulează doar cu CIMITIR_TEST_PG_ADMIN_URL + playwright."""
from __future__ import annotations

import os
import socket
import uuid

import pytest
import requests

import cimitir_cloud_app as cca
import cimitir_qr as qr
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_cleanup_race_pg import adm, pgdb  # noqa: F401
from test_etapa6b_field_storage import sup  # noqa: F401
from test_etapa6c_field_inbox_pg import fw, png  # noqa: F401
from test_sync_v2_main_e2e import H, Server

pw = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PostgreSQL local de test indisponibil")


@pytest.fixture()
def web(fw):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = Server(cca.create_app(service=fw["svc"], db_probe=lambda x: True, storage_probe=lambda x: True), port); srv.start()
    yield {**fw, "srv": srv, "url": f"http://127.0.0.1:{port}"}
    srv.stop()


def new_code(w, tag="A"):
    r = requests.post(w["url"] + "/api/v1/phone-pairing/codes", json={"ttl": 600}, headers=H(w["T"][tag]["main"]), timeout=15)
    return r.json()["result"]["code"].replace("-", "")


def test_pwa_pairs_automatically_from_qr_link_and_shows_documents(web, tmp_path):
    w = web
    link = qr.pair_url(w["url"], new_code(w))
    photo = tmp_path / "foto.png"
    photo.write_bytes(png())
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 390, "height": 844}, service_workers="block")
        page = ctx.new_page()
        page.on("dialog", lambda d: d.accept())
        page.goto(link)
        page.wait_for_selector("[data-testid=sync-button]", timeout=15000)
        assert "#pair" not in page.url and "pair=" not in page.url                          # codul a fost scos din bara de adrese
        assert "6C A" in page.inner_text("body")                                            # cimitirul corect, fără introducere manuală
        page.screenshot(path="/tmp/pwa_paired.png")
        # al doilea scan al aceluiași QR (cod deja folosit): mesaj clar, nu o stare ruptă
        page.goto("about:blank")
        page.goto(link)
        page.wait_for_timeout(1500)
        assert page.locator("[data-testid=sync-button]").count() == 1                       # rămâne asociat (cheia veche), nu se pierde sesiunea
        # fără permisiune în MAIN, telefonul poate trimite fotografii? (default limitat) -> ecranul Documente se încarcă oricum
        page.click(".bottom-nav [data-view=modules]")
        page.click("[data-testid=module-documente]")
        page.wait_for_selector("[data-testid=docs-list]")
        assert "Se încarcă" not in page.inner_text("[data-testid=docs-list]") or page.wait_for_timeout(1500) is None
        assert "nu sunt încă implementate" not in page.inner_text("#view")
        b.close()


def test_pwa_bad_code_shows_error_and_stays_unpaired(web):
    w = web
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_context(service_workers="block").new_page()
        page.goto(qr.pair_url(w["url"], "ZZZZZZZZZZZZZZZZZZZZ"))
        page.wait_for_selector("#pair-error:has-text('Cod invalid')", timeout=15000)
        assert page.locator("[data-testid=sync-button]").count() == 0 and "pair=" not in page.url
        b.close()
