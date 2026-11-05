"""BUG-04 (A6) — după restaurarea unei copii vechi pe un MAIN care sincronizează, prima modificare locală suprascrie
în tăcere, pe server, modificările altui calculator făcute între copie și restaurare (fără conflict).

Fișier/cauză:
  * backend/entity_sync_api.py:132-141 `_last_writer_is` + :158/:196: un `base_version` depășit NU e conflict dacă versiunea
    curentă a rândului pe server a fost produsă de ACELAȘI `client_id`;
  * `client_id` este în tabela `settings` din baza SQLite (desktop/cloud_sync.py:220-226), deci copia restaurată păstrează
    același id, dar „uită" versiunile (rândurile revin la base_version vechi);
  * cloud_sync.sync_all trimite outbox-ul ÎNAINTE de pull (desktop/entity_sync.py:495-500), deci rândul editat imediat după
    restaurare pleacă cu base_version vechi, iar `_last_writer_is` îl acceptă: rândul este rescris integral (toate coloanele).
Scenariu: A creează rândul (v1) -> COPIE -> B schimbă telefonul (v2) -> A aduce v2 și schimbă adresa (v3, autor A)
-> A restaurează copia (rândul revine la v1 local) -> A schimbă prenumele -> sync.
AȘTEPTAT: conflict (sau păstrarea telefonului lui B pe server). OBȚINUT (fără fix): `applied`, telefonul lui B și adresa
revin în tăcere la valorile din copie; nimic în sync_conflicts.
Al doilea test simulează remedierea propusă (client_id nou după restaurare) și arată că serverul raportează conflictul.
Rulare: BUGAUDIT_PG_ADMIN_URL=postgresql://<superuser>@127.0.0.1:<port>/postgres python -m pytest validation/bug_audit/test_bug04_restore_sync_silent_overwrite.py -q
"""
import uuid

import pytest

import cimitir_backup as cb
from conftest import needs_pg, pg_rows, run_main

pytestmark = needs_pg

SETUP = """
entity_sync.ensure_tracking(force=True)
conn = db.get_connection()
conn.execute("INSERT INTO concesionari (nume, prenume, telefon, adresa) VALUES (?, 'Ion', '0700-ORIGINAL', 'Str. Veche 1')", (NUME,))
conn.close()
rep = cloud_sync.sync_all(); out['ok'] = rep['ok']; out['lines'] = rep['lines']
"""
B_EDIT = """
rep = cloud_sync.sync_all()
conn = db.get_connection()
conn.execute("UPDATE concesionari SET telefon='0711-DE-LA-B' WHERE nume=?", (NUME,))
conn.close()
rep = cloud_sync.sync_all(); out['ok'] = rep['ok']
"""
A_EDIT = """
rep = cloud_sync.sync_all()
conn = db.get_connection()
out['seen_b'] = conn.execute("SELECT telefon FROM concesionari WHERE nume=?", (NUME,)).fetchone()['telefon']
conn.execute("UPDATE concesionari SET adresa='Str. Noua 99' WHERE nume=?", (NUME,))
conn.close()
rep = cloud_sync.sync_all(); out['ok'] = rep['ok']
"""
A_AFTER_RESTORE = """
RESET
conn = db.get_connection()
out['local_before'] = dict(conn.execute("SELECT telefon, adresa, version FROM concesionari WHERE nume=?", (NUME,)).fetchone())
conn.execute("UPDATE concesionari SET prenume='Ion-Corectat' WHERE nume=?", (NUME,))
conn.close()
rep = cloud_sync.sync_all(); out['ok'] = rep['ok']; out['lines'] = rep['lines']
"""


def _scenario(tmp_path, srv, reset_client_id: bool):
    nume = "BA4-" + uuid.uuid4().hex[:6]
    pre = f"NUME = {nume!r}\n"
    a, b = tmp_path / "A", tmp_path / "B"
    url, key = srv["url"], srv["key"]
    r = run_main(a, pre + SETUP, url, key)
    assert r["ok"], r
    copy = cb.backup(a, tmp_path / "copii-A")                              # copia de rezervă a lui A (rândul la v1)
    assert run_main(b, pre + B_EDIT, url, key)["ok"]
    r = run_main(a, pre + A_EDIT, url, key)
    assert r["ok"] and r["seen_b"] == "0711-DE-LA-B", r
    server = pg_rows(srv["dsn"], "SELECT telefon, adresa, version FROM concesionari WHERE nume=%s", (nume,))[0]
    assert server == {"telefon": "0711-DE-LA-B", "adresa": "Str. Noua 99", "version": 3}, server
    cb.restore(copy, a, force=True)                                        # restaurarea manuală (aceeași funcție ca la pornire)
    reset = "db.set_setting('cloud_client_id', '')" if reset_client_id else "pass"
    # BUG-02: restaurarea scoate cheia din folderul de date -> utilizatorul o reintroduce (save_config din run_main)
    r = run_main(a, pre + A_AFTER_RESTORE.replace("RESET", reset), url, key)
    assert r["local_before"]["version"] == 1, r
    server = pg_rows(srv["dsn"], "SELECT prenume, telefon, adresa, version FROM concesionari WHERE nume=%s", (nume,))[0]
    conflicts = pg_rows(srv["dsn"], "SELECT reason FROM sync_conflicts WHERE entity='concesionari' AND uid=(SELECT uid FROM concesionari WHERE nume=%s)", (nume,))
    return server, conflicts, r


def test_restored_main_does_not_silently_overwrite_other_client_changes(tmp_path, classic_server):
    server, conflicts, r = _scenario(tmp_path, classic_server, reset_client_id=False)
    lost = server["telefon"] != "0711-DE-LA-B"
    assert not (lost and not conflicts), (
        f"modificarea lui B a fost suprascrisă FĂRĂ conflict după restaurare: server={server}, conflicte={conflicts}, sync={r['lines']}")


def test_fix_simulation_new_client_id_after_restore_reports_conflict(tmp_path, classic_server):
    """Remedierea propusă (client_id regenerat la restaurare): serverul nu mai aplică orbește, ci raportează conflict."""
    server, conflicts, r = _scenario(tmp_path, classic_server, reset_client_id=True)
    assert server["telefon"] == "0711-DE-LA-B" and conflicts, (server, conflicts, r)


def test_check_restore_does_not_resurrect_row_deleted_elsewhere(tmp_path, classic_server):
    """Verificare (nu bug așteptat): rândul șters de B după copie nu reapare pe server după restaurarea lui A."""
    srv = classic_server
    nume = "BA4D-" + uuid.uuid4().hex[:6]
    pre = f"NUME = {nume!r}\n"
    a, b = tmp_path / "A", tmp_path / "B"
    assert run_main(a, pre + SETUP, srv["url"], srv["key"])["ok"]
    copy = cb.backup(a, tmp_path / "copii-A")
    r = run_main(b, pre + """
rep = cloud_sync.sync_all()
conn = db.get_connection()
conn.execute("DELETE FROM concesionari WHERE nume=?", (NUME,))
conn.close()
rep = cloud_sync.sync_all(); out['ok'] = rep['ok']
""", srv["url"], srv["key"])
    assert r["ok"], r
    assert not pg_rows(srv["dsn"], "SELECT 1 FROM concesionari WHERE nume=%s", (nume,))
    cb.restore(copy, a, force=True)
    r = run_main(a, pre + """
rep = cloud_sync.sync_all()
conn = db.get_connection()
out['local'] = conn.execute("SELECT COUNT(*) AS n FROM concesionari WHERE nume=?", (NUME,)).fetchone()['n']
out['lines'] = rep['lines']
""", srv["url"], srv["key"])
    assert r["local"] == 0, f"rândul șters în Cloud a rămas local după restaurare + sync: {r}"
    assert not pg_rows(srv["dsn"], "SELECT 1 FROM concesionari WHERE nume=%s", (nume,)), "rândul șters a fost recreat pe server"
