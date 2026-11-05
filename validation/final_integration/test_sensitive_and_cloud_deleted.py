"""Audit final — protecția CNP/CI și documente șterse din Cloud, combinate cu restaurarea (BUG-01 + BUG-07 + BUG-04). Date fictive, PG local temporar."""
import sqlite3
import uuid
from pathlib import Path

import pytest

import cimitir_backup as cb
from conftest import needs_pg, pg_rows, run_main

pytestmark = needs_pg
CNP_FICTIV = "1800101123456"

DOC_SETUP = """
import os, media_sync
rep = cloud_sync.sync_all()
conn = db.get_connection()
conn.execute("INSERT INTO locuri_de_veci (cod_loc, points) VALUES (?, '[[0,0],[1,0],[1,1]]')", (COD,))
conn.close()
rep = cloud_sync.sync_all()
media_dir = os.path.join(os.environ['CIMITIR_DATA_DIR'], 'media', 'documente'); os.makedirs(media_dir, exist_ok=True)
path = os.path.join(media_dir, COD + '-contract.pdf'); open(path, 'wb').write(b'%PDF-1.4 fictiv ' + COD.encode() * 50)
conn = db.get_connection()
conn.execute("INSERT INTO documents (entity_type, entity_id, doc_type, file_path) VALUES ('loc', ?, 'contract_scan', ?)", (COD, path))
out['media_id'] = conn.execute("SELECT media_id FROM documents WHERE file_path=?", (path,)).fetchone()['media_id']
conn.close()
"""
SYNC = "rep = cloud_sync.sync_all(); out['ok'] = rep['ok']; out['lines'] = rep['lines']\n"
LOCAL_DOC = """
conn = db.get_connection()
out['local'] = conn.execute("SELECT COUNT(*) AS n FROM documents WHERE media_id=?", (MID,)).fetchone()['n']
out['pending'] = [dict(r) for r in conn.execute("SELECT action, state, last_error FROM cloud_sync_outbox WHERE kind='media' AND entity_uid=?", (MID,))]
conn.close()
"""


def test_restore_of_already_uploaded_document_deleted_in_cloud_is_removed_not_reuploaded(tmp_path, classic_server):
    """Copia conține documentul DEJA urcat (fără operațiune în coadă); B îl șterge din Cloud; A restaurează și sincronizează."""
    srv = classic_server
    cod = "FI7-" + uuid.uuid4().hex[:6]
    a, b = tmp_path / "A", tmp_path / "B"
    r = run_main(a, f"COD = {cod!r}\n" + DOC_SETUP + SYNC, srv["url"], srv["key"])
    mid = r["media_id"]
    assert pg_rows(srv["dsn"], "SELECT 1 FROM media_objects WHERE id=%s", (mid,)), r
    copy = cb.backup(a, tmp_path / "copii-A")                                           # copia conține documentul deja în Cloud
    rb = run_main(b, f"MID = {mid!r}\n" + SYNC + """
conn = db.get_connection(); conn.execute("DELETE FROM documents WHERE media_id=?", (MID,)); conn.close()
""" + SYNC, srv["url"], srv["key"])
    assert not pg_rows(srv["dsn"], "SELECT 1 FROM media_objects WHERE id=%s", (mid,)), rb
    cb.restore(copy, a, force=True)
    ra = run_main(a, f"MID = {mid!r}\n" + SYNC + SYNC + LOCAL_DOC, srv["url"], srv["key"])
    assert not pg_rows(srv["dsn"], "SELECT 1 FROM media_objects WHERE id=%s", (mid,)), f"documentul șters a reapărut în Cloud: {ra}"
    assert ra["local"] == 0, f"documentul șters în Cloud a rămas pe calculatorul restaurat: {ra}"
    assert not [p for p in ra["pending"] if p["state"] == "error"], ra


def test_pre_fix_identity_upload_in_error_state_inside_a_restored_backup_does_not_reach_the_cloud(tmp_path, classic_server):
    """Copie veche (înainte de BUG-01) cu operațiuni de urcare pentru acte de identitate în stările `pending` ȘI `error`. După restaurare + sync nu are voie să ajungă nimic în media_objects."""
    srv = classic_server
    cod = "FI1-" + uuid.uuid4().hex[:6]
    a = tmp_path / "A"
    seed = f"COD = {cod!r}\n" + """
import os, media_sync, cloud_sync as cs
rep = cloud_sync.sync_all()
conn = db.get_connection()
conn.execute("INSERT INTO locuri_de_veci (cod_loc, points) VALUES (?, '[[0,0],[1,0],[1,1]]')", (COD,))
conn.close()
rep = cloud_sync.sync_all()
db.set_setting('_sync_applying', '1')
media_dir = os.path.join(os.environ['CIMITIR_DATA_DIR'], 'media', 'documente'); os.makedirs(media_dir, exist_ok=True)
conn = db.get_connection()
ids = []
for i, (dt, st) in enumerate((('carte_identitate', 'pending'), ('cerere_doc', 'error'), ('carte_identitate', 'error'))):
    path = os.path.join(media_dir, COD + f'-{i}.pdf'); open(path, 'wb').write(b'%PDF-1.4 ACT-FICTIV ' + COD.encode() * 20)
    mid = f'ffff{i}' + COD.replace('-', '').lower().ljust(27, '0')[:27]
    conn.execute("INSERT INTO documents (entity_type, entity_id, doc_type, file_path, media_id) VALUES ('loc', ?, ?, ?, ?)", (COD, dt, path, mid))
    conn.execute("INSERT INTO cloud_sync_outbox (kind, entity, entity_uid, action, base_version, state, op_id, created_at, attempts, last_error, tracking_code, status, note, payload) "
                 "VALUES ('media','documents', ?, 'upload', 0, ?, ?, strftime('%s','now'), 0, '', '', '', '', '{}')", (mid, st, 'op' + mid))
    ids.append(mid)
conn.close()
db.set_setting('_sync_applying', '')
out['ids'] = ids
"""
    r = run_main(a, seed, srv["url"], srv["key"])
    copy = cb.backup(a, tmp_path / "copii-A")
    cb.restore(copy, a, force=True)
    after = run_main(a, "MID = ''\n" + SYNC + """
import media_sync
conn = db.get_connection()
out['queue'] = [dict(x) for x in conn.execute("SELECT entity_uid, state FROM cloud_sync_outbox WHERE kind='media'")]
conn.close()
""", srv["url"], srv["key"])
    got = pg_rows(srv["dsn"], "SELECT id FROM media_objects WHERE id = ANY(%s)", (r["ids"],))
    assert not got, f"acte de identitate din copia veche au ajuns în Cloud: {got}"
    stuck = [q for q in after["queue"] if q["entity_uid"] in r["ids"]]
    assert not [q for q in stuck if q["state"] == "pending"], f"operațiuni ACTIVE pentru acte de identitate au rămas în coadă: {stuck}"
    assert all(q["state"] == "error" for q in stuck)       # reziduu inert: rândurile `error` NU sunt curățate de BUG-01 (doar `pending`) și rămân ca „erori" permanente în raportul de sincronizare


def test_cnp_is_plaintext_in_backup_copy_characterization(tmp_path):
    """RISC CONFIRMAT (nu bug de cod): copia de rezervă conține CNP/CI în clar (SQLite necriptat) și este un simplu folder, fără parolă."""
    data = tmp_path / "data"
    data.mkdir()
    c = sqlite3.connect(data / "cimitir.db")
    c.execute("CREATE TABLE concesionari (id INTEGER PRIMARY KEY, nume TEXT, cnp TEXT, bi_ci TEXT)")
    c.execute("INSERT INTO concesionari (nume, cnp, bi_ci) VALUES ('Fictiv', ?, 'XX123456')", (CNP_FICTIV,))
    c.commit()
    c.close()
    copy = cb.backup(data, tmp_path / "copii")
    raw = (copy / "cimitir.db").read_bytes()
    assert CNP_FICTIV.encode() in raw and b"XX123456" in raw


def test_cnp_columns_reach_cloud_in_plaintext_characterization(tmp_path, classic_server):
    """Caracterizare: sincronizarea clasică urcă `cnp`/`bi_ci` ale concesionarilor în PostgreSQL (Cloud) în clar; protecția este doar acces (RLS/permisiuni), nu criptare la nivel de câmp."""
    srv = classic_server
    nume = "FICNP-" + uuid.uuid4().hex[:6]
    a = tmp_path / "A"
    code = f"NUME = {nume!r}\n" + f"""
rep = cloud_sync.sync_all()
entity_sync.ensure_tracking(force=True)
conn = db.get_connection()
conn.execute("INSERT INTO concesionari (nume, prenume, cnp, bi_ci) VALUES (?, 'X', '{CNP_FICTIV}', 'XX123456')", (NUME,))
conn.close()
rep = cloud_sync.sync_all(); out['ok'] = rep['ok']; out['lines'] = rep['lines']
"""
    r = run_main(a, code, srv["url"], srv["key"])
    assert r["ok"], r
    row = pg_rows(srv["dsn"], "SELECT cnp, bi_ci FROM concesionari WHERE nume=%s", (nume,))[0]
    assert row["cnp"] == CNP_FICTIV and row["bi_ci"] == "XX123456"
