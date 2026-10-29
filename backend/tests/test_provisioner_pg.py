"""Teste de integrare ale provizionării (Faza A): PostgreSQL LOCAL real (clustere izolate), Storage emulat, procese backend reale, HTTPS real cu CA locală.
NU testează Supabase real, Docker real, Caddy/Let's Encrypt real sau Windows. Rulare: cd backend && python -m pytest -n 0 tests/test_provisioner_pg.py
Sar automat dacă PostgreSQL server nu este instalat. Fără date reale (registrul 3213 — banovita nu este folosit)."""
import json
import os
import shutil
import sqlite3
import ssl
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))
if not list(Path("/usr/lib/postgresql").glob("*/bin/initdb")):
    pytest.skip("PostgreSQL server local indisponibil", allow_module_level=True)

import cimitir_instance_grant as ig  # noqa: E402
import cimitir_prov_demo as demo  # noqa: E402
import cimitir_prov_pg as pgops  # noqa: E402
import cimitir_prov_runtime as rt  # noqa: E402
import cimitir_provisioner as pv  # noqa: E402
from cimitir_prov_store import ProvisionError, Vault  # noqa: E402

ADMIN_PW = "Parola-Sigura-123!x"


def make_source(dest: Path, extra_migrations=None, break_app=False) -> Path:
    for rel in ("shared", "backend/templates", "backend/static", "backend/cimitir/pg_migrations", "backend/cimitir/pg_rollbacks"):
        shutil.copytree(ROOT / rel, dest / rel, ignore=shutil.ignore_patterns("__pycache__"))
    (dest / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "backend" / "attachments.py", dest / "backend" / "attachments.py")
    shutil.copy(ROOT / "scripts" / "cloud_admin_recovery.py", dest / "scripts" / "cloud_admin_recovery.py")
    for name, sql in (extra_migrations or {}).items():
        (dest / "backend/cimitir/pg_migrations" / name).write_text(sql)
    if break_app:
        f = dest / "shared" / "cimitir_cloud_app.py"
        f.write_text("raise SystemExit(9)\n" + f.read_text())
    return dest


@pytest.fixture(scope="module")
def env():
    base = Path(tempfile.mkdtemp(prefix="provtest-", dir="/tmp"))
    os.chmod(base, 0o755)
    a, b = demo.LocalSupabaseProject(base, "alfa").start(), demo.LocalSupabaseProject(base, "beta").start()
    prov = pv.Provisioner(base / "home", "TEST", Vault(base / "home" / "vault", Vault.master_from_env({Vault_ENV: Vault.new_master()})))
    prov.start_timeout = 8.0
    owner = prov.reg.authenticate(prov.reg.create_operator("owner1", "owner", "TEST"))
    oper = prov.reg.authenticate(prov.reg.create_operator("oper1", "operator", "TEST"))
    prov.import_release(owner, str(ROOT), "v1")
    yield {"base": base, "a": a, "b": b, "prov": prov, "owner": owner, "oper": oper, "keys": {}}
    for i in prov.reg.list_instances():
        prov._stop_procs(i)
    for p in (a, b):
        p.destroy()
    shutil.rmtree(base, ignore_errors=True)


Vault_ENV = "CIMITIR_PROVISIONER_MASTER_KEY"


def https(env, inst, path, method="GET", body=None):
    import socket
    ctx = ssl.create_default_context(cafile=str(env["prov"].ca.ca_pem))
    data = json.dumps(body).encode() if body is not None else b""
    with socket.create_connection(("127.0.0.1", inst["edge_port"]), timeout=10) as raw, ctx.wrap_socket(raw, server_hostname=inst["domain"]) as s:
        s.sendall((f"{method} {path} HTTP/1.1\r\nHost: {inst['domain']}\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\n\r\n").encode() + data)
        buf = b""
        while chunk := s.recv(65536):
            buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    return int(head.split()[1]), (json.loads(rest) if rest.strip().startswith(b"{") else {})


class TestProvisioning:
    def test_01_register_validations(self, env):
        prov, owner = env["prov"], env["owner"]
        r = prov.register(owner, "parohia-alfa", "Parohia Alfa", "alfa.parohii.example", generate_owner_key=True)
        assert r["status"] == "registered" and r["owner_private_key_ONCE"]
        env["keys"]["alfa"] = r["owner_private_key_ONCE"]
        inst = prov.reg.get_instance("parohia-alfa")
        assert "private" not in json.dumps(inst["data"]) and r["owner_private_key_ONCE"] not in json.dumps(inst["data"])
        with pytest.raises(ProvisionError) as e:
            prov.register(owner, "parohia-alfa", "X", "alt.parohii.example", generate_owner_key=True)
        assert e.value.code == "DUPLICATE"
        with pytest.raises(ProvisionError):
            prov.register(owner, "Bad Slug", "X", "x.example.com", generate_owner_key=True)
        with pytest.raises(ProvisionError):
            prov.register(owner, "fara-cheie", "X", "y.example.com")
        r2 = prov.register(owner, "parohia-beta", "Parohia Beta", "beta.parohii.example", generate_owner_key=True)
        env["keys"]["beta"] = r2["owner_private_key_ONCE"]
        assert {r["backend_port"], r["edge_port"]}.isdisjoint({r2["backend_port"], r2["edge_port"]})

    def test_02_link_rules(self, env):
        prov, owner = env["prov"], env["owner"]
        bad = dict(env["a"].link_params(), project_ref="txuxkjgftopznvmooexq")
        with pytest.raises(ProvisionError) as e:
            prov.link(owner, "parohia-alfa", bad)
        assert e.value.code == "PROTECTED_PROJECT"
        with pytest.raises(ProvisionError):
            prov.link(owner, "parohia-alfa", {k: v for k, v in env["a"].link_params().items() if k != "storage_secret_key"})
        prov.link(owner, "parohia-alfa", env["a"].link_params())
        with pytest.raises(ProvisionError) as e:                       # același proiect nu poate servi două parohii
            prov.link(owner, "parohia-beta", env["a"].link_params())
        assert e.value.code == "DUPLICATE"
        prov.link(owner, "parohia-beta", env["b"].link_params())

    def test_03_admin_url_must_match(self, env):
        with pytest.raises(ProvisionError) as e:
            env["prov"].verify(env["owner"], "parohia-alfa", env["b"].admin_url())
        assert e.value.code == "ADMIN_URL_MISMATCH"

    def test_04_provision_both(self, env):
        prov, owner = env["prov"], env["owner"]
        for slug, key in (("parohia-alfa", "a"), ("parohia-beta", "b")):
            out = prov.provision(owner, slug, env[key].admin_url())
            h = out["steps"]["health"]
            assert out["instance"]["status"] == "running" and h["ready"] and h["https_ok"] and h["database"] == "up" and h["storage"] == "configured"
            assert out["steps"]["secure"]["login_check"]["least_privilege"] is True
            assert out["steps"]["migrate"]["applied"]

    def test_05_https_verified_and_wrong_ca_rejected(self, env):
        inst = env["prov"].reg.get_instance("parohia-alfa")
        st, body = https(env, inst, "/healthz")
        assert st == 200 and body["ok"]
        other = Vault(env["base"] / "other-vault", Vault.master_from_env({Vault_ENV: Vault.new_master()}))
        bad_ca = rt.LocalCA(other, env["base"] / "other-ca")
        with pytest.raises(ssl.SSLError):
            rt.https_get(inst["domain"], "127.0.0.1", inst["edge_port"], "/healthz", bad_ca.ca_pem)
        with pytest.raises(ssl.SSLError):                              # numele din certificat trebuie să corespundă domeniului
            rt.https_get("alt-domeniu.example", "127.0.0.1", inst["edge_port"], "/healthz", env["prov"].ca.ca_pem)

    def test_06_idempotent_reprovision(self, env):
        prov, owner = env["prov"], env["owner"]
        before = pgops.table_counts(pgops.connect(env["a"].admin_url()))
        out = prov.provision(owner, "parohia-alfa", env["a"].admin_url())
        assert out["steps"]["migrate"]["already_current"] is True and out["instance"]["status"] == "running"
        with pgops.connect(env["a"].admin_url()) as c:
            assert pgops.table_counts(c) == before
            assert c.execute("SELECT count(*) - count(DISTINCT version) FROM schema_migrations").fetchone()[0] == 0

    def test_07_isolation_and_secrets(self, env):
        prov = env["prov"]
        ia, ib = prov.reg.get_instance("parohia-alfa"), prov.reg.get_instance("parohia-beta")
        pa, pb = prov._secret(ia, "runtime_db_password"), prov._secret(ib, "runtime_db_password")
        ka, kb = prov._secret(ia, "storage_secret_key"), prov._secret(ib, "storage_secret_key")
        assert pa != pb and ka != kb and prov._secret(ia, "backup_key") != prov._secret(ib, "backup_key")
        with pytest.raises(ProvisionError):                            # credențialele alfa nu deschid proiectul beta
            pgops.connect(pgops.make_url("127.0.0.1", env["b"].port, "cimitir", "cimitir_cp_app", pa, "disable"))
        with pytest.raises(ProvisionError):
            pgops.connect(pgops.make_url("127.0.0.1", env["a"].port, "cimitir", "cimitir_cp_app", pb, "disable"))
        import requests
        assert requests.get(env["b"].supabase_url + "/storage/v1/bucket/documente", headers={"apikey": ka}, timeout=5).status_code == 401
        # nicio parolă/cheie nu apare în registru, jurnal de audit, fișiere de instanță sau seif în clar
        secrets_ = [pa, pb, ka, kb, ADMIN_PW, env["a"].admin_password, env["b"].admin_password, prov._secret(ia, "backup_key"), env["keys"]["alfa"]]
        blobs = [(prov.home / "provisioner.db").read_bytes(), json.dumps(prov.reg.audit_list(limit=1000)).encode(), json.dumps(prov.reg.steps(ia["id"])).encode()]
        for f in (prov.home / "instances").rglob("*"):
            if f.is_file() and f.suffix not in (".pem",) and f.name != "backend.log":
                blobs.append(f.read_bytes())
        for f in (prov.home / "vault").glob("*.sec"):
            blobs.append(f.read_bytes())
        for s in secrets_:
            assert all(s.encode() not in b for b in blobs), "secret găsit în clar"
        for f in (prov.home / "instances").rglob("backend.log"):
            assert all(s.encode() not in f.read_bytes() for s in (pa, pb, ka, kb))
        assert (prov.home / "vault").stat().st_mode & 0o077 == 0
        for f in (prov.home / "vault").glob("*.sec"):
            assert f.stat().st_mode & 0o077 == 0
        # fișier de seif mutat de la o instanță la alta nu se decriptează (AAD)
        src = prov.home / "vault" / ("TEST__" + ia["id"] + "__runtime_db_password.sec")
        dst = prov.home / "vault" / ("TEST__" + ib["id"] + "__runtime_db_password.sec")
        keep = dst.read_bytes()
        dst.write_bytes(src.read_bytes())
        with pytest.raises(ProvisionError):
            prov._secret(ib, "runtime_db_password")
        dst.write_bytes(keep)
        assert prov._secret(ib, "runtime_db_password") == pb

    def test_08_runtime_role_least_privilege(self, env):
        prov = env["prov"]
        ia = prov.reg.get_instance("parohia-alfa")
        rep = pgops.verify_runtime_login(prov._runtime_url(ia))
        assert rep["least_privilege"] and not rep["superuser"] and not rep["bypassrls"] and not rep["createrole"] and rep["app_function_ok"]
        with pgops.connect(env["a"].admin_url()) as c:
            assert c.execute("SELECT rolpassword IS NULL FROM pg_authid WHERE rolname='cimitir_app'").fetchone()[0] is True

    def test_09_admin_init_with_grant_only(self, env):
        prov = env["prov"]
        ia = prov.reg.get_instance("parohia-alfa")
        body = {"email": "admin@alfa.example", "password": ADMIN_PW, "organization_name": "Parohia Alfa", "cemetery_name": "Cimitir Alfa"}
        st, j = https(env, ia, "/api/v1/admin/bootstrap", "POST", {**body, "token": "licenta.fake.lic"})
        assert st in (400, 401, 403)
        beta_grant = ig.issue_grant(env["keys"]["beta"], "parohia-beta")           # grant al altei instanțe (cheie + audiență diferite)
        st, _ = https(env, ia, "/api/v1/admin/bootstrap", "POST", {**body, "token": beta_grant})
        assert st in (400, 401, 403)
        st, j = https(env, ia, "/api/v1/admin/bootstrap", "POST", {**body, "token": ig.issue_grant(env["keys"]["alfa"], "parohia-alfa")})
        assert st == 201, j
        st, _ = https(env, ia, "/api/v1/admin/bootstrap", "POST", {**body, "token": ig.issue_grant(env["keys"]["alfa"], "parohia-alfa")})
        assert st == 409
        h = prov.health(env["owner"], "parohia-alfa")
        assert h["admin_initialized"] is True and prov.get_public(env["owner"], "parohia-alfa")["admin_initialized"] is True
        assert prov.health(env["owner"], "parohia-beta")["admin_initialized"] is False

    def test_10_backup_verify_restore(self, env):
        prov, owner = env["prov"], env["owner"]
        b = prov.backup(owner, "parohia-alfa", env["a"].admin_url())
        assert prov.verify_backup(owner, "parohia-alfa", b["id"])["ok"]
        bk = prov.reg.get_backup(prov.reg.get_instance("parohia-alfa")["id"], b["id"])
        raw = Path(bk["path"]).read_bytes()
        Path(bk["path"]).write_bytes(raw[:-5] + b"XXXXX")                          # ciphertext modificat
        with pytest.raises(ProvisionError) as e:
            prov.verify_backup(owner, "parohia-alfa", b["id"])
        assert e.value.code == "BACKUP_CORRUPT"
        Path(bk["path"]).write_bytes(raw)
        assert prov.verify_backup(owner, "parohia-alfa", b["id"])["ok"]
        assert b"cp_admin" not in raw and b"PGDMP" not in raw                       # criptat
        with pgops.connect(env["a"].admin_url()) as c:
            before = pgops.table_counts(c)
        scratch = env["b"]                                                          # exercițiu: restaurare în altă bază, goală
        with pgops.connect(scratch.admin_url("postgres")) as c:
            c.execute("CREATE DATABASE scratch_alfa")
        res = prov.restore(owner, "parohia-alfa", env["a"].admin_url(), b["id"], target_database_url=scratch.admin_url("scratch_alfa"))
        assert res["tables"] == before
        with pytest.raises(ProvisionError) as e:                                    # țintă nevidă refuzată
            prov.restore(owner, "parohia-alfa", env["a"].admin_url(), b["id"], target_database_url=scratch.admin_url("scratch_alfa"))
        assert e.value.code == "TARGET_NOT_EMPTY"
        env["backup_id"], env["counts_before"] = b["id"], before

    def test_11_in_place_restore_needs_confirmation(self, env):
        prov, owner, oper = env["prov"], env["owner"], env["oper"]
        with pytest.raises(ProvisionError) as e:
            prov.restore(oper, "parohia-alfa", env["a"].admin_url(), env["backup_id"])
        assert e.value.code == "FORBIDDEN"
        with pytest.raises(ProvisionError) as e:
            prov.restore(owner, "parohia-alfa", env["a"].admin_url(), env["backup_id"], confirmation="inventat", confirm_slug="parohia-alfa")
        assert e.value.code == "CONFIRMATION"
        with pgops.connect(env["a"].admin_url()) as c:                              # dezastru simulat
            c.execute("DELETE FROM cp_org_roles")
            c.execute("DELETE FROM cp_admin_credentials")
        conf = prov.confirmation(owner, "restore", "parohia-alfa")
        with pytest.raises(ProvisionError):
            prov.restore(owner, "parohia-alfa", env["a"].admin_url(), env["backup_id"], confirmation=conf["code"], confirm_slug="altceva")
        conf = prov.confirmation(owner, "restore", "parohia-alfa")
        res = prov.restore(owner, "parohia-alfa", env["a"].admin_url(), env["backup_id"], confirmation=conf["code"], confirm_slug="parohia-alfa")
        assert res["restored_to"] == "instance" and res["safety_backup"]
        assert res["tables"] == env["counts_before"]
        with pytest.raises(ProvisionError):                                         # codul e de unică folosință
            prov.restore(owner, "parohia-alfa", env["a"].admin_url(), env["backup_id"], confirmation=conf["code"], confirm_slug="parohia-alfa")
        h = prov.health(owner, "parohia-alfa")
        assert h["ready"] and h["admin_initialized"] is True
        assert pgops.verify_runtime_login(prov._runtime_url(prov.reg.get_instance("parohia-alfa")))["least_privilege"]

    def test_12_update_preserves_data_and_applies_migrations(self, env):
        prov, owner = env["prov"], env["owner"]
        src = make_source(env["base"] / "src-v2", {"027_prov_test_marker.sql": "CREATE TABLE IF NOT EXISTS prov_test_marker (id integer PRIMARY KEY);\nGRANT SELECT ON prov_test_marker TO cimitir_cp_app;\n"})
        prov.import_release(owner, str(src), "v2")
        with pgops.connect(env["a"].admin_url()) as c:
            before = pgops.table_counts(c)
        out = prov.update(owner, "parohia-alfa", env["a"].admin_url(), "v2")
        assert out["from"] == "v1" and out["to"] == "v2" and out["migrations"]["applied"] == [27] and out["instance"]["status"] == "running"
        with pgops.connect(env["a"].admin_url()) as c:
            after = pgops.table_counts(c)
        assert {t: n for t, n in after.items() if t in before and t != "schema_migrations"} == {t: n for t, n in before.items() if t != "schema_migrations"}
        assert "prov_test_marker" in after and after["schema_migrations"] == before["schema_migrations"] + 1
        assert prov.health(owner, "parohia-alfa")["admin_initialized"] is True
        assert prov.get_public(owner, "parohia-alfa")["release"] == "v2"

    def test_13_failed_migration_rolls_back_and_keeps_old_backend(self, env):
        prov, owner = env["prov"], env["owner"]
        src = make_source(env["base"] / "src-v3", {"027_prov_test_marker.sql": "CREATE TABLE IF NOT EXISTS prov_test_marker (id integer PRIMARY KEY);\nGRANT SELECT ON prov_test_marker TO cimitir_cp_app;\n",
                                                   "028_bad.sql": "CREATE TABLE prov_bad_leftover (x int);\nSELECT 1/0;\n"})
        prov.import_release(owner, str(src), "v3")
        with pytest.raises(ProvisionError) as e:
            prov.update(owner, "parohia-alfa", env["a"].admin_url(), "v3")
        assert e.value.code == "MIGRATION_FAILED"
        with pgops.connect(env["a"].admin_url()) as c:
            assert c.execute("SELECT to_regclass('public.prov_bad_leftover') IS NULL").fetchone()[0]
        assert prov.health(owner, "parohia-alfa")["ready"] and prov.get_public(owner, "parohia-alfa")["release"] == "v2"

    def test_14_broken_release_auto_rollback(self, env):
        prov, owner = env["prov"], env["owner"]
        src = make_source(env["base"] / "src-v4", {"027_prov_test_marker.sql": "CREATE TABLE IF NOT EXISTS prov_test_marker (id integer PRIMARY KEY);\nGRANT SELECT ON prov_test_marker TO cimitir_cp_app;\n"}, break_app=True)
        prov.import_release(owner, str(src), "v4")
        with pytest.raises(ProvisionError) as e:
            prov.update(owner, "parohia-alfa", env["a"].admin_url(), "v4")
        assert e.value.code == "UPDATE_ROLLED_BACK"
        h = prov.health(owner, "parohia-alfa")
        assert h["ready"] and prov.get_public(owner, "parohia-alfa")["release"] == "v2" and h["admin_initialized"] is True

    def test_15_tampered_release_refused(self, env):
        prov, owner = env["prov"], env["owner"]
        f = prov.release_dir("v2") / "shared" / "cimitir_cloud_app.py"
        orig = f.read_text()
        f.write_text(orig + "\n# modificat\n")
        with pytest.raises(ProvisionError) as e:
            prov.update(owner, "parohia-alfa", env["a"].admin_url(), "v2")
        assert e.value.code == "RELEASE_TAMPERED"
        f.write_text(orig)
        rt.verify_release(prov.release_dir("v2"))

    def test_16_authorization_of_destructive_actions(self, env):
        prov, owner, oper = env["prov"], env["owner"], env["oper"]
        url = env["b"].admin_url()
        with pytest.raises(ProvisionError) as e:
            prov.suspend(oper, "parohia-beta", url, "x", "parohia-beta")
        assert e.value.code == "FORBIDDEN"
        with pytest.raises(ProvisionError) as e:
            prov.confirmation(oper, "suspend", "parohia-beta")
        assert e.value.code == "FORBIDDEN"
        with pytest.raises(ProvisionError) as e:
            prov.suspend(owner, "parohia-beta", url, "", "parohia-beta")
        assert e.value.code == "CONFIRMATION"
        c1 = prov.confirmation(owner, "suspend", "parohia-beta")
        with pytest.raises(ProvisionError):                                         # cod emis pentru altă acțiune
            prov.deactivate(owner, "parohia-beta", url, c1["code"], "parohia-beta")
        with pytest.raises(ProvisionError):                                         # cod emis pentru altă instanță
            prov.suspend(owner, "parohia-alfa", env["a"].admin_url(), c1["code"], "parohia-alfa")
        assert prov.get_public(owner, "parohia-beta")["status"] == "running"
        # operator din alt mediu
        prod_op = {"id": "x", "name": "prod-op", "role": "owner", "environment": "PROD"}
        with pytest.raises(ProvisionError) as e:
            prov.get_public(prod_op, "parohia-beta")
        assert e.value.code == "ENV_MISMATCH"

    def test_17_suspend_resume_deactivate(self, env):
        prov, owner = env["prov"], env["owner"]
        url = env["b"].admin_url()
        ib = prov.reg.get_instance("parohia-beta")
        pw = prov._secret(ib, "runtime_db_password")
        c = prov.confirmation(owner, "suspend", "parohia-beta")
        assert prov.suspend(owner, "parohia-beta", url, c["code"], "parohia-beta")["status"] == "suspended"
        with pytest.raises(OSError):
            rt.https_get(ib["domain"], "127.0.0.1", ib["edge_port"], "/healthz", prov.ca.ca_pem)
        with pytest.raises(ProvisionError):
            pgops.connect(pgops.make_url("127.0.0.1", env["b"].port, "cimitir", "cimitir_cp_app", pw, "disable"))
        with pytest.raises(ProvisionError) as e:
            prov.deploy(owner, "parohia-beta")
        assert e.value.code == "BAD_STATE"
        assert prov.resume(owner, "parohia-beta", url)["status"] == "running"
        assert prov.health(owner, "parohia-beta")["ready"]
        c = prov.confirmation(owner, "deactivate", "parohia-beta")
        assert prov.deactivate(owner, "parohia-beta", url, c["code"], "parohia-beta")["status"] == "deactivated"
        assert prov.vault.get(prov._vname(ib, "runtime_db_password")) is None and prov.vault.get(prov._vname(ib, "storage_secret_key")) is None
        with pytest.raises(ProvisionError):
            pgops.connect(pgops.make_url("127.0.0.1", env["b"].port, "cimitir", "cimitir_cp_app", pw, "disable"))
        with pgops.connect(url) as cc:                                              # datele NU sunt șterse
            assert pgops.table_counts(cc)
        assert prov.reg.backups(ib["id"]) is not None

    def test_18_secret_rotation_and_tls_renewal(self, env):
        prov, owner = env["prov"], env["owner"]
        ia = prov.reg.get_instance("parohia-alfa")
        old = prov._secret(ia, "runtime_db_password")
        r = prov.secure(owner, "parohia-alfa", env["a"].admin_url(), rotate=True)
        assert r["rotated"] and prov._secret(ia, "runtime_db_password") != old
        with pytest.raises(ProvisionError):
            pgops.connect(pgops.make_url("127.0.0.1", env["a"].port, "cimitir", "cimitir_cp_app", old, "disable"))
        prov.restart(owner, "parohia-alfa")
        assert prov.health(owner, "parohia-alfa")["ready"]
        assert prov.renew_tls(owner, "parohia-alfa")["renewed"] is False
        assert prov.renew_tls(owner, "parohia-alfa", force=True)["renewed"] is True
        assert prov.health(owner, "parohia-alfa")["https_ok"]                       # edge-ul a preluat certificatul nou fără repornire

    def test_19_reconcile_after_crash(self, env):
        prov = env["prov"]
        ia = prov.reg.get_instance("parohia-alfa")
        prov._stop_procs(ia)
        assert not prov._health(prov.reg.get_instance(ia["id"]))["backend_process"]
        assert prov.reconcile() == {"parohia-alfa": "repornită"}
        assert prov.health(env["owner"], "parohia-alfa")["ready"]

    def test_20_audit_complete_chained_and_tamper_evident(self, env):
        prov = env["prov"]
        acts = {a["action"] for a in prov.reg.audit_list(limit=1000)}
        for need in ("register", "link", "verify", "migrate", "secure", "deploy", "backup", "restore", "update", "suspend", "resume", "deactivate", "import_release", "health"):
            assert need in acts, need
        outcomes = {a["outcome"] for a in prov.reg.audit_list(limit=1000)}
        assert {"ok", "error", "denied"} <= outcomes
        assert prov.reg.audit_verify()["ok"]
        import cimitir_prov_store as ps
        db = env["base"] / "audit-copy.db"
        prov.reg.audit("t", "x")
        sqlite3.connect(prov.home / "provisioner.db").backup(sqlite3.connect(db))
        copy = ps.Registry(db)
        assert copy.audit_verify()["ok"]
        c = sqlite3.connect(db)
        with pytest.raises(sqlite3.DatabaseError):
            c.execute("UPDATE audit SET outcome='ok' WHERE id=1")
        c.execute("DROP TRIGGER audit_no_update")                                    # atacator cu acces la fișier: modificarea se DETECTEAZĂ prin lanț
        c.execute("UPDATE audit SET detail='{}' WHERE id=2")
        c.commit()
        assert copy.audit_verify()["ok"] is False
        c.close()
        assert prov.reg.audit_verify()["ok"]


def test_21_http_api_end_to_end_with_client(env):
    """Circuitul complet prin API-ul HTTP + clientul MAIN: register → link → provision → status → health (parohie nouă, proiect nou)."""
    from fastapi.testclient import TestClient
    import cimitir_prov_client as pc
    import cimitir_provisioner_app as appm
    prov = env["prov"]
    c = demo.LocalSupabaseProject(env["base"], "gama").start()
    try:
        tc = TestClient(appm.create_app(prov))
        tok = prov.reg.create_operator("api-owner", "owner", "TEST")

        class Ad:
            def request(self, method, url, json=None, headers=None, timeout=None, verify=None):
                return tc.request(method, url[url.index("/api"):], json=json, headers=headers)
        cl = pc.ProvisionerClient("http://x", tok, session=Ad())
        r = cl.register("parohia-gama", "Parohia Gama", "gama.parohii.example", generate_owner_key=True)
        cl.link("parohia-gama", c.link_params())
        with pytest.raises(pc.ProvisionerClientError):                                  # release inexistent => refuz explicit
            cl.provision("parohia-gama", c.admin_url(), release="v-inexistent")
        prov.set_default_release(env["owner"], "v2")
        out = cl.provision("parohia-gama", c.admin_url())
        assert out["instance"]["status"] == "running" and out["steps"]["health"]["ready"]
        assert pc.status_lines(cl.get("parohia-gama"))[0].endswith("Activă (sănătoasă)")
        assert cl.health("parohia-gama")["https_ok"]
        assert r["owner_private_key_ONCE"] not in json.dumps(cl.get("parohia-gama"))
        assert c.admin_password not in json.dumps(cl.audit())
        assert prov.reg.audit_verify()["ok"]
    finally:
        prov._stop_procs(prov.reg.get_instance("parohia-gama"))
        c.destroy()
