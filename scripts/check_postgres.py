#!/usr/bin/env python
"""check_postgres.py — verifică o bază PostgreSQL pentru Cimitir (doar citire; nu modifică nimic, nu activează nimic).

Verifică: conexiunea, versiunea PostgreSQL, latența (conectare + interogări) și starea schemei
(migrații aplicate / lipsă, tabele așteptate, drept de creare în schema `public`).

Citește DATABASE_URL din mediu (sau din .env, ca restul aplicației). NU afișează niciodată DATABASE_URL sau parola;
și mesajele de eroare sunt curățate de orice fragment al lor.

Utilizare:
    DATABASE_URL=postgresql://... python scripts/check_postgres.py [--json] [--timeout 10] [--samples 5] [--strict]

Cod de ieșire: 0 = conexiune OK și versiune acceptată; 1 = configurare/conexiune/versiune eșuată;
2 = doar cu --strict: conexiunea e bună, dar schema nu e completă (migrații lipsă).
Notă: PostgreSQL devine motorul aplicației doar cu CIMITIR_DB_ENGINE=postgres; scriptul funcționează independent de asta.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "shared", ROOT / "backend" / "cimitir"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import cimitir_config  # noqa: E402
import pg_compat  # noqa: E402

MIN_SERVER_VERSION = 130000  # gen_random_uuid() este nativ din PostgreSQL 13
MIGRATIONS_DIR = ROOT / "backend" / "cimitir" / "pg_migrations"


def expected_schema() -> dict:
    """Migrațiile din repo și tabelele pe care le creează (extrase din fișierele .sql)."""
    versions, tables = {}, set()
    for path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
        versions[int(path.name[:3])] = path.name
        tables |= set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", path.read_text(encoding="utf-8")))
    return {"migrations": versions, "tables": tables | {"schema_migrations"}}


def run_checks(url: str, timeout: int, samples: int) -> dict:
    import psycopg

    report: dict = {"ok": False, "engine_configured": cimitir_config.db_engine(), "connection": {}, "version": {},
                    "latency_ms": {}, "schema": {}, "warnings": []}
    started = time.perf_counter()
    conn = psycopg.connect(url, connect_timeout=max(1, timeout), autocommit=True, application_name="cimitir-check")
    report["connection"] = {"connected": True, "connect_ms": round((time.perf_counter() - started) * 1000, 1)}
    try:
        conn.read_only = True  # garanție suplimentară: scriptul nu poate modifica date
        num = int(conn.execute("SHOW server_version_num").fetchone()[0])
        report["version"] = {"server_version": conn.execute("SHOW server_version").fetchone()[0], "num": num,
                             "supported": num >= MIN_SERVER_VERSION, "minimum": "13"}

        timings = []
        for _ in range(max(1, samples)):
            t0 = time.perf_counter()
            conn.execute("SELECT 1").fetchone()
            timings.append((time.perf_counter() - t0) * 1000)
        report["latency_ms"] = {"connect": report["connection"]["connect_ms"], "query_min": round(min(timings), 2),
                                "query_avg": round(statistics.mean(timings), 2), "query_max": round(max(timings), 2),
                                "samples": len(timings)}

        want = expected_schema()
        present = {r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_type='BASE TABLE'")}
        applied = set()
        if "schema_migrations" in present:
            applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        pending = [name for v, name in want["migrations"].items() if v not in applied]
        missing = sorted(want["tables"] - present)
        can_create = bool(conn.execute("SELECT has_schema_privilege(current_schema(), 'CREATE')").fetchone()[0])
        if not present:
            state = "empty"          # bază goală: schema se va crea automat la prima pornire cu engine=postgres
        elif pending or missing:
            state = "incomplete"
        else:
            state = "complete"
        report["schema"] = {"database": conn.execute("SELECT current_database()").fetchone()[0],
                            "schema": conn.execute("SELECT current_schema()").fetchone()[0], "state": state,
                            "tables_present": len(present), "tables_expected": len(want["tables"]),
                            "tables_missing": missing if state != "empty" else [],
                            "migrations_applied": sorted(applied), "migrations_pending": pending,
                            "can_create": can_create}
        if not can_create and state != "complete":
            report["warnings"].append("Utilizatorul nu poate crea tabele în schema curentă: migrațiile automate vor eșua.")
        if not report["version"]["supported"]:
            report["warnings"].append("PostgreSQL mai vechi de 13: gen_random_uuid() nu este nativ.")
    finally:
        conn.close()
    report["ok"] = bool(report["connection"].get("connected") and report["version"].get("supported"))
    return report


def render(report: dict) -> str:
    c, v, lat, sc = report["connection"], report["version"], report["latency_ms"], report["schema"]
    mark = lambda ok: "OK " if ok else "EROARE"  # noqa: E731
    lines = [
        f"Motor configurat în aplicație: {report['engine_configured']}"
        + ("" if report["engine_configured"] == "postgres" else "  (PostgreSQL NU este activ; verificarea este doar informativă)"),
        f"[{mark(c.get('connected'))}] Conexiune: {'reușită' if c.get('connected') else 'eșuată'}"
        + (f" în {c['connect_ms']} ms" if c.get("connected") else ""),
    ]
    if v:
        lines.append(f"[{mark(v['supported'])}] Versiune PostgreSQL: {v['server_version']} (minim {v['minimum']})")
        lines.append(f"[OK ] Latență interogare: min {lat['query_min']} / medie {lat['query_avg']} / max {lat['query_max']} ms "
                     f"({lat['samples']} măsurători)")
        state_text = {"complete": "completă", "empty": "bază goală (se creează automat la prima pornire cu engine=postgres)",
                      "incomplete": "INCOMPLETĂ"}[sc["state"]]
        lines.append(f"[{mark(sc['state'] != 'incomplete')}] Schemă: {state_text} — baza '{sc['database']}', schema '{sc['schema']}', "
                     f"{sc['tables_present']}/{sc['tables_expected']} tabele, migrații aplicate {sc['migrations_applied'] or 'niciuna'}")
        if sc["migrations_pending"]:
            lines.append(f"      migrații neaplicate: {', '.join(sc['migrations_pending'])}")
        if sc["tables_missing"]:
            lines.append(f"      tabele lipsă: {', '.join(sc['tables_missing'])}")
        lines.append(f"[{mark(sc['can_create'])}] Drept de creare în schema curentă: {'da' if sc['can_create'] else 'nu'}")
    lines += [f"AVERTISMENT: {w}" for w in report["warnings"]]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Verifică conexiunea, versiunea, latența și schema PostgreSQL (doar citire).")
    ap.add_argument("--json", action="store_true", help="rezultat JSON (fără date sensibile)")
    ap.add_argument("--timeout", type=int, default=10, help="timeout de conectare, secunde (implicit 10)")
    ap.add_argument("--samples", type=int, default=5, help="numărul de măsurători de latență (implicit 5)")
    ap.add_argument("--strict", action="store_true", help="cod de ieșire 2 dacă schema nu este completă")
    args = ap.parse_args(argv)

    cimitir_config.load_env()
    url = ""
    try:
        url = pg_compat.database_url()
        report = run_checks(url, args.timeout, args.samples)
    except Exception as exc:  # orice eroare: mesaj curățat, fără URL/parolă
        kind = type(exc).__name__
        message = pg_compat.redact(str(exc).strip().splitlines()[0] if str(exc).strip() else "", url)
        if isinstance(exc, pg_compat.PgConfigError):
            message = "DATABASE_URL nu este setat (variabilă de mediu sau .env)."
        elif isinstance(exc, ModuleNotFoundError):
            message = "Lipsește driverul: pip install 'psycopg[binary]'"
        report = {"ok": False, "engine_configured": cimitir_config.db_engine(), "connection": {"connected": False},
                  "error": {"type": kind, "message": message[:300]}}
        if args.json:
            print(json.dumps(report, ensure_ascii=False))
        else:
            print(f"Motor configurat în aplicație: {report['engine_configured']}")
            print(f"[EROARE] Conexiune: eșuată — {kind}: {message[:300]}")
        return 1

    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render(report))
    if not report["ok"]:
        return 1
    if args.strict and report["schema"]["state"] != "complete":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
