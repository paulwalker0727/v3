"""
media_service.py — Etapa 7 (server, PostgreSQL): documente/fotografii în storage online.

  * octeții merg în storage (`cimitir_storage`: local | s3 | supabase) — niciodată în PostgreSQL;
  * în PostgreSQL rămâne rândul din `media_objects`: id, entity_uid, filename, storage_path, mime, size, sha256,
    uploaded_by, uploaded_at (+ `change_seq`, doar pentru sincronizare incrementală);
  * fișierele locale ale serverului (`DATA_DIR/media`) NU se șterg după încărcare; la nevoie, un fișier lipsă local se
    readuce din storage într-un cache (`DATA_DIR/media_cache`), verificat cu sha256.

`register_file` este blocul pe care un viitor script de migrare a fișierelor vechi se poate sprijini (nu există încă).
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import cimitir_storage as storage_mod

DATA_DIR = os.environ.get("CIMITIR_DATA_DIR") or ""
MAX_BYTES = int(os.environ.get("CIMITIR_MEDIA_MAX_BYTES", str(40 * 1024 * 1024)))
_SHA = re.compile(r"^[0-9a-f]{64}$")


def _db():
    import postgres_store
    return postgres_store._db()


def enabled() -> bool:
    import cimitir_config
    return cimitir_config.db_engine() == "postgres"


def cache_dir() -> str:
    base = os.environ.get("CIMITIR_DATA_DIR") or DATA_DIR or "shared_data"
    path = os.path.join(base, "media_cache")
    os.makedirs(path, exist_ok=True)
    return path


def _row(r) -> Dict[str, Any]:
    d = dict(r)
    if isinstance(d.get("uploaded_at"), datetime):
        d["uploaded_at"] = d["uploaded_at"].isoformat()
    return d


# ------------------------------------------------------------------ entity uid
def entity_uid_for(entity_type: str, entity_id: Any) -> str:
    """uid-ul entității (cel din Etapa 6) pentru un (entity_type, entity_id) al serverului. Fără uid propriu:
    cerere -> `cerere:<cod_urmarire>`, aviz -> `aviz:<uid loc>:<nr_aviz>`. Dacă nu se găsește: `<tip>:<id>`."""
    ref = str(entity_id)
    tables = {"inhumat": "inhumati", "contract": "contracte", "concesionar": "concesionari", "plata": "plati"}
    try:
        with _db() as conn:
            if entity_type == "loc":
                r = conn.execute("SELECT uid FROM locuri_de_veci WHERE cod_loc = ? AND cemetery_uid = app_default_cemetery_uid()", (ref,)).fetchone()
                return r["uid"] if r and r["uid"] else f"loc:{ref}"
            if entity_type in tables:
                r = conn.execute(f"SELECT uid FROM {tables[entity_type]} WHERE id = ? AND cemetery_uid = app_default_cemetery_uid()", (int(ref),)).fetchone()
                return r["uid"] if r and r["uid"] else f"{entity_type}:{ref}"
            if entity_type == "cerere":
                r = conn.execute("SELECT cod_urmarire FROM cereri_concesionare WHERE id = ? AND cemetery_uid = app_default_cemetery_uid()", (int(ref),)).fetchone()
                return f"cerere:{r['cod_urmarire']}" if r and r["cod_urmarire"] else f"cerere:{ref}"
            if entity_type == "aviz":
                r = conn.execute("SELECT a.nr_aviz, l.uid FROM amenajari_avize a LEFT JOIN locuri_de_veci l ON l.cod_loc = a.cod_loc "
                                 "AND l.cemetery_uid = a.cemetery_uid WHERE a.id = ? AND a.cemetery_uid = app_default_cemetery_uid()", (int(ref),)).fetchone()
                return f"aviz:{r['uid'] or ''}:{r['nr_aviz']}" if r else f"aviz:{ref}"
    except Exception:
        pass
    return f"{entity_type}:{ref}"


# ------------------------------------------------------------------ înregistrare
def insert_metadata(media_id: str, entity_uid: str, filename: str, storage_path: str, mime: str, size: int, sha256: str,
                    uploaded_by: str) -> Dict[str, Any]:
    with _db() as conn:
        conn.execute(
            "INSERT INTO media_objects (id, entity_uid, filename, storage_path, mime, size, sha256, uploaded_by) "
            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (id) DO NOTHING",
            (media_id, entity_uid, filename, storage_path, mime, int(size), sha256, uploaded_by))
        return _row(conn.execute("SELECT * FROM media_objects WHERE id = ?", (media_id,)).fetchone())


def get_object(media_id: str) -> Optional[Dict[str, Any]]:
    with _db() as conn:
        r = conn.execute("SELECT * FROM media_objects WHERE id = ?", (media_id,)).fetchone()
        return _row(r) if r else None


def register_file(table: str, entity_type: str, doc_type: str, entity_uid: str, local_path: str, filename: str = "",
                  uploaded_by: str = "server", media_id: Optional[str] = None) -> Dict[str, Any]:
    """Pune un fișier local în storage + metadate în PostgreSQL. Idempotent pe `media_id`. Nu șterge fișierul local."""
    media_id = media_id or str(uuid.uuid4())
    filename = storage_mod.safe_name(filename or os.path.basename(local_path))
    size, sha = os.path.getsize(local_path), storage_mod.sha256_file(local_path)
    if size > MAX_BYTES:
        raise ValueError(f"fișier prea mare ({size} > {MAX_BYTES} octeți)")
    existing = get_object(media_id)
    if existing:
        if existing["sha256"] != sha:
            raise ValueError("media_id există deja cu alt conținut (sha256 diferit)")
        return existing
    key = storage_mod.build_key(table, entity_type, doc_type, entity_uid, media_id, filename)
    mime = storage_mod.guess_mime(filename)
    storage_mod.get_storage().put_file(key, local_path, mime)
    return insert_metadata(media_id, entity_uid, filename, key, mime, size, sha, uploaded_by)


def delete_object(media_id: str) -> bool:
    """Șterge metadatele și obiectul din storage (fișierele locale rămân neatinse)."""
    obj = get_object(media_id)
    if not obj:
        return False
    try:
        storage_mod.get_storage().delete(obj["storage_path"])
    except storage_mod.StorageError as exc:
        print(f"[media] ștergere storage {media_id}: {exc}")
    with _db() as conn:
        conn.execute("DELETE FROM media_objects WHERE id = ?", (media_id,))
    import shutil
    shutil.rmtree(os.path.join(cache_dir(), storage_mod.safe_name(media_id, "x")), ignore_errors=True)   # și copia din cache
    return True


# ------------------------------------------------------------------ citire / cache
def materialize(media_id: str) -> Optional[str]:
    """Calea unui fișier local cu conținutul din storage (din cache dacă e deja adus; verificat sha256). None dacă nu se poate."""
    obj = get_object(media_id)
    if not obj:
        return None
    dest = os.path.join(cache_dir(), storage_mod.safe_name(media_id, "x"), storage_mod.safe_name(obj["filename"]))
    if os.path.exists(dest) and storage_mod.sha256_file(dest) == obj["sha256"]:
        return dest
    try:
        storage_mod.get_storage().download_to(obj["storage_path"], dest)
    except storage_mod.StorageError as exc:
        print(f"[media] descărcare {media_id}: {exc}")
        return None
    if storage_mod.sha256_file(dest) != obj["sha256"]:
        os.remove(dest)
        print(f"[media] sha256 diferit după descărcare: {media_id}")
        return None
    return dest


def changes(since: int, limit: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with _db() as conn:
        for r in conn.execute("SELECT * FROM media_objects WHERE change_seq > ? AND cemetery_uid = app_default_cemetery_uid() ORDER BY change_seq LIMIT ?", (since, limit + 1)).fetchall():
            d = _row(r)
            out.append({"id": d["id"], "seq": int(d["change_seq"] or 0), "deleted": False, "object": {
                k: d[k] for k in ("id", "entity_uid", "filename", "storage_path", "mime", "size", "sha256", "uploaded_by", "uploaded_at")}})
        for t in conn.execute("SELECT uid, change_seq FROM sync_tombstones WHERE entity='media_objects' AND change_seq > ? "
                              "AND cemetery_uid = app_default_cemetery_uid() ORDER BY change_seq LIMIT ?", (since, limit + 1)).fetchall():
            out.append({"id": t["uid"], "seq": int(t["change_seq"]), "deleted": True, "object": None})
    out.sort(key=lambda c: c["seq"])
    return out
