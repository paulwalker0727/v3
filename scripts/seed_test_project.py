#!/usr/bin/env python3
"""Seed FICTIV pentru un proiect Supabase TEST: primul administrator (Owner) + organizația „Parohia Test (FICTIV)” + cimitirul „Cimitir Test FICTIV”.

Nu rulează pe PROD (ref protejat), nu rulează dacă există deja un administrator (nu suprascrie nimic) și NU tipărește/salvează parola: ea se citește din
CIMITIR_SEED_ADMIN_PASSWORD (mediu). Rol runtime: CIMITIR_CP_DATABASE_URL (cimitir_cp_app); CIMITIR_CP_ADMIN_DATABASE_URL e necesar planului de control pentru seed.
  python scripts/seed_test_project.py --project-ref <REF> --email admin.test@example.invalid
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))

import cimitir_cloud_admin as adm  # noqa: E402
import cimitir_cloud_control_pg as ccp  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-ref", required=True)
    ap.add_argument("--email", default="admin.test@example.invalid")
    a = ap.parse_args()
    if a.project_ref in ccp.PROTECTED_PROJECT_REFS or a.project_ref not in os.environ.get("CIMITIR_CP_DATABASE_URL", ""):
        print("REFUZ: ref protejat sau URL runtime care nu corespunde.", file=sys.stderr)
        return 2
    pw = os.environ.get("CIMITIR_SEED_ADMIN_PASSWORD", "")
    if not pw:
        print("CIMITIR_SEED_ADMIN_PASSWORD lipsește.", file=sys.stderr)
        return 2
    token = secrets.token_urlsafe(32)
    env = dict(os.environ, **{adm.ENV_TOKEN: token, adm.ENV_TOKEN_EXPIRES: str(time.time() + 600)})
    svc = adm.AdminService(ccp.PostgresControlPlane(), environ=env)
    if svc.status()["initialized"]:
        print("Există deja un administrator: nu modific nimic.")
        return 0
    out = svc.bootstrap(token, a.email, pw, "Administrator Test (FICTIV)", organization_name="Parohia Test (FICTIV)", cemetery_name="Cimitir Test FICTIV")
    print("OK: administrator + organizație + cimitir FICTIVE create:", {k: v for k, v in out.items() if k in ("organization_uid", "cemetery_uid")})
    return 0


if __name__ == "__main__":
    sys.exit(main())
