"""
media_sync.py — Etapa 7 (MAIN): documentele și fotografiile în storage online, prin API.

  Desktop (SQLite: fișiere în DATA_DIR/media + cache) --HTTPS--> FastAPI --> storage (S3 / Supabase / local) + PostgreSQL (metadate)

  * Nou fișier (documente/photos): se salvează LOCAL ca până acum (DATA_DIR/media) — merge și offline. Un trigger SQLite îi dă un
    `media_id` și pune o operațiune `upload` în `cloud_sync_outbox` (aceeași coadă ca Etapele 2-6). Când există internet, fișierul
    se încarcă în bucăți (1 MiB) la `/api/sync/media`; serverul verifică sha256 și abia apoi scrie metadatele în PostgreSQL.
    Fișierul local NU se șterge niciodată după încărcare.
  * Un fișier creat pe alt calculator apare aici ca rând local (metadate descărcate incremental); octeții se aduc din storage, prin
    API, la prima deschidere (sau în fundal, pentru fișiere mici), se verifică sha256 și rămân în cache (DATA_DIR/media_cache).
  * Ștergere locală => operațiune `delete` (obiectul din storage se șterge; fișierul local gestionat urmează regula veche).
  * Desktopul nu primește niciodată chei de storage. Fișierele vechi NU sunt migrate automat: `enqueue_existing()` e doar piesa pe care
    se poate sprijini un script de migrare separat (nefolosită nicăieri).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import cimitir_storage as storage_mod
import cloud_sync as cs
import database as db
import entity_sync as es

SET_CAPABLE = "cloud_media_sync"
SET_CURSOR = "media_cursor"
CHUNK = 1024 * 1024
PREFETCH_MAX = 20
PREFETCH_BYTES = 10 * 1024 * 1024
TABLES = ("documents", "photos")
_READY: set = set()
_NOT_APPLYING = es._NOT_APPLYING
_OUTBOX = es._OUTBOX_COLS
_NOW = es._NOW
_SHA = re.compile(r"^[0-9a-f]{64}$")


# ------------------------------------------------------------------ capabilități
def capable() -> Optional[bool]:
    if not es.enabled():
        return False
    value = (db.get_setting(SET_CAPABLE) or "").strip()
    return None if value == "" else value == "1"


def set_capable(info: Any) -> None:
    flag = bool(info) and es.enabled()
    before = capable()
    db.set_setting(SET_CAPABLE, "1" if flag else "0")
    if flag and before is not True:
        ensure_tracking(force=True)
    if not flag:
        disable_tracking()


# ------------------------------------------------------------------ captare locală (triggere)
def _create_triggers(conn, table: str) -> None:
    for suffix in ("ai", "ad"):
        conn.execute(f"DROP TRIGGER IF EXISTS cm_{table}_{suffix}")
    skip_cerere = "AND NEW.entity_type <> 'cerere'" if table == "documents" else ""
    skip_cerere_old = "AND OLD.entity_type <> 'cerere'" if table == "documents" else ""
    conn.execute(f"""CREATE TRIGGER cm_{table}_ai AFTER INSERT ON {table} WHEN {_NOT_APPLYING} {skip_cerere}
BEGIN
  UPDATE {table} SET media_id = lower(hex(randomblob(16))) WHERE id = NEW.id AND (media_id IS NULL OR media_id = '');
  INSERT INTO cloud_sync_outbox ({_OUTBOX})
  SELECT 'media','{table}',media_id,'upload',0,'pending',lower(hex(randomblob(16))),{_NOW},0,'','','','{{}}'
  FROM {table} WHERE id = NEW.id;
END""")
    conn.execute(f"""CREATE TRIGGER cm_{table}_ad AFTER DELETE ON {table} WHEN {_NOT_APPLYING} AND OLD.media_id IS NOT NULL {skip_cerere_old}
BEGIN
  INSERT INTO cloud_sync_outbox ({_OUTBOX})
  SELECT 'media','{table}',OLD.media_id,'delete',0,'pending',lower(hex(randomblob(16))),{_NOW},0,'','','','{{}}'
  WHERE NOT EXISTS (SELECT 1 FROM cloud_sync_outbox WHERE kind='media' AND entity_uid=OLD.media_id AND action='upload'
                    AND COALESCE(state,'pending')='pending');
  DELETE FROM cloud_sync_outbox WHERE kind='media' AND entity_uid=OLD.media_id AND action='upload' AND COALESCE(state,'pending')='pending';
END""")


def ensure_tracking(force: bool = False) -> bool:
    if capable() is False:
        return False
    key = db.current_db_path()
    if key in _READY and not force:
        return True
    cs.ensure_tables()
    conn = db.get_connection()
    try:
        for table in TABLES:
            if not es._table_exists(conn, table):
                return False
            db._add_column_if_missing(conn, table, "media_id TEXT")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_media ON {table}(media_id)")
            _create_triggers(conn, table)
    finally:
        conn.close()
    db.set_media_resolver(ensure_local)
    _READY.add(key)
    return True


def disable_tracking() -> None:
    conn = db.get_connection()
    try:
        for table in TABLES:
            for suffix in ("ai", "ad"):
                conn.execute(f"DROP TRIGGER IF EXISTS cm_{table}_{suffix}")
        if es._table_exists(conn, "cloud_sync_outbox"):
            conn.execute("DELETE FROM cloud_sync_outbox WHERE kind='media'")
    finally:
        conn.close()
    _READY.discard(db.current_db_path())


def enqueue_existing(limit: int = 0) -> int:
    """PIESĂ pentru un viitor script de migrare (NU se apelează automat): pune în coadă fișierele locale existente, fără media_id."""
    ensure_tracking()
    total = 0
    conn = db.get_connection()
    try:
        for table in TABLES:
            where = "media_id IS NULL OR media_id=''" + (" AND entity_type <> 'cerere'" if table == "documents" else "")
            rows = conn.execute(f"SELECT id FROM {table} WHERE {where} ORDER BY id" + (f" LIMIT {int(limit)}" if limit else "")).fetchall()
            for r in rows:
                mid = os.urandom(16).hex()
                conn.execute(f"UPDATE {table} SET media_id=? WHERE id=?", (mid, r["id"]))
                conn.execute(f"INSERT INTO cloud_sync_outbox ({_OUTBOX}) VALUES ('media',?,?,'upload',0,'pending',?,{_NOW},0,'','','','{{}}')",
                             (table, mid, os.urandom(16).hex()))
                total += 1
    finally:
        conn.close()
    return total


# ------------------------------------------------------------------ uid de entitate (local)
def _entity_uid(conn, table: str, row) -> str:
    if table == "photos":
        r = conn.execute("SELECT uid FROM locuri_de_veci WHERE cod_loc=?", (row["cod_loc"],)).fetchone()
        return (r["uid"] if r and r["uid"] else f"loc:{row['cod_loc']}")
    etype, eid = row["entity_type"], str(row["entity_id"])
    tables = {"inhumat": "inhumati", "contract": "contracte", "concesionar": "concesionari", "plata": "plati"}
    if etype == "loc":
        r = conn.execute("SELECT uid FROM locuri_de_veci WHERE cod_loc=?", (eid,)).fetchone()
    elif etype in tables:
        r = conn.execute(f"SELECT uid FROM {tables[etype]} WHERE id=?", (int(eid),)).fetchone() if eid.isdigit() else None
    elif etype == "aviz" and eid.isdigit():
        a = conn.execute("SELECT a.nr_aviz, l.uid FROM amenajari_avize a LEFT JOIN locuri_de_veci l ON l.cod_loc=a.cod_loc "
                         "WHERE a.id=?", (int(eid),)).fetchone()
        return f"aviz:{a['uid'] or ''}:{a['nr_aviz']}" if a else f"aviz:{eid}"
    else:
        r = None
    return r["uid"] if r and r["uid"] else f"{etype}:{eid}"


def _local_entity(conn, entity_type: str, uid: str) -> Optional[str]:
    """uid -> entity_id local (cod_loc pentru loc, id numeric în rest). None dacă entitatea nu există local."""
    tables = {"inhumat": "inhumati", "contract": "contracte", "concesionar": "concesionari", "plata": "plati"}
    if entity_type == "loc":
        r = conn.execute("SELECT cod_loc FROM locuri_de_veci WHERE uid=?", (uid,)).fetchone()
        return r["cod_loc"] if r else None
    if entity_type in tables:
        r = conn.execute(f"SELECT id FROM {tables[entity_type]} WHERE uid=?", (uid,)).fetchone()
        return str(r["id"]) if r else None
    if entity_type == "aviz" and uid.startswith("aviz:"):          # uid compus: aviz:<uid loc>:<nr_aviz>
        try:
            _, loc_uid, nr = uid.split(":", 2)
        except ValueError:
            return None
        r = conn.execute("SELECT a.id FROM amenajari_avize a JOIN locuri_de_veci l ON l.cod_loc=a.cod_loc WHERE l.uid=? AND a.nr_aviz=?",
                         (loc_uid, nr)).fetchone()
        return str(r["id"]) if r else None
    return None


# ------------------------------------------------------------------ trimitere
def _pending_ops() -> List[Dict[str, Any]]:
    cs.ensure_tables()
    conn = db.get_connection()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM cloud_sync_outbox WHERE kind='media' "
                                              "AND COALESCE(state,'pending')='pending' ORDER BY id")]
    finally:
        conn.close()


def _set_state(op_id: int, state: str, error: str = "") -> None:
    conn = db.get_connection()
    try:
        conn.execute("UPDATE cloud_sync_outbox SET state=?, attempts=COALESCE(attempts,0)+1, last_error=? WHERE id=?",
                     (state, error[:300], op_id))
    finally:
        conn.close()


def _upload(op: Dict[str, Any]) -> str:
    """'done' | 'dropped' | 'error' | 'retry'. RetryLater se propagă (offline)."""
    table, media_id = op["entity"], op["entity_uid"]
    conn = db.get_connection()
    try:
        row = conn.execute(f"SELECT * FROM {table} WHERE media_id=?", (media_id,)).fetchone()
        if row is None:
            cs._delete_op(op["id"])
            return "dropped"
        path = row["file_path"]
        if not path or not os.path.isfile(path):
            _set_state(op["id"], "error", "fișierul local lipsește")
            return "error"
        entity_uid = _entity_uid(conn, table, row)
        etype = "loc" if table == "photos" else row["entity_type"]
        dtype = "poza_loc" if table == "photos" else (row["doc_type"] or "document")
    finally:
        conn.close()
    sha, size = storage_mod.sha256_file(path), os.path.getsize(path)
    if size == 0:
        _set_state(op["id"], "error", "fișier gol")
        return "error"
    chunks = 0
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            cs._request("PUT", f"/media/{media_id}/chunks/{chunks}", data=block, timeout=120,
                        headers={"Content-Type": "application/octet-stream"})
            chunks += 1
    try:
        cs._request("POST", f"/media/{media_id}/complete", timeout=180, json={
            "entity_uid": entity_uid, "filename": storage_mod.display_name(path), "sha256": sha, "size": size, "chunks": chunks,
            "table": table, "entity_type": etype, "doc_type": dtype, "uploaded_by": db.get_setting("current_user") or cs.client_id()})
    except cs.Rejected as exc:
        if getattr(exc, "status", None) in (409, 422) or (exc.args and exc.args[0] in (409, 422)):   # bucăți lipsă / sha256 diferit
            _set_state(op["id"], "error" if int(op.get("attempts") or 0) >= 4 else "pending", f"reîncărcare: {exc}")
            return "retry"
        raise
    if table == "documents":
        conn = db.get_connection()
        try:
            conn.execute("UPDATE documents SET sha256=COALESCE(sha256, ?), size=COALESCE(size, ?) WHERE media_id=?", (sha, size, media_id))
        finally:
            conn.close()
    cs._delete_op(op["id"])      # fișierul local rămâne întotdeauna
    return "done"


def flush_media() -> Tuple[bool, List[str], Dict[str, int]]:
    counts = {"uploaded": 0, "deleted": 0, "dropped": 0, "error": 0, "retry": 0}
    if capable() is False or not ensure_tracking():
        return True, [], counts
    lines: List[str] = []
    for op in _pending_ops():
        try:
            if op["action"] == "upload":
                result = _upload(op)
                key = {"done": "uploaded"}.get(result, result)
                counts[key] += 1
            else:
                cs._request("DELETE", f"/media/{op['entity_uid']}")
                cs._delete_op(op["id"])
                counts["deleted"] += 1
        except cs.RetryLater as exc:
            _set_state(op["id"], "pending", str(exc))
            lines.append(f"Documente amânate (offline): {exc}")
            return False, lines, counts
        except cs.Rejected as exc:
            _set_state(op["id"], "error", str(exc))
            counts["error"] += 1
    if counts["uploaded"] or counts["deleted"] or counts["error"]:
        lines.append(f"Documente: {counts['uploaded']} încărcate, {counts['deleted']} șterse, {counts['error']} erori")
    return counts["error"] == 0 and counts["retry"] == 0, lines, counts


# ------------------------------------------------------------------ descărcare / cache
def cache_path(media_id: str, filename: str) -> str:
    return os.path.join(db.cache_dir("media_cache"), storage_mod.safe_name(media_id, "x"), storage_mod.safe_name(filename))


def ensure_local(media_id: str, hint: str = "") -> Optional[str]:
    """Aduce din storage (prin API) fișierul unui media_id și îl pune în cache; verifică sha256. None = indisponibil/offline."""
    conn = db.get_connection()
    try:
        row = conn.execute("SELECT file_path, sha256 FROM documents WHERE media_id=? UNION ALL "
                           "SELECT file_path, NULL FROM photos WHERE media_id=? LIMIT 1", (media_id, media_id)).fetchone()
    finally:
        conn.close()
    dest = row["file_path"] if row and row["file_path"] and os.path.dirname(row["file_path"]).startswith(db.cache_dir("media_cache")) \
        else cache_path(media_id, hint or (os.path.basename(row["file_path"]) if row and row["file_path"] else "fisier"))
    expected = row["sha256"] if row and row["sha256"] and _SHA.match(row["sha256"] or "") else None
    if os.path.isfile(dest) and (not expected or storage_mod.sha256_file(dest) == expected):
        return dest
    if not cs.is_configured() or capable() is False:
        return None
    try:
        resp = cs._request("GET", f"/media/{media_id}", stream=True, timeout=60)
    except (cs.RetryLater, cs.Rejected):
        return None
    tmp = dest + ".part"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    h = hashlib.sha256()
    try:
        with open(tmp, "wb") as fh:
            for block in resp.iter_content(CHUNK):
                h.update(block)
                fh.write(block)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        _rm(tmp)
        return None
    want = (resp.headers.get("X-Sha256") or expected or "").lower()
    if want and h.hexdigest() != want:
        _rm(tmp)
        return None
    os.replace(tmp, dest)
    return dest


def _rm(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _apply_object(conn, obj: Dict[str, Any]) -> str:
    media_id = obj["id"]
    for table in TABLES:
        if conn.execute(f"SELECT 1 FROM {table} WHERE media_id=?", (media_id,)).fetchone():
            return "known"
    info = storage_mod.parse_key(obj["storage_path"])
    dest = cache_path(media_id, obj["filename"])
    stamp = datetime.now().strftime("%d.%m.%Y %H:%M")
    if info["table"] == "photos":
        ref = _local_entity(conn, "loc", obj["entity_uid"])
        if ref is None:
            return "unresolved"
        conn.execute("INSERT INTO photos (cod_loc, file_path, caption, uploader, date_added, is_cover, media_id) VALUES (?,?,?,?,?,0,?)",
                     (ref, dest, obj["filename"], obj.get("uploaded_by") or "", stamp, media_id))
        return "inserted"
    ref = _local_entity(conn, info["entity_type"], obj["entity_uid"])
    if ref is None:
        return "unresolved"       # cerere / aviz / entitate încă necunoscută local: rămâne doar în storage
    conn.execute("INSERT INTO documents (entity_type, entity_id, doc_type, file_path, caption, nr_evidenta, date_added, media_id, sha256, size) "
                 "VALUES (?,?,?,?,?,'',?,?,?,?)",
                 (info["entity_type"], ref, info["doc_type"], dest, obj["filename"], stamp, media_id, obj["sha256"], obj["size"]))
    return "inserted"


def pull_media() -> Tuple[bool, str, int]:
    """Metadatele noi/șterse de pe server -> rânduri locale (octeții se aduc la cerere sau prefetch)."""
    if capable() is False or not ensure_tracking():
        return True, "Documente: indisponibile pe acest server.", 0
    added = removed = unresolved = 0
    prefetch: List[Dict[str, Any]] = []
    while True:
        since = int(db.get_setting(SET_CURSOR) or 0)
        try:
            page = cs._request("GET", "/media", params={"since": since, "limit": 500}).json()
        except (cs.RetryLater, cs.Rejected) as exc:
            return False, f"Documente (descărcare): {exc}", added
        with es._Applying() as conn:
            for ch in page.get("changes", []):
                if ch.get("deleted"):
                    for table in TABLES:
                        removed += conn.execute(f"DELETE FROM {table} WHERE media_id=?", (ch["id"],)).rowcount or 0
                    continue
                outcome = _apply_object(conn, ch["object"])
                added += outcome == "inserted"
                unresolved += outcome == "unresolved"
                if outcome == "inserted" and int(ch["object"]["size"]) <= PREFETCH_BYTES:
                    prefetch.append(ch["object"])
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (SET_CURSOR, str(int(page.get("cursor") or since))))
        if not page.get("more"):
            break
    fetched = 0
    for obj in prefetch[:PREFETCH_MAX]:       # pregătire pentru offline (fișiere mici); restul se aduc la prima deschidere
        fetched += bool(ensure_local(obj["id"], obj["filename"]))
    note = f", {unresolved} fără entitate locală" if unresolved else ""
    return True, f"Documente descărcate: {added} noi, {removed} șterse, {fetched} aduse în cache{note}", added


def sync_media() -> Tuple[bool, str]:
    ok_push, lines, _c = flush_media()
    ok_pull, msg, _n = pull_media()
    return ok_push and ok_pull, "; ".join(lines + [msg]) if lines else msg
