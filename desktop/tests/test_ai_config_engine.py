"""Teste pentru ecranul Setări AI: config persistent + motor Google Gemini."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def isolated_config(tmp_path, monkeypatch):
    """Redirecționează configul într-un folder temporar."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    # reimport modulul ca să recalculeze CONFIG_DIR pe tmp
    for m in list(sys.modules):
        if m.startswith("ai_scanare.config"):
            del sys.modules[m]
    from ai_scanare import config as c
    return c


def test_config_defaults_and_roundtrip(isolated_config):
    c = isolated_config
    assert c.load_config()["mode"] == c.MODE_AI
    assert c.is_ai_enabled() is False
    c.save_config({"api_key": "AQ.abcdef123456", "mode": "ai", "model": "auto"})
    assert c.get_api_key() == "AQ.abcdef123456"
    assert c.is_ai_enabled() is True
    assert c.status_text().startswith("AI activ")


def test_config_clear_key_goes_offline(isolated_config):
    c = isolated_config
    c.save_config({"api_key": "AIzaXYZ", "mode": "ai", "model": "gemini-flash-latest"})
    assert "gemini-flash-latest" in c.status_text()
    c.clear_key()
    assert c.get_api_key() == ""
    assert c.get_mode() == c.MODE_OFFLINE
    assert c.is_ai_enabled() is False


def test_mask_key(isolated_config):
    c = isolated_config
    assert c.mask_key("AQ.abcdef123456") == "AQ.****3456"
    assert c.mask_key("") == "(nicio cheie)"
    assert c.mask_key("short") == "sh****"


def test_key_shape_validation():
    from ai_scanare import google_engine as ge
    assert ge.validate_key_shape(" AQ.x ") == "AQ.x"
    assert ge.validate_key_shape("AIzaXXX") == "AIzaXXX"
    with pytest.raises(ge.GeminiError):
        ge.validate_key_shape("nope")
    with pytest.raises(ge.GeminiError):
        ge.validate_key_shape("")


def test_model_id_and_ordering():
    from ai_scanare import google_engine as ge
    assert ge.model_id("models/gemini-flash-latest") == "gemini-flash-latest"
    visible = ["gemini-2.5-flash-lite", "gemini-flash-latest", "embedding-001"]
    ordered = ge._ordered_candidates(visible)
    # preferatul cel mai de sus vizibil e primul
    assert ordered[0] == "gemini-flash-latest"


class _FakeModel:
    def __init__(self, name, gen=True):
        self.name = name
        self.supported_actions = ["generateContent"] if gen else []


class _FakeModels:
    def list(self):
        return [
            _FakeModel("models/gemini-flash-latest"),
            _FakeModel("models/gemini-2.5-flash-lite"),
            _FakeModel("models/embedding-001", gen=False),
        ]

    def generate_content(self, model=None, contents=None, config=None):
        class R:
            text = ('{"document_type":"buletin","fields":{"nume":{"value":"POP","confidence":0.9}}}'
                    if isinstance(contents, list) else "OK")
        return R()


class _FakeFiles:
    def upload(self, file=None, config=None):
        class F:
            name = "files/abc"
        return F()

    def delete(self, name=None):
        pass


class _FakeClient:
    def __init__(self):
        self.models = _FakeModels()
        self.files = _FakeFiles()


def test_choose_working_model_auto_and_prefer(monkeypatch):
    from ai_scanare import google_engine as ge
    fc = _FakeClient()
    assert ge.choose_working_model(fc) == "gemini-flash-latest"
    assert ge.choose_working_model(fc, prefer="gemini-2.5-flash-lite") == "gemini-2.5-flash-lite"


def test_extract_document_with_fake_client(tmp_path, monkeypatch):
    from PIL import Image
    img = tmp_path / "doc.png"
    Image.new("RGB", (8, 8), "white").save(img)
    from ai_scanare import google_engine as ge
    monkeypatch.setattr(ge, "_client", lambda k: _FakeClient())
    res = ge.extract_document("AQ.key", str(img), "buletin")
    assert res["engine"] == "gemini"
    assert res["model"] == "gemini-flash-latest"
    assert res["fields"]["nume"]["value"] == "POP"


def test_extract_document_rejects_unsupported(tmp_path, monkeypatch):
    from ai_scanare import google_engine as ge
    monkeypatch.setattr(ge, "_client", lambda k: _FakeClient())
    bad = tmp_path / "x.txt"
    bad.write_text("hi")
    with pytest.raises(ge.GeminiError):
        ge.extract_document("AQ.key", str(bad), "buletin")


def test_ai_client_routing(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for m in list(sys.modules):
        if m.startswith("ai_scanare"):
            del sys.modules[m]
    from ai_scanare import config as c, ai_client, google_engine, offline_ocr
    monkeypatch.setattr(google_engine, "extract_document",
                        lambda k, fp, dt, model=None: {"engine": "gemini", "fields": {}})
    monkeypatch.setattr(offline_ocr, "extract_document_offline",
                        lambda fp, dt: {"engine": "tesseract", "fields": {}})
    monkeypatch.setattr(offline_ocr, "tesseract_available", lambda: True)

    c.save_config({"api_key": "AQ.key12345678", "mode": "ai", "model": "auto"})
    assert ai_client.extract_document_sync("/x.png", "buletin")["engine"] == "gemini"
    assert ai_client.active_engine_status()["engine"] == "google"

    c.set_mode("offline")
    assert ai_client.extract_document_sync("/x.png", "buletin")["engine"] == "tesseract"

    c.set_mode("ai")
    monkeypatch.setattr(google_engine, "extract_document",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("net")))
    r = ai_client.extract_document_sync("/x.png", "buletin")
    assert r["engine"] == "tesseract"
    assert "_gemini_error" in r


# ============ CRIPTARE CHEIE PE DISC ============

def test_key_encrypted_on_disk(isolated_config):
    c = isolated_config
    c.save_config({"api_key": "AQ.secret-key-123", "mode": "ai", "model": "auto"})
    raw = c.CONFIG_FILE.read_text(encoding="utf-8")
    assert "AQ.secret-key-123" not in raw
    assert '"api_key": ""' in raw
    assert "enc:v1:" in raw or "b64:v1:" in raw
    assert c.get_api_key() == "AQ.secret-key-123"
    assert c.is_ai_enabled() is True


def test_encrypt_decrypt_roundtrip(isolated_config):
    c = isolated_config
    token = c.encrypt_key("AIzaTestKey42")
    assert token.startswith(("enc:v1:", "b64:v1:"))
    assert c.decrypt_key(token) == "AIzaTestKey42"
    assert c.encrypt_key("") == ""
    assert c.decrypt_key("") == ""


def test_legacy_plaintext_migrated_to_encrypted(isolated_config):
    c = isolated_config
    import json
    c.CONFIG_FILE.write_text(
        json.dumps({"api_key": "AIzaLegacyKey", "mode": "ai", "model": "auto"}),
        encoding="utf-8",
    )
    assert c.get_api_key() == "AIzaLegacyKey"
    raw = c.CONFIG_FILE.read_text(encoding="utf-8")
    assert "AIzaLegacyKey" not in raw
    assert "api_key_enc" in raw
    assert c.get_api_key() == "AIzaLegacyKey"


def test_corrupt_encrypted_key_returns_empty(isolated_config, monkeypatch):
    c = isolated_config
    for env in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_AI_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    import json
    c.CONFIG_FILE.write_text(
        json.dumps({"api_key": "", "api_key_enc": "enc:v1:corupt", "mode": "ai"}),
        encoding="utf-8",
    )
    assert c.get_api_key() == ""
    assert c.is_ai_enabled() is False
