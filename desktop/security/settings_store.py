"""
security/settings_store.py — Cache local criptat pentru licență și config sensibil.

Fișier: %LOCALAPPDATA%\\Cimitir\\license.dat  (Windows)
        ~/.local/share/Cimitir/license.dat    (Linux)
        ~/Library/Application Support/Cimitir/license.dat  (macOS)

Conținut criptat cu AES-256-GCM, cheia derivată din hardware_id.
"""
import json
import os
import sys
from typing import Any, Dict, Optional

from . import crypto_utils
from .hardware_id import get_hardware_id


def _data_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        p = os.path.join(base, "Cimitir")
    elif sys.platform == "darwin":
        p = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Cimitir")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
        p = os.path.join(base, "Cimitir")
    os.makedirs(p, exist_ok=True)
    return p


LICENSE_FILE = os.path.join(_data_dir(), "license.dat")


def save_license_cache(payload: Dict[str, Any]) -> None:
    hw = get_hardware_id()
    plain = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    blob = crypto_utils.encrypt(plain, hw, purpose="license")
    tmp = LICENSE_FILE + ".tmp"
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, LICENSE_FILE)


def load_license_cache() -> Optional[Dict[str, Any]]:
    if not os.path.exists(LICENSE_FILE):
        return None
    hw = get_hardware_id()
    try:
        with open(LICENSE_FILE, "rb") as f:
            blob = f.read()
        plain = crypto_utils.decrypt(blob, hw, purpose="license")
        return json.loads(plain.decode("utf-8"))
    except Exception:
        # fișier modificat / mutat pe alt PC / cheie invalidă → invalidăm cache-ul
        try:
            os.remove(LICENSE_FILE)
        except Exception:
            pass
        return None


def clear_license_cache() -> None:
    try:
        if os.path.exists(LICENSE_FILE):
            os.remove(LICENSE_FILE)
    except Exception:
        pass
