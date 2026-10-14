"""cimitir_field_inbox_http.py — API HTTPS pentru Field Inbox v1 (FastAPI), peste `FieldInboxService`. Fără UI.

Autentificare: `Authorization: Binding <cheie>` (aceeași schemă ca restul API-ului cloud v1; cheia nu apare în URL/log/răspuns). Tenantul (organizație/cimitir) se derivă din legătură.
Clientul (telefon / MAIN) nu primește niciodată parole PostgreSQL, SUPABASE_SECRET_KEY, service_role, parola cimitir_cp_app sau credențiale de cleanup — doar URL-uri semnate scurte (<= 300 s).

  POST /api/v1/field-inbox/items                                   {idempotency_key, item_type, payload, grave_uid?, target_uid?, work_stage?}
  POST /api/v1/field-inbox/items/{uid}/files                       corp = octeții fișierului; Content-Type = PDF/JPEG/PNG; X-File-Uid, X-File-Name, [X-File-Sha256]
  GET  /api/v1/field-inbox/me                                      -> {cemetery_uid, device_type} al legăturii (telefonul își verifică cimitirul)
  GET  /api/v1/field-inbox/items?status=&item_type=&limit=&offset=
  GET  /api/v1/field-inbox/items/{uid}
  GET  /api/v1/field-inbox/items/{uid}/files/{file_uid}/url?ttl=    (MAIN, sau telefonul pentru itemurile proprii PENDING)
  POST /api/v1/field-inbox/items/{uid}/accept | /reject {reason} | /imported                (doar MAIN)
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Any, Callable, Optional

import anyio
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

import cimitir_cloud_control as cc
import cimitir_cloud_wire as wire
import cimitir_field_inbox as fi
import cimitir_field_storage as fs
import cimitir_storage as st

log = logging.getLogger("cimitir.field_inbox_http")
PREFIX = wire.API + "/field-inbox"
_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}
_ERRORS = [
    (wire.Unauthenticated, 401, "UNAUTHENTICATED"), (cc.ScopeError, 404, "SCOPE_MISMATCH"), (cc.PermissionDenied, 403, "FORBIDDEN"),
    (fi.IdempotencyConflict, 409, "IDEMPOTENCY_CONFLICT"), (fi.InvalidTransition, 409, "INVALID_TRANSITION"), (fi.InvalidItem, 400, "ITEM_INVALID"),
    (fs.HashMismatch, 400, "HASH_MISMATCH"), (fs.BadContent, 415, "CONTENT_REJECTED"), (fs.Conflict, 409, "STORAGE_CONFLICT"), (fs.NotReady, 409, "NOT_READY"),
    (fs.FieldStorageError, 400, "STORAGE_INVALID"), (st.StorageError, 502, "STORAGE_UNAVAILABLE"),
]


def _error(exc: BaseException, secret: str = "") -> JSONResponse:
    for cls, status, code in _ERRORS:
        if isinstance(exc, cls):
            msg = str(exc).replace(secret, "***") if secret else str(exc)
            return JSONResponse({"ok": False, "code": code, "message": msg}, status_code=status, headers=_HEADERS)
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return JSONResponse({"ok": False, "code": "REQUEST_INVALID", "message": "cerere invalidă"}, status_code=400, headers=_HEADERS)
    log.error("eroare internă: %s", type(exc).__name__)     # doar clasa: mesajul/traceback-ul pot conține date sensibile
    return JSONResponse({"ok": False, "code": "INTERNAL", "message": "eroare internă"}, status_code=500, headers=_HEADERS)


def _binding_key(request: Request) -> str:
    scheme, _, secret = (request.headers.get("authorization") or "").partition(" ")
    if scheme != "Binding" or not secret.strip():
        raise wire.Unauthenticated("autentificare lipsă")
    return secret.strip()


async def _body_limited(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise fs.BadContent("fișier prea mare")
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            raise fs.BadContent("fișier prea mare")
    return bytes(buf)


async def _json_body(request: Request, limit: int = 128 * 1024) -> Any:
    import json
    return json.loads(await _body_limited(request, limit))


def create_router(service: fi.FieldInboxService) -> APIRouter:
    router = APIRouter(prefix=PREFIX)

    def route(method: str, path: str, fn: Callable[[Request, str, str], Any], status: int = 200):
        async def handler(request: Request):
            secret = ""
            try:
                secret = _binding_key(request)
                try:
                    a = await anyio.to_thread.run_sync(lambda: service.authenticate(secret))
                except cc.ScopeError:
                    raise wire.Unauthenticated("legătură invalidă") from None
                result = await fn(request, secret, a["cemetery_uid"])
            except Exception as exc:  # noqa: BLE001
                return _error(exc, secret)
            code = status(result) if callable(status) else status
            return JSONResponse({"ok": True, "result": result}, status_code=code, headers=_HEADERS)
        router.add_api_route(path, handler, methods=[method])

    async def run(fn):
        return await anyio.to_thread.run_sync(fn)

    async def create(request: Request, key: str, cem: str):
        b = await _json_body(request)
        if not isinstance(b, dict):
            raise ValueError("corp invalid")
        return await run(lambda: service.create_item(key, cem, b.get("idempotency_key"), b.get("item_type"), b.get("payload"), b.get("grave_uid"), b.get("target_uid"), b.get("work_stage")))

    async def upload(request: Request, key: str, cem: str):
        data = await _body_limited(request, fs.MAX_BYTES)
        h = request.headers
        mime = (h.get("content-type") or "").split(";")[0].strip().lower()
        declared = (h.get("x-file-sha256") or "").lower()
        if declared and not hmac.compare_digest(declared, hashlib.sha256(data).hexdigest()):
            raise fs.HashMismatch("SHA-256 declarat diferit de conținut")
        return await run(lambda: service.attach_file(key, cem, request.path_params["uid"], h.get("x-file-uid", ""), h.get("x-file-name", "") or "fisier", mime, data))

    async def lst(request: Request, key: str, cem: str):
        q = request.query_params
        return await run(lambda: service.list_items(key, cem, q.get("status"), q.get("item_type"), int(q.get("limit", 50)), int(q.get("offset", 0))))

    async def detail(request: Request, key: str, cem: str):
        return await run(lambda: service.get_item(key, cem, request.path_params["uid"]))

    async def url(request: Request, key: str, cem: str):
        return await run(lambda: service.file_url(key, cem, request.path_params["uid"], request.path_params["fuid"], int(request.query_params.get("ttl", 60))))

    async def accept(request: Request, key: str, cem: str):
        return await run(lambda: service.accept(key, cem, request.path_params["uid"]))

    async def reject(request: Request, key: str, cem: str):
        b = await _json_body(request)
        return await run(lambda: service.reject(key, cem, request.path_params["uid"], b.get("reason") if isinstance(b, dict) else None))

    async def imported(request: Request, key: str, cem: str):
        return await run(lambda: service.mark_imported(key, cem, request.path_params["uid"]))

    async def me(request: Request, key: str, cem: str):
        a = await run(lambda: service.authenticate(key))
        return {"cemetery_uid": a["cemetery_uid"], "device_type": a["device_type"]}

    route("GET", "/me", me)
    route("POST", "/items", create, lambda r: 200 if r["replayed"] else 201)
    route("POST", "/items/{uid}/files", upload, lambda r: 200 if r["replayed"] else 201)
    route("GET", "/items", lst)
    route("GET", "/items/{uid}", detail)
    route("GET", "/items/{uid}/files/{fuid}/url", url)
    route("POST", "/items/{uid}/accept", accept)
    route("POST", "/items/{uid}/reject", reject)
    route("POST", "/items/{uid}/imported", imported)
    return router


ENV_CORS = "CIMITIR_FIELD_INBOX_CORS_ORIGINS"
_CORS_HEADERS = ["Authorization", "Content-Type", "X-File-Uid", "X-File-Name", "X-File-Sha256"]


def cors_origins(raw: Optional[str] = None) -> list:
    """Originile portalului telefonului (listă explicită, separată prin virgulă); `*` și valorile fără schemă sunt ignorate."""
    raw = os.environ.get(ENV_CORS, "") if raw is None else raw
    out = []
    for o in (x.strip().rstrip("/") for x in raw.split(",")):
        if o and o != "*" and o.startswith(("http://", "https://")):
            out.append(o)
    return out


def create_app(service: Optional[fi.FieldInboxService] = None, origins: Optional[list] = None) -> FastAPI:
    """Aplicația cloud Field Inbox; fără `service` îl construiește din mediu (CIMITIR_CP_DATABASE_URL, SUPABASE_*)."""
    app = FastAPI(title="Cimitir Field Inbox", docs_url=None, redoc_url=None, openapi_url=None)
    allowed = cors_origins() if origins is None else origins
    if allowed:
        from starlette.middleware.cors import CORSMiddleware
        app.add_middleware(CORSMiddleware, allow_origins=allowed, allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=_CORS_HEADERS, max_age=600)
    import cimitir_phone_pairing as pp
    import cimitir_phone_pairing_http as pph
    service = service or fi.build_service_from_env()
    app.include_router(create_router(service))
    app.include_router(pph.create_router(pp.PhonePairingService(service)))

    @app.get(wire.API + "/field-inbox-health")
    def health():
        return {"ok": True, "version": 1}

    return app
