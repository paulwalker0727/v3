"""HTTP Sync v2: `/api/v1/sync-v2/*` (Binding). Tenantul vine din binding; mesajele de eroare sunt fixe."""
from __future__ import annotations

import json

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import cimitir_cloud_control as cc
import cimitir_cloud_wire as wire
import cimitir_field_inbox_http as fih
import cimitir_sync_v2 as sv

PREFIX = wire.API + "/sync-v2"
PUSH_LIMIT = 512 * 1024


def create_router(service: sv.SyncV2Service) -> APIRouter:
    router = APIRouter(prefix=PREFIX)

    def add(path: str, methods: list, fn):
        async def handler(request: Request):
            secret = ""
            try:
                secret = fih._binding_key(request)
                try:
                    result = await fn(request, secret)
                except cc.ScopeError as exc:
                    if str(exc) == "legătură invalidă":
                        raise wire.Unauthenticated("legătură invalidă") from None
                    raise
                return JSONResponse({"ok": True, "result": result}, headers=fih._HEADERS)
            except Exception as exc:  # noqa: BLE001
                return fih._error(exc, secret)
        router.add_api_route(path, handler, methods=methods)

    def run(fn):
        return anyio.to_thread.run_sync(fn)

    def qint(request: Request, name: str, default: int) -> int:
        try:
            return int(request.query_params.get(name, default))
        except ValueError:
            return default

    async def status(_r, key):
        return await run(lambda: service.status(key))

    async def pull(r: Request, key):
        return await run(lambda: service.pull(key, r.query_params.get("entity", ""), qint(r, "since", 0), qint(r, "limit", 500)))

    async def row(r: Request, key):
        return await run(lambda: service.get_row(key, r.query_params.get("entity", ""), r.query_params.get("uid", "")))

    async def push(r: Request, key):
        body = json.loads(await fih._body_limited(r, PUSH_LIMIT))
        return await run(lambda: service.push(key, body.get("ops") if isinstance(body, dict) else None))

    async def conflicts(r: Request, key):
        return await run(lambda: service.conflicts(key, r.query_params.get("all") != "1", qint(r, "limit", 100)))

    async def resolve(r: Request, key):
        body = json.loads(await fih._body_limited(r, 4096))
        return await run(lambda: service.resolve(key, int(r.path_params["cid"]), str(body.get("resolution") or "")))

    async def devices(_r, key):
        return await run(lambda: service.devices(key))

    async def permissions(r: Request, key):
        body = json.loads(await fih._body_limited(r, 4096))
        return await run(lambda: service.set_permissions(key, r.path_params["ref"], body.get("entities")))

    async def history(r: Request, key):
        return await run(lambda: service.history(key, r.query_params.get("entity", ""), r.query_params.get("uid", ""), qint(r, "limit", 100)))

    async def roles(_r, key):
        return await run(lambda: service.roles(key))

    async def capabilities(r: Request, key):
        body = json.loads(await fih._body_limited(r, 4096))
        return await run(lambda: service.set_capabilities(key, r.path_params["ref"], body.get("role"), body.get("capabilities")))

    async def sectors(r: Request, key):
        body = json.loads(await fih._body_limited(r, 2_000_000))
        return await run(lambda: service.put_sectors(key, body.get("sectors")))

    async def runs(r: Request, key):
        return await run(lambda: service.runs(key, qint(r, "limit", 50)))

    add("/status", ["GET"], status)
    add("/pull", ["GET"], pull)
    add("/row", ["GET"], row)
    add("/push", ["POST"], push)
    add("/conflicts", ["GET"], conflicts)
    add("/conflicts/{cid}/resolve", ["POST"], resolve)
    add("/devices", ["GET"], devices)
    add("/devices/{ref}/permissions", ["PUT"], permissions)
    add("/history", ["GET"], history)
    add("/roles", ["GET"], roles)
    add("/devices/{ref}/capabilities", ["PUT"], capabilities)
    add("/sectors", ["PUT"], sectors)
    add("/runs", ["GET"], runs)
    return router
