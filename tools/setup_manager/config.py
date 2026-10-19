"""Configurația Setup Manager: câmpuri nesecrete în config.json local; secretele doar în memorie (opțional criptate cu Windows DPAPI)."""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote, unquote, urlparse, urlunparse

from . import redact

DEFAULTS: Dict[str, Any] = {
    "env_name": "TEST",
    "project_ref": "fommbiwndlxkfsnlqatq",
    "supabase_url": "https://fommbiwndlxkfsnlqatq.supabase.co",
    "db_connection_string": "postgresql://postgres.fommbiwndlxkfsnlqatq@aws-0-eu-west-1.pooler.supabase.com:5432/postgres",
    "db_username": "postgres.fommbiwndlxkfsnlqatq",
    "storage_bucket": "cimitir-private-test",
    "cloud_public_url": "http://127.0.0.1:8085",
    "container_name": "cimitir-cloud-sync-v2-main-test",
    "docker_port": 8085,
    "image_tag": "sync-v2-main-test-1",
    "source_container": "",
    "backup_path": "",
    "repo_dir": "",
    "main_cloud_url": "http://127.0.0.1:8085",
    "main_data_dir": "",
    "mobile_public_url": "",
}
SECRET_FIELDS = ("db_password", "supabase_secret_key", "runtime_db_url")
TRANSIENT_FIELDS = ("cloud_admin_email", "cloud_admin_password")      # niciodată persistate
KEEP = "__KEEP__"


class _Dpapi:
    """Criptare Windows DPAPI (doar utilizatorul curent). Pe alte sisteme: indisponibilă."""

    @staticmethod
    def available() -> bool:
        return sys.platform == "win32"

    @staticmethod
    def _call(data: bytes, protect: bool) -> bytes:
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        buf = ctypes.create_string_buffer(data, len(data))
        src = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        out = Blob()
        fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
        ok = fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out))
        if not ok:
            raise OSError("DPAPI a eșuat")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)

    @classmethod
    def protect(cls, data: bytes) -> bytes:
        return cls._call(data, True)

    @classmethod
    def unprotect(cls, data: bytes) -> bytes:
        return cls._call(data, False)


def default_home() -> Path:
    env = os.environ.get("SETUP_MANAGER_HOME")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "CimitirSetupManager" if base else Path.home() / ".cimitir_setup_manager"


class ConfigStore:
    def __init__(self, home: Optional[Path] = None):
        self.home = Path(home) if home else default_home()
        self.home.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.values: Dict[str, Any] = dict(DEFAULTS)
        self.secrets: Dict[str, str] = {}
        self.transient: Dict[str, str] = {}
        self.persist_secrets = False
        self.load()

    @property
    def path(self) -> Path:
        return self.home / "config.json"

    @property
    def secrets_path(self) -> Path:
        return self.home / "secrets.dpapi"

    def load(self) -> None:
        with self._lock:
            if self.path.exists():
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                    for k in DEFAULTS:
                        if k in data:
                            self.values[k] = data[k]
                    self.persist_secrets = bool(data.get("_persist_secrets"))
                except (OSError, ValueError):
                    pass
            if self.persist_secrets and _Dpapi.available() and self.secrets_path.exists():
                try:
                    blob = json.loads(_Dpapi.unprotect(self.secrets_path.read_bytes()).decode("utf-8"))
                    for k in SECRET_FIELDS:
                        if blob.get(k):
                            self.secrets[k] = blob[k]
                            redact.register(blob[k])
                except (OSError, ValueError):
                    pass

    def save(self) -> None:
        with self._lock:
            data = dict(self.values)
            data["_persist_secrets"] = self.persist_secrets
            self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            if self.persist_secrets and _Dpapi.available():
                self.secrets_path.write_bytes(_Dpapi.protect(json.dumps(self.secrets).encode("utf-8")))
            elif self.secrets_path.exists():
                self.secrets_path.unlink()

    def update(self, patch: Dict[str, Any]) -> None:
        with self._lock:
            for k, v in patch.items():
                if k in DEFAULTS and k != "env_name":
                    self.values[k] = v.strip() if isinstance(v, str) else v
                elif k in SECRET_FIELDS:
                    if v is None or v == KEEP:
                        continue
                    if v == "":
                        self.secrets.pop(k, None)
                    else:
                        self.secrets[k] = v
                        redact.register(v)
                elif k in TRANSIENT_FIELDS:
                    if v != KEEP:
                        self.transient[k] = v or ""
                        redact.register(v or "")
                elif k == "persist_secrets":
                    if bool(v) and not _Dpapi.available():
                        raise ValueError("Salvarea criptată a secretelor cere Windows (DPAPI). Secretele rămân doar în memorie.")
                    self.persist_secrets = bool(v)
            self._extract_url_password()
            if str(self.values.get("docker_port") or "").isdigit():
                self.values["docker_port"] = int(self.values["docker_port"])
            self.save()

    def _extract_url_password(self) -> None:
        """Parola din connection string se mută în câmpul secret și se scoate din URL (nu se salvează pe disc)."""
        u = urlparse(self.values.get("db_connection_string") or "")
        if u.password:
            pw = unquote(u.password)
            self.secrets["db_password"] = pw
            redact.register(pw)
            if not self.values.get("db_username"):
                self.values["db_username"] = unquote(u.username or "")
            netloc = (quote(unquote(u.username or ""), safe=".") + "@" if u.username else "") + (u.hostname or "") + (f":{u.port}" if u.port else "")
            self.values["db_connection_string"] = urlunparse(u._replace(netloc=netloc))

    def effective(self) -> Dict[str, Any]:
        """Valori + secrete + tranziente, doar pentru uz intern (niciodată serializat spre UI/loguri)."""
        with self._lock:
            return {**self.values, **self.secrets, **self.transient}

    def public(self) -> Dict[str, Any]:
        with self._lock:
            out = dict(self.values)
            for k in SECRET_FIELDS:
                out[k] = {"set": k in self.secrets}
            for k in TRANSIENT_FIELDS:
                out[k] = {"set": bool(self.transient.get(k))}
            out["persist_secrets"] = self.persist_secrets
            out["dpapi_available"] = _Dpapi.available()
            return out


def build_db_url(cfg: Dict[str, Any], url_key: str = "db_connection_string") -> str:
    """URL PostgreSQL complet (cu utilizator/parolă din câmpuri) pentru psycopg; NU se afișează."""
    u = urlparse(cfg.get(url_key) or "")
    user = cfg.get("db_username") or unquote(u.username or "")
    pw = cfg.get("db_password") or (unquote(u.password) if u.password else "")
    netloc = quote(user, safe=".") + (":" + quote(pw, safe="") if pw else "") + "@" if user else ""
    netloc += (u.hostname or "") + (f":{u.port}" if u.port else "")
    q = u.query
    if "supabase" in (u.hostname or "") and "sslmode" not in q:
        q = (q + "&" if q else "") + "sslmode=require"
    return urlunparse(u._replace(netloc=netloc, query=q, path=u.path or "/postgres"))


def db_host_port(cfg: Dict[str, Any]) -> tuple:
    u = urlparse(cfg.get("db_connection_string") or "")
    return (u.hostname or "", u.port or 5432)


def repo_root(cfg: Dict[str, Any]) -> Path:
    """Rădăcina repo-ului: câmpul repo_dir sau două niveluri deasupra tools/setup_manager."""
    custom = (cfg.get("repo_dir") or "").strip()
    return Path(custom) if custom else Path(__file__).resolve().parents[2]


def migrations_dir(cfg: Dict[str, Any]) -> Path:
    return repo_root(cfg) / "backend" / "cimitir" / "pg_migrations"
