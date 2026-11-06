"""Backup / restaurare: protecția operațiunilor offline nesincronizate, --force, destinații, migrare explicită.
Doar date fictive, în foldere temporare; nu se atinge nicio bază reală și nu există acces la rețea/Cloud."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "shared", _ROOT / "desktop"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import cimitir_backup as bk  # noqa: E402

SCRIPT = _ROOT / "scripts" / "backup_restore.py"
MIGRATE = _ROOT / "scripts" / "migrate_backups_out_of_git.py"
OUTBOX = ("CREATE TABLE cloud_sync_outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, entity TEXT, entity_uid TEXT, action TEXT, "
          "base_version INTEGER, state TEXT, op_id TEXT, created_at TEXT, attempts INTEGER DEFAULT 0, last_error TEXT)")


def make_data(folder: Path, ops=(), legacy_outbox=False, label="v1") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(folder / "cimitir.db")
    conn.execute("CREATE TABLE concesionari (id INTEGER PRIMARY KEY, nume TEXT)")
    conn.execute("INSERT INTO concesionari (nume) VALUES (?)", (label,))
    if legacy_outbox:
        conn.execute("CREATE TABLE cloud_sync_outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, tracking_code TEXT, status TEXT)")
        conn.execute("INSERT INTO cloud_sync_outbox (tracking_code, status) VALUES ('T1', 'x')")
    else:
        conn.execute(OUTBOX)
        for op_id, state in ops:
            conn.execute("INSERT INTO cloud_sync_outbox (kind, entity, entity_uid, action, base_version, state, op_id, created_at, attempts, last_error) "
                         "VALUES ('entity','concesionari','u1','update',1,?,?,'1',2,'eroare')", (state, op_id))
    conn.commit()
    conn.close()
    (folder / "media").mkdir(exist_ok=True)
    (folder / "media" / "f.txt").write_text("fictiv", encoding="utf-8")
    return folder


def tree_digest(folder: Path) -> dict:
    out = {}
    for p in sorted(Path(folder).rglob("*")):
        if p.is_file():
            out[str(p.relative_to(folder))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def names(db_file: Path):
    c = sqlite3.connect(db_file)
    try:
        return [r[0] for r in c.execute("SELECT nume FROM concesionari ORDER BY id")]
    finally:
        c.close()


def siblings(data: Path):
    return sorted(p.name for p in data.parent.iterdir() if p.name.startswith(data.name + ".inainte-de-restaurare"))


@pytest.fixture()
def pair(tmp_path):
    """backup al lui v1 (fără operațiuni); folderul curent v2 se creează în teste."""
    old = make_data(tmp_path / "old", label="v1")
    snap = bk.backup(old, tmp_path / "backups")
    return tmp_path, snap


@pytest.mark.parametrize("state", ["pending", "retry", "conflict", "error"])
def test_restore_refused_when_open_offline_operation_would_disappear(pair, state):
    tmp, snap = pair
    cur = make_data(tmp / "cur", ops=[("op-1", state)], label="v2")
    before = tree_digest(cur)
    with pytest.raises(SystemExit) as e:
        bk.restore(snap, cur, force=True)
    assert "nesincronizate" in str(e.value)
    assert tree_digest(cur) == before and siblings(cur) == []


def test_force_alone_never_allows_loss_of_offline_operations(pair):
    tmp, snap = pair
    cur = make_data(tmp / "cur", ops=[("op-1", "pending")], label="v2")
    with pytest.raises(SystemExit):
        bk.restore(snap, cur, force=True, allow_unsent_loss=False)
    assert names(cur / "cimitir.db") == ["v2"]


def test_closed_operations_do_not_block_restore(pair):
    tmp, snap = pair
    cur = make_data(tmp / "cur", ops=[("op-1", "done"), ("op-2", "sent")], label="v2")
    bk.restore(snap, cur, force=True)
    assert names(cur / "cimitir.db") == ["v1"]


def test_operation_already_identical_in_backup_is_not_at_risk(tmp_path):
    old = make_data(tmp_path / "old", ops=[("op-1", "pending")], label="v1")
    snap = bk.backup(old, tmp_path / "backups")
    cur = make_data(tmp_path / "cur", ops=[("op-1", "pending")], label="v2")
    assert bk.unsent_operations_at_risk(snap, cur) == 0
    bk.restore(snap, cur, force=True)
    assert names(cur / "cimitir.db") == ["v1"]


def test_operation_changed_after_backup_is_at_risk(tmp_path):
    old = make_data(tmp_path / "old", ops=[("op-1", "pending")], label="v1")
    snap = bk.backup(old, tmp_path / "backups")
    cur = make_data(tmp_path / "cur", ops=[("op-1", "conflict")], label="v2")
    assert bk.unsent_operations_at_risk(snap, cur) == 1


def test_explicit_discard_keeps_full_safety_copy_with_the_operations(pair):
    tmp, snap = pair
    cur = make_data(tmp / "cur", ops=[("op-1", "pending")], label="v2")
    bk.restore(snap, cur, force=True, allow_unsent_loss=True)
    assert names(cur / "cimitir.db") == ["v1"]
    safety = siblings(cur)
    assert len(safety) == 1
    c = sqlite3.connect(tmp / safety[0] / "cimitir.db")
    try:
        assert c.execute("SELECT op_id FROM cloud_sync_outbox").fetchall() == [("op-1",)]
    finally:
        c.close()


def test_legacy_outbox_without_state_column_counts_every_row_as_at_risk(pair):
    tmp, snap = pair
    cur = make_data(tmp / "cur", legacy_outbox=True, label="v2")
    assert bk.unsent_operations_at_risk(snap, cur) == 1
    with pytest.raises(SystemExit):
        bk.restore(snap, cur, force=True)


def test_unreadable_current_database_blocks_instead_of_guessing(pair):
    tmp, snap = pair
    cur = tmp / "cur"
    cur.mkdir()
    (cur / "cimitir.db").write_bytes(b"not a database" * 100)
    before = tree_digest(cur)
    with pytest.raises(ValueError):
        bk.restore(snap, cur, force=True)
    assert tree_digest(cur) == before and siblings(cur) == []


def test_schedule_restore_is_blocked_and_leaves_no_marker(pair):
    tmp, snap = pair
    cur = make_data(tmp / "cur", ops=[("op-1", "error")], label="v2")
    with pytest.raises(SystemExit):
        bk.schedule_restore(snap, cur)
    assert not bk.pending_marker(cur).exists()


def test_operations_created_after_scheduling_block_the_restore_at_startup_and_cancel_it(pair):
    tmp, snap = pair
    cur = make_data(tmp / "cur", label="v2")
    bk.schedule_restore(snap, cur)
    conn = sqlite3.connect(cur / "cimitir.db")
    conn.execute("INSERT INTO cloud_sync_outbox (kind, entity, entity_uid, action, base_version, state, op_id, created_at, attempts, last_error) "
                 "VALUES ('entity','concesionari','u9','update',1,'pending','op-new','1',0,'')")
    conn.commit()
    conn.close()
    before = tree_digest(cur)
    msg = bk.apply_pending_restore(cur)
    assert msg and "nesincronizate" in msg
    assert tree_digest(cur) == before and siblings(cur) == []
    assert not bk.pending_marker(cur).exists(), "restaurarea blocată nu trebuie să rămână programată (s-ar aplica prin surprindere mai târziu)"
    assert bk.apply_pending_restore(cur) is None


def test_restore_into_fresh_temp_directory_is_valid_and_resets_cloud_client(pair):
    tmp, snap = pair
    target = tmp / "restaurat"
    bk.restore(snap, target)
    assert names(target / "cimitir.db") == ["v1"]
    assert (target / "media" / "f.txt").read_text(encoding="utf-8") == "fictiv"
    assert bk.verify(snap) == []
    c = sqlite3.connect(target / "cimitir.db")
    try:
        assert c.execute("SELECT value FROM settings WHERE key='cloud_client_id'").fetchone()[0] == ""
        assert c.execute("SELECT value FROM settings WHERE key='restored_at'").fetchone()[0]
    finally:
        c.close()


def test_restore_never_overwrites_non_empty_folder_without_force(pair):
    tmp, snap = pair
    cur = make_data(tmp / "cur", label="v2")
    with pytest.raises(SystemExit):
        bk.restore(snap, cur)
    assert names(cur / "cimitir.db") == ["v2"]


def test_manifest_with_path_traversal_and_tampered_file_are_reported(pair):
    tmp, snap = pair
    manifest = json.loads((snap / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"]["../evil.txt"] = "0" * 64
    (snap / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    problems = bk.verify(snap)
    assert any("cale nesigură" in p for p in problems)
    with pytest.raises(SystemExit):
        bk.restore(snap, tmp / "dest")
    assert not (tmp / "dest").exists()


# ---------------------------------------------------------------- CLI
def run_cli(*args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_")}
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60)


def test_cli_force_without_discard_unsent_refuses_and_changes_nothing(pair):
    tmp, snap = pair
    cur = make_data(tmp / "cur", ops=[("op-1", "pending")], label="v2")
    before = tree_digest(cur)
    p = run_cli("restore", str(snap), str(cur), "--force")
    assert p.returncode != 0 and "nesincronizate" in (p.stderr + p.stdout)
    assert tree_digest(cur) == before


def test_cli_discard_unsent_requires_force(pair):
    tmp, snap = pair
    cur = make_data(tmp / "cur", ops=[("op-1", "pending")], label="v2")
    p = run_cli("restore", str(snap), str(cur), "--discard-unsent")
    assert p.returncode == 2 and "--force" in p.stderr
    assert names(cur / "cimitir.db") == ["v2"]


def test_cli_force_and_discard_unsent_restores_and_keeps_safety_copy(pair):
    tmp, snap = pair
    cur = make_data(tmp / "cur", ops=[("op-1", "pending")], label="v2")
    p = run_cli("restore", str(snap), str(cur), "--force", "--discard-unsent")
    assert p.returncode == 0, p.stderr
    assert names(cur / "cimitir.db") == ["v1"] and len(siblings(cur)) == 1


def test_cli_backup_refuses_destination_inside_git_checkout():
    p = run_cli("backup", str(_ROOT / "shared"), str(_ROOT / "nu-scrie-aici"))
    assert p.returncode != 0
    assert not (_ROOT / "nu-scrie-aici").exists()


# ---------------------------------------------------------------- destinații
def test_unsafe_destinations_inside_repo_are_detected_and_external_is_allowed(tmp_path):
    assert bk.is_unsafe_backup_destination(_ROOT / "backups") is True
    assert bk.is_unsafe_backup_destination(_ROOT) is True
    assert bk.is_unsafe_backup_destination(tmp_path / "externe") is False


def test_configured_destination_in_git_is_relocated_with_warning_not_used(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "xdg"))
    data = make_data(tmp_path / "data")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        dest = bk.backup_destination(data, lambda k: str(_ROOT / "backups") if k == "backup_dir" else None)
    assert not bk.is_unsafe_backup_destination(dest) and "Cimitir-backups" in str(dest)
    assert any("depozit Git" in str(w.message) for w in caught)


def test_unavailable_destination_is_logged_as_failure_without_exception(tmp_path):
    data = make_data(tmp_path / "data")
    blocker = tmp_path / "ocupat"
    blocker.write_text("sunt un fișier", encoding="utf-8")
    res = bk.run_backup_job(data, lambda k: str(blocker / "sub") if k == "backup_dir" else None, force=True, trigger="windows-task")
    assert res["result"] == "failed" and res.get("error")
    log = bk.read_log(data)
    assert log and log[0]["result"] == "failed"


def test_missing_database_is_a_logged_failure_not_a_crash(tmp_path):
    data = tmp_path / "gol"
    data.mkdir()
    res = bk.run_backup_job(data, lambda k: str(tmp_path / "b") if k == "backup_dir" else None, force=True, trigger="windows-task")
    assert res["result"] == "failed"


# ---------------------------------------------------------------- migrare explicită
def make_legacy(tmp_path):
    data = make_data(tmp_path / "data")
    legacy = bk.legacy_backup_dir(data)
    good = bk.backup(data, legacy)
    nomanifest = legacy / "cimitir-20200101-000000"
    nomanifest.mkdir()
    (nomanifest / "cimitir.db").write_bytes((good / "cimitir.db").read_bytes())
    return data, legacy, good, nomanifest


def test_migration_copies_verifies_and_never_touches_originals(tmp_path):
    data, legacy, good, nomanifest = make_legacy(tmp_path)
    before = tree_digest(legacy)
    dest = tmp_path / "extern"
    res = bk.migrate_legacy_backups(data, lambda k: str(dest) if k == "backup_dir" else None)
    assert res["copied"] == 1 and any("manifest lipsă" in e for e in res["errors"])
    assert tree_digest(legacy) == before
    assert bk.verify(dest / good.name) == []
    again = bk.migrate_legacy_backups(data, lambda k: str(dest) if k == "backup_dir" else None)
    assert again["copied"] == 0 and again["skipped"] == 1


def test_migration_adopt_missing_marks_adopted_and_original_stays_unchanged(tmp_path):
    data, legacy, good, nomanifest = make_legacy(tmp_path)
    before = tree_digest(legacy)
    dest = tmp_path / "extern"
    res = bk.migrate_legacy_backups(data, lambda k: str(dest) if k == "backup_dir" else None, adopt_missing=True)
    assert res["adopted"] == 1 and not res["errors"]
    adopted = json.loads((dest / nomanifest.name / "manifest.json").read_text(encoding="utf-8"))
    assert adopted["adopted"] is True and "adoption_warning" in adopted
    assert tree_digest(legacy) == before and not (nomanifest / "manifest.json").exists()


def test_migration_refuses_destination_inside_source_or_git(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "xdg"))
    data, legacy, *_ = make_legacy(tmp_path)
    with pytest.raises(ValueError):
        bk.migrate_legacy_backups(data, lambda k: str(legacy / "sub") if k == "backup_dir" else None)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = bk.migrate_legacy_backups(data, lambda k: str(_ROOT / "copii") if k == "backup_dir" else None)
    assert not bk.is_unsafe_backup_destination(Path(res["destination"]))      # destinația din Git este relocată, nu folosită
    assert not (_ROOT / "copii").exists()


def test_old_backups_remain_listed_for_consultation_after_destination_change(tmp_path):
    data, legacy, good, _ = make_legacy(tmp_path)
    rows = bk.available_backup_rows(data, lambda k: str(tmp_path / "nou") if k == "backup_dir" else None)
    assert [r["name"] for r in rows if r["legacy"]] == [good.name]


def test_migration_script_is_dry_run_without_apply(tmp_path):
    data, legacy, *_ = make_legacy(tmp_path)
    dest = tmp_path / "extern"
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_")}
    p = subprocess.run([sys.executable, str(MIGRATE), "--data-dir", str(data), "--dest", str(dest)], capture_output=True, text=True, env=env, timeout=60)
    assert p.returncode == 0 and "DRY RUN" in p.stdout
    assert not dest.exists()
