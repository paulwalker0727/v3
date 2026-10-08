"""Creează o copie de lucru a bazei Cimitir pentru teste. Originalul nu este modificat.

    python scripts/copy_db_for_testing.py "C:\\Cimitir\\data" "C:\\Cimitir\\data-test"

- sursa se deschide read-only (mode=ro) și se copiază cu API-ul de backup SQLite (sigur cu WAL);
- folderul media se copiază, iar căile din documents/photos sunt rescrise spre copie,
  astfel încât ștergerea unui document în test nu atinge fișierele originale;
- refuză să scrie peste o destinație existentă (fără --force) sau în folderul sursei.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path


def _rewrite_paths(conn: sqlite3.Connection, src_media: Path, dst_media: Path) -> dict:
    stats = {"rewritten": 0, "external": 0}
    src_prefix = str(src_media.resolve())
    for table in ("documents", "photos"):
        try:
            rows = conn.execute(f"SELECT id, file_path FROM {table}").fetchall()
        except sqlite3.OperationalError:
            continue
        for row_id, file_path in rows:
            if file_path and str(Path(file_path)).startswith(src_prefix):
                new_path = dst_media / Path(file_path).relative_to(src_media.resolve())
                conn.execute(f"UPDATE {table} SET file_path=? WHERE id=?", (str(new_path), row_id))
                stats["rewritten"] += 1
            elif file_path:
                stats["external"] += 1
    conn.commit()
    return stats


def copy_for_testing(source: Path, dest_dir: Path, force: bool = False) -> dict:
    source = Path(source).expanduser().resolve()
    src_db = source / "cimitir.db" if source.is_dir() else source
    src_dir = src_db.parent
    dest_dir = Path(dest_dir).expanduser().resolve()
    if not src_db.is_file():
        raise SystemExit(f"Nu există baza sursă: {src_db}")
    if dest_dir == src_dir:
        raise SystemExit("Destinația trebuie să fie alt folder decât sursa.")
    dest_db = dest_dir / "cimitir.db"
    if dest_db.exists() and not force:
        raise SystemExit(f"{dest_db} există deja. Folosește --force pentru a o înlocui.")
    dest_dir.mkdir(parents=True, exist_ok=True)
    for leftover in (dest_db, dest_dir / "cimitir.db-wal", dest_dir / "cimitir.db-shm"):
        if leftover.exists():
            leftover.unlink()

    src_conn = sqlite3.connect(f"file:{src_db.as_posix()}?mode=ro", uri=True)
    dst_conn = sqlite3.connect(dest_db)
    try:
        src_conn.backup(dst_conn)
    finally:
        src_conn.close()
    src_media = src_dir / "media"
    dst_media = dest_dir / "media"
    if src_media.is_dir():
        shutil.copytree(src_media, dst_media, dirs_exist_ok=True)
    stats = _rewrite_paths(dst_conn, src_media, dst_media)
    dst_conn.close()
    return {"source_db": str(src_db), "copy_db": str(dest_db), **stats}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="Folderul cu cimitir.db sau fișierul .db")
    parser.add_argument("dest_dir", type=Path, help="Folderul de test (nou)")
    parser.add_argument("--force", action="store_true", help="Înlocuiește copia existentă")
    args = parser.parse_args(argv)
    info = copy_for_testing(args.source, args.dest_dir, args.force)
    print(f"[copie] sursă (neatinsă): {info['source_db']}")
    print(f"[copie] copie de lucru:   {info['copy_db']}")
    print(f"[copie] căi media rescrise: {info['rewritten']}; externe (neatinse, protejate la ștergere): {info['external']}")
    print(f'[copie] apoi: python desktop/start_local.py --data-dir "{args.dest_dir}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
