"""HTTP API read-only pentru Cimitir Mobile v2."""
from __future__ import annotations

from typing import Any

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import cimitir_cloud_control as cc
import cimitir_cloud_wire as wire
import cimitir_field_inbox_http as fih
import cimitir_mobile_registry as mr

PREFIX = wire.API + "/mobile"


def create_router(service: mr.MobileRegistryService) -> APIRouter:
    router = APIRouter(prefix=PREFIX)

    async def run(fn):
        return await anyio.to_thread.run_sync(fn)

    def route(path: str, fn):
        async def handler(request: Request):
            secret = ""
            try:
                secret = fih._binding_key(request)
                try:
                    await run(lambda: service.inbox.authenticate(secret))
                except cc.ScopeError:
                    raise wire.Unauthenticated("legătură invalidă") from None
                result = await fn(request, secret)
                return JSONResponse({"ok": True, "result": result}, headers=fih._HEADERS)
            except Exception as exc:  # noqa: BLE001
                return fih._error(exc, secret)
        router.add_api_route(path, handler, methods=["GET"])

    async def bootstrap(_request: Request, key: str):
        return await run(lambda: service.bootstrap(key))

    async def summary(_request: Request, key: str):
        return await run(lambda: service.summary(key))

    async def search(request: Request, key: str):
        q = (request.query_params.get("q") or "")[:120]
        try:
            limit = int(request.query_params.get("limit", "12"))
        except ValueError:
            limit = 12
        return await run(lambda: service.search(key, q, limit))

    async def map_data(request: Request, key: str):
        try:
            limit = int(request.query_params.get("limit", str(mr.MAP_LIMIT_MAX)))
        except ValueError:
            limit = mr.MAP_LIMIT_MAX
        return await run(lambda: service.map_data(key, limit))

    async def grave(request: Request, key: str):
        return await run(lambda: service.grave_detail(key, request.path_params["uid"]))

    async def data_status(_request: Request, key: str):
        return await run(lambda: service.data_status(key))

    route("/bootstrap", bootstrap)
    route("/data-status", data_status)
    route("/summary", summary)
    route("/search", search)
    route("/map", map_data)
    route("/graves/{uid}", grave)
    return router
