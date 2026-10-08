#!/usr/bin/env python
"""verify_online_startup.py — verifică pornirea serverului în modul ONLINE cu PostgreSQL + Storage Supabase, fără a afișa secrete.

Pași (fiecare cu PASS/FAIL/WARN; cod de ieșire 0 doar dacă nu există FAIL):
  1. Variabile de mediu (doar CONFIGURAT/NECONFIGURAT): CIMITIR_MODE=online, CIMITIR_DB_ENGINE=postgres, DATABASE_URL,
     CIMITIR_PUBLIC_URL (https), CIMITIR_STORAGE_DRIVER=supabase, SUPABASE_*; CIMITIR_ALLOW_DEFAULT_USERS nu trebuie să fie 1.
  2. PostgreSQL: conexiune, versiune, schemă/migrații (doar citire, ca scripts/check_postgres.py).
  3. Supabase Storage: autentificare + bucket PRIVAT (nu creează nimic).
  4. (doar cu --start) pornirea reală a aplicației in-process (evenimentele de startup rulează ca în producție: se aplică
     migrațiile PostgreSQL lipsă!) + /api/health, /portal, /admin, /api/sync/ping fără cheie (401), motorul AI, conturi riscante,
     cheia de sincronizare în PostgreSQL (existentă/inexistentă, fără valoare).

    python scripts/verify_online_startup.py            # doar citire (pașii 1-3)
    python scripts/verify_online_startup.py --start    # + pornire reală (pasul 4)

Variabilele se iau din mediu sau din backend/.env (ca serverul). Nu afișează DATABASE_URL, chei, parole sau gazde.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "shared", ROOT / "backend" / "cimitir", ROOT / "backend", ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import cimitir_config  # noqa: E402

cimitir_config.load_env()

RESULTS: list = []


def report(status: str, label: str, detail: str = "") -> None:
    RESULTS.append(status)
    print(f"[{status:<4}] {label}" + (f" — {detail}" if detail else ""))


def has(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())


def step_env() -> None:
    print("== 1. Variabile de mediu ==")
    mode = os.environ.get("CIMITIR_MODE", "").strip().lower()
    engine = os.environ.get("CIMITIR_DB_ENGINE", "").strip().lower()
    report("PASS" if mode == "online" else "FAIL", "CIMITIR_MODE=online", "" if mode == "online" else f"acum: {mode or 'nesetat'}")
    report("PASS" if engine == "postgres" else "FAIL", "CIMITIR_DB_ENGINE=postgres", "" if engine == "postgres" else f"acum: {engine or 'nesetat'}")
    report("PASS" if has("DATABASE_URL") else "FAIL", "DATABASE_URL", "CONFIGURAT" if has("DATABASE_URL") else "NECONFIGURAT")
    public = os.environ.get("CIMITIR_PUBLIC_URL", "").strip()
    if not public:
        report("FAIL", "CIMITIR_PUBLIC_URL", "NECONFIGURAT (QR mobil și MAIN au nevoie de adresa publică https)")
    elif not public.startswith("https://"):
        report("FAIL", "CIMITIR_PUBLIC_URL", "nu începe cu https://")
    else:
        report("PASS", "CIMITIR_PUBLIC_URL", "CONFIGURAT (https)")
    driver = os.environ.get("CIMITIR_STORAGE_DRIVER", "").strip().lower()
    report("PASS" if driver == "supabase" else "FAIL", "CIMITIR_STORAGE_DRIVER=supabase", "" if driver == "supabase" else f"acum: {driver or 'nesetat'}")
    for name, alts in (("SUPABASE_URL", ()), ("SUPABASE_SECRET_KEY", ("SUPABASE_SERVICE_ROLE_KEY",)), ("SUPABASE_BUCKET", ())):
        ok = has(name) or any(has(a) for a in alts)
        report("PASS" if ok else "FAIL", name, "CONFIGURAT" if ok else "NECONFIGURAT")
    if os.environ.get("CIMITIR_ALLOW_DEFAULT_USERS", "").strip() == "1":
        report("FAIL", "CIMITIR_ALLOW_DEFAULT_USERS", "este 1: permite recrearea conturilor admin/user implicite; elimină-l")
    else:
        report("PASS", "CIMITIR_ALLOW_DEFAULT_USERS", "nesetat")
    if has("JWT_SECRET"):
        report("PASS", "JWT_SECRET", "CONFIGURAT")
    else:
        report("WARN", "JWT_SECRET", "NECONFIGURAT: pe Render discul e efemer, deci sesiunile se pierd la fiecare redeploy")
    if has("GEMINI_API_KEY") or has("EMERGENT_LLM_KEY"):
        report("PASS", "Cheie AI", "CONFIGURAT")
    else:
        report("WARN", "Cheie AI", "nicio cheie: extragerea AI rămâne dezactivată (normal în prima etapă)")


def step_postgres() -> None:
    print("== 2. PostgreSQL ==")
    if not has("DATABASE_URL"):
        report("FAIL", "Conexiune PostgreSQL", "DATABASE_URL lipsește")
        return
    try:
        import check_postgres
        rep = check_postgres.run_checks(os.environ["DATABASE_URL"], 10, 3)
    except Exception as exc:
        report("FAIL", "Conexiune PostgreSQL", type(exc).__name__)
        return
    c, v, sc = rep["connection"], rep["version"], rep["schema"]
    report("PASS" if c.get("connected") else "FAIL", "Conexiune PostgreSQL", f"{c.get('connect_ms')} ms")
    if v:
        report("PASS" if v["supported"] else "FAIL", "Versiune PostgreSQL", v["server_version"])
    if sc:
        pending = sc.get("migrations_pending") or []
        status = "PASS" if sc["state"] == "complete" else ("WARN" if sc["state"] == "empty" or pending else "FAIL")
        report(status, "Schemă", f"{sc['state']}, tabele {sc['tables_present']}/{sc['tables_expected']}, neaplicate: {', '.join(pending) or 'niciuna'}"
                                  + (" (se aplică la pornirea cu --start / pe Render)" if pending else ""))
        report("PASS" if sc["can_create"] else "FAIL", "Drept de creare în schemă")
    for w in rep.get("warnings", []):
        report("WARN", w)


def step_storage() -> None:
    print("== 3. Supabase Storage ==")
    try:
        import check_cloud_config
        warnings: list = []
        ok = check_cloud_config.check_storage(False, warnings)
    except Exception as exc:
        report("FAIL", "Storage", type(exc).__name__)
        return
    report("PASS" if ok else "FAIL", "Storage ONLINE + bucket PRIVAT")
    for w in dict.fromkeys(warnings):
        report("WARN", w)


def step_start() -> None:
    print("== 4. Pornire reală a aplicației (online + PostgreSQL) ==")
    if os.environ.get("CIMITIR_MODE", "").strip().lower() != "online" or cimitir_config.db_engine() != "postgres":
        report("FAIL", "Pornire", "refuzată: CIMITIR_MODE=online și CIMITIR_DB_ENGINE=postgres sunt obligatorii (altfel ar scrie într-o bază locală)")
        return
    if importlib.util.find_spec("httpx") is None:
        report("FAIL", "Pornire", "lipsește httpx pentru clientul de test (pip install httpx; nu e nevoie de el pe Render)")
        return
    try:
        from fastapi.testclient import TestClient
        import server
    except Exception as exc:
        report("FAIL", "import backend", type(exc).__name__)
        return
    try:
        with TestClient(server.app, follow_redirects=True) as client:
            health = client.get("/api/health")
            body = health.json() if health.headers.get("content-type", "").startswith("application/json") else {}
            ok = health.status_code == 200 and body.get("database") == "postgres" and body.get("database_status") == "online"
            report("PASS" if ok else "FAIL", "/api/health", f"{health.status_code}, database={body.get('database')}, status={body.get('database_status')}")
            report("PASS" if client.get("/portal").status_code == 200 else "FAIL", "/portal")
            admin = client.get("/admin")
            report("PASS" if admin.status_code == 200 and admin.url.path.startswith("/admin") else "FAIL", "/admin (pagina de acces)", str(admin.status_code))
            ping = client.get("/api/sync/ping")
            report("PASS" if ping.status_code == 401 else "FAIL", "/api/sync/ping fără cheie", f"{ping.status_code} (așteptat 401)")
        report_ai()
        report_accounts()
    except Exception as exc:
        report("FAIL", "Pornire / cereri", type(exc).__name__)


def report_ai() -> None:
    import ai_extract
    st = ai_extract.engine_status()
    report("PASS", "Motor AI", f"{'disponibil: ' + st['model'] if st['available'] else 'indisponibil (fără cheie), aplicația rulează normal'}")


def report_accounts() -> None:
    import cimitir_auth as auth
    import lock_accounts
    users = auth.list_users()
    risky = [u["username"] for u in users if u["active"] and lock_accounts.is_target(u["username"], {"alin"})]
    report("PASS" if not risky else "FAIL", "Conturi implicite/tst_* active", "niciunul" if not risky else ", ".join(risky) + " (rulează scripts/lock_accounts.py --apply)")
    weak = lock_accounts.default_password_accounts(auth, [u for u in users if u["active"]])
    report("PASS" if not weak else "FAIL", "Parole implicite pe conturi active", "niciuna" if not weak else ", ".join(weak))
    admins = [u for u in users if u["role"] == "admin" and u["active"]]
    report("PASS" if admins else "FAIL", "Administrator activ", str(len(admins)))
    import store as cim
    import server
    cim.bind_source(server.DEFAULT_SOURCE)
    has_key = bool(cim.get_setting("sync_key_hash"))
    report("PASS" if has_key else "WARN", "Cheie MAIN (sync_key_hash) în PostgreSQL",
           "există" if has_key else "nu există încă: generează-o din /admin/setari")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", action="store_true", help="pornește aplicația real (rulează evenimentele de startup, inclusiv migrațiile lipsă)")
    args = ap.parse_args(argv)
    step_env()
    step_postgres()
    step_storage()
    if args.start:
        step_start()
    else:
        print("== 4. Pornire reală: omisă (folosește --start; aplică migrațiile PostgreSQL lipsă, ca serverul) ==")
    fails, warns = RESULTS.count("FAIL"), RESULTS.count("WARN")
    print(f"\nREZULTAT: {'FAIL' if fails else 'PASS'} ({RESULTS.count('PASS')} PASS, {warns} WARN, {fails} FAIL)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
