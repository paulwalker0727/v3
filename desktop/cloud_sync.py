"""
cloud_sync.py — sincronizare MAIN <-> server online prin API HTTPS (`/api/sync/*`, vezi backend/sync_api.py).

- MAIN rămâne sursa oficială; SQLite local nu este înlocuit niciodată de date de la server.
- Către server: proiecția publică a hărții (poligoane, sectoare, scară, disponibilitate) cu versiune monotonă,
  fundalul hărții, deciziile asupra cererilor. Fiecare operațiune are `op_id`; cele eșuate rămân în
  `cloud_sync_outbox` și se reiau la următoarea sincronizare (offline-first).
- De la server: cererile noi (confirmate cu `ack` după salvare locală) și directorul de personal cu lease offline.
- Nu se transferă fișierul .db.
- Etapa 6: datele de bază (locuri, concesionari, contracte, înhumați, plăți) circulă pe entități, bidirecțional, prin
  `entity_sync.py` (aceeași coadă `cloud_sync_outbox`, același `op_id`, aceeași cheie). Desktopul vorbește DOAR cu API-ul
  (FastAPI); PostgreSQL este accesat exclusiv de server.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

import cimitir_auth
import cloud_profiles
import cimitir_public
import cimitir_requests
import database as db

SET_URL = "cloud_api_url"
SET_KEY = "cloud_sync_key"
SET_AUTO = "cloud_sync_auto"
SET_BURIALS = "cloud_publish_burials"
SET_LAST = "cloud_sync_last"
SET_LAST_ERROR = "cloud_sync_last_error"
SET_INSECURE = "cloud_allow_insecure"
SET_BOUND = "cloud_bound_cemetery"
SET_QUEUED_HASH = "cloud_map_hash_queued"
SET_QUEUED_IMAGE = "cloud_image_hash_queued"
SET_IMAGE_VERSION = "cloud_image_version"
SET_MAP_VERSION = "sync_map_version"
SET_CLIENT_ID = "cloud_client_id"
SET_LAST_OK_ISO = "cloud_sync_last_ok_iso"

TIMEOUT = 20
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


class RetryLater(Exception):
    """Eroare temporară (rețea/5xx): operațiunea rămâne în coadă."""


class Rejected(Exception):
    """Serverul a refuzat definitiv operațiunea (409/4xx)."""

    def __init__(self, status: int, detail: Any):
        super().__init__(f"{status}: {detail}")
        self.status, self.detail = status, detail


# ------------------------------------------------------------------ configurare
def _connection() -> Tuple[str, str]:
    """(url, cheie): profilul activ sau setările vechi; cheia vine DOAR din SecretStore (sau din valoarea veche încă nemigrată)."""
    try:
        return cloud_profiles.effective_connection()
    except Exception as exc:
        print(f"[cloud_sync] conexiune: {type(exc).__name__}")
        return (db.get_setting(SET_URL) or "").strip().rstrip("/"), ""


def get_config() -> Dict[str, Any]:
    url, key = _connection()
    return {
        "url": url,
        "key": key,
        "auto": (db.get_setting(SET_AUTO) or "0") == "1",
        "burials": (db.get_setting(SET_BURIALS) or "0") == "1",
        "last": db.get_setting(SET_LAST) or "",
        "last_error": db.get_setting(SET_LAST_ERROR) or "",
    }


def save_config(url: str, key: str, auto: bool, publish_burials: Optional[bool] = None) -> None:
    managed = cloud_profiles.active_profile() is not None
    if not managed:
        db.set_setting(SET_URL, (url or "").strip().rstrip("/"))
        if (key or "").strip():
            cloud_profiles.save_legacy_key(key)  # SecretStore; niciodată plaintext în SQLite
    db.set_setting(SET_AUTO, "1" if auto else "0")
    if publish_burials is not None:
        db.set_setting(SET_BURIALS, "1" if publish_burials else "0")
    cfg = get_config()
    if cfg["url"] and cfg["key"]:
        try:
            import entity_sync
            entity_sync.ensure_tracking(force=True)
            import media_sync
            media_sync.ensure_tracking(force=True)
        except Exception as exc:
            print(f"[cloud_sync] entity tracking: {exc}")


def is_configured() -> bool:
    cfg = get_config()
    return bool(cfg["url"] and cfg["key"])


def url_is_secure(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return True
    return parsed.scheme == "http" and (parsed.hostname in _LOCAL_HOSTS or (db.get_setting(SET_INSECURE) or "") == "1")


_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}\.[A-Za-z0-9_-]{32,}$")
_BINDING_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}\.[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.[A-Za-z0-9_-]{32,}$")


def validate_url(url: str) -> str:
    """Mesaj de eroare sau '' (adresa serverului: doar origine https://gazdă[:port], fără cale)."""
    url = (url or "").strip()
    if not url:
        return "Completează adresa serverului."
    if re.search(r"\s", url):
        return "Adresa conține spații."
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "Adresa trebuie să înceapă cu https:// (ex: https://cimitir.onrender.com)."
    if not url_is_secure(url):
        return "Adresa trebuie să fie HTTPS (http:// e permis doar pentru localhost)."
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        return "Introdu doar adresa serverului, fără cale (fără /admin, /api sau parametri)."
    return ""


def validate_key(key: str) -> str:
    """Mesaj de eroare sau '' (forma cheii: <id_cimitir>.<secret>, generată din /admin/setari)."""
    key = (key or "").strip()
    if not key:
        return "Completează cheia de sincronizare."
    if re.search(r"\s", key) or key[0] in "'\"" or key[-1] in "'\"":
        return "Cheia conține spații sau ghilimele; copiaz-o exact, fără ele."
    if not (_KEY_RE.match(key) or _BINDING_KEY_RE.match(key)):
        return "Cheia nu are forma corectă (<id_cimitir>.<secret>). Generează „cheie MAIN” din /admin/setari de pe server."
    return ""


def validate_config(url: str, key: str) -> List[str]:
    return [m for m in (validate_url(url), validate_key(key)) if m]


def check_connection(url: str, key: str) -> Tuple[bool, str]:
    """Verifică adresa + cheia FĂRĂ a le salva: stare server, cheie, mod online. Mesaje clare în română."""
    errors = validate_config(url, key)
    if errors:
        return False, " ".join(errors)
    base = url.strip().rstrip("/")
    try:
        health = requests.get(f"{base}/api/health", timeout=TIMEOUT)
    except requests.RequestException as exc:
        return False, f"Serverul nu răspunde la {base} ({type(exc).__name__}). Pe planul gratuit Render serverul adoarme; reîncearcă după un minut."
    if health.status_code != 200:
        return False, f"Adresa nu pare serverul Cimitir (/api/health a răspuns {health.status_code})."
    try:
        hj = health.json()
    except ValueError:
        return False, "Adresa răspunde, dar nu este serverul Cimitir (răspuns invalid la /api/health)."
    if hj.get("database_status") not in (None, "online"):
        return False, "Serverul pornește, dar baza de date este OFFLINE."
    headers = {"X-Sync-Key": key.strip(), "X-Sync-Client": client_id()}
    try:
        resp = requests.get(f"{base}/api/sync/connection-info", headers=headers, timeout=TIMEOUT)
        if resp.status_code == 404:  # server mai vechi sau mod local
            resp = requests.get(f"{base}/api/sync/ping", headers=headers, timeout=TIMEOUT)
            if resp.status_code == 404:
                return False, "Serverul rulează în mod local: sincronizarea cere CIMITIR_MODE=online."
    except requests.RequestException as exc:
        return False, f"Fără conexiune la server ({type(exc).__name__})."
    if resp.status_code == 401:
        return False, "Cheie de sincronizare invalidă (401). Generează o cheie nouă din /admin/setari."
    if resp.status_code == 429:
        return False, "Prea multe încercări cu cheie greșită; reîncearcă peste câteva minute."
    if resp.status_code >= 400:
        return False, f"Serverul a refuzat conexiunea ({resp.status_code})."
    info = resp.json()
    if not key.strip().startswith(f"{info.get('cemetery', '')}."):
        return False, "Cheia aparține altui cimitir decât cel al serverului."
    notes = list(info.get("warnings") or [])
    if info.get("server_url") and info["server_url"].rstrip("/") != base and info.get("url_configured"):
        notes.append(f"Serverul își declară adresa {info['server_url']}; verifică dacă ai introdus-o pe cea corectă.")
    msg = "Conexiune reușită cu serverul."
    return True, msg + ((" Atenție: " + " ".join(notes)) if notes else "")


def _url(path: str) -> str:
    return f"{get_config()['url']}/api/sync{path}"


def portal_url() -> str:
    cfg = get_config()
    return f"{cfg['url']}/portal/harta" if cfg["url"] else ""


def client_id() -> str:
    """Identificatorul acestei instalări MAIN (stabil, generat o singură dată)."""
    value = (db.get_setting(SET_CLIENT_ID) or "").strip()
    if not value:
        value = "main-" + uuid.uuid4().hex[:10]
        db.set_setting(SET_CLIENT_ID, value)
    return value


def _mark_online(flag: bool) -> None:
    try:
        import entity_sync
        entity_sync.set_online(flag)
    except Exception:
        pass


def _request(method: str, path: str, **kwargs) -> requests.Response:
    cfg = get_config()
    if not url_is_secure(cfg["url"]):
        raise Rejected(0, "Adresa serverului trebuie să fie HTTPS.")
    kwargs.setdefault("timeout", TIMEOUT)
    try:
        extra = kwargs.pop("headers", None) or {}
        resp = requests.request(method, _url(path), headers={"X-Sync-Key": cfg["key"], "X-Sync-Client": client_id(), **extra}, **kwargs)
    except requests.RequestException as exc:
        _mark_online(False)
        raise RetryLater(f"Fără conexiune la server: {exc}")
    if resp.status_code >= 500:
        _mark_online(False)
        raise RetryLater(f"Server indisponibil ({resp.status_code})")
    _mark_online(True)
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail")
        except ValueError:
            detail = resp.text[:200]
        raise Rejected(resp.status_code, detail)
    return resp


# ------------------------------------------------------------------ tabele locale
def ensure_tables() -> None:
    conn = db.get_connection()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS cloud_sync_map (tracking_code TEXT PRIMARY KEY, cerere_id INTEGER, "
                     "pushed_status TEXT DEFAULT '', imported_at TEXT, pushed_version INTEGER DEFAULT 0)")
        conn.execute("CREATE TABLE IF NOT EXISTS cloud_sync_outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, tracking_code TEXT, "
                     "status TEXT, note TEXT, created_at TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS cloud_sync_docs (tracking_code TEXT, doc_id INTEGER, "
                     "PRIMARY KEY (tracking_code, doc_id))")
        for table, col in (("cloud_sync_map", "pushed_version INTEGER DEFAULT 0"), ("cloud_sync_map", "revision INTEGER DEFAULT 0"), ("cloud_sync_outbox", "op_id TEXT"),
                           ("cloud_sync_outbox", "kind TEXT"), ("cloud_sync_outbox", "payload TEXT"),
                           ("cloud_sync_outbox", "attempts INTEGER DEFAULT 0"), ("cloud_sync_outbox", "last_error TEXT"),
                           ("cloud_sync_outbox", "entity TEXT"), ("cloud_sync_outbox", "entity_uid TEXT"),
                           ("cloud_sync_outbox", "action TEXT"), ("cloud_sync_outbox", "base_version INTEGER DEFAULT 0"),
                           ("cloud_sync_outbox", "state TEXT DEFAULT 'pending'"), ("cloud_sync_outbox", "conflict TEXT")):
            existing = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if col.split()[0] not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
    finally:
        conn.close()


def _enqueue(kind: str, payload: Dict[str, Any], tracking_code: str = "", replace_kind: bool = False) -> str:
    ensure_tables()
    op_id = payload.get("op_id") or str(uuid.uuid4())
    payload["op_id"] = op_id
    conn = db.get_connection()
    try:
        if replace_kind:
            conn.execute("DELETE FROM cloud_sync_outbox WHERE kind=?", (kind,))
        conn.execute("INSERT INTO cloud_sync_outbox (tracking_code, status, note, created_at, op_id, kind, payload, attempts) "
                     "VALUES (?,?,?,?,?,?,?,0)", (tracking_code, payload.get("status", ""), "", datetime.now().isoformat(),
                                                  op_id, kind, json.dumps(payload)))
    finally:
        conn.close()
    return op_id


def pending_count() -> int:
    ensure_tables()
    conn = db.get_connection()
    try:
        return int(conn.execute("SELECT COUNT(*) AS n FROM cloud_sync_outbox "
                                "WHERE COALESCE(state,'pending')='pending'").fetchone()["n"])
    finally:
        conn.close()


def pending_operations() -> List[Dict[str, Any]]:
    ensure_tables()
    conn = db.get_connection()
    try:
        return [dict(r) for r in conn.execute("SELECT id, kind, op_id, attempts, last_error, entity, entity_uid, state "
                                              "FROM cloud_sync_outbox ORDER BY id")]
    finally:
        conn.close()


# ------------------------------------------------------------------ conexiune / izolare cimitir
def _bind_cemetery(server_cemetery_id: str) -> None:
    """Prima sincronizare leagă MAIN de cimitirul serverului; o cheie a altui cimitir este refuzată."""
    bound = db.get_setting(SET_BOUND) or ""
    if bound and bound != server_cemetery_id:
        raise Rejected(403, "Cheia aparține altui cimitir decât cel legat de acest MAIN.")
    if not bound:
        local = db.get_setting("cemetery_id") or ""
        conn = db.get_connection()
        try:
            conn.execute("UPDATE users SET cemetery_id=? WHERE COALESCE(cemetery_id,'') IN ('', ?)", (server_cemetery_id, local))
        finally:
            conn.close()
        db.set_setting("cemetery_id", server_cemetery_id)
        db.set_setting(SET_BOUND, server_cemetery_id)


def ping() -> Tuple[bool, str]:
    if not is_configured():
        return False, "Adresa serverului sau cheia de sincronizare lipsesc."
    try:
        info = _request("GET", "/ping").json()
        _bind_cemetery(info["cemetery_id"])
        import entity_sync
        entity_sync.set_capable(bool(info.get("entity_sync")))
        import media_sync
        media_sync.set_capable(info.get("media"))
    except RetryLater as exc:
        return False, str(exc)
    except Rejected as exc:
        return False, "Cheie de sincronizare invalidă (401)." if exc.status == 401 else f"Refuzat: {exc.detail}"
    return True, "Conexiune reușită cu serverul."


def delete_demo_data() -> Tuple[bool, str]:
    return False, "Nu mai este necesar: serverul primește doar proiecția reală a hărții din MAIN."


# ------------------------------------------------------------------ operațiuni din coadă
def _send(op: Dict[str, Any]) -> None:
    payload = json.loads(op["payload"])
    kind = op["kind"]
    if kind == "map":
        _request("POST", "/map", json=payload, timeout=120)
    elif kind == "map_image":
        with open(payload["path"], "rb") as handle:
            _request("POST", "/map/image", files={"file": (os.path.basename(payload["path"]), handle)},
                     data={"map_version": payload["version"], "sha256": payload["sha256"],
                           "width": payload["width"], "height": payload["height"]}, timeout=180)
    elif kind == "status":
        _request("POST", f"/requests/{payload['code']}/status", json=payload)
    elif kind == "ack":
        _request("POST", "/requests/ack", json=payload)
    elif kind == "holders":
        _request("POST", "/holders", json=payload)
    elif kind == "conflict_resolved":
        _request("POST", f"/entities/conflicts/{payload['conflict_op_id']}/resolve", json={"resolution": payload["resolution"]})


def flush_outbox() -> Tuple[bool, List[str]]:
    """Trimite operațiunile în ordine. La eroare temporară se oprește (ordinea se păstrează) și reia mai târziu."""
    ensure_tables()
    lines: List[str] = []
    conflict = False
    conn = db.get_connection()
    try:
        ops = [dict(r) for r in conn.execute("SELECT * FROM cloud_sync_outbox WHERE kind IS NOT NULL AND kind NOT IN ('entity','media') "
                                             "AND COALESCE(state,'pending')='pending' ORDER BY id")]
    finally:
        conn.close()
    for op in ops:
        try:
            _send(op)
            lines.append(f"Trimis: {op['kind']}")
        except RetryLater as exc:
            _mark_failed(op["id"], str(exc))
            lines.append(f"Amânat ({op['kind']}): {exc}")
            return False, lines
        except (Rejected, OSError) as exc:
            if isinstance(exc, Rejected) and exc.status == 409:
                lines.append(f"Respins de server ({op['kind']}): versiune veche/conflict — {exc.detail}")
                _on_conflict(op, exc)
                conflict = True
            else:
                _mark_failed(op["id"], str(exc))
                lines.append(f"Eroare ({op['kind']}): {exc}")
                return False, lines
        _delete_op(op["id"])
    return (not conflict), lines


def _on_conflict(op: Dict[str, Any], exc: Rejected) -> None:
    if op["kind"] in ("map", "map_image"):
        db.set_setting(SET_LAST_ERROR, f"Serverul are o versiune mai nouă a hărții ({exc.detail}). "
                                       "Verifică baza locală; folosește republicarea forțată dacă e intenționat.")


def _mark_failed(op_id: int, error: str) -> None:
    conn = db.get_connection()
    try:
        conn.execute("UPDATE cloud_sync_outbox SET attempts=COALESCE(attempts,0)+1, last_error=? WHERE id=?", (error[:300], op_id))
    finally:
        conn.close()


def _delete_op(op_id: int) -> None:
    conn = db.get_connection()
    try:
        conn.execute("DELETE FROM cloud_sync_outbox WHERE id=?", (op_id,))
    finally:
        conn.close()


# ------------------------------------------------------------------ hartă (MAIN -> server)
def queue_map() -> int:
    """Pune în coadă harta curentă dacă s-a schimbat (nu necesită internet). Întoarce nr. de locuri."""
    cfg = get_config()
    snapshot = cimitir_public.build_snapshot(include_burials=cfg["burials"])
    digest = cimitir_public.snapshot_hash(snapshot)
    if digest != (db.get_setting(SET_QUEUED_HASH) or ""):
        version = int(db.get_setting(SET_MAP_VERSION) or 0) + 1
        _enqueue("map", {**snapshot, "map_version": version, "snapshot_hash": digest}, replace_kind=True)
        db.set_setting(SET_MAP_VERSION, str(version))
        db.set_setting(SET_QUEUED_HASH, digest)
    return len(snapshot["plots"])


def push_map() -> Tuple[bool, str]:
    plots = queue_map()
    ok, lines = flush_outbox()
    return ok, f"Harta: {plots} locuri, versiunea {db.get_setting(SET_MAP_VERSION)}" + ("" if ok else f" ({lines[-1]})")


def republish_preview() -> Dict[str, Any]:
    """Compară harta locală (MAIN) cu proiecția publică de pe server, fără să modifice nimic."""
    info = _request("GET", "/ping").json()
    snapshot = cimitir_public.build_snapshot(include_burials=get_config()["burials"])
    local_hash = cimitir_public.snapshot_hash(snapshot)
    local_version = int(db.get_setting(SET_MAP_VERSION) or 0)
    server_version = int(info.get("map_version") or 0)
    differences = []
    if server_version > local_version:
        differences.append(f"Serverul are versiunea hărții {server_version}, mai nouă decât cea locală ({local_version}).")
    if info.get("plots") is not None and int(info["plots"]) != len(snapshot["plots"]):
        differences.append(f"Locuri: {len(snapshot['plots'])} local față de {info['plots']} pe server.")
    if info.get("sectors") is not None and int(info["sectors"]) != len(snapshot["sectors"]):
        differences.append(f"Sectoare: {len(snapshot['sectors'])} local față de {info['sectors']} pe server.")
    in_sync = bool(info.get("map_hash")) and info.get("map_hash") == local_hash
    return {"server_version": server_version, "local_version": local_version, "in_sync": in_sync,
            "differences": differences, "local_plots": len(snapshot["plots"]), "server_plots": info.get("plots")}


def force_republish() -> None:
    """Retrimite harta și disponibilitatea din MAIN peste proiecția publică (după confirmarea operatorului).

    Datele locale rămân neatinse; se actualizează doar versiunea publicată pe server."""
    info = _request("GET", "/ping").json()
    db.set_setting(SET_MAP_VERSION, str(int(info["map_version"])))
    db.set_setting(SET_QUEUED_HASH, "")
    db.set_setting(SET_QUEUED_IMAGE, "")
    db.set_setting(SET_LAST_ERROR, "")


def _image_size(path: str) -> Tuple[int, int]:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.width, im.height
    except Exception:
        return 0, 0


def queue_map_image() -> bool:
    path = (db.get_setting("map_path") or "").strip()
    if not path or not os.path.isfile(path):
        return False
    with open(path, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    if digest != (db.get_setting(SET_QUEUED_IMAGE) or ""):
        version = int(db.get_setting(SET_IMAGE_VERSION) or 0) + 1
        width, height = _image_size(path)
        _enqueue("map_image", {"path": path, "sha256": digest, "version": version, "width": width, "height": height},
                 replace_kind=True)
        db.set_setting(SET_IMAGE_VERSION, str(version))
        db.set_setting(SET_QUEUED_IMAGE, digest)
    return True


def push_map_image() -> Tuple[bool, str]:
    if not queue_map_image():
        return True, "Fundal hartă: fără imagine."
    ok, lines = flush_outbox()
    return ok, "Fundal hartă: " + ("sincronizat." if ok else lines[-1])


# ------------------------------------------------------------------ cereri
def _local_cerere(code: str) -> Optional[Dict[str, Any]]:
    conn = db.get_connection()
    try:
        row = conn.execute("SELECT * FROM cereri_concesionare WHERE cod_urmarire=?", (code,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _download_document(code: str, cerere_id: int, doc: Dict[str, Any]) -> bool:
    """Descarcă, verifică mărimea/SHA-256, scrie durabil (fsync) și re-verifică copia din media. False = transfer parțial."""
    try:
        resp = _request("GET", f"/requests/{code}/documents/{doc['id']}", timeout=120)
    except (RetryLater, Rejected):
        return False
    content = resp.content
    if (doc.get("size") is not None and len(content) != int(doc["size"])) or \
            (doc.get("sha256") and hashlib.sha256(content).hexdigest() != doc["sha256"]):
        return False
    tmp_dir = os.path.join(db.media_root(), "_cloud_tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    name = f"{code}_{doc['id']}_{(doc.get('caption') or 'document')}"[:120].replace("/", "_").replace("\\", "_")
    tmp_path = os.path.join(tmp_dir, name)
    with open(tmp_path, "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    doc_id = db.add_document("cerere", cerere_id, doc.get("doc_type") or "document", tmp_path,
                             caption=doc.get("caption") or name, copy=True)
    os.remove(tmp_path)
    stored = db.get_document_path(doc_id) if doc_id else None
    if not stored or not os.path.exists(stored):
        return False
    with open(stored, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest() == hashlib.sha256(content).hexdigest()


def pull_cereri() -> Tuple[bool, str, int]:
    ensure_tables()
    try:
        items = _request("GET", "/requests").json()["requests"]
    except (RetryLater, Rejected) as exc:
        return False, f"Cereri: {exc}", 0
    new_count, to_ack, pending_docs, revisions = 0, [], 0, {}
    for item in items:
        code = item["cod_urmarire"]
        local = _local_cerere(code)
        if local is None:
            cerere_id = db.add_cerere_concesionare({k: item.get(k, "") for k in
                                                    ("nume", "prenume", "cnp", "bi_ci", "adresa", "telefon", "email",
                                                     "grad_rudenie", "observatii", "cod_loc")}, cod_urmarire=code)
            if not cerere_id:
                continue
            conn = db.get_connection()
            try:
                conn.execute("UPDATE cereri_concesionare SET tip=?, payload=?, privacy_version=? WHERE id=?",
                             (item.get("tip") or "concesionare", item.get("payload") or None, item.get("privacy_version") or None, cerere_id))
            finally:
                conn.close()
            cimitir_requests.audit("sync", "cloud", "request_imported", code, item.get("tip") or "")
            new_count += 1
        else:
            cerere_id = local["id"]
            conn = db.get_connection()
            try:
                known = conn.execute("SELECT COALESCE(revision,0) AS r FROM cloud_sync_map WHERE tracking_code=?", (code,)).fetchone()
                if known is not None and int(item.get("revision") or 0) > int(known["r"]):
                    obs = ((local.get("observatii") or "") + "\n" + (item.get("observatii") or "")).strip()
                    conn.execute("UPDATE cereri_concesionare SET status='nou', observatii=? WHERE id=?", (obs, cerere_id))
                    conn.execute("UPDATE cloud_sync_map SET pushed_status='nou' WHERE tracking_code=?", (code,))
                    cimitir_requests.audit("sync", "cloud", "completion_received", code, "")
            finally:
                conn.close()
        conn = db.get_connection()
        try:
            conn.execute("INSERT OR IGNORE INTO cloud_sync_map (tracking_code, cerere_id, pushed_status, imported_at) "
                         "VALUES (?,?, 'nou', ?)", (code, cerere_id, datetime.now().isoformat()))
            done = {r["doc_id"] for r in conn.execute("SELECT doc_id FROM cloud_sync_docs WHERE tracking_code=?", (code,))}
        finally:
            conn.close()
        all_ok = True
        for doc in item.get("documents", []):
            if doc["id"] in done:
                continue
            if _download_document(code, cerere_id, doc):
                conn = db.get_connection()
                try:
                    conn.execute("INSERT OR IGNORE INTO cloud_sync_docs VALUES (?,?)", (code, doc["id"]))
                finally:
                    conn.close()
            else:
                all_ok = False
                pending_docs += 1
        if all_ok:
            to_ack.append(code)
            revisions[code] = int(item.get("revision") or 0)
            conn = db.get_connection()
            try:
                conn.execute("UPDATE cloud_sync_map SET revision=? WHERE tracking_code=?", (revisions[code], code))
            finally:
                conn.close()
    if to_ack:
        _enqueue("ack", {"codes": to_ack, "revisions": revisions})
        flush_outbox()
    suffix = f" ({pending_docs} documente de reluat)" if pending_docs else ""
    return pending_docs == 0, f"Cereri noi: {new_count}{suffix}", new_count


def queue_statuses() -> int:
    ensure_tables()
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT m.tracking_code, m.pushed_status, m.pushed_version, c.status, c.review_note FROM cloud_sync_map m "
            "JOIN cereri_concesionare c ON c.id=m.cerere_id WHERE c.status IN ('validat','respins','completari') "
            "AND c.status<>COALESCE(m.pushed_status,'')").fetchall()
        queued = 0
        for r in rows:
            version = int(r["pushed_version"] or 0) + 1
            _enqueue("status", {"code": r["tracking_code"], "status": r["status"], "note": (r["review_note"] or "") if r["status"] in ("respins", "completari") else "",
                                "version": version}, tracking_code=r["tracking_code"])
            conn.execute("UPDATE cloud_sync_map SET pushed_status=?, pushed_version=? WHERE tracking_code=?",
                         (r["status"], version, r["tracking_code"]))
            queued += 1
    finally:
        conn.close()
    return queued


def push_statuses() -> Tuple[bool, str, int]:
    queued = queue_statuses()
    ok, lines = flush_outbox()
    return ok, f"Decizii trimise: {queued}" + ("" if ok else f" ({lines[-1]})"), queued


def push_holders() -> Tuple[bool, str]:
    """Titularii contractelor (hash cu sare) pentru verificarea dreptului asupra locului la cererile online."""
    try:
        salt = _request("GET", "/config").json()["entitlement_salt"]
    except (RetryLater, Rejected) as exc:
        return False, f"Titulari: {exc}"
    holders = cimitir_requests.build_holders(salt)
    digest = hashlib.sha256(json.dumps(holders, sort_keys=True).encode()).hexdigest()
    if digest != (db.get_setting("cloud_holders_hash") or ""):
        version = int(db.get_setting("cloud_holders_version") or 0) + 1
        _enqueue("holders", {"holders": holders, "version": version}, replace_kind=True)
        db.set_setting("cloud_holders_version", str(version))
        db.set_setting("cloud_holders_hash", digest)
    ok, lines = flush_outbox()
    return ok, f"Titulari: {len(holders)}" + ("" if ok else f" ({lines[-1]})")


# ------------------------------------------------------------------ personal (server -> MAIN)
def pull_staff() -> Tuple[bool, str]:
    try:
        data = _request("GET", "/staff").json()
    except (RetryLater, Rejected) as exc:
        return False, f"Personal: {exc} (accesul local se reînnoiește doar la reconectare)"
    stats = cimitir_auth.apply_staff_snapshot(data["staff"], data["lease_hours"])
    return True, (f"Personal: {stats['created']} noi, {stats['updated']} actualizați, {stats['revoked']} revocați; "
                  f"acces offline {data['lease_hours']:g} h")


def sync_entities() -> Tuple[bool, str]:
    """Etapa 6: modificările locale -> API (cu op_id/base_version), apoi modificările serverului -> cache SQLite."""
    import entity_sync
    return entity_sync.sync_entities()


def sync_media() -> Tuple[bool, str]:
    """Etapa 7: documente/fotografii noi -> storage online (prin API), metadate și fișiere de la alte calculatoare -> cache local."""
    import media_sync
    return media_sync.sync_media()


def report_status() -> Tuple[bool, str]:
    """Raportează serverului ultima sincronizare reușită, eroarea curentă și operațiunile locale restante."""
    import socket
    body = {"host": socket.gethostname(), "last_success": db.get_setting(SET_LAST_OK_ISO) or "",
            "last_error": db.get_setting(SET_LAST_ERROR) or "", "pending": pending_count()}
    try:
        import entity_sync
        body["conflicts"] = entity_sync.conflict_count()
    except Exception:
        pass
    try:
        _request("POST", "/status", json=body)
    except (RetryLater, Rejected) as exc:
        return False, f"Raport stare: {exc}"
    return True, "Stare raportată serverului."


# ------------------------------------------------------------------ ciclu complet
def sync_all() -> Dict[str, Any]:
    """Rulează întregul ciclu; nu ridică excepții. Întoarce starea (ultima reușită, eroare, operațiuni restante)."""
    if not is_configured():
        return {"ok": False, "lines": ["Sincronizarea nu este configurată (adresă server + cheie)."], "cereri_noi": 0,
                "pending": 0}
    ensure_tables()
    for queue in (queue_map, queue_map_image, queue_statuses):
        try:
            queue()
        except Exception as exc:
            print(f"[cloud_sync] {queue.__name__}: {exc}")
    report: Dict[str, Any] = {"ok": True, "lines": [], "cereri_noi": 0}
    first_error = ""
    ok, msg = ping()
    report["lines"].append(msg)
    if not ok:
        db.set_setting(SET_LAST_ERROR, msg)
        return {**report, "ok": False, "pending": pending_count()}
    db.set_setting(SET_LAST_ERROR, "")
    for step in (sync_entities, sync_media, push_map, push_map_image, push_holders, pull_cereri, push_statuses, pull_staff):
        try:
            result = step()
        except Exception as exc:
            result = (False, f"{step.__name__}: {exc}")
        ok, msg = result[0], result[1]
        if step is pull_cereri:
            report["cereri_noi"] = result[2]
        report["lines"].append(msg)
        report["ok"] &= ok
        if not ok and not first_error:
            first_error = msg
    if report["ok"]:
        db.set_setting(SET_LAST, datetime.now().strftime("%d.%m.%Y %H:%M"))
        db.set_setting(SET_LAST_OK_ISO, datetime.now().astimezone().isoformat(timespec="seconds"))
        db.set_setting(SET_LAST_ERROR, "")
    elif not (db.get_setting(SET_LAST_ERROR) or ""):
        db.set_setting(SET_LAST_ERROR, first_error[:200])
    report["pending"] = pending_count()
    try:
        report_status()
    except Exception as exc:
        print(f"[cloud_sync] report_status: {exc}")
    return report
