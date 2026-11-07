"""Regresii P3 (găsite de auditul de testare): scrieri directe în DB accesibile din CemeteryUI care ocoleau _allowed(). SQLite sintetic în tmp_path."""
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
def test_clear_guides_cere_dreptul_delete(env):
    env.login(["cfg", "edit"])
    db.clear_guides()
    assert _count("SELECT COUNT(*) FROM guides") == 1
    env.login(["cfg", "delete"])
    db.clear_guides()
    assert _count("SELECT COUNT(*) FROM guides") == 0


# Toolbar "Import locuri" -> reports_export.import_locuri: direct INSERT/UPDATE, no guard at all
def test_import_locuri_respecta_add_si_edit(env):
    env.login(["cfg"])  # view-only (e.g. edit revoked in open session; button stays active)
    f = env.tmp / "imp.csv"
    with open(f, "w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=["cod_loc", "sector"])
        w.writeheader()
        w.writerow({"cod_loc": "IMP-1", "sector": "X"})
        w.writerow({"cod_loc": "A-1", "sector": "HACK"})
    import reports_export as rex
    updated, created, errors = rex.import_locuri(str(f))
    assert db.load_loc_by_cod("IMP-1") is None and (db.load_loc_by_cod("A-1") or {}).get("sector") != "HACK"
    assert len(errors) == 2
    env.login(["cfg", "add"])
    rex.import_locuri(str(f))
    assert db.load_loc_by_cod("IMP-1") is not None and (db.load_loc_by_cod("A-1") or {}).get("sector") != "HACK"
    env.login(["cfg", "edit"])
    rex.import_locuri(str(f))
    assert (db.load_loc_by_cod("A-1") or {}).get("sector") == "HACK"


# AdvancedEditor.update_guide: direct UPDATE guides, no guard
def test_update_guide_cere_dreptul_edit(env):
    env.login(["cfg"])
    import map_editor_advanced as mea
    mea.AdvancedEditor.update_guide(None, env.gid, [[1, 1], [2, 2]])
    c = db.get_connection()
    try:
        pts = json.loads(c.execute("SELECT points FROM guides WHERE id=?", (env.gid,)).fetchone()[0])
    finally:
        c.close()
    assert pts == [[0, 0], [10, 0]]
    env.login(["cfg", "edit"])
    mea.AdvancedEditor.update_guide(None, env.gid, [[1, 1], [2, 2]])
    c = db.get_connection()
    try:
        assert json.loads(c.execute("SELECT points FROM guides WHERE id=?", (env.gid,)).fetchone()[0]) == [[1, 1], [2, 2]]
    finally:
        c.close()


# Row insert flow: shift_rows_order runs (unguarded) BEFORE add_row is refused -> row order corrupted
def test_inserarea_refuzata_nu_renumeroteaza_randurile(env):
    env.login(["cfg", "edit"])
    db.shift_rows_order("S1", 1, 1)
    rid = db.add_row("S1", "Rând nou", 1, [(0, 9), (5, 9)], 10, 10)
    orders = sorted(r["order_index"] for r in db.list_rows("S1"))
    assert rid is None and orders == [1, 2]


# save_loc_metadata on missing loc INSERTs a new plot with only `edit`
def test_metadatele_unui_loc_inexistent_cer_add(env):
    env.login(["cfg", "edit"])
    assert db.save_loc_metadata("META-NEW", {"sector": "Z"}) is False
    assert db.load_loc_by_cod("META-NEW") is None
    env.login(["cfg", "add"])
    assert db.save_loc_metadata("META-NEW", {"sector": "Z"})


# save_sector_to_db: new sector created with `edit` only (INSERT OR REPLACE)
def test_sector_nou_cere_add_si_cel_existent_edit(env):
    env.login(["cfg", "edit"])
    assert db.save_sector_to_db("SEC-NEW", [(0, 0), (1, 1)], "#000") is False
    env.login(["cfg", "add"])
    assert db.save_sector_to_db("SEC-NEW", [(0, 0), (1, 1)], "#000")
    assert db.save_sector_to_db("SEC-NEW", [(0, 0), (2, 2)], "#000") is False
    env.login(["cfg", "edit"])
    assert db.save_sector_to_db("SEC-NEW", [(0, 0), (2, 2)], "#000")


# set_cerere_status has no DB guard (UI gated by access_session.require('approve') only)
def test_set_cerere_status_cere_approve(env):
    env.login(["cfg"])
    assert db.set_cerere_status(1, "respins") is False
    assert "Drepturi insuficiente" in db.last_refusal()


# sanity: guarded functions refuse correctly
def test_guarded_functions_refuse(env):
    env.login(["cfg", "edit"])
    assert db.add_plata("A-1", 1, "2024-01-01") is None
    assert db.add_guide("h", [(0, 0), (1, 0)]) is None
    assert not db.delete_plot("A-1")
