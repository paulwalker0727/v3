#!/usr/bin/env python3
"""Generează tokenul de inițializare a primului administrator Cloud (criptografic, 256 biți) + expirarea, ca linii pentru fișierul de mediu al containerului.

  python scripts/generate_bootstrap_token.py [--hours 24] [--env-file deploy/cloud/.env.cloud]

Tokenul se tipărește O SINGURĂ DATĂ pe terminal și (opțional) se scrie în fișierul de mediu (permisiuni 600). Nu intră în Git și nu e logat de server.
După crearea administratorului, serverul îl ignoră definitiv (starea e în PostgreSQL); scoate liniile din fișierul de mediu după inițializare.
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0, help="valabilitate (implicit 24 h, max 168)")
    ap.add_argument("--env-file", help="fișier de mediu Docker unde se scriu/înlocuiesc BOOTSTRAP_TOKEN și BOOTSTRAP_TOKEN_EXPIRES_AT")
    a = ap.parse_args()
    hours = min(max(a.hours, 0.25), 168.0)
    token, expires = secrets.token_urlsafe(40), int(time.time() + hours * 3600)
    lines = {"BOOTSTRAP_TOKEN": token, "BOOTSTRAP_TOKEN_EXPIRES_AT": str(expires)}
    if a.env_file:
        p = Path(a.env_file)
        keep = [ln for ln in (p.read_text(encoding="utf-8").splitlines() if p.exists() else []) if ln.split("=", 1)[0] not in lines]
        p.write_text("\n".join(keep + [f"{k}={v}" for k, v in lines.items()]) + "\n", encoding="utf-8")
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
        print(f"Scris în {p} (repornește containerul Cloud). Tokenul pentru formularul /admin/:")
    else:
        print("Adaugă în mediul containerului Cloud:")
        for k, v in lines.items():
            print(f"{k}={v}")
    print(f"\nToken de inițializare (afișat o singură dată): {token}\nExpiră în {hours:g} h. Deschide https://<adresa-cloud>/admin/ și completează formularul.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
