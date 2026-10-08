#!/usr/bin/env python
"""make_demo_db.py — creează o bază SQLite DEMO nouă, într-un folder de date NOU (implicit: director temporar).

- Nu atinge niciun DATA_DIR existent: refuză dacă folderul țintă conține deja cimitir.db.
- Utilizatorii demo (admin_demo / user_demo) au parole generate acum, afișate o singură dată; nimic nu se salvează în repo.
- JWT secret-ul se generează la runtime de aplicație (JWT_SECRET sau <data>/.jwt_secret) — nu se scrie în baza de date.

Folosire:
    python scripts/make_demo_db.py [--data-dir DIR] [--offline]
Apoi: CIMITIR_DATA_DIR=DIR uvicorn server:app   (din backend/)
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def create_demo_db(data_dir: str, offline: bool = True) -> dict:
    """Creează baza demo în data_dir (nou). Întoarce {'data_dir', 'db_path', 'users': {username: parola}}."""
    target = Path(data_dir).resolve()
    target.mkdir(parents=True, exist_ok=True)
    if (target / "cimitir.db").exists():
        raise SystemExit(f"REFUZAT: {target / 'cimitir.db'} există deja; alege un folder nou.")
    os.environ["CIMITIR_DATA_DIR"] = str(target)
    for p in (ROOT / "shared", ROOT / "backend" / "cimitir", ROOT / "backend"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    import cimitir_auth as auth
    import seed_demo

    if offline:
        seed_demo._download = lambda url, dest: False
    seed_demo.main()
    users = {"admin_demo": secrets.token_urlsafe(16), "user_demo": secrets.token_urlsafe(16)}
    for name, role in (("admin_demo", auth.ROLE_ADMIN), ("user_demo", auth.ROLE_USER)):
        auth.create_user(name, users[name], role)
    return {"data_dir": str(target), "db_path": str(target / "cimitir.db"), "users": users}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", help="folder NOU pentru baza demo (implicit: director temporar)")
    ap.add_argument("--offline", action="store_true", help="nu descarcă imagini (harta/fotografiile)")
    args = ap.parse_args()
    data_dir = args.data_dir or tempfile.mkdtemp(prefix="cimitir_demo_")
    info = create_demo_db(data_dir, offline=args.offline)
    print(f"Bază demo creată: {info['db_path']}")
    for name, pw in info["users"].items():
        print(f"  {name} / {pw}")
    print("Parolele sunt generate acum și NU se salvează nicăieri.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
