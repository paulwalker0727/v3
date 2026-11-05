"""BUG-02 — cazuri-limită: starea locală (secrete, licență, jurnale, catalog) rămâne după restaurare."""
import sqlite3
from pathlib import Path

import pytest

import cimitir_backup as cb
import cimitir_registry as reg


def _mk_db(path, value="copie"):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO settings VALUES ('marker', ?)", (value,))
    conn.commit()
    conn.close()


def _state(data):
    (data / "secrets").mkdir()
    (data / "secrets" / "legacy_sync_key.bin").write_bytes(b"\x00\x01cheie-fictiva\xff")
    (data / "secrets" / "profiles").mkdir()
    (data / "secrets" / "profiles" / "p1.bin").write_bytes(b"profil-fictiv")
    (data / "license.dat").write_bytes(b"licenta-fictiva")
    (data / ".jwt_secret").write_text("jwt-fictiv", encoding="utf-8")
    (data / "logs").mkdir()
    (data / "logs" / "cimitir.log").write_text("jurnal", encoding="utf-8")


def test_secrets_license_logs_are_byte_identical_and_safety_folder_stays_intact(tmp_path):
    data = tmp_path / "Cimitir"
    data.mkdir()
    _mk_db(data / "cimitir.db")
    _state(data)
    copy = cb.backup(data, tmp_path / "copii")
    cb.restore(copy, data, force=True)
    assert (data / "secrets" / "legacy_sync_key.bin").read_bytes() == b"\x00\x01cheie-fictiva\xff"
    assert (data / "secrets" / "profiles" / "p1.bin").read_bytes() == b"profil-fictiv"
    assert (data / "license.dat").read_bytes() == b"licenta-fictiva"
    assert (data / ".jwt_secret").read_text(encoding="utf-8") == "jwt-fictiv"
    safety = next(p for p in tmp_path.iterdir() if ".inainte-de-restaurare-" in p.name)
    assert (safety / "secrets" / "legacy_sync_key.bin").is_file() and (safety / "license.dat").is_file(), "folderul de siguranță rămâne intact"
    assert (safety / "logs" / "cimitir.log").is_file() and (safety / "cimitir.db").is_file()


def test_restore_works_when_optional_items_do_not_exist(tmp_path):
    data = tmp_path / "Cimitir"
    data.mkdir()
    _mk_db(data / "cimitir.db", "x")
    copy = cb.backup(data, tmp_path / "copii")
    cb.restore(copy, data, force=True)
    assert not (data / "secrets").exists() and not (data / "license.dat").exists()
    assert sqlite3.connect(data / "cimitir.db").execute("SELECT value FROM settings").fetchone()[0] == "x"


def test_restore_into_a_new_empty_folder_has_nothing_to_carry_over(tmp_path):
    src = tmp_path / "Sursa"
    src.mkdir()
    _mk_db(src / "cimitir.db")
    _state(src)
    copy = cb.backup(src, tmp_path / "copii")
    target = tmp_path / "Nou"
    cb.restore(copy, target)
    assert (target / "cimitir.db").is_file() and not (target / "secrets").exists() and not (target / "license.dat").exists()


def test_restored_database_content_comes_from_the_copy_not_the_old_state(tmp_path):
    data = tmp_path / "Cimitir"
    data.mkdir()
    _mk_db(data / "cimitir.db", "copie")
    _state(data)
    copy = cb.backup(data, tmp_path / "copii")
    conn = sqlite3.connect(data / "cimitir.db")
    conn.execute("UPDATE settings SET value='modificat'")
    conn.commit()
    conn.close()
    cb.restore(copy, data, force=True)
    assert sqlite3.connect(data / "cimitir.db").execute("SELECT value FROM settings WHERE key='marker'").fetchone()[0] == "copie"


def test_legacy_catalog_and_sidecars_are_kept(tmp_path):
    data = tmp_path / "Cimitir"
    data.mkdir()
    _mk_db(data / "cimitir.db")
    reg.set_legacy_mode(True, data)
    other = data / "registries" / "alt-registru"
    other.mkdir(parents=True)
    _mk_db(other / "database.sqlite", "alt")
    (data / "registry_catalog.sqlite-wal").write_bytes(b"wal-fictiv")
    copy = cb.backup(data, tmp_path / "copii")
    cb.restore(copy, data, force=True)
    assert reg.catalog_exists(data) and (data / "registry_catalog.sqlite-wal").read_bytes() == b"wal-fictiv"
    assert sqlite3.connect(other / "database.sqlite").execute("SELECT value FROM settings").fetchone()[0] == "alt"


def test_failed_restore_rolls_back_with_all_state_in_place(tmp_path, monkeypatch):
    data = tmp_path / "Cimitir"
    data.mkdir()
    _mk_db(data / "cimitir.db")
    _state(data)
    copy = cb.backup(data, tmp_path / "copii")
    monkeypatch.setattr(cb, "_rebase_media_paths", lambda *a, **k: (_ for _ in ()).throw(OSError("simulat")))
    with pytest.raises(OSError):
        cb.restore(copy, data, force=True)
    assert (data / "secrets" / "legacy_sync_key.bin").is_file() and (data / "license.dat").is_file() and (data / "logs" / "cimitir.log").is_file()
