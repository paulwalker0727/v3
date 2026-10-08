"""
cloud_config.py — fereastra „Administrare online -> Configurare Cloud” (doar administratori).

Principii:
- valorile se scriu DOAR în backend/.env (fișier ignorat de Git); nu în SQLite/PostgreSQL, nu în cod, nu în executabil;
- câmpurile secrete (DATABASE_URL conține parola, SUPABASE_SECRET_KEY) nu sunt NICIODATĂ trimise înapoi în pagină;
  câmp lăsat gol la salvare = „păstrează valoarea existentă”;
- mesajele de eroare nu conțin valori: se întorc doar clasa erorii și un indiciu fix (autentificare, DNS, SSL, timeout, HTTP);
- dacă backend-ul rulează pe un server unde .env nu poate fi scris (sau CIMITIR_CLOUD_CONFIG_READONLY=1), formularul e dezactivat
  și se afișează că secretele se configurează pe server; nimic nu se trimite automat.
"""
from __future__ import annotations

import os
import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

BACKEND_DIR = Path(__file__).resolve().parent
SECTION_HEADER = ("# ==================================================\n"
                  "# CONFIGURARE CLOUD - SUPABASE\n"
                  "# ==================================================")

FIELDS = ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_BUCKET", "CIMITIR_STORAGE_DRIVER")
SECRET_FIELDS = ("DATABASE_URL", "SUPABASE_SECRET_KEY")  # DATABASE_URL conține parola bazei de date
DEFAULTS = {"SUPABASE_BUCKET": "cimitir-private", "CIMITIR_STORAGE_DRIVER": "supabase"}
LABELS = {"DATABASE_URL": "PostgreSQL DATABASE_URL", "SUPABASE_URL": "Supabase URL",
          "SUPABASE_SECRET_KEY": "Supabase Secret Key", "SUPABASE_BUCKET": "Bucket",
          "CIMITIR_STORAGE_DRIVER": "Driver storage"}
DRIVERS = ("supabase", "local", "s3")
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")
_write_lock = threading.Lock()


# ------------------------------------------------------------------ fișierul .env
def env_path() -> Path:
    raw = os.environ.get("CIMITIR_CLOUD_ENV_FILE", "").strip()
    return Path(raw) if raw else BACKEND_DIR / ".env"


def _parse(line: str) -> Optional[Tuple[str, str]]:
    s = line.strip()
    if not s or s.startswith("#") or "=" not in s:
        return None
    key, _, value = s.partition("=")
    key, value = key.strip(), value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return (key, value) if key else None


def file_values() -> Dict[str, str]:
    try:
        text = env_path().read_text(encoding="utf-8")
    except OSError:
        return {}
    return {k: v for k, v in (p for p in map(_parse, text.splitlines()) if p)}


def is_readonly() -> Tuple[bool, str]:
    """(readonly, motiv). Readonly dacă e cerut explicit sau dacă .env nu poate fi scris de acest proces."""
    if os.environ.get("CIMITIR_CLOUD_CONFIG_READONLY", "").strip() == "1":
        return True, "Configurarea din interfață este dezactivată pe acest server (CIMITIR_CLOUD_CONFIG_READONLY=1)."
    p = env_path()
    try:
        ok = os.access(p, os.W_OK) if p.exists() else os.access(p.parent, os.W_OK)
    except OSError:
        ok = False
    if not ok:
        return True, "Fișierul backend/.env nu poate fi scris de acest server."
    return False, ""


def current(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def effective(name: str) -> str:
    return current(name) or DEFAULTS.get(name, "")


def secret_key_value() -> str:
    return current("SUPABASE_SECRET_KEY") or current("SUPABASE_SERVICE_ROLE_KEY")


def field_states() -> Dict[str, Dict[str, Any]]:
    """Pentru fiecare câmp: configurat? sursa (fișier / mediul serverului / lipsă). FĂRĂ valori pentru câmpurile secrete."""
    fv = file_values()
    out: Dict[str, Dict[str, Any]] = {}
    for name in FIELDS:
        env_v = secret_key_value() if name == "SUPABASE_SECRET_KEY" else current(name)
        file_v = (fv.get(name) or "").strip()
        if name == "SUPABASE_SECRET_KEY" and not file_v:
            file_v = (fv.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
        if file_v and env_v:
            source = "file"
        elif env_v:
            source = "server"
        elif file_v:
            source = "file_pending"  # scris în backend/.env, dar încă neîncărcat în procesul curent (necesită restart)
        else:
            source = "none"
        out[name] = {"configured": bool(env_v or file_v), "source": source,
                     "value": "" if name in SECRET_FIELDS else (env_v or DEFAULTS.get(name, ""))}
    return out


def is_complete() -> bool:
    return bool(current("DATABASE_URL") and current("SUPABASE_URL") and secret_key_value()) \
        and bool(effective("SUPABASE_BUCKET")) and effective("CIMITIR_STORAGE_DRIVER") == "supabase"


# ------------------------------------------------------------------ validare
def validate(values: Dict[str, str]) -> Tuple[Dict[str, str], List[str], List[str]]:
    """Normalizează și validează valorile NOI (doar cele nevide). Întoarce (valori, erori, avertismente). Fără valori în mesaje."""
    clean: Dict[str, str] = {}
    errors: List[str] = []
    warnings: List[str] = []
    for name in FIELDS:
        raw = values.get(name)
        if raw is None:
            continue
        v = str(raw).strip()
        if not v:
            continue
        if re.search(r"[\r\n\x00\s]", v):
            errors.append(f"{LABELS[name]}: valoarea nu poate conține spații sau linii noi.")
            continue
        if name == "DATABASE_URL":
            u = urlparse(v)
            if u.scheme not in ("postgresql", "postgres") or not u.hostname:
                errors.append("DATABASE_URL: format invalid (postgresql://utilizator:parola@gazdă:5432/bază).")
                continue
            if u.port == 6543:
                warnings.append("DATABASE_URL folosește portul 6543 (Transaction Pooler). Aplicația cere conexiune directă sau Session Pooler, port 5432.")
            if "sslmode=" not in (u.query or "") and u.hostname not in ("localhost", "127.0.0.1", "::1"):
                warnings.append("DATABASE_URL nu are ?sslmode=require; pentru servicii externe se recomandă.")
        elif name == "SUPABASE_URL":
            u = urlparse(v)
            if u.scheme != "https" or not u.hostname:
                errors.append("SUPABASE_URL: trebuie să fie de forma https://<proiect>.supabase.co")
                continue
            v = v.rstrip("/")
        elif name == "SUPABASE_SECRET_KEY":
            if len(v) < 20 or '"' in v or "'" in v:
                errors.append("SUPABASE_SECRET_KEY: valoare invalidă (prea scurtă sau cu ghilimele).")
                continue
        elif name == "SUPABASE_BUCKET":
            if not _BUCKET_RE.match(v):
                errors.append("SUPABASE_BUCKET: doar litere mici, cifre, - și _ (2-63 caractere).")
                continue
        elif name == "CIMITIR_STORAGE_DRIVER":
            v = v.lower()
            if v not in DRIVERS:
                errors.append("CIMITIR_STORAGE_DRIVER: valori permise: " + ", ".join(DRIVERS) + ".")
                continue
        clean[name] = v
    return clean, errors, warnings


# ------------------------------------------------------------------ scriere
def write_values(updates: Dict[str, str]) -> List[str]:
    """Actualizează DOAR cheile date în backend/.env (restul fișierului rămâne neschimbat). Scriere atomică. Întoarce cheile modificate."""
    if not updates:
        return []
    path = env_path()
    with _write_lock:
        try:
            text = path.read_bytes().decode("utf-8") if path.exists() else ""
        except OSError as exc:
            raise RuntimeError(f"backend/.env nu poate fi citit ({type(exc).__name__})") from None
        eol = "\r\n" if "\r\n" in text else "\n"
        lines = text.splitlines()
        pending = dict(updates)
        changed: List[str] = []
        for i, line in enumerate(lines):
            item = _parse(line)
            if item and item[0] in pending:
                key = item[0]
                new = pending.pop(key)
                if item[1] != new:
                    changed.append(key)
                lines[i] = f"{key}={new}"
        if pending:
            has_section = any("CONFIGURARE CLOUD - SUPABASE" in ln for ln in lines)
            if lines and lines[-1].strip():
                lines.append("")
            if not has_section:
                lines += SECTION_HEADER.split("\n") + [""]
            for key, val in pending.items():
                lines.append(f"{key}={val}")
                changed.append(key)
        data = (eol.join(lines) + eol).encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, path)
        except OSError as exc:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise RuntimeError(f"Scrierea în backend/.env a eșuat ({type(exc).__name__})") from None
    return changed


def apply_runtime(updates: Dict[str, str]) -> None:
    """Reîncarcă configurația în procesul curent (fără restart): mediul, pool-ul PostgreSQL și cache-ul storage."""
    for k, v in updates.items():
        os.environ[k] = v
    try:
        import pg_compat
        pg_compat.close_pool()
        pg_compat._fail_until = 0.0
    except Exception:
        pass
    try:
        import cimitir_storage
        cimitir_storage.reset_storage()
    except Exception:
        pass


def restart_hint() -> List[str]:
    """Mesaje clare despre ce NU se poate aplica fără restart."""
    import cimitir_config
    notes = []
    if cimitir_config.db_engine() != "postgres" or cimitir_config.mode() != "online":
        notes.append("Serverul rulează cu CIMITIR_MODE/CIMITIR_DB_ENGINE diferite de online/postgres. "
                     "Verifică-le în backend/.env și repornește serverul (RESTART_BACKEND.bat).")
    return notes


# ------------------------------------------------------------------ mascarea erorilor
def _pg_hint(exc: BaseException) -> str:
    low = f"{type(exc).__name__} {exc}".lower()
    if "password authentication" in low or "authentication failed" in low or "no password supplied" in low:
        return "autentificare eșuată (utilizator sau parolă)"
    if "translate host" in low or "name or service not known" in low or "nodename nor servname" in low or "getaddrinfo" in low:
        return "gazdă necunoscută (DNS)"
    if "timeout" in low or "timed out" in low:
        return "timeout la conectare"
    if "ssl" in low:
        return "problemă SSL (încearcă ?sslmode=require)"
    if "refused" in low:
        return "conexiune refuzată (port/gazdă)"
    if "does not exist" in low:
        return "baza de date sau utilizatorul nu există"
    return "conexiune imposibilă"


# ------------------------------------------------------------------ teste (nu modifică starea aplicației)
def _eff(values: Dict[str, str], name: str) -> str:
    v = (values.get(name) or "").strip()
    if v:
        return v
    return secret_key_value() if name == "SUPABASE_SECRET_KEY" else current(name) or DEFAULTS.get(name, "")


def test_postgres(values: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Conectare + SELECT 1 cu valorile din formular (câmp gol = valoarea salvată). Nu scrie nimic."""
    import time
    values = values or {}
    url = (values.get("DATABASE_URL") or "").strip() or current("DATABASE_URL")
    if not url:
        return {"ok": False, "state": "NECONFIGURAT", "title": "PostgreSQL", "lines": [("err", "DATABASE_URL nu este configurat.")]}
    clean, errors, _ = validate({"DATABASE_URL": url})
    if errors:
        return {"ok": False, "state": "OFFLINE", "title": "PostgreSQL", "lines": [("err", e) for e in errors]}
    try:
        import psycopg
        t0 = time.perf_counter()
        with psycopg.connect(url, connect_timeout=8, autocommit=True) as conn:
            conn.execute("SELECT 1").fetchone()
        ms = round((time.perf_counter() - t0) * 1000)
        return {"ok": True, "state": "ONLINE", "title": "PostgreSQL", "lines": [("ok", f"PostgreSQL: ONLINE (răspuns în {ms} ms)")]}
    except Exception as exc:
        return {"ok": False, "state": "OFFLINE", "title": "PostgreSQL",
                "lines": [("err", f"PostgreSQL: OFFLINE ({type(exc).__name__}: {_pg_hint(exc)})")]}


def test_storage(values: Optional[Dict[str, str]] = None, create_bucket: bool = False) -> Dict[str, Any]:
    """Verifică Supabase Storage: bucketul există și este PRIVAT. Opțional creează bucketul privat dacă lipsește."""
    values = values or {}
    driver = _eff(values, "CIMITIR_STORAGE_DRIVER").lower()
    if driver != "supabase":
        return {"ok": False, "state": "NEAPLICABIL", "title": "Supabase Storage",
                "lines": [("warn", f"CIMITIR_STORAGE_DRIVER={driver or 'local'}: testul Supabase nu se aplică. Setează supabase pentru cloud.")]}
    url, key, bucket = _eff(values, "SUPABASE_URL"), _eff(values, "SUPABASE_SECRET_KEY"), _eff(values, "SUPABASE_BUCKET")
    missing = [LABELS[n] for n, v in (("SUPABASE_URL", url), ("SUPABASE_SECRET_KEY", key), ("SUPABASE_BUCKET", bucket)) if not v]
    if missing:
        return {"ok": False, "state": "NECONFIGURAT", "title": "Supabase Storage",
                "lines": [("err", "Lipsesc: " + ", ".join(missing) + ".")]}
    clean, errors, _ = validate({"SUPABASE_URL": url, "SUPABASE_SECRET_KEY": key, "SUPABASE_BUCKET": bucket})
    if errors:
        return {"ok": False, "state": "OFFLINE", "title": "Supabase Storage", "lines": [("err", e) for e in errors]}
    import cimitir_storage as cs
    try:
        store = cs.SupabaseStorage(clean["SUPABASE_URL"], clean["SUPABASE_SECRET_KEY"], clean["SUPABASE_BUCKET"])
        info = store.bucket_info()
        lines: List[tuple] = []
        if info is None and create_bucket and store.auth_ok():
            lines.append(("ok", f"Bucket {store.ensure_bucket()} (privat)."))
            info = store.bucket_info()
        if info is None and not store.auth_ok():
            return {"ok": False, "state": "OFFLINE", "title": "Supabase Storage", "auth_ok": False,
                    "lines": lines + [("err", "Supabase Storage: OFFLINE (cheia sau URL-ul sunt respinse: autentificare eșuată)")]}
        if info is None:
            return {"ok": False, "state": "OFFLINE", "title": "Supabase Storage", "auth_ok": True,
                    "lines": [("err", "Supabase Storage: conectat, dar bucketul nu există. Bifează „Creează bucketul privat dacă lipsește”.")]}
        if info.get("public"):
            return {"ok": False, "state": "OFFLINE", "title": "Supabase Storage", "auth_ok": True,
                    "lines": lines + [("err", "Bucketul este PUBLIC; documentele trebuie păstrate într-un bucket PRIVAT.")]}
        return {"ok": True, "state": "ONLINE", "title": "Supabase Storage",
                "lines": lines + [("ok", "Supabase Storage: ONLINE"), ("ok", "Bucket: PRIVAT")]}
    except Exception as exc:
        m = re.search(r"Supabase (\d{3})", str(exc))
        code = m.group(1) if m else ""
        hint = {"401": "cheie respinsă", "403": "cheie respinsă / permisiuni", "404": "URL sau bucket greșit"}.get(code, "")
        if not code:
            hint = "gazdă inaccesibilă (verifică SUPABASE_URL)"
        return {"ok": False, "state": "OFFLINE", "title": "Supabase Storage",
                "lines": [("err", f"Supabase Storage: OFFLINE ({type(exc).__name__}{', HTTP ' + code if code else ''}: {hint})")]}


def status() -> Dict[str, Any]:
    """Indicatori pentru pagină: testele rulează în paralel, fiecare cu timeout propriu."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        pg, st = pool.submit(test_postgres), pool.submit(test_storage)
        pg_r, st_r = pg.result(), st.result()
    return {"postgres": pg_r["state"], "storage": st_r["state"], "complete": is_complete()}
