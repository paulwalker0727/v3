"""Audit final — scenarii combinate sincronizare + restaurare (BUG-04 + BUG-08 + BUG-01 + BUG-07 împreună). Doar date fictive, PostgreSQL LOCAL temporar,
server clasic uvicorn real pe port liber, clienți MAIN în procese separate (SQLite izolat). Nu atinge Supabase/PROD/Docker.
Rulare: BUGAUDIT_PG_ADMIN_URL=postgresql://postgres@127.0.0.1:<port>/postgres python -m pytest validation/final_integration -q
"""
import sqlite3
import uuid
from pathlib import Path

import pytest

import cimitir_backup as cb
from conftest import needs_pg, pg_rows, run_main

pytestmark = needs_pg

CREATE = """
entity_sync.ensure_tracking(force=True)
conn = db.get_connection()
conn.execute("INSERT INTO concesionari (nume, prenume, telefon, adresa) VALUES (?, 'Ion', '0700-ORIG', 'Str. Veche 1')", (NUME,))
conn.close()
rep = cloud_sync.sync_all(); out['ok'] = rep['ok']
"""
SYNC = "rep = cloud_sync.sync_all(); out['ok'] = rep['ok']; out['lines'] = rep['lines']\n"
SHOW = """
conn = db.get_connection()
r = conn.execute("SELECT prenume, telefon, adresa, version FROM concesionari WHERE nume=?", (NUME,)).fetchone()
out['row'] = dict(r) if r else None
out['outbox'] = [dict(x) for x in conn.execute("SELECT entity, action, state, last_error FROM cloud_sync_outbox")]
conn.close()
"""


def edit(sql: str, sync: bool = True) -> str:
    return f"conn = db.get_connection()\nconn.execute({sql!r}, (NUME,))\nconn.close()\n" + (SYNC if sync else "")


def srv_row(srv, nume):
    return pg_rows(srv["dsn"], "SELECT prenume, telefon, adresa, version FROM concesionari WHERE nume=%s", (nume,))[0]


def conflicts(srv, nume):
    return pg_rows(srv["dsn"], "SELECT reason FROM sync_conflicts WHERE entity='concesionari' AND uid=(SELECT uid FROM concesionari WHERE nume=%s)", (nume,))


def setup_two(tmp_path, srv):
    nume = "FI-" + uuid.uuid4().hex[:6]
    pre = f"NUME = {nume!r}\n"
    a, b = tmp_path / "A", tmp_path / "B"
    assert run_main(a, pre + CREATE, srv["url"], srv["key"])["ok"]
    assert run_main(b, pre + SYNC, srv["url"], srv["key"])["ok"]                  # B aduce rândul (v1)
    return nume, pre, a, b


def test_two_computers_edit_same_record_from_same_base_no_silent_loss(tmp_path, classic_server):
    srv = classic_server
    nume, pre, a, b = setup_two(tmp_path, srv)
    run_main(a, pre + edit("UPDATE concesionari SET telefon='0711-DE-LA-A' WHERE nume=?", sync=False), srv["url"], srv["key"])   # ambele offline, aceeași bază v1
    run_main(b, pre + edit("UPDATE concesionari SET telefon='0722-DE-LA-B' WHERE nume=?", sync=False), srv["url"], srv["key"])
    ra = run_main(a, pre + SYNC, srv["url"], srv["key"])
    rb = run_main(b, pre + SYNC, srv["url"], srv["key"])
    server, conf = srv_row(srv, nume), conflicts(srv, nume)
    assert server["telefon"] == "0711-DE-LA-A", (server, ra, rb)                  # primul ajuns câștigă
    assert conf, f"modificarea lui B nu a lăsat NICIO urmă (nici conflict): server={server}, B={rb}"
    assert rb["ok"] is False or conf                                              # B este informat (conflict), nu primește „OK" fals


def test_two_computers_edit_different_fields_concurrently_no_silent_loss(tmp_path, classic_server):
    srv = classic_server
    nume, pre, a, b = setup_two(tmp_path, srv)
    run_main(a, pre + edit("UPDATE concesionari SET telefon='0711-A' WHERE nume=?", sync=False), srv["url"], srv["key"])
    run_main(b, pre + edit("UPDATE concesionari SET adresa='Str. Noua B 5' WHERE nume=?", sync=False), srv["url"], srv["key"])
    run_main(a, pre + SYNC, srv["url"], srv["key"])
    rb = run_main(b, pre + SYNC, srv["url"], srv["key"])
    server, conf = srv_row(srv, nume), conflicts(srv, nume)
    kept_both = server["telefon"] == "0711-A" and server["adresa"] == "Str. Noua B 5"
    assert kept_both or conf, f"o modificare a dispărut fără conflict: {server}, {rb['lines']}"


def test_restore_old_backup_with_already_delivered_op_does_not_revert_other_computer(tmp_path, classic_server):
    """Copia conține în coadă o operațiune deja livrată (offline netrimisă la momentul copiei). După restaurare, retrimiterea ei NU are voie să anuleze modificarea lui B."""
    srv = classic_server
    nume, pre, a, b = setup_two(tmp_path, srv)
    run_main(a, pre + edit("UPDATE concesionari SET prenume='Ion-A1' WHERE nume=?", sync=False), srv["url"], srv["key"])   # offline, în coadă
    copy = cb.backup(a, tmp_path / "copii-A")                                       # copia conține operațiunea în așteptare
    assert run_main(a, pre + SYNC, srv["url"], srv["key"])["ok"]                      # livrată (v2)
    assert run_main(b, pre + SYNC + edit("UPDATE concesionari SET telefon='0799-B-DUPA' WHERE nume=?"), srv["url"], srv["key"])["ok"]   # B (v3)
    cb.restore(copy, a, force=True)
    r = run_main(a, pre + SYNC + SHOW, srv["url"], srv["key"])
    server = srv_row(srv, nume)
    assert server["telefon"] == "0799-B-DUPA", f"modificarea lui B a fost anulată de retrimiterea din copie: {server}; {r['lines']}"
    assert server["version"] >= 3


def test_restore_discards_unsent_offline_ops_but_keeps_them_recoverable_in_safety_folder(tmp_path, classic_server):
    """Operațiune offline NETRIMISĂ, făcută după copie: restaurarea o scoate din MAIN fără nicio avertizare. Rămâne doar în folderul de siguranță."""
    srv = classic_server
    nume, pre, a, b = setup_two(tmp_path, srv)
    copy = cb.backup(a, tmp_path / "copii-A")
    run_main(a, pre + edit("UPDATE concesionari SET telefon='0788-NETRIMIS' WHERE nume=?", sync=False), srv["url"], srv["key"])
    pending = run_main(a, pre + SHOW, srv["url"], srv["key"])["outbox"]
    assert any(o["entity"] == "concesionari" and o["state"] == "pending" for o in pending)
    cb.restore(copy, a, force=True)
    after = run_main(a, pre + SHOW, srv["url"], srv["key"])
    assert after["row"]["telefon"] == "0700-ORIG" and not [o for o in after["outbox"] if o["entity"] == "concesionari" and o["state"] == "pending"]
    safety = sorted(a.parent.glob("A.inainte-de-restaurare-*"))
    assert safety, "nu există folder de siguranță"
    old = sqlite3.connect(safety[-1] / "cimitir.db")
    assert old.execute("SELECT telefon FROM concesionari WHERE nume=?", (nume,)).fetchone()[0] == "0788-NETRIMIS"
    old.close()
    assert srv_row(srv, nume)["telefon"] == "0700-ORIG"                              # niciodată trimisă -> pierdută pe server dacă nu se recuperează manual


def test_offline_then_retry_after_failed_attempt_delivers_exactly_once(tmp_path, classic_server):
    srv = classic_server
    nume, pre, a, b = setup_two(tmp_path, srv)
    r = run_main(a, pre + edit("UPDATE concesionari SET telefon='0733-RETRY' WHERE nume=?", sync=False), srv["url"], srv["key"])
    dead = run_main(a, pre + "cloud_sync.save_config('http://127.0.0.1:9', %r, False, False)\n" % srv["key"] + SYNC + SHOW, configure=False)
    assert dead["row"]["telefon"] == "0733-RETRY" and any(o["state"] in ("pending", "retry") for o in dead["outbox"] if o["entity"] == "concesionari"), dead
    assert srv_row(srv, nume)["telefon"] == "0700-ORIG"
    ok = run_main(a, pre + SYNC + SYNC + SHOW, srv["url"], srv["key"])                  # revenire online + a doua rulare (idempotent)
    server = srv_row(srv, nume)
    assert server["telefon"] == "0733-RETRY" and server["version"] == 2, (server, ok)
    assert pg_rows(srv["dsn"], "SELECT count(*) AS n FROM concesionari WHERE nume=%s", (nume,))[0]["n"] == 1


def test_restored_main_with_same_copy_on_two_computers_reports_conflict_not_overwrite(tmp_path, classic_server):
    """Aceeași copie restaurată pe DOUĂ calculatoare care apoi editează același rând (RAPORT_AUDIT BUG-04, ultimul paragraf)."""
    srv = classic_server
    nume, pre, a, b = setup_two(tmp_path, srv)
    copy = cb.backup(a, tmp_path / "copii-A")
    assert run_main(a, pre + SYNC + edit("UPDATE concesionari SET telefon='0711-A-DUPA' WHERE nume=?"), srv["url"], srv["key"])["ok"]
    cb.restore(copy, a, force=True)
    cb.restore(copy, b, force=True)
    run_main(a, pre + edit("UPDATE concesionari SET adresa='A-restaurat' WHERE nume=?"), srv["url"], srv["key"])
    run_main(b, pre + edit("UPDATE concesionari SET adresa='B-restaurat' WHERE nume=?"), srv["url"], srv["key"])
    server, conf = srv_row(srv, nume), conflicts(srv, nume)
    assert server["telefon"] == "0711-A-DUPA", f"modificarea făcută între copie și restaurare a fost suprascrisă: {server}"
    assert conf, f"nicio urmă a modificărilor refuzate: {server}"                      # cele două editări concurente pe aceeași bază veche sunt refuzate și jurnalizate, nu aplicate orbește
    assert server["adresa"] == "Str. Veche 1" or server["adresa"] in ("A-restaurat", "B-restaurat")
