import pytest

from tools.setup_manager import guard, pairing
from tools.setup_manager.tests.conftest import FakeNet


def _net(ok_all=True):
    n = FakeNet()
    if ok_all:
        for p in ("/healthz", "/api/v1/health", "/api/v1/admin/status", "/mobile/", "/mobile/manifest.webmanifest", "/mobile/sw.js", "/admin/"):
            n.http_map[p] = (200, b"{}")
        n.http_map["/api/v1/sync-v2/status"] = (401, b"{}")
        for name in ("mobile_app.js", "mobile_offline.js", "mobile_photos.js", "jsQR.js", "mobile_app.css", "mobile-icon-192.png", "mobile-icon-512.png"):
            n.http_map["/mobile-assets/" + name] = (200, b"x")
    return n


def test_probe_all_pass(test_cfg):
    res = pairing.probe(test_cfg, _net())
    assert all(r["status"] == "PASS" for r in res), res


def test_probe_detects_missing_mobile_assets(test_cfg):
    n = _net()
    n.http_map["/mobile-assets/mobile_photos.js"] = (404, b"")
    res = {r["id"]: r for r in pairing.probe(test_cfg, n)}
    assert res["mobile_assets"]["status"] == "FAIL" and "mobile_photos.js" in res["mobile_assets"]["detail"]


def test_probe_sync_guard_requires_401(test_cfg):
    n = _net()
    n.http_map["/api/v1/sync-v2/status"] = (200, b"{}")
    assert {r["id"]: r for r in pairing.probe(test_cfg, n)}["sync_guard"]["status"] == "FAIL"


def test_probe_cloud_down(test_cfg):
    n = FakeNet()
    n.http_map["/healthz"] = ConnectionRefusedError()
    res = pairing.probe(test_cfg, n)
    assert res[0]["status"] == "FAIL" and "indisponibil" in res[0]["detail"]


def test_probe_refuses_prod_url(test_cfg):
    with pytest.raises(guard.GuardError):
        pairing.probe(dict(test_cfg, main_cloud_url="https://txuxkjgftopznvmooexq.example.com"), _net())


class FakeProc:
    def __init__(self, lines, rc=0):
        self.stdout, self.rc = iter(lines), rc

    def wait(self):
        return self.rc

    def kill(self):
        pass


def _iso(cfg, **kw):
    out = []
    args = dict(which="provision", approval=pairing.APPROVAL_PHRASE, mode="execute", org_uid="", emit=out.append)
    args.update(kw)
    return pairing.run_isolated(cfg, **args), out


def _cfg(c):
    return dict(c, cloud_admin_email="admin@test.ro", cloud_admin_password="Adm1n-Pass-xyz")


def test_isolated_dry_run_does_not_execute(test_cfg):
    called = []
    r, _ = _iso(_cfg(test_cfg), mode="dry_run", runner=lambda *a, **k: called.append(1))
    assert r["dry_run"] and not r["executed"] and not called


def test_isolated_requires_approval_and_credentials(test_cfg):
    with pytest.raises(guard.GuardError, match="Aprobare"):
        _iso(_cfg(test_cfg), approval="da")
    with pytest.raises(guard.GuardError, match="credențialele|e-mailul"):
        _iso(test_cfg)
    with pytest.raises(guard.GuardError, match="UID"):
        _iso(_cfg(test_cfg), which="sync", org_uid="")
    with pytest.raises(guard.GuardError, match="necunoscut"):
        _iso(_cfg(test_cfg), which="rm -rf")


@pytest.mark.parametrize("url", ["http://127.0.0.1:8084", "http://127.0.0.1:9999", "http://evil.example.com", "https://txuxkjgftopznvmooexq.supabase.co"])
def test_isolated_only_on_configured_test_cloud(test_cfg, url):
    cfg = _cfg(test_cfg)
    cfg["main_cloud_url"] = url
    with pytest.raises(guard.GuardError):
        _iso(cfg, runner=lambda *a, **k: FakeProc([]))


def test_isolated_runs_script_with_env_and_counts(test_cfg):
    captured = {}

    def runner(cmd, env=None, **kw):
        captured["cmd"], captured["env"] = cmd, env
        return FakeProc(["PASS 1 asociere\n", "PASS 2 izolare password=Adm1n-Pass-xyz\n", "SKIP 3\n"], 0)
    r, out = _iso(_cfg(test_cfg), runner=runner)
    assert r["ok"] and r["counts"] == {"PASS": 2, "FAIL": 0, "SKIP": 1}
    assert captured["cmd"][-1] == "--yes-write-test-data" and captured["cmd"][1].endswith("verify_provision_live.py")
    assert captured["env"]["CLOUD_URL"] == "http://127.0.0.1:8085" and captured["env"]["CIMITIR_LIVE_ADMIN_PASSWORD"] == "Adm1n-Pass-xyz"
    assert not any("Adm1n-Pass-xyz" in line for line in out)


def test_isolated_failure_reported(test_cfg):
    r, _ = _iso(_cfg(test_cfg), runner=lambda *a, **k: FakeProc(["FAIL 1 x\n"], 1))
    assert not r["ok"] and r["counts"]["FAIL"] == 1


def test_checklist_persistence_and_validation(tmp_path):
    cl = pairing.Checklist(tmp_path)
    cl.set("offline", "pass", "ok, parola=Sup3rSecret")
    v = {s["id"]: s for s in cl.view()}
    assert v["offline"]["status"] == "pass" and v["conflicts"]["status"] == "pending"
    assert "Sup3rSecret" not in v["offline"]["note"]
    with pytest.raises(ValueError):
        cl.set("nope", "pass")
    with pytest.raises(ValueError):
        cl.set("offline", "maybe")
    assert {s["id"] for s in pairing.MANUAL_STEPS} >= {"main_config", "mobile_open", "sync_entities", "sync_docs", "conflicts", "offline"}


def test_snippet_has_no_secrets_and_mentions_mobile_https(test_cfg):
    s = pairing.main_config_snippet(test_cfg)
    assert "127.0.0.1:8085" in s and "HTTPS" in s
