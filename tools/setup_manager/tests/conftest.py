"""Fixture comune. Testele cu PostgreSQL real rulează doar dacă SM_TEST_PG_URL (URL admin către un server LOCAL de test, ex. postgresql://postgres:pw@127.0.0.1:5544/postgres) e setat."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shared"))
MIG_DIR = ROOT / "backend" / "cimitir" / "pg_migrations"
PG_URL = os.environ.get("SM_TEST_PG_URL", "")


def _swap_db(url: str, name: str) -> str:
    base, _, _ = url.rpartition("/")
    return f"{base}/{name}"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SETUP_MANAGER_HOME", str(tmp_path / "home"))
    from tools.setup_manager import redact
    redact.clear()
    yield
    redact.clear()


@pytest.fixture(scope="session")
def pg_template():
    if not PG_URL:
        pytest.skip("SM_TEST_PG_URL nesetat (teste PostgreSQL real omise)")
    import psycopg
    host = psycopg.conninfo.conninfo_to_dict(PG_URL).get("host", "")
    if host not in ("127.0.0.1", "localhost", "/tmp/pg") and not host.startswith("/"):
        pytest.fail("SM_TEST_PG_URL trebuie să indice un server LOCAL")
    name = "sm_tpl_" + uuid.uuid4().hex[:8]
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    with psycopg.connect(_swap_db(PG_URL, name), autocommit=True) as c:
        for f in sorted(MIG_DIR.glob("0[0-2][0-9]_*.sql")):
            if int(f.name[:3]) <= 23:
                c.execute(f.read_text(encoding="utf-8"))
        c.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        for f in sorted(MIG_DIR.glob("0[0-2][0-9]_*.sql")):
            if int(f.name[:3]) <= 23:
                c.execute("INSERT INTO schema_migrations (version, name) VALUES (%s,%s)", (int(f.name[:3]), f.name))
    yield name
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}"')


@pytest.fixture()
def pg_db(pg_template):
    """Bază nouă = starea TEST descrisă: 001–023 aplicate și înregistrate; 024–026 lipsă."""
    import psycopg
    name = "sm_t_" + uuid.uuid4().hex[:8]
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}" TEMPLATE "{pg_template}"')
    url = _swap_db(PG_URL, name)
    yield url
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture()
def test_cfg():
    return {"env_name": "TEST", "project_ref": "fommbiwndlxkfsnlqatq", "supabase_url": "https://fommbiwndlxkfsnlqatq.supabase.co",
            "db_connection_string": "postgresql://postgres.fommbiwndlxkfsnlqatq@aws-0-eu-west-1.pooler.supabase.com:5432/postgres",
            "db_username": "postgres.fommbiwndlxkfsnlqatq", "db_password": "", "supabase_secret_key": "", "storage_bucket": "cimitir-private-test",
            "cloud_public_url": "http://127.0.0.1:8085", "container_name": "cimitir-cloud-sync-v2-main-test", "docker_port": 8085,
            "image_tag": "t1", "source_container": "", "backup_path": "", "repo_dir": "", "main_cloud_url": "http://127.0.0.1:8085"}


class FakeNet:
    """Rețea/DB/Docker simulate; răspunsurile se configurează per test."""

    def __init__(self, pg_url=None):
        self.pg_url, self.http_map, self.dns_fail, self.tcp_fail = pg_url, {}, set(), set()
        self.docker_calls, self.docker_map, self.http_calls = [], {}, []

    def resolve(self, host):
        if host in self.dns_fail:
            raise OSError("dns")
        return ["1.2.3.4"]

    def tcp(self, host, port, timeout=6):
        if (host, port) in self.tcp_fail:
            raise OSError("tcp")

    def http(self, method, url, headers=None, timeout=8, body=None):
        self.http_calls.append((method, url))
        for k, v in self.http_map.items():
            if url.endswith(k):
                if isinstance(v, Exception):
                    raise v
                return v
        return 404, b"{}"

    def pg(self, url):
        import psycopg
        return psycopg.connect(self.pg_url or url, connect_timeout=5)

    def docker(self, args, timeout=60):
        self.docker_calls.append(args)
        key = " ".join(args[:2])
        for k, v in self.docker_map.items():
            if key.startswith(k):
                return v
        return 1, "", "no docker"
