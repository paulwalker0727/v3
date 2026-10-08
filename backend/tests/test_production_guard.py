"""Protecția anti-pytest pe producție. Doar valori simulate; nu se conectează la nicio bază."""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))
sys.path.insert(0, str(ROOT / "backend" / "cimitir"))

import cimitir_test_guard as g  # noqa: E402

REF = "txuxkjgftopznvmooexq"
PROD_URL = f"postgresql://postgres.{REF}:x@aws-0-eu-west-1.pooler.supabase.com:5432/postgres?sslmode=require"
SRV_VAR = "ETAPA6_" + "SERVER_URL"
TEST_URL = "postgresql://tester:x@localhost:5432/cimitir_test"


def _issues(tmp_path, env=None, files=None):
    for rel, text in (files or {}).items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return g.find_issues(env or {}, tmp_path)


def test_prod_url_in_environment_is_blocked(tmp_path):
    assert _issues(tmp_path, {"DATABASE_URL": PROD_URL})


def test_prod_ref_alone_is_blocked_even_with_other_host(tmp_path):
    assert _issues(tmp_path, {"DATABASE_URL": f"postgresql://postgres.{REF}:x@db.example.com:5432/postgres"})


def test_any_supabase_host_is_blocked(tmp_path):
    assert _issues(tmp_path, {"DATABASE_URL": "postgresql://u:x@aws-0-eu-west-1.pooler.supabase.com:5432/other"})


def test_remote_non_test_database_is_blocked(tmp_path):
    assert _issues(tmp_path, {"DATABASE_URL": "postgresql://u:x@db.example.com:5432/cimitir"})


def test_prod_in_backend_env_file_is_blocked(tmp_path):
    assert _issues(tmp_path, {}, {"backend/.env": f"DATABASE_URL={PROD_URL}\n"})


def test_prod_in_root_env_file_and_env_file_variable_is_blocked(tmp_path):
    assert _issues(tmp_path, {}, {".env": f'DATABASE_URL="{PROD_URL}"\n'})
    extra = tmp_path / "extra.env"
    extra.write_text(f"DATABASE_URL={PROD_URL}\n", encoding="utf-8")
    assert _issues(tmp_path, {"CIMITIR_ENV_FILE": str(extra)})


def test_supabase_url_and_secret_key_are_blocked(tmp_path):
    assert _issues(tmp_path, {"SUPABASE_URL": f"https://{REF}.supabase.co"})
    assert _issues(tmp_path, {"SUPABASE_SECRET_KEY": "sb_secret_SIMULAT"})


def test_messages_never_contain_values(tmp_path):
    text = " ".join(_issues(tmp_path, {"DATABASE_URL": PROD_URL, "SUPABASE_SECRET_KEY": "sb_secret_SIMULAT"}))
    assert REF not in text and "sb_secret_SIMULAT" not in text and "pooler.supabase.com" not in text and "postgresql://" not in text


@pytest.mark.parametrize("url", ["", "sqlite:///tmp/x.db", "postgresql://u:x@localhost:5432/cimitir", "postgresql://u:x@127.0.0.1/db"])
def test_safe_defaults_pass(tmp_path, url):
    assert _issues(tmp_path, {"DATABASE_URL": url}) == []


def test_empty_environment_passes(tmp_path):
    assert _issues(tmp_path, {}) == []


def test_explicit_test_database_is_accepted(tmp_path):
    assert _issues(tmp_path, {"CIMITIR_TEST_DATABASE_URL": TEST_URL, "DATABASE_URL": TEST_URL}) == []
    remote_test = "postgresql://t:x@test-db.example.com:5432/cimitir_test"
    assert _issues(tmp_path, {"CIMITIR_TEST_DATABASE_URL": remote_test, "DATABASE_URL": remote_test}) == []


def test_test_database_variable_cannot_point_to_prod_or_unnamed_db(tmp_path):
    assert _issues(tmp_path, {"CIMITIR_TEST_DATABASE_URL": PROD_URL})
    assert _issues(tmp_path, {"CIMITIR_TEST_DATABASE_URL": PROD_URL.replace("/postgres?", "/prod_test?")})
    assert _issues(tmp_path, {"CIMITIR_TEST_DATABASE_URL": "postgresql://u:x@localhost:5432/cimitir"})


def test_test_database_does_not_whitelist_a_different_database_url(tmp_path):
    assert _issues(tmp_path, {"CIMITIR_TEST_DATABASE_URL": TEST_URL, "DATABASE_URL": PROD_URL})
    assert _issues(tmp_path, {"CIMITIR_TEST_DATABASE_URL": TEST_URL}, {"backend/.env": f"DATABASE_URL={PROD_URL}\n"})


@pytest.mark.parametrize("bypass", ["CIMITIR_ALLOW_PROD_TESTS", "ALLOW_PRODUCTION_TESTS", "PYTEST_ALLOW_PROD", "CIMITIR_SKIP_TEST_GUARD", "FORCE"])
def test_there_is_no_bypass_variable(tmp_path, bypass):
    assert _issues(tmp_path, {"DATABASE_URL": PROD_URL, bypass: "1"})


def test_live_tests_need_separate_test_environment(tmp_path):
    live = lambda env: g.live_allowed(env, tmp_path)  # noqa: E731
    assert live({}) is False
    assert live({"CIMITIR_TEST_DATABASE_URL": TEST_URL}) is False
    assert live({SRV_VAR: "http://127.0.0.1:9000"}) is False
    assert live({"CIMITIR_TEST_DATABASE_URL": PROD_URL, SRV_VAR: "http://127.0.0.1:9000"}) is False
    assert live({"CIMITIR_TEST_DATABASE_URL": TEST_URL, SRV_VAR: "http://127.0.0.1:9000"}) is True


def test_live_sources_are_detected():
    assert g.is_live_source('BASE = "http://127.0.0.1:' + '8001"')
    assert g.is_live_source(f'SERVER = os.environ.get("{SRV_VAR}")')
    assert not g.is_live_source("def test_x():\n    assert 1 + 1 == 2\n")


def test_pytest_stops_before_the_first_test_when_prod_is_detected(tmp_path):
    (tmp_path / "conftest.py").write_text(textwrap.dedent(f"""
        import sys
        from pathlib import Path
        sys.path.insert(0, {str(ROOT / 'shared')!r})
        import cimitir_test_guard as g
        def pytest_configure(config):
            g.enforce_or_exit(env={{}}, root=Path(__file__).parent)
    """), encoding="utf-8")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / ".env").write_text(f"DATABASE_URL={PROD_URL}\n", encoding="utf-8")
    marker = tmp_path / "A_RULAT"
    (tmp_path / "test_w.py").write_text(f"def test_w():\n    open({str(marker)!r}, 'w').write('x')\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SECRET_KEY", "CIMITIR_ENV_FILE")}
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist", str(tmp_path / "test_w.py")],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == g.EXIT_CODE
    assert not marker.exists()
    assert "PYTEST REFUZAT" in proc.stdout + proc.stderr
    assert REF not in proc.stdout + proc.stderr


# ------------------------------------------------------------ proiect Supabase TEST (ref simulat)
TREF = "abcdefghijklmnopqrst"
TEST_PG = f"postgresql://postgres.{TREF}:x@aws-0-eu-west-1.pooler.supabase.com:5432/postgres?sslmode=require"
TEST_ENV = {"CIMITIR_LIVE_TESTS": "1", "CIMITIR_TEST_PROJECT_REF": TREF, "DATABASE_URL": TEST_PG,
            "SUPABASE_URL": f"https://{TREF}.supabase.co", "SUPABASE_SECRET_KEY": "sb_secret_SIMULAT"}


def test_supabase_test_project_is_accepted_only_when_everything_matches(tmp_path):
    assert _issues(tmp_path, dict(TEST_ENV)) == []
    assert _issues(tmp_path, {}, {"backend/.env": "\n".join(f"{k}={v}" for k, v in TEST_ENV.items()) + "\n"}) == []


@pytest.mark.parametrize("drop", ["CIMITIR_LIVE_TESTS", "CIMITIR_TEST_PROJECT_REF"])
def test_supabase_test_project_needs_live_flag_and_ref(tmp_path, drop):
    env = {k: v for k, v in TEST_ENV.items() if k != drop}
    assert _issues(tmp_path, env)


def test_live_flag_must_be_exactly_one(tmp_path):
    assert _issues(tmp_path, {**TEST_ENV, "CIMITIR_LIVE_TESTS": "true"})


def test_connection_must_match_the_test_ref_exactly(tmp_path):
    other = "zzzzzzzzzzzzzzzzzzzz"
    assert _issues(tmp_path, {**TEST_ENV, "DATABASE_URL": TEST_PG.replace(TREF, other)})
    assert _issues(tmp_path, {**TEST_ENV, "SUPABASE_URL": f"https://{other}.supabase.co"})
    assert _issues(tmp_path, {**TEST_ENV, "DATABASE_URL": TEST_PG.replace("?sslmode=require", "")})
    assert _issues(tmp_path, {**TEST_ENV, "DATABASE_URL": TEST_PG.replace("aws-0-eu-west-1.pooler.supabase.com", f"db.{TREF}.supabase.co")})


def test_production_ref_is_a_hard_block_even_with_live_flag_and_matching_vars(tmp_path):
    prod_env = {"CIMITIR_LIVE_TESTS": "1", "CIMITIR_TEST_PROJECT_REF": REF, "DATABASE_URL": PROD_URL,
                "SUPABASE_URL": f"https://{REF}.supabase.co", "SUPABASE_SECRET_KEY": "sb_secret_SIMULAT"}
    assert _issues(tmp_path, prod_env)
    assert _issues(tmp_path, {**TEST_ENV, "DATABASE_URL": PROD_URL})
    assert _issues(tmp_path, {**TEST_ENV, "SUPABASE_URL": f"https://{REF}.supabase.co"})
    assert _issues(tmp_path, {**TEST_ENV, "CIMITIR_TEST_DATABASE_URL": PROD_URL})
    assert _issues(tmp_path, TEST_ENV, {"backend/.env": f"DATABASE_URL={PROD_URL}\n"})
    assert _issues(tmp_path, {**TEST_ENV, "CIMITIR_TEST_PROJECT_REF": REF})


def test_test_ref_must_look_like_a_project_ref(tmp_path):
    assert _issues(tmp_path, {**TEST_ENV, "CIMITIR_TEST_PROJECT_REF": "nu-e-ref"})


def test_hard_block_messages_do_not_leak_values(tmp_path):
    text = " ".join(_issues(tmp_path, {**TEST_ENV, "DATABASE_URL": PROD_URL}))
    assert REF not in text and "pooler.supabase.com" not in text and "postgresql://" not in text and "sb_secret" not in text


def test_pytest_exits_before_the_first_test_even_with_live_flag_on_production(tmp_path):
    (tmp_path / "conftest.py").write_text(textwrap.dedent(f"""
        import sys
        from pathlib import Path
        sys.path.insert(0, {str(ROOT / 'shared')!r})
        import cimitir_test_guard as g
        def pytest_configure(config):
            g.enforce_or_exit(env={{}}, root=Path(__file__).parent)
    """), encoding="utf-8")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / ".env").write_text(f"CIMITIR_LIVE_TESTS=1\nCIMITIR_TEST_PROJECT_REF={REF}\nDATABASE_URL={PROD_URL}\n", encoding="utf-8")
    marker = tmp_path / "A_RULAT"
    (tmp_path / "test_w.py").write_text(f"def test_w():\n    open({str(marker)!r}, 'w').write('x')\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SECRET_KEY", "CIMITIR_ENV_FILE", "CIMITIR_LIVE_TESTS", "CIMITIR_TEST_PROJECT_REF")}
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist", str(tmp_path / "test_w.py")],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == g.EXIT_CODE and not marker.exists()
    assert REF not in proc.stdout + proc.stderr


# ------------------------------------------------------------ migrările multi-tenant (008–013): blocate pe producție
def test_tenant_migrations_are_blocked_on_production_even_with_allow_flag():
    import postgres_store as ps
    assert ps.tenant_migration_block_reason(PROD_URL, "1")
    assert ps.tenant_migration_block_reason(f"postgresql://postgres.{REF}:x@db.example.com:5432/postgres", "1")


def test_tenant_migrations_need_explicit_allow_flag_on_test_database():
    import postgres_store as ps
    assert ps.tenant_migration_block_reason(TEST_PG, "")
    assert ps.tenant_migration_block_reason(TEST_PG, "true")
    assert ps.tenant_migration_block_reason(TEST_PG, "1") == ""


def test_tenant_migration_block_message_has_no_values():
    import postgres_store as ps
    msg = ps.tenant_migration_block_reason(PROD_URL, "1")
    assert REF not in msg and "postgresql://" not in msg
