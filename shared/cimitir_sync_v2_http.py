"""HTTP Sync v2: `/api/v1/sync-v2/*` (Binding). Tenantul vine din binding; mesajele de eroare sunt fixe."""
from __future__ import annotations

import json
from typing import Any

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

import cimitir_cloud_control as cc
import cimitir_cloud_wire as wire
import cimitir_field_inbox_http as fih
import cimitir_field_storage as fs
import cimitir_sync_v2 as sv
import cimitir_sync_v2_docs as sdocs

PREFIX = wire.API + "/sync-v2"
PUSH_LIMIT = 512 * 1024


def create_router(service: sv.SyncV2Service, docs: "sdocs.DocumentService | None" = None, issuer: Any = None) -> APIRouter:
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

    async def profiles(_r, key):
        return await run(lambda: service.profiles(key))

    async def save_profile(r: Request, key):
        body = json.loads(await fih._body_limited(r, 32 * 1024))
        return await run(lambda: service.save_profile(key, r.path_params["pkey"], body.get("label"), body.get("matrix")))

    async def matrix(r: Request, key):
        body = json.loads(await fih._body_limited(r, 32 * 1024))
        return await run(lambda: service.set_matrix(key, r.path_params["ref"], body.get("profile"), body.get("matrix"), bool(body.get("view_pii"))))

    async def audit(r: Request, key):
        return await run(lambda: service.audit(key, qint(r, "limit", 100)))

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
    add("/permissions/profiles", ["GET"], profiles)
    add("/permissions/profiles/{pkey}", ["PUT"], save_profile)
    add("/devices/{ref}/matrix", ["PUT"], matrix)
    add("/audit", ["GET"], audit)
    if issuer is not None:
        async def iss_templates(_r, key):
            return await run(lambda: issuer.templates(key))

        async def iss_issue(r: Request, key):
            body = json.loads(await fih._body_limited(r, 16 * 1024))
            return await run(lambda: issuer.issue(key, str(body.get("kind") or ""), str(body.get("entity_uid") or ""), body.get("fields")))

        async def iss_next(r: Request, key):
            return await run(lambda: issuer.suggest(key, r.query_params.get("kind", "")))

        async def iss_list(r: Request, key):
            return await run(lambda: issuer.list(key, r.query_params.get("entity_uid", ""), r.query_params.get("kind", ""), qint(r, "limit", 100)))

        async def iss_pdf(request: Request):
            secret = ""
            try:
                secret = fih._binding_key(request)
                try:
                    if request.path_params["uid"] == "preview":
                        data, name = await run(lambda: issuer.preview(secret, request.query_params.get("kind", ""), request.query_params.get("entity_uid", ""))), "previzualizare.pdf"
                    else:
                        data, name = await run(lambda: issuer.pdf(secret, request.path_params["uid"]))
                except cc.ScopeError as exc:
                    if str(exc) == "legătură invalidă":
                        raise wire.Unauthenticated("legătură invalidă") from None
                    raise
                return Response(data, media_type="application/pdf", headers={**fih._HEADERS, "X-Content-Type-Options": "nosniff", "Content-Disposition": f'inline; filename="{name}"'})
            except Exception as exc:  # noqa: BLE001
                return fih._error(exc, secret)

        add("/issue/templates", ["GET"], iss_templates)
        add("/issue", ["POST"], iss_issue)
        add("/issue", ["GET"], iss_list)
        add("/issue/next", ["GET"], iss_next)
        router.add_api_route("/issue/{uid}/pdf", iss_pdf, methods=["GET"])
    if docs is not None:
        async def doc_list(r: Request, key):
            return await run(lambda: docs.list(key, qint(r, "since", 0), r.query_params.get("entity", ""), r.query_params.get("entity_uid", ""), qint(r, "limit", 200)))

        async def doc_declare(r: Request, key):
            body = json.loads(await fih._body_limited(r, 16 * 1024))
            return await run(lambda: docs.declare(key, body))

        async def doc_upload(r: Request, key):
            data = await fih._body_limited(r, sdocs.MAX_BYTES)
            return await run(lambda: docs.upload(key, r.path_params["uid"], data))

        async def doc_delete(r: Request, key):
            return await run(lambda: docs.delete(key, r.path_params["uid"]))

        async def doc_file(request: Request):
            secret = ""
            try:
                secret = fih._binding_key(request)
                try:
                    f = await run(lambda: docs.fetch(secret, request.path_params["uid"]))
                except cc.ScopeError as exc:
                    if str(exc) == "legătură invalidă":
                        raise wire.Unauthenticated("legătură invalidă") from None
                    raise
                return Response(f["data"], media_type=f["mime"], headers={**fih._HEADERS, "X-Content-SHA256": f["sha256"], "X-Content-Type-Options": "nosniff",
                                                                          "Content-Disposition": 'inline; filename="document"'})
            except Exception as exc:  # noqa: BLE001
                return fih._error(exc, secret)

        add("/documents", ["GET"], doc_list)
        add("/documents/declare", ["POST"], doc_declare)
        add("/documents/{uid}/content", ["PUT"], doc_upload)
        add("/documents/{uid}", ["DELETE"], doc_delete)
        router.add_api_route("/documents/{uid}/file", doc_file, methods=["GET"])
    return router
