#!/usr/bin/env python3
"""Pregătește un proiect Supabase TEST (NOU/gol sau deja pregătit) pentru Sync v2: aplică migrațiile 001–NNN (idempotente, în ordine), fixează parola rolului runtime `cimitir_cp_app`
(minim de drepturi, fără BYPASSRLS) și verifică RLS. NU salvează secrete: URL-ul administrativ vine din CIMITIR_CP_ADMIN_DATABASE_URL, parola rolului runtime din CIMITIR_CP_APP_PASSWORD.

  --project-ref <ref>   obligatoriu; trebuie să coincidă cu URL-ul; proiectul PROD este refuzat
  --check-only          doar raportează starea (nu modifică nimic)
Rollback: backend/cimitir/pg_rollbacks/*_ROLLBACK.sql (024 -> 023 -> 022). Nu șterge date.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))
from cimitir_cloud_control_pg import PROTECTED_PROJECT_REFS  # noqa: E402

MIG = ROOT / "backend" / "cimitir" / "pg_migrations"
CORE = ("locuri_de_veci", "concesionari", "contracte", "inhumati", "plati", "amenajari_avize", "sync_v2_documents", "sync_v2_ops", "sync_v2_conflicts", "cp_storage_objects")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-ref", required=True)
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()
    url = os.environ.get("CIMITIR_CP_ADMIN_DATABASE_URL", "")
    m = re.search(r"postgres(?:ql)?://([^:@/]+)", url)
    if a.project_ref in PROTECTED_PROJECT_REFS or not m or not m.group(1).endswith("." + a.project_ref):
        print("REFUZ: ref protejat sau URL-ul administrativ nu corespunde proiectului indicat.", file=sys.stderr)
        return 2
    app_pw = os.environ.get("CIMITIR_CP_APP_PASSWORD", "")
    with psycopg.connect(url, autocommit=True, connect_timeout=20) as c:
        have = {r[0] for r in c.execute("SELECT version FROM schema_migrations").fetchall()} if c.execute("SELECT to_regclass('schema_migrations')").fetchone()[0] else set()
        print("migrări deja înregistrate:", len(have))
        if a.check_only:
            print("roluri:", [r[0] for r in c.execute("SELECT rolname FROM pg_roles WHERE rolname LIKE 'cimitir%'").fetchall()])
            return 0
        for f in sorted(MIG.glob("*.sql")):
            try:
                c.execute(f.read_text(encoding="utf-8"))
                print("OK  ", f.name)
            except Exception as exc:  # noqa: BLE001
                print("FAIL", f.name, type(exc).__name__, str(exc)[:300])
                return 1
        if app_pw:
            c.execute(psycopg.sql.SQL("ALTER ROLE cimitir_cp_app LOGIN PASSWORD {}").format(psycopg.sql.Literal(app_pw)))
            c.execute("GRANT cimitir_cp_app TO CURRENT_USER") if False else None
        r = c.execute("SELECT rolcanlogin, rolbypassrls, rolsuper FROM pg_roles WHERE rolname='cimitir_cp_app'").fetchone()
        print("cimitir_cp_app (login, bypassrls, super):", r)
        bad = [t for t in CORE if c.execute("SELECT relrowsecurity FROM pg_class WHERE relname=%s AND relnamespace='public'::regnamespace", (t,)).fetchone() in (None, (False,))]
        print("tabele fără RLS:", bad)
        return 0 if r and r[0] and not r[1] and not r[2] and not bad else 1


if __name__ == "__main__":
    import psycopg.sql  # noqa: F401
    sys.exit(main())
