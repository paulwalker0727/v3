"""BUG-05c — cazuri-limită pentru carantina eșuată."""
import sqlite3
from pathlib import Path

import cimitir_backup as cb


def _data(tmp_path) -> Path:
    data = tmp_path / "Cimitir"
    data.mkdir()
    conn = sqlite3.connect(data / "cimitir.db")
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()
    return data


def _bad_verify(monkeypatch):
    monkeypatch.setattr(cb, "verify", lambda p: ["lipsește/modificat: cimitir.db"])


def test_error_message_does_not_claim_quarantine_when_rename_failed(tmp_path, monkeypatch):
    _bad_verify(monkeypatch)
    real = Path.rename
    monkeypatch.setattr(Path, "rename", lambda self, t: (_ for _ in ()).throw(PermissionError("blocat")) if str(t).endswith(".invalid") else real(self, t))
    res = cb.run_backup_job(_data(tmp_path), {"backup_dir": str(tmp_path / "copii")}.get, force=True, trigger="manual")
    assert res["result"] == "failed"
    assert "pusă în carantină" not in res["error"] and "nu mai apare în listă" in res["error"], res["error"]


def test_when_rename_and_manifest_removal_both_fail_copy_is_deleted(tmp_path, monkeypatch):
    _bad_verify(monkeypatch)
    dest = tmp_path / "copii"
    real_rename, real_unlink = Path.rename, Path.unlink
    monkeypatch.setattr(Path, "rename", lambda self, t: (_ for _ in ()).throw(PermissionError("blocat")) if str(t).endswith(".invalid") else real_rename(self, t))
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: (_ for _ in ()).throw(PermissionError("blocat")) if self.name == "manifest.json" else real_unlink(self, *a, **k))
    res = cb.run_backup_job(_data(tmp_path), {"backup_dir": str(dest)}.get, force=True, trigger="manual")
    assert res["result"] == "failed" and "ștearsă" in res["error"], res
    assert cb.list_backups(dest) == [] and not [p for p in dest.iterdir() if p.name.startswith("cimitir-")]


def test_normal_quarantine_still_renames_to_invalid(tmp_path, monkeypatch):
    _bad_verify(monkeypatch)
    dest = tmp_path / "copii"
    res = cb.run_backup_job(_data(tmp_path), {"backup_dir": str(dest)}.get, force=True, trigger="manual")
    assert "pusă în carantină" in res["error"]
    assert [p.name for p in dest.iterdir() if p.name.endswith(".invalid")] and cb.list_backups(dest) == []


def test_failed_quarantine_copy_is_not_counted_or_restorable(tmp_path, monkeypatch):
    data, dest = _data(tmp_path), tmp_path / "copii"
    good = cb.run_backup_job(data, {"backup_dir": str(dest)}.get, force=True, trigger="manual")
    assert good["result"] == "ok"
    real = Path.rename
    _bad_verify(monkeypatch)
    monkeypatch.setattr(Path, "rename", lambda self, t: (_ for _ in ()).throw(PermissionError("blocat")) if str(t).endswith(".invalid") else real(self, t))
    import time
    time.sleep(1.1)
    cb.run_backup_job(data, {"backup_dir": str(dest)}.get, force=True, trigger="manual")
    assert [r["name"] for r in cb.list_backups(dest)] == [Path(good["path"]).name]
