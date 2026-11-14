"""scripts/macos/verify_cross_sync.py: protecții anti-PROD și scenariul Mac <-> Windows, cu SIMULATOR de server (două baze locale, un server fals).
Verifică logica uneltei; sincronizarea REALĂ pe Cloud TEST se rulează manual (vezi docs/MACOS.md). Date fictive, fără rețea."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_p2_sync_erori_client import DESKTOP, POST, PRE, SHARED  # noqa: E402

TOOL = DESKTOP.parent / "scripts" / "macos" / "verify_cross_sync.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("verify_cross_sync_tool", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tool = _load_tool()
TEST_URL = "https://cloud-test.example.invalid"


def test_refuses_without_explicit_confirmation_or_with_a_different_url():
    with pytest.raises(tool.Refused):
        tool.check_target(TEST_URL, TEST_URL, True, confirmed=False)
    with pytest.raises(tool.Refused):
        tool.check_target(TEST_URL, "https://alt-cloud.example.invalid", True, confirmed=True)
    with pytest.raises(tool.Refused):
        tool.check_target("", TEST_URL, True, confirmed=True)
    with pytest.raises(tool.Refused):
        tool.check_target(TEST_URL, TEST_URL, False, confirmed=True)
    tool.check_target(TEST_URL, TEST_URL + "/", True, confirmed=True)


def test_refuses_the_protected_production_project_even_if_url_matches():
    import cimitir_test_guard as guard
    prod = f"https://{guard.PROTECTED_PROJECT_REFS[0]}.supabase.co"
    with pytest.raises(tool.Refused):
        tool.check_target(prod, prod, True, confirmed=True)


def _run(tmp_path, body):
    code = (PRE.format(desktop=str(DESKTOP), shared=str(SHARED)) +
            f"\nimport importlib.util\nspec = importlib.util.spec_from_file_location('t', {str(TOOL)!r})\ntool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)\n"
            "logs = []\nlog = logs.append\nPATH_MAC, PATH_WIN = PATH_A, PATH_B\n"
            + body + POST)
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_") and k not in ("DATABASE_URL", "SUPABASE_URL")}
    env.update(CIMITIR_DATA_DIR=str(tmp_path / "data"), CIMITIR_ENV_FILE=str(tmp_path / "nu.env"), CIMITIR_LOG_DIR=str(tmp_path / "logs"),
               CIMITIR_DB_ENGINE="sqlite", CIMITIR_MODE="local", DATABASE_URL="", QT_QPA_PLATFORM="offscreen")
    done = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(DESKTOP), capture_output=True, text=True, timeout=150)
    assert done.returncode == 0, done.stderr[-4000:]
    return json.loads([ln for ln in done.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:])


GO = """
def go(path, role, phase, **kw):
    use(path)
    return tool.run_phase(phase, role, db, es, log=log, **kw)
"""


def test_mac_writes_windows_checks_and_the_reverse(tmp_path):
    out = _run(tmp_path, GO + """
out["mac_write"] = go(PATH_MAC, "mac", "write")
out["win_check"] = go(PATH_WIN, "windows", "check")
out["win_write"] = go(PATH_WIN, "windows", "write")
out["mac_check"] = go(PATH_MAC, "mac", "check")
out["server_rows"] = len(SRV.rows)
use(PATH_WIN); out["win_sees_mac"] = tool.verify_other_side(db, "mac")
use(PATH_MAC); out["mac_sees_win"] = tool.verify_other_side(db, "windows")
""")
    assert out["mac_write"] == 0 and out["win_check"] == 0 and out["win_write"] == 0 and out["mac_check"] == 0
    assert out["server_rows"] == 6
    for key in ("win_sees_mac", "mac_sees_win"):
        assert out[key]["ok"] and out[key]["contracte_legate"] == 1


def test_check_fails_when_the_other_side_has_not_written_anything(tmp_path):
    out = _run(tmp_path, GO + """out["code"] = go(PATH_WIN, "windows", "check")""")
    assert out["code"] == 1


def test_offline_write_stays_queued_then_flush_delivers_it_once(tmp_path):
    out = _run(tmp_path, GO + """
real = es._call
state = {"offline": True}
def flaky(method, op, **kw):
    if state["offline"] and op in ("push", "pull"):
        raise cs.RetryLater("Fara conexiune la server: offline")
    return real(method, op, **kw)
es._call = flaky
out["local"] = go(PATH_MAC, "mac", "write-local")
use(PATH_MAC); out["queued"] = len(outbox()); out["server_offline"] = len(SRV.rows)
out["flush_offline"] = go(PATH_MAC, "mac", "flush")
state["offline"] = False
out["flush_online"] = go(PATH_MAC, "mac", "flush")
use(PATH_MAC); out["queued_after"] = len(outbox())
out["dup_ops"] = len(SRV.log) - len(set(SRV.log))
out["win_check"] = go(PATH_WIN, "windows", "check")
""")
    assert out["local"] == 0 and out["queued"] >= 3 and out["server_offline"] == 0
    assert out["flush_offline"] == 1 and out["flush_online"] == 0 and out["queued_after"] == 0
    assert out["win_check"] == 0


def test_cleanup_sends_tombstones_and_only_touches_own_markers(tmp_path):
    out = _run(tmp_path, GO + """
go(PATH_MAC, "mac", "write"); go(PATH_WIN, "windows", "write")
go(PATH_WIN, "windows", "check"); go(PATH_MAC, "mac", "check")
out["cleanup"] = go(PATH_MAC, "mac", "cleanup")
out["win_after"] = go(PATH_WIN, "windows", "check")
use(PATH_WIN)
out["win_sees_mac"] = tool.verify_other_side(db, "mac")["locuri"]
out["win_own"] = {k: len(v) for k, v in tool.own_markers(db, "windows").items()}
use(PATH_MAC)
out["mac_own"] = {k: len(v) for k, v in tool.own_markers(db, "mac").items()}
""")
    assert out["cleanup"] == 0 and out["win_after"] == 1 and out["win_sees_mac"] == 0
    assert out["win_own"] == {"loc": 1, "concesionar": 1, "contract": 1} and out["mac_own"] == {"loc": 0, "concesionar": 0, "contract": 0}


def test_refuses_to_push_a_database_that_holds_real_looking_data(tmp_path):
    out = _run(tmp_path, GO + """
use(PATH_MAC)
db.save_plot_to_db("A-01", SQ, "#00ff00")
try:
    go(PATH_MAC, "mac", "write"); out["refused"] = False
except tool.Refused as exc:
    out["refused"] = True; out["msg"] = str(exc)
out["server_rows"] = len(SRV.rows)
out["allowed"] = go(PATH_MAC, "mac", "write", allow_existing=True)
""")
    assert out["refused"] is True and out["server_rows"] == 0 and "SYNCTEST" in out["msg"]
    assert out["allowed"] == 0
