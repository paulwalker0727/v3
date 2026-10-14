"""
Cimitir Web — aplicație 100% Python (FastAPI + Jinja2 + HTMX).

Înlocuiește frontend-ul React cu pagini randate server-side. Backend-ul de date
(`cimitir/`) rămâne neschimbat: SQLite prin facade-ul `store`.

Rulare locală:   python desktop/start_local.py   (sau: python -m uvicorn server:app --port 8000)
Aici (Emergent): supervisor rulează uvicorn server:app pe portul 8001.
"""
import logging
import os
import re
import uuid
from urllib.parse import urlparse
import sys
import io
import json
import shutil
import tempfile
from datetime import date, datetime, timezone, timedelta
from typing import Optional
from pathlib import Path
from urllib.parse import urlencode

ROOT_DIR = Path(os.environ.get("CIMITIR_BACKEND_DIR") or Path(__file__).parent)
_SHARED_DIR = str(ROOT_DIR.parent / "shared")
if _SHARED_DIR not in sys.path:
    sys.path.insert(0, _SHARED_DIR)

import cimitir_config  # noqa: E402
import cimitir_backup  # noqa: E402

RESTORE_MESSAGE = cimitir_backup.apply_pending_restore(cimitir_config.working_dir())
if RESTORE_MESSAGE:
    print(f"[backup] {RESTORE_MESSAGE}")

cimitir_config.load_env()
cimitir_config.data_dir()

CIMITIR_DIR = str(ROOT_DIR / "cimitir")
if CIMITIR_DIR not in sys.path:
    sys.path.insert(0, CIMITIR_DIR)
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import jwt  # noqa: E402
from fastapi import FastAPI, APIRouter, Request, UploadFile, File, Form, HTTPException  # noqa: E402
from fastapi.responses import (FileResponse, Response, RedirectResponse,  # noqa: E402
                               HTMLResponse, JSONResponse)
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402
from starlette.middleware.cors import CORSMiddleware  # noqa: E402
from psycopg import errors as psycopg_errors  # noqa: E402
import psycopg  # noqa: E402


class _DbConnLogFilter(logging.Filter):
    """psycopg.pool scrie în log mesaje de conectare cu host, adrese IP și utilizator. Le reducem la motiv (fără detalii de rețea/cont)."""
    _REASON = re.compile(r"(?:FATAL|ERROR):\s*([^\n\r]+)|(Connection refused|timeout expired|could not translate host name|Name or service not known|Network is unreachable|SSL[^\n\r]*)", re.I)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        low = msg.lower()
        if "connect" in low and ("host" in low or "server at" in low or "port" in low):
            m = self._REASON.search(msg)
            reason = (m.group(1) or m.group(2)) if m else "conectare eșuată"
            reason = re.sub(r"\s+for user \"[^\"]*\"", "", reason).strip()[:120]
            record.msg, record.args = f"PostgreSQL: conectare eșuată ({reason})", ()
        return True


for _lg in ("psycopg.pool", "psycopg"):
    logging.getLogger(_lg).addFilter(_DbConnLogFilter())


import store as cim          # noqa: E402
from cimitir_loc_options import (LOC_SELECT_FIELDS, is_allowed_value, loc_observatii,  # noqa: E402
                                 observatii_metadata, options_for)
import auth as cim_auth      # noqa: E402
import sync_api               # noqa: E402
import entity_sync_api        # noqa: E402
import media_api              # noqa: E402
import mobile_upload          # noqa: E402
import system_admin           # noqa: E402
import cimitir_notify         # noqa: E402
import cimitir_public         # noqa: E402
import cimitir_requests       # noqa: E402
import audit_entities         # noqa: E402
audit_entities.install()      # audit pentru locuri/concesionari/contracte/înhumați/plăți modificate din panoul web
import citizen_api            # noqa: E402
import web_public            # noqa: E402

JWT_ALG = "HS256"
COOKIE = "cim_session"


def _jwt_secret() -> str:
    return cimitir_config.jwt_secret()


# ---- sursă implicită: <DATA_DIR>/cimitir.db (creată gol la primul start; demo: scripts/make_demo_db.py) -----
def _bootstrap_default_source():
    if cimitir_config.db_engine() == "postgres":
        # PostgreSQL online: o singură bază, configurată doar prin DATABASE_URL (schema se migrează automat).
        src = dict(cim.POSTGRES_SOURCE)
        try:
            cim.ensure_initialized(src)
        except Exception as e:
            import pg_compat
            print(f"[bootstrap] PostgreSQL indisponibil: {type(e).__name__}: "
                  f"{pg_compat.redact(e, os.environ.get('DATABASE_URL'))}")
        return src
    default_path = os.path.join(cim.DATA_DIR, "cimitir.db")
    import cimitir_registry
    if cimitir_registry.catalog_exists(cim.DATA_DIR):  # registre locale (Etapa 3): baza = registrul activ din catalog
        try:
            binding = cimitir_registry.binding_from_catalog(cim.DATA_DIR)
            if binding is not None:
                cimitir_registry.ensure_prepared(binding)
                return {"id": "cimitir", "name": "Cimitir", "engine": "sqlite", "path": str(binding.database)}
        except Exception as e:
            print(f"[bootstrap] registrul activ nu poate fi deschis: {type(e).__name__}: {e}")
        if not cimitir_registry.legacy_fallback_allowed(cim.DATA_DIR):  # nu creăm cimitir.db; așteptăm registrul din MAIN
            return {"id": "cimitir", "name": "Cimitir", "engine": "sqlite", "path": default_path}
    src = {"id": "cimitir", "name": "Cimitir", "engine": "sqlite", "path": default_path}
    try:
        cim.ensure_initialized(src)
    except Exception as e:
        print(f"[bootstrap] {e}")
    return src


DEFAULT_SOURCE = _bootstrap_default_source()
STARTUP_INFO = cimitir_config.log_startup("backend")

app = FastAPI(title="Cimitir Web — Python")


@app.get("/api/public/status")
def api_status():
    """Stare server + bază de date (public, fără secrete): server online, PostgreSQL conectat, motor activ."""
    engine = cim.process_engine()
    if engine == "postgres":
        import postgres_store
        db_info = postgres_store.health()
        db_info = {k: db_info.get(k) for k in ("engine", "connected", "schema_version", "latency_ms", "error")
                   if k in db_info}
    else:
        try:
            cim.bind_source(DEFAULT_SOURCE)
            cim.get_setting("cemetery_id")
            connected, err = True, None
        except Exception as exc:  # pragma: no cover
            connected, err = False, type(exc).__name__
        db_info = {"engine": "sqlite", "connected": connected}
        if err:
            db_info["error"] = err
    ok = bool(db_info.get("connected"))
    return JSONResponse({"server": "online", "status": "ok" if ok else "degraded", "mode": cimitir_config.mode(),
                         "database": db_info}, status_code=200 if ok else 503)



@app.get("/api/health")
def api_health():
    """Stare neconfidențială: motor DB, stare DB, stare server, versiune, oră. Nu expune DATABASE_URL, utilizator/parolă DB sau JWT_SECRET."""
    engine = cim.process_engine()
    try:
        if engine == "postgres":
            import postgres_store
            db_online = bool(postgres_store.health().get("connected"))
        else:
            cim.bind_source(DEFAULT_SOURCE)
            cim.get_setting("cemetery_id")
            db_online = True
    except Exception:
        db_online = False
    body = {"database": engine,
            "database_status": "online" if db_online else "offline",
            "server_status": "online",
            "version": cimitir_config.app_version(),
            "time": datetime.now(timezone.utc).isoformat()}
    return JSONResponse(body, status_code=200 if db_online else 503,
                        headers={"Cache-Control": "no-store"})


@app.on_event("startup")
def _start_system_logging():
    system_admin.install_logging()


@app.on_event("startup")
def _start_background_jobs():
    import portal_jobs
    portal_jobs.start(DEFAULT_SOURCE)
admin = APIRouter(prefix="/admin")
api_admin = APIRouter(prefix="/api/admin")
templates = Jinja2Templates(directory=str(ROOT_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(ROOT_DIR / "static")), name="static")


# =============================================================================
# STATUS helpers
# =============================================================================
def plot_status(loc: dict, expired_cods: set) -> str:
    cod = loc.get("cod_loc") or ""
    if cod in expired_cods:
        return "expirat"
    disp = str(loc.get("disponibilitate") or "").lower()
    stare = str(loc.get("stare") or "").lower()
    ocupat = loc.get("ocupat")
    if "rezerv" in disp or "rezerv" in stare:
        return "rezervat"
    if ocupat in (1, "1", True) or "ocupat" in disp or "ocupat" in stare or "indisponibil" in disp:
        return "ocupat"
    return "liber"


def _expired_cods() -> set:
    out = set()
    try:
        for c in cim.expiring_contracts(0):
            if c.get("cod_loc"):
                out.add(c["cod_loc"])
    except Exception:
        pass
    return out


STATUS_META = {
    "liber": {"label": "Liber", "fill": "#10b981", "stroke": "#059669", "dot": "#10b981"},
    "ocupat": {"label": "Ocupat", "fill": "#ef4444", "stroke": "#dc2626", "dot": "#ef4444"},
    "rezervat": {"label": "Rezervat", "fill": "#f59e0b", "stroke": "#d97706", "dot": "#f59e0b"},
    "expirat": {"label": "Expirat", "fill": "#64748b", "stroke": "#475569", "dot": "#64748b"},
}
STATUS_KEYS = ["liber", "ocupat", "rezervat", "expirat"]


def lei(n) -> str:
    try:
        v = float(n or 0)
    except Exception:
        v = 0.0
    s = f"{v:,.2f}".replace(",", " ").replace(".", ",")
    return f"{s} lei"


# expose helpers to Jinja
templates.env.globals.update(STATUS_META=STATUS_META, STATUS_KEYS=STATUS_KEYS)
templates.env.filters["lei"] = lei


# =============================================================================
# AUTH — cookie session
# =============================================================================
REGISTRY_CHANGED_MSG = "Registrul activ s-a schimbat. Reîncarcă pagina și autentifică-te din nou."


class RegistryChanged(HTTPException):
    """Sesiunea a fost emisă pentru alt registru decât cel activ acum (MAIN a comutat): fail closed, 409."""

    def __init__(self):
        super().__init__(status_code=409, detail=REGISTRY_CHANGED_MSG)


def _active_registry_uid() -> Optional[str]:
    import cimitir_registry
    b = cimitir_registry.current_binding()
    return b.cemetery_uid if b is not None else None


def _registry_label() -> Optional[str]:
    """„Organizație — Cimitir” pentru registrul activ (None în afara modului registre)."""
    import cimitir_registry
    b = cimitir_registry.current_binding()
    if b is None:
        return None
    try:
        cat = cimitir_registry.RegistryCatalog(cimitir_config.data_dir())
        cem = cat.get_cemetery(b.cemetery_uid) or {}
        org = cat.get_organization(b.organization_uid) or {}
    except Exception:  # noqa: BLE001
        return None
    cem_name = (cem.get("name") or "").strip()
    org_name = (org.get("display_name") or org.get("official_name") or "").strip()
    return " — ".join(x for x in (org_name, cem_name) if x) or None


def create_token(user: dict, source_id: str) -> str:
    payload = {"sub": str(user["id"]), "username": user["username"], "role": user["role"],
               "source_id": source_id, "type": "access", "mcp": bool(user.get("must_change_password")),
               "cemetery_uid": _active_registry_uid(),
               "jti": uuid.uuid4().hex, "exp": datetime.now(timezone.utc) + timedelta(hours=12)}
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALG)


def _revoked_key(jti: str) -> str:
    return f"revoked_session:{jti}"


def revoke_token(token: str) -> bool:
    """Invalidează server-side sesiunea (jti) până la expirarea naturală a tokenului. Întoarce True dacă a fost revocată."""
    try:
        p = _decode_and_bind(token, check_revoked=False)
    except Exception:  # noqa: BLE001 - token invalid/expirat: nimic de revocat
        return False
    jti = p.get("jti")
    if not jti:
        return False
    cim.set_setting(_revoked_key(jti), str(int(p.get("exp") or 0)))
    # igienă: ștergem revocările deja expirate (tokenul oricum nu mai e valid)
    conn = cim.get_connection()
    try:
        conn.execute("DELETE FROM settings WHERE key LIKE 'revoked_session:%' AND CAST(value AS BIGINT) < ?", (int(datetime.now(timezone.utc).timestamp()),))
    except Exception:  # noqa: BLE001
        pass
    finally:
        conn.close()
    return True


def _cookie_secure(request: Request) -> bool:
    """Cookie `Secure` când cererea vine prin HTTPS (direct sau prin proxy cu X-Forwarded-Proto) sau forțat din mediu.
    Pe http://localhost (dezvoltare) rămâne nesetat, altfel browserul nu ar mai trimite cookie-ul."""
    if os.environ.get("CIMITIR_COOKIE_SECURE", "").strip() == "1":
        return True
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "").split(",")[0].strip().lower()
    return proto == "https"


def _decode_and_bind(token: str, check_revoked: bool = True) -> dict:
    p = jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALG])
    active = _active_registry_uid()
    if active is not None and p.get("cemetery_uid") != active:
        raise RegistryChanged()
    src = cim.get_source(p.get("source_id") or DEFAULT_SOURCE["id"]) or DEFAULT_SOURCE
    cim.bind_source(src)
    if check_revoked and p.get("jti") and cim.get_setting(_revoked_key(p["jti"])):
        raise jwt.InvalidTokenError("sesiune închisă (logout)")
    return p


def current_user(request: Request) -> Optional[dict]:
    """Returnează userul din cookie + leagă sursa activă, sau None."""
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    try:
        p = _decode_and_bind(token)
    except RegistryChanged:
        raise
    except Exception:
        return None
    src = cim.current_source() or DEFAULT_SOURCE
    fresh = cim_auth.get_session_user(int(p["sub"]), cim.get_setting("cemetery_id") or None)
    if not fresh:
        return None
    audit_entities.ACTOR.set(("staff", fresh["username"]))
    return {"id": fresh["id"], "username": fresh["username"], "role": fresh["role"], "rights": fresh["rights"],
            "source_id": p.get("source_id"),
            "source": {"id": src["id"], "engine": src["engine"],
                       "name": _registry_label() or cim.get_setting("cimitir_nume") or src.get("name")}}


def _soft_user(request: Request) -> Optional[dict]:
    """Ca current_user, dar o sesiune a altui registru = „nelogat” (pagini de login/logout/parolă)."""
    try:
        return current_user(request)
    except RegistryChanged:
        return None


@app.exception_handler(RegistryChanged)
async def _registry_changed_handler(request: Request, exc: RegistryChanged):
    hdr = {"Cache-Control": "no-store"}
    accept = request.headers.get("accept") or ""
    if request.url.path.startswith("/api/") or "application/json" in accept or request.headers.get("x-requested-with") == "fetch":
        return JSONResponse({"detail": REGISTRY_CHANGED_MSG, "code": "registry_changed"}, status_code=409, headers=hdr)
    if request.headers.get("hx-request"):
        return HTMLResponse(f'<div class="registry-changed" data-testid="registry-changed-msg">{REGISTRY_CHANGED_MSG}</div>',
                            status_code=409, headers={**hdr, "X-Registry-Changed": "1"})
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Registru schimbat</title>"
        "<body style='font-family:sans-serif;max-width:34em;margin:4em auto;padding:0 1em'>"
        f"<h2 data-testid='registry-changed-msg'>{REGISTRY_CHANGED_MSG}</h2>"
        "<p><a href='/admin/access' data-testid='registry-changed-login'>Autentificare</a></p>", status_code=409, headers=hdr)


def _ctx(request: Request, user: dict, **extra) -> dict:
    """Context comun pentru template-uri (badge-uri sidebar etc.)."""
    cereri_noi = 0
    expira = 0
    try:
        cereri_noi = sum(1 for c in cim.list_cereri_concesionare() if c.get("status") == "nou")
        expira = len(cim.expiring_contracts(30))
    except Exception:
        pass
    base = {"request": request, "user": user, "badges": {"cereri": cereri_noi, "expira": expira},
            "portal_url": "/portal"}
    base.update(extra)
    return base


def _redirect_login():
    return RedirectResponse(url="/admin/access", status_code=303)


def _require(request: Request):
    user = current_user(request)
    if not user:
        return None
    return user


def _require_right(request: Request, right: str) -> dict:
    """401 fără sesiune validă, 403 fără dreptul cerut (drepturile se reîncarcă din DB la fiecare cerere)."""
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401)
    if right not in user["rights"]:
        raise HTTPException(status_code=403, detail=f"Drept lipsă: {right}")
    return user


def _require_writer(request: Request) -> dict:
    return _require_right(request, "edit")


def _require_admin(request: Request) -> dict:
    return _require_right(request, "manage_users")


# =============================================================================
# ACCESS (public) — selectare bază de date + login
# =============================================================================
@admin.get("/access", response_class=HTMLResponse)
def access_page(request: Request):
    return templates.TemplateResponse(request, "access.html", _access_ctx(request))


@admin.post("/access/login")
def access_login(request: Request, username: str = Form(...), password: str = Form(...),
                 source_id: str = Form(DEFAULT_SOURCE["id"])):
    src = cim.get_source(source_id) or DEFAULT_SOURCE
    try:
        cim.ensure_initialized(src)
    except Exception as e:
        return _access_error(request, f"Baza de date indisponibilă: {e}", source_id)
    user, reason = cim_auth.authenticate_ex(username, password, cim.get_setting("cemetery_id") or None)
    if not user:
        messages = {"locked": "Prea multe încercări eșuate. Cont blocat 5 minute.",
                    "inactive": "Contul este dezactivat.", "wrong_cemetery": "Contul nu este atribuit acestui cimitir.",
                    "lease_expired": "Accesul a expirat. Reconectează-te la server."}
        return _access_error(request, messages.get(reason, "Utilizator sau parolă greșite."), source_id)
    token = create_token(user, src["id"])
    phone = "mobile" in (request.headers.get("user-agent") or "").lower()
    resp = RedirectResponse(url="/admin/schimba-parola" if user.get("must_change_password") else "/admin/harta" if phone else "/admin/", status_code=303)
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=12 * 3600, secure=_cookie_secure(request))
    return resp


@admin.get("/schimba-parola", response_class=HTMLResponse)
def password_page(request: Request):
    user = _soft_user(request)
    if not user:
        return _redirect_login()
    return templates.TemplateResponse(request, "schimba_parola.html", {
        "request": request, "user": user, "forced": _token_needs_change(request), "min_len": cim_auth.MIN_PASSWORD_LEN})


@admin.post("/schimba-parola", response_class=HTMLResponse)
def password_change(request: Request, old_password: str = Form(...), new_password: str = Form(...),
                    confirm_password: str = Form(...)):
    user = _soft_user(request)
    if not user:
        return _redirect_login()
    error = "Parolele noi nu coincid." if new_password != confirm_password else cim_auth.change_own_password(
        user["id"], old_password, new_password)
    if error:
        return templates.TemplateResponse(request, "schimba_parola.html", {
            "request": request, "user": user, "forced": _token_needs_change(request), "error": error,
            "min_len": cim_auth.MIN_PASSWORD_LEN}, status_code=400)
    resp = RedirectResponse(url="/admin/", status_code=303)
    resp.set_cookie(COOKIE, create_token(user, user["source_id"]), httponly=True, samesite="lax", max_age=12 * 3600,
                    secure=_cookie_secure(request))
    return resp


_PASSWORD_EXEMPT = ("/admin/schimba-parola", "/admin/logout", "/admin/access", "/admin/access/login")


def _token_needs_change(request: Request) -> bool:
    try:
        return bool(jwt.decode(request.cookies.get(COOKIE) or "", _jwt_secret(), algorithms=[JWT_ALG]).get("mcp"))
    except Exception:
        return False


def _access_ctx(request, **extra) -> dict:
    sources = [{"id": s["id"], "name": s["name"], "engine": s["engine"], "available": True}
               for s in cim.list_sources()]
    admin_user = _soft_user(request)
    ctx = {"request": request, "sources": sources,
           "is_admin": bool(admin_user and "manage_users" in admin_user["rights"])}
    ctx.update(extra)
    return ctx


def _access_error(request, msg, source_id):
    return templates.TemplateResponse(
        request, "access.html", _access_ctx(request, error=msg, selected_id=source_id), status_code=200)


@admin.post("/access/create-db")
def access_create_db(request: Request, name: str = Form(...)):
    _require_admin(request)
    cim.create_sqlite_source(name)
    return RedirectResponse(url="/admin/access", status_code=303)


@admin.post("/access/upload-db")
async def access_upload_db(request: Request, file: UploadFile = File(...)):
    _require_admin(request)
    fname = file.filename or "cimitir.db"
    if not fname.lower().endswith(".db"):
        return RedirectResponse(url="/admin/access", status_code=303)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    try:
        shutil.copyfileobj(file.file, tmp)
        tmp.close()
        import sqlite3 as _sq
        try:
            c = _sq.connect(tmp.name)
            c.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
            c.close()
        except Exception:
            return RedirectResponse(url="/admin/access", status_code=303)
        base = os.path.splitext(os.path.basename(fname))[0]
        safe = "".join(ch for ch in base if ch.isalnum() or ch in ("-", "_", " ")).strip().replace(" ", "_") or "cimitir_importat"
        dest = os.path.join(cim.DATA_DIR, f"{safe}.db")
        n = 1
        while os.path.exists(dest):
            dest = os.path.join(cim.DATA_DIR, f"{safe}_{n}.db"); n += 1
        shutil.move(tmp.name, dest)
        src = {"id": os.path.splitext(os.path.basename(dest))[0], "name": safe,
               "engine": "sqlite", "path": dest}
        cim.ensure_initialized(src)
    finally:
        if os.path.exists(tmp.name):
            try:
                os.unlink(tmp.name)
            except Exception:
                pass
    return RedirectResponse(url="/admin/access", status_code=303)


@admin.get("/logout")
def logout_get(request: Request):
    """GET nu mai schimbă starea (un link/<img> străin nu poate deconecta utilizatorul). Deconectarea reală e POST /admin/logout."""
    return RedirectResponse(url="/admin/", status_code=303)


@admin.post("/logout")
def logout(request: Request):
    """Deconectare: revocă sesiunea (jti) server-side și șterge cookie-ul. CSRF: request_guard respinge POST din altă origine (403)."""
    token = request.cookies.get(COOKIE)
    who = _soft_user(request) if token else None
    revoked = revoke_token(token) if token else False
    if who:
        cimitir_requests.audit("staff", who["username"], "logout", "", "sesiune revocată" if revoked else "")
    resp = RedirectResponse(url="/admin/access", status_code=303)
    resp.delete_cookie(COOKIE, path="/", httponly=True, samesite="lax", secure=_cookie_secure(request))
    return resp


# =============================================================================
# PAGINI (necesită login)
# =============================================================================
@app.get("/public/loc/{cod_loc}", include_in_schema=False)
def legacy_qr_redirect(cod_loc: str):
    from urllib.parse import quote
    return RedirectResponse(url=f"/portal/loc/{quote(cod_loc, safe='')}", status_code=307)


@app.get("/", include_in_schema=False)
def root_redirect():
    return RedirectResponse(url="/portal", status_code=303)


@admin.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    user = _require(request)
    if not user:
        return _redirect_login()
    expired = _expired_cods()
    locuri = cim.list_locuri()
    counts = {"liber": 0, "ocupat": 0, "rezervat": 0, "expirat": 0}
    for l in locuri:
        st = plot_status(l, expired)
        counts[st] = counts.get(st, 0) + 1
    total = len(locuri)
    ocupate = counts["ocupat"] + counts["expirat"]
    contracte = cim.load_contracte()
    expiring = cim.expiring_contracts(30)
    inhumati = cim.load_all_inhumati()
    plati = cim.list_plati()
    cereri = cim.list_cereri_concesionare()
    data = {
        "total_locuri": total, "status_counts": counts,
        "grad_ocupare": round((ocupate / total * 100) if total else 0, 1),
        "total_contracte": len(contracte), "contracte_expira": len(expiring),
        "expiring": expiring[:10], "total_inhumati": len(inhumati),
        "total_incasari": round(cim.total_plati(), 2), "nr_plati": len(plati),
        "cereri_noi": sum(1 for c in cereri if c.get("status") == "nou"),
        "total_cereri": len(cereri),
        "recent_inhumati": sorted(inhumati, key=lambda x: str(x.get("id") or 0), reverse=True)[:5],
        "recent_cereri": cereri[:5],
    }
    return templates.TemplateResponse(request, "dashboard.html", _ctx(request, user, s=data))


@admin.get("/harta", response_class=HTMLResponse)
def harta(request: Request):
    user = _require(request)
    if not user:
        return _redirect_login()
    return templates.TemplateResponse(request, "harta.html", _ctx(request, user))


_TEREN_SKIP_DOCS = ("carte_identitate", "cerere_doc")


def _teren_cloud_url() -> str:
    u = (os.environ.get("CIMITIR_FIELD_INBOX_URL") or "").strip().rstrip("/")
    p = urlparse(u)
    return u if p.scheme in ("http", "https") and p.hostname else ""


def _teren_loc(cod_loc: str):
    """Locul selectat pe hartă: cod + grave_uid (uid-ul local) + lucrările cu uid (țintă pentru poze lucrare). None dacă nu există."""
    if not cim.load_loc_by_cod(cod_loc):
        return None
    conn = cim.get_connection()
    try:
        r = conn.execute(f"SELECT uid FROM locuri_de_veci WHERE cod_loc = ? AND {cim.tenant_scope()}", (cod_loc,)).fetchone()
        avize = []
        try:
            for a in conn.execute(f"SELECT uid, id, nr_aviz, lucrare FROM amenajari_avize WHERE cod_loc = ? AND uid IS NOT NULL AND uid <> '' AND {cim.tenant_scope()} ORDER BY id", (cod_loc,)).fetchall():
                label = f"{(a['lucrare'] or 'Lucrare').strip()[:40]} #{a['id']}" + (f" (aviz {a['nr_aviz']})" if a["nr_aviz"] else "")
                avize.append({"uid": a["uid"], "label": label})
        except Exception:  # noqa: BLE001 - lucrările nu au încă uid (nimic importat din teren)
            avize = []
    finally:
        conn.close()
    uid = (r["uid"] if r else None) or None
    return {"cod_loc": cod_loc, "grave_uid": uid, "cemetery_uid": _active_registry_uid() or "", "avize": avize}


@admin.get("/teren", response_class=HTMLResponse)
def teren_page(request: Request, cod_loc: str = ""):
    user = _require(request)
    if not user:
        return _redirect_login()
    cem_label = _registry_label() or cim.get_setting("cimitir_nume") or ""
    cfg = {"cloud_url": _teren_cloud_url(), "cemetery_uid": _active_registry_uid() or "", "cemetery_label": cem_label,
           "loc": _teren_loc(cod_loc.strip()) if cod_loc.strip() else None}
    doc_types = [(k, v) for k, v in attachments.DOC_TYPES.items() if k not in _TEREN_SKIP_DOCS]
    return templates.TemplateResponse(request, "teren.html", _ctx(request, user, cfg=cfg, doc_types=doc_types), headers={"Cache-Control": "no-store"})


@admin.get("/teren/loc/{cod_loc}")
def teren_loc(request: Request, cod_loc: str):
    if not _require(request):
        raise HTTPException(status_code=401)
    d = _teren_loc(cod_loc)
    if not d:
        raise HTTPException(status_code=404, detail="Locul nu există")
    return JSONResponse(d, headers={"Cache-Control": "no-store"})


def _build_map():
    expired = _expired_cods()
    plots = []
    for r in cim.map_rows():
        try:
            pts = json.loads(r["points"]) if r.get("points") else []
        except Exception:
            pts = []
        if not pts:
            continue
        plots.append({
            "cod_loc": r["cod_loc"], "points": pts, "color": r.get("color") or "#0078FF",
            "status": plot_status(r, expired), "sector": r.get("sector"),
            "parcela": r.get("parcela"), "rand": r.get("rand"), "numar": r.get("numar"),
            "tip": r.get("tip"),
        })
    sectors = []
    for r in cim.sector_rows():
        try:
            pts = json.loads(r["points"]) if r.get("points") else []
        except Exception:
            pts = []
        if pts:
            sectors.append({"id": r["id"], "points": pts, "color": r.get("color") or "#FF0000"})
    background = None
    if cimitir_public.background_image(cim) is not None:
        try:
            w = int(cim.get_setting("map_bg_w") or 0)
            h = int(cim.get_setting("map_bg_h") or 0)
        except Exception:
            w, h = 0, 0
        background = {"url": "/api/admin/map/background", "width": w or 940, "height": h or 650}
    return {"plots": plots, "sectors": sectors, "background": background}


@api_admin.get("/map.json")
def map_json(request: Request):
    if not _require(request):
        raise HTTPException(status_code=401)
    return JSONResponse(_build_map())


@api_admin.get("/map/background")
def map_background(request: Request):
    if not _require(request):
        raise HTTPException(status_code=401)
    img = cimitir_public.background_image(cim)
    if img is None:
        raise HTTPException(status_code=404)
    return Response(content=img[0], media_type=img[1])


def _plots_enriched():
    m = _build_map()
    out = []
    for p in m["plots"]:
        loc = dict(p)
        loc["localizare"] = " · ".join([x for x in [
            f"Sector {p['sector']}" if p.get("sector") else "",
            f"Parcela {p['parcela']}" if p.get("parcela") else "",
            f"Rând {p['rand']}" if p.get("rand") else "",
            f"Nr. {p['numar']}" if p.get("numar") else "",
        ] if x])
        out.append(loc)
    return out


@admin.get("/locuri", response_class=HTMLResponse)
def locuri(request: Request):
    user = _require(request)
    if not user:
        return _redirect_login()
    plots = _plots_enriched()
    counts = {}
    for p in plots:
        counts[p["status"]] = counts.get(p["status"], 0) + 1
    return templates.TemplateResponse(request, "locuri.html", _ctx(request, user, plots=plots, counts=counts))


def _norm(s) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", str(s or "").lower()) if unicodedata.category(c) != "Mn")


@admin.get("/cauta", response_class=HTMLResponse)
def search_page(request: Request, q: str = ""):
    """Căutare unificată (persoane/înhumați, concesionari, cod loc, contracte, plăți). Max 25 rezultate pe categorie."""
    user = _require(request)
    if not user:
        return _redirect_login()
    needle = _norm(q.strip())
    res = {"locuri": [], "inhumati": [], "concesionari": [], "contracte": [], "plati": []}
    if len(needle) >= 2:
        def hit(*vals):
            return any(needle in _norm(v) for v in vals)
        res["locuri"] = [p for p in _plots_enriched() if hit(p.get("cod_loc"), p.get("localizare"), p.get("sector"))][:25]
        res["inhumati"] = [r for r in cim.load_all_inhumati() if hit(r.get("nume"), r.get("prenume"), r.get("cnp"), r.get("cod_loc"))][:25]
        for r in res["inhumati"]:
            r["nume_complet"] = f"{r.get('nume', '')} {r.get('prenume', '')}".strip()
        res["concesionari"] = [r for r in cim.load_concesionari() if hit(r.get("nume"), r.get("prenume"), r.get("cnp"), r.get("telefon"), r.get("email"))][:25]
        res["contracte"] = [r for r in cim.load_contracte() if hit(r.get("numar_contract"), r.get("cod_loc"), r.get("owner_fullname"))][:25]
        res["plati"] = [r for r in cim.list_plati() if hit(r.get("chitanta_nr"), r.get("cod_loc"))][:25]
    total = sum(len(v) for v in res.values())
    ctx = _ctx(request, user, q=q, res=res, total=total, short=bool(q.strip()) and len(needle) < 2, can_write="edit" in user["rights"])
    tpl = "partials/search_results.html" if request.headers.get("hx-request") else "cauta.html"
    return templates.TemplateResponse(request, tpl, ctx, headers={"Cache-Control": "no-store"})


@admin.get("/inhumati", response_class=HTMLResponse)
def inhumati(request: Request):
    user = _require(request)
    if not user:
        return _redirect_login()
    rows = cim.load_all_inhumati()
    for r in rows:
        r["nume_complet"] = f"{r.get('nume','')} {r.get('prenume','')}".strip()
    return templates.TemplateResponse(request, "inhumati.html", _ctx(request, user, rows=rows))


@admin.get("/contracte", response_class=HTMLResponse)
def contracte(request: Request):
    user = _require(request)
    if not user:
        return _redirect_login()
    rows = cim.load_contracte()
    for c in rows:
        if c.get("concesionar_id"):
            try:
                cc = cim.get_concesionar(int(c["concesionar_id"]))
                if cc:
                    c["concesionar_nume"] = f"{cc.get('nume','')} {cc.get('prenume','')}".strip()
            except Exception:
                pass
    return templates.TemplateResponse(request, "contracte.html", _ctx(request, user, rows=rows))


@admin.get("/concesionari", response_class=HTMLResponse)
def concesionari(request: Request):
    user = _require(request)
    if not user:
        return _redirect_login()
    rows = cim.load_concesionari()
    return templates.TemplateResponse(request, "concesionari.html", _ctx(request, user, rows=rows))


@admin.get("/plati", response_class=HTMLResponse)
def plati(request: Request):
    user = _require(request)
    if not user:
        return _redirect_login()
    rows = cim.list_plati()
    total = sum(float(r.get("suma") or 0) for r in rows)
    return templates.TemplateResponse(request, "plati.html", _ctx(request, user, rows=rows, total=total))


@admin.get("/cereri", response_class=HTMLResponse)
def cereri(request: Request, status: str = ""):
    user = _require(request)
    if not user:
        return _redirect_login()
    items = cim.list_cereri_concesionare(status or None)
    for c in items:
        c["documents"] = _docs(cim.list_documents("cerere", c["id"]))
    return templates.TemplateResponse(request, "cereri.html", _ctx(request, user, rows=items, tab=status))


def _parse_report_date(value: str):
    """Acceptă date ISO și formatele istorice folosite de aplicația desktop."""
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value or "", fmt).date()
        except ValueError:
            continue
    return None


def _report_contracts(start: str, end: str, sector: str, locuri_by_cod: dict):
    start_date = _parse_report_date(start)
    end_date = _parse_report_date(end)
    rows = []
    for contract in cim.load_contracte():
        cod = contract.get("cod_loc") or ""
        loc_sector = str(contract.get("sector") or locuri_by_cod.get(cod, {}).get("sector") or "")
        expires = contract.get("data_sfarsit") or contract.get("valabilitate") or ""
        expires_date = _parse_report_date(expires)
        if not expires_date:
            continue
        if sector and loc_sector != sector:
            continue
        if start_date and expires_date < start_date:
            continue
        if end_date and expires_date > end_date:
            continue
        row = dict(contract)
        row["_expira"] = expires
        row["_sector"] = loc_sector
        rows.append(row)
    return sorted(rows, key=lambda item: _parse_report_date(item.get("_expira")) or date.max)


def _sector_breakdown(plots: list) -> list:
    by_sector: dict = {}
    for plot in plots:
        key = plot.get("sector") or "—"
        row = by_sector.setdefault(key, {"sector": f"Sector {key}", "liber": 0, "ocupat": 0, "rezervat": 0, "expirat": 0})
        row[plot["status"]] += 1
    return list(by_sector.values())


def _payment_totals(sector: str, locuri_by_cod: dict) -> tuple:
    amounts = [float(p.get("suma") or 0) for p in cim.list_plati()
               if not sector or str(locuri_by_cod.get(p.get("cod_loc") or "", {}).get("sector") or "") == sector]
    return round(sum(amounts), 2), len(amounts)


def _expiring_contracts(start: str, end: str, sector: str, locuri_by_cod: dict) -> list:
    rows = _report_contracts(start, end, sector, locuri_by_cod)
    if start or end:
        return rows
    horizon = date.today() + timedelta(days=30)
    return [row for row in rows if (_parse_report_date(row.get("_expira")) or date.max) <= horizon]


def _page_window(total: int, page: int, page_size: int) -> tuple:
    page_size = min(max(page_size, 5), 100)
    page_count = max(1, (total + page_size - 1) // page_size)
    page = min(max(page, 1), page_count)
    return page, page_size, page_count, (page - 1) * page_size


@admin.get("/rapoarte", response_class=HTMLResponse)
def rapoarte(request: Request, sector: str = "", start: str = "", end: str = "",
             page: int = 1, page_size: int = 10):
    user = _require(request)
    if not user:
        return _redirect_login()
    expired = _expired_cods()
    locuri_all = cim.list_locuri()
    locuri_by_cod = {str(loc.get("cod_loc") or ""): loc for loc in locuri_all}
    available_sectors = sorted({str(loc.get("sector") or "") for loc in locuri_all if loc.get("sector")})
    if sector and sector not in available_sectors:
        sector = ""
    locuri_scope = [loc for loc in locuri_all if not sector or str(loc.get("sector") or "") == sector]
    counts = {"liber": 0, "ocupat": 0, "rezervat": 0, "expirat": 0}
    for loc in locuri_scope:
        counts[plot_status(loc, expired)] += 1
    total = len(locuri_scope)
    plots = [plot for plot in _plots_enriched() if not sector or str(plot.get("sector") or "") == sector]
    expiring = _expiring_contracts(start, end, sector, locuri_by_cod)
    page, page_size, page_count, offset = _page_window(len(expiring), page, page_size)
    total_incasari, nr_plati = _payment_totals(sector, locuri_by_cod)
    stats = {
        "grad_ocupare": round(((counts["ocupat"] + counts["expirat"]) / total * 100) if total else 0, 1),
        "total_locuri": total, "total_incasari": total_incasari, "nr_plati": nr_plati,
        "contracte_expira": len(expiring), "expiring": expiring[offset:offset + page_size],
        "status_counts": counts,
    }
    query = urlencode({"sector": sector, "start": start, "end": end, "page_size": page_size})
    return templates.TemplateResponse(request, "rapoarte.html",
                                      _ctx(request, user, stats=stats, sectors=_sector_breakdown(plots),
                                           available_sectors=available_sectors,
                                           filters={"sector": sector, "start": start, "end": end},
                                           pagination={"page": page, "page_count": page_count, "page_size": page_size,
                                                       "total": len(expiring), "query": query}))


@admin.get("/utilizatori", response_class=HTMLResponse)
def utilizatori(request: Request):
    user = _require(request)
    if not user:
        return _redirect_login()
    if "manage_users" not in user["rights"]:
        return RedirectResponse(url="/admin/", status_code=303)
    return templates.TemplateResponse(request, "utilizatori.html", _ctx(request, user, rows=cim_auth.list_users()))


@admin.get("/setari", response_class=HTMLResponse)
def setari(request: Request):
    user = _require(request)
    if not user:
        return _redirect_login()
    return templates.TemplateResponse(request, "setari.html", _config_ctx(request, user))


# =============================================================================
# PLOT INSPECTOR (drawer partial)
# =============================================================================
def _docs(docs):
    out = []
    for d in docs:
        ext = os.path.splitext(d.get("file_path", ""))[1].lower().lstrip(".")
        out.append({"id": d["id"], "doc_type": d.get("doc_type", ""), "caption": d.get("caption", ""),
                    "date_added": d.get("date_added", ""), "ext": ext, "is_pdf": ext == "pdf",
                    "is_image": ext in ("png", "jpg", "jpeg", "webp", "gif", "bmp")})
    return out


LUCRARE_STARI = {"planificata": "Planificată", "in_lucru": "În lucru", "finalizata": "Finalizată"}


def _loc_detail(cod_loc: str):
    loc = cim.load_loc_by_cod(cod_loc)
    if not loc:
        return None
    contracte = [c for c in cim.load_contracte() if c.get("cod_loc") == cod_loc]
    conces = None
    if contracte and contracte[-1].get("concesionar_id"):
        try:
            conces = cim.get_concesionar(int(contracte[-1]["concesionar_id"]))
        except Exception:
            conces = None
    inhumati_l = cim.load_inhumati_for_loc(cod_loc)
    avize = cim.list_avize_for_loc(cod_loc)
    for av in avize:
        counts = {t: 0 for t in attachments.LUCRARE_FOTO}
        total = 0
        for dd in cim.list_documents("aviz", av["id"]):
            total += 1
            if dd.get("doc_type") in counts:
                counts[dd["doc_type"]] += 1
        av["foto"], av["foto_total"] = counts, total
    loc_photos = _docs(cim.list_documents("loc", cod_loc, "poza_loc"))
    loc["status"] = plot_status(loc, _expired_cods())
    loc["observatii"] = loc_observatii(loc)
    loc_options = {f: options_for(f, loc.get(f)) for f in LOC_SELECT_FIELDS}
    return {"loc": loc, "loc_options": loc_options, "concesionar": conces, "contracte": contracte, "inhumati": inhumati_l,
            "avize": avize, "lucrari_extended": getattr(cim, "ENGINE", "") != "postgres", "stari": LUCRARE_STARI, "loc_photos": loc_photos,
            "plati": {"total": cim.total_plati(cod_loc), "items": cim.list_plati(cod_loc)}}


def _drawer(request, cod_loc, error: str = "", status_code: int = 200, open_tab: str = "", form=None):
    d = _loc_detail(cod_loc)
    if not d:
        return HTMLResponse("<div class='p-6'>Locul nu există.</div>", status_code=404)
    user = _require(request) or {"rights": []}
    can_contract = "approve" in user["rights"]
    options = []
    if can_contract:
        options = [{"id": c["id"], "name": f"{c.get('nume', '')} {c.get('prenume', '')}".strip() or f"#{c['id']}"} for c in cim.load_concesionari()]
    return templates.TemplateResponse(request, "partials/inspector.html", {
        "request": request, "d": d, "cod": cod_loc, "error": error, "can_contract": can_contract,
        "open_tab": open_tab, "form": form or {}, "conces_options": options, "can_history": "approve" in user["rights"], "today": datetime.now().strftime("%d.%m.%Y")},
        status_code=status_code)


@admin.get("/ui/loc/{cod_loc}", response_class=HTMLResponse)
def loc_drawer(request: Request, cod_loc: str):
    if not _require(request):
        raise HTTPException(status_code=401)
    return _drawer(request, cod_loc)


# ---- mutations loc-scoped (return refreshed drawer) -------------------------
@admin.post("/ui/loc/{cod_loc}/edit", response_class=HTMLResponse)
async def loc_edit(request: Request, cod_loc: str):
    _require_writer(request)
    form = dict(await request.form())
    form.pop("extra_data", None)
    current = cim.load_loc_by_cod(cod_loc) or {}
    bad = [f for f in LOC_SELECT_FIELDS if f in form and not is_allowed_value(f, form[f], current.get(f))]
    if bad:
        return _drawer(request, cod_loc, error=f"Valoare nepermisă pentru: {', '.join(bad)}.", status_code=400)
    if "observatii" in form:  # aceeași scriere ca în MAIN (EditorActions.save_extra_data): extra_data + loc_extra + coloană
        merged = {**current, "extra_data": {**(current.get("extra_data") or {}), **(cim.load_extra_data(cod_loc) or {})}}
        form.update(observatii_metadata(merged, str(form["observatii"])))
    cim.save_loc_metadata(cod_loc, form)
    if "observatii" in form:
        cim.save_extra_data(cod_loc, form["extra_data"])
    return _drawer(request, cod_loc)


@admin.post("/ui/loc/{cod_loc}/inhumat", response_class=HTMLResponse)
async def loc_inhumat(request: Request, cod_loc: str):
    _require_writer(request)
    cim.save_inhumat(cod_loc, dict(await request.form()))
    return _drawer(request, cod_loc, open_tab="t-inhumati")


@admin.post("/ui/inhumat/{iid}/delete", response_class=HTMLResponse)
async def inhumat_delete(request: Request, iid: int, cod_loc: str = Form(...)):
    _require_writer(request)
    cim.delete_inhumat(iid)
    return _drawer(request, cod_loc, open_tab="t-inhumati")


def _date_ro(value) -> Optional[date]:
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(value or "").strip(), fmt).date()
        except ValueError:
            continue
    return None


@admin.post("/ui/loc/{cod_loc}/contract", response_class=HTMLResponse)
async def loc_contract(request: Request, cod_loc: str):
    """Creare contract de concesiune din web/telefon — numai cu dreptul `approve`; un singur contract activ pe loc,
    număr unic; datele titularului se copiază din concesionar (ca în MAIN)."""
    user = _require_right(request, "approve")
    if not cim.load_loc_by_cod(cod_loc):
        raise HTTPException(status_code=404, detail="Locul nu există")
    f = await request.form()

    def err(msg: str):
        return _drawer(request, cod_loc, error=msg, status_code=422, open_tab="t-contracte", form={k: str(v) for k, v in f.items()})

    numar = str(f.get("numar_contract") or "").strip()[:60]
    if not numar:
        return err("Numărul contractului este obligatoriu.")
    existing = cim.load_contracte()
    if any(str(c.get("numar_contract") or "").strip().lower() == numar.lower() for c in existing):
        return err(f"Există deja un contract cu numărul „{numar}”.")
    if any(c.get("cod_loc") == cod_loc and (c.get("valabilitate") or "") != "anulat" for c in existing):
        return err(f"Locul „{cod_loc}” are deja un contract activ.")
    raw_c, raw_s = str(f.get("data_contract") or "").strip(), str(f.get("data_inceput") or "").strip()
    d_contract = _date_ro(raw_c) if raw_c else datetime.now().date()
    if not d_contract:
        return err("Data contractului nu este validă (zz.ll.aaaa).")
    d_start = _date_ro(raw_s) if raw_s else d_contract
    if not d_start:
        return err("Data de început nu este validă (zz.ll.aaaa).")
    raw_end = str(f.get("data_sfarsit") or "").strip()
    d_end = _date_ro(raw_end) if raw_end else None
    if raw_end and not d_end:
        return err("Data de sfârșit nu este validă (zz.ll.aaaa).")
    if d_end and d_end < d_start:
        return err("Data de sfârșit nu poate fi înaintea celei de început.")
    cid = int(str(f.get("concesionar_id") or "0") or 0) if str(f.get("concesionar_id") or "").isdigit() else 0
    if cid and not cim.get_concesionar(cid):
        return err("Concesionarul ales nu există.")
    if not cid:
        nume = str(f.get("nume") or "").strip()
        if not nume:
            return err("Alegeți un concesionar existent sau completați numele unuia nou.")
        cid = cim.save_concesionar({k: str(f.get(k) or "").strip()[:200] for k in ("nume", "prenume", "cnp", "bi_ci", "adresa", "telefon", "email")})
        if not cid:
            return err("Concesionarul nu a putut fi creat.")
    cc = cim.get_concesionar(cid) or {}
    fmt = lambda d: d.strftime("%d.%m.%Y") if d else ""  # noqa: E731
    contract_id = cim.save_contract({
        "numar_contract": numar, "cod_loc": cod_loc, "concesionar_id": cid, "data_contract": fmt(d_contract),
        "data_inceput": fmt(d_start), "data_sfarsit": fmt(d_end), "observatii": str(f.get("observatii") or "").strip()[:500],
        "owner_fullname": f"{cc.get('nume', '')} {cc.get('prenume', '')}".strip(), "owner_address": cc.get("adresa") or "",
        "owner_id_card_serie_number": cc.get("bi_ci") or "", "owner_cnp": cc.get("cnp") or "",
        "owner_phone": cc.get("telefon") or "", "owner_email": cc.get("email") or ""})
    if not contract_id:
        return err("Contractul nu a putut fi salvat.")
    cimitir_requests.audit("staff", user["username"], "web_create_contract", f"contract:{contract_id}", f"loc={cod_loc} nr={numar} concesionar={cid}")
    return _drawer(request, cod_loc, open_tab="t-contracte")


@admin.post("/ui/contract/{cid}/delete", response_class=HTMLResponse)
async def contract_delete(request: Request, cid: int, cod_loc: str = Form(...)):
    _require_writer(request)
    cim.delete_contract(cid)
    return _drawer(request, cod_loc, open_tab="t-contracte")


@admin.post("/ui/loc/{cod_loc}/plata", response_class=HTMLResponse)
async def loc_plata(request: Request, cod_loc: str):
    _require_writer(request)
    b = dict(await request.form())
    try:
        suma = float(b.get("suma") or 0)
    except Exception:
        suma = 0.0
    cim.add_plata(cod_loc, suma, b.get("data_plata", ""), b.get("tip", ""), b.get("chitanta_nr", ""), b.get("observatii", ""))
    return _drawer(request, cod_loc, open_tab="t-plati")


@admin.post("/ui/plata/{pid}/delete", response_class=HTMLResponse)
async def plata_delete(request: Request, pid: int, cod_loc: str = Form(...)):
    _require_writer(request)
    cim.delete_plata(pid)
    return _drawer(request, cod_loc, open_tab="t-plati")


@admin.post("/ui/loc/{cod_loc}/aviz", response_class=HTMLResponse)
async def loc_aviz(request: Request, cod_loc: str):
    _require_writer(request)
    b = dict(await request.form())
    aid = cim.add_aviz(cod_loc, b.get("nr", ""), b.get("data", ""), b.get("lucrare", ""))
    if aid and getattr(cim, "ENGINE", "") != "postgres":
        cim.update_aviz(aid, _lucrare_fields(b, {}))
    return _drawer(request, cod_loc, open_tab="t-avize")


def _lucrare_fields(b: dict, current: dict) -> dict:
    """Câmpurile permise pentru lucrare; la «finalizată» se completează data, la altă stare se șterge."""
    out = {k: str(b[k]).strip()[:500] for k in ("nr", "data", "lucrare", "observatii", "executant", "data_inceput") if k in b}
    stare = str(b.get("stare", current.get("stare", "")) or "")
    if stare in LUCRARE_STARI:
        out["stare"] = stare
        out["data_finalizare"] = (current.get("data_finalizare") or datetime.now().strftime("%d.%m.%Y")) if stare == "finalizata" else ""
    elif not current.get("stare"):
        out["stare"] = "planificata"
    return out


@admin.post("/ui/aviz/{aid}/edit", response_class=HTMLResponse)
async def aviz_edit(request: Request, aid: int):
    _require_writer(request)
    cur = cim.get_aviz(aid)
    if not cur:
        raise HTTPException(status_code=404, detail="Lucrarea nu există")
    b = dict(await request.form())
    fields = _lucrare_fields(b, cur)
    if getattr(cim, "ENGINE", "") == "postgres":
        fields = {k: v for k, v in fields.items() if k in ("nr", "data", "lucrare")}
    cim.update_aviz(aid, fields)
    cimitir_requests.audit("staff", (_require(request) or {}).get("username", ""), "aviz.update", f"aviz:{aid}", json.dumps(fields, ensure_ascii=False))
    return _drawer(request, cur["cod_loc"], open_tab="t-avize")


_INHUMAT_EDIT = ("nume", "prenume", "cnp", "data_nasterii", "data_deces", "data_inhumare", "data_deshumare", "sex", "cauza_deces",
                 "profesie", "religie", "ultima_adresa", "observatii", "nr_certificat_deces", "nr_adeverinta_inhumare")


@admin.post("/ui/inhumat/{iid}/edit", response_class=HTMLResponse)
async def inhumat_edit(request: Request, iid: int):
    _require_writer(request)
    cur = cim.get_inhumat(iid)
    if not cur or not cur.get("cod_loc"):
        raise HTTPException(status_code=404, detail="Înhumatul nu există")
    b = dict(await request.form())
    data = {k: (str(b[k]).strip()[:500] if k in b else (cur.get(k) or "")) for k in _INHUMAT_EDIT}
    cim.save_inhumat(cur["cod_loc"], data, inhumat_id=iid)
    cimitir_requests.audit("staff", (_require(request) or {}).get("username", ""), "inhumat.update", f"inhumat:{iid}", ",".join(k for k in b if k in _INHUMAT_EDIT))
    return _drawer(request, cur["cod_loc"], open_tab="t-inhumati")


@admin.post("/ui/aviz/{aid}/delete", response_class=HTMLResponse)
async def aviz_delete(request: Request, aid: int, cod_loc: str = Form(...)):
    _require_writer(request)
    cim.delete_aviz(aid)
    return _drawer(request, cod_loc, open_tab="t-avize")


_IMG_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")


@admin.post("/ui/loc/{cod_loc}/document", response_class=HTMLResponse)
async def loc_document(request: Request, cod_loc: str, file: UploadFile = File(...)):
    _require_writer(request)
    suffix = os.path.splitext(file.filename or "upload.png")[1] or ".png"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        shutil.copyfileobj(file.file, tmp)
        tmp.close()
        if suffix.lower() in _IMG_SUFFIXES:
            try:
                from PIL import Image as PILImage
                with PILImage.open(tmp.name) as im:
                    im.load()
            except Exception:
                pass
        did = cim.add_document("loc", cod_loc, "poza_loc", tmp.name, caption=f"Document {cod_loc}")
        if did and getattr(cim, "ENGINE", "") == "postgres":
            _c = cim.get_connection()
            try:
                _r = _c.execute("SELECT media_id FROM documents WHERE id=?", (did,)).fetchone()
            finally:
                _c.close()
            if not (_r and _r["media_id"]):
                cim.delete_document(did)   # nu lăsăm documente doar pe discul serverului dacă Storage e indisponibil
                raise HTTPException(status_code=503, detail="Documentul nu a putut fi stocat (Storage indisponibil). Încercați din nou.")
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
    return _drawer(request, cod_loc)


@admin.post("/ui/document/{doc_id}/delete", response_class=HTMLResponse)
async def document_delete(request: Request, doc_id: int, cod_loc: str = Form(...)):
    _require_writer(request)
    cim.delete_document(doc_id)
    return _drawer(request, cod_loc)


@admin.get("/file/document/{doc_id}")
def document_file(request: Request, doc_id: int):
    if not _require(request):
        raise HTTPException(status_code=401)
    path = cim.get_document_path(doc_id)
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404)
    return FileResponse(path)


# =============================================================================
# ATAȘAMENTE — arhivă generală (JPEG/PNG/PDF) pentru loc, concesionar, contract, înhumat, plată, aviz, cerere
# =============================================================================
import attachments  # noqa: E402
import scanning  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402


def _ai_failure_reason(exc: Exception) -> str:
    """Categorie scurtă a eșecului AI (fără mesajul furnizorului: poate conține identificatori de cheie)."""
    m = str(exc).lower()
    if "budget" in m:
        return "budget"
    if "rate" in m and "limit" in m:
        return "rate_limit"
    if "timeout" in m or "timed out" in m:
        return "timeout"
    if "auth" in m or "api key" in m or "401" in m or "403" in m:
        return "auth"
    return "other"


_AI_REASON_TEXT = {"budget": "limita de utilizare a serviciului AI a fost atinsă", "rate_limit": "serviciul AI este suprasolicitat",
                   "timeout": "serviciul AI nu a răspuns la timp", "auth": "serviciul AI nu este configurat corect",
                   "other": "serviciul AI este indisponibil sau documentul este ilizibil"}


def _ai_ready() -> bool:
    try:
        import ai_extract
        return bool(ai_extract.engine_status().get("available"))
    except Exception:  # noqa: BLE001
        return False

logger = logging.getLogger("cimitir.web")


def _att_panel(request: Request, etype: str, eid: str, user: dict, error: str = "", status_code: int = 200, notice: str = "",
               selected: str = ""):
    ent = attachments.resolve_entity(cim, etype, eid)
    if not ent:
        raise HTTPException(status_code=404, detail="Înregistrarea nu există")
    items = attachments.list_attachments(cim, etype, eid)
    groups = []
    for it in items:
        if groups and groups[-1][0] == it["doc_type"]:
            groups[-1][1].append(it)
        else:
            for g in groups:
                if g[0] == it["doc_type"]:
                    g[1].append(it)
                    break
            else:
                groups.append((it["doc_type"], [it]))
    return templates.TemplateResponse(request, "partials/attachments.html", {
        "request": request, "ent": ent, "items": items, "groups": groups, "error": error, "notice": notice,
        "scan_kinds": scanning.kinds_for(etype), "ai_ready": _ai_ready(),
        "can_write": "edit" in user["rights"], "can_history": "approve" in user["rights"], "doc_types": attachments.DOC_TYPES,
        "suggested": attachments.SUGGESTED.get(etype, ["alt_document"]),
        "selected": selected if selected in attachments.DOC_TYPES else "", "lucrare_foto": attachments.LUCRARE_FOTO},
        status_code=status_code, headers={"Cache-Control": "no-store"})


@admin.get("/att/{etype}/{eid}", response_class=HTMLResponse)
def att_panel(request: Request, etype: str, eid: str, doc_type: str = ""):
    user = _require(request)
    if not user:
        raise HTTPException(status_code=401)
    return _att_panel(request, etype, eid, user, selected=doc_type)


@admin.post("/att/{etype}/{eid}", response_class=HTMLResponse)
async def att_upload(request: Request, etype: str, eid: str, files: list[UploadFile] = File(...),
                     doc_type: str = Form("alt_document"), title: str = Form(""), description: str = Form("")):
    user = _require_writer(request)
    if not attachments.resolve_entity(cim, etype, eid):
        raise HTTPException(status_code=404, detail="Înregistrarea nu există")
    errors, saved = [], []
    for f in files:
        data = await f.read()
        try:
            info = attachments.add_attachment(cim, etype, eid, f.filename or "", data, doc_type,
                                              title if len(files) == 1 else "", description, user["username"],
                                              source="telefon" if "mobile" in (request.headers.get("user-agent") or "").lower() else "web")
            cimitir_requests.audit("staff", user["username"], "attachment.add", f"{etype}:{eid}", attachments.audit_detail(info))
            saved.append(info)
        except attachments.AttachmentError as exc:
            errors.append(f"{safe_name_for_msg(f.filename)}: {exc}")
    photos = sum(1 for i in saved if str(i.get("mime", "")).startswith("image/"))
    notice = ("" if not saved else "Fotografie salvată" if len(saved) == 1 and photos == 1 else "Document salvat" if len(saved) == 1
              else f"{len(saved)} fișiere salvate")
    return _att_panel(request, etype, eid, user, error=" ".join(errors), status_code=422 if errors else 200, notice=notice,
                      selected=doc_type)


@admin.post("/mobile/token")
def mobile_token(request: Request, etype: str = Form(...), eid: str = Form(...)):
    """„Continuă pe telefon” din panoul web: token temporar (QR) legat de O singură entitate. Drept: edit."""
    user = _require_writer(request)
    if etype not in attachments.ENTITIES or etype == "cerere":
        raise HTTPException(status_code=422, detail="Tip de entitate neacceptat")
    source_id = (cim.current_source() or {}).get("id")
    if not source_id:
        raise HTTPException(status_code=409, detail="Baza de date activă nu este selectată")
    return JSONResponse(mobile_upload.issue(request, source_id, etype, eid, user["username"]), headers={"Cache-Control": "no-store"})


def safe_name_for_msg(name: Optional[str]) -> str:
    return attachments.safe_name(name or "fișier")


def _entity_current(etype: str, eid: str) -> Optional[dict]:
    try:
        return (cim.get_concesionar(int(eid)) if etype == "concesionar" else cim.get_inhumat(int(eid)) if etype == "inhumat" else None)
    except Exception:  # noqa: BLE001
        return None


def _scan_review(request: Request, ent: dict, kind: str, att_id: int, rows: list, error: str = "", status_code: int = 200):
    return templates.TemplateResponse(request, "partials/scan_review.html", {
        "request": request, "ent": ent, "kind": kind, "kind_label": scanning.KINDS[kind]["label"], "att_id": att_id, "rows": rows,
        "error": error}, status_code=status_code, headers={"Cache-Control": "no-store"})


@admin.post("/att/{etype}/{eid}/scan", response_class=HTMLResponse)
async def att_scan(request: Request, etype: str, eid: str):
    """Fotografie document -> (1) originalul se arhivează, (2) AI propune datele, (3) utilizatorul le confirmă separat."""
    user = _require_writer(request)
    ent = attachments.resolve_entity(cim, etype, eid)
    if not ent:
        raise HTTPException(status_code=404, detail="Înregistrarea nu există")
    form = await request.form()
    kind = str(form.get("kind") or "")
    picked, data = None, b""
    for part in form.getlist("file"):       # 2 câmpuri `file` (cameră / fișier): cel necompletat vine ca text gol, nu ca fișier
        if hasattr(part, "read"):
            content = await part.read()
            if content:
                picked, data = part, content
                break
    if not scanning.allowed(kind, etype):
        return _att_panel(request, etype, eid, user, error="Acest tip de scanare nu este disponibil pentru această înregistrare.", status_code=422)
    if picked is None:
        return _att_panel(request, etype, eid, user, error="Nu a fost selectat niciun fișier.", status_code=422)
    spec = scanning.KINDS[kind]
    try:
        info = attachments.add_attachment(cim, etype, eid, picked.filename or "", data, spec["doc_type"], spec["title"],
                                          scanning.AUTO_DESCRIPTION, user["username"], source="scan")
    except attachments.AttachmentError as exc:
        return _att_panel(request, etype, eid, user, error=str(exc), status_code=422)
    cimitir_requests.audit("staff", user["username"], "attachment.add", f"{etype}:{eid}", attachments.audit_detail(info))
    import tempfile
    ext = os.path.splitext(info["filename"])[1].lower() or ".jpg"
    fd, tmp = tempfile.mkstemp(suffix=ext, prefix="scan_")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        import ai_extract
        extraction = await run_in_threadpool(ai_extract.extract_document, tmp, kind)
    except Exception as exc:  # noqa: BLE001
        reason = _ai_failure_reason(exc)
        logger.warning("Extragere AI eșuată (%s): %s/%s", kind, type(exc).__name__, reason)
        cimitir_requests.audit("staff", user["username"], "scan.extract_failed", f"{etype}:{eid}", json.dumps({"kind": kind, "attachment_id": info["id"], "error": type(exc).__name__, "reason": reason}))
        return _att_panel(request, etype, eid, user, error="Originalul a fost salvat în arhivă, dar extragerea automată nu a reușit"
                          f" ({_AI_REASON_TEXT[reason]}). Poți completa datele manual.")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    current = _entity_current(etype, eid) or {}
    rows = scanning.propose(kind, etype, current, extraction if isinstance(extraction, dict) else {})
    cimitir_requests.audit("staff", user["username"], "scan.extract", f"{etype}:{eid}", json.dumps(
        {"kind": kind, "attachment_id": info["id"], "engine": (extraction or {}).get("engine"), "fields_found": sum(1 for r in rows if r["value"])}))
    return _scan_review(request, ent, kind, info["id"], rows)


@admin.post("/att/{etype}/{eid}/scan/apply", response_class=HTMLResponse)
async def att_scan_apply(request: Request, etype: str, eid: str):
    """Aplică numai câmpurile bifate și editate/confirmate de utilizator. Originalul rămâne în arhivă indiferent de rezultat."""
    user = _require_writer(request)
    ent = attachments.resolve_entity(cim, etype, eid)
    if not ent:
        raise HTTPException(status_code=404, detail="Înregistrarea nu există")
    f = await request.form()
    kind = str(f.get("kind") or "")
    if not scanning.allowed(kind, etype):
        raise HTTPException(status_code=422, detail="Tip de scanare invalid")
    att_raw = str(f.get("attachment_id") or "")
    att = attachments.get_attachment(cim, int(att_raw)) if att_raw.isdigit() else None
    if not att or att.get("entity_type") != etype or str(att.get("entity_id")) != str(eid):
        raise HTTPException(status_code=422, detail="Originalul scanat nu este în arhiva acestei înregistrări")
    current = _entity_current(etype, eid)
    if not current:
        raise HTTPException(status_code=404, detail="Înregistrarea nu există")
    fields = scanning.KINDS[kind]["fields"][etype]
    changes, rows, first_error = {}, [], ""
    for name, label in fields:
        raw = str(f.get(f"field_{name}") or "").strip()
        on = bool(f.get(f"apply_{name}"))
        rows.append({"field": name, "label": label, "current": str(current.get(name) or ""), "value": raw, "confidence": 1.0,
                     "low": False, "differs": raw != str(current.get(name) or ""), "checked": on})
        if on:
            err = scanning.validate_value(name, raw)
            if err and not first_error:
                first_error = err
            changes[name] = raw
    if first_error:
        return _scan_review(request, ent, kind, int(att_raw), rows, error=first_error, status_code=422)
    if not changes:
        return _att_panel(request, etype, eid, user, notice="Nu s-a aplicat nicio modificare. Originalul rămâne în arhivă.")
    merged = {**current, **changes}
    if etype == "concesionar":
        saved = cim.save_concesionar(merged, int(eid))
    else:
        saved = cim.save_inhumat(current.get("cod_loc") or "", merged, int(eid))
    if not saved:
        return _scan_review(request, ent, kind, int(att_raw), rows, error="Datele nu au putut fi salvate.", status_code=500)
    cimitir_requests.audit("staff", user["username"], "scan.confirm", f"{etype}:{eid}", json.dumps(
        {"kind": kind, "attachment_id": int(att_raw), "fields": sorted(changes)}))
    labels = dict(fields)
    return _att_panel(request, etype, eid, user, notice="Date confirmate și salvate: " + ", ".join(labels[k] for k in changes) + ".")


@admin.get("/attfile/{doc_id}")
def att_file(request: Request, doc_id: int, download: int = 0):
    if not _require(request):
        raise HTTPException(status_code=401)
    row = attachments.get_attachment(cim, doc_id)
    path = cim.get_document_path(doc_id) if row else None
    if not row or not path or not os.path.exists(path):
        raise HTTPException(status_code=404)
    disp = "attachment" if download else "inline"
    return FileResponse(path, media_type=row["mime"], headers={
        "Content-Disposition": f'{disp}; filename="{attachments.safe_name(row["filename"])}"',
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


@admin.post("/attdel/{doc_id}", response_class=HTMLResponse)
def att_delete(request: Request, doc_id: int):
    user = _require_writer(request)
    row = attachments.get_attachment(cim, doc_id)
    if not row:
        raise HTTPException(status_code=404)
    etype, eid = row["entity_type"], str(row["entity_id"])
    if cim.delete_document(doc_id):
        cimitir_requests.audit("staff", user["username"], "attachment.delete", f"{etype}:{eid}", attachments.audit_detail(
            {"id": doc_id, "doc_type": row.get("doc_type"), "filename": row["filename"], "mime": row["mime"],
             "size": row.get("size"), "sha256": row.get("sha256")}))
    return _att_panel(request, etype, eid, user)


# =============================================================================
# DOCUMENTE PDF (aceleași generatoare ca în MAIN) + ISTORIC / AUDIT PE ENTITATE
# =============================================================================
_DATE_Q = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _pdf_response(request: Request, kind: str, ref: str, build, filename: str, download: int = 0):
    user = _require(request)
    if not user:
        raise HTTPException(status_code=401)
    import pdf_reports  # noqa: F401  (încărcat la cerere: reportlab/QR)
    try:
        data = build(pdf_reports)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("PDF %s %s a eșuat: %s", kind, ref, type(exc).__name__)
        raise HTTPException(status_code=404, detail="Documentul nu a putut fi generat (înregistrare inexistentă sau date incomplete).")
    if not data:
        raise HTTPException(status_code=404, detail="Documentul nu a putut fi generat.")
    cimitir_requests.audit("staff", user["username"], "pdf.generate", f"{kind}:{ref}", json.dumps({"kind": kind, "size": len(data)}))
    disp = "attachment" if download else "inline"
    return Response(content=data, media_type="application/pdf", headers={
        "Content-Disposition": f'{disp}; filename="{filename}"', "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@admin.get("/pdf/contract/{cid}")
def pdf_contract(request: Request, cid: int, download: int = 0):
    return _pdf_response(request, "contract", str(cid), lambda m: m.render_contract_pdf(cid), f"contract_{cid}.pdf", download)


@admin.get("/pdf/chitanta/{pid}")
def pdf_chitanta(request: Request, pid: int, download: int = 0):
    return _pdf_response(request, "chitanta", str(pid), lambda m: m.render_receipt_pdf(pid), f"chitanta_{pid}.pdf", download)


@admin.get("/pdf/adeverinta/{iid}")
def pdf_adeverinta(request: Request, iid: int, download: int = 0):
    return _pdf_response(request, "adeverinta", str(iid), lambda m: m.render_burial_certificate_pdf(iid), f"adeverinta_inhumare_{iid}.pdf", download)


@admin.get("/pdf/expirari")
def pdf_expirari(request: Request, days: int = 30, download: int = 0):
    days = max(1, min(int(days), 3650))
    return _pdf_response(request, "expirari", str(days), lambda m: m.render_expiring_contracts_pdf(days), f"contracte_expira_{days}z.pdf", download)


def _range(start: str, end: str):
    s, e = (start or "").strip(), (end or "").strip()
    if (s and not _DATE_Q.match(s)) or (e and not _DATE_Q.match(e)):
        raise HTTPException(status_code=422, detail="Interval invalid (aaaa-ll-zz).")
    return s or None, e or None


@admin.get("/pdf/raport-financiar")
def pdf_financiar(request: Request, start: str = "", end: str = "", download: int = 0):
    s, e = _range(start, end)
    return _pdf_response(request, "raport-financiar", f"{s or ''}..{e or ''}", lambda m: m.render_financial_report_pdf(s, e), "raport_financiar.pdf", download)


@admin.get("/pdf/registru-inhumari")
def pdf_registru(request: Request, start: str = "", end: str = "", download: int = 0):
    s, e = _range(start, end)
    return _pdf_response(request, "registru-inhumari", f"{s or ''}..{e or ''}", lambda m: m.render_burials_register_pdf(s, e), "registru_inhumari.pdf", download)


def _uid_of(etype: str, eid: str) -> str:
    spec = attachments.ENTITIES.get(etype)
    if not spec:
        return ""
    _, table, col, _ = spec
    conn = cim.get_connection()
    try:
        row = conn.execute(f"SELECT uid FROM {table} WHERE {col} = ?", (int(eid) if col == "id" else eid,)).fetchone()
        return (dict(row).get("uid") or "") if row else ""
    except Exception:  # noqa: BLE001 - SQLite fără coloana uid
        return ""
    finally:
        conn.close()


@admin.get("/istoric/{etype}/{eid}", response_class=HTMLResponse)
def entity_history(request: Request, etype: str, eid: str):
    """Istoric / audit al unei înregistrări (creare, modificări cu valori vechi/noi, atașamente, PDF-uri). Drept: approve."""
    user = _require_right(request, "approve")
    ent = attachments.resolve_entity(cim, etype, eid)
    if not ent:
        raise HTTPException(status_code=404, detail="Înregistrarea nu există")
    uid = _uid_of(etype, eid)
    conn = cim.get_connection()
    try:
        rows = [dict(r) for r in conn.execute(
            f"SELECT id, ts, actor_type, actor, action, target, detail FROM audit_log WHERE (target = ? OR target LIKE ?) AND {cim.tenant_scope(level='org')} "
            "ORDER BY id DESC LIMIT 200", (f"{etype}:{eid}", f"%:{uid}" if uid else "\x00nu-exista")).fetchall()]
    finally:
        conn.close()
    events = []
    for r in rows:
        changes, extra = [], ""
        try:
            d = json.loads(r["detail"] or "{}")
        except Exception:  # noqa: BLE001
            d, extra = {}, (r["detail"] or "")[:200]
        if isinstance(d, dict) and ("old" in d or "new" in d):
            old, new = d.get("old") or {}, d.get("new") or {}
            for k in dict.fromkeys(list(old) + list(new)):
                o, n = old.get(k), new.get(k)
                if o in (None, "") and n in (None, ""):
                    continue                      # câmp gol -> gol: nu aduce informație
                if o == n:
                    continue
                changes.append({"field": k, "old": o, "new": n})
                if len(changes) >= 14:
                    break
        elif isinstance(d, dict) and d:
            extra = ", ".join(f"{k}={v}" for k, v in list(d.items())[:6])
        events.append({"ts": r["ts"], "actor": r["actor"], "actor_type": r["actor_type"], "action": r["action"], "changes": changes, "extra": extra})
    return templates.TemplateResponse(request, "partials/history.html", {"request": request, "ent": ent, "events": events},
                                      headers={"Cache-Control": "no-store"})


@admin.get("/loc/{cod_loc}/fisa.pdf")
def loc_fisa_pdf(request: Request, cod_loc: str):
    if not _require(request):
        raise HTTPException(status_code=401)
    import fisa_pdf
    try:
        pdf = fisa_pdf.build_fisa_pdf(cod_loc)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Eroare PDF: {e}")
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="fisa_{cod_loc}.pdf"', "Cache-Control": "private, no-store"})


# =============================================================================
# LIST-PAGE mutations (redirect back)
# =============================================================================
def _back(url="/"):
    return RedirectResponse(url="/admin" + url, status_code=303)


@admin.post("/ui/concesionar")
async def concesionar_save(request: Request):
    _require_writer(request)
    b = dict(await request.form())
    cid = b.pop("id", "")
    data = {k: b.get(k, "") for k in ("nume", "prenume", "cnp", "bi_ci", "adresa", "telefon", "email", "observatii")}
    cim.save_concesionar(data, concesionar_id=int(cid) if cid else None)
    return _back("/concesionari")


@admin.post("/ui/concesionar/{cid}/delete")
def concesionar_delete(request: Request, cid: int):
    _require_writer(request)
    cim.delete_concesionar(cid)
    return _back("/concesionari")


def _main_only() -> HTTPException:
    return HTTPException(status_code=403, detail={
        "error": "main_only", "message": "Aprobarea/respingerea cererilor și crearea contractelor se fac numai în aplicația MAIN."})


@admin.post("/ui/cerere/{cid}/status")
async def cerere_status(request: Request, cid: int):
    """Aprobare / respingere cerere din panoul web (și de pe telefon) — numai cu dreptul `approve`.
    Aprobarea folosește aceeași funcție tranzacțională ca MAIN (concesionar + contract draft + loc rezervat, idempotentă)."""
    user = _require_right(request, "approve")
    form = await request.form()
    status, note = str(form.get("status") or ""), str(form.get("note") or "").strip()[:500]
    cur = cim.get_cerere_concesionare(cid)
    if not cur:
        raise HTTPException(status_code=404, detail="Cererea nu există")
    if status == "validat":
        try:
            res = cimitir_requests.approve_cerere(cid, actor=user["username"])
        except cimitir_requests.ApprovalError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)
        if not res.get("already"):
            cimitir_requests.audit("staff", user["username"], "web_approve_request", str(cid), f"concesionar={res.get('concesionar_id')} contract={res.get('contract_id')}")
    elif status == "respins":
        if cur.get("status") == "validat":
            return JSONResponse({"detail": "Cererea a fost deja aprobată."}, status_code=409)
        if cur.get("status") != "respins":
            cim.set_cerere_status(cid, "respins", note or "Respinsă")
            conn = cim.get_connection()
            try:
                conn.execute("UPDATE cereri_concesionare SET public_message=?, closed_at=? WHERE id=?",
                             (note, datetime.now().isoformat(timespec="seconds"), cid))
            finally:
                conn.close()
            cimitir_requests.audit("staff", user["username"], "web_reject_request", str(cid), note[:200])
            try:
                cimitir_notify.notify_status_change(cur.get("cod_urmarire") or "", "respins", note)
            except Exception:  # noqa: BLE001 - notificarea nu blochează decizia
                pass
    else:
        raise HTTPException(status_code=422, detail="Status invalid (validat | respins)")
    return RedirectResponse(url="/admin/cereri", status_code=303)


def _rights_from_form(b) -> list:
    return [r for r in (b if isinstance(b, list) else [b]) if r in ("edit", "approve")]


def _would_remove_last_admin(uid: int, new_role=None, new_active=None) -> bool:
    target = next((u for u in cim_auth.list_users() if u["id"] == uid), None)
    if not target or target["role"] != "admin" or not target["active"]:
        return False
    if new_role == "admin" and new_active in (None, True):
        return False
    return cim_auth.active_admin_count() <= 1


@admin.post("/ui/user")
async def user_create(request: Request):
    _require_right(request, "manage_users")
    form = await request.form()
    cim_auth.create_user(form.get("username", ""), form.get("password", ""), form.get("role", "operator"),
                         cemetery_id=cim.get_setting("cemetery_id") or "")
    return _back("/utilizatori")


@admin.post("/ui/user/{uid}/update")
async def user_update(request: Request, uid: int):
    _require_right(request, "manage_users")
    form = await request.form()
    role = form.get("role", "")
    active = form.get("active") == "1"
    if _would_remove_last_admin(uid, role, active):
        raise HTTPException(status_code=400, detail="Trebuie să rămână cel puțin un administrator activ.")
    cim_auth.update_user(uid, role=role, rights=_rights_from_form(form.getlist("rights")), active=active)
    return _back("/utilizatori")


@admin.post("/ui/user/{uid}/password")
async def user_password(request: Request, uid: int):
    _require_right(request, "manage_users")
    b = dict(await request.form())
    cim_auth.change_password(uid, b.get("password", ""))
    return _back("/utilizatori")


@admin.post("/ui/user/{uid}/delete")
def user_delete(request: Request, uid: int):
    me = _require_right(request, "manage_users")
    if uid == me["id"] or _would_remove_last_admin(uid, None, False):
        raise HTTPException(status_code=400, detail="Nu poți șterge contul curent sau ultimul administrator.")
    cim_auth.delete_user(uid)
    return _back("/utilizatori")


_CONFIG_KEYS = ("cimitir_nume", "portal_requests_enabled", "offline_lease_hours", "sync_map_version", "sync_last_push_at",
                "retention_days_closed", "retention_days_rejected", "privacy_text", "privacy_version", "portal_require_account",
                "notify_email_enabled", "alert_emails", "public_base_url", "backup_enabled", "backup_dir", "backup_keep_days")


def _config_ctx(request, user, **extra):
    cfg = {k: cim.get_setting(k) or "" for k in _CONFIG_KEYS}
    cfg["portal_requests_enabled"] = cfg["portal_requests_enabled"] or "1"
    cfg["offline_lease_hours"] = cfg["offline_lease_hours"] or str(cim_auth.DEFAULT_LEASE_HOURS)
    cfg["retention_days_closed"] = cfg["retention_days_closed"] or "365"
    cfg["retention_days_rejected"] = cfg["retention_days_rejected"] or "90"
    cfg["privacy_text"] = cfg["privacy_text"] or cimitir_requests.DEFAULT_PRIVACY_TEXT
    cfg["portal_require_account"] = "1" if citizen_api.require_account() else "0"
    cfg["notify_email_enabled"] = "1" if cimitir_notify.enabled() else "0"
    cfg["backup_enabled"] = cfg["backup_enabled"] or "1"
    cfg["backup_keep_days"] = cfg["backup_keep_days"] or "14"
    cfg["backup_dir_effective"] = cfg["backup_dir"] or str(cimitir_backup.default_backup_dir(cimitir_config.working_dir()))
    cfg["email_provider"] = cimitir_notify.provider_name()
    can_config = "manage_config" in user["rights"]
    if can_config and cimitir_config.mode() == "online" and "conn" not in extra:
        try:
            extra["conn"] = sync_api.connection_info(user["source_id"] or DEFAULT_SOURCE["id"], sync_api.request_base_url(request))
        except Exception:
            extra["conn"] = None
    return _ctx(request, user, cfg=cfg, can_config=can_config,
                mode=cimitir_config.mode(), **extra)


@admin.post("/ui/settings")
async def settings_save(request: Request):
    _require_right(request, "manage_config")
    b = dict(await request.form())
    if b.get("cimitir_nume") is not None:
        cim.set_setting("cimitir_nume", b.get("cimitir_nume", "")[:120])
    if b.get("portal_requests_enabled") in ("0", "1"):
        cim.set_setting("portal_requests_enabled", b["portal_requests_enabled"])
    try:
        hours = int(b.get("offline_lease_hours") or 0)
    except ValueError:
        hours = 0
    if 1 <= hours <= 720:
        cim.set_setting("offline_lease_hours", str(hours))
    for key, low, high in (("retention_days_closed", 1, 3650), ("retention_days_rejected", 1, 3650)):
        try:
            value = int(b.get(key) or 0)
        except ValueError:
            value = 0
        if low <= value <= high:
            cim.set_setting(key, str(value))
    if b.get("privacy_text") and b["privacy_text"] != cimitir_requests.DEFAULT_PRIVACY_TEXT:
        if b["privacy_text"][:4000] != (cim.get_setting("privacy_text") or ""):
            cim.set_setting("privacy_text", b["privacy_text"][:4000])
            cim.set_setting("privacy_version", datetime.now().strftime("%Y%m%d%H%M%S"))
    if b.get("portal_require_account") in ("0", "1"):
        cim.set_setting("portal_require_account", b["portal_require_account"])
    if b.get("notify_email_enabled") in ("0", "1"):
        cim.set_setting("notify_email_enabled", b["notify_email_enabled"])
    _save_ops_settings(b)
    cimitir_requests.audit("staff", _require_right(request, "manage_config")["username"], "settings_changed")
    return _back("/setari")


def _save_ops_settings(b: dict) -> None:
    if b.get("alert_emails") is not None:
        emails = [e.strip() for e in b["alert_emails"].replace(";", ",").split(",") if "@" in e and len(e.strip()) <= 120]
        cim.set_setting("alert_emails", ",".join(emails[:5]))
    if b.get("public_base_url") is not None:
        url = b["public_base_url"].strip().rstrip("/")[:200]
        cim.set_setting("public_base_url", url if url.startswith(("http://", "https://")) else "")
    if b.get("email_provider") in ("mock", "smtp"):
        cim.set_setting("email_provider", b["email_provider"])
    if b.get("backup_enabled") in ("0", "1"):
        cim.set_setting("backup_enabled", b["backup_enabled"])
    if b.get("backup_dir") is not None:
        cim.set_setting("backup_dir", b["backup_dir"].strip()[:300])
    if (b.get("backup_keep_days") or "").isdigit():
        cim.set_setting("backup_keep_days", str(min(max(int(b["backup_keep_days"]), 1), 365)))


@admin.get("/monitorizare", response_class=HTMLResponse)
def monitorizare(request: Request):
    user = _require(request)
    if not user:
        return _redirect_login()
    if "manage_config" not in user["rights"]:
        return RedirectResponse(url="/admin/", status_code=303)
    conn = cim.get_connection()
    try:
        by_status = {r["status"]: r["n"] for r in conn.execute(f"SELECT status, COUNT(*) AS n FROM cereri_concesionare WHERE {cim.tenant_scope()} GROUP BY status")}
        unacked = conn.execute(f"SELECT COUNT(*) AS n FROM cereri_concesionare WHERE COALESCE(acked,0)=0 AND {cim.tenant_scope()}").fetchone()["n"]
        citizens = conn.execute(f"SELECT COUNT(*) AS n FROM citizens WHERE {cim.tenant_scope(level='org')}").fetchone()["n"]
    finally:
        conn.close()
    clients = sync_api.clients_status()
    try:
        entity_conflicts = entity_sync_api.open_conflicts(50) if cimitir_config.db_engine() == "postgres" else []
    except Exception:
        entity_conflicts = []
    return templates.TemplateResponse(request, "monitorizare.html", _ctx(
        request, user, entity_conflicts=entity_conflicts, by_status=by_status, unacked=unacked, citizens=citizens, audit=cimitir_requests.recent_audit(100),
        emails=cimitir_notify.recent(50), email_provider=cimitir_notify.provider_name(),
        last_push=cim.get_setting("sync_last_push_at") or "—", map_version=cim.get_setting("sync_map_version") or "0",
        mode=cimitir_config.mode(), clients=clients, stale_minutes=sync_api.stale_minutes(),
        offline_minutes=sync_api.offline_minutes(), offline_clients=[c for c in clients if c["offline"]],
        backups=cimitir_backup.list_backups(_backup_dir())[:15], backup_dir=str(_backup_dir()),
        restore_pending=cimitir_backup.pending_marker(cimitir_config.working_dir()).is_file()))


# =============================================================================
# SISTEM (Etapa 8) — pagină tehnică, DOAR administratori (dreptul manage_users). Fără SQL/comenzi/căi din browser.
# =============================================================================
def _system_gate(request: Request):
    """(user, răspuns_de_refuz). 401/redirect fără sesiune, 403 pentru utilizatori fără drept de administrator."""
    user = current_user(request)
    if not user:
        if request.method == "GET":
            return None, RedirectResponse(url="/admin/access", status_code=303)
        return None, JSONResponse({"detail": "Autentificare necesară"}, status_code=401)
    if "manage_users" not in user["rights"]:
        return None, HTMLResponse("<h1>403</h1><p>Pagina Sistem este rezervată administratorilor.</p><p><a href=\"/admin/\">Înapoi</a></p>", status_code=403)
    return user, None


def _same_origin(request: Request) -> bool:
    site = request.headers.get("sec-fetch-site")
    if site:
        return site in ("same-origin", "none")
    origin = request.headers.get("origin")
    if not origin:
        return True    # client non-browser (nu poartă cookie-ul de sesiune implicit)
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
    return urlparse(origin).netloc == host


@admin.get("/system", response_class=HTMLResponse)
def system_page(request: Request):
    user, refuse = _system_gate(request)
    if refuse:
        return refuse
    status = system_admin.collect_status(_backup_dir())
    return templates.TemplateResponse(request, "system.html", _ctx(
        request, user, st=status, backup_size=system_admin.fmt_bytes(status["backup"]["size"])))


def _system_action(request: Request, action: str, runner):
    user, refuse = _system_gate(request)
    if refuse:
        return refuse
    if not _same_origin(request):
        return JSONResponse({"detail": "Cerere respinsă (origine diferită)"}, status_code=403)
    result = runner()
    cimitir_requests.audit("staff", user["username"], f"system_{action}", "", "ok" if result["ok"] else "atenție")
    return templates.TemplateResponse(request, "partials/system_result.html", {"request": request, "result": result},
                                      headers={"Cache-Control": "no-store"})


@admin.post("/system/test-db", response_class=HTMLResponse)
def system_test_db(request: Request):
    return _system_action(request, "test_db", system_admin.test_db)


@admin.post("/system/test-storage", response_class=HTMLResponse)
def system_test_storage(request: Request):
    return _system_action(request, "test_storage", system_admin.test_storage)


@admin.post("/system/backup", response_class=HTMLResponse)
def system_backup(request: Request):
    return _system_action(request, "backup", lambda: system_admin.backup_now(_backup_dir(), cim.get_setting))


@admin.post("/system/check-sync", response_class=HTMLResponse)
def system_check_sync(request: Request):
    return _system_action(request, "check_sync", system_admin.check_sync)


@admin.post("/system/persistence/create", response_class=HTMLResponse)
def system_persistence_create(request: Request):
    return _system_action(request, "persistence_create", system_admin.persistence_create)


@admin.post("/system/persistence/verify", response_class=HTMLResponse)
def system_persistence_verify(request: Request):
    return _system_action(request, "persistence_verify", system_admin.persistence_verify)


@admin.post("/system/persistence/cleanup", response_class=HTMLResponse)
def system_persistence_cleanup(request: Request):
    return _system_action(request, "persistence_cleanup", system_admin.persistence_cleanup)


@admin.get("/system/logs", response_class=HTMLResponse)
def system_logs(request: Request, lines: int = 200, errors: int = 0):
    user, refuse = _system_gate(request)
    if refuse:
        return refuse
    lines = lines if lines in (100, 200, 500, 1000) else 200
    return templates.TemplateResponse(request, "system_logs.html", _ctx(
        request, user, logs=system_admin.tail_logs(lines, bool(errors)), lines=lines, errors_only=bool(errors)),
        headers={"Cache-Control": "no-store"})


# =============================================================================
# ADMINISTRARE ONLINE -> CONFIGURARE CLOUD (doar administratori; valorile se scriu DOAR în backend/.env)
# Secretele nu sunt trimise niciodată înapoi în pagină, nu intră în jurnal, nu intră în mesajele de eroare, nu se salvează în DB.
# =============================================================================
import cloud_config  # noqa: E402

_NO_STORE = {"Cache-Control": "no-store"}


def _cloud_result(request: Request, ok: bool, title: str, lines: list, trigger: bool = False):
    headers = dict(_NO_STORE)
    if trigger:
        headers["HX-Trigger"] = "cloud-saved"
    return templates.TemplateResponse(request, "partials/system_result.html",
                                      {"request": request, "result": {"ok": ok, "title": title, "lines": lines}}, headers=headers)


def _cloud_values(form) -> dict:
    return {n: str(form.get(n) or "") for n in cloud_config.FIELDS}


@admin.get("/cloud", response_class=HTMLResponse)
def cloud_page(request: Request):
    user, refuse = _system_gate(request)
    if refuse:
        return refuse
    readonly, reason = cloud_config.is_readonly()
    return templates.TemplateResponse(request, "cloud.html", _ctx(
        request, user, fields=cloud_config.field_states(), readonly=readonly, readonly_reason=reason,
        labels=cloud_config.LABELS, drivers=cloud_config.DRIVERS, notes=cloud_config.restart_hint()),
        headers=_NO_STORE)


@admin.get("/cloud/status", response_class=HTMLResponse)
async def cloud_status(request: Request):
    user, refuse = _system_gate(request)
    if refuse:
        return refuse
    st = await run_in_threadpool(cloud_config.status)
    return templates.TemplateResponse(request, "partials/cloud_status.html",
                                      {"request": request, "st": st, "fields": cloud_config.field_states()}, headers=_NO_STORE)


async def _cloud_form(request: Request):
    """(user, form, refuz). Doar POST din aceeași origine, doar administratori."""
    user, refuse = _system_gate(request)
    if refuse:
        return None, None, refuse
    if not _same_origin(request):
        return None, None, JSONResponse({"detail": "Cerere respinsă (origine diferită)"}, status_code=403)
    return user, await request.form(), None


def _cloud_audit(user: dict, action: str, detail: str = "") -> None:
    try:
        cimitir_requests.audit("staff", user["username"], action, "", detail)
    except Exception:
        pass  # auditul nu trebuie să blocheze configurarea (ex. baza tocmai a fost schimbată)


def _cloud_do_save(form) -> tuple:
    """(ok, linii, modificate). Câmpurile secrete lăsate goale păstrează valoarea existentă."""
    readonly, reason = cloud_config.is_readonly()
    if readonly:
        return False, [("err", reason + " Secretele trebuie configurate pe server (variabile de mediu / secret manager); "
                                       "aplicația nu încearcă să le trimită.")], []
    clean, errors, warnings = cloud_config.validate(_cloud_values(form))
    if errors:
        return False, [("err", e) for e in errors], []
    if not clean:
        return True, [("ok", "Nimic de salvat: câmpurile secrete lăsate goale păstrează valorile existente.")], []
    lines: list = []
    if "DATABASE_URL" in clean:
        pg = cloud_config.test_postgres({"DATABASE_URL": clean["DATABASE_URL"]})
        if not pg["ok"]:
            return False, [("err", "Nu am salvat: noul DATABASE_URL nu se poate conecta (previne blocarea accesului la aplicație)."),
                           *pg["lines"]], []
    storage_keys = ("SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_BUCKET")
    if any(k in clean for k in storage_keys) and cloud_config._eff(clean, "CIMITIR_STORAGE_DRIVER").lower() == "supabase":
        # rotirea cheii: nu salva o cheie/URL care nu autentifică (altfel documentele devin inaccesibile)
        probe = cloud_config.test_storage({k: clean[k] for k in storage_keys if k in clean})
        if not probe["ok"] and not probe.get("auth_ok") and probe["state"] != "NECONFIGURAT":
            return False, [("err", "Nu am salvat: noile date Supabase nu autentifică (cheia/URL-ul sunt respinse). Valorile salvate au rămas neschimbate."),
                           *probe["lines"]], []
    before = cloud_config.field_states()
    try:
        changed = cloud_config.write_values(clean)
    except RuntimeError as exc:
        return False, [("err", str(exc))], []
    cloud_config.apply_runtime(clean)
    lines.append(("ok", "Salvat în backend/.env (nu în baza de date): " + (", ".join(changed) if changed else "nicio valoare nouă") + "."))
    lines.append(("ok", "Configurația a fost reîncărcată în server (conexiuni noi), fără restart."))
    for name in clean:
        if before[name]["source"] == "server":
            lines.append(("warn", f"{name} este setat și în mediul serverului; la repornire poate avea prioritate față de backend/.env."))
    lines += [("warn", w) for w in warnings] + [("warn", n) for n in cloud_config.restart_hint()]
    return True, lines, changed


@admin.post("/cloud/save", response_class=HTMLResponse)
async def cloud_save(request: Request):
    user, form, refuse = await _cloud_form(request)
    if refuse:
        return refuse
    ok, lines, changed = await run_in_threadpool(_cloud_do_save, form)
    _cloud_audit(user, "cloud_config_save", ("câmpuri: " + ",".join(changed)) if changed else ("ok" if ok else "respins"))
    return _cloud_result(request, ok, "Salvare configurație cloud", lines, trigger=True)


@admin.post("/cloud/test-postgres", response_class=HTMLResponse)
async def cloud_test_postgres(request: Request):
    user, form, refuse = await _cloud_form(request)
    if refuse:
        return refuse
    res = await run_in_threadpool(cloud_config.test_postgres, _cloud_values(form))
    _cloud_audit(user, "cloud_test_postgres", res["state"])
    return _cloud_result(request, res["ok"], "Test PostgreSQL", res["lines"])


@admin.post("/cloud/test-storage", response_class=HTMLResponse)
async def cloud_test_storage(request: Request):
    user, form, refuse = await _cloud_form(request)
    if refuse:
        return refuse
    res = await run_in_threadpool(cloud_config.test_storage, _cloud_values(form), form.get("create_bucket") == "1")
    _cloud_audit(user, "cloud_test_storage", res["state"])
    return _cloud_result(request, res["ok"], "Test Supabase Storage", res["lines"])


def _cloud_save_and_test(form) -> tuple:
    ok, lines, changed = _cloud_do_save(form)
    if not ok:
        return False, lines, changed
    pg = cloud_config.test_postgres()
    st = cloud_config.test_storage(create_bucket=form.get("create_bucket") == "1")
    lines += pg["lines"] + st["lines"]
    if pg["ok"] and cimitir_config.db_engine() == "postgres":
        try:
            cim.ensure_initialized(DEFAULT_SOURCE)
            lines.append(("ok", "Schema PostgreSQL verificată / aplicată."))
        except Exception as exc:
            lines.append(("err", f"Schema PostgreSQL nu a putut fi inițializată ({type(exc).__name__})."))
            return False, lines, changed
    return pg["ok"] and st["ok"], lines, changed


@admin.post("/cloud/save-test", response_class=HTMLResponse)
async def cloud_save_test(request: Request):
    user, form, refuse = await _cloud_form(request)
    if refuse:
        return refuse
    ok, lines, changed = await run_in_threadpool(_cloud_save_and_test, form)
    _cloud_audit(user, "cloud_config_save_test", ("câmpuri: " + ",".join(changed)) if changed else ("ok" if ok else "atenție"))
    return _cloud_result(request, ok, "Salvare și testare", lines, trigger=True)


def _backup_dir() -> Path:
    custom = (cim.get_setting("backup_dir") or "").strip()
    return Path(custom) if custom else cimitir_backup.default_backup_dir(cimitir_config.working_dir())


@admin.post("/ui/backup")
def backup_now(request: Request):
    user = _require_right(request, "manage_config")
    target = cimitir_backup.run_scheduled(cimitir_config.working_dir(), cim.get_setting, force=True)
    cimitir_requests.audit("staff", user["username"], "backup_manual", "", target.name if target else "eșuat")
    return _back("/monitorizare")


@admin.post("/ui/backup/restore")
async def backup_restore(request: Request):
    user = _require_right(request, "manage_config")
    name = str((await request.form()).get("name") or "")
    known = {row["name"]: row["path"] for row in cimitir_backup.list_backups(_backup_dir())}
    if name not in known:
        raise HTTPException(status_code=404, detail="Copie necunoscută")
    try:
        cimitir_backup.schedule_restore(Path(known[name]), cimitir_config.working_dir())
    except SystemExit as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    cimitir_requests.audit("staff", user["username"], "restore_scheduled", "", name)
    return _back("/monitorizare")

@admin.post("/ui/purge")
def purge_now(request: Request):
    user = _require_right(request, "manage_config")
    n = cimitir_requests.purge_expired(int(cim.get_setting("retention_days_closed") or 365),
                                       int(cim.get_setting("retention_days_rejected") or 90))
    cimitir_notify.purge_sent(int(cim.get_setting("retention_days_closed") or 365))
    cimitir_requests.audit("staff", user["username"], "purge_manual", "", f"{n} cereri")
    return _back("/monitorizare")


@admin.post("/ui/sync-key", response_class=HTMLResponse)
def sync_key_issue(request: Request):
    user = _require_right(request, "manage_config")
    if cimitir_config.mode() != "online":
        raise HTTPException(status_code=404)
    key = sync_api.issue_sync_key(user["source_id"] or DEFAULT_SOURCE["id"])
    cimitir_requests.audit("staff", user["username"], "sync_key_issued", "", "cheie MAIN generată")
    return templates.TemplateResponse(request, "setari.html", _config_ctx(request, user, new_key=key))


@admin.get("/ui/connection-info")
def sync_connection_info_admin(request: Request):
    user = _require_right(request, "manage_config")
    if cimitir_config.mode() != "online":
        raise HTTPException(status_code=404)
    info = sync_api.connection_info(user["source_id"] or DEFAULT_SOURCE["id"], sync_api.request_base_url(request))
    return JSONResponse(info, headers={"Cache-Control": "no-store"})


# =============================================================================
# EXPORT EXCEL
# =============================================================================
@admin.get("/export/locuri.xlsx")
def export_locuri_xlsx(request: Request):
    if not _require(request):
        raise HTTPException(status_code=401)
    locuri_all = cim.list_locuri()
    contracte_all = cim.load_contracte()
    expired = _expired_cods()
    rows = []
    for loc in locuri_all:
        cod = loc.get("cod_loc")
        contracte_loc = [c for c in contracte_all if c.get("cod_loc") == cod]
        conces_nume, contract_numar, contract_sfarsit = "", "", ""
        if contracte_loc:
            c = contracte_loc[-1]
            contract_numar = c.get("numar_contract") or ""
            contract_sfarsit = c.get("data_sfarsit") or c.get("valabilitate") or ""
            if c.get("concesionar_id"):
                try:
                    conces = cim.get_concesionar(int(c["concesionar_id"]))
                    if conces:
                        conces_nume = f"{conces.get('nume', '')} {conces.get('prenume', '')}".strip()
                except Exception:
                    pass
        rows.append({
            "Cod loc": cod, "Sector": loc.get("sector"), "Parcelă": loc.get("parcela"),
            "Rând": loc.get("rand"), "Număr": loc.get("numar"), "Tip": loc.get("tip"),
            "Categorie": loc.get("categorie"), "Capacitate": loc.get("capacitate"),
            "Suprafață (m²)": loc.get("suprafata"), "Status": plot_status(loc, expired),
            "Concesionar": conces_nume, "Contract nr.": contract_numar,
            "Valabilitate/Sfârșit": contract_sfarsit,
            "Nr. înhumați": len(cim.load_inhumati_for_loc(cod)),
            "Total plăți (lei)": cim.total_plati(cod),
        })
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Locuri de veci"
    headers = list(rows[0].keys()) if rows else ["Cod loc"]
    ws.append(headers)
    for row in rows:
        ws.append([row.get(h) for h in headers])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Response(content=buf.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="locuri_de_veci.xlsx"'})


# =============================================================================
# WIRING
# =============================================================================
_cors = [o.strip() for o in os.environ.get("CIMITIR_CORS_ORIGINS", "").split(",") if o.strip()]
if _cors:
    app.add_middleware(CORSMiddleware, allow_origins=_cors, allow_credentials=False,
                       allow_methods=["GET", "POST"], allow_headers=["*"])



async def _db_unavailable(request: Request, exc: Exception):
    """Baza de date inaccesibilă: 503 controlat (fără detalii de conexiune), clienții MAIN păstrează outbox-ul și reîncearcă."""
    logging.getLogger("cimitir").error("PostgreSQL indisponibil: %s", type(exc).__name__)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Baza de date este temporar indisponibilă"}, status_code=503, headers={"Retry-After": "30"})
    return HTMLResponse("<h1>Serviciu temporar indisponibil</h1><p>Baza de date nu răspunde. Încercați din nou în câteva minute.</p>",
                        status_code=503, headers={"Retry-After": "30"})


try:
    from pg_compat import PgUnavailable as _PgUnavailable  # noqa: E402
except Exception:  # noqa: BLE001 - mod SQLite: nu există
    _PgUnavailable = psycopg.OperationalError
for _exc_cls in {psycopg.OperationalError, _PgUnavailable}:
    app.add_exception_handler(_exc_cls, _db_unavailable)


@app.middleware("http")
async def registry_binding(request: Request, call_next):
    """Registre locale: fiecare cerere lucrează pe registrul activ ales în MAIN (detectat din catalog, schimbare sigură)."""
    import cimitir_registry
    if cimitir_registry.catalog_exists(cimitir_config.data_dir()):
        try:
            binding = cimitir_registry.bind_context_from_catalog(cimitir_config.data_dir())
        except Exception as exc:
            return JSONResponse({"detail": f"Registrul activ nu poate fi deschis ({type(exc).__name__})."}, status_code=503)
        if binding is None and request.url.path != "/api/public/health" \
                and not cimitir_registry.legacy_fallback_allowed(cimitir_config.data_dir()):
            if request.url.path.startswith("/api/"):
                return JSONResponse({"detail": "Niciun registru activ. Deschide un registru în MAIN."}, status_code=503)
            return HTMLResponse("<!doctype html><meta charset=utf-8><title>Cimitir</title><body style='font-family:sans-serif;max-width:34em;margin:4em auto'>"
                                "<h2>Niciun registru deschis</h2><p>Portalul local pornește imediat ce deschizi un cimitir în aplicația MAIN "
                                "(Organizație → Cimitir → Deschide registru).</p>", status_code=503, headers={"Retry-After": "5"})
    return await call_next(request)


@app.middleware("http")
async def request_guard(request: Request, call_next):
    """(1) Octetul NUL în cale/query nu poate fi stocat în PostgreSQL: 400 în loc de 500.
    (2) CSRF: orice cerere care modifică starea pe /admin și /api/admin trebuie să fie de aceeași origine
    (Sec-Fetch-Site / Origin); clienții non-browser fără aceste antete (teste, scripturi) nu sunt afectați."""
    if "\x00" in request.url.path or "%00" in (request.url.query or "").lower() or "\x00" in (request.url.query or ""):
        return JSONResponse({"detail": "Cerere invalidă"}, status_code=400)
    path = request.url.path
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and (path.startswith("/admin") or path.startswith("/api/admin")) \
            and not _same_origin(request):
        return JSONResponse({"detail": "Cerere respinsă (origine diferită)"}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def force_password_change(request: Request, call_next):
    path = request.url.path.rstrip("/") or "/"
    if (path.startswith("/admin") or path.startswith("/api/admin")) and path not in _PASSWORD_EXEMPT \
            and _token_needs_change(request):
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Schimbă parola implicită înainte de a continua."}, status_code=403)
        return RedirectResponse(url="/admin/schimba-parola", status_code=303)
    return await call_next(request)


app.include_router(admin)
app.include_router(sync_api.router)
app.include_router(entity_sync_api.router)
app.include_router(media_api.router)
app.include_router(mobile_upload.sync_router)
app.include_router(mobile_upload.public_router)
app.include_router(api_admin)

# Portal public (rutează pe sursa implicită): /portal/*, /api/public/*
web_public.register(app)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=cimitir_config.portal_host(), port=cimitir_config.portal_port(), reload=False)
