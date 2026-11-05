"""BUG-05a — cazuri-limită pentru lock rămas / copie în curs."""
import os
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


def _locked(tmp_path):
    data, dest = _data(tmp_path), tmp_path / "copii"
    dest.mkdir()
    (dest / cb.LOCK_NAME).write_text("")
    return data, dest, {"backup_dir": str(dest)}.get


def test_scheduled_busy_is_logged_and_cli_exits_2(tmp_path, capsys, monkeypatch):
    data, dest, _ = _locked(tmp_path)
    monkeypatch.setenv("CIMITIR_DATA_DIR", str(data))
    assert cb.cli(["--data-dir", str(data), "--dest", str(dest)]) == 2
    assert "OCUPAT" in capsys.readouterr().out
    [entry] = cb.read_log(data)
    assert entry["result"] == "busy" and entry["trigger"] == "windows-task" and entry["error"]


def test_busy_raises_visible_warning_in_health(tmp_path):
    data, dest, get = _locked(tmp_path)
    cb.run_backup_job(data, get, force=True, trigger="windows-task")
    state = cb.health(data, get)
    assert state["level"] == "warning" and "NU a făcut copie" in state["message"], state


def test_in_app_busy_check_does_not_fill_the_log(tmp_path):
    data, _, get = _locked(tmp_path)
    assert cb.run_backup_job(data, get, trigger="app")["result"] == "busy"
    assert cb.read_log(data) == []


def test_busy_warning_clears_after_successful_copy(tmp_path):
    data, dest, get = _locked(tmp_path)
    cb.run_backup_job(data, get, force=True, trigger="windows-task")
    old = os.path.getmtime(dest / cb.LOCK_NAME) - cb.LOCK_STALE_SECONDS - 60
    os.utime(dest / cb.LOCK_NAME, (old, old))
    assert cb.run_backup_job(data, get, force=True, trigger="windows-task")["result"] == "ok"
    assert cb.health(data, get)["level"] == "ok"


def test_cli_success_and_failure_codes_unchanged(tmp_path, monkeypatch):
    data = _data(tmp_path)
    monkeypatch.setenv("CIMITIR_DATA_DIR", str(data))
    assert cb.cli(["--data-dir", str(data), "--dest", str(tmp_path / "ok"), "--force"]) == 0
    blocker = tmp_path / "fisier"
    blocker.write_text("nu e folder")
    assert cb.cli(["--data-dir", str(data), "--dest", str(blocker / "sub"), "--force"]) == 1
