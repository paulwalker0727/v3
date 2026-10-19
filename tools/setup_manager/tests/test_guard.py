import pytest

from tools.setup_manager import guard

PROD = "txuxkjgftopznvmooexq"
OLD = "nhhczdmcocbobptogvsw"


def test_prod_ref_blocked_in_any_field(test_cfg):
    for key in ("supabase_url", "db_connection_string", "project_ref", "cloud_public_url", "supabase_secret_key", "db_username"):
        bad = dict(test_cfg, **{key: f"https://{PROD}.supabase.co"})
        with pytest.raises(guard.GuardError, match="PROD"):
            guard.assert_read_allowed(bad)
        with pytest.raises(guard.GuardError):
            guard.assert_write_allowed(bad)


def test_previous_test_project_preserved_read_ok_write_blocked(test_cfg):
    cfg = dict(test_cfg, project_ref=OLD, supabase_url=f"https://{OLD}.supabase.co", db_username=f"postgres.{OLD}",
               db_connection_string=f"postgresql://postgres.{OLD}@aws-0-eu-west-1.pooler.supabase.com:5432/postgres")
    guard.assert_read_allowed(cfg)
    with pytest.raises(guard.GuardError, match="PĂSTRAT"):
        guard.assert_write_allowed(cfg)


def test_valid_test_config_is_consistent_and_writable(test_cfg):
    assert guard.consistency_issues(test_cfg) == []
    guard.assert_write_allowed(test_cfg)


def test_inconsistencies_block_writes(test_cfg):
    cases = [dict(test_cfg, supabase_url="https://aaaaaaaaaaaaaaaaaaaa.supabase.co"),
             dict(test_cfg, db_username="postgres.aaaaaaaaaaaaaaaaaaaa", db_connection_string="postgresql://x@aws-0-eu-west-1.pooler.supabase.com:5432/postgres"),
             dict(test_cfg, db_connection_string="postgresql://postgres.fommbiwndlxkfsnlqatq@aws-0-eu-west-1.pooler.supabase.com:6543/postgres"),
             dict(test_cfg, storage_bucket="cimitir-private"), dict(test_cfg, project_ref="short")]
    for c in cases:
        assert guard.consistency_issues(c), c
        with pytest.raises(guard.GuardError):
            guard.assert_write_allowed(c)


def test_env_name_must_be_test(test_cfg):
    with pytest.raises(guard.GuardError):
        guard.assert_read_allowed(dict(test_cfg, env_name="PROD"))


@pytest.mark.parametrize("port", [8084, "8084", 80, 70000, "abc", None])
def test_bad_ports(port):
    with pytest.raises(guard.GuardError):
        guard.assert_port_ok(port)


def test_good_port():
    assert guard.assert_port_ok("8085") == 8085


@pytest.mark.parametrize("name", ["cimitir-cloud-etapa789-test", "cimitir-cloud-etapa789-test-prev", "cimitir-cloud-test", "bad name!", ""])
def test_protected_or_invalid_containers(name):
    with pytest.raises(guard.GuardError):
        guard.assert_container_ok(name)


def test_target_container_ok():
    assert guard.assert_container_ok("cimitir-cloud-sync-v2-main-test")


@pytest.mark.parametrize("tag", ["latest", "Bad Tag", "", "-x"])
def test_bad_tags(tag):
    with pytest.raises(guard.GuardError):
        guard.assert_tag_ok(tag)
