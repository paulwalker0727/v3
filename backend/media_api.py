"""Etapa 7 — documente online: `/api/sync/media/*` (FastAPI este singurul care vorbește cu storage-ul; desktopul nu primește chei).

  PUT    /api/sync/media/{id}/chunks/{n}   corp brut (<= 8 MiB) — încărcare în bucăți (trece de limitele proxy-ului), reluabilă
  POST   /api/sync/media/{id}/complete     {entity_uid, filename, sha256, size, chunks, table, entity_type, doc_type, uploaded_by}
                                           -> asamblează, VERIFICĂ sha256+dimensiunea, pune în storage, scrie metadatele în PostgreSQL
  GET    /api/sync/media/{id}              descarcă octeții (din storage; antet X-Sha256)
  GET    /api/sync/media?since=&limit=     metadate incrementale (+ ștergeri), cursor `change_seq`
  DELETE /api/sync/media/{id}              șterge obiectul și metadatele
  GET    /api/sync/media/state             driverul activ (fără secrete) și numărul de obiecte

Idempotent pe `media_id`: aceeași încărcare repetată (același sha256) întoarce `duplicate: true`; alt conținut pe același id = 409.
Nimic nu se scrie în PostgreSQL înainte ca sha256 să fie verificat și obiectul să fie în storage.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import StreamingResponse

import cimitir_config
import cimitir_storage as storage_mod
import media_service as media
import sync_api

router = APIRouter(prefix="/api/sync/media")
MAX_CHUNK = 8 * 1024 * 1024
_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


def _guard(request: Request) -> str:
    sid = sync_api._auth(request)
    if cimitir_config.db_engine() != "postgres":
        raise HTTPException(status_code=501, detail="Documentele online cer serverul pe PostgreSQL.")
    return sid


def _check_id(media_id: str) -> str:
    if not _ID.match(media_id or ""):
        raise HTTPException(status_code=422, detail="media_id invalid")
    return media_id


def _uploads_root() -> str:
    return os.path.join(os.environ.get("CIMITIR_DATA_DIR") or "shared_data", "media_uploads")


def _upload_dir(media_id: str) -> str:
    path = os.path.join(_uploads_root(), media_id)
    os.makedirs(path, exist_ok=True)
    return path


def describe() -> Dict[str, Any]:
    try:
        return {**storage_mod.get_storage().describe(), "max_bytes": media.MAX_BYTES}
    except storage_mod.StorageError as exc:
        return {"driver": storage_mod.driver_name(), "error": str(exc)}


@router.put("/{media_id}/chunks/{index}")
async def put_chunk(request: Request, media_id: str, index: int):
    _guard(request)
    _check_id(media_id)
    if not 0 <= index < 10000:
        raise HTTPException(status_code=422, detail="index invalid")
    body = await request.body()
    if not body or len(body) > MAX_CHUNK:
        raise HTTPException(status_code=413 if body else 422, detail=f"bucată goală sau > {MAX_CHUNK} octeți")
    path = os.path.join(_upload_dir(media_id), f"{index:05d}.part")
    with open(path + ".tmp", "wb") as fh:
        fh.write(body)
    os.replace(path + ".tmp", path)
    return {"ok": True, "index": index, "size": len(body)}


@router.post("/{media_id}/complete")
def complete(request: Request, media_id: str, payload: Dict[str, Any] = Body(...)):
    _guard(request)
    _check_id(media_id)
    client_id = sync_api.client_id_of(request)
    sha = str(payload.get("sha256") or "").lower()
    if not _SHA.match(sha):
        raise HTTPException(status_code=422, detail="sha256 invalid")
    existing = media.get_object(media_id)
    if existing:
        if existing["sha256"] != sha:
            raise HTTPException(status_code=409, detail="media_id există deja cu alt conținut")
        shutil.rmtree(os.path.join(_uploads_root(), media_id), ignore_errors=True)
        return {"ok": True, "duplicate": True, "object": existing}
    try:
        chunks, size = int(payload.get("chunks") or 0), int(payload.get("size") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="chunks/size invalide")
    entity_uid = str(payload.get("entity_uid") or "").strip()[:200]
    filename = storage_mod.safe_name(str(payload.get("filename") or "fisier"))
    table = "photos" if payload.get("table") == "photos" else "documents"
    if not entity_uid or chunks < 1 or size < 0 or size > media.MAX_BYTES:
        raise HTTPException(status_code=422, detail="entity_uid/chunks/size invalide sau fișier prea mare")
    directory = _upload_dir(media_id)
    parts = [os.path.join(directory, f"{i:05d}.part") for i in range(chunks)]
    missing = [i for i, p in enumerate(parts) if not os.path.exists(p)]
    if missing:
        raise HTTPException(status_code=409, detail={"error": "chunks_missing", "missing": missing[:50]})
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".assembled")
    try:
        h, total = hashlib.sha256(), 0
        with os.fdopen(fd, "wb") as out:
            for p in parts:
                with open(p, "rb") as fh:
                    data = fh.read()
                h.update(data)
                total += len(data)
                out.write(data)
        if total != size or h.hexdigest() != sha:
            raise HTTPException(status_code=422, detail={"error": "checksum_mismatch", "size": total, "sha256": h.hexdigest()})
        key = storage_mod.build_key(table, str(payload.get("entity_type") or ""), str(payload.get("doc_type") or ""),
                                    entity_uid, media_id, filename)
        mime = storage_mod.guess_mime(filename)
        try:
            storage_mod.get_storage().put_file(key, tmp, mime)
        except storage_mod.StorageError as exc:
            raise HTTPException(status_code=503, detail=f"storage indisponibil: {exc}")
        obj = media.insert_metadata(media_id, entity_uid, filename, key, mime, total, sha,
                                    str(payload.get("uploaded_by") or client_id)[:120])
    finally:
        shutil.rmtree(directory, ignore_errors=True)   # nu rămâne nimic parțial/greșit; la mismatch clientul reîncarcă de la zero
    sync_api._save_client(client_id, last_seen=sync_api._now_iso())
    return {"ok": True, "duplicate": False, "object": obj}


@router.get("/state")
def state(request: Request):
    _guard(request)
    with media._db() as conn:
        n = int(conn.execute("SELECT COUNT(*) AS n FROM media_objects").fetchone()["n"])
        total = int(conn.execute("SELECT COALESCE(SUM(size),0) AS s FROM media_objects").fetchone()["s"])
    return {"storage": describe(), "objects": n, "bytes": total}


@router.get("")
def list_changes(request: Request, since: int = 0, limit: int = 500):
    _guard(request)
    limit = max(1, min(int(limit), 1000))
    changes = media.changes(since, limit)
    more = len(changes) > limit
    changes = changes[:limit]
    return {"changes": changes, "cursor": changes[-1]["seq"] if changes else since, "more": more}


@router.get("/{media_id}")
def download(request: Request, media_id: str):
    _guard(request)
    _check_id(media_id)
    obj = media.get_object(media_id)
    if not obj:
        raise HTTPException(status_code=404, detail="obiect necunoscut")
    try:
        stream = storage_mod.get_storage().open_stream(obj["storage_path"])
    except storage_mod.NotFound:
        raise HTTPException(status_code=404, detail="obiectul lipsește din storage")
    except storage_mod.StorageError as exc:
        raise HTTPException(status_code=503, detail=f"storage indisponibil: {exc}")
    return StreamingResponse(stream, media_type=obj["mime"], headers={
        "X-Sha256": obj["sha256"], "Content-Length": str(obj["size"]),
        "Content-Disposition": f'attachment; filename="{obj["filename"]}"'})


@router.delete("/{media_id}")
def remove(request: Request, media_id: str):
    _guard(request)
    _check_id(media_id)
    return {"ok": True, "deleted": media.delete_object(media_id)}
