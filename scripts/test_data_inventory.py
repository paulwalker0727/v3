#!/usr/bin/env python3
"""Inventariere și arhivare CONTROLATĂ a registrelor FICTIVE de test din Cloud TEST (organizații `LIVE-PROVISION-*`, cimitire `LIVE-VERIFY-*`). Nu șterge nimic, niciodată.

  show --cemetery UID [...]       READ-ONLY, pentru ORICE cimitir: există? status, legături active, număr de rânduri sincronizate (verificare MAIN→PostgreSQL)
  inventory                       listează registrele fictive (și numără datele lor); orice altceva este „protejat” și nu poate fi modificat
  archive --cemetery UID [...]    PREVIZUALIZARE (implicit, nu scrie nimic): ce înregistrări ar fi afectate + tokenul de confirmare
  archive ... --apply --confirm TOKEN [--revoke-bindings]   aplică: status cimitir => ARCHIVED (reversibil: status înapoi pe DRAFT/ACTIVE), opțional revocă legăturile active
Mediu: CIMITIR_CP_ADMIN_DATABASE_URL (rol administrativ al proiectului TEST; valoarea nu se afișează). Pentru Supabase: --project-ref obligatoriu și egal cu ref-ul din URL; PROD refuzat.
Se refuză orice cimitir care nu are nume `LIVE-VERIFY-*` și nu aparține unei organizații `LIVE-PROVISION-*` (alte date fictive, ex. seed-ul „Parohia Test (FICTIV)”, sunt PROTEJATE), chiar dacă UID-ul e dat explicit.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))
sys.path.insert(0, str(ROOT / "scripts"))
from apply_cloud_admin_migration import ref_of  # noqa: E402
from cimitir_cloud_control_pg import PROTECTED_PROJECT_REFS  # noqa: E402

TEST_CEM = "LIVE-VERIFY-%"
TEST_ORG = "LIVE-PROVISION-%"
DATA_TABLES = ("locuri_de_veci", "concesionari", "contracte", "inhumati", "plati", "amenajari_avize", "sync_v2_documents")

LIST_SQL = """
SELECT c.uid::text AS cem, c.name AS cem_name, c.status, o.uid::text AS org, o.display_name AS org_name,
       (c.name LIKE %s OR o.display_name LIKE %s) AS is_test
  FROM cemeteries c JOIN organizations o ON o.uid = c.organization_uid ORDER BY o.display_name, c.name"""


def connect():
    url = os.environ.get("CIMITIR_CP_ADMIN_DATABASE_URL", "")
    return url


def guard(url: str, project_ref: str) -> None:
    if not url:
        sys.exit("Setează CIMITIR_CP_ADMIN_DATABASE_URL.")
    ref = ref_of(url)
    if ref in PROTECTED_PROJECT_REFS or any(p in url for p in PROTECTED_PROJECT_REFS):
        sys.exit("REFUZ: proiectul de producție este blocat.")
    if "supabase" in url and (not project_ref or project_ref != ref):
        sys.exit("REFUZ: pentru Supabase dă --project-ref egal cu ref-ul din URL.")


def rows(c):
    return [dict(zip(("cem", "cem_name", "status", "org", "org_name", "is_test"), r)) for r in c.execute(LIST_SQL, (TEST_CEM, TEST_ORG)).fetchall()]


def details(c, r):
    out = {"legaturi_active": c.execute("SELECT count(*) FROM device_cemetery_bindings WHERE cemetery_uid=%s::uuid AND active AND revoked_at IS NULL", (r["cem"],)).fetchone()[0]}
    for t in DATA_TABLES:
        if c.execute("SELECT to_regclass(%s)", (t,)).fetchone()[0] and c.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name='cemetery_uid'", (t,)).fetchone():
            n = c.execute(f"SELECT count(*) FROM {t} WHERE cemetery_uid=%s::uuid", (r["cem"],)).fetchone()[0]
            if n:
                out[t] = n
    return out


def token(uids) -> str:
    return "ARHIVEAZA-" + hashlib.sha256(",".join(sorted(uids)).encode()).hexdigest()[:10]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=("inventory", "show", "archive"))
    ap.add_argument("--project-ref", default="")
    ap.add_argument("--cemetery", action="append", default=[])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--confirm", default="")
    ap.add_argument("--revoke-bindings", action="store_true")
    a = ap.parse_args()
    url = connect()
    guard(url, a.project_ref)
    import psycopg
    with psycopg.connect(url, connect_timeout=20) as c:
        allr = rows(c)
        if a.command == "inventory":
            for r in allr:
                tag = "FICTIV " if r["is_test"] else "PROTEJAT"
                print(f"[{tag}] {r['org_name']} / {r['cem_name']} · {r['status']} · cem={r['cem']} org={r['org']}" + (f" · {details(c, r)}" if r["is_test"] else ""))
            print(f"Total: {len(allr)} cimitire, {sum(1 for r in allr if r['is_test'])} fictive (arhivabile), {sum(1 for r in allr if not r['is_test'])} protejate.")
            return 0
        if a.command == "show":
            for u in a.cemetery or []:
                r = next((x for x in allr if x["cem"] == u.lower()), None)
                if not r:
                    print(f"{u}: NU există în Cloud")
                    continue
                print(f"{r['org_name']} / {r['cem_name']} · {r['status']} · org={r['org']}")
                for k, v in details(c, r).items():
                    print(f"   {k}: {v}")
            return 0
        if not a.cemetery:
            sys.exit("Indică cel puțin un --cemetery UID (vezi `inventory`).")
        byid = {r["cem"]: r for r in allr}
        sel, bad = [], []
        for u in a.cemetery:
            r = byid.get(u.lower())
            (sel if r and r["is_test"] else bad).append(r or {"cem": u, "org_name": "?", "cem_name": "inexistent"})
        for r in bad:
            print(f"[REFUZAT] {r['cem']} ({r['org_name']} / {r['cem_name']}): nu este un registru fictiv de test (sau nu există).")
        if bad or not sel:
            return 2
        tok = token([r["cem"] for r in sel])
        print("PREVIZUALIZARE — se vor modifica DOAR aceste înregistrări (nimic nu se șterge):")
        for r in sel:
            d = details(c, r)
            print(f" • {r['org_name']} / {r['cem_name']} ({r['cem']}): status {r['status']} → ARCHIVED; date păstrate: { {k: v for k, v in d.items() if k != 'legaturi_active'} or 'fără' }; "
                  f"legături active: {d['legaturi_active']}" + (" → vor fi REVOCATE" if a.revoke_bindings and d["legaturi_active"] else " (rămân neatinse)"))
        if not a.apply:
            print(f"\nNimic nu s-a scris. Pentru aplicare: ... --apply --confirm {tok}" + (" --revoke-bindings" if a.revoke_bindings else ""))
            return 0
        if a.confirm != tok:
            print("REFUZ: tokenul de confirmare nu corespunde previzualizării curente.", file=sys.stderr)
            return 2
        for r in sel:
            if r["status"] != "ARCHIVED":
                c.execute("UPDATE cemeteries SET status='ARCHIVED' WHERE uid=%s::uuid", (r["cem"],))
            if a.revoke_bindings:
                c.execute("UPDATE device_cemetery_bindings SET active=false, revoked_at=now() WHERE cemetery_uid=%s::uuid AND active AND revoked_at IS NULL", (r["cem"],))
        c.commit()
        print(f"OK: {len(sel)} cimitir(e) arhivate (reversibil prin UPDATE cemeteries SET status='DRAFT').")
    return 0


if __name__ == "__main__":
    sys.exit(main())
