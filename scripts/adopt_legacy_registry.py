#!/usr/bin/env python
"""adopt_legacy_registry.py — adoptă instalația veche (DATA_DIR/cimitir.db + media) într-un registru local nou.

DRY-RUN implicit: arată ce s-ar muta și nu scrie nimic.
    python scripts/adopt_legacy_registry.py --data-dir C:\\Cimitir\\data --cemetery-uid <UID>
    python scripts/adopt_legacy_registry.py --data-dir ... --org-type parohie --org-name "Parohia X" --cemetery-name "Cimitir X"

Mutarea reală cere ambele opțiuni: --apply --confirm. Originalele se păstrează în <data>/legacy_adopted_<uid>/.
Cod de ieșire: 0 OK | 1 eroare | 2 nimic de adoptat / confirmare lipsă.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))

import cimitir_registry as reg  # noqa: E402
import cimitir_registry_adopt as adopt  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--cemetery-uid", help="cimitir existent în catalog (UUID)")
    ap.add_argument("--org-type", default="altul", choices=sorted(reg.ORG_TYPES))
    ap.add_argument("--org-name", help="organizație nouă (creată doar cu --apply)")
    ap.add_argument("--cemetery-name", help="cimitir nou (creat doar cu --apply)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--confirm", action="store_true", help="confirmarea explicită a mutării")
    a = ap.parse_args(argv)

    legacy = adopt.detect_legacy(a.data_dir)
    if legacy is None:
        print("Nu există nicio instalație veche (cimitir.db) de adoptat.")
        return 2
    print("Am găsit registru existent")
    print(f"{legacy['records']} înregistrări")
    print(f"{legacy['media_files']} fișiere media")
    if a.cemetery_uid:
        target = reg.valid_uid(a.cemetery_uid)
    elif a.org_name and a.cemetery_name:
        target = "<UID nou, generat la --apply>"
    else:
        print("Indică --cemetery-uid sau --org-name + --cemetery-name.")
        return 1
    print(f"Va fi mutat în {target}")
    if not a.apply:
        print("DRY-RUN: nu s-a modificat nimic. Pentru mutare: --apply --confirm")
        return 0
    if not a.confirm:
        print("Lipsește --confirm: nu mut nimic.")
        return 2
    try:
        cat = reg.RegistryCatalog(a.data_dir)
        uid = target if a.cemetery_uid else cat.create_cemetery(cat.create_organization(a.org_type, a.org_name), a.cemetery_name)
        out = adopt.adopt_legacy(uid, confirm=True, data_dir=a.data_dir, catalog=cat)
    except reg.RegistryError as exc:
        print(f"EȘUAT: {exc}")
        return 1
    print(f"Adoptat în {out['cemetery_uid']} ({out['records']} înregistrări, {out['media_files']} media). Originale: {out['archive']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
