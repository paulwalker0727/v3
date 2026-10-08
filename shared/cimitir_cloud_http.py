"""cimitir_cloud_http.py — FastAPI ca STRAT DE TRANSPORT peste serviciile testate (Etapa 4, Pasul 5). Simulator SQLite local; fără Supabase/PostgreSQL/Render.

Doar rute fixe (tabelul OPS din cimitir_cloud_wire) — nu există rută generică spre funcții/DB. Scope-ul (organizație/cimitir/actor) se DERIVĂ pe server din autorizarea
`Pairing <cod>` (înainte de binding) sau `Binding <cheie>` (după redeem); ce trimite clientul în corp este doar verificat față de scope, niciodată crezut.
Secretele vin numai în header; nu apar în URL, log, traceback sau răspunsuri de eroare. Idempotența vine din serviciile subiacente (retry sigur după răspuns pierdut).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import cimitir_cloud_control as cc
import cimitir_cloud_media as cm
import cimitir_cloud_publish as cpub
import cimitir_cloud_staging as cs
import cimitir_cloud_wire as wire
import cimitir_plan_guard as pg

log = logging.getLogger("cimitir.cloud_http")
PRE_BINDING_BOOTSTRAP = {"preflight_for_code", "begin_run", "redeem_code"}
USED_CODE_OK = {"begin_run", "redeem_code", "mark_failed_unauthenticated", "get_run"}
MODULE_OPS = {"staging_open": cs.staging_open, "staging_status": cs.staging_status, "staging_put_batch": cs.staging_put_batch, "staging_snapshot": cs.staging_snapshot,
              "staging_cleanup": cs.staging_cleanup, "media_put_chunk": cm.media_put_chunk, "media_finalize": cm.media_finalize, "media_verify": cm.media_verify,
              "media_cleanup": cm.media_cleanup, "publish_flow_b": cpub.publish_flow_b}


class Ctx:
    def __init__(self, kind: str, secret: str, actor: str, org: str, cem: Optional[str], binding_uid: Optional[str] = None):
        self.kind, self.secret, self.actor, self.org, self.cem, self.binding_uid = kind, secret, actor, org, cem, binding_uid


def _used_code_ok(cp, row, op: str) -> bool:
    """Un cod consumat rămâne valid pentru reluarea bootstrap-ului CARE l-a consumat (cât timp rulează); citirile cer run-ul în curs."""
    if op in USED_CODE_OK:
        return True
    if op not in ("can", "export_snapshot") or not row["used_bootstrap_id"]:
        return False
    c = cp._c()
    try:
        r = c.execute("SELECT state FROM bootstrap_runs WHERE bootstrap_id=?", (row["used_bootstrap_id"],)).fetchone()
    finally:
        c.close()
    return bool(r and r["state"] in ("planned", "running", "verifying"))


def _authenticate(cp, header: str, op: str) -> Ctx:
    scheme, _, secret = (header or "").partition(" ")
    auth = wire.OPS[op][1]
    if not secret:
        raise wire.Unauthenticated("autentificare lipsă")
    if scheme == "Pairing" and auth in ("pairing", "either"):
        c = cp._c()
        try:
            row = c.execute("SELECT * FROM pairing_codes WHERE code_hash=?", (cc._h(secret),)).fetchone()
        finally:
            c.close()
        if not row or row["expires_at"] < cp.clock() or (row["used_at"] and not _used_code_ok(cp, row, op)):
            raise wire.Unauthenticated("cod de pairing invalid sau expirat")
        ctx = Ctx("pairing", secret, row["issued_by"], row["organization_uid"], row["cemetery_uid"])
        if op in PRE_BINDING_BOOTSTRAP and not cp.can(ctx.actor, "bootstrap", ctx.org):
            raise cc.PermissionDenied("capabilitatea BOOTSTRAP lipsește pentru emitentul codului")
        return ctx
    if scheme == "Binding" and auth in ("binding", "either"):
        try:
            b = cp.authenticate(secret, None, allow_pending=(op != "authenticate"))
        except cc.ScopeError:
            raise wire.Unauthenticated("legătură invalidă") from None
        c = cp._c()
        try:
            iss = c.execute("SELECT principal_uid FROM binding_issuers WHERE binding_uid=?", (b["binding_uid"],)).fetchone()
        finally:
            c.close()
        if not iss:
            raise wire.Unauthenticated("legătură fără emitent")
        if op != "authenticate" and not cp.can(iss["principal_uid"], "bootstrap", b["organization_uid"]):
            raise cc.PermissionDenied("capabilitatea BOOTSTRAP lipsește")
        return Ctx("binding", secret, iss["principal_uid"], b["organization_uid"], b["cemetery_uid"], b["binding_uid"])
    raise wire.Unauthenticated("schemă de autentificare nepermisă pentru această operație")


def _check_scope(ctx: Ctx, org: Any = None, cem: Any = None) -> None:
    if (org is not None and org != ctx.org) or (cem is not None and ctx.cem is not None and cem != ctx.cem):
        raise cc.ScopeError("resursă inexistentă în scope-ul autorizării")


def _run_in_scope(cp, ctx: Ctx, bid: str) -> Dict[str, Any]:
    run = cp.get_run(pg.require_bid(bid))
    if run["organization_uid"] != ctx.org or (ctx.cem is not None and run["cemetery_uid"] != ctx.cem):
        raise cc.ScopeError("bootstrap inexistent în scope")
    return run


def _dispatch(cp, ctx: Ctx, op: str, args: list, kw: dict) -> Any:
    if op in MODULE_OPS:
        _actor, org, cem, *rest = args
        _check_scope(ctx, org, cem)
        pg.check_transfer(cp, ctx.binding_uid, ctx.org, ctx.cem, op, rest)  # Pasul 5.1: run B real al acestui binding, același plan/sursă, înainte de scriere
        if op == "publish_flow_b":
            rest[1] = ctx.secret
        return MODULE_OPS[op](cp, ctx.actor, ctx.org, ctx.cem, *rest, **{k: v for k, v in kw.items() if k != "hook"})
    if op == "can":
        _check_scope(ctx, args[2])
        return cp.can(ctx.actor, args[1], ctx.org)
    if op == "export_snapshot":
        _check_scope(ctx, args[1], args[2])
        return cp.export_snapshot(ctx.actor, ctx.org, args[2])
    if op == "preflight_for_code":
        _check_scope(ctx, None, args[2])
        return cp.preflight_for_code(ctx.secret, args[1], args[2])
    if op == "begin_run":
        _check_scope(ctx, None, args[4])
        if not kw.get("plan"):
            raise pg.PlanRejected("planul lipsește: serverul nu începe un bootstrap fără plan verificabil")
        pg.require_bid(args[2])
        return cp.begin_run(ctx.secret, args[1], args[2], args[3], args[4], args[5], plan=kw["plan"])
    if op == "redeem_code":
        _check_scope(ctx, None, args[3])
        if not kw.get("bootstrap_id"):
            raise pg.PlanRejected("pairing-ul se face doar în cadrul unui bootstrap validat")
        pg.require_bid(kw["bootstrap_id"])
        return cp.redeem_code(ctx.secret, args[1], args[2], args[3], args[4] if len(args) > 4 else "", **kw)
    if op == "mark_failed_unauthenticated":
        _run_in_scope(cp, ctx, args[0])
        return cp.mark_failed_unauthenticated(args[0], args[1], args[2])
    if op == "get_run":
        return _run_in_scope(cp, ctx, args[0])
    if op == "authenticate":
        cem = args[1] if len(args) > 1 else None
        _check_scope(ctx, None, cem)
        return {k: v for k, v in cp.authenticate(ctx.secret, cem).items() if k != "key_hash"}
    if op == "revoke_by_key":
        return cp.revoke_by_key(ctx.secret)
    _run_in_scope(cp, ctx, args[1])
    return getattr(cp, op)(ctx.secret, *args[1:])


def _error(exc: BaseException, secret: str) -> JSONResponse:
    for cls, status, code in wire.ERRORS:
        if isinstance(exc, cls):
            return JSONResponse({"ok": False, "code": code, "message": str(exc).replace(secret, "***") if secret else str(exc)}, status_code=status)
    if isinstance(exc, (ValueError, KeyError, TypeError, IndexError, AttributeError)):
        return JSONResponse({"ok": False, "code": "REQUEST_INVALID", "message": "cerere invalidă"}, status_code=400)
    log.error("eroare internă: %s", type(exc).__name__)  # doar clasa: fără mesaj/traceback (pot conține date sensibile)
    return JSONResponse({"ok": False, "code": "INTERNAL", "message": "eroare internă"}, status_code=500)


def create_app(control_db: str, fault: Optional[Dict[str, Any]] = None, clock: Callable[[], float] = time.time) -> FastAPI:
    """`fault` (doar pentru teste, injectat de lansatorul de test): {"op", "nth", "mode": "kill"|"delay", "delay"} — acționează DUPĂ commit, ÎNAINTE de răspuns."""
    import asyncio

    import anyio
    cp = cc.LocalControlPlane(control_db, clock=clock)
    app = FastAPI(title="Cimitir cloud simulator transport", docs_url=None, redoc_url=None, openapi_url=None)
    seen: Dict[str, int] = {}

    def make(op: str):
        async def handler(request: Request):
            secret = ""
            try:
                raw = await request.body()
                body = wire.decode(__import__("json").loads(raw)) if raw else {}
                header = request.headers.get("authorization", "")
                secret = header.partition(" ")[2]
                args, kw = body.get("args", []), body.get("kwargs", {})
                if not isinstance(args, list) or not isinstance(kw, dict):
                    raise ValueError("corp invalid")
                result = await anyio.to_thread.run_sync(lambda: _dispatch(cp, _authenticate(cp, header, op), op, args, kw))
            except Exception as exc:  # noqa: BLE001
                return _error(exc, secret)
            if fault and fault["op"] == op:
                seen[op] = seen.get(op, 0) + 1
                if seen[op] == fault.get("nth", 1):
                    if fault["mode"] == "kill":
                        os._exit(137)
                    await asyncio.sleep(fault.get("delay", 2))
            return JSONResponse({"ok": True, "result": wire.encode(result)})
        return handler

    for op, (path, _auth) in wire.OPS.items():
        app.add_api_route(wire.API + path, make(op), methods=["POST"], name=op)

    @app.get(wire.API + "/health")
    def health():
        return {"ok": True, "version": 1}

    return app
