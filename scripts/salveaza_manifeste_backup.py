"""BUG-09 — pune la adăpost manifestele copiilor de rezervă (`manifest.json`) înainte de `git pull` / `git checkout` / merge.

Pe un depozit care are `shared_data-backups/*/manifest.json` URMĂRITE în Git (orice clonă dinainte de commit-ul 6a9774b), trecerea la un
commit care le-a scos din index le ȘTERGE de pe disc; fără manifest, copia dispare din MAIN (nu se listează, nu se verifică, nu se restaurează).

    python scripts/salveaza_manifeste_backup.py save     [--repo DEPOZIT] [--dest FOLDER]    # ÎNAINTE de pull / checkout / merge
    python scripts/salveaza_manifeste_backup.py restore  [--repo DEPOZIT] [--dest FOLDER]    # DUPĂ, dacă manifestele au dispărut
    python scripts/salveaza_manifeste_backup.py status   [--repo DEPOZIT] [--dest FOLDER]

- copiază DOAR fișierele `manifest.json` (mici) din folderele `*-backups` aflate direct în rădăcina depozitului;
- folderul implicit de salvare este `<părintele depozitului>/cimitir-manifeste-salvate` (în afara depozitului; un folder din interior este refuzat);
- nu șterge nimic și nu suprascrie niciodată un fișier existent (`restore` readuce doar manifestele care lipsesc, și doar dacă folderul copiei există încă);
- nu rulează comenzi Git și nu modifică istoricul.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import List, Tuple

DEFAULT_DEST_NAME = "cimitir-manifeste-salvate"


def _backup_roots(repo: Path) -> List[Path]:
    return sorted(p for p in repo.iterdir() if p.is_dir() and p.name.endswith("-backups")) if repo.is_dir() else []


def _manifests(root: Path) -> List[Path]:
    return sorted(root.glob("*/manifest.json"))


def default_dest(repo: Path) -> Path:
    return repo.resolve().parent / DEFAULT_DEST_NAME


def _check_dest(repo: Path, dest: Path) -> None:
    repo, dest = repo.resolve(), dest.resolve()
    if dest == repo or repo in dest.parents:
        raise SystemExit(f"Folderul de salvare trebuie să fie ÎN AFARA depozitului (altfel Git îl poate șterge): {dest}")


def save(repo: Path, dest: Path) -> Tuple[int, int]:
    """(copiate, deja_salvate). Nu suprascrie un fișier salvat cu conținut diferit: îl păstrează și raportează."""
    repo = Path(repo).resolve()
    _check_dest(repo, dest)
    copied = same = 0
    for root in _backup_roots(repo):
        for manifest in _manifests(root):
            target = Path(dest) / manifest.relative_to(repo)
            if target.is_file():
                if target.read_bytes() == manifest.read_bytes():
                    same += 1
                    continue
                target = target.with_name("manifest.%s.json" % manifest.stat().st_mtime_ns)      # variantă diferită: nu pierdem niciuna
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(manifest, target)
            copied += 1
    return copied, same


def restore(repo: Path, dest: Path) -> Tuple[int, int]:
    """(readuse, sărite). Readuce doar manifestele lipsă, doar pentru foldere de copii care mai există pe disc."""
    repo = Path(repo).resolve()
    _check_dest(repo, dest)
    restored = skipped = 0
    for saved in sorted(Path(dest).glob("*-backups/*/manifest.json")):
        target = repo / saved.relative_to(dest)
        if target.exists() or not target.parent.is_dir():
            skipped += 1
            continue
        shutil.copy2(saved, target)
        restored += 1
    return restored, skipped


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("save", "restore", "status"))
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]), help="rădăcina depozitului (implicit: acesta)")
    parser.add_argument("--dest", help=f"folderul de salvare (implicit: <depozit>/../{DEFAULT_DEST_NAME})")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    dest = Path(args.dest).resolve() if args.dest else default_dest(repo)
    if args.action == "save":
        copied, same = save(repo, dest)
        print(f"[manifeste] salvate: {copied}, deja salvate identic: {same} -> {dest}")
    elif args.action == "restore":
        restored, skipped = restore(repo, dest)
        print(f"[manifeste] readuse: {restored}, sărite (există deja / lipsește folderul copiei): {skipped}")
    else:
        on_disk = sum(len(_manifests(r)) for r in _backup_roots(repo))
        saved = len(list(dest.glob("*-backups/*/manifest.json"))) if dest.is_dir() else 0
        print(f"[manifeste] pe disc în depozit: {on_disk}; salvate în {dest}: {saved}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
