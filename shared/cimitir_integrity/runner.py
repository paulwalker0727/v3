"""Rulează verificările de integritate (strict citire) și produce un Report."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

from . import checks_places as cp
from . import checks_records as cr
from .context import Ctx, _authorizer, open_readonly
from .model import CATEGORIES, Report

TOOL_VERSION = "1.0"
TABLES = ("locuri_de_veci", "sectoare", "concesionari", "contracte", "inhumati", "amenajari_avize", "documents", "photos", "plati")

STEPS = [
    ("locuri", "Locuri de veci", cp.check_locuri),
    ("sectoare", "Sectoare", cp.check_sectoare),
    ("concesionari", "Concesionari", cp.check_concesionari),
    ("contracte", "Contracte", cp.check_contracte),
    ("inhumati", "Înhumați", cr.check_inhumati),
    ("avize", "Avize", cr.check_avize),
    ("documente", "Documente și fotografii", cr.check_documente),
    ("plati", "Plăți", cr.check_plati),
    ("sincronizare", "Sincronizare (uid)", cr.check_sincronizare),
    ("financiar", "Evidențe financiare", cr.check_financiar),
]


def fingerprint(path: str) -> Dict[str, str]:
    """SHA-256 al fișierului bazei și al -wal (dacă există) + lista fișierelor auxiliare: dovada că verificarea nu a modificat nimic."""
    out = {}
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(path) + suffix)
        if p.is_file():
            if suffix == "-shm":
                out[p.name] = "prezent"
                continue
            h = hashlib.sha256()
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            out[p.name] = h.hexdigest()
    return out


def run_checks(conn: sqlite3.Connection, *, today: Optional[date] = None, max_per_code: int = 5000, deep: bool = False,
               media_root: Optional[str] = None, include_info: bool = True, categories: Optional[Iterable[str]] = None,
               progress: Optional[Callable[[int, int, str], None]] = None, cancel: Optional[Callable[[], bool]] = None,
               db_label: str = "") -> Report:
    """`conn` poate fi orice conexiune sqlite3; pe durata rulării i se instalează un autorizator care REFUZĂ orice instrucțiune de scriere."""
    ctx = Ctx(conn, today=today, max_per_code=max_per_code, deep=deep, media_root=media_root, include_info=include_info)
    wanted = set(categories) if categories else set(CATEGORIES)
    steps = [s for s in STEPS if s[0] in wanted]
    started = datetime.now(timezone.utc)
    t0, errors, counts = time.monotonic(), [], {}
    conn.set_authorizer(cp_auth)
    try:
        for i, (_cat, label, fn) in enumerate(steps):
            if cancel and cancel():
                errors.append("Anulat de utilizator")
                break
            if progress:
                progress(i, len(steps), label)
            try:
                fn(ctx)
            except Exception as exc:  # noqa: BLE001  (o verificare defectă nu oprește restul; se raportează)
                errors.append(f"{label}: {type(exc).__name__}: {str(exc)[:200]}")
        if progress:
            progress(len(steps), len(steps), "Gata")
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES if ctx.columns(t)}
    finally:
        conn.set_authorizer(None)
    for code, n in list(ctx.truncated.items()):
        ctx.add("META002", "raport", code, f"Verificarea {code} a găsit {n} probleme; lista detaliată conține doar primele {max_per_code}.", {"cod": code, "gasite": n, "listate": max_per_code})
    for s in sorted(set(ctx.skipped)):
        ctx.add("META001", "schema", s, f"Verificare omisă: lipsește {s}.", {"lipsa": s})
    meta: Dict[str, Any] = {
        "tool": "verificare_evidente", "tool_version": TOOL_VERSION, "mode": "read-only (SELECT only; autorizator SQLite activ)",
        "database": db_label, "started_utc": started.isoformat(timespec="seconds"), "duration_s": round(time.monotonic() - t0, 2),
        "reference_date": ctx.today.isoformat(), "row_counts": counts, "checks_run": [s[1] for s in steps], "check_errors": errors,
        "found": dict(sorted(ctx.found.items())), "deep_hash_check": deep, "max_per_code": max_per_code,
        "privacy": "Raportul nu conține CNP, serii CI, parole, telefoane, e-mailuri, adrese sau nume; înregistrările se identifică prin id/uid/cod_loc.",
    }
    return Report(ctx.sorted_issues(), meta)


cp_auth = _authorizer


def run_on_file(path: str, **kw) -> Report:
    """Deschide fișierul strict read-only, rulează verificările și atașează amprentele înainte/după."""
    before = fingerprint(path)
    conn = open_readonly(path)
    try:
        rep = run_checks(conn, db_label=os.path.basename(path), **kw)
    finally:
        conn.close()
    after = fingerprint(path)
    rep.meta["db_fingerprint_before"], rep.meta["db_fingerprint_after"] = before, after
    rep.meta["db_unchanged"] = before == after
    return rep
