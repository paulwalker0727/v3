"""Etapa 6 — compatibilitate PWA Android/iPhone (verificări statice ale sursei livrate; testarea pe dispozitive reale rămâne manuală, vezi ETAPA5_9_RAPORT.md)."""
from __future__ import annotations

import json
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "mobile_app.html"


def _sw_shell():
    text = (STATIC / "mobile_sw.js").read_text(encoding="utf-8")
    return set(re.findall(r"'(/[^']+)'", text.split("const SHELL")[1].split("];")[0]))


def test_every_script_and_style_of_the_shell_page_is_precached_for_offline_use():
    html = TEMPLATE.read_text(encoding="utf-8")
    refs = set(re.findall(r'(?:src|href)="(/mobile-assets/[^"]+)"', html)) - {"/mobile-assets/mobile-icon-192.png"}
    assert refs and refs <= _sw_shell()
    app = (STATIC / "mobile_app.js").read_text(encoding="utf-8")
    for mod in re.findall(r"from '\./([a-z_]+\.js)'", app):
        assert f"/mobile-assets/{mod}" in _sw_shell(), mod
    assert "/mobile/" in _sw_shell() and "/mobile/manifest.webmanifest" in _sw_shell()


def test_ios_android_install_metadata_and_manifest():
    html = TEMPLATE.read_text(encoding="utf-8")
    for needle in ("viewport-fit=cover", "apple-mobile-web-app-capable", "apple-touch-icon", 'rel="manifest"'):
        assert needle in html
    man = json.loads((STATIC / "mobile_manifest.webmanifest").read_text(encoding="utf-8"))
    assert man["display"] == "standalone" and man["start_url"] == "/mobile/" and man["scope"] == "/mobile/"
    assert {i["sizes"] for i in man["icons"]} >= {"192x192", "512x512"}


def test_offline_queue_resumes_without_background_sync_and_storage_is_persistent():
    app = (STATIC / "mobile_app.js").read_text(encoding="utf-8")
    assert "SyncManager" not in app and "sync.register" not in app       # Safari/iOS nu au Background Sync
    assert "addEventListener('online'" in app and "visibilitychange" in app and "setInterval" in app
    assert "storage?.persist" in app                                      # reduce evacuarea IndexedDB (iOS/Android)
    assert "e.status===401" in app and "mirrorClear" in app              # acces revocat => date locale șterse, re-asociere cerută


def test_service_worker_never_caches_api_responses():
    sw = (STATIC / "mobile_sw.js").read_text(encoding="utf-8")
    assert "url.pathname.startsWith('/api/')) return" in sw and "event.request.method !== 'GET'" in sw
