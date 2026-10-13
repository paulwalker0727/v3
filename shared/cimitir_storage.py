"""
cimitir_storage.py — Etapa 7: storage de obiecte pentru documente/fotografii (în afara PostgreSQL).

În PostgreSQL rămân DOAR metadatele (tabela `media_objects`); octeții (PDF/JPG) stau în storage.

Drivere (alegerea: `CIMITIR_STORAGE_DRIVER`):
  local     implicit — folder pe discul serverului (`<DATA_DIR>/storage_objects`, sau `CIMITIR_STORAGE_LOCAL_DIR`).
  s3        orice S3-compatibil (AWS S3, Cloudflare R2, Backblaze B2, MinIO, Supabase S3):
            S3_BUCKET, S3_REGION, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, [S3_ENDPOINT_URL], [S3_PREFIX], [S3_FORCE_PATH_STYLE=1]
  supabase  Supabase Storage (API REST): SUPABASE_URL, SUPABASE_SECRET_KEY (cheia „secret” sb_secret_...; vechiul nume
            SUPABASE_SERVICE_ROLE_KEY e acceptat ca rezervă), SUPABASE_BUCKET (bucket PRIVAT), [SUPABASE_PREFIX]

Cheile storage-ului există DOAR pe server (FastAPI); desktopul nu le primește niciodată.
Un script de migrare a fișierelor vechi se poate adăuga ulterior peste `put_file` (vezi `media_service.register_file`).
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

CHUNK = 1024 * 1024


class StorageError(Exception):
    """Eroare de storage (rețea, autentificare, bucket lipsă)."""


class NotFound(StorageError):
    pass


class AlreadyExists(StorageError):
    pass


# ------------------------------------------------------------------ utilitare
def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def guess_mime(filename: str, default: str = "application/octet-stream") -> str:
    return mimetypes.guess_type(filename)[0] or default


_TS_PREFIX = re.compile(r"^\d{8}_\d{6}_\d{6}_")


def display_name(path: str) -> str:
    """Numele de afișat al unui fișier gestionat: fără prefixul de timp pus la import (`20261004_142931_693925_<nume>`)."""
    return _TS_PREFIX.sub("", os.path.basename(path)) or os.path.basename(path)


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(name: str, default: str = "fisier") -> str:
    base = os.path.basename(str(name or "").replace("\\", "/")).strip().strip(".")
    base = _SAFE.sub("_", base)[:120].strip("._")
    return base or default


def build_key(table: str, entity_type: str, doc_type: str, entity_uid: str, media_id: str, filename: str) -> str:
    """Cale de obiect: `documents/<entity_type>/<doc_type>/<entity_uid>/<media_id>/<filename>`
    sau `photos/loc/<entity_uid>/<media_id>/<filename>`. Tipul se recuperează din cale (nu se păstrează coloane în plus)."""
    if table == "photos":
        head = ["photos", "loc"]
    else:
        head = ["documents", safe_name(entity_type, "x"), safe_name(doc_type or "document", "document")]
    return "/".join(head + [safe_name(entity_uid, "x"), safe_name(media_id, "x"), safe_name(filename)])


def parse_key(key: str) -> Dict[str, str]:
    """Inversul lui build_key: {table, entity_type, doc_type}."""
    p = key.split("/")
    if p and p[0] == "photos":
        return {"table": "photos", "entity_type": "loc", "doc_type": "poza_loc"}
    if len(p) >= 3 and p[0] == "documents":
        return {"table": "documents", "entity_type": p[1], "doc_type": p[2]}
    return {"table": "documents", "entity_type": "", "doc_type": ""}


def _check_key(key: str) -> str:
    key = str(key or "").strip().lstrip("/")
    if not key or ".." in key.split("/") or "\\" in key or "\x00" in key:
        raise StorageError(f"cheie invalidă: {key!r}")
    return key


# ------------------------------------------------------------------ interfață
class Storage:
    name = "abstract"

    def put_file(self, key: str, path: str, mime: str = "") -> None:
        raise NotImplementedError

    def open_stream(self, key: str) -> Iterator[bytes]:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError

    def describe(self) -> Dict[str, Any]:
        return {"driver": self.name}

    def get_bytes(self, key: str) -> bytes:
        return b"".join(self.open_stream(key))

    def download_to(self, key: str, dest: str) -> str:
        """Descarcă atomic (fișier temporar + rename) — un fișier parțial nu rămâne niciodată la destinație."""
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest) or ".", suffix=".part")
        try:
            with os.fdopen(fd, "wb") as fh:
                for block in self.open_stream(key):
                    fh.write(block)
            os.replace(tmp, dest)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return dest


# ------------------------------------------------------------------ local
class LocalStorage(Storage):
    name = "local"

    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / _check_key(key)).resolve()
        if self.root != p and self.root not in p.parents:
            raise StorageError("cheie în afara storage-ului")
        return p

    def put_file(self, key: str, path: str, mime: str = "") -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        shutil.copyfile(path, tmp)
        os.replace(tmp, dest)

    def open_stream(self, key: str) -> Iterator[bytes]:
        p = self._path(key)
        if not p.is_file():
            raise NotFound(key)

        def gen():
            with open(p, "rb") as fh:
                for block in iter(lambda: fh.read(CHUNK), b""):
                    yield block
        return gen()

    def delete(self, key: str) -> None:
        path = self._path(key)
        try:
            path.unlink()
        except FileNotFoundError:
            return
        for parent in path.parents:           # curăță folderele rămase goale (fără a atinge rădăcina)
            if parent == self.root:
                break
            try:
                parent.rmdir()
            except OSError:
                break

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def describe(self) -> Dict[str, Any]:
        return {"driver": "local", "location": str(self.root)}


# ------------------------------------------------------------------ S3-compatibil
class S3Storage(Storage):
    name = "s3"

    def __init__(self, bucket: str, region: str = "", access_key: str = "", secret_key: str = "",
                 endpoint_url: str = "", prefix: str = "", path_style: bool = False):
        import boto3
        from botocore.config import Config
        if not bucket:
            raise StorageError("S3_BUCKET lipsește")
        self.bucket, self.prefix = bucket, prefix.strip("/")
        self.endpoint_url = endpoint_url
        cfg = Config(s3={"addressing_style": "path" if path_style or endpoint_url else "auto"},
                     retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=10, read_timeout=60)
        self.client = boto3.client("s3", region_name=region or None, endpoint_url=endpoint_url or None,
                                   aws_access_key_id=access_key or None, aws_secret_access_key=secret_key or None, config=cfg)

    def _k(self, key: str) -> str:
        key = _check_key(key)
        return f"{self.prefix}/{key}" if self.prefix else key

    def _wrap(self, exc: Exception, key: str) -> Exception:
        from botocore.exceptions import ClientError
        if isinstance(exc, ClientError) and exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return NotFound(key)
        return StorageError(f"S3: {exc}")

    def put_file(self, key: str, path: str, mime: str = "") -> None:
        try:
            self.client.upload_file(path, self.bucket, self._k(key), ExtraArgs={"ContentType": mime or guess_mime(key)})
        except Exception as exc:
            raise self._wrap(exc, key)

    def open_stream(self, key: str) -> Iterator[bytes]:
        try:
            body = self.client.get_object(Bucket=self.bucket, Key=self._k(key))["Body"]
        except Exception as exc:
            raise self._wrap(exc, key)

        def gen():
            try:
                for block in body.iter_chunks(CHUNK):
                    yield block
            finally:
                body.close()
        return gen()

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=self._k(key))
        except Exception as exc:
            raise self._wrap(exc, key)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._k(key))
            return True
        except Exception as exc:
            err = self._wrap(exc, key)
            if isinstance(err, NotFound):
                return False
            raise err

    def describe(self) -> Dict[str, Any]:
        return {"driver": "s3", "bucket": self.bucket, "endpoint": self.endpoint_url or "aws", "prefix": self.prefix}


# ------------------------------------------------------------------ Supabase Storage (REST)
class SupabaseStorage(Storage):
    name = "supabase"

    def __init__(self, url: str, service_key: str, bucket: str, prefix: str = ""):
        if not (url and service_key and bucket):
            raise StorageError("SUPABASE_URL / SUPABASE_SECRET_KEY / SUPABASE_BUCKET lipsesc")
        self.base = url.rstrip("/") + "/storage/v1"
        self.bucket, self.prefix = bucket, prefix.strip("/")
        # cheile noi „sb_secret_...” nu sunt JWT: se trimit doar în `apikey`; cheile vechi (JWT service_role) și în Authorization
        self.headers = {"apikey": service_key} if service_key.startswith("sb_") else {"Authorization": f"Bearer {service_key}", "apikey": service_key}

    def _k(self, key: str) -> str:
        from urllib.parse import quote
        key = _check_key(key)
        return quote(f"{self.prefix}/{key}" if self.prefix else key, safe="/")

    def _req(self, method: str, url: str, key: str = "", **kw):
        import requests
        try:
            resp = requests.request(method, url, headers={**self.headers, **kw.pop("headers", {})}, timeout=(10, 120), **kw)
        except requests.RequestException as exc:
            raise StorageError(f"Supabase indisponibil: {exc}")
        if resp.status_code in (400, 404) and method in ("GET", "HEAD"):
            raise NotFound(key)
        if resp.status_code >= 400:
            raise StorageError(f"Supabase {resp.status_code}: {resp.text[:200]}")
        return resp

    def put_file(self, key: str, path: str, mime: str = "") -> None:
        with open(path, "rb") as fh:
            self._req("POST", f"{self.base}/object/{self.bucket}/{self._k(key)}", key, data=fh,
                      headers={"Content-Type": mime or guess_mime(key), "x-upsert": "true"})

    def open_stream(self, key: str) -> Iterator[bytes]:
        resp = self._req("GET", f"{self.base}/object/authenticated/{self.bucket}/{self._k(key)}", key, stream=True)

        def gen():
            try:
                for block in resp.iter_content(CHUNK):
                    if block:
                        yield block
            finally:
                resp.close()
        return gen()

    def delete(self, key: str) -> None:
        try:
            self._req("DELETE", f"{self.base}/object/{self.bucket}/{self._k(key)}", key)
        except StorageError as exc:
            if "not_found" not in str(exc) and "NoSuchKey" not in str(exc):   # Supabase răspunde 400 {"statusCode":"404"} pentru obiect lipsă
                raise

    def exists(self, key: str) -> bool:
        try:
            self._req("HEAD", f"{self.base}/object/authenticated/{self.bucket}/{self._k(key)}", key)
            return True
        except NotFound:
            return False

    def put_bytes(self, key: str, data: bytes, mime: str = "", upsert: bool = False) -> None:
        """Scrie octeții; fără upsert, un obiect existent ridică AlreadyExists (409 Duplicate)."""
        try:
            self._req("POST", f"{self.base}/object/{self.bucket}/{self._k(key)}", key, data=data,
                      headers={"Content-Type": mime or guess_mime(key), "x-upsert": "true" if upsert else "false"})
        except StorageError as exc:
            if "Duplicate" in str(exc) or "KeyAlreadyExists" in str(exc):
                raise AlreadyExists(key) from None
            raise

    def stat(self, key: str) -> Optional[Dict[str, Any]]:
        """{size, mime} din HEAD (None dacă lipsește)."""
        try:
            r = self._req("HEAD", f"{self.base}/object/authenticated/{self.bucket}/{self._k(key)}", key)
        except NotFound:
            return None
        return {"size": int(r.headers.get("content-length") or -1), "mime": r.headers.get("content-type", "")}

    def signed_url(self, key: str, ttl: int) -> str:
        """URL semnat cu token scurt (fără cheia secretă în URL); valabil `ttl` secunde."""
        resp = self._req("POST", f"{self.base}/object/sign/{self.bucket}/{self._k(key)}", key, json={"expiresIn": int(ttl)})
        rel = resp.json().get("signedURL") or ""
        if not rel:
            raise StorageError("Supabase nu a întors signedURL")
        return rel if rel.startswith("http") else self.base + rel

    def list_prefix(self, prefix: str) -> list:
        """Obiectele DIRECTE din `prefix` (un nivel): [{name, size, created_at, updated_at}]."""
        out, offset = [], 0
        while True:
            page = self._req("POST", f"{self.base}/object/list/{self.bucket}", prefix, json={"prefix": _check_key(prefix).strip("/"), "limit": 100, "offset": offset}).json()
            for o in page:
                if o.get("id"):
                    out.append({"name": o["name"], "size": (o.get("metadata") or {}).get("size"), "created_at": o.get("created_at"), "updated_at": o.get("updated_at")})
            if len(page) < 100:
                return out
            offset += 100

    def auth_ok(self) -> bool:
        """True doar dacă cheia AUTENTIFICĂ (listarea bucketurilor răspunde 200). 400/401/403 = cheie/URL respinse.
        Necesar pentru că un 400/404 la GET este altfel interpretat ca „inexistent” (ex. bucket lipsă)."""
        import requests
        try:
            return requests.get(f"{self.base}/bucket", headers=self.headers, timeout=(10, 30)).status_code == 200
        except requests.RequestException:
            return False

    def bucket_info(self) -> Optional[Dict[str, Any]]:
        """Metadatele bucketului (inclusiv `public`) sau None dacă nu există."""
        try:
            return self._req("GET", f"{self.base}/bucket/{self.bucket}", self.bucket).json()
        except NotFound:
            return None

    def ensure_bucket(self) -> str:
        """Creează bucketul PRIVAT dacă lipsește. Întoarce 'exists' | 'created'. Nu modifică un bucket existent."""
        if self.bucket_info() is not None:
            return "exists"
        self._req("POST", f"{self.base}/bucket", self.bucket, json={"id": self.bucket, "name": self.bucket, "public": False})
        return "created"

    def describe(self) -> Dict[str, Any]:
        return {"driver": "supabase", "bucket": self.bucket, "url": self.base, "prefix": self.prefix}


# ------------------------------------------------------------------ fabrică
_instance: Optional[Storage] = None
_instance_key: Optional[tuple] = None


def _env_signature() -> tuple:
    keys = ("CIMITIR_STORAGE_DRIVER", "CIMITIR_STORAGE_LOCAL_DIR", "CIMITIR_DATA_DIR", "S3_BUCKET", "S3_ENDPOINT_URL",
            "S3_ACCESS_KEY_ID", "S3_PREFIX", "SUPABASE_URL", "SUPABASE_BUCKET")
    return tuple(os.environ.get(k, "") for k in keys)


def driver_name() -> str:
    return (os.environ.get("CIMITIR_STORAGE_DRIVER") or "local").strip().lower()


def build_storage() -> Storage:
    name = driver_name()
    env = os.environ.get
    if name == "local":
        root = env("CIMITIR_STORAGE_LOCAL_DIR") or os.path.join(env("CIMITIR_DATA_DIR") or "shared_data", "storage_objects")
        return LocalStorage(root)
    if name == "s3":
        return S3Storage(env("S3_BUCKET", ""), env("S3_REGION", ""), env("S3_ACCESS_KEY_ID", ""), env("S3_SECRET_ACCESS_KEY", ""),
                         env("S3_ENDPOINT_URL", ""), env("S3_PREFIX", ""), env("S3_FORCE_PATH_STYLE", "") == "1")
    if name == "supabase":
        return SupabaseStorage(env("SUPABASE_URL", ""), env("SUPABASE_SECRET_KEY", "") or env("SUPABASE_SERVICE_ROLE_KEY", ""),
                               env("SUPABASE_BUCKET", ""),
                               env("SUPABASE_PREFIX", ""))
    raise StorageError(f"CIMITIR_STORAGE_DRIVER necunoscut: {name} (local | s3 | supabase)")


def get_storage() -> Storage:
    global _instance, _instance_key
    sig = _env_signature()
    if _instance is None or _instance_key != sig:
        _instance, _instance_key = build_storage(), sig
    return _instance


def reset_storage() -> None:
    global _instance, _instance_key
    _instance, _instance_key = None, None
