"""
security/password_kdf.py — Migrare hash parole SHA-256 → PBKDF2-HMAC-SHA256.

Formate suportate:
    - Legacy:  "<salt_hex>$<sha256_hex>"                  (16-byte salt + SHA256)
    - Modern:  "pbkdf2$<iterations>$<salt_hex>$<hash_hex>" (200k iter, SHA-256)

`verify(password, stored)` detectează automat formatul.
`hash_password(password)` returnează ÎNTOTDEAUNA formatul modern.

Integrarea în auth.py existent:
    - la login: dacă verify() e OK și formatul e legacy → re-hash cu formatul nou
      și update în DB (transparent pentru user).
"""
import hashlib
import hmac
import secrets

DEFAULT_ITERATIONS = 200_000
SALT_BYTES = 16


def hash_password(password: str, iterations: int = DEFAULT_ITERATIONS) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=32)
    return f"pbkdf2${iterations}${salt.hex()}${dk.hex()}"


def verify(password: str, stored: str) -> bool:
    if not stored or not password:
        return False
    try:
        if stored.startswith("pbkdf2$"):
            _, iters_s, salt_hex, hash_hex = stored.split("$", 3)
            iters = int(iters_s)
            dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                     bytes.fromhex(salt_hex), iters, dklen=32)
            return hmac.compare_digest(dk.hex(), hash_hex)
        # legacy: salt$sha256
        if "$" in stored:
            salt_hex, sha_hex = stored.split("$", 1)
            h = hashlib.sha256()
            h.update(bytes.fromhex(salt_hex))
            h.update(password.encode("utf-8"))
            return hmac.compare_digest(h.hexdigest(), sha_hex)
    except Exception:
        return False
    return False


def needs_upgrade(stored: str) -> bool:
    """True dacă hash-ul e legacy (SHA-256) — trebuie re-hash-uit la modern la login."""
    return bool(stored) and not stored.startswith("pbkdf2$")
