"""Rulează exact pașii din GHID_WINDOWS.md (pe Linux, cu aceleași comenzi Python): modul LOCAL și modul ONLINE.

Cerere cu document depusă de un cetățean (cont obligatoriu, ca implicit) -> preluată în MAIN -> aprobată în MAIN -> rezultat în portal.
Date fictive; fiecare scenariu folosește foldere temporare.
"""
import json
import os
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import requests

DESKTOP = Path(__file__).resolve().parents[1]
ROOT = DESKTOP.parent
PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
CITIZEN = {"email": "cetatean.fictiv@example.com", "password": "Parola-cetatean-2026"}
SEED = """
db.save_plot_to_db("A-1", [(0,0),(40,0),(40,30),(0,30)], "#111")
db.save_loc_metadata("A-1", {"sector":"S1","parcela":"1","rand":"1","numar":"1","disponibilitate":"Liber"})
db.set_setting("px_per_meter", "10")
"""


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _env(data: Path, **extra) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_") and k not in ("JWT_SECRET", "PORT")}
    env.update(CIMITIR_DATA_DIR=str(data), CIMITIR_ENV_FILE=str(data.parent / "nu.env"), CIMITIR_LOG_DIR=str(data.parent / "logs"),
               PYTHONUNBUFFERED="1", QT_QPA_PLATFORM="offscreen", CIMITIR_IGNORE_PROJECT_ENV="1")
    env.update(extra)
    return env


def _wait(port: int, proc: subprocess.Popen) -> None:
    for _ in range(150):
        if proc.poll() is not None:
            break
        try:
            if requests.get(f"http://127.0.0.1:{port}/api/public/health", timeout=2).json().get("ok"):
                return
        except Exception:
            time.sleep(0.3)
    raise AssertionError("serverul nu a pornit")


def _main(data: Path, code: str) -> dict:
    """Operațiuni MAIN pe baza MAIN: aceleași module ca desktopul."""
    pre = (f"import sys, json; sys.path[:0]=[r'{DESKTOP}', r'{ROOT / 'shared'}']\n"
           "import database as db, cimitir_requests, cloud_sync\ndb.init_db()\nout = {}\n")
    proc = subprocess.run([sys.executable, "-c", pre + textwrap.dedent(code) + "\nprint('RESULT ' + json.dumps(out, default=str))"],
                          env=_env(data), cwd=str(DESKTOP), capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2500:]
    return json.loads([ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:])


def _citizen_submit(base: str, cod_loc: str) -> tuple:
    session = requests.Session()
    assert session.post(f"{base}/api/public/account/register", json=CITIZEN, timeout=15).status_code == 200
    anonymous = requests.post(f"{base}/api/public/cerere", data={"nume": "Fictiv", "prenume": "Cetatean", "cod_loc": cod_loc, "privacy_ack": "1"}, timeout=15)
    assert anonymous.status_code == 401, "cu cont obligatoriu (implicit), cererea anonimă e refuzată"
    sent = session.post(f"{base}/api/public/cerere",
                        data={"nume": "Fictiv", "prenume": "Cetatean", "cod_loc": cod_loc, "privacy_ack": "1",
                              "cnp": "1800101123456", "telefon": "0700000000"},
                        files=[("files", ("act-identitate.pdf", PDF, "application/pdf"))], timeout=30)
    assert sent.status_code == 200, sent.text
    body = sent.json()
    assert body["documente"] == 1 and body["respinse"] == []
    return session, body["cod_urmarire"]


def _status(session, base, code) -> str:
    return session.get(f"{base}/api/public/account/cereri/{code}", timeout=10).json()["status"]


def test_guide_local_mode_request_with_document_to_main_approval_to_portal(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    port = _port()
    proc = subprocess.Popen([sys.executable, str(DESKTOP / "start_local.py"), "--portal-only", "--data-dir", str(data), "--port", str(port)],
                            env=_env(data), cwd=str(DESKTOP), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        _wait(port, proc)
        for path in ("/portal", "/portal/harta", "/portal/cerere", "/portal/cont", "/admin/access", "/api/public/health"):
            assert requests.get(base + path, timeout=10).status_code == 200, path
        _main(data, SEED)
        session, code = _citizen_submit(base, "A-1")
        assert _status(session, base, code) == "nou"

        out = _main(data, """
rows = db.list_cereri_concesionare()
out['n'] = len(rows)
docs = db.list_documents("cerere", rows[0]["id"])
out['docs'] = [d["file_path"] for d in docs]
out['approved'] = cimitir_requests.approve_cerere(rows[0]["id"], actor="MAIN")
""")
        assert out["n"] == 1 and len(out["docs"]) == 1 and out["approved"]["ok"]
        stored = Path(out["docs"][0])
        assert stored.exists() and stored.read_bytes() == PDF and stored.parent == data / "media" / "cerere"
        assert _status(session, base, code) == "validat"
        assert requests.get(f"{base}/api/public/cerere/{code}", timeout=10).json()["status"] == "validat"
        assert (data / "cimitir.db").exists() and (data / ".jwt_secret").exists()
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_guide_online_mode_sync_configuration_and_full_flow(tmp_path):
    server_data, main_data = tmp_path / "server-data", tmp_path / "main-data"
    server_data.mkdir()
    main_data.mkdir()
    port = _port()
    base = f"http://127.0.0.1:{port}"
    server_env = _env(server_data, CIMITIR_MODE="online")
    key_run = subprocess.run([sys.executable, str(ROOT / "scripts" / "issue_sync_key.py"), "--source", "cimitir"], env=server_env,
                             cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    assert key_run.returncode == 0, key_run.stderr[-1500:]
    key = key_run.stdout.strip().splitlines()[-1]
    assert key.startswith("cimitir.")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "server:app", "--app-dir", str(ROOT / "backend"), "--host", "127.0.0.1",
                             "--port", str(port), "--no-proxy-headers", "--log-level", "warning"], env=server_env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _wait(port, proc)
        configure = [sys.executable, str(ROOT / "scripts" / "configure_main_sync.py"), "--data-dir", str(main_data)]
        done = subprocess.run(configure + ["--url", base, "--key", key, "--auto"], env=_env(main_data), cwd=str(ROOT),
                              capture_output=True, text=True, timeout=60)
        assert done.returncode == 0 and "Id client MAIN: main-" in done.stdout and key not in done.stdout + done.stderr
        tested = subprocess.run(configure + ["--test"], env=_env(main_data), cwd=str(ROOT), capture_output=True, text=True, timeout=60)
        assert tested.returncode == 0, tested.stdout + tested.stderr

        _main(main_data, SEED)
        first = _main(main_data, "out['r'] = cloud_sync.sync_all()")["r"]
        assert first["ok"] is True
        assert [p["cod_loc"] for p in requests.get(f"{base}/api/public/map", timeout=10).json()["plots"]] == ["A-1"]

        session, code = _citizen_submit(base, "A-1")
        pulled = _main(main_data, """
out['r'] = cloud_sync.sync_all()
rows = db.list_cereri_concesionare()
out['n'] = len(rows)
out['docs'] = [d["file_path"] for d in db.list_documents("cerere", rows[0]["id"])] if rows else []
""")
        assert pulled["r"]["ok"] and pulled["n"] == 1 and len(pulled["docs"]) == 1
        assert Path(pulled["docs"][0]).read_bytes() == PDF and Path(pulled["docs"][0]).is_relative_to(main_data / "media")
        assert _status(session, base, code) == "nou"

        approved = _main(main_data, """
rows = db.list_cereri_concesionare()
out['a'] = cimitir_requests.approve_cerere(rows[0]["id"], actor="MAIN")
out['r'] = cloud_sync.sync_all()
out['pending'] = cloud_sync.pending_count()
""")
        assert approved["a"]["ok"] and approved["r"]["ok"] and approved["pending"] == 0
        assert _status(session, base, code) == "validat"
        assert requests.get(f"{base}/api/public/cerere/{code}", timeout=10).json()["status"] == "validat"
        assert (server_data / "cimitir.db").exists() and (main_data / "cimitir.db").exists()
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_guide_reset_admin_password_script(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    script = [sys.executable, str(ROOT / "scripts" / "reset_admin_password.py"), "--data-dir", str(data)]
    listed = subprocess.run(script + ["--list"], env=_env(data), capture_output=True, text=True, timeout=60)
    assert listed.returncode == 0 and "admin" in listed.stdout and "cimitir.db" in listed.stdout
    weak = subprocess.run(script + ["--username", "admin", "--password-env", "NEW_PW"], env=_env(data, NEW_PW="admin123"),
                          capture_output=True, text=True, timeout=60)
    assert weak.returncode == 2
    reset = subprocess.run(script + ["--username", "admin", "--password-env", "NEW_PW"], env=_env(data, NEW_PW="Parola-noua-recuperata-1"),
                           capture_output=True, text=True, timeout=60)
    assert reset.returncode == 0 and "Parola-noua" not in reset.stdout + reset.stderr
    missing = subprocess.run(script + ["--username", "nimeni", "--password-env", "NEW_PW"], env=_env(data, NEW_PW="Parola-noua-recuperata-1"),
                             capture_output=True, text=True, timeout=60)
    assert missing.returncode == 3
    created = subprocess.run(script + ["--username", "nimeni", "--create-admin", "--password-env", "NEW_PW"],
                             env=_env(data, NEW_PW="Parola-noua-recuperata-2"), capture_output=True, text=True, timeout=60)
    assert created.returncode == 0

    port = _port()
    proc = subprocess.Popen([sys.executable, str(DESKTOP / "start_local.py"), "--portal-only", "--data-dir", str(data), "--port", str(port)],
                            env=_env(data), cwd=str(DESKTOP), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _wait(port, proc)
        base = f"http://127.0.0.1:{port}"
        bad = requests.post(f"{base}/admin/access/login", data={"username": "admin", "password": "admin123"}, allow_redirects=False, timeout=10)
        assert bad.status_code != 303, "parola implicită nu mai funcționează după resetare"
        good = requests.post(f"{base}/admin/access/login", data={"username": "admin", "password": "Parola-noua-recuperata-1"},
                             allow_redirects=False, timeout=10)
        assert good.status_code == 303 and "/schimba-parola" not in good.headers.get("location", "")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
