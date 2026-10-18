#!/usr/bin/env python3
"""Aplică DOAR migrările 025 (provizionare registru) și 026 (cereri de înregistrare din MAIN) pe un Cloud TEST existent, după verificarea schemei. Nu reaplică 001–024. Fără ștergeri de date.

Mediu (valorile NU se afișează):  CIMITIR_CP_ADMIN_DATABASE_URL = URL administrativ (owner-ul tabelelor; NU rolul runtime cimitir_cp_app)
  --project-ref <ref>   obligatoriu pentru Supabase: trebuie să coincidă cu ref-ul din URL; proiectul PROD este refuzat
  --check-only          raport read-only: migrări/funcții/tabele/rol runtime/RLS + numărul de rânduri (nu modifică nimic)
  --rollback            rulează rollback-urile 026 și 025 (elimină funcțiile și tabelul de cereri; organizațiile/cimitirele create rămân)
Cere 023 aplicată. O singură tranzacție; numărul de rânduri din tabelele de control trebuie să rămână identic. Idempotent.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))
sys.path.insert(0, str(ROOT / "scripts"))
from apply_cloud_admin_migration import ref_of  # noqa: E402
from cimitir_cloud_control_pg import PROTECTED_PROJECT_REFS  # noqa: E402

_M, _R = ROOT / "backend" / "cimitir" / "pg_migrations", ROOT / "backend" / "cimitir" / "pg_rollbacks"
MIGS = (_M / "025_admin_provision_registry.sql", _M / "026_registry_requests.sql")
ROLLBACKS = (_R / "026_registry_requests_ROLLBACK.sql", _R / "025_admin_provision_registry_ROLLBACK.sql")
FUNCS = ("cp_admin_is_provisioner(uuid)", "cp_admin_registry_lookup(uuid,uuid,uuid)", "cp_admin_provision(uuid,uuid,text,uuid,text)")
FUNCS26 = ("cp_regreq_create(text,uuid,text,uuid,text,text)", "cp_regreq_list(uuid)", "cp_regreq_decide(uuid,uuid,boolean)", "cp_regreq_poll(uuid,text)", "cp_regreq_claim(uuid,text)")
NEED = ("organizations", "cemeteries", "identities", "cp_org_roles", "cp_admin_state", "cp_admin_credentials", "cp_platform_admins")
COUNT = ("organizations", "cemeteries", "devices", "device_cemetery_bindings", "identities", "cp_org_roles", "cp_admin_credentials")
OPTIONAL_COUNT = ("sync_v2_documents",)


def report(c) -> dict:
    out = {"tabele_lipsa": [t for t in NEED if c.execute("SELECT to_regclass(%s)", (t,)).fetchone()[0] is None]}
    out["roluri"] = {r[0]: {"login": r[1], "bypassrls": r[2], "super": r[3]} for r in c.execute(
        "SELECT rolname, rolcanlogin, rolbypassrls, rolsuper FROM pg_roles WHERE rolname LIKE 'cimitir%'").fetchall()}
    out["023_aplicata"] = bool(c.execute("SELECT to_regprocedure('cp_admin_status()') IS NOT NULL").fetchone()[0])
    out["025_aplicata"] = all(c.execute("SELECT to_regprocedure(%s) IS NOT NULL", (f,)).fetchone()[0] for f in FUNCS)
    out["026_aplicata"] = all(c.execute("SELECT to_regprocedure(%s) IS NOT NULL", (f,)).fetchone()[0] for f in FUNCS26) and c.execute("SELECT to_regclass('cp_registry_requests')").fetchone()[0] is not None
    out["rls"] = {t: bool((c.execute("SELECT relrowsecurity FROM pg_class WHERE oid = to_regclass(%s)", (t,)).fetchone() or [False])[0])
                  for t in ("organizations", "cemeteries", "cp_org_roles", "sync_v2_documents") if c.execute("SELECT to_regclass(%s)", (t,)).fetchone()[0]}
    out["randuri"] = {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in COUNT + OPTIONAL_COUNT if c.execute("SELECT to_regclass(%s)", (t,)).fetchone()[0]}
    return out


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
    if "supabase" in url and (not a.project_ref or a.project_ref != ref):
        print("REFUZ: pentru Supabase dă --project-ref care coincide cu ref-ul din URL.", file=sys.stderr)
        return 2
    import psycopg
    with psycopg.connect(url, connect_timeout=20) as c:
        rep = report(c)
        for k, v in rep.items():
            print(f"{k}: {v}")
        if a.check_only:
            return 0 if not rep["tabele_lipsa"] and rep["023_aplicata"] else 1
        if rep["tabele_lipsa"] or not rep["023_aplicata"]:
            print("REFUZ: schema nu e compatibilă (lipsesc tabele sau migrarea 023). Nu s-a modificat nimic.", file=sys.stderr)
            return 1
        before = rep["randuri"]
        try:
            for f in (ROLLBACKS if a.rollback else MIGS):
                c.execute(f.read_text(encoding="utf-8"))
            now = report(c)
            if now["randuri"] != before:
                raise RuntimeError(f"numărul de rânduri s-a schimbat: {before} -> {now['randuri']}")
            if not a.rollback and not (now["025_aplicata"] and now["026_aplicata"]):
                raise RuntimeError("funcțiile 025/026 lipsesc după aplicare")
            c.commit()
        except Exception as exc:  # noqa: BLE001
            c.rollback()
            print(f"EȘEC (ROLLBACK tranzacție, nimic modificat): {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
            return 1
    print("OK: rollback 026+025 aplicat." if a.rollback else "OK: migrările 025 și 026 sunt aplicate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
