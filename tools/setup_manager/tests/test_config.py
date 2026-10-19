import json

from tools.setup_manager import redact
from tools.setup_manager.config import ConfigStore, build_db_url


def test_password_in_url_moves_to_secret_and_is_not_saved(tmp_path):
    s = ConfigStore(tmp_path)
    s.update({"db_connection_string": "postgresql://postgres.fommbiwndlxkfsnlqatq:Pa%24%24w0rd@aws-0-eu-west-1.pooler.supabase.com:5432/postgres"})
    assert s.secrets["db_password"] == "Pa$$w0rd"
    assert ":" not in s.values["db_connection_string"].split("@")[0].split("//")[1]
    saved = (tmp_path / "config.json").read_text()
    assert "w0rd" not in saved


def test_secrets_never_in_config_file_and_masked_in_public(tmp_path):
    s = ConfigStore(tmp_path)
    s.update({"supabase_secret_key": "sb_secret_TOPSECRET123", "db_password": "dbpass-123", "cloud_admin_password": "adm-pass-1"})
    assert all("TOPSECRET" not in p.read_text() and "dbpass" not in p.read_text() and "adm-pass" not in p.read_text() for p in tmp_path.rglob("*") if p.is_file())
    pub = json.dumps(s.public())
    assert "TOPSECRET" not in pub and "dbpass" not in pub and "adm-pass" not in pub
    assert s.public()["supabase_secret_key"] == {"set": True}
    assert redact.redact("x sb_secret_TOPSECRET123 y") == "x *** y"


def test_keep_sentinel_and_clear(tmp_path):
    s = ConfigStore(tmp_path)
    s.update({"db_password": "abcd1234"})
    s.update({"db_password": "__KEEP__"})
    assert s.secrets["db_password"] == "abcd1234"
    s.update({"db_password": ""})
    assert "db_password" not in s.secrets


def test_env_name_cannot_be_changed(tmp_path):
    s = ConfigStore(tmp_path)
    s.update({"env_name": "PROD"})
    assert s.values["env_name"] == "TEST"


def test_persist_secrets_refused_off_windows(tmp_path):
    import sys
    import pytest
    s = ConfigStore(tmp_path)
    if sys.platform != "win32":
        with pytest.raises(ValueError):
            s.update({"persist_secrets": True})


def test_build_db_url_quotes_and_adds_sslmode():
    url = build_db_url({"db_connection_string": "postgresql://x@aws-0-eu-west-1.pooler.supabase.com:5432/postgres", "db_username": "postgres.abc", "db_password": "p@ss/w:rd"})
    assert url.startswith("postgresql://postgres.abc:p%40ss%2Fw%3Ard@aws-0-eu-west-1.pooler.supabase.com:5432/postgres") and "sslmode=require" in url


def test_defaults_match_requirements(tmp_path):
    v = ConfigStore(tmp_path).values
    assert v["project_ref"] == "fommbiwndlxkfsnlqatq" and v["storage_bucket"] == "cimitir-private-test" and v["docker_port"] == 8085
    assert v["container_name"] == "cimitir-cloud-sync-v2-main-test" and "aws-0-eu-west-1.pooler.supabase.com:5432" in v["db_connection_string"]
