"""macOS: adaptări aplicate la pornire, fără a modifica `desktop/license` (modul protejat, neschimbat față de baza Windows).

- Fișierul de licență (validator._license_dir, ramura non-Windows) stă în $XDG_CONFIG_HOME/CimitirDesktop -> îl mutăm în ~/CIMITIR-MAC/date/config.
- Amprenta dispozitivului: ramura implicită non-Windows din validator folosește primele 64 de caractere din `ioreg` (conțin contoare
  variabile) și numele calculatorului (se schimbă pe Mac) -> amprentă instabilă. Aici: IOPlatformUUID + serie + UUID-ul volumului de sistem.
Pe Windows/Linux `apply()` nu face nimic.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

_IOREG = "/usr/sbin/ioreg"
_DISKUTIL = "/usr/sbin/diskutil"
_APPLIED = False


def _run(cmd: list) -> str:
    try:
        return subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=8, universal_newlines=True).strip()
    except Exception:
        return ""


def _ioreg_value(key: str) -> str:
    for line in _run([_IOREG, "-rd1", "-c", "IOPlatformExpertDevice"]).splitlines():
        if f'"{key}"' in line and "=" in line:
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def collect_components() -> dict:
    disk = ""
    for line in _run([_DISKUTIL, "info", "/"]).splitlines():
        if "Volume UUID" in line and ":" in line:
            disk = line.split(":", 1)[1].strip()
            break
    return {"machine_guid": _ioreg_value("IOPlatformUUID"), "baseboard": _ioreg_value("IOPlatformSerialNumber"), "disk": disk}


def get_hwid() -> str:
    """Același format ca validator.get_hwid (SHA-256 hex), dar fără numele calculatorului."""
    c = collect_components()
    raw = "|".join([c.get("machine_guid", ""), c.get("baseboard", ""), c.get("disk", "")]) + "|"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def apply() -> bool:
    global _APPLIED
    if sys.platform != "darwin":
        return False
    if _APPLIED:
        return True
    shared = str(Path(__file__).resolve().parents[1] / "shared")
    if shared not in sys.path:
        sys.path.insert(0, shared)
    import cimitir_platform
    os.environ["XDG_CONFIG_HOME"] = str(cimitir_platform.mac_subdir("config"))
    import license as license_pkg
    from license import validator
    validator.collect_components = collect_components
    validator.get_hwid = get_hwid
    license_pkg.get_hwid = get_hwid
    _APPLIED = True
    return True
