"""Pasul 6 — MAIN -> telefon (QR cu token temporar) -> încărcare document, prin internet (HTTPS).

Flux:
  MAIN (X-Sync-Key)  POST /api/sync/mobile/tokens {entity_type, entity_uid}      -> {url, tid, expires_at, qr_png}
  Telefon            GET  /api/m/<token>                                        -> pagină mobilă (entitatea + cameră/PDF)
                     GET  /api/m/<token>/info                                   -> JSON cu entitatea (fără secrete)
                     POST /api/m/<token>/upload (multipart: file, doc_type, description)
  MAIN               GET  /api/sync/mobile/tokens/<tid>                         -> stare (folosit / expirat / documente)
                     apoi sincronizarea media obișnuită (Etapa 7) aduce documentul; UID-ul entității e același.

Garanții:
  * tokenul = `<id cimitir>.<secret 256 biți>`; în baza de date se păstrează DOAR sha256(secret); URL-ul nu conține parole/chei;
  * valabilitate 5–10 minute (implicit 10), legat de O SINGURĂ entitate (tip + id + uid), `max_uses` implicit 1 (single-use, atomic);
  * entitatea NU se citește din cererea telefonului: câmpurile `entity_*` trimise de client sunt doar comparate și, dacă diferă, respinse;
  * documentul trece prin același pipeline ca tot restul (`attachments.add_attachment` -> Storage -> metadate în PostgreSQL);
  * telefonul vorbește doar cu FastAPI; nu primește SUPABASE_SECRET_KEY / chei de storage / DATABASE_URL;
  * online: URL-ul public vine din CIMITIR_PUBLIC_URL (https, fără localhost / IP privat). Modul LAN (URL din cerere) există doar ca
    fallback când serverul NU e în mod online.
"""
from __future__ import annotations

import base64
import hashlib
import io
import ipaddress
import json
import os
import secrets
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

import attachments
import cimitir_auth
import cimitir_config
import cimitir_requests
import portal_ratelimit
import store as cim
import sync_api

sync_router = APIRouter(prefix="/api/sync/mobile")
public_router = APIRouter(prefix="/api/m")

TTL_MIN, TTL_MAX, TTL_DEFAULT = 5, 10, 10          # minute
MAX_USES_LIMIT = 5
_NOSTORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}
_ENTITY_TABLES = {"loc": "locuri_de_veci", "concesionar": "concesionari", "contract": "contracte", "inhumat": "inhumati", "plata": "plati"}
_SECRET_NAMES = ("SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY", "S3_SECRET_ACCESS_KEY", "DATABASE_URL")

_DDL = ("CREATE TABLE IF NOT EXISTS mobile_upload_tokens ("
        "token_hash TEXT PRIMARY KEY, tid TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, entity_uid TEXT, "
        "created_by TEXT, created_at BIGINT NOT NULL, expires_at BIGINT NOT NULL, max_uses INTEGER NOT NULL DEFAULT 1, "
        "uses INTEGER NOT NULL DEFAULT 0, revoked INTEGER NOT NULL DEFAULT 0, last_doc_id INTEGER, used_at BIGINT)")


# ------------------------------------------------------------------ utilitare
def _now() -> int:
    return int(time.time())


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _ensure_table() -> None:
    if cim.active_engine() == "postgres":
        return          # PostgreSQL (sursa principală): tabela vine din migrarea 006
    if cimitir_config.mode() == "online":
        # online, sursa de adevăr este PostgreSQL (Supabase); SQLite nu e acceptat ca bază principală
        raise HTTPException(status_code=503, detail="Serverul online cere PostgreSQL (CIMITIR_DB_ENGINE=postgres + DATABASE_URL).")
    conn = cim.get_connection()
    try:
        conn.execute(_DDL)
    finally:
        conn.close()


def _q(sql: str, params: tuple = (), one: bool = False):
    conn = cim.get_connection()
    try:
        cur = conn.execute(sql, params)
        if one:
            r = cur.fetchone()
            return dict(r) if r else None
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _exec(sql: str, params: tuple = ()) -> int:
    conn = cim.get_connection()
    try:
        return int(conn.execute(sql, params).rowcount or 0)
    finally:
        conn.close()


def _is_private_host(host: str) -> bool:
    h = (host or "").strip().lower().strip("[]")
    if not h or h == "localhost" or h.endswith((".local", ".localhost", ".internal", ".lan")):
        return True
    try:
        ip = ipaddress.ip_address(h)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_reserved
    except ValueError:
        return "." not in h          # nume fără punct (ex. „server”) = rețea locală


def public_base(request: Request) -> Dict[str, Any]:
    """{url, lan}. Online: DOAR CIMITIR_PUBLIC_URL (https, public). Local: același URL dacă există, altfel LAN din cerere (fallback)."""
    cimitir_config.load_env()
    configured = (os.environ.get("CIMITIR_PUBLIC_URL") or cim.get_setting("public_base_url") or "").strip().rstrip("/")
    online = cimitir_config.mode() == "online"
    if configured:
        p = urlparse(configured)
        if online and (p.scheme != "https" or _is_private_host(p.hostname or "")):
            raise HTTPException(status_code=503, detail="CIMITIR_PUBLIC_URL trebuie să fie o adresă publică https (nu localhost / IP privat).")
        if p.scheme in ("http", "https") and p.hostname:
            return {"url": configured, "lan": _is_private_host(p.hostname or "")}
    if online:
        raise HTTPException(status_code=503, detail="CIMITIR_PUBLIC_URL nu este configurat pe server; codul QR nu poate fi generat.")
    return {"url": str(request.base_url).rstrip("/"), "lan": True}          # fallback LAN (mod local)


def _qr_png_b64(url: str) -> str:
    try:
        import qrcode
        buf = io.BytesIO()
        qrcode.make(url, box_size=8, border=2).save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:  # noqa: BLE001
        return ""


def uid_of(etype: str, eid: str) -> str:
    """Același format de UID ca în media_service.entity_uid_for (dar și pentru SQLite)."""
    try:
        if etype == "loc":
            r = _q(f"SELECT uid FROM locuri_de_veci WHERE cod_loc = ? AND {cim.tenant_scope()}", (str(eid),), one=True)
            return (r or {}).get("uid") or f"loc:{eid}"
        if etype in _ENTITY_TABLES:
            r = _q(f"SELECT uid FROM {_ENTITY_TABLES[etype]} WHERE id = ? AND {cim.tenant_scope()}", (int(eid),), one=True)
            return (r or {}).get("uid") or f"{etype}:{eid}"
        if etype == "aviz":
            r = _q(f"SELECT a.nr_aviz, l.uid FROM amenajari_avize a LEFT JOIN locuri_de_veci l ON l.cod_loc = a.cod_loc AND {cim.tenant_scope('l')} "
                   f"WHERE a.id = ? AND {cim.tenant_scope('a')}", (int(eid),), one=True)
            return f"aviz:{r['uid'] or ''}:{r['nr_aviz']}" if r else f"aviz:{eid}"
    except Exception:  # noqa: BLE001
        pass
    return f"{etype}:{eid}"


def ref_from_uid(etype: str, uid: str) -> Optional[str]:
    """UID de entitate (cel al MAIN) -> id-ul entității pe server. None dacă serverul nu o are (încă nesincronizată)."""
    uid = (uid or "").strip()
    if not uid:
        return None
    try:
        if etype == "loc":
            r = _q(f"SELECT cod_loc FROM locuri_de_veci WHERE uid = ? AND {cim.tenant_scope()}", (uid,), one=True)
            return r["cod_loc"] if r else None
        if etype in _ENTITY_TABLES:
            r = _q(f"SELECT id FROM {_ENTITY_TABLES[etype]} WHERE uid = ? AND {cim.tenant_scope()}", (uid,), one=True)
            return str(r["id"]) if r else None
        if etype == "aviz" and uid.startswith("aviz:"):
            _, loc_uid, nr = uid.split(":", 2)
            r = _q(f"SELECT a.id FROM amenajari_avize a JOIN locuri_de_veci l ON l.cod_loc = a.cod_loc AND {cim.tenant_scope('l')} "
                   f"WHERE l.uid = ? AND a.nr_aviz = ? AND {cim.tenant_scope('a')}", (loc_uid, nr), one=True)
            return str(r["id"]) if r else None
    except Exception:  # noqa: BLE001
        return None
    return None


# ------------------------------------------------------------------ emitere
def issue(request: Request, source_id: str, etype: str, eid: str, issued_by: str, ttl_minutes: Any = None,
          max_uses: Any = None) -> Dict[str, Any]:
    """Creează tokenul pentru O entitate. `eid` = id-ul entității pe server (deja rezolvat)."""
    _ensure_table()
    ent = attachments.resolve_entity(cim, etype, eid)
    if not ent:
        raise HTTPException(status_code=404, detail="Entitatea nu există pe server (sincronizează întâi datele din MAIN).")
    base = public_base(request)
    try:
        ttl = int(ttl_minutes if ttl_minutes not in (None, "") else TTL_DEFAULT)
        uses = int(max_uses if max_uses not in (None, "") else 1)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="ttl_minutes / max_uses invalide")
    ttl = max(TTL_MIN, min(TTL_MAX, ttl))
    uses = max(1, min(MAX_USES_LIMIT, uses))
    secret = secrets.token_urlsafe(32)
    now = _now()
    h = _hash(secret)
    tid = h[:16]
    _exec("INSERT INTO mobile_upload_tokens (token_hash, tid, entity_type, entity_id, entity_uid, created_by, created_at, expires_at, "
          "max_uses, uses, revoked) VALUES (?,?,?,?,?,?,?,?,?,0,0)",
          (h, tid, etype, str(eid), uid_of(etype, str(eid)), (issued_by or "main")[:80], now, now + ttl * 60, uses))
    url = f"{base['url']}/api/m/{source_id}.{secret}"
    cimitir_requests.audit("staff", issued_by or "main", "mobile.token.issue", f"{etype}:{eid}",
                           json.dumps({"tid": tid, "ttl_min": ttl, "max_uses": uses, "lan": base["lan"]}))
    return {"ok": True, "tid": tid, "url": url, "expires_at": now + ttl * 60, "expires_in": ttl * 60, "max_uses": uses,
            "lan": base["lan"], "entity": {**ent, "uid": uid_of(etype, str(eid))}, "qr_png": _qr_png_b64(url)}


@sync_router.post("/tokens")
def issue_for_main(request: Request, payload: Dict[str, Any] = Body(...)):
    source_id = sync_api._auth(request)
    etype = str(payload.get("entity_type") or "").strip()
    if etype not in attachments.ENTITIES or etype == "cerere":
        raise HTTPException(status_code=422, detail="entity_type invalid (loc, concesionar, contract, inhumat, plata, aviz)")
    if payload.get("entity_uid"):
        eid = ref_from_uid(etype, str(payload["entity_uid"]))
        if not eid:
            raise HTTPException(status_code=404, detail="Entitatea nu există pe server (rulează sincronizarea în MAIN).")
    elif payload.get("entity_id") not in (None, ""):
        eid = str(payload["entity_id"])
    else:
        raise HTTPException(status_code=422, detail="entity_uid lipsește")
    return JSONResponse(issue(request, source_id, etype, eid, str(payload.get("issued_by") or "main"),
                              payload.get("ttl_minutes"), payload.get("max_uses")), headers=_NOSTORE)


def _own_token(request: Request, tid: str) -> Dict[str, Any]:
    sync_api._auth(request)
    _ensure_table()
    row = _q("SELECT * FROM mobile_upload_tokens WHERE tid = ?", (tid[:16],), one=True)
    if not row:
        raise HTTPException(status_code=404, detail="Token necunoscut")
    return row


def _state(row: Dict[str, Any]) -> str:
    if row["revoked"]:
        return "revoked"
    if row["uses"] >= row["max_uses"]:
        return "used"
    if row["expires_at"] <= _now():
        return "expired"
    return "active"


@sync_router.get("/tokens/{tid}")
def token_status(request: Request, tid: str):
    row = _own_token(request, tid)
    docs = [d for d in attachments.list_attachments(cim, row["entity_type"], row["entity_id"]) if d.get("source") == "mobile"]
    return JSONResponse({"ok": True, "tid": row["tid"], "state": _state(row), "uses": row["uses"], "max_uses": row["max_uses"],
                         "expires_at": row["expires_at"], "entity_type": row["entity_type"], "entity_uid": row["entity_uid"],
                         "last_doc_id": row["last_doc_id"],
                         "documents": [{"id": d["id"], "filename": d["filename"], "doc_type": d["doc_type"], "size": d["size"],
                                        "date_added": d["date_added"]} for d in docs]}, headers=_NOSTORE)


@sync_router.delete("/tokens/{tid}")
def revoke(request: Request, tid: str):
    row = _own_token(request, tid)
    _exec("UPDATE mobile_upload_tokens SET revoked = 1 WHERE token_hash = ?", (row["token_hash"],))
    cimitir_requests.audit("staff", "main", "mobile.token.revoke", f"{row['entity_type']}:{row['entity_id']}", json.dumps({"tid": row["tid"]}))
    return {"ok": True}


# ------------------------------------------------------------------ telefon (autentificare = tokenul)
def _client(request: Request) -> str:
    return "mobile:" + portal_ratelimit.client_ip(request)


def _fail(request: Request, status: int, detail: str, code: str):
    """Răspuns generic + numărare eșecuri per IP (după 5 eșecuri: 429)."""
    cimitir_auth.record_failure(_client(request))
    raise HTTPException(status_code=status, detail={"error": code, "message": detail})


def _load(request: Request, token: str) -> Dict[str, Any]:
    wait = cimitir_auth.lock_remaining(_client(request))
    if wait:
        raise portal_ratelimit.reject(wait, "Prea multe încercări. Reîncearcă peste câteva minute.")
    source_id, _, secret = (token or "").partition(".")
    src = cim.get_source(source_id) if source_id else None
    if not src or src.get("engine") not in ("sqlite", "postgres") or len(secret) < 20 or len(secret) > 80:
        _fail(request, 404, "Link invalid.", "invalid")
    cim.bind_source(src)
    _ensure_table()
    row = _q("SELECT * FROM mobile_upload_tokens WHERE token_hash = ?", (_hash(secret),), one=True)
    if not row:
        _fail(request, 404, "Link invalid.", "invalid")
    cimitir_auth.clear_failures(_client(request))          # token valid => ștergem eșecurile anterioare ale acestui IP
    row["source_id"] = source_id
    return row


def _check_active(request: Request, row: Dict[str, Any]) -> None:
    st = _state(row)
    if st == "active":
        return
    cimitir_requests.audit("mobile", f"mobile:{row['created_by']}", "mobile.reject", f"{row['entity_type']}:{row['entity_id']}",
                           json.dumps({"tid": row["tid"], "reason": st}))
    msgs = {"expired": "Linkul a expirat. Generează un cod QR nou din MAIN.", "used": "Linkul a fost deja folosit. Generează un cod QR nou din MAIN.",
            "revoked": "Linkul a fost anulat."}
    raise HTTPException(status_code=410, detail={"error": st, "message": msgs[st]})


def _info(row: Dict[str, Any]) -> Dict[str, Any]:
    ent = attachments.resolve_entity(cim, row["entity_type"], row["entity_id"])
    if not ent:
        raise HTTPException(status_code=404, detail={"error": "entity_missing", "message": "Entitatea nu mai există."})
    sug = attachments.SUGGESTED.get(row["entity_type"], ["alt_document"])
    types = [t for t in sug if t != "carte_identitate"]            # copia CI cere motiv scris + MAIN; nu din telefon
    n = len(attachments.list_attachments(cim, row["entity_type"], row["entity_id"]))
    return {"ok": True, "entity": {"type": ent["type"], "label": ent["label"], "title": ent["title"]},
            "expires_in": max(0, row["expires_at"] - _now()), "remaining_uses": max(0, row["max_uses"] - row["uses"]),
            "doc_types": [{"value": t, "label": attachments.DOC_TYPES.get(t, t)} for t in types],
            "existing_count": n, "cimitir": cim.get_setting("cimitir_nume") or "Cimitir",
            "max_mb": attachments.MAX_BYTES // (1024 * 1024)}


@public_router.get("/{token}/info")
def info(request: Request, token: str):
    row = _load(request, token)
    _check_active(request, row)
    return JSONResponse(_info(row), headers=_NOSTORE)


@public_router.get("/{token}", response_class=HTMLResponse)
def page(request: Request, token: str):
    row = _load(request, token)
    _check_active(request, row)
    data = _info(row)
    html = _PAGE.replace("__TOKEN__", json.dumps(token)).replace("__INFO__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    return HTMLResponse(html, headers={**_NOSTORE, "Content-Security-Policy":
                                       "default-src 'none'; img-src 'self' data: blob:; style-src 'unsafe-inline'; "
                                       "script-src 'unsafe-inline'; connect-src 'self'; form-action 'none'; base-uri 'none'"})


@public_router.post("/{token}/upload")
def upload(request: Request, token: str, file: UploadFile = File(...), doc_type: str = Form("alt_document"),
           description: str = Form(""), entity_type: str = Form(""), entity_id: str = Form(""), entity_uid: str = Form("")):
    row = _load(request, token)
    _check_active(request, row)
    etype, eid = row["entity_type"], row["entity_id"]
    # tokenul e legat de o singură entitate: orice altă țintă cerută de client => respins
    if (entity_type and entity_type != etype) or (entity_id and str(entity_id) != str(eid)) or \
            (entity_uid and row.get("entity_uid") and entity_uid != row["entity_uid"]):
        cimitir_requests.audit("mobile", f"mobile:{row['created_by']}", "mobile.reject", f"{etype}:{eid}",
                               json.dumps({"tid": row["tid"], "reason": "entity_mismatch", "asked": f"{entity_type}:{entity_id}"[:60]}))
        raise HTTPException(status_code=403, detail={"error": "entity_mismatch", "message": "Acest link este valabil doar pentru entitatea pentru care a fost generat."})
    data = file.file.read(attachments.MAX_BYTES + 1)
    try:
        name, _mime = attachments.validate_file(file.filename or "", data)
    except attachments.AttachmentError as exc:
        raise HTTPException(status_code=422, detail={"error": "invalid_file", "message": str(exc)})
    if doc_type == "carte_identitate":
        doc_type = "alt_document"
    # rezervare ATOMICĂ a unei utilizări (single-use chiar și la cereri concurente)
    reserved = _exec("UPDATE mobile_upload_tokens SET uses = uses + 1, used_at = ? WHERE token_hash = ? AND revoked = 0 "
                     "AND uses < max_uses AND expires_at > ?", (_now(), row["token_hash"], _now()))
    if reserved != 1:
        _check_active(request, _q("SELECT * FROM mobile_upload_tokens WHERE token_hash = ?", (row["token_hash"],), one=True))
        raise HTTPException(status_code=410, detail={"error": "used", "message": "Linkul nu mai este valabil."})
    actor = f"mobile:{row['created_by']}"
    try:
        info_ = attachments.add_attachment(cim, etype, eid, name, data, doc_type, "", (description or "")[:1000], actor, source="mobile")
    except attachments.AttachmentError as exc:
        _exec("UPDATE mobile_upload_tokens SET uses = uses - 1 WHERE token_hash = ? AND uses > 0", (row["token_hash"],))
        raise HTTPException(status_code=422, detail={"error": "rejected", "message": str(exc)})
    except Exception:  # noqa: BLE001
        _exec("UPDATE mobile_upload_tokens SET uses = uses - 1 WHERE token_hash = ? AND uses > 0", (row["token_hash"],))
        raise HTTPException(status_code=503, detail={"error": "storage", "message": "Salvarea a eșuat. Încearcă din nou."})
    _exec("UPDATE mobile_upload_tokens SET last_doc_id = ? WHERE token_hash = ?", (info_["id"], row["token_hash"]))
    _tag_media(info_["id"], actor)
    cimitir_requests.audit("mobile", actor, "attachment.add", f"{etype}:{eid}",
                           json.dumps({"source": "mobile", "tid": row["tid"], **json.loads(attachments.audit_detail(info_))}))
    left = max(0, row["max_uses"] - row["uses"] - 1)
    return JSONResponse({"ok": True, "document": {"id": info_["id"], "filename": info_["filename"], "size": info_["size"],
                                                  "doc_type": info_["doc_type"]}, "remaining_uses": left}, headers=_NOSTORE)


def _tag_media(doc_id: int, actor: str) -> None:
    """PostgreSQL: marchează în media_objects cine a încărcat (nu schimbă UID-ul / calea / conținutul)."""
    try:
        r = _q("SELECT media_id FROM documents WHERE id = ?", (doc_id,), one=True)
        if r and r.get("media_id"):
            _exec("UPDATE media_objects SET uploaded_by = ? WHERE id = ?", (actor[:120], r["media_id"]))
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ pagina mobilă
_PAGE = r"""<!doctype html><html lang="ro"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>Încarcă document</title>
<style>
*{box-sizing:border-box}body{margin:0;font:16px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#0f1f17;color:#eef3ef;min-height:100vh}
main{max-width:520px;margin:0 auto;padding:18px 16px 40px}h1{font-size:20px;margin:6px 0 2px}small,.muted{color:#9fb3a7}
.card{background:#16291f;border:1px solid #2a4033;border-radius:14px;padding:14px;margin:12px 0}.lbl{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:#c9a35a}
.title{font-size:20px;font-weight:700;margin-top:2px;word-break:break-word}select,textarea{width:100%;padding:12px;border-radius:10px;border:1px solid #35503f;background:#0f1f17;color:#eef3ef;font-size:16px}
button{width:100%;min-height:48px;border-radius:12px;border:0;font-size:16px;font-weight:700;margin-top:10px;cursor:pointer}
.b1{background:#c9a35a;color:#1a1408}.b2{background:transparent;border:1px solid #c9a35a;color:#e7c98a}.b1:disabled{opacity:.5}
.ok{background:#12351f;border-color:#2f7a4a}.err{background:#3a1717;border-color:#8a3030}.bar{height:8px;background:#0f1f17;border-radius:6px;overflow:hidden;margin-top:10px}
.bar i{display:block;height:100%;width:0;background:#c9a35a;transition:width .2s}.hide{display:none}.timer{float:right;font-variant-numeric:tabular-nums}
</style></head><body><main>
<small id="cim"></small><h1>Încarcă document</h1>
<div class="card"><span class="lbl" id="elabel"></span><span class="timer muted" id="timer"></span><div class="title" id="etitle"></div>
<small class="muted" id="ecount"></small></div>
<div id="form"><div class="card"><label class="lbl" for="dt">Tip document</label><select id="dt"></select>
<label class="lbl" for="desc" style="display:block;margin-top:12px">Descriere (opțional)</label><textarea id="desc" rows="2" maxlength="1000"></textarea>
<button class="b2" id="bcam" type="button">Fă fotografie</button><button class="b2" id="bfile" type="button">Alege PDF / imagine</button>
<input id="icam" type="file" accept="image/*" capture="environment" class="hide"><input id="ifile" type="file" accept="application/pdf,image/jpeg,image/png" class="hide">
<div id="picked" class="muted" style="margin-top:10px"></div><div class="bar hide" id="bar"><i id="bari"></i></div>
<button class="b1" id="go" type="button" disabled>Încarcă</button></div></div>
<div class="card hide" id="msg"></div></main>
<script>
var TOKEN=__TOKEN__,INFO=__INFO__,file=null,left=INFO.expires_in,busy=false;
function $(i){return document.getElementById(i)}
$('cim').textContent=INFO.cimitir;$('elabel').textContent=INFO.entity.label;$('etitle').textContent=INFO.entity.title||'';
$('ecount').textContent=INFO.existing_count+' documente atașate până acum';
INFO.doc_types.forEach(function(t){var o=document.createElement('option');o.value=t.value;o.textContent=t.label;$('dt').appendChild(o)});
function show(kind,text){var m=$('msg');m.className='card '+kind;m.textContent=text;m.classList.remove('hide')}
function tick(){if(left<=0){$('form').classList.add('hide');$('timer').textContent='expirat';show('err','Linkul a expirat. Generează un cod QR nou din MAIN.');return}
var mm=Math.floor(left/60),ss=left%60;$('timer').textContent=mm+':'+(ss<10?'0':'')+ss;left--;setTimeout(tick,1000)}tick();
function pick(f){if(!f)return;file=f;$('picked').textContent=f.name+' ('+Math.max(1,Math.round(f.size/1024))+' KB)';$('go').disabled=false}
$('bcam').onclick=function(){$('icam').click()};$('bfile').onclick=function(){$('ifile').click()};
$('icam').onchange=function(e){pick(e.target.files[0])};$('ifile').onchange=function(e){pick(e.target.files[0])};
function shrink(f,cb){if(!/^image\/(jpeg|png)/.test(f.type)||f.size<1500000){return cb(f)}
var img=new Image(),u=URL.createObjectURL(f);img.onload=function(){var s=Math.min(1,2400/Math.max(img.width,img.height)),c=document.createElement('canvas');
c.width=Math.round(img.width*s);c.height=Math.round(img.height*s);c.getContext('2d').drawImage(img,0,0,c.width,c.height);
c.toBlob(function(b){URL.revokeObjectURL(u);cb(b?new File([b],(f.name||'foto').replace(/\.[^.]+$/,'')+'.jpg',{type:'image/jpeg'}):f)},'image/jpeg',0.85)};img.onerror=function(){cb(f)};img.src=u}
$('go').onclick=function(){if(!file||busy)return;busy=true;$('go').disabled=true;$('msg').classList.add('hide');
shrink(file,function(f){var fd=new FormData();fd.append('file',f,f.name);fd.append('doc_type',$('dt').value);fd.append('description',$('desc').value);
var x=new XMLHttpRequest();x.open('POST','/api/m/'+encodeURIComponent(TOKEN)+'/upload');$('bar').classList.remove('hide');
x.upload.onprogress=function(e){if(e.lengthComputable)$('bari').style.width=Math.round(e.loaded/e.total*100)+'%'};
x.onload=function(){busy=false;var r={};try{r=JSON.parse(x.responseText)}catch(_){}
if(x.status===200){left=0;$('form').classList.add('hide');$('timer').textContent='';show('ok','Document încărcat: '+r.document.filename+'. Îl vei vedea în MAIN după sincronizare. Poți închide pagina.')}
else{var d=(r.detail&&r.detail.message)||r.detail||('Eroare '+x.status);$('go').disabled=false;show('err',typeof d==='string'?d:'Încărcarea a eșuat.')}};
x.onerror=function(){busy=false;$('go').disabled=false;show('err','Fără conexiune. Verifică internetul și încearcă din nou.')};x.send(fd)})};
</script></body></html>"""
