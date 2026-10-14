"""MAIN local-first: backend local pe 127.0.0.1:8000 (implicit), LAN doar opt-in, port ocupat, oprire curată, fără internet.

Rulează backend-ul real (subproces uvicorn, SQLite temporar). Fără rețea externă, fără Supabase/Render.
"""
import ast
import json
import os
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
import requests

DESKTOP = Path(__file__).resolve().parents[1]
ROOT = DESKTOP.parent
sys.path.insert(0, str(DESKTOP))
sys.path.insert(0, str(ROOT / "shared"))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def data_env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("CIMITIR_DATA_DIR", str(data))
    monkeypatch.setenv("CIMITIR_ENV_FILE", str(tmp_path / "nu.env"))
    monkeypatch.setenv("CIMITIR_DB_ENGINE", "sqlite")
    monkeypatch.setenv("CIMITIR_MODE", "local")
    monkeypatch.setenv("DATABASE_URL", "")
    for var in ("CIMITIR_HOST", "CIMITIR_PORT", "PORT"):
        monkeypatch.delenv(var, raising=False)
    return data


# ---------------------------------------------------------------- implicit: 127.0.0.1:8000
def test_defaults_are_loopback_8000_and_ignore_generic_port(data_env, monkeypatch):
    import cimitir_config as cfg
    assert (cfg.LOCAL_HOST, cfg.LOCAL_PORT) == ("127.0.0.1", 8000)
    assert cfg.portal_host() == "127.0.0.1" and cfg.desktop_port() == 8000
    monkeypatch.setenv("PORT", "9999")
    assert cfg.desktop_port() == 8000, "variabila generică PORT (hosting) nu mută backend-ul local"
    monkeypatch.setenv("CIMITIR_PORT", "8123")
    assert cfg.desktop_port() == 8123
    assert cfg.is_loopback_host("127.0.0.1") and cfg.is_loopback_host("localhost") and not cfg.is_loopback_host("0.0.0.0")


def test_no_default_binds_to_all_interfaces():
    launcher = (DESKTOP / "launcher.py").read_text(encoding="utf-8")
    tree = ast.parse(launcher)
    consts = {n.targets[0].id: n.value.value for n in tree.body
              if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and isinstance(n.targets[0], ast.Name)}
    assert consts["LAN_HOST"] == "0.0.0.0"
    methods = {n.name: ast.get_source_segment(launcher, n) for c in tree.body if isinstance(c, ast.ClassDef)
               for n in c.body if isinstance(n, ast.FunctionDef)}
    assert "LAN_HOST" not in methods["start_local"] and "0.0.0.0" not in methods["start_local"]
    assert "ensure_portal_status(host, port)" in methods["_start_portal"] and "LOCAL_HOST" in methods["start_local"]
    assert "_confirm_lan" in methods["start_server"], "modul de rețea cere confirmare explicită"
    assert "lan_opt_in" in launcher.split("def main")[1], "pornirea automată în rețea cere opt-in memorat"


def test_web_server_default_host_is_loopback():
    tree = ast.parse((DESKTOP / "web_server.py").read_text(encoding="utf-8"))
    run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run")
    assert run.args.defaults[0].value == "127.0.0.1"


def test_portal_cli_default_host_and_port_come_from_local_defaults(data_env):
    text = (DESKTOP / "start_local.py").read_text(encoding="utf-8")
    assert "cimitir_config.desktop_port()" in text and "cimitir_config.portal_port()" not in text


# ---------------------------------------------------------------- pornire, reutilizare, port ocupat
def test_start_reuse_own_instance_and_report_busy_ports_without_switching(data_env, tmp_path, monkeypatch):
    import start_local as sl
    port = _free_port()
    st = sl.ensure_portal_status("127.0.0.1", port, health_timeout=40)
    try:
        assert st.state == "started" and st.ok and st.proc is not None and st.proc.poll() is None
        info = sl.health_info("127.0.0.1", port)
        assert info and info["ok"] is True and len(info["instance"]) == 16
        again = sl.ensure_portal_status("127.0.0.1", port, health_timeout=5)
        assert again.state == "reused" and again.proc is None and again.ok, "își reutilizează propria instanță"
        assert st.proc.poll() is None, "reutilizarea nu oprește și nu repornește procesul"

        other = tmp_path / "alta"
        other.mkdir()
        monkeypatch.setenv("CIMITIR_DATA_DIR", str(other))
        foreign = sl.ensure_portal_status("127.0.0.1", port, health_timeout=3)
        assert foreign.state == "foreign_instance" and not foreign.ok and foreign.proc is None
        assert str(port) in foreign.message and "nu pornesc pe alt port" in foreign.message
        monkeypatch.setenv("CIMITIR_DATA_DIR", str(data_env))
    finally:
        sl.stop_portal(st.proc)

    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        bport = busy.getsockname()[1]
        other_prog = sl.ensure_portal_status("127.0.0.1", bport, health_timeout=2)
        assert other_prog.state == "busy_other" and other_prog.proc is None and not other_prog.ok
        assert str(bport) in other_prog.message and "alt program" in other_prog.message and "nu pornesc pe alt port" in other_prog.message
        assert not sl.port_is_free("127.0.0.1", bport)
        assert busy.getsockname()[1] == bport


def test_busy_port_check_does_not_start_anything_on_another_port(data_env):
    import start_local as sl
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        started = []
        orig = sl.start_portal
        sl.start_portal = lambda *a, **k: started.append(a) or orig(*a, **k)
        try:
            assert not sl.ensure_portal_status("127.0.0.1", port, health_timeout=2).ok
        finally:
            sl.start_portal = orig
        assert started == []


# ---------------------------------------------------------------- oprire curată
def test_shutdown_stops_only_our_process_and_leaves_no_zombie(data_env):
    import start_local as sl
    port = _free_port()
    st = sl.ensure_portal_status("127.0.0.1", port, health_timeout=40)
    assert st.ok and st.proc is not None
    pid = st.proc.pid
    reused = sl.ensure_portal_status("127.0.0.1", port, health_timeout=5)
    sl.stop_portal(reused.proc)
    assert st.proc.poll() is None, "o instanță reutilizată (proc=None) nu se oprește"
    sl.stop_portal(st.proc)
    assert st.proc.poll() is not None
    assert sl.port_is_free("127.0.0.1", port)
    if sys.platform.startswith("linux"):
        assert not Path(f"/proc/{pid}").exists(), "proces zombie rămas"
    sl.stop_portal(st.proc)
    sl.stop_portal(None)


@pytest.mark.skipif(os.name == "nt", reason="semnale POSIX")
def test_shutdown_kills_a_process_that_ignores_terminate(data_env):
    import start_local as sl
    code = "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);print('gata',flush=True);time.sleep(120)"
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "gata"
    t0 = time.monotonic()
    sl.stop_portal(proc)
    assert proc.poll() is not None and time.monotonic() - t0 < 20
    if sys.platform.startswith("linux"):
        assert not Path(f"/proc/{proc.pid}").exists()


# ---------------------------------------------------------------- fără internet
def test_main_works_offline_with_cloud_configured_but_unreachable(data_env):
    """Cloud configurat către o adresă inexistentă: portalul local pornește, SQLite lucrează, sincronizarea doar eșuează curat."""
    script = textwrap.dedent(f"""
        import json, sys, time
        sys.path.insert(0, {str(DESKTOP)!r}); sys.path.insert(0, {str(ROOT / 'shared')!r})
        import database as db, cloud_sync as cs
        db.init_db()
        cs.save_config("https://cimitir-offline.invalid", "k" * 24, True)
        db.set_setting("proba_locala", "ok")
        t = time.monotonic(); report = cs.sync_all(); dt = time.monotonic() - t
        print(json.dumps({{"ok": report["ok"], "seconds": dt, "local": db.get_setting("proba_locala"),
                          "pending": cs.pending_count(), "err": bool(db.get_setting(cs.SET_LAST_ERROR))}}))
    """)
    proc = subprocess.run([sys.executable, "-c", script], env={**os.environ, "CIMITIR_SECRET_STORE": "test-encrypted", "CIMITIR_ALLOW_TEST_SECRETSTORE": "1", "CIMITIR_TEST_SECRET_KEY": "rAR-xhkir67T-0hgHU_d6gxHdQt7zqO9zPP-nl-r7yw="}, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-800:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["ok"] is False and out["local"] == "ok" and out["err"] is True and out["seconds"] < 60

    import start_local as sl
    port = _free_port()
    st = sl.ensure_portal_status("127.0.0.1", port, health_timeout=40)
    try:
        assert st.ok
        assert requests.get(f"http://127.0.0.1:{port}/portal", timeout=15).status_code == 200
        assert requests.get(f"http://127.0.0.1:{port}/api/public/health", timeout=15).json()["ok"] is True
    finally:
        sl.stop_portal(st.proc)


def test_local_backend_serves_without_any_cloud_setting_and_only_on_loopback(data_env):
    import start_local as sl
    port = _free_port()
    st = sl.ensure_portal_status("127.0.0.1", port, health_timeout=40)
    try:
        assert st.ok
        lan_ip = None
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("10.255.255.255", 1))
                lan_ip = s.getsockname()[0]
        except OSError:
            pass
        if lan_ip and lan_ip != "127.0.0.1":
            with socket.socket() as probe:
                probe.settimeout(1)
                assert probe.connect_ex((lan_ip, port)) != 0, "backend-ul local nu trebuie să asculte pe interfața de rețea"
    finally:
        sl.stop_portal(st.proc)
