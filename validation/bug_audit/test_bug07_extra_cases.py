"""BUG-07 — cazuri-limită: tombstone-ul blochează recrearea; documentele legitime se încarcă normal."""
import hashlib
import uuid

import requests

import cimitir_backup as cb
from conftest import needs_pg, pg_rows, run_main
from test_bug07_restore_resurrects_deleted_document import A_SETUP, B_DELETE, SYNC

pytestmark = needs_pg

LOCAL_STATE = """
conn = db.get_connection()
out['docs'] = conn.execute("SELECT COUNT(*) AS n FROM documents WHERE media_id=?", (MID,)).fetchone()['n']
out['pending'] = conn.execute("SELECT COUNT(*) AS n FROM cloud_sync_outbox WHERE kind='media' AND COALESCE(state,'pending')='pending'").fetchone()['n']
out['errors'] = conn.execute("SELECT COUNT(*) AS n FROM cloud_sync_outbox WHERE kind='media' AND state='error'").fetchone()['n']
conn.close()
"""


def _headers(srv):
    return {"X-Sync-Key": srv["key"], "X-Sync-Client": "test-client"}


def _scenario(tmp_path, srv):
    cod = "BX7-" + uuid.uuid4().hex[:6]
    a, b = tmp_path / "A", tmp_path / "B"
    r = run_main(a, f"COD = {cod!r}\n" + A_SETUP, srv["url"], srv["key"])
    mid = r["media_id"]
    copy = cb.backup(a, tmp_path / "copii-A")
    run_main(a, SYNC, srv["url"], srv["key"])
    run_main(b, f"MID = {mid!r}\n" + B_DELETE, srv["url"], srv["key"])
    run_main(a, SYNC, srv["url"], srv["key"])
    return a, mid, copy


def test_after_restore_stale_op_is_dropped_local_row_removed_and_no_error_left(tmp_path, classic_server):
    srv = classic_server
    a, mid, copy = _scenario(tmp_path, srv)
    cb.restore(copy, a, force=True)
    r = run_main(a, f"MID = {mid!r}\n" + SYNC + LOCAL_STATE, srv["url"], srv["key"])
    assert not pg_rows(srv["dsn"], "SELECT 1 FROM media_objects WHERE id=%s", (mid,))
    assert r["docs"] == 0, "pull-ul elimină rândul local al documentului șters în Cloud"
    assert r["pending"] == 0 and r["errors"] == 0, r


def test_server_refuses_chunk_and_complete_for_deleted_media(tmp_path, classic_server):
    srv = classic_server
    a, mid, copy = _scenario(tmp_path, srv)
    data = b"%PDF-1.4 fictiv"
    r = requests.put(f"{srv['url']}/api/sync/media/{mid}/chunks/0", data=data, headers=_headers(srv), timeout=30)
    assert r.status_code == 410, (r.status_code, r.text)
    body = {"entity_uid": "x", "filename": "f.pdf", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "chunks": 1,
            "table": "documents", "entity_type": "loc", "doc_type": "contract_scan"}
    r = requests.post(f"{srv['url']}/api/sync/media/{mid}/complete", json=body, headers=_headers(srv), timeout=30)
    assert r.status_code == 410 and r.json()["detail"]["error"] == "deleted", (r.status_code, r.text)
    assert not pg_rows(srv["dsn"], "SELECT 1 FROM media_objects WHERE id=%s", (mid,))


def test_never_deleted_document_still_uploads_after_restore(tmp_path, classic_server):
    srv = classic_server
    cod = "BN7-" + uuid.uuid4().hex[:6]
    a = tmp_path / "A"
    r = run_main(a, f"COD = {cod!r}\n" + A_SETUP, srv["url"], srv["key"])
    mid = r["media_id"]
    copy = cb.backup(a, tmp_path / "copii-A")
    cb.restore(copy, a, force=True)
    run_main(a, SYNC, srv["url"], srv["key"])
    assert pg_rows(srv["dsn"], "SELECT 1 FROM media_objects WHERE id=%s", (mid,)), "documentul nesters se încarcă normal"


def test_unknown_media_id_is_not_treated_as_deleted(tmp_path, classic_server):
    srv = classic_server
    fresh = str(uuid.uuid4())
    r = requests.put(f"{srv['url']}/api/sync/media/{fresh}/chunks/0", data=b"abc", headers=_headers(srv), timeout=30)
    assert r.status_code == 200, (r.status_code, r.text)
