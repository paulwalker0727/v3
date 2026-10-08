"""
system_admin.py — Etapa 8: panou tehnic `/admin/system` (doar administratori).

Principii: doar operații FIXE și sigure (teste, backup, citirea jurnalului aplicației, verificări în citire); nimic nu primește SQL,
cale de fișier sau comandă din browser. Nicio parolă/cheie/URL cu credențiale nu ajunge în pagină sau în jurnal: `redact()` maschează
URL-uri cu parolă, chei de sincronizare, JWT/Bearer și valorile exacte ale variabilelor de mediu secrete.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

import cimitir_config

STARTED_AT = datetime.now()
LOG_NAME = "cimitir.log"
_SECRET_ENV = re.compile(r"(SECRET|PASSWORD|PASSWD|TOKEN|API_?KEY|ACCESS_KEY|SERVICE_ROLE|PRIVATE|DATABASE_URL|MONGO_URL)", re.I)
_PATTERNS = [
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer ***"),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*)://([^:/@\s]+):([^@\s]+)@"), r"\1://\2:***@"),
    (re.compile(r"(?i)([A-Za-z0-9_-]*(?:password|passwd|pwd|secret|token|api[_-]?key|authorization|x-sync-key|service_role_key|access_key)"
                r"[A-Za-z0-9_-]*)(\"?\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;}&]+)"), r"\1\2***"),
    (re.compile(r"\bcimitir\.[A-Za-z0-9_-]{20,}"), "cimitir.***"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]*"), "***jwt***"),
]


def redact(text: Any) -> str:
    out = str(text)
    for value in sorted({v for k, v in os.environ.items() if _SECRET_ENV.search(k) and len(v) >= 6}, key=len, reverse=True):
        out = out.replace(value, "***")
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return out


class _RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


# ------------------------------------------------------------------ jurnal aplicație
_handler: Optional[RotatingFileHandler] = None


def log_path() -> Path:
    return Path(cimitir_config.data_dir()) / "logs" / LOG_NAME


def install_logging() -> Optional[Path]:
    """Jurnal rotativ (1 MB x 4) în DATA_DIR/logs, fără secrete; prinde și erorile uvicorn (tracebackuri 500). Access log NU se scrie."""
    global _handler
    if _handler is not None:
        return log_path()
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setLevel(logging.INFO)
        handler.setFormatter(_RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root = logging.getLogger()
        root.addHandler(handler)
        if root.level == logging.NOTSET or root.level > logging.WARNING:
            root.setLevel(logging.WARNING)
        logging.getLogger("cimitir").setLevel(logging.INFO)
        logging.getLogger("uvicorn").addHandler(handler)
        _handler = handler
        logging.getLogger("cimitir").info("jurnal pornit (versiune %s)", cimitir_config.app_version())
        return path
    except Exception as exc:  # pragma: no cover
        print(f"[system_admin] jurnal indisponibil: {type(exc).__name__}")
        return None


_HEADER = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d{3} (DEBUG|INFO|WARNING|ERROR|CRITICAL) ")


def _blocks(max_bytes: int = 2_000_000) -> List[Dict[str, Any]]:
    base = log_path()
    files = [p for p in [base] + [base.with_name(f"{LOG_NAME}.{i}") for i in (1, 2, 3)] if p.is_file()]
    chunks, budget = [], max_bytes
    for path in files:                       # de la cel mai nou la cel mai vechi
        data = path.read_text(encoding="utf-8", errors="replace")[-budget:]
        chunks.insert(0, data)
        budget -= len(data)
        if budget <= 0:
            break
    blocks: List[Dict[str, Any]] = []
    for line in "".join(chunks).splitlines():
        m = _HEADER.match(line)
        if m:
            blocks.append({"at": datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"), "level": m.group(2), "text": line})
        elif blocks:
            blocks[-1]["text"] += "\n" + line
    return blocks


def error_stats() -> Dict[str, Any]:
    now, errors = datetime.now(), []
    try:
        errors = [b for b in _blocks() if b["level"] in ("ERROR", "CRITICAL")]
    except Exception:
        pass
    last = errors[-1] if errors else None
    return {"last_24h": sum(1 for b in errors if now - b["at"] <= timedelta(hours=24)),
            "last_7d": sum(1 for b in errors if now - b["at"] <= timedelta(days=7)),
            "last_error_at": last["at"].isoformat(timespec="seconds") if last else "",
            "last_error": redact(last["text"].splitlines()[0])[:300] if last else ""}


def tail_logs(lines: int = 200, errors_only: bool = False) -> Dict[str, Any]:
    """Ultimele linii din jurnalul APLICAȚIEI (singurul fișier citibil; calea nu vine din browser), mascate."""
    lines = max(10, min(int(lines), 1000))
    try:
        blocks = _blocks()
    except Exception as exc:
        return {"available": False, "text": "", "count": 0, "note": f"jurnal ilizibil: {type(exc).__name__}"}
    if errors_only:
        blocks = [b for b in blocks if b["level"] in ("ERROR", "CRITICAL", "WARNING")]
    text = "\n".join(b["text"] for b in blocks).splitlines()[-lines:]
    return {"available": log_path().is_file(), "text": redact("\n".join(text)), "count": len(text),
            "note": "" if blocks else "Niciun eveniment în jurnal."}


# ------------------------------------------------------------------ baza de date
def engine() -> str:
    import store as cim
    return cim.process_engine()


def db_status() -> Dict[str, Any]:
    started = time.perf_counter()
    if engine() == "postgres":
        import postgres_store
        info = postgres_store.health()
        return {"engine": "PostgreSQL", "online": bool(info.get("connected")), "latency_ms": info.get("latency_ms"),
                "version": info.get("server_version") or "", "schema_version": info.get("schema_version"), "error": info.get("error", "")}
    try:
        import store as cim
        cim.get_setting("cemetery_id")
        return {"engine": "SQLite", "online": True, "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "version": "", "schema_version": None, "error": ""}
    except Exception as exc:
        return {"engine": "SQLite", "online": False, "latency_ms": None, "version": "", "schema_version": None, "error": type(exc).__name__}


def db_size_bytes() -> Optional[int]:
    """Doar metode fixe: pg_database_size / mărimea fișierului. None dacă nu se poate afla."""
    try:
        if engine() == "postgres":
            import postgres_store
            with postgres_store._db() as conn:
                return int(conn.execute("SELECT pg_database_size(current_database()) AS s").fetchone()["s"])
        import cimitir_registry
        binding = cimitir_registry.binding_from_catalog(cimitir_config.data_dir()) if cimitir_registry.current_binding() is None else cimitir_registry.current_binding()
        path = binding.database if binding is not None else Path(cimitir_config.data_dir()) / "cimitir.db"
        return path.stat().st_size if path.is_file() else None
    except Exception:
        return None


def test_db() -> Dict[str, Any]:
    st = db_status()
    if not st["online"]:
        return {"ok": False, "title": "Test conexiune DB", "lines": [("err", f"{st['engine']}: OFFLINE ({st['error'] or 'fără detalii'})")]}
    lines = [("ok", f"{st['engine']}: conectat, răspuns în {st['latency_ms']} ms" + (f", versiune {st['version']}" if st["version"] else ""))]
    try:
        import store as cim
        conn = cim.get_connection()
        try:
            n_loc = conn.execute(f"SELECT COUNT(*) AS n FROM locuri_de_veci WHERE {cim.tenant_scope()}").fetchone()["n"]
            n_ctr = conn.execute(f"SELECT COUNT(*) AS n FROM contracte WHERE {cim.tenant_scope()}").fetchone()["n"]
        finally:
            conn.close()
        lines.append(("ok", f"Citire de probă: {n_loc} locuri, {n_ctr} contracte"))
    except Exception as exc:
        lines.append(("err", f"Citire de probă eșuată: {type(exc).__name__}"))
    if st["schema_version"] is not None:
        lines.append(("ok", f"Versiune schemă (migrații aplicate): {st['schema_version']}"))
    return {"ok": all(k == "ok" for k, _ in lines), "title": "Test conexiune DB", "lines": lines}


# ------------------------------------------------------------------ storage
def _with_timeout(fn, seconds: float):
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(fn).result(timeout=seconds)
    finally:
        pool.shutdown(wait=False)


def storage_status() -> Dict[str, Any]:
    """HEAD pe o cheie de probă (max 5 s): ONLINE dacă storage-ul răspunde, indiferent dacă obiectul există."""
    import cimitir_storage as cs
    try:
        s = cs.get_storage()
        info = s.describe()
        _with_timeout(lambda: s.exists("_healthcheck/probe"), 5)
        return {"online": True, "driver": info.get("driver"), "location": info.get("bucket") or "", "error": ""}
    except FutureTimeout:
        return {"online": False, "driver": cs.driver_name(), "location": "", "error": "timeout (5 s)"}
    except Exception as exc:
        return {"online": False, "driver": cs.driver_name(), "location": "", "error": redact(f"{type(exc).__name__}: {exc}")[:200]}


def test_storage() -> Dict[str, Any]:
    """Scriere + citire + sha256 + ștergere a unui obiect de probă `_healthcheck/<uuid>` (nu atinge documentele)."""
    import cimitir_storage as cs
    lines: List[tuple] = []
    tmp = Path(cimitir_config.data_dir()) / f".storage-probe-{uuid.uuid4().hex}"
    key = f"_healthcheck/{uuid.uuid4().hex}.txt"
    payload = f"cimitir storage probe {datetime.now().isoformat()}".encode()
    try:
        s = cs.get_storage()
        info = s.describe()
        lines.append(("ok", f"Driver: {info.get('driver')}" + (f", bucket {info['bucket']}" if info.get("bucket") else "")))
        tmp.write_bytes(payload)
        t0 = time.perf_counter()
        _with_timeout(lambda: s.put_file(key, str(tmp), "text/plain"), 30)
        lines.append(("ok", f"Scriere reușită ({round((time.perf_counter() - t0) * 1000)} ms)"))
        same = hashlib.sha256(_with_timeout(lambda: s.get_bytes(key), 30)).hexdigest() == hashlib.sha256(payload).hexdigest()
        lines.append(("ok" if same else "err", "Citire + sha256: identic" if same else "Citire: conținut diferit!"))
        _with_timeout(lambda: s.delete(key), 30)
        gone = not _with_timeout(lambda: s.exists(key), 30)
        lines.append(("ok" if gone else "warn", "Ștergere obiect de probă reușită" if gone else "Obiectul de probă nu s-a șters"))
    except FutureTimeout:
        lines.append(("err", "Storage-ul nu a răspuns la timp"))
    except Exception as exc:
        lines.append(("err", redact(f"Storage indisponibil: {type(exc).__name__}: {exc}")[:300]))
    finally:
        tmp.unlink(missing_ok=True)
    return {"ok": all(k == "ok" for k, _ in lines), "title": "Test storage", "lines": lines}


def smtp_status() -> Dict[str, Any]:
    import cimitir_notify
    cfg = cimitir_notify.smtp_settings()
    configured = bool(cfg["host"] and cfg["from"])
    return {"configured": configured, "host": cfg["host"] if configured else "", "provider": cimitir_notify.provider_name()}


# ------------------------------------------------------------------ backup
def _pg_env() -> Dict[str, str]:
    url = urlparse(os.environ.get("DATABASE_URL", ""))
    if not url.hostname:
        raise RuntimeError("DATABASE_URL lipsește")
    env = {**os.environ, "PGHOST": url.hostname, "PGPORT": str(url.port or 5432), "PGUSER": unquote(url.username or ""),
           "PGDATABASE": (url.path or "/").lstrip("/")}
    if url.password:
        env["PGPASSWORD"] = unquote(url.password)
    ssl = parse_qs(url.query).get("sslmode")
    if ssl:
        env["PGSSLMODE"] = ssl[0]
    return env


def pg_backup(dest_root: Path) -> Path:
    """`pg_dump` (format custom) în `cimitir-<data>/` cu manifest.json (sha256). Parola merge doar prin mediu, nu pe linia de comandă."""
    exe = shutil.which("pg_dump")
    if not exe:
        raise RuntimeError("pg_dump nu este instalat pe server")
    target = Path(dest_root) / f"cimitir-{datetime.now():%Y%m%d-%H%M%S}"
    while target.exists():
        time.sleep(1)
        target = Path(dest_root) / f"cimitir-{datetime.now():%Y%m%d-%H%M%S}"
    target.mkdir(parents=True)
    dump = target / "database.dump"
    proc = subprocess.run([exe, "--format=custom", "--no-owner", "--no-privileges", "--file", str(dump)],
                          env=_pg_env(), capture_output=True, text=True, timeout=600)
    if proc.returncode != 0 or not dump.is_file() or dump.stat().st_size == 0:
        shutil.rmtree(target, ignore_errors=True)
        err = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else f"cod {proc.returncode}"
        raise RuntimeError("pg_dump a eșuat: " + redact(err)[:300])
    h = hashlib.sha256()
    with open(dump, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    (target / "manifest.json").write_text(json.dumps({"created": datetime.now().isoformat(), "engine": "postgres",
                                                       "files": {"database.dump": h.hexdigest()}}, indent=1), encoding="utf-8")
    return target


def fmt_bytes(n: Optional[int]) -> str:
    if n is None:
        return "—"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return "—"


def backup_now(dest_root: Path, get_setting) -> Dict[str, Any]:
    import cimitir_backup
    started = time.perf_counter()
    try:
        if engine() == "postgres":
            Path(dest_root).mkdir(parents=True, exist_ok=True)
            target = pg_backup(Path(dest_root))
            cimitir_backup.prune(Path(dest_root), int(get_setting("backup_keep_days") or "14"))
        else:
            target = cimitir_backup.run_scheduled(Path(cimitir_config.working_dir()), get_setting, force=True)
            if target is None:
                raise RuntimeError("copia de rezervă a eșuat")
    except Exception as exc:
        logging.getLogger("cimitir").error("backup manual eșuat: %s", redact(exc))
        return {"ok": False, "title": "Backup acum", "lines": [("err", redact(f"Backup eșuat: {exc}")[:400])]}
    size = sum(p.stat().st_size for p in Path(target).rglob("*") if p.is_file())
    logging.getLogger("cimitir").info("backup manual: %s (%s octeți)", Path(target).name, size)
    pg = engine() == "postgres"
    lines = [("ok", f"Copie creată: {Path(target).name} — {fmt_bytes(size)} în {round(time.perf_counter() - started, 1)} s"),
             ("ok", "Tip: " + ("pg_dump (format custom)" if pg else "copie SQLite + media") + f". Folder: {Path(target).parent.name}")]
    if pg:
        lines.append(("warn", "Restaurarea unei copii PostgreSQL se face manual cu pg_restore (nu din browser)."))
    return {"ok": True, "title": "Backup acum", "lines": lines}


def last_backup(dest_root: Path) -> Dict[str, Any]:
    import cimitir_backup
    rows = cimitir_backup.list_backups(Path(dest_root))
    if not rows:
        return {"exists": False, "name": "", "created": "", "size": 0, "age_hours": None, "count": 0}
    age = (datetime.now() - datetime.fromisoformat(rows[0]["created"])).total_seconds() / 3600
    return {"exists": True, "name": rows[0]["name"], "created": rows[0]["created"], "size": rows[0]["size"],
            "age_hours": round(age, 1), "count": len(rows)}


# ------------------------------------------------------------------ sincronizare
def _outbox_pending() -> Optional[int]:
    try:
        import store as cim
        conn = cim.get_connection()
        try:
            return int(conn.execute("SELECT COUNT(*) AS n FROM cloud_sync_outbox WHERE COALESCE(state,'pending')='pending'").fetchone()["n"])
        finally:
            conn.close()
    except Exception:
        return None


def sync_summary() -> Dict[str, Any]:
    import store as cim
    import sync_api
    try:
        clients = sync_api.clients_status()
    except Exception:
        clients = []
    stamps = [s for s in [c.get("last_success") for c in clients] + [cim.get_setting("sync_last_push_at") or ""] if s]
    reported = [c["pending"] for c in clients if c.get("pending") is not None]
    return {"last": max(stamps) if stamps else "", "outbox_here": _outbox_pending(),
            "pending_reported": sum(reported) if reported else (0 if clients else None),
            "clients": len(clients), "offline": sum(1 for c in clients if c.get("offline"))}


def _check_media_objects(sample: int = 25) -> List[tuple]:
    try:
        import cimitir_storage as cs
        import media_service
        with media_service._db() as conn:
            total = int(conn.execute("SELECT COUNT(*) AS n FROM media_objects").fetchone()["n"])
            rows = conn.execute("SELECT id, storage_path FROM media_objects ORDER BY uploaded_at DESC LIMIT ?", (sample,)).fetchall()
        s = cs.get_storage()
        missing = [r["id"] for r in rows if not _with_timeout(lambda p=r["storage_path"]: s.exists(p), 10)]
        return [("ok", f"Documente în storage: {total} înregistrate"),
                ("err" if missing else "ok", f"Ultimele {len(rows)} obiecte verificate: "
                 + (f"{len(missing)} LIPSĂ ({', '.join(m[:8] for m in missing[:5])})" if missing else "toate prezente"))]
    except Exception as exc:
        return [("warn", f"Verificarea documentelor a fost omisă: {type(exc).__name__}")]


def check_sync() -> Dict[str, Any]:
    """Verificări DOAR în citire: clienți MAIN, coadă, conflicte deschise, registru, obiecte din storage, cereri neconfirmate."""
    import store as cim
    import sync_api
    sm, lines = sync_summary(), []
    lines.append(("ok" if sm["last"] else "warn", f"Ultima sincronizare: {sm['last'] or 'niciodată'}"))
    if not sm["clients"]:
        lines.append(("warn", "Niciun client MAIN nu a raportat încă."))
    for c in sync_api.clients_status():
        who = c["client_id"] + (f" ({c['host']})" if c.get("host") else "")
        if c.get("offline"):
            lines.append(("warn", f"{who}: OFFLINE din {c.get('last_seen') or '?'}"))
        elif c.get("stale"):
            lines.append(("warn", f"{who}: raport vechi ({c.get('last_seen') or '?'})"))
        else:
            lines.append(("ok", f"{who}: activ, ultima sincronizare reușită {c.get('last_success') or '—'}"))
        if c.get("pending"):
            lines.append(("warn", f"{who}: {c['pending']} operații în coadă la ultimul raport"))
        if c.get("conflicts"):
            lines.append(("warn", f"{who}: {c['conflicts']} conflict(e) de rezolvat în MAIN"))
        if c.get("last_error") and c.get("error_active"):
            lines.append(("err", redact(f"{who}: eroare activă — {c['last_error']}")[:300]))
    if sm["outbox_here"]:
        lines.append(("warn", f"Outbox pe acest nod: {sm['outbox_here']} operații în așteptare"))
    if engine() == "postgres":
        try:
            conn = cim.get_connection()
            try:
                open_n = int(conn.execute(f"SELECT COUNT(*) AS n FROM sync_conflicts WHERE resolved_at IS NULL AND {cim.tenant_scope()}").fetchone()["n"])
                counts = {t: int(conn.execute(f"SELECT COUNT(*) AS n FROM {t} WHERE {cim.tenant_scope()}").fetchone()["n"])
                          for t in ("locuri_de_veci", "concesionari", "contracte", "inhumati", "plati")}
            finally:
                conn.close()
            lines.append(("warn" if open_n else "ok", f"Conflicte de entitate deschise: {open_n}"))
            lines.append(("ok", "Registru pe server: " + ", ".join(f"{k} {v}" for k, v in counts.items())))
        except Exception as exc:
            lines.append(("err", f"Verificarea registrului a eșuat: {type(exc).__name__}"))
        lines += _check_media_objects()
    else:
        lines.append(("ok", "Mod SQLite: sincronizarea pe entități (PostgreSQL) nu se aplică."))
    try:
        conn = cim.get_connection()
        try:
            unacked = int(conn.execute("SELECT COUNT(*) AS n FROM cereri_concesionare WHERE COALESCE(acked,0)=0").fetchone()["n"])
        finally:
            conn.close()
        lines.append(("warn" if unacked else "ok", f"Cereri neconfirmate de MAIN: {unacked}"))
    except Exception:
        pass
    return {"ok": all(k == "ok" for k, _ in lines), "title": "Verifică sincronizarea", "lines": lines}


# ------------------------------------------------------------------ verificare producție (tip bază de date + persistență)
_CLOUD_PROVIDERS = [
    ("Supabase", (".supabase.co", ".supabase.com")), ("Neon", (".neon.tech",)), ("AWS (RDS/Aurora)", (".rds.amazonaws.com", ".amazonaws.com")),
    ("Azure", (".postgres.database.azure.com", ".azure.com")), ("Google Cloud SQL", (".googleapis.com", ".cloudsql.net")),
    ("Render", (".render.com",)), ("Railway", (".railway.app", ".rlwy.net")), ("Aiven", (".aivencloud.com",)),
    ("DigitalOcean", (".ondigitalocean.com",)), ("CockroachDB", (".cockroachlabs.cloud",)), ("ElephantSQL", (".elephantsql.com",)),
    ("Crunchy Bridge", (".postgresbridge.com",)), ("Timescale", (".tsdb.cloud.timescale.com", ".timescaledb.io")),
]
_LOCAL_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".localdomain", ".home", ".svc", ".cluster.local")
_DEV_PORTS = {55432}
_DEV_DB_RE = re.compile(r"(?:^|[_-])(dev|devel|development|test|testing|local|demo|sandbox)(?:$|[_-])", re.I)
PROBE_KEY = "_probe:persistence:active"
RESULT_KEY = "_probe:persistence:result"
PROBE_MARKER = "CIMITIR-PERSISTENCE-PROBE"


def _host_class(host: str) -> str:
    """'socket' | 'loopback' | 'private' | 'public'. Numele fără punct (ex. serviciu docker) și sufixele interne = private."""
    h = (host or "").strip().strip("[]").lower()
    if not h or h.startswith("/"):
        return "socket"
    if h in ("localhost", "ip6-localhost"):
        return "loopback"
    try:
        ip = ipaddress.ip_address(h)
        if ip.is_loopback or ip.is_unspecified:
            return "loopback"
        return "private" if (ip.is_private or ip.is_link_local or ip.is_reserved) else "public"
    except ValueError:
        pass
    if "." not in h or h.endswith(_LOCAL_SUFFIXES):
        return "private"
    return "public"


def _provider(host: str) -> str:
    h = (host or "").lower()
    for name, suffixes in _CLOUD_PROVIDERS:
        if any(h.endswith(s) or h == s.lstrip(".") for s in suffixes):
            return name
    return ""


def classify_database(eng: str, url: str) -> Dict[str, Any]:
    """Tipul bazei: sqlite | postgres_local | postgres_cloud | postgres_unconfigured. CIMITIR_DB_ENGINE=postgres NU dovedește cloud:
    contează gazda din DATABASE_URL (loopback/rețea privată = local), furnizorul cunoscut și TLS. Rezultatul nu conține gazda, utilizatorul sau parola."""
    base: Dict[str, Any] = {"engine": eng, "provider": "", "host_class": "", "tls": None, "fingerprint": "", "checks": []}
    note = ("warn", "CIMITIR_DB_ENGINE=postgres doar alege motorul; nu dovedește că baza este cloud sau persistentă. Se analizează DATABASE_URL.")
    if eng != "postgres":
        base.update(kind="sqlite", label="SQLite (local)", tone="warn", checks=[
            ("warn", "Motor SQLite: fișier local pe acest calculator/server. Nu este bază PostgreSQL cloud."),
            ("warn", "Pentru producție online este nevoie de PostgreSQL cloud persistent.")])
        return base
    parsed = urlparse(url or "")
    if not parsed.hostname and not (url or "").strip():
        base.update(kind="postgres_unconfigured", label="PostgreSQL nesetat (DATABASE_URL lipsește)", tone="err",
                    checks=[note, ("err", "DATABASE_URL lipsește sau este gol.")])
        return base
    host, port, dbname = parsed.hostname or "", parsed.port or 5432, (parsed.path or "/").lstrip("/")
    ssl = (parse_qs(parsed.query).get("sslmode") or [""])[0].lower()
    tls = ssl in ("require", "verify-ca", "verify-full")
    hclass, provider = _host_class(host), _provider(host)
    base.update(provider=provider, host_class=hclass, tls=tls,
                fingerprint=hashlib.sha256(f"{host}:{port}/{dbname}".encode()).hexdigest()[:12])
    dev_hints = port in _DEV_PORTS or bool(_DEV_DB_RE.search(dbname))
    checks = [note]
    if hclass in ("socket", "loopback", "private"):
        names = {"socket": "socket Unix", "loopback": "loopback (127.0.0.1/localhost)", "private": "rețea privată/internă"}
        checks += [("warn", f"Gazdă: {names[hclass]} → PostgreSQL local/dezvoltare, nu cloud."),
                   ("warn", "Datele nu sunt păstrate de un furnizor cloud: se pierd dacă mediul (pod/PC) este recreat.")]
        if dev_hints:
            checks.append(("warn", "Port sau nume de bază de dezvoltare/test detectat."))
        base.update(kind="postgres_local", label="PostgreSQL local / dezvoltare", tone="warn", checks=checks)
        return base
    checks.append(("ok", f"Gazdă publică; furnizor: {provider or 'necunoscut (neconfirmat)'} → candidat PostgreSQL cloud."))
    checks.append(("ok", "TLS cerut (sslmode=" + ssl + ")") if tls else ("warn", f"TLS neobligatoriu (sslmode={ssl or 'implicit'}); pentru cloud folosește sslmode=require."))
    if not provider:
        checks.append(("warn", "Furnizor necunoscut: confirmă manual că este o bază gestionată și persistentă."))
    if dev_hints:
        checks.append(("warn", "Numele bazei sau portul sugerează dezvoltare/test."))
    if port == 6543:
        checks.append(("warn", "Port 6543 = pooler în mod tranzacție (PgBouncer): aplicația folosește advisory locks de sesiune; folosește conexiunea directă (5432) sau poolerul în mod sesiune."))
    base.update(kind="postgres_cloud", label="PostgreSQL cloud" + (f" ({provider})" if provider else ""), tone="ok", checks=checks)
    return base


def _setting_json(key: str) -> Optional[dict]:
    import store as cim
    try:
        value = cim.get_setting(key)
        data = json.loads(value) if value else None
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _delete_probe_keys() -> None:
    import store as cim
    conn = cim.get_connection()
    try:
        conn.execute("DELETE FROM settings WHERE key = ?", (PROBE_KEY,))
    finally:
        conn.close()


def _boot_id() -> str:
    return STARTED_AT.isoformat(timespec="microseconds")


def persistence_state(fingerprint: str) -> Dict[str, Any]:
    """Starea ultimei probe/rezultat. `verified` doar dacă rezultatul OK aparține aceleiași baze (amprentă gazdă:port/bază)."""
    probe, result = _setting_json(PROBE_KEY), _setting_json(RESULT_KEY)
    return {"probe": probe, "result": result,
            "verified": bool(result and result.get("ok") and result.get("fingerprint") == fingerprint),
            "stale_result": bool(result and result.get("ok") and result.get("fingerprint") != fingerprint)}


def production_check() -> Dict[str, Any]:
    """Nivel: sqlite | pg_local | pg_cloud_unverified | pg_cloud_persistent | pg_unconfigured. Doar `pg_cloud_persistent` = gata de producție."""
    cls = classify_database(engine(), os.environ.get("DATABASE_URL", ""))
    pers = persistence_state(cls["fingerprint"]) if db_status()["online"] else {"probe": None, "result": None, "verified": False, "stale_result": False}
    if cls["kind"] == "postgres_cloud":
        level = "pg_cloud_persistent" if pers["verified"] else "pg_cloud_unverified"
    else:
        level = {"sqlite": "sqlite", "postgres_local": "pg_local"}.get(cls["kind"], "pg_unconfigured")
    labels = {"sqlite": "SQLITE · local", "pg_local": "POSTGRESQL LOCAL / DEZVOLTARE", "pg_unconfigured": "POSTGRESQL NECONFIGURAT",
              "pg_cloud_unverified": "POSTGRESQL CLOUD · persistență neconfirmată", "pg_cloud_persistent": "POSTGRESQL CLOUD · PERSISTENT CONFIRMAT"}
    tone = "ok" if level == "pg_cloud_persistent" else ("err" if level == "pg_unconfigured" else "warn")
    res = pers["result"] or {}
    last = ("Niciun test de persistență rulat." if not res else
            f"Ultimul test: {'REUȘIT' if res.get('ok') else 'EȘUAT'} la {str(res.get('verified_at') or '')[:19].replace('T', ' ')}"
            + (" (altă bază, nu mai este valabil)" if pers["stale_result"] else ""))
    return {"level": level, "label": labels[level], "tone": tone, "ready": level == "pg_cloud_persistent", "db": cls,
            "persistence": {**pers, "summary": last, "probe_pending": bool(pers["probe"])}}


def persistence_create(created_by: str = "") -> Dict[str, Any]:
    """Pasul 1: scrie O înregistrare tehnică (cheie `_probe:persistence:active` în `settings`, cu marker și UUID) și o recitește. Nu atinge date reale."""
    import store as cim
    title = "Test persistență · pasul 1 (creare probă)"
    if not db_status()["online"]:
        return {"ok": False, "title": title, "lines": [("err", "Baza de date este OFFLINE; nu se poate crea proba.")]}
    existing = _setting_json(PROBE_KEY)
    if existing:
        return {"ok": False, "title": title, "lines": [("warn", f"Există deja o probă ({str(existing.get('id'))[:8]}), creată la {str(existing.get('created_at'))[:19].replace('T', ' ')}."),
                                                        ("warn", "Repornește serverul și apasă „Verifică după restart”, sau curăță proba.")]}
    probe = {"marker": PROBE_MARKER, "id": uuid.uuid4().hex, "created_at": datetime.now().isoformat(timespec="seconds"),
             "boot_id": _boot_id(), "created_by": created_by[:60],
             "fingerprint": classify_database(engine(), os.environ.get("DATABASE_URL", ""))["fingerprint"]}
    cim.set_setting(PROBE_KEY, json.dumps(probe))
    back = _setting_json(PROBE_KEY)
    if not back or back.get("id") != probe["id"]:
        return {"ok": False, "title": title, "lines": [("err", "Proba a fost scrisă, dar nu a putut fi recitită.")]}
    return {"ok": True, "title": title, "lines": [
        ("ok", f"Probă tehnică creată și recitită ({probe['id'][:8]}); folosește doar cheia izolată {PROBE_KEY}."),
        ("warn", "ACUM repornește serverul (ex. START_SERVER.bat sau restart supervisor), apoi apasă „Verifică după restart”."),
        ("ok", "Nicio dată reală (locuri, contracte, înhumați) nu a fost modificată.")]}


def persistence_verify() -> Dict[str, Any]:
    """Pasul 2: dacă serverul a fost repornit și proba există, persistența este confirmată; proba se șterge. Dacă lipsește după restart, testul EȘUEAZĂ."""
    title = "Test persistență · pasul 2 (verificare după restart)"
    if not db_status()["online"]:
        return {"ok": False, "title": title, "lines": [("err", "Baza de date este OFFLINE; nu se poate verifica.")]}
    probe = _setting_json(PROBE_KEY)
    fp = classify_database(engine(), os.environ.get("DATABASE_URL", ""))["fingerprint"]
    if not probe or probe.get("marker") != PROBE_MARKER:
        lost = _setting_json(RESULT_KEY) is None
        return {"ok": False, "title": title, "lines": [("warn", "Nu există nicio probă activă. Apasă mai întâi „Creează probă”." + ("" if lost else " (Rezultatul anterior rămâne valabil.)"))]}
    if probe.get("boot_id") == _boot_id():
        return {"ok": False, "title": title, "lines": [
            ("ok", f"Proba {str(probe['id'])[:8]} există în baza de date."),
            ("warn", "ÎN AȘTEPTARE: serverul nu a fost repornit de la crearea probei, deci persistența nu este încă dovedită."),
            ("warn", "Repornește serverul, apoi apasă din nou „Verifică după restart”.")]}
    if probe.get("fingerprint") != fp:
        _delete_probe_keys()
        return {"ok": False, "title": title, "lines": [("err", "Proba a fost creată pe altă bază de date (DATABASE_URL s-a schimbat). Proba a fost curățată; repetă testul.")]}
    import store as cim
    result = {"ok": True, "verified_at": datetime.now().isoformat(timespec="seconds"), "fingerprint": fp, "probe_id": probe["id"],
              "created_at": probe.get("created_at"), "boots_apart": True}
    cim.set_setting(RESULT_KEY, json.dumps(result))
    _delete_probe_keys()
    cleaned = _setting_json(PROBE_KEY) is None
    return {"ok": cleaned, "title": title, "lines": [
        ("ok", f"Proba {str(probe['id'])[:8]} (creată {str(probe.get('created_at'))[:19].replace('T', ' ')}) a PERSISTAT după restartul serverului."),
        ("ok" if cleaned else "err", "Proba a fost ștearsă după verificare." if cleaned else "Proba NU a putut fi ștearsă; folosește „Curăță proba”."),
        ("warn", "Persistența este confirmată pentru această bază. Statutul „cloud” depinde separat de gazda din DATABASE_URL.")]}


def persistence_cleanup() -> Dict[str, Any]:
    title = "Test persistență · curățare probă"
    probe = _setting_json(PROBE_KEY)
    if not probe:
        return {"ok": True, "title": title, "lines": [("ok", "Nu există probă de curățat.")]}
    if probe.get("marker") != PROBE_MARKER:
        return {"ok": False, "title": title, "lines": [("err", "Cheia nu are markerul probei; nu a fost ștearsă.")]}
    _delete_probe_keys()
    gone = _setting_json(PROBE_KEY) is None
    return {"ok": gone, "title": title, "lines": [("ok" if gone else "err", "Proba a fost ștearsă (fără a conta ca verificare)." if gone else "Ștergerea a eșuat.")]}


def collect_status(backup_dir: Path) -> Dict[str, Any]:
    return {"server": {"online": True, "started_at": STARTED_AT.isoformat(timespec="seconds"), "mode": cimitir_config.mode()},
            "db": db_status(), "db_size": fmt_bytes(db_size_bytes()), "version": cimitir_config.app_version(),
            "sync": sync_summary(), "backup": last_backup(backup_dir), "backup_dir_name": Path(backup_dir).name,
            "prod": production_check(), "storage": storage_status(), "smtp": smtp_status(), "errors": error_stats(), "log_available": log_path().is_file()}
