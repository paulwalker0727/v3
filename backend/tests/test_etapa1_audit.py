"""Etapa 1 — audit și stabilizare: rute, confidențialitate, roluri, config, pornire, concurență."""
from __future__ import annotations

import importlib.util
import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
DESKTOP_DIR = ROOT / "desktop"
BACKEND_DIR = ROOT / "backend"
SHARED_DIR = ROOT / "shared"

SENTINELS = {
    "cnp": "1800101123456",
    "cnp_decedat": "2500202654321",
    "adresa": "Strada Secreta 99",
    "telefon": "0799123456",
    "email": "secret.persoana@example.com",
    "bi_ci": "XZ987654",
}
PUBLIC_PREFIXES = ("/portal", "/api/public")
ADMIN_PREFIXES = ("/admin", "/api/admin")
OPEN_ADMIN_PATHS = {"/admin/access", "/admin/access/login", "/admin/logout"}
INFRA_PATHS = {"/", "/api/health", "/static", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect", "/public/loc/{cod_loc}"}  # ultima: redirect pentru QR-urile tipărite


def _clear_modules():
    for name in ["server", "store", "database", "auth", "web_public", "mongo_store",
                 "cimitir_config", "cimitir_database", "cimitir_auth", "cimitir_public", "sync_api",
                 "cimitir_notify", "cimitir_requests", "citizen_api"]:
        sys.modules.pop(name, None)


def _load_server(data_dir: Path):
    os.environ["CIMITIR_DATA_DIR"] = str(data_dir)
    _clear_modules()
    spec = importlib.util.spec_from_file_location("server_etapa1", BACKEND_DIR / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def app_env(tmp_path):
    server = _load_server(tmp_path / "data")
    import database as db  # resolved from backend/cimitir injected by server
    db.init_db()
    db.save_plot_to_db("T-01", [(0, 0), (40, 0), (40, 30), (0, 30)], "#22c55e")
    db.save_loc_metadata("T-01", {"sector": "A", "parcela": "1", "rand": "1", "numar": "1",
                                  "disponibilitate": "Liber"})
    db.save_inhumat("T-01", {"nume": "Ionescu", "prenume": "Vasile", "cnp": SENTINELS["cnp_decedat"],
                             "data_nasterii": "01.01.1940", "data_deces": "02.02.2020"})
    cid = db.save_concesionar({"nume": "Popa", "prenume": "Maria", "cnp": SENTINELS["cnp"],
                               "bi_ci": SENTINELS["bi_ci"], "adresa": SENTINELS["adresa"],
                               "telefon": SENTINELS["telefon"], "email": SENTINELS["email"]})
    db.save_contract({"numar_contract": "C-1", "cod_loc": "T-01", "concesionar_id": cid,
                      "data_sfarsit": "2099-01-01"})
    return server, TestClient(server.app), db


SEED_ADMIN_PASSWORD = os.environ.get("TEST_SEED_ADMIN_PASSWORD", "admin123")


def _login(client, username="admin", password=SEED_ADMIN_PASSWORD):
    resp = client.post("/admin/access/login", data={"username": username, "password": password,
                                                    "source_id": "cimitir"}, follow_redirects=False)
    assert resp.status_code == 303
    if resp.headers["location"] == "/admin/schimba-parola":
        ch = client.post("/admin/schimba-parola", data={"old_password": password, "new_password": "Parola-test-987",
                                                        "confirm_password": "Parola-test-987"}, follow_redirects=False)
        assert ch.status_code == 303
    return resp


def _concrete(path: str) -> str:
    out = path
    for token in ("cod_loc", "iid", "cid", "pid", "aid", "doc_id", "uid"):
        out = out.replace("{" + token + "}", "1" if token != "cod_loc" else "T-01")
    return out


# --------------------------------------------------------------------------- rute
def test_route_inventory_uses_only_clear_prefixes(app_env):
    server, _, _ = app_env
    seen = set()
    for route in server.app.routes:
        path = getattr(route, "path", "")
        for method in getattr(route, "methods", None) or {"MOUNT"}:
            assert (method, path) not in seen, f"rută duplicată: {method} {path}"
            seen.add((method, path))
        assert (path in INFRA_PATHS or path.startswith(PUBLIC_PREFIXES + ADMIN_PREFIXES + ("/static", "/api/sync"))), \
            f"rută în afara prefixelor /portal /admin /api/public /api/admin: {path}"


def test_portal_routes_do_not_collide_with_admin(app_env):
    server, _, _ = app_env
    public = {r.path for r in server.app.routes if r.path.startswith("/portal")}
    assert public == {"/portal", "/portal/harta", "/portal/cerere", "/portal/loc/{cod_loc}", "/portal/cont", "/portal/prelucrare-date"}
    assert not any(r.path.startswith("/portal") for r in server.admin.routes)


def test_admin_routes_reject_anonymous_requests(app_env):
    server, client, _ = app_env
    body = {"cod_loc": "T-01", "username": "x", "password": "x", "name": "x", "status": "validat",
            "cimitir_nume": "hack"}
    checked = 0
    for route in server.app.routes:
        path = route.path
        if not path.startswith(ADMIN_PREFIXES) or path in OPEN_ADMIN_PATHS:
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            resp = client.request(method, _concrete(path), data=body if method == "POST" else None,
                                  follow_redirects=False)
            assert resp.status_code in (401, 403, 303, 307, 422), f"{method} {path} -> {resp.status_code}"
            checked += 1
    assert checked >= 30


def test_anonymous_cannot_create_or_upload_databases(app_env):
    _, client, _ = app_env
    resp = client.post("/admin/access/create-db", data={"name": "intrus"}, follow_redirects=False)
    assert resp.status_code == 401
    resp = client.post("/admin/access/upload-db", files={"file": ("x.db", b"SQLite format 3\0")},
                       follow_redirects=False)
    assert resp.status_code == 401


def test_login_page_does_not_leak_defaults_or_counts(app_env):
    _, client, _ = app_env
    text = client.get("/admin/access").text
    assert "admin123" not in text and "user123" not in text
    assert 'value="admin"' not in text
    assert "db-stats" not in text


# --------------------------------------------------------------- confidențialitate
def test_public_surface_never_exposes_personal_data(app_env):
    _, client, _ = app_env
    post = client.post("/api/public/cerere", data={
        "nume": "Georgescu", "prenume": "Ana", "cnp": SENTINELS["cnp"] + "9",
        "bi_ci": SENTINELS["bi_ci"] + "9", "adresa": SENTINELS["adresa"] + " bis",
        "telefon": SENTINELS["telefon"] + "9", "email": "x" + SENTINELS["email"], "cod_loc": "T-01"})
    assert post.status_code == 200
    code = post.json()["cod_urmarire"]
    assert code.startswith("CR-")
    urls = ["/portal", "/portal/harta", "/portal/cerere", "/portal/loc/T-01", "/api/public/health",
            "/api/public/map", "/api/public/loc/T-01", "/api/public/search", "/api/public/search?q=Ionescu",
            "/api/public/search?q=Popa", "/api/public/search?q=" + SENTINELS["cnp"],
            f"/api/public/cerere/{code}"]
    for url in urls:
        resp = client.get(url)
        assert resp.status_code == 200, url
        blob = resp.text + str(dict(resp.headers))
        for label, value in SENTINELS.items():
            assert value not in blob, f"{label} apare în {url}"
        assert "Popa" not in blob and "Georgescu" not in blob, f"nume concesionar/solicitant în {url}"
    post_blob = post.text
    for value in SENTINELS.values():
        assert value not in post_blob


def test_public_tracking_returns_only_status_fields(app_env):
    _, client, db = app_env
    created = client.post("/api/public/cerere", data={"nume": "A", "prenume": "B"}).json()
    status = client.get(f"/api/public/cerere/{created['cod_urmarire']}").json()
    assert set(status) == {"cod_urmarire", "status", "status_label", "depusa_la", "actualizata_la"}
    assert status["status"] == "nou"
    assert client.get("/api/public/cerere/CR-NEEXISTENT").status_code == 404
    db.set_cerere_status(created["id"], "validat", "ok", None)
    assert client.get(f"/api/public/cerere/{created['cod_urmarire']}").json()["status"] == "validat"


def test_public_write_only_touches_cereri_and_documents(app_env):
    _, client, db = app_env
    conn = db.get_connection()
    before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("locuri_de_veci", "concesionari", "contracte", "inhumati", "users", "plati")}
    conn.close()
    client.post("/api/public/cerere", data={"nume": "A", "prenume": "B", "cod_loc": "T-01"},
                files=[("files", ("a.pdf", b"%PDF-1.4 test", "application/pdf"))])
    conn = db.get_connection()
    after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in before}
    conn.close()
    assert before == after


def test_public_limits_are_enforced(app_env):
    _, client, _ = app_env
    assert client.get("/api/public/search?limit=100000").status_code == 422
    resp = client.post("/api/public/cerere", data={"nume": "A", "prenume": "B", "observatii": "x" * 5000})
    assert resp.status_code == 413


# ---------------------------------------------------------------------- roluri
def test_readonly_role_cannot_mutate_operator_can(app_env):
    _, client, db = app_env
    import auth
    auth.create_user("citeste", "parola-lunga-1", "user")
    auth.create_user("opereaza", "parola-lunga-2", "operator")
    _login(client, "citeste", "parola-lunga-1")
    assert client.get("/admin/").status_code == 200
    resp = client.post("/admin/ui/loc/T-01/edit", data={"sector": "Z"})
    assert resp.status_code == 403
    assert client.post("/admin/ui/contract/1/delete", data={"cod_loc": "T-01"}).status_code == 403
    assert db.load_loc_by_cod("T-01")["sector"] == "A"
    client.cookies.clear()
    _login(client, "opereaza", "parola-lunga-2")
    assert client.post("/admin/ui/loc/T-01/edit", data={"sector": "B"}).status_code == 200
    assert db.load_loc_by_cod("T-01")["sector"] == "B"
    assert client.post("/admin/ui/user", data={"username": "n", "password": "p"},
                       follow_redirects=False).status_code == 403


def test_web_request_decisions_require_approve_right_and_valid_status(app_env):
    """Politică actualizată (Etapa 12): aprobarea/respingerea din web e permisă cu dreptul `approve` (admin/operator)."""
    _, client, _ = app_env
    created = client.post("/api/public/cerere", data={"nume": "A", "prenume": "B"}).json()
    assert client.post(f"/admin/ui/cerere/{created['id']}/status", data={"status": "respins"}, follow_redirects=False).status_code in (401, 403)  # anonim
    _login(client)
    assert client.post(f"/admin/ui/cerere/{created['id']}/status", data={"status": "hacked"}, follow_redirects=False).status_code == 422
    assert client.post("/admin/ui/cerere/9999/status", data={"status": "respins"}, follow_redirects=False).status_code == 404
    assert client.get("/admin/cereri").status_code == 200
    assert client.get("/api/public/cerere/" + created["cod_urmarire"]).json()["status"] == "nou"
    ok = client.post(f"/admin/ui/cerere/{created['id']}/status", data={"status": "respins", "note": "Incomplet"}, follow_redirects=False)
    assert ok.status_code == 303
    assert client.get("/api/public/cerere/" + created["cod_urmarire"]).json()["status"] == "respins"
    # după decizie nu se poate aproba
    assert client.post(f"/admin/ui/cerere/{created['id']}/status", data={"status": "validat"}, follow_redirects=False).status_code == 409


def test_admin_pages_render_with_admin_prefix(app_env):
    _, client, _ = app_env
    _login(client)
    for page in ("", "harta", "locuri", "inhumati", "contracte", "concesionari", "plati", "cereri",
                 "rapoarte", "utilizatori", "setari"):
        resp = client.get(f"/admin/{page}")
        assert resp.status_code == 200, page
        assert 'href="/admin/harta"' in resp.text
    assert client.get("/api/admin/map.json").json()["plots"]
    drawer = client.get("/admin/ui/loc/T-01")
    assert drawer.status_code == 200 and 'hx-post="/admin/ui/loc/T-01/edit"' in drawer.text


# ----------------------------------------------------------------- config / DB
def _run_py(code: str, env_extra: dict, cwd: Path) -> str:
    env = {k: v for k, v in os.environ.items() if k not in ("CIMITIR_DATA_DIR", "CIMITIR_ENV_FILE", "JWT_SECRET")}
    env.update(env_extra)
    proc = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=str(cwd), env=env,
                          capture_output=True, text=True, check=False, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip().splitlines()[-1]


def test_all_components_open_the_same_db_file(tmp_path):
    data = tmp_path / "data-x"
    code_tpl = """
        import sys; sys.path.insert(0, r"{path}")
        import database; print(database.DB_PATH)
    """
    paths = {
        "desktop": _run_py(code_tpl.format(path=DESKTOP_DIR), {"CIMITIR_DATA_DIR": str(data)}, tmp_path),
        "backend": _run_py(code_tpl.format(path=BACKEND_DIR / "cimitir"), {"CIMITIR_DATA_DIR": str(data)}, tmp_path),
        "shared": _run_py(code_tpl.format(path=SHARED_DIR).replace("import database; print(database.DB_PATH)",
                                                                   "import cimitir_database as d; print(d.DB_PATH)"),
                          {"CIMITIR_DATA_DIR": str(data)}, tmp_path),
    }
    assert len(set(paths.values())) == 1, paths
    assert Path(paths["desktop"]) == (data / "cimitir.db").resolve()


def test_default_db_path_is_identical_without_env(tmp_path):
    code = """
        import sys; sys.path.insert(0, r"{p}")
        import database; print(database.DB_PATH)
    """
    home = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path), "CIMITIR_ENV_FILE": str(tmp_path / "nu-exista.env")}
    a = _run_py(code.format(p=DESKTOP_DIR), home, DESKTOP_DIR)
    b = _run_py(code.format(p=BACKEND_DIR / "cimitir"), home, BACKEND_DIR)
    assert a == b
    assert Path(a) == ROOT / "shared_data" / "cimitir.db"


def test_env_file_is_read_without_overriding_real_environment(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text('CIMITIR_DATA_DIR="%s"\n# comentariu\nJWT_SECRET=din-fisier\n' % (tmp_path / "din-env"),
                        encoding="utf-8")
    code = f"""
        import sys; sys.path.insert(0, r"{SHARED_DIR}")
        import cimitir_config as c, os
        print(c.data_dir(), os.environ['JWT_SECRET'])
    """
    out = _run_py(code, {"CIMITIR_ENV_FILE": str(env_file)}, tmp_path)
    assert out.split()[0] == str((tmp_path / "din-env").resolve()) and out.split()[1] == "din-fisier"
    out = _run_py(code, {"CIMITIR_ENV_FILE": str(env_file), "JWT_SECRET": "din-mediu"}, tmp_path)
    assert out.split()[1] == "din-mediu"


def test_jwt_secret_is_stable_across_restarts(tmp_path):
    code = f"""
        import sys; sys.path.insert(0, r"{SHARED_DIR}")
        import cimitir_config as c
        print(c.jwt_secret())
    """
    env = {"CIMITIR_DATA_DIR": str(tmp_path / "d"), "CIMITIR_ENV_FILE": str(tmp_path / "nu.env")}
    assert _run_py(code, env, tmp_path) == _run_py(code, env, tmp_path)


def test_startup_logs_absolute_db_path(tmp_path):
    code = f"""
        import sys; sys.path.insert(0, r"{SHARED_DIR}")
        import cimitir_config as c
        c.log_startup("test")
    """
    out = _run_py(code, {"CIMITIR_DATA_DIR": str(tmp_path / "log")}, tmp_path)
    assert out == f"[config] test: db={(tmp_path / 'log' / 'cimitir.db').resolve()}"


def test_cloud_folder_warning():
    sys.path.insert(0, str(SHARED_DIR))
    import cimitir_config
    assert cimitir_config.sync_folder_warning(r"C:\Users\x\OneDrive\Cimitir")
    assert not cimitir_config.sync_folder_warning(r"C:\Cimitir\data")


def test_concurrent_processes_write_same_db_without_loss(tmp_path):
    data = tmp_path / "conc"
    worker = f"""
        import sys; sys.path.insert(0, r"{SHARED_DIR}")
        import cimitir_database as d
        d.init_db()
        bad = 0
        for i in range(25):
            if not d.add_cerere_concesionare({{"nume": "N", "prenume": sys.argv[1] + str(i)}}):
                bad += 1
            d.count_cereri_noi()
        print(bad)
    """
    env = {**{k: v for k, v in os.environ.items() if k != "JWT_SECRET"}, "CIMITIR_DATA_DIR": str(data), "CIMITIR_ENV_FILE": str(tmp_path / "nu.env")}
    procs = [subprocess.Popen([sys.executable, "-c", textwrap.dedent(worker), f"w{n}_"], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for n in range(4)]
    results = [p.communicate(timeout=120) for p in procs]
    assert all(p.returncode == 0 for p in procs), [r[1] for r in results]
    assert [r[0].strip() for r in results] == ["0"] * 4
    import sqlite3
    conn = sqlite3.connect(data / "cimitir.db")
    total, distinct = conn.execute("SELECT COUNT(*), COUNT(DISTINCT cod_urmarire) FROM cereri_concesionare").fetchone()
    assert total == 100 and distinct == 100
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


# --------------------------------------------------------------------- pornire
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_local():
    spec = importlib.util.spec_from_file_location("start_local_under_test", DESKTOP_DIR / "start_local.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_start_local_command_is_safe_uvicorn_invocation():
    sl = _start_local()
    cmd = sl.portal_command("127.0.0.1", 8765)
    assert cmd[0] == sys.executable and cmd[1:3] == ["-m", "uvicorn"] and "server:app" in cmd
    assert "--host" in cmd and "127.0.0.1" in cmd
    source = (DESKTOP_DIR / "start_local.py").read_text(encoding="utf-8")
    assert "shell=True" not in source


def test_start_local_portal_passes_health_check_on_chosen_db(tmp_path):
    sl = _start_local()
    port = _free_port()
    data = tmp_path / "start"
    data.mkdir()
    env = sl.child_environment(data, "127.0.0.1", port)
    proc = sl.start_portal(env, "127.0.0.1", port, data / "logs" / "portal.log")
    try:
        assert sl.wait_for_health("127.0.0.1", port, 40, proc), sl._tail(data / "logs" / "portal.log")
        assert (data / "cimitir.db").exists()
        assert f"db={(data / 'cimitir.db').resolve()}" in (data / "logs" / "portal.log").read_text(encoding="utf-8")
        import urllib.request
        assert urllib.request.urlopen(f"http://127.0.0.1:{port}/portal", timeout=5).status == 200
    finally:
        sl.stop_portal(proc)


def test_start_local_reports_busy_port_and_dead_portal(tmp_path):
    sl = _start_local()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        busy = s.getsockname()[1]
        assert not sl.port_is_free("127.0.0.1", busy)
        proc = subprocess.run([sys.executable, str(DESKTOP_DIR / "start_local.py"), "--portal-only",
                               "--data-dir", str(tmp_path / "b"), "--port", str(busy)],
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 3 and "ocupat" in proc.stdout
    dead = subprocess.Popen([sys.executable, "-c", "raise SystemExit(1)"])
    dead.wait()
    assert sl.wait_for_health("127.0.0.1", _free_port(), 5, dead) is False


# ------------------------------------------------------------ copie de lucru DB
def _load_copy_script():
    spec = importlib.util.spec_from_file_location("copy_db_for_testing", ROOT / "scripts" / "copy_db_for_testing.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_copy_for_testing_leaves_original_untouched_and_protects_media(tmp_path):
    import hashlib
    import sqlite3
    original = tmp_path / "real"
    (original / "media" / "loc").mkdir(parents=True)
    media_file = original / "media" / "loc" / "doc.txt"
    media_file.write_text("document real", encoding="utf-8")
    conn = sqlite3.connect(original / "cimitir.db")
    conn.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, file_path TEXT)")
    conn.execute("INSERT INTO documents (file_path) VALUES (?)", (str(media_file.resolve()),))
    conn.commit()
    conn.close()
    digest = hashlib.sha256((original / "cimitir.db").read_bytes()).hexdigest()

    script = _load_copy_script()
    info = script.copy_for_testing(original, tmp_path / "test-copy")
    assert hashlib.sha256((original / "cimitir.db").read_bytes()).hexdigest() == digest
    assert info["rewritten"] == 1
    copy_conn = sqlite3.connect(tmp_path / "test-copy" / "cimitir.db")
    new_path = copy_conn.execute("SELECT file_path FROM documents").fetchone()[0]
    copy_conn.close()
    assert new_path.startswith(str((tmp_path / "test-copy" / "media").resolve()))
    assert Path(new_path).read_text(encoding="utf-8") == "document real"
    with pytest.raises(SystemExit):
        script.copy_for_testing(original, tmp_path / "test-copy")
    with pytest.raises(SystemExit):
        script.copy_for_testing(original, original)


def test_delete_document_never_removes_files_outside_managed_media(app_env, tmp_path):
    _, _, db = app_env
    outside = tmp_path / "originalul-meu.txt"
    outside.write_text("nu sterge", encoding="utf-8")
    doc_id = db.add_document("loc", "T-01", "poza_loc", str(outside), copy=False)
    assert doc_id
    assert db.delete_document(doc_id) is True
    assert outside.exists()
    managed = tmp_path / "temp-upload.txt"
    managed.write_text("copie gestionata", encoding="utf-8")
    doc_id = db.add_document("loc", "T-01", "poza_loc", str(managed))
    stored = db.get_document_path(doc_id)
    assert Path(stored).exists()
    db.delete_document(doc_id)
    assert not Path(stored).exists()
