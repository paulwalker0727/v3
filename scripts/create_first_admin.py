#!/usr/bin/env python
"""create_first_admin.py — wizard local pentru PRIMUL administrator (baza PostgreSQL/SQLite nouă, fără niciun admin).

Functioneaza DOAR cand tabela users nu contine niciun cont cu rol admin; altfel refuza (cod 1) si nu modifica nimic.
Numele si parola se cer de la tastatura (parola nu apare pe ecran, nu se salveaza in fisiere si nu se afiseaza niciodata).
Contul creat trebuie sa-si schimbe parola la primul login. Nu exista admin/admin123 in modul online/PostgreSQL.

    python scripts/create_first_admin.py
    python scripts/create_first_admin.py --username director --password-env MY_TEMP_PASSWORD   (automatizari)

Ruleaza-l pe calculatorul/serverul care are backend/.env (citeste DATABASE_URL de acolo). Aplica schema (migratiile) daca baza e goala.
Cod de iesire: 0 = creat; 1 = exista deja un admin; 2 = date invalide / baza inaccesibila.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "shared", ROOT / "backend" / "cimitir", ROOT / "backend"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import cimitir_config  # noqa: E402

cimitir_config.load_env()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--username", help="Numele administratorului (implicit: se cere)")
    ap.add_argument("--password-env", metavar="VAR", help="Citeste parola din variabila de mediu VAR (automatizari)")
    args = ap.parse_args(argv)

    import cimitir_auth as auth
    try:
        # fara conturi implicite in modul online/PostgreSQL; aplica migratiile pe o baza goala
        os.environ.pop(auth.ENV_BOOT_PASSWORD, None)  # wizardul nu foloseste bootstrap-ul din mediu
        auth.init_auth()
        exists = auth.admin_exists()
    except Exception as exc:  # fara detalii: pot contine gazda/URL
        print(f"EROARE: baza de date nu este accesibila ({type(exc).__name__}). Ruleaza CHECK_CLOUD_CONFIG.bat.")
        return 2
    if exists:
        print("Exista deja un administrator. Bootstrap-ul este dezactivat; nu am modificat nimic.")
        print("(Pentru o parola uitata foloseste scripts/reset_admin_password.py.)")
        return 1

    username = (args.username or input("Nume administrator nou: ")).strip()
    if args.password_env:
        password = os.environ.get(args.password_env, "")
    else:
        password = getpass.getpass("Parola (minim %d caractere): " % auth.MIN_ADMIN_PASSWORD_LEN)
        if password != getpass.getpass("Confirma parola: "):
            print("Parolele nu coincid.")
            return 2
    ok, message = auth.create_first_admin(username, password)
    print(("OK: " if ok else "EROARE: ") + message)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
