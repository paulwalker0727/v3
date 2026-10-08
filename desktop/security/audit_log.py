"""
security/audit_log.py — Jurnal de audit criptat local, conform GDPR.

Loguri stocate în %LOCALAPPDATA%\\Cimitir\\audit\\ (rotație zilnică).
Fiecare linie e criptată cu AES-256-GCM (cheia derivată din hardware_id).

API:
    log(actor, action, entity_type=None, entity_id=None, meta=None)
    read_recent(limit=100) → listă intrări decriptate

Acțiuni logate (recomandat):
    - login / logout
    - create/update/delete: loc, inhumat, concesionar, contract, plată
    - export DB, backup, restore
    - încercări de license activation
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import crypto_utils
from .hardware_id import get_hardware_id


def _audit_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        p = os.path.join(base, "Cimitir", "audit")
    elif sys.platform == "darwin":
        p = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Cimitir", "audit")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
        p = os.path.join(base, "Cimitir", "audit")
    os.makedirs(p, exist_ok=True)
    return p


def _file_for_today() -> str:
    d = datetime.now(timezone.utc).strftime("%Y%m%d")
    return os.path.join(_audit_dir(), f"audit-{d}.log.enc")


def log(actor: str, action: str, entity_type: Optional[str] = None,
        entity_id: Optional[Any] = None, meta: Optional[Dict[str, Any]] = None) -> None:
    """Adaugă o intrare criptată în jurnalul zilei. Silent la eroare (nu vrem să blocăm app-ul)."""
    try:
        entry = {
            "t": time.time(),
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": actor or "",
            "action": action,
            "entity_type": entity_type or "",
            "entity_id": str(entity_id) if entity_id is not None else "",
            "meta": meta or {},
        }
        plaintext = json.dumps(entry, ensure_ascii=False).encode("utf-8")
        blob = crypto_utils.encrypt(plaintext, get_hardware_id(), purpose="audit")
        # 4 bytes length prefix, apoi blob-ul → permite append multiple entries
        import struct
        with open(_file_for_today(), "ab") as f:
            f.write(struct.pack(">I", len(blob)))
            f.write(blob)
    except Exception:
        pass


def read_recent(limit: int = 100, date: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Citește intrări recente (implicit din ziua curentă). `date` în format YYYYMMDD.
    Returnează listă descresetor (cele mai recente primele).
    """
    import struct
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y%m%d")
    path = os.path.join(_audit_dir(), f"audit-{date}.log.enc")
    if not os.path.exists(path):
        return []
    hw = get_hardware_id()
    entries: List[Dict[str, Any]] = []
    try:
        with open(path, "rb") as f:
            data = f.read()
        pos = 0
        while pos < len(data):
            (blen,) = struct.unpack(">I", data[pos:pos + 4])
            pos += 4
            blob = data[pos:pos + blen]
            pos += blen
            try:
                plaintext = crypto_utils.decrypt(blob, hw, purpose="audit")
                entries.append(json.loads(plaintext.decode("utf-8")))
            except Exception:
                continue
    except Exception:
        return []
    entries.reverse()
    return entries[:limit]


def list_log_files() -> List[str]:
    """Listă fișiere de log disponibile (fără extensia .enc)."""
    d = _audit_dir()
    if not os.path.isdir(d):
        return []
    return sorted([f for f in os.listdir(d) if f.startswith("audit-") and f.endswith(".log.enc")])
