"""Limitare de rată pentru portal și API-ul de sincronizare (persistată în `settings`, deci valabilă pe mai multe procese).

Fereastră glisantă pe (găleată, IP). Antetul X-Forwarded-For nu este citit aici: îl tratează uvicorn, doar dacă
rulează cu `--proxy-headers` și proxy-ul e în `--forwarded-allow-ips`.
"""
from __future__ import annotations

import json
import time
from typing import Dict, List, Optional

from fastapi import HTTPException, Request

import store as cim

MAX_EVENTS = 200
# găleată -> (limită, fereastră în secunde)
LIMITS: Dict[str, tuple] = {
    "register": (10, 3600),
    "cerere": (20, 3600),
    "completari": (20, 3600),
    "login_fail_ip": (20, 900),
    "forgot": (10, 3600),
    "forgot_email": (3, 3600),
    "reset": (10, 3600),
}


def client_ip(request: Request) -> str:
    """Adresa clientului văzută de uvicorn. În spatele unui proxy se pornește uvicorn cu `--proxy-headers
    --forwarded-allow-ips <ip-proxy>` (alege ultima adresă nede încredere); altfel cu `--no-proxy-headers`."""
    return request.client.host if request.client else "necunoscut"


def _key(bucket: str, ip: str) -> str:
    return f"rl:{bucket}:{ip}"


def _events(bucket: str, ip: str, window: int) -> List[float]:
    try:
        raw = json.loads(cim.get_setting(_key(bucket, ip)) or "[]")
    except ValueError:
        raw = []
    cutoff = time.time() - window
    return [t for t in raw if isinstance(t, (int, float)) and t > cutoff]


def retry_after(bucket: str, ip: str) -> int:
    """Secunde până la deblocare (0 = neblocat)."""
    limit, window = LIMITS[bucket]
    events = _events(bucket, ip, window)
    return max(1, int(events[0] + window - time.time()) + 1) if len(events) >= limit else 0


def record(bucket: str, ip: str) -> None:
    _, window = LIMITS[bucket]
    events = _events(bucket, ip, window) + [time.time()]
    cim.set_setting(_key(bucket, ip), json.dumps(events[-MAX_EVENTS:]))


def reject(seconds: int, message: Optional[str] = None) -> HTTPException:
    return HTTPException(status_code=429, detail=message or "Prea multe încercări. Reîncercați mai târziu.",
                         headers={"Retry-After": str(seconds)})


def enforce(request: Request, bucket: str, key: Optional[str] = None) -> None:
    """Refuză cu 429 dacă limita e atinsă; altfel înregistrează cererea curentă (cheie implicită: IP-ul clientului)."""
    ip = key or client_ip(request)
    wait = retry_after(bucket, ip)
    if wait:
        raise reject(wait)
    record(bucket, ip)


def blocked(request: Request, bucket: str) -> None:
    """Refuză cu 429 dacă limita e atinsă, fără a înregistra (pentru eșecuri înregistrate separat)."""
    wait = retry_after(bucket, client_ip(request))
    if wait:
        raise reject(wait)
