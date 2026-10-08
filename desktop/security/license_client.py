"""
security/license_client.py — Client pentru serverul de licențe Cimitir.

Flow:
    1. La prima pornire → nu există license cache → cere key user → activate()
    2. La porniri ulterioare → citește cache → validează OFFLINE token JWT
        - dacă e valid și nu a expirat → GO
        - dacă expiră curând → încearcă heartbeat online (opțional)
        - dacă a expirat cache-ul → cere validare online
    3. Dacă server-ul e offline și cache-ul e ok → GO (grace period)

Toate erorile sunt LicenseError cu mesaje user-friendly în română.
"""
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import jwt
import requests

from . import settings_store
from .config import (
    LICENSE_SERVER_URL,
    LICENSE_PUBLIC_KEY_PEM,
    LICENSE_OFFLINE_GRACE_DAYS,
    APP_VERSION,
)
from .hardware_id import get_hardware_id, get_device_name


class LicenseError(Exception):
    """Ridicat când licența nu poate fi validată. Aplicația trebuie să refuze să pornească."""


class ActivationRequired(Exception):
    """Ridicat când nu există licență cache-uită și userul trebuie să introducă o cheie."""


def _now() -> int:
    return int(time.time())


def _verify_local_token(token: str, hardware_id: str) -> Dict[str, Any]:
    """
    Verifică token-ul JWT semnat de server cu cheia publică RS256.
    Ridică LicenseError la eroare.
    """
    try:
        payload = jwt.decode(
            token,
            LICENSE_PUBLIC_KEY_PEM,
            algorithms=["RS256"],
            options={"require": ["exp", "iat", "license_key", "hardware_id"]},
        )
    except jwt.ExpiredSignatureError:
        raise LicenseError("Token-ul licenței a expirat. Verifică conexiunea la internet.")
    except jwt.InvalidTokenError as e:
        raise LicenseError(f"Token licență invalid: {e}")

    if payload.get("hardware_id") != hardware_id:
        raise LicenseError(
            "Licența nu este autorizată pentru acest calculator. "
            "Contactează suportul pentru transfer licență."
        )
    return payload


def _post(path: str, body: dict, timeout: float = 6.0) -> requests.Response:
    url = LICENSE_SERVER_URL.rstrip("/") + path
    return requests.post(url, json=body, timeout=timeout,
                         headers={"User-Agent": f"Cimitir/{APP_VERSION}"})


# -----------------------------------------------------------------------------
# ACTIVARE — apelată o singură dată, la prima pornire / după logout
# -----------------------------------------------------------------------------
def activate_license(license_key: str) -> Dict[str, Any]:
    """
    Trimite cheia + hardware_id la server. Salvează token-ul în cache local criptat.
    """
    key = (license_key or "").strip().upper()
    if not key.startswith("CIM-") or len(key.replace("-", "")) < 15:
        raise LicenseError("Format cheie invalid. Cheia trebuie să înceapă cu 'CIM-'.")

    hw = get_hardware_id()
    body = {
        "license_key": key,
        "hardware_id": hw,
        "device_name": get_device_name(),
        "app_version": APP_VERSION,
    }
    try:
        r = _post("/api/public/activate", body, timeout=10.0)
    except requests.RequestException as e:
        raise LicenseError(
            "Nu m-am putut conecta la serverul de licențe.\n"
            "Verifică conexiunea la internet și încearcă din nou.\n\n"
            f"Detalii tehnice: {e}"
        )

    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", "Cerere respinsă")
        except Exception:
            detail = r.text or "Cerere respinsă"
        raise LicenseError(detail)

    data = r.json()
    token = data.get("license_token")
    if not token:
        raise LicenseError("Răspuns invalid de la server (token lipsă)")

    payload = _verify_local_token(token, hw)

    cache = {
        "license_key": key,
        "license_token": token,
        "activated_at": _now(),
        "last_online_check": _now(),
        "hardware_id": hw,
        "payload": payload,
        "server_license_info": data.get("license", {}),
    }
    settings_store.save_license_cache(cache)
    return cache


# -----------------------------------------------------------------------------
# VALIDARE — apelată la fiecare pornire
# -----------------------------------------------------------------------------
def validate_license(check_online: bool = True) -> Dict[str, Any]:
    """
    Returnează payload-ul licenței dacă e ok.
    Ridică ActivationRequired dacă nu există cache.
    Ridică LicenseError dacă licența nu e validă.
    """
    cache = settings_store.load_license_cache()
    if not cache:
        raise ActivationRequired("Nu există licență activă pe acest calculator.")

    hw = get_hardware_id()
    if cache.get("hardware_id") != hw:
        settings_store.clear_license_cache()
        raise LicenseError("Cache licență invalidat (device changed).")

    token = cache.get("license_token")
    try:
        payload = _verify_local_token(token, hw)
    except LicenseError as e:
        # token expirat/invalid → încearcă re-activare online
        if check_online:
            try:
                fresh = activate_license(cache["license_key"])
                return fresh["payload"]
            except Exception:
                pass
        # dacă grace period neexpirat → permitem run
        last_ok = cache.get("last_online_check", 0)
        offline_days = (_now() - last_ok) / 86400
        if offline_days < LICENSE_OFFLINE_GRACE_DAYS:
            # returnăm cache-ul chiar dacă tokenul e expirat, dar în grace period
            return cache.get("payload", {})
        raise e

    # Token valid local — dacă avem net, întrebăm rapid serverul (non-blocant)
    if check_online:
        try:
            r = _post("/api/public/validate", {
                "license_key": cache["license_key"], "hardware_id": hw,
            }, timeout=3.0)
            if r.ok:
                data = r.json()
                if not data.get("valid", False):
                    reason = data.get("reason", "unknown")
                    settings_store.clear_license_cache()
                    _MSG = {
                        "not_found": "Licență inexistentă",
                        "revoked": "Licența a fost revocată de administrator",
                        "expired": "Licența a expirat",
                        "activation_revoked": "Activarea a fost revocată pentru acest device",
                        "not_activated": "Activarea nu mai există",
                    }
                    raise LicenseError(_MSG.get(reason, "Licență invalidă"))
                cache["last_online_check"] = _now()
                settings_store.save_license_cache(cache)
        except LicenseError:
            raise
        except requests.RequestException:
            # server offline → păstrăm cache-ul valid local
            pass

    return payload


def heartbeat() -> None:
    """Ping periodic către server. Fail silent."""
    cache = settings_store.load_license_cache()
    if not cache:
        return
    try:
        _post("/api/public/heartbeat", {
            "license_key": cache["license_key"],
            "hardware_id": cache["hardware_id"],
            "app_version": APP_VERSION,
        }, timeout=3.0)
    except Exception:
        pass


def get_current_license_info() -> Optional[Dict[str, Any]]:
    """Returnează info despre licența curentă pentru fereastra About / status bar."""
    cache = settings_store.load_license_cache()
    if not cache:
        return None
    payload = cache.get("payload", {})
    info = cache.get("server_license_info", {})
    exp = payload.get("license_expires_at") or payload.get("exp")
    return {
        "license_key": cache.get("license_key"),
        "customer_name": payload.get("customer_name") or info.get("customer_name", ""),
        "license_type": payload.get("license_type") or info.get("license_type", ""),
        "features": payload.get("features", []),
        "expires_at": datetime.fromtimestamp(exp, tz=timezone.utc).strftime("%d.%m.%Y") if exp else "Nelimitat",
        "activated_at": datetime.fromtimestamp(cache.get("activated_at", 0), tz=timezone.utc).strftime("%d.%m.%Y") if cache.get("activated_at") else "",
    }


def deactivate() -> None:
    """Șterge cache-ul local. Util pentru transfer licență."""
    settings_store.clear_license_cache()
