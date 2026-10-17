# ruff: noqa: F811
"""Cloud admin (migrarea 023) + asocierea MAIN prin HTTP REAL (uvicorn, port local) peste PostgreSQL LOCAL REAL (rol cimitir_cp_app, fără BYPASSRLS).
Rulează doar cu CIMITIR_TEST_PG_ADMIN_URL=postgresql://<superuser>@127.0.0.1:<port>/postgres (altfel SKIP). NU atinge Supabase. Rezultate = LOCAL, nu LIVE."""
from __future__ import annotations

import socket
import threading
import time
import uuid

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

import cimitir_bootstrap as bs
import cimitir_cloud_admin as ad
import cimitir_cloud_admin_http as adh
import cimitir_cloud_app as capp
import cimitir_cloud_client as client
import cimitir_cloud_control as cc
import cimitir_cloud_control_pg as ccp
import cimitir_cloud_http as ch
import cimitir_phone_pairing as pp
import cimitir_phone_pairing_http as pph
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_cleanup_race_pg import adm, pgdb  # noqa: F401

import os

pytestmark = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PostgreSQL local de test indisponibil")
TOKEN = "t" * 12 + uuid.uuid4().hex + uuid.uuid4().hex
PW = "Parola-de-test-2026!"
EMAIL = "parohiabanovita2022@gmail.com"
H = {"X-Cimitir-Admin": "1"}


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, app):
        self.port = _free_port()
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error", proxy_headers=False))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.05)
        self.url = f"http://127.0.0.1:{self.port}"

    def stop(self):
        self.server.should_exit = True
        self.thread.join(5)


@pytest.fixture()
def cw(renv, pgdb, monkeypatch):
    with adm(pgdb) as c:
        c.execute("TRUNCATE cp_admin_sessions, cp_admin_recovery, cp_admin_credentials, cp_bootstrap_runs, cp_failed_redeems, cp_audit_log, cp_platform_admins, cp_org_roles, "
                  "cp_binding_issuers, device_pairing_codes, device_cemetery_bindings, devices, cemeteries, organizations, identities CASCADE")
        c.execute("UPDATE cp_admin_state SET bootstrap_completed_at=NULL, bootstrap_identity_uid=NULL")
    monkeypatch.setenv("CIMITIR_CP_DATABASE_URL", pgdb["app"])
    monkeypatch.setenv("CIMITIR_CP_ADMIN_DATABASE_URL", pgdb["admin"])
    monkeypatch.setenv("CIMITIR_ALLOW_TEST_SECRETSTORE", "1")
    monkeypatch.setenv(ad.ENV_TOKEN, TOKEN)
    monkeypatch.setenv(ad.ENV_TOKEN_EXPIRES, str(time.time() + 3600))
    renv.cp = ccp.PostgresControlPlane()
    w = World(renv)
    b = w.local(w.org1, name="Craiova TEST")
    renv.cp.create_cemetery(w.owner1, w.org1, b.cemetery_uid, "Craiova TEST")
    admin = ad.AdminService(renv.cp)
    app = FastAPI()
    app.include_router(ch.create_router(renv.cp))
    app.include_router(adh.create_router(admin))
    import cimitir_field_inbox as fi
    import cimitir_field_storage as fs
    app.include_router(pph.create_router(pp.PhonePairingService(fi.FieldInboxService(renv.cp, fs.FieldMediaStore(renv.cp, None, cleanup_url=pgdb["app"])))))
    srv = Server(app)
    http = httpx.Client(base_url=srv.url, timeout=10)
    yield {"w": w, "b": b, "renv": renv, "admin": admin, "srv": srv, "http": http, "pg": pgdb, "cp": renv.cp}
    http.close()
    srv.stop()


def api(cw, method, path, token=None, **kw):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = cw["http"].request(method, "/api/v1/admin" + path, headers=headers, **kw)
    return r.status_code, r.json()


def bootstrap(cw, **over):
    body = {"token": TOKEN, "email": EMAIL, "password": PW, "display_name": "Paroh", "organization_uid": cw["w"].org1, "cemetery_uid": cw["b"].cemetery_uid, **over}
    return api(cw, "POST", "/bootstrap", json=body)


def login(cw, pw=PW, email=EMAIL):
    st, j = api(cw, "POST", "/login", json={"email": email, "password": pw})
    return st, j, (j["result"]["token"] if j.get("ok") else None)


# ===================================================================== bootstrap primul administrator
def test_status_and_unauthenticated_denied(cw):
    st, j = api(cw, "GET", "/status")
    assert st == 200 and j["result"] == {"initialized": False, "bootstrap_open": True}
    for method, path in (("GET", "/me"), ("GET", "/codes?organization_uid=x"), ("GET", "/devices?organization_uid=x")):
        assert api(cw, method, path)[0] == 401
    assert api(cw, "POST", "/codes", json={})[0] == 401


def test_bootstrap_wrong_token_weak_password_and_success_once(cw):
    assert bootstrap(cw, token="x" * 40)[0] == 403
    assert bootstrap(cw, password="scurta")[0] == 400
    st, j = bootstrap(cw)
    assert st == 201 and j["result"]["organization_uid"] == cw["w"].org1
    assert api(cw, "GET", "/status")[1]["result"]["initialized"] is True
    st, j = bootstrap(cw, email="alt@example.com")
    assert st == 409 and j["code"] == "BOOTSTRAP_CLOSED"
    with adm(cw["pg"]) as c:
        assert c.execute("SELECT count(*) FROM cp_admin_credentials").fetchone()[0] == 1
        h = c.execute("SELECT password_hash FROM cp_admin_credentials").fetchone()[0]
    assert h.startswith("scrypt$") and PW not in h
    # persistent după „repornire”: o instanță nouă a serviciului vede tot starea din PostgreSQL
    fresh = ad.AdminService(ccp.PostgresControlPlane())
    assert fresh.status()["initialized"] is True
    with pytest.raises(ad.BootstrapClosed):
        fresh.bootstrap(TOKEN, "x@example.com", PW, organization_uid=str(uuid.uuid4()), organization_name="Alta")


def test_bootstrap_token_expired_or_missing(cw, monkeypatch):
    monkeypatch.setenv(ad.ENV_TOKEN_EXPIRES, str(time.time() - 5))
    assert api(cw, "GET", "/status")[1]["result"]["bootstrap_open"] is False
    assert bootstrap(cw)[0] == 403
    monkeypatch.delenv(ad.ENV_TOKEN)
    assert bootstrap(cw)[0] == 403


def test_bootstrap_creates_new_org_cemetery_owner_consistently(cw):
    org, cem = str(uuid.uuid4()), str(uuid.uuid4())
    st, j = bootstrap(cw, organization_uid=org, organization_name="Primăria Nouă", cemetery_uid=cem, cemetery_name="Cimitir Nou")
    assert st == 201
    _s, _j, tok = login(cw)
    me = api(cw, "GET", "/me", tok)[1]["result"]
    o = [x for x in me["organizations"] if x["organization_uid"] == org][0]
    assert o["is_owner"] and o["name"] == "Primăria Nouă" and o["cemeteries"] == [{"uid": cem, "name": "Cimitir Nou", "status": "DRAFT"}]


def test_bootstrap_cemetery_of_other_org_refused(cw):
    st, j = bootstrap(cw, cemetery_uid=str(uuid.uuid4()), organization_uid=str(uuid.uuid4()), organization_name="X")
    assert st == 201  # cimitir nou într-o organizație nouă: ok
    with adm(cw["pg"]) as c:  # reset pentru al doilea caz
        c.execute("TRUNCATE cp_admin_credentials CASCADE")
        c.execute("UPDATE cp_admin_state SET bootstrap_completed_at=NULL")
    st, j = bootstrap(cw, organization_uid=str(uuid.uuid4()), organization_name="Y", cemetery_uid=cw["b"].cemetery_uid)
    assert st == 400


# ===================================================================== login / sesiuni
def test_login_lockout_logout_and_password_change(cw):
    bootstrap(cw)
    st, j, tok = login(cw)
    assert st == 200 and tok
    assert api(cw, "GET", "/me", tok)[1]["result"]["email"] == EMAIL
    assert api(cw, "POST", "/logout", tok, json={})[0] == 200
    assert api(cw, "GET", "/me", tok)[0] == 401                                  # sesiune revocată
    assert login(cw, "gresita-gresita-123")[0] == 401
    assert login(cw, email="necunoscut@example.com")[0] == 401                  # același răspuns
    for _ in range(4):
        login(cw, "gresita-gresita-123")
    assert login(cw)[0] == 429                                                    # contul e blocat chiar și cu parola corectă
    with adm(cw["pg"]) as c:
        c.execute("UPDATE cp_admin_credentials SET locked_until=NULL, failed_attempts=0")
    _s, _j, t1 = login(cw)
    _s, _j, t2 = login(cw)
    assert api(cw, "POST", "/password", t1, json={"old_password": "gresita-gresita-123", "new_password": "NouaParola-2026-ok"})[0] == 401
    assert api(cw, "POST", "/password", t1, json={"old_password": PW, "new_password": "NouaParola-2026-ok"})[0] == 200
    assert api(cw, "GET", "/me", t2)[0] == 401 and api(cw, "GET", "/me", t1)[0] == 200  # celelalte sesiuni sunt revocate
    assert login(cw, "NouaParola-2026-ok")[0] == 200


def test_cookie_session_requires_csrf_header(cw):
    bootstrap(cw)
    c = httpx.Client(base_url=cw["srv"].url)
    r = c.post("/api/v1/admin/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200 and "HttpOnly" in r.headers["set-cookie"] and "SameSite=strict" in r.headers["set-cookie"]
    assert c.get("/api/v1/admin/me").status_code == 200
    assert c.post("/api/v1/admin/logout", json={}).status_code == 403                  # fără X-Cimitir-Admin
    assert c.post("/api/v1/admin/logout", json={}, headers=H).status_code == 200


def test_recovery_flow_does_not_touch_data(cw):
    bootstrap(cw)
    _s, _j, tok = login(cw)
    assert api(cw, "POST", "/recover", json={"token": "rec-" + "z" * 40, "email": EMAIL, "new_password": "ParolaRecuperata-2026"})[0] == 403
    rt = cw["admin"].issue_recovery(EMAIL)
    assert rt and cw["admin"].issue_recovery("nimeni@example.com") is None
    assert api(cw, "POST", "/recover", json={"token": rt, "email": "alt@example.com", "new_password": "ParolaRecuperata-2026"})[0] == 403
    assert api(cw, "POST", "/recover", json={"token": rt, "email": EMAIL, "new_password": "ParolaRecuperata-2026"})[0] == 200
    assert api(cw, "POST", "/recover", json={"token": rt, "email": EMAIL, "new_password": "ParolaRecuperata-2026"})[0] == 403   # o singură folosire
    assert api(cw, "GET", "/me", tok)[0] == 401                                       # sesiunile vechi revocate
    assert login(cw, "ParolaRecuperata-2026")[0] == 200
    with adm(cw["pg"]) as c:
        assert c.execute("SELECT count(*) FROM cemeteries").fetchone()[0] >= 1


# ===================================================================== coduri MAIN + asociere prin HTTP real (fix 404)
def test_main_association_over_real_http_with_admin_issued_code(cw):
    r, b, w = cw["renv"], cw["b"], cw["w"]
    bootstrap(cw)
    _s, _j, tok = login(cw)
    st, j = api(cw, "POST", "/codes", tok, json={"organization_uid": w.org1, "cemetery_uid": b.cemetery_uid, "kind": "main"})
    assert st == 201 and j["result"]["ttl"] == cc.CODE_TTL
    code = j["result"]["display"]
    assert "-" in code and code.replace("-", "") != code
    info = httpx.get(cw["srv"].url + "/api/v1/bootstrap/info").json()
    assert info["ok"] and "preflight_for_code" in info["ops"]
    rcp = client.RemoteControlPlane(cw["srv"].url, retries=0)
    try:
        norm = pp.normalize_code(code)
        pre = rcp.preflight_for_code(norm, r.inst, b.cemetery_uid)                       # înainte: HTTP 404
        assert pre["organization_uid"] == w.org1
        plan = bs.plan_flow_a(b, rcp, norm, r.inst, cemetery_name="Craiova TEST")
        report = bs.execute_flow_a(b, rcp, r.store, plan, plan["plan_hash"], norm, r.inst, cloud_url=cw["srv"].url)
        assert report["result"] == "completed"
        reg = bs.LocalRegistry(b)
        assert reg.sync_state == "BOUND" and reg.get("cloud_url") == cw["srv"].url
        key = r.store.get(reg.get("cloud_secret_ref"))
        assert key and key.startswith("cimitir.")
        blob = open(b.database, "rb").read()
        assert key.encode() not in blob                                                  # cheia nu e în SQLite
        assert bs.ping(b, rcp, r.store, r.inst)["binding_uid"] == report["binding_uid"]
    finally:
        rcp.close()
    devs = api(cw, "GET", f"/devices?organization_uid={w.org1}", tok)[1]["result"]
    assert [d["kind"] for d in devs] == ["main"] and devs[0]["state"] == "active"
    # codul consumat nu mai poate fi folosit; revocarea dispozitivului invalidă cheia
    rcp2 = client.RemoteControlPlane(cw["srv"].url, retries=0)
    with pytest.raises(cc.ScopeError):
        rcp2.preflight_for_code(norm, str(uuid.uuid4()), b.cemetery_uid)
    assert api(cw, "POST", "/devices/revoke", tok, json={"organization_uid": w.org1, "binding_uid": devs[0]["binding_uid"]})[0] == 200
    with pytest.raises(cc.ScopeError):
        rcp2.authenticate(key, b.cemetery_uid)
    rcp2.close()


def test_expired_and_revoked_main_code_rejected_and_listed(cw):
    r, b, w = cw["renv"], cw["b"], cw["w"]
    bootstrap(cw)
    _s, _j, tok = login(cw)
    body = {"organization_uid": w.org1, "cemetery_uid": b.cemetery_uid, "kind": "main"}
    c1 = api(cw, "POST", "/codes", tok, json=body)[1]["result"]
    c2 = api(cw, "POST", "/codes", tok, json=body)[1]["result"]
    assert api(cw, "POST", "/codes/revoke", tok, json={"organization_uid": w.org1, "code_id": c1["code_id"]})[0] == 200
    assert api(cw, "POST", "/codes/revoke", tok, json={"organization_uid": w.org1, "code_id": c1["code_id"]})[0] == 404     # deja revocat
    with adm(cw["pg"]) as c:
        c.execute("UPDATE device_pairing_codes SET expires_at = now() - interval '1 minute', created_at = now() - interval '20 minutes' WHERE left(code_hash,16)=%s", (c2["code_id"],))
    rcp = client.RemoteControlPlane(cw["srv"].url, retries=0)
    for c in (c1, c2):
        with pytest.raises(cc.ScopeError):
            rcp.preflight_for_code(c["code"], r.inst, b.cemetery_uid)
    states = {x["code_id"]: x["state"] for x in api(cw, "GET", f"/codes?organization_uid={w.org1}", tok)[1]["result"]}
    assert states[c1["code_id"]] == "revoked" and states[c2["code_id"]] == "expired"
    rcp.close()


def test_blocked_active_empty_is_not_bypassed(cw):
    r, b, w = cw["renv"], cw["b"], cw["w"]
    bootstrap(cw)
    _s, _j, tok = login(cw)
    with adm(cw["pg"]) as c:
        c.execute("UPDATE cemeteries SET status='ACTIVE' WHERE uid=%s", (b.cemetery_uid,))
    code = api(cw, "POST", "/codes", tok, json={"organization_uid": w.org1, "cemetery_uid": b.cemetery_uid, "kind": "main"})[1]["result"]["code"]
    rcp = client.RemoteControlPlane(cw["srv"].url, retries=0)
    with pytest.raises(bs.BlockedFlow) as ei:
        bs.plan_flow_a(b, rcp, code, r.inst, cemetery_name="x")
    assert ei.value.decision == "BLOCKED_ACTIVE_EMPTY"
    rcp.close()


# ===================================================================== telefon + izolare
def test_phone_code_issue_redeem_list_revoke(cw):
    b, w = cw["b"], cw["w"]
    bootstrap(cw)
    _s, _j, tok = login(cw)
    body = {"organization_uid": w.org1, "cemetery_uid": b.cemetery_uid, "kind": "phone"}
    c1 = api(cw, "POST", "/codes", tok, json=body)[1]["result"]
    c2 = api(cw, "POST", "/codes", tok, json=body)[1]["result"]
    assert c1["code"] != c2["code"] and len(c1["code"]) == 20
    assert api(cw, "POST", "/codes/revoke", tok, json={"organization_uid": w.org1, "code_id": c2["code_id"]})[0] == 200
    r = httpx.post(cw["srv"].url + "/api/v1/phone-pairing/redeem", json={"code": c2["display"], "device_name": "Telefon X", "client_id": "cid-" + "a" * 10})
    assert r.status_code in (400, 401, 403, 404, 422), r.text                         # cod revocat: respins
    r = httpx.post(cw["srv"].url + "/api/v1/phone-pairing/redeem", json={"code": c1["display"], "device_name": "Telefon Y", "client_id": "cid-" + "b" * 10})
    assert r.status_code == 201, r.text
    phone_key = r.json()["result"]["binding_key"] if "result" in r.json() else r.json()["binding_key"]
    devs = api(cw, "GET", f"/devices?organization_uid={w.org1}", tok)[1]["result"]
    ph = [d for d in devs if d["kind"] == "phone"][0]
    assert ph["state"] == "active" and ph["device_name"] == "Telefon Y"
    assert api(cw, "POST", "/devices/revoke", tok, json={"organization_uid": w.org1, "binding_uid": ph["binding_uid"]})[0] == 200
    with pytest.raises(cc.ScopeError):
        cw["cp"].authenticate(phone_key)
    assert [d["state"] for d in api(cw, "GET", f"/devices?organization_uid={w.org1}", tok)[1]["result"] if d["kind"] == "phone"] == ["revoked"]


def test_cross_organization_isolation_and_roles(cw):
    b, w = cw["b"], cw["w"]
    bootstrap(cw)
    _s, _j, tok1 = login(cw)
    with adm(cw["pg"]) as c:  # al doilea administrator (owner2) creat direct în DB de test
        c.execute("INSERT INTO cp_admin_credentials (identity_uid, email, display_name, password_hash) VALUES (%s,'owner2@example.com','O2',%s)", (w.owner2, ad.hash_password(PW)))
    _s, _j, tok2 = login(cw, PW, "owner2@example.com")
    me2 = api(cw, "GET", "/me", tok2)[1]["result"]
    assert [o["organization_uid"] for o in me2["organizations"]] == [w.org2]
    body = {"organization_uid": w.org1, "cemetery_uid": b.cemetery_uid, "kind": "main"}
    assert api(cw, "POST", "/codes", tok2, json=body)[0] in (403, 404)
    assert api(cw, "GET", f"/codes?organization_uid={w.org1}", tok2)[0] in (403, 404)
    assert api(cw, "GET", f"/devices?organization_uid={w.org1}", tok2)[0] in (403, 404)
    body2 = {"organization_uid": w.org2, "cemetery_uid": b.cemetery_uid, "kind": "main"}          # cimitirul org1 în org2
    assert api(cw, "POST", "/codes", tok2, json=body2)[0] in (403, 404)
    code = api(cw, "POST", "/codes", tok1, json=body)[1]["result"]
    assert api(cw, "POST", "/codes/revoke", tok2, json={"organization_uid": w.org2, "code_id": code["code_id"]})[0] == 404
    assert api(cw, "POST", "/cemeteries", tok2, json={"organization_uid": w.org1, "name": "Intrus"})[0] in (403, 404)
    st, j = api(cw, "POST", "/cemeteries", tok1, json={"organization_uid": w.org1, "name": "Al doilea"})
    assert st == 201 and j["result"]["status"] == "DRAFT"


def test_cloud_app_mounts_bootstrap_and_admin_routes(cw):
    import cimitir_field_inbox as fi
    import cimitir_field_storage as fs
    svc = fi.FieldInboxService(cw["cp"], fs.FieldMediaStore(cw["cp"], None, cleanup_url=cw["pg"]["app"]))
    app = capp.create_app(svc, storage_probe=lambda s: True)
    paths = {r.path for r in app.routes}
    assert "/api/v1/bootstrap/preflight" in paths and "/api/v1/bootstrap/pair" in paths and "/api/v1/admin/login" in paths and "/admin/" in paths
    from starlette.testclient import TestClient
    tc = TestClient(app)
    page = tc.get("/admin/")
    assert page.status_code == 200 and "script-src 'self'" in page.headers["content-security-policy"]
    assert tc.get("/admin-assets/cloud_admin.js").status_code == 200 and tc.get("/admin-assets/secret.py").status_code == 404
    assert tc.post("/api/v1/bootstrap/preflight", json={"args": [None, "i", "c"]}).status_code == 401
