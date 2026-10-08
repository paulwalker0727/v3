"""Pasul 6 — MAIN: „Continuă pe telefon” / „Încărcare document de pe telefon” (nucleu fără GUI, testabil).

MAIN vorbește DOAR cu FastAPI (X-Sync-Key, HTTPS). Nu are acces la PostgreSQL/Supabase și nu primește chei de storage.
  1. `request_token(tip, id_local)` -> cere serverului un token temporar legat de UID-ul entității; serverul răspunde cu URL-ul public
     (CIMITIR_PUBLIC_URL) și un cod QR. Dacă serverul nu cunoaște încă entitatea (404) se rulează o sincronizare a entităților și se reia o dată.
  2. `token_status(tid)` — în așteptare / folosit / expirat.
  3. `fetch_uploaded(tid)` — după upload: sincronizare media (Etapa 7) => documentul apare în `documents` local, cu ACELAȘI UID de entitate.
SQLite-ul local rămâne cache/offline/outbox; sursa de adevăr este PostgreSQL pe server.
"""
from __future__ import annotations

import base64
from typing import Any, Dict, Optional, Tuple

import cloud_sync
import database as db

ENTITY_TABLES = {"inhumat": "inhumati", "contract": "contracte", "concesionar": "concesionari", "plata": "plati"}
SUPPORTED = ("loc", "concesionar", "contract", "inhumat", "plata", "aviz")


def local_uid(entity_type: str, entity_id: Any) -> str:
    """UID-ul local al entității (același format ca pe server: media_service.entity_uid_for)."""
    conn = db.get_connection()
    try:
        ref = str(entity_id)
        if entity_type == "loc":
            r = conn.execute("SELECT uid FROM locuri_de_veci WHERE cod_loc = ?", (ref,)).fetchone()
        elif entity_type in ENTITY_TABLES and ref.isdigit():
            r = conn.execute(f"SELECT uid FROM {ENTITY_TABLES[entity_type]} WHERE id = ?", (int(ref),)).fetchone()
        elif entity_type == "aviz" and ref.isdigit():
            a = conn.execute("SELECT a.nr_aviz, l.uid FROM amenajari_avize a LEFT JOIN locuri_de_veci l ON l.cod_loc = a.cod_loc "
                             "WHERE a.id = ?", (int(ref),)).fetchone()
            return f"aviz:{a['uid'] or ''}:{a['nr_aviz']}" if a else f"aviz:{ref}"
        else:
            r = None
        return r["uid"] if r and r["uid"] else f"{entity_type}:{ref}"
    finally:
        conn.close()


def request_token(entity_type: str, entity_id: Any, issued_by: str = "main", ttl_minutes: int = 10, max_uses: int = 1) -> Dict[str, Any]:
    """Cere un token. Ridică cloud_sync.Rejected / RetryLater (cu mesaj clar) dacă serverul refuză sau nu e accesibil."""
    if entity_type not in SUPPORTED:
        raise cloud_sync.Rejected(422, "Tip de entitate neacceptat pentru telefon.")
    body = {"entity_type": entity_type, "entity_uid": local_uid(entity_type, entity_id), "issued_by": issued_by,
            "ttl_minutes": ttl_minutes, "max_uses": max_uses}
    try:
        return cloud_sync._request("POST", "/mobile/tokens", json=body).json()
    except cloud_sync.Rejected as exc:
        if exc.status != 404:
            raise
    cloud_sync.sync_entities()                      # serverul nu are încă entitatea: o trimitem, apoi reluat o singură dată
    return cloud_sync._request("POST", "/mobile/tokens", json=body).json()


def token_status(tid: str) -> Dict[str, Any]:
    return cloud_sync._request("GET", f"/mobile/tokens/{tid}").json()


def revoke(tid: str) -> None:
    cloud_sync._request("DELETE", f"/mobile/tokens/{tid}")


def fetch_uploaded(tid: str) -> Tuple[Dict[str, Any], str]:
    """Starea tokenului + (dacă s-a încărcat ceva) sincronizarea media, ca documentul să apară în MAIN."""
    st = token_status(tid)
    msg = ""
    if st.get("uses"):
        _ok, msg = cloud_sync.sync_media()
    return st, msg


def qr_png_bytes(b64: str) -> Optional[bytes]:
    try:
        return base64.b64decode(b64) if b64 else None
    except Exception:
        return None


def local_documents(entity_type: str, entity_id: Any):
    return db.list_documents(entity_type, str(entity_id))
