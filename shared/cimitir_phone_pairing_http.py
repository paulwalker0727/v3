"""cimitir_phone_pairing_http.py — API HTTPS pentru Phone Pairing v1 (FastAPI), peste `PhonePairingService`. Fără UI.

  POST /api/v1/phone-pairing/codes                 Binding <cheie MAIN>   {ttl?}                       -> cod (afișat o singură dată)
  POST /api/v1/phone-pairing/redeem                fără autentificare     {code, device_name?, client_id?} -> binding_key (o singură dată)
  GET  /api/v1/phone-pairing/phones                Binding <cheie MAIN>
  POST /api/v1/phone-pairing/phones/{binding_uid}/revoke   Binding <cheie MAIN>

Sursa pentru rate limiting: IP-ul conexiunii (socket). X-Forwarded-For se ia în calcul DOAR dacă IP-ul conexiunii aparține unui proxy declarat explicit în
CIMITIR_PAIRING_TRUSTED_PROXIES (listă de IP/CIDR separate prin virgulă, ex. `10.0.0.5,172.16.0.0/12`); atunci clientul real = prima intrare de la dreapta care NU e proxy de încredere.
Fără setare, cu configurație invalidă (intrare nevalidă, CIDR /0) sau cu X-Forwarded-For malformat, header-ele forward sunt IGNORATE și se folosește IP-ul socket (rate limiting rămâne activ).
Alte header-e (X-Real-IP, Forwarded, ...) nu sunt folosite niciodată. IPv6: găleata e pe /64. Răspunsurile de eroare nu conțin codul/cheia.
"""
from __future__ import annotations

import ipaddress
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


ENV_TRUSTED = "CIMITIR_PAIRING_TRUSTED_PROXIES"
_MAX_XFF_LEN, _MAX_XFF_ENTRIES = 1024, 20
_warned_invalid = False


def parse_trusted_proxies(raw: str):
    """Listă de rețele (IP/CIDR) de proxy-uri de încredere; ValueError la orice intrare invalidă sau prea largă (/0)."""
    nets = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            if (raw or "").strip():
                raise ValueError("intrare goală")
            continue
        net = ipaddress.ip_network(part, strict=True)
        if net.prefixlen == 0:
            raise ValueError("rețea prea largă")
        nets.append(net)
    return tuple(nets)


def trusted_proxies():
    """Rețelele de încredere din mediu; configurația invalidă => () (fail-safe: header-ele forward se ignoră), cu avertisment o singură dată."""
    global _warned_invalid
    try:
        return parse_trusted_proxies(os.environ.get(ENV_TRUSTED, ""))
    except ValueError:
        if not _warned_invalid:
            _warned_invalid = True
            log.warning("%s este invalid: header-ele forward sunt ignorate, rate limiting folosește IP-ul conexiunii", ENV_TRUSTED)
        return ()


def _ip(value: str):
    try:
        ip = ipaddress.ip_address((value or "").strip())
    except ValueError:
        return None
    return ip.ipv4_mapped if ip.version == 6 and ip.ipv4_mapped else ip


def _bucket_ip(ip) -> str:
    return str(ip) if ip.version == 4 else str(ipaddress.ip_network(f"{ip}/64", strict=False))


def resolve_client_ip(peer: str, xff_headers, trusted) -> str:
    """IP-ul folosit pentru rate limiting. `peer` = IP-ul conexiunii; `xff_headers` = valorile X-Forwarded-For; `trusted` = rețele de încredere."""
    p = _ip(peer)
    if p is None:
        return peer or "unknown"
    if not trusted or not any(p in n for n in trusted):
        return _bucket_ip(p)
    joined = ",".join(xff_headers or [])
    entries = [e.strip() for e in joined.split(",")]
    if not joined.strip() or len(joined) > _MAX_XFF_LEN or len(entries) > _MAX_XFF_ENTRIES or not all(entries):
        return _bucket_ip(p)
    for e in reversed(entries):
        ip = _ip(e)
        if ip is None:
            return _bucket_ip(p)
        if not any(ip in n for n in trusted):
            return _bucket_ip(ip)
    return _bucket_ip(p)


def client_source(request: Request) -> str:
    peer = request.client.host if request.client else ""
    return resolve_client_ip(peer, request.headers.getlist("x-forwarded-for"), trusted_proxies())


_HOST_RE = __import__("re").compile(r"^[A-Za-z0-9.-]{1,253}(:\d{1,5})?$")


def public_base_url(request: Request) -> str:
    """Adresa publică pentru QR/linkuri: 1) CIMITIR_PUBLIC_URL; 2) X-Forwarded-Proto/Host DOAR de la un proxy declarat în CIMITIR_PAIRING_TRUSTED_PROXIES (Cloudflare/nginx/Caddy);
    3) adresa cererii. Anteturile de la clienți nedeclarați sunt ignorate (nu se poate falsifica linkul din QR)."""
    from urllib.parse import urlparse
    u = (os.environ.get("CIMITIR_PUBLIC_URL") or "").strip().rstrip("/")
    p = urlparse(u)
    if p.scheme in ("http", "https") and p.hostname and not p.username:
        return u
    peer = _ip(request.client.host if request.client else "")
    trusted = trusted_proxies()
    if peer is not None and trusted and any(peer in n for n in trusted):
        host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
        proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
        if host and _HOST_RE.match(host) and proto in ("http", "https"):
            return f"{proto}://{host}"
    return str(request.base_url).rstrip("/")


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

    async def codes(_request: Request, key: str):
        return await run(lambda: service.list_codes(key))

    async def revoke_code(request: Request, key: str):
        b = await fih._json_body(request, 1024)
        return await run(lambda: service.revoke_code(key, b.get("code_id") if isinstance(b, dict) else None))

    route("POST", "/codes", issue, 201)
    route("GET", "/codes", codes)
    route("POST", "/codes/revoke", revoke_code)
    route("POST", "/redeem", redeem, 201, auth=False)
    route("GET", "/phones", phones)
    route("POST", "/phones/{buid}/revoke", revoke)
    return router
