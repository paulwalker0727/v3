"""cimitir_prov_storage.py — verificarea și pregătirea Supabase Storage (bucket PRIVAT) pentru o instanță.

Cheia secretă de Storage e necesară și backendului la runtime, deci se păstrează în seif (per instanță). Verificarea face o scriere/citire/ștergere de probă.
"""
from __future__ import annotations

import secrets
from typing import Any, Dict

from cimitir_prov_store import ProvisionError, redact_text


def _headers(key: str) -> Dict[str, str]:
    return {"apikey": key} if key.startswith("sb_") else {"Authorization": f"Bearer {key}", "apikey": key}


def ensure_and_verify(supabase_url: str, secret_key: str, bucket: str, create: bool = True, timeout: int = 10) -> Dict[str, Any]:
    import requests
    base = supabase_url.rstrip("/") + "/storage/v1"
    h = _headers(secret_key)
    out: Dict[str, Any] = {"bucket": bucket}

    def call(method, path, **kw):
        try:
            return requests.request(method, base + path, headers={**h, **kw.pop("headers", {})}, timeout=timeout, **kw)
        except requests.RequestException as exc:
            raise ProvisionError("STORAGE_UNREACHABLE", "Storage indisponibil: " + redact_text(type(exc).__name__), 502) from None

    r = call("GET", f"/bucket/{bucket}")
    if r.status_code in (400, 404):
        if not create:
            raise ProvisionError("BUCKET_MISSING", "bucketul nu există", 409)
        c = call("POST", "/bucket", json={"id": bucket, "name": bucket, "public": False})
        if c.status_code >= 400:
            raise ProvisionError("BUCKET_CREATE", f"crearea bucketului a eșuat ({c.status_code})", 502)
        out["created"] = True
        r = call("GET", f"/bucket/{bucket}")
    if r.status_code in (401, 403):
        raise ProvisionError("STORAGE_AUTH", "cheia secretă de Storage este refuzată", 401)
    if r.status_code >= 400:
        raise ProvisionError("STORAGE_ERROR", f"Storage a răspuns {r.status_code}", 502)
    if r.json().get("public"):
        raise ProvisionError("BUCKET_PUBLIC", "bucketul este PUBLIC; documentele trebuie să fie private", 409)
    out["private"] = True
    key = f"_provision_probe/{secrets.token_hex(6)}.txt"
    body = secrets.token_bytes(16)
    w = call("POST", f"/object/{bucket}/{key}", data=body, headers={"Content-Type": "application/octet-stream", "x-upsert": "true"})
    g = call("GET", f"/object/authenticated/{bucket}/{key}")
    call("DELETE", f"/object/{bucket}/{key}")
    if w.status_code >= 400 or g.status_code >= 400 or g.content != body:
        raise ProvisionError("STORAGE_RW", "proba de scriere/citire în Storage a eșuat", 502)
    out["write_read_delete"] = True
    return out
