"""Etapa 10 — backup automat programat: verificare per copie, avertizare la eșec, retenție protejată, jurnal, rulare fără interfață (CLI),
interfața unificată „Copii de rezervă". Doar date fictive, în directoare temporare; nu atinge baza reală."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "shared", ROOT / "desktop"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import cimitir_backup as bk


def make_data(tmp_path: Path, settings: dict | None = None) -> Path:
    data = tmp_path / "data"
    (data / "media").mkdir(parents=True)
    (data / "media" / "poza.bin").write_bytes(b"fictiv-1234")
    conn = sqlite3.connect(data / "cimitir.db")
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE concesionari (id INTEGER PRIMARY KEY, nume TEXT)")
    conn.execute("INSERT INTO concesionari (nume) VALUES ('Fictiv')")
    for k, v in (settings or {}).items():
        conn.execute("INSERT INTO settings VALUES (?, ?)", (k, v))
    conn.commit()
    conn.close()
    return data


def getter(values: dict):
    return lambda key: values.get(key)


def fake_old(dest: Path, name: str, age_days: int, valid: bool = True) -> Path:
    """Copie fictivă veche (prefix + ștampilă în trecut) cu manifest minim."""
    stamp = (datetime.now() - timedelta(days=age_days)).strftime("%Y%m%d-%H%M%S")
    path = dest / f"cimitir-{stamp}"
    path.mkdir(parents=True)
    (path / "cimitir.db").write_bytes(b"x")
    manifest = {"cimitir.db": "0" * 64} if valid else {"cimitir.db": "0" * 64, "lipsa.bin": "0" * 64}
    (path / "manifest.json").write_text(json.dumps({"files": manifest}), encoding="utf-8")
    return path


def test_job_makes_verified_copy_and_logs_it(tmp_path):
    data, dest = make_data(tmp_path), tmp_path / "copii"
    res = bk.run_backup_job(data, getter({"backup_dir": str(dest)}), force=True, trigger="manual")
    assert res["result"] == "ok" and res["verified"] is True and Path(res["path"]).is_dir()
    assert bk.verify(Path(res["path"])) == []
    [entry] = bk.read_log(data)
    assert entry["result"] == "ok" and entry["trigger"] == "manual" and entry["location"] == res["path"] and entry["time"]
    assert bk.log_path(data).parent == tmp_path, "jurnalul este lângă folderul de date, nu în interiorul lui (nu intră în copii)"
    assert bk.health(data, getter({"backup_dir": str(dest)}))["level"] == "ok"


def test_not_due_and_disabled_do_nothing_and_do_not_log(tmp_path):
    data, dest = make_data(tmp_path), tmp_path / "copii"
    cfg = {"backup_dir": str(dest)}
    assert bk.run_backup_job(data, getter(cfg))["result"] == "ok"
    assert bk.run_backup_job(data, getter(cfg))["result"] == "skipped"
    assert bk.run_backup_job(data, getter({**cfg, "backup_enabled": "0"}), force=False)["result"] == "disabled"
    assert len(bk.read_log(data)) == 1 and len(bk.list_backups(dest)) == 1


def test_failure_is_logged_and_raises_a_visible_error_until_next_success(tmp_path):
    data, blocker = make_data(tmp_path), tmp_path / "fisier"
    blocker.write_text("nu e folder")
    bad = {"backup_dir": str(blocker / "sub")}
    res = bk.run_backup_job(data, getter(bad), force=True)
    assert res["result"] == "failed" and res["error"]
    state = bk.health(data, getter(bad))
    assert state["level"] == "error" and "EȘUAT" in state["message"]
    good = {"backup_dir": str(tmp_path / "bun")}
    assert bk.run_backup_job(data, getter(good), force=True)["result"] == "ok"
    assert bk.health(data, getter(good))["level"] == "ok"
    assert [e["result"] for e in bk.read_log(data)] == ["ok", "failed"]


def test_copy_that_fails_verification_is_quarantined_and_reported(tmp_path, monkeypatch):
    data, dest = make_data(tmp_path), tmp_path / "copii"
    monkeypatch.setattr(bk, "verify", lambda p: ["sumă de control greșită: cimitir.db"])
    res = bk.run_backup_job(data, getter({"backup_dir": str(dest)}), force=True)
    assert res["result"] == "failed" and "verificarea" in res["error"]
    assert bk.list_backups(dest) == [], "copia nevalidă nu apare în listă și nu poate fi restaurată"
    assert any(p.name.endswith(".invalid") for p in dest.iterdir())
    assert bk.health(data, getter({"backup_dir": str(dest)}))["level"] == "error"


def test_stale_backup_raises_a_warning_and_missing_one_too(tmp_path):
    data, dest = make_data(tmp_path), tmp_path / "copii"
    cfg = getter({"backup_dir": str(dest)})
    assert bk.health(data, cfg)["level"] == "warning"
    bk.run_backup_job(data, cfg, force=True)
    future = datetime.now() + timedelta(hours=60)
    assert bk.health(data, cfg, now=future)["level"] == "warning"
    assert bk.health(data, getter({"backup_dir": str(dest), "backup_enabled": "0"}))["level"] == "off"


def test_retention_never_goes_below_floor_and_keeps_protected_copies(tmp_path):
    data, dest = make_data(tmp_path), tmp_path / "copii"
    old = [fake_old(dest, f"v{i}", 100 + i) for i in range(6)]
    bk.set_protected(old[5], True)
    cfg = {"backup_dir": str(dest), "backup_keep_days": "1", "backup_keep_min": "1"}
    res = bk.run_backup_job(data, getter(cfg), force=True)
    names = {p.name for p in dest.iterdir() if p.is_dir()}
    assert res["result"] == "ok" and res["pruned"] == 3
    assert old[5].name in names, "copia protejată rămâne"
    assert len(bk.list_backups(dest)) == bk.MIN_KEEP_FLOOR + 1, "noua copie + pragul minim impus de MAIN (3) + cea protejată"
    assert bk.is_protected(old[5])


def test_retention_deletes_nothing_when_newest_copies_are_not_intact(tmp_path):
    dest = tmp_path / "copii"
    for i in range(5):
        fake_old(dest, f"v{i}", 200 + i)
    fake_old(dest, "defect", 0, valid=False)
    assert bk.prune(dest, keep_days=1, keep_min=3) == 0
    assert len(bk.list_backups(dest)) == 6


def test_retention_ignores_foreign_folders_and_files(tmp_path):
    dest = tmp_path / "copii"
    for i in range(5):
        fake_old(dest, f"v{i}", 300 + i)
    (dest / "documentele-mele").mkdir()
    (dest / "documentele-mele" / "important.txt").write_text("nu se șterge")
    (dest / "cimitir-nota.txt").write_text("nu se șterge")
    bk.prune(dest, keep_days=1, keep_min=3)
    assert (dest / "documentele-mele" / "important.txt").is_file() and (dest / "cimitir-nota.txt").is_file()


def test_lock_prevents_two_simultaneous_copies(tmp_path):
    data, dest = make_data(tmp_path), tmp_path / "copii"
    dest.mkdir()
    (dest / bk.LOCK_NAME).write_text("")
    assert bk.run_backup_job(data, getter({"backup_dir": str(dest)}), force=True)["result"] == "busy"
    old = datetime.now().timestamp() - bk.LOCK_STALE_SECONDS - 60
    os.utime(dest / bk.LOCK_NAME, (old, old))
    assert bk.run_backup_job(data, getter({"backup_dir": str(dest)}), force=True)["result"] == "ok"
    assert not (dest / bk.LOCK_NAME).exists()


def test_scheduled_job_never_restores_or_touches_source(tmp_path):
    data, dest = make_data(tmp_path), tmp_path / "copii"
    before = (data / "cimitir.db").read_bytes()
    bk.run_backup_job(data, getter({"backup_dir": str(dest)}), force=True)
    assert not bk.pending_marker(data).exists()
    assert sqlite3.connect(data / "cimitir.db").execute("SELECT nume FROM concesionari").fetchone()[0] == "Fictiv"
    assert (data / "media" / "poza.bin").read_bytes() == b"fictiv-1234" and before


def _cli(tmp_path, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_")}
    env["CIMITIR_ENV_FILE"] = str(tmp_path / "nu.env")
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "backup_scheduled.py"), "--data-dir", str(tmp_path / "data"), *args],
                          env=env, capture_output=True, text=True, timeout=120)


def test_headless_cli_uses_main_settings_and_signals_failure_by_exit_code(tmp_path):
    dest = tmp_path / "copii-cli"
    make_data(tmp_path, {"backup_dir": str(dest), "backup_keep_days": "30"})
    ok = _cli(tmp_path)
    assert ok.returncode == 0 and "OK" in ok.stdout, ok.stdout + ok.stderr
    assert len(bk.list_backups(dest)) == 1
    again = _cli(tmp_path)
    assert again.returncode == 0 and "skipped" in again.stdout and len(bk.list_backups(dest)) == 1
    status = _cli(tmp_path, "--status")
    assert status.returncode == 0 and status.stdout.startswith("[ok]")
    blocker = tmp_path / "fisier"
    blocker.write_text("x")
    fail = _cli(tmp_path, "--force", "--dest", str(blocker / "sub"))
    assert fail.returncode == 1 and "EȘEC" in fail.stdout
    assert _cli(tmp_path, "--status").returncode == 1
    entries = bk.read_log(tmp_path / "data")
    assert [e["result"] for e in entries] == ["failed", "ok"] and all(e["trigger"] == "windows-task" for e in entries)


QT_WORKER = r'''
import json, os, sys
sys.path.insert(0, {desktop!r})
os.chdir({desktop!r})
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QTableWidget, QLabel, QMainWindow
app = QApplication([])
import database as db, cimitir_backup, cimitir_config, backup_window
db.init_db()
out = {{}}
win = backup_window.BackupWindow()
win.txt_dir.setText({dest!r})
win.spin_days.setValue(7); win.spin_min.setValue(5); win.spin_interval.setValue(12)
win.findChild(QPushButton, "backup-save-settings").click()
out["settings"] = [db.get_setting(k) for k in ("backup_keep_days", "backup_keep_min", "backup_interval_hours", "backup_time", "backup_dir")]
win.findChild(QPushButton, "backup-now").click()
out["log_rows"] = win.findChild(QTableWidget, "backup-log-table").rowCount()
out["warning_level"] = win.lbl_warning.property("level")
win.findChild(QTableWidget, "backup-table").selectRow(0)
win.findChild(QPushButton, "backup-protect").click()
out["protected"] = cimitir_backup.is_protected(win._selected())
out["cmd"] = win.txt_command.toPlainText()
asked = []
QMessageBox.question = staticmethod(lambda *a, **k: (asked.append(a[2]) or QMessageBox.No))
win.findChild(QPushButton, "backup-restore").click()
out["asked"] = len(asked); out["marker_after_no"] = cimitir_backup.pending_marker(cimitir_config.working_dir()).exists()
# eșec -> avertizare vizibilă
blocker = os.path.join({tmp!r}, "blocker"); open(blocker, "w").write("x")
win.txt_dir.setText(os.path.join(blocker, "sub"))
win.findChild(QPushButton, "backup-now").click()
out["fail_status"] = win.lbl_status.text(); out["fail_level"] = win.lbl_warning.property("level"); out["fail_text"] = win.lbl_warning.text()
host = QMainWindow()
label = backup_window.attach_status_label(host)
out["label_visible"] = not label.isHidden(); out["label_text"] = label.text()
print("RESULT " + json.dumps(out))
'''


def test_backup_window_unified_schedule_log_warning_and_manual_restore(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_")}
    env.update(CIMITIR_DATA_DIR=str(tmp_path / "data"), CIMITIR_ENV_FILE=str(tmp_path / "nu.env"), CIMITIR_LOG_DIR=str(tmp_path / "logs"), QT_QPA_PLATFORM="offscreen")
    code = QT_WORKER.format(desktop=str(ROOT / "desktop"), dest=str(tmp_path / "copii"), tmp=str(tmp_path))
    proc = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(ROOT / "desktop"), capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2500:]
    out = json.loads([ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:])
    assert out["settings"][:4] == ["7", "5", "12", "02:00"] and out["settings"][4].endswith("copii")
    assert out["log_rows"] == 1 and out["warning_level"] == "ok"
    assert out["protected"] is True and "backup_scheduled.py" in out["cmd"]
    assert out["asked"] == 1 and out["marker_after_no"] is False, "restaurarea cere confirmare explicită; la «Nu» nu se programează nimic"
    assert "EȘUAT" in out["fail_status"] and out["fail_level"] == "error" and "EȘUAT" in out["fail_text"]
    assert out["label_visible"] is True and "EȘUAT" in out["label_text"]
