"""cimitir_prov_demo.py — „proiect Supabase” LOCAL ȘI IZOLAT pentru demonstrație/teste: un cluster PostgreSQL propriu (scram, doar 127.0.0.1) + un emulator minimal
al API-ului Supabase Storage (bucket privat, apikey). Servește ca „proiectul beneficiarului” față de care se rulează provizionarea REALĂ (aceleași apeluri ca spre Supabase).
NU este Supabase: nu testează pooler-ul, TLS-ul PostgreSQL al Supabase, limitele sau comportamentul real al platformei.
"""
from __future__ import annotations

import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request, Response  # noqa: E402

PG_GLOB = "/usr/lib/postgresql"


def _pg_bin() -> str:
    d = sorted(Path(PG_GLOB).glob("*/bin"))
    if not d:
        raise RuntimeError("PostgreSQL server nu este instalat (apt install postgresql)")
    return str(d[-1])


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _as_postgres(cmd: str, env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    if os.geteuid() == 0:
        return subprocess.run(["su", "postgres", "-s", "/bin/bash", "-c", cmd], capture_output=True, text=True, env=env)
    return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, env=env)


class StorageEmulator:
    def __init__(self, secret_key: str):
        self.key, self.buckets, self.objects = secret_key, {}, {}
        app = FastAPI()
        self.app = app

        def authed(r: Request) -> bool:
            return r.headers.get("apikey") == self.key

        @app.get("/storage/v1/bucket/{b}")
        def get_bucket(b: str, r: Request):
            if not authed(r):
                return Response(status_code=401)
            return self.buckets.get(b) or Response('{"error":"not_found"}', status_code=404, media_type="application/json")

        @app.post("/storage/v1/bucket")
        async def mk_bucket(r: Request):
            if not authed(r):
                return Response(status_code=401)
            j = await r.json()
            self.buckets[j["id"]] = {"id": j["id"], "name": j["name"], "public": bool(j.get("public"))}
            return {"name": j["name"]}

        @app.post("/storage/v1/object/{b}/{key:path}")
        async def put(b: str, key: str, r: Request):
            if not authed(r):
                return Response(status_code=401)
            if b not in self.buckets:
                return Response(status_code=404)
            if (b, key) in self.objects and r.headers.get("x-upsert") != "true":
                return Response('{"error":"Duplicate"}', status_code=409, media_type="application/json")
            self.objects[(b, key)] = (await r.body(), r.headers.get("content-type", ""))
            return {"Key": f"{b}/{key}"}

        @app.api_route("/storage/v1/object/authenticated/{b}/{key:path}", methods=["GET", "HEAD"])
        def get(b: str, key: str, r: Request):
            if not authed(r):
                return Response(status_code=401)
            o = self.objects.get((b, key))
            return Response(o[0], media_type=o[1] or "application/octet-stream") if o else Response(status_code=404)

        @app.delete("/storage/v1/object/{b}/{key:path}")
        def delete(b: str, key: str, r: Request):
            if not authed(r):
                return Response(status_code=401)
            self.objects.pop((b, key), None)
            return {"message": "deleted"}

        self.port = _free_port()
        import uvicorn
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self):
        self.thread.start()
        end = time.time() + 10
        while time.time() < end and not self.server.started:
            time.sleep(0.05)

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=5)


class LocalSupabaseProject:
    """Un „proiect” = cluster PG izolat (parolă proprie, port propriu) + Storage emulat (cheie proprie)."""

    def __init__(self, base: Path, name: str):
        self.base, self.name = Path(base), name
        self.pg_dir = self.base / f"pg-{name}"
        self.port = _free_port()
        self.admin_password = secrets.token_urlsafe(24)
        self.storage_key = "sb_secret_" + secrets.token_urlsafe(24)
        self.ref = "".join(chr(97 + int(c, 16) % 26) for c in __import__("hashlib").sha256(name.encode()).hexdigest()[:20])
        self.dbname = "cimitir"
        self.storage = StorageEmulator(self.storage_key)

    def start(self) -> "LocalSupabaseProject":
        self.base.mkdir(parents=True, exist_ok=True)
        for p in (self.base, *self.base.parents):
            try:
                if str(p) in ("/", "/tmp"):
                    break
                p.chmod(p.stat().st_mode | 0o555)
            except OSError:
                break
        pw = self.base / f".pw-{self.name}"
        pw.write_text(self.admin_password)
        pw.chmod(0o644)
        b = _pg_bin()
        if os.geteuid() == 0:
            self.pg_dir.mkdir(parents=True, exist_ok=True)
            shutil.chown(self.pg_dir, "postgres")
        r = _as_postgres(f"{b}/initdb -D '{self.pg_dir}' -U postgres --auth-host=scram-sha-256 --auth-local=peer --pwfile='{pw}' -E UTF8 --locale=C.UTF-8")
        pw.unlink(missing_ok=True)
        if r.returncode != 0:
            raise RuntimeError("initdb: " + r.stderr[-300:])
        r = _as_postgres(f"{b}/pg_ctl -D '{self.pg_dir}' -o '-p {self.port} -c listen_addresses=127.0.0.1 -c unix_socket_directories={self.pg_dir}' -l '{self.pg_dir}/server.log' -w start")
        if r.returncode != 0:
            raise RuntimeError("pg_ctl: " + r.stderr[-300:])
        import psycopg
        with psycopg.connect(self.admin_url("postgres"), autocommit=True) as c:
            c.execute(f"CREATE DATABASE {self.dbname}")
        self.storage.start()
        return self

    def admin_url(self, db: Optional[str] = None) -> str:
        return f"postgresql://postgres:{self.admin_password}@127.0.0.1:{self.port}/{db or self.dbname}?sslmode=disable"

    @property
    def supabase_url(self) -> str:
        return f"http://127.0.0.1:{self.storage.port}"

    def link_params(self, bucket: str = "documente") -> Dict[str, Any]:
        return {"supabase_url": self.supabase_url, "project_ref": self.ref, "db_host": "127.0.0.1", "db_port": self.port, "db_name": self.dbname,
                "bucket": bucket, "storage_secret_key": self.storage_key}

    def stop(self):
        self.storage.stop()
        _as_postgres(f"{_pg_bin()}/pg_ctl -D '{self.pg_dir}' -m immediate stop")

    def destroy(self):
        self.stop()
        shutil.rmtree(self.pg_dir, ignore_errors=True)
