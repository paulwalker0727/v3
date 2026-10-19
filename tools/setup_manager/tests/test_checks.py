import json

import pytest

from tools.setup_manager import checks
from tools.setup_manager.tests.conftest import FakeNet

OK_BUCKET = (200, json.dumps({"id": "cimitir-private-test", "public": False}).encode())


def _cfg(c, **kw):
    return dict(c, db_password="pw", supabase_secret_key="sb_secret_k123456", **kw)


def _checker(cfg, net):
    return checks.Checker(cfg, net)


def test_all_nine_checks_returned_and_prod_blocked_first(test_cfg):
    res = _checker(_cfg(test_cfg, supabase_url="https://txuxkjgftopznvmooexq.supabase.co"), FakeNet()).run_all()
    assert len(res) == 1 and res[0]["status"] == "FAIL" and "PROD" in res[0]["detail"]


def test_dns_failure_and_tcp_failure_reported(test_cfg):
    n = FakeNet()
    n.dns_fail.add("aws-0-eu-west-1.pooler.supabase.com")
    r = _checker(_cfg(test_cfg), n).dns_tcp()
    assert r["status"] == "FAIL" and "DNS" in r["detail"]
    n2 = FakeNet()
    n2.tcp_fail.add(("aws-0-eu-west-1.pooler.supabase.com", 5432))
    assert "TCP" in _checker(_cfg(test_cfg), n2).dns_tcp()["detail"]
    assert _checker(_cfg(test_cfg), FakeNet()).dns_tcp()["status"] == "PASS"


def test_pg_auth_failure_is_explained_without_leaking(test_cfg):
    class Bad(FakeNet):
        def pg(self, url):
            raise RuntimeError('FATAL: password authentication failed for user "postgres.fommbiwndlxkfsnlqatq" pw=SECRETPW')
    from tools.setup_manager import redact
    redact.register("SECRETPW")
    r = _checker(_cfg(test_cfg), Bad()).pg_auth()
    assert r["status"] == "FAIL" and "parol" in r["detail"].lower() and "SECRETPW" not in r["detail"]


def test_pg_auth_missing_password(test_cfg):
    assert _checker(dict(test_cfg), FakeNet()).pg_auth()["status"] == "FAIL"


def test_identify_with_key_accepted_and_rejected(test_cfg):
    n = FakeNet()
    n.http_map["/storage/v1/bucket"] = (200, b"[]")
    assert _checker(_cfg(test_cfg), n).identify()["status"] == "PASS"
    n.http_map["/storage/v1/bucket"] = (401, b"{}")
    r = _checker(_cfg(test_cfg), n).identify()
    assert r["status"] == "FAIL" and "altui proiect" in r["detail"]


def test_identify_preserved_project_flagged(test_cfg):
    old = "nhhczdmcocbobptogvsw"
    cfg = _cfg(test_cfg, project_ref=old, supabase_url=f"https://{old}.supabase.co", db_username=f"postgres.{old}",
               db_connection_string="postgresql://x@aws-0-eu-west-1.pooler.supabase.com:5432/postgres")
    r = _checker(cfg, FakeNet()).identify()
    assert r["status"] == "FAIL" and "intact" in r["detail"]


def test_storage_private_public_missing_denied(test_cfg):
    n = FakeNet()
    path = "/storage/v1/bucket/cimitir-private-test"
    n.http_map[path] = OK_BUCKET
    assert _checker(_cfg(test_cfg), n).storage()["status"] == "PASS"
    n.http_map[path] = (200, b'{"public": true}')
    assert "PUBLIC" in _checker(_cfg(test_cfg), n).storage()["detail"]
    n.http_map[path] = (404, b'{"error":"Bucket not found"}')
    r = _checker(_cfg(test_cfg), n).storage()
    assert r["status"] == "FAIL" and "nu există" in r["detail"]
    n.http_map[path] = (403, b"{}")
    assert _checker(_cfg(test_cfg), n).storage()["status"] == "FAIL"


def test_health_pass_and_fail(test_cfg):
    n = FakeNet()
    n.http_map["/healthz"] = (200, b'{"ok":true}')
    n.http_map["/api/v1/health"] = (200, b'{"ok":true}')
    assert _checker(test_cfg, n).health()["status"] == "PASS"
    n.http_map["/api/v1/health"] = (503, b'{"database":"down","storage":"configured"}')
    r = _checker(test_cfg, n).health()
    assert r["status"] == "FAIL" and "database=down" in r["detail"]
    n.http_map["/healthz"] = ConnectionRefusedError()
    assert "indisponibil" in _checker(test_cfg, n).health()["detail"]


def test_docker_mobile_missing_files_detected(test_cfg):
    n = FakeNet()
    n.docker_map["image inspect"] = (0, "[]", "")
    n.docker_map["run --rm"] = (0, json.dumps({"static": ["mobile_photos.js", "jsQR.js"], "tpl": True}) + "\n", "")
    r = _checker(test_cfg, n).docker_mobile()
    assert r["status"] == "FAIL" and "mobile_photos.js" in r["detail"]
    n.docker_map["run --rm"] = (0, json.dumps({"static": [], "tpl": True}), "")
    assert _checker(test_cfg, n).docker_mobile()["status"] == "PASS"
    cmd = next(c for c in n.docker_calls if c[0] == "run")
    assert "--network" in cmd and "none" in cmd and "--rm" in cmd


def test_docker_unavailable_is_not_pass(test_cfg):
    class NoDocker(FakeNet):
        def docker(self, args, timeout=60):
            raise FileNotFoundError("docker")
    assert _checker(test_cfg, NoDocker()).docker_mobile()["status"] in ("WARN", "FAIL")


def test_repo_dockerfile_copies_all_mobile_files(test_cfg):
    assert checks.dockerfile_coverage(test_cfg) == []


def test_mobile_expected_derived_from_cloud_app(test_cfg):
    names = checks.mobile_expected(test_cfg)
    assert {"mobile_photos.js", "jsQR.js", "mobile_app.js", "mobile_sw.js"} <= set(names)


def test_dockerfile_coverage_detects_missing(test_cfg, tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "Dockerfile").write_text("COPY backend/static/mobile_app.js backend/static/\n")
    cfg = dict(test_cfg, repo_dir=str(tmp_path))
    assert "mobile_app.css" in checks.dockerfile_coverage(cfg)


@pytest.mark.pg
def test_db_checks_on_real_postgres(pg_db, test_cfg):
    n = FakeNet(pg_url=pg_db)
    cfg = _cfg(test_cfg)
    c = _checker(cfg, n)
    assert c.pg_auth()["status"] == "PASS"
    assert c.pg_permissions()["status"] in ("PASS", "WARN")
    s6 = c.schema_objects()
    assert s6["status"] == "WARN" and "024" in s6["detail"]         # 024–026 lipsesc, restul OK
    s7 = c.migrations_state()
    assert s7["status"] == "WARN" and "023: APLICATĂ" in s7["detail"] and "024: LIPSĂ" in s7["detail"]
    assert "[24, 25, 26]" in s7["detail"]
    assert c.conn is not None


@pytest.mark.pg
def test_checks_detect_inconsistent_state(pg_db, test_cfg):
    import psycopg
    with psycopg.connect(pg_db) as cn:
        cn.execute("DROP FUNCTION cp_admin_status()")
        cn.commit()
    c = _checker(_cfg(test_cfg), FakeNet(pg_url=pg_db))
    c.pg_auth()
    assert c.schema_objects()["status"] == "FAIL" and "023" in c.schema_objects()["detail"]
    assert c.migrations_state()["status"] == "FAIL"


def test_db_checks_skip_without_connection(test_cfg):
    c = _checker(test_cfg, FakeNet())
    for fn in (c.pg_permissions, c.schema_objects, c.migrations_state):
        assert fn()["status"] == "SKIP"


def test_run_all_never_raises_and_has_nine(test_cfg):
    n = FakeNet()
    n.dns_fail.add("aws-0-eu-west-1.pooler.supabase.com")
    res = _checker(dict(_cfg(test_cfg), db_password="Zx9uniquepw"), n).run_all()
    assert len(res) == 9 and [r["id"] for r in res][:2] == ["dns_tcp", "pg_auth"]
    assert all(r["status"] in ("PASS", "FAIL", "WARN", "SKIP") for r in res)
    assert "Zx9uniquepw" not in json.dumps(res) and "sb_secret_k123456" not in json.dumps(res)
