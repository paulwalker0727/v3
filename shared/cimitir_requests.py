"""Cereri: aprobare tranzacțională (MAIN), validare încărcări, drept asupra locului, jurnal, retenție."""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import cimitir_db_router as db
import cimitir_public

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_DOCS_PER_REQUEST = 10
_MAGIC = {".pdf": (b"%PDF-",), ".png": (b"\x89PNG\r\n\x1a\n",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",)}
REQUEST_TYPES = ("concesionare", "actualizare_inhumat")
STATUSES = ("nou", "completari", "validat", "respins")
DEFAULT_PRIVACY_TEXT = (
    "ȘABLON — de revizuit și aprobat de operator/juriști înainte de publicare. Datele din cerere (identificare, contact, "
    "documente) sunt folosite pentru soluționarea cererii de concesionare/actualizare a evidenței. Sunt transmise către "
    "evidența oficială a cimitirului și șterse de pe serverul online după preluare; starea cererii rămâne vizibilă în "
    "contul dumneavoastră. Termenele de păstrare și drepturile dumneavoastră se stabilesc de operator.")


class ApprovalError(Exception):
    pass


def audit(actor_type: str, actor: str, action: str, target: str = "", detail: str = "") -> None:
    conn = db.get_connection()
    try:
        conn.execute("INSERT INTO audit_log (ts, actor_type, actor, action, target, detail) VALUES (?,?,?,?,?,?)",
                     (datetime.now().isoformat(timespec="seconds"), actor_type, str(actor)[:80], action, str(target)[:120], str(detail)[:300]))
    finally:
        conn.close()


def recent_audit(limit: int = 100) -> List[Dict[str, Any]]:
    conn = db.get_connection()
    try:
        return [dict(r) for r in conn.execute(f"SELECT * FROM audit_log WHERE {db.tenant_scope(level='org')} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    finally:
        conn.close()


def safe_filename(name: str) -> str:
    base = os.path.basename((name or "").replace("\\", "/"))
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .")[:80]
    return base or "document"


def validate_upload(filename: str, data: bytes) -> str:
    """Numele curățat; ValueError dacă tipul (extensie + semnătură), dimensiunea sau conținutul nu sunt acceptate."""
    name = safe_filename(filename)
    ext = os.path.splitext(name)[1].lower()
    if ext not in _MAGIC:
        raise ValueError("Tip de fișier neacceptat (PDF, JPG, PNG).")
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("Fișier gol sau mai mare de 10 MB.")
    if not any(data.startswith(m) for m in _MAGIC[ext]):
        raise ValueError("Conținutul nu corespunde extensiei.")
    return name


def holder_hash(salt: str, cnp: str) -> str:
    return hashlib.sha256((salt + re.sub(r"\D", "", cnp or "")).encode()).hexdigest()


def build_holders(salt: str) -> List[Dict[str, str]]:
    """MAIN: titularii contractelor (hash cu sare a CNP), fără alte date. Contractele draft contează."""
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT c.cod_loc AS cod, COALESCE(NULLIF(c.owner_cnp,''), k.cnp) AS cnp FROM contracte c "
            f"LEFT JOIN concesionari k ON k.id=c.concesionar_id WHERE c.cod_loc<>'' AND COALESCE(c.valabilitate,'')<>'anulat' AND {db.tenant_scope('c')}").fetchall()
    finally:
        conn.close()
    out = {(r["cod"], holder_hash(salt, r["cnp"])) for r in rows if re.sub(r"\D", "", r["cnp"] or "")}
    return [{"cod_loc": c, "h": h} for c, h in sorted(out)]


def approve_cerere(cerere_id: int, concesionar_id: Optional[int] = None, actor: str = "MAIN") -> Dict[str, Any]:
    """Aprobare într-o singură tranzacție: re-verifică disponibilitatea, creează/leagă concesionarul și contractul
    draft, rezervă locul și marchează cererea. Idempotentă: a doua aprobare întoarce același rezultat."""
    conn = db.get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        ce = conn.execute("SELECT * FROM cereri_concesionare WHERE id=?", (cerere_id,)).fetchone()
        if not ce:
            raise ApprovalError("Cererea nu există.")
        if ce["status"] == "validat":
            conn.execute("ROLLBACK")
            return {"ok": True, "already": True, "concesionar_id": ce["concesionar_id"], "contract_id": ce["contract_id"]}
        if ce["status"] == "respins":
            raise ApprovalError("Cererea a fost respinsă; nu mai poate fi aprobată.")
        cod = (ce["cod_loc"] or "").strip()
        tip = ce["tip"] or "concesionare"
        loc = conn.execute(f"SELECT * FROM locuri_de_veci WHERE cod_loc=? AND {db.tenant_scope()}", (cod,)).fetchone() if cod else None
        if tip == "concesionare" and cod:
            if not loc:
                raise ApprovalError(f"Locul „{cod}” nu există în evidență.")
            if cimitir_public.availability(dict(loc)) != "disponibil":
                raise ApprovalError(f"Locul „{cod}” nu mai este disponibil (alt solicitant sau contract existent).")
            if conn.execute(f"SELECT 1 FROM contracte WHERE cod_loc=? AND COALESCE(valabilitate,'')<>'anulat' AND {db.tenant_scope()}", (cod,)).fetchone():
                raise ApprovalError(f"Locul „{cod}” are deja un contract.")
        cnp = (ce["cnp"] or "").strip()
        cid = concesionar_id
        if cid is None and cnp:
            row = conn.execute(f"SELECT id FROM concesionari WHERE cnp=? AND {db.tenant_scope()}", (cnp,)).fetchone()
            cid = row["id"] if row else None
        if cid is None:
            cur = conn.execute("INSERT INTO concesionari (nume, prenume, cnp, bi_ci, adresa, telefon, email, observatii) "
                               "VALUES (?,?,?,?,?,?,?,?)", tuple(ce[k] or "" for k in
                                                                 ("nume", "prenume", "cnp", "bi_ci", "adresa", "telefon", "email", "observatii")))
            cid = cur.lastrowid
        contract_id = None
        if tip == "concesionare" and loc:
            full = f"{ce['nume'] or ''} {ce['prenume'] or ''}".strip()
            cur = conn.execute(
                "INSERT INTO contracte (cod_loc, id_loc, concesionar_id, data_contract, valabilitate, observatii, owner_fullname, "
                "owner_cnp, owner_id_card_serie_number, owner_address, owner_phone, owner_email) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (cod, loc["id"], cid, datetime.now().strftime("%d.%m.%Y"), "draft",
                 f"Contract generat din cererea online #{cerere_id}.", full, ce["cnp"] or "", ce["bi_ci"] or "",
                 ce["adresa"] or "", ce["telefon"] or "", ce["email"] or ""))
            contract_id = cur.lastrowid
            conn.execute("UPDATE locuri_de_veci SET disponibilitate='Rezervat', stare='Rezervat', ocupat=0 WHERE id=?", (loc["id"],))
        elif tip == "actualizare_inhumat" and loc:
            p = json.loads(ce["payload"] or "{}")
            if p.get("nume_decedat"):
                conn.execute("INSERT INTO inhumati (id_loc, nume, prenume, data_deces, observatii) VALUES (?,?,?,?,?)",
                             (loc["id"], p.get("nume_decedat", ""), p.get("prenume_decedat", ""), p.get("data_deces", ""),
                              f"Din cererea online #{cerere_id} (de verificat)."))
        conn.execute("UPDATE cereri_concesionare SET status='validat', concesionar_id=?, contract_id=?, reviewed_at=?, closed_at=?, "
                     "review_note=? WHERE id=?", (cid, contract_id, datetime.now().strftime("%d.%m.%Y %H:%M"),
                                                  datetime.now().isoformat(timespec="seconds"),
                                                  f"Concesionar #{cid}" + (f", contract #{contract_id}" if contract_id else ""), cerere_id))
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()
    audit("staff", actor, "approve_request", str(cerere_id), f"concesionar={cid} contract={contract_id}")
    return {"ok": True, "already": False, "concesionar_id": cid, "contract_id": contract_id}


def purge_expired(days_closed: int, days_rejected: int, now: Optional[datetime] = None) -> int:
    """Server: șterge datele personale/documentele cererilor închise peste termenele de păstrare."""
    now = now or datetime.now()
    conn = db.get_connection()
    try:
        rows = conn.execute("SELECT id, status, closed_at FROM cereri_concesionare WHERE closed_at IS NOT NULL AND closed_at<>''").fetchall()
    finally:
        conn.close()
    n = 0
    for r in rows:
        limit = days_rejected if r["status"] == "respins" else days_closed
        try:
            closed = datetime.fromisoformat(r["closed_at"])
        except ValueError:
            continue
        if now - closed < timedelta(days=limit):
            continue
        for d in db.list_documents("cerere", r["id"]):
            path = db.get_document_path(d["id"])
            db.delete_document(d["id"])
            if path and os.path.exists(path):
                os.remove(path)
        conn = db.get_connection()
        try:
            sets = ", ".join(f"{k}=''" for k in ("nume", "prenume", "cnp", "bi_ci", "adresa", "telefon", "email", "grad_rudenie",
                                                 "observatii", "payload", "public_message"))
            conn.execute(f"UPDATE cereri_concesionare SET {sets}, citizen_id=NULL, closed_at='' WHERE id=?", (r["id"],))
        finally:
            conn.close()
        n += 1
    if n:
        audit("system", "retention", "purge", "", f"{n} cereri")
    return n
