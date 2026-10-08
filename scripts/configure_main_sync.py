"""Configurează sincronizarea MAIN -> server online (adresa HTTPS + cheia), fără interfața desktop.

    python scripts/configure_main_sync.py --data-dir C:\\Cimitir\\data --url https://cimitir.exemplu.ro --key cimitir.<secret> --auto
    python scripts/configure_main_sync.py --data-dir C:\\Cimitir\\data --test      # doar verifică conexiunea

Adresele http:// sunt refuzate de MAIN (în afară de localhost). Cheia se obține de pe server cu scripts/issue_sync_key.py.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "desktop"), str(ROOT / "shared")]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", help="Folderul bazei MAIN (implicit: CIMITIR_DATA_DIR)")
    parser.add_argument("--url")
    parser.add_argument("--key")
    parser.add_argument("--auto", action="store_true", help="Sincronizare automată în fundal")
    parser.add_argument("--test", action="store_true", help="Verifică conexiunea (ping) și iese")
    args = parser.parse_args()
    if args.data_dir:
        os.environ["CIMITIR_DATA_DIR"] = args.data_dir
    import cloud_sync
    import database as db
    db.init_db()
    cloud_sync.ensure_tables()
    if args.url or args.key:
        cfg = cloud_sync.get_config()
        cloud_sync.save_config(args.url or cfg["url"], args.key or cfg["key"], args.auto or cfg["auto"])
    cfg = cloud_sync.get_config()
    if not cloud_sync.url_is_secure(cfg["url"]):
        print("EROARE: adresa trebuie să fie https:// (sau localhost).")
        return 2
    print(f"Bază MAIN: {db.DB_PATH}\nServer: {cfg['url']}\nId client MAIN: {cloud_sync.client_id()}")
    if args.test:
        ok, message = cloud_sync.ping()
        print(message)
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
