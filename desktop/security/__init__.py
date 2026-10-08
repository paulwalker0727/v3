"""
security/__init__.py — Modul de securitate pentru clientul desktop Cimitir.

Sub-module:
    hardware_id     — fingerprint unic per device (MAC + CPU + Disk)
    license_client  — activare, validare offline JWT, cache criptat
    integrity       — verificare SHA-256 fișiere critice la pornire
    audit_log       — jurnal criptat AES-256 pentru acțiuni sensibile
    db_crypto       — wrapper SQLCipher pentru DB criptată
    crypto_utils    — helpers de criptare (Fernet, AES-GCM)
    settings_store  — cache local criptat (licență, config)
    security_gate   — poarta principală apelată din main.py

Toate secretele (URL server, cheia publică RS256) sunt embedded ca constante
în `security/config.py` — obfuscate cu PyArmor la build.
"""
from .security_gate import run_security_gate, SecurityError  # noqa: F401
from .hardware_id import get_hardware_id  # noqa: F401
