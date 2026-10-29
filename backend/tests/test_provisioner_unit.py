"""Teste unitare/API ale provizionării (fără PostgreSQL): seif, redactare, releases, Docker/Caddy generate, HTTP API (autentificare, roluri, limitare), client."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

import cimitir_prov_client as pc  # noqa: E402
import cimitir_prov_runtime as rt  # noqa: E402
import cimitir_provisioner as pv  # noqa: E402
import cimitir_provisioner_app as app_mod  # noqa: E402
from cimitir_prov_store import ProvisionError, Registry, Vault, redact, redact_text  # noqa: E402

MK = "CIMITIR_PROVISIONER_MASTER_KEY"


@pytest.fixture()
def prov(tmp_path):
    p = pv.Provisioner(tmp_path / "home", "TEST", Vault(tmp_path / "home" / "vault", Vault.master_from_env({MK: Vault.new_master()})))
    return p


def test_redaction():
    assert "s3cr3t" not in redact_text("postgresql://u:s3cr3t@h:5432/db") and "sb_secret_abcdefghijk" not in redact_text("sb_secret_abcdefghijk")
    r = redact({"admin_database_url": "postgresql://a:b@c/d", "password": "x", "slug": "p", "owner_public_key": "PUB", "nested": [{"token": "t"}]})
    assert r["admin_database_url"] == "***" and r["password"] == "***" and r["slug"] == "p" and r["owner_public_key"] == "PUB" and r["nested"][0]["token"] == "***"


def test_vault_master_key_rules(tmp_path):
    with pytest.raises(ProvisionError):
        Vault.master_from_env({})
    with pytest.raises(ProvisionError):
        Vault.master_from_env({MK: "scurt"})
    f = tmp_path / "mk"
    f.write_text(Vault.new_master())
    f.chmod(0o644)
    with pytest.raises(ProvisionError) as e:
        Vault.master_from_env({"CIMITIR_PROVISIONER_MASTER_KEY_FILE": str(f)})
    assert "0600" in str(e.value)
    f.chmod(0o600)
    assert len(Vault.master_from_env({"CIMITIR_PROVISIONER_MASTER_KEY_FILE": str(f)})) == 32


def test_vault_wrong_key_and_tamper(tmp_path):
    v = Vault(tmp_path / "v", Vault.master_from_env({MK: Vault.new_master()}))
    v.set("TEST/a/x", "valoare")
    assert v.get("TEST/a/x") == "valoare" and v.get("TEST/a/lipsa") is None
    other = Vault(tmp_path / "v", Vault.master_from_env({MK: Vault.new_master()}))
    with pytest.raises(ProvisionError):
        other.get("TEST/a/x")
    p = next((tmp_path / "v").glob("*.sec"))
    p.write_bytes(p.read_bytes()[:-1] + b"\x00")
    with pytest.raises(ProvisionError):
        v.get("TEST/a/x")
    with pytest.raises(ProvisionError):
        v.get("../etc/passwd")


def test_release_build_verify_and_provisioner_code_excluded(tmp_path):
    info = rt.build_release(ROOT, tmp_path / "r1", "v1")
    assert info["files"] > 50
    names = {p.name for p in (tmp_path / "r1" / "shared").glob("*.py")}
    assert "cimitir_cloud_app.py" in names and not any(n.startswith("cimitir_prov") for n in names)       # fără codul provisionerului în instanță
    assert not any(p.name.endswith(".env") or p.name == ".env" for p in (tmp_path / "r1").rglob("*"))
    rt.verify_release(tmp_path / "r1")
    (tmp_path / "r1" / "shared" / "extra.py").write_text("x=1")
    with pytest.raises(ProvisionError):
        rt.verify_release(tmp_path / "r1")
    with pytest.raises(ProvisionError):
        rt.build_release(ROOT, tmp_path / "r1", "v1")


def test_docker_command_has_no_secrets_and_is_hardened():
    secret = "PAROLA-SUPER-SECRETA"
    captured = {}
    d = rt.DockerProvider(runner=lambda cmd, env: captured.update(cmd=cmd, env=env) or 0)
    d.start("parohia-x", "v1", 18100, {"CIMITIR_CP_DATABASE_URL": f"postgresql://u:{secret}@h/db", "SUPABASE_SECRET_KEY": "sb_secret_zzzz"})
    run = captured["cmd"]
    assert secret not in " ".join(run) and "sb_secret_zzzz" not in " ".join(run)
    assert "--read-only" in run and "--cap-drop" in run and "no-new-privileges" in run and "127.0.0.1:18100:8080" in run
    assert captured["env"]["CIMITIR_CP_DATABASE_URL"].endswith("/db") and secret in captured["env"]["CIMITIR_CP_DATABASE_URL"]       # valoarea trece doar prin mediul procesului


def test_caddy_site_has_tls_hsts_and_local_upstream():
    c = rt.caddy_site("parohie.exemplu.ro", 18100)
    assert c.startswith("parohie.exemplu.ro {") and "Strict-Transport-Security" in c and "reverse_proxy 127.0.0.1:18100" in c and "health_uri /healthz" in c


def test_ports_unique_and_registry_uniqueness(prov):
    owner = prov.reg.authenticate(prov.reg.create_operator("o", "owner", "TEST"))
    seen = set()
    for i in range(5):
        r = prov.register(owner, f"par-{i}-x", "P", f"p{i}.exemplu.ro", generate_owner_key=True)
        assert r["backend_port"] not in seen and r["edge_port"] not in seen
        seen |= {r["backend_port"], r["edge_port"]}
    assert len({prov.vault.get(prov._vname(prov.reg.get_instance(f"par-{i}-x"), "backup_key")) for i in range(5)}) == 5     # chei de backup distincte


def test_prod_rejects_local_projects(tmp_path):
    p = pv.Provisioner(tmp_path / "h", "PROD", Vault(tmp_path / "h" / "v", Vault.master_from_env({MK: Vault.new_master()})))
    o = p.reg.authenticate(p.reg.create_operator("po", "owner", "PROD"))
    p.register(o, "par-prod-1", "P", "prod.exemplu.ro", generate_owner_key=True)
    with pytest.raises(ProvisionError) as e:
        p.link(o, "par-prod-1", {"supabase_url": "http://127.0.0.1:1", "project_ref": "abcdefghijklmnopqrst", "db_host": "127.0.0.1", "db_port": 5432, "db_name": "x", "bucket": "b", "storage_secret_key": "k"})
    assert e.value.code == "LOCAL_IN_PROD"
    with pytest.raises(ProvisionError) as e:
        p.link(o, "par-prod-1", {"supabase_url": "https://abcdefghijklmnopqrst.supabase.co", "project_ref": "abcdefghijklmnopqrst", "db_host": "db.abcdefghijklmnopqrst.supabase.co", "db_port": 5432,
                                 "db_name": "postgres", "bucket": "b", "storage_secret_key": "k", "sslmode": "disable"})
    assert e.value.code == "INSECURE_TLS_DB"
    with pytest.raises(ProvisionError) as e:
        p.link(o, "par-prod-1", {"supabase_url": "https://txuxkjgftopznvmooexq.supabase.co", "project_ref": "txuxkjgftopznvmooexq", "db_host": "x", "db_port": 5432, "db_name": "x", "bucket": "b", "storage_secret_key": "k"})
    assert e.value.code == "PROTECTED_PROJECT"
    test_op = p.reg.authenticate(p.reg.create_operator("to", "owner", "TEST"))        # identitățile TEST și PROD sunt separate
    with pytest.raises(ProvisionError) as e:
        p.list_public(test_op)
    assert e.value.code == "ENV_MISMATCH"


def test_protected_refs_match_test_guard():
    import cimitir_test_guard as g
    assert tuple(pv.PROTECTED_REFS) == tuple(g.PROTECTED_PROJECT_REFS)


def test_build_provisioner_requires_env(tmp_path, monkeypatch):
    monkeypatch.delenv("CIMITIR_PROVISIONER_ENV", raising=False)
    with pytest.raises(ProvisionError):
        app_mod.build_provisioner(tmp_path)


class _Adapter:
    def __init__(self, tc):
        self.tc = tc

    def request(self, method, url, json=None, headers=None, timeout=None, verify=None):
        return self.tc.request(method, url[url.index("/api"):], json=json, headers=headers)


def test_http_api_auth_roles_and_client(prov, monkeypatch):
    from fastapi.testclient import TestClient
    tc = TestClient(app_mod.create_app(prov))
    owner_t, oper_t = prov.reg.create_operator("o", "owner", "TEST"), prov.reg.create_operator("p", "operator", "TEST")
    API = app_mod.API
    assert tc.get(API + "/health").json()["env"] == "TEST"
    assert tc.get(API + "/instances").status_code == 401
    assert tc.get(API + "/instances", headers={"Authorization": "Bearer licenta.lic.semnata"}).status_code == 401
    grant_like = "grant.eyJ2IjoxfQ.sig"
    assert tc.get(API + "/instances", headers={"Authorization": "Bearer " + grant_like}).status_code == 401      # grantul proprietarului instanței NU autorizează provizionarea
    assert tc.get("/docs").status_code == 404 and tc.get("/openapi.json").status_code == 404
    cl = pc.ProvisionerClient("http://x", owner_t, session=_Adapter(tc))
    r = cl.register("par-api-1", "Parohia API", "api.exemplu.ro", generate_owner_key=True)
    assert r["status"] == "registered" and r["owner_private_key_ONCE"]
    lst = cl.list_instances()
    assert [i["slug"] for i in lst] == ["par-api-1"] and "owner_private_key_ONCE" not in json.dumps(lst)
    lines = pc.status_lines(lst[0])
    assert "Înregistrată" in lines[0] and "ÎN AȘTEPTARE" in lines[-1]
    with pytest.raises(pc.ProvisionerClientError) as e:
        cl.register("par-api-1", "dup", "alt.exemplu.ro", generate_owner_key=True)
    assert e.value.code == "DUPLICATE"
    opc = pc.ProvisionerClient("http://x", oper_t, session=_Adapter(tc))
    with pytest.raises(pc.ProvisionerClientError) as e:
        opc.action("par-api-1", "confirmations", action="suspend")
    assert e.value.status == 403
    with pytest.raises(pc.ProvisionerClientError) as e:
        opc.action("par-api-1", "deactivate", admin_database_url="postgresql://a:PAROLA-ADM@h/d", confirmation="x", confirm_slug="par-api-1")
    assert e.value.code == "FORBIDDEN" and "PAROLA-ADM" not in str(e.value)
    assert all("PAROLA-ADM" not in json.dumps(a) for a in prov.reg.audit_list(limit=100))
    assert any(a["outcome"] == "denied" for a in prov.reg.audit_list(limit=100))
    for _ in range(11):
        tc.get(API + "/instances", headers={"Authorization": "Bearer cpv_gresit"})
    assert tc.get(API + "/instances", headers={"Authorization": "Bearer cpv_gresit"}).status_code == 429
    assert prov.reg.audit_verify()["ok"]


def test_operator_tokens_stored_hashed(prov):
    t = prov.reg.create_operator("hash-test", "owner", "TEST")
    assert t.encode() not in (prov.home / "provisioner.db").read_bytes() and prov.reg.authenticate(t)["name"] == "hash-test"
    prov.reg.disable_operator("hash-test")
    assert prov.reg.authenticate(t) is None


def test_confirmation_expires(tmp_path):
    clock = [1000.0]
    r = Registry(tmp_path / "r.db", clock=lambda: clock[0])
    code = r.new_confirmation("op", "suspend", "i1", ttl=300)
    clock[0] += 301
    assert not r.consume_confirmation(code, "op", "suspend", "i1")
    code = r.new_confirmation("op", "suspend", "i1", ttl=300)
    assert not r.consume_confirmation(code, "alt", "suspend", "i1") and r.consume_confirmation(code, "op", "suspend", "i1") and not r.consume_confirmation(code, "op", "suspend", "i1")
