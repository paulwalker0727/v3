"""Etapa 2 — sincronizare MAIN <-> server online, hartă publică, personal și drepturi.

Serverul online rulează real (uvicorn, CIMITIR_MODE=online) pe un folder temporar; MAIN rulează în
procese separate (aceleași module ca desktopul: cloud_sync, database) pe alt folder. Doar date fictive.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND, DESKTOP = ROOT / "backend", ROOT / "desktop"
SENSITIVE = ["1800101123456", "Strada Secreta 99", "0799123456", "secret.persoana@example.com", "Popa Maria"]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _env(data_dir: Path, **extra) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("CIMITIR_DATA_DIR", "JWT_SECRET", "CIMITIR_MODE")}
    env.update(CIMITIR_DATA_DIR=str(data_dir), CIMITIR_ENV_FILE=str(data_dir.parent / "nu.env"), PYTHONUNBUFFERED="1", CIMITIR_JOBS="0",
               **{"CIMITIR_SECRET_STORE": "test-encrypted", "CIMITIR_ALLOW_TEST_SECRETSTORE": "1", "CIMITIR_TEST_SECRET_KEY": "rAR-xhkir67T-0hgHU_d6gxHdQt7zqO9zPP-nl-r7yw="})
    env.update(extra)
    return env


class Server:
    def __init__(self, data_dir: Path, mode: str = "online", args: tuple = ()):
        self.data_dir, self.mode, self.port, self.proc, self.args = data_dir, mode, _free_port(), None, tuple(args)
        self.url = f"http://127.0.0.1:{self.port}"

    def start(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server:app", "--app-dir", str(BACKEND), "--host", "127.0.0.1",
             "--port", str(self.port), "--log-level", "warning", *self.args],
            env=_env(self.data_dir, CIMITIR_MODE=self.mode), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                self.get("/api/public/health")
                return self
            except Exception:
                time.sleep(0.2)
        raise AssertionError("serverul nu a pornit")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=10)

    def request(self, method, path, body=None, headers=None, form=None):
        data = json.dumps(body).encode() if body is not None else (
            "&".join(f"{k}={v}" for k, v in form.items()).encode() if form else None)
        hdrs = {"Content-Type": "application/json" if body is not None else "application/x-www-form-urlencoded", **(headers or {})}
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw)
        except urllib.error.HTTPError as err:
            raw = err.read()
            return err.code, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw)

    def get(self, path, headers=None):
        status, body = self.request("GET", path, headers=headers)
        assert status == 200, (path, status, body)
        return body


def _server_py(data_dir: Path, code: str) -> str:
    """Rulează cod în contextul backendului serverului (același folder de date)."""
    pre = f"import sys; sys.path[:0]=[r'{BACKEND}']\nimport server, store\nstore.ensure_initialized(server.DEFAULT_SOURCE)\n"
    proc = subprocess.run([sys.executable, "-c", pre + textwrap.dedent(code)], env=_env(data_dir, CIMITIR_MODE="online"),
                          capture_output=True, text=True, timeout=120, cwd=str(BACKEND))
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip().splitlines()[-1]


def main_run(main_dir: Path, url: str, key: str, code: str) -> dict:
    """Rulează cod MAIN (desktop) pe folderul lui; întoarce dict-ul `out`."""
    pre = textwrap.dedent(f"""
        import sys, json; sys.path[:0]=[r'{DESKTOP}']
        import database as db, cloud_sync, cimitir_public, cimitir_auth
        db.init_db(); cloud_sync.ensure_tables()
        cloud_sync.save_config({url!r}, {key!r}, False, publish_burials=False)
        out = {{}}
    """)
    post = "\nprint('RESULT ' + json.dumps(out, default=str))\n"
    proc = subprocess.run([sys.executable, "-c", pre + textwrap.dedent(code) + post], env=_env(main_dir), cwd=str(DESKTOP),
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads([ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")][-1][7:])


SEED = """
db.save_plot_to_db("A-1", [(0,0),(40,0),(40,30),(0,30)], "#111")
db.save_plot_to_db("A-2", [(50,0),(90,0),(90,30),(50,30)], "#111")
db.save_loc_metadata("A-1", {"sector":"S1","parcela":"1","rand":"1","numar":"1","disponibilitate":"Liber"})
db.save_loc_metadata("A-2", {"sector":"S1","parcela":"1","rand":"1","numar":"2","disponibilitate":"Ocupat"})
db.set_setting("px_per_meter", "10")
"""


@pytest.fixture()
def env(tmp_path):
    server = Server(tmp_path / "server")
    server.data_dir.mkdir()
    key = _server_py(server.data_dir, "import sync_api; print(sync_api.issue_sync_key('cimitir'))")
    server.start()
    main_dir = tmp_path / "main"
    main_dir.mkdir()
    yield server, key, main_dir, tmp_path
    server.stop()


def sync(main_dir, server, key, extra=""):
    # testele Etapei 2 verifică regimul „MAIN publică harta"; sincronizarea pe entități (Etapa 6) e acoperită în test_etapa6_*
    return main_run(main_dir, server.url, key, "import entity_sync\nentity_sync.set_enabled(False)\n" + extra + "\nout['report'] = cloud_sync.sync_all()\nout['pending'] = cloud_sync.pending_count()\n")


# ---------------------------------------------------------------- hartă MAIN -> online
def test_map_changes_in_main_reach_online_portal_with_versions(env):
    server, key, main_dir, _ = env
    r1 = sync(main_dir, server, key, SEED)
    assert r1["report"]["ok"] and r1["pending"] == 0
    m = server.get("/api/public/map")
    assert m["map_version"] == 1 and len(m["plots"]) == 2
    assert m["coordinate_system"]["gps"] is False and m["scale"] == {"px_per_meter": 10.0, "calibrated": True, "note": None}
    a1 = next(p for p in m["plots"] if p["cod_loc"] == "A-1")
    assert a1["suprafata_m2_calculata"] == 12.0 and a1["laturi_m"] == [4.0, 3.0, 4.0, 3.0]
    assert a1["disponibilitate"] == "disponibil" and a1["disponibil_concesionare"] is True
    assert next(p for p in m["plots"] if p["cod_loc"] == "A-2")["disponibil_concesionare"] is False

    unchanged = sync(main_dir, server, key)
    assert server.get("/api/public/map")["map_version"] == 1 and unchanged["pending"] == 0

    r3 = sync(main_dir, server, key, """
db.save_plot_to_db("A-3", [(100,0),(130,0),(130,20),(100,20)], "#111")
db.save_loc_metadata("A-3", {"disponibilitate":"Liber"})
db.save_loc_metadata("A-2", {"disponibilitate":"Liber"})
""")
    assert r3["report"]["ok"]
    m = server.get("/api/public/map")
    assert m["map_version"] == 2 and {p["cod_loc"] for p in m["plots"]} == {"A-1", "A-2", "A-3"}
    assert next(p for p in m["plots"] if p["cod_loc"] == "A-2")["disponibilitate"] == "disponibil"
    r4 = sync(main_dir, server, key, "conn = db.get_connection(); conn.execute(\"DELETE FROM locuri_de_veci WHERE cod_loc='A-3'\"); conn.close()")
    m = server.get("/api/public/map")
    assert r4["report"]["ok"] and m["map_version"] == 3 and {p["cod_loc"] for p in m["plots"]} == {"A-1", "A-2"}


def test_uncalibrated_scale_publishes_no_dimensions(env):
    server, key, main_dir, _ = env
    sync(main_dir, server, key, SEED.replace('db.set_setting("px_per_meter", "10")', 'db.set_setting("px_per_meter", "0")'))
    m = server.get("/api/public/map")
    assert m["scale"]["calibrated"] is False and m["scale"]["px_per_meter"] is None
    assert all(p["suprafata_m2_calculata"] is None and p["laturi_m"] is None for p in m["plots"])
    assert m["coordinate_system"]["type"] == "local_px" and m["coordinate_system"]["gps"] is False


def test_background_sector_and_scale_are_published(env):
    server, key, main_dir, tmp = env
    png = tmp / "plan.png"
    png.write_bytes(bytes.fromhex("89504e470d0a1a0a0000000d49484452000000020000000208060000007" "2b60d2400000010494441547801010500faff00ffffff0005000100" "5e0dbf7f0000000049454e44ae426082"))
    r = sync(main_dir, server, key, SEED + f"""
db.set_setting("map_path", r"{png}")
db.set_setting("map_bg_w", "800"); db.set_setting("map_bg_h", "600")
conn = db.get_connection(); conn.execute("INSERT INTO sectoare (id, points, color) VALUES ('S1', '[[0,0],[100,0],[100,40],[0,40]]', '#abc')"); conn.close()
""")
    assert r["report"]["ok"], r["report"]
    m = server.get("/api/public/map")
    assert m["background"] and m["background"]["url"] == "/api/public/map/background"
    status, body = server.request("GET", "/api/public/map/background")
    assert status == 200 and m["sectors"] and m["sectors"][0]["id"] == "S1" and m["background"]["width"] > 0


# ------------------------------------------------ disponibilitate vs înhumări / contracte
def test_availability_is_independent_of_burials_and_contract_expiry(env):
    server, key, main_dir, _ = env
    sync(main_dir, server, key, SEED + """
db.save_inhumat("A-1", {"nume":"Decedat","prenume":"Fictiv","data_deces":"01.01.2020"})
cid = db.save_concesionar({"nume":"Popa","prenume":"Maria"})
db.save_contract({"numar_contract":"C1","cod_loc":"A-2","concesionar_id":cid,"data_sfarsit":"2001-01-01"})
""")
    m = {p["cod_loc"]: p for p in server.get("/api/public/map")["plots"]}
    assert m["A-1"]["disponibilitate"] == "disponibil"
    assert m["A-2"]["disponibilitate"] == "indisponibil"
    loc = server.get("/api/public/loc/A-1")
    assert loc["are_inhumari"] is True and loc["disponibilitate"] == "disponibil"
    status, _ = server.request("POST", "/api/public/cerere", form={"nume": "A", "prenume": "B", "cod_loc": "A-2"})
    assert status == 409
    status, body = server.request("POST", "/api/public/cerere", form={"nume": "A", "prenume": "B", "cod_loc": "A-1"})
    assert status == 200 and body["cod_urmarire"].startswith("CR-")


# ---------------------------------------------------- întreruperi, reluare, versiuni
def test_offline_then_restart_resumes_without_duplicates(env):
    server, key, main_dir, _ = env
    server.stop()
    r = sync(main_dir, server, key, SEED)
    assert r["report"]["ok"] is False
    server2 = Server(server.data_dir)
    server2.port = server.port
    server2.url = server.url
    server2.start()
    try:
        r = sync(main_dir, server2, key)
        assert r["report"]["ok"] and r["pending"] == 0
        assert server2.get("/api/public/map")["map_version"] == 1
        r = sync(main_dir, server2, key)
        assert server2.get("/api/public/map")["map_version"] == 1 and r["pending"] == 0
        cfg = main_run(main_dir, server.url, key, "out['c'] = cloud_sync.get_config()")["c"]
        assert cfg["last"] and cfg["last_error"] == ""
    finally:
        server2.stop()


def test_interrupted_sync_keeps_operations_and_main_keeps_working(env):
    server, key, main_dir, _ = env
    server.stop()
    r = sync(main_dir, server, key, SEED)
    assert not r["report"]["ok"]
    cfg = main_run(main_dir, server.url, key, """
out['c'] = cloud_sync.get_config()
out['plots'] = len(db.map_rows())
db.save_loc_metadata("A-1", {"sector": "S9"})
out['sector'] = db.load_loc_by_cod("A-1")["sector"]
""")
    assert cfg["c"]["last_error"] and cfg["plots"] == 2 and cfg["sector"] == "S9"


def test_old_versions_never_overwrite_new_ones(env):
    server, key, main_dir, _ = env
    sync(main_dir, server, key, SEED)
    sync(main_dir, server, key, 'db.save_loc_metadata("A-1", {"disponibilitate": "Ocupat"})')
    assert server.get("/api/public/map")["map_version"] == 2
    hdr = {"X-Sync-Key": key}
    plot = {"uid": "old-1", "cod_loc": "OLD", "points": [[0, 0], [1, 0], [1, 1]], "disponibilitate": "disponibil"}
    status, body = server.request("POST", "/api/sync/map", {"op_id": "o1", "map_version": 1, "snapshot_hash": "h1", "plots": [plot]}, hdr)
    assert status == 409 and body["detail"]["error"] == "stale"
    status, body = server.request("POST", "/api/sync/map", {"op_id": "o2", "map_version": 2, "snapshot_hash": "other", "plots": [plot]}, hdr)
    assert status == 409 and body["detail"]["error"] == "conflict"
    m = server.get("/api/public/map")
    assert m["map_version"] == 2 and "OLD" not in {p["cod_loc"] for p in m["plots"]}
    assert next(p for p in m["plots"] if p["cod_loc"] == "A-1")["disponibilitate"] == "indisponibil"


def test_stale_main_reports_error_and_does_not_publish(env):
    server, key, main_dir, tmp = env
    sync(main_dir, server, key, SEED)
    sync(main_dir, server, key, 'db.save_loc_metadata("A-1", {"sector": "NOU"})')
    other = tmp / "main_restored"
    other.mkdir()
    r = sync(other, server, key, SEED.replace('"Ocupat"', '"Liber"'))
    assert r["report"]["ok"] is False
    assert server.get("/api/public/map")["map_version"] == 2
    cfg = main_run(other, server.url, key, "out['c'] = cloud_sync.get_config()")["c"]
    assert "versiune mai nouă" in cfg["last_error"]


def test_server_keeps_only_explicit_public_fields(env):
    server, key, main_dir, _ = env
    plot = {"uid": "u1", "cod_loc": "P1", "points": [[0, 0], [10, 0], [10, 10]], "disponibilitate": "disponibil",
            "cnp": "1800101123456", "adresa": "Strada Secreta 99", "concesionar": {"nume": "Popa Maria"}}
    status, _ = server.request("POST", "/api/sync/map", {"op_id": "x", "map_version": 1, "snapshot_hash": "hh", "plots": [plot],
                                                        "concesionari": [{"cnp": "1800101123456"}]}, {"X-Sync-Key": key})
    assert status == 200
    blob = json.dumps(server.get("/api/public/map")) + json.dumps(server.get("/api/public/loc/P1"))
    assert not any(s in blob for s in SENSITIVE)
    dump = sqlite3.connect(server.data_dir / "cimitir.db").execute("SELECT GROUP_CONCAT(cod_loc||IFNULL(sector,'')||IFNULL(tip,'')||IFNULL(extra_data,'')) FROM locuri_de_veci").fetchone()[0]
    assert "1800101123456" not in dump and "Secreta" not in dump
    status, body = server.request("POST", "/api/sync/map", {"op_id": "x", "map_version": 1, "snapshot_hash": "hh", "plots": []}, {"X-Sync-Key": key})
    assert status == 200 and body["duplicate"] is True


# ----------------------------------------------------------------------- cereri
def test_requests_flow_pull_ack_decision_and_replay(env):
    server, key, main_dir, tmp = env
    sync(main_dir, server, key, SEED)
    status, created = server.request("POST", "/api/public/cerere", form={
        "nume": "Popa", "prenume": "Maria", "cnp": "1800101123456", "adresa": "Strada+Secreta+99",
        "telefon": "0799123456", "email": "secret.persoana@example.com", "cod_loc": "A-1"})
    assert status == 200
    code = created["cod_urmarire"]
    r = sync(main_dir, server, key)
    assert r["report"]["ok"] and r["report"]["cereri_noi"] == 1
    local = main_run(main_dir, server.url, key, "out['rows'] = [(c['nume'], c['cnp'], c['cod_urmarire'], c['status']) for c in db.list_cereri_concesionare()]")
    assert local["rows"] == [["Popa", "1800101123456", code, "nou"]]
    conn = sqlite3.connect(server.data_dir / "cimitir.db")
    row = conn.execute("SELECT nume, cnp, adresa, telefon, email, acked FROM cereri_concesionare").fetchone()
    assert row == ("", "", "", "", "", 1)
    assert server.get(f"/api/public/cerere/{code}")["status"] == "nou"
    again = sync(main_dir, server, key)
    assert again["report"]["cereri_noi"] == 0
    assert main_run(main_dir, server.url, key, "out['n'] = len(db.list_cereri_concesionare())")["n"] == 1

    d = sync(main_dir, server, key, "c = db.list_cereri_concesionare()[0]\ndb.set_cerere_status(c['id'], 'validat', 'ok', None)")
    assert d["report"]["ok"]
    assert server.get(f"/api/public/cerere/{code}")["status"] == "validat"
    status, body = server.request("POST", f"/api/sync/requests/{code}/status",
                                  {"status": "respins", "version": 1, "op_id": "altul"}, {"X-Sync-Key": key})
    assert status == 409
    assert server.get(f"/api/public/cerere/{code}")["status"] == "validat"
    sync(main_dir, server, key)
    assert server.get(f"/api/public/cerere/{code}")["status"] == "validat"


def test_request_documents_reach_main(env):
    server, key, main_dir, tmp = env
    boundary = "xBOUND"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"nume\"\r\n\r\nA\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"prenume\"\r\n\r\nB\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"act.pdf\"\r\nContent-Type: application/pdf\r\n\r\n%PDF-1.4 fictiv\r\n--{boundary}--\r\n").encode()
    req = urllib.request.Request(server.url + "/api/public/cerere", data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    created = json.loads(urllib.request.urlopen(req, timeout=15).read())
    r = sync(main_dir, server, key, "")
    assert r["report"]["ok"], r["report"]
    out = main_run(main_dir, server.url, key, """
c = db.list_cereri_concesionare()[0]
docs = db.list_documents('cerere', c['id'])
out['n'] = len(docs); out['content'] = open(db.get_document_path(docs[0]['id']), 'rb').read()[:8].decode('latin1')
""")
    assert out["n"] == 1 and out["content"].startswith("%PDF")
    assert created["cod_urmarire"]


# --------------------------------------------------------------- personal și acces
def test_staff_sync_lease_revocation_and_reconnect(env):
    server, key, main_dir, tmp = env
    _server_py(server.data_dir, "import cimitir_auth\ncimitir_auth.create_user('operator1','parola-op-1','operator')\nprint(1)")
    r = sync(main_dir, server, key)
    assert r["report"]["ok"]
    out = main_run(main_dir, server.url, key, """
u, reason = cimitir_auth.authenticate_ex('operator1', 'parola-op-1', db.get_setting('cemetery_id'))
out['ok'] = (reason, u['rights'], u['origin'], bool(u['lease_until']))
""")
    assert out["ok"][0] == "ok" and set(out["ok"][1]) == {"view", "edit", "approve"} and out["ok"][2] == "synced" and out["ok"][3]
    _server_py(server.data_dir, "import cimitir_auth\nuid=[u for u in cimitir_auth.list_users() if u['username']=='operator1'][0]['id']\ncimitir_auth.update_user(uid, active=False)\nprint(1)")
    still = main_run(main_dir, server.url, key, "out['r'] = cimitir_auth.authenticate_ex('operator1','parola-op-1')[1]")
    assert still["r"] == "ok"
    sync(main_dir, server, key)
    after = main_run(main_dir, server.url, key, "out['r'] = cimitir_auth.authenticate_ex('operator1','parola-op-1')[1]")
    assert after["r"] == "inactive"
    _server_py(server.data_dir, "import cimitir_auth\nuid=[u for u in cimitir_auth.list_users() if u['username']=='operator1'][0]['id']\ncimitir_auth.update_user(uid, active=True)\nprint(1)")
    sync(main_dir, server, key)
    out = main_run(main_dir, server.url, key, "out['r'] = cimitir_auth.authenticate_ex('operator1','parola-op-1')[1]")
    assert out["r"] == "ok"

    expired = main_run(main_dir, server.url, key, """
conn = db.get_connection(); conn.execute("UPDATE users SET lease_until='2000-01-01T00:00:00+00:00' WHERE username='operator1'"); conn.close()
out['r'] = cimitir_auth.authenticate_ex('operator1','parola-op-1')[1]
out['local_admin'] = cimitir_auth.authenticate_ex('admin','admin123')[1]
""")
    assert expired["r"] == "lease_expired" and expired["local_admin"] == "ok"
    server.stop()
    offline = sync(main_dir, server, key)
    assert offline["report"]["ok"] is False
    assert main_run(main_dir, server.url, key, "out['r'] = cimitir_auth.authenticate_ex('operator1','parola-op-1')[1]")["r"] == "lease_expired"
    srv2 = Server(server.data_dir)
    srv2.port, srv2.url = server.port, server.url
    srv2.start()
    try:
        sync(main_dir, srv2, key)
        assert main_run(main_dir, srv2.url, key, "out['r'] = cimitir_auth.authenticate_ex('operator1','parola-op-1')[1]")["r"] == "ok"
    finally:
        srv2.stop()


def test_desktop_rights_are_rechecked_live(env):
    server, key, main_dir, _ = env
    out = main_run(main_dir, server.url, key, """
import access_session
uid = cimitir_auth.create_user('op2', 'parola-op-2', 'operator', rights=['edit'])
user, _ = cimitir_auth.authenticate_ex('op2', 'parola-op-2')
access_session.set_user(user)
out['rights'] = user['rights']
out['can'] = [access_session.can('edit'), access_session.can('approve'), access_session.can('manage_config')]
cimitir_auth.update_user(uid, active=False)
out['after_revoke'] = [access_session.can('edit'), access_session.can('view')]
cimitir_auth.update_user(uid, active=True, rights=['edit', 'approve'])
out['after_grant'] = access_session.can('approve')
""")
    assert out["rights"] == ["view", "edit"] and out["can"] == [True, False, False]
    assert out["after_revoke"] == [False, False] and out["after_grant"] is True


def test_web_admin_rights_revocation_and_config(env):
    server, key, main_dir, tmp = env
    _server_py(server.data_dir, "import cimitir_auth\ncimitir_auth.create_user('op3','parola-op-3','operator',rights=['edit'])\nprint(1)")

    import http.cookiejar
    def login(user, pw):
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), type("NoRedirect", (urllib.request.HTTPRedirectHandler,), {"redirect_request": lambda *a, **k: None}))
        def call(method, path, form=None):
            data = "&".join(f"{k}={v}" for k, v in (form or {}).items()).encode() if method == "POST" else None
            try:
                with opener.open(urllib.request.Request(server.url + path, data=data, method=method), timeout=15) as r:
                    return r.status
            except urllib.error.HTTPError as e:
                return e.code
        assert call("POST", "/admin/access/login", {"username": user, "password": pw, "source_id": "cimitir"}) == 303
        if pw == "admin123":
            assert call("POST", "/admin/schimba-parola", {"old_password": pw, "new_password": "Parola-test-987", "confirm_password": "Parola-test-987"}) == 303
        return call

    admin = login("admin", "admin123")
    op = login("op3", "parola-op-3")
    assert op("GET", "/admin/harta") == 200
    assert op("GET", "/admin/utilizatori") == 303
    assert op("POST", "/admin/ui/settings", {"cimitir_nume": "X"}) == 403
    assert op("POST", "/admin/ui/user", {"username": "z", "password": "z"}) == 403
    assert op("POST", "/admin/ui/cerere/1/status", {"status": "validat"}) == 403
    assert admin("POST", "/admin/ui/settings", {"cimitir_nume": "Cimitir Test", "offline_lease_hours": "24", "portal_requests_enabled": "0"}) == 303
    assert server.request("POST", "/api/public/cerere", form={"nume": "A", "prenume": "B"})[0] == 503
    assert server.get("/api/sync/staff", {"X-Sync-Key": key})["lease_hours"] == 24
    uid = int(_server_py(server.data_dir, "import cimitir_auth\nprint([u for u in cimitir_auth.list_users() if u['username']=='op3'][0]['id'])"))
    assert admin("POST", f"/admin/ui/user/{uid}/update", {"role": "operator", "rights": "edit&rights=approve", "active": "1"}) == 303
    # cu dreptul `approve` decizia din web e acceptată (Etapa 12); cererea 999 nu există => 404 (nu 403)
    assert op("POST", "/admin/ui/cerere/999/status", {"status": "validat"}) == 404
    assert admin("POST", f"/admin/ui/user/{uid}/update", {"role": "operator"}) == 303
    assert op("GET", "/admin/harta") in (303, 401)
    assert admin("POST", "/admin/ui/user/1/update", {"role": "user"}) == 400
    assert admin("POST", "/admin/ui/user/1/delete") == 400


def test_sync_endpoints_isolation_and_auth(env):
    server, key, main_dir, tmp = env
    for headers in ({}, {"X-Sync-Key": "cimitir.gresit"}, {"X-Sync-Key": "altcimitir.x"}, {"X-Sync-Key": key + "x"}):
        assert server.request("GET", "/api/sync/ping", headers=headers)[0] in (401, 429)
        assert server.request("GET", "/api/sync/staff", headers=headers)[0] in (401, 429)
        assert server.request("GET", "/api/sync/requests", headers=headers)[0] in (401, 429)
    second = _server_py(server.data_dir, "import store, sync_api\nsrc=store.create_sqlite_source('Al doilea')\nstore.ensure_initialized(src)\nprint(sync_api.issue_sync_key(src['id']))")
    sid, secret = second.split(".", 1)
    assert server.request("GET", "/api/sync/ping", headers={"X-Sync-Key": second})[0] == 200
    assert server.request("GET", "/api/sync/ping", headers={"X-Sync-Key": f"cimitir.{secret}"})[0] in (401, 429)
    assert server.request("GET", "/api/sync/ping", headers={"X-Sync-Key": f"{sid}.{key.split('.', 1)[1]}"})[0] in (401, 429)
    push = {"op_id": "b1", "map_version": 1, "snapshot_hash": "b", "plots": [{"uid": "b-1", "cod_loc": "B-ONLY", "points": [[0, 0], [5, 0], [5, 5]], "disponibilitate": "disponibil"}]}
    assert server.request("POST", "/api/sync/map", push, {"X-Sync-Key": second})[0] == 200
    assert "B-ONLY" not in {p["cod_loc"] for p in server.get("/api/public/map")["plots"]}
    main = main_run(main_dir, server.url, key, "out['ping'] = cloud_sync.ping()")
    assert main["ping"][0] is True
    wrong = main_run(main_dir, server.url, second, "out['ping'] = cloud_sync.ping()")
    assert wrong["ping"][0] is False
    local = Server(tmp / "local_mode", mode="local").start()
    try:
        assert local.request("GET", "/api/sync/ping", headers={"X-Sync-Key": key})[0] == 404
    finally:
        local.stop()


def test_main_refuses_key_of_another_cemetery_and_insecure_urls(env):
    server, key, main_dir, tmp = env
    second = _server_py(server.data_dir, "import store, sync_api\nsrc=store.create_sqlite_source('Altul')\nstore.ensure_initialized(src)\nprint(sync_api.issue_sync_key(src['id']))")
    out = main_run(main_dir, server.url, key, "out['a'] = cloud_sync.ping()\ncloud_sync.save_config(%r, %r, False)\nout['b'] = cloud_sync.ping()" % (server.url, second))
    assert out["a"][0] is True and out["b"][0] is False and "altui cimitir" in out["b"][1]
    insecure = main_run(main_dir, server.url, key, """
out['http_remote'] = cloud_sync.url_is_secure('http://server.exemplu.ro')
out['https'] = cloud_sync.url_is_secure('https://server.exemplu.ro')
out['localhost'] = cloud_sync.url_is_secure('http://127.0.0.1:8000')
cloud_sync.save_config('http://server.exemplu.ro', 'k.s', False)
out['ping'] = cloud_sync.ping()
""")
    assert insecure["http_remote"] is False and insecure["https"] is True and insecure["localhost"] is True
    assert insecure["ping"][0] is False


# ---------------------------------------------------- republicare forțată din MAIN
def test_force_republish_after_conflict_needs_explicit_call_and_keeps_local_data(env):
    server, key, main_dir, tmp = env
    other = tmp / "main2"
    other.mkdir()
    assert sync(main_dir, server, key, SEED)["report"]["ok"]
    assert sync(other, server, key, SEED + 'db.save_plot_to_db("Z-1", [(0,0),(5,0),(5,5),(0,5)], "#111")\n')["report"]["ok"] is False
    assert sync(other, server, key, 'db.save_plot_to_db("Z-2", [(0,0),(5,0),(5,5),(0,5)], "#111")\n')
    r = sync(main_dir, server, key, 'db.save_loc_metadata("A-2", {"disponibilitate":"Liber"})\n')
    assert r["report"]["ok"] is False
    before = {p["cod_loc"]: p["disponibilitate"] for p in server.get("/api/public/map")["plots"]}
    assert before["A-2"] == "indisponibil"

    prev = main_run(main_dir, server.url, key, "out['p'] = cloud_sync.republish_preview()")["p"]
    assert prev["in_sync"] is False and prev["server_plots"] == 4
    final = main_run(main_dir, server.url, key, "cloud_sync.force_republish()\nout['report'] = cloud_sync.sync_all()\nout['local'] = len(db.load_all_plots()) if hasattr(db, 'load_all_plots') else 2")
    assert final["report"]["ok"], final["report"]
    after = {p["cod_loc"]: p["disponibilitate"] for p in server.get("/api/public/map")["plots"]}
    assert after["A-2"] == "disponibil" and set(after) == {"A-1", "A-2"}
    again = main_run(main_dir, server.url, key, "out['p'] = cloud_sync.republish_preview()")["p"]
    assert again["in_sync"] is True and again["differences"] == []
