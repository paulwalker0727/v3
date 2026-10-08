"""API de sincronizare MAIN <-> server online (`/api/sync/*`), activ doar cu CIMITIR_MODE=online.

- Autentificare: antet `X-Sync-Key: <id_cimitir>.<secret>` (cheie per cimitir; în DB doar hash-ul).
- MAIN rămâne sursa evidențelor. Serverul primește doar proiecția publică a hărții (listă explicită de câmpuri)
  și ține cererile până la confirmarea (ack) din MAIN, după care datele personale sunt șterse.
- Versiuni + op_id: operațiunile repetate sunt idempotente, versiunile vechi nu suprascriu pe cele noi (409).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

import cimitir_auth
import cimitir_config
import cimitir_notify
import cimitir_public
import cimitir_requests
import portal_ratelimit
import store as cim

router = APIRouter(prefix="/api/sync")
MAX_IMAGE_BYTES = 25 * 1024 * 1024
_IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
IMAGE_EXT = set(_IMAGE_MIME)


def _hash_key(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def issue_sync_key(source_id: str) -> str:
    """Generează (și înlocuiește) cheia de sincronizare a cimitirului. Se afișează o singură dată."""
    secret = secrets.token_urlsafe(32)
    src = cim.get_source(source_id)
    if not src:
        raise ValueError(f"Cimitir necunoscut: {source_id}")
    cim.bind_source(src)
    cim.set_setting("sync_key_hash", _hash_key(secret))
    return f"{source_id}.{secret}"


def _is_https_or_local(url: str) -> bool:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1"))


def connection_info(source_id: str, request_base: str = "") -> Dict[str, Any]:
    """Datele de conectare pentru MAIN. NU conține cheia secretă (doar dacă există una activă)."""
    configured = (os.environ.get("CIMITIR_PUBLIC_URL") or cim.get_setting("public_base_url") or "").strip().rstrip("/")
    base = configured or (request_base or "").strip().rstrip("/")
    warnings = []
    if not configured:
        warnings.append("CIMITIR_PUBLIC_URL nu este setat: adresa afișată e dedusă din cererea curentă și poate fi greșită în spatele unui proxy.")
    if base and not _is_https_or_local(base):
        warnings.append("Adresa nu este HTTPS: MAIN refuză adresele http:// (în afară de localhost).")
    return {"server_url": base, "url_configured": bool(configured), "https": base.startswith("https://"),
            "cemetery": source_id, "cemetery_id": cim.get_setting("cemetery_id"),
            "mode": cimitir_config.mode(), "db_engine": cimitir_config.db_engine(), "version": cimitir_config.app_version(),
            "health_url": f"{base}/api/health" if base else "", "ping_url": f"{base}/api/sync/ping" if base else "",
            "key_header": "X-Sync-Key", "key_format": "<id_cimitir>.<secret>",
            "key_configured": bool(cim.get_setting("sync_key_hash")),
            "entity_sync": cimitir_config.db_engine() == "postgres", "lease_hours": lease_hours(),
            "warnings": warnings}


def lease_hours() -> float:
    try:
        return float(cim.get_setting("offline_lease_hours") or cimitir_auth.DEFAULT_LEASE_HOURS)
    except ValueError:
        return float(cimitir_auth.DEFAULT_LEASE_HOURS)


def _auth(request: Request) -> str:
    """Leagă cimitirul cheii și întoarce id-ul lui. 404 dacă serverul nu e în mod online.

    Orice cheie greșită (inclusiv format invalid sau cimitir necunoscut) se numără per IP: după 5 eșecuri, 429 + Retry-After.
    """
    if cimitir_config.mode() != "online":
        raise HTTPException(status_code=404)
    client = "sync:" + portal_ratelimit.client_ip(request)
    source_id, _, secret = request.headers.get("X-Sync-Key", "").partition(".")
    src = cim.get_source(source_id) if source_id else None
    if src and src.get("engine") in ("sqlite", "postgres") and secret:
        cim.bind_source(src)
        stored = cim.get_setting("sync_key_hash") or ""
        if stored and hmac.compare_digest(stored, _hash_key(secret)):
            cimitir_auth.clear_failures(client)
            return source_id
    else:
        fallback = next((s for s in cim.list_sources() if s.get("engine") in ("sqlite", "postgres")), None)
        if fallback is None:
            raise HTTPException(status_code=401, detail="Cheie de sincronizare invalidă")
        cim.bind_source(fallback)
    wait = cimitir_auth.lock_remaining(client)
    if wait:
        raise portal_ratelimit.reject(wait, "Prea multe încercări. Reîncearcă peste câteva minute.")
    cimitir_auth.record_failure(client)
    raise HTTPException(status_code=401, detail="Cheie de sincronizare invalidă")


def _already_applied(op_id: str) -> bool:
    conn = cim.get_connection()
    try:
        return bool(op_id) and conn.execute("SELECT 1 FROM sync_ops WHERE op_id=?", (op_id,)).fetchone() is not None
    finally:
        conn.close()


def _record_op(op_id: str, kind: str, result: str) -> None:
    conn = cim.get_connection()
    try:
        conn.execute("INSERT OR IGNORE INTO sync_ops (op_id, kind, applied_at, result) VALUES (?,?,?,?)",
                     (op_id, kind, datetime.now(timezone.utc).isoformat(timespec="seconds"), result))
    finally:
        conn.close()


CLIENTS_KEY = "sync_clients"
STALE_MINUTES = 15
OFFLINE_MINUTES = 60
MAX_CLIENTS = 50


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(value: Any, limit: int = 200) -> str:
    return "".join(ch for ch in str(value or "") if ch.isprintable())[:limit]


def _load_clients() -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(cim.get_setting(CLIENTS_KEY) or "{}")
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _save_client(client_id: str, **fields: Any) -> None:
    clients = _load_clients()
    entry = clients.get(client_id, {})
    entry.update(fields)
    clients[client_id] = entry
    if len(clients) > MAX_CLIENTS:
        oldest = sorted(clients, key=lambda k: clients[k].get("last_seen") or "")[: len(clients) - MAX_CLIENTS]
        for key in oldest:
            clients.pop(key, None)
    cim.set_setting(CLIENTS_KEY, json.dumps(clients))


def client_id_of(request: Request) -> str:
    return _clean(request.headers.get("X-Sync-Client"), 40) or "necunoscut"


def stale_minutes() -> int:
    try:
        return max(1, int(cim.get_setting("sync_stale_minutes") or STALE_MINUTES))
    except ValueError:
        return STALE_MINUTES


def alert_recipients() -> list:
    return [e.strip() for e in (cim.get_setting("alert_emails") or "").split(",") if "@" in e]


def check_offline_alerts() -> int:
    """Trimite pe email (o singură dată per episod) alerta pentru fiecare MAIN offline peste prag. Întoarce câte alerte au plecat."""
    recipients = alert_recipients()
    if cimitir_config.mode() != "online" or not recipients:
        return 0
    sent, limit, name = 0, offline_minutes(), cim.get_setting("cimitir_nume") or "Cimitir"
    for cid, c in _load_clients().items():
        if _age_seconds(c.get("last_seen")) <= limit * 60 or c.get("alerted_for") == c.get("last_seen"):
            continue
        subject = f"[{name}] MAIN offline: {cid}"
        body = (f"Clientul MAIN {cid}{' (' + c['host'] + ')' if c.get('host') else ''} nu a mai contactat serverul de peste {limit} minute.\n"
                f"Ultima activitate: {c.get('last_seen') or 'necunoscută'}. Operațiuni locale restante raportate: {c.get('pending', 'necunoscut')}.\n"
                "Cererile noi ale cetățenilor se acumulează pe server până la următoarea sincronizare. Verificați calculatorul MAIN și conexiunea la internet.")
        delivered = 0
        for address in recipients:
            try:
                cimitir_notify.send_message(address, subject, body)
                delivered += 1
            except Exception as exc:
                print(f"[alert] {cid} -> {address[:1]}***: {exc}")
        if delivered:
            _save_client(cid, alerted_for=c.get("last_seen"), alerted_at=_now_iso())
            sent += 1
    return sent


def offline_minutes() -> int:
    try:
        return max(1, int(cim.get_setting("sync_offline_alert_minutes") or OFFLINE_MINUTES))
    except ValueError:
        return OFFLINE_MINUTES


def _age_seconds(value: Any) -> float:
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(value)).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def clients_status() -> list:
    """Clienții MAIN pentru pagina de monitorizare; `stale` = raportul e mai vechi decât pragul."""
    limit, offline_limit, rows = stale_minutes() * 60, offline_minutes() * 60, []
    for cid, c in _load_clients().items():
        age = _age_seconds(c.get("reported_at") or c.get("last_seen"))
        idle = _age_seconds(c.get("last_seen"))
        rows.append({"client_id": cid, "host": c.get("host", ""), "last_seen": c.get("last_seen", ""),
                     "last_success": c.get("last_success", ""), "pending": c.get("pending"), "conflicts": c.get("conflicts", 0),
                     "last_error": c.get("last_error", ""), "last_error_at": c.get("last_error_at", ""),
                     "error_active": bool(c.get("error_active")), "alerted_at": c.get("alerted_at", ""), "reported_at": c.get("reported_at", ""),
                     "stale": age > limit, "offline": idle > offline_limit,
                     "idle_minutes": None if idle == float("inf") else int(idle // 60)})
    return sorted(rows, key=lambda r: r["last_seen"], reverse=True)


@router.get("/connection-info")
def sync_connection_info(request: Request):
    """Pentru validările din MAIN (autentificat cu X-Sync-Key). Nu întoarce cheia."""
    sid = _auth(request)
    return connection_info(sid, request_base_url(request))


def request_base_url(request: Request) -> str:
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
    return f"{proto}://{host}" if host else ""


@router.get("/ping")
def ping(request: Request):
    sid = _auth(request)
    _save_client(client_id_of(request), last_seen=_now_iso())
    return {"ok": True, "cemetery": sid, "cemetery_id": cim.get_setting("cemetery_id"),
            "server_time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "map_version": int(cim.get_setting("sync_map_version") or 0), "lease_hours": lease_hours(),
            "map_hash": cim.get_setting("sync_map_hash") or "", "plots": _count("locuri_de_veci"),
            "sectors": _count("sectoare"),
            "entity_sync": cimitir_config.db_engine() == "postgres",
            "entity_sync_active": (cim.get_setting("entity_sync_active") or "") == "1",
            "media": _media_capability()}


def _media_capability():
    """Etapa 7: documentele online (storage). None = serverul nu le suportă (ex. SQLite)."""
    if cimitir_config.db_engine() != "postgres":
        return None
    import media_api
    info = media_api.describe()
    return {"driver": info.get("driver"), "max_bytes": info.get("max_bytes"), "chunk_bytes": media_api.MAX_CHUNK,
            "ok": "error" not in info}


def _count(table: str) -> int:
    conn = cim.get_connection()
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


@router.post("/map")
def push_map(request: Request, payload: Dict[str, Any] = Body(...)):
    _auth(request)
    try:
        version = int(payload["map_version"])
        op_id = str(payload["op_id"])
        snap_hash = str(payload["snapshot_hash"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=422, detail="map_version, op_id, snapshot_hash obligatorii")
    current = int(cim.get_setting("sync_map_version") or 0)
    if version < current:
        raise HTTPException(status_code=409, detail={"error": "stale", "current_version": current})
    if version == current:
        if snap_hash == (cim.get_setting("sync_map_hash") or ""):
            return {"ok": True, "duplicate": True, "map_version": current}
        raise HTTPException(status_code=409, detail={"error": "conflict", "current_version": current})

    snap = cimitir_public.sanitize_snapshot(payload)
    # Etapa 6: dacă locurile/înhumații vin prin sincronizarea pe entități (PostgreSQL = sursa principală),
    # proiecția publică a hărții actualizează DOAR sectoarele; nu șterge și nu rescrie registrul.
    entity_mode = (cim.get_setting("entity_sync_active") or "") == "1"
    conn = cim.get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        keep = [p["uid"] for p in ([] if entity_mode else snap["plots"])]
        for p in ([] if entity_mode else snap["plots"]):
            vals = (p["cod_loc"], json.dumps(p["points"]), p["color"] or "#0078FF", p["sector"], p["parcela"], p["rand"],
                    p["numar"], p["tip"], p["categorie"], p["capacitate"], p["suprafata_declarata"],
                    p["disponibilitate"], int(p["are_inhumari"] or 0), version)
            row = conn.execute(f"SELECT id FROM locuri_de_veci WHERE (uid=? OR cod_loc=?) AND {cim.tenant_scope()}", (p["uid"], p["cod_loc"])).fetchone()
            if row:
                conn.execute("UPDATE locuri_de_veci SET cod_loc=?, points=?, color=?, sector=?, parcela=?, rand=?, numar=?, "
                             "tip=?, categorie=?, capacitate=?, suprafata=?, disponibilitate=?, are_inhumari=?, "
                             "sync_version=?, uid=? WHERE id=?", (*vals, p["uid"], row["id"]))
            else:
                conn.execute("INSERT INTO locuri_de_veci (cod_loc, points, color, sector, parcela, rand, numar, tip, categorie,"
                             " capacitate, suprafata, disponibilitate, are_inhumari, sync_version, uid, extra_data) "
                             "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, '{}')", (*vals, p["uid"]))
        if not entity_mode:
            marks = ",".join("?" * len(keep)) or "''"
            conn.execute(f"DELETE FROM locuri_de_veci WHERE (uid IS NULL OR uid NOT IN ({marks})) AND {cim.tenant_scope()}", keep)
        conn.execute(f"DELETE FROM sectoare WHERE {cim.tenant_scope()}")
        for s in snap["sectors"]:
            conn.execute("INSERT INTO sectoare (id, points, color) VALUES (?,?,?)",
                         (str(s["id"]), json.dumps(s["points"]), s["color"] or ""))
        if not entity_mode:
            conn.execute(f"DELETE FROM inhumati WHERE {cim.tenant_scope()}")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    for b in ([] if entity_mode else snap["burials"]):
        cim.save_inhumat(b["cod_loc"], {"nume": b["nume"], "prenume": b["prenume"],
                                        "data_nasterii": b["an_nastere"], "data_deces": b["an_deces"]})
    cim.set_setting("px_per_meter", str(snap["scale"]["px_per_meter"] or ""))
    cim.set_setting("sync_map_hash", snap_hash)
    cim.set_setting("sync_last_push_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    cim.set_setting("sync_map_version", str(version))
    _record_op(op_id, "map", f"v{version}")
    return {"ok": True, "duplicate": False, "map_version": version, "plots": len(snap["plots"]),
            "sectors": len(snap["sectors"]), "burials": len(snap["burials"])}


@router.post("/map/image")
async def push_map_image(request: Request, file: UploadFile = File(...), map_version: int = Form(...),
                         sha256: str = Form(...), width: int = Form(0), height: int = Form(0)):
    _auth(request)
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in IMAGE_EXT:
        raise HTTPException(status_code=415, detail="Format imagine neacceptat")
    current = int(cim.get_setting("sync_image_version") or 0)
    if map_version < current:
        raise HTTPException(status_code=409, detail={"error": "stale", "current_version": current})
    data = await file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Imagine prea mare")
    if hashlib.sha256(data).hexdigest() != sha256:
        raise HTTPException(status_code=422, detail="Sumă de control invalidă")
    if map_version == current and sha256 == (cim.get_setting("sync_image_hash") or ""):
        return {"ok": True, "duplicate": True}
    cim.set_setting("sync_map_image_b64", base64.b64encode(data).decode("ascii"))
    cim.set_setting("sync_map_image_mime", _IMAGE_MIME[ext])
    cim.set_setting("map_bg_w", str(int(width)))
    cim.set_setting("map_bg_h", str(int(height)))
    cim.set_setting("sync_image_hash", sha256)
    cim.set_setting("sync_image_version", str(map_version))
    return {"ok": True, "duplicate": False}


@router.get("/staff")
def staff(request: Request):
    _auth(request)
    return {"staff": cimitir_auth.export_staff(), "lease_hours": lease_hours(),
            "server_time": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_PERSONAL = ("nume", "prenume", "cnp", "bi_ci", "adresa", "telefon", "email", "grad_rudenie", "observatii")


@router.get("/requests")
def pending_requests(request: Request):
    _auth(request)
    conn = cim.get_connection()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM cereri_concesionare WHERE COALESCE(acked,0)=0 AND COALESCE(status,'nou') NOT IN ('validat','respins') ORDER BY id").fetchall()]
    finally:
        conn.close()
    items = []
    for r in rows:
        docs = []
        for d in cim.list_documents("cerere", r["id"]):
            path = d.get("file_path") or ""
            if path and os.path.exists(path):
                docs.append({"id": d["id"], "doc_type": d.get("doc_type"), "caption": d.get("caption"),
                             "size": os.path.getsize(path), "sha256": d.get("sha256") or _sha256_file(path)})
        items.append({"seq": r["id"], "cod_urmarire": r["cod_urmarire"], "created_at": r["created_at"],
                      "cod_loc": r["cod_loc"], "tip": r.get("tip") or "concesionare", "payload": r.get("payload") or "",
                      "revision": int(r.get("revision") or 0), "privacy_version": r.get("privacy_version") or "",
                      **{k: r[k] or "" for k in _PERSONAL}, "documents": docs})
    return {"requests": items}


@router.get("/requests/{code}/documents/{doc_id}")
def request_document(request: Request, code: str, doc_id: int):
    _auth(request)
    req = cim.get_cerere_tracking(code)
    path = cim.get_document_path(doc_id)
    doc = next((d for d in cim.list_documents("cerere", _request_id(code)) if d["id"] == doc_id), None) if req else None
    if not doc or not path or not os.path.exists(path):
        raise HTTPException(status_code=404)
    return FileResponse(path)


def _request_id(code: str) -> int:
    conn = cim.get_connection()
    try:
        row = conn.execute("SELECT id FROM cereri_concesionare WHERE cod_urmarire=?", (code.strip().upper(),)).fetchone()
        return int(row["id"]) if row else 0
    finally:
        conn.close()


@router.post("/requests/ack")
def ack_requests(request: Request, payload: Dict[str, Any] = Body(...)):
    """MAIN a salvat DURABIL cererile: serverul șterge datele personale și fișierele (rămân cod, stare, nume documente).
    O cerere modificată între timp de cetățean (revision mai mare) rămâne neconfirmată."""
    _auth(request)
    acked, revisions = 0, payload.get("revisions") or {}
    for code in payload.get("codes") or []:
        rid = _request_id(str(code))
        if not rid:
            continue
        cur = cim.get_cerere_concesionare(rid) or {}
        if str(code) in revisions and int(revisions[str(code)]) != int(cur.get("revision") or 0):
            continue
        for doc in cim.list_documents("cerere", rid):
            path = doc.get("file_path") or ""
            if doc.get("media_id"):     # Etapa 7: și octeții din storage se șterg (date personale), nu doar copia locală
                import media_service
                media_service.delete_object(doc["media_id"])
            conn = cim.get_connection()
            try:
                conn.execute("UPDATE documents SET file_path='', media_id=NULL WHERE id=?", (doc["id"],))
            finally:
                conn.close()
            if path and os.path.exists(path):
                os.remove(path)
        conn = cim.get_connection()
        try:
            sets = ", ".join(f"{k}=''" for k in _PERSONAL)
            conn.execute(f"UPDATE cereri_concesionare SET {sets}, payload='', acked=1 WHERE id=?", (rid,))
        finally:
            conn.close()
        cimitir_requests.audit("sync", "MAIN", "ack", str(code))
        acked += 1
    return {"ok": True, "acked": acked}


@router.get("/config")
def sync_config(request: Request):
    """Sarea pentru verificarea titularului + termenele de păstrare (MAIN construiește proiecția cu ele)."""
    _auth(request)
    salt = cim.get_setting("entitlement_salt")
    if not salt:
        salt = secrets.token_hex(16)
        cim.set_setting("entitlement_salt", salt)
    return {"entitlement_salt": salt, "holders_version": int(cim.get_setting("holders_version") or 0)}


@router.post("/holders")
def push_holders(request: Request, payload: Dict[str, Any] = Body(...)):
    _auth(request)
    try:
        version = int(payload["version"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=422, detail="version obligatorie")
    current = int(cim.get_setting("holders_version") or 0)
    if version < current:
        raise HTTPException(status_code=409, detail={"error": "stale", "current_version": current})
    if version == current:
        return {"ok": True, "duplicate": True}
    conn = cim.get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(f"DELETE FROM loc_holders WHERE {cim.tenant_scope()}")
        for h in payload.get("holders") or []:
            conn.execute("INSERT OR IGNORE INTO loc_holders (cod_loc, holder_hash) VALUES (?,?)",
                         (str(h["cod_loc"])[:60], str(h["h"])[:64]))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    cim.set_setting("holders_version", str(version))
    return {"ok": True, "duplicate": False, "holders": len(payload.get("holders") or [])}


@router.post("/requests/{code}/status")
def set_request_status(request: Request, code: str, payload: Dict[str, Any] = Body(...)):
    _auth(request)
    status, op_id = payload.get("status"), str(payload.get("op_id") or "")
    try:
        version = int(payload["version"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=422, detail="version obligatorie")
    if status not in cimitir_requests.STATUSES or not op_id:
        raise HTTPException(status_code=422, detail="status/op_id invalide")
    rid = _request_id(code)
    if not rid:
        raise HTTPException(status_code=404, detail="Cerere necunoscută")
    if _already_applied(op_id):
        return {"ok": True, "duplicate": True}
    cur = cim.get_cerere_concesionare(rid)
    if cur.get("status") in ("validat", "respins") and not int(cur.get("sync_version") or 0) and status != cur.get("status"):
        # decizia a fost luată deja din panoul web (sync_version rămâne 0 pentru deciziile web): evităm dubla aprobare/contradicția
        raise HTTPException(status_code=409, detail={"error": "decided_on_server", "status": cur.get("status")})
    if version <= int(cur.get("sync_version") or 0):
        raise HTTPException(status_code=409, detail={"error": "stale", "current_version": int(cur.get("sync_version") or 0)})
    cim.set_cerere_status(rid, status, str(payload.get("note") or "")[:500], None)
    conn = cim.get_connection()
    try:
        closed = datetime.now().isoformat(timespec="seconds") if status in ("validat", "respins") else ""
        conn.execute("UPDATE cereri_concesionare SET sync_version=?, public_message=?, closed_at=? WHERE id=?",
                     (version, str(payload.get("note") or "")[:500], closed, rid))
    finally:
        conn.close()
    cimitir_requests.audit("sync", "MAIN", "status", code, f"{status} v{version}")
    _record_op(op_id, "status", f"{code}:{status}:v{version}")
    cimitir_notify.notify_status_change(code, status, str(payload.get("note") or ""))
    return {"ok": True, "duplicate": False, "version": version}


@router.post("/status")
def report_status(request: Request, payload: Dict[str, Any] = Body(...)):
    """MAIN raportează la reconectare: ultima sincronizare reușită, eroarea curentă, operațiunile locale restante."""
    _auth(request)
    try:
        pending = int(payload["pending"]) if payload.get("pending") is not None else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="pending invalid")
    if pending is not None and pending < 0:
        raise HTTPException(status_code=422, detail="pending invalid")
    now, error = _now_iso(), _clean(payload.get("last_error"), 300)
    fields = {"last_seen": now, "reported_at": now, "host": _clean(payload.get("host"), 80),
              "last_success": _clean(payload.get("last_success"), 40), "pending": pending, "error_active": bool(error)}
    try:
        fields["conflicts"] = max(0, int(payload.get("conflicts") or 0))   # Etapa 6: conflicte de rezolvat în MAIN
    except (TypeError, ValueError):
        pass
    if error:
        fields.update(last_error=error, last_error_at=now)
    _save_client(client_id_of(request), **fields)
    return {"ok": True}
