"""Migrare EXPLICITĂ a copiilor Cimitir în afara surselor Git, fără a muta/șterge originalele.

Exemple (pe o clonă cu date fictive întâi):
  python scripts/migrate_backups_out_of_git.py --data-dir ./shared_data
  python scripts/migrate_backups_out_of_git.py --data-dir ./shared_data --apply
  python scripts/migrate_backups_out_of_git.py --data-dir ./shared_data --apply --adopt-missing

Implicit doar afișează planul. `--adopt-missing` permite refacerea unui manifest
lipsă numai în COPIA EXTERNĂ, marcată `adopted: true`. Nu dovedește integritatea
istorică. Verifică manual folderul sursă și fă mai întâi o copie independentă.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))
import cimitir_backup as cb  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path, help="folderul bazei Cimitir (de exemplu ./shared_data)")
    parser.add_argument("--source", type=Path, help="folderul backupurilor vechi (implicit <data-dir>-backups)")
    parser.add_argument("--dest", type=Path, help="destinație externă (implicit destinația automată sigură)")
    parser.add_argument("--apply", action="store_true", help="execută migrarea; fără acest argument este doar DRY RUN")
    parser.add_argument("--adopt-missing", action="store_true", help="adoptă explicit copii cu manifestul pierdut")
    args = parser.parse_args(argv)
    data = args.data_dir.resolve()
    src = args.source.resolve() if args.source else cb.legacy_backup_dir(data)
    dest = cb.backup_destination(data, (lambda name: str(args.dest) if name == "backup_dir" and args.dest else None))
    print(f"Sursă: {src}\nDestinație: {dest}\nSursa va rămâne neatinsă.")
    if cb.is_unsafe_backup_destination(dest):
        parser.error("destinația este într-un depozit Git")
    if not args.apply:
        candidates = [p for p in sorted(src.glob("cimitir-????????-??????")) if p.is_dir()] if src.exists() else []
        print(f"[DRY RUN] {len(candidates)} foldere candidate; folosește --apply pentru copiere.")
        return 0
    try:
        res = cb.migrate_legacy_backups(data, lambda name: str(args.dest) if name == "backup_dir" and args.dest else None,
                                        adopt_missing=args.adopt_missing, source=src)
    except ValueError as exc:
        print(f"[EROARE] {exc}")
        return 1
    print(f"Copiate: {res['copied']}; existente: {res['skipped']}; adoptate: {res['adopted']}; erori: {len(res['errors'])}")
    for error in res["errors"]:
        print("[ATENȚIE] " + error)
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
