"""Etapa 7 — documente online. Două părți:

1. Drivere de storage (fără server): local, S3 (moto = S3 simulat), Supabase (server HTTP simulat).
2. End-to-end: 2 clienți MAIN (SQLite, procese separate) <-> FastAPI real <-> PostgreSQL + storage-ul serverului (driver local în pod).

Rulare:  cd backend && python -m pytest tests/test_etapa7_media.py -q -n 0
Partea 2 se sare dacă serverul nu rulează pe PostgreSQL. Datele de test au prefix „E7-" și sunt șterse la final.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import textwrap
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))
import cimitir_storage as st  # noqa: E402

SERVER = os.environ.get("ETAPA6_SERVER_URL", "http://127.0.0.1:8001").rstrip("/")
PY = sys.executable
RUN = uuid.uuid4().hex[:6]


# =============================================================================== 1. DRIVERE
def _roundtrip(storage: st.Storage, tmp_path):
    src = tmp_path / "in.pdf"
    payload = os.urandom(3 * 1024 * 1024 + 17)
    src.write_bytes(payload)
    key = st.build_key("documents", "inhumat", "certificat_deces", "uid-1", "mid12345", "Certificat deces (1).pdf")
    assert key == "documents/inhumat/certificat_deces/uid-1/mid12345/Certificat_deces_1_.pdf"
    assert not storage.exists(key)
    storage.put_file(key, str(src), "application/pdf")
    assert storage.exists(key)
    assert storage.get_bytes(key) == payload
    dest = tmp_path / "cache" / "out.pdf"
    storage.download_to(key, str(dest))
    assert dest.read_bytes() == payload and not list((tmp_path / "cache").glob("*.part"))
    with pytest.raises(st.NotFound):
        storage.get_bytes(key + "-lipsa")
    storage.delete(key)
    assert not storage.exists(key)
    storage.delete(key)                         # a doua ștergere nu aruncă
    assert st.parse_key(key) == {"table": "documents", "entity_type": "inhumat", "doc_type": "certificat_deces"}
    with pytest.raises(st.StorageError):
        storage.exists("../evadare")


def test_local_driver(tmp_path):
    _roundtrip(st.LocalStorage(str(tmp_path / "bucket")), tmp_path)


def test_s3_driver_against_simulated_s3(tmp_path):
    from moto import mock_aws
    import boto3
    with mock_aws():
        boto3.client("s3", region_name="eu-central-1").create_bucket(
            Bucket="cimitir-media", CreateBucketConfiguration={"LocationConstraint": "eu-central-1"})
        storage = st.S3Storage("cimitir-media", "eu-central-1", "test", "test", prefix="prod")
        _roundtrip(storage, tmp_path)
        # prefixul se aplică la cheie
        src = tmp_path / "p.jpg"
        src.write_bytes(b"jpg")
        storage.put_file("photos/loc/u/m/p.jpg", str(src), "image/jpeg")
        listed = boto3.client("s3", region_name="eu-central-1").list_objects_v2(Bucket="cimitir-media")["Contents"]
        assert [o["Key"] for o in listed] == ["prod/photos/loc/u/m/p.jpg"]


class _FakeSupabase(BaseHTTPRequestHandler):
    store: dict = {}
    KEY = "service-role-test"

    def log_message(self, *a):
        pass

    def _auth(self):
        if self.headers.get("Authorization") != f"Bearer {self.KEY}":
            self.send_response(401); self.end_headers(); return False
        return True

    def _path(self):
        parts = self.path.split("/")      # /storage/v1/object/[authenticated/]bucket/key...
        i = 4 + (1 if parts[4] == "authenticated" else 0)
        return parts[i] + "/" + "/".join(parts[i + 1:])

    def do_POST(self):
        if not self._auth(): return
        data = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.store[self._path()] = data
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"Key":"ok"}')

    def do_GET(self):
        if not self._auth(): return
        data = self.store.get(self._path())
        if data is None:
            self.send_response(400); self.end_headers(); self.wfile.write(b'{"error":"not_found"}'); return
        self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_HEAD(self):
        if not self._auth(): return
        self.send_response(200 if self._path() in self.store else 400); self.end_headers()

    def do_DELETE(self):
        if not self._auth(): return
        self.store.pop(self._path(), None)
        self.send_response(200); self.end_headers(); self.wfile.write(b"{}")


def test_supabase_driver_against_simulated_supabase(tmp_path):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FakeSupabase)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}"
        _roundtrip(st.SupabaseStorage(url, _FakeSupabase.KEY, "cimitir"), tmp_path)
        with pytest.raises(st.StorageError):          # cheie greșită => eroare clară, nu date
            st.SupabaseStorage(url, "gresita", "cimitir").exists("x/y")
    finally:
        httpd.shutdown()


def test_supabase_driver_against_real_test_project_bucket(tmp_path):
    """Storage REAL, doar proiectul Supabase TEST și bucket-ul separat `cimitir-private-test` (nu există ocolire către producție)."""
    url, key, bucket = (os.environ.get(k, "").strip() for k in ("SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_BUCKET"))
    ref = os.environ.get("CIMITIR_TEST_PROJECT_REF", "").strip()
    if not (os.environ.get("CIMITIR_LIVE_TESTS") == "1" and ref and url and key and bucket):
        pytest.skip("Storage TEST nu este configurat (CIMITIR_LIVE_TESTS, CIMITIR_TEST_PROJECT_REF, SUPABASE_*)")
    import cimitir_test_guard as guard
    assert not guard.has_protected_ref(url + key + ref), "Storage de producție interzis în teste"
    assert url == f"https://{ref}.supabase.co" and bucket == "cimitir-private-test"
    _roundtrip(st.SupabaseStorage(url, key, bucket, prefix=f"pytest-{uuid.uuid4().hex[:8]}"), tmp_path)


def test_factory_reads_env_and_never_needs_keys_for_local(monkeypatch, tmp_path):
    monkeypatch.setenv("CIMITIR_STORAGE_DRIVER", "local")
    monkeypatch.setenv("CIMITIR_STORAGE_LOCAL_DIR", str(tmp_path / "b"))
    st.reset_storage()
    assert st.get_storage().describe()["driver"] == "local"
    monkeypatch.setenv("CIMITIR_STORAGE_DRIVER", "supabase")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    with pytest.raises(st.StorageError):
        st.get_storage()
    monkeypatch.setenv("CIMITIR_STORAGE_DRIVER", "ceva")
    with pytest.raises(st.StorageError):
        st.get_storage()
    st.reset_storage()


# =============================================================================== 2. END-TO-END
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


def _server_ok() -> bool:
    try:
        r = requests.get(f"{SERVER}/api/public/status", timeout=5).json()
        return r.get("database", {}).get("engine") == "postgres"
    except Exception:
        return False


e2e = pytest.mark.skipif(not _server_ok(), reason="serverul PostgreSQL nu rulează")


@pytest.fixture(scope="module")
def key():
    out = subprocess.run([PY, str(ROOT / "scripts" / "issue_sync_key.py"), "--source", "cimitir"], cwd=ROOT / "backend",
                         capture_output=True, text=True, timeout=60, env={**os.environ, **_server_env()})
    line = [l for l in out.stdout.splitlines() if l.startswith("cimitir.")]
    assert line, out.stdout + out.stderr
    return line[-1].strip()


def client(name: str, key: str, code: str, url: str = SERVER) -> dict:
    data_dir = f"/tmp/e7-{RUN}-{name}"
    script = textwrap.dedent(f"""
        import json, os, sys, hashlib
        sys.path.insert(0, {str(ROOT / 'desktop')!r})
        import database as db, cloud_sync, entity_sync, media_sync
        db.init_db()
        cloud_sync.save_config({url!r}, {key!r}, False, False)
        out = {{}}
        def sha(p): return hashlib.sha256(open(p, 'rb').read()).hexdigest()
    """) + textwrap.dedent(code) + "\nprint('RESULT=' + json.dumps(out, default=str))\n"
    env = {**os.environ, "CIMITIR_SECRET_STORE": "test-encrypted", "CIMITIR_ALLOW_TEST_SECRETSTORE": "1", "CIMITIR_TEST_SECRET_KEY": "rAR-xhkir67T-0hgHU_d6gxHdQt7zqO9zPP-nl-r7yw=", "CIMITIR_DATA_DIR": data_dir, "CIMITIR_MODE": "local", "CIMITIR_DB_ENGINE": "sqlite",
           "PYTHONPATH": str(ROOT / "shared")}
    env.pop("DATABASE_URL", None)
    p = subprocess.run([PY, "-c", script], capture_output=True, text=True, env=env, timeout=240, cwd=ROOT / "desktop")
    assert p.returncode == 0, p.stdout[-1500:] + p.stderr[-2500:]
    return json.loads([l for l in p.stdout.splitlines() if l.startswith("RESULT=")][-1][len("RESULT="):])


def pg_one(sql, params=()):
    with _pg() as conn:
        return conn.execute(sql, params).fetchone()


H = lambda k, c="e7": {"X-Sync-Key": k, "X-Sync-Client": c}      # noqa: E731
BIG = Path(f"/tmp/e7-{RUN}-big.pdf")
SMALLF = Path(f"/tmp/e7-{RUN}-small.pdf")
NUME = f"E7-{RUN}-Ionescu"
state: dict = {}


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield
    if not _server_ok():
        return
    with _pg() as conn:
        for (path,) in conn.execute("SELECT storage_path FROM media_objects WHERE filename LIKE %s", (f"e7-{RUN}%",)).fetchall():
            f = Path(_loc()) / path
            f.unlink(missing_ok=True)
        conn.execute("DELETE FROM media_objects WHERE filename LIKE %s", (f"e7-{RUN}%",))
        conn.execute("DELETE FROM inhumati WHERE nume LIKE %s", (f"E7-{RUN}%",))
        conn.execute("DELETE FROM locuri_de_veci WHERE cod_loc LIKE %s", (f"E7-{RUN}%",))


def _loc() -> str:
    k = state["key"]
    return requests.get(f"{SERVER}/api/sync/media/state", headers=H(k), timeout=10).json()["storage"]["location"]


@e2e
def test_01_ping_advertises_media_and_state(key):
    state["key"] = key
    m = requests.get(f"{SERVER}/api/sync/ping", headers=H(key), timeout=10).json()["media"]
    assert m["driver"] == "local" and m["ok"] is True and m["chunk_bytes"] == 8 * 1024 * 1024
    s = requests.get(f"{SERVER}/api/sync/media/state", headers=H(key), timeout=10).json()
    assert s["storage"]["driver"] == "local" and "secret" not in json.dumps(s).lower()


@e2e
def test_02_new_documents_upload_in_chunks_metadata_only_in_postgres(key):
    SMALLF.write_bytes(os.urandom(300 * 1024))
    BIG.write_bytes(os.urandom(11 * 1024 * 1024 + 5))          # 12 bucăți; peste pragul de prefetch (10 MiB)
    state["small_sha"], state["big_sha"] = hashlib.sha256(SMALLF.read_bytes()).hexdigest(), hashlib.sha256(BIG.read_bytes()).hexdigest()
    res = client("A", key, f"""
        shutil = __import__('shutil')
        os.makedirs('/tmp/e7-{RUN}-s', exist_ok=True)
        shutil.copy({str(SMALLF)!r}, '/tmp/e7-{RUN}-s/e7-{RUN}-certificat.pdf')
        shutil.copy({str(BIG)!r}, '/tmp/e7-{RUN}-s/e7-{RUN}-mare.pdf')
        conn = db.get_connection()
        conn.execute("INSERT INTO locuri_de_veci (cod_loc, points, sector) VALUES ('E7-{RUN}-L1', '[[0,0],[1,0],[1,1]]', 'A')")
        conn.execute("INSERT INTO inhumati (nume, prenume, id_loc) SELECT ?, 'Ion', id FROM locuri_de_veci WHERE cod_loc='E7-{RUN}-L1'", ({NUME!r},))
        iid = conn.execute("SELECT id FROM inhumati WHERE nume=?", ({NUME!r},)).fetchone()['id']
        conn.close()
        d1 = db.add_document('inhumat', iid, 'certificat_deces', '/tmp/e7-{RUN}-s/e7-{RUN}-certificat.pdf', caption='cert')
        d2 = db.add_document('inhumat', iid, 'alte', '/tmp/e7-{RUN}-s/e7-{RUN}-mare.pdf', caption='mare')
        out['local1'] = db.get_document_path(d1); out['local2'] = db.get_document_path(d2)
        out['queued'] = cloud_sync.pending_count()
        rep = cloud_sync.sync_all()
        out['ok'] = rep['ok']; out['lines'] = rep['lines']; out['pending'] = cloud_sync.pending_count()
        out['originals_exist'] = [os.path.exists(out['local1']), os.path.exists(out['local2'])]
        conn = db.get_connection()
        out['mids'] = [r['media_id'] for r in conn.execute("SELECT media_id FROM documents ORDER BY id")]
        conn.close()
    """)
    assert res["queued"] >= 2 and res["pending"] == 0 and res["originals_exist"] == [True, True], res
    mid_small, mid_big = res["mids"]
    state.update(mid_small=mid_small, mid_big=mid_big)
    iuid = pg_one("SELECT uid FROM inhumati WHERE nume=%s", (NUME,))[0]
    row = pg_one("SELECT id, entity_uid, filename, storage_path, mime, size, sha256, uploaded_by, uploaded_at FROM media_objects WHERE id=%s", (mid_big,))
    assert row[1] == iuid and row[2] == f"e7-{RUN}-mare.pdf" and row[4] == "application/pdf"
    assert row[5] == BIG.stat().st_size and row[6] == state["big_sha"] and row[7] and row[8]
    assert row[3] == f"documents/inhumat/alte/{iuid}/{mid_big}/e7-{RUN}-mare.pdf"
    stored = Path(_loc()) / row[3]
    assert stored.is_file() and hashlib.sha256(stored.read_bytes()).hexdigest() == state["big_sha"]
    # PostgreSQL: niciun octet de fișier — coloanele sunt exact cele cerute (+ change_seq de sincronizare), fără tip bytea
    cols = [c[0] for c in _pg().execute("SELECT column_name FROM information_schema.columns WHERE table_name='media_objects' AND table_schema='public' ORDER BY ordinal_position").fetchall()]
    assert cols == ["id", "entity_uid", "filename", "storage_path", "mime", "size", "sha256", "uploaded_by", "uploaded_at", "change_seq",
                    "organization_uid", "cemetery_uid"]   # + tenantul (migrările 009/011)
    assert _pg().execute("SELECT count(*) FROM information_schema.columns WHERE data_type='bytea' AND table_schema='public'").fetchone()[0] == 0
    assert pg_one("SELECT pg_column_size(m.*) FROM media_objects m WHERE id=%s", (mid_big,))[0] < 1000


@e2e
def test_03_other_computer_gets_metadata_then_files_from_storage_with_cache(key):
    res = client("B", key, f"""
        rep = cloud_sync.sync_all()
        conn = db.get_connection()
        iid = conn.execute("SELECT id FROM inhumati WHERE nume=?", ({NUME!r},)).fetchone()['id']
        conn.close()
        cache_big = media_sync.cache_path({state['mid_big']!r}, 'e7-{RUN}-mare.pdf')
        cache_small = media_sync.cache_path({state['mid_small']!r}, 'e7-{RUN}-certificat.pdf')
        out['media_line'] = [l for l in rep['lines'] if l.startswith('Documente')]; out['pending'] = cloud_sync.pending_count()
        out['small_prefetched'] = os.path.exists(cache_small)
        out['big_before'] = os.path.exists(cache_big)
        docs = db.list_documents('inhumat', iid)                   # citire: aduce din storage dacă lipsește local
        out['types'] = sorted(d['doc_type'] for d in docs)
        by = {{d['media_id']: d for d in docs}}
        out['big_after'] = os.path.exists(by[{state['mid_big']!r}]['file_path']); out['big_sha'] = sha(by[{state['mid_big']!r}]['file_path'])
        out['small_sha'] = sha(by[{state['mid_small']!r}]['file_path'])
        out['queued_back'] = len([o for o in cloud_sync.pending_operations() if o['kind'] == 'media'])   # ce vine de la server nu se retrimite
        # offline: fișierul e în cache => se deschide și fără server
        cloud_sync.save_config('http://127.0.0.1:9', {key!r}, False, False)
        out['offline_path_ok'] = os.path.exists(db.get_document_path(by[{state['mid_big']!r}]['id']))
    """)
    assert res["media_line"] and "2 noi" in res["media_line"][0] and res["pending"] == 0 and res["queued_back"] == 0, res
    assert res["small_prefetched"] is True and res["big_before"] is False and res["big_after"] is True
    assert res["types"] == ["alte", "certificat_deces"]
    assert res["big_sha"] == state["big_sha"] and res["small_sha"] == state["small_sha"] and res["offline_path_ok"]


@e2e
def test_04_offline_photo_is_saved_locally_queued_and_uploaded_on_reconnect(key):
    pic = f"/tmp/e7-{RUN}-poza.jpg"
    Path(pic).write_bytes(b"\xff\xd8\xff" + os.urandom(50_000))
    off = client("A", key, f"""
        shutil = __import__('shutil'); os.makedirs('/tmp/e7-{RUN}-s', exist_ok=True)
        shutil.copy({pic!r}, '/tmp/e7-{RUN}-s/e7-{RUN}-poza.jpg')
        pid = db.save_photo('E7-{RUN}-L1', '/tmp/e7-{RUN}-s/e7-{RUN}-poza.jpg', {{'caption': 'poza'}})
        cloud_sync.save_config('http://127.0.0.1:9', {key!r}, False, False)
        rep = cloud_sync.sync_all()
        out['ok'] = rep['ok']; out['queue'] = len([o for o in cloud_sync.pending_operations() if o['kind'] == 'media'])
        out['local'] = db.list_photos_for_loc('E7-{RUN}-L1')[0]['full_path']; out['exists'] = os.path.exists(out['local'])
    """, url="http://127.0.0.1:9")
    assert off["ok"] is False and off["queue"] == 1 and off["exists"] is True, off
    assert pg_one("SELECT count(*) FROM media_objects WHERE filename=%s", (f"e7-{RUN}-poza.jpg",))[0] == 0
    back = client("A", key, f"""
        rep = cloud_sync.sync_all()
        out['ok'] = rep['ok']; out['queue'] = len([o for o in cloud_sync.pending_operations() if o['kind'] == 'media'])
        out['exists'] = os.path.exists(db.list_photos_for_loc('E7-{RUN}-L1')[0]['full_path'])
    """)
    assert back["queue"] == 0 and back["exists"] is True, back          # încărcat, iar originalul local rămâne
    row = pg_one("SELECT storage_path, sha256, size FROM media_objects WHERE filename=%s", (f"e7-{RUN}-poza.jpg",))
    luid = pg_one("SELECT uid FROM locuri_de_veci WHERE cod_loc=%s", (f"E7-{RUN}-L1",))[0]
    assert row[0].startswith(f"photos/loc/{luid}/") and row[1] == hashlib.sha256(Path(pic).read_bytes()).hexdigest()


@e2e
def test_05_server_verifies_sha256_and_is_idempotent(key):
    mid = uuid.uuid4().hex
    data = os.urandom(5000)
    put = lambda i, b: requests.put(f"{SERVER}/api/sync/media/{mid}/chunks/{i}", data=b, headers=H(key), timeout=15)
    assert put(0, data[:3000]).status_code == 200 and put(1, data[3000:]).status_code == 200
    meta = {"entity_uid": "uid-x", "filename": f"e7-{RUN}-v.bin", "size": 5000, "chunks": 2, "table": "documents",
            "entity_type": "loc", "doc_type": "alte"}
    bad = requests.post(f"{SERVER}/api/sync/media/{mid}/complete", json={**meta, "sha256": "0" * 64}, headers=H(key), timeout=15)
    assert bad.status_code == 422 and bad.json()["detail"]["error"] == "checksum_mismatch"
    assert pg_one("SELECT count(*) FROM media_objects WHERE id=%s", (mid,))[0] == 0              # nimic scris
    assert put(0, data[:3000]).status_code == 200 and put(1, data[3000:]).status_code == 200
    good = {**meta, "sha256": hashlib.sha256(data).hexdigest()}
    r1 = requests.post(f"{SERVER}/api/sync/media/{mid}/complete", json=good, headers=H(key), timeout=15).json()
    r2 = requests.post(f"{SERVER}/api/sync/media/{mid}/complete", json=good, headers=H(key), timeout=15).json()
    assert r1["duplicate"] is False and r2["duplicate"] is True and r2["object"]["id"] == mid
    other = requests.post(f"{SERVER}/api/sync/media/{mid}/complete", json={**good, "sha256": "1" * 64}, headers=H(key), timeout=15)
    assert other.status_code == 409
    miss = requests.post(f"{SERVER}/api/sync/media/{uuid.uuid4().hex}/complete", json={**good, "chunks": 3}, headers=H(key), timeout=15)
    assert miss.status_code == 409 and miss.json()["detail"]["error"] == "chunks_missing"
    dl = requests.get(f"{SERVER}/api/sync/media/{mid}", headers=H(key), timeout=15)
    assert dl.status_code == 200 and dl.content == data and dl.headers["X-Sha256"] == hashlib.sha256(data).hexdigest()
    assert requests.get(f"{SERVER}/api/sync/media/{mid}").status_code in (401, 403)                 # cheie obligatorie
    assert requests.put(f"{SERVER}/api/sync/media/../chunks/0", data=b"x", headers=H(key)).status_code in (404, 422)
    assert requests.delete(f"{SERVER}/api/sync/media/{mid}", headers=H(key), timeout=15).json()["deleted"] is True
    assert requests.get(f"{SERVER}/api/sync/media/{mid}", headers=H(key), timeout=15).status_code == 404


@e2e
def test_06_delete_propagates_to_storage_and_other_computer(key):
    client("A", key, f"""
        conn = db.get_connection()
        did = conn.execute("SELECT id FROM documents WHERE media_id=?", ({state['mid_small']!r},)).fetchone()['id']
        conn.close()
        out['deleted'] = db.delete_document(did)
        out['ok'] = cloud_sync.sync_all()['ok']
    """)
    assert pg_one("SELECT count(*) FROM media_objects WHERE id=%s", (state["mid_small"],))[0] == 0
    assert not any(Path(_loc()).rglob(f"*{state['mid_small']}*"))
    res = client("B", key, f"""
        cloud_sync.sync_all()
        conn = db.get_connection()
        out['left'] = [r['media_id'] for r in conn.execute("SELECT media_id FROM documents")]
        conn.close()
    """)
    assert state["mid_small"] not in res["left"] and state["mid_big"] in res["left"]


@e2e
def test_07_server_side_documents_go_to_storage_and_come_back_from_it():
    """Fișiere încărcate pe server (portal/admin): octeții în storage, metadate în PostgreSQL; după pierderea copiei locale
    se readuc din storage (cu verificare sha256). Fișierul local nu se șterge după încărcare."""
    env = {**os.environ, **_server_env(), "PYTHONPATH": ":".join(str(ROOT / p) for p in ("backend", "backend/cimitir", "shared")),
           "CIMITIR_DATA_DIR": str(ROOT / "shared_data")}
    code = textwrap.dedent(f"""
        import json, os, hashlib
        import store as cim
        cim.ensure_initialized(cim.get_source('cimitir'))        # procesul nu trece prin startup-ul serverului: rezolvă tenantul activ
        src = '/tmp/e7-{RUN}-server.pdf'; open(src, 'wb').write(os.urandom(70000))
        want = hashlib.sha256(open(src, 'rb').read()).hexdigest()
        did = cim.add_document('loc', 'E7-{RUN}-L1', 'poza_loc', src, caption='server')
        d = cim.list_documents('loc', 'E7-{RUN}-L1', 'poza_loc')[0]
        out = {{'media_id': d['media_id'], 'local_exists': os.path.exists(d['file_path']), 'want': want}}
        os.remove(d['file_path'])                                  # pierdem copia locală a serverului
        back = cim.get_document_path(did)
        out['restored'] = os.path.exists(back) and hashlib.sha256(open(back, 'rb').read()).hexdigest() == want
        out['restored_in_cache'] = 'media_cache' in back
        cim.delete_document(did)
        print('RESULT=' + json.dumps(out))
    """)
    p = subprocess.run([PY, "-c", code], capture_output=True, text=True, env=env, cwd=ROOT / "backend", timeout=60)
    assert p.returncode == 0, p.stderr[-2000:]
    res = json.loads([l for l in p.stdout.splitlines() if l.startswith("RESULT=")][-1][7:])
    assert res["local_exists"] and res["restored"] and res["restored_in_cache"], res
    assert pg_one("SELECT count(*) FROM media_objects WHERE id=%s", (res["media_id"],))[0] == 0   # ștergerea documentului a șters și metadatele


@e2e
def test_08_whole_api_with_s3_driver_against_simulated_s3(key):
    """Aceeași API (bucăți, sha256, descărcare, ștergere) cu driverul S3 activ, într-un proces separat (FastAPI TestClient + moto)."""
    env = {**os.environ, **_server_env(), "PYTHONPATH": ":".join(str(ROOT / p) for p in ("backend", "backend/cimitir", "shared")),
           "CIMITIR_DATA_DIR": f"/tmp/e7-{RUN}-s3srv", "CIMITIR_STORAGE_DRIVER": "s3", "S3_BUCKET": "cimitir-media",
           "S3_REGION": "eu-central-1", "S3_ACCESS_KEY_ID": "test", "S3_SECRET_ACCESS_KEY": "test", "S3_PREFIX": "prod",
           "AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test"}
    code = textwrap.dedent(f"""
        import hashlib, json, os, boto3
        from moto import mock_aws
        with mock_aws():
            boto3.client('s3', region_name='eu-central-1').create_bucket(Bucket='cimitir-media',
                CreateBucketConfiguration={{'LocationConstraint': 'eu-central-1'}})
            from fastapi.testclient import TestClient
            import server
            c = TestClient(server.app)
            h = {{'X-Sync-Key': {key!r}, 'X-Sync-Client': 'e7-s3'}}
            mid = 's3' + os.urandom(8).hex(); data = os.urandom(70000); sha = hashlib.sha256(data).hexdigest()
            out = {{'ping': c.get('/api/sync/ping', headers=h).json()['media']}}
            assert c.put(f'/api/sync/media/{{mid}}/chunks/0', content=data[:40000], headers=h).status_code == 200
            assert c.put(f'/api/sync/media/{{mid}}/chunks/1', content=data[40000:], headers=h).status_code == 200
            r = c.post(f'/api/sync/media/{{mid}}/complete', headers=h, json={{'entity_uid': 'u-s3', 'filename': 'e7-{RUN}-s3.pdf',
                'sha256': sha, 'size': len(data), 'chunks': 2, 'table': 'documents', 'entity_type': 'loc', 'doc_type': 'alte'}})
            out['complete'] = r.status_code
            keys = [o['Key'] for o in boto3.client('s3', region_name='eu-central-1').list_objects_v2(Bucket='cimitir-media')['Contents']]
            out['keys'] = keys
            d = c.get(f'/api/sync/media/{{mid}}', headers=h)
            out['download_ok'] = d.status_code == 200 and d.content == data
            out['deleted'] = c.delete(f'/api/sync/media/{{mid}}', headers=h).json()['deleted']
            out['left'] = boto3.client('s3', region_name='eu-central-1').list_objects_v2(Bucket='cimitir-media').get('KeyCount')
        print('RESULT=' + json.dumps(out))
    """)
    p = subprocess.run([PY, "-c", code], capture_output=True, text=True, env=env, cwd=ROOT / "backend", timeout=120)
    assert p.returncode == 0, p.stderr[-2500:]
    res = json.loads([l for l in p.stdout.splitlines() if l.startswith("RESULT=")][-1][7:])
    assert res["ping"]["driver"] == "s3" and res["complete"] == 200 and res["download_ok"] and res["deleted"] and res["left"] == 0, res
    assert len(res["keys"]) == 1 and res["keys"][0].startswith("prod/documents/loc/alte/u-s3/s3") and res["keys"][0].endswith("/e7-%s-s3.pdf" % RUN)
