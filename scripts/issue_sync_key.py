"""Emite (sau rotește) cheia de sincronizare a unui cimitir pe serverul online.

    $env:CIMITIR_MODE = "online"; $env:CIMITIR_DATA_DIR = "D:\\cimitir-server"
    python scripts/issue_sync_key.py --source cimitir

Cheia se afișează o singură dată; în DB se păstrează doar hash-ul. Cheia veche nu mai funcționează.
Se introduce în MAIN → ☁ Sincronizare cloud (Adresa serverului: https://...).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default="cimitir", help="Id-ul cimitirului (implicit: cimitir)")
    args = parser.parse_args()
    import server  # noqa: F401  (pregătește căile și configurarea)
    import store
    import sync_api
    source = store.get_source(args.source)
    if not source:
        print(f"Cimitir necunoscut: {args.source}. Disponibile: {[s['id'] for s in store.list_sources()]}")
        return 2
    store.ensure_initialized(source)
    print(sync_api.issue_sync_key(args.source))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
