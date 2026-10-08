"""cimitir_secretstore.py — stocarea secretelor (cheia de sync a legăturii) în afara SQLite/loguri/backup-uri obișnuite.

Backend-uri:
- `DpapiSecretStore` — Windows, singurul backend comercial/production;
- `TestEncryptedSecretStore` — EXCLUSIV TEST: fișiere criptate autentificat (Fernet = AES-128-CBC + HMAC-SHA256) cu o cheie EFEMERĂ ținută doar în
  memorie (sau transmisă explicit unui proces copil de test). Fără cheie, fișierele sunt nedecriptabile;
- `MemorySecretStore` — EXCLUSIV TEST: nu persistă nimic.
Ambele backend-uri de test cer `CIMITIR_ALLOW_TEST_SECRETSTORE=1` și sunt REFUZATE când `CIMITIR_COMMERCIAL=1` sau aplicația e înghețată
(PyInstaller). Nu mai există niciun backend base64/plaintext. Fără backend explicit pe un sistem non-Windows: `SecretStoreUnavailable`.
SQLite păstrează doar numele (referința); valoarea nu apare în baze, loguri sau rapoarte.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Callable, Dict, Optional

ENV_BACKEND = "CIMITIR_SECRET_STORE"      # "dpapi" | "test-encrypted" | "memory"
ENV_COMMERCIAL = "CIMITIR_COMMERCIAL"      # "1" = interzice orice backend de test
ENV_ALLOW_TEST = "CIMITIR_ALLOW_TEST_SECRETSTORE"  # "1" = permite backend-urile EXCLUSIV de test
ENV_TEST_KEY = "CIMITIR_TEST_SECRET_KEY"   # cheia Fernet efemeră, transmisă doar proceselor copil de test
_NAME_RE = re.compile(r"^[A-Za-z0-9_./-]{1,200}$")
_ENTROPY = b"cimitir.secretstore.v1"


class SecretStoreError(Exception):
    pass


class SecretStoreUnavailable(SecretStoreError):
    pass


def _check_name(name: str) -> str:
    if not _NAME_RE.match(name or "") or ".." in name:
        raise SecretStoreError(f"nume de secret invalid: {name!r}")
    return name


def _require_test_only(label: str) -> None:
    if os.environ.get(ENV_COMMERCIAL) == "1" or getattr(sys, "frozen", False):
        raise SecretStoreUnavailable(f"{label} este interzis în configurația comercială/production")
    if os.environ.get(ENV_ALLOW_TEST) != "1":
        raise SecretStoreUnavailable(f"{label} este exclusiv pentru teste: setează {ENV_ALLOW_TEST}=1")


class SecretStore:
    def set(self, name: str, value: str) -> None:
        raise NotImplementedError

    def get(self, name: str) -> Optional[str]:
        raise NotImplementedError

    def delete(self, name: str) -> None:
        raise NotImplementedError

    def exists(self, name: str) -> bool:
        return self.get(name) is not None


class MemorySecretStore(SecretStore):
    """EXCLUSIV TEST: nu persistă nimic."""

    def __init__(self) -> None:
        _require_test_only("MemorySecretStore")
        self._d: Dict[str, str] = {}

    def set(self, name, value):
        self._d[_check_name(name)] = value

    def get(self, name):
        return self._d.get(_check_name(name))

    def delete(self, name):
        self._d.pop(_check_name(name), None)


class _FileStore(SecretStore):
    """Un fișier per secret în `root` (în afara registrului)."""
    suffix = ".bin"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, name: str) -> Path:
        return self.root / (_check_name(name).replace("/", "__") + self.suffix)

    def _encode(self, raw: bytes) -> bytes:
        raise NotImplementedError

    def _decode(self, blob: bytes) -> bytes:
        raise NotImplementedError

    def set(self, name, value):
        self.root.mkdir(parents=True, exist_ok=True)
        p = self._path(name)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(self._encode(value.encode("utf-8")))
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, p)

    def get(self, name):
        p = self._path(name)
        if not p.is_file():
            return None
        try:
            return self._decode(p.read_bytes()).decode("utf-8")
        except Exception as exc:
            raise SecretStoreError(f"secretul {name!r} nu poate fi citit ({type(exc).__name__})") from None

    def delete(self, name):
        self._path(name).unlink(missing_ok=True)


class DpapiSecretStore(_FileStore):
    """Windows DPAPI (CryptProtectData, domeniul utilizatorului). `protect`/`unprotect` sunt injectabile pentru teste."""

    def __init__(self, root: Path, protect: Optional[Callable[[bytes], bytes]] = None,
                 unprotect: Optional[Callable[[bytes], bytes]] = None) -> None:
        super().__init__(root)
        if protect is None or unprotect is None:
            if sys.platform != "win32":
                raise SecretStoreUnavailable("DPAPI există doar pe Windows")
            protect, unprotect = _win_protect, _win_unprotect
        self._protect, self._unprotect = protect, unprotect

    def _encode(self, raw):
        return self._protect(raw)

    def _decode(self, blob):
        return self._unprotect(blob)


class TestEncryptedSecretStore(_FileStore):
    """EXCLUSIV TEST: Fernet (criptare autentificată) cu cheie efemeră. Cheia nu se scrie pe disc; un alt proces o primește doar prin
    `CIMITIR_TEST_SECRET_KEY`. Refuzat în configurația comercială/production și fără `CIMITIR_ALLOW_TEST_SECRETSTORE=1`."""
    __test__ = False
    suffix = ".enc"

    def __init__(self, root: Path, key: Optional[bytes] = None) -> None:
        _require_test_only("TestEncryptedSecretStore")
        from cryptography.fernet import Fernet
        super().__init__(root)
        self.key = key if key is not None else Fernet.generate_key()
        self._f = Fernet(self.key)

    def _encode(self, raw):
        return self._f.encrypt(raw)

    def _decode(self, blob):
        return self._f.decrypt(blob)


def _win_protect(data: bytes) -> bytes:  # pragma: no cover - doar Windows
    return _win_call(data, True)


def _win_unprotect(blob: bytes) -> bytes:  # pragma: no cover - doar Windows
    return _win_call(blob, False)


def _win_call(data: bytes, protect: bool) -> bytes:  # pragma: no cover - doar Windows
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def make(b: bytes):
        buf = ctypes.create_string_buffer(b, len(b))
        return Blob(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    src, keep1 = make(data)
    ent, keep2 = make(_ENTROPY)
    out = Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, ctypes.byref(ent), None, None, 0, ctypes.byref(out)):
        raise SecretStoreError("DPAPI a eșuat")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def secrets_dir(data_dir=None) -> Path:
    import cimitir_config
    return Path(data_dir if data_dir is not None else cimitir_config.data_dir()) / "secrets"


def default_store(data_dir=None) -> SecretStore:
    """Windows -> DPAPI; altfel numai cu backend explicit de test (test-encrypted / memory). Niciodată plaintext tăcut."""
    choice = (os.environ.get(ENV_BACKEND) or "").strip().lower()
    root = secrets_dir(data_dir)
    if choice == "memory":
        return MemorySecretStore()
    if choice == "test-encrypted":
        key = (os.environ.get(ENV_TEST_KEY) or "").encode("ascii") or None
        return TestEncryptedSecretStore(root, key)
    if choice in ("", "dpapi"):
        if sys.platform == "win32":
            return DpapiSecretStore(root)
        raise SecretStoreUnavailable("fără DPAPI pe acest sistem: backend-urile de test: CIMITIR_SECRET_STORE=test-encrypted + CIMITIR_ALLOW_TEST_SECRETSTORE=1, doar în teste")
    raise SecretStoreUnavailable(f"backend necunoscut: {choice!r}")
