"""
security/db_crypto.py — Wrapper SQLCipher pentru DB criptată.

Folosește pysqlcipher3 (extensie SQLite cu criptare AES-256).
Cheia DB e derivată din hardware_id + license_key (nu e stocată nicăieri).

INSTALARE (Windows):
    pip install pysqlcipher3

    Dacă instalarea eșuează (nu găsește DLL), soluții:
      A) Folosește `sqlcipher-binary` (wheel pre-built):
          pip install sqlcipher-binary
      B) Instalează SQLCipher separat (Zetetic sau BinAO) și adaugă .dll în folder

Migrare DB necriptat → criptat:
    - La primul start cu SQLCipher, dacă există cimitir.db necriptat →
      creează cimitir.encrypted.db, migrează datele, șterge originalul.
"""
import hashlib
import os
import shutil
import sys
from typing import Optional

# import condiționat — dacă lipsește pysqlcipher, folosim sqlite3 normal (mod dev)
try:
    from pysqlcipher3 import dbapi2 as sqlcipher  # noqa: F401
    HAS_SQLCIPHER = True
except Exception:
    HAS_SQLCIPHER = False


def _derive_db_key(hardware_id: str, license_key: str) -> str:
    """
    Cheie hex pentru SQLCipher (PBKDF2 pe hardware+license).
    Returnează 64 caractere hex (256 biți).
    """
    material = f"{hardware_id}|{license_key}|cimitir-db-v1".encode("utf-8")
    salt = b"CIMITIR_DB_SALT_v1_2026"
    return hashlib.pbkdf2_hmac("sha256", material, salt, iterations=200_000).hex()


def get_encrypted_connection(db_path: str, hardware_id: str, license_key: str):
    """
    Deschide o conexiune SQLCipher către db_path.
    Setează PRAGMA key + configurații de compatibilitate.
    """
    if not HAS_SQLCIPHER:
        raise RuntimeError(
            "pysqlcipher3 nu este instalat. "
            "Rulează: pip install pysqlcipher3  (sau folosește sqlcipher-binary)"
        )
    conn = sqlcipher.connect(db_path, timeout=15.0, isolation_level=None)
    key = _derive_db_key(hardware_id, license_key)
    # PRAGMA key trebuie să fie prima instrucțiune după deschidere
    conn.execute(f"PRAGMA key = \"x'{key}'\"")
    conn.execute("PRAGMA cipher_page_size = 4096")
    conn.execute("PRAGMA kdf_iter = 200000")
    conn.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA512")
    conn.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def migrate_plaintext_to_encrypted(plain_db: str, encrypted_db: str,
                                    hardware_id: str, license_key: str) -> None:
    """
    Migrează un cimitir.db (sqlite standard) → cimitir.encrypted.db (SQLCipher).
    Folosește comanda SQLCipher ATTACH → sqlcipher_export → DETACH.
    """
    if not HAS_SQLCIPHER:
        raise RuntimeError("pysqlcipher3 nu este instalat.")
    if not os.path.exists(plain_db):
        raise FileNotFoundError(plain_db)

    key = _derive_db_key(hardware_id, license_key)

    # deschide DB-ul necriptat (fără PRAGMA key)
    conn = sqlcipher.connect(plain_db, isolation_level=None)
    try:
        conn.execute(f"ATTACH DATABASE '{encrypted_db}' AS encrypted KEY \"x'{key}'\"")
        conn.execute("SELECT sqlcipher_export('encrypted')")
        conn.execute("DETACH DATABASE encrypted")
    finally:
        conn.close()


def is_database_encrypted(db_path: str, hardware_id: str, license_key: str) -> bool:
    """Testează dacă un fișier .db este un SQLCipher valid cu cheia curentă."""
    if not HAS_SQLCIPHER or not os.path.exists(db_path):
        return False
    try:
        conn = get_encrypted_connection(db_path, hardware_id, license_key)
        conn.execute("SELECT count(*) FROM sqlite_master")
        conn.close()
        return True
    except Exception:
        return False


def ensure_encrypted_db(plain_db: str, encrypted_db: str,
                        hardware_id: str, license_key: str) -> str:
    """
    Asigură existența DB-ului criptat.
      - Dacă encrypted_db există și e valid → returnează calea lui.
      - Altfel, dacă plain_db există → migrează.
      - Altfel, creează un DB gol criptat.
    """
    if is_database_encrypted(encrypted_db, hardware_id, license_key):
        return encrypted_db
    if os.path.exists(plain_db):
        migrate_plaintext_to_encrypted(plain_db, encrypted_db, hardware_id, license_key)
        # backup DB-ul plain, apoi îl ștergem
        backup = plain_db + ".backup_before_encrypt"
        try:
            shutil.copy2(plain_db, backup)
            os.remove(plain_db)
        except Exception:
            pass
        return encrypted_db
    # niciun DB — creează gol criptat
    conn = get_encrypted_connection(encrypted_db, hardware_id, license_key)
    conn.close()
    return encrypted_db
