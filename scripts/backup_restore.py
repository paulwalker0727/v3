"""Backup și restaurare pentru folderul de date Cimitir (MAIN sau server).

    python scripts/backup_restore.py backup  "C:\\Cimitir\\data"  "D:\\backup-cimitir"
    python scripts/backup_restore.py restore "D:\\backup-cimitir\\cimitir-20260101-120000" "C:\\Cimitir\\data-restaurat"
    python scripts/backup_restore.py verify  "D:\\backup-cimitir\\cimitir-20260101-120000"

- baza se copiază cu API-ul de backup SQLite (consistent chiar dacă aplicația rulează, WAL);
- media se copiază integral; `manifest.json` conține SHA-256 pentru fiecare fișier;
- restaurarea verifică manifestul, refuză o destinație nevidă fără --force și păstrează o copie de siguranță.
Opriți aplicația înainte de restaurare.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))
from cimitir_backup import backup, restore, verify  # noqa: E402,F401


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["backup", "restore", "verify"])
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path, nargs="?")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    if args.action == "backup":
        print(f"[backup] creat: {backup(args.source, args.target)}")
    elif args.action == "verify":
        problems = verify(args.source)
        print("[verify] OK" if not problems else "[verify] PROBLEME: " + "; ".join(problems))
        return 1 if problems else 0
    else:
        print(f"[restore] restaurat în: {restore(args.source, args.target, args.force)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
