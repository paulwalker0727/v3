#!/usr/bin/env python
"""lock_accounts.py — dezactivează conturile implicite și de test înainte ca URL-ul să devină public.

Ținte (implicit): `admin`, `user` și orice cont care începe cu `tst_`. Conturile din `--keep` (implicit: Alin) rămân active.
Nu șterge nimic: doar `active=0` (reversibil din /admin/utilizatori). Refuză să lase serverul fără niciun administrator activ.
Afișează doar nume de cont, niciodată parole sau hash-uri.

    python scripts/lock_accounts.py                  # dry-run: arată ce s-ar dezactiva
    python scripts/lock_accounts.py --apply          # dezactivează efectiv
    python scripts/lock_accounts.py --keep Alin --keep alt_cont --apply

Rulează cu aceleași variabile de mediu ca serverul (CIMITIR_DB_ENGINE=postgres, DATABASE_URL=...) sau cu backend/.env.
Cod de ieșire: 0 = nu mai există conturi-țintă active și niciun cont păstrat nu are parolă implicită;
1 = au rămas riscuri (sau dry-run cu ținte active); 2 = refuzat (ar rămâne fără admin activ) / eroare.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "shared", ROOT / "backend" / "cimitir", ROOT / "backend"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

DEFAULT_TARGETS = ("admin", "user")
TEST_PREFIX = "tst_"


def is_target(username: str, keep: set) -> bool:
    name = (username or "").strip().lower()
    if name in keep:
        return False
    return name in DEFAULT_TARGETS or name.startswith(TEST_PREFIX)


def default_password_accounts(auth, users) -> list:
    """Conturile a căror parolă este una dintre parolele implicite cunoscute (verificare locală, fără a afișa parola)."""
    found = []
    conn = auth.db.get_connection()
    try:
        rows = {r["username"]: r["password_hash"] for r in conn.execute("SELECT username, password_hash FROM users").fetchall()}
    finally:
        conn.close()
    for u in users:
        pw_hash = rows.get(u["username"], "")
        if any(auth.verify_password(pw, pw_hash) for pw in auth.DEFAULT_PASSWORDS):
            found.append(u["username"])
    return found


def plan(users, keep: set) -> dict:
    targets = [u for u in users if is_target(u["username"], keep)]
    to_disable = [u for u in targets if u["active"]]
    remaining_admins = [u for u in users if u["role"] == "admin" and u["active"] and u not in to_disable]
    return {"targets": targets, "to_disable": to_disable, "remaining_admins": remaining_admins}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="dezactivează efectiv (fără el: dry-run)")
    ap.add_argument("--keep", action="append", default=None, metavar="USER", help="cont care rămâne activ (repetabil; implicit: Alin)")
    args = ap.parse_args(argv)
    keep = {k.strip().lower() for k in (args.keep if args.keep is not None else ["Alin"]) if k.strip()}

    import cimitir_auth as auth
    import cimitir_config

    cimitir_config.load_env()
    print(f"Motor: {cimitir_config.db_engine()} · mod: {cimitir_config.mode()}")
    if cimitir_config.db_engine() != "postgres":
        print("ATENȚIE: motorul nu este postgres; scriptul lucrează pe baza locală SQLite, nu pe Supabase.")
    if os.environ.get(auth.ENV_ALLOW_DEFAULT, "").strip() == "1":
        print(f"ATENȚIE: {auth.ENV_ALLOW_DEFAULT}=1 este setat; elimină-l din mediul serverului (permite recrearea conturilor implicite).")

    auth.db.init_db()
    users = auth.list_users()
    p = plan(users, keep)
    if args.apply and p["to_disable"] and not p["remaining_admins"]:
        print("REFUZAT: după dezactivare nu ar mai rămâne niciun administrator activ. Creează/păstrează un admin real (--keep).")
        return 2

    for u in users:
        mark = "ȚINTĂ" if is_target(u["username"], keep) else ("PĂSTRAT" if u["username"].lower() in keep else "—")
        print(f"  {u['username']:<22} rol={u['role']:<9} activ={'da' if u['active'] else 'nu':<3} {mark}")

    if not p["targets"]:
        print("Nu există conturi implicite sau tst_*.")
    changed = 0
    for u in p["to_disable"]:
        if args.apply:
            if auth.update_user(u["id"], active=False):
                changed += 1
                print(f"  dezactivat: {u['username']}")
        else:
            print(f"  [dry-run] s-ar dezactiva: {u['username']}")

    users_after = auth.list_users() if args.apply else users
    still_active = [u for u in users_after if is_target(u["username"], keep) and u["active"]]
    weak = default_password_accounts(auth, [u for u in users_after if u["active"]])
    for name in weak:
        print(f"RISC: contul activ „{name}” are încă o parolă implicită; schimb-o din /admin/schimba-parola.")
    active_admins = [u for u in users_after if u["role"] == "admin" and u["active"]]
    print(f"Administratori activi: {len(active_admins)} · conturi-țintă încă active: {len(still_active)}"
          + ("" if args.apply else " (dry-run: nimic modificat; folosește --apply)"))
    return 0 if not still_active and not weak else 1


if __name__ == "__main__":
    raise SystemExit(main())
