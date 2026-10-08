"""Etapa 11 — /admin/system: clasificare bază (SQLite / PG local / PG cloud) și test de persistență sigur (probă tehnică izolată)."""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "backend", ROOT / "backend" / "cimitir", ROOT / "shared"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import system_admin as sa  # noqa: E402

PW = "S3cretPassw0rd-xyz"


def cls(url, eng="postgres"):
    return sa.classify_database(eng, url)


@pytest.mark.parametrize("url", [
    f"postgresql://u:{PW}@127.0.0.1:55432/cimitir_dev",
    f"postgresql://u:{PW}@localhost:5432/cimitir",
    f"postgresql://u:{PW}@[::1]:5432/cimitir",
    f"postgresql://u:{PW}@10.0.3.4:5432/cimitir",
    f"postgresql://u:{PW}@192.168.1.20/cimitir",
    f"postgresql://u:{PW}@postgres/cimitir",
    f"postgresql://u:{PW}@db.default.svc.cluster.local/cimitir",
    f"postgresql://u:{PW}@pg.internal:5432/cimitir",
    "postgresql:///cimitir?host=/var/run/postgresql",
])
def test_local_hosts_are_never_cloud(url):
    c = cls(url)
    assert c["kind"] == "postgres_local" and c["tone"] == "warn" and "local" in c["label"].lower()


@pytest.mark.parametrize("url,provider", [
    (f"postgresql://postgres:{PW}@db.abcdefghijklmnop.supabase.co:5432/postgres?sslmode=require", "Supabase"),
    (f"postgresql://u:{PW}@aws-0-eu.pooler.supabase.com:6543/postgres?sslmode=require", "Supabase"),
    (f"postgresql://u:{PW}@ep-cool-123.eu-central-1.aws.neon.tech/db?sslmode=require", "Neon"),
    (f"postgresql://u:{PW}@mydb.abc.eu-west-1.rds.amazonaws.com/db?sslmode=verify-full", "AWS (RDS/Aurora)"),
    (f"postgresql://u:{PW}@sql.exemplu-necunoscut.ro:5432/cimitir?sslmode=require", ""),
    (f"postgresql://u:{PW}@34.120.5.9:5432/cimitir?sslmode=require", ""),
])
def test_public_hosts_are_cloud_candidates(url, provider):
    c = cls(url)
    assert c["kind"] == "postgres_cloud" and c["provider"] == provider and c["tls"] is True


def test_engine_flag_alone_is_not_proof_of_cloud():
    assert cls(f"postgresql://u:{PW}@127.0.0.1:55432/cimitir_dev")["kind"] != "postgres_cloud"
    assert any("nu dovedește" in text for _, text in cls(f"postgresql://u:{PW}@127.0.0.1/x")["checks"])
    assert cls("")["kind"] == "postgres_unconfigured" and cls("")["tone"] == "err"
    s = cls("", eng="sqlite")
    assert s["kind"] == "sqlite" and "SQLite" in s["label"]


def test_cloud_without_tls_and_dev_name_warn():
    c = cls(f"postgresql://u:{PW}@db.x.supabase.co/cimitir_dev")
    assert c["kind"] == "postgres_cloud" and c["tls"] is False
    texts = " ".join(t for _, t in c["checks"])
    assert "TLS neobligatoriu" in texts and "dezvoltare/test" in texts


def test_classification_never_leaks_credentials_or_host():
    url = f"postgresql://adminuser:{PW}@db.secretref123.supabase.co:5432/postgres?sslmode=require"
    blob = json.dumps(cls(url))
    for secret in (PW, "adminuser", "secretref123", url):
        assert secret not in blob


def test_fingerprint_changes_with_database_not_with_password():
    a = cls("postgresql://u:p1@db.a.supabase.co/postgres")["fingerprint"]
    b = cls("postgresql://u:p2@db.a.supabase.co/postgres")["fingerprint"]
    c = cls("postgresql://u:p1@db.b.supabase.co/postgres")["fingerprint"]
    assert a == b != c


# ------------------------------------------------------- persistență: procese separate = restart real, SQLite temporar
def _run(tmp_path, body):
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL",)}
    env.update(CIMITIR_DATA_DIR=str(tmp_path), CIMITIR_DB_ENGINE="sqlite", CIMITIR_MODE="local", CIMITIR_IGNORE_PROJECT_ENV="1",
               PYTHONPATH=":".join(str(ROOT / p) for p in ("backend", "backend/cimitir", "shared")))
    code = "import json, system_admin as sa, store as cim\nimport database; database.init_db()\n" + textwrap.dedent(body)
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=ROOT / "backend", timeout=60)
    assert p.returncode == 0, p.stderr[-1500:]
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_persistence_flow_across_real_restart_and_cleanup(tmp_path):
    _run(tmp_path, "cim.set_setting('real_setting', 'valoare-reala')\nprint(json.dumps(1))")
    step1 = _run(tmp_path, """
        r = sa.persistence_create()
        again = sa.persistence_create()
        early = sa.persistence_verify()
        print(json.dumps({"create": r["ok"], "dup_blocked": not again["ok"], "early_ok": early["ok"],
                          "early_text": " ".join(t for _, t in early["lines"]), "probe": cim.get_setting(sa.PROBE_KEY) is not None,
                          "prod": sa.production_check()["persistence"]["probe_pending"]}))
    """)
    assert step1["create"] and step1["dup_blocked"] and step1["probe"] and step1["prod"]
    assert step1["early_ok"] is False and "nu a fost repornit" in step1["early_text"]
    step2 = _run(tmp_path, """
        v = sa.persistence_verify()
        after = sa.production_check()
        print(json.dumps({"ok": v["ok"], "text": " ".join(t for _, t in v["lines"]), "probe_gone": cim.get_setting(sa.PROBE_KEY) is None,
                          "verified": after["persistence"]["verified"], "level": after["level"], "ready": after["ready"],
                          "real": cim.get_setting('real_setting')}))
    """)
    assert step2["ok"] and "PERSISTAT" in step2["text"] and step2["probe_gone"] and step2["verified"]
    assert step2["level"] == "sqlite" and step2["ready"] is False      # persistența singură nu face din SQLite „cloud”
    assert step2["real"] == "valoare-reala"                              # datele reale nu sunt atinse
    step3 = _run(tmp_path, "v = sa.persistence_verify()\nprint(json.dumps({'ok': v['ok'], 'text': v['lines'][0][1]}))")
    assert step3["ok"] is False and "Nu există nicio probă" in step3["text"]


def test_missing_probe_after_restart_is_not_confused_and_cleanup_works(tmp_path):
    _run(tmp_path, "cim.set_setting('x','y')\nprint(1)")
    out = _run(tmp_path, """
        sa.persistence_create()
        c = sa.persistence_cleanup()
        c2 = sa.persistence_cleanup()
        print(json.dumps({"c": c["ok"], "gone": cim.get_setting(sa.PROBE_KEY) is None, "c2": c2["ok"],
                          "verified": sa.production_check()["persistence"]["verified"]}))
    """)
    assert out == {"c": True, "gone": True, "c2": True, "verified": False}


def test_production_level_requires_cloud_and_verified_persistence(monkeypatch):
    monkeypatch.setattr(sa, "engine", lambda: "postgres")
    monkeypatch.setattr(sa, "db_status", lambda: {"online": True})
    cloud = "postgresql://u:p@db.x.supabase.co:5432/postgres?sslmode=require"
    fp = sa.classify_database("postgres", cloud)["fingerprint"]
    monkeypatch.setenv("DATABASE_URL", cloud)
    state = {"probe": None, "result": None, "verified": False, "stale_result": False}
    monkeypatch.setattr(sa, "persistence_state", lambda f: dict(state))
    assert sa.production_check()["level"] == "pg_cloud_unverified"
    state.update(result={"ok": True, "fingerprint": fp}, verified=True)
    pc = sa.production_check()
    assert pc["level"] == "pg_cloud_persistent" and pc["ready"] is True
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:55432/cimitir_dev")
    assert sa.production_check()["level"] == "pg_local"      # chiar cu „verified" rămas din altă bază


def test_transaction_pooler_port_is_flagged():
    c = cls(f"postgresql://u:{PW}@aws-0-eu.pooler.supabase.com:6543/postgres?sslmode=require")
    assert any("6543" in text for _, text in c["checks"])
