"""cimitir_prov_pg.py — operațiuni PostgreSQL ale provizionării: verificare, migrări idempotente, rol runtime cu privilegii minime, backup/restore criptat.

Credențialele ADMINISTRATIVE (URL cu parolă) sunt primite per apel, folosite în memorie și NU se persistă/loghează. Rolul runtime primește o parolă
proprie, generată per instanță (seif), diferită de orice altă instanță.
"""
from __future__ import annotations

import base64
import hashlib
import importlib
import importlib.util
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, unquote, urlparse

from cimitir_prov_store import ProvisionError, redact_text

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROLE = "cimitir_cp_app"
LOCK_KEY = 7346102                      # aceeași cheie ca setup_manager / aplicația: serializare cu migrările aplicației
_SM = None


def setup_manager():
    """Reutilizează analiza/planificarea migrărilor din tools/setup_manager (aceeași logică ca Centrul Cloud)."""
    global _SM
    if _SM:
        return _SM
    pkg = ROOT / "tools" / "setup_manager"
    name = "_cimitir_sm_prov"
    spec = importlib.util.spec_from_file_location(name, pkg / "__init__.py", submodule_search_locations=[str(pkg)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    _SM = importlib.import_module(name + ".migrations")
    return _SM


def _psycopg():
    import psycopg
    return psycopg


def make_url(host: str, port: int, dbname: str, user: str, password: str, sslmode: str = "require") -> str:
    return f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{int(port)}/{quote(dbname, safe='')}?sslmode={sslmode}"


def split_url(url: str) -> Dict[str, Any]:
    p = urlparse(url)
    if p.scheme not in ("postgresql", "postgres") or not p.hostname:
        raise ProvisionError("BAD_DB_URL", "URL PostgreSQL invalid")
    return {"host": p.hostname, "port": p.port or 5432, "dbname": unquote(p.path.lstrip("/") or "postgres"), "user": unquote(p.username or ""),
            "password": unquote(p.password or ""), "sslmode": (dict(x.split("=", 1) for x in p.query.split("&") if "=" in x).get("sslmode") or "require")}


def connect(url: str, autocommit: bool = True, timeout: int = 10):
    try:
        return _psycopg().connect(url, connect_timeout=timeout, autocommit=autocommit)
    except Exception as exc:  # noqa: BLE001
        raise ProvisionError("DB_UNREACHABLE", "conectarea la PostgreSQL a eșuat: " + redact_text(f"{type(exc).__name__}: {str(exc)[:160]}"), 502) from None


def verify_admin(url: str) -> Dict[str, Any]:
    with connect(url) as c:
        ver = c.execute("SHOW server_version").fetchone()[0]
        r = c.execute("SELECT rolsuper, rolcreaterole, current_user, current_database() FROM pg_roles WHERE rolname=current_user").fetchone()
        if not (r[0] or r[1]):
            raise ProvisionError("DB_PRIVILEGES", "contul administrativ nu poate crea roluri (cerut pentru rolul runtime)", 400)
        n = c.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'").fetchone()[0]
        return {"server_version": ver, "database": r[3], "public_tables": n, "empty": n == 0}


def table_counts(c) -> Dict[str, int]:
    out = {}
    for (t,) in c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1").fetchall():
        out[t] = c.execute(f'SELECT count(*) FROM public."{t}"').fetchone()[0]
    return out


# ------------------------------------------------------------------ migrări
def plan_migrations(url: str, mig_dir: Path) -> Dict[str, Any]:
    sm = setup_manager()
    migs = sm.load_migrations(Path(mig_dir))
    if not migs:
        raise ProvisionError("NO_MIGRATIONS", "release fără migrări", 500)
    with connect(url, autocommit=False) as c:
        plan = sm.build_plan(sm.analyze(c, migs))
        c.rollback()
    return {"plan": plan, "migs": migs, "versions": [m.version for m in migs]}


def apply_migrations(url: str, mig_dir: Path) -> Dict[str, Any]:
    """Idempotent: planul vine din starea REALĂ a bazei; o singură tranzacție + lock consultativ; verificare după fiecare migrare; fără scădere a rândurilor."""
    sm = setup_manager()
    p = plan_migrations(url, mig_dir)
    plan, migs = p["plan"], p["migs"]
    if plan["blocked"]:
        raise ProvisionError("MIGRATION_BLOCKED", "migrări blocate: " + " | ".join(plan["blocked"])[:500], 409)
    if not plan["steps"]:
        return {"applied": [], "already_current": True, "log": []}
    by_ver = {m.version: m for m in migs}
    log: List[str] = []
    conn = connect(url, autocommit=False, timeout=20)
    try:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        conn.commit()
        try:
            fresh = sm.build_plan(sm.analyze(conn, migs))
            conn.rollback()
            if fresh["plan_hash"] != plan["plan_hash"]:
                raise ProvisionError("STATE_CHANGED", "starea bazei s-a schimbat în timpul planificării; reia operația", 409)
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
            before = table_counts(conn)
            for s in plan["steps"]:
                m = by_ver[s["version"]]
                if s["action"] == "apply":
                    conn.execute(m.path.read_text(encoding="utf-8"))
                ins = sm.inspect_migration(conn, m)
                if ins["missing"]:
                    raise RuntimeError(f"{m.name}: lipsesc {', '.join(ins['missing'][:6])}")
                conn.execute("INSERT INTO schema_migrations (version,name) SELECT %s,%s WHERE NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version=%s)", (m.version, m.name, m.version))
                log.append(f"{m.name}: {'aplicată' if s['action'] == 'apply' else 'înregistrată'}")
            after = table_counts(conn)
            bad = {t: (n, after.get(t)) for t, n in before.items() if after.get(t, -1) < n}
            if bad:
                raise RuntimeError(f"integritate: rânduri scăzute/tabele dispărute {bad}")
            conn.commit()
        except ProvisionError:
            conn.rollback()
            raise
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            raise ProvisionError("MIGRATION_FAILED", "migrare eșuată, ROLLBACK complet: " + redact_text(f"{type(exc).__name__}: {str(exc)[:300]}"), 500) from None
        finally:
            try:
                conn.rollback()
                conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
                conn.commit()
            except Exception:  # noqa: BLE001
                pass
    finally:
        conn.close()
    return {"applied": [s["version"] for s in plan["steps"]], "already_current": False, "log": log}


def migration_status(url: str, mig_dir: Path) -> Dict[str, Any]:
    p = plan_migrations(url, mig_dir)
    return {"current": not p["plan"]["steps"] and not p["plan"]["blocked"], "pending": [s["version"] for s in p["plan"]["steps"]], "blocked": p["plan"]["blocked"]}


# ------------------------------------------------------------------ rol runtime
def ensure_runtime_role(admin_url: str, password: str, role: str = RUNTIME_ROLE) -> Dict[str, Any]:
    """Rolul e creat de migrări (014); aici i se fixează parola proprie a instanței și atributele minime. Idempotent; rotația = același apel cu altă parolă."""
    from psycopg import sql
    with connect(admin_url) as c:
        exists = c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
        if not exists:
            raise ProvisionError("ROLE_MISSING", f"rolul {role} lipsește: migrările nu au fost aplicate", 409)
        c.execute(sql.SQL("ALTER ROLE {} WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB NOREPLICATION PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
        for other in ("cimitir_app",):                                   # roluri LOGIN din migrări, nefolosite de backend: fără parolă = fără autentificare
            if c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (other,)).fetchone():
                c.execute(sql.SQL("ALTER ROLE {} WITH PASSWORD NULL").format(sql.Identifier(other)))
        return role_report(c, role)


def role_report(c, role: str = RUNTIME_ROLE) -> Dict[str, Any]:
    r = c.execute("SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication, rolcanlogin FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
    members = [x[0] for x in c.execute("SELECT g.rolname FROM pg_auth_members m JOIN pg_roles g ON g.oid=m.roleid JOIN pg_roles u ON u.oid=m.member WHERE u.rolname=%s", (role,)).fetchall()]
    return {"role": role, "superuser": r[0], "bypassrls": r[1], "createrole": r[2], "createdb": r[3], "replication": r[4], "login": r[5], "member_of": members,
            "minimal": not any(r[:5]) and r[5] and not members}


def verify_runtime_login(runtime_url: str) -> Dict[str, Any]:
    """Conectare REALĂ cu rolul runtime: atribute, imposibilitatea de a crea obiecte/roluri, funcțiile aplicației accesibile."""
    out: Dict[str, Any] = {}
    with connect(runtime_url) as c:
        out.update(role_report(c, c.execute("SELECT current_user").fetchone()[0].split(".")[0]))
        for label, stmt in (("create_table", "CREATE TABLE _prov_probe (x int)"), ("create_role", "CREATE ROLE _prov_probe_role"), ("drop_migrations", "DROP TABLE schema_migrations")):
            try:
                c.execute(stmt)
                out[label + "_denied"] = False
                c.execute("DROP TABLE IF EXISTS _prov_probe")
            except Exception:  # noqa: BLE001
                out[label + "_denied"] = True
        out["app_function_ok"] = bool(c.execute("SELECT to_regprocedure('cp_admin_status()') IS NOT NULL").fetchone()[0])
    out["least_privilege"] = bool(out["minimal"] and out["create_table_denied"] and out["create_role_denied"] and out["drop_migrations_denied"])
    return out


# ------------------------------------------------------------------ backup / restore (pg_dump criptat)
def _bin(name: str) -> str:
    p = shutil.which(name)
    if not p:
        for d in sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True):
            if (d / name).is_file():
                return str(d / name)
        raise ProvisionError("PG_TOOLS", f"{name} nu este instalat (pachetul postgresql-client)", 500)
    return p


def _pg_env(url: str) -> Dict[str, str]:
    u = split_url(url)
    return {**{k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "HOME")}, "PGPASSWORD": u["password"], "PGSSLMODE": u["sslmode"]}


def _pg_args(url: str) -> List[str]:
    u = split_url(url)
    return ["-h", u["host"], "-p", str(u["port"]), "-U", u["user"], "-d", u["dbname"]]


def make_backup(admin_url: str, key_b64: str, dest: Path) -> Dict[str, Any]:
    """pg_dump format custom -> criptare AES-256-GCM (cheie per instanță, din seif). Parola nu apare în argv."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    dest.parent.mkdir(parents=True, exist_ok=True)
    with connect(admin_url) as c:
        manifest = {"tables": table_counts(c), "server_version": c.execute("SHOW server_version").fetchone()[0]}
    with tempfile.TemporaryDirectory() as td:
        raw = Path(td) / "dump.pgc"
        r = subprocess.run([_bin("pg_dump"), "-Fc", "--no-owner", "-f", str(raw), *_pg_args(admin_url)], env=_pg_env(admin_url), capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise ProvisionError("BACKUP_FAILED", "pg_dump a eșuat: " + redact_text(r.stderr[:300]), 500)
        data = raw.read_bytes()
    nonce = secrets.token_bytes(12)
    blob = b"CIMBK1" + nonce + AESGCM(base64.urlsafe_b64decode(key_b64)).encrypt(nonce, data, dest.name.encode())
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(blob)
    os.chmod(tmp, 0o600)
    os.replace(tmp, dest)
    return {"sha256": hashlib.sha256(blob).hexdigest(), "size": len(blob), "manifest": manifest}


def decrypt_backup(path: Path, key_b64: str, expect_sha256: str = "") -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    blob = Path(path).read_bytes()
    if expect_sha256 and hashlib.sha256(blob).hexdigest() != expect_sha256:
        raise ProvisionError("BACKUP_CORRUPT", "suma de control a backupului nu corespunde", 409)
    if not blob.startswith(b"CIMBK1"):
        raise ProvisionError("BACKUP_FORMAT", "format de backup necunoscut", 409)
    try:
        return AESGCM(base64.urlsafe_b64decode(key_b64)).decrypt(blob[6:18], blob[18:], Path(path).name.encode())
    except Exception:  # noqa: BLE001
        raise ProvisionError("BACKUP_AUTH", "backupul nu poate fi autentificat/decriptat (cheie greșită sau fișier modificat)", 409) from None


def verify_backup(path: Path, key_b64: str, expect_sha256: str) -> Dict[str, Any]:
    data = decrypt_backup(path, key_b64, expect_sha256)
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "d.pgc"
        f.write_bytes(data)
        r = subprocess.run([_bin("pg_restore"), "--list", str(f)], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise ProvisionError("BACKUP_CORRUPT", "arhiva pg_dump nu poate fi citită: " + redact_text(r.stderr[:200]), 409)
    return {"ok": True, "toc_entries": sum(1 for ln in r.stdout.splitlines() if ln and not ln.startswith(";"))}


def restore_backup(admin_url: str, path: Path, key_b64: str, expect_sha256: str, clean: bool) -> Dict[str, Any]:
    data = decrypt_backup(path, key_b64, expect_sha256)
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "d.pgc"
        f.write_bytes(data)
        cmd = [_bin("pg_restore"), "--no-owner", "--exit-on-error", *(["--clean", "--if-exists"] if clean else []), *_pg_args(admin_url), str(f)]
        r = subprocess.run(cmd, env=_pg_env(admin_url), capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        raise ProvisionError("RESTORE_FAILED", "pg_restore a eșuat: " + redact_text(r.stderr[:300]), 500)
    with connect(admin_url) as c:
        return {"tables": table_counts(c)}
