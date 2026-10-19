import time
import zipfile

import pytest

from tools.setup_manager import guard, migrations as M
from tools.setup_manager.tests.conftest import MIG_DIR


def test_extract_objects_ignores_comments_do_blocks_and_function_bodies():
    sql = """
    -- CREATE TABLE ghost (id int);
    CREATE TABLE IF NOT EXISTS public.alpha (id int);
    CREATE OR REPLACE FUNCTION fn_one() RETURNS trigger LANGUAGE plpgsql AS $f$ BEGIN CREATE TABLE inner_t (x int); END $f$;
    DO $$ BEGIN EXECUTE 'CREATE TABLE in_do (x int)'; CREATE TABLE do_t (x int); END $$;
    ALTER TABLE alpha ENABLE ROW LEVEL SECURITY;
    CREATE POLICY pol_a ON alpha FOR SELECT USING (true);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_a ON alpha (id);
    ALTER TABLE alpha ADD COLUMN IF NOT EXISTS extra int;
    """
    o = M.extract_objects(sql)
    assert o["table"] == [("alpha",)] and o["function"] == [("fn_one",)] and o["rls"] == [("alpha",)]
    assert o["policy"] == [("pol_a", "alpha")] and o["index"] == [("idx_a",)] and o["column"] == [("alpha", "extra")]


def test_real_migrations_load_in_order():
    migs = M.load_migrations(MIG_DIR)
    assert [m.version for m in migs] == list(range(1, 27))
    assert migs[23].name.startswith("024_") and any(("sync_v2_documents",) == t for t in migs[23].objects["table"])


def test_superseded_policy_not_expected_from_021():
    migs = {m.version: m for m in M.load_migrations(MIG_DIR)}
    assert migs[21].checkable() == 0          # mobile_cp_test_insert este eliminată dinamic de 022


def _rows(states):
    return {"history_table": True, "duplicates": [], "migrations": [
        {"version": v, "name": f"{v:03d}_x.sql", "sha256": f"h{v}", "recorded": st in (M.ST_APPLIED, M.ST_INCONSISTENT), "state": st,
         "checked": 3, "present": 3, "missing": ["t"] if st in (M.ST_PENDING, M.ST_INCONSISTENT, M.ST_PARTIAL) else [], "missing_count": 1 if st in (M.ST_PENDING, M.ST_INCONSISTENT, M.ST_PARTIAL) else 0}
        for v, st in states.items()]}


def test_plan_orders_and_blocks_on_inconsistency():
    ok = M.build_plan(_rows({22: M.ST_APPLIED, 23: M.ST_APPLIED, 24: M.ST_PENDING, 25: M.ST_PENDING, 26: M.ST_PENDING}))
    assert [s["version"] for s in ok["steps"]] == [24, 25, 26] and ok["confirm_phrase"] == "APLIC 024-025-026" and ok["executable"]
    bad = M.build_plan(_rows({22: M.ST_APPLIED, 23: M.ST_INCONSISTENT, 24: M.ST_PENDING}))
    assert not bad["executable"] and bad["blocked"]
    dep = M.build_plan(_rows({22: M.ST_APPLIED, 23: M.ST_PENDING, 24: M.ST_APPLIED, 25: M.ST_PENDING}))
    assert [s["version"] for s in dep["steps"]] == [23, 25]


def test_plan_nothing_to_do():
    p = M.build_plan(_rows({22: M.ST_APPLIED, 23: M.ST_APPLIED}))
    assert p["steps"] == [] and not p["executable"] and p["confirm_phrase"] == ""


def test_duplicate_history_blocks():
    a = _rows({23: M.ST_APPLIED, 24: M.ST_PENDING})
    a["duplicates"] = [23]
    assert not M.build_plan(a)["executable"]


def test_backup_verification(tmp_path):
    assert not M.verify_backup(str(tmp_path / "missing.zip"))["ok"]
    z = tmp_path / "b.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("dump.sql", "-- PostgreSQL database dump\n" + "CREATE TABLE a(id int);\n" * 20)
    r = M.verify_backup(str(z))
    assert r["ok"] and r["kind"] == "zip" and not r["stale"]
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"PK\x03\x04" + b"junk" * 100)
    assert not M.verify_backup(str(bad))["ok"]
    sql = tmp_path / "d.sql"
    sql.write_text("-- PostgreSQL database dump\n" + "CREATE TABLE a(id int);\n" * 20)
    assert M.verify_backup(str(sql))["kind"] == "sql"
    dump = tmp_path / "d.dump"
    dump.write_bytes(b"PGDMP" + b"\x00" * 400)
    assert M.verify_backup(str(dump))["kind"] == "pg_dump-custom"
    tiny = tmp_path / "t.sql"
    tiny.write_text("x")
    assert not M.verify_backup(str(tiny))["ok"]
    old = time.time() - 30 * 86400
    import os
    os.utime(sql, (old, old))
    assert M.verify_backup(str(sql))["stale"]
    assert not M.verify_backup(str(tmp_path))["ok"]


def test_apply_refused_for_prod_before_any_connection(test_cfg):
    cfg = dict(test_cfg, supabase_url="https://txuxkjgftopznvmooexq.supabase.co")
    called = []
    with pytest.raises(guard.GuardError, match="PROD"):
        M.apply_plan("postgresql://x", cfg, [], {"steps": [], "executable": True, "plan_hash": "x", "confirm_phrase": "y", "blocked": []}, "y", {"ok": True}, "execute",
                     connect=lambda u: called.append(u))
    assert not called
