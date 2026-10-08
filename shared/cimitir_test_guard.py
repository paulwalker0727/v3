"""Protecție anti-pytest pe producție: pytest se oprește (FAIL FAST) înainte de colectare, deci înainte de orice scriere.

Reguli:
- Proiectul de producție (PROTECTED_PROJECT_REFS) este BLOCAT PERMANENT: în DATABASE_URL, SUPABASE_URL, CIMITIR_TEST_*,
  în mediu sau în fișierele .env, indiferent de orice altă variabilă. Nu există ocolire.
- Un proiect Supabase de TEST se acceptă doar dacă TOATE: CIMITIR_LIVE_TESTS=1, CIMITIR_TEST_PROJECT_REF=<ref>, ref-ul nu e protejat,
  iar DATABASE_URL (utilizator `*.<ref>`, host pooler, sslmode=require) și SUPABASE_URL (`<ref>.supabase.co`) indică exact acel ref.
- O bază PostgreSQL non-Supabase de test se acceptă doar prin CIMITIR_TEST_DATABASE_URL, cu „test” în numele bazei.
- Testele „live” (care lovesc un server pornit) sunt SKIP fără un mediu de test valid.
Mesajele conțin doar sursa și numele cheii, niciodată valori.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

PROTECTED_PROJECT_REFS = ("txuxkjgftopznvmooexq",)
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
CHECKED_KEYS = ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SECRET_KEY", "CIMITIR_TEST_DATABASE_URL", "CIMITIR_TEST_PROJECT_REF", "CIMITIR_LIVE_TESTS")
LIVE_MARKERS = ("localhost:8001", "127.0.0.1:8001", "ETAPA6_SERVER_URL", "_server_env(", "CIMITIR_TEST_BASE_URL")
EXIT_CODE = 4
_REF_RE = re.compile(r"^[a-z]{20}$")
_USER_REF_RE = re.compile(r"\.([a-z]{20})$")
_SB_URL_RE = re.compile(r"^([a-z]{20})\.supabase\.co$")


def parse_env_text(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, value = s.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _user(url: str) -> str:
    try:
        return (urlparse(url).username or "").lower()
    except ValueError:
        return ""


def _query(url: str) -> str:
    try:
        return (urlparse(url).query or "").lower()
    except ValueError:
        return ""


def _db_name(url: str) -> str:
    try:
        return urlparse(url).path.lstrip("/").lower()
    except ValueError:
        return ""


def _is_supabase_host(host: str) -> bool:
    return host.endswith((".supabase.com", ".supabase.co", ".supabase.net")) or host in ("supabase.com", "supabase.co")


def has_protected_ref(value: str) -> bool:
    low = (value or "").lower()
    return any(ref in low for ref in PROTECTED_PROJECT_REFS)


def valid_test_ref(ref: str) -> bool:
    ref = (ref or "").strip()
    return bool(_REF_RE.match(ref)) and not has_protected_ref(ref)


def database_url_issue(url: str, test_ref: str = "") -> str:
    """Motivul pentru care un DATABASE_URL nu e permis la teste ('' = permis). `test_ref` = ref-ul de test validat și activat."""
    url = (url or "").strip()
    if not url or url.lower().startswith("sqlite"):
        return ""
    if has_protected_ref(url):
        return "indică proiectul Supabase de producție (blocat permanent)"
    host = _host(url)
    if _is_supabase_host(host):
        if not test_ref:
            return "indică o gazdă Supabase fără CIMITIR_LIVE_TESTS=1 și CIMITIR_TEST_PROJECT_REF valid"
        m = _USER_REF_RE.search(_user(url))
        if not m or m.group(1) != test_ref:
            return "utilizatorul nu corespunde exact proiectului de test"
        if not host.startswith("aws-") or "pooler.supabase.com" not in host:
            return "trebuie să fie Session Pooler Supabase"
        if "sslmode=require" not in _query(url) and "sslmode=verify" not in _query(url):
            return "lipsește sslmode=require"
        return ""
    if host not in LOOPBACK_HOSTS:
        return "indică o bază de date remote (doar CIMITIR_TEST_DATABASE_URL e permis)"
    return ""


def check_test_database_url(url: str, test_ref: str = "") -> str:
    url = (url or "").strip()
    if not url:
        return "lipsește"
    if has_protected_ref(url):
        return "indică proiectul Supabase de producție (blocat permanent)"
    if _is_supabase_host(_host(url)):
        return database_url_issue(url, test_ref)
    if "test" not in _db_name(url):
        return "numele bazei trebuie să conțină „test”"
    return ""


def _scan(values: Dict[str, str], label: str, test_url: str, test_url_valid: bool, test_ref: str) -> List[str]:
    issues: List[str] = []
    for key in CHECKED_KEYS:
        if has_protected_ref(values.get(key) or ""):
            issues.append(f"{label}: {key} conține proiectul de producție (blocat permanent)")
    if issues:
        return issues
    db = (values.get("DATABASE_URL") or "").strip()
    if db and not (test_url_valid and db == test_url):
        reason = database_url_issue(db, test_ref)
        if reason:
            issues.append(f"{label}: DATABASE_URL {reason}")
    sb = (values.get("SUPABASE_URL") or "").strip()
    if sb:
        m = _SB_URL_RE.match(_host(sb))
        if not (test_ref and m and m.group(1) == test_ref):
            issues.append(f"{label}: SUPABASE_URL nu indică proiectul de test activat")
    if (values.get("SUPABASE_SECRET_KEY") or "").strip() and not test_ref:
        issues.append(f"{label}: SUPABASE_SECRET_KEY este configurată fără un proiect de test activat")
    return issues


def collect_sources(env, root: Path) -> List[tuple]:
    sources = []
    files = [root / ".env", root / "backend" / ".env"]
    for var in ("CIMITIR_ENV_FILE", "CIMITIR_CLOUD_ENV_FILE"):
        raw = (env.get(var) or "").strip()
        if raw:
            files.append(Path(raw))
    for f in files:
        try:
            sources.append((f"fișier {f.name} ({f.parent.name})", parse_env_text(f.read_text(encoding="utf-8"))))
        except OSError:
            continue
    sources.append(("mediu", {k: env.get(k, "") for k in CHECKED_KEYS}))
    return sources


def _effective(sources, key: str) -> str:
    """Mediul are prioritate; altfel ultima valoare din fișiere."""
    value = ""
    for _, values in sources:
        if (values.get(key) or "").strip():
            value = values[key].strip()
    return value


def active_test_ref(sources) -> str:
    """Ref-ul de test activat (altfel ''): LIVE=1 + ref valid, neprotejat."""
    if _effective(sources, "CIMITIR_LIVE_TESTS") != "1":
        return ""
    ref = _effective(sources, "CIMITIR_TEST_PROJECT_REF")
    return ref if valid_test_ref(ref) else ""


def find_issues(env=None, root: Optional[Path] = None) -> List[str]:
    env = os.environ if env is None else env
    root = root or Path(__file__).resolve().parents[1]
    sources = collect_sources(env, root)
    test_ref = active_test_ref(sources)
    issues: List[str] = []
    if _effective(sources, "CIMITIR_LIVE_TESTS") == "1" and not test_ref:
        issues.append("CIMITIR_LIVE_TESTS=1 fără CIMITIR_TEST_PROJECT_REF valid (sau ref-ul e cel de producție)")
    ref_cfg = _effective(sources, "CIMITIR_TEST_PROJECT_REF")
    if ref_cfg and not valid_test_ref(ref_cfg):
        issues.append("CIMITIR_TEST_PROJECT_REF este invalid sau protejat")
    test_url = _effective(sources, "CIMITIR_TEST_DATABASE_URL")
    test_url_valid = False
    if test_url:
        reason = check_test_database_url(test_url, test_ref)
        if reason:
            issues.append(f"CIMITIR_TEST_DATABASE_URL {reason}")
        else:
            test_url_valid = True
    for label, values in sources:
        issues += _scan(values, label, test_url, test_url_valid, test_ref)
    return issues


def enforce_or_exit(env=None, root: Optional[Path] = None) -> None:
    issues = find_issues(env, root)
    if not issues:
        return
    import pytest
    msg = ("PYTEST REFUZAT (protecție producție): mediul indică producția sau o bază neconfirmată ca test.\n  - "
           + "\n  - ".join(issues)
           + "\nProiectul de producție e blocat permanent. Folosește SQLite (implicit) sau un proiect de test activat cu "
             "CIMITIR_LIVE_TESTS=1 + CIMITIR_TEST_PROJECT_REF. Nu există variabilă de ocolire.")
    pytest.exit(msg, returncode=EXIT_CODE)


def live_allowed(env=None, root: Optional[Path] = None) -> bool:
    env = os.environ if env is None else env
    root = root or Path(__file__).resolve().parents[1]
    sources = collect_sources(env, root)
    if find_issues(env, root):
        return False
    if active_test_ref(sources):
        return bool((env.get("ETAPA6_SERVER_URL") or "").strip())
    test_url = _effective(sources, "CIMITIR_TEST_DATABASE_URL")
    return bool(test_url) and not check_test_database_url(test_url) and bool((env.get("ETAPA6_SERVER_URL") or "").strip())


def is_live_source(text: str) -> bool:
    return any(m in text for m in LIVE_MARKERS)


def skip_live_items(items, env=None) -> None:
    if live_allowed(env):
        return
    import pytest
    marker = pytest.mark.skip(reason="test live: lipsește un mediu separat de test (CIMITIR_LIVE_TESTS=1 + proiect de test, sau CIMITIR_TEST_DATABASE_URL, + ETAPA6_SERVER_URL)")
    cache: Dict[str, bool] = {}
    for item in items:
        path = str(getattr(item, "path", None) or getattr(item, "fspath", ""))
        if path not in cache:
            try:
                cache[path] = is_live_source(Path(path).read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                cache[path] = False
        if cache[path]:
            item.add_marker(marker)
