#!/usr/bin/env python
"""Unealtă de operator pentru autorizarea de inițializare a administratorului Cloud (fără BOOTSTRAP_TOKEN).
  keygen --out owner.key          creează cheia privată (0600) și afișează cheia PUBLICĂ + amprenta (pentru CIMITIR_INSTANCE_ADMIN_PUBKEY)
  grant --key owner.key --instance-id ID [--ttl 300]   emite un grant scurt (se lipește în MAIN sau se trimite direct la /api/v1/admin/bootstrap)
Cheia privată NU se afișează și nu se pune în Git/loguri. Normal, MAIN face aceste operații singur (fila „Inițializare administrator Cloud”)."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))
import cimitir_instance_grant as ig  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("--out", required=True)
    g = sub.add_parser("grant")
    g.add_argument("--key", required=True)
    g.add_argument("--instance-id", required=True)
    g.add_argument("--ttl", type=int, default=300)
    a = ap.parse_args(argv)
    if a.cmd == "keygen":
        out = Path(a.out)
        if out.exists():
            print("Fișierul există deja; nu se suprascrie.", file=sys.stderr)
            return 2
        kp = ig.generate_keypair()
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(kp["private_key"])
        print(f"CIMITIR_INSTANCE_ADMIN_PUBKEY={kp['public_key']}\nAmprentă: {ig.fingerprint(kp['public_key'])}")
        return 0
    print(ig.issue_grant(Path(a.key).read_text().strip(), a.instance_id, a.ttl))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
