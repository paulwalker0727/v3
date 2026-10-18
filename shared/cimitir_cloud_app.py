"""cimitir_cloud_app.py — procesul de rulat PERMANENT în cloud (host Linux/container, în spatele unui reverse proxy HTTPS).

Compune, fără să le modifice, componentele existente: Field Inbox v1 + Phone Pairing v1 (cimitir_field_inbox_http) peste PostgreSQL (plan de control, rolul
`cimitir_cp_app`) și Supabase Storage PRIVAT. Adaugă doar: pagina „Trimite din teren” fără MAIN (`/phone`), health read-only și pornirea fail-fast.

  python -m cimitir_cloud_app      # PORT (implicit 8080), CIMITIR_CLOUD_HOST (implicit 0.0.0.0); uvicorn pornește cu proxy_headers=False:
                                   # încrederea în proxy e DOAR CIMITIR_PAIRING_TRUSTED_PROXIES. Nu porni cu `uvicorn ... --factory` fără --no-proxy-headers.

Nu depinde de SQLite/fișiere/procesul MAIN. Browserul nu primește niciodată credențiale de server (parola PostgreSQL, SUPABASE_SECRET_KEY, service_role, cleanup).
"""
from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

import anyio
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

import cimitir_cloud_admin as admin_mod
import cimitir_cloud_admin_http as adminh
import cimitir_cloud_http as cloud_http
import cimitir_cloud_wire as wire
import cimitir_field_inbox_http as fih
import cimitir_phone_pairing_http as pph
import cimitir_mobile_http as mih
import cimitir_mobile_registry as mir
import cimitir_sync_v2 as sv2
import cimitir_sync_v2_http as sv2h
import cimitir_sync_v2_docs as sdocs

log = logging.getLogger("cimitir.cloud_app")
ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
REQUIRED_ENV = ("CIMITIR_CP_DATABASE_URL", "SUPABASE_URL", "SUPABASE_BUCKET", "CIMITIR_CLOUD_PROJECT_REF")
HEALTH_PATH = wire.API + "/health"
_NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}
_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_STATIC = {"teren.js": "application/javascript", "teren_ui.js": "application/javascript", "teren.css": "text/css"}
_MOBILE_STATIC = {"mobile_app.js": "application/javascript", "mobile_offline.js": "application/javascript", "mobile_photos.js": "application/javascript", "jsQR.js": "application/javascript", "mobile_app.css": "text/css", "mobile-icon-192.png": "image/png", "mobile-icon-512.png": "image/png"}
_ADMIN_STATIC = {"cloud_admin.js": "application/javascript", "cloud_admin.css": "text/css"}
_SKIP_DOCS = ("carte_identitate", "cerere_doc")
_DB_TIMEOUT = 3


def missing_env(environ: Optional[Dict[str, str]] = None) -> list:
    """Numele (niciodată valorile) variabilelor obligatorii lipsă."""
    env = os.environ if environ is None else environ
    miss = [k for k in REQUIRED_ENV if not (env.get(k) or "").strip()]
    if not ((env.get("SUPABASE_SECRET_KEY") or "").strip() or (env.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()):
        miss.append("SUPABASE_SECRET_KEY")
    return miss


def _public_url() -> str:
    u = (os.environ.get("CIMITIR_PUBLIC_URL") or "").strip().rstrip("/")
    p = urlparse(u)
    return u if p.scheme in ("http", "https") and p.hostname else ""


def probe_database(service: Any) -> bool:
    """SELECT 1 cu rolul aplicației, timeout scurt. Orice eroare => False; mesajul (poate conține gazda/utilizatorul) nu se loghează și nu se expune."""
    try:
        import psycopg
        with psycopg.connect(service.cp.url, connect_timeout=_DB_TIMEOUT, autocommit=True) as c:
            return c.execute("SELECT 1").fetchone()[0] == 1
    except Exception as exc:  # noqa: BLE001
        log.warning("health: baza de date indisponibilă (%s)", type(exc).__name__)
        return False


def probe_storage(service: Any) -> bool:
    """Doar configurare (fără apel de rețea, fără valori): driverul Supabase cu bucket setat."""
    st = getattr(getattr(service, "media", None), "storage", None)
    return getattr(st, "name", "") == "supabase" and bool(getattr(st, "bucket", ""))


def _page_loc(q) -> Optional[Dict[str, Any]]:
    """Deep-link opțional (QR/legătură): ?cod_loc=&grave_uid=&target_uid=&target_label= — doar UUID-uri valide; fără acces la vreun registru."""
    gid = (q.get("grave_uid") or "").strip()
    if not _UUID_RE.match(gid):
        return None
    cod = (q.get("cod_loc") or "").strip()[:40] or gid[:8]
    avize = []
    tid = (q.get("target_uid") or "").strip()
    if _UUID_RE.match(tid):
        avize.append({"uid": tid.lower(), "label": ((q.get("target_label") or "").strip()[:60] or "Lucrare (din link)")})
    return {"cod_loc": cod, "grave_uid": gid.lower(), "avize": avize}


def _template():
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(BACKEND / "templates")), autoescape=True)
    return env


def create_app(service: Any = None, origins: Optional[list] = None, pairing: Any = None,
               db_probe: Optional[Callable[[Any], bool]] = None, storage_probe: Optional[Callable[[Any], bool]] = None) -> FastAPI:
    import cimitir_field_inbox as fi
    service = service or fi.build_service_from_env()
    app = fih.create_app(service, origins, pairing)
    app.include_router(mih.create_router(mir.MobileRegistryService(service)))
    sync_service = sv2.SyncV2Service(service)
    media = getattr(service, "media", None)
    app.include_router(sv2h.create_router(sync_service, sdocs.DocumentService(sync_service, media.storage) if media is not None else None))
    app.include_router(cloud_http.create_router(service.cp))               # /api/v1/bootstrap/* (asociere MAIN), pe PostgreSQL
    admin_service = admin_mod.AdminService(service.cp)
    app.include_router(adminh.create_router(admin_service))                 # /api/v1/admin/* (sesiune administrativă)
    jenv = _template()
    sys.path.insert(0, str(BACKEND))
    import attachments
    doc_types = [(k, v) for k, v in attachments.DOC_TYPES.items() if k not in _SKIP_DOCS]
    db_ok, st_ok = db_probe or probe_database, storage_probe or probe_storage

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return JSONResponse({"ok": True, "api": "up"}, headers=_NO_STORE)

    @app.get(HEALTH_PATH, include_in_schema=False)
    async def health():
        db = await anyio.to_thread.run_sync(lambda: bool(db_ok(service)))
        st = bool(st_ok(service))
        body = {"ok": db and st, "api": "up", "database": "up" if db else "down", "storage": "configured" if st else "not_configured"}
        return JSONResponse(body, status_code=200 if body["ok"] else 503, headers=_NO_STORE)

    @app.get("/phone", include_in_schema=False)
    def phone_page(request: Request):
        cfg = {"standalone": True, "cloud_url": _public_url(), "cemetery_uid": "", "cemetery_label": "", "loc": _page_loc(request.query_params)}
        html = jenv.get_template("phone_capture.html").render(cfg=cfg, doc_types=doc_types)
        return HTMLResponse(html, headers={**_NO_STORE, "Content-Security-Policy": _CSP, "X-Frame-Options": "DENY"})

    @app.get("/admin", include_in_schema=False)
    def admin_redirect():
        return RedirectResponse("/admin/", status_code=307, headers=_NO_STORE)

    @app.get("/admin/", include_in_schema=False)
    def admin_page():
        html = (BACKEND / "templates" / "cloud_admin.html").read_text(encoding="utf-8")
        return HTMLResponse(html, headers={**_NO_STORE, "Content-Security-Policy": _CSP, "X-Frame-Options": "DENY"})

    @app.get("/admin-assets/{name}", include_in_schema=False)
    def admin_static(name: str):
        if name not in _ADMIN_STATIC:
            return Response(status_code=404)
        return FileResponse(BACKEND / "static" / name, media_type=_ADMIN_STATIC[name], headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})

    @app.get("/static/{name}", include_in_schema=False)
    def static_file(name: str):
        if name not in _STATIC:
            return Response(status_code=404)
        return FileResponse(BACKEND / "static" / name, media_type=_STATIC[name], headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})

    @app.get("/mobile", include_in_schema=False)
    def mobile_redirect():
        return RedirectResponse("/mobile/", status_code=307, headers=_NO_STORE)

    @app.get("/mobile/", include_in_schema=False)
    def mobile_page():
        html = jenv.get_template("mobile_app.html").render()
        return HTMLResponse(html, headers={**_NO_STORE, "Content-Security-Policy": _CSP, "X-Frame-Options": "DENY"})

    @app.get("/mobile/manifest.webmanifest", include_in_schema=False)
    def mobile_manifest():
        return FileResponse(BACKEND / "static" / "mobile_manifest.webmanifest", media_type="application/manifest+json",
                            headers={"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"})

    @app.get("/mobile/sw.js", include_in_schema=False)
    def mobile_sw():
        return FileResponse(BACKEND / "static" / "mobile_sw.js", media_type="application/javascript",
                            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/mobile/", "X-Content-Type-Options": "nosniff"})

    @app.get("/mobile-assets/{name}", include_in_schema=False)
    def mobile_static(name: str):
        if name not in _MOBILE_STATIC:
            return Response(status_code=404)
        return FileResponse(BACKEND / "static" / name, media_type=_MOBILE_STATIC[name],
                            headers={"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"})

    return app


def validate_trusted_proxies() -> int:
    """Fail-fast: valoare invalidă => ValueError. Returnează numărul de rețele de încredere (0 = XFF ignorat)."""
    return len(pph.parse_trusted_proxies(os.environ.get(pph.ENV_TRUSTED, "")))


def preflight(environ: Optional[Dict[str, str]] = None) -> list:
    """Probleme de configurare (fără valori). Listă goală = poate porni."""
    env = os.environ if environ is None else environ
    issues = [f"lipsește variabila {k}" for k in missing_env(env)]
    ref = (env.get("CIMITIR_CLOUD_PROJECT_REF") or "").strip()
    if ref and not re.fullmatch(r"[a-z]{20}", ref):
        issues.append("CIMITIR_CLOUD_PROJECT_REF invalid (20 litere mici)")
    host = (urlparse(env.get("SUPABASE_URL") or "").hostname or "")
    if ref and host and host != f"{ref}.supabase.co":
        issues.append("SUPABASE_URL nu corespunde CIMITIR_CLOUD_PROJECT_REF")
    db_url = env.get("CIMITIR_CP_DATABASE_URL") or ""
    if ref and "supabase" in db_url and ref not in db_url:
        issues.append("CIMITIR_CP_DATABASE_URL nu corespunde CIMITIR_CLOUD_PROJECT_REF")
    try:
        pph.parse_trusted_proxies(env.get(pph.ENV_TRUSTED, ""))
    except ValueError:
        issues.append(f"{pph.ENV_TRUSTED} invalid (listă IP/CIDR, fără /0)")
    return issues


def schema_issues(db_url: str) -> list:
    """Migrarea 023 (admin + revocare coduri) trebuie aplicată înainte de pornire; mesajele nu conțin gazde/credențiale."""
    try:
        import psycopg
        with psycopg.connect(db_url, connect_timeout=_DB_TIMEOUT, autocommit=True) as c:
            ok = c.execute("SELECT to_regprocedure('cp_admin_status()') IS NOT NULL").fetchone()[0]
        return [] if ok else ["migrarea 023_cloud_admin_auth.sql nu este aplicată (rulează scripts/apply_cloud_admin_migration.py)"]
    except Exception as exc:  # noqa: BLE001
        log.warning("schemă: verificarea migrării 023 nu a putut rula acum (%s); health va raporta baza de date", type(exc).__name__)
        return []


def main() -> int:
    import uvicorn
    logging.basicConfig(level=os.environ.get("CIMITIR_LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    issues = preflight()
    if issues:
        for i in issues:
            log.error("configurare: %s", i)
        return 2
    schema = schema_issues(os.environ["CIMITIR_CP_DATABASE_URL"])
    if schema:
        for i in schema:
            log.error("schemă: %s", i)
        return 3
    try:
        import psycopg
        with psycopg.connect(os.environ["CIMITIR_CP_DATABASE_URL"], connect_timeout=_DB_TIMEOUT, autocommit=True) as c:
            if not c.execute("SELECT to_regprocedure('cp_admin_provision(uuid,uuid,text,uuid,text)') IS NOT NULL").fetchone()[0]:
                log.warning("migrarea 025 nu este aplicată: provizionarea automată a registrului din MAIN este indisponibilă (scripts/apply_cloud_provision_migration.py)")
    except Exception:  # noqa: BLE001
        pass
    st = admin_mod.bootstrap_token_state()
    if st not in ("ok", "missing"):
        log.warning("BOOTSTRAP_TOKEN neutilizabil (%s): inițializarea primului administrator rămâne dezactivată", st)
    if not validate_trusted_proxies():
        log.warning("%s gol: X-Forwarded-For este ignorat; în spatele unui reverse proxy rate limiting-ul vede IP-ul proxy-ului", pph.ENV_TRUSTED)
    raw_cors = os.environ.get(fih.ENV_CORS, "")
    if len([x for x in raw_cors.split(",") if x.strip()]) != len(fih.cors_origins(raw_cors)):
        log.warning("%s: intrările invalide sau '*' sunt ignorate (doar origini explicite http(s)://gazdă)", fih.ENV_CORS)
    uvicorn.run("cimitir_cloud_app:create_app", factory=True, host=os.environ.get("CIMITIR_CLOUD_HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8080")),
                proxy_headers=False, server_header=False, access_log=False, log_level=os.environ.get("CIMITIR_LOG_LEVEL", "info").lower())
    return 0


if __name__ == "__main__":
    sys.exit(main())
