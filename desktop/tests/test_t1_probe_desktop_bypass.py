"""T1 probe (testing agent): direct DB writes reachable from CemeteryUI that bypass _allowed(). Synthetic SQLite in tmp_path.
Tests named *_gap assert the CURRENT (unsafe) behaviour -> they pass while the gap exists; reported to main agent."""
import csv
import json
import sys
from pathlib import Path

import pytest

DESKTOP = Path(__file__).resolve().parents[1]
SHARED = DESKTOP.parent / "shared"
for p in (str(DESKTOP), str(SHARED)):
    if p not in sys.path:
        sys.path.insert(0, p)

import database as db  # noqa: E402
import cimitir_auth as auth  # noqa: E402
import access_session  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_audit_actor", "")
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "cimitir.db"))
    db.init_db()
    auth.init_auth()
    db.set_access_guard(None)
    db.save_plot_to_db("A-1", [(0, 0), (1, 0), (1, 1)], "#fff")
    gid = db.add_guide("h", [(0, 0), (10, 0)])
    db.add_row("S1", "Rând 1", 1, [(0, 0), (5, 0)], 10, 10)
    db.add_row("S1", "Rând 2", 2, [(0, 5), (5, 5)], 10, 10)

    def login(rights_list, role="operator"):
        uid = auth.create_user(f"t{len(auth.list_users())}", "Parola-test-123", role, rights=rights_list)
        access_session.set_user(auth.get_session_user(uid))
        db.set_access_guard(access_session.can)
        return uid
    yield type("E", (), {"login": staticmethod(login), "gid": gid, "tmp": tmp_path})
    db.set_access_guard(None)
    access_session.set_user(None)


def _count(sql):
    c = db.get_connection()
    try:
        return c.execute(sql).fetchone()[0]
    finally:
        c.close()


# Toolbar "Sterge ghidaje" -> db.clear_guides(): no _allowed("delete")
def test_clear_guides_without_delete_gap(env):
    env.login(["cfg", "edit"])
    assert db.delete_guide(env.gid) in (None, False) or _count("SELECT COUNT(*) FROM guides") == 1
    before = _count("SELECT COUNT(*) FROM guides")
    db.clear_guides()
    after = _count("SELECT COUNT(*) FROM guides")
    print("guides before/after clear (edit-only user):", before, after)
    assert after == 0  # GAP


# Toolbar "Import locuri" -> reports_export.import_locuri: direct INSERT/UPDATE, no guard at all
def test_import_locuri_bypasses_guard_gap(env):
    env.login(["cfg"])  # view-only (e.g. edit revoked in open session; button stays active)
    f = env.tmp / "imp.csv"
    with open(f, "w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=["cod_loc", "sector"])
        w.writeheader()
        w.writerow({"cod_loc": "IMP-1", "sector": "X"})
        w.writerow({"cod_loc": "A-1", "sector": "HACK"})
    import reports_export as rex
    updated, created, errors = rex.import_locuri(str(f))
    print("import by view-only user: updated", updated, "created", created, errors)
    assert db.load_loc_by_cod("IMP-1") is not None  # GAP: created without add
    assert (db.load_loc_by_cod("A-1") or {}).get("sector") == "HACK"  # GAP: updated without edit


# AdvancedEditor.update_guide: direct UPDATE guides, no guard
def test_update_guide_bypasses_guard_gap(env):
    env.login(["cfg"])
    import map_editor_advanced as mea
    mea.AdvancedEditor.update_guide(None, env.gid, [[1, 1], [2, 2]])
    c = db.get_connection()
    try:
        pts = json.loads(c.execute("SELECT points FROM guides WHERE id=?", (env.gid,)).fetchone()[0])
    finally:
        c.close()
    print("guide points after view-only update:", pts)
    assert pts == [[1, 1], [2, 2]]  # GAP


# Row insert flow: shift_rows_order runs (unguarded) BEFORE add_row is refused -> row order corrupted
def test_shift_rows_before_refused_add_row_gap(env):
    env.login(["cfg", "edit"])
    db.shift_rows_order("S1", 1, 1)
    rid = db.add_row("S1", "Rând nou", 1, [(0, 9), (5, 9)], 10, 10)
    orders = sorted(r["order_index"] for r in db.list_rows("S1"))
    print("add_row result", rid, "orders now", orders)
    assert rid is None and orders == [2, 3]  # GAP: renumbered with a hole although insert was refused


# save_loc_metadata on missing loc INSERTs a new plot with only `edit`
def test_save_loc_metadata_creates_loc_with_edit_only_gap(env):
    env.login(["cfg", "edit"])
    assert db.save_loc_metadata("META-NEW", {"sector": "Z"})
    assert db.load_loc_by_cod("META-NEW") is not None  # GAP: creation should need add


# save_sector_to_db: new sector created with `edit` only (INSERT OR REPLACE)
def test_new_sector_with_edit_only(env):
    env.login(["cfg", "edit"])
    ok = db.save_sector_to_db("SEC-NEW", [(0, 0), (1, 1)], "#000")
    print("new sector with edit only:", ok)


# set_cerere_status has no DB guard (UI gated by access_session.require('approve') only)
def test_set_cerere_status_unguarded(env):
    env.login(["cfg"])
    import inspect
    src = inspect.getsource(db.set_cerere_status) if hasattr(db, "set_cerere_status") else ""
    print("set_cerere_status has _allowed:", "_allowed" in src)


# sanity: guarded functions refuse correctly
def test_guarded_functions_refuse(env):
    env.login(["cfg", "edit"])
    assert db.add_plata("A-1", 1, "2024-01-01") is None
    assert db.add_guide("h", [(0, 0), (1, 0)]) is None
    assert not db.delete_plot("A-1")
