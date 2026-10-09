# ruff: noqa: F811,F401
"""Pasul 6A — REVALIDARE: suitele existente Pas 4 / 5 / 5.1 (Flux A/B, HTTP, plan guard server-side) rulate PESTE PostgreSQL local, cu logica nemodificată.
Doar `renv`/`env` (planul de control) și helperul SQL `q` sunt înlocuite; aserțiunile pe schema SQLite a simulatorului sunt traduse minimal (tabele cp_*/canonice).
Rulează doar cu CIMITIR_TEST_PG_ADMIN_URL (PostgreSQL local)."""
from __future__ import annotations

import re
import sqlite3
import sys

import psycopg
import pytest
from psycopg.types.string import TextLoader

import cimitir_cloud_control_pg as ccp
import test_etapa4_pas5_http as m5
import test_etapa4_pas5_tester_extra as m5x
import test_etapa4_pas51_server_validation as m51
import test_etapa4_pas51_tester_extra as m51x
import test_etapa4_pas4_flux_b as m4
import test_etapa4_pas4_tester_extra as m4x
from test_etapa4_pas1 import env  # noqa: F401  (înlocuit mai jos de varianta PG)
from test_etapa4_pas51_server_validation import bsrv  # noqa: F401
from test_etapa4_pas6a_postgres import ADMIN, pgdb, pytestmark  # noqa: F401

_PGSTATE = {}
_STATUS = "(CASE WHEN revoked_at IS NOT NULL THEN 'revoked' WHEN active THEN 'active' ELSE 'pending' END)"


def _pg_sql(sql: str) -> str:
    sql = re.sub(r"rowid=\(SELECT (MIN|MAX)\(rowid\) FROM (\w+)\)", lambda m: f"ctid=(SELECT ctid FROM {m.group(2)} ORDER BY ctid {'ASC' if m.group(1) == 'MIN' else 'DESC'} LIMIT 1)", sql)
    sql = re.sub(r"\bbindings\b", "device_cemetery_bindings", sql)
    sql = re.sub(r"\bstatus\b(?=[^()]*\bFROM\s+device_cemetery_bindings)", _STATUS + " AS status", sql, count=1) if "device_cemetery_bindings" in sql else sql
    sql = ccp._TABLE_RE.sub(lambda m: ccp._TABLES[m.group(1)] if m.group(1) != "bindings" else m.group(1), sql)
    return sql.replace("?", "%s")


def pgq(path, query, *args):
    if str(path) != "pg:env":
        return _orig_q(path, query, *args)
    q2 = query
    q2 = re.sub(r"rowid=\(SELECT (MIN|MAX)\(rowid\) FROM (\w+)\)", lambda m: f"ctid=(SELECT ctid FROM {m.group(2)} ORDER BY ctid {'ASC' if m.group(1) == 'MIN' else 'DESC'} LIMIT 1)", q2)
    if "sqlite_master" in q2 and not args:
        with _conn() as c:
            return c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1").fetchall()
    if "sqlite_master" in q2:
        t = ccp._TABLES.get(args[0], args[0])
        with psycopg.connect(_PGSTATE["admin"], autocommit=True) as c:
            return c.execute("SELECT 1 FROM pg_tables WHERE tablename=%s", (t,)).fetchall()
    if "FROM bindings" in q2 and "sync_epoch" in q2:
        q2 = q2.replace("sync_epoch", "(SELECT data_epoch FROM cemeteries c WHERE c.uid=bindings.cemetery_uid)")
    if "FROM bindings" in q2 and "status" in q2:
        q2 = q2.replace("status", _STATUS)
    with _conn() as c:
        cur = c.execute(_pg_sql(q2), args or None)
        return cur.fetchall() if cur.description else []


_orig_q = m5.q


def _conn():
    c = psycopg.connect(_PGSTATE["admin"], autocommit=True)
    for t in ("uuid", "jsonb"):
        c.adapters.register_loader(t, TextLoader)
    return c


class _Tamper:
    def __init__(self):
        self.c = _conn()

    def execute(self, sql, args=()):
        if "sqlite_master" in sql:
            return self.c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND (tablename LIKE 'cp\\_%' OR tablename IN "
                                  "('organizations','cemeteries','devices','device_cemetery_bindings','device_pairing_codes','identities')) ORDER BY 1")
        return self.c.execute(_pg_sql(sql.replace("issued_by", "created_by")), tuple(args) or None)

    def commit(self):
        pass

    def close(self):
        self.c.close()


class _Sqlite3Shim:
    def connect(self, path, *a, **k):
        return _Tamper() if str(path) == "pg:env" else sqlite3.connect(path, *a, **k)

    def __getattr__(self, n):
        return getattr(sqlite3, n)


_base_env = getattr(env, "__wrapped__", None) or env._get_wrapped_function()


@pytest.fixture(name="env")
def pg_env(tmp_path, monkeypatch, pgdb):
    gen = _base_env(tmp_path, monkeypatch)
    e = next(gen)
    with psycopg.connect(pgdb["admin"], autocommit=True) as c:
        c.execute("TRUNCATE cp_bootstrap_runs, cp_failed_redeems, cp_audit_log, cp_platform_admins, cp_org_roles, cp_binding_issuers, device_pairing_codes, "
                  "device_cemetery_bindings, devices, cemeteries, organizations, identities CASCADE")
    monkeypatch.setenv("CIMITIR_CP_DATABASE_URL", pgdb["app"])
    monkeypatch.setenv("CIMITIR_CP_ADMIN_DATABASE_URL", pgdb["admin"])
    _PGSTATE["admin"] = pgdb["admin"]
    e.cp = ccp.PostgresControlPlane(clock=e.clock)
    e.cp.path = "pg:env"
    e.pg = pgdb
    for m in (m5, m5x, m51, m51x, m4, m4x):
        if hasattr(m, "q"):
            monkeypatch.setattr(m, "q", pgq)
        if hasattr(m, "sqlite3"):
            monkeypatch.setattr(m, "sqlite3", _Sqlite3Shim())
    yield e
    try:
        next(gen)
    except StopIteration:
        pass


@pytest.fixture()
def renv(env, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("CIMITIR_SECRET_STORE", "test-encrypted")
    monkeypatch.setenv("CIMITIR_TEST_SECRET_KEY", Fernet.generate_key().decode())
    env.cp = ccp.PostgresControlPlane()  # ceas real (procesele copil nu pot folosi ceasul fals)
    env.cp.path = "pg:env"
    env.store = env.ss.default_store(env.data)
    env.procs = []
    yield env
    for p in env.procs:
        if p.poll() is None:
            p.kill()
            p.wait()


@pytest.fixture()
def renv_logs(renv):
    (renv.tmp / "logs").mkdir(exist_ok=True)
    return renv


_SKIP_ENV = "env"
for _mod, _tag in ((m5, "pas5"), (m5x, "pas5x"), (m51, "pas51"), (m51x, "pas51x"), (m4, "pas4"), (m4x, "pas4x")):
    for _n in dir(_mod):
        if _n.startswith("test_"):
            globals()[f"{_n}__{_tag}"] = getattr(_mod, _n)
