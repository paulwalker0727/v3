"""Etapa 6 — test end-to-end al sincronizării pe entități: 2 clienți MAIN (SQLite, procese separate) <-> FastAPI real
(uvicorn pe SERVER_URL) <-> PostgreSQL.

Rulare (serverul trebuie să ruleze, cu CIMITIR_DB_ENGINE=postgres):
    cd backend && python -m pytest tests/test_etapa6_entity_sync.py -q -n 0
Variabile: ETAPA6_SERVER_URL (implicit http://127.0.0.1:8001). Se sare peste teste dacă serverul nu e pe PostgreSQL.
Datele de test au prefix „E6-" și sunt șterse la final.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
SERVER = os.environ.get("ETAPA6_SERVER_URL", "http://127.0.0.1:8001").rstrip("/")
PY = sys.executable
RUN = uuid.uuid4().hex[:6]


def _server_env() -> dict:
    """Variabilele serverului reale (backend/.env), nu cele forțate pe SQLite de conftest.py."""
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


pytestmark = pytest.mark.skipif(not _server_ok(), reason="serverul PostgreSQL nu rulează")


@pytest.fixture(scope="module")
def key():
    out = subprocess.run([PY, str(ROOT / "scripts" / "issue_sync_key.py"), "--source", "cimitir"], cwd=ROOT / "backend",
                         capture_output=True, text=True, timeout=60, env={**os.environ, **_server_env()})
    line = [l for l in out.stdout.splitlines() if l.startswith("cimitir.")]
    assert line, out.stdout + out.stderr
    return line[-1].strip()


def client(name: str, key: str, code: str, url: str = SERVER) -> dict:
    """Rulează `code` într-un proces MAIN izolat (propriul folder de date SQLite). Ultima linie JSON = rezultatul."""
    data_dir = f"/tmp/e6-{RUN}-{name}"
    script = textwrap.dedent(f"""
        import json, sys
        sys.path.insert(0, {str(ROOT / 'desktop')!r})
        import database as db, cloud_sync, entity_sync
        db.init_db()
        cloud_sync.save_config({url!r}, {key!r}, False, False)
        out = {{}}
    """) + textwrap.dedent(code) + "\nprint('RESULT=' + json.dumps(out, default=str))\n"
    env = {**os.environ, "CIMITIR_SECRET_STORE": "test-encrypted", "CIMITIR_ALLOW_TEST_SECRETSTORE": "1", "CIMITIR_TEST_SECRET_KEY": "rAR-xhkir67T-0hgHU_d6gxHdQt7zqO9zPP-nl-r7yw=", "CIMITIR_DATA_DIR": data_dir, "CIMITIR_MODE": "local", "CIMITIR_DB_ENGINE": "sqlite",
           "PYTHONPATH": str(ROOT / "shared")}
    env.pop("DATABASE_URL", None)
    p = subprocess.run([PY, "-c", script], capture_output=True, text=True, env=env, timeout=180, cwd=ROOT / "desktop")
    assert p.returncode == 0, p.stdout[-1500:] + p.stderr[-2500:]
    line = [l for l in p.stdout.splitlines() if l.startswith("RESULT=")][-1]
    return json.loads(line[len("RESULT="):])


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield
    with _pg() as conn:
        conn.execute("DELETE FROM plati WHERE observatii LIKE %s", (f"E6-{RUN}%",))
        conn.execute("DELETE FROM inhumati WHERE nume LIKE %s", (f"E6-{RUN}%",))
        conn.execute("DELETE FROM contracte WHERE numar_contract LIKE %s", (f"E6-{RUN}%",))
        conn.execute("DELETE FROM concesionari WHERE nume LIKE %s", (f"E6-{RUN}%",))
        conn.execute("DELETE FROM locuri_de_veci WHERE cod_loc LIKE %s", (f"E6-{RUN}%",))
        conn.execute("DELETE FROM sync_conflicts WHERE entity IN ('concesionari','contracte','locuri_de_veci')")


def pg_one(sql, params=()):
    with _pg() as conn:
        return conn.execute(sql, params).fetchone()


L1, L2 = f"E6-{RUN}-L1", f"E6-{RUN}-L2"
NUME = f"E6-{RUN}-Popescu"


def test_01_ping_advertises_entity_sync(key):
    r = requests.get(f"{SERVER}/api/sync/ping", headers={"X-Sync-Key": key}, timeout=10).json()
    assert r["entity_sync"] is True


def test_02_client_a_pushes_local_data_to_postgres(key):
    res = client("A", key, f"""
        db.set_setting('cloud_sync_auto', '0')
        entity_sync.ensure_tracking(force=True)
        conn = db.get_connection()
        conn.execute("INSERT INTO locuri_de_veci (cod_loc, points, sector, parcela) VALUES (?, '[[0,0],[1,0],[1,1]]', 'A', '1')", ({L1!r},))
        conn.execute("INSERT INTO locuri_de_veci (cod_loc, points, sector, parcela) VALUES (?, '[[5,5],[6,5],[6,6]]', 'A', '1')", ({L2!r},))
        loc = conn.execute("SELECT id FROM locuri_de_veci WHERE cod_loc=?", ({L1!r},)).fetchone()['id']
        conn.execute("INSERT INTO concesionari (nume, prenume, cnp) VALUES (?, 'Ion', '1800101123456')", ({NUME!r},))
        cid = conn.execute("SELECT id FROM concesionari WHERE nume=?", ({NUME!r},)).fetchone()['id']
        conn.execute("INSERT INTO contracte (numar_contract, concesionar_id, id_loc, cod_loc) VALUES (?, ?, ?, ?)", ('E6-{RUN}-C1', cid, loc, {L1!r}))
        ctr = conn.execute("SELECT id FROM contracte WHERE numar_contract='E6-{RUN}-C1'").fetchone()['id']
        conn.execute("INSERT INTO inhumati (nume, prenume, id_loc, contract_id) VALUES ('E6-{RUN}-Defunct', 'Maria', ?, ?)", (loc, ctr))
        conn.execute("INSERT INTO plati (cod_loc, contract_id, suma, observatii) VALUES (?, ?, 150.5, 'E6-{RUN}-plata')", ({L1!r}, ctr))
        conn.close()
        out['pending_before'] = cloud_sync.pending_count()
        rep = cloud_sync.sync_all()
        out['ok'] = rep['ok']; out['lines'] = rep['lines']; out['pending_after'] = cloud_sync.pending_count()
    """)
    assert res["pending_before"] >= 6, res           # captat de triggere, fără internet implicat
    assert res["pending_after"] == 0, res
    row = pg_one("SELECT c.nume, l.cod_loc, c.version FROM contracte k JOIN concesionari c ON c.id=k.concesionar_id "
                 "JOIN locuri_de_veci l ON l.id=k.id_loc WHERE k.numar_contract=%s", (f"E6-{RUN}-C1",))
    assert row and row[0] == NUME and row[1] == L1 and row[2] == 1
    assert pg_one("SELECT COUNT(*) FROM inhumati i JOIN contracte k ON k.id=i.contract_id WHERE i.nume=%s",
                  (f"E6-{RUN}-Defunct",))[0] == 1
    assert pg_one("SELECT suma FROM plati WHERE observatii=%s", (f"E6-{RUN}-plata",))[0] == 150.5


def test_03_client_b_downloads_everything_into_its_cache(key):
    res = client("B", key, f"""
        rep = cloud_sync.sync_all()
        conn = db.get_connection()
        out['ok'] = rep['ok']
        out['locs'] = [r['cod_loc'] for r in conn.execute("SELECT cod_loc FROM locuri_de_veci WHERE cod_loc LIKE 'E6-{RUN}%'")]
        r = conn.execute("SELECT c.nume, l.cod_loc, k.numar_contract, c.version FROM contracte k JOIN concesionari c ON c.id=k.concesionar_id "
                         "JOIN locuri_de_veci l ON l.id=k.id_loc WHERE k.numar_contract='E6-{RUN}-C1'").fetchone()
        out['join'] = dict(r) if r else None
        out['inh'] = conn.execute("SELECT COUNT(*) AS n FROM inhumati i JOIN contracte k ON k.id=i.contract_id WHERE i.nume='E6-{RUN}-Defunct'").fetchone()['n']
        out['plata'] = conn.execute("SELECT suma FROM plati WHERE observatii='E6-{RUN}-plata'").fetchone()['suma']
        out['pending'] = cloud_sync.pending_count()   # ce vine de la server NU intră în outbox
        conn.close()
    """)
    assert sorted(res["locs"]) == sorted([L1, L2]), res
    assert res["join"]["nume"] == NUME and res["join"]["cod_loc"] == L1 and res["join"]["version"] == 1
    assert res["inh"] == 1 and res["plata"] == 150.5 and res["pending"] == 0, res


def test_04_offline_edits_queue_and_flush_when_back_online(key):
    off = client("B", key, f"""
        conn = db.get_connection()
        conn.execute("UPDATE concesionari SET telefon='0700-OFFLINE' WHERE nume=?", ({NUME!r},))
        conn.close()
        rep = cloud_sync.sync_all()          # fără server: nimic nu se pierde
        out['ok'] = rep['ok']; out['pending'] = cloud_sync.pending_count(); out['online'] = entity_sync.is_online()
    """, url="http://127.0.0.1:9")
    assert off["ok"] is False and off["pending"] >= 1 and off["online"] is False, off
    assert pg_one("SELECT telefon FROM concesionari WHERE nume=%s", (NUME,))[0] in (None, "")
    back = client("B", key, """
        rep = cloud_sync.sync_all()
        out['ok'] = rep['ok']; out['pending'] = cloud_sync.pending_count(); out['online'] = entity_sync.is_online()
    """)
    assert back["pending"] == 0 and back["online"] is True, back
    assert pg_one("SELECT telefon, version FROM concesionari WHERE nume=%s", (NUME,)) == ("0700-OFFLINE", 2)


def test_05_server_is_idempotent_per_op_id(key):
    uid = uuid.uuid4().hex
    op = {"op_id": f"e6-{RUN}-idem", "entity": "concesionari", "uid": uid, "action": "upsert", "base_version": 0,
          "data": {"nume": f"E6-{RUN}-Idem", "prenume": "X"}}
    h = {"X-Sync-Key": key, "X-Sync-Client": "test-idem"}
    r1 = requests.post(f"{SERVER}/api/sync/entities/push", json={"ops": [op]}, headers=h, timeout=15).json()["results"][0]
    r2 = requests.post(f"{SERVER}/api/sync/entities/push", json={"ops": [op]}, headers=h, timeout=15).json()["results"][0]
    assert r1["status"] == "applied" and r1["version"] == 1 and not r1.get("duplicate")
    assert r2["status"] == "applied" and r2["version"] == 1 and r2["duplicate"] is True
    assert pg_one("SELECT COUNT(*), MAX(version) FROM concesionari WHERE uid=%s", (uid,)) == (1, 1)
    # aceeași modificare reluată cu op_id NOU și base_version vechi, dar identică => adoptată, fără versiune nouă
    op2 = {**op, "op_id": f"e6-{RUN}-idem2"}
    r3 = requests.post(f"{SERVER}/api/sync/entities/push", json={"ops": [op2]}, headers=h, timeout=15).json()["results"][0]
    assert r3["status"] == "applied" and r3["version"] == 1
    with _pg() as conn:
        conn.execute("DELETE FROM concesionari WHERE uid=%s", (uid,))


def test_06_two_computers_same_object_conflict_is_detected_not_overwritten(key):
    # ambele clienți pornesc din aceeași versiune; A trimite întâi, B (modificare concurentă) trebuie să primească conflict
    client("A", key, """cloud_sync.sync_all()""")                           # A se aliniază la v2
    client("A", key, f"""
        conn = db.get_connection(); conn.execute("UPDATE concesionari SET adresa='Adresa A' WHERE nume=?", ({NUME!r},)); conn.close()
        out['ok'] = cloud_sync.sync_all()['ok']
    """)
    assert pg_one("SELECT adresa, version FROM concesionari WHERE nume=%s", (NUME,)) == ("Adresa A", 3)
    res = client("B", key, f"""
        conn = db.get_connection(); conn.execute("UPDATE concesionari SET adresa='Adresa B' WHERE nume=?", ({NUME!r},)); conn.close()
        # B modifică din versiunea veche (2) FĂRĂ să fi tras între timp modificarea lui A: blocăm descărcarea
        entity_sync.pull_entities = lambda: (True, 'skip', 0)
        rep = cloud_sync.sync_all()
        out['ok'] = rep['ok']; out['lines'] = rep['lines']
        out['conflicts'] = entity_sync.list_conflicts()
        conn = db.get_connection()
        out['local_adresa'] = conn.execute("SELECT adresa FROM concesionari WHERE nume=?", ({NUME!r},)).fetchone()['adresa']
        conn.close()
    """)
    assert res["ok"] is False and len(res["conflicts"]) == 1, res
    c = res["conflicts"][0]
    assert c["reason"] == "version_mismatch" and c["server"]["adresa"] == "Adresa A" and c["local"]["adresa"] == "Adresa B"
    assert res["local_adresa"] == "Adresa B"                                  # nimic local suprascris în tăcere
    assert pg_one("SELECT adresa FROM concesionari WHERE nume=%s", (NUME,))[0] == "Adresa A"   # nici pe server
    srv = pg_one("SELECT reason, base_version, server_version, client_data::jsonb->>'adresa' FROM sync_conflicts "
                 "WHERE entity='concesionari' AND resolved_at IS NULL ORDER BY id DESC LIMIT 1")
    assert srv == ("version_mismatch", 2, 3, "Adresa B")                      # serverul raportează conflictul


def test_07_resolve_conflict_keep_server_then_mine(key):
    keep_server = client("B", key, f"""
        c = entity_sync.list_conflicts()[0]
        out['resolved'] = entity_sync.resolve_conflict(c['op_id'], 'server')
        cloud_sync.sync_all()
        conn = db.get_connection()
        out['adresa'] = conn.execute("SELECT adresa FROM concesionari WHERE nume=?", ({NUME!r},)).fetchone()['adresa']
        conn.close()
        out['left'] = len(entity_sync.list_conflicts()); out['pending'] = cloud_sync.pending_count()
    """)
    assert keep_server["resolved"] and keep_server["adresa"] == "Adresa A" and keep_server["left"] == 0
    assert keep_server["pending"] == 0
    assert pg_one("SELECT COUNT(*) FROM sync_conflicts WHERE entity='concesionari' AND resolved_at IS NULL")[0] == 0
    # al doilea conflict, rezolvat „al meu"
    with _pg() as conn:      # modificare „din panoul web": trigger-ul PostgreSQL crește versiunea
        conn.execute("UPDATE concesionari SET observatii='din admin web' WHERE nume=%s", (NUME,))
    mine = client("B", key, f"""
        conn = db.get_connection(); conn.execute("UPDATE concesionari SET observatii='din B' WHERE nume=?", ({NUME!r},)); conn.close()
        entity_sync.pull_entities = lambda: (True, 'skip', 0)
        cloud_sync.sync_all()
        cs = entity_sync.list_conflicts()
        out['n'] = len(cs); out['reason'] = cs[0]['reason'] if cs else None
        entity_sync.resolve_conflict(cs[0]['op_id'], 'mine')
        out['ok'] = cloud_sync.sync_all()['ok']
    """)
    assert mine["n"] == 1 and mine["reason"] == "version_mismatch" and mine["ok"] is True, mine
    assert pg_one("SELECT observatii FROM concesionari WHERE nume=%s", (NUME,))[0] == "din B"


def test_08_delete_propagates_to_other_computer(key):
    client("A", key, f"""
        cloud_sync.sync_all()
        conn = db.get_connection(); conn.execute("DELETE FROM plati WHERE observatii='E6-{RUN}-plata'"); conn.close()
        out['ok'] = cloud_sync.sync_all()['ok']
    """)
    assert pg_one("SELECT COUNT(*) FROM plati WHERE observatii=%s", (f"E6-{RUN}-plata",))[0] == 0
    assert pg_one("SELECT COUNT(*) FROM sync_tombstones WHERE entity='plati'")[0] >= 1
    res = client("B", key, f"""
        cloud_sync.sync_all()
        conn = db.get_connection()
        out['n'] = conn.execute("SELECT COUNT(*) AS n FROM plati WHERE observatii='E6-{RUN}-plata'").fetchone()['n']
        conn.close(); out['pending'] = cloud_sync.pending_count()
    """)
    assert res["n"] == 0 and res["pending"] == 0


def test_09_lost_ack_followed_by_new_edit_is_not_a_false_conflict(key):
    """Confirmarea unui op se pierde pe drum; clientul editează iar din aceeași bază veche. Același client e ultimul
    autor, deci serverul continuă lucrul propriu (nu raportează conflict cu el însuși)."""
    uid, h = uuid.uuid4().hex, {"X-Sync-Key": key, "X-Sync-Client": "main-lostack"}
    post = lambda op: requests.post(f"{SERVER}/api/sync/entities/push", json={"ops": [op]}, headers=h, timeout=15).json()["results"][0]
    base = {"entity": "concesionari", "uid": uid, "action": "upsert"}
    a = post({**base, "op_id": f"e6-{RUN}-la1", "base_version": 0, "data": {"nume": f"E6-{RUN}-LostAck", "adresa": "v1"}})
    assert a["status"] == "applied" and a["version"] == 1
    b = post({**base, "op_id": f"e6-{RUN}-la2", "base_version": 0, "data": {"nume": f"E6-{RUN}-LostAck", "adresa": "v2"}})
    assert b["status"] == "applied" and b["version"] == 2, b
    # alt client, aceeași bază veche => conflict real
    h2 = {"X-Sync-Key": key, "X-Sync-Client": "main-other"}
    c = requests.post(f"{SERVER}/api/sync/entities/push", headers=h2, timeout=15,
                      json={"ops": [{**base, "op_id": f"e6-{RUN}-la3", "base_version": 1, "data": {"nume": f"E6-{RUN}-LostAck", "adresa": "v3"}}]}).json()["results"][0]
    assert c["status"] == "conflict" and c["reason"] == "version_mismatch" and c["server_version"] == 2
    assert pg_one("SELECT adresa FROM concesionari WHERE uid=%s", (uid,))[0] == "v2"
    with _pg() as conn:
        conn.execute("DELETE FROM concesionari WHERE uid=%s", (uid,))


def test_10_child_before_parent_is_retried_not_lost(key):
    h = {"X-Sync-Key": key, "X-Sync-Client": "main-order"}
    parent, child = uuid.uuid4().hex, uuid.uuid4().hex
    kid = {"op_id": f"e6-{RUN}-k", "entity": "contracte", "uid": child, "action": "upsert", "base_version": 0,
           "data": {"numar_contract": f"E6-{RUN}-C-order", "concesionar_uid": parent}}
    r = requests.post(f"{SERVER}/api/sync/entities/push", json={"ops": [kid]}, headers=h, timeout=15).json()["results"][0]
    assert r["status"] == "retry" and r["reason"].startswith("missing_parent")
    assert pg_one("SELECT COUNT(*) FROM sync_ops WHERE op_id=%s", (kid["op_id"],))[0] == 0   # nu se memorează: va fi reluat
    par = {"op_id": f"e6-{RUN}-p", "entity": "concesionari", "uid": parent, "action": "upsert", "base_version": 0,
           "data": {"nume": f"E6-{RUN}-Parent"}}
    rs = requests.post(f"{SERVER}/api/sync/entities/push", json={"ops": [par, kid]}, headers=h, timeout=15).json()["results"]
    assert [x["status"] for x in rs] == ["applied", "applied"]
    with _pg() as conn:
        conn.execute("DELETE FROM contracte WHERE uid=%s", (child,))
        conn.execute("DELETE FROM concesionari WHERE uid=%s", (parent,))


def test_11_same_cod_loc_created_on_two_computers_is_reported(key):
    cod = f"E6-{RUN}-DUP"
    a = client("A", key, f"""
        conn = db.get_connection(); conn.execute("INSERT INTO locuri_de_veci (cod_loc, sector) VALUES (?, 'S-A')", ({cod!r},)); conn.close()
        out['ok'] = cloud_sync.sync_all()['ok']
    """)
    assert a["ok"] is True
    b = client("B", key, f"""
        conn = db.get_connection(); conn.execute("DELETE FROM locuri_de_veci WHERE cod_loc=?", ({cod!r},))
        conn.execute("INSERT INTO locuri_de_veci (cod_loc, sector) VALUES (?, 'S-B')", ({cod!r},)); conn.close()
        entity_sync.pull_entities = lambda: (True, 'skip', 0)
        cloud_sync.sync_all()
        cs = entity_sync.list_conflicts()
        out['reasons'] = [c['reason'] for c in cs if c['entity'] == 'locuri_de_veci']
    """)
    assert "natural_key" in b["reasons"], b
    assert pg_one("SELECT sector FROM locuri_de_veci WHERE cod_loc=%s", (cod,))[0] == "S-A"
    res = client("B", key, f"""
        cs = [c for c in entity_sync.list_conflicts() if c['reason'] == 'natural_key']
        entity_sync.resolve_conflict(cs[0]['op_id'], 'mine')
        out['ok'] = cloud_sync.sync_all()['ok']
    """)
    assert res["ok"] is True
    assert pg_one("SELECT sector FROM locuri_de_veci WHERE cod_loc=%s", (cod,))[0] == "S-B"


def test_12_public_portal_hides_burials_until_operator_publishes(key):
    cod = L1
    r = requests.get(f"{SERVER}/api/public/search", params={"q": f"E6-{RUN}-Defunct"}, timeout=10).json()
    assert r["results"] == []      # registrul complet e pe server, dar publicarea numelor e opt-in
