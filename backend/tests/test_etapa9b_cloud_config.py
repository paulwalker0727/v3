"""Configurare cloud Supabase: backend/.env(.example), .gitignore, scripts/check_cloud_config.py (fără secrete în ieșire), scurtături Windows."""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "shared", ROOT / "backend" / "cimitir"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import cimitir_storage as cs  # noqa: E402

KEYS = ["CIMITIR_MODE", "CIMITIR_DB_ENGINE", "DATABASE_URL", "SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_BUCKET", "CIMITIR_STORAGE_DRIVER"]


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def _kv(path):
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def test_env_example_has_the_cloud_section_without_secrets():
    text = (ROOT / "backend" / ".env.example").read_text(encoding="utf-8")
    assert "CONFIGURARE CLOUD - SUPABASE" in text
    kv = _kv(ROOT / "backend" / ".env.example")
    for k in KEYS:
        assert k in kv, k
    assert kv["CIMITIR_MODE"] == "online" and kv["CIMITIR_DB_ENGINE"] == "postgres"
    assert kv["CIMITIR_STORAGE_DRIVER"] == "supabase" and kv["SUPABASE_BUCKET"] == "cimitir-private"
    assert kv["DATABASE_URL"] == kv["SUPABASE_URL"] == kv["SUPABASE_SECRET_KEY"] == ""
    assert not re.search(r"postgres(ql)?://[^\s:]+:[^\s@]+@", re.sub(r"(?m)^#.*$", "", text))
    assert "sb_secret_" not in re.sub(r"(?m)^#.*$", "", text)


def test_real_env_has_the_cloud_section_and_is_not_tracked():
    has_section = "CONFIGURARE CLOUD - SUPABASE" in (ROOT / "backend" / ".env").read_text(encoding="utf-8")
    assert has_section, "backend/.env nu are secțiunea CONFIGURARE CLOUD - SUPABASE"
    assert _git("ls-files", "backend/.env").stdout.strip() == ""
    assert _git("check-ignore", "-q", "backend/.env").returncode == 0, "backend/.env nu este ignorat de Git"
    assert _git("check-ignore", "-q", "backend/.env.example").returncode == 1, ".env.example trebuie versionat"
    for name in (".env", "backend/.env.local", "backend/.env.production"):
        assert _git("check-ignore", "-q", name).returncode == 0, name


def test_storage_uses_secret_key_names_and_headers(monkeypatch):
    new = cs.SupabaseStorage("https://x.supabase.co", "sb_secret_abc", "b")
    assert new.headers == {"apikey": "sb_secret_abc"}
    legacy = cs.SupabaseStorage("https://x.supabase.co", "eyJ.a.b", "b")
    assert legacy.headers["Authorization"] == "Bearer eyJ.a.b" and legacy.headers["apikey"] == "eyJ.a.b"
    for k in ("SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CIMITIR_STORAGE_DRIVER", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_BUCKET", "b")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "sb_secret_legacyname")
    assert cs.build_storage().headers == {"apikey": "sb_secret_legacyname"}
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_preferred")
    assert cs.build_storage().headers == {"apikey": "sb_secret_preferred"}
    monkeypatch.delenv("SUPABASE_SECRET_KEY")
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY")
    with pytest.raises(cs.StorageError):
        cs.build_storage()


def test_ensure_bucket_creates_only_a_private_bucket_and_never_touches_existing(monkeypatch):
    st = cs.SupabaseStorage("https://x.supabase.co", "sb_secret_abc", "cimitir-private")
    calls, state = [], {"exists": False}

    class R:
        def __init__(self, data): self._d = data
        def json(self): return self._d

    def fake(method, url, key="", **kw):
        calls.append((method, url.rsplit("/storage/v1", 1)[1], kw.get("json")))
        if method == "GET":
            if not state["exists"]:
                raise cs.NotFound(key)
            return R({"public": False})
        state["exists"] = True
        return R({})
    monkeypatch.setattr(st, "_req", fake)
    assert st.ensure_bucket() == "created"
    assert calls[1] == ("POST", "/bucket", {"id": "cimitir-private", "name": "cimitir-private", "public": False})
    calls.clear()
    assert st.ensure_bucket() == "exists" and all(m == "GET" for m, _, _ in calls)


# ------------------------------------------------------- scripts/check_cloud_config.py
SECRETS = ["Sup3rSecretPassw0rd", "sb_secret_TOPSECRETVALUE123", "projsecretref789"]


def _check(tmp_path, env_lines, extra=()):
    envfile = tmp_path / "cloud.env"
    envfile.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SUPABASE_", "CIMITIR_")) and k != "DATABASE_URL"}
    env.update(CIMITIR_ENV_FILE=str(envfile), CIMITIR_IGNORE_PROJECT_ENV="1", CIMITIR_DATA_DIR=str(tmp_path / "d"))
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_cloud_config.py"), *extra], capture_output=True, text=True,
                       env=env, cwd=ROOT, timeout=90)
    return p.returncode, p.stdout + p.stderr


def test_check_script_empty_config_reports_neconfigurat(tmp_path):
    code, out = _check(tmp_path, ["CIMITIR_MODE=online", "CIMITIR_DB_ENGINE=postgres", "DATABASE_URL=", "SUPABASE_URL=", "SUPABASE_SECRET_KEY=",
                                  "SUPABASE_BUCKET=cimitir-private", "CIMITIR_STORAGE_DRIVER=supabase"])
    assert code == 1
    for line in ("DATABASE_URL: NECONFIGURAT", "PostgreSQL: NECONFIGURAT", "SUPABASE_URL: NECONFIGURAT",
                 "SUPABASE_SECRET_KEY: NECONFIGURAT", "SUPABASE_BUCKET: CONFIGURAT", "Storage: NECONFIGURAT"):
        assert line in out, out


def test_check_script_never_prints_secrets_and_flags_problems(tmp_path):
    code, out = _check(tmp_path, [
        "CIMITIR_MODE=online", "CIMITIR_DB_ENGINE=postgres", f"DATABASE_URL=postgresql://postgres:{SECRETS[0]}@127.0.0.1:9/postgres",
        f"SUPABASE_URL=http://127.0.0.1:9/{SECRETS[2]}", f"SUPABASE_SECRET_KEY={SECRETS[1]}", "SUPABASE_BUCKET=cimitir-private",
        "CIMITIR_STORAGE_DRIVER=supabase"])
    assert code == 1
    for s in SECRETS + ["postgresql://", "127.0.0.1:9"]:
        assert s not in out, f"ieșirea conține {s!r}"
    for line in ("DATABASE_URL: CONFIGURAT", "PostgreSQL: OFFLINE", "SUPABASE_URL: CONFIGURAT", "SUPABASE_SECRET_KEY: CONFIGURAT",
                 "SUPABASE_BUCKET: CONFIGURAT", "Storage: OFFLINE"):
        assert line in out, out
    assert "bază locală" in out or "local" in out.lower()


def test_check_script_warns_on_transaction_pooler_and_missing_tls(tmp_path):
    _, out = _check(tmp_path, ["CIMITIR_MODE=online", "CIMITIR_DB_ENGINE=postgres",
                               f"DATABASE_URL=postgresql://postgres.{SECRETS[2]}:{SECRETS[0]}@aws-0-eu.pooler.supabase.com:6543/postgres"])
    assert "6543" in out and "TLS" in out and SECRETS[0] not in out and SECRETS[2] not in out


# ------------------------------------------------------- scurtături Windows
def test_windows_shortcuts_exist_and_are_safe():
    d = ROOT / "tools" / "windows"
    for name in ("CONFIGURE_CLOUD.bat", "CHECK_CLOUD_CONFIG.bat", "RESTART_BACKEND.bat"):
        raw = (d / name).read_bytes()
        raw.decode("ascii")
        assert b"\r\n" in raw and not re.search(rb"(?i)postgres(ql)?://|sb_secret_|password\s*=", raw), name
    assert "notepad" in (d / "CONFIGURE_CLOUD.bat").read_text().lower() and "backend\\.env" in (d / "CONFIGURE_CLOUD.bat").read_text()
    assert "check_cloud_config.py" in (d / "CHECK_CLOUD_CONFIG.bat").read_text()
    restart = (d / "RESTART_BACKEND.bat").read_text()
    assert "uvicorn" in restart and "server:app" in restart and "START_SERVER.bat" in restart
