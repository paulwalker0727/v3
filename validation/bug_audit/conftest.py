"""Audit Etapa 10 — infrastructură comună pentru testele de reproducere (doar local, date fictive).

- Clienți MAIN: procese separate, fiecare cu propriul CIMITIR_DATA_DIR temporar (SQLite), ca în test_etapa6_entity_sync.
- Server clasic: uvicorn real pe un port liber, pe o bază PostgreSQL TEMPORARĂ creată și ștearsă de test.
  Necesită BUGAUDIT_PG_ADMIN_URL=postgresql://<superuser>@127.0.0.1:<port>/postgres (altfel testele PG sunt SKIP).
  NU atinge Supabase, Docker-ul existent sau vreo bază reală.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "shared", ROOT / "desktop"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

PY = sys.executable
PG_ADMIN = os.environ.get("BUGAUDIT_PG_ADMIN_URL", "").strip()
TEST_SECRET_ENV = {"CIMITIR_SECRET_STORE": "test-encrypted", "CIMITIR_ALLOW_TEST_SECRETSTORE": "1",
                   "CIMITIR_TEST_SECRET_KEY": "rAR-xhkir67T-0hgHU_d6gxHdQt7zqO9zPP-nl-r7yw="}

needs_pg = pytest.mark.skipif(not PG_ADMIN, reason="BUGAUDIT_PG_ADMIN_URL nesetat (PostgreSQL LOCAL de test)")


def main_env(data_dir: Path) -> dict:
    env = {**os.environ, **TEST_SECRET_ENV, "CIMITIR_DATA_DIR": str(data_dir), "CIMITIR_MODE": "local",
           "CIMITIR_DB_ENGINE": "sqlite", "PYTHONPATH": str(ROOT / "shared"), "QT_QPA_PLATFORM": "offscreen"}
    for k in ("DATABASE_URL", "CIMITIR_BACKUP_DIR", "CIMITIR_REGISTRY_UID"):
        env.pop(k, None)
    return env


def run_main(data_dir: Path, code: str, url: str = "", key: str = "", configure: bool = True) -> dict:
    """Rulează `code` într-un proces MAIN izolat. `out` (dict) este întors ca JSON."""
    head = textwrap.dedent(f"""
        import json, sys
        sys.path.insert(0, {str(ROOT / 'desktop')!r})
        import database as db, cloud_sync, entity_sync
        db.init_db()
        if {configure!r} and {url!r}:
            cloud_sync.save_config({url!r}, {key!r}, False, False)
        out = {{}}
    """)
    script = head + textwrap.dedent(code) + "\nprint('RESULT=' + json.dumps(out, default=str))\n"
    p = subprocess.run([PY, "-c", script], capture_output=True, text=True, env=main_env(data_dir), timeout=240,
                       cwd=ROOT / "desktop")
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-3000:]
    line = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT=")][-1]
    return json.loads(line[len("RESULT="):])


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def classic_server():
    """Server clasic (backend/server.py) pe PostgreSQL temporar propriu. Întoarce url, key, dsn."""
    if not PG_ADMIN:
        pytest.skip("BUGAUDIT_PG_ADMIN_URL nesetat")
    import psycopg
    name = "bugaudit_" + uuid.uuid4().hex[:8]
    with psycopg.connect(PG_ADMIN, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    from psycopg.conninfo import conninfo_to_dict
    info = conninfo_to_dict(PG_ADMIN)              # URL de server (TCP sau socket unix: ?host=/tmp)
    dsn = (f"postgresql://{info.get('user', 'postgres')}@/{name}?host={info.get('host', '127.0.0.1')}"
           f"&port={info.get('port', 5432)}")
    data = Path(tempfile.mkdtemp(prefix="ba-srv-"))
    env = {**os.environ, "CIMITIR_MODE": "online", "CIMITIR_DB_ENGINE": "postgres", "DATABASE_URL": dsn,
           "CIMITIR_DATA_DIR": str(data), "JWT_SECRET": uuid.uuid4().hex, "CIMITIR_ALLOW_TENANT_MIGRATIONS": "1",
           "CIMITIR_ALLOW_DEFAULT_USERS": "1", "PYTHONPATH": ""}
    out = subprocess.run([PY, str(ROOT / "scripts" / "issue_sync_key.py"), "--source", "cimitir"], cwd=ROOT / "backend",
                         capture_output=True, text=True, timeout=180, env=env)
    keys = [ln.strip() for ln in out.stdout.splitlines() if ln.startswith("cimitir.")]
    assert keys, out.stdout[-1500:] + out.stderr[-1500:]
    port = _free_port()
    proc = subprocess.Popen([PY, "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=ROOT / "backend", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    import requests
    for _ in range(90):
        try:
            if requests.get(url + "/api/public/status", timeout=2).json()["database"]["engine"] == "postgres":
                break
        except Exception:
            time.sleep(1)
    else:
        proc.kill()
        pytest.fail("serverul de test nu a pornit")
    try:
        yield {"url": url, "key": keys[-1], "dsn": dsn}
    finally:
        proc.terminate()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()
        with psycopg.connect(PG_ADMIN, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def pg_rows(dsn: str, sql: str, args=()):
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as c:
        return c.execute(sql, args).fetchall()


def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
