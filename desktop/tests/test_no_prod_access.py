"""Dovadă explicită că testele nu pot atinge PROD: protecția anti-pytest (shared/cimitir_test_guard.py) oprește rularea (cod 4)
înainte de colectare dacă mediul indică proiectul de producție; nu se face nicio conexiune (procesul se oprește în pytest_configure)."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))
import cimitir_test_guard as guard  # noqa: E402

PROD_REF = guard.PROTECTED_PROJECT_REFS[0]


def _pytest(extra_env):
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SECRET_KEY") and not k.startswith("CIMITIR_TEST")}
    env.update(extra_env)
    return subprocess.run([sys.executable, "-m", "pytest", "desktop/tests/test_client_guide.py", "-q", "-p", "no:cacheprovider"],
                          cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)


def test_pytest_refuses_to_start_when_database_url_points_to_production():
    url = f"postgresql://postgres.{PROD_REF}:x@aws-0-eu.pooler.supabase.com:5432/postgres?sslmode=require"
    p = _pytest({"DATABASE_URL": url})
    assert p.returncode == guard.EXIT_CODE == 4
    assert "PYTEST REFUZAT" in p.stdout + p.stderr


def test_pytest_refuses_to_start_when_supabase_url_points_to_production():
    p = _pytest({"SUPABASE_URL": f"https://{PROD_REF}.supabase.co"})
    assert p.returncode == 4


def test_production_ref_cannot_be_unlocked_by_live_test_flags():
    p = _pytest({"DATABASE_URL": f"postgresql://postgres.{PROD_REF}:x@aws-0-eu.pooler.supabase.com:5432/postgres?sslmode=require",
                 "CIMITIR_LIVE_TESTS": "1", "CIMITIR_TEST_PROJECT_REF": PROD_REF})
    assert p.returncode == 4


def test_guard_reports_no_issue_for_isolated_sqlite_environment():
    assert guard.find_issues({"CIMITIR_DB_ENGINE": "sqlite", "DATABASE_URL": ""}, ROOT / "nu-exista") == []
