"""BUG-03 — cazuri-limită: copie în interiorul folderului de date."""
import sqlite3
from pathlib import Path

import pytest

import cimitir_backup as cb


def _data(tmp_path, value="vechi") -> Path:
    data = tmp_path / "Cimitir"
    data.mkdir()
    conn = sqlite3.connect(data / "cimitir.db")
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO settings VALUES ('marker', ?)", (value,))
    conn.commit()
    conn.close()
    return data


def _marker(data):
    conn = sqlite3.connect(data / "cimitir.db")
    try:
        return conn.execute("SELECT value FROM settings WHERE key='marker'").fetchone()[0]
    finally:
        conn.close()


def _change(data, value):
    conn = sqlite3.connect(data / "cimitir.db")
    conn.execute("UPDATE settings SET value=? WHERE key='marker'", (value,))
    conn.commit()
    conn.close()


def test_restore_state_and_backups_stay_listed_afterwards(tmp_path):
    data = _data(tmp_path)
    dest = data / "copii"
    copy = cb.backup(data, dest)
    _change(data, "modificat")
    cb.restore(copy, data, force=True)
    assert _marker(data) == "vechi"
    assert [r["name"] for r in cb.list_backups(dest)] == [copy.name], "copiile rămân în folderul lor din folderul de date"
    assert cb.verify(copy) == []
    safeties = [p for p in tmp_path.iterdir() if ".inainte-de-restaurare-" in p.name]
    assert len(safeties) == 1 and (safeties[0] / "cimitir.db").is_file(), "starea anterioară este păstrată"


def test_backup_directly_inside_data_dir_without_subfolder(tmp_path):
    data = _data(tmp_path)
    copy = cb.backup(data, data)
    assert copy.parent == data.resolve()
    _change(data, "modificat")
    cb.restore(copy, data, force=True)
    assert _marker(data) == "vechi" and cb.list_backups(data) and cb.verify(next(iter(data.glob("cimitir-*")))) == []


def test_failed_restore_with_inside_copy_rolls_back_without_losing_copies(tmp_path, monkeypatch):
    data = _data(tmp_path)
    copy = cb.backup(data, data / "copii")
    _change(data, "modificat")
    monkeypatch.setattr(cb, "_rebase_media_paths", lambda *a, **k: (_ for _ in ()).throw(OSError("simulat")))
    with pytest.raises(OSError):
        cb.restore(copy, data, force=True)
    assert _marker(data) == "modificat", "starea anterioară revine intactă"
    assert cb.verify(copy) == [] and [r["name"] for r in cb.list_backups(data / "copii")] == [copy.name]
    assert not [p for p in tmp_path.iterdir() if ".inainte-de-restaurare-" in p.name]


def test_copy_outside_data_dir_behaves_as_before(tmp_path):
    data = _data(tmp_path)
    copy = cb.backup(data, tmp_path / "extern")
    _change(data, "modificat")
    cb.restore(copy, data, force=True)
    assert _marker(data) == "vechi" and not (data / "extern").exists()
    assert [r["name"] for r in cb.list_backups(tmp_path / "extern")] == [copy.name]


def test_pending_restore_flow_from_inside_copy(tmp_path):
    data = _data(tmp_path)
    copy = cb.backup(data, data / "copii")
    _change(data, "modificat")
    cb.schedule_restore(copy, data)
    msg = cb.apply_pending_restore(data)
    assert msg and "restaurată" in msg and _marker(data) == "vechi"
    assert not cb.pending_marker(data).exists()
    assert cb.list_backups(data / "copii")
