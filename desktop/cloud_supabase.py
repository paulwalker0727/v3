"""cloud_supabase.py — verificarea și pregătirea proiectului Supabase PROPRIU al parohiei (fără UI; testabilă).

Reutilizează logica validată din `tools/setup_manager` (analiză/plan/aplicare migrări, protecții, mascare secrete); nu creează un sistem paralel.

Reguli de securitate:
- Parola PostgreSQL se ține doar într-o `AdminSession` (memorie, expiră, se șterge explicit); NU se scrie în SQLite, SecretStore, loguri sau rapoarte.
- Verificarea este read-only (tranzacție read-only, rollback). Instalarea/actualizarea schemei cere: plan previzualizat, backup MAIN verificat,
  backup/waiver pentru baza Cloud, fraza exactă de confirmare; totul într-o singură tranzacție cu rollback complet la eroare.
- Refurile Supabase protejate (PROD-ul și TEST-ul dezvoltatorului) rămân blocate de `guard`; proiectul parohiei folosește mediul `PARISH`.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urlparse

import cloud_profiles as cp
import cloud_simple as simple
from app_logging import get_logger

logger = get_logger(__name__)

SESSION_TTL = 15 * 60
SET_LAST_CHECK = "cloud_center_supabase_last"
DEFAULT_BUCKET = "cimitir-fisiere"
_PKG = "cimitir_setup_manager"


class CenterError(Exception):
    """Mesaj fix, în română, sigur de afișat (fără secrete)."""


# ------------------------------------------------------------------ încărcarea Setup Manager
def _roots() -> List[Path]:
    roots = [Path(__file__).resolve().parent.parent]
    if getattr(sys, "_MEIPASS", None):
        roots.insert(0, Path(sys._MEIPASS))
    roots.append(Path(sys.executable).resolve().parent)
    return roots


_SM: Optional[Tuple[Any, Any, Any]] = None


def setup_manager() -> Tuple[Any, Any, Any]:
    """(migrations, guard, redact) din tools/setup_manager, sub un nume propriu (desktop/tools are alt conținut)."""
    global _SM
    if _SM:
        return _SM
    for root in _roots():
        pkg = root / "tools" / "setup_manager"
        if (pkg / "migrations.py").is_file():
            break
    else:
        raise CenterError("Modulul Setup Manager lipsește din această instalare (tools/setup_manager): instalarea schemei este indisponibilă.")
    spec = importlib.util.spec_from_file_location(_PKG, pkg / "__init__.py", submodule_search_locations=[str(pkg)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_PKG] = mod
    spec.loader.exec_module(mod)
    _SM = (importlib.import_module(_PKG + ".migrations"), importlib.import_module(_PKG + ".guard"), importlib.import_module(_PKG + ".redact"))
    return _SM


def migrations_dir() -> Path:
    env = os.environ.get("CIMITIR_MIGRATIONS_DIR", "").strip()
    if env and Path(env).is_dir():
        return Path(env)
    for root in _roots():
        for rel in ("backend/cimitir/pg_migrations", "pg_migrations"):
            if (root / rel).is_dir():
                return root / rel
    raise CenterError("Fișierele de migrare (pg_migrations) nu au fost găsite în această instalare.")


# ------------------------------------------------------------------ autorizare temporară
class AdminSession:
    """Parola PostgreSQL a proiectului, doar în memorie, cu expirare. Nu se serializează și nu apare în repr."""

    def __init__(self, ttl: int = SESSION_TTL, clock: Callable[[], float] = time.monotonic):
        self._ttl, self._clock = ttl, clock
        self._pw, self._exp = "", 0.0

    def authorize(self, password: str) -> None:
        if not password or re.search(r"[\r\n\x00]", password):
            raise CenterError("Parola bazei de date este goală sau conține caractere nepermise.")
        self._pw, self._exp = password, self._clock() + self._ttl
        setup_manager()[2].register(password)

    @property
    def active(self) -> bool:
        if self._pw and self._clock() >= self._exp:
            self.clear()
        return bool(self._pw)

    @property
    def password(self) -> str:
        if not self.active:
            raise CenterError("Autorizarea temporară lipsește sau a expirat. Introdu din nou parola bazei de date.")
        return self._pw

    def clear(self) -> None:
        self._pw, self._exp = "", 0.0

    def __repr__(self) -> str:
        return f"<AdminSession active={self.active}>"

    __str__ = __repr__


# ------------------------------------------------------------------ date proiect
@dataclass
class SupabaseInput:
    project_ref: str = ""
    supabase_url: str = ""
    db_host: str = ""
    db_port: int = 5432
    db_user: str = ""
    db_name: str = "postgres"
    storage_bucket: str = DEFAULT_BUCKET

    def normalized(self) -> "SupabaseInput":
        ref = (self.project_ref or "").strip().lower()
        url = (self.supabase_url or "").strip().rstrip("/") or (f"https://{ref}.supabase.co" if ref else "")
        user = (self.db_user or "").strip() or (f"postgres.{ref}" if ref and "pooler" in (self.db_host or "") else "postgres")
        try:
            port = int(self.db_port or 5432)
        except (TypeError, ValueError):
            port = 0
        return SupabaseInput(ref, url, (self.db_host or "").strip().lower(), port, user, (self.db_name or "postgres").strip() or "postgres",
                             (self.storage_bucket or "").strip() or DEFAULT_BUCKET)


def parse_connection_string(uri: str) -> Tuple[Dict[str, Any], str]:
    """Extrage (câmpuri, parolă) dintr-un șir `postgresql://user:parola@host:port/db`. Parola se întoarce separat, ca să nu rămână în câmpurile vizibile."""
    u = urlparse((uri or "").strip())
    if u.scheme not in ("postgres", "postgresql") or not u.hostname:
        raise CenterError("Șirul de conexiune trebuie să înceapă cu postgresql:// și să conțină gazda.")
    user = unquote(u.username or "")
    ref = ""
    m = re.match(r"^[A-Za-z0-9_]+\.([a-z]{20})$", user) or re.match(r"^db\.([a-z]{20})\.supabase\.co$", u.hostname)
    if m:
        ref = m.group(1)
    fields = {"project_ref": ref, "db_host": u.hostname, "db_port": u.port or 5432, "db_user": user, "db_name": (u.path or "/postgres").lstrip("/") or "postgres"}
    return fields, unquote(u.password or "")


def to_cfg(inp: SupabaseInput) -> Dict[str, Any]:
    _, guard, _ = setup_manager()
    i = inp.normalized()
    return {"env_name": guard.PARISH_ENV, "project_ref": i.project_ref, "supabase_url": i.supabase_url,
            "db_connection_string": f"postgresql://{quote(i.db_user, safe='.')}@{i.db_host}:{i.db_port}/{i.db_name}",
            "db_username": i.db_user, "storage_bucket": i.storage_bucket, "docker_port": 8080}


def _conn_kwargs(inp: SupabaseInput, password: str) -> Dict[str, Any]:
    i = inp.normalized()
    return dict(host=i.db_host, port=i.db_port, dbname=i.db_name, user=i.db_user, password=password, sslmode="require", connect_timeout=8)


def _default_connect(**kw):
    import psycopg
    return psycopg.connect(**kw)


def _url(inp: SupabaseInput, password: str) -> str:
    from psycopg.conninfo import make_conninfo
    return make_conninfo(**_conn_kwargs(inp, password))


# ------------------------------------------------------------------ verificare
@dataclass
class VerifyResult:
    checks: List[cp.Check] = field(default_factory=list)
    reachable: bool = False
    empty_project: Optional[bool] = None
    plan: Optional[Dict[str, Any]] = None
    analysis: Optional[Dict[str, Any]] = None
    bucket_state: str = ""            # ok | missing | public | unknown

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.checks.append(cp.Check(name, status, detail))

    @property
    def ok(self) -> bool:
        return self.reachable and not any(c.status == "FAIL" for c in self.checks)

    @property
    def pending(self) -> List[str]:
        return [s["name"] for s in (self.plan or {}).get("steps", [])]

    @property
    def schema_current(self) -> bool:
        return self.plan is not None and not self.plan["steps"] and not self.plan["blocked"]

    def text(self) -> str:
        return "\n".join(f"[{'OK' if c.status == 'OK' else 'EROARE' if c.status == 'FAIL' else 'ATENȚIE' if c.status == 'WARN' else '—'}] {c.name}"
                         + (f" — {c.detail}" if c.detail else "") for c in self.checks)

    def summary(self) -> Dict[str, Any]:
        return {"ok": self.ok, "pending": self.pending, "bucket": self.bucket_state, "checked_at": int(time.time())}


def _hint(exc: BaseException) -> str:
    return cp._pg_hint(exc)  # noqa: SLF001  (aceleași mesaje fixe ca în profilurile Cloud, fără valori secrete)


def verify_project(inp: SupabaseInput, password: str, connect: Optional[Callable[..., Any]] = None, mig_dir: Optional[Path] = None) -> VerifyResult:
    """Verificare READ-ONLY: coerență ref/URL/utilizator, conexiune+TLS, este proiect Supabase, schema (migrări lipsă), bucket Storage."""
    mig, guard, redact = setup_manager()
    res = VerifyResult()
    redact.register(password)
    i = inp.normalized()
    cfg = to_cfg(i)
    try:
        guard.assert_read_allowed(cfg)
    except guard.GuardError as exc:
        res.add("Protecții", "FAIL", str(exc))
        return res
    issues = guard.consistency_issues(cfg)
    if issues:
        for msg in issues:
            res.add("Coerența datelor proiectului", "FAIL", msg)
        return res
    res.add("Coerența datelor proiectului", "OK", "referință, URL și utilizator se potrivesc")
    if not password:
        res.add("Autentificare PostgreSQL", "FAIL", "parola bazei de date lipsește (autorizare temporară)")
        return res
    conn = None
    try:
        conn = (connect or _default_connect)(**_conn_kwargs(i, password))
        conn.read_only = True
        conn.execute("SELECT 1").fetchone()
        res.reachable = True
        res.add("Conexiune și autentificare PostgreSQL", "OK", "conectat (tranzacție doar-citire)")
        _verify_open(conn, i, res, mig, mig_dir)
    except CenterError:
        raise
    except Exception as exc:  # noqa: BLE001
        msg = redact.redact(f"{type(exc).__name__}: {_hint(exc)}")
        res.add("Conexiune și autentificare PostgreSQL" if not res.reachable else "Verificare", "FAIL", msg)
    finally:
        if conn is not None:
            try:
                conn.rollback()
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    return res


def _verify_open(conn, i: SupabaseInput, res: VerifyResult, mig, mig_dir: Optional[Path]) -> None:
    try:
        row = conn.execute("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()").fetchone()
        res.add("Conexiune criptată (TLS)", "OK" if row and row[0] else "FAIL", "" if row and row[0] else "conexiunea nu este criptată")
    except Exception:  # noqa: BLE001
        conn.rollback()
        res.add("Conexiune criptată (TLS)", "WARN", "nu se poate verifica")
    has = conn.execute("SELECT to_regnamespace('storage') IS NOT NULL, to_regnamespace('auth') IS NOT NULL").fetchone()
    if not (has[0] and has[1]):
        res.add("Proiect Supabase", "FAIL", "baza nu pare un proiect Supabase (lipsesc schemele auth/storage): proiect respins")
        return
    res.add("Proiect Supabase", "OK", "schemele auth și storage există")
    migs = mig.load_migrations(mig_dir or migrations_dir())
    res.analysis = mig.analyze(conn, migs)
    res.plan = mig.build_plan(res.analysis)
    conn.rollback()
    conn.read_only = True
    res.empty_project = _is_empty(conn)
    if res.plan["blocked"]:
        for b in res.plan["blocked"]:
            res.add("Schema Cimitir", "FAIL", b)
    elif res.plan["steps"]:
        res.add("Schema Cimitir", "WARN", f"{len(res.plan['steps'])} migrări lipsă: " + ", ".join(res.pending))
    else:
        res.add("Schema Cimitir", "OK", f"toate cele {len(migs)} migrări sunt aplicate și verificate")
    try:
        b = conn.execute("SELECT public FROM storage.buckets WHERE id = %s", (i.storage_bucket,)).fetchone()
    except Exception:  # noqa: BLE001
        conn.rollback()
        b = "?"
    if b == "?":
        res.bucket_state = "unknown"
        res.add("Storage", "WARN", "nu pot citi lista bucketurilor")
    elif b is None:
        res.bucket_state = "missing"
        res.add(f"Storage — bucket „{i.storage_bucket}”", "WARN", "lipsește; va fi creat PRIVAT la instalare")
    elif b[0]:
        res.bucket_state = "public"
        res.add(f"Storage — bucket „{i.storage_bucket}”", "FAIL", "bucketul este PUBLIC; documentele parohiei trebuie să fie private")
    else:
        res.bucket_state = "ok"
        res.add(f"Storage — bucket „{i.storage_bucket}”", "OK", "există și este privat")
    conn.rollback()


def _is_empty(conn) -> bool:
    """Proiect fără date de salvat: niciun rând în tabelele din schema public."""
    for (t,) in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall():
        if conn.execute(f'SELECT EXISTS (SELECT 1 FROM public."{t}")').fetchone()[0]:
            return False
    return True


# ------------------------------------------------------------------ backup și instalare/actualizare schemă
def local_backup() -> Path:
    """Backup MAIN (SQLite + media) verificat prin manifest, înainte de orice operațiune periculoasă."""
    import cimitir_backup
    import cimitir_config
    import database as db
    from backup_window import backup_folder
    dest = backup_folder()
    dest.mkdir(parents=True, exist_ok=True)
    path = cimitir_backup.backup(cimitir_config.working_dir(), dest)
    problems = cimitir_backup.verify(path)
    if problems:
        raise CenterError("Backupul local nu a trecut verificarea: " + "; ".join(problems[:3]))
    db.set_setting("cloud_center_last_backup", str(path))
    return path


def _local_backup_valid(path: str) -> bool:
    import cimitir_backup
    try:
        return bool(path) and not cimitir_backup.verify(Path(path))
    except (OSError, ValueError, KeyError):
        return False


def preview(res: VerifyResult, inp: SupabaseInput) -> Dict[str, Any]:
    """Previzualizare cu ce se va executa; nu modifică nimic."""
    if not res.ok or res.plan is None:
        raise CenterError("Verifică mai întâi proiectul (toate verificările critice trebuie să treacă).")
    steps = [f"{s['name']} — {'aplicare' if s['action'] == 'apply' else 'doar înregistrare în istoric'}: {s['why']}" for s in res.plan["steps"]]
    bucket = res.bucket_state == "missing"
    if bucket:
        steps.append(f"Storage: creare bucket PRIVAT „{inp.normalized().storage_bucket}”")
    return {"steps": steps, "blocked": res.plan["blocked"], "confirm_phrase": res.plan["confirm_phrase"] or ("APLIC BUCKET" if bucket else ""),
            "create_bucket": bucket, "needs_db_backup": not bool(res.empty_project), "executable": res.plan["executable"] or (bucket and not res.plan["blocked"])}


def db_backup_info(res: VerifyResult, backup_path: str = "") -> Dict[str, Any]:
    """Proiect gol -> nimic de salvat (documentat). Altfel e obligatoriu un backup al bazei Cloud (fișier pg_dump) verificat."""
    mig = setup_manager()[0]
    if res.empty_project:
        return {"ok": True, "kind": "empty-project", "detail": "proiect gol: nu există date de salvat"}
    return mig.verify_backup(backup_path)


def apply_schema(inp: SupabaseInput, password: str, res: VerifyResult, confirm: str, local_backup_path: str, db_backup: Dict[str, Any],
                 connect: Optional[Callable[[str], Any]] = None, mig_dir: Optional[Path] = None, bucket_connect: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    """Instalează/actualizează schema (o tranzacție, rollback complet la eroare) și apoi creează bucketul privat. Refuză fără backup și confirmare exactă."""
    mig, guard, redact = setup_manager()
    redact.register(password)
    try:
        guard.assert_write_allowed(to_cfg(inp))
    except guard.GuardError as exc:
        raise CenterError(redact.redact(str(exc))) from None
    pv = preview(res, inp)
    if not pv["executable"]:
        raise CenterError("Planul nu poate fi executat: " + (" | ".join(pv["blocked"]) or "nu există nimic de aplicat."))
    if not _local_backup_valid(local_backup_path):
        raise CenterError("Backupul local MAIN lipsește sau nu este valid: instalarea este refuzată.")
    if not db_backup.get("ok"):
        raise CenterError("Backupul bazei Cloud nu este verificat: instalarea este refuzată.")
    if confirm != pv["confirm_phrase"]:
        raise CenterError(f"Confirmare incorectă. Tastează exact: {pv['confirm_phrase']}")
    cfg = to_cfg(inp)
    out: Dict[str, Any] = {"schema": None, "bucket": None}
    try:
        if res.plan["steps"]:
            migs = mig.load_migrations(mig_dir or migrations_dir())
            out["schema"] = mig.apply_plan(_url(inp, password), cfg, migs, res.plan, res.plan["confirm_phrase"], db_backup, "execute", connect=connect)
            if not out["schema"].get("executed"):
                return out
        if pv["create_bucket"]:
            out["bucket"] = _create_bucket(inp, password, bucket_connect or _default_connect)
    except guard.GuardError as exc:
        raise CenterError(redact.redact(str(exc))) from None
    logger.info("Schema Supabase actualizată pentru proiectul %s (fără secrete în jurnal).", inp.normalized().project_ref)
    return out


def _create_bucket(inp: SupabaseInput, password: str, connect) -> str:
    i = inp.normalized()
    conn = connect(**_conn_kwargs(i, password))
    try:
        conn.execute("INSERT INTO storage.buckets (id, name, public) VALUES (%s, %s, false) ON CONFLICT (id) DO NOTHING", (i.storage_bucket, i.storage_bucket))
        row = conn.execute("SELECT public FROM storage.buckets WHERE id=%s", (i.storage_bucket,)).fetchone()
        if row is None or row[0]:
            raise CenterError("Bucketul nu a putut fi creat privat.")
        conn.commit()
        return f"bucket privat „{i.storage_bucket}” creat/confirmat"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ------------------------------------------------------------------ profil Cloud (fără parole)
def reconfigure_warning(inp: SupabaseInput) -> str:
    """Text de avertizare dacă profilul curent indică deja alt proiect Supabase (reconfigurare)."""
    prof = simple.current_profile()
    ref = inp.normalized().project_ref
    if prof and prof.project_ref and prof.project_ref != ref:
        return ("Profilul Cloud folosește deja alt proiect Supabase. Schimbarea NU mută datele și NU modifică proiectul vechi; "
                "backendul Cloud trebuie reasociat cu noul proiect, iar telefoanele pot necesita reasociere.")
    return ""


def ensure_profile_for_env(env: str, confirm_production: bool = False, store: Any = None) -> cp.Profile:
    """Profilul Cloud al mediului cerut (TEST/PRODUCTION), activat. Niciodată nu schimbă mediul unui profil existent: la nevoie creează unul separat."""
    cur = simple.current_profile()
    target = cur if cur and cur.environment == env else next((p for p in cp.list_profiles() if p.environment == env), None)
    if env == "PRODUCTION" and not confirm_production:
        raise cp.ConfirmationRequired("production_activation")
    if target is None:
        taken = {p.name.lower() for p in cp.list_profiles()}
        base = "Cloud PROD" if env == "PRODUCTION" else "Cloud TEST"
        name, n = base, 2
        while name.lower() in taken:
            name, n = f"{base} {n}", n + 1
        target = cp.create_profile({"name": name, "environment": env}, None, store)
    return target if target.is_active else cp.activate_profile(target.id, confirm_production=confirm_production)


def save_to_profile(inp: SupabaseInput, environment_label: str = "TEST", name: str = "", confirm_production: bool = False, store: Any = None) -> cp.Profile:
    """Salvează DOAR datele nesecrete ale proiectului în profilul Cloud al mediului ales (parola PostgreSQL nu se salvează). PROD cere confirmare explicită;
    profilurile TEST și PROD rămân separate (mediul unui profil existent nu se schimbă)."""
    i = inp.normalized()
    prof = ensure_profile_for_env(simple.env_value(environment_label), confirm_production, store)
    fields = {k: getattr(prof, k) for k in cp.PLAIN_FIELDS}
    fields.update(project_ref=i.project_ref, supabase_url=i.supabase_url, db_host=i.db_host, db_port=i.db_port,
                  db_name=i.db_name, db_user=i.db_user, sslmode="require", storage_bucket=i.storage_bucket)
    if name:
        fields["name"] = name
    return cp.update_profile(prof.id, fields, None, store)


def input_from_profile() -> SupabaseInput:
    prof = simple.current_profile()
    if prof is None:
        return SupabaseInput()
    return SupabaseInput(prof.project_ref, prof.supabase_url, prof.db_host, prof.db_port or 5432, prof.db_user, prof.db_name or "postgres",
                         prof.storage_bucket or DEFAULT_BUCKET)
