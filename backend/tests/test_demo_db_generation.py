import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "make_demo_db.py"


def _run(data_dir, env_extra=None):
    import os
    env = {**os.environ, "CIMITIR_DB_ENGINE": "sqlite", "CIMITIR_MODE": "local", "DATABASE_URL": ""}
    env.pop("CIMITIR_DATA_DIR", None)
    env.pop("JWT_SECRET", None)
    return subprocess.run([sys.executable, str(SCRIPT), "--data-dir", str(data_dir), "--offline"],
                          capture_output=True, text=True, env=env, timeout=120)


def test_make_demo_db_creates_fresh_db_with_generated_users(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    r1, r2 = _run(a), _run(b)
    assert r1.returncode == 0, r1.stderr[-800:]
    assert r2.returncode == 0, r2.stderr[-800:]
    conn = sqlite3.connect(a / "cimitir.db")
    assert conn.execute("SELECT COUNT(*) FROM locuri_de_veci").fetchone()[0] > 0
    names = {r[0] for r in conn.execute("SELECT username FROM users")}
    assert {"admin_demo", "user_demo"} <= names
    assert conn.execute("SELECT COUNT(*) FROM settings WHERE key LIKE '%jwt%'").fetchone()[0] == 0
    pw = [l.split("/")[1].strip() for l in r1.stdout.splitlines() if l.strip().startswith("admin_demo")][0]
    pw2 = [l.split("/")[1].strip() for l in r2.stdout.splitlines() if l.strip().startswith("admin_demo")][0]
    assert pw != pw2 and len(pw) >= 16
    h1 = sqlite3.connect(a / "cimitir.db").execute("SELECT password_hash FROM users WHERE username='admin_demo'").fetchone()[0]
    h2 = sqlite3.connect(b / "cimitir.db").execute("SELECT password_hash FROM users WHERE username='admin_demo'").fetchone()[0]
    assert h1 != h2


def test_make_demo_db_refuses_existing_database(tmp_path):
    assert _run(tmp_path / "x").returncode == 0
    again = _run(tmp_path / "x")
    assert again.returncode != 0 and "REFUZAT" in (again.stderr + again.stdout)


def test_demo_db_not_tracked_in_git():
    out = subprocess.run(["git", "ls-files", "backend/data"], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == ""
