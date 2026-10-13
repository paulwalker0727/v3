"""cimitir_phone_pairing_http.py — API HTTPS pentru Phone Pairing v1 (FastAPI), peste `PhonePairingService`. Fără UI.

  POST /api/v1/phone-pairing/codes                 Binding <cheie MAIN>   {ttl?}                       -> cod (afișat o singură dată)
  POST /api/v1/phone-pairing/redeem                fără autentificare     {code, device_name?, client_id?} -> binding_key (o singură dată)
  GET  /api/v1/phone-pairing/phones                Binding <cheie MAIN>
  POST /api/v1/phone-pairing/phones/{binding_uid}/revoke   Binding <cheie MAIN>

Sursa pentru rate limiting: IP-ul conexiunii; în spatele a N proxy-uri de încredere se setează CIMITIR_PAIRING_TRUSTED_PROXIES=N (se ia a N-a intrare de la dreapta din X-Forwarded-For).
Fără setare, X-Forwarded-For este IGNORAT (nu poate fi falsificat pentru a ocoli limita). Răspunsurile de eroare nu conțin codul/cheia.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import cimitir_cloud_control as cc
import cimitir_cloud_wire as wire
import cimitir_field_inbox_http as fih
import cimitir_phone_pairing as pp

log = logging.getLogger("cimitir.phone_pairing_http")
PREFIX = wire.API + "/phone-pairing"
_PAIR_ERRORS = [(pp.TooManyOpenCodes, 429, "TOO_MANY_OPEN_CODES"), (pp.PairingRejected, 401, "PAIRING_REJECTED")]


def _error(exc: BaseException, secret: str = "") -> JSONResponse:
    if isinstance(exc, pp.RateLimited):
        return JSONResponse({"ok": False, "code": "RATE_LIMITED", "message": str(exc)}, status_code=429, headers={**fih._HEADERS, "Retry-After": str(exc.retry_after)})
    for cls, status, code in _PAIR_ERRORS:
        if isinstance(exc, cls):
            return JSONResponse({"ok": False, "code": code, "message": str(exc)}, status_code=status, headers=fih._HEADERS)
    return fih._error(exc, secret)


def client_source(request: Request) -> str:
    peer = request.client.host if request.client else ""
    hops = os.environ.get("CIMITIR_PAIRING_TRUSTED_PROXIES", "0")
    if hops.isdigit() and int(hops) > 0:
        parts = [p.strip() for p in (request.headers.get("x-forwarded-for") or "").split(",") if p.strip()]
        if len(parts) >= int(hops):
            return parts[-int(hops)]
    return peer


def create_router(service: pp.PhonePairingService) -> APIRouter:
    router = APIRouter(prefix=PREFIX)

    def route(method: str, path: str, fn, status: int = 200, auth: bool = True):
        async def handler(request: Request):
            secret = ""
            try:
                if auth:
                    secret = fih._binding_key(request)
                    try:
                        await run(lambda: service.inbox.authenticate(secret))
                    except cc.ScopeError:
                        raise wire.Unauthenticated("legătură invalidă") from None
                result = await fn(request, secret)
            except Exception as exc:  # noqa: BLE001
                return _error(exc, secret)
            return JSONResponse({"ok": True, "result": result}, status_code=status, headers=fih._HEADERS)
        router.add_api_route(path, handler, methods=[method])

    async def run(fn):
        return await anyio.to_thread.run_sync(fn)

    async def issue(request: Request, key: str):
        raw = await fih._body_limited(request, 4096)
        b = __import__("json").loads(raw) if raw.strip() else {}
        if not isinstance(b, dict):
            raise ValueError("corp invalid")
        ttl = b.get("ttl", pp.DEFAULT_TTL)
        if isinstance(ttl, bool) or not isinstance(ttl, int):
            raise ValueError("ttl invalid")
        return await run(lambda: service.issue_code(key, ttl))

    async def redeem(request: Request, _key: str):
        b = await fih._json_body(request, 4096)
        if not isinstance(b, dict):
            raise ValueError("corp invalid")
        cid: Optional[str] = b.get("client_id") if isinstance(b.get("client_id"), str) else None
        return await run(lambda: service.redeem(b.get("code"), b.get("device_name"), cid, client_source(request)))

    async def phones(_request: Request, key: str):
        return await run(lambda: service.list_phones(key))

    async def revoke(request: Request, key: str):
        return await run(lambda: service.revoke_phone(key, request.path_params["buid"]))

    route("POST", "/codes", issue, 201)
    route("POST", "/redeem", redeem, 201, auth=False)
    route("GET", "/phones", phones)
    route("POST", "/phones/{buid}/revoke", revoke)
    return router
