import json
import os

import pytest

from tools.setup_manager import dockermgr as D, guard

IMG = "cimitir-cloud:t1"


def _docker(containers=None, images=True, env=None, fail_run=False, calls=None):
    containers = containers or []
    calls = calls if calls is not None else []

    def fn(args, timeout=60):
        calls.append(list(args))
        if args[0] == "version":
            return 0, "27.0.1\n", ""
        if args[0] == "ps":
            return 0, "\n".join(json.dumps(c) for c in containers), ""
        if args[0] == "images":
            return 0, (f"{IMG}  1 min ago\n" if images else ""), ""
        if args[0] == "inspect":
            return 0, json.dumps([{"Config": {"Env": env or ["PATH=/x", "BOOTSTRAP_TOKEN=tok-12345678"]}}]), ""
        if args[0] == "run":
            return (1, "", "boom") if fail_run else (0, "abc123\n", "")
        return 0, "", ""
    return fn, calls


def _c(name, ports="", image=IMG):
    return {"Names": name, "Image": image, "Status": "Up 2 hours", "Ports": ports}


@pytest.mark.parametrize("args", [["compose", "down"], ["system", "prune", "-f"], ["volume", "rm", "x"], ["rmi", "x"], ["kill", "x"], ["network", "rm", "x"],
                                  ["run", "-v", "/:/x", "img"], ["run", "--privileged", "img"], ["exec", "x"], ["rm", "-f", "cimitir-cloud-etapa789-test"], ["rm", "-v", "x"]])
def test_forbidden_docker_commands_refused_before_subprocess(args):
    with pytest.raises(guard.GuardError):
        D.run_docker(args)


def test_rm_allowed_only_for_container_created_in_operation(monkeypatch):
    seen = []
    monkeypatch.setattr(D.subprocess, "run", lambda *a, **k: type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})() if seen.append(a[0]) is None else None)
    with pytest.raises(guard.GuardError):
        D.run_docker(["rm", "-f", "someone-else"])
    D._created.add("mine")
    D.run_docker(["rm", "-f", "mine"])
    D._created.discard("mine")
    assert seen == [["docker", "rm", "-f", "mine"]]


def test_status_flags_protected_8084_container(test_cfg):
    fn, _ = _docker([_c("cimitir-cloud-etapa789-test", "127.0.0.1:8084->8080/tcp"), _c("other", "0.0.0.0:9000->8080/tcp")])
    st = D.status(test_cfg, fn)
    by = {c["name"]: c for c in st["containers"]}
    assert by["cimitir-cloud-etapa789-test"]["protected"] and not by["other"]["protected"]
    assert st["docker"] and st["port"] == 8085


def test_status_when_docker_missing(test_cfg):
    def nofn(args, timeout=60):
        raise FileNotFoundError("docker")
    st = D.status(test_cfg, nofn)
    assert st["docker"] is False and "indisponibil" in st["error"]


def test_plan_refuses_port_8084_and_protected_name(test_cfg):
    fn, _ = _docker()
    with pytest.raises(guard.GuardError):
        D.plan(dict(test_cfg, docker_port=8084), "start", fn)
    with pytest.raises(guard.GuardError):
        D.plan(dict(test_cfg, container_name="cimitir-cloud-etapa789-test"), "update", fn)


def _full(test_cfg, **kw):
    return dict(test_cfg, db_password="dbpw-123456", supabase_secret_key="sb_secret_abcdef123", runtime_db_url="", **kw)


def test_plan_start_ready_and_env_keys_only(test_cfg):
    fn, _ = _docker()
    p = D.plan(_full(test_cfg), "start", fn)
    assert p["issues"] == [] and p["ready"], p
    keys = {k["key"] for k in p["env_keys"]}
    assert {"CIMITIR_CP_DATABASE_URL", "SUPABASE_URL", "SUPABASE_BUCKET", "CIMITIR_CLOUD_PROJECT_REF", "SUPABASE_SECRET_KEY"} <= keys
    assert "dbpw-123456" not in json.dumps(p) and "sb_secret_abcdef123" not in json.dumps(p)
    assert any("ADMINISTRATIVE" in w for w in p["warnings"])
    assert "127.0.0.1:8085:8080" in p["command"] and "--read-only" in p["command"]


def test_plan_start_blocked_when_exists_port_busy_no_image(test_cfg):
    fn, _ = _docker([_c("cimitir-cloud-sync-v2-main-test", "127.0.0.1:8085->8080/tcp")], images=False)
    p = D.plan(_full(test_cfg), "start", fn)
    assert not p["ready"] and any("există deja" in i for i in p["issues"]) and any("Imaginea" in i for i in p["issues"])


def test_plan_env_inconsistent_with_ref_is_blocked(test_cfg):
    fn, _ = _docker()
    p = D.plan(_full(test_cfg, source_container="src", project_ref="aaaaaaaaaaaaaaaaaaaa"), "start", fn)
    assert not p["ready"]


def test_prod_in_source_env_blocked(test_cfg):
    env = ["EXTRA_URL=https://txuxkjgftopznvmooexq.supabase.co"]
    fn, _ = _docker(env=env)
    p = D.plan(_full(test_cfg, source_container="src"), "start", fn)
    assert not p["ready"] and any("PROD" in i for i in p["issues"])


def test_compose_env_overrides_source_and_skips_system_vars(test_cfg):
    env, src, _ = D.compose_env(_full(test_cfg), {"SUPABASE_BUCKET": "old", "KEEP_ME": "1"})
    assert env["SUPABASE_BUCKET"] == "cimitir-private-test" and env["KEEP_ME"] == "1" and src["KEEP_ME"].startswith("copiat")
    assert env["PORT"] == "8080"


def test_preflight_matches_repo_preflight():
    try:
        import cimitir_cloud_app as app
    except Exception:
        pytest.skip("cimitir_cloud_app indisponibil (dependențe Cloud neinstalate)")
    good = {"CIMITIR_CP_DATABASE_URL": "postgresql://u@h/db", "SUPABASE_URL": "https://aaaaaaaaaaaaaaaaaaaa.supabase.co", "SUPABASE_BUCKET": "b",
            "CIMITIR_CLOUD_PROJECT_REF": "aaaaaaaaaaaaaaaaaaaa", "SUPABASE_SECRET_KEY": "k"}
    for env in (good, {**good, "SUPABASE_URL": "https://bbbbbbbbbbbbbbbbbbbb.supabase.co"}, {**good, "CIMITIR_CLOUD_PROJECT_REF": "X"},
                {k: v for k, v in good.items() if k != "SUPABASE_BUCKET"}, {k: v for k, v in good.items() if k != "SUPABASE_SECRET_KEY"}):
        assert bool(D.preflight_env(env)) == bool(app.preflight(env)), env


def test_execute_start_runs_and_removes_env_file(test_cfg, monkeypatch):
    fn, calls = _docker()
    envfiles = []
    real = D._write_env_file
    monkeypatch.setattr(D, "_write_env_file", lambda e: envfiles.append(real(e)) or envfiles[-1])
    r = D.execute(_full(test_cfg), "start", fn, health=lambda port: True)
    assert r["ok"] and envfiles and not os.path.exists(envfiles[0])
    run = next(c for c in calls if c[0] == "run")
    assert "127.0.0.1:8085:8080" in run and "cimitir-cloud-sync-v2-main-test" in run and "8084" not in " ".join(run)
    assert all(c[0] not in ("rm", "stop", "kill") for c in calls)


def test_execute_update_keeps_old_as_prev_and_never_removes_it(test_cfg):
    fn, calls = _docker([_c("cimitir-cloud-sync-v2-main-test", "127.0.0.1:8085->8080/tcp")])
    r = D.execute(_full(test_cfg), "update", fn, health=lambda p: True)
    assert r["ok"]
    cmds = [c[0] for c in calls]
    assert ["rename", "cimitir-cloud-sync-v2-main-test", "cimitir-cloud-sync-v2-main-test-prev"] in calls
    assert "rm" not in cmds
    assert not any(c[0] in ("stop", "rename") and ("cimitir-cloud-etapa789-test" in c) for c in calls)


def test_execute_update_rolls_back_when_unhealthy(test_cfg):
    fn, calls = _docker([_c("cimitir-cloud-sync-v2-main-test", "127.0.0.1:8085->8080/tcp")])
    r = D.execute(_full(test_cfg), "update", fn, health=lambda p: False)
    assert not r["ok"]
    assert ["rename", "cimitir-cloud-sync-v2-main-test-prev", "cimitir-cloud-sync-v2-main-test"] in calls
    assert calls[-1] == ["start", "cimitir-cloud-sync-v2-main-test"]
    rm = [c for c in calls if c[0] == "rm"]
    assert rm == [["rm", "-f", "cimitir-cloud-sync-v2-main-test"]]         # doar containerul nou, creat în această operație


def test_execute_update_run_failure_restores_old(test_cfg):
    fn, calls = _docker([_c("cimitir-cloud-sync-v2-main-test", "127.0.0.1:8085->8080/tcp")], fail_run=True)
    with pytest.raises(RuntimeError, match="restaurată"):
        D.execute(_full(test_cfg), "update", fn, health=lambda p: True)
    assert calls[-1] == ["start", "cimitir-cloud-sync-v2-main-test"] and all(c[0] != "rm" for c in calls)


def test_update_refused_when_prev_exists(test_cfg):
    fn, _ = _docker([_c("cimitir-cloud-sync-v2-main-test", "127.0.0.1:8085->8080/tcp"), _c("cimitir-cloud-sync-v2-main-test-prev")])
    p = D.plan(_full(test_cfg), "update", fn)
    assert not p["ready"] and any("-prev" in i for i in p["issues"])


def test_execute_blocked_for_prod(test_cfg):
    fn, calls = _docker()
    with pytest.raises(guard.GuardError):
        D.execute(_full(test_cfg, supabase_url="https://txuxkjgftopznvmooexq.supabase.co"), "start", fn, health=lambda p: True)
    assert calls == []


def test_logs_are_redacted(test_cfg):
    from tools.setup_manager import redact
    redact.register("supersecretvalue")
    assert "supersecretvalue" not in redact.redact("error with supersecretvalue in dsn postgresql://u:supersecretvalue@h/db")
