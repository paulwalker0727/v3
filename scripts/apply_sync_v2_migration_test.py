"""Aplică 022_sync_v2.sql EXCLUSIV pe proiectul Supabase TEST, cu verificări înainte și după. Drepturi minime (coloane explicite), backup al stării anterioare pentru rollback verificabil, fără ștergeri de date.
Rollback: backend/cimitir/pg_rollbacks/022_sync_v2_ROLLBACK.sql (restaurează exact starea anterioară; refuză dacă amprenta nu coincide).

URL-ul administrativ se citește DOAR din variabila de mediu CIMITIR_CP_ADMIN_DATABASE_URL (nu se afișează, nu se loghează, nu se salvează).
  --check-only : doar verificările de dinainte (nu modifică nimic)
  fără opțiuni : verificări înainte -> migrarea într-O SINGURĂ tranzacție -> verificări după -> COMMIT doar dacă totul e în regulă (altfel ROLLBACK)
Refuză proiectul PROD (ref blocat) și orice URL al cărui ref nu coincide cu --project-ref.
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

MIGRATION = ROOT / "backend" / "cimitir" / "pg_migrations" / "022_sync_v2.sql"
LIVE = ("locuri_de_veci", "concesionari", "contracte", "inhumati", "plati", "amenajari_avize")
NEW = ("sync_v2_ops", "sync_v2_conflicts", "sync_v2_permissions", "sync_v2_runs")
NEED_COLS = ("uid", "organization_uid", "cemetery_uid", "version", "change_seq")


def counts(c) -> dict:
    return {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in LIVE + ("sync_tombstones",)}


def precheck(c) -> list:
    errs = []
    if not c.execute("SELECT 1 FROM pg_roles WHERE rolname='cimitir_cp_app'").fetchone():
        errs.append("rolul cimitir_cp_app lipsește (migrările 014+ nu sunt aplicate)")
    for f in ("cp_org", "cp_org_ok", "cp_cem_ok"):
        if not c.execute("SELECT 1 FROM pg_proc WHERE proname=%s", (f,)).fetchone():
            errs.append(f"funcția {f}() lipsește (migrările 014+)")
    if not c.execute("SELECT 1 FROM pg_class WHERE relname='sync_change_seq' AND relkind='S'").fetchone():
        errs.append("secvența sync_change_seq lipsește (migrarea 003)")
    for t in LIVE + ("sync_tombstones",):
        if c.execute("SELECT to_regclass(%s)", (t,)).fetchone()[0] is None:
            errs.append(f"tabela {t} lipsește")
            continue
        cols = {r[0] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s", (t,)).fetchall()}
        need = ("uid", "organization_uid", "cemetery_uid") if t == "sync_tombstones" else NEED_COLS
        miss = [x for x in need if x not in cols]
        if miss:
            errs.append(f"{t}: coloane lipsă {miss}")
    return errs


def postcheck(c, before: dict) -> list:
    errs = []
    after = counts(c)
    for t, n in before.items():
        if after[t] < n:
            errs.append(f"{t}: rânduri pierdute ({n} -> {after[t]})")
    for t in NEW:
        if c.execute("SELECT to_regclass(%s)", (t,)).fetchone()[0] is None:
            errs.append(f"tabela {t} nu a fost creată")
    for t in LIVE:
        for p in ("SELECT", "DELETE"):
            if not c.execute("SELECT has_table_privilege('cimitir_cp_app', %s, %s)", (t, p)).fetchone()[0]:
                errs.append(f"cimitir_cp_app fără {p} pe {t}")
        for p in ("INSERT", "UPDATE"):
            if c.execute("SELECT has_table_privilege('cimitir_cp_app', %s, %s)", (t, p)).fetchone()[0]:
                errs.append(f"cimitir_cp_app are {p} la nivel de TABEL pe {t} (trebuie doar pe coloane)")
        for col in ("id", "version", "change_seq", "modified_at"):
            for p in ("INSERT", "UPDATE"):
                if c.execute("SELECT has_column_privilege('cimitir_cp_app', %s, %s, %s)", (t, col, p)).fetchone()[0]:
                    errs.append(f"cimitir_cp_app are {p} pe {t}.{col} (interzis)")
        for col in ("uid", "organization_uid", "cemetery_uid"):
            if c.execute("SELECT has_column_privilege('cimitir_cp_app', %s, %s, 'UPDATE')", (t, col)).fetchone()[0]:
                errs.append(f"cimitir_cp_app poate modifica {t}.{col} (interzis)")
        if not c.execute("SELECT relrowsecurity FROM pg_class WHERE oid=%s::regclass", (t,)).fetchone()[0]:
            errs.append(f"RLS dezactivat pe {t}")
    if not c.execute("SELECT count(*) FROM sync_v2_migration_backup WHERE batch='pre-022' AND kind='fingerprint'").fetchone()[0]:
        errs.append("backup-ul stării anterioare (rollback) lipsește")
    for t in ("sync_tombstones",):
        for p in ("INSERT", "UPDATE", "DELETE"):
            if c.execute("SELECT has_table_privilege('cimitir_cp_app', %s, %s)", (t, p)).fetchone()[0]:
                errs.append(f"cimitir_cp_app are {p} pe {t} (interzis)")
    if c.execute("SELECT has_table_privilege('cimitir_cp_app', 'sync_v2_migration_backup', 'SELECT')").fetchone()[0]:
        errs.append("cimitir_cp_app vede sync_v2_migration_backup")
    if c.execute("SELECT rolbypassrls FROM pg_roles WHERE rolname='cimitir_cp_app'").fetchone()[0]:
        errs.append("cimitir_cp_app are BYPASSRLS (interzis)")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-ref", required=True, help="ref-ul proiectului TEST (20 litere)")
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()
    url = os.environ.get("CIMITIR_CP_ADMIN_DATABASE_URL", "")
    if not url:
        print("Lipsește CIMITIR_CP_ADMIN_DATABASE_URL.")
        return 2
    if not re.fullmatch(r"[a-z]{20}", args.project_ref) or args.project_ref in PROTECTED_PROJECT_REFS or any(r in url for r in PROTECTED_PROJECT_REFS):
        print("Proiect refuzat (PROD blocat sau ref invalid).")
        return 2
    if "supabase" in url and args.project_ref not in url:
        print("URL-ul nu corespunde proiectului TEST indicat.")
        return 2
    import psycopg
    with psycopg.connect(url, connect_timeout=15, autocommit=False) as c:
        errs = precheck(c)
        if errs:
            print("VERIFICARE ÎNAINTE: EȘUATĂ\n  - " + "\n  - ".join(errs))
            return 1
        before = counts(c)
        print("VERIFICARE ÎNAINTE: OK. Rânduri existente:", ", ".join(f"{k}={v}" for k, v in before.items()))
        if args.check_only:
            return 0
        c.execute(MIGRATION.read_text(encoding="utf-8"))
        errs = postcheck(c, before)
        if errs:
            c.rollback()
            print("VERIFICARE DUPĂ: EȘUATĂ — ROLLBACK, nimic nu s-a salvat\n  - " + "\n  - ".join(errs))
            return 1
        c.commit()
        print("VERIFICARE DUPĂ: OK. 022_sync_v2.sql aplicat; datele existente nu au fost modificate sau șterse.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
