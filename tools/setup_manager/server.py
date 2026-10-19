"""Serverul local Setup Manager (FastAPI). Ascultă DOAR pe 127.0.0.1; token de sesiune + verificare Host/Origin; mod implicit Dry Run."""
from __future__ import annotations

import json
import secrets
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from . import checks, dockermgr, guard, migrations as mig, pairing, report
from .config import ConfigStore, build_db_url, migrations_dir
from .redact import redact, redact_obj

STATIC = Path(__file__).parent / "static"
EXEC_PHRASE = "ACTIVEAZA SCRIEREA TEST"
MODE_TTL = 15 * 60
LOCAL_HOSTS = {"127.0.0.1", "localhost"}


class ModeState:
    def __init__(self):
        self._mode, self._until = "dry_run", 0.0
        self._lock = threading.Lock()

    @property
    def mode(self) -> str:
        with self._lock:
            if self._mode == "execute" and time.time() > self._until:
                self._mode = "dry_run"
            return self._mode

    def remaining(self) -> int:
        return max(0, int(self._until - time.time())) if self.mode == "execute" else 0

    def set(self, mode: str) -> None:
        with self._lock:
            self._mode, self._until = mode, time.time() + MODE_TTL if mode == "execute" else 0.0

    def reset(self) -> None:
        self.set("dry_run")


class ConfigPatch(BaseModel):
    values: Dict[str, Any]


class ModeReq(BaseModel):
    mode: str
    confirm: str = ""


class ApplyReq(BaseModel):
    plan_hash: str
    confirm: str = ""
    backup_path: str = ""


class BackupReq(BaseModel):
    path: str


class ConfirmReq(BaseModel):
    confirm: str = ""


class DockerReq(BaseModel):
    kind: str
    confirm: str = ""


class IsolatedReq(BaseModel):
    which: str
    approval: str = ""
    org_uid: str = ""
    email: str = ""
    password: str = ""


class ManualReq(BaseModel):
    step: str
    status: str
    note: str = ""


def create_app(store: Optional[ConfigStore] = None, net: Optional[checks.RealNet] = None, docker=None, token: Optional[str] = None,
               port: int = 8765) -> FastAPI:
    store = store or ConfigStore()
    net = net or checks.RealNet()
    docker = docker or dockermgr.run_docker
    token = token or secrets.token_urlsafe(24)
    mode = ModeState()
    jobs = dockermgr.Jobs()
    checklist = pairing.Checklist(store.home)
    last: Dict[str, Any] = {}
    app = FastAPI(title="Cimitir Setup Manager", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.token, app.state.mode, app.state.store, app.state.last = token, mode, store, last

    @app.middleware("http")
    async def security(request: Request, call_next):
        host = (request.headers.get("host") or "").split(":")[0].lower()
        if host not in LOCAL_HOSTS:
            return JSONResponse({"error": "Host nepermis"}, status_code=403)
        if request.method in ("POST", "PUT", "DELETE"):
            origin = request.headers.get("origin")
            if origin and (origin.split("//")[-1].split(":")[0].lower() not in LOCAL_HOSTS):
                return JSONResponse({"error": "Origin nepermis"}, status_code=403)
        resp: Response = await call_next(request)
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        return resp

    def auth(request: Request) -> None:
        if not secrets.compare_digest(request.headers.get("x-sm-token", ""), token):
            raise HTTPException(401, "Token de sesiune lipsă sau invalid")

    def cfg() -> Dict[str, Any]:
        return store.effective()

    def gerr(exc: Exception) -> HTTPException:
        return HTTPException(400, redact(str(exc)))

    def guarded(fn):
        try:
            return fn()
        except guard.GuardError as exc:
            raise gerr(exc)

    def db_url() -> str:
        guarded(lambda: guard.assert_read_allowed(cfg()))
        return build_db_url(cfg())

    def analyze_db() -> Dict[str, Any]:
        migs = mig.load_migrations(migrations_dir(cfg()))
        conn = net.pg(db_url())
        try:
            try:
                conn.read_only = True
            except Exception:  # noqa: BLE001
                pass
            a = mig.analyze(conn, migs)
            conn.rollback()
        finally:
            conn.close()
        return {"migs": migs, "analysis": a, "plan": mig.build_plan(a)}

    @app.get("/", response_class=HTMLResponse)
    def index():
        html = (STATIC / "index.html").read_text(encoding="utf-8").replace("__SM_TOKEN__", token)
        return HTMLResponse(html)

    @app.get("/assets/{name}")
    def asset(name: str):
        types = {"app.js": "application/javascript", "app.css": "text/css"}
        if name not in types:
            raise HTTPException(404)
        return Response((STATIC / name).read_bytes(), media_type=types[name])

    @app.get("/api/state", dependencies=[Depends(auth)])
    def state():
        c = cfg()
        issues: List[str] = []
        prod_blocked = False
        try:
            guard.assert_read_allowed(c)
        except guard.GuardError as exc:
            prod_blocked = True
            issues.append(str(exc))
        issues += guard.consistency_issues(c)
        return {"mode": mode.mode, "mode_remaining": mode.remaining(), "config": store.public(), "prod_blocked": prod_blocked, "issues": issues,
                "protected": {"prod_refs": list(guard.PROTECTED_PROD_REFS), "preserved_refs": list(guard.PRESERVED_TEST_REFS), "ports": list(guard.PROTECTED_PORTS)},
                "exec_phrase": EXEC_PHRASE, "approval_phrase": pairing.APPROVAL_PHRASE, "checks": last.get("checks", [])}

    @app.put("/api/config", dependencies=[Depends(auth)])
    def put_config(req: ConfigPatch):
        try:
            store.update(req.values)
        except ValueError as exc:
            raise gerr(exc)
        return state()

    @app.post("/api/mode", dependencies=[Depends(auth)])
    def set_mode(req: ModeReq):
        if req.mode == "dry_run":
            mode.reset()
        elif req.mode == "execute":
            if req.confirm != EXEC_PHRASE:
                raise HTTPException(400, f"Confirmare incorectă. Tastează exact: {EXEC_PHRASE}")
            guarded(lambda: guard.assert_write_allowed(cfg()))
            mode.set("execute")
        else:
            raise HTTPException(400, "Mod invalid")
        return {"mode": mode.mode, "mode_remaining": mode.remaining()}

    @app.post("/api/checks", dependencies=[Depends(auth)])
    def run_checks():
        res = checks.Checker(cfg(), net).run_all()
        last["checks"], last["checks_at"] = res, time.strftime("%Y-%m-%d %H:%M:%S")
        return {"results": res, "summary": {s: sum(1 for r in res if r["status"] == s) for s in ("PASS", "FAIL", "WARN", "SKIP")}}

    @app.get("/api/migrations/plan", dependencies=[Depends(auth)])
    def migrations_plan():
        try:
            d = analyze_db()
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, redact(checks.explain_pg_error(exc)))
        last["plan"], last["plan_at"] = d["plan"], time.strftime("%Y-%m-%d %H:%M:%S")
        return {"plan": d["plan"], "migrations": [{k: v for k, v in r.items() if k != "sha256"} for r in d["analysis"]["migrations"]],
                "history_table": d["analysis"]["history_table"], "duplicates": d["analysis"]["duplicates"], "mode": mode.mode}

    @app.post("/api/migrations/backup", dependencies=[Depends(auth)])
    def backup(req: BackupReq):
        store.update({"backup_path": req.path})
        return mig.verify_backup(req.path)

    @app.post("/api/migrations/apply", dependencies=[Depends(auth)])
    def apply(req: ApplyReq):
        c = cfg()
        guarded(lambda: guard.assert_write_allowed(c))
        try:
            d = analyze_db()
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, redact(checks.explain_pg_error(exc)))
        plan = d["plan"]
        if plan["plan_hash"] != req.plan_hash:
            raise HTTPException(409, "Planul s-a schimbat; regenerează-l.")
        bk = mig.verify_backup(req.backup_path or c.get("backup_path", ""))
        try:
            res = mig.apply_plan(build_db_url(c), c, d["migs"], plan, req.confirm, bk, mode.mode, connect=net.pg)
        except guard.GuardError as exc:
            raise gerr(exc)
        if res.get("executed") or res.get("rolled_back"):
            mode.reset()
            for k in ("checks", "checks_at", "plan", "plan_at"):
                last.pop(k, None)
        return redact_obj({**res, "backup": bk})

    @app.post("/api/storage/bucket", dependencies=[Depends(auth)])
    def create_bucket(req: ConfirmReq):
        c = cfg()
        guarded(lambda: guard.assert_write_allowed(c))
        bucket = c.get("storage_bucket", "")
        phrase = f"CREEAZA BUCKET {bucket}"
        if mode.mode != "execute":
            return {"executed": False, "dry_run": True, "message": f"Dry Run: ar crea bucketul PRIVAT „{bucket}”. Confirmare cerută: {phrase}"}
        if req.confirm != phrase:
            raise HTTPException(400, f"Confirmare incorectă. Tastează exact: {phrase}")
        key = c.get("supabase_secret_key", "")
        code, body = net.http("POST", f"{c['supabase_url'].rstrip('/')}/storage/v1/bucket", {"Authorization": f"Bearer {key}", "apikey": key, "Content-Type": "application/json"},
                              15, json.dumps({"id": bucket, "name": bucket, "public": False}).encode())
        mode.reset()
        return {"executed": code in (200, 201), "message": f"HTTP {code}", "detail": redact(body.decode("utf-8", "ignore")[:200])}

    @app.get("/api/docker/status", dependencies=[Depends(auth)])
    def docker_status():
        try:
            return dockermgr.status(cfg(), docker)
        except Exception as exc:  # noqa: BLE001
            return {"docker": False, "error": redact(f"{type(exc).__name__}: {exc}")}

    @app.post("/api/docker/plan", dependencies=[Depends(auth)])
    def docker_plan(req: DockerReq):
        if req.kind not in ("build", "start", "update"):
            raise HTTPException(400, "Tip invalid")
        try:
            return redact_obj(dockermgr.plan(cfg(), req.kind, docker))
        except guard.GuardError as exc:
            raise gerr(exc)

    @app.post("/api/docker/run", dependencies=[Depends(auth)])
    def docker_run(req: DockerReq):
        c = cfg()
        if req.kind not in ("build", "start", "update"):
            raise HTTPException(400, "Tip invalid")
        guarded(lambda: guard.assert_write_allowed(c))
        p = guarded(lambda: dockermgr.plan(c, req.kind, docker))
        phrase = {"build": f"CONSTRUIESC {c.get('image_tag')}", "start": f"PORNESC {c.get('container_name')}", "update": f"ACTUALIZEZ {c.get('container_name')}"}[req.kind]
        if mode.mode != "execute":
            return redact_obj({"executed": False, "dry_run": True, "plan": p, "confirm_phrase": phrase})
        if req.confirm != phrase:
            raise HTTPException(400, f"Confirmare incorectă. Tastează exact: {phrase}")
        if not p["ready"]:
            raise HTTPException(400, "Nu e gata: " + " | ".join(p["issues"]))
        if req.kind == "build":
            jid = jobs.start("Construire imagine", lambda emit: dockermgr.build_image(c, emit))
        else:
            def work(emit):
                r = dockermgr.execute(c, req.kind, docker)
                for ln in r["log"]:
                    emit(ln)
                return r
            jid = jobs.start("Docker " + req.kind, work)
        mode.reset()
        return {"executed": True, "job": jid}

    @app.get("/api/jobs/{jid}", dependencies=[Depends(auth)])
    def job(jid: str):
        j = jobs.get(jid)
        if not j:
            raise HTTPException(404)
        return j

    @app.get("/api/docker/logs", dependencies=[Depends(auth)])
    def docker_logs():
        name = guarded(lambda: guard.assert_container_ok(cfg().get("container_name")))
        try:
            rc, out, err = docker(["logs", "--tail", "80", name], 30)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"logs": f"Docker indisponibil: {type(exc).__name__}", "rc": -1}
        return {"logs": redact(out + err)[-6000:], "rc": rc}

    @app.get("/api/pairing", dependencies=[Depends(auth)])
    def pairing_state():
        return {"manual": checklist.view(), "snippet": pairing.main_config_snippet(cfg()), "approval_phrase": pairing.APPROVAL_PHRASE}

    @app.post("/api/pairing/probe", dependencies=[Depends(auth)])
    def pairing_probe():
        res = guarded(lambda: pairing.probe(cfg(), net))
        last["pairing_probe"] = res
        return {"results": res}

    @app.post("/api/pairing/isolated", dependencies=[Depends(auth)])
    def pairing_isolated(req: IsolatedReq):
        c = cfg()
        guarded(lambda: pairing.validate_isolated_target(c, c.get("main_cloud_url") or c.get("cloud_public_url") or ""))
        if mode.mode != "execute":
            return {"executed": False, "dry_run": True, "message": f"Dry Run: testul izolat ({req.which}) ar scrie registre FICTIVE izolate. Necesită modul scriere + aprobarea „{pairing.APPROVAL_PHRASE}”."}

        if req.approval != pairing.APPROVAL_PHRASE:
            raise HTTPException(400, f"Aprobare lipsă. Tastează exact: {pairing.APPROVAL_PHRASE}")
        store.update({"cloud_admin_email": req.email, "cloud_admin_password": req.password})
        c = cfg()

        def work(emit):
            try:
                return pairing.run_isolated(c, req.which, req.approval, "execute", req.org_uid, emit)
            finally:
                store.update({"cloud_admin_email": "", "cloud_admin_password": ""})
        jid = jobs.start("Test izolat " + req.which, work)
        mode.reset()
        return {"executed": True, "job": jid}

    @app.put("/api/pairing/manual", dependencies=[Depends(auth)])
    def pairing_manual(req: ManualReq):
        try:
            checklist.set(req.step, req.status, req.note)
        except ValueError as exc:
            raise gerr(exc)
        last["checklist"] = checklist.view()
        return {"manual": checklist.view()}

    @app.post("/api/report", dependencies=[Depends(auth)])
    def make_report():
        last["checklist"] = checklist.view()
        text = report.render(store.public(), {"mode": mode.mode, **last})
        path = report.save(store.home, text)
        return {"path": str(path), "text": text}

    return app
