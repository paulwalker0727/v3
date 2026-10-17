"""Etapa 7/8 (MAIN, fără rețea): indicator conexiune/sincronizare + validarea fișierelor trimise + schimbarea adresei Cloud fără pierdere de date locale."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="cimitir_test_e789_")
os.environ["CIMITIR_DATA_DIR"] = _TMP
os.environ["CIMITIR_LOG_DIR"] = os.path.join(_TMP, "logs")
os.environ.setdefault("CIMITIR_DB_ENGINE", "sqlite")
os.environ.setdefault("CIMITIR_MODE", "local")
os.environ.setdefault("DATABASE_URL", "")
DESKTOP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DESKTOP))
sys.path.insert(0, str(DESKTOP.parent / "shared"))

import pytest  # noqa: E402

import cloud_simple as simple  # noqa: E402
import database as db  # noqa: E402
import sync_v2  # noqa: E402
import sync_v2_docs as docs  # noqa: E402

KEY = "cimitir.11111111-2222-3333-4444-555555555555." + "A" * 43


@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "cimitir.db"))
    db.init_db()
    return tmp_path


def _bind(monkeypatch, on=True):
    monkeypatch.setattr(sync_v2, "is_binding", lambda key=None: on)


def test_indicator_states(fresh, monkeypatch):
    _bind(monkeypatch, False)
    assert simple.sync_indicator()["state"] == simple.IND_NONE
    _bind(monkeypatch)
    db.set_setting(sync_v2.SET_LAST_REPORT, json.dumps({"ok": True, "state": "ok"}))
    assert simple.sync_indicator()["state"] == simple.IND_CONNECTED
    db.set_setting(sync_v2.SET_RUNNING, "1")
    assert simple.sync_indicator()["state"] == simple.IND_SYNCING
    db.set_setting(sync_v2.SET_RUNNING, "0")
    db.set_setting(sync_v2.SET_LAST_REPORT, json.dumps({"ok": False, "state": "offline"}))
    ind = simple.sync_indicator()
    assert ind["state"] == simple.IND_OFFLINE and "reconectare" in ind["text"]
    db.set_setting(sync_v2.SET_LAST_REPORT, json.dumps({"ok": False, "state": "partial"}))
    db.set_setting("cloud_sync_last_error", "Refuzat de server")
    ind = simple.sync_indicator()
    assert ind["state"] == simple.IND_ERROR and ind["detail"] == "Refuzat de server"


def test_inspect_file_validates_type_signature_size(tmp_path):
    ok = tmp_path / "a.png"; ok.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 20)
    info = docs.inspect_file(str(ok))
    assert info["mime"] == "image/png" and len(info["sha256"]) == 64
    for name, data, msg in (("b.png", b"MZ not an image", "conținutul"), ("c.exe", b"MZ", "format"), ("d.pdf", b"", "gol")):
        f = tmp_path / name; f.write_bytes(data)
        with pytest.raises(ValueError, match=msg):
            docs.inspect_file(str(f))
    with pytest.raises(ValueError, match="lipsește"):
        docs.inspect_file(str(tmp_path / "nu-exista.pdf"))


def test_scope_change_keeps_local_data_and_resets_only_cursors(fresh, monkeypatch):
    conn = db.get_connection()
    conn.execute("INSERT INTO locuri_de_veci (cod_loc, points) VALUES ('X-1','[]')")
    conn.commit(); conn.close()
    monkeypatch.setattr(sync_v2, "connection", lambda: ("https://a.example.ro", KEY, "registry"))
    sync_v2._reset_cursors_if_scope_changed()
    db.set_setting("sync_v2_docs_cursor", "42")
    monkeypatch.setattr(sync_v2, "connection", lambda: ("https://b.example.ro", KEY, "registry"))        # doar adresa Cloud s-a schimbat
    sync_v2._reset_cursors_if_scope_changed()
    assert db.get_setting("sync_v2_docs_cursor") == "0"
    conn = db.get_connection()
    assert conn.execute("SELECT count(*) FROM locuri_de_veci WHERE cod_loc='X-1'").fetchone()[0] == 1       # datele locale rămân
    conn.close()
