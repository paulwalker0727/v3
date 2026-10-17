#!/usr/bin/env python3
"""Recuperare administrativă Cloud FĂRĂ resetarea bazei: emite un token de unică folosință (15 min) pentru un administrator existent.

Rulează pe serverul Cloud (are acces la mediul containerului):
  docker exec -it <container> python /app/scripts/cloud_admin_recovery.py --email admin@exemplu.ro
Apoi, în https://<adresa-cloud>/admin/ → „Am pierdut accesul (recuperare)”: token + email + parolă nouă. Toate sesiunile vechi sunt revocate; datele rămân neschimbate.
Tokenul apare O SINGURĂ DATĂ pe terminal (în DB există doar sha256).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--minutes", type=int, default=15)
    a = ap.parse_args()
    import cimitir_cloud_admin as ad
    import cimitir_cloud_control_pg as ccp
    svc = ad.AdminService(ccp.PostgresControlPlane())
    token = svc.issue_recovery(a.email, ttl=min(max(a.minutes, 1), 60) * 60)
    if not token:
        print("Nu există un administrator cu acest email.", file=sys.stderr)
        return 1
    print(f"Token de recuperare (o singură folosință, expiră în {a.minutes} min):\n{token}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
