"""Etapa 10 — pregătire Windows: intrările build-ului sunt complete (verificare statică) și `launcher.py --backup-run` (exact calea din Cimitir.exe) funcționează
fără interfață pe date fictive. NU validează un executabil Windows."""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_build_inputs_are_complete():
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "windows" / "verify_build_inputs.py")], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout[-3000:]
    assert "0 FAIL" in proc.stdout


def test_powershell_scripts_are_utf8_with_bom_and_have_no_secrets():
    for name in ("Register-CimitirBackupTask.ps1", "Unregister-CimitirBackupTask.ps1", "Verify-CimitirBuild.ps1"):
        raw = (ROOT / "scripts" / "windows" / name).read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf"), f"{name}: fără BOM, diacriticele se strică în Windows PowerShell 5.1"
        text = raw.decode("utf-8-sig").lower()
        assert "password" not in text and "token" not in text


def test_launcher_backup_run_works_headless_on_temp_data(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    conn = sqlite3.connect(data / "cimitir.db")
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE concesionari (id INTEGER PRIMARY KEY, nume TEXT)")
    conn.commit()
    conn.close()
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_")}
    env.update(CIMITIR_ENV_FILE=str(tmp_path / "nu.env"), QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, str(ROOT / "desktop" / "launcher.py"), "--backup-run", "--force", "--data-dir", str(data)],
                          env=env, capture_output=True, text=True, timeout=120, cwd=str(ROOT / "desktop"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    backups = [p for p in (tmp_path / "data-backups").iterdir() if p.name.startswith("cimitir-")]
    assert len(backups) == 1 and (backups[0] / "manifest.json").is_file()
    assert (tmp_path / "data.backup-log.jsonl").is_file()
