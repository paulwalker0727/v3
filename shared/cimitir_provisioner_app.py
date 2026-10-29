"""cimitir_provisioner_app.py — serviciul HTTP de administrare al OPERATORULUI (provizionare instanțe) + CLI.

  python -m cimitir_provisioner_app init-owner --home DIR --name NUME      # creează primul owner (tokenul se afișează O SINGURĂ DATĂ); necesită CIMITIR_PROVISIONER_MASTER_KEY[_FILE]
  python -m cimitir_provisioner_app new-master-key                         # generează cheia-master a seifului
  python -m cimitir_provisioner_app serve --home DIR [--port 8443]         # ascultă DOAR pe 127.0.0.1 (expunere publică: numai prin reverse proxy HTTPS)
Autentificare: `Authorization: Bearer cpv_...` (tokenuri de operator). Licența .lic NU este acceptată. Parolele administrative vin în corpul cererii, nu se stochează/loghează.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import cimitir_provisioner as pv
from cimitir_prov_store import ProvisionError, Vault, redact_text

log = logging.getLogger("cimitir.provisioner")
API = "/api/v1/provisioner"
_H = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


class _Fails:
    def __init__(self, limit=10, window=300):
        self.limit, self.window, self.d, self.lock = limit, window, defaultdict(list), threading.Lock()

    def blocked(self, ip):
        with self.lock:
            now = time.time()
            self.d[ip] = [t for t in self.d[ip] if now - t < self.window]
            return len(self.d[ip]) >= self.limit

    def hit(self, ip):
        with self.lock:
            self.d[ip].append(time.time())


def create_app(prov: pv.Provisioner) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    fails = _Fails()

    def err(e: ProvisionError):
        return JSONResponse({"ok": False, "code": e.code, "message": redact_text(e)}, status_code=e.status, headers=_H)

    async def run(request: Request, fn, status=200):
        ip = request.client.host if request.client else "?"
        if fails.blocked(ip):
            return JSONResponse({"ok": False, "code": "RATE_LIMIT", "message": "prea multe încercări eșuate"}, status_code=429, headers=_H)
        auth = request.headers.get("authorization", "")
        op = prov.reg.authenticate(auth[7:].strip()) if auth[:7].lower() == "bearer " else None
        if not op:
            fails.hit(ip)
            return JSONResponse({"ok": False, "code": "UNAUTHORIZED", "message": "token de operator lipsă sau invalid"}, status_code=401, headers=_H)
        try:
            body: Dict[str, Any] = {}
            raw = await request.body()
            if raw:
                body = __import__("json").loads(raw)
                if not isinstance(body, dict):
                    raise ProvisionError("BAD_REQUEST", "corp JSON invalid")
            import anyio
            res = await anyio.to_thread.run_sync(lambda: fn(op, body))
            return JSONResponse({"ok": True, "result": res}, status_code=status, headers=_H)
        except ValueError:
            return err(ProvisionError("BAD_REQUEST", "JSON invalid"))
        except ProvisionError as exc:
            return err(exc)
        except Exception as exc:  # noqa: BLE001
            log.error("eroare internă: %s", type(exc).__name__)
            return err(ProvisionError("INTERNAL", "eroare internă", 500))

    def route(method: str, path: str, fn, status: int = 200):
        async def h(request: Request, ident: str = ""):
            return await run(request, lambda op, b: fn(op, request.path_params.get("ident", ""), b), status)
        app.add_api_route(API + path, h, methods=[method], include_in_schema=False)

    @app.get(API + "/health", include_in_schema=False)
    def health():
        return JSONResponse({"ok": True, "env": prov.env}, headers=_H)

    g = lambda b, k, d="": b.get(k, d)  # noqa: E731
    adm = lambda b: g(b, "admin_database_url")  # noqa: E731
    route("GET", "/instances", lambda op, i, b: prov.list_public(op))
    route("POST", "/instances", lambda op, i, b: prov.register(op, g(b, "slug"), g(b, "parish_name"), g(b, "domain"), g(b, "owner_public_key"), bool(b.get("generate_owner_key"))), 201)
    route("GET", "/instances/{ident}", lambda op, i, b: prov.get_public(op, i))
    route("POST", "/instances/{ident}/link", lambda op, i, b: prov.link(op, i, b))
    route("POST", "/instances/{ident}/verify", lambda op, i, b: prov.verify(op, i, adm(b)))
    route("POST", "/instances/{ident}/provision", lambda op, i, b: prov.provision(op, i, adm(b), b.get("release")))
    route("POST", "/instances/{ident}/health", lambda op, i, b: prov.health(op, i))
    route("POST", "/instances/{ident}/update", lambda op, i, b: prov.update(op, i, adm(b), g(b, "release")))
    route("POST", "/instances/{ident}/rotate-secret", lambda op, i, b: prov.secure(op, i, adm(b), rotate=True))
    route("POST", "/instances/{ident}/renew-tls", lambda op, i, b: prov.renew_tls(op, i, bool(b.get("force"))))
    route("POST", "/instances/{ident}/restart", lambda op, i, b: prov.restart(op, i))
    route("POST", "/instances/{ident}/backup", lambda op, i, b: prov.backup(op, i, adm(b)))
    route("GET", "/instances/{ident}/backups", lambda op, i, b: prov.list_backups(op, i))
    route("POST", "/instances/{ident}/backups/verify", lambda op, i, b: prov.verify_backup(op, i, g(b, "backup_id")))
    route("POST", "/instances/{ident}/restore", lambda op, i, b: prov.restore(op, i, adm(b), g(b, "backup_id"), g(b, "target_database_url"), g(b, "confirmation"), g(b, "confirm_slug")))
    route("POST", "/instances/{ident}/confirmations", lambda op, i, b: prov.confirmation(op, g(b, "action"), i))
    route("POST", "/instances/{ident}/suspend", lambda op, i, b: prov.suspend(op, i, adm(b), g(b, "confirmation"), g(b, "confirm_slug")))
    route("POST", "/instances/{ident}/resume", lambda op, i, b: prov.resume(op, i, adm(b)))
    route("POST", "/instances/{ident}/deactivate", lambda op, i, b: prov.deactivate(op, i, adm(b), g(b, "confirmation"), g(b, "confirm_slug")))
    route("GET", "/audit", lambda op, i, b: _audit(prov, op, ""))
    route("GET", "/audit/verify", lambda op, i, b: prov.reg.audit_verify())
    route("POST", "/releases", lambda op, i, b: prov.import_release(op, g(b, "source_path"), g(b, "version")))
    route("POST", "/releases/default", lambda op, i, b: prov.set_default_release(op, g(b, "version")))
    return app


def _audit(prov, op, iid):
    prov._authz(op, "audit")
    return prov.reg.audit_list(iid)


def build_provisioner(home: Path) -> pv.Provisioner:
    env = (os.environ.get("CIMITIR_PROVISIONER_ENV") or "").strip().upper()
    if env not in ("TEST", "PROD"):
        raise ProvisionError("BAD_ENV", "setează CIMITIR_PROVISIONER_ENV=TEST sau PROD (identitățile TEST și PROD sunt separate)", 500)
    return pv.Provisioner(home, env, Vault(Path(home) / "vault", Vault.master_from_env()))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cimitir_provisioner_app")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("new-master-key")
    for n in ("init-owner", "serve"):
        s = sub.add_parser(n)
        s.add_argument("--home", required=True)
    sub.choices["init-owner"].add_argument("--name", required=True)
    sub.choices["serve"].add_argument("--port", type=int, default=8443)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "new-master-key":
            print(Vault.new_master())
            return 0
        prov = build_provisioner(Path(a.home))
        if a.cmd == "init-owner":
            print("TOKEN OWNER (se afișează o singură dată; se stochează doar hash-ul):\n" + prov.reg.create_operator(a.name, "owner", prov.env))
            return 0
        import uvicorn
        logging.basicConfig(level="INFO")
        for k, v in prov.reconcile().items():
            log.info("instanța %s: %s", k, v)
        uvicorn.run(create_app(prov), host="127.0.0.1", port=a.port, proxy_headers=False, server_header=False, access_log=False)
        return 0
    except ProvisionError as exc:
        print(f"EROARE [{exc.code}]: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
