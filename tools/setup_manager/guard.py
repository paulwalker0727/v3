"""Protecții obligatorii: PROD blocat în cod, proiectul TEST anterior păstrat intact, portul/containerul vechi (8084) neatinse, TEST strict separat."""
from __future__ import annotations

import re
from typing import Dict, List
from urllib.parse import urlparse, unquote

PROTECTED_PROD_REFS = ("txuxkjgftopznvmooexq",)       # același ref ca în shared/cimitir_cloud_control_pg.py și cimitir_test_guard.py
PRESERVED_TEST_REFS = ("nhhczdmcocbobptogvsw",)       # proiectul TEST anterior: se păstrează intact (nicio scriere)
PROTECTED_PORTS = (8084,)                             # containerul vechi: nu se modifică, oprește sau șterge
PROTECTED_CONTAINERS = ("cimitir-cloud-etapa789-test", "cimitir-cloud-test", "cimitir-cloud-mobile-v2-test",
                        "cimitir-cloud-mobile-v2-next", "cimitir-cloud-mobile-v2-import")
REF_RE = re.compile(r"^[a-z]{20}$")
NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,62}$")
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,40}$")
ENV_NAME = "TEST"
PARISH_ENV = "PARISH"     # proiectul Supabase propriu al unei parohii, administrat din MAIN (refurile protejate rămân blocate)


class GuardError(Exception):
    """Operație refuzată de o protecție; mesajul nu conține secrete."""


def _strings(cfg: Dict) -> List[str]:
    return [str(v) for v in cfg.values() if isinstance(v, (str, int)) and str(v)]


def touches_prod(*values: str) -> bool:
    low = " ".join(str(v or "") for v in values).lower()
    return any(ref in low for ref in PROTECTED_PROD_REFS)


def assert_not_prod(cfg: Dict) -> None:
    if touches_prod(*_strings(cfg)):
        raise GuardError("PROD este BLOCAT: configurația indică proiectul de producție. Nicio operație nu a fost executată.")


def _db_parts(cfg: Dict) -> Dict[str, str]:
    u = urlparse(cfg.get("db_connection_string") or "")
    try:
        port = u.port or 0
    except ValueError:
        port = 0
    user = cfg.get("db_username") or unquote(u.username or "")
    return {"host": (u.hostname or "").lower(), "port": str(port), "user": user, "db": u.path.lstrip("/")}


def consistency_issues(cfg: Dict) -> List[str]:
    """Coerența ref ↔ URL ↔ utilizator ↔ pooler. Listă goală = coerent."""
    out: List[str] = []
    ref = (cfg.get("project_ref") or "").strip()
    if not REF_RE.match(ref):
        out.append("Project Ref invalid (20 litere mici).")
        return out
    host = (urlparse(cfg.get("supabase_url") or "").hostname or "").lower()
    if host != f"{ref}.supabase.co":
        out.append("Supabase URL nu corespunde Project Ref (așteptat https://<ref>.supabase.co).")
    if (urlparse(cfg.get("supabase_url") or "").scheme) != "https":
        out.append("Supabase URL trebuie să folosească https.")
    p = _db_parts(cfg)
    if "supabase" in p["host"]:
        if p["user"] and not p["user"].endswith("." + ref) and ref not in p["host"]:
            out.append("Utilizatorul PostgreSQL pe pooler trebuie să fie postgres.<ref> cu același Project Ref.")
        if "pooler" in p["host"] and p["port"] == "6543":
            out.append("Portul 6543 (Transaction Pooler) nu e acceptat: migrările cer Session Pooler (5432).")
        if "pooler" not in p["host"] and p["port"] not in ("5432", "0"):
            out.append("Port PostgreSQL neașteptat pentru Supabase.")
    bucket = (cfg.get("storage_bucket") or "").strip()
    if bucket and cfg.get("env_name", ENV_NAME) != PARISH_ENV and "test" not in bucket.lower():
        out.append("Bucketul TEST trebuie să conțină „test” în nume (separare strictă TEST/PROD).")
    port = cfg.get("docker_port")
    try:
        assert_port_ok(port)
    except GuardError as exc:
        out.append(str(exc))
    for k in ("main_cloud_url", "cloud_public_url"):
        if k == "main_cloud_url" and touches_prod(cfg.get(k, "")):
            out.append("URL-ul Cloud indică PROD.")
    return out


def assert_read_allowed(cfg: Dict) -> None:
    assert_not_prod(cfg)
    if cfg.get("env_name", ENV_NAME) not in (ENV_NAME, PARISH_ENV):
        raise GuardError("Mediul permis este doar TEST sau proiectul propriu al parohiei (PARISH).")


def assert_write_allowed(cfg: Dict) -> None:
    assert_read_allowed(cfg)
    ref = (cfg.get("project_ref") or "").strip()
    if any(r in " ".join(_strings(cfg)).lower() for r in PRESERVED_TEST_REFS):
        raise GuardError("Proiectul TEST anterior este PĂSTRAT INTACT: nicio scriere/migrare permisă.")
    issues = consistency_issues(cfg)
    if issues:
        raise GuardError("Configurație incoerentă, scrierea e blocată: " + " ".join(issues))
    if not REF_RE.match(ref):
        raise GuardError("Project Ref invalid.")


def assert_port_ok(port) -> int:
    try:
        p = int(port)
    except (TypeError, ValueError):
        raise GuardError("Port Docker invalid.") from None
    if not 1024 <= p <= 65535:
        raise GuardError("Port Docker în afara intervalului 1024–65535.")
    if p in PROTECTED_PORTS:
        raise GuardError(f"Portul {p} aparține containerului vechi și este protejat: nu se folosește.")
    return p


def assert_container_ok(name: str) -> str:
    n = (name or "").strip()
    if not NAME_RE.match(n):
        raise GuardError("Nume container invalid.")
    if n in PROTECTED_CONTAINERS or n.endswith("-prev") and n[:-5] in PROTECTED_CONTAINERS:
        raise GuardError(f"Containerul „{n}” este protejat (versiunea veche): nu se modifică.")
    return n


def assert_tag_ok(tag: str) -> str:
    t = (tag or "").strip()
    if not TAG_RE.match(t) or t == "latest":
        raise GuardError("Tag imagine invalid (litere mici/cifre/._-, fără „latest”).")
    return t
