"""BUG-01 (A1 / O-1) — conducta clasică `media_sync` pune în coadă pentru încărcare actele de identitate.

Fișier/cauză: desktop/media_sync.py:62-73 (`_create_triggers`): triggerul AFTER INSERT pe `documents` exclude doar
`entity_type='cerere'`, NU și `doc_type IN ('carte_identitate','cerere_doc')`. Serverul clasic nu filtrează nici el
(backend/media_api.py:84-129 `complete` acceptă orice doc_type). Sync v2 le exclude (desktop/sync_v2_docs.py:111-114,
shared/cimitir_sync_v2_docs.py:23/76).

AȘTEPTAT: niciun `upload` în cloud_sync_outbox pentru carte_identitate / cerere_doc; contract_scan -> 1 upload.
OBȚINUT (fără fix): 3 operațiuni `upload` (inclusiv cele două acte de identitate).
Rulare: python -m pytest validation/bug_audit/test_bug01_media_sync_identity_docs.py -q
"""
from conftest import run_main

CODE = """
import media_sync
db.set_setting('cloud_media_sync', '1')
assert media_sync.ensure_tracking(force=True)
conn = db.get_connection()
loc = conn.execute("INSERT INTO locuri_de_veci (cod_loc, points) VALUES ('BA-1', '[[0,0],[1,0],[1,1]]')").lastrowid
for dt in ('carte_identitate', 'contract_scan', 'cerere_doc'):
    conn.execute("INSERT INTO documents (entity_type, entity_id, doc_type, file_path) VALUES ('loc', ?, ?, ?)",
                 (str(loc), dt, '/tmp/ba-' + dt + '.pdf'))
rows = conn.execute("SELECT d.doc_type FROM cloud_sync_outbox o JOIN documents d ON d.media_id = o.entity_uid "
                    "WHERE o.kind='media' AND o.action='upload'").fetchall()
out['queued'] = sorted(r['doc_type'] for r in rows)
"""


def test_identity_documents_are_not_queued_for_classic_upload(tmp_path):
    res = run_main(tmp_path / "main", CODE, url="http://127.0.0.1:9", key="cimitir.fictiv")
    assert "contract_scan" in res["queued"], res
    leaked = [d for d in res["queued"] if d in ("carte_identitate", "cerere_doc")]
    assert not leaked, f"acte de identitate puse în coada de încărcare clasică: {leaked} (toate: {res['queued']})"
