#!/usr/bin/env python3
"""Aplică 023_cloud_admin_auth.sql pe Cloud-ul TEST (Supabase) sau pe un PostgreSQL de test, într-O SINGURĂ tranzacție, cu verificări înainte/după. Fără ștergeri de date.

Mediu (valorile NU se afișează):  CIMITIR_CP_ADMIN_DATABASE_URL = URL administrativ (owner-ul tabelelor; NU rolul runtime cimitir_cp_app)
  --project-ref <ref>   obligatoriu pentru Supabase: ref-ul din URL trebuie să coincidă; proiectul PROD este refuzat
  --check-only          doar verificări
  --rollback            rulează 023_cloud_admin_auth_ROLLBACK.sql
După aplicare rolul runtime primește DOAR drepturile din 023 (vezi fișierul). Idempotent: poate fi rulat de mai multe ori.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))
from cimitir_cloud_control_pg import PROTECTED_PROJECT_REFS  # noqa: E402

MIG = ROOT / "backend" / "cimitir" / "pg_migrations" / "023_cloud_admin_auth.sql"
ROLLBACK = ROOT / "backend" / "cimitir" / "pg_rollbacks" / "023_cloud_admin_auth_ROLLBACK.sql"
NEED = ("organizations", "cemeteries", "identities", "devices", "device_cemetery_bindings", "device_pairing_codes", "cp_org_roles", "cp_audit_log")
TABLES = ("cp_admin_state", "cp_admin_credentials", "cp_admin_sessions", "cp_admin_recovery")


def ref_of(url: str) -> str:
    m = re.search(r"//[^:@/]+\.([a-z]{20})(?::|@)", url) or re.search(r"//[^/@]*@db\.([a-z]{20})\.supabase\.co", url)
    return m.group(1) if m else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-ref", default="")
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    a = ap.parse_args()
    url = os.environ.get("CIMITIR_CP_ADMIN_DATABASE_URL", "")
    if not url:
        print("Setează CIMITIR_CP_ADMIN_DATABASE_URL (URL administrativ).", file=sys.stderr)
        return 2
    ref = ref_of(url)
    if ref in PROTECTED_PROJECT_REFS or any(p in url for p in PROTECTED_PROJECT_REFS):
        print("REFUZ: proiectul de producție este blocat.", file=sys.stderr)
        return 2
    if ("supabase" in url) and (not a.project_ref or a.project_ref != ref):
        print("REFUZ: pentru Supabase dă --project-ref care coincide cu ref-ul din URL.", file=sys.stderr)
        return 2
    import psycopg
    with psycopg.connect(url, connect_timeout=20) as c:
        if not c.execute("SELECT 1 FROM pg_roles WHERE rolname='cimitir_cp_app'").fetchone():
            print("rolul cimitir_cp_app lipsește (migrările 014+ nu sunt aplicate)", file=sys.stderr)
            return 1
        miss = [t for t in NEED if c.execute("SELECT to_regclass(%s)", (t,)).fetchone()[0] is None]
        if miss:
            print(f"tabele lipsă (migrările 008–019): {miss}", file=sys.stderr)
            return 1
        before = {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("organizations", "cemeteries", "devices", "device_cemetery_bindings", "identities", "cp_org_roles")}
        print("Stare înainte:", before)
        if a.check_only:
            done = c.execute("SELECT to_regprocedure('cp_admin_status()') IS NOT NULL").fetchone()[0]
            print("023 aplicată:", bool(done))
            return 0
        try:
            c.execute((ROLLBACK if a.rollback else MIG).read_text(encoding="utf-8"))
            if not a.rollback:
                bad = [t for t in TABLES if c.execute("SELECT to_regclass(%s)", (t,)).fetchone()[0] is None]
                if bad or not c.execute("SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='device_pairing_codes' AND column_name='revoked_at'").fetchone():
                    raise RuntimeError(f"verificare după migrare eșuată: {bad or 'revoked_at'}")
            after = {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in before}
            if after != before:
                raise RuntimeError(f"numărul de rânduri s-a schimbat: {before} -> {after}")
            c.commit()
        except Exception as exc:  # noqa: BLE001
            c.rollback()
            print(f"EȘEC (ROLLBACK tranzacție, nimic modificat): {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
            return 1
    print("OK: migrarea a fost aplicată." if not a.rollback else "OK: rollback aplicat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
