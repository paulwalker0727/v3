"""
security/integrity.py — Verificare hash SHA-256 pentru fișierele critice.

La build (compute_hashes.py) → generează `integrity.json` cu hash-uri.
La runtime → verifică fiecare fișier. Dacă unul e modificat/lipsă → refuză pornirea.

Fișierele critice sunt scripturile Python compilate și fișierele de resurse.
DB-ul (SQLite/SQLCipher) NU se verifică — se modifică la runtime.
"""
import hashlib
import json
import os
import sys
from typing import Dict, List

from .config import INTEGRITY_MANIFEST, ENFORCE_INTEGRITY


class IntegrityError(Exception):
    pass


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _app_base_dir() -> str:
    """Directorul din care rulează aplicația (dezarhivat sau _MEIPASS-ul din PyInstaller)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def compute_manifest(base_dir: str, patterns: List[str]) -> Dict[str, str]:
    """Utilitar de build — parcurge fișierele care se potrivesc și generează hash-urile."""
    import fnmatch
    result: Dict[str, str] = {}
    for root, _dirs, files in os.walk(base_dir):
        for fn in files:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, base_dir).replace("\\", "/")
            if any(fnmatch.fnmatch(rel, p) for p in patterns):
                result[rel] = _sha256_file(full)
    return result


def save_manifest(manifest: Dict[str, str], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "files": manifest}, f, ensure_ascii=False, indent=2)


def check_integrity() -> None:
    """
    Ridică IntegrityError dacă fișierele nu se potrivesc cu manifestul.
    Dacă manifestul lipsește (build fără hashuri) și ENFORCE_INTEGRITY=False → trece.
    """
    base = _app_base_dir()
    manifest_path = os.path.join(base, INTEGRITY_MANIFEST)

    if not os.path.exists(manifest_path):
        if ENFORCE_INTEGRITY and getattr(sys, "frozen", False):
            raise IntegrityError(
                "Manifest de integritate lipsă. Aplicația a fost modificată sau nu a fost "
                "împachetată corect. Contactează suportul."
            )
        return  # dev mode fără manifest → OK

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise IntegrityError(f"Manifest de integritate corupt: {e}")

    files = data.get("files", {})
    for rel, expected in files.items():
        full = os.path.join(base, rel)
        if not os.path.exists(full):
            if ENFORCE_INTEGRITY:
                raise IntegrityError(f"Fișier critic lipsă: {rel}")
            continue
        got = _sha256_file(full)
        if got.lower() != expected.lower():
            if ENFORCE_INTEGRITY:
                raise IntegrityError(
                    f"Fișier critic modificat: {rel}\n"
                    "Aplicația a fost alterată. Reinstalează dintr-o sursă oficială."
                )
