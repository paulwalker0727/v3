"""
security/hardware_id.py — Fingerprint hardware stabil (multi-platform).

Combină:
    - UUID sistem (uuid.getnode) — MAC-ul primului adaptor real
    - CPU ID (via wmic/sysctl/proc)
    - Disk serial (via wmic/hdparm)
    - Machine ID (Linux: /etc/machine-id, Windows: MachineGuid, macOS: IOPlatformUUID)

Rezultatul e un hash SHA-256 hex (64 caractere) — fingerprint-ul TREBUIE să fie
identic la fiecare pornire pe același PC, dar diferit pe alt PC.

Fingerprint-ul e trimis DOAR la activare online, apoi cache-uit local.
Nu conține date personale — este un hash irreversibil.
"""
import hashlib
import platform
import subprocess
import sys
import uuid
from typing import List


def _run(cmd: List[str], timeout: float = 4.0) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           creationflags=(0x08000000 if sys.platform == "win32" else 0))  # CREATE_NO_WINDOW
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _machine_id() -> str:
    """Machine ID persistent, dependent de sistem (nu se schimbă la reinstalări soft)."""
    if sys.platform == "win32":
        # MachineGuid din HKLM\SOFTWARE\Microsoft\Cryptography
        out = _run(["reg", "query", r"HKLM\SOFTWARE\Microsoft\Cryptography", "/v", "MachineGuid"])
        for line in out.splitlines():
            if "MachineGuid" in line:
                parts = line.split()
                if parts:
                    return parts[-1]
        return ""
    if sys.platform == "darwin":
        out = _run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"])
        for line in out.splitlines():
            if "IOPlatformUUID" in line:
                return line.split('=')[-1].strip().strip('"')
        return ""
    # linux
    try:
        with open("/etc/machine-id", "r") as f:
            return f.read().strip()
    except Exception:
        pass
    try:
        with open("/var/lib/dbus/machine-id", "r") as f:
            return f.read().strip()
    except Exception:
        return ""


def _cpu_info() -> str:
    if sys.platform == "win32":
        out = _run(["wmic", "cpu", "get", "ProcessorId", "/value"])
        for line in out.splitlines():
            if "ProcessorId=" in line:
                return line.split("=", 1)[1].strip()
        return platform.processor() or ""
    if sys.platform == "darwin":
        out = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        return out or platform.processor()
    return platform.processor() or ""


def _disk_serial() -> str:
    if sys.platform == "win32":
        out = _run(["wmic", "diskdrive", "get", "SerialNumber", "/value"])
        serials = []
        for line in out.splitlines():
            if "SerialNumber=" in line:
                v = line.split("=", 1)[1].strip()
                if v:
                    serials.append(v)
        return "|".join(sorted(serials))
    if sys.platform == "darwin":
        out = _run(["system_profiler", "SPStorageDataType"])
        for line in out.splitlines():
            if "Volume UUID" in line:
                return line.split(":", 1)[1].strip()
        return ""
    # linux — necesită root pentru hdparm; folosim /sys ca alternativă neinvazivă
    try:
        import os
        for dev in sorted(os.listdir("/sys/block")):
            if dev.startswith(("sd", "nvme", "vd")):
                p = f"/sys/block/{dev}/device/serial"
                try:
                    with open(p, "r") as f:
                        s = f.read().strip()
                        if s:
                            return s
                except Exception:
                    continue
    except Exception:
        pass
    return ""


def _mac_address() -> str:
    n = uuid.getnode()
    # ignoră MAC-uri random (bit 41 = multicast) — uuid.getnode() setează bit 40 dacă nu găsește real
    if (n >> 40) & 0x01:
        return ""
    return f"{n:012x}"


def get_hardware_id() -> str:
    """Returnează fingerprint SHA-256 hex (64 caractere) — stabil per device."""
    parts = [
        f"os={platform.system()}",
        f"machine_id={_machine_id()}",
        f"cpu={_cpu_info()}",
        f"disk={_disk_serial()}",
        f"mac={_mac_address()}",
        # nod: NU includem hostname (userul îl poate schimba)
    ]
    joined = "|".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def get_device_name() -> str:
    """Numele afișabil al device-ului (hostname). Trimis la activare pentru identificare."""
    try:
        return platform.node() or "unknown"
    except Exception:
        return "unknown"


if __name__ == "__main__":
    print(f"Hardware ID: {get_hardware_id()}")
    print(f"Device name: {get_device_name()}")
