"""Pasul 6 — QR MAIN -> telefon (token temporar) -> upload -> Storage + PostgreSQL -> MAIN vede documentul.

Rulare (serverul de pe :8001 trebuie să fie în mod online, PostgreSQL):
    cd backend && python -m pytest tests/test_step6_mobile_upload.py -q -n 0 -s

Ce se acoperă:
  * MAIN (SQLite = cache) -> sync -> același UID pe server; MAIN cere token (X-Sync-Key) pentru UID-ul entității;
  * telefonul (doar token, HTTPS/public URL) vede entitatea, încarcă PDF; FastAPI -> Storage -> metadate PostgreSQL;
  * MAIN rulează sync și vede documentul (un singur document logic, același entity_uid); al doilea MAIN îl vede și el;
  * token expirat / refolosit / pentru altă entitate / fals / fără cheie de sincronizare => respins;
  * URL public: CIMITIR_PUBLIC_URL (https, public); localhost / IP privat respins în mod online;
  * driver Supabase (simulat) + canar secret: telefonul nu primește niciodată SUPABASE_SECRET_KEY.
Datele de test au prefix „S6-<run>” și sunt șterse la final.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import textwrap
import threading
import time
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))
sys.path.insert(0, str(Path(__file__).parent))
from test_etapa7_media import _FakeSupabase  # noqa: E402

SERVER = os.environ.get("ETAPA6_SERVER_URL", "http://127.0.0.1:8001").rstrip("/")
PY = sys.executable
RUN = uuid.uuid4().hex[:6]
PDF = b"%PDF-1.4\n" + os.urandom(2048) + b"\n%%EOF"
PDF2 = b"%PDF-1.4\n" + os.urandom(1024) + b"\n%%EOF"
CANARY = "eyJhbGciOiJIUzI1NiJ9.CANARY" + uuid.uuid4().hex     # cheie de tip „service role” (Bearer) – canar pentru scurgeri
S: dict = {}


def _server_env() -> dict:
    env = {}
    for line in (ROOT / "backend" / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _pg():
    import psycopg
    return psycopg.connect(_server_env()["DATABASE_URL"], autocommit=True)


def pg(sql, params=(), all=False):
    with _pg() as conn:
        cur = conn.execute(sql, params)
        try:
            return cur.fetchall() if all else cur.fetchone()
        except Exception:
            return None


def _server_ok() -> bool:
    try:
        r = requests.get(f"{SERVER}/api/public/status", timeout=5).json()
        return r.get("database", {}).get("engine") == "postgres" and r.get("mode") == "online"
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _server_ok(), reason="serverul online/PostgreSQL nu rulează")


def _storage():
    sys.path.insert(0, str(ROOT / "backend"))
    import cimitir_config
    cimitir_config.load_env()
    import cimitir_storage
    return cimitir_storage.get_storage()


def _storage_read(path: str) -> bytes:
    return b"".join(_storage().open_stream(path))


@pytest.fixture(scope="module")
def key():
    out = subprocess.run([PY, str(ROOT / "scripts" / "issue_sync_key.py"), "--source", "cimitir"], cwd=ROOT / "backend",
                         capture_output=True, text=True, timeout=60, env={**os.environ, **_server_env()})
    line = [ln for ln in out.stdout.splitlines() if ln.startswith("cimitir.")]
    assert line, out.stdout + out.stderr
    S["key"] = line[-1].strip()
    return S["key"]


def H(k):
    return {"X-Sync-Key": k, "X-Sync-Client": f"s6-{RUN}"}


def main_client(name: str, key: str, code: str, url: str = SERVER) -> dict:
    """Un calculator MAIN simulat: SQLite propriu (cache), desktop/* real, vorbește cu serverul doar prin API."""
    data_dir = f"/tmp/s6-{RUN}-{name}"
    script = textwrap.dedent(f"""
        import json, os, sys, hashlib
        sys.path.insert(0, {str(ROOT / 'desktop')!r})
        import database as db, cloud_sync, entity_sync, media_sync, mobile_upload
        db.init_db()
        cloud_sync.save_config({url!r}, {key!r}, False, False)
        out = {{}}
        def sha(p): return hashlib.sha256(open(p, 'rb').read()).hexdigest()
    """) + textwrap.dedent(code) + "\nprint('RESULT=' + json.dumps(out, default=str))\n"
    env = {**os.environ, "CIMITIR_DATA_DIR": data_dir, "CIMITIR_MODE": "local", "CIMITIR_DB_ENGINE": "sqlite", "PYTHONPATH": str(ROOT / "shared")}
    env.pop("DATABASE_URL", None)
    p = subprocess.run([PY, "-c", script], capture_output=True, text=True, env=env, timeout=240, cwd=ROOT / "desktop")
    assert p.returncode == 0, p.stdout[-1500:] + p.stderr[-2500:]
    return json.loads([ln for ln in p.stdout.splitlines() if ln.startswith("RESULT=")][-1][len("RESULT="):])


def local(url: str, base: str = SERVER) -> str:
    """URL public (https://…/api/m/<token>) -> același drum, dar prin serverul local al testului."""
    return base + url[url.index("/api/m/"):]


def token_of(url: str) -> str:
    return url.split("/api/m/", 1)[1]


def upload(url, data=PDF, name="doc.pdf", base=SERVER, **form):
    return requests.post(local(url, base) + "/upload", files={"file": (name, data, "application/pdf")},
                         data={"doc_type": "alt_document", **form}, timeout=30)


def _unlock():
    with _pg() as conn:
        conn.execute("UPDATE settings SET value='0,0' WHERE key LIKE %s", ("%mobile:%",))


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    if _server_ok():
        _unlock()
    yield
    if _server_ok():
        _unlock()
    if not _server_ok():
        return
    with _pg() as conn:
        for (path,) in conn.execute("SELECT m.storage_path FROM media_objects m JOIN locuri_de_veci l ON l.uid = m.entity_uid "
                                    "WHERE l.cod_loc LIKE %s", (f"S6-{RUN}%",)).fetchall():
            try:
                _storage().delete(path)
            except Exception:
                pass
        for uid in S.get("six_uids", []):
            for (path,) in conn.execute("SELECT storage_path FROM media_objects WHERE entity_uid=%s", (uid,)).fetchall():
                try:
                    _storage().delete(path)
                except Exception:
                    pass
            conn.execute("DELETE FROM documents WHERE media_id IN (SELECT id FROM media_objects WHERE entity_uid=%s)", (uid,))
            conn.execute("DELETE FROM media_objects WHERE entity_uid=%s", (uid,))
        conn.execute("DELETE FROM plati WHERE chitanta_nr=%s", (f"S6-{RUN}-P",))
        conn.execute("DELETE FROM amenajari_avize WHERE nr_aviz=%s", (f"S6-{RUN}-A",))
        conn.execute("DELETE FROM inhumati WHERE nume=%s", (f"S6{RUN}",))
        conn.execute("DELETE FROM contracte WHERE numar_contract=%s", (f"S6-{RUN}-C",))
        conn.execute("DELETE FROM concesionari WHERE nume=%s", (f"S6{RUN}",))
        conn.execute("DELETE FROM media_objects WHERE entity_uid IN (SELECT uid FROM locuri_de_veci WHERE cod_loc LIKE %s)", (f"S6-{RUN}%",))
        conn.execute("DELETE FROM documents WHERE entity_type='loc' AND entity_id LIKE %s", (f"S6-{RUN}%",))
        conn.execute("DELETE FROM mobile_upload_tokens WHERE entity_id LIKE %s", (f"S6-{RUN}%",))
        conn.execute("DELETE FROM locuri_de_veci WHERE cod_loc LIKE %s", (f"S6-{RUN}%",))


# =========================================================================== 1. UID MAIN <-> SERVER
def test_01_main_entities_reach_server_with_the_same_uid(key):
    res = main_client("A", key, f"""
        conn = db.get_connection()
        for c in ('S6-{RUN}-L1', 'S6-{RUN}-L2'):
            conn.execute("INSERT INTO locuri_de_veci (cod_loc, points, sector) VALUES (?, '[[0,0],[1,0],[1,1]]', 'A')", (c,))
        conn.close()
        ok, msg = cloud_sync.sync_entities()
        out['ok'] = ok; out['msg'] = msg
        conn = db.get_connection()
        out['uids'] = {{r['cod_loc']: r['uid'] for r in conn.execute("SELECT cod_loc, uid FROM locuri_de_veci WHERE cod_loc LIKE 'S6-{RUN}%'")}}
        conn.close()
    """)
    assert res["ok"], res
    for cod, uid in res["uids"].items():
        assert uid and pg("SELECT uid FROM locuri_de_veci WHERE cod_loc=%s", (cod,))[0] == uid       # UID desktop == UID server
    S["uid1"], S["uid2"] = res["uids"][f"S6-{RUN}-L1"], res["uids"][f"S6-{RUN}-L2"]


# =========================================================================== 2. QR / TOKEN
def test_02_main_requests_qr_token_bound_to_entity_uid_and_public_https_url(key):
    res = main_client("A", key, f"""
        t = mobile_upload.request_token('loc', 'S6-{RUN}-L1', issued_by='main-test', ttl_minutes=60)
        out['tok'] = {{k: t[k] for k in ('tid', 'url', 'expires_in', 'max_uses', 'lan', 'entity')}}
        out['qr_len'] = len(t.get('qr_png') or '')
        out['qr_magic'] = (mobile_upload.qr_png_bytes(t['qr_png']) or b'')[:4].hex()
        out['status'] = mobile_upload.token_status(t['tid'])['state']
    """)
    t = res["tok"]
    S.update(tok1=t, url1=t["url"])
    public = _server_env()["CIMITIR_PUBLIC_URL"].rstrip("/")
    assert t["url"].startswith(public + "/api/m/") and t["url"].startswith("https://")      # folosește CIMITIR_PUBLIC_URL
    assert not re.search(r"localhost|127\.0\.0\.1|192\.168\.|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.", t["url"]) and t["lan"] is False
    assert t["expires_in"] == 600 and 300 <= t["expires_in"] <= 600 and t["max_uses"] == 1           # ttl cerut 60 min => plafonat la 10
    assert t["entity"]["uid"] == S["uid1"] and t["entity"]["type"] == "loc"                           # legat de UID-ul MAIN
    assert res["qr_magic"] == "89504e47" and res["qr_len"] > 200 and res["status"] == "active"
    # URL-ul nu conține parole / chei: doar <cimitir>.<secret aleator>; cheia de sincronizare NU apare
    secret_part = token_of(t["url"])
    assert S["key"].split(".", 1)[1] not in t["url"] and "key" not in secret_part.lower().replace("=", "")
    # în PostgreSQL se păstrează doar hash-ul; secretul în clar nu apare în nicio coloană
    row = pg("SELECT row_to_json(t)::text FROM mobile_upload_tokens t WHERE tid=%s", (t["tid"],))[0]
    assert secret_part.split(".", 1)[1] not in row and json.loads(row)["entity_uid"] == S["uid1"]
    # ttl sub 5 min => 5 min (prin API direct)
    r = requests.post(f"{SERVER}/api/sync/mobile/tokens", headers=H(S["key"]),
                      json={"entity_type": "loc", "entity_uid": S["uid2"], "ttl_minutes": 1}, timeout=15).json()
    assert r["expires_in"] == 300
    S["url2"] = r["url"]


# =========================================================================== 3. TELEFON -> UPLOAD
def test_03_phone_sees_entity_uploads_pdf_to_storage_and_metadata_in_postgres(key):
    page = requests.get(local(S["url1"]), timeout=15)
    assert page.status_code == 200 and "text/html" in page.headers["content-type"] and page.headers["cache-control"] == "no-store"
    assert f"S6-{RUN}-L1" in page.text and "capture=\"environment\"" in page.text                  # vede entitatea; cameră + fișier
    info = requests.get(local(S["url1"]) + "/info", timeout=15).json()
    assert info["entity"]["title"] == f"S6-{RUN}-L1" and info["entity"]["type"] == "loc" and 0 < info["expires_in"] <= 600
    for blob in (page.text, json.dumps(info)):
        assert not re.search(r"SUPABASE|sb_secret|DATABASE_URL|postgresql://|X-Sync-Key|storage_path|JWT_SECRET", blob, re.I)

    r = upload(S["url1"], description="foto telefon")
    assert r.status_code == 200, r.text
    doc = r.json()["document"]
    assert doc["filename"] == "doc.pdf" and doc["size"] == len(PDF) and r.json()["remaining_uses"] == 0
    d = pg("SELECT entity_type, entity_id, source, uploaded_by, sha256, size, media_id, description FROM documents WHERE id=%s", (doc["id"],))
    assert d[:3] == ("loc", f"S6-{RUN}-L1", "mobile") and d[3] == "mobile:main-test" and d[4] == hashlib.sha256(PDF).hexdigest()
    assert d[5] == len(PDF) and d[6] and d[7] == "foto telefon"
    m = pg("SELECT entity_uid, filename, storage_path, mime, size, sha256, uploaded_by, uploaded_at FROM media_objects WHERE id=%s", (d[6],))
    assert m[0] == S["uid1"] and m[3] == "application/pdf" and m[4] == len(PDF) and m[5] == d[4] and m[6] == "mobile:main-test" and m[7]
    assert m[2] == f"documents/loc/alt_document/{S['uid1']}/{d[6]}/doc.pdf"
    assert hashlib.sha256(_storage_read(m[2])).hexdigest() == d[4]                                    # octeții sunt în Storage (driverul configurat)
    assert pg("SELECT count(*) FROM information_schema.columns WHERE table_name='media_objects' AND data_type='bytea'")[0] == 0
    a = pg("SELECT actor_type, actor, detail FROM audit_log WHERE action='attachment.add' AND target=%s ORDER BY id DESC LIMIT 1", (f"loc:S6-{RUN}-L1",))
    assert a[0] == "mobile" and a[1] == "mobile:main-test" and json.loads(a[2])["source"] == "mobile"  # auditul arată sursa mobile
    S.update(doc_id=doc["id"], media_id=d[6], sha=d[4])


# =========================================================================== 4. MAIN VEDE DOCUMENTUL
def test_04_main_sees_the_document_after_sync_single_logical_copy_same_uid(key):
    res = main_client("A", key, f"""
        st, msg = mobile_upload.fetch_uploaded({S['tok1']['tid']!r})
        out['state'], out['uses'], out['docs'] = st['state'], st['uses'], [d['filename'] for d in st['documents']]
        docs = db.list_documents('loc', 'S6-{RUN}-L1')
        out['n_docs'] = len(docs)
        conn = db.get_connection()
        r = conn.execute("SELECT media_id, sha256 FROM documents WHERE entity_type='loc' AND entity_id='S6-{RUN}-L1'").fetchall()
        out['rows'] = [(x['media_id'], x['sha256']) for x in r]
        conn.close()
        path = db.get_document_path(docs[0]['id']) if docs else ''
        out['file_sha'] = sha(path) if path and os.path.exists(path) else ''
        cloud_sync.sync_entities(); cloud_sync.sync_media(); cloud_sync.sync_media()    # încă două cicluri: nu trebuie să apară dubluri
        out['n_after'] = len(db.list_documents('loc', 'S6-{RUN}-L1'))
    """)
    assert res["uses"] == 1 and res["state"] == "used" and res["docs"] == ["doc.pdf"], res
    assert res["n_docs"] == 1 and res["n_after"] == 1 and res["file_sha"] == S["sha"], res        # MAIN vede documentul, o singură copie
    assert res["rows"][0][0] == S["media_id"]                                                      # același media_id ca pe server
    assert pg("SELECT count(*) FROM documents WHERE entity_type='loc' AND entity_id=%s", (f"S6-{RUN}-L1",))[0] == 1
    assert pg("SELECT count(*) FROM media_objects WHERE entity_uid=%s", (S["uid1"],))[0] == 1       # nicio a doua copie logică pe server
    # al doilea calculator MAIN: același document, același UID de entitate
    b = main_client("B", S["key"], f"""
        cloud_sync.sync_entities(); cloud_sync.sync_media()
        docs = db.list_documents('loc', 'S6-{RUN}-L1'); out['n'] = len(docs)
        conn = db.get_connection(); out['uid'] = conn.execute("SELECT uid FROM locuri_de_veci WHERE cod_loc='S6-{RUN}-L1'").fetchone()['uid']; conn.close()
        out['sha'] = sha(db.get_document_path(docs[0]['id'])) if docs else ''
    """)
    assert b["n"] == 1 and b["uid"] == S["uid1"] and b["sha"] == S["sha"], b


# =========================================================================== 5. REFOLOSIRE / EXPIRARE / ENTITATE GREȘITĂ
def test_05_reused_token_is_rejected(key):
    r = upload(S["url1"], data=PDF2, name="al-doilea.pdf")
    assert r.status_code == 410 and r.json()["detail"]["error"] == "used"
    assert requests.get(local(S["url1"]) + "/info", timeout=15).status_code == 410
    assert pg("SELECT count(*) FROM documents WHERE entity_type='loc' AND entity_id=%s", (f"S6-{RUN}-L1",))[0] == 1


def test_06_expired_token_is_rejected(key):
    t = requests.post(f"{SERVER}/api/sync/mobile/tokens", headers=H(key), json={"entity_type": "loc", "entity_uid": S["uid1"]}, timeout=15).json()
    pg("UPDATE mobile_upload_tokens SET expires_at = expires_at - 3600 WHERE tid=%s", (t["tid"],))
    for resp in (requests.get(local(t["url"]), timeout=15), requests.get(local(t["url"]) + "/info", timeout=15), upload(t["url"])):
        assert resp.status_code == 410 and "expir" in json.dumps(resp.json()).lower(), resp.text
    assert requests.get(f"{SERVER}/api/sync/mobile/tokens/{t['tid']}", headers=H(key), timeout=15).json()["state"] == "expired"
    assert pg("SELECT count(*) FROM documents WHERE entity_type='loc' AND entity_id=%s", (f"S6-{RUN}-L1",))[0] == 1


def test_07_token_for_entity_A_cannot_attach_to_entity_B(key):
    url2 = S["url2"]                                                    # token valid pentru L2
    for extra in ({"entity_id": f"S6-{RUN}-L1"}, {"entity_type": "contract"}, {"entity_uid": S["uid1"]}):
        r = upload(url2, **extra)
        assert r.status_code == 403 and r.json()["detail"]["error"] == "entity_mismatch", (extra, r.text)
    # nicio cale de a schimba ținta: documentul, dacă se acceptă, ajunge DOAR la L2
    assert pg("SELECT count(*) FROM documents WHERE entity_id=%s", (f"S6-{RUN}-L2",))[0] == 0
    assert pg("SELECT count(*) FROM documents WHERE entity_type='loc' AND entity_id=%s", (f"S6-{RUN}-L1",))[0] == 1
    ok = upload(url2, name="pentru-l2.pdf")
    assert ok.status_code == 200
    assert pg("SELECT entity_id FROM documents WHERE id=%s", (ok.json()["document"]["id"],))[0] == f"S6-{RUN}-L2"
    assert pg("SELECT count(*) FROM documents WHERE entity_type='loc' AND entity_id=%s", (f"S6-{RUN}-L1",))[0] == 1
    a = pg("SELECT count(*) FROM audit_log WHERE action='mobile.reject' AND detail LIKE '%%entity_mismatch%%' AND target=%s", (f"loc:S6-{RUN}-L2",))[0]
    assert a >= 3


def test_08_invalid_file_does_not_consume_the_token_and_bad_type_rejected(key):
    t = requests.post(f"{SERVER}/api/sync/mobile/tokens", headers=H(key), json={"entity_type": "loc", "entity_uid": S["uid1"]}, timeout=15).json()
    bad = requests.post(local(t["url"]) + "/upload", files={"file": ("x.exe", b"MZ\x00\x00", "application/octet-stream")}, timeout=15)
    fake = requests.post(local(t["url"]) + "/upload", files={"file": ("x.pdf", b"nu e pdf", "application/pdf")}, timeout=15)
    assert bad.status_code == 422 and fake.status_code == 422
    assert requests.get(f"{SERVER}/api/sync/mobile/tokens/{t['tid']}", headers=H(key), timeout=15).json()["state"] == "active"
    assert upload(t["url"], name="bun.pdf").status_code == 200


def test_09_no_valid_token_or_key_means_no_upload(key):
    # fără cheie de sincronizare / cheie greșită: nu se poate emite token (numărate separat de limitarea telefonului)
    assert requests.post(f"{SERVER}/api/sync/mobile/tokens", json={"entity_type": "loc", "entity_uid": S["uid1"]}, timeout=15).status_code == 401
    assert requests.post(f"{SERVER}/api/sync/mobile/tokens", headers=H("cimitir.gresita"), json={"entity_type": "loc", "entity_uid": S["uid1"]},
                         timeout=15).status_code in (401, 429)
    # entitate necunoscută pe server => 404, nu token
    assert requests.post(f"{SERVER}/api/sync/mobile/tokens", headers=H(key), json={"entity_type": "loc", "entity_uid": "uid-inexistent"},
                         timeout=15).status_code == 404
    assert requests.post(f"{SERVER}/api/sync/mobile/tokens", headers=H(key), json={"entity_type": "cerere", "entity_uid": "x"}, timeout=15).status_code == 422
    # telefon: token inventat / modificat / lipsă => respins, nimic scris
    before = pg("SELECT count(*) FROM documents")[0]
    forged = S["url1"].rsplit(".", 1)[0] + "." + "A" * 43
    assert upload(forged).status_code == 404
    assert upload(S["url1"].replace("/api/m/", "/api/m/x")).status_code == 404
    assert requests.post(f"{SERVER}/api/m/upload", files={"file": ("a.pdf", PDF)}, timeout=15).status_code in (404, 405, 422)
    assert pg("SELECT count(*) FROM documents")[0] == before


def test_10_revoked_token_stops_working(key):
    t = requests.post(f"{SERVER}/api/sync/mobile/tokens", headers=H(key), json={"entity_type": "loc", "entity_uid": S["uid1"]}, timeout=15).json()
    assert requests.delete(f"{SERVER}/api/sync/mobile/tokens/{t['tid']}", headers=H(key), timeout=15).json()["ok"]
    assert upload(t["url"]).status_code == 410


def test_11_public_https_path_reaches_the_phone_page(key):
    """Calea reală de internet: URL-ul public (HTTPS, prin ingress) servește pagina telefonului. Nu consumă tokenul."""
    t = requests.post(f"{SERVER}/api/sync/mobile/tokens", headers=H(key), json={"entity_type": "loc", "entity_uid": S["uid1"]}, timeout=15).json()
    try:
        r = requests.get(t["url"], timeout=20)
        info = requests.get(t["url"] + "/info", timeout=20)
    except requests.RequestException as exc:
        pytest.skip(f"URL-ul public nu e accesibil din pod: {exc}")
    assert r.status_code == 200 and info.status_code == 200 and t["url"].startswith("https://")
    assert info.json()["entity"]["title"] == f"S6-{RUN}-L1"
    S["public_ok"] = True


# =========================================================================== 12. SUPABASE (simulat) + canar secret + URL public
def _spawn(port: int, extra: dict):
    env = {**os.environ, **_server_env(), **extra}
    p = subprocess.Popen([PY, "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT / "backend", env=env,
                         stdout=open(f"/tmp/s6-server-{port}.log", "w"), stderr=subprocess.STDOUT)
    for _ in range(60):
        try:
            if requests.get(f"http://127.0.0.1:{port}/api/public/status", timeout=2).status_code == 200:
                return p
        except Exception:
            time.sleep(0.5)
    p.kill()
    raise AssertionError("serverul de test nu a pornit")


def test_12_supabase_storage_driver_and_phone_never_gets_the_secret_key(key):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FakeSupabase)
    _FakeSupabase.KEY = CANARY
    _FakeSupabase.store.clear()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    proc = _spawn(8012, {"CIMITIR_STORAGE_DRIVER": "supabase", "SUPABASE_URL": base, "SUPABASE_SECRET_KEY": CANARY, "SUPABASE_BUCKET": "cimitir",
                         "CIMITIR_PUBLIC_URL": "https://cimitir-test.example.org"})
    srv = "http://127.0.0.1:8012"
    try:
        t = requests.post(f"{srv}/api/sync/mobile/tokens", headers=H(key), json={"entity_type": "loc", "entity_uid": S["uid1"]}, timeout=20).json()
        assert t["url"].startswith("https://cimitir-test.example.org/api/m/")                  # URL din CIMITIR_PUBLIC_URL
        seen = [json.dumps(t)]
        page = requests.get(local(t["url"], srv), timeout=20)
        info = requests.get(local(t["url"], srv) + "/info", timeout=20)
        up = upload(t["url"], base=srv, name="supa.pdf")
        seen += [page.text, info.text, up.text, json.dumps(dict(up.headers)), json.dumps(dict(page.headers))]
        assert up.status_code == 200, up.text
        assert all(CANARY not in s and "SUPABASE" not in s.upper() for s in seen)               # telefonul nu primește cheia
        mid = pg("SELECT media_id FROM documents WHERE id=%s", (up.json()["document"]["id"],))[0]
        path = pg("SELECT storage_path FROM media_objects WHERE id=%s", (mid,))[0]
        assert f"cimitir/{path}" in _FakeSupabase.store                                          # octeții au ajuns în Supabase Storage (simulat)
        assert hashlib.sha256(_FakeSupabase.store[f"cimitir/{path}"]).hexdigest() == hashlib.sha256(PDF).hexdigest()
        state = requests.get(f"{srv}/api/sync/media/state", headers=H(key), timeout=15).text
        assert CANARY not in state                                                               # nici API-ul de stare nu o arată
        pg("DELETE FROM media_objects WHERE id=%s", (mid,))
        pg("DELETE FROM documents WHERE media_id=%s", (mid,))
    finally:
        proc.terminate()
        httpd.shutdown()


@pytest.mark.parametrize("url", ["http://192.168.1.5:8000", "https://localhost:8443", "https://10.0.0.7", "http://cimitir.example.org", ""])
def test_13_online_refuses_lan_localhost_or_missing_public_url(key, url):
    proc = _spawn(8013, {"CIMITIR_PUBLIC_URL": url})
    try:
        r = requests.post("http://127.0.0.1:8013/api/sync/mobile/tokens", headers=H(key),
                          json={"entity_type": "loc", "entity_uid": S["uid1"]}, timeout=20)
        # public_base_url din setări (dacă există) poate acoperi lipsa; altfel 503
        assert r.status_code == 503 or (url == "" and r.status_code == 200 and r.json()["url"].startswith("https://")), (url, r.status_code, r.text)
    finally:
        proc.terminate()
        time.sleep(0.5)


def test_15_all_six_entity_types_main_to_phone_to_storage_to_main(key):
    """loc, concesionar, contract, înhumat, plată, aviz: MAIN -> QR -> telefon -> FastAPI -> Storage + PostgreSQL -> MAIN."""
    L = f"S6-{RUN}-L3"
    setup = main_client("C", key, f"""
        conn = db.get_connection()
        conn.execute("INSERT INTO locuri_de_veci (cod_loc, points, sector) VALUES (?, '[[0,0],[1,0],[1,1]]', 'A')", ('{L}',))
        conn.execute("INSERT INTO concesionari (nume, prenume) VALUES ('S6{RUN}', 'Titular')")
        cid = conn.execute("SELECT id FROM concesionari WHERE nume='S6{RUN}'").fetchone()['id']
        lid = conn.execute("SELECT id FROM locuri_de_veci WHERE cod_loc=?", ('{L}',)).fetchone()['id']
        conn.execute("INSERT INTO contracte (numar_contract, concesionar_id, id_loc, cod_loc) VALUES ('S6-{RUN}-C', ?, ?, ?)", (cid, lid, '{L}'))
        ctid = conn.execute("SELECT id FROM contracte WHERE numar_contract='S6-{RUN}-C'").fetchone()['id']
        conn.execute("INSERT INTO inhumati (nume, prenume, id_loc) VALUES ('S6{RUN}', 'Defunct', ?)", (lid,))
        conn.execute("INSERT INTO plati (cod_loc, contract_id, suma, data_plata, tip, chitanta_nr) VALUES (?, ?, 100, '2026-10-01', 'concesiune', 'S6-{RUN}-P')", ('{L}', ctid))
        conn.execute("INSERT INTO amenajari_avize (cod_loc, nr_aviz, data_aviz) VALUES (?, 'S6-{RUN}-A', '2026-10-01')", ('{L}',))
        ids = {{'loc': '{L}', 'concesionar': cid, 'contract': ctid,
                'inhumat': conn.execute("SELECT id FROM inhumati WHERE nume='S6{RUN}'").fetchone()['id'],
                'plata': conn.execute("SELECT id FROM plati WHERE chitanta_nr='S6-{RUN}-P'").fetchone()['id'],
                'aviz': conn.execute("SELECT id FROM amenajari_avize WHERE nr_aviz='S6-{RUN}-A'").fetchone()['id']}}
        conn.close()
        ok, msg = cloud_sync.sync_entities()
        out['sync_ok'], out['sync_msg'] = ok, msg
        out['ids'] = ids
        out['toks'] = {{}}
        for et, i in ids.items():
            t = mobile_upload.request_token(et, i, issued_by='main-six')
            out['toks'][et] = {{'tid': t['tid'], 'url': t['url'], 'uid': t['entity']['uid'], 'type': t['entity']['type']}}
        out['local_uid'] = {{et: mobile_upload.local_uid(et, i) for et, i in ids.items()}}
    """)
    assert setup["sync_ok"], setup["sync_msg"]
    S["six_uids"] = list(setup["local_uid"].values())
    ids, toks = setup["ids"], setup["toks"]
    shas = {}
    for et, tk in toks.items():
        assert tk["type"] == et and tk["uid"] == setup["local_uid"][et]                   # token legat de UID-ul MAIN
        data = PDF + et.encode()
        shas[et] = hashlib.sha256(data).hexdigest()
        r = requests.post(local(tk["url"]) + "/upload", files={"file": (f"{et}.pdf", data, "application/pdf")},
                          data={"doc_type": "alt_document", "description": f"test {et}"}, timeout=60)
        assert r.status_code == 200, (et, r.text)
        d = pg("SELECT entity_type, entity_id, source, uploaded_by, sha256, media_id FROM documents WHERE id=%s", (r.json()["document"]["id"],))
        assert d[0] == et and d[2] == "mobile" and d[3] == "mobile:main-six" and d[4] == shas[et], (et, d)
        m = pg("SELECT entity_uid, storage_path, sha256 FROM media_objects WHERE id=%s", (d[5],))
        assert m[0] == setup["local_uid"][et], (et, m[0], setup["local_uid"][et])          # același UID desktop <-> cloud
        assert m[1].startswith(f"documents/{et}/")
        assert hashlib.sha256(_storage_read(m[1])).hexdigest() == shas[et]                  # octeții sunt în cimitir-private
        a = pg("SELECT actor_type, detail FROM audit_log WHERE action='attachment.add' AND target=%s ORDER BY id DESC LIMIT 1", (f"{et}:{d[1]}",))
        assert a[0] == "mobile" and json.loads(a[1])["source"] == "mobile"
    seen = main_client("C", key, f"""
        ids = {json.dumps(ids)}
        toks = {json.dumps({k: v['tid'] for k, v in toks.items()})}
        out['docs'] = {{}}
        for et, i in ids.items():
            st, msg = mobile_upload.fetch_uploaded(toks[et])
            docs = db.list_documents(et, str(i))
            out['docs'][et] = {{'state': st['state'], 'n': len(docs), 'sha': sha(db.get_document_path(docs[0]['id'])) if docs else ''}}
        cloud_sync.sync_entities(); cloud_sync.sync_media()
        out['after'] = {{et: len(db.list_documents(et, str(i))) for et, i in ids.items()}}
    """)
    for et in toks:
        assert seen["docs"][et] == {"state": "used", "n": 1, "sha": shas[et]}, (et, seen["docs"][et])      # MAIN vede documentul
        assert seen["after"][et] == 1                                                                         # nicio a doua copie
        assert pg("SELECT count(*) FROM media_objects WHERE entity_uid=%s", (setup["local_uid"][et],))[0] == 1


def test_14_brute_force_on_phone_endpoint_is_rate_limited(key):
    codes = [requests.get(f"{SERVER}/api/m/cimitir.{'B' * 43}/info", timeout=15).status_code for _ in range(8)]
    assert codes[0] == 404 and 429 in codes
