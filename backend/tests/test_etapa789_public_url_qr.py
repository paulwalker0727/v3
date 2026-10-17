"""QR/asociere: adresa publică în spatele Cloudflare/reverse proxy (CIMITIR_PUBLIC_URL, proxy declarat) și decodarea QR cu jsQR (fallback pentru browsere fără BarcodeDetector).
Camera reală NU este testată (vezi raportul)."""
from __future__ import annotations

import base64
import re

import pytest
from starlette.requests import Request

import cimitir_phone_pairing_http as pph
import cimitir_qr as qr

CODE = "ABCDE-FGHJK-LMNPQ-RSTUV"


def req(peer="198.51.100.7", headers=None, host="cloud.internal:8080"):
    hs = [(b"host", host.encode())] + [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": hs, "client": (peer, 5555), "scheme": "http", "server": (host.split(":")[0], 8080), "query_string": b""})


def test_public_url_env_wins_over_forwarded_headers(monkeypatch):
    monkeypatch.setenv("CIMITIR_PUBLIC_URL", "https://parohia.example.ro/")
    monkeypatch.setenv("CIMITIR_PAIRING_TRUSTED_PROXIES", "10.0.0.0/8")
    r = req("10.1.2.3", {"X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https"})
    assert pph.public_base_url(r) == "https://parohia.example.ro"
    assert qr.pair_url(pph.public_base_url(r), CODE) == f"https://parohia.example.ro/mobile/#pair={CODE}"


def test_forwarded_headers_only_from_declared_proxy(monkeypatch):
    monkeypatch.delenv("CIMITIR_PUBLIC_URL", raising=False)
    monkeypatch.setenv("CIMITIR_PAIRING_TRUSTED_PROXIES", "10.0.0.0/8")
    h = {"X-Forwarded-Host": "parohia.example.ro", "X-Forwarded-Proto": "https"}
    assert pph.public_base_url(req("10.1.2.3", h)) == "https://parohia.example.ro"
    assert pph.public_base_url(req("198.51.100.7", h)) == "http://cloud.internal:8080"          # client oarecare: antet ignorat
    assert pph.public_base_url(req("10.1.2.3", {"X-Forwarded-Host": "a/b", "X-Forwarded-Proto": "https"})) == "http://cloud.internal:8080"
    assert pph.public_base_url(req("10.1.2.3", {"X-Forwarded-Host": "x.ro", "X-Forwarded-Proto": "javascript"})) == "http://cloud.internal:8080"


def test_no_proxy_config_ignores_forwarded(monkeypatch):
    monkeypatch.delenv("CIMITIR_PUBLIC_URL", raising=False)
    monkeypatch.delenv("CIMITIR_PAIRING_TRUSTED_PROXIES", raising=False)
    assert pph.public_base_url(req("10.1.2.3", {"X-Forwarded-Host": "x.ro", "X-Forwarded-Proto": "https"})) == "http://cloud.internal:8080"


def test_jsqr_decodes_our_pairing_qr_in_chromium(tmp_path):
    pw = pytest.importorskip("playwright.sync_api")
    from pathlib import Path
    link = qr.pair_url("https://parohia.example.ro", CODE)
    png = qr.png_bytes(link, scale=8)
    js = (Path(__file__).resolve().parents[1] / "static" / "jsQR.js").read_text(encoding="utf-8")
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.set_content("<canvas id=c></canvas>")
        page.add_script_tag(content=js)
        txt = page.evaluate("""async (b64) => { const img = new Image(); img.src = 'data:image/png;base64,' + b64; await img.decode();
            const c = document.getElementById('c'); c.width = img.width + 40; c.height = img.height + 40; const x = c.getContext('2d'); x.fillStyle = '#fff'; x.fillRect(0,0,c.width,c.height);
            x.drawImage(img, 20, 20); const d = x.getImageData(0, 0, c.width, c.height); const r = jsQR(d.data, d.width, d.height); return r ? r.data : null; }""", base64.b64encode(png).decode())
        b.close()
    assert txt == link and re.search(r"pair=([A-Za-z0-9-]{10,40})", txt).group(1) == CODE
