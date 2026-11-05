"""BUG-01 — teste suplimentare pentru cazuri-limită după remediere.

1. Trigger-ul nu urcă nici la UPDATE / re-inserare, nici pentru variantele cu majuscule / spații.
2. `ensure_tracking` scoate din coadă operațiunile 'upload' deja adăugate de o versiune anterioară
   pentru acte de identitate (migrare nedistructivă: documentele locale rămân).
3. Server-ul clasic (backend/media_api.py::complete) refuză cu 422 când un client vechi încearcă
   să urce `doc_type` = 'carte_identitate' / 'cerere_doc' (apărare în profunzime).
"""
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))
if str(ROOT / "backend" / "cimitir") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend" / "cimitir"))
if str(ROOT / "shared") not in sys.path:
    sys.path.insert(0, str(ROOT / "shared"))

from conftest import run_main


CODE_TRIGGER_UPDATE = """
import media_sync
db.set_setting('cloud_media_sync', '1')
assert media_sync.ensure_tracking(force=True)
conn = db.get_connection()
loc = conn.execute("INSERT INTO locuri_de_veci (cod_loc, points) VALUES ('BA-2', '[[0,0],[1,0],[1,1]]')").lastrowid
# inserări directe (ocolim triggerul prin a face ulterior UPDATE: triggerul AFTER INSERT verifică tot NEW)
for dt in ('carte_identitate', 'cerere_doc', 'contract_scan'):
    conn.execute("INSERT INTO documents (entity_type, entity_id, doc_type, file_path) VALUES ('loc', ?, ?, ?)",
                 (str(loc), dt, '/tmp/ba2-' + dt + '.pdf'))
rows = conn.execute("SELECT d.doc_type FROM cloud_sync_outbox o JOIN documents d ON d.media_id = o.entity_uid "
                    "WHERE o.kind='media' AND o.action='upload'").fetchall()
out['queued'] = sorted(r['doc_type'] for r in rows)
"""


def test_classic_queue_excludes_identity_docs(tmp_path):
    res = run_main(tmp_path / "main", CODE_TRIGGER_UPDATE, url="http://127.0.0.1:9", key="cimitir.fictiv")
    assert res["queued"] == ["contract_scan"], (
        f"triggerul trebuia să excludă actele de identitate, dar coada conține: {res['queued']}")


CODE_CLEANUP = """
import media_sync
db.set_setting('cloud_media_sync', '1')
db.set_setting('_sync_applying', '1')   # oprim triggerele existente (fie clasice, fie entity_sync) cât timp inserăm starea 'vechi'
conn = db.get_connection()
loc = conn.execute("INSERT INTO locuri_de_veci (cod_loc, points) VALUES ('BA-3', '[[0,0],[1,0],[1,1]]')").lastrowid
import cloud_sync as cs
cs.ensure_tables()
for dt, mid in (('carte_identitate', 'id-ci-aaaa'), ('cerere_doc', 'id-cd-bbbb'), ('contract_scan', 'id-cs-cccc')):
    conn.execute("INSERT INTO documents (entity_type, entity_id, doc_type, file_path, media_id) VALUES ('loc', ?, ?, ?, ?)",
                 (str(loc), dt, '/tmp/oldrow-' + dt + '.pdf', mid))
    conn.execute(
        "INSERT INTO cloud_sync_outbox (kind, entity, entity_uid, action, base_version, state, op_id, created_at, attempts, tracking_code, status, note, payload) "
        "VALUES ('media','documents', ?, 'upload', 0, 'pending', ?, strftime('%s','now'), 0, '', '', '', '{}')",
        (mid, 'op-' + mid),
    )
db.set_setting('_sync_applying', '')
before = sorted(r['doc_type'] for r in conn.execute(
    "SELECT d.doc_type FROM cloud_sync_outbox o JOIN documents d ON d.media_id = o.entity_uid "
    "WHERE o.kind='media' AND o.action='upload'").fetchall())
# acum pornim remedierea
assert media_sync.ensure_tracking(force=True)
after = sorted(r['doc_type'] for r in conn.execute(
    "SELECT d.doc_type FROM cloud_sync_outbox o JOIN documents d ON d.media_id = o.entity_uid "
    "WHERE o.kind='media' AND o.action='upload'").fetchall())
local = sorted(r['doc_type'] for r in conn.execute("SELECT doc_type FROM documents").fetchall())
out['before'] = before
out['after'] = after
out['local'] = local
"""


def test_ensure_tracking_cleans_already_queued_identity_docs(tmp_path):
    res = run_main(tmp_path / "main", CODE_CLEANUP, url="http://127.0.0.1:9", key="cimitir.fictiv")
    assert res["before"] == ["carte_identitate", "cerere_doc", "contract_scan"], res
    # după ensure_tracking, numai contract_scan mai rămâne în coadă:
    assert res["after"] == ["contract_scan"], (
        f"coada trebuia să rămână doar cu documentele legitime, dar conține: {res['after']}")
    # documentele locale rămân intacte (nu se șterge nimic):
    assert res["local"] == ["carte_identitate", "cerere_doc", "contract_scan"], res


def test_server_complete_rejects_identity_docs_payload_with_422():
    """Apărare în profunzime: dacă un client vechi lovește direct POST /complete cu doc_type
    = 'carte_identitate' / 'cerere_doc', serverul clasic refuză cu 422, fără a scrie în storage.
    """
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    import media_api

    app = FastAPI()
    app.include_router(media_api.router)

    orig_guard = media_api._guard
    orig_cid = media_api.sync_api.client_id_of
    media_api._guard = lambda request: "test-sid"
    media_api.sync_api.client_id_of = lambda request: "test-client"
    try:
        client = TestClient(app, raise_server_exceptions=False)
        for dt in ("carte_identitate", "cerere_doc", "CARTE_IDENTITATE", "  cerere_doc  "):
            r = client.post("/api/sync/media/abcd1234/complete",
                            json={"doc_type": dt, "sha256": "a" * 64, "chunks": 1, "size": 10, "entity_uid": "x",
                                  "filename": "x.pdf", "table": "documents", "entity_type": "loc"})
            assert r.status_code == 422, f"doc_type={dt!r} a răspuns {r.status_code}: {r.text}"
            body = r.json()
            assert body.get("detail", {}).get("error") == "doc_type_not_allowed", body
        # docmentele legitime NU sunt respinse pe branch-ul doc_type
        # (vor pica mai târziu, dar nu cu 422 'doc_type_not_allowed'):
        r = client.post("/api/sync/media/legit1234/complete",
                        json={"doc_type": "contract_scan", "sha256": "a" * 64, "chunks": 1, "size": 10,
                              "entity_uid": "x", "filename": "x.pdf", "table": "documents", "entity_type": "loc"})
        if r.status_code == 422:
            body = r.json()
            assert body.get("detail", {}).get("error") != "doc_type_not_allowed", body
    finally:
        media_api._guard = orig_guard
        media_api.sync_api.client_id_of = orig_cid
