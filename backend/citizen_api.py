"""Conturi cetățeni (separate de personal) + cererile proprii, documente autorizate, completări."""
from __future__ import annotations

import hashlib
import os
import secrets
import tempfile
import time
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import jwt
from fastapi import APIRouter, Body, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse

import cimitir_auth
import cimitir_config
import cimitir_notify
import cimitir_requests as reqs
import portal_ratelimit
import store as cim

router = APIRouter(prefix="/api/public/account")
privacy_router = APIRouter(prefix="/api/public")
COOKIE = "cim_citizen"


def _fails_get(key: tuple) -> tuple:
    raw = cim.get_setting("loginfail:%s|%s" % key) or "0,0"
    tries, until = raw.split(",")
    return int(tries), float(until)


def _fails_set(key: tuple, tries: int, until: float) -> None:
    cim.set_setting("loginfail:%s|%s" % key, "%d,%s" % (tries, until))
LABELS = {"nou": "Înregistrată", "completari": "Sunt necesare completări", "validat": "Aprobată (contract în pregătire)",
          "respins": "Respinsă"}


def require_account() -> bool:
    value = cim.get_setting("portal_require_account")
    if value in ("0", "1"):
        return value == "1"
    return os.environ.get("CIMITIR_PORTAL_REQUIRE_ACCOUNT", "1") != "0"


def privacy_info() -> dict:
    return {"version": cim.get_setting("privacy_version") or "sablon-1",
            "text": cim.get_setting("privacy_text") or reqs.DEFAULT_PRIVACY_TEXT,
            "retention_days_closed": int(cim.get_setting("retention_days_closed") or 365),
            "retention_days_rejected": int(cim.get_setting("retention_days_rejected") or 90)}


@privacy_router.get("/privacy")
def privacy():
    return privacy_info()


def current_citizen(request: Request) -> Optional[dict]:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    try:
        p = jwt.decode(token, cimitir_config.jwt_secret(), algorithms=["HS256"], audience="citizen")
    except jwt.PyJWTError:
        return None
    conn = cim.get_connection()
    try:
        row = conn.execute("SELECT id, email, full_name, active, password_hash FROM citizens WHERE id=?", (int(p["sub"]),)).fetchone()
    finally:
        conn.close()
    if not row or not row["active"] or p.get("pv") != _pw_version(row["password_hash"]):
        return None
    return {k: row[k] for k in ("id", "email", "full_name", "active")}


def _need(request: Request) -> dict:
    c = current_citizen(request)
    if not c:
        raise HTTPException(status_code=401, detail="Autentificare necesară")
    return c


def _pw_version(password_hash: str) -> str:
    """Amprenta parolei curente: sesiunile existente nu mai sunt valide după resetarea parolei."""
    return hashlib.sha256(("pv:" + password_hash).encode()).hexdigest()[:16]


def _token(cid: int, password_hash: str) -> str:
    return jwt.encode({"sub": str(cid), "aud": "citizen", "pv": _pw_version(password_hash), "exp": int(time.time()) + 8 * 3600},
                      cimitir_config.jwt_secret(), algorithm="HS256")


def _secure_cookie(request: Optional[Request]) -> bool:
    """`Secure` când conexiunea e HTTPS (direct / X-Forwarded-Proto) sau CIMITIR_COOKIE_SECURE=1; nu pe http://localhost."""
    if os.environ.get("CIMITIR_COOKIE_SECURE", "").strip() == "1":
        return True
    if request is None:
        return False
    return (request.headers.get("x-forwarded-proto") or request.url.scheme or "").split(",")[0].strip().lower() == "https"


def _cookie(resp: Response, cid: int, request: Optional[Request] = None) -> None:
    conn = cim.get_connection()
    try:
        row = conn.execute("SELECT password_hash FROM citizens WHERE id=?", (cid,)).fetchone()
    finally:
        conn.close()
    resp.set_cookie(COOKIE, _token(cid, row["password_hash"]), httponly=True, samesite="lax", max_age=8 * 3600, path="/",
                    secure=_secure_cookie(request))


@router.post("/register")
def register(request: Request, response: Response, payload: dict = Body(...)):
    portal_ratelimit.enforce(request, "register")
    email = str(payload.get("email") or "").strip().lower()
    password = str(payload.get("password") or "")
    if "@" not in email or len(email) > 120 or len(password) < 10:
        raise HTTPException(status_code=422, detail="Email valid și parolă de minimum 10 caractere.")
    conn = cim.get_connection()
    try:
        if conn.execute(f"SELECT 1 FROM citizens WHERE lower(email)=lower(?) AND {cim.tenant_scope(level='org')}", (email,)).fetchone():
            raise HTTPException(status_code=409, detail="Există deja un cont cu acest email.")
        cur = conn.execute("INSERT INTO citizens (uid, email, password_hash, full_name, created_at, active) VALUES (?,?,?,?,?,1)",
                           (str(uuid.uuid4()), email, cimitir_auth.hash_password(password), str(payload.get("full_name") or "")[:120],
                            datetime.now(timezone.utc).isoformat(timespec="seconds")))
        cid = cur.lastrowid
    finally:
        conn.close()
    reqs.audit("citizen", email, "register")
    _cookie(response, cid, request)
    return {"ok": True, "email": email}


@router.post("/login")
def login(request: Request, response: Response, payload: dict = Body(...)):
    email = str(payload.get("email") or "").strip().lower()
    portal_ratelimit.blocked(request, "login_fail_ip")
    key = (email, portal_ratelimit.client_ip(request))
    tries, until = _fails_get(key)
    if until > time.time():
        raise HTTPException(status_code=429, detail="Prea multe încercări. Reîncercați peste câteva minute.")
    conn = cim.get_connection()
    try:
        row = conn.execute(f"SELECT * FROM citizens WHERE lower(email)=lower(?) AND {cim.tenant_scope(level='org')}", (email,)).fetchone()
    finally:
        conn.close()
    if not row or not row["active"] or not cimitir_auth.verify_password(str(payload.get("password") or ""), row["password_hash"]):
        tries += 1
        _fails_set(key, tries, time.time() + 300 if tries >= 5 else 0)
        portal_ratelimit.record("login_fail_ip", portal_ratelimit.client_ip(request))
        reqs.audit("citizen", email, "login_failed")
        raise HTTPException(status_code=401, detail="Email sau parolă greșite.")
    _fails_set(key, 0, 0)
    _cookie(response, row["id"], request)
    return {"ok": True, "email": email}


RESET_TTL_SECONDS = 3600
FORGOT_MESSAGE = "Dacă există un cont cu acest email, am trimis un link pentru resetarea parolei (valabil 1 oră)."


def _ensure_resets() -> None:
    conn = cim.get_connection()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS citizen_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT, citizen_id INTEGER NOT NULL, token_hash TEXT NOT NULL UNIQUE,
            expires_at REAL NOT NULL, used_at TEXT, created_at TEXT)""")
    finally:
        conn.close()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def public_base_url(request: Request) -> str:
    configured = (os.environ.get("CIMITIR_PUBLIC_URL") or cim.get_setting("public_base_url") or "").strip().rstrip("/")
    return configured or str(request.base_url).rstrip("/")


def _send_reset_email(request: Request, email: str, name: str, token: str) -> None:
    link = f"{public_base_url(request)}/portal/cont?reset={token}"
    cimitir = cim.get_setting("cimitir_nume") or "Cimitir"
    body = (f"Bună ziua{' ' + name if name else ''},\n\nAm primit o cerere de resetare a parolei pentru contul dumneavoastră de pe portalul {cimitir}.\n"
            f"Deschideți linkul (valabil 1 oră, o singură utilizare):\n{link}\n\nDacă nu ați cerut resetarea, ignorați acest mesaj; parola nu se schimbă.\n\nCu respect,\n{cimitir}")
    cimitir_notify.send_message(email, "Resetarea parolei contului de pe portal", body)


@router.post("/forgot")
def forgot_password(request: Request, payload: dict = Body(...)):
    """Răspunde mereu la fel (nu dezvăluie dacă emailul are cont)."""
    portal_ratelimit.enforce(request, "forgot")
    email = str(payload.get("email") or "").strip().lower()
    if "@" not in email or len(email) > 120:
        return {"ok": True, "message": FORGOT_MESSAGE}
    portal_ratelimit.enforce(request, "forgot_email", key=hashlib.sha256(email.encode()).hexdigest()[:20])
    _ensure_resets()
    conn = cim.get_connection()
    try:
        row = conn.execute(f"SELECT id, full_name FROM citizens WHERE lower(email)=lower(?) AND active=1 AND {cim.tenant_scope(level='org')}", (email,)).fetchone()
        token = secrets.token_urlsafe(32)
        if row:
            conn.execute("UPDATE citizen_resets SET used_at=? WHERE citizen_id=? AND used_at IS NULL", ("superseded", row["id"]))
            conn.execute("INSERT INTO citizen_resets (citizen_id, token_hash, expires_at, created_at) VALUES (?,?,?,?)",
                         (row["id"], _hash_token(token), time.time() + RESET_TTL_SECONDS, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    finally:
        conn.close()
    if row:
        reqs.audit("citizen", email, "password_reset_requested")
        try:
            _send_reset_email(request, email, row["full_name"] or "", token)
        except Exception as exc:
            reqs.audit("citizen", email, "password_reset_email_failed", "", str(exc)[:120])
    return {"ok": True, "message": FORGOT_MESSAGE}


@router.post("/reset")
def reset_password(request: Request, payload: dict = Body(...)):
    portal_ratelimit.enforce(request, "reset")
    token, password = str(payload.get("token") or ""), str(payload.get("password") or "")
    if len(password) < 10:
        raise HTTPException(status_code=422, detail="Parola trebuie să aibă minimum 10 caractere.")
    _ensure_resets()
    conn = cim.get_connection()
    try:
        row = conn.execute("SELECT id, citizen_id, expires_at, used_at FROM citizen_resets WHERE token_hash=?", (_hash_token(token),)).fetchone()
        if not row or row["used_at"] or row["expires_at"] < time.time():
            raise HTTPException(status_code=400, detail="Linkul de resetare este invalid sau a expirat. Cereți unul nou.")
        conn.execute("UPDATE citizens SET password_hash=? WHERE id=? AND active=1", (cimitir_auth.hash_password(password), row["citizen_id"]))
        conn.execute("UPDATE citizen_resets SET used_at=? WHERE citizen_id=? AND used_at IS NULL",
                     (datetime.now(timezone.utc).isoformat(timespec="seconds"), row["citizen_id"]))
        email = conn.execute("SELECT email FROM citizens WHERE id=?", (row["citizen_id"],)).fetchone()["email"]
    finally:
        conn.close()
    reqs.audit("citizen", email, "password_reset_done")
    return {"ok": True}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    c = _need(request)
    return {"email": c["email"], "full_name": c["full_name"]}


@router.delete("")
def delete_account(request: Request, response: Response):
    """Șterge contul și anonimizează cererile proprii."""
    c = _need(request)
    conn = cim.get_connection()
    try:
        conn.execute("UPDATE cereri_concesionare SET citizen_id=NULL WHERE citizen_id=?", (c["id"],))
        conn.execute("DELETE FROM citizens WHERE id=?", (c["id"],))
    finally:
        conn.close()
    reqs.audit("citizen", c["email"], "delete_account")
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


def _own(request: Request, code: str) -> tuple:
    c = _need(request)
    conn = cim.get_connection()
    try:
        row = conn.execute("SELECT * FROM cereri_concesionare WHERE cod_urmarire=? AND citizen_id=?", (code.strip().upper(), c["id"])).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Cerere negăsită")
    return c, dict(row)


def _view(row: dict, with_docs: bool = False) -> dict:
    out = {"cod_urmarire": row["cod_urmarire"], "tip": row.get("tip") or "concesionare", "status": row["status"],
           "status_label": LABELS.get(row["status"], row["status"]), "cod_loc": row["cod_loc"], "depusa_la": row["created_at"],
           "actualizata_la": row["reviewed_at"], "mesaj": row.get("public_message") or ""}
    if with_docs:
        out["documente"] = [{"id": d["id"], "nume": d.get("caption"),
                             "disponibil_pentru_descarcare": bool(d.get("file_path")) and os.path.exists(d["file_path"])}
                            for d in cim.list_documents("cerere", row["id"])]
    return out


@router.get("/cereri")
def my_requests(request: Request):
    c = _need(request)
    conn = cim.get_connection()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM cereri_concesionare WHERE citizen_id=? ORDER BY id DESC", (c["id"],))]
    finally:
        conn.close()
    return {"cereri": [_view(r) for r in rows]}


@router.get("/cereri/{code}")
def my_request(request: Request, code: str):
    return _view(_own(request, code)[1], with_docs=True)


@router.get("/cereri/{code}/documents/{doc_id}")
def my_document(request: Request, code: str, doc_id: int):
    c, row = _own(request, code)
    doc = next((d for d in cim.list_documents("cerere", row["id"]) if d["id"] == doc_id), None)
    if not doc:
        raise HTTPException(status_code=404)
    path = doc.get("file_path") or ""
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=410, detail="Documentul a fost preluat de instituție și nu mai este păstrat online.")
    reqs.audit("citizen", c["email"], "document_download", code, str(doc_id))
    return FileResponse(path, filename=reqs.safe_filename(doc.get("caption") or "document"))


def _stored_in_storage(doc_id: int) -> bool:
    """Pe server (PostgreSQL) un document este „salvat" doar dacă are `media_id` (obiect în Storage)."""
    if getattr(cim, "ENGINE", "") != "postgres":
        return True
    conn = cim.get_connection()
    try:
        row = conn.execute("SELECT media_id FROM documents WHERE id=?", (doc_id,)).fetchone()
    finally:
        conn.close()
    return bool(row and row["media_id"])


async def save_documents(cerere_id: int, files: List[UploadFile]) -> tuple:
    """Validează (tip, semnătură, dimensiune) și salvează în spațiul privat. Întoarce (salvate, respinse)."""
    saved, rejected = 0, []
    existing = len(cim.list_documents("cerere", cerere_id))
    for f in files or []:
        if not f or not f.filename:
            continue
        if existing + saved >= reqs.MAX_DOCS_PER_REQUEST:
            rejected.append(f"{f.filename}: prea multe documente")
            continue
        data = await f.read(reqs.MAX_UPLOAD_BYTES + 1)
        try:
            name = reqs.validate_upload(f.filename, data)
        except ValueError as exc:
            rejected.append(f"{reqs.safe_filename(f.filename)}: {exc}")
            continue
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(name)[1])
        try:
            tmp.write(data)
            tmp.close()
            did = cim.add_document("cerere", cerere_id, "cerere_doc", tmp.name, caption=name)
        finally:
            os.unlink(tmp.name)
        if did:
            if not _stored_in_storage(did):
                # Storage indisponibil în acest moment: fără obiect în Storage documentul ar rămâne doar pe discul serverului
                # (efemer în cloud) iar cetățeanul ar primi „salvat". Îl retragem și îl raportăm ca respins => reîncearcă.
                cim.delete_document(did)
                rejected.append(f"{name}: stocarea documentului a eșuat temporar, încercați din nou")
                continue
            conn = cim.get_connection()
            try:
                conn.execute("UPDATE documents SET sha256=?, size=? WHERE id=?", (hashlib.sha256(data).hexdigest(), len(data), did))
            finally:
                conn.close()
            saved += 1
    return saved, rejected


@router.post("/cereri/{code}/completari")
async def submit_completion(request: Request, code: str, mesaj: str = Form(""), files: List[UploadFile] = File(default=[])):
    c, row = _own(request, code)
    portal_ratelimit.enforce(request, "completari")
    if row["status"] != "completari":
        raise HTTPException(status_code=409, detail="Cererea nu așteaptă completări.")
    saved, rejected = await save_documents(row["id"], files)
    if not saved and not mesaj.strip():
        raise HTTPException(status_code=422, detail="Adăugați un mesaj sau documente. " + "; ".join(rejected))
    conn = cim.get_connection()
    try:
        obs = ((row.get("observatii") or "") + f"\n[Completare cetățean {datetime.now():%d.%m.%Y}] {mesaj.strip()[:1000]}").strip()
        conn.execute("UPDATE cereri_concesionare SET status='nou', observatii=?, revision=COALESCE(revision,0)+1, acked=0, "
                     "public_message='' WHERE id=?", (obs, row["id"]))
    finally:
        conn.close()
    reqs.audit("citizen", c["email"], "completion_submitted", code, f"{saved} documente")
    return {"ok": True, "documente": saved, "respinse": rejected}
