"""Restaurare vs. jurnalul `field_import_journal`: nu se pierde silențios starea importurilor din teren (Mobile -> Field Inbox -> MAIN).
Doar date fictive, foldere temporare; nicio bază reală, fără rețea/Cloud."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "shared", _ROOT / "desktop"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import cimitir_backup as bk  # noqa: E402
from test_restore_offline_protection import OUTBOX, siblings, tree_digest  # noqa: E402

SCRIPT = _ROOT / "scripts" / "backup_restore.py"
JOURNAL_DDL = ("CREATE TABLE field_import_journal (inbox_item_uid TEXT PRIMARY KEY, cemetery_uid TEXT NOT NULL, item_type TEXT NOT NULL, "
               "local_entity_uid TEXT, import_state TEXT NOT NULL CHECK (import_state IN ('PREPARING','LOCAL_COMMITTED','COMPLETE')), "
               "local_refs TEXT, last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
CEM = "11111111-1111-4111-8111-111111111111"


def uid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def make(folder: Path, imports=(), journal=True, label="v1") -> Path:
    """Registru fictiv: o tabelă `inhumati` (entitatea importată) + jurnalul; fiecare import = entitate + intrare în jurnal, ca în `_commit_local`."""
    folder.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(folder / "cimitir.db")
    conn.execute("CREATE TABLE concesionari (id INTEGER PRIMARY KEY, nume TEXT)")
    conn.execute("INSERT INTO concesionari (nume) VALUES (?)", (label,))
    conn.execute("CREATE TABLE inhumati (id INTEGER PRIMARY KEY, uid TEXT UNIQUE, nume TEXT)")
    conn.execute(OUTBOX)
    if journal:
        conn.execute(JOURNAL_DDL)
    for n, state in imports:
        if state != "PREPARING":
            conn.execute("INSERT INTO inhumati (uid, nume) VALUES (?, ?)", (uid(n), f"Fictiv {n}"))
        conn.execute("INSERT INTO field_import_journal VALUES (?,?,?,?,?,?,?,?,?)",
                     (uid(n), CEM, "DECEASED", uid(n), state, None, "", "2026-01-01T00:00:00", "2026-01-01T00:00:00"))
    conn.commit()
    conn.close()
    return folder


def rows(folder: Path, sql: str):
    c = sqlite3.connect(folder / "cimitir.db")
    try:
        return c.execute(sql).fetchall()
    finally:
        c.close()


def snap_of(tmp: Path, **kw) -> Path:
    return bk.backup(make(tmp / "old", **kw), tmp / "backups")


@pytest.mark.parametrize("state", ["LOCAL_COMMITTED", "COMPLETE"])
def test_restore_of_backup_made_before_import_is_blocked_and_changes_nothing(tmp_path, state):
    snap = snap_of(tmp_path)
    cur = make(tmp_path / "cur", imports=[(1, state)], label="v2")
    before = tree_digest(cur)
    assert bk.field_imports_at_risk(snap, cur) == [uid(1)]
    with pytest.raises(SystemExit) as e:
        bk.restore(snap, cur, force=True)
    assert "importuri din teren" in str(e.value)
    assert tree_digest(cur) == before and siblings(cur) == []


def test_force_alone_does_not_allow_import_loss_nor_does_unsent_discard(tmp_path):
    snap = snap_of(tmp_path)
    cur = make(tmp_path / "cur", imports=[(1, "COMPLETE")], label="v2")
    with pytest.raises(SystemExit):
        bk.restore(snap, cur, force=True, allow_unsent_loss=True)
    assert rows(cur, "SELECT nume FROM concesionari") == [("v2",)]


def test_restore_of_backup_made_after_import_is_allowed_and_keeps_journal_and_entity(tmp_path):
    snap = snap_of(tmp_path, imports=[(1, "COMPLETE")])
    cur = make(tmp_path / "cur", imports=[(1, "COMPLETE"), (2, "COMPLETE")], label="v2")
    assert bk.field_imports_at_risk(snap, cur) == [uid(2)]
    cur_same = make(tmp_path / "cur_same", imports=[(1, "COMPLETE")], label="v2")
    assert bk.field_imports_at_risk(snap, cur_same) == []
    bk.restore(snap, cur_same, force=True)
    assert rows(cur_same, "SELECT inbox_item_uid, import_state FROM field_import_journal") == [(uid(1), "COMPLETE")]
    assert rows(cur_same, "SELECT uid FROM inhumati") == [(uid(1),)]


def test_backup_with_local_committed_and_current_complete_is_not_a_loss(tmp_path):
    """Entitatea există în copie (aceeași tranzacție); reluarea pasului cloud (IMPORTED) este idempotentă."""
    snap = snap_of(tmp_path, imports=[(1, "LOCAL_COMMITTED")])
    cur = make(tmp_path / "cur", imports=[(1, "COMPLETE")], label="v2")
    assert bk.field_imports_at_risk(snap, cur) == []
    bk.restore(snap, cur, force=True)
    assert rows(cur, "SELECT import_state FROM field_import_journal") == [("LOCAL_COMMITTED",)]
    assert rows(cur, "SELECT uid FROM inhumati") == [(uid(1),)]


def test_partial_import_preparing_only_is_not_a_loss(tmp_path):
    snap = snap_of(tmp_path)
    cur = make(tmp_path / "cur", imports=[(1, "PREPARING")], label="v2")
    assert bk.field_imports_at_risk(snap, cur) == []
    bk.restore(snap, cur, force=True)
    assert rows(cur, "SELECT nume FROM concesionari") == [("v1",)]


def test_historic_backup_without_journal_table(tmp_path):
    snap = snap_of(tmp_path, journal=False)
    assert bk.verify(snap) == []
    clean = make(tmp_path / "clean", journal=True, label="v2")
    assert bk.field_imports_at_risk(snap, clean) == []
    dirty = make(tmp_path / "dirty", imports=[(1, "COMPLETE")], label="v2")
    assert bk.field_imports_at_risk(snap, dirty) == [uid(1)]
    with pytest.raises(SystemExit):
        bk.restore(snap, dirty, force=True)


def test_current_without_journal_table_is_not_at_risk(tmp_path):
    snap = snap_of(tmp_path, imports=[(1, "COMPLETE")])
    cur = make(tmp_path / "cur", journal=False, label="v2")
    assert bk.field_imports_at_risk(snap, cur) == []


def test_conflict_between_mixed_items_only_lists_the_missing_ones(tmp_path):
    snap = snap_of(tmp_path, imports=[(1, "COMPLETE"), (2, "PREPARING")])
    cur = make(tmp_path / "cur", imports=[(1, "COMPLETE"), (2, "COMPLETE"), (3, "LOCAL_COMMITTED")], label="v2")
    assert bk.field_imports_at_risk(snap, cur) == [uid(2), uid(3)]


def test_offline_operation_check_still_applies_alongside_journal_check(tmp_path):
    snap = snap_of(tmp_path)
    cur = make(tmp_path / "cur", label="v2")
    c = sqlite3.connect(cur / "cimitir.db")
    c.execute("INSERT INTO cloud_sync_outbox (kind, entity, entity_uid, action, base_version, state, op_id, created_at, attempts, last_error) "
              "VALUES ('entity','concesionari','u1','update',1,'pending','op-1','1',0,'')")
    c.commit()
    c.close()
    with pytest.raises(SystemExit) as e:
        bk.restore(snap, cur, force=True)
    assert "nesincronizate" in str(e.value)


def test_explicit_acceptance_restores_keeps_safety_copy_and_writes_report_without_copying_journal(tmp_path):
    snap = snap_of(tmp_path)
    cur = make(tmp_path / "cur", imports=[(1, "COMPLETE"), (2, "LOCAL_COMMITTED")], label="v2")
    bk.restore(snap, cur, force=True, allow_import_loss=True)
    assert rows(cur, "SELECT nume FROM concesionari") == [("v1",)]
    assert rows(cur, "SELECT uid FROM inhumati") == []
    assert rows(cur, "SELECT COUNT(*) FROM field_import_journal") == [(0,)], "jurnalul vechi nu se copiază peste cel restaurat (ar descrie entități inexistente)"
    safety = siblings(cur)
    assert len(safety) == 1
    assert rows(tmp_path / safety[0], "SELECT inbox_item_uid FROM field_import_journal ORDER BY 1") == [(uid(1),), (uid(2),)]
    assert rows(tmp_path / safety[0], "SELECT uid FROM inhumati ORDER BY 1") == [(uid(1),), (uid(2),)]
    report = json.loads((cur / bk.IMPORT_REPORT).read_text(encoding="utf-8"))
    assert report["lost_item_uids"] == [uid(1), uid(2)] and report["safety_folder"] == safety[0]


def test_no_report_when_nothing_is_lost(tmp_path):
    snap = snap_of(tmp_path)
    cur = make(tmp_path / "cur", label="v2")
    bk.restore(snap, cur, force=True)
    assert not (cur / bk.IMPORT_REPORT).exists()


def test_schedule_restore_blocked_leaves_no_marker(tmp_path):
    snap = snap_of(tmp_path)
    cur = make(tmp_path / "cur", imports=[(1, "COMPLETE")], label="v2")
    with pytest.raises(SystemExit):
        bk.schedule_restore(snap, cur)
    assert not bk.pending_marker(cur).exists()


def test_import_done_after_scheduling_cancels_restore_at_startup(tmp_path):
    snap = snap_of(tmp_path)
    cur = make(tmp_path / "cur", label="v2")
    bk.schedule_restore(snap, cur)
    c = sqlite3.connect(cur / "cimitir.db")
    c.execute("INSERT INTO inhumati (uid, nume) VALUES (?, 'Fictiv')", (uid(9),))
    c.execute("INSERT INTO field_import_journal VALUES (?,?,?,?,?,?,?,?,?)", (uid(9), CEM, "DECEASED", uid(9), "COMPLETE", None, "", "t", "t"))
    c.commit()
    c.close()
    before = tree_digest(cur)
    msg = bk.apply_pending_restore(cur)
    assert msg and "importuri din teren" in msg
    assert tree_digest(cur) == before and siblings(cur) == []
    assert not bk.pending_marker(cur).exists()
    assert bk.apply_pending_restore(cur) is None


def test_unreadable_current_database_blocks_instead_of_guessing(tmp_path):
    snap = snap_of(tmp_path)
    cur = tmp_path / "cur"
    cur.mkdir()
    (cur / "cimitir.db").write_bytes(b"not a database" * 100)
    with pytest.raises(ValueError):
        bk.field_imports_at_risk(snap, cur)


def test_restore_into_fresh_temp_directory_is_always_possible(tmp_path):
    snap = snap_of(tmp_path, imports=[(1, "COMPLETE")])
    target = tmp_path / "temp-restore"
    bk.restore(snap, target)
    assert rows(target, "SELECT import_state FROM field_import_journal") == [("COMPLETE",)]


def test_cli_discard_imports_requires_force(tmp_path):
    snap = snap_of(tmp_path)
    cur = make(tmp_path / "cur", imports=[(1, "COMPLETE")], label="v2")
    r = subprocess.run([sys.executable, str(SCRIPT), "restore", str(snap), str(cur), "--discard-imports"], capture_output=True, text=True)
    assert r.returncode != 0 and "--force" in r.stderr
    r = subprocess.run([sys.executable, str(SCRIPT), "restore", str(snap), str(cur), "--force"], capture_output=True, text=True)
    assert r.returncode != 0 and "importuri din teren" in (r.stderr + r.stdout)
    assert rows(cur, "SELECT nume FROM concesionari") == [("v2",)]
    r = subprocess.run([sys.executable, str(SCRIPT), "restore", str(snap), str(cur), "--force", "--discard-imports"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert rows(cur, "SELECT nume FROM concesionari") == [("v1",)]


def test_journal_ddl_matches_application_schema():
    """Schema de test = schema reală (field_import.ensure_tables): altfel testele ar valida un jurnal inventat."""
    src = (_ROOT / "desktop" / "field_import.py").read_text(encoding="utf-8")
    assert 'JOURNAL = "field_import_journal"' in src
    for col in ("inbox_item_uid TEXT PRIMARY KEY", "import_state TEXT NOT NULL CHECK", "local_entity_uid TEXT"):
        assert col in src and col in JOURNAL_DDL
