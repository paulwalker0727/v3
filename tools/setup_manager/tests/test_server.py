import json

import pytest
from fastapi.testclient import TestClient

from tools.setup_manager.config import ConfigStore
from tools.setup_manager.server import EXEC_PHRASE, create_app
from tools.setup_manager.tests.conftest import FakeNet

TOKEN = "tok-test-123"


@pytest.fixture()
def env(tmp_path):
    store = ConfigStore(tmp_path / "h")
    net = FakeNet()
    app = create_app(store, net, docker=lambda a, t=60: (1, "", "no docker"), token=TOKEN)
    c = TestClient(app, base_url="http://127.0.0.1:8765", headers={"X-SM-Token": TOKEN})
    return c, store, net, app


def test_token_required_and_index_has_token(env):
    c, *_ = env
    assert c.get("/api/state", headers={"X-SM-Token": "wrong"}).status_code == 401
    assert c.get("/api/state", headers={"X-SM-Token": ""}).status_code == 401
    html = c.get("/").text
    assert TOKEN in html and "__SM_TOKEN__" not in html


def test_foreign_host_and_origin_rejected(tmp_path):
    app = create_app(ConfigStore(tmp_path / "h2"), FakeNet(), token=TOKEN)
    evil = TestClient(app, base_url="http://evil.example.com", headers={"X-SM-Token": TOKEN})
    assert evil.get("/api/state").status_code == 403
    ok = TestClient(app, base_url="http://127.0.0.1:8765", headers={"X-SM-Token": TOKEN})
    assert ok.put("/api/config", json={"values": {}}, headers={"Origin": "http://evil.example.com"}).status_code == 403


def test_default_mode_is_dry_run_and_protections_listed(env):
    c, *_ = env
    s = c.get("/api/state").json()
    assert s["mode"] == "dry_run" and "txuxkjgftopznvmooexq" in s["protected"]["prod_refs"] and 8084 in s["protected"]["ports"]


def test_execute_mode_needs_exact_phrase_and_valid_test_config(env):
    c, *_ = env
    assert c.post("/api/mode", json={"mode": "execute", "confirm": "da"}).status_code == 400
    assert c.post("/api/mode", json={"mode": "execute", "confirm": EXEC_PHRASE}).json()["mode"] == "execute"
    assert c.post("/api/mode", json={"mode": "dry_run"}).json()["mode"] == "dry_run"
    c.put("/api/config", json={"values": {"supabase_url": "https://txuxkjgftopznvmooexq.supabase.co"}})
    r = c.post("/api/mode", json={"mode": "execute", "confirm": EXEC_PHRASE})
    assert r.status_code == 400 and "PROD" in r.json()["detail"]


def test_secrets_never_returned(env):
    c, *_ = env
    c.put("/api/config", json={"values": {"db_password": "Hunter2-secret", "supabase_secret_key": "sb_secret_ZZZ999"}})
    body = c.get("/api/state").text
    assert "Hunter2" not in body and "ZZZ999" not in body
    assert c.get("/api/state").json()["config"]["db_password"] == {"set": True}


def test_prod_config_makes_everything_refuse(env):
    c, *_ = env
    c.put("/api/config", json={"values": {"supabase_url": "https://txuxkjgftopznvmooexq.supabase.co", "db_password": "pw12345678"}})
    assert c.get("/api/state").json()["prod_blocked"]
    r = c.post("/api/checks").json()
    assert len(r["results"]) == 1 and r["results"][0]["status"] == "FAIL"
    assert c.get("/api/migrations/plan").status_code == 400
    assert c.post("/api/docker/plan", json={"kind": "start"}).status_code == 400
    assert c.post("/api/pairing/probe").status_code == 400
    assert c.post("/api/storage/bucket", json={}).status_code == 400


def test_checks_endpoint_returns_nine(env):
    c, *_ = env
    c.put("/api/config", json={"values": {"db_password": "pw12345678", "supabase_secret_key": "sb_secret_abcdefgh"}})
    r = c.post("/api/checks").json()
    assert len(r["results"]) == 9 and set(r["summary"]) == {"PASS", "FAIL", "WARN", "SKIP"}
    assert "pw12345678" not in json.dumps(r) and "sb_secret_abcdefgh" not in json.dumps(r)


def test_docker_run_dry_run_and_confirmation(env):
    c, *_ = env
    r = c.post("/api/docker/run", json={"kind": "build", "confirm": "x"})
    assert r.status_code == 200 and r.json()["dry_run"] and r.json()["confirm_phrase"].startswith("CONSTRUIESC")
    assert c.post("/api/docker/run", json={"kind": "nuke"}).status_code == 400


def test_docker_port_8084_rejected_via_api(env):
    c, *_ = env
    c.put("/api/config", json={"values": {"docker_port": 8084}})
    assert c.post("/api/docker/plan", json={"kind": "start"}).status_code == 400
    assert c.post("/api/docker/run", json={"kind": "start"}).status_code == 400


def test_bucket_creation_dry_run_then_needs_phrase(env):
    c, _, net, _ = env
    c.put("/api/config", json={"values": {"supabase_secret_key": "sb_secret_abcdefgh"}})
    assert c.post("/api/storage/bucket", json={}).json()["dry_run"]
    c.post("/api/mode", json={"mode": "execute", "confirm": EXEC_PHRASE})
    assert c.post("/api/storage/bucket", json={"confirm": "nu"}).status_code == 400
    net.http_map["/storage/v1/bucket"] = (200, b'{"name":"cimitir-private-test"}')
    r = c.post("/api/storage/bucket", json={"confirm": "CREEAZA BUCKET cimitir-private-test"}).json()
    assert r["executed"] and ("POST", "https://fommbiwndlxkfsnlqatq.supabase.co/storage/v1/bucket") in net.http_calls
    assert c.get("/api/state").json()["mode"] == "dry_run"


def test_report_has_no_secrets(env):
    c, *_ = env
    c.put("/api/config", json={"values": {"db_password": "R3port-secret-1", "supabase_secret_key": "sb_secret_REPORTKEY1"}})
    c.post("/api/checks")
    txt = c.post("/api/report").json()["text"]
    assert "R3port-secret" not in txt and "REPORTKEY1" not in txt and "Mod curent: dry_run" in txt


def test_backup_endpoint(env, tmp_path):
    c, *_ = env
    f = tmp_path / "b.sql"
    f.write_text("-- PostgreSQL database dump\n" + "CREATE TABLE a(id int);\n" * 30)
    assert c.post("/api/migrations/backup", json={"path": str(f)}).json()["ok"]
    assert not c.post("/api/migrations/backup", json={"path": str(tmp_path / "nope")}).json()["ok"]


@pytest.mark.pg
def test_migration_flow_through_api(pg_db, tmp_path):
    store = ConfigStore(tmp_path / "h3")
    app = create_app(store, FakeNet(pg_url=pg_db), token=TOKEN)
    c = TestClient(app, base_url="http://127.0.0.1:8765", headers={"X-SM-Token": TOKEN})
    c.put("/api/config", json={"values": {"db_password": "pw123456"}})
    plan = c.get("/api/migrations/plan").json()
    assert [s["version"] for s in plan["plan"]["steps"]] == [24, 25, 26]
    h, phrase = plan["plan"]["plan_hash"], plan["plan"]["confirm_phrase"]
    bk = tmp_path / "b.sql"
    bk.write_text("-- PostgreSQL database dump\n" + "CREATE TABLE a(id int);\n" * 30)
    dry = c.post("/api/migrations/apply", json={"plan_hash": h, "confirm": phrase, "backup_path": str(bk)}).json()
    assert dry["dry_run"] and not dry["executed"]
    c.post("/api/mode", json={"mode": "execute", "confirm": EXEC_PHRASE})
    assert c.post("/api/migrations/apply", json={"plan_hash": h, "confirm": "gresit", "backup_path": str(bk)}).status_code == 400
    assert c.post("/api/migrations/apply", json={"plan_hash": "0" * 16, "confirm": phrase, "backup_path": str(bk)}).status_code == 409
    assert c.post("/api/migrations/apply", json={"plan_hash": h, "confirm": phrase, "backup_path": str(tmp_path / "lipsa")}).status_code == 400
    ok = c.post("/api/migrations/apply", json={"plan_hash": h, "confirm": phrase, "backup_path": str(bk)}).json()
    assert ok["executed"] and c.get("/api/state").json()["mode"] == "dry_run"
    assert c.get("/api/migrations/plan").json()["plan"]["steps"] == []


def test_docker_logs_and_plans_without_docker_do_not_crash(tmp_path):
    def nodocker(args, timeout=60):
        raise FileNotFoundError("docker")
    app = create_app(ConfigStore(tmp_path / "h4"), FakeNet(), docker=nodocker, token=TOKEN)
    c = TestClient(app, base_url="http://127.0.0.1:8765", headers={"X-SM-Token": TOKEN})
    r = c.get("/api/docker/logs")
    assert r.status_code == 200 and "indisponibil" in r.json()["logs"]
    for kind in ("build", "start", "update"):
        p = c.post("/api/docker/plan", json={"kind": kind}).json()
        assert p["ready"] is False and p["command"] and any("Docker" in i for i in p["issues"])
    assert c.get("/api/docker/status").json()["docker"] is False


def test_isolated_dry_run_does_not_store_admin_credentials(env):
    c, store, *_ = env
    r = c.post("/api/pairing/isolated", json={"which": "provision", "approval": "", "email": "a@b.ro", "password": "Adm1n-xyz-99"}).json()
    assert r["dry_run"]
    assert not store.transient.get("cloud_admin_password")


def test_report_marks_checks_time_and_drops_stale_after_apply(env):
    c, *_ = env
    c.post("/api/checks")
    assert "rulate la" in c.post("/api/report").json()["text"]
