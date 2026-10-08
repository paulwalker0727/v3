#!/usr/bin/env python
"""rotate_supabase_secrets.py — rotire sigura a SUPABASE_SECRET_KEY (sau a parolei bazei de date) in backend/.env.

Flux (dupa ce ai generat valoarea noua in Supabase):
    python scripts/rotate_supabase_secrets.py                      # cheia secreta (implicit)
    python scripts/rotate_supabase_secrets.py --what db-password   # parola bazei, in DATABASE_URL

- valoarea noua se cere de la tastatura (nu apare pe ecran) sau din variabila indicata cu --value-env VAR;
- se TESTEAZA inainte de salvare (Storage: autentificare + bucket; PostgreSQL: conectare); daca testul pica, backend/.env ramane NESCHIMBAT;
- se scrie DOAR cheia respectiva in backend/.env (restul fisierului ramane); nu se afiseaza niciodata valoarea veche sau noua;
- la final se afiseaza doar stari. Apoi: RESTART_BACKEND.bat si stergi cheia veche din Supabase.
Cod de iesire: 0 = rotit; 1 = refuzat (test esuat / date invalide); 2 = eroare.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path
from urllib.parse import quote, urlparse

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "shared", ROOT / "backend" / "cimitir", ROOT / "backend"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import cimitir_config  # noqa: E402

cimitir_config.load_env()
import cloud_config as cc  # noqa: E402


def _ask(label: str) -> str:
    first = getpass.getpass(f"{label}: ")
    if first != getpass.getpass("Confirma: "):
        raise SystemExit("Valorile nu coincid.")
    return first.strip()


def _with_new_password(url: str, new_password: str) -> str:
    u = urlparse(url)
    host = u.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    auth = quote(u.username or "", safe=".") + ":" + quote(new_password, safe="")
    netloc = f"{auth}@{host}" + (f":{u.port}" if u.port else "")
    return u._replace(netloc=netloc).geturl()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--what", choices=("secret-key", "db-password"), default="secret-key")
    ap.add_argument("--value-env", metavar="VAR", help="citeste valoarea noua din variabila de mediu VAR (automatizari)")
    args = ap.parse_args(argv)

    if cc.is_readonly()[0]:
        print("backend/.env nu poate fi scris aici (server administrat extern): schimba secretul direct pe server.")
        return 1
    new = os.environ.get(args.value_env, "").strip() if args.value_env else _ask("Valoarea NOUA (nu se afiseaza)")
    if not new:
        print("Valoare goala; nu am modificat nimic.")
        return 1

    if args.what == "secret-key":
        clean, errors, _ = cc.validate({"SUPABASE_SECRET_KEY": new})
        if errors:
            print("REFUZAT:", *errors)
            return 1
        probe = cc.test_storage({"SUPABASE_SECRET_KEY": new})
        if not (probe["ok"] or probe.get("auth_ok")):
            print("REFUZAT: noua cheie nu autentifica la Supabase Storage. backend/.env a ramas NESCHIMBAT.")
            for _, text in probe["lines"]:
                print("  -", text)
            return 1
        cc.write_values(clean)
        print("SUPABASE_SECRET_KEY: ACTUALIZAT")
        print("Storage: ONLINE" if probe["ok"] else "Storage: autentificare OK, dar bucketul lipseste (ruleaza CHECK_CLOUD_CONFIG.bat --create-bucket)")
    else:
        old = os.environ.get("DATABASE_URL", "").strip()
        if not old:
            print("DATABASE_URL nu este configurat.")
            return 1
        url = _with_new_password(old, new)
        clean, errors, _ = cc.validate({"DATABASE_URL": url})
        if errors:
            print("REFUZAT:", *errors)
            return 1
        probe = cc.test_postgres({"DATABASE_URL": url})
        if not probe["ok"]:
            print("REFUZAT: noua parola nu se conecteaza. backend/.env a ramas NESCHIMBAT.")
            for _, text in probe["lines"]:
                print("  -", text)
            return 1
        cc.write_values(clean)
        print("DATABASE_URL (parola): ACTUALIZAT")
        print("PostgreSQL: ONLINE")
    print("Urmatorii pasi: RESTART_BACKEND.bat; apoi sterge cheia/parola VECHE din Supabase; la final CHECK_CLOUD_CONFIG.bat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
