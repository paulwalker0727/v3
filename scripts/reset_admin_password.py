"""Resetează parola unui cont de personal (MAIN și administrarea web folosesc aceeași bază) sau listează conturile.

    python scripts/reset_admin_password.py --data-dir "C:\\Cimitir\\data" --list
    python scripts/reset_admin_password.py --data-dir "C:\\Cimitir\\data" --username admin
    python scripts/reset_admin_password.py --data-dir "C:\\Cimitir\\data" --username admin --create-admin

Parola nouă se cere de la tastatură (nu apare pe ecran și nu se afișează niciodată).
Scriptul deblochează contul după încercări greșite și îl reactivează. Oprește MAIN și portalul înainte.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "shared")]


def _ask_password() -> str:
    first = getpass.getpass("Parolă nouă: ")
    if first != getpass.getpass("Confirmă parola nouă: "):
        raise SystemExit("Parolele nu coincid.")
    return first


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", help="Folderul cu cimitir.db (implicit: CIMITIR_DATA_DIR / .env)")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--list", action="store_true", help="Listează conturile (fără parole) și iese")
    parser.add_argument("--create-admin", action="store_true", help="Creează contul dacă nu există (rol admin)")
    parser.add_argument("--password-env", metavar="VAR", help="Citește parola din variabila de mediu VAR (automatizări)")
    args = parser.parse_args(argv)
    if args.data_dir:
        os.environ["CIMITIR_DATA_DIR"] = args.data_dir
    import cimitir_auth as auth
    import cimitir_config

    auth.init_auth()
    print(f"Bază de date: {cimitir_config.db_path()}")
    users = auth.list_users()
    if args.list:
        for u in users:
            print(f"  {u['username']:<20} rol={u['role']:<9} activ={'da' if u.get('active', 1) else 'nu'}")
        return 0

    password = os.environ.get(args.password_env, "") if args.password_env else _ask_password()
    error = auth.validate_new_password(password)
    if error:
        print(f"EROARE: {error}")
        return 2
    target = next((u for u in users if u["username"] == args.username), None)
    if target is None:
        if not args.create_admin:
            print(f"Contul „{args.username}” nu există. Folosește --list sau --create-admin.")
            return 3
        auth.create_user(args.username, password, auth.ROLE_ADMIN)
        print(f"Cont creat: {args.username} (admin).")
        return 0
    auth.change_password(target["id"], password)
    auth.update_user(target["id"], active=True)
    auth.clear_failures(args.username)
    print(f"Parola contului „{args.username}” a fost schimbată; contul este activ și deblocat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
