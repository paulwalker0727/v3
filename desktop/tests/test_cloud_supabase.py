"""Centrul de configurare Cloud — logica Supabase (ETAPA 1+2): verificare read-only, respingerea proiectelor greșite, instalare cu backup + confirmare,
rollback, secrete absente din loguri/fișiere/profil. Testele cu PostgreSQL real rulează doar dacă SM_TEST_PG_URL indică un server LOCAL de test."""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import uuid
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="cimitir_test_cloud_supabase_")
os.environ["CIMITIR_DATA_DIR"] = _TMP
os.environ["CIMITIR_LOG_DIR"] = os.path.join(_TMP, "logs")
os.environ.setdefault("CIMITIR_DB_ENGINE", "sqlite")
os.environ.setdefault("CIMITIR_MODE", "local")
os.environ.setdefault("DATABASE_URL", "")

DESKTOP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DESKTOP))
sys.path.insert(0, str(DESKTOP.parent / "shared"))

import pytest  # noqa: E402

import cimitir_registry as reg  # noqa: E402
import cimitir_secretstore as ss  # noqa: E402
import cloud_profiles as cp  # noqa: E402
import cloud_simple as simple  # noqa: E402
import cloud_supabase as sb  # noqa: E402
import database as db  # noqa: E402

PG_URL = os.environ.get("SM_TEST_PG_URL", "")
REF = "abcdefghijklmnopqrst"
PW = "S3cret!pw-Zq9"
HOST = "aws-0-eu-central-1.pooler.supabase.com"
USER = f"postgres.{REF}"


def inp(**kw):
    base = dict(project_ref=REF, db_host=HOST, db_port=5432, db_user=USER, storage_bucket="cimitir-fisiere")
    base.update(kw)
    return sb.SupabaseInput(**base)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "cimitir.db"))
    db.init_db()
    st = ss.DpapiSecretStore(tmp_path / "secrets", lambda b: b"F:" + bytes(reversed(b)), lambda b: bytes(reversed(b[2:])))
    monkeypatch.setattr(cp, "get_store", lambda: st)
    monkeypatch.setattr(reg, "current_binding", lambda: None)
    return st


# ------------------------------------------------------------------ PostgreSQL real, „Supabase” simulat (scheme auth/storage + storage.buckets)
@pytest.fixture()
def sup(request):
    if not PG_URL:
        pytest.skip("SM_TEST_PG_URL nesetat (teste PostgreSQL real omise)")
    import psycopg
    name = "sb_t_" + uuid.uuid4().hex[:8]
    base = PG_URL.rpartition("/")[0]
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    local = f"{base}/{name}"
    with psycopg.connect(local, autocommit=True) as c:
        c.execute("CREATE SCHEMA auth; CREATE SCHEMA storage; CREATE TABLE storage.buckets (id text PRIMARY KEY, name text, public boolean DEFAULT false)")

    def connect(**kw):
        if kw["password"] != PW:
            raise psycopg.OperationalError(f"password authentication failed for user \"{kw['user']}\" (password {kw['password']})")
        return psycopg.connect(local, connect_timeout=5)

    class Env:
        url = local
        connect_kw = staticmethod(connect)
        connect_url = staticmethod(lambda url: psycopg.connect(local, autocommit=False))

        @staticmethod
        def conn():
            return psycopg.connect(local, autocommit=True)

    yield Env
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


# ------------------------------------------------------------------ date de intrare
def test_parse_connection_string_separates_password():
    fields, pw = sb.parse_connection_string(f"postgresql://{USER}:S3cret%21pw@{HOST}:5432/postgres")
    assert pw == "S3cret!pw" and fields["project_ref"] == REF and fields["db_host"] == HOST and fields["db_user"] == USER
    assert "S3cret" not in repr(fields)
    with pytest.raises(sb.CenterError):
        sb.parse_connection_string("https://example.com")


def test_session_expires_and_never_prints_password():
    now = [0.0]
    s = sb.AdminSession(ttl=60, clock=lambda: now[0])
    s.authorize(PW)
    assert s.active and s.password == PW and PW not in repr(s) and PW not in str(s)
    now[0] = 61
    assert not s.active
    with pytest.raises(sb.CenterError):
        s.password  # noqa: B018
    s.authorize(PW)
    s.clear()
    assert not s.active


# ------------------------------------------------------------------ respingerea proiectelor greșite (fără rețea)
@pytest.mark.parametrize("kw,needle", [
    (dict(supabase_url="https://zzzzzzzzzzzzzzzzzzzz.supabase.co"), "URL"),
    (dict(db_user="postgres.zzzzzzzzzzzzzzzzzzzz"), "Utilizatorul"),
    (dict(db_port=6543), "6543"),
    (dict(project_ref="short"), "invalid"),
    (dict(project_ref="txuxkjgftopznvmooexq", db_user="postgres.txuxkjgftopznvmooexq"), "PROD"),
])
def test_wrong_project_rejected_before_any_connection(kw, needle):
    called = []
    res = sb.verify_project(inp(**kw), PW, connect=lambda **k: called.append(k))
    assert not res.ok and not called
    assert needle in res.text() and PW not in res.text()


def test_missing_password_rejected():
    res = sb.verify_project(inp(), "", connect=lambda **k: pytest.fail("nu trebuie să se conecteze"))
    assert not res.ok and "parola" in res.text()


def test_auth_failure_is_scrubbed():
    import psycopg

    def bad(**kw):
        raise psycopg.OperationalError(f"password authentication failed (password {kw['password']})")
    res = sb.verify_project(inp(), PW, connect=bad)
    assert not res.ok and "autentificare" in res.text() and PW not in res.text()


@pytest.mark.parametrize("ref", ["txuxkjgftopznvmooexq", "nhhczdmcocbobptogvsw"])
def test_developer_projects_cannot_be_written_from_main(ref):
    bad = inp(project_ref=ref, db_user=f"postgres.{ref}")
    res = sb.VerifyResult(reachable=True, plan={"steps": [{"name": "001", "action": "apply", "why": "x", "version": 1}], "blocked": [], "executable": True,
                                                 "confirm_phrase": "APLIC 001"})
    with pytest.raises(sb.CenterError):
        sb.apply_schema(bad, PW, res, "APLIC 001", "/x", {"ok": True})


# ------------------------------------------------------------------ PostgreSQL real
def test_non_supabase_database_rejected(sup):
    with sup.conn() as c:
        c.execute("DROP SCHEMA storage CASCADE")
    res = sb.verify_project(inp(), PW, connect=sup.connect_kw)
    assert not res.ok and "respins" in res.text()


def test_verify_empty_project_lists_all_missing_migrations_read_only(sup):
    res = sb.verify_project(inp(), PW, connect=sup.connect_kw)
    assert res.ok and res.empty_project is True and len(res.pending) == 26 and res.bucket_state == "missing"
    assert res.plan["executable"] and PW not in res.text()
    with sup.conn() as c:                                         # verificarea nu a modificat nimic
        assert c.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM storage.buckets").fetchone()[0] == 0


def _install(sup, res, backup_path, phrase=None, db_backup=None, mig_dir=None):
    pv = sb.preview(res, inp())
    return sb.apply_schema(inp(), PW, res, phrase if phrase is not None else pv["confirm_phrase"], str(backup_path),
                           db_backup or sb.db_backup_info(res), connect=sup.connect_url, mig_dir=mig_dir, bucket_connect=sup.connect_kw)


@pytest.fixture()
def local_backup(store, tmp_path, monkeypatch):
    import backup_window
    import cimitir_config
    monkeypatch.setattr(cimitir_config, "working_dir", lambda: Path(os.environ["CIMITIR_DATA_DIR"]))
    (Path(os.environ["CIMITIR_DATA_DIR"])).mkdir(exist_ok=True)
    import shutil
    shutil.copy(db.DB_PATH, Path(os.environ["CIMITIR_DATA_DIR"]) / "cimitir.db")
    monkeypatch.setattr(backup_window, "backup_folder", lambda: tmp_path / "backups")
    return sb.local_backup()


def test_install_refused_without_valid_local_backup_or_exact_confirmation(sup, local_backup):
    res = sb.verify_project(inp(), PW, connect=sup.connect_kw)
    with pytest.raises(sb.CenterError, match="Backupul local"):
        _install(sup, res, "")
    with pytest.raises(sb.CenterError, match="Backupul local"):
        _install(sup, res, local_backup / "inexistent")
    with pytest.raises(sb.CenterError, match="Confirmare incorectă"):
        _install(sup, res, local_backup, phrase="da")
    with pytest.raises(sb.CenterError, match="bazei Cloud"):
        _install(sup, res, local_backup, db_backup={"ok": False})
    with sup.conn() as c:                                         # nimic nu s-a executat
        assert c.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'").fetchone()[0] == 0


def test_install_full_schema_and_private_bucket_then_idempotent(sup, local_backup):
    res = sb.verify_project(inp(), PW, connect=sup.connect_kw)
    out = _install(sup, res, local_backup)
    assert out["schema"]["executed"] and "bucket privat" in out["bucket"]
    res2 = sb.verify_project(inp(), PW, connect=sup.connect_kw)
    assert res2.ok and res2.schema_current and res2.bucket_state == "ok" and res2.pending == []
    with pytest.raises(sb.CenterError, match="nu poate fi executat"):
        sb.apply_schema(inp(), PW, res2, "", str(local_backup), {"ok": True}, connect=sup.connect_url)


def test_nonempty_project_requires_verified_db_backup(sup, local_backup, tmp_path):
    res = sb.verify_project(inp(), PW, connect=sup.connect_kw)
    _install(sup, res, local_backup)
    with sup.conn() as c:
        c.execute("DELETE FROM schema_migrations WHERE version=26")
        c.execute("DROP TABLE IF EXISTS registry_requests CASCADE")
    res3 = sb.verify_project(inp(), PW, connect=sup.connect_kw)
    assert res3.ok and res3.pending == ["026_registry_requests.sql"]
    res3.empty_project = False
    assert not sb.db_backup_info(res3, str(tmp_path / "lipsa.sql"))["ok"]
    good = tmp_path / "dump.sql"
    good.write_text("-- PostgreSQL database dump\nCREATE TABLE t (id int);\n" * 20)
    assert sb.db_backup_info(res3, str(good))["ok"]


def test_failed_migration_rolls_back_everything(sup, local_backup, tmp_path):
    d = tmp_path / "mig"
    d.mkdir()
    (d / "001_ok.sql").write_text("CREATE TABLE parohie_ok (id int);\n")
    (d / "002_bad.sql").write_text("CREATE TABLE parohie_bad (id int);\nSELECT 1/0;\n")
    res = sb.verify_project(inp(), PW, connect=sup.connect_kw, mig_dir=d)
    assert res.ok and res.pending == ["001_ok.sql", "002_bad.sql"]
    out = _install(sup, res, local_backup, mig_dir=d)
    assert out["schema"]["executed"] is False and out["schema"]["rolled_back"] and out["bucket"] is None
    with sup.conn() as c:
        assert c.execute("SELECT to_regclass('public.parohie_ok')").fetchone()[0] is None


def test_public_bucket_is_rejected(sup):
    with sup.conn() as c:
        c.execute("INSERT INTO storage.buckets VALUES ('cimitir-fisiere','cimitir-fisiere', true)")
    res = sb.verify_project(inp(), PW, connect=sup.connect_kw)
    assert not res.ok and res.bucket_state == "public"


def test_no_secret_in_logs_profile_or_local_db(sup, store, local_backup, caplog):
    caplog.set_level(logging.DEBUG)
    res = sb.verify_project(inp(), PW, connect=sup.connect_kw)
    _install(sup, res, local_backup)
    prof = sb.save_to_profile(inp(), "TEST", store=store)
    assert prof.is_active and not (prof.db_password_ref or prof.supabase_secret_ref or prof.sync_key_ref)
    assert (prof.project_ref, prof.db_host, prof.storage_bucket) == (REF, HOST, "cimitir-fisiere")
    blob = res.text() + caplog.text + repr(prof) + Path(db.DB_PATH).read_bytes().decode("latin-1")
    for p in Path(os.environ["CIMITIR_LOG_DIR"]).glob("*"):
        blob += p.read_text(errors="ignore")
    assert PW not in blob and "S3cret" not in blob


# ------------------------------------------------------------------ profil: TEST / PROD separate, reconfigurare
def test_profile_prod_requires_confirmation_and_is_separate_from_test(store):
    t = sb.save_to_profile(inp(), "TEST", store=store)
    assert t.environment == "TEST" and t.is_active
    with pytest.raises(cp.ConfirmationRequired):
        sb.save_to_profile(inp(), "PROD", store=store)
    assert [p.environment for p in cp.list_profiles()] == ["TEST"]
    other = inp(project_ref="bcdefghijklmnopqrstu", db_user="postgres.bcdefghijklmnopqrstu")
    assert "alt proiect Supabase" in sb.reconfigure_warning(other)
    assert sb.reconfigure_warning(inp()) == ""
    assert sb.input_from_profile().project_ref == REF


def test_preview_requires_valid_verification():
    with pytest.raises(sb.CenterError):
        sb.preview(sb.VerifyResult(), inp())


def test_saving_test_never_mutates_existing_prod_profile(store):
    prod = cp.create_profile({"name": "Parohia PROD", "environment": "PRODUCTION", "api_base_url": "https://cloud.parohie.ro"}, {"db_password": "pg-prod-pw-123456"}, store)
    cp.activate_profile(prod.id, confirm_production=True)
    t = sb.save_to_profile(inp(), "TEST", store=store)
    assert t.environment == "TEST" and t.id != prod.id and t.is_active and not t.db_password_ref
    after = cp.get_profile(prod.id)
    assert (after.name, after.environment, after.api_base_url, after.db_password_ref) == ("Parohia PROD", "PRODUCTION", "https://cloud.parohie.ro", prod.db_password_ref)
    assert not after.is_active and after.project_ref == ""
    t2 = sb.save_to_profile(inp(storage_bucket="alt-bucket"), "TEST", store=store)
    assert t2.id == t.id and len(cp.list_profiles()) == 2
