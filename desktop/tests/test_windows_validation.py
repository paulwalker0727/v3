"""Instalarea în copie TEST (install_test_copy) și validatorul Windows (validate_windows): logica de siguranță, verificată pe un arbore sintetic și pe PostgreSQL local."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="cimitir_test_winval_")
os.environ["CIMITIR_DATA_DIR"] = os.path.join(_TMP, "env", "data")
os.environ["CIMITIR_LOG_DIR"] = os.path.join(_TMP, "env", "logs")
os.environ["CIMITIR_ENV_ROOT"] = os.path.join(_TMP, "env")
os.environ.setdefault("CIMITIR_DB_ENGINE", "sqlite")
os.environ.setdefault("CIMITIR_MODE", "local")
os.environ.setdefault("DATABASE_URL", "")
Path(os.environ["CIMITIR_DATA_DIR"]).mkdir(parents=True, exist_ok=True)

DESKTOP = Path(__file__).resolve().parent.parent
REPO = DESKTOP.parent
WV = REPO / "deploy" / "windows_validation"
sys.path.insert(0, str(DESKTOP))
sys.path.insert(0, str(REPO / "shared"))

import pytest  # noqa: E402


def _load(name):
    spec = importlib.util.spec_from_file_location(name, WV / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


itc = _load("install_test_copy")
vw = _load("validate_windows")


def h(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture()
def world(tmp_path, monkeypatch):
    src = tmp_path / "v3-main"
    (src / "desktop" / "license").mkdir(parents=True)
    (src / "desktop" / "main.py").write_text("print('main')\n")
    (src / "desktop" / "a.py").write_text("A=1\n")
    (src / "desktop" / "license" / "validator.py").write_text("L=1\n")
    (src / ".env").write_text("DATABASE_URL=postgresql://u:SECRETPW@x/y\n")
    (src / ".git").mkdir()
    (src / ".git" / "HEAD").write_text("ref")
    live = tmp_path / "live_data"
    live.mkdir()
    import sqlite3
    cx = sqlite3.connect(live / "cimitir.db")
    cx.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    cx.execute("INSERT INTO settings VALUES ('cimitir_nume', 'Parohia X')")
    cx.commit()
    cx.close()
    appdata = tmp_path / "AppData"
    (appdata / "CimitirDesktop").mkdir(parents=True)
    (appdata / "CimitirDesktop" / "license.key").write_text('{"payload":{},"semnatura":"x"}')
    monkeypatch.setenv("APPDATA", str(appdata))
    bundle = tmp_path / "bundle"
    (bundle / "overlay" / "desktop").mkdir(parents=True)
    (bundle / "overlay" / "desktop" / "a.py").write_text("A=2\n")
    (bundle / "overlay" / "desktop" / "new.py").write_text("N=1\n")
    manifest = {"base_commit": "60f292c", "files": [
        {"path": "desktop/a.py", "status": "M", "base_sha256": h(b"A=1\n"), "base_sha256_lf": h(b"A=1\n"), "new_sha256": h(b"A=2\n")},
        {"path": "desktop/new.py", "status": "A", "new_sha256": h(b"N=1\n")}]}
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    return dict(src=src, live=live, appdata=appdata, bundle=bundle, tmp=tmp_path)


def test_install_never_touches_source_and_isolates_data_and_license(world):
    t = world["tmp"] / "copy-test"
    before = itc.fingerprint(world["src"])
    live_before = (world["live"] / "cimitir.db").read_bytes()
    assert b"Parohia X" in live_before
    lic_before = (world["appdata"] / "CimitirDesktop" / "license.key").read_bytes()
    rep = itc.install(world["src"], t, world["live"], world["bundle"], log=lambda *_: None)
    assert rep["result"] == "PASS"
    assert itc.fingerprint(world["src"]) == before and (world["live"] / "cimitir.db").read_bytes() == live_before
    assert (t / "desktop" / "a.py").read_text() == "A=2\n" and (t / "desktop" / "new.py").exists()
    assert (world["src"] / "desktop" / "a.py").read_text() == "A=1\n"
    assert not (t / ".env").exists() and not (t / ".git").exists()
    env = t / "test_env"
    assert (env / "data" / "cimitir.db").read_bytes() == live_before
    assert (env / "appdata" / "CimitirDesktop" / "license.key").read_bytes() == lic_before
    assert rep["license_sha256"] == h(lic_before)
    bat = (t / "RUN_MAIN_TEST.bat").read_text()
    assert "CIMITIR_DATA_DIR=%CIMITIR_ENV_ROOT%\\data" in bat and "APPDATA=%CIMITIR_ENV_ROOT%\\appdata" in bat and "CIMITIR_IGNORE_PROJECT_ENV=1" in bat and "CIMITIR_LOG_DIR=%CIMITIR_ENV_ROOT%\\logs" in bat
    assert "8084" not in bat and "docker" not in bat.lower()
    assert (t / "manifest.json").exists()


def test_drift_stops_before_creating_anything(world):
    (world["src"] / "desktop" / "a.py").write_text("A=999  # modificat local\n")
    t = world["tmp"] / "copy-test"
    with pytest.raises(itc.InstallError, match="diferă de commitul de bază"):
        itc.install(world["src"], t, world["live"], world["bundle"], log=lambda *_: None)
    assert not t.exists()
    rep = itc.install(world["src"], t, world["live"], world["bundle"], allow_drift=True, log=lambda *_: None)
    assert rep["skipped"] == ["desktop/a.py"] and (t / "desktop" / "a.py").read_text() == "A=999  # modificat local\n"


def test_crlf_checkout_is_still_exact(world):
    (world["src"] / "desktop" / "a.py").write_bytes(b"A=1\r\n")
    assert itc.check_exact_version(world["src"], json.loads((world["bundle"] / "manifest.json").read_text()))["drift"] == []


@pytest.mark.parametrize("where", ["inside_source", "same", "nonempty", "inside_live"])
def test_dangerous_targets_refused(world, where):
    t = {"inside_source": world["src"] / "copie", "same": world["src"], "nonempty": world["tmp"] / "ocupat",
         "inside_live": world["live"] / "copie"}[where]
    if where == "nonempty":
        t.mkdir()
        (t / "x.txt").write_text("x")
    with pytest.raises(itc.InstallError):
        itc.install(world["src"], t, world["live"], world["bundle"], log=lambda *_: None)
    assert (world["src"] / "desktop" / "a.py").read_text() == "A=1\n"


def test_non_main_source_refused(world):
    with pytest.raises(itc.InstallError, match="nu pare o instalare MAIN"):
        itc.install(world["tmp"], world["tmp"] / "x", None, world["bundle"], log=lambda *_: None)


# ------------------------------------------------------------------ validatorul (fără rețea reală; Supabase simulat = PostgreSQL local)
def test_validator_refuses_without_isolation_env(monkeypatch, capsys):
    monkeypatch.delenv("CIMITIR_ENV_ROOT")
    assert vw.main(["--no-db", "--host", "x"]) == 2
    assert "izolarea" in capsys.readouterr().out


def test_validator_blocks_protected_port_8084(monkeypatch):
    rep = vw.Report()
    vw.check_backend(rep, {"backend": "http://127.0.0.1:8084"})
    assert rep.rows[0]["status"] == vw.FAIL and len(rep.rows) == 1


def test_report_scrubs_password():
    rep = vw.Report(["Pa55w0rd-secret"])
    rep.add("X", vw.FAIL, "t", "eroare Pa55w0rd-secret in text")
    assert "Pa55w0rd" not in rep.markdown() and "***" in rep.markdown()


PG_URL = os.environ.get("SM_TEST_PG_URL", "")
REF = "abcdefghijklmnopqrst"
PW = "S3cret!pw-Zq9"


class FakeHttp:
    @staticmethod
    def get(url, **kw):
        class R:
            status_code = 200
            json = staticmethod(lambda: {"ok": True})
        return R


class FakeAdmin:
    def __init__(self, url):
        pass

    def status(self):
        return {"initialized": False, "bootstrap_open": False}

    def close(self):
        pass


def test_validator_end_to_end_against_local_postgres_with_26_migrations_applied(monkeypatch, tmp_path):
    if not PG_URL:
        pytest.skip("SM_TEST_PG_URL nesetat")
    import psycopg
    import cimitir_auth
    import cloud_supabase as sb
    import database as db
    from license import validator
    name = "wv_" + os.urandom(4).hex()
    base = PG_URL.rpartition("/")[0]
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    local = f"{base}/{name}"
    try:
        with psycopg.connect(local, autocommit=True) as c:
            c.execute("CREATE SCHEMA auth; CREATE SCHEMA storage; CREATE TABLE storage.buckets (id text PRIMARY KEY, name text, public boolean DEFAULT false)")

        def connect(**kw):
            if kw["password"] != PW:
                raise psycopg.OperationalError("password authentication failed")
            return psycopg.connect(local, connect_timeout=5)
        inp = sb.SupabaseInput(REF, "", "aws-0-eu-central-1.pooler.supabase.com", 5432, f"postgres.{REF}", "postgres", "cimitir-private-test")
        res = sb.verify_project(inp, PW, connect=connect)
        bk = sb.local_backup  # copie locală reală nu e necesară aici: aplicăm direct prin apply_plan
        mig, _, _ = sb.setup_manager()
        migs = mig.load_migrations(sb.migrations_dir())
        out = mig.apply_plan(sb._url(inp, PW), sb.to_cfg(inp), migs, res.plan, res.plan["confirm_phrase"], {"ok": True}, "execute",  # noqa: SLF001
                             connect=lambda url: psycopg.connect(local, autocommit=False))
        assert out["executed"]
        with psycopg.connect(local, autocommit=True) as c:
            c.execute("INSERT INTO storage.buckets VALUES ('cimitir-private-test','cimitir-private-test', false)")
        del bk

        env_root = tmp_path / "env"
        data = env_root / "data"
        data.mkdir(parents=True)
        monkeypatch.setenv("CIMITIR_ENV_ROOT", str(env_root))
        monkeypatch.setenv("CIMITIR_DATA_DIR", str(data))
        monkeypatch.setattr(db, "DB_PATH", str(data / "cimitir.db"))
        db.init_db()
        cimitir_auth.init_auth()

        class OK:
            ok, motiv, client, expira = True, "OK", "Parohia Test", None
        monkeypatch.setattr(validator, "verifica_licenta_curenta", lambda: OK)
        cfg = dict(ref=REF, host="aws-0-eu-central-1.pooler.supabase.com", port=5432, user=None, bucket="cimitir-private-test",
                   backend="http://127.0.0.1:8085", expected_migrations=26, wrong_password_test=True)
        rep = vw.run(cfg, env_root, PW, connect=connect, http=FakeHttp, admin_factory=FakeAdmin)
        st = {r["id"]: r["status"] for r in rep.rows}
        assert st["SB-04"] == "PASS", rep.markdown()
        for cid in ("ENV-01", "LIC-01", "LIC-03", "AUTH-01", "AUTH-02", "AUTH-03", "PERM-01", "UI-01", "UI-02", "PERM-02", "CFG-01", "SB-01", "SB-02", "SB-03",
                    "SB-05", "SB-06", "SB-07", "SB-08", "SB-09", "BE-00", "BE-01", "SEC-01", "ENV-02", "ENV-03"):
            assert st[cid] == "PASS", (cid, rep.markdown())
        assert st["BE-02"] == "MANUAL" and st["BE-03"] == "MANUAL" and not rep.failed
        assert PW not in rep.markdown()
        with psycopg.connect(local, autocommit=True) as c:                       # verificarea nu a adăugat nimic
            assert c.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 26
    finally:
        with psycopg.connect(PG_URL, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_install_neutralizes_copied_settings_and_never_touches_originals(world):
    import sqlite3
    real_backup = world["tmp"] / "REAL_BACKUPS"
    for d in (world["live"], world["live"] / "registries" / "r1"):
        d.mkdir(parents=True, exist_ok=True)
    dbs = [world["live"] / "cimitir.db", world["live"] / "registries" / "r1" / "database.sqlite"]
    for p in dbs:
        c = sqlite3.connect(p)
        c.execute("DROP TABLE IF EXISTS settings")
        c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        c.executemany("INSERT INTO settings VALUES (?,?)", [("backup_dir", str(real_backup)), ("cloud_sync_auto", "1"), ("cloud_media_sync", "1"), ("cloud_url", "https://x")])
        c.execute("CREATE TABLE cloud_connection_profiles (id TEXT, name TEXT, environment TEXT, api_base_url TEXT, is_active INTEGER)")
        c.executemany("INSERT INTO cloud_connection_profiles VALUES (?,?,?,?,?)", [("1", "PROD real", "PRODUCTION", "https://cloud.parohie.ro", 1), ("2", "T", "TEST", "http://127.0.0.1:8085", 0)])
        c.commit()
        c.close()
    before = [p.read_bytes() for p in dbs]
    t = world["tmp"] / "copy-test"
    t.mkdir()
    rep = itc.install(world["src"], t, world["live"], world["bundle"], log=lambda *_: None)
    assert [p.read_bytes() for p in dbs] == before
    for rel in ("cimitir.db", "registries/r1/database.sqlite"):
        c = sqlite3.connect(t / "test_env" / "data" / rel)
        st = dict(c.execute("SELECT key, value FROM settings").fetchall())
        assert st["backup_dir"] == str(t / "test_env" / "backups") and st["cloud_sync_auto"] == "0" and st["cloud_media_sync"] == "0" and st["cloud_url"] == "https://x"
        assert c.execute("SELECT count(*) FROM cloud_connection_profiles WHERE is_active=1").fetchone()[0] == 0
        c.close()
    assert rep["sanitized"]["profiles_deactivated"] and not real_backup.exists()
    bat = (t / "RUN_MAIN_TEST.bat").read_bytes()
    assert b"\r\r\n" not in bat and bat.count(b"\r\n") == bat.count(b"\n")


def test_validator_persistence_uses_dedicated_test_profile_and_keeps_deactivated_prod(monkeypatch, tmp_path):
    import cloud_profiles as cp
    import cimitir_secretstore as ss
    import database as db
    env_root = tmp_path / "env"
    (env_root / "data").mkdir(parents=True)
    monkeypatch.setenv("CIMITIR_ENV_ROOT", str(env_root))
    monkeypatch.setenv("CIMITIR_DATA_DIR", str(env_root / "data"))
    monkeypatch.setattr(db, "DB_PATH", str(env_root / "data" / "cimitir.db"))
    db.init_db()
    st = ss.DpapiSecretStore(tmp_path / "sec", lambda b: b"F:" + bytes(reversed(b)), lambda b: bytes(reversed(b[2:])))
    monkeypatch.setattr(cp, "get_store", lambda: st)
    prod = cp.create_profile({"name": "PROD real", "environment": "PRODUCTION", "api_base_url": "https://cloud.parohie.ro"}, {"db_password": "pg-prod-pw-123456"}, st)
    assert not prod.is_active
    rep = vw.Report()
    vw.check_persistence(rep, dict(ref=REF, host="aws-0-eu-central-1.pooler.supabase.com", port=5432, user=None, bucket="cimitir-private-test",
                                   backend="http://127.0.0.1:8085", expected_migrations=26), env_root)
    assert [r["status"] for r in rep.rows if r["id"] == "CFG-01"] == ["PASS"]
    after = cp.get_profile(prod.id)
    assert (after.name, after.environment, after.api_base_url, after.db_password_ref) == ("PROD real", "PRODUCTION", "https://cloud.parohie.ro", prod.db_password_ref)
    assert cp.active_profile().environment == "TEST"


def test_validator_skips_persistence_when_copy_not_isolated(monkeypatch, tmp_path):
    import sqlite3
    env_root = tmp_path / "env"
    (env_root / "data").mkdir(parents=True)
    c = sqlite3.connect(env_root / "data" / "cimitir.db")
    c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("INSERT INTO settings VALUES ('cloud_publish_burials','1')")
    c.commit()
    c.close()
    monkeypatch.setenv("CIMITIR_DATA_DIR", str(env_root / "data"))
    rep = vw.Report()
    assert vw.check_copy_settings(rep, env_root) is False
    assert {r["id"]: r["status"] for r in rep.rows}["ENV-03"] == "FAIL"
