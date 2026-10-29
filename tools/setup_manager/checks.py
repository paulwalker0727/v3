"""Cele 9 verificări automate („Verifică toate conexiunile”). Doar citire. Rezultat: PASS / FAIL / WARN / SKIP + explicație fără date sensibile."""
from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from . import guard, migrations as mig
from .config import build_db_url, db_host_port, migrations_dir, repo_root
from .redact import redact

PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"
MOBILE_BASE = ("mobile_app.js", "mobile_ext.js", "mobile_offline.js", "mobile_photos.js", "jsQR.js", "mobile_app.css", "mobile-icon-192.png", "mobile-icon-512.png",
               "mobile_manifest.webmanifest", "mobile_sw.js")


class RealNet:
    """Acces la rețea/DB/Docker; se înlocuiește în teste."""

    def resolve(self, host: str) -> List[str]:
        return sorted({i[4][0] for i in socket.getaddrinfo(host, None)})

    def tcp(self, host: str, port: int, timeout: float = 6.0) -> None:
        with socket.create_connection((host, port), timeout=timeout):
            pass

    def http(self, method: str, url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 8.0, body: Optional[bytes] = None) -> Tuple[int, bytes]:
        req = urllib.request.Request(url, method=method, headers=headers or {}, data=body)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read(1 << 20)
        except urllib.error.HTTPError as e:
            return e.code, e.read(1 << 16)

    def pg(self, url: str):
        import psycopg
        return psycopg.connect(url, connect_timeout=12, autocommit=False)

    def docker(self, args: List[str], timeout: int = 60) -> Tuple[int, str, str]:
        from .dockermgr import run_docker
        return run_docker(args, timeout)


def R(cid: str, title: str, status: str, detail: str, hint: str = "") -> Dict[str, str]:
    return {"id": cid, "title": title, "status": status, "detail": redact(detail), "hint": redact(hint)}


def explain_pg_error(exc: Exception) -> str:
    m = str(exc).lower()
    if "password authentication failed" in m:
        return "Autentificare respinsă: utilizator sau parolă greșite (pe Session Pooler utilizatorul este postgres.<ref>)."
    if "tenant or user not found" in m:
        return "Pooler: „Tenant or user not found” — Project Ref/regiunea din host nu corespund proiectului."
    if "timeout" in m or "timed out" in m:
        return "Timeout la conectare (firewall/rețea sau host greșit)."
    if "could not translate host" in m or "name or service not known" in m or "getaddrinfo" in m:
        return "Numele gazdei nu se rezolvă (DNS)."
    if "ssl" in m:
        return "Eroare SSL; folosește sslmode=require."
    if "connection refused" in m:
        return "Conexiune refuzată (port/host greșit)."
    return f"{type(exc).__name__}: {str(exc)[:200]}"


def _json(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8", "ignore"))
    except ValueError:
        return None


def mobile_expected(cfg: Dict[str, Any]) -> List[str]:
    """Fișierele Mobile servite de Cloud: cheile _MOBILE_STATIC din codul curent + manifest/service worker."""
    src = repo_root(cfg) / "shared" / "cimitir_cloud_app.py"
    names = set(MOBILE_BASE)
    try:
        m = re.search(r"_MOBILE_STATIC\s*=\s*\{(.*?)\}", src.read_text(encoding="utf-8"), re.S)
        if m:
            names |= set(re.findall(r'"([^"]+\.[a-zA-Z]+)"\s*:', m.group(1)))
    except OSError:
        pass
    return sorted(names)


def dockerfile_coverage(cfg: Dict[str, Any]) -> List[str]:
    """Analiză statică a Dockerfile: fișierele Mobile necopiate în imagine."""
    df = repo_root(cfg) / "Dockerfile"
    try:
        text = df.read_text(encoding="utf-8")
    except OSError:
        return []
    copied = set()
    for line in text.splitlines():
        if line.strip().upper().startswith("COPY"):
            copied |= {Path(t).name for t in line.split()[1:-1]}
    return [n for n in mobile_expected(cfg) if n not in copied]


class Checker:
    def __init__(self, cfg: Dict[str, Any], net: Optional[RealNet] = None):
        self.cfg = cfg
        self.net = net or RealNet()
        self.conn = None
        self.analysis: Optional[Dict[str, Any]] = None
        self.migs = None

    def _url(self) -> str:
        return build_db_url(self.cfg)

    def run_all(self) -> List[Dict[str, str]]:
        try:
            guard.assert_read_allowed(self.cfg)
        except guard.GuardError as exc:
            return [R("guard", "Protecție PROD/TEST", FAIL, str(exc))]
        steps: List[Callable[[], Dict[str, str]]] = [self.dns_tcp, self.pg_auth, self.identify, self.pg_permissions, self.storage,
                                                       self.schema_objects, self.migrations_state, self.docker_mobile, self.health]
        out = []
        try:
            for fn in steps:
                try:
                    out.append(fn())
                except Exception as exc:  # noqa: BLE001
                    out.append(R(fn.__name__, fn.__name__, FAIL, f"Eroare neașteptată: {type(exc).__name__}: {str(exc)[:200]}"))
        finally:
            if self.conn is not None:
                try:
                    self.conn.rollback()
                    self.conn.close()
                except Exception:  # noqa: BLE001
                    pass
        return out

    def _need_db(self, cid: str, title: str) -> Optional[Dict[str, str]]:
        if self.conn is None:
            return R(cid, title, SKIP, "Omis: depinde de autentificarea PostgreSQL (pasul 2).")
        return None

    def dns_tcp(self):
        t = "1. DNS și TCP"
        host, port = db_host_port(self.cfg)
        sb = urlparse(self.cfg.get("supabase_url") or "").hostname or ""
        bad, good = [], []
        for h, p in ((host, port), (sb, 443)):
            if not h:
                bad.append(f"adresă lipsă pentru portul {p}")
                continue
            try:
                self.net.resolve(h)
            except OSError as exc:
                bad.append(f"DNS {h}: {type(exc).__name__}")
                continue
            try:
                self.net.tcp(h, p)
                good.append(f"{h}:{p}")
            except OSError as exc:
                bad.append(f"TCP {h}:{p}: {type(exc).__name__}")
        if bad:
            return R("dns_tcp", t, FAIL, "; ".join(bad), "Verifică internetul/firewall-ul și adresele din configurare.")
        return R("dns_tcp", t, PASS, "DNS și TCP OK: " + ", ".join(good))

    def pg_auth(self):
        t = "2. Autentificare PostgreSQL"
        if not urlparse(self._url()).password:
            return R("pg_auth", t, FAIL, "Parola PostgreSQL nu este introdusă.")
        try:
            self.conn = self.net.pg(self._url())
            row = self.conn.execute("SELECT current_user, current_database(), split_part(version(), ' ', 2)").fetchone()
        except Exception as exc:  # noqa: BLE001
            self.conn = None
            return R("pg_auth", t, FAIL, explain_pg_error(exc))
        return R("pg_auth", t, PASS, f"Autentificat ca „{row[0]}” în baza „{row[1]}” (PostgreSQL {row[2]}).")

    def identify(self):
        t = "3. Identificarea proiectului Supabase TEST"
        issues = guard.consistency_issues(self.cfg)
        ref = self.cfg.get("project_ref", "")
        if issues:
            return R("identify", t, FAIL, " ".join(issues))
        notes = [f"Project Ref {ref} coerent cu URL, utilizator și pooler."]
        if any(r in " ".join(map(str, self.cfg.values())).lower() for r in guard.PRESERVED_TEST_REFS):
            return R("identify", t, FAIL, "Configurația indică proiectul TEST anterior, păstrat intact: scrierea este blocată.")
        key = self.cfg.get("supabase_secret_key")
        if not key:
            return R("identify", t, WARN, notes[0] + " Cheia secretă lipsește: apartenența cheii la proiect nu a fost confirmată.")
        try:
            code, _ = self.net.http("GET", f"{self.cfg['supabase_url'].rstrip('/')}/storage/v1/bucket", {"Authorization": f"Bearer {key}", "apikey": key})
        except OSError as exc:
            return R("identify", t, FAIL, f"Supabase inaccesibil: {type(exc).__name__}")
        if code in (401, 403):
            return R("identify", t, FAIL, "Cheia secretă este respinsă de proiectul " + ref + " (cheie a altui proiect sau invalidă).")
        if code != 200:
            return R("identify", t, FAIL, f"Răspuns neașteptat de la Supabase Storage API: HTTP {code}.")
        return R("identify", t, PASS, notes[0] + " Cheia secretă aparține proiectului.")

    def pg_permissions(self):
        t = "4. Permisiuni PostgreSQL"
        s = self._need_db("pg_permissions", t)
        if s:
            return s
        c = self.conn
        r = c.execute("SELECT rolsuper, rolcreaterole, rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()
        can_create = c.execute("SELECT has_schema_privilege(current_user,'public','CREATE')").fetchone()[0]
        foreign = c.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tableowner<>current_user").fetchone()[0]
        has_hist = c.execute("SELECT to_regclass('public.schema_migrations') IS NOT NULL").fetchone()[0]
        probs, warns = [], []
        if not can_create:
            probs.append("fără CREATE pe schema public (migrările nu pot rula)")
        if foreign:
            warns.append(f"{foreign} tabele aparțin altui rol: ALTER/CREATE POLICY pe ele poate eșua")
        if r[0]:
            warns.append("rol superuser: principiul privilegiilor minime recomandă un rol administrativ dedicat")
        roles = c.execute("SELECT rolname, rolbypassrls, rolsuper FROM pg_roles WHERE rolname='cimitir_cp_app'").fetchone()
        if roles and (roles[1] or roles[2]):
            probs.append("rolul runtime cimitir_cp_app ocolește RLS (BYPASSRLS/superuser)")
        elif not roles:
            warns.append("rolul runtime cimitir_cp_app nu există încă (apare după migrările 014+)")
        if has_hist:
            c.execute("SELECT 1 FROM schema_migrations LIMIT 1")
        c.rollback()
        if probs:
            return R("pg_permissions", t, FAIL, "; ".join(probs + warns))
        return R("pg_permissions", t, WARN if warns else PASS, "; ".join(warns) if warns else "CREATE pe public, rolul runtime fără BYPASSRLS, acces la istoricul migrărilor.")

    def storage(self):
        t = "5. Supabase Storage și bucket privat"
        key, bucket = self.cfg.get("supabase_secret_key"), (self.cfg.get("storage_bucket") or "").strip()
        if not key or not bucket:
            return R("storage", t, FAIL, "Cheia secretă sau numele bucketului lipsește.")
        try:
            code, body = self.net.http("GET", f"{self.cfg['supabase_url'].rstrip('/')}/storage/v1/bucket/{bucket}", {"Authorization": f"Bearer {key}", "apikey": key})
        except OSError as exc:
            return R("storage", t, FAIL, f"Storage inaccesibil: {type(exc).__name__}")
        data = _json(body) or {}
        if code in (401, 403):
            return R("storage", t, FAIL, "Cheia secretă nu are acces la Storage.")
        if code == 404 or (code == 400 and "not found" in str(data).lower()):
            return R("storage", t, FAIL, f"Bucketul „{bucket}” nu există.", "Îl poți crea PRIVAT din secțiunea Storage (cu confirmare, în modul scriere).")
        if code != 200:
            return R("storage", t, FAIL, f"HTTP {code} de la Storage.")
        if data.get("public"):
            return R("storage", t, FAIL, f"Bucketul „{bucket}” este PUBLIC; trebuie privat.", "Setează bucketul ca Private din consola Supabase.")
        return R("storage", t, PASS, f"Bucketul „{bucket}” există și este privat.")

    def _analysis(self):
        if self.analysis is None:
            self.migs = mig.load_migrations(migrations_dir(self.cfg))
            self.analysis = mig.analyze(self.conn, self.migs)
            self.conn.rollback()
        return self.analysis

    def schema_objects(self):
        t = "6. Tabele, funcții și politici RLS"
        s = self._need_db("schema_objects", t)
        if s:
            return s
        a = self._analysis()
        broken = [r for r in a["migrations"] if r["missing_count"] and r["state"] in (mig.ST_INCONSISTENT, mig.ST_PARTIAL)]
        pending = [r for r in a["migrations"] if r["state"] == mig.ST_PENDING]
        tot = sum(r["checked"] for r in a["migrations"])
        if broken:
            return R("schema_objects", t, FAIL, "; ".join(f"{r['name']}: lipsesc {r['missing_count']} ({', '.join(r['missing'][:4])})" for r in broken))
        if pending:
            return R("schema_objects", t, WARN, "Obiecte lipsă doar din migrări neaplicate: " + ", ".join(f"{r['version']:03d} ({r['missing_count']})" for r in pending)
                     + f". Restul obiectelor verificate OK ({tot - sum(r['missing_count'] for r in pending)}).")
        return R("schema_objects", t, PASS, f"Toate cele {tot} obiecte verificabile (tabele, funcții, politici, RLS, indexuri, trigger-e) există.")

    def migrations_state(self):
        t = "7. Migrări existente"
        s = self._need_db("migrations_state", t)
        if s:
            return s
        a = self._analysis()
        plan = mig.build_plan(a)
        s2326 = ", ".join(f"{k}: {v}" for k, v in plan["summary"].items() if int(k) >= 23)
        det = f"Migrări 023–026 → {s2326}. " + (f"De aplicat/înregistrat: {[x['version'] for x in plan['steps']]}. " if plan["steps"] else "Nimic de aplicat. ")
        if not a["history_table"]:
            det += "Tabelul schema_migrations lipsește. "
        if a["duplicates"]:
            return R("migrations_state", t, FAIL, det + f"Duplicate în istoric: {a['duplicates']}.")
        if plan["blocked"]:
            return R("migrations_state", t, FAIL, det + "BLOCAT: " + " | ".join(plan["blocked"][:3]))
        return R("migrations_state", t, WARN if plan["steps"] else PASS, det.strip())

    def docker_mobile(self):
        t = "8. Fișiere Mobile din imaginea Docker"
        static_missing = dockerfile_coverage(self.cfg)
        tag = (self.cfg.get("image_tag") or "").strip()
        image = f"cimitir-cloud:{tag}"
        try:
            rc, _, err = self.net.docker(["image", "inspect", image], 30)
        except Exception as exc:  # noqa: BLE001
            msg = f"Docker indisponibil ({type(exc).__name__})."
            return R("docker_mobile", t, FAIL if static_missing else WARN, msg + (f" Analiza Dockerfile: NU copiază {static_missing}." if static_missing else ""))
        if rc != 0:
            return R("docker_mobile", t, FAIL if static_missing else WARN, f"Imaginea {image} nu există încă (construiește-o). " + (f"Dockerfile nu copiază: {static_missing}." if static_missing else ""))
        names = mobile_expected(self.cfg)
        script = ("import os,json;b='/app/backend/static/';n=%r;print(json.dumps({'static':[x for x in n if not os.path.isfile(b+x)],"
                  "'tpl':os.path.isfile('/app/backend/templates/mobile_app.html')}))") % (names,)
        rc, out, err = self.net.docker(["run", "--rm", "--network", "none", "--entrypoint", "python", image, "-c", script], 90)
        data = _json(out.strip().splitlines()[-1].encode()) if rc == 0 and out.strip() else None
        if data is None:
            return R("docker_mobile", t, FAIL, "Nu am putut inspecta imaginea: " + err[:200])
        miss = list(data["static"]) + ([] if data["tpl"] else ["templates/mobile_app.html"])
        if miss:
            return R("docker_mobile", t, FAIL, f"Lipsesc din imaginea {image}: {', '.join(miss)}. Fără ele aplicația Mobile nu se încarcă.",
                     "Reconstruiește imaginea după aplicarea corecției Dockerfile din acest patch.")
        return R("docker_mobile", t, PASS, f"Toate cele {len(names)} fișiere Mobile + șablonul există în {image}.")

    def health(self):
        t = "9. Endpointuri /healthz și /api/v1/health"
        base = f"http://127.0.0.1:{self.cfg.get('docker_port')}"
        res, bad = [], []
        for path in ("/healthz", "/api/v1/health"):
            try:
                code, body = self.net.http("GET", base + path)
            except OSError as exc:
                bad.append(f"{path}: indisponibil ({type(exc).__name__})")
                continue
            d = _json(body) or {}
            if code == 200:
                res.append(f"{path}: 200")
            else:
                bad.append(f"{path}: HTTP {code} (database={d.get('database', '?')}, storage={d.get('storage', '?')})")
        if bad:
            return R("health", t, FAIL, "; ".join(bad + res), "Pornește containerul TEST (secțiunea Docker) și verifică logurile.")
        return R("health", t, PASS, "; ".join(res) + f" pe {base}")
