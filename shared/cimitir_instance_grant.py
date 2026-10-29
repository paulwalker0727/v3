"""cimitir_instance_grant.py — autorizare de inițializare a primului administrator Cloud, fără BOOTSTRAP_TOKEN în mediul containerului.

Dreptul de administrare asupra INSTANȚEI = posesia cheii private „proprietar instanță” (Ed25519, DISTINCTĂ de cheia de semnare a licențelor).
Serverul Cloud cunoaște doar cheia PUBLICĂ (`CIMITIR_INSTANCE_ADMIN_PUBKEY`) și identificatorul instanței (`CIMITIR_INSTANCE_ID`), setate o singură dată la
provisionarea instanței. MAIN (sau scriptul operatorului) semnează un „grant” scurt, legat de instanță, cu expirare ≤ 15 min; serverul îl verifică și îl acceptă
doar cât timp instanța nu are administrator. După inițializare, orice grant este inutil (starea „bootstrap_completed” e persistentă în PostgreSQL), deci nu poate fi reutilizat.
Licența `.lic` NU poate crea un administrator: nu conține și nu deblochează această cheie. Grantul și cheia privată nu se loghează.
Format grant: `grant.<b64url(payload JSON)>.<b64url(semnătură Ed25519 peste payload)>`.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import time
from typing import Any, Dict, Optional

ENV_PUBKEY, ENV_INSTANCE_ID = "CIMITIR_INSTANCE_ADMIN_PUBKEY", "CIMITIR_INSTANCE_ID"
PREFIX = "grant."
PURPOSE = "cloud-admin-bootstrap"
MAX_TTL = 900
SKEW = 30
KEY_SECRET_PREFIX = "instance-owner/"


class GrantError(Exception):
    """Mesaj generic, fără valori secrete."""


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _crypto():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
    except ImportError:  # pragma: no cover
        raise GrantError("modulul `cryptography` lipsește: autorizarea prin grant nu este disponibilă") from None
    return serialization, Ed25519PrivateKey, Ed25519PublicKey


def generate_keypair() -> Dict[str, str]:
    """(privată, publică) în base64url; privata se păstrează DOAR în SecretStore/fișier protejat, publica merge în CIMITIR_INSTANCE_ADMIN_PUBKEY."""
    ser, Priv, _Pub = _crypto()
    k = Priv.generate()
    raw_priv = k.private_bytes(ser.Encoding.Raw, ser.PrivateFormat.Raw, ser.NoEncryption())
    raw_pub = k.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)
    return {"private_key": _b64(raw_priv), "public_key": _b64(raw_pub)}


def public_from_private(private_key: str) -> str:
    ser, Priv, _Pub = _crypto()
    k = Priv.from_private_bytes(_unb64(private_key))
    return _b64(k.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw))


def issue_grant(private_key: str, instance_id: str, ttl: int = MAX_TTL, now: Optional[float] = None) -> str:
    if not instance_id or not isinstance(instance_id, str):
        raise GrantError("identificatorul instanței lipsește")
    ttl = max(30, min(int(ttl), MAX_TTL))
    _ser, Priv, _Pub = _crypto()
    t = int(now if now is not None else time.time())
    payload = json.dumps({"v": 1, "purpose": PURPOSE, "aud": instance_id, "iat": t, "exp": t + ttl, "jti": secrets.token_urlsafe(12)},
                         sort_keys=True, separators=(",", ":")).encode("utf-8")
    sig = Priv.from_private_bytes(_unb64(private_key)).sign(payload)
    return PREFIX + _b64(payload) + "." + _b64(sig)


def is_grant(token: Any) -> bool:
    return isinstance(token, str) and token.startswith(PREFIX)


def configured(env=None) -> bool:
    env = os.environ if env is None else env
    return bool((env.get(ENV_PUBKEY) or "").strip() and (env.get(ENV_INSTANCE_ID) or "").strip())


def verify_grant(token: str, env=None, now: Optional[float] = None) -> Dict[str, Any]:
    """Verifică semnătura, scopul, audiența (instanța), expirarea și durata maximă. Mesaje generice (fără valori)."""
    env = os.environ if env is None else env
    if not configured(env):
        raise GrantError("autorizarea prin grant nu este configurată pe această instanță")
    try:
        _ser, _Priv, Pub = _crypto()
        _p, body, sig = token.strip().split(".")
        payload = _unb64(body)
        Pub.from_public_bytes(_unb64(env[ENV_PUBKEY].strip())).verify(_unb64(sig), payload)
        data = json.loads(payload)
    except GrantError:
        raise
    except Exception:  # noqa: BLE001  (format/semnătură invalide: nu se detaliază)
        raise GrantError("grant invalid") from None
    t = now if now is not None else time.time()
    if data.get("v") != 1 or data.get("purpose") != PURPOSE or data.get("aud") != env[ENV_INSTANCE_ID].strip():
        raise GrantError("grant invalid pentru această instanță")
    if not (isinstance(data.get("exp"), int) and isinstance(data.get("iat"), int)) or data["exp"] - data["iat"] > MAX_TTL:
        raise GrantError("grant invalid")
    if t > data["exp"] + SKEW or t < data["iat"] - SKEW:
        raise GrantError("grant expirat")
    return {"jti": data.get("jti", ""), "exp": data["exp"]}


# ------------------------------------------------------------------ MAIN: cheia proprietarului instanței în SecretStore
def secret_name(instance_id: str) -> str:
    import re
    return KEY_SECRET_PREFIX + re.sub(r"[^A-Za-z0-9_-]", "_", instance_id)[:100]


def fingerprint(public_key: str) -> str:
    import hashlib
    return hashlib.sha256(public_key.strip().encode("ascii", "ignore")).hexdigest()[:16]
