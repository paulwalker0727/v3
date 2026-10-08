"""
security/crypto_utils.py — Helpers de criptare simetrică pentru cache local.

Criptare AES-256-GCM cu cheie derivată din:
    - hardware_id  (device fingerprint)
    - un salt statisc embedded (obfuscat la build)

Fișierele criptate NU pot fi mutate pe alt PC (cheia se re-derivă din hardware).
"""
import base64
import hashlib
import os
import struct
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Salt static — obfuscat cu PyArmor la build. Nu-l face secret prea important;
# ceea ce protejează fișierul este că cheia depinde de hardware_id.
_STATIC_SALT = b"CIMITIR_v1_local_cache_salt_2026"


def _derive_key(hardware_id: str, purpose: str = "cache") -> bytes:
    """
    Cheie AES-256 (32 bytes) derivată din hardware_id + salt + purpose.
    PBKDF2-HMAC-SHA256, 200_000 iterații.
    """
    material = f"{hardware_id}|{purpose}".encode("utf-8")
    return hashlib.pbkdf2_hmac("sha256", material, _STATIC_SALT, iterations=200_000, dklen=32)


def encrypt(plaintext: bytes, hardware_id: str, purpose: str = "cache",
            aad: Optional[bytes] = None) -> bytes:
    """
    Criptează bytes. Format ieșire: [4B len_nonce][nonce][ciphertext+tag]
    """
    key = _derive_key(hardware_id, purpose)
    aes = AESGCM(key)
    nonce = os.urandom(12)
    ct = aes.encrypt(nonce, plaintext, aad)
    return struct.pack(">I", len(nonce)) + nonce + ct


def decrypt(blob: bytes, hardware_id: str, purpose: str = "cache",
            aad: Optional[bytes] = None) -> bytes:
    """Decriptează bytes. Ridică cryptography.exceptions.InvalidTag dacă e modificat."""
    key = _derive_key(hardware_id, purpose)
    aes = AESGCM(key)
    (nlen,) = struct.unpack(">I", blob[:4])
    nonce = blob[4:4 + nlen]
    ct = blob[4 + nlen:]
    return aes.decrypt(nonce, ct, aad)


def encrypt_str(text: str, hardware_id: str, purpose: str = "cache") -> str:
    """Return base64 string — util pentru stocare în fișiere text."""
    return base64.b64encode(encrypt(text.encode("utf-8"), hardware_id, purpose)).decode("ascii")


def decrypt_str(b64: str, hardware_id: str, purpose: str = "cache") -> str:
    return decrypt(base64.b64decode(b64), hardware_id, purpose).decode("utf-8")
