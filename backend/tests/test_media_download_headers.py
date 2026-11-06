"""Descărcarea documentelor (/api/sync/media/{id}) nu trebuie să poată fi păstrată în cache partajat/proxy. Fără PostgreSQL: dependențele sunt înlocuite."""
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "cimitir"))
sys.path.insert(0, str(ROOT / "shared"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import media_api  # noqa: E402


class _Storage:
    def open_stream(self, path):
        return io.BytesIO(b"%PDF-fictiv")


def test_download_is_no_store_and_nosniff(monkeypatch):
    monkeypatch.setattr(media_api, "_guard", lambda request: "sid")
    monkeypatch.setattr(media_api.media, "get_object", lambda mid: {"storage_path": "x", "mime": "application/pdf", "sha256": "0" * 64, "size": 11, "filename": "doc.pdf"})
    monkeypatch.setattr(media_api.storage_mod, "get_storage", lambda: _Storage())
    app = FastAPI()
    app.include_router(media_api.router)
    r = TestClient(app).get("/api/sync/media/abcdefgh12")
    assert r.status_code == 200 and r.content == b"%PDF-fictiv"
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"


def test_download_without_valid_session_is_refused():
    app = FastAPI()
    app.include_router(media_api.router)
    r = TestClient(app).get("/api/sync/media/abcdefgh12")
    assert r.status_code in (401, 403, 404, 501)      # 404 = serverul nu e în mod online (închis implicit)
