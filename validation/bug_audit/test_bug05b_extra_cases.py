"""BUG-05b — cazuri-limită: retenția verifică COMPLET (SHA-256 + integrity_check) cele mai noi `keep_min` copii."""
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import cimitir_backup as cb


def _data(tmp_path) -> Path:
    data = tmp_path / "Cimitir"
    data.mkdir()
    conn = sqlite3.connect(data / "cimitir.db")
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()
    return data


def _aged(data, dest, days):
    copy = cb.backup(data, dest)
    target = dest / f"cimitir-{datetime.now() - timedelta(days=days):%Y%m%d-%H%M%S}"
    copy.rename(target)
    return target


def _damage(copy):
    with open(copy / "cimitir.db", "r+b") as fh:
        fh.seek(200)
        fh.write(b"\xff" * 3000)


def _names(dest):
    return {r["name"] for r in cb.list_backups(dest)}


def test_damaged_in_newest_window_deletes_nothing(tmp_path):
    data, dest = _data(tmp_path), tmp_path / "copii"
    copies = [_aged(data, dest, d) for d in (60, 50, 40, 30)]
    _damage(copies[2])
    _damage(copies[3])                                   # cele mai noi 3 conțin 2 copii deteriorate
    before = _names(dest)
    assert cb.prune(dest, keep_days=14, keep_min=3) == 0
    assert _names(dest) == before


def test_expired_copies_beyond_min_valid_are_still_pruned(tmp_path):
    data, dest = _data(tmp_path), tmp_path / "copii"
    old = [_aged(data, dest, d) for d in (90, 80)]
    recent = [_aged(data, dest, d) for d in (3, 2, 1)]
    assert cb.prune(dest, keep_days=14, keep_min=3) == 2
    assert all(not p.exists() for p in old) and all(p.exists() for p in recent)


def test_damaged_copies_do_not_count_and_block_deletion(tmp_path):
    data, dest = _data(tmp_path), tmp_path / "copii"
    good = [_aged(data, dest, d) for d in (90, 80, 70)]
    bad = [_aged(data, dest, d) for d in (30, 20)]
    for b in bad:
        _damage(b)
    assert cb.prune(dest, keep_days=14, keep_min=3) == 0       # fereastra celor mai noi 3 conține copii deteriorate: nimic nu se șterge
    assert all(p.exists() for p in good + bad)


def test_protected_copy_is_never_pruned(tmp_path):
    data, dest = _data(tmp_path), tmp_path / "copii"
    old = _aged(data, dest, 100)
    cb.set_protected(old)
    for d in (3, 2, 1):
        _aged(data, dest, d)
    cb.prune(dest, keep_days=14, keep_min=3)
    assert old.exists()


def test_keep_min_one_keeps_newest_valid_only(tmp_path):
    data, dest = _data(tmp_path), tmp_path / "copii"
    old = _aged(data, dest, 40)
    new = _aged(data, dest, 20)
    assert cb.prune(dest, keep_days=14, keep_min=1) == 1
    assert not old.exists() and new.exists()


def test_empty_or_missing_destination_is_safe(tmp_path):
    assert cb.prune(tmp_path / "nu-exista", keep_days=14) == 0


def test_damage_beyond_the_newest_window_does_not_block_pruning(tmp_path):
    data, dest = _data(tmp_path), tmp_path / "copii"
    old_bad = _aged(data, dest, 90)
    _damage(old_bad)
    recent = [_aged(data, dest, d) for d in (3, 2, 1)]
    assert cb.prune(dest, keep_days=14, keep_min=3) == 1
    assert not old_bad.exists() and all(p.exists() for p in recent)


def test_quick_check_is_not_enough_sha_mismatch_blocks_pruning(tmp_path):
    data, dest = _data(tmp_path), tmp_path / "copii"
    old = _aged(data, dest, 90)
    recent = [_aged(data, dest, d) for d in (3, 2, 1)]
    raw = bytearray((recent[0] / "cimitir.db").read_bytes())
    raw[-1] ^= 0xFF                                                          # fișierul există, SHA diferă (integrity_check rămâne ok)
    (recent[0] / "cimitir.db").write_bytes(bytes(raw))
    assert cb.prune(dest, keep_days=14, keep_min=3) == 0
    assert old.exists()
