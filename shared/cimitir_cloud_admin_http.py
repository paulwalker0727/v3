"""cimitir_cloud_admin_http.py — rutele administrative `/api/v1/admin/*` (sesiune de administrator real; fără chei de sincronizare, fără utilizatorul postgres).

Sesiunea: cookie HttpOnly + SameSite=Strict (UI web) SAU `Authorization: Bearer <token>` (MAIN/scripturi). Cererile care modifică și vin pe cookie cer headerul
`X-Cimitir-Admin: 1` (nu poate fi trimis cross-site fără preflight CORS, care nu e permis). Răspunsurile nu conțin hash-uri, parole sau chei.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import cimitir_cloud_admin as ad
import cimitir_phone_pairing_http as pph

log = logging.getLogger("cimitir.cloud_admin")
COOKIE = "cimitir_admin"
_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}
_LOCAL = ("localhost", "127.0.0.1", "::1")


def _ok(result: Any = None, status: int = 200) -> JSONResponse:
    return JSONResponse({"ok": True, "result": result}, status_code=status, headers=_HEADERS)


def _err(exc: BaseException) -> JSONResponse:
    if isinstance(exc, ad.AdminError):
        return JSONResponse({"ok": False, "code": exc.code, "message": str(exc)}, status_code=exc.status, headers=_HEADERS)
    log.error("eroare internă admin: %s", type(exc).__name__)
    return JSONResponse({"ok": False, "code": "INTERNAL", "message": "eroare internă"}, status_code=500, headers=_HEADERS)


async def _body(request: Request) -> Dict[str, Any]:
    raw = await request.body()
    if not raw:
        return {}
    try:
        data = __import__("json").loads(raw)
    except ValueError:
        raise ad.BadRequest("JSON invalid") from None
    if not isinstance(data, dict):
        raise ad.BadRequest("corp invalid")
    return data


def _token(request: Request):
    """(token, din_cookie)"""
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip(), False
    return request.cookies.get(COOKIE), True


def create_router(admin: ad.AdminService) -> APIRouter:
    router = APIRouter(prefix="/api/v1/admin")

    def route(method: str, path: str, fn: Callable[..., Any], auth: bool = True, status: int = 200):
        async def handler(request: Request):
            try:
                body = await _body(request) if method == "POST" else {}
                ip = pph.client_source(request)
                ctx: Dict[str, Any] = {"ip": ip, "q": dict(request.query_params), "request": request}
                if auth:
                    token, via_cookie = _token(request)
                    if not token:
                        raise ad.AuthRequired("sesiune lipsă sau expirată")
                    if via_cookie and method != "GET" and request.headers.get("x-cimitir-admin") != "1":
                        raise ad.Forbidden("antet CSRF lipsă")
                    ctx["token"] = token
                    ctx["identity"] = await anyio.to_thread.run_sync(admin.principal, token)
                result = await anyio.to_thread.run_sync(lambda: fn(body, ctx))
                if isinstance(result, JSONResponse):
                    return result
                return _ok(result, status)
            except Exception as exc:  # noqa: BLE001
                return _err(exc)
        router.add_api_route(path, handler, methods=[method])

    def _secure(request: Request) -> bool:
        return (request.url.hostname or "") not in _LOCAL

    def login(b, c):
        res = admin.login(b.get("email"), b.get("password"), c["ip"])
        resp = _ok({"token": res["token"], "expires_in": res["expires_in"]})
        resp.set_cookie(COOKIE, res["token"], max_age=res["expires_in"], httponly=True, samesite="strict", secure=_secure(c["request"]), path="/api/v1/admin")
        return resp

    def logout(b, c):
        admin.logout(c.get("token"))
        resp = _ok({"logged_out": True})
        resp.delete_cookie(COOKIE, path="/api/v1/admin")
        return resp

    def recover(b, c):
        admin.recover(b.get("token"), b.get("email"), b.get("new_password"), c["ip"])
        return {"recovered": True}

    route("GET", "/status", lambda b, c: admin.status(), auth=False)
    route("POST", "/bootstrap", lambda b, c: admin.bootstrap(
        b.get("token"), b.get("email"), b.get("password"), b.get("display_name", ""), b.get("organization_uid"), b.get("organization_name", ""),
        b.get("cemetery_uid"), b.get("cemetery_name", ""), c["ip"]), auth=False, status=201)
    route("POST", "/login", login, auth=False)
    route("POST", "/recover", recover, auth=False)
    route("POST", "/logout", logout)
    route("GET", "/me", lambda b, c: admin.me(c["identity"]))
    route("POST", "/password", lambda b, c: admin.change_password(c["identity"], c["token"], b.get("old_password"), b.get("new_password")) or {"changed": True})
    route("POST", "/codes", lambda b, c: admin.issue_code(c["identity"], b.get("organization_uid"), b.get("cemetery_uid"), b.get("kind"), b.get("ttl")), status=201)
    route("GET", "/codes", lambda b, c: admin.list_codes(c["identity"], c["q"].get("organization_uid", ""), c["q"].get("cemetery_uid")))
    route("POST", "/codes/revoke", lambda b, c: admin.revoke_code(c["identity"], b.get("organization_uid"), b.get("code_id")))
    route("GET", "/devices", lambda b, c: admin.list_devices(c["identity"], c["q"].get("organization_uid", "")))
    route("POST", "/devices/revoke", lambda b, c: admin.revoke_device(c["identity"], b.get("organization_uid"), b.get("binding_uid")))
    route("POST", "/cemeteries", lambda b, c: admin.create_cemetery(c["identity"], b.get("organization_uid"), b.get("name"), b.get("cemetery_uid")), status=201)
    return router
