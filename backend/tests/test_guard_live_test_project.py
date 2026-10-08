"""Guard live pe proiectul Supabase TEST real. Nicio conexiune și niciun WRITE: se verifică doar decizia guard-ului,
iar conexiunea (read-only, SELECT) se face exclusiv pe proiectul TEST, după ce guard-ul l-a acceptat.
Rulare: CIMITIR_LIVE_TESTS=1 CIMITIR_TEST_PROJECT_REF=<ref test> DATABASE_URL=<url test> pytest backend/tests/test_guard_live_test_project.py
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))
import cimitir_test_guard as g  # noqa: E402

PROD = "txuxkjgftopznvmooexq"
TEST_REF = os.environ.get("CIMITIR_TEST_PROJECT_REF", "")
REAL = {k: os.environ.get(k, "") for k in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SECRET_KEY", "CIMITIR_TEST_PROJECT_REF", "CIMITIR_LIVE_TESTS")}
pytestmark = pytest.mark.skipif(not (REAL["CIMITIR_LIVE_TESTS"] == "1" and REAL["DATABASE_URL"] and REAL["SUPABASE_URL"] and TEST_REF),
                                reason="necesită mediul real de TEST (CIMITIR_LIVE_TESTS=1, CIMITIR_TEST_PROJECT_REF, DATABASE_URL, SUPABASE_URL)")


def _issues(env):
    return g.find_issues(env, Path("/nonexistent-root"))


def _prod_url():
    return f"postgresql://postgres.{PROD}:SIMULAT@aws-0-eu-west-1.pooler.supabase.com:5432/postgres?sslmode=require"


def test_real_test_project_is_not_the_protected_one():
    assert TEST_REF not in g.PROTECTED_PROJECT_REFS and TEST_REF != PROD
    assert f".{TEST_REF}" in REAL["DATABASE_URL"] and PROD not in REAL["DATABASE_URL"] + REAL["SUPABASE_URL"]


def test_test_project_allowed():
    assert _issues(dict(REAL)) == []


def test_test_project_allowed_and_reachable_read_only():
    import psycopg
    assert _issues(dict(REAL)) == []
    with psycopg.connect(REAL["DATABASE_URL"], connect_timeout=20) as c:
        assert c.execute("SELECT 1").fetchone() == (1,)
        c.rollback()


def test_production_refused():
    assert _issues({**REAL, "DATABASE_URL": _prod_url()})
    assert _issues({**REAL, "SUPABASE_URL": f"https://{PROD}.supabase.co"})


def test_production_refused_even_with_live_flag_and_matching_ref():
    prod = {"CIMITIR_LIVE_TESTS": "1", "CIMITIR_TEST_PROJECT_REF": PROD, "DATABASE_URL": _prod_url(), "SUPABASE_URL": f"https://{PROD}.supabase.co"}
    assert _issues(prod)
    assert _issues({**REAL, "CIMITIR_TEST_PROJECT_REF": PROD})
    assert _issues({**REAL, "CIMITIR_TEST_DATABASE_URL": _prod_url()})


def test_missing_test_ref_is_refused():
    assert _issues({**REAL, "CIMITIR_TEST_PROJECT_REF": ""})
    assert _issues({**REAL, "CIMITIR_LIVE_TESTS": ""})


def test_mismatched_ref_is_refused():
    other = "zzzzzzzzzzzzzzzzzzzz"
    assert _issues({**REAL, "CIMITIR_TEST_PROJECT_REF": other})
    assert _issues({**REAL, "DATABASE_URL": REAL["DATABASE_URL"].replace(TEST_REF, other)})
    assert _issues({**REAL, "SUPABASE_URL": f"https://{other}.supabase.co"})


def _run_pytest_with_env(tmp_path, env_overrides, drop=()):
    (tmp_path / "conftest.py").write_text(textwrap.dedent(f"""
        import sys
        from pathlib import Path
        sys.path.insert(0, {str(ROOT / 'shared')!r})
        import cimitir_test_guard as g
        def pytest_configure(config):
            g.enforce_or_exit(root=Path(__file__).parent)
    """), encoding="utf-8")
    marker = tmp_path / "WRITE_ATTEMPT"
    (tmp_path / "test_w.py").write_text(f"def test_w():\n    open({str(marker)!r}, 'w').write('x')\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in REAL and k not in drop}
    env.update(env_overrides)
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist", str(tmp_path / "test_w.py")],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    return proc, marker


def test_pytest_exits_before_any_write_on_production(tmp_path):
    env = {"CIMITIR_LIVE_TESTS": "1", "CIMITIR_TEST_PROJECT_REF": PROD, "DATABASE_URL": _prod_url(), "SUPABASE_URL": f"https://{PROD}.supabase.co"}
    proc, marker = _run_pytest_with_env(tmp_path, env)
    assert proc.returncode == g.EXIT_CODE and not marker.exists()
    assert PROD not in proc.stdout + proc.stderr


def test_pytest_exits_before_any_write_when_ref_missing_or_mismatched(tmp_path):
    no_ref = {k: v for k, v in REAL.items() if k != "CIMITIR_TEST_PROJECT_REF"}
    proc, marker = _run_pytest_with_env(tmp_path, no_ref)
    assert proc.returncode == g.EXIT_CODE and not marker.exists()
    tmp2 = tmp_path / "m"
    tmp2.mkdir()
    proc, marker = _run_pytest_with_env(tmp2, {**REAL, "CIMITIR_TEST_PROJECT_REF": "zzzzzzzzzzzzzzzzzzzz"})
    assert proc.returncode == g.EXIT_CODE and not marker.exists()


def test_pytest_runs_on_the_real_test_project(tmp_path):
    proc, marker = _run_pytest_with_env(tmp_path, dict(REAL))
    assert proc.returncode == 0 and marker.exists()
