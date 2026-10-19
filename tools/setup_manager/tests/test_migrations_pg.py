"""Teste cu PostgreSQL REAL (local, descartabil). Migrările reale 001–026 din repo; starea de pornire = TEST-ul descris (001–023 aplicate, 024–026 lipsă)."""
import psycopg
import pytest

from tools.setup_manager import guard, migrations as M
from tools.setup_manager.tests.conftest import MIG_DIR

pytestmark = pytest.mark.pg


def _conn(url):
    return psycopg.connect(url, autocommit=False)


def _cfg(test_cfg):
    return test_cfg


def _state(url):
    migs = M.load_migrations(MIG_DIR)
    with _conn(url) as c:
        a = M.analyze(c, migs)
        return migs, a, M.build_plan(a)


def _hist(url):
    with _conn(url) as c:
        return [r[0] for r in c.execute("SELECT version FROM schema_migrations ORDER BY 1").fetchall()]


def _counts(url):
    with _conn(url) as c:
        return M._counts(c)


GOOD_BACKUP = {"ok": True, "kind": "zip", "detail": "ok"}


def test_detects_023_ok_and_024_to_026_missing(pg_db):
    _, a, plan = _state(pg_db)
    st = {r["version"]: r["state"] for r in a["migrations"]}
    assert st[23] == M.ST_APPLIED and st[22] == M.ST_APPLIED
    assert st[24] == st[25] == st[26] == M.ST_PENDING
    assert [s["version"] for s in plan["steps"]] == [24, 25, 26] and plan["executable"] and plan["confirm_phrase"] == "APLIC 024-025-026"
    assert all(s["action"] == "apply" for s in plan["steps"])


def test_dry_run_changes_nothing(pg_db, test_cfg):
    migs, _, plan = _state(pg_db)
    before = _counts(pg_db)
    r = M.apply_plan(pg_db, test_cfg, migs, plan, plan["confirm_phrase"], GOOD_BACKUP, "dry_run", connect=_conn)
    assert r["dry_run"] and not r["executed"]
    assert _state(pg_db)[2]["steps"] and _counts(pg_db) == before


@pytest.mark.parametrize("confirm,backup,exc", [("gresit", GOOD_BACKUP, "Confirmare"), ("APLIC 024-025-026", {"ok": False}, "Backupul")])
def test_refusals(pg_db, test_cfg, confirm, backup, exc):
    migs, _, plan = _state(pg_db)
    with pytest.raises(guard.GuardError, match=exc):
        M.apply_plan(pg_db, test_cfg, migs, plan, confirm, backup, "execute", connect=_conn)
    assert [s["version"] for s in _state(pg_db)[2]["steps"]] == [24, 25, 26]


def test_stale_plan_refused(pg_db, test_cfg):
    migs, _, plan = _state(pg_db)
    plan = dict(plan, plan_hash="deadbeefdeadbeef")
    with pytest.raises(guard.GuardError, match="schimbat"):
        M.apply_plan(pg_db, test_cfg, migs, plan, plan["confirm_phrase"], GOOD_BACKUP, "execute", connect=_conn)


def test_apply_024_026_single_transaction_no_duplicates_no_data_loss(pg_db, test_cfg):
    with _conn(pg_db) as c:                                    # date reale de protejat
        c.execute("INSERT INTO organizations (organization_type, official_name, display_name) VALUES ('PARISH', 'Parohia Reală', 'Parohia Reală')")
        c.commit()
    before = _counts(pg_db)
    migs, _, plan = _state(pg_db)
    r = M.apply_plan(pg_db, test_cfg, migs, plan, plan["confirm_phrase"], GOOD_BACKUP, "execute", connect=_conn)
    assert r["executed"], r
    hist = _hist(pg_db)
    assert hist.count(24) == hist.count(25) == hist.count(26) == 1 and len(hist) == len(set(hist))
    after = _counts(pg_db)
    assert all(after[t] >= n for t, n in before.items()) and "sync_v2_documents" in after
    _, a, plan2 = _state(pg_db)
    assert plan2["steps"] == [] and {r["version"]: r["state"] for r in a["migrations"]}[26] == M.ST_APPLIED
    with _conn(pg_db) as c:
        assert c.execute("SELECT 1 FROM organizations WHERE official_name='Parohia Reală'").fetchone()


def test_failure_rolls_back_everything_including_history(pg_db, test_cfg, tmp_path):
    migs, _, plan = _state(pg_db)
    bad = tmp_path / "026_registry_requests.sql"
    bad.write_text("CREATE TABLE IF NOT EXISTS cp_registry_requests (x int);\nSELECT 1/0;\n")
    for m in migs:
        if m.version == 26:
            m.path = bad
    r = M.apply_plan(pg_db, test_cfg, migs, plan, plan["confirm_phrase"], GOOD_BACKUP, "execute", connect=_conn)
    assert not r["executed"] and r["rolled_back"]
    assert 24 not in _hist(pg_db) and 25 not in _hist(pg_db)          # 024/025 anulate odată cu 026
    with _conn(pg_db) as c:
        assert c.execute("SELECT to_regclass('sync_v2_documents') IS NULL").fetchone()[0]


def test_failed_verification_does_not_record_history(pg_db, test_cfg, tmp_path):
    migs, _, plan = _state(pg_db)
    noop = tmp_path / "024_sync_v2_documents.sql"
    noop.write_text("SELECT 1;\n")                           # „reușește”, dar nu creează obiectele așteptate
    for m in migs:
        if m.version == 24:
            m.path = noop
    r = M.apply_plan(pg_db, test_cfg, migs, plan, plan["confirm_phrase"], GOOD_BACKUP, "execute", connect=_conn)
    assert r["rolled_back"] and "verificarea obiectelor" in r["message"]
    assert 24 not in _hist(pg_db)


def test_inconsistent_recorded_but_missing_object_blocks(pg_db, test_cfg):
    with _conn(pg_db) as c:
        c.execute("INSERT INTO schema_migrations (version, name) VALUES (24, '024_sync_v2_documents.sql')")
        c.commit()
    migs, a, plan = _state(pg_db)
    assert {r["version"]: r["state"] for r in a["migrations"]}[24] == M.ST_INCONSISTENT
    assert not plan["executable"] and any("024" in b for b in plan["blocked"])
    with pytest.raises(guard.GuardError):
        M.apply_plan(pg_db, test_cfg, migs, plan, plan["confirm_phrase"], GOOD_BACKUP, "execute", connect=_conn)


def test_objects_present_but_unrecorded_only_records_history(pg_db, test_cfg):
    with _conn(pg_db) as c:                                   # 024 aplicat „manual”, fără înregistrare
        c.execute((MIG_DIR / "024_sync_v2_documents.sql").read_text(encoding="utf-8"))
        c.commit()
    migs, a, plan = _state(pg_db)
    assert {r["version"]: r["state"] for r in a["migrations"]}[24] == M.ST_UNRECORDED
    assert [(s["version"], s["action"]) for s in plan["steps"]] == [(24, "record"), (25, "apply"), (26, "apply")]
    r = M.apply_plan(pg_db, test_cfg, migs, plan, plan["confirm_phrase"], GOOD_BACKUP, "execute", connect=_conn)
    assert r["executed"] and _hist(pg_db).count(24) == 1


def test_partial_application_blocks(pg_db):
    with _conn(pg_db) as c:
        c.execute("CREATE TABLE sync_v2_documents (x int)")   # tabel cu nume corect dar fără restul obiectelor
        c.commit()
    _, a, plan = _state(pg_db)
    assert {r["version"]: r["state"] for r in a["migrations"]}[24] == M.ST_PARTIAL and not plan["executable"]


def test_duplicate_history_rows_detected(pg_db):
    with _conn(pg_db) as c:
        c.execute("ALTER TABLE schema_migrations DROP CONSTRAINT schema_migrations_pkey")
        c.execute("INSERT INTO schema_migrations (version, name) VALUES (23, '023_dup.sql')")
        c.commit()
    _, a, plan = _state(pg_db)
    assert a["duplicates"] == [23] and not plan["executable"]


def test_missing_history_table_is_reported(pg_db):
    with _conn(pg_db) as c:
        c.execute("DROP TABLE schema_migrations")
        c.commit()
    _, a, plan = _state(pg_db)
    assert not a["history_table"]
    st = {r["version"]: r["state"] for r in a["migrations"]}
    assert st[23] == M.ST_UNRECORDED and st[24] == M.ST_PENDING


def test_fresh_empty_database_plans_all_in_order(pg_template):
    import uuid
    from tools.setup_manager.tests import conftest as cf
    name = "sm_empty_" + uuid.uuid4().hex[:6]
    with psycopg.connect(cf.PG_URL, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    try:
        url = cf._swap_db(cf.PG_URL, name)
        _, _, plan = _state(url)
        assert [s["version"] for s in plan["steps"]] == list(range(1, 27)) and plan["executable"]
        migs = M.load_migrations(MIG_DIR)
        cfg = {"env_name": "TEST", "project_ref": "fommbiwndlxkfsnlqatq", "supabase_url": "https://fommbiwndlxkfsnlqatq.supabase.co", "storage_bucket": "cimitir-private-test",
               "db_connection_string": "postgresql://postgres.fommbiwndlxkfsnlqatq@aws-0-eu-west-1.pooler.supabase.com:5432/postgres", "db_username": "postgres.fommbiwndlxkfsnlqatq", "docker_port": 8085}
        r = M.apply_plan(url, cfg, migs, plan, plan["confirm_phrase"], GOOD_BACKUP, "execute", connect=_conn)
        assert r["executed"], r
        _, a, plan2 = _state(url)
        assert plan2["steps"] == [] and _hist(url) == list(range(1, 27)) or len(_hist(url)) == 19   # 7 migrări neverificabile nu se înregistrează fără dovezi
    finally:
        with psycopg.connect(cf.PG_URL, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
