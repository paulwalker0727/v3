"""Pornire unificată + modul LOCAL (desktop și portal pe aceeași bază SQLite).

Portalul rulează real (subproces uvicorn, CIMITIR_MODE implicit = local); MAIN este simulat prin aceleași
module ca desktopul (database, cimitir_requests) pe același folder de date. Doar date fictive.
"""
import json
import os
import re
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


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _env(data_dir: Path, **extra) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("CIMITIR_DATA_DIR", "JWT_SECRET", "CIMITIR_MODE", "PORT")}
    env.update(CIMITIR_DATA_DIR=str(data_dir), CIMITIR_ENV_FILE=str(data_dir.parent / "nu.env"), PYTHONUNBUFFERED="1",
               QT_QPA_PLATFORM="offscreen", CIMITIR_IGNORE_PROJECT_ENV="1", CIMITIR_PORTAL_REQUIRE_ACCOUNT="0", CIMITIR_LOG_DIR=str(data_dir.parent / "logs"))
    env.update(extra)
    return env


def _wait_health(port: int, proc: subprocess.Popen, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline and proc.poll() is None:
        try:
            if requests.get(f"http://127.0.0.1:{port}/api/public/health", timeout=2).json().get("ok"):
                return True
        except Exception:
            time.sleep(0.3)
    return False


def _desktop(data_dir: Path, code: str) -> dict:
    pre = f"import sys, json; sys.path[:0]=[r'{DESKTOP}']\nimport database as db, cimitir_requests, cimitir_auth\ndb.init_db()\nout = {{}}\n"
    proc = subprocess.run([sys.executable, "-c", pre + textwrap.dedent(code) + "\nprint('RESULT ' + json.dumps(out, default=str))"],
                          env=_env(data_dir), cwd=str(DESKTOP), capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2500:]
    return json.loads([ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:])


@pytest.fixture()
def portal_local(tmp_path):
    """Portalul pornit exact ca în utilizarea reală: `start_local.py --portal-only`."""
    data = tmp_path / "data"
    data.mkdir()
    port = _free_port()
    proc = subprocess.Popen([sys.executable, str(DESKTOP / "start_local.py"), "--portal-only", "--data-dir", str(data),
                             "--port", str(port)], env=_env(data), cwd=str(DESKTOP),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert _wait_health(port, proc), "portalul nu a pornit"
    yield data, f"http://127.0.0.1:{port}", proc
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


# ---------------------------------------------------------------- referințe și structură
def test_duplicate_portal_removed_and_nothing_references_it():
    assert not (DESKTOP / "web_public.py").exists()
    pattern = re.compile(r"^\s*(import|from)\s+web_public\b", re.M)
    for path in list(DESKTOP.rglob("*.py")) + [DESKTOP / "cimitir.spec"]:
        if "tests" in path.parts and path.name == "test_startup_unification.py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert not pattern.search(text), f"{path} încă importă desktop/web_public"


def test_launcher_starts_the_unified_portal_not_the_legacy_one():
    text = (DESKTOP / "launcher.py").read_text(encoding="utf-8")
    assert "ensure_portal" in text and "--portal" in text
    assert "web_server.app" in text and "MOBILE_SCAN_PORT_OFFSET" in text
    server = (DESKTOP / "web_server.py").read_text(encoding="utf-8")
    assert "web_public" not in server.replace("Portalul public", "")


def test_portal_command_dev_vs_frozen(monkeypatch):
    import start_local
    dev = start_local.portal_command("127.0.0.1", 8000)
    assert dev[:3] == [sys.executable, "-m", "uvicorn"] and "server:app" in dev
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", "/opt/cimitir/_internal", raising=False)
    assert start_local.portal_command("0.0.0.0", 8123) == [sys.executable, "--portal", "--host", "0.0.0.0", "--port", "8123"]
    assert start_local.backend_dir() == Path("/opt/cimitir/_internal") / "portal"


def test_spec_packages_the_portal_and_installer_ships_the_whole_folder():
    from tests.test_build_packaging import _eval_spec
    kw = _eval_spec("0", "0")["analysis_kwargs"]
    dests = {d[1] for d in kw["datas"]}
    assert {"portal/templates", "portal/static", "portal/fonts", "portal/doc_templates"} <= dests
    for src, _ in kw["datas"]:
        assert os.path.exists(src)
    assert {"server", "store", "sync_api", "citizen_api", "web_public", "start_local", "jinja2"} <= set(kw["hiddenimports"])
    assert any(str(p).endswith("backend") for p in kw["pathex"])
    iss = (DESKTOP / "installer" / "cimitir_setup.iss").read_text(encoding="utf-8")
    assert 'Source: "{#SourceDir}\\*"' in iss and "recursesubdirs" in iss


def test_launcher_portal_flag_serves_the_same_portal(tmp_path):
    """`launcher.py --portal` este calea pe care o folosește Cimitir.exe înghețat."""
    data = tmp_path / "data"
    data.mkdir()
    port = _free_port()
    proc = subprocess.Popen([sys.executable, str(DESKTOP / "launcher.py"), "--portal", "--host", "127.0.0.1", "--port", str(port)],
                            env=_env(data), cwd=str(DESKTOP), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert _wait_health(port, proc)
        for path in ("/portal", "/admin/access", "/portal/harta"):
            assert requests.get(f"http://127.0.0.1:{port}{path}", timeout=10).status_code == 200
        assert (data / "cimitir.db").exists()
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_ensure_portal_starts_reuses_and_reports_busy_port(tmp_path, monkeypatch):
    import start_local
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("CIMITIR_DATA_DIR", str(data))
    monkeypatch.setenv("CIMITIR_ENV_FILE", str(tmp_path / "nu.env"))
    port = _free_port()
    proc, message, ok = start_local.ensure_portal("127.0.0.1", port, health_timeout=40)
    try:
        assert ok and proc is not None and str(port) in message
        again, message2, ok2 = start_local.ensure_portal("127.0.0.1", port, health_timeout=5)
        assert ok2 and again is None and "deja pornit" in message2
    finally:
        start_local.stop_portal(proc)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        _, msg3, ok3 = start_local.ensure_portal("127.0.0.1", s.getsockname()[1], health_timeout=2)
        assert not ok3 and "ocupat" in msg3


# ---------------------------------------------------------------- modul LOCAL: aceeași bază SQLite
def test_local_mode_desktop_and_portal_share_one_sqlite_database(portal_local):
    data, url, _ = portal_local
    assert requests.get(f"{url}/api/sync/ping", timeout=10).status_code == 404, "API-ul de sincronizare e dezactivat în modul local"

    _desktop(data, """
db.save_plot_to_db("L-1", [(0,0),(40,0),(40,30),(0,30)], "#111")
db.save_loc_metadata("L-1", {"sector":"S1","parcela":"1","rand":"1","numar":"1","disponibilitate":"Liber"})
db.set_setting("px_per_meter", "10")
""")
    plots = requests.get(f"{url}/api/public/map", timeout=10).json()["plots"]
    assert [p["cod_loc"] for p in plots] == ["L-1"] and plots[0]["disponibil_concesionare"] is True

    sent = requests.post(f"{url}/api/public/cerere", data={"nume": "Fictiv", "prenume": "Test", "cod_loc": "L-1", "privacy_ack": "1",
                                                           "email": "fictiv@example.com", "cnp": "1800101123456"}, timeout=15)
    assert sent.status_code == 200, sent.text
    code = sent.json()["cod_urmarire"]
    assert requests.get(f"{url}/api/public/cerere/{code}", timeout=10).json()["status"] == "nou"

    out = _desktop(data, """
rows = db.list_cereri_concesionare()
out['n'] = len(rows); out['code'] = rows[0]['cod_urmarire']
out['approved'] = cimitir_requests.approve_cerere(rows[0]['id'])['already']
""")
    assert out["n"] == 1 and out["code"] == code and out["approved"] is False

    # rezultatul din MAIN e vizibil imediat în portal (aceeași bază), fără sincronizare
    assert requests.get(f"{url}/api/public/cerere/{code}", timeout=10).json()["status"] == "validat"
    assert requests.get(f"{url}/api/public/map", timeout=10).json()["plots"][0]["disponibilitate"] == "rezervat"


def test_local_mode_web_admin_cannot_approve_or_create_contracts(portal_local):
    data, url, _ = portal_local
    sent = requests.post(f"{url}/api/public/cerere", data={"nume": "A", "prenume": "B", "privacy_ack": "1"}, timeout=15).json()
    session = requests.Session()
    session.post(f"{url}/admin/access/login", data={"username": "admin", "password": "admin123"}, allow_redirects=False, timeout=10)
    r = session.post(f"{url}/admin/schimba-parola", data={"old_password": "admin123", "new_password": "Parola-test-987",
                                                           "confirm_password": "Parola-test-987"}, allow_redirects=False, timeout=10)
    assert r.status_code == 303
    cid = _desktop(data, "out['id'] = db.list_cereri_concesionare()[0]['id']")["id"]
    for status in ("validat", "respins"):
        resp = session.post(f"{url}/admin/ui/cerere/{cid}/status", data={"status": status}, allow_redirects=False, timeout=10)
        assert resp.status_code == 403 and resp.json()["detail"]["error"] == "main_only"
    assert session.post(f"{url}/admin/ui/loc/L-1/contract", data={"numar_contract": "X"}, allow_redirects=False, timeout=10).status_code == 403
    page = session.get(f"{url}/admin/cereri", timeout=10).text
    assert f'data-testid="cerere-main-only-{cid}"' in page and "cerere-approve-" not in page and "cerere-reject-" not in page
    assert requests.get(f"{url}/api/public/cerere/{sent['cod_urmarire']}", timeout=10).json()["status"] == "nou"


def test_web_server_keeps_only_phone_scanning_and_react_is_gone():
    import web_server
    paths = {r.path for r in web_server.app.routes} - {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    assert paths == {"/mobile-scan", "/api/ai-scan-upload", "/api/ai-scan-result/{session}"}
    assert not (DESKTOP / "web_build").exists()
    text = (DESKTOP / "web_server.py").read_text(encoding="utf-8")
    assert "get_current_user" not in text and "StaticFiles" not in text and "/auth/login" not in text
    assert "web_build" not in (DESKTOP / "cimitir.spec").read_text(encoding="utf-8")
    assert "verify_install.ps1" in (DESKTOP / "installer" / "cimitir_setup.iss").read_text(encoding="utf-8")


def test_portal_ignores_forwarded_headers_unless_behind_proxy(monkeypatch):
    import start_local
    monkeypatch.delenv("CIMITIR_BEHIND_PROXY", raising=False)
    assert "--no-proxy-headers" in start_local.portal_command("127.0.0.1", 8000)
    monkeypatch.setenv("CIMITIR_BEHIND_PROXY", "1")
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "10.1.1.1")
    cmd = start_local.portal_command("127.0.0.1", 8000)
    assert "--proxy-headers" in cmd and cmd[cmd.index("--forwarded-allow-ips") + 1] == "10.1.1.1"
