"""ai_extract portabil: Emergent (dacă există) -> google-genai (GEMINI_API_KEY) -> indisponibil. Fără rețea, cu client Gemini mock."""
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
for p in (_BACKEND / "cimitir", _BACKEND, _BACKEND.parent / "shared"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import ai_extract  # noqa: E402

PAYLOAD = {"document_type": "buletin", "fields": {"nume": {"value": "Popescu", "confidence": 0.98}}, "raw_text": ""}


class FakeModels:
    def __init__(self, outcomes):
        self.calls, self.outcomes = [], list(outcomes)

    def generate_content(self, model, contents, config):
        self.calls.append(SimpleNamespace(model=model, contents=contents, config=config))
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(text=out)


def _fake_client(outcomes):
    return SimpleNamespace(models=FakeModels(outcomes))


@pytest.fixture()
def clean_env(monkeypatch):
    for k in ("EMERGENT_LLM_KEY", "GEMINI_API_KEY", "GEMINI_MODEL"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


@pytest.fixture()
def png(tmp_path):
    from PIL import Image
    f = tmp_path / "ci.png"
    Image.new("RGB", (40, 30), "white").save(f)
    return str(f)


@pytest.fixture()
def pdf(tmp_path):
    import fitz
    d = fitz.open()
    d.new_page().insert_text((72, 72), "test")
    f = tmp_path / "doc.pdf"
    d.save(str(f))
    d.close()
    return str(f)


def test_gemini_image_default_model(clean_env, png):
    clean_env.setenv("GEMINI_API_KEY", "AIza-test")
    client = _fake_client([json.dumps(PAYLOAD)])
    clean_env.setattr(ai_extract, "_gemini_client", lambda: client)
    assert ai_extract.engine_status() == {"available": True, "engine": "gemini", "model": "gemini-3.8-flash", "provider": "google-genai"}
    res = ai_extract.extract_document(png, ai_extract.DocumentType.BULETIN)
    assert res["fields"]["nume"]["value"] == "Popescu" and res["engine"] == "gemini"
    call = client.models.calls[0]
    assert call.model == "gemini-3.8-flash"
    assert call.config.system_instruction == ai_extract.BULETIN_PROMPT
    assert call.config.response_mime_type == "application/json"
    part = call.contents[0]
    assert part.inline_data.mime_type == "image/png" and part.inline_data.data


def test_gemini_model_from_env_and_fenced_json(clean_env, png):
    clean_env.setenv("GEMINI_API_KEY", "AIza-test")
    clean_env.setenv("GEMINI_MODEL", "gemini-3.7-flash")
    client = _fake_client(["```json\n" + json.dumps(PAYLOAD) + "\n```"])
    clean_env.setattr(ai_extract, "_gemini_client", lambda: client)
    assert ai_extract.engine_status()["model"] == "gemini-3.7-flash"
    res = ai_extract.extract_document(png, ai_extract.DocumentType.CERTIFICAT_DECES)
    assert res["fields"]["nume"]["value"] == "Popescu"
    assert client.models.calls[0].model == "gemini-3.7-flash"
    assert client.models.calls[0].config.system_instruction == ai_extract.CERTIFICAT_DECES_PROMPT


def test_gemini_pdf_sent_inline_then_image_fallback(clean_env, pdf):
    clean_env.setenv("GEMINI_API_KEY", "AIza-test")
    client = _fake_client([RuntimeError("pdf refuzat"), json.dumps(PAYLOAD)])
    clean_env.setattr(ai_extract, "_gemini_client", lambda: client)
    res = ai_extract.extract_document(pdf, ai_extract.DocumentType.ADEVERINTA_ADRESA)
    assert res["fields"]["nume"]["value"] == "Popescu"
    first, second = client.models.calls
    assert first.contents[0].inline_data.mime_type == "application/pdf"
    assert second.contents[0].inline_data.mime_type == "image/png"


def test_no_ai_key_unavailable_and_clear_error(clean_env, png):
    assert ai_extract.engine_status() == {"available": False, "engine": "none", "model": ""}
    with pytest.raises(RuntimeError, match="lipsește cheia AI"):
        ai_extract.extract_document(png, ai_extract.DocumentType.BULETIN)


def test_invalid_document_type(clean_env, png):
    with pytest.raises(ValueError):
        ai_extract.extract_document(png, "nu_exista")


def test_emergent_key_without_package_is_unavailable(clean_env, png):
    clean_env.setenv("EMERGENT_LLM_KEY", "sk-emergent-test")
    clean_env.setattr(ai_extract, "_emergent_importable", lambda: False)
    assert ai_extract.engine_status()["available"] is False
    with pytest.raises(RuntimeError, match="lipsește cheia AI"):
        ai_extract.extract_document(png, ai_extract.DocumentType.BULETIN)


def test_emergent_preferred_when_available(clean_env, png):
    clean_env.setenv("EMERGENT_LLM_KEY", "sk-emergent-test")
    clean_env.setenv("GEMINI_API_KEY", "AIza-test")
    clean_env.setattr(ai_extract, "_emergent_importable", lambda: True)

    async def fake_async(file_path, document_type, force_image=False):
        return dict(PAYLOAD)

    def boom():
        raise AssertionError("Gemini direct nu trebuie folosit când Emergent e disponibil")

    clean_env.setattr(ai_extract, "_extract_async", fake_async)
    clean_env.setattr(ai_extract, "_gemini_client", boom)
    st = ai_extract.engine_status()
    assert st["available"] and st["model"] == "gemini-3-flash-preview" and st["provider"] == "emergent"
    assert ai_extract.extract_document(png, ai_extract.DocumentType.BULETIN)["fields"]["nume"]["value"] == "Popescu"


def test_app_starts_and_admin_ok_without_ai_key(clean_env):
    from fastapi.testclient import TestClient
    import server
    c = TestClient(server.app, follow_redirects=True)
    assert c.get("/api/health").status_code == 200
    r = c.get("/admin")
    assert r.status_code == 200 and r.url.path.startswith("/admin")
