"""Audit final — `Cimitir.exe --backup-run` / Planificator: ce se poate verifica pe Linux (cod Python identic: scripts/backup_scheduled.py -> cimitir_backup.cli) și analiză STATICĂ a scripturilor PowerShell.
NIMIC de aici nu dovedește comportamentul pe Windows (Task Scheduler, .exe cu console=False, DPAPI, antivirus). Testul real Windows este SKIP pe Linux, nu PASS."""
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))
import cimitir_backup as cb  # noqa: E402

SCRIPT = ROOT / "scripts" / "backup_scheduled.py"
PS1 = ROOT / "scripts" / "windows" / "Register-CimitirBackupTask.ps1"


def make_data(base: Path) -> Path:
    d = base / "data"
    d.mkdir(parents=True)
    c = sqlite3.connect(d / "cimitir.db")
    c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("INSERT INTO settings VALUES ('backup_interval_hours','1')")
    c.commit()
    c.close()
    return d


def run_cli(data: Path, *extra: str, env_extra=None):
    env = {**os.environ, "CIMITIR_IGNORE_PROJECT_ENV": "1", "PYTHONPATH": str(ROOT / "shared")}
    env.pop("CIMITIR_BACKUP_DIR", None)
    env.pop("CIMITIR_DATA_DIR", None)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(SCRIPT), "--data-dir", str(data), *extra], capture_output=True, text=True, env=env, timeout=120)


def last_log(data: Path):
    rows = cb.read_log(data, 5)
    return rows[0] if rows else None


def test_exit_0_and_verified_copy_and_log_on_success(tmp_path):
    data = make_data(tmp_path)
    p = run_cli(data, "--force")
    assert p.returncode == 0, p.stdout + p.stderr
    e = last_log(data)
    assert e["result"] == "ok" and e["trigger"] == "windows-task" and e["verified"] is True
    assert cb.verify(Path(e["path"])) == []


def test_exit_1_logged_and_status_reports_error_when_database_missing(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    p = run_cli(data, "--force")
    assert p.returncode == 1 and last_log(data)["result"] == "failed"
    st = run_cli(data, "--status")
    assert st.returncode == 1 and st.stdout.startswith("[error]")


def test_exit_1_when_destination_cannot_be_created(tmp_path):
    data = make_data(tmp_path)
    blocker = tmp_path / "fisier"
    blocker.write_text("x")
    p = run_cli(data, "--force", "--dest", str(blocker / "copii"))
    assert p.returncode == 1 and last_log(data)["result"] == "failed"
    assert cb.health(data, lambda k: None)["level"] == "error"


def test_exit_2_busy_lock_is_logged_and_health_warns(tmp_path):
    data = make_data(tmp_path)
    dest = tmp_path / "copii"
    dest.mkdir()
    (dest / cb.LOCK_NAME).write_text("")
    p = run_cli(data, "--force", "--dest", str(dest))
    assert p.returncode == 2 and last_log(data)["result"] == "busy"
    assert cb.health(data, lambda k: None)["level"] == "warning"
    assert cb.list_backups(dest) == []


def test_stale_lock_older_than_2h_is_recovered(tmp_path):
    data = make_data(tmp_path)
    dest = tmp_path / "copii"
    dest.mkdir()
    lock = dest / cb.LOCK_NAME
    lock.write_text("")
    old = time.time() - cb.LOCK_STALE_SECONDS - 60
    os.utime(lock, (old, old))
    assert run_cli(data, "--force", "--dest", str(dest)).returncode == 0 and len(cb.list_backups(dest)) == 1


def test_backup_runs_while_main_holds_the_database_open_and_writes(tmp_path):
    """„Execuție fără MAIN deschis" = fără interfață; cazul opus (MAIN deschis și scriind) nu trebuie să producă copie coruptă."""
    data = make_data(tmp_path)
    conn = sqlite3.connect(data / "cimitir.db", timeout=30, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    stop = threading.Event()

    def writer():
        i = 0
        while not stop.is_set():
            conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (f"k{i % 50}", "v" * 200))
            conn.commit()
            i += 1
    t = threading.Thread(target=writer)
    t.start()
    try:
        results = [run_cli(data, "--force").returncode for _ in range(3)]
    finally:
        stop.set()
        t.join()
        conn.close()
    assert results == [0, 0, 0]
    assert all(cb.verify(Path(r["path"])) == [] for r in cb.list_backups(cb.default_backup_dir(data)))


@pytest.mark.xfail(strict=True, reason="NOU: dacă folderul de date din `--data-dir` nu poate fi creat/deschis (ex. disc D: lipsă), cli() ridică excepție ÎNAINTE de run_backup_job: cod 1 în Task Scheduler, dar NICIUN rând în jurnal și nicio avertizare în MAIN (exe cu console=False nu arată traceback-ul).")
def test_target_failure_before_the_job_still_leaves_a_log_entry(tmp_path):
    notdir = tmp_path / "fisier.txt"
    notdir.write_text("x")
    p = run_cli(notdir, "--force")
    assert p.returncode == 1
    assert cb.read_log(notdir, 5), f"niciun rând în jurnal pentru eșecul din faza de inițializare; stderr={p.stderr[-200:]}"


# ------------------------------------------------------------------ analiză statică PowerShell / launcher (NU execută nimic Windows)
def test_ps1_has_utf8_bom_for_windows_powershell_5_1():
    assert PS1.read_bytes()[:3] == b"\xef\xbb\xbf"


def test_ps1_task_execution_time_limit_matches_stale_lock_window():
    src = PS1.read_text(encoding="utf-8-sig")
    assert "-ExecutionTimeLimit (New-TimeSpan -Hours 2)" in src and cb.LOCK_STALE_SECONDS == 2 * 3600
    assert "-StartWhenAvailable" in src and "-MultipleInstances IgnoreNew" in src


def test_characterize_ps1_registers_without_principal_so_it_runs_only_when_user_is_logged_on():
    src = PS1.read_text(encoding="utf-8-sig")
    assert not re.search(r"-Principal|-LogonType|-RunLevel|-User\b", src), "scriptul a primit un Principal (S-8 remediat?)"


def test_characterize_ps1_has_no_failure_notification_channel():
    src = PS1.read_text(encoding="utf-8-sig")
    assert not re.search(r"BurntToast|msg\.exe|Send-MailMessage|New-EventLog|Write-EventLog|eventcreate|wevtutil|ToastNotification", src, re.I), "există un canal de notificare"


def test_characterize_ps1_does_not_normalize_trailing_backslash_in_datadir():
    src = PS1.read_text(encoding="utf-8-sig")
    assert "TrimEnd" not in src


def test_launcher_handles_backup_run_before_any_qt_or_gui_import():
    src = (ROOT / "desktop" / "launcher.py").read_text(encoding="utf-8")
    head = src.split('if "--backup-run" in sys.argv:')[0]
    assert "PySide6" not in head and "QApplication" not in head
    block = src.split('if "--backup-run" in sys.argv:')[1].split("if \"--portal\"")[0]
    assert "raise SystemExit(cimitir_backup.cli(" in block


def test_spec_builds_windowed_exe_so_cli_output_is_invisible():
    assert "console=False" in (ROOT / "desktop" / "cimitir.spec").read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform != "win32", reason="NEVERIFICAT pe Windows: Task Scheduler / Cimitir.exe --backup-run / Start-ScheduledTask nu pot rula pe Linux")
def test_windows_real_task_scheduler_run():
    pytest.fail("de executat manual pe Windows (vezi AUDIT_FINAL_INTEGRARE.md §Windows)")
