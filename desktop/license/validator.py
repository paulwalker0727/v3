"""
Validator licențe Cimitir Desktop.

Combină:
  * license_core  — semnare/verificare Ed25519
  * hwid          — generare HWID unic per calculator
  * verify        — verificare fișier .lic (semnătură + HWID + expirare)
  * public_key    — cheia publică Ed25519 încorporată

Locație licență (per user):
  Windows: %APPDATA%\\CimitirDesktop\\license.key
  Fallback (non-Windows, doar pentru testare): ~/.config/CimitirDesktop/license.key

Format fișier .lic (JSON):
{
  "payload": {
    "hwid": "…",
    "client": "Primăria X",
    "expira": "2027-12-31" | null,
    "emis_la": "2026-01-15T…Z"
  },
  "semnatura": "<base64 Ed25519>"
}
"""
from __future__ import annotations

import os
import sys
import json
import base64
import hashlib
import platform
import subprocess
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timezone, date, timedelta
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization


# ============================================================================
# CHEIA PUBLICĂ Ed25519 — încorporată (cheia privată rămâne doar la emitent)
# ============================================================================
PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEA2RwZkS8rNff79LHyE3ZrK7powwhUNfR3+4p+wlMUgAw=
-----END PUBLIC KEY-----
"""


# ============================================================================
# LOCAȚIE FIȘIER LICENȚĂ
# ============================================================================
APP_FOLDER_NAME = "CimitirDesktop"
LICENSE_FILE_NAME = "license.key"


def _license_dir() -> Path:
    """Returnează directorul unde se salvează licența (creat automat)."""
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Roaming")
        d = Path(base) / APP_FOLDER_NAME
    else:
        # fallback pentru testare pe Linux/Mac (target este Windows)
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        d = Path(base) / APP_FOLDER_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


LICENSE_DIR = _license_dir()
LICENSE_FILE = LICENSE_DIR / LICENSE_FILE_NAME


# ============================================================================
# CRIPTO CORE (Ed25519)
# ============================================================================
def _canonical(payload: dict) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def load_private_key(pem_bytes: bytes) -> Ed25519PrivateKey:
    return serialization.load_pem_private_key(pem_bytes, password=None)


def load_public_key(pem_bytes: bytes) -> Ed25519PublicKey:
    return serialization.load_pem_public_key(pem_bytes)


def sign_payload(payload: dict, priv: Ed25519PrivateKey) -> str:
    return base64.b64encode(priv.sign(_canonical(payload))).decode("ascii")


def verify_payload(payload: dict, semnatura_b64: str, pub: Ed25519PublicKey) -> bool:
    try:
        pub.verify(base64.b64decode(semnatura_b64), _canonical(payload))
        return True
    except Exception:
        return False


def make_license(payload: dict, priv: Ed25519PrivateKey) -> dict:
    return {"payload": payload, "semnatura": sign_payload(payload, priv)}


def is_expired(expira) -> bool:
    if expira is None:
        return False
    try:
        return date.today() > date.fromisoformat(expira)
    except ValueError:
        return True


_PUB = load_public_key(PUBLIC_KEY_PEM)


# ============================================================================
# HWID — SHA-256 din machine-id + baseboard + disk + hostname
# ============================================================================
def _run(cmd: list) -> str:
    try:
        out = subprocess.check_output(
            cmd, stderr=subprocess.DEVNULL, timeout=8, universal_newlines=True
        )
        return out.strip()
    except Exception:
        return ""


# ---- Windows ----
def _win_machine_guid() -> str:
    try:
        import winreg  # type: ignore
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography"
        ) as k:
            val, _ = winreg.QueryValueEx(k, "MachineGuid")
            return str(val)
    except Exception:
        return ""


def _win_baseboard_serial() -> str:
    out = _run([
        "powershell", "-NoProfile", "-Command",
        "(Get-CimInstance Win32_BaseBoard).SerialNumber",
    ])
    if out:
        return out
    out = _run(["wmic", "baseboard", "get", "serialnumber"])
    lines = [l.strip() for l in out.splitlines() if l.strip() and "SerialNumber" not in l]
    return lines[0] if lines else ""


def _win_disk_serial() -> str:
    out = _run([
        "powershell", "-NoProfile", "-Command",
        "(Get-CimInstance Win32_DiskDrive | Select-Object -First 1).SerialNumber",
    ])
    if out:
        return out
    out = _run(["wmic", "diskdrive", "get", "serialnumber"])
    lines = [l.strip() for l in out.splitlines() if l.strip() and "SerialNumber" not in l]
    return lines[0] if lines else ""


# ---- Linux / Mac (fallback pentru testare) ----
def _nix_machine_id() -> str:
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            v = Path(p).read_text().strip()
            if v:
                return v
        except Exception:
            pass
    return _run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"])[:64]


def _nix_baseboard_serial() -> str:
    for p in ("/sys/class/dmi/id/board_serial", "/sys/class/dmi/id/product_uuid"):
        try:
            v = Path(p).read_text().strip()
            if v and v not in ("None", ""):
                return v
        except Exception:
            pass
    return ""


def _nix_disk_serial() -> str:
    out = _run(["lsblk", "-ndo", "SERIAL"])
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    return lines[0] if lines else ""


def collect_components() -> dict:
    if platform.system() == "Windows":
        return {
            "machine_guid": _win_machine_guid(),
            "baseboard": _win_baseboard_serial(),
            "disk": _win_disk_serial(),
        }
    return {
        "machine_guid": _nix_machine_id(),
        "baseboard": _nix_baseboard_serial(),
        "disk": _nix_disk_serial(),
    }


def get_hwid() -> str:
    """Returnează HWID-ul unic (SHA-256 hex) al calculatorului curent."""
    c = collect_components()
    raw = "|".join([c.get("machine_guid", ""), c.get("baseboard", ""), c.get("disk", "")])
    raw += "|" + platform.node()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_nume_pc() -> str:
    return platform.node() or "PC"


# ============================================================================
# VERIFICARE FIȘIER LICENȚĂ
# ============================================================================
@dataclass
class RezultatVerificare:
    ok: bool
    motiv: str = ""
    client: str = ""
    expira: Optional[str] = None
    hwid: str = ""


def _incarca_licenta(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def verifica_fisier(path: Path, hwid_curent: Optional[str] = None) -> RezultatVerificare:
    """Verifică un fișier .lic: semnătură Ed25519 + HWID + expirare."""
    if not path.exists():
        return RezultatVerificare(False, "Fișier licență lipsă")
    try:
        lic = _incarca_licenta(path)
        payload = lic["payload"]
        semnatura = lic["semnatura"]
    except Exception:
        return RezultatVerificare(False, "Fișier licență corupt")

    if not verify_payload(payload, semnatura, _PUB):
        return RezultatVerificare(False, "Semnătură invalidă (licență modificată)")

    hwid_curent = hwid_curent or get_hwid()
    if payload.get("hwid") != hwid_curent:
        return RezultatVerificare(False, "Licență emisă pentru alt calculator")

    if is_expired(payload.get("expira")):
        return RezultatVerificare(False, f"Licență expirată la {payload.get('expira')}")

    return RezultatVerificare(
        ok=True,
        motiv="OK",
        client=payload.get("client", ""),
        expira=payload.get("expira"),
        hwid=hwid_curent,
    )


def salveaza_licenta(path: Path, licenta: dict) -> None:
    """Salvează un dict licență (payload+semnătură) într-un fișier JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(licenta, ensure_ascii=False, indent=2), encoding="utf-8")


def verifica_licenta_curenta() -> RezultatVerificare:
    """Verifică licența salvată la calea implicită (LICENSE_FILE)."""
    return verifica_fisier(LICENSE_FILE)


# ============================================================================
# GATE PRINCIPAL — apelat din main.py
# ============================================================================
def ensure_valid_license(app=None):
    """
    Verifică licența la pornire. Dacă lipsește / e invalidă / expirată,
    afișează dialogul de activare.

    Returnează tuple (ok: bool, info: RezultatVerificare).
    """
    rezultat = verifica_licenta_curenta()
    if rezultat.ok:
        return True, rezultat

    # licența e invalidă / lipsă -> arată dialogul de activare
    try:
        from PySide6.QtWidgets import QApplication, QDialog
        from .activation_dialog import ActivationDialog
    except ImportError as e:
        return False, RezultatVerificare(False, f"PySide6 indisponibil: {e}")

    if app is None:
        app = QApplication.instance() or QApplication(sys.argv)

    dlg = ActivationDialog(motiv=rezultat.motiv)
    dlg.exec()
    if not dlg.activat:
        return False, RezultatVerificare(False, "Activare anulată")

    # re-verifică după activare
    rezultat = verifica_licenta_curenta()
    return rezultat.ok, rezultat


__all__ = [
    "PUBLIC_KEY_PEM",
    "LICENSE_DIR",
    "LICENSE_FILE",
    "load_private_key",
    "load_public_key",
    "sign_payload",
    "verify_payload",
    "make_license",
    "is_expired",
    "get_hwid",
    "get_nume_pc",
    "collect_components",
    "verifica_fisier",
    "verifica_licenta_curenta",
    "salveaza_licenta",
    "ensure_valid_license",
    "RezultatVerificare",
]
