#!/usr/bin/env python
"""check_cloud_config.py — verifică configurarea cloud (Supabase) fără să afișeze vreo valoare secretă.

Citește backend/.env (sau CIMITIR_ENV_FILE) și variabilele de mediu. Afișează doar stări: CONFIGURAT / NECONFIGURAT, ONLINE / OFFLINE.
Nu afișează niciodată parola, connection string-ul, gazda, cheia secretă sau URL-ul Supabase.
Opțiune: --create-bucket creează bucketul PRIVAT dacă lipsește (singura scriere; nu modifică un bucket existent).
Cod de ieșire: 0 = totul ONLINE; 1 = ceva lipsește sau e OFFLINE.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "shared", ROOT / "backend" / "cimitir", ROOT / "backend"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import cimitir_config  # noqa: E402

cimitir_config.load_env()
import system_admin  # noqa: E402


def out(text: str) -> None:
    print(text)  # doar texte fixe + numele clasei de excepție; valorile nu ajung niciodată aici (redact ar masca și „SECRET_KEY: CONFIGURAT”)


def has(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())


def check_postgres(warnings: list) -> bool:
    url = os.environ.get("DATABASE_URL", "")
    cls = system_admin.classify_database("postgres", url)
    if cimitir_config.db_engine() != "postgres":
        warnings.append("CIMITIR_DB_ENGINE trebuie să fie postgres (acum aplicația nu folosește PostgreSQL).")
    if os.environ.get("CIMITIR_MODE", "").strip().lower() != "online":
        warnings.append("CIMITIR_MODE trebuie să fie online.")
    if not has("DATABASE_URL"):
        out("PostgreSQL: NECONFIGURAT")
        return False
    warnings += [text for kind, text in cls["checks"] if kind != "ok" and "nu dovedește" not in text]
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=10, autocommit=True) as conn:
            conn.execute("SELECT 1").fetchone()
        out("PostgreSQL: ONLINE")
        return True
    except Exception as exc:
        out(f"PostgreSQL: OFFLINE ({type(exc).__name__})")
        return False


def check_storage(create_bucket: bool, warnings: list) -> bool:
    import cimitir_storage as cs
    if cs.driver_name() != "supabase":
        warnings.append("CIMITIR_STORAGE_DRIVER trebuie să fie supabase.")
    if not (has("SUPABASE_URL") and (has("SUPABASE_SECRET_KEY") or has("SUPABASE_SERVICE_ROLE_KEY")) and has("SUPABASE_BUCKET")):
        out("Storage: NECONFIGURAT")
        return False
    try:
        store = cs.SupabaseStorage(os.environ["SUPABASE_URL"], os.environ.get("SUPABASE_SECRET_KEY") or os.environ["SUPABASE_SERVICE_ROLE_KEY"],
                                   os.environ["SUPABASE_BUCKET"], os.environ.get("SUPABASE_PREFIX", ""))
        if not store.auth_ok():
            out("Storage: OFFLINE (cheie sau URL respinse: autentificare eșuată)")
            return False
        info = store.bucket_info()
        if info is None and create_bucket:
            out(f"Bucket: {store.ensure_bucket().upper()} (privat)")
            info = store.bucket_info()
        if info is None:
            out("Storage: OFFLINE (bucket inexistent; rulează cu --create-bucket)")
            return False
        if info.get("public"):
            warnings.append("Bucketul este PUBLIC; documentele trebuie păstrate într-un bucket PRIVAT.")
        out("Storage: ONLINE")
        out("Bucket: " + ("PUBLIC (nepermis)" if info.get("public") else "PRIVAT"))
        return not info.get("public")
    except Exception as exc:
        code = re.search(r"Supabase (\d{3})", str(exc))
        out(f"Storage: OFFLINE ({type(exc).__name__}{', HTTP ' + code.group(1) if code else ''})")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Verifică configurarea cloud Supabase (fără secrete în ieșire).")
    ap.add_argument("--create-bucket", action="store_true", help="creează bucketul privat dacă lipsește")
    args = ap.parse_args()
    warnings: list = []
    out("DATABASE_URL: " + ("CONFIGURAT" if has("DATABASE_URL") else "NECONFIGURAT"))
    pg_ok = check_postgres(warnings)
    out("SUPABASE_URL: " + ("CONFIGURAT" if has("SUPABASE_URL") else "NECONFIGURAT"))
    out("SUPABASE_SECRET_KEY: " + ("CONFIGURAT" if has("SUPABASE_SECRET_KEY") or has("SUPABASE_SERVICE_ROLE_KEY") else "NECONFIGURAT"))
    out("SUPABASE_BUCKET: " + ("CONFIGURAT" if has("SUPABASE_BUCKET") else "NECONFIGURAT"))
    st_ok = check_storage(args.create_bucket, warnings)
    for text in dict.fromkeys(warnings):
        out("ATENȚIE: " + text)
    return 0 if pg_ok and st_ok else 1


if __name__ == "__main__":
    sys.exit(main())
