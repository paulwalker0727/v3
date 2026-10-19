"""Asistent de asociere MAIN ↔ Cloud ↔ Mobile: sonde automate (doar citire), teste izolate prin scripturile existente (cu aprobare) și listă de verificat manual."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from . import guard
from .checks import RealNet, mobile_expected
from .config import repo_root
from .redact import redact, register

APPROVAL_PHRASE = "APROB DATE TEST IZOLATE"
ISOLATED_SCRIPTS = {"provision": "scripts/verify_provision_live.py", "sync": "scripts/verify_cloud_live.py"}

MANUAL_STEPS: List[Dict[str, str]] = [
    {"id": "main_config", "title": "MAIN → Cloud: configurare și cerere de înregistrare",
     "how": "În MAIN (cu CIMITIR_DATA_DIR curat de test): 🔗 Asociază cu Cloud → adresa Cloud afișată mai sus → trimite cererea → confirmă-o în /admin/ al Cloud-ului. MAIN trebuie să afișeze „Sincronizarea este activă”. (TEST_LAPTOP_WINDOWS_DOCKER.md §3)"},
    {"id": "device_auth", "title": "Autentificarea și asocierea dispozitivelor",
     "how": "În /admin/ verifică dispozitivul MAIN (legătură activă) și revocarea/regenerarea codului. Un cod revocat nu mai trebuie să funcționeze."},
    {"id": "mobile_open", "title": "Conectarea aplicației Mobile",
     "how": "Mobile cere HTTPS public (vezi TEST_TELEFON_ANDROID_IPHONE.md). Deschide <URL Mobile>/mobile/, scanează QR-ul de asociere din MAIN/admin și confirmă că lista de locuri apare."},
    {"id": "sync_entities", "title": "Sincronizare: locuri de veci, concesionari, contracte, plăți, avize",
     "how": "Creează în MAIN câte o înregistrare de test din fiecare tip (numai în registrul izolat), apasă Sincronizează și verifică în Mobile + cu `py scripts\\test_data_inventory.py show --cemetery <UID>` (read-only)."},
    {"id": "sync_docs", "title": "Sincronizare documente și fotografii",
     "how": "MAIN → atașează un PDF mic la un loc → Mobile îl vede; Mobile → fotografie → apare în MAIN. Tipurile carte_identitate/cerere_doc NU se sincronizează (corect)."},
    {"id": "conflicts", "title": "Conflicte de versiune",
     "how": "Modifică același câmp al aceleiași înregistrări în MAIN și în Mobile fără sincronizare între ele, apoi sincronizează ambele: trebuie să apară un conflict vizibil (fereastra Conflicte Cloud), fără pierdere de date."},
    {"id": "offline", "title": "Comportament offline și reluarea sincronizării",
     "how": "Oprește rețeaua pe Mobile/MAIN, creează o modificare, repornește rețeaua: coada se golește singură, fără duplicate (verifică numărul de rânduri)."},
]


def probe(cfg: Dict[str, Any], net: Optional[RealNet] = None) -> List[Dict[str, str]]:
    """Sonde read-only către Cloud: sănătate, rute protejate, pagină și fișiere Mobile."""
    net = net or RealNet()
    base = (cfg.get("main_cloud_url") or cfg.get("cloud_public_url") or "").rstrip("/")
    guard.assert_read_allowed({**cfg, "main_cloud_url": base})
    if not base:
        return [{"id": "url", "title": "Adresa Cloud", "status": "FAIL", "detail": "Adresa Cloud pentru MAIN nu este configurată."}]
    out: List[Dict[str, str]] = []

    def get(path: str, ok=(200,)):
        try:
            code, _ = net.http("GET", base + path, timeout=8)
        except OSError as exc:
            return False, f"indisponibil ({type(exc).__name__})"
        return code in ok, f"HTTP {code}"

    for cid, title, path, ok in (
        ("healthz", "MAIN→Cloud: /healthz", "/healthz", (200,)),
        ("health", "Cloud: /api/v1/health (baza + storage)", "/api/v1/health", (200,)),
        ("admin_status", "Cloud: stare administrare", "/api/v1/admin/status", (200,)),
        ("sync_guard", "Sync v2 cere autentificare (fără cheie ⇒ 401/403)", "/api/v1/sync-v2/status", (401, 403)),
        ("mobile_page", "Mobile: pagina /mobile/", "/mobile/", (200,)),
        ("mobile_manifest", "Mobile: manifest PWA", "/mobile/manifest.webmanifest", (200,)),
        ("mobile_sw", "Mobile: service worker (offline)", "/mobile/sw.js", (200,)),
        ("admin_page", "Cloud: pagina /admin/", "/admin/", (200,)),
    ):
        ok_, d = get(path, ok)
        out.append({"id": cid, "title": title, "status": "PASS" if ok_ else "FAIL", "detail": d})
    skip = {"mobile_manifest.webmanifest", "mobile_sw.js"}
    bad = []
    for name in mobile_expected(cfg):
        if name in skip:
            continue
        ok_, d = get(f"/mobile-assets/{name}")
        if not ok_:
            bad.append(f"{name} ({d})")
    out.append({"id": "mobile_assets", "title": "Mobile: fișiere /mobile-assets/*", "status": "FAIL" if bad else "PASS",
                "detail": ("lipsesc: " + ", ".join(bad)) if bad else "toate fișierele Mobile sunt servite (HTTP 200)"})
    return [{**r, "detail": redact(r["detail"])} for r in out]


def validate_isolated_target(cfg: Dict[str, Any], url: str) -> str:
    u = urlparse(url or "")
    guard.assert_write_allowed(cfg)
    if guard.touches_prod(url):
        raise guard.GuardError("Adresa Cloud indică PROD.")
    if u.port in guard.PROTECTED_PORTS:
        raise guard.GuardError("Portul 8084 (containerul vechi) este protejat.")
    if u.hostname in ("127.0.0.1", "localhost"):
        if u.port != int(cfg.get("docker_port") or 0):
            raise guard.GuardError("Testele izolate rulează doar pe containerul TEST configurat (portul Docker din configurare).")
    elif u.scheme != "https" or url.rstrip("/") != (cfg.get("cloud_public_url") or "").rstrip("/"):
        raise guard.GuardError("Adresa publică trebuie să fie HTTPS și identică cu „Cloud Public URL” configurat.")
    return url.rstrip("/")


def run_isolated(cfg: Dict[str, Any], which: str, approval: str, mode: str, org_uid: str, emit: Callable[[str], None],
                 runner: Optional[Callable] = None) -> Dict[str, Any]:
    """Rulează un script existent care scrie DOAR registre fictive izolate. Cere: mod scriere + fraza de aprobare + credențiale admin Cloud TEST."""
    if which not in ISOLATED_SCRIPTS:
        raise guard.GuardError("Test necunoscut.")
    url = validate_isolated_target(cfg, cfg.get("main_cloud_url") or cfg.get("cloud_public_url") or "")
    if mode != "execute":
        return {"executed": False, "dry_run": True, "message": "Dry Run: testul izolat NU a rulat. Scrie date fictive doar în modul scriere, cu aprobare."}
    if approval != APPROVAL_PHRASE:
        raise guard.GuardError(f"Aprobare lipsă. Tastează exact: {APPROVAL_PHRASE}")
    email, pw = cfg.get("cloud_admin_email") or "", cfg.get("cloud_admin_password") or ""
    if not email or not pw:
        raise guard.GuardError("Introdu e-mailul și parola administratorului Cloud TEST (nu se salvează).")
    if which == "sync" and not re.fullmatch(r"[0-9a-fA-F-]{32,36}", org_uid or ""):
        raise guard.GuardError("Testul de sincronizare cere UID-ul unei organizații TEST (CIMITIR_LIVE_ORG_UID).")
    register(pw)
    script = repo_root(cfg) / ISOLATED_SCRIPTS[which]
    if not script.exists():
        raise guard.GuardError(f"Scriptul {ISOLATED_SCRIPTS[which]} lipsește din repo.")
    env = {k: v for k, v in os.environ.items() if not re.search(r"(?i)(secret|password|token|database_url)", k)}
    env.update({"CLOUD_URL": url, "CIMITIR_LIVE_ADMIN_EMAIL": email, "CIMITIR_LIVE_ADMIN_PASSWORD": pw})
    if org_uid:
        env["CIMITIR_LIVE_ORG_UID"] = org_uid
    cmd = [sys.executable, str(script), "--yes-write-test-data"]
    emit(f"Rulez {ISOLATED_SCRIPTS[which]} pe {url} (date fictive izolate; datele reale nu sunt atinse)")
    proc = (runner or subprocess.Popen)(cmd, env=env, cwd=str(repo_root(cfg)), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    t0 = time.time()
    for line in proc.stdout:
        line = redact(line.rstrip())[:300]
        emit(line)
        m = re.match(r"^\s*\[?(PASS|FAIL|SKIP)\b", line)
        if m:
            counts[m.group(1)] += 1
        if time.time() - t0 > 900:
            proc.kill()
            emit("Timeout 15 min.")
            break
    rc = proc.wait()
    return {"executed": True, "exit_code": rc, "counts": counts, "ok": rc == 0 and counts["FAIL"] == 0}


class Checklist:
    def __init__(self, home: Path):
        self.path = Path(home) / "pairing_checklist.json"

    def load(self) -> Dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def set(self, step_id: str, status: str, note: str = "") -> None:
        if step_id not in {s["id"] for s in MANUAL_STEPS} or status not in ("pending", "pass", "fail"):
            raise ValueError("Pas sau stare invalidă.")
        d = self.load()
        d[step_id] = {"status": status, "note": redact(note)[:500], "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.path.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")

    def view(self) -> List[Dict[str, Any]]:
        d = self.load()
        return [{**s, **d.get(s["id"], {"status": "pending", "note": ""})} for s in MANUAL_STEPS]


def main_config_snippet(cfg: Dict[str, Any]) -> str:
    """Valori de folosit în MAIN (fără secrete)."""
    lines = [f"Adresa Cloud (MAIN → Asociază cu Cloud): {cfg.get('main_cloud_url') or cfg.get('cloud_public_url')}",
             "set CIMITIR_CLOUD_HTTP_TIMEOUT=60        (dacă apar ReadTimeout)"]
    if cfg.get("main_data_dir"):
        lines.append(f"set CIMITIR_DATA_DIR={cfg['main_data_dir']}        (director de date de test, separat de MAIN real)")
    if cfg.get("mobile_public_url"):
        lines.append(f"Adresa Mobile (HTTPS): {cfg['mobile_public_url'].rstrip('/')}/mobile/")
    else:
        lines.append("Adresa Mobile: lipsește — telefonul cere un URL HTTPS public (vezi TEST_TELEFON_ANDROID_IPHONE.md).")
    return "\n".join(lines)
