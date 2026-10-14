"""Cloud deployment readiness: aplicația cloud (cimitir_cloud_app) fără MAIN, fără PostgreSQL real (servicii in-memory cu aceleași validări).
Acoperă: health, pagina Phone Capture, pairing, create item, upload, CORS, trusted proxy, fără scurgeri de secrete, MAIN absent, pornire „curată” ca în container."""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

import pytest
from starlette.testclient import TestClient

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "desktop" / "tests"))
import phone_capture_fakes as pcf  # noqa: E402

import cimitir_cloud_app as cca  # noqa: E402
import cimitir_cloud_control_pg as ccp  # noqa: E402
import cimitir_phone_pairing as pp  # noqa: E402
import cimitir_phone_pairing_http as pph  # noqa: E402

SENTINELS = {"CIMITIR_CP_DATABASE_URL": "postgresql://cimitir_cp_app.abcdefghijklmnopqrst:PGSENTINELPW@db.invalid:5432/postgres",
             "SUPABASE_SECRET_KEY": "sb_secret_SENTINEL_9f3", "SUPABASE_SERVICE_ROLE_KEY": "service_role_SENTINEL",
             "CIMITIR_CP_CLEANUP_DATABASE_URL": "postgresql://cleanup:CLEANSENTINEL@db.invalid/postgres", "CIMITIR_CP_ADMIN_DATABASE_URL": "postgresql://admin:ADMINSENTINEL@db.invalid/postgres"}
LEAK_MARKERS = ("PGSENTINELPW", "sb_secret_SENTINEL", "service_role_SENTINEL", "CLEANSENTINEL", "ADMINSENTINEL")
PHONE_ORIGIN = "https://portal.exemplu.ro"
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64
GRAVE = str(uuid.uuid4())


class RecordingPairing(pcf.PhonePairing):
    sources: list = []

    def redeem(self, code, device_name=None, client_id=None, source=""):
        self.sources.append(source)
        return super().redeem(code, device_name, client_id, source)


class World:
    def __init__(self, origins=(), db=True, storage=True):
        self.inbox = pcf.PhoneInbox()
        self.pairing = RecordingPairing(self.inbox)
        self.pairing.sources = []
        self.app = cca.create_app(self.inbox, list(origins), self.pairing, lambda _s: db, lambda _s: storage)

    def client(self, client=None):
        if not client:
            return TestClient(self.app)

        async def with_peer(scope, receive, send):
            await self.app({**scope, "client": client} if scope["type"] == "http" else scope, receive, send)
        return TestClient(with_peer)

    def pair(self, c=None):
        c = c or self.client()
        r = c.post("/api/v1/phone-pairing/redeem", json={"code": self.pairing.new_code(), "device_name": "Telefon"})
        assert r.status_code == 201, r.text
        return r.json()["result"]["binding_key"]


@pytest.fixture(autouse=True)
def secrets_env(monkeypatch):
    for k, v in SENTINELS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv(pph.ENV_TRUSTED, raising=False)
    monkeypatch.delenv("CIMITIR_FIELD_INBOX_CORS_ORIGINS", raising=False)
    monkeypatch.setattr(pph, "_warned_invalid", False)


@pytest.fixture()
def w():
    return World()


def auth(key):
    return {"Authorization": f"Binding {key}"}


def item_body(**kw):
    return {"idempotency_key": "tc-" + uuid.uuid4().hex, "item_type": "GRAVE_PHOTO", "payload": {"caption": "intrare"}, "grave_uid": GRAVE, **kw}


def no_leak(*texts):
    blob = "\n".join(texts)
    for m in LEAK_MARKERS:
        assert m not in blob


# ------------------------------------------------------------------ health
def test_liveness_and_readiness_ok(w):
    c = w.client()
    r = c.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True, "api": "up"}
    r = c.get("/api/v1/health")
    assert r.status_code == 200 and r.json() == {"ok": True, "api": "up", "database": "up", "storage": "configured"}
    assert r.headers["cache-control"] == "no-store"
    no_leak(r.text)


@pytest.mark.parametrize("db,storage,dbs,sts", [(False, True, "down", "configured"), (True, False, "up", "not_configured"), (False, False, "down", "not_configured")])
def test_readiness_degraded_is_503_without_secrets(db, storage, dbs, sts):
    r = World(db=db, storage=storage).client().get("/api/v1/health")
    assert r.status_code == 503 and r.json() == {"ok": False, "api": "up", "database": dbs, "storage": sts}
    no_leak(r.text)


def test_liveness_survives_db_down():
    assert World(db=False).client().get("/healthz").status_code == 200


def test_real_db_probe_unreachable_is_down_and_does_not_leak(caplog):
    class Cp:
        url = "postgresql://cimitir_cp_app:PGSENTINELPW@127.0.0.1:1/postgres"

    class Svc:
        cp = Cp()
    caplog.set_level("DEBUG")
    assert cca.probe_database(Svc()) is False
    assert "PGSENTINELPW" not in caplog.text and "127.0.0.1" not in caplog.text


def test_storage_probe_only_reports_configuration():
    import cimitir_storage as st

    class M:
        storage = st.SupabaseStorage("https://abcdefghijklmnopqrst.supabase.co", "sb_secret_SENTINEL_9f3", "bucket")

    class Svc:
        media = M()
    assert cca.probe_storage(Svc()) is True
    assert cca.probe_storage(object()) is False

    class Local:
        media = type("M", (), {"storage": st.LocalStorage})()
    assert cca.probe_storage(Local()) is False


def test_docs_and_openapi_disabled(w):
    c = w.client()
    for p in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(p).status_code == 404


# ------------------------------------------------------------------ templates / static / Phone Capture page
def test_phone_page_loads_standalone_without_portal_login(w):
    r = w.client().get("/phone")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    for t in ("teren-page", "teren-pair", "teren-pair-code", "tile-deceased", "tile-work", "tile-permit", "tile-document", "tile-grave-photo", "tile-work-photo",
              "teren-send-btn", "teren-grave-uid-input", "f-executant", "f-data-inceput", "f-stage"):
        assert f'data-testid="{t}"' in r.text
    assert "/static/teren.js" in r.text and "/static/teren_ui.js" in r.text and "/static/teren.css" in r.text
    assert "teren-back-to-map" not in r.text and "/admin/" not in r.text
    assert "carte_identitate" not in r.text and "alt_document" in r.text
    h = r.headers
    assert h["cache-control"] == "no-store" and h["x-frame-options"] == "DENY" and h["referrer-policy"] == "no-referrer"
    csp = h["content-security-policy"]
    assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp and "frame-ancestors 'none'" in csp and "connect-src 'self'" in csp
    assert "style=" not in r.text and "<script>" not in r.text
    no_leak(r.text)


def test_static_assets_available_and_whitelisted(w):
    c = w.client()
    for name, ct in (("teren.js", "javascript"), ("teren_ui.js", "javascript"), ("teren.css", "text/css")):
        r = c.get(f"/static/{name}")
        assert r.status_code == 200 and ct in r.headers["content-type"] and r.headers["x-content-type-options"] == "nosniff"
        no_leak(r.text)
        assert "console." not in r.text
    for name in ("app.js", "app.css", "..%2Fserver.py", "%2e%2e/server.py", "teren.js.map"):
        assert c.get(f"/static/{name}").status_code == 404


def test_page_config_is_json_and_deeplink_validated(w):
    c = w.client()
    g, t = str(uuid.uuid4()), str(uuid.uuid4())

    def cfg(q=""):
        return json.loads(re.search(r'<script type="application/json" id="teren-cfg">(.*?)</script>', c.get("/phone" + q).text, re.S).group(1))

    base = cfg()
    assert base["standalone"] is True and base["loc"] is None and base["cemetery_uid"] == ""
    loc = cfg(f"?cod_loc=A-1-10&grave_uid={g.upper()}&target_uid={t}&target_label=Soclu")["loc"]
    assert loc == {"cod_loc": "A-1-10", "grave_uid": g, "avize": [{"uid": t, "label": "Soclu"}]}
    assert cfg("?grave_uid=not-a-uuid")["loc"] is None and cfg(f"?grave_uid={g}&target_uid=zzz")["loc"]["avize"] == []
    evil = c.get("/phone?cod_loc=%3C%2Fscript%3E%3Cscript%3Ealert(1)%3C%2Fscript%3E&grave_uid=" + g).text
    assert "</script><script>" not in evil and evil.count("<script") == 3


def test_cloud_url_comes_from_env_only(monkeypatch, w):
    c = w.client()
    assert '"cloud_url": ""' in c.get("/phone").text
    monkeypatch.setenv("CIMITIR_PUBLIC_URL", "https://field.exemplu.ro/")
    assert '"cloud_url": "https://field.exemplu.ro"' in c.get("/phone").text
    monkeypatch.setenv("CIMITIR_PUBLIC_URL", "javascript:alert(1)")
    assert '"cloud_url": ""' in c.get("/phone").text


# ------------------------------------------------------------------ pairing / field inbox / upload (fără MAIN)
def test_pairing_redeem_then_me_and_single_use(w):
    c = w.client()
    code = w.pairing.new_code()
    r = c.post("/api/v1/phone-pairing/redeem", json={"code": code, "device_name": "Telefon"})
    assert r.status_code == 201 and r.json()["result"]["device_type"] == "MOBILE"
    key = r.json()["result"]["binding_key"]
    me = c.get("/api/v1/field-inbox/me", headers=auth(key))
    assert me.status_code == 200 and me.json()["result"] == {"cemetery_uid": pcf.CEM, "device_type": "MOBILE"}
    again = c.post("/api/v1/phone-pairing/redeem", json={"code": code})
    assert again.status_code == 401 and again.json()["code"] == "PAIRING_REJECTED"
    assert c.post("/api/v1/phone-pairing/redeem", json={"code": "ZZZZ-ZZZZ"}).status_code == 401
    no_leak(r.text, again.text)


def test_endpoints_require_binding(w):
    c = w.client()
    assert c.get("/api/v1/field-inbox/me").status_code == 401
    assert c.post("/api/v1/field-inbox/items", json=item_body()).status_code == 401
    assert c.get("/api/v1/field-inbox/me", headers=auth("cimitir.x.y")).status_code == 401


def test_create_item_and_upload_attachment(w):
    c = w.client()
    key = w.pair(c)
    r = c.post("/api/v1/field-inbox/items", json=item_body(), headers=auth(key))
    assert r.status_code == 201 and r.json()["result"]["item"]["status"] == "PENDING"
    uid = r.json()["result"]["item"]["uid"]
    fuid = str(uuid.uuid4())
    u = c.post(f"/api/v1/field-inbox/items/{uid}/files", content=PNG, headers={**auth(key), "Content-Type": "image/png", "X-File-Uid": fuid, "X-File-Name": "IMG_1.png"})
    assert u.status_code == 201 and u.json()["result"]["file"]["status"] == "READY"
    assert w.inbox.blobs[fuid] == PNG
    replay = c.post(f"/api/v1/field-inbox/items/{uid}/files", content=PNG, headers={**auth(key), "Content-Type": "image/png", "X-File-Uid": fuid, "X-File-Name": "IMG_1.png"})
    assert replay.status_code == 200 and replay.json()["result"]["replayed"] is True
    bad = c.post(f"/api/v1/field-inbox/items/{uid}/files", content=b"MZ-not-an-image", headers={**auth(key), "Content-Type": "image/png", "X-File-Uid": str(uuid.uuid4())})
    assert bad.status_code == 415
    no_leak(r.text, u.text, bad.text)


def test_idempotent_create_replays(w):
    c = w.client()
    key, body = w.pair(c), item_body()
    a = c.post("/api/v1/field-inbox/items", json=body, headers=auth(key))
    b = c.post("/api/v1/field-inbox/items", json=body, headers=auth(key))
    assert a.status_code == 201 and b.status_code == 200 and a.json()["result"]["item"]["uid"] == b.json()["result"]["item"]["uid"]


def test_internal_errors_do_not_leak_secrets(w, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("falló " + SENTINELS["CIMITIR_CP_DATABASE_URL"] + SENTINELS["SUPABASE_SECRET_KEY"])
    monkeypatch.setattr(w.inbox, "create_item", boom)
    c = w.client()
    r = c.post("/api/v1/field-inbox/items", json=item_body(), headers=auth(w.pair(c)))
    assert r.status_code == 500 and r.json()["code"] == "INTERNAL"
    no_leak(r.text)


# ------------------------------------------------------------------ CORS
def test_cors_allowed_origin():
    c = World(origins=[PHONE_ORIGIN]).client()
    pre = c.options("/api/v1/field-inbox/items", headers={"Origin": PHONE_ORIGIN, "Access-Control-Request-Method": "POST",
                                                           "Access-Control-Request-Headers": "authorization,content-type,x-file-uid,x-file-name,x-file-sha256"})
    assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == PHONE_ORIGIN
    assert "access-control-allow-credentials" not in pre.headers and pre.headers["access-control-allow-origin"] != "*"
    assert set(pre.headers["access-control-allow-methods"].replace(" ", "").split(",")) == {"GET", "POST"}
    r = c.get("/api/v1/field-inbox/me", headers={"Origin": PHONE_ORIGIN})
    assert r.headers["access-control-allow-origin"] == PHONE_ORIGIN and "access-control-allow-credentials" not in r.headers


@pytest.mark.parametrize("origin", ["https://evil.example", "http://portal.exemplu.ro", "https://portal.exemplu.ro.evil.example", "null", "https://sub.portal.exemplu.ro"])
def test_cors_rejected_origin(origin):
    c = World(origins=[PHONE_ORIGIN]).client()
    pre = c.options("/api/v1/field-inbox/items", headers={"Origin": origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"})
    assert pre.status_code == 400 and "access-control-allow-origin" not in pre.headers
    r = c.get("/api/v1/field-inbox/me", headers={"Origin": origin})
    assert "access-control-allow-origin" not in r.headers and "access-control-allow-credentials" not in r.headers


def test_cors_wildcard_and_junk_ignored_from_env(monkeypatch):
    monkeypatch.setenv("CIMITIR_FIELD_INBOX_CORS_ORIGINS", "*, portal.exemplu.ro , ftp://x.ro, https://portal.exemplu.ro/")
    inbox = pcf.PhoneInbox()
    app = cca.create_app(inbox, None, pcf.PhonePairing(inbox), lambda _s: True, lambda _s: True)
    c = TestClient(app)
    ok = c.get("/healthz", headers={"Origin": "https://portal.exemplu.ro"})
    assert ok.headers["access-control-allow-origin"] == "https://portal.exemplu.ro"
    for o in ("https://anything.example", "http://x.ro"):
        assert "access-control-allow-origin" not in c.get("/healthz", headers={"Origin": o}).headers
    monkeypatch.setenv("CIMITIR_FIELD_INBOX_CORS_ORIGINS", "*")
    c2 = TestClient(cca.create_app(inbox, None, pcf.PhonePairing(inbox), lambda _s: True, lambda _s: True))
    assert "access-control-allow-origin" not in c2.get("/healthz", headers={"Origin": "https://anything.example"}).headers


def test_same_origin_phone_page_needs_no_cors(w):
    c = w.client()
    assert "access-control-allow-origin" not in c.get("/phone", headers={"Origin": "https://evil.example"}).headers


# ------------------------------------------------------------------ trusted proxy
def redeem_source(w, peer, xff=None):
    c = w.client(client=(peer, 50000))
    h = {"X-Forwarded-For": xff} if xff else {}
    r = c.post("/api/v1/phone-pairing/redeem", json={"code": w.pairing.new_code()}, headers=h)
    assert r.status_code == 201
    return w.pairing.sources[-1]


def test_xff_ignored_by_default(w):
    assert redeem_source(w, "10.0.0.5", "203.0.113.9") == "10.0.0.5"
    assert redeem_source(w, "198.51.100.7", "203.0.113.9, 10.0.0.5") == "198.51.100.7"


def test_xff_honoured_only_from_trusted_proxy(w, monkeypatch):
    monkeypatch.setenv(pph.ENV_TRUSTED, "10.0.0.5, 172.28.0.0/24")
    assert redeem_source(w, "10.0.0.5", "203.0.113.9") == "203.0.113.9"
    assert redeem_source(w, "172.28.0.3", "9.9.9.9, 203.0.113.9") == "203.0.113.9"
    assert redeem_source(w, "198.51.100.7", "203.0.113.9") == "198.51.100.7"
    assert redeem_source(w, "10.0.0.5", "garbage") == "10.0.0.5"
    assert redeem_source(w, "10.0.0.5") == "10.0.0.5"


def test_invalid_trusted_config_is_fail_safe_and_preflight_flags_it(w, monkeypatch):
    monkeypatch.setenv(pph.ENV_TRUSTED, "0.0.0.0/0")
    assert redeem_source(w, "10.0.0.5", "203.0.113.9") == "10.0.0.5"
    env = {"CIMITIR_CP_DATABASE_URL": "postgresql://u:p@h/db", "SUPABASE_URL": "https://abcdefghijklmnopqrst.supabase.co", "SUPABASE_BUCKET": "b",
           "SUPABASE_SECRET_KEY": "k", "CIMITIR_CLOUD_PROJECT_REF": "abcdefghijklmnopqrst", pph.ENV_TRUSTED: "0.0.0.0/0"}
    assert cca.preflight(env) == [f"{pph.ENV_TRUSTED} invalid (listă IP/CIDR, fără /0)"]


# ------------------------------------------------------------------ preflight / project guard
GOOD_ENV = {"CIMITIR_CP_DATABASE_URL": "postgresql://cimitir_cp_app.abcdefghijklmnopqrst:p@aws-0-eu.pooler.supabase.com:5432/postgres", "SUPABASE_URL": "https://abcdefghijklmnopqrst.supabase.co",
            "SUPABASE_BUCKET": "b", "SUPABASE_SECRET_KEY": "k", "CIMITIR_CLOUD_PROJECT_REF": "abcdefghijklmnopqrst"}


def test_preflight_ok_and_reports_names_only():
    assert cca.preflight(GOOD_ENV) == []
    out = cca.preflight({"SUPABASE_URL": "https://x.supabase.co", "CIMITIR_CP_DATABASE_URL": SENTINELS["CIMITIR_CP_DATABASE_URL"]})
    assert "lipsește variabila SUPABASE_BUCKET" in out and "lipsește variabila SUPABASE_SECRET_KEY" in out and "lipsește variabila CIMITIR_CLOUD_PROJECT_REF" in out
    no_leak("\n".join(out))
    assert cca.preflight({**GOOD_ENV, "SUPABASE_URL": "https://zzzzzzzzzzzzzzzzzzzz.supabase.co"}) == ["SUPABASE_URL nu corespunde CIMITIR_CLOUD_PROJECT_REF"]
    assert cca.preflight({**GOOD_ENV, "CIMITIR_CP_DATABASE_URL": "postgresql://u:p@zzzzzzzzzzzzzzzzzzzz.pooler.supabase.com/db"}) == ["CIMITIR_CP_DATABASE_URL nu corespunde CIMITIR_CLOUD_PROJECT_REF"]
    assert cca.preflight({**GOOD_ENV, "SUPABASE_SECRET_KEY": "", "SUPABASE_SERVICE_ROLE_KEY": "legacy"}) == []


def test_project_guard_requires_explicit_cloud_ref(monkeypatch):
    pooler = "postgresql://cimitir_cp_app.abcdefghijklmnopqrst:p@aws-0-eu.pooler.supabase.com:5432/postgres"
    direct = "postgresql://cimitir_cp_app:p@db.abcdefghijklmnopqrst.supabase.co:5432/postgres"
    for k in (ccp.CLOUD_REF_ENV, "CIMITIR_LIVE_TESTS", "CIMITIR_TEST_PROJECT_REF"):
        monkeypatch.delenv(k, raising=False)
    for u in (pooler, direct):
        with pytest.raises(RuntimeError):
            ccp._guard(u)
    monkeypatch.setenv(ccp.CLOUD_REF_ENV, "abcdefghijklmnopqrst")
    ccp._guard(pooler)
    ccp._guard(direct)
    with pytest.raises(RuntimeError):
        ccp._guard("postgresql://u:p@zzzzzzzzzzzzzzzzzzzz.pooler.supabase.com/db")
    ccp._guard("postgresql://u:p@127.0.0.1:5432/db")


def test_protected_production_project_stays_blocked_even_if_configured(monkeypatch):
    prod = ccp.PROTECTED_PROJECT_REFS[0]
    monkeypatch.setenv(ccp.CLOUD_REF_ENV, prod)
    with pytest.raises(RuntimeError):
        ccp._guard(f"postgresql://cimitir_cp_app.{prod}:p@aws-0-eu.pooler.supabase.com:5432/postgres")


# ------------------------------------------------------------------ MAIN absent
FORBIDDEN_MODULES = ("database", "server", "desktop", "main", "cimitir_database", "cimitir_db_router", "field_import", "field_inbox_client", "cloud_profiles", "web_server", "PySide6", "PyQt5", "PyQt6")


def test_cloud_import_closure_has_no_main_modules(tmp_path):
    code = ("import sys, json, cimitir_cloud_app\n"
            "bad = [m for m in sys.modules if m.split('.')[0] in %r]\n"
            "print(json.dumps(bad))" % (FORBIDDEN_MODULES,))
    env = {"PATH": os.environ["PATH"], "PYTHONPATH": str(REPO / "shared"), "HOME": str(tmp_path)}
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=tmp_path, env=env, timeout=60)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout) == []


def test_cloud_sources_do_not_reference_main_or_local_files():
    src = (REPO / "shared" / "cimitir_cloud_app.py").read_text(encoding="utf-8")
    imports = re.findall(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", src, re.M)
    assert not [i for i in imports if i.split(".")[0] in FORBIDDEN_MODULES + ("sqlite3", "cimitir_database")]
    assert "CIMITIR_DATA_DIR" not in src and "cimitir.db" not in src and ".sqlite" not in src


def test_phone_flow_with_main_absent(tmp_path, monkeypatch):
    """Pairing -> pagină -> create item -> upload cu: niciun fișier SQLite deschis, CIMITIR_DATA_DIR inexistent, director gol, fără proces/registru MAIN."""
    import sqlite3
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CIMITIR_DATA_DIR", str(tmp_path / "nu-exista-main"))
    monkeypatch.setenv("HOME", str(tmp_path))
    real = sqlite3.connect

    def only_memory(database, *a, **k):
        if str(database) != ":memory:" and not str(database).startswith("file::memory:"):
            raise AssertionError(f"acces la fișier SQLite local: {database}")
        return real(database, *a, **k)
    monkeypatch.setattr(sqlite3, "connect", only_memory)
    w = World()
    c = w.client()
    assert c.get("/phone").status_code == 200 and c.get("/static/teren.js").status_code == 200
    key = w.pair(c)
    uid = c.post("/api/v1/field-inbox/items", json=item_body(), headers=auth(key)).json()["result"]["item"]["uid"]
    assert c.post(f"/api/v1/field-inbox/items/{uid}/files", content=PNG, headers={**auth(key), "Content-Type": "image/png", "X-File-Uid": str(uuid.uuid4())}).status_code == 201
    assert c.get("/api/v1/health").json()["ok"] is True
    assert list(tmp_path.iterdir()) == [] and not (tmp_path / "nu-exista-main").exists()
    assert len(w.inbox.items) == 1 and len(w.inbox.blobs) == 1


# ------------------------------------------------------------------ pornire „curată” ca în container (copiază exact ce copiază Dockerfile)
def dockerfile_copies():
    out = []
    for line in (REPO / "Dockerfile").read_text(encoding="utf-8").splitlines():
        m = re.match(r"COPY\s+(.+?)\s+(\S+)$", line.strip())
        if m:
            out.append((m.group(1).split(), m.group(2)))
    return out


def image_tree(dst: Path) -> Path:
    for srcs, target in dockerfile_copies():
        for s in srcs:
            src = REPO / s
            to = dst / target if target.endswith("/") else dst / target
            if target.endswith("/") and src.is_file():
                (dst / target).mkdir(parents=True, exist_ok=True)
                shutil.copy(src, dst / target / src.name)
            elif src.is_dir():
                shutil.copytree(src, to, ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
            else:
                to.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(src, to)
    return dst


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def clean_env(tree: Path, **extra):
    env = {"PATH": os.environ["PATH"], "HOME": str(tree), "PYTHONPATH": str(tree / "shared"), "PYTHONDONTWRITEBYTECODE": "1"}
    env.update(extra)
    return env


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)


def test_container_start_fails_fast_without_env(tmp_path):
    tree = image_tree(tmp_path / "app")
    p = subprocess.run([sys.executable, "-m", "cimitir_cloud_app"], capture_output=True, text=True, cwd=tree, env=clean_env(tree), timeout=60)
    assert p.returncode == 2
    assert "CIMITIR_CP_DATABASE_URL" in p.stderr + p.stdout and "SUPABASE_BUCKET" in p.stderr + p.stdout


def test_container_start_clean_tree_serves_health_page_static(tmp_path):
    tree = image_tree(tmp_path / "app")
    port = free_port()
    ref = "abcdefghijklmnopqrst"
    env = clean_env(tree, PORT=str(port), CIMITIR_CLOUD_HOST="127.0.0.1", CIMITIR_CP_DATABASE_URL=f"postgresql://cimitir_cp_app:PGSENTINELPW@127.0.0.1:1/postgres",
                    SUPABASE_URL=f"https://{ref}.supabase.co", SUPABASE_SECRET_KEY="sb_secret_SENTINEL_9f3", SUPABASE_BUCKET="field-private", CIMITIR_CLOUD_PROJECT_REF=ref,
                    CIMITIR_PAIRING_TRUSTED_PROXIES="127.0.0.1", CIMITIR_FIELD_INBOX_CORS_ORIGINS=PHONE_ORIGIN)
    proc = subprocess.Popen([sys.executable, "-m", "cimitir_cloud_app"], cwd=tree, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if get(base + "/healthz")[0] == 200:
                    break
            except OSError:
                time.sleep(0.2)
        else:
            raise AssertionError("serverul nu a pornit")
        s, body, _h = get(base + "/api/v1/health")
        assert s == 503 and json.loads(body) == {"ok": False, "api": "up", "database": "down", "storage": "configured"}
        s, page, h = get(base + "/phone")
        assert s == 200 and "teren-page" in page and "content-security-policy" in {k.lower() for k in h}
        assert get(base + "/static/teren.js")[0] == 200 and get(base + "/static/teren_ui.js")[0] == 200 and get(base + "/static/teren.css")[0] == 200
        assert get(base + "/static/app.js")[0] == 404 and get(base + "/docs")[0] == 404
        s, _b, h = get(base + "/healthz", {"Origin": PHONE_ORIGIN})
        assert h.get("access-control-allow-origin") == PHONE_ORIGIN
        assert "access-control-allow-origin" not in {k.lower() for k in get(base + "/healthz", {"Origin": "https://evil.example"})[2]}
        s, b, _h = get(base + "/api/v1/field-inbox/me")
        assert s == 401
        no_leak(body, page, b)
    finally:
        proc.terminate()
        out = proc.communicate(timeout=15)[0]
    no_leak(out)
    assert "127.0.0.1:1" not in out
    assert list(p.name for p in tree.rglob("*.db")) == []


# ------------------------------------------------------------------ artefacte de deploy
def test_dockerfile_contract():
    d = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^HEALTHCHECK .*\n?.*/healthz", d, re.M) and 'CMD ["python", "-m", "cimitir_cloud_app"]' in d
    assert re.search(r"^USER 10001", d, re.M) and "requirements-cloud.txt" in d
    assert not re.search(r"^(ENV|ARG)\s+\w*(SECRET|PASSWORD|KEY|TOKEN|DATABASE_URL)", d, re.M | re.I)
    assert not re.search(r"https?://(?!127\.0\.0\.1)", d.split("HEALTHCHECK")[0])
    for srcs, _t in dockerfile_copies():
        for s in srcs:
            assert (REPO / s).exists(), s


def test_env_example_has_placeholders_only():
    txt = (REPO / ".env.example").read_text(encoding="utf-8")
    keys = set(re.findall(r"^([A-Z][A-Z0-9_]+)=", txt, re.M))
    assert set(cca.REQUIRED_ENV) <= keys and {"CIMITIR_PAIRING_TRUSTED_PROXIES", "CIMITIR_FIELD_INBOX_CORS_ORIGINS", "SUPABASE_SECRET_KEY", "CIMITIR_PUBLIC_URL"} <= keys
    for k in ("CIMITIR_CP_ADMIN_DATABASE_URL", "CIMITIR_CP_CLEANUP_DATABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "DATABASE_URL", "JWT_SECRET"):
        assert k not in keys
    assert ccp.PROTECTED_PROJECT_REFS[0] not in txt and not re.search(r"sb_secret_[A-Za-z0-9]{10,}|eyJ[A-Za-z0-9_-]{20,}", txt)
    for line in re.findall(r"^(?:CIMITIR_CP_DATABASE_URL|SUPABASE_URL|SUPABASE_SECRET_KEY|SUPABASE_BUCKET|CIMITIR_CLOUD_PROJECT_REF|CIMITIR_PUBLIC_URL)=(.*)$", txt, re.M):
        assert "<" in line
    assert "*" not in re.search(r"^CIMITIR_FIELD_INBOX_CORS_ORIGINS=(.*)$", txt, re.M).group(1)


def test_env_files_are_git_ignored():
    gi = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"^\.env$", gi, re.M) and "!.env.example" in gi


def test_requirements_cloud_pinned_and_minimal():
    lines = [x for x in (REPO / "backend" / "requirements-cloud.txt").read_text(encoding="utf-8").splitlines() if x and not x.startswith("#")]
    assert all("==" in x for x in lines)
    names = {x.split("==")[0].lower() for x in lines}
    assert {"fastapi", "uvicorn", "psycopg", "psycopg-binary", "requests", "jinja2", "starlette"} <= names
    assert not names & {"pyside6", "pyqt5", "pymongo", "motor", "pytest", "litellm", "pandas", "numpy"}
