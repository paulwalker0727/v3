"""BUG-07 (A6) — după restaurarea unei copii, un document șters între timp din Cloud (de alt calculator) este REÎNCĂRCAT
și reapare peste tot.

Fișier/cauză:
  * copia conține în `cloud_sync_outbox` operațiunea `upload` încă în așteptare la momentul copiei (desktop/media_sync.py:67-73);
  * după restaurare, desktop/media_sync.py:199-243 `_upload` o retrimite cu același media_id;
  * serverul (backend/media_api.py:84-129 `complete`) verifică doar `media.get_object(media_id)`; nu consultă tombstone-ul din
    `sync_tombstones` (backend/cimitir/media_service.py:157), deci obiectul șters este recreat.
Scenariu: A adaugă un document (în coadă) -> COPIE -> A sincronizează (urcat) -> B îl șterge (șters în Cloud) -> A sincronizează
-> A restaurează copia -> A sincronizează.
AȘTEPTAT: documentul rămâne șters (cel mult conflict/avertisment). OBȚINUT (fără fix): obiectul revine în media_objects.
Risc: un document șters intenționat (ex. copie de act eliminată la cerere) reapare în Cloud și pe celelalte calculatoare.
"""
import uuid

import cimitir_backup as cb
from conftest import needs_pg, pg_rows, run_main

pytestmark = needs_pg

A_SETUP = """
import os, media_sync
rep = cloud_sync.sync_all()                          # stabilește capabilitățile (entități + media)
conn = db.get_connection()
conn.execute("INSERT INTO locuri_de_veci (cod_loc, points) VALUES (?, '[[0,0],[1,0],[1,1]]')", (COD,))
conn.close()
rep = cloud_sync.sync_all(); out['ok1'] = rep['ok']
out['media_capable'] = media_sync.capable()
media_dir = db.get_media_dir() if hasattr(db, 'get_media_dir') else os.path.join(os.environ['CIMITIR_DATA_DIR'], 'media')
os.makedirs(os.path.join(media_dir, 'documente'), exist_ok=True)
path = os.path.join(media_dir, 'documente', COD + '-contract.pdf')
open(path, 'wb').write(b'%PDF-1.4 fictiv ' + COD.encode() * 50)
conn = db.get_connection()
conn.execute("INSERT INTO documents (entity_type, entity_id, doc_type, file_path) VALUES ('loc', ?, 'contract_scan', ?)", (COD, path))
out['media_id'] = conn.execute("SELECT media_id FROM documents WHERE file_path=?", (path,)).fetchone()['media_id']
out['pending'] = conn.execute("SELECT COUNT(*) AS n FROM cloud_sync_outbox WHERE kind='media' AND action='upload'").fetchone()['n']
conn.close()
"""
SYNC = "rep = cloud_sync.sync_all(); out['ok'] = rep['ok']; out['lines'] = rep['lines']\n"
B_DELETE = """
rep = cloud_sync.sync_all()
conn = db.get_connection()
out['b_has'] = conn.execute("SELECT COUNT(*) AS n FROM documents WHERE media_id=?", (MID,)).fetchone()['n']
conn.execute("DELETE FROM documents WHERE media_id=?", (MID,))
conn.close()
rep = cloud_sync.sync_all(); out['ok'] = rep['ok']; out['lines'] = rep['lines']
"""


def test_restore_does_not_resurrect_document_deleted_in_cloud(tmp_path, classic_server):
    srv = classic_server
    cod = "BA7-" + uuid.uuid4().hex[:6]
    a, b = tmp_path / "A", tmp_path / "B"
    r = run_main(a, f"COD = {cod!r}\n" + A_SETUP, srv["url"], srv["key"])
    assert r["ok1"] and r["media_capable"] and r["pending"] == 1, r
    mid = r["media_id"]
    copy = cb.backup(a, tmp_path / "copii-A")                     # copia conține upload-ul în așteptare
    r = run_main(a, SYNC, srv["url"], srv["key"])
    assert pg_rows(srv["dsn"], "SELECT 1 FROM media_objects WHERE id=%s", (mid,)), r
    r = run_main(b, f"MID = {mid!r}\n" + B_DELETE, srv["url"], srv["key"])
    assert r["b_has"] == 1 and r["ok"], r
    assert not pg_rows(srv["dsn"], "SELECT 1 FROM media_objects WHERE id=%s", (mid,)), "B nu a șters documentul din Cloud"
    run_main(a, SYNC, srv["url"], srv["key"])
    cb.restore(copy, a, force=True)
    r = run_main(a, SYNC, srv["url"], srv["key"])                 # cheia se reintroduce (BUG-02) prin save_config
    back = pg_rows(srv["dsn"], "SELECT id, filename FROM media_objects WHERE id=%s", (mid,))
    assert not back, f"documentul șters în Cloud a fost reîncărcat după restaurare: {back}; sync={r['lines']}"
