#!/usr/bin/env python
"""harden_supabase_rls.py — inchide accesul prin API-ul Supabase (PostgREST) la tabelele aplicatiei.

Supabase da implicit drepturi complete rolurilor `anon` si `authenticated` pe tabelele din schema `public`; cheia publishable/anon
este publica prin design. Fara RLS, oricine ar putea citi/scrie `users` (hash-uri de parole), CNP-uri etc. prin /rest/v1/.
Aplicatia se conecteaza direct ca rol `postgres` (ocoleste RLS), deci nu este afectata.

Scriptul este idempotent: ENABLE ROW LEVEL SECURITY pe toate tabelele din `public` (fara politici = acces refuzat pentru anon/authenticated),
REVOKE ALL de la anon/authenticated si DEFAULT PRIVILEGES pentru tabele viitoare. Nu afiseaza URL/parola; doar numere.
Ruleaza-l dupa migratii:  python scripts/harden_supabase_rls.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))
import cimitir_config  # noqa: E402

cimitir_config.load_env()


def main() -> int:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL: NECONFIGURAT")
        return 1
    import psycopg
    try:
        with psycopg.connect(url, connect_timeout=15, autocommit=True) as conn:
            roles = {r[0] for r in conn.execute("SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')").fetchall()}
            tables = [r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()]
            for t in tables:
                conn.execute(f'ALTER TABLE public."{t}" ENABLE ROW LEVEL SECURITY')
            for role in sorted(roles):
                conn.execute(f'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}')
                conn.execute(f'REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role}')
                conn.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {role}')
                conn.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {role}')
            on = conn.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND rowsecurity").fetchone()[0]
            left = conn.execute("SELECT count(*) FROM information_schema.role_table_grants WHERE table_schema='public' "
                                "AND grantee IN ('anon','authenticated')").fetchone()[0]
    except Exception as exc:
        print(f"EROARE: {type(exc).__name__}")
        return 2
    print(f"Tabele in public: {len(tables)}; RLS activ: {on}; drepturi ramase pentru anon/authenticated: {left}")
    return 0 if on == len(tables) and left == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
