"""Igienă patch: fără secrete, fără .env reale, fără comenzi distructive Docker/SQL în cod, fără apeluri către terți."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SM = ROOT / "tools" / "setup_manager"
SECRET_PATTERNS = [re.compile(p) for p in (r"eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.", r"sb_secret_[A-Za-z0-9]{8,}", r"postgres(?:ql)?://[^:/@\s\"']+:[^@\s\"'*{<]+@")]


def _files(only_src=False):
    out = []
    for p in list(SM.rglob("*")) + [ROOT / "setup-test.ps1"]:
        if p.is_file() and "__pycache__" not in p.parts and p.suffix in (".py", ".js", ".html", ".css", ".md", ".ps1", ".ini", ".txt"):
            if only_src and "tests" in p.parts:
                continue
            out.append(p)
    return out


def test_no_secrets_in_sources_or_tests():
    for p in _files():
        if p.name == "test_hygiene.py":
            continue
        txt = p.read_text(encoding="utf-8")
        for rx in (SECRET_PATTERNS if "tests" not in p.parts else SECRET_PATTERNS[:1]):
            assert not rx.search(txt), f"posibil secret în {p}"


def test_no_env_db_or_backup_files_shipped():
    bad = [p for p in SM.rglob("*") if p.is_file() and (p.name.startswith(".env") or p.suffix in (".db", ".sqlite", ".dump", ".bak", ".zip", ".sql"))]
    assert not bad, bad


def test_no_destructive_commands_in_code():
    code = "\n".join(p.read_text(encoding="utf-8") for p in _files(only_src=True) if p.suffix in (".py", ".ps1"))
    for needle in ("compose down", "system prune", "volume rm", "docker kill", "DROP TABLE", "DROP DATABASE", "TRUNCATE", "DELETE FROM"):
        if needle in code:
            lines = [ln for ln in code.splitlines() if needle in ln]
            assert all(("fără" in ln.lower() or "interzis" in ln.lower() or ln.strip().startswith("#")) for ln in lines), (needle, lines)


def test_server_binds_loopback_only():
    main = (SM / "__main__.py").read_text(encoding="utf-8")
    assert 'host="127.0.0.1"' in main and "0.0.0.0" not in main


def test_no_third_party_hosts_in_code():
    code = "\n".join(p.read_text(encoding="utf-8") for p in SM.glob("*.py"))
    hosts = set(re.findall(r"https?://([a-zA-Z0-9.-]+)", code))
    allowed = {"127.0.0.1", "localhost"}
    assert all(h in allowed or "supabase" in h or h.endswith("example.com") for h in hosts), hosts


def test_static_assets_have_no_external_urls():
    for n in ("index.html", "app.js", "app.css"):
        txt = (SM / "static" / n).read_text(encoding="utf-8")
        assert not re.search(r"(src|href)=\"https?://|fetch\(\"https?://|@import url\(", txt)


def test_gitignore_blocks_local_state():
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "CimitirSetupManager" in gi and "setup_manager_home" in gi
