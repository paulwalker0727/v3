"""Etapa 3 — portal cetățeni, cereri cu documente, preluare în MAIN, aprobare/respingere, acces, retenție, backup."""
from __future__ import annotations

import http.cookiejar
import importlib.util
import json
import sqlite3
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from test_etapa2_sync import SEED, Server, _server_py, main_run, sync  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PDF = b"%PDF-1.4\n%fictiv\n"
CNP_HOLDER = "1800101123456"
PRIVATE = [CNP_HOLDER, "Strada Secreta 99", "0799123456", "titular@example.com", "Popa Maria"]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Client:
    """Client HTTP cu sesiune proprie (cookie jar): cetățean sau personal."""

    def __init__(self, server):
        self.server = server
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect)

    def call(self, method, path, json_body=None, fields=None, files=None, raw=False):
        headers, data = {}, None
        if json_body is not None:
            data, headers = json.dumps(json_body).encode(), {"Content-Type": "application/json"}
        elif fields is not None or files:
            boundary = uuid.uuid4().hex
            parts = []
            for k, v in (fields or {}).items():
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
            for name, content in files or []:
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{name}"\r\n'
                             f'Content-Type: application/octet-stream\r\n\r\n'.encode() + content + b"\r\n")
            data = b"".join(parts) + f"--{boundary}--\r\n".encode()
            headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        try:
            with self.opener.open(urllib.request.Request(self.server.url + path, data=data, method=method, headers=headers), timeout=30) as r:
                body = r.read()
                return r.status, (body if raw else (json.loads(body) if body[:1] in (b"{", b"[") else body))
        except urllib.error.HTTPError as e:
            body = e.read()
            return e.code, (body if raw else (json.loads(body) if body[:1] in (b"{", b"[") else body))

    def register(self, email):
        assert self.call("POST", "/api/public/account/register", {"email": email, "password": "parola-lunga-123"})[0] == 200
        return self

    def apply(self, cod_loc="A-1", tip="concesionare", files=None, **over):
        fields = {"nume": "Popa", "prenume": "Maria", "cnp": CNP_HOLDER, "adresa": "Strada Secreta 99", "telefon": "0799123456",
                  "email": "titular@example.com", "cod_loc": cod_loc, "tip": tip, "privacy_ack": "1"}
        fields.update(over)
        return self.call("POST", "/api/public/cerere", fields=fields, files=files if files is not None else [("act.pdf", PDF)])


@pytest.fixture()
def env3(tmp_path, monkeypatch):
    monkeypatch.setenv("CIMITIR_PORTAL_REQUIRE_ACCOUNT", "1")
    monkeypatch.setenv("CIMITIR_ALLOW_DEFAULT_USERS", "1")  # doar teste: serverul online de test folosește conturile implicite
    server = Server(tmp_path / "server")
    server.data_dir.mkdir()
    key = _server_py(server.data_dir, "import sync_api; print(sync_api.issue_sync_key('cimitir'))")
    server.start()
    main_dir = tmp_path / "main"
    main_dir.mkdir()
    sync(main_dir, server, key, SEED.replace('"Ocupat"', '"Liber"'))
    yield server, key, main_dir, tmp_path
    server.stop()


def local_counts(main_dir, server, key):
    return main_run(main_dir, server.url, key, """
conn = db.get_connection()
out.update({t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ('cereri_concesionare','concesionari','contracte','inhumati')})
out['plot'] = conn.execute("SELECT disponibilitate FROM locuri_de_veci WHERE cod_loc='A-1'").fetchone()[0]
conn.close()
""")


# ------------------------------------------------------------------ flux complet
def test_full_flow_map_request_documents_main_review_and_citizen_result(env3):
    server, key, main_dir, tmp = env3
    m = server.get("/api/public/map")
    assert {p["cod_loc"]: p["disponibil_concesionare"] for p in m["plots"]} == {"A-1": True, "A-2": True}

    anon = Client(server)
    assert anon.apply()[0] == 401
    c1, c2 = Client(server).register("c1@example.com"), Client(server).register("c2@example.com")
    assert c1.apply(privacy_ack="0")[0] == 422
    status, body = c1.apply(files=[("act.pdf", PDF), ("virus.pdf", b"MZ\x90\x00 executabil"), ("x.exe", PDF), ("../../etc/passwd.pdf", PDF)])
    assert status == 200 and body["documente"] == 2 and len(body["respinse"]) == 2
    code = body["cod_urmarire"]

    # izolare între cetățeni și între cetățean / anonim / personal
    assert c2.call("GET", "/api/public/account/cereri")[1]["cereri"] == []
    assert c2.call("GET", f"/api/public/account/cereri/{code}")[0] == 404
    detail = c1.call("GET", f"/api/public/account/cereri/{code}")[1]
    doc_id = detail["documente"][0]["id"]
    assert c2.call("GET", f"/api/public/account/cereri/{code}/documents/{doc_id}")[0] == 404
    assert anon.call("GET", f"/api/public/account/cereri/{code}/documents/{doc_id}")[0] == 401
    status, content = c1.call("GET", f"/api/public/account/cereri/{code}/documents/{doc_id}", raw=True)
    assert status == 200 and content.startswith(b"%PDF")
    assert anon.call("GET", f"/api/public/cerere/{code}")[1].keys() == {"cod_urmarire", "status", "status_label", "depusa_la", "actualizata_la"}
    assert anon.call("GET", f"/admin/file/document/{doc_id}")[0] in (303, 401)
    assert c1.call("GET", "/admin/")[0] == 303

    # preluare în MAIN (documente verificate, ack după salvare), fără duplicate
    r = sync(main_dir, server, key)
    assert r["report"]["ok"] and r["report"]["cereri_noi"] == 1
    out = main_run(main_dir, server.url, key, """
c = db.list_cereri_concesionare()[0]
docs = db.list_documents('cerere', c['id'])
out.update(tip=c['tip'], n=len(docs), pdf=all(open(db.get_document_path(d['id']),'rb').read().startswith(b'%PDF') for d in docs), cod=c['cod_urmarire'])
""")
    assert out["tip"] == "concesionare" and out["n"] == 2 and out["pdf"] and out["cod"] == code
    after = c1.call("GET", f"/api/public/account/cereri/{code}")[1]
    assert all(d["disponibil_pentru_descarcare"] is False for d in after["documente"])
    assert c1.call("GET", f"/api/public/account/cereri/{code}/documents/{doc_id}")[0] == 410
    conn = sqlite3.connect(server.data_dir / "cimitir.db")
    assert conn.execute("SELECT nume, cnp, adresa, acked FROM cereri_concesionare").fetchone() == ("", "", "", 1)

    # completări: MAIN cere, cetățeanul răspunde, MAIN primește fără duplicat
    sync(main_dir, server, key, "c = db.list_cereri_concesionare()[0]\ndb.set_cerere_status(c['id'], 'completari', 'Lipsește copia CI', None)")
    view = c1.call("GET", f"/api/public/account/cereri/{code}")[1]
    assert view["status"] == "completari" and view["mesaj"] == "Lipsește copia CI"
    assert c2.call("POST", f"/api/public/account/cereri/{code}/completari", fields={"mesaj": "x"})[0] == 404
    assert c1.call("POST", f"/api/public/account/cereri/{code}/completari", fields={"mesaj": "Atașez CI"}, files=[("ci.pdf", PDF)])[0] == 200
    assert c1.call("POST", f"/api/public/account/cereri/{code}/completari", fields={"mesaj": "din nou"})[0] == 409
    r = sync(main_dir, server, key)
    assert r["report"]["ok"] and r["report"]["cereri_noi"] == 0
    out = main_run(main_dir, server.url, key, """
rows = db.list_cereri_concesionare(); c = rows[0]
out.update(n=len(rows), status=c['status'], obs=c['observatii'], docs=len(db.list_documents('cerere', c['id'])))
""")
    assert out["n"] == 1 and out["status"] == "nou" and "Atașez CI" in out["obs"] and out["docs"] == 3

    # aprobare în MAIN: draft contract, loc rezervat, repetare fără duplicate, rezultat la cetățean
    approve = "import cimitir_requests\nc = db.list_cereri_concesionare()[0]\nout['r'] = cimitir_requests.approve_cerere(c['id'])\nout['r2'] = cimitir_requests.approve_cerere(c['id'])\n"
    out = sync(main_dir, server, key, approve)
    assert out["r"]["already"] is False and out["r2"]["already"] is True and out["r"]["contract_id"] == out["r2"]["contract_id"]
    counts = local_counts(main_dir, server, key)
    assert (counts["concesionari"], counts["contracte"], counts["plot"]) == (1, 1, "Rezervat")
    sync(main_dir, server, key)
    sync(main_dir, server, key)
    assert local_counts(main_dir, server, key)["contracte"] == 1
    final = c1.call("GET", f"/api/public/account/cereri/{code}")[1]
    assert final["status"] == "validat" and "Aprobată" in final["status_label"] and "contract #" not in final["mesaj"]
    m = {p["cod_loc"]: p for p in server.get("/api/public/map")["plots"]}
    assert m["A-1"]["disponibilitate"] == "rezervat"
    assert c2.apply("A-1")[0] == 409

    # respingere cu motiv, vizibilă în contul cetățeanului
    status, body2 = c2.apply("A-2", files=[])
    assert status == 200
    sync(main_dir, server, key)
    sync(main_dir, server, key, "rows = [c for c in db.list_cereri_concesionare() if c['status']=='nou']\ndb.set_cerere_status(rows[0]['id'], 'respins', 'Acte incomplete', None)")
    rej = c2.call("GET", f"/api/public/account/cereri/{body2['cod_urmarire']}")[1]
    assert rej["status"] == "respins" and rej["mesaj"] == "Acte incomplete"

    blob = json.dumps([server.get("/api/public/map"), server.get("/api/public/loc/A-1"), server.get("/api/public/search?q=Popa"),
                       server.get("/api/public/privacy"), server.get(f"/api/public/cerere/{code}")])
    assert not any(s in blob for s in PRIVATE)


# ---------------------------------------------------- concurență, reluare, transferuri
def test_concurrent_requests_same_plot_never_double_assign(env3):
    server, key, main_dir, _ = env3
    c1, c2 = Client(server).register("a@example.com"), Client(server).register("b@example.com")
    assert c1.apply("A-1")[0] == 200 and c2.apply("A-1", cnp="2900202654321", nume="Ionescu")[0] == 200
    sync(main_dir, server, key)
    ids = main_run(main_dir, server.url, key, "out['ids'] = [c['id'] for c in db.list_cereri_concesionare()]")["ids"]
    code = """
import cimitir_requests
try:
    out['r'] = cimitir_requests.approve_cerere(%d)
except cimitir_requests.ApprovalError as e:
    out['err'] = str(e)
"""
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda i: main_run(main_dir, server.url, key, code % i), ids))
    assert sorted("r" in r for r in results) == [False, True]
    counts = local_counts(main_dir, server, key)
    assert (counts["contracte"], counts["concesionari"], counts["plot"]) == (1, 1, "Rezervat")


def test_approval_rechecks_availability_and_rolls_back(env3):
    server, key, main_dir, _ = env3
    Client(server).register("a@example.com").apply("A-1")
    sync(main_dir, server, key)
    out = main_run(main_dir, server.url, key, """
import cimitir_requests
db.save_loc_metadata('A-1', {'disponibilitate': 'Ocupat'})
c = db.list_cereri_concesionare()[0]
try:
    cimitir_requests.approve_cerere(c['id'])
except cimitir_requests.ApprovalError as e:
    out['err'] = str(e)
out['status'] = db.get_cerere_concesionare(c['id'])['status']
""")
    assert "nu mai este disponibil" in out["err"] and out["status"] == "nou"
    counts = local_counts(main_dir, server, key)
    assert counts["concesionari"] == 0 and counts["contracte"] == 0


def test_partial_transfer_is_not_acked_and_resumes_without_duplicates(env3):
    server, key, main_dir, _ = env3
    code = Client(server).register("a@example.com").apply("A-1")[1]["cod_urmarire"]
    conn = sqlite3.connect(server.data_dir / "cimitir.db")
    path = Path(conn.execute("SELECT file_path FROM documents WHERE file_path<>''").fetchone()[0])
    original = path.read_bytes()
    path.write_bytes(original + b"CORUPT")
    r = sync(main_dir, server, key)
    assert r["report"]["ok"] is False
    assert conn.execute("SELECT acked, nume FROM cereri_concesionare").fetchone() == (0, "Popa")
    path.write_bytes(original)
    assert sync(main_dir, server, key)["report"]["ok"]
    assert conn.execute("SELECT acked, nume FROM cereri_concesionare").fetchone() == (1, "")
    out = main_run(main_dir, server.url, key, "c = db.list_cereri_concesionare()\nout['n'] = len(c); out['docs'] = len(db.list_documents('cerere', c[0]['id']))")
    assert out == {"n": 1, "docs": 1}
    assert code


def test_server_restart_between_pull_and_ack_keeps_single_request(env3):
    server, key, main_dir, tmp = env3
    Client(server).register("a@example.com").apply("A-1")
    server.stop()
    assert sync(main_dir, server, key)["report"]["ok"] is False
    server2 = Server(server.data_dir)
    server2.port, server2.url = server.port, server.url
    server2.start()
    try:
        sync(main_dir, server2, key)
        sync(main_dir, server2, key)
        assert local_counts(main_dir, server2, key)["cereri_concesionare"] == 1
    finally:
        server2.stop()


# ---------------------------------------------------------- drept asupra locului
def test_update_burial_request_requires_holder_and_main_applies_once(env3):
    server, key, main_dir, _ = env3
    sync(main_dir, server, key, """
cid = db.save_concesionar({"nume": "Popa", "prenume": "Maria", "cnp": "%s"})
db.save_contract({"numar_contract": "C1", "cod_loc": "A-2", "concesionar_id": cid, "data_sfarsit": "2099-01-01"})
""" % CNP_HOLDER)
    c = Client(server).register("a@example.com")
    extra = {"nume_decedat": "Decedat", "prenume_decedat": "Fictiv", "data_deces": "01.02.2024"}
    assert c.apply("A-2", "actualizare_inhumat", cnp="2900202654321", **extra)[0] == 403
    assert c.apply("A-1", "actualizare_inhumat", **extra)[0] == 403
    assert c.apply("A-2", "actualizare_inhumat", cnp="123", **extra)[0] == 422
    status, body = c.apply("A-2", "actualizare_inhumat", **extra)
    assert status == 200
    sync(main_dir, server, key)
    out = main_run(main_dir, server.url, key, """
import cimitir_requests
c = db.list_cereri_concesionare()[0]
a = cimitir_requests.approve_cerere(c['id']); b = cimitir_requests.approve_cerere(c['id'])
out['already'] = [a['already'], b['already']]
out['n'] = len(db.load_all_inhumati())
""")
    assert out["already"] == [False, True] and out["n"] == 1


# ------------------------------------------------ documente, conturi, retenție, jurnal
def test_upload_limits_private_storage_and_no_static_access(env3):
    server, key, main_dir, _ = env3
    c = Client(server).register("a@example.com")
    status, body = c.apply("A-1", files=[("mare.pdf", PDF + b"0" * (10 * 1024 * 1024 + 10)), ("ok.png", b"\x89PNG\r\n\x1a\n123")])
    assert status == 200 and body["documente"] == 1 and "10 MB" in body["respinse"][0]
    conn = sqlite3.connect(server.data_dir / "cimitir.db")
    path = Path(conn.execute("SELECT file_path FROM documents").fetchone()[0])
    assert str(path).startswith(str(server.data_dir.resolve())) and "static" not in path.parts
    for probe in (f"/static/{path.name}", f"/static/../media/{path.name}", f"/media/{path.name}"):
        assert c.call("GET", probe)[0] in (404, 401, 403)


def test_citizen_accounts_are_separate_from_staff_and_throttled(env3):
    server, key, main_dir, _ = env3
    c = Client(server).register("a@example.com")
    assert c.call("GET", "/api/admin/map.json")[0] == 401
    staff = Client(server)
    assert staff.call("POST", "/admin/access/login", fields={"username": "admin", "password": "admin123", "source_id": "cimitir"})[0] == 303
    staff.call("POST", "/admin/schimba-parola", fields={"old_password": "admin123", "new_password": "Parola-test-987", "confirm_password": "Parola-test-987"})
    assert staff.call("GET", "/api/public/account/me")[0] == 401
    assert c.call("POST", "/api/public/account/register", {"email": "a@example.com", "password": "parola-lunga-123"})[0] == 409
    assert c.call("POST", "/api/public/account/register", {"email": "x@example.com", "password": "scurta"})[0] == 422
    victim = Client(server)
    statuses = [victim.call("POST", "/api/public/account/login", {"email": "a@example.com", "password": "gresita-%d" % i})[0] for i in range(7)]
    assert statuses[:5] == [401] * 5 and 429 in statuses[5:]
    assert Client(server).call("POST", "/api/public/account/login", {"email": "a@example.com", "password": "parola-lunga-123"})[0] == 429
    other = Client(server).register("b@example.com")
    assert other.call("DELETE", "/api/public/account")[0] == 200
    assert other.call("GET", "/api/public/account/me")[0] == 401


def test_privacy_retention_audit_and_monitoring(env3):
    server, key, main_dir, _ = env3
    priv = server.get("/api/public/privacy")
    assert "ȘABLON" in priv["text"] and priv["retention_days_closed"] == 365
    c = Client(server).register("a@example.com")
    code = c.apply("A-1")[1]["cod_urmarire"]
    conn = sqlite3.connect(server.data_dir / "cimitir.db")
    assert conn.execute("SELECT privacy_version, privacy_ack_at, citizen_id FROM cereri_concesionare").fetchone()[0] == priv["version"]
    sync(main_dir, server, key)
    sync(main_dir, server, key, "import cimitir_requests\nc = db.list_cereri_concesionare()[0]\ncimitir_requests.approve_cerere(c['id'])")
    staff = Client(server)
    staff.call("POST", "/admin/access/login", fields={"username": "admin", "password": "admin123", "source_id": "cimitir"})
    staff.call("POST", "/admin/schimba-parola", fields={"old_password": "admin123", "new_password": "Parola-test-987", "confirm_password": "Parola-test-987"})
    assert staff.call("POST", "/admin/ui/settings", fields={"retention_days_closed": "1", "retention_days_rejected": "1"})[0] == 303
    page = staff.call("GET", "/admin/monitorizare", raw=True)[1].decode()
    for action in ("request_submitted", "ack", "status", "register"):
        assert action in page
    assert 'data-testid="count-validat"' in page
    conn.execute("UPDATE cereri_concesionare SET closed_at='2000-01-01T00:00:00'")
    conn.commit()
    assert staff.call("POST", "/admin/ui/purge")[0] == 303
    row = conn.execute("SELECT status, nume, cnp, citizen_id FROM cereri_concesionare").fetchone()
    assert row == ("validat", "", "", None)
    assert "purge" in staff.call("GET", "/admin/monitorizare", raw=True)[1].decode()
    _server_py(server.data_dir, "import cimitir_auth\ncimitir_auth.create_user('op','parola-op-123','operator')\nprint(1)")
    op = Client(server)
    op.call("POST", "/admin/access/login", fields={"username": "op", "password": "parola-op-123", "source_id": "cimitir"})
    assert op.call("GET", "/admin/monitorizare")[0] == 303
    assert code


# ------------------------------------------------------------ backup / restaurare
def test_backup_restore_roundtrip_and_tamper_detection(tmp_path):
    spec = importlib.util.spec_from_file_location("backup_restore", ROOT / "scripts" / "backup_restore.py")
    br = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(br)
    data = tmp_path / "data"
    (data / "media").mkdir(parents=True)
    (data / "media" / "doc.pdf").write_bytes(PDF)
    conn = sqlite3.connect(data / "cimitir.db")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES ('important')")
    conn.commit()
    target = br.backup(data, tmp_path / "bk")
    conn.close()
    assert br.verify(target) == []
    restored = br.restore(target, tmp_path / "restaurat")
    assert sqlite3.connect(restored / "cimitir.db").execute("SELECT v FROM t").fetchone()[0] == "important"
    assert (restored / "media" / "doc.pdf").read_bytes() == PDF
    with pytest.raises(SystemExit):
        br.restore(target, tmp_path / "restaurat")
    br.restore(target, tmp_path / "restaurat", force=True)
    assert list(tmp_path.glob("restaurat.inainte-de-restaurare-*"))
    (target / "media" / "doc.pdf").write_bytes(b"alterat")
    assert br.verify(target)
    with pytest.raises(SystemExit):
        br.restore(target, tmp_path / "altul")
