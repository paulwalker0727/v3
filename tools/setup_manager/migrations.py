"""Detectare, planificare și aplicare controlată a migrărilor (backend/cimitir/pg_migrations/NNN_*.sql).

Starea fiecărei migrări combină `schema_migrations` cu obiectele REALE din PostgreSQL (tabele, coloane, funcții, politici RLS, indexuri, trigger-e),
extrase static din fișierul SQL. Nimic nu se presupune: o migrare înregistrată dar cu obiecte lipsă e INCONSISTENTĂ și blochează planul.
Scrierea: o singură tranzacție, verificări după fiecare migrare, fără scădere a numărului de rânduri, istoric fără duplicate.
"""
from __future__ import annotations

import hashlib
import json
import re
import tarfile
import time
import zipfile
import gzip
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import guard
from .redact import redact

MIGRATION_LOCK_KEY = 7346102                      # aceeași cheie ca backend/cimitir/pg_compat.py (serializare cu aplicația)
DEPENDENCIES = {22: [21], 23: [22], 24: [22, 23], 25: [23], 26: [25]}   # dependențe explicite documentate; restul: ordine numerică strictă
ST_APPLIED, ST_UNRECORDED, ST_INCONSISTENT, ST_PARTIAL, ST_PENDING, ST_UNVERIFIABLE = (
    "APLICATĂ", "APLICATĂ_NEÎNREGISTRATĂ", "INCONSISTENTĂ", "PARȚIALĂ", "LIPSĂ", "NEVERIFICABILĂ")

_IDENT = r'"?([a-z_][a-z0-9_]*)"?'
_RX = {
    "table": re.compile(r"CREATE\s+(?:UNLOGGED\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?" + _IDENT + r"\s*\(", re.I),
    "function": re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?:public\.)?" + _IDENT + r"\s*\(", re.I),
    "policy": re.compile(r"CREATE\s+POLICY\s+" + _IDENT + r"\s+ON\s+(?:public\.)?" + _IDENT, re.I),
    "rls": re.compile(r"ALTER\s+TABLE\s+(?:ONLY\s+)?(?:public\.)?" + _IDENT + r"\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY", re.I),
    "index": re.compile(r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?" + _IDENT + r"\s+ON", re.I),
    "sequence": re.compile(r"CREATE\s+SEQUENCE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?" + _IDENT, re.I),
    "trigger": re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?TRIGGER\s+" + _IDENT + r"\s+[^;]*?\bON\s+(?:public\.)?" + _IDENT, re.I | re.S),
    "column": re.compile(r"ALTER\s+TABLE\s+(?:ONLY\s+)?(?:IF\s+EXISTS\s+)?(?:public\.)?" + _IDENT + r"\s+ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?" + _IDENT, re.I),
}
_DROP = {
    "table": re.compile(r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:public\.)?" + _IDENT, re.I),
    "function": re.compile(r"DROP\s+FUNCTION\s+(?:IF\s+EXISTS\s+)?(?:public\.)?" + _IDENT, re.I),
    "policy": re.compile(r"DROP\s+POLICY\s+(?:IF\s+EXISTS\s+)?" + _IDENT + r"\s+ON\s+(?:public\.)?" + _IDENT, re.I),
    "index": re.compile(r"DROP\s+INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+EXISTS\s+)?(?:public\.)?" + _IDENT, re.I),
    "trigger": re.compile(r"DROP\s+TRIGGER\s+(?:IF\s+EXISTS\s+)?" + _IDENT + r"\s+ON\s+(?:public\.)?" + _IDENT, re.I),
    "column": re.compile(r"ALTER\s+TABLE\s+(?:ONLY\s+)?(?:IF\s+EXISTS\s+)?(?:public\.)?" + _IDENT + r"\s+DROP\s+COLUMN\s+(?:IF\s+EXISTS\s+)?" + _IDENT, re.I),
    "sequence": re.compile(r"DROP\s+SEQUENCE\s+(?:IF\s+EXISTS\s+)?(?:public\.)?" + _IDENT, re.I),
}
_DYN_DROP_POLICY = re.compile(r"DROP\s+POLICY\s+IF\s+EXISTS\s+" + _IDENT + r"\s+ON\s+%I", re.I)
_DOLLAR = re.compile(r"(\$[A-Za-z_0-9]*\$).*?\1", re.S)
_DO_BLOCK = re.compile(r"\bDO\s+(\$[A-Za-z_0-9]*\$).*?\1", re.S | re.I)


def strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sql)


def extract_objects(sql: str) -> Dict[str, List[tuple]]:
    """Obiecte create de nivel superior (în afara blocurilor DO și a corpurilor $$...$$); returnează {tip: [(nume, [tabel])]}."""
    s = strip_comments(sql)
    s = _DO_BLOCK.sub(" ", s)
    out: Dict[str, List[tuple]] = {k: [] for k in _RX}
    out["function"] = [(m.group(1).lower(),) for m in _RX["function"].finditer(s)]
    s = _DOLLAR.sub(" '' ", s)
    for kind, rx in _RX.items():
        if kind == "function":
            continue
        for m in rx.finditer(s):
            out[kind].append(tuple(g.lower() for g in m.groups()))
    for kind in out:
        out[kind] = sorted(set(out[kind]))
    return out


def extract_drops(sql: str) -> Dict[str, set]:
    s = _DOLLAR.sub(" '' ", _DO_BLOCK.sub(" ", strip_comments(sql)))
    return {k: {tuple(g.lower() for g in m.groups()) for m in rx.finditer(s)} for k, rx in _DROP.items()}


@dataclass
class Migration:
    version: int
    name: str
    path: Path
    sha256: str
    objects: Dict[str, List[tuple]] = field(default_factory=dict)

    def checkable(self) -> int:
        return sum(len(v) for v in self.objects.values())


def load_migrations(directory: Path) -> List[Migration]:
    migs: List[Migration] = []
    for p in sorted(Path(directory).glob("[0-9][0-9][0-9]_*.sql")):
        text = p.read_text(encoding="utf-8")
        migs.append(Migration(int(p.name[:3]), p.name, p, hashlib.sha256(text.encode("utf-8")).hexdigest(), extract_objects(text)))
    drops: Dict[int, Dict[str, set]] = {m.version: extract_drops(m.path.read_text(encoding="utf-8")) for m in migs}
    dyn: Dict[int, set] = {m.version: {g.lower() for g in _DYN_DROP_POLICY.findall(strip_comments(m.path.read_text(encoding="utf-8")))} for m in migs}
    for m in migs:                                           # obiectele eliminate/înlocuite de migrări ulterioare nu mai sunt verificate la cele vechi
        later = [drops[x.version] for x in migs if x.version > m.version]
        for kind, items in m.objects.items():
            later_dyn = set().union(*[dyn[x.version] for x in migs if x.version > m.version]) if kind == "policy" else set()
            m.objects[kind] = [it for it in items if not any(it in d.get(kind, set()) for d in later) and it[0] not in later_dyn]
    return migs


def _label(kind: str, item: tuple) -> str:
    return f"{kind}:{item[1]}.{item[0]}" if kind in ("policy", "trigger", "column") else f"{kind}:{item[0]}"


def probe_object(c, kind: str, item: tuple) -> bool:
    q = None
    if kind == "table":
        q, a = "SELECT to_regclass(%s) IS NOT NULL", (f"public.{item[0]}",)
    elif kind == "sequence":
        q, a = "SELECT to_regclass(%s) IS NOT NULL", (f"public.{item[0]}",)
    elif kind == "index":
        q, a = "SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname='public' AND indexname=%s)", (item[0],)
    elif kind == "function":
        q, a = "SELECT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname=%s)", (item[0],)
    elif kind == "policy":
        q, a = "SELECT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public' AND policyname=%s AND tablename=%s)", (item[0], item[1])
    elif kind == "rls":
        q, a = "SELECT COALESCE((SELECT relrowsecurity FROM pg_class WHERE oid=to_regclass(%s)), false)", (f"public.{item[0]}",)
    elif kind == "trigger":
        q, a = ("SELECT EXISTS (SELECT 1 FROM pg_trigger t JOIN pg_class r ON r.oid=t.tgrelid JOIN pg_namespace n ON n.oid=r.relnamespace "
                "WHERE n.nspname='public' AND t.tgname=%s AND r.relname=%s AND NOT t.tgisinternal)"), (item[0], item[1])
    elif kind == "column":
        q, a = ("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name=%s AND column_name=%s)"), (item[0], item[1])
    return bool(c.execute(q, a).fetchone()[0])


def inspect_migration(c, m: Migration) -> Dict[str, Any]:
    present, missing = [], []
    for kind, items in m.objects.items():
        for it in items:
            (present if probe_object(c, kind, it) else missing).append(_label(kind, it))
    return {"present": present, "missing": missing}


def recorded_versions(c) -> Optional[Dict[int, str]]:
    if c.execute("SELECT to_regclass('public.schema_migrations') IS NULL").fetchone()[0]:
        return None
    return {int(r[0]): r[1] for r in c.execute("SELECT version, name FROM schema_migrations").fetchall()}


def analyze(c, migs: List[Migration]) -> Dict[str, Any]:
    rec = recorded_versions(c)
    dup = []
    if rec is not None:
        dup = [int(r[0]) for r in c.execute("SELECT version FROM schema_migrations GROUP BY version HAVING count(*)>1").fetchall()]
    rows = []
    for m in migs:
        ins = inspect_migration(c, m)
        recorded = rec is not None and m.version in rec
        total = len(ins["present"]) + len(ins["missing"])
        if total == 0:
            st = ST_APPLIED if recorded else ST_UNVERIFIABLE
        elif not ins["missing"]:
            st = ST_APPLIED if recorded else ST_UNRECORDED
        elif recorded:
            st = ST_INCONSISTENT
        elif ins["present"]:
            st = ST_PARTIAL
        else:
            st = ST_PENDING
        rows.append({"version": m.version, "name": m.name, "sha256": m.sha256, "recorded": recorded, "state": st,
                     "checked": total, "present": len(ins["present"]), "missing": ins["missing"][:40], "missing_count": len(ins["missing"])})
    return {"history_table": rec is not None, "duplicates": dup, "migrations": rows}


def build_plan(analysis: Dict[str, Any], target_min: int = 0) -> Dict[str, Any]:
    """Plan ordonat (crescător). Pași: `apply` (migrare lipsă) și `record` (obiecte verificate, doar istoricul lipsește). Blocări explicite."""
    rows = {r["version"]: r for r in analysis["migrations"]}
    steps: List[Dict[str, Any]] = []
    blocks: List[str] = []
    ok_states = {ST_APPLIED, ST_UNRECORDED}
    planned: Dict[int, str] = {}
    inferred: List[int] = []
    if analysis["duplicates"]:
        blocks.append(f"Istoric cu înregistrări duplicate pentru versiunile {analysis['duplicates']}: se repară manual înainte de orice migrare.")
    for v in sorted(rows):
        r = rows[v]
        st = r["state"]
        if st == ST_UNRECORDED:
            planned[v] = "record"
            steps.append({"version": v, "name": r["name"], "action": "record", "why": "obiectele există și sunt verificate; lipsește doar înregistrarea în schema_migrations"})
        elif st == ST_PENDING:
            planned[v] = "apply"
            steps.append({"version": v, "name": r["name"], "action": "apply", "why": f"{r['missing_count']} obiecte lipsesc din PostgreSQL"})
        elif st == ST_INCONSISTENT:
            blocks.append(f"{r['name']}: înregistrată în schema_migrations dar lipsesc {r['missing_count']} obiecte ({', '.join(r['missing'][:5])}). Nu se presupune nimic; verifică manual.")
        elif st == ST_PARTIAL:
            blocks.append(f"{r['name']}: aplicată parțial (lipsesc {r['missing_count']} obiecte). Repară manual (rollback din pg_rollbacks/) înainte de plan.")
        elif st == ST_UNVERIFIABLE:
            lower_pending = any(rows[x]["state"] == ST_PENDING for x in rows if x < v)
            higher_ok = any(rows[x]["state"] in ok_states for x in rows if x > v)
            if lower_pending:
                planned[v] = "apply"
                steps.append({"version": v, "name": r["name"], "action": "apply", "why": "neverificabilă static; o migrare anterioară lipsește, deci aceasta nu poate fi aplicată încă"})
            elif higher_ok:
                inferred.append(v)
            else:
                blocks.append(f"{r['name']}: neînregistrată și fără obiecte detectabile static; nu poate fi verificată automat.")
    for s in steps:
        for d in DEPENDENCIES.get(s["version"], []):
            if d in rows and rows[d]["state"] not in ok_states and d not in planned and d not in inferred:
                blocks.append(f"{s['name']} depinde de migrarea {d:03d}, care nu este aplicată/verificată.")
            elif d in planned and d > s["version"]:
                blocks.append("Ordine invalidă a dependențelor.")
        lower_bad = [v for v in rows if v < s["version"] and (rows[v]["state"] in (ST_INCONSISTENT, ST_PARTIAL)
                                                              or (rows[v]["state"] == ST_UNVERIFIABLE and v not in planned and v not in inferred))]
        if lower_bad:
            blocks.append(f"{s['name']} blocată: migrări anterioare neclare ({', '.join(f'{v:03d}' for v in lower_bad)}).")
    blocks = list(dict.fromkeys(blocks))
    versions = [s["version"] for s in steps]
    fingerprint = json.dumps([[s["version"], s["action"], rows[s["version"]]["sha256"]] for s in steps], sort_keys=True)
    phrase = ("APLIC " + "-".join(f"{v:03d}" for v in versions)) if versions else ""
    return {"steps": steps, "blocked": blocks, "plan_hash": hashlib.sha256(fingerprint.encode()).hexdigest()[:16], "confirm_phrase": phrase,
            "executable": bool(steps) and not blocks, "inferred_applied": inferred,
            "summary": {f"{v:03d}": rows[v]["state"] for v in sorted(rows) if v >= 22}}


def verify_backup(path: str, max_age_hours: float = 24 * 7) -> Dict[str, Any]:
    """Backup lizibil? Acceptă .zip, .tar(.gz), .gz, .sql, .dump/.backup (pg_dump custom), director (toc.dat). Nu citește conținutul în afara antetelor."""
    p = Path(path or "")
    res: Dict[str, Any] = {"ok": False, "kind": "", "size": 0, "age_hours": None, "detail": "", "stale": False}
    if not path or not p.exists():
        res["detail"] = "Calea backupului nu există."
        return res
    try:
        if p.is_dir():
            if not (p / "toc.dat").exists():
                res["detail"] = "Director fără toc.dat (nu pare backup pg_dump format director)."
                return res
            res.update(kind="pg_dump-dir", size=sum(f.stat().st_size for f in p.iterdir() if f.is_file()))
            mtime = (p / "toc.dat").stat().st_mtime
        else:
            st = p.stat()
            res["size"], mtime = st.st_size, st.st_mtime
            if st.st_size < 200:
                res["detail"] = "Fișier prea mic pentru un backup real."
                return res
            head = p.open("rb").read(16)
            if zipfile.is_zipfile(p):
                with zipfile.ZipFile(p) as z:
                    bad = z.testzip()
                    names = z.namelist()
                if bad:
                    res["detail"] = f"Arhivă ZIP coruptă (intrare {bad})."
                    return res
                res.update(kind="zip", detail=f"ZIP valid, {len(names)} intrări")
            elif head.startswith(b"PGDMP"):
                res.update(kind="pg_dump-custom", detail="Format custom pg_dump (antet PGDMP)")
            elif head[:2] == b"\x1f\x8b":
                with gzip.open(p, "rb") as g:
                    chunk = g.read(1 << 20)
                if not chunk:
                    res["detail"] = "GZIP gol."
                    return res
                if tarfile.is_tarfile(p):
                    with tarfile.open(p) as t:
                        n = len(t.getnames())
                    res.update(kind="tar.gz", detail=f"TAR.GZ valid, {n} intrări")
                else:
                    res.update(kind="gz", detail="GZIP lizibil")
            elif tarfile.is_tarfile(p):
                with tarfile.open(p) as t:
                    n = len(t.getnames())
                res.update(kind="tar", detail=f"TAR valid, {n} intrări")
            else:
                txt = p.open("rb").read(1 << 16).decode("utf-8", "ignore")
                if "PostgreSQL database dump" in txt or re.search(r"CREATE\s+TABLE|COPY\s+\w+", txt, re.I):
                    res.update(kind="sql", detail="Dump SQL text")
                else:
                    res["detail"] = "Format necunoscut (nu e ZIP/TAR/GZ/SQL/pg_dump)."
                    return res
    except (OSError, zipfile.BadZipFile, tarfile.TarError, EOFError) as exc:
        res["detail"] = f"Arhiva nu poate fi citită: {type(exc).__name__}"
        return res
    res["age_hours"] = round((time.time() - mtime) / 3600, 1)
    res["stale"] = res["age_hours"] > max_age_hours
    res["ok"] = True
    res["detail"] = (res["detail"] or "backup lizibil") + (f" — ATENȚIE: vechime {res['age_hours']} h" if res["stale"] else "")
    return res


def _counts(c) -> Dict[str, int]:
    out = {}
    for (t,) in c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1").fetchall():
        out[t] = c.execute(f'SELECT count(*) FROM public."{t}"').fetchone()[0]
    return out


def apply_plan(url: str, cfg: Dict[str, Any], migs: List[Migration], plan: Dict[str, Any], confirm: str, backup: Dict[str, Any],
               mode: str, connect=None) -> Dict[str, Any]:
    """Aplică planul într-o SINGURĂ tranzacție. Refuză fără: mod scriere, ghid TEST, backup verificat, plan nemodificat, fraza exactă de confirmare."""
    guard.assert_write_allowed(cfg)
    if mode != "execute":
        return {"executed": False, "dry_run": True, "message": "Mod Dry Run: nimic nu a fost executat.", "steps": plan["steps"]}
    if not plan["executable"]:
        raise guard.GuardError("Planul nu este executabil: " + " | ".join(plan["blocked"] or ["nu există pași"]))
    if not backup.get("ok"):
        raise guard.GuardError("Backupul nu este verificat/lizibil: migrarea este refuzată.")
    if confirm != plan["confirm_phrase"]:
        raise guard.GuardError(f"Confirmare incorectă. Tastează exact: {plan['confirm_phrase']}")
    import psycopg
    by_ver = {m.version: m for m in migs}
    log: List[str] = []
    conn = (connect or (lambda u: psycopg.connect(u, connect_timeout=20, autocommit=False)))(url)
    try:
        conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
        conn.commit()
        try:
            fresh = build_plan(analyze(conn, migs))
            conn.rollback()
            if fresh["plan_hash"] != plan["plan_hash"]:
                raise guard.GuardError("Starea bazei s-a schimbat de la generarea planului; regenerează planul.")
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
            before = _counts(conn)
            for s in plan["steps"]:
                m = by_ver[s["version"]]
                sp = f"sm_{m.version}"
                conn.execute(f"SAVEPOINT {sp}")
                if s["action"] == "apply":
                    conn.execute(m.path.read_text(encoding="utf-8"))
                ins = inspect_migration(conn, m)
                if ins["missing"]:
                    raise RuntimeError(f"{m.name}: verificarea obiectelor a eșuat, lipsesc: {', '.join(ins['missing'][:8])}")
                conn.execute("INSERT INTO schema_migrations (version, name) SELECT %s, %s WHERE NOT EXISTS (SELECT 1 FROM schema_migrations WHERE version=%s)",
                             (m.version, m.name, m.version))
                n = conn.execute("SELECT count(*) FROM schema_migrations WHERE version=%s", (m.version,)).fetchone()[0]
                if n != 1:
                    raise RuntimeError(f"{m.name}: istoric duplicat/lipsă după înregistrare ({n} rânduri)")
                log.append(f"{m.name}: {'aplicată' if s['action'] == 'apply' else 'înregistrată în istoric'} și verificată ({len(ins['present'])} obiecte)")
            after = _counts(conn)
            shrunk = {t: (n, after.get(t)) for t, n in before.items() if t in after and after[t] < n}
            dropped = [t for t in before if t not in after]
            if shrunk or dropped:
                raise RuntimeError(f"Integritate: rânduri scăzute/tabele dispărute {shrunk or dropped}")
            conn.commit()
            return {"executed": True, "dry_run": False, "log": log, "message": "Migrările au fost aplicate într-o tranzacție și verificate."}
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            if isinstance(exc, guard.GuardError):
                raise
            return {"executed": False, "dry_run": False, "log": log, "rolled_back": True,
                    "message": "EȘEC — ROLLBACK complet, nimic modificat: " + redact(f"{type(exc).__name__}: {str(exc)[:300]}")}
        finally:
            try:
                conn.rollback()
                conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))
                conn.commit()
            except Exception:  # noqa: BLE001
                pass
    finally:
        conn.close()
