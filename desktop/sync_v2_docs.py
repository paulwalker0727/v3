"""sync_v2_docs.py — documente/fotografii MAIN (SQLite, Windows) <-> Cloud (Sync v2, Storage privat) <-> Mobile.

Transport: același `Authorization: Binding` ca restul Sync v2 (`/api/v1/sync-v2/documents*`). MAIN nu primește chei de Storage. Sistemul local de documente (`documents`/`photos`, fișiere în DATA_DIR)
NU se modifică: se citește (trimitere) și se completează cu rânduri noi marcate `media_id` (descărcare), fără a șterge vreodată un fișier local.
  * trimitere: fiecare document/fotografie locală (fără acte de identitate/cereri) -> declarare idempotentă (SHA-256) -> încărcare -> verificare; duplicate = același conținut la aceeași entitate.
  * descărcare: documentele/fotografiile create pe telefon apar local (cache DATA_DIR/media_cache), cu SHA-256 verificat.
  * ștergere locală a unui document trimis -> ștergere LOGICĂ în cloud (octeții rămân în Storage).
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

import requests

import cloud_sync as cs
import database as db
import entity_sync as es
import media_sync as ms
import sync_v2 as s2

SET_CURSOR = "sync_v2_docs_cursor"
BLOCKED_TYPES = ("carte_identitate", "cerere_doc")
MAX_BYTES = 25 * 1024 * 1024
MAGIC = {"application/pdf": (b"%PDF-",), "image/jpeg": (b"\xff\xd8\xff",), "image/png": (b"\x89PNG\r\n\x1a\n",)}
EXT_MIME = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
ETYPE_TO_ENTITY = {"loc": "locuri_de_veci", "concesionar": "concesionari", "contract": "contracte", "inhumat": "inhumati", "plata": "plati", "aviz": "amenajari_avize"}
ENTITY_TO_ETYPE = {v: k for k, v in ETYPE_TO_ENTITY.items()}
TABLE_OF = {"concesionar": "concesionari", "contract": "contracte", "inhumat": "inhumati", "plata": "plati", "aviz": "amenajari_avize"}
_DDL = ("CREATE TABLE IF NOT EXISTS sync_v2_docs_state (media_id TEXT PRIMARY KEY, status TEXT NOT NULL, sha256 TEXT, size INTEGER, note TEXT, updated_at TEXT)")


def _ensure(conn) -> None:
    conn.execute(_DDL)
    for t in ("documents", "photos"):
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({t})").fetchall()}
        if "media_id" not in cols:
            conn.execute(f"ALTER TABLE {t} ADD COLUMN media_id TEXT")
    for col in ("sha256 TEXT", "size INTEGER"):
        try:
            conn.execute(f"ALTER TABLE documents ADD COLUMN {col}")
        except Exception:  # noqa: BLE001  (există deja)
            pass


def _set(conn, media_id: str, status: str, sha: str = "", size: int = 0, note: str = "") -> None:
    conn.execute("INSERT INTO sync_v2_docs_state (media_id,status,sha256,size,note,updated_at) VALUES (?,?,?,?,?,?) "
                 "ON CONFLICT(media_id) DO UPDATE SET status=excluded.status, sha256=excluded.sha256, size=excluded.size, note=excluded.note, updated_at=excluded.updated_at",
                 (media_id, status, sha, size, note[:200], datetime.now().isoformat(timespec="seconds")))


def _entity_ref(conn, row, table: str):
    """(entitate cloud, uid părinte) pentru un rând local de documents/photos; None dacă părintele nu are încă uid."""
    if table == "photos":
        r = conn.execute("SELECT uid FROM locuri_de_veci WHERE cod_loc=?", (row["cod_loc"],)).fetchone()
        return ("locuri_de_veci", r["uid"]) if r and r["uid"] else None
    etype, eid = row["entity_type"], str(row["entity_id"])
    if etype == "loc":
        r = conn.execute("SELECT uid FROM locuri_de_veci WHERE cod_loc=?", (eid,)).fetchone()
    elif etype in TABLE_OF and eid.isdigit():
        r = conn.execute(f"SELECT uid FROM {TABLE_OF[etype]} WHERE id=?", (int(eid),)).fetchone()
    else:
        return None
    return (ETYPE_TO_ENTITY[etype], r["uid"]) if r and r["uid"] else None


def inspect_file(path: str) -> Dict[str, Any]:
    """{mime, size, sha256} după extensie ȘI semnătură; ridică ValueError cu motiv în română dacă fișierul nu poate fi trimis."""
    if not path or not os.path.isfile(path):
        raise ValueError("fișierul local lipsește")
    mime = EXT_MIME.get(os.path.splitext(path)[1].lower())
    if not mime:
        raise ValueError("format neacceptat (doar JPEG, PNG, PDF)")
    size = os.path.getsize(path)
    if not 0 < size <= MAX_BYTES:
        raise ValueError("fișier gol sau prea mare (max 25 MB)")
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        head = fh.read(16)
        h.update(head)
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    if not any(head.startswith(m) for m in MAGIC[mime]):
        raise ValueError("conținutul nu corespunde extensiei")
    return {"mime": mime, "size": size, "sha256": h.hexdigest()}


def _call(method: str, op: str, **kw):
    return s2.request(method, op, **kw).json()


def push_documents() -> Dict[str, int]:
    """Trimite documentele/fotografiile locale nesincronizate și ștergerile. Ridică cs.RetryLater/cs.Rejected doar pentru erori de conexiune/autentificare."""
    counts = {"sent": 0, "ready": 0, "duplicate": 0, "skipped": 0, "waiting": 0, "error": 0, "deleted": 0}
    conn = db.get_connection()
    try:
        _ensure(conn)
        for t in ("documents", "photos"):
            conn.execute(f"UPDATE {t} SET media_id=lower(hex(randomblob(16))) WHERE media_id IS NULL OR media_id=''")
        known = {r["media_id"]: r["status"] for r in conn.execute("SELECT media_id,status FROM sync_v2_docs_state").fetchall()}
        todo = []
        for t in ("documents", "photos"):
            for row in conn.execute(f"SELECT * FROM {t}").fetchall():
                if known.get(row["media_id"]) in ("done", "imported", "skipped", "duplicate"):
                    continue
                todo.append((t, row))
        for t, row in todo:
            mid = row["media_id"]
            if t == "documents" and (row["entity_type"] == "cerere" or (row["doc_type"] or "") in BLOCKED_TYPES):
                _set(conn, mid, "skipped", note="act de identitate / cerere: nu se sincronizează")
                counts["skipped"] += 1
                continue
            ref = _entity_ref(conn, row, t)
            if ref is None:
                counts["waiting"] += 1
                continue
            try:
                info = inspect_file(row["file_path"])
            except ValueError as exc:
                _set(conn, mid, "error", note=str(exc))
                counts["error"] += 1
                continue
            body = {"uid": mid, "entity": ref[0], "entity_uid": ref[1], "doc_type": "fotografie_loc" if t == "photos" else (row["doc_type"] or "alt_document"),
                    "kind": "photo" if t == "photos" or info["mime"].startswith("image/") else "document", "filename": os.path.basename(row["file_path"]),
                    "caption": row["caption"] or "", **info}
            try:
                res = _call("POST", "documents/declare", json=body)
                if res.get("status") == "retry":
                    counts["waiting"] += 1
                    continue
                if res.get("status") == "duplicate":
                    _set(conn, mid, "duplicate", info["sha256"], info["size"], f"duplicat al {res.get('duplicate_of')}")
                    counts["duplicate"] += 1
                    continue
                if res.get("status") == "upload":
                    with open(row["file_path"], "rb") as fh:
                        _call("PUT", f"documents/{mid}/content", data=fh.read())
                    counts["sent"] += 1
                _set(conn, mid, "done", info["sha256"], info["size"])
                counts["ready"] += 1
            except cs.Rejected as exc:
                if exc.status in (401, 0):
                    raise
                _set(conn, mid, "error", note=str(exc.detail)[:150])
                counts["error"] += 1
        live = {r["media_id"] for t in ("documents", "photos") for r in conn.execute(f"SELECT media_id FROM {t}").fetchall()}
        for r in conn.execute("SELECT media_id FROM sync_v2_docs_state WHERE status='done'").fetchall():
            if r["media_id"] not in live:
                try:
                    _call("DELETE", f"documents/{r['media_id']}")
                except cs.Rejected as exc:
                    if exc.status not in (404,):
                        counts["error"] += 1
                        continue
                _set(conn, r["media_id"], "deleted")
                counts["deleted"] += 1
        conn.commit()
    finally:
        conn.close()
    return counts


def _download(mid: str) -> bytes:
    url, key, _src = s2.connection()
    try:
        resp = requests.get(f"{url}{s2.API}/documents/{mid}/file", headers={"Authorization": f"Binding {key}"}, timeout=cs.TIMEOUT)
    except requests.RequestException as exc:
        raise cs.RetryLater(f"Fără conexiune la server ({type(exc).__name__})")
    if resp.status_code >= 500:
        raise cs.RetryLater(f"Server indisponibil ({resp.status_code})")
    if resp.status_code != 200:
        raise cs.Rejected(resp.status_code, "documentul nu poate fi descărcat acum")
    return resp.content


def pull_documents() -> Dict[str, int]:
    """Documentele/fotografiile apărute în cloud (ex. de pe telefon) -> rânduri locale + fișier în cache. Cursorul nu trece peste un element a cărui entitate lipsește încă."""
    counts = {"imported": 0, "known": 0, "unresolved": 0, "deleted_remote": 0, "error": 0}
    while True:
        since = int(db.get_setting(SET_CURSOR) or 0)
        page = _call("GET", "documents", params={"since": since, "limit": 200})
        docs = page.get("documents", [])
        if not docs:
            break
        stop = False
        for d in docs:
            conn = db.get_connection()
            try:
                _ensure(conn)
                if d["deleted"]:
                    counts["deleted_remote"] += 1      # fișierul local nu se șterge automat (protecție împotriva pierderii)
                    db.set_setting(SET_CURSOR, str(d["seq"]))
                    continue
                mid = d["uid"]
                if conn.execute("SELECT 1 FROM sync_v2_docs_state WHERE media_id=?", (mid,)).fetchone() or \
                        any(conn.execute(f"SELECT 1 FROM {t} WHERE media_id=?", (mid,)).fetchone() for t in ("documents", "photos")):
                    counts["known"] += 1
                    db.set_setting(SET_CURSOR, str(d["seq"]))
                    continue
                etype = ENTITY_TO_ETYPE.get(d["entity"])
                ref = ms._local_entity(conn, etype, d["entity_uid"]) if etype else None
                if ref is None:
                    counts["unresolved"] += 1
                    stop = True
                    break
                data = _download(mid)
                if hashlib.sha256(data).hexdigest() != d["sha256"]:
                    counts["error"] += 1
                    _set(conn, mid, "error", note="SHA-256 diferit la descărcare")
                    conn.commit()
                    db.set_setting(SET_CURSOR, str(d["seq"]))
                    continue
                dest = ms.cache_path(mid, d["filename"])
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as fh:
                    fh.write(data)
                stamp = datetime.now().strftime("%d.%m.%Y %H:%M")
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, '1')", (es.APPLYING_KEY,))
                try:
                    if d["entity"] == "locuri_de_veci" and d["kind"] == "photo":
                        conn.execute("INSERT INTO photos (cod_loc, file_path, caption, uploader, date_added, is_cover, media_id) VALUES (?,?,?,?,?,0,?)",
                                     (ref, dest, d["caption"] or d["filename"], "telefon" if d["origin"] == "MOBILE" else "", stamp, mid))
                    else:
                        conn.execute("INSERT INTO documents (entity_type, entity_id, doc_type, file_path, caption, nr_evidenta, date_added, media_id, sha256, size) "
                                     "VALUES (?,?,?,?,?,'',?,?,?,?)", (etype, ref, d["doc_type"] or "alt_document", dest, d["caption"] or d["filename"], stamp, mid, d["sha256"], d["size"]))
                finally:
                    conn.execute("DELETE FROM settings WHERE key=?", (es.APPLYING_KEY,))
                _set(conn, mid, "imported", d["sha256"], d["size"])
                conn.commit()
                counts["imported"] += 1
                db.set_setting(SET_CURSOR, str(d["seq"]))
            finally:
                conn.close()
        if stop or not page.get("more"):
            break
    return counts


def sync_documents() -> Dict[str, Any]:
    """Un ciclu complet (descărcare + trimitere); nu ridică excepții. `ok` = False dacă a rămas ceva de reluat."""
    out: Dict[str, Any] = {"ok": False, "lines": []}
    try:
        pushed = push_documents()
        pulled = pull_documents()
        out.update(push=pushed, pull=pulled, ok=not (pushed["error"] or pulled["error"]))
        out["lines"].append(f"Documente: trimise {pushed['sent']}, duplicate evitate {pushed['duplicate']}, primite {pulled['imported']}"
                            f"{', în așteptare ' + str(pushed['waiting'] + pulled['unresolved']) if pushed['waiting'] + pulled['unresolved'] else ''}.")
    except cs.RetryLater as exc:
        out["lines"].append(f"Documente: offline ({exc}); se reiau la reconectare.")
    except cs.Rejected as exc:
        if exc.status == 404:      # server Cloud mai vechi, fără modulul de documente: registrele se sincronizează normal
            out["ok"] = True
            out["lines"].append("Documente: serverul Cloud nu are încă modulul de documente (actualizează Cloud-ul).")
        else:
            out["lines"].append(f"Documente: refuzat de server ({exc.detail}).")
    except Exception as exc:  # noqa: BLE001
        out["lines"].append(f"Documente: eroare neașteptată ({type(exc).__name__}).")
    return out


def new_media_id() -> str:
    return uuid.uuid4().hex
