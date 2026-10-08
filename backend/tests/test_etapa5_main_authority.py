"""Etapa 5 — autoritatea MAIN (aprobare/contract), monitorizarea clienților MAIN, modul online prin HTTPS.

Serverul online rulează real (uvicorn, CIMITIR_MODE=online; varianta HTTPS cu certificat autosemnat generat în test);
MAIN rulează în procese separate, cu aceleași module ca desktopul. Doar date fictive.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import json
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

from test_etapa2_sync import BACKEND, SEED, Server, _env, _free_port, _server_py, main_run, sync  # noqa: E402
from test_etapa3_requests import Client  # noqa: E402

KEY_CMD = "import sync_api; print(sync_api.issue_sync_key('cimitir'))"


def _admin(server) -> Client:
    client = Client(server)
    assert client.call("POST", "/admin/access/login", fields={"username": "admin", "password": "admin123"})[0] == 303
    assert client.call("POST", "/admin/schimba-parola", fields={"old_password": "admin123", "new_password": "Parola-test-987",
                                                                 "confirm_password": "Parola-test-987"})[0] == 303
    return client


def _apply(server, cod_loc="A-1"):
    status, body = server.request("POST", "/api/public/cerere", form={"nume": "Fictiv", "prenume": "Test", "cod_loc": cod_loc, "privacy_ack": "1"})
    assert status == 200, body
    return body["cod_urmarire"]


@pytest.fixture()
def online(tmp_path):
    server = Server(tmp_path / "server")
    server.data_dir.mkdir()
    key = _server_py(server.data_dir, KEY_CMD)
    server.start()
    main_dir = tmp_path / "main"
    main_dir.mkdir()
    yield server, key, main_dir
    server.stop()


# ------------------------------------------------------------ contractele rămân în MAIN; cererile se pot decide din web cu dreptul `approve`
def test_web_contracts_and_request_decisions_need_approve_right(online):
    server, key, main_dir = online
    sync(main_dir, server, key, SEED.replace('"Ocupat"', '"Liber"'))
    code = _apply(server)
    sync(main_dir, server, key)
    admin = _admin(server)

    page = admin.call("GET", "/admin/cereri")[1].decode()
    assert "cerere-approve-" in page and "cerere-reject-" in page and "create_contract" not in page
    # Etapa 12: crearea unui contract din web e permisă cu dreptul `approve`; fără concesionar => 422, formularul apare în sertar
    assert admin.call("POST", "/admin/ui/loc/A-1/contract", fields={"numar_contract": "C-1"})[0] == 422
    drawer = admin.call("GET", "/admin/ui/loc/A-1")[1].decode()
    assert "contract-add-toggle" in drawer and "contract-main-only" not in drawer
    # status invalid / cerere inexistentă
    assert admin.call("POST", "/admin/ui/cerere/1/status", fields={"status": "nou"})[0] == 422
    assert admin.call("POST", "/admin/ui/cerere/999/status", fields={"status": "validat"})[0] == 404

    # respingere din web: portalul vede decizia, iar MAIN nu mai primește cererea la preluare
    assert admin.call("POST", "/admin/ui/cerere/1/status", fields={"status": "respins", "note": "Documente lipsă"})[0] == 303
    assert server.get(f"/api/public/cerere/{code}")["status"] == "respins"
    assert sync(main_dir, server, key)["report"]["ok"]
    assert admin.call("POST", "/admin/ui/cerere/1/status", fields={"status": "validat"})[0] in (303, 409)
    # MAIN nu poate contrazice o decizie luată din web
    st, body = server.request("POST", f"/api/sync/requests/{code}/status", {"status": "validat", "op_id": "m1", "version": 1}, headers={"X-Sync-Key": key})
    assert st == 409 and body["detail"]["error"] == "decided_on_server"

    # a doua cerere: aprobare din web (concesionar + contract draft + loc rezervat, idempotent)
    code2 = _apply(server)
    cid2 = 2
    assert admin.call("POST", f"/admin/ui/cerere/{cid2}/status", fields={"status": "validat"})[0] == 303
    assert server.get(f"/api/public/cerere/{code2}")["status"] == "validat"
    assert admin.call("POST", f"/admin/ui/cerere/{cid2}/status", fields={"status": "validat"})[0] == 303      # idempotent
    assert admin.call("POST", f"/admin/ui/cerere/{cid2}/status", fields={"status": "respins"})[0] == 409     # nu se răzgândește după aprobare
    pend = server.request("GET", "/api/sync/requests", headers={"X-Sync-Key": key})[1]
    assert code2 not in json.dumps(pend)


def test_sync_status_channel_from_main_still_works_after_web_lock(online):
    server, key, _ = online
    code = _apply(server, "A-1")
    assert server.request("POST", f"/api/sync/requests/{code}/status", {"status": "validat", "op_id": "t1", "version": 1}, headers={"X-Sync-Key": key})[0] == 200
    assert server.request("POST", f"/api/sync/requests/{code}/status", {"status": "validat", "op_id": "t2", "version": 2})[0] == 401


# ------------------------------------------------------------ monitorizare clienți MAIN
def test_monitoring_shows_main_client_last_success_errors_pending_and_stale_info(online):
    server, key, main_dir = online
    admin = _admin(server)
    page = admin.call("GET", "/admin/monitorizare")[1].decode()
    assert "sync-clients-empty" in page and "sync-no-clients-alert" in page and "sync-offline-alert" not in page

    r = sync(main_dir, server, key, SEED.replace('"Ocupat"', '"Liber"') + "\nout['cid'] = cloud_sync.client_id()")
    cid = r["cid"]
    assert cid.startswith("main-") and r["report"]["ok"]
    page = admin.call("GET", "/admin/monitorizare")[1].decode()
    assert f'data-testid="sync-client-{cid}"' in page
    assert f'data-testid="sync-pending-{cid}">0' in page
    assert "sync-offline-alert" not in page and "sync-no-clients-alert" not in page
    assert "Actuală" in page and "Informație veche" not in page.split('data-testid="sync-clients-table"')[1].split("</table>")[0]
    last_ok = r["report"] and main_run(main_dir, server.url, key, "out['ok_at'] = db.get_setting(cloud_sync.SET_LAST_OK_ISO)")["ok_at"]
    assert last_ok and last_ok[:16] in page

    # eroare + operațiuni restante raportate la reconectare
    main_run(main_dir, server.url, key, """
db.set_setting(cloud_sync.SET_LAST_ERROR, "Fără conexiune la server: test")
cloud_sync._enqueue("status", {"code": "X", "status": "validat", "version": 1}, tracking_code="X")
cloud_sync._enqueue("status", {"code": "Y", "status": "respins", "version": 1}, tracking_code="Y")
out['reported'] = cloud_sync.report_status()
""")
    page = admin.call("GET", "/admin/monitorizare")[1].decode()
    assert f'data-testid="sync-pending-{cid}">2' in page
    assert "Fără conexiune la server: test" in page and "rezolvată" not in page

    # după o sincronizare reușită eroarea rămâne în istoric, marcată ca rezolvată
    main_run(main_dir, server.url, key, "db.set_setting(cloud_sync.SET_LAST_ERROR, '')\nconn = db.get_connection(); conn.execute('DELETE FROM cloud_sync_outbox'); conn.close()\nout['r'] = cloud_sync.sync_all()['ok']")
    page = admin.call("GET", "/admin/monitorizare")[1].decode()
    assert f'data-testid="sync-pending-{cid}">0' in page and "rezolvată" in page

    # raport vechi => marcat explicit ca informație veche
    _server_py(server.data_dir, """
import json, datetime
store.bind_source(server.DEFAULT_SOURCE)
clients = json.loads(store.get_setting('sync_clients'))
old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)).isoformat(timespec='seconds')
for c in clients.values():
    c['reported_at'] = c['last_seen'] = old
store.set_setting('sync_clients', json.dumps(clients))
print('ok')
""")
    page = admin.call("GET", "/admin/monitorizare")[1].decode()
    assert f'data-testid="sync-stale-{cid}"><b>Offline</b> · <b>Informație veche</b>' in page
    assert 'data-testid="sync-offline-alert"' in page and f'data-testid="sync-offline-{cid}"' in page and "peste 60 minute" in page


def test_status_endpoint_requires_key_validates_input_and_sanitizes(online):
    server, key, _ = online
    hdr = {"X-Sync-Key": key, "X-Sync-Client": "main-test"}
    assert server.request("POST", "/api/sync/status", {"pending": 1})[0] == 401
    assert server.request("POST", "/api/sync/status", {"pending": -3}, headers=hdr)[0] == 422
    assert server.request("POST", "/api/sync/status", {"pending": "abc"}, headers=hdr)[0] == 422
    assert server.request("POST", "/api/sync/status", {"pending": 4, "host": "PC<script>", "last_error": "x" * 900}, headers=hdr)[0] == 200
    admin = _admin(server)
    page = admin.call("GET", "/admin/monitorizare")[1].decode()
    assert "main-test" in page and "PC<script>" not in page and "PC&lt;script&gt;" in page and "x" * 301 not in page


def test_local_mode_server_has_no_sync_api_and_no_sync_key(tmp_path):
    server = Server(tmp_path / "local", mode="local")
    server.data_dir.mkdir()
    server.start()
    try:
        assert server.request("GET", "/api/sync/ping", headers={"X-Sync-Key": "cimitir.x"})[0] == 404
        assert server.request("POST", "/api/sync/status", {"pending": 0}, headers={"X-Sync-Key": "cimitir.x"})[0] == 404
        admin = _admin(server)
        assert admin.call("POST", "/admin/ui/sync-key")[0] == 404
    finally:
        server.stop()


# ------------------------------------------------------------ modul online prin HTTPS
def _self_signed(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "server-cert.pem", tmp_path / "server-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    return cert_path, key_path


class HttpsServer(Server):
    def __init__(self, data_dir, cert, key):
        super().__init__(data_dir)
        self.cert, self.key = cert, key
        self.url = f"https://127.0.0.1:{self.port}"
        self.ctx = ssl.create_default_context(cafile=str(cert))

    def start(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server:app", "--app-dir", str(BACKEND), "--host", "127.0.0.1", "--port", str(self.port),
             "--log-level", "warning", "--ssl-keyfile", str(self.key), "--ssl-certfile", str(self.cert)],
            env=_env(self.data_dir, CIMITIR_MODE="online"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                self.get("/api/public/health")
                return self
            except Exception:
                time.sleep(0.2)
        raise AssertionError("serverul HTTPS nu a pornit")

    def request(self, method, path, body=None, headers=None, form=None):
        data = json.dumps(body).encode() if body is not None else (
            "&".join(f"{k}={v}" for k, v in form.items()).encode() if form else None)
        hdrs = {"Content-Type": "application/json" if body is not None else "application/x-www-form-urlencoded", **(headers or {})}
        try:
            with urllib.request.urlopen(urllib.request.Request(self.url + path, data=data, method=method, headers=hdrs), timeout=15, context=self.ctx) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw)
        except urllib.error.HTTPError as err:
            raw = err.read()
            return err.code, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw)


def test_online_mode_main_to_server_over_https_full_request_flow(tmp_path, monkeypatch):
    cert, key_file = _self_signed(tmp_path)
    server = HttpsServer(tmp_path / "server", cert, key_file)
    server.data_dir.mkdir()
    key = _server_py(server.data_dir, KEY_CMD)
    server.start()
    main_dir = tmp_path / "main"
    main_dir.mkdir()
    try:
        # fără certificat de încredere, MAIN refuză conexiunea și păstrează operațiunile
        monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        untrusted = sync(main_dir, server, key, SEED.replace('"Ocupat"', '"Liber"'))
        assert untrusted["report"]["ok"] is False and server.get("/api/public/map")["map_version"] == 0

        # cu CA-ul serverului: hartă, cerere din portal, preluare, aprobare în MAIN, rezultat în portal
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(cert))
        first = sync(main_dir, server, key)
        assert first["report"]["ok"] is True and server.get("/api/public/map")["map_version"] == 1
        code = _apply(server)
        got = sync(main_dir, server, key)
        assert got["report"]["cereri_noi"] == 1
        assert server.get(f"/api/public/cerere/{code}")["status"] == "nou"
        done = sync(main_dir, server, key, "import cimitir_requests\nc = db.list_cereri_concesionare()[0]\nout['r'] = cimitir_requests.approve_cerere(c['id'])")
        assert done["report"]["ok"] and done["pending"] == 0
        assert server.get(f"/api/public/cerere/{code}")["status"] == "validat"
        assert {p["cod_loc"]: p["disponibilitate"] for p in server.get("/api/public/map")["plots"]}["A-1"] == "rezervat"
        assert main_run(main_dir, server.url, key, "out['cid'] = cloud_sync.client_id()")["cid"].startswith("main-")
    finally:
        server.stop()


# ------------------------------------------------------------ limitare de rată
def test_sync_key_bad_attempts_are_throttled_per_ip_including_unknown_cemetery_and_malformed(online):
    server, key, _ = online
    bad = ["cimitir.gresit", "fara-punct", "necunoscut.abc", "cimitir.", ".x"]
    codes = [server.request("GET", "/api/sync/ping", headers={"X-Sync-Key": k})[0] for k in bad]
    assert codes == [401] * 5
    status, _ = server.request("GET", "/api/sync/ping", headers={"X-Sync-Key": "cimitir.iar-gresit"})
    assert status == 429
    req = urllib.request.Request(server.url + "/api/sync/ping", headers={"X-Sync-Key": "x.y"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as err:
        assert err.code == 429 and int(err.headers["Retry-After"]) > 0
    # cheia corectă rămâne utilizabilă (MAIN legitim nu e blocat de atacator)
    assert server.request("GET", "/api/sync/ping", headers={"X-Sync-Key": key})[0] == 200


def test_portal_rate_limits_register_requests_and_login_failures_across_emails(online):
    server, _, _ = online
    statuses = [server.request("POST", "/api/public/account/register", {"email": f"u{i}@example.com", "password": "parola-lunga-123"})[0]
                for i in range(12)]
    assert statuses[:10] == [200] * 10 and statuses[10:] == [429, 429]

    login = [server.request("POST", "/api/public/account/login", {"email": f"x{i}@example.com", "password": "gresita-123456"})[0] for i in range(24)]
    assert login[:20] == [401] * 20 and set(login[20:]) == {429}

    sent = [server.request("POST", "/api/public/cerere", form={"nume": "A", "prenume": "B", "privacy_ack": "1"})[0] for i in range(22)]
    assert sent[:20] == [200] * 20 and sent[20:] == [429, 429]
    # alte rute publice nu sunt afectate
    assert server.request("GET", "/api/public/health")[0] == 200


def _apply_from(server, ip):
    return server.request("POST", "/api/public/cerere", form={"nume": "A", "prenume": "B", "privacy_ack": "1"},
                          headers={"X-Forwarded-For": ip})[0]


def test_x_forwarded_for_is_ignored_unless_uvicorn_runs_behind_a_trusted_proxy(tmp_path):
    direct = Server(tmp_path / "direct", args=("--no-proxy-headers",))
    direct.data_dir.mkdir()
    direct.start()
    try:
        statuses = [_apply_from(direct, f"10.0.0.{i}") for i in range(22)]
        assert statuses[:20] == [200] * 20 and statuses[20:] == [429, 429], "rotirea antetului nu trebuie să ocolească limita"
        assert _apply_from(direct, "9.9.9.9") == 429
    finally:
        direct.stop()


def test_behind_trusted_proxy_each_forwarded_client_gets_its_own_budget(tmp_path):
    proxied = Server(tmp_path / "proxied", args=("--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"))
    proxied.data_dir.mkdir()
    proxied.start()
    try:
        assert [_apply_from(proxied, "10.0.0.1") for _ in range(21)][-1] == 429
        assert _apply_from(proxied, "10.0.0.2") == 200
    finally:
        proxied.stop()
