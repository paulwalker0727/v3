"""Arhivă generală de atașamente (fotografii JPEG/PNG și PDF) pentru orice entitate importantă.

Reutilizează tabela `documents` (entity_type + entity_id + doc_type) și pipeline-ul de Storage existent
(`add_document` -> media_service -> Supabase/S3/local). Nu există un al doilea sistem de fișiere.
Telefonul/browserul vorbesc doar cu FastAPI; nu primesc chei de Storage.
"""
from __future__ import annotations

import json
import mimetypes
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional, Tuple

# entity_type -> (etichetă, tabel, coloană id, coloane pentru titlu)
ENTITIES: Dict[str, Tuple[str, str, str, Tuple[str, ...]]] = {
    "loc": ("Loc de veci", "locuri_de_veci", "cod_loc", ("cod_loc",)),
    "concesionar": ("Concesionar", "concesionari", "id", ("nume", "prenume")),
    "contract": ("Contract de concesiune", "contracte", "id", ("numar_contract",)),
    "inhumat": ("Înhumat", "inhumati", "id", ("nume", "prenume")),
    "plata": ("Plată", "plati", "id", ("chitanta_nr",)),
    "aviz": ("Aviz construcție / amenajare", "amenajari_avize", "id", ("nr_aviz",)),
    "cerere": ("Cerere din portal", "cereri_concesionare", "id", ("cod_urmarire",)),
}

DOC_TYPES: Dict[str, str] = {
    "contract_concesiune": "Contract concesiune", "act_aditional": "Act adițional", "chitanta": "Chitanță",
    "dovada_plata": "Dovadă plată", "certificat": "Certificat", "cerere": "Cerere", "aviz_constructie": "Aviz construcție",
    "schita": "Schiță", "carte_identitate": "Carte de identitate", "declaratie": "Declarație",
    "fotografie_loc": "Fotografie loc", "fotografie_monument": "Fotografie monument", "proces_verbal": "Proces verbal",
    "poza_loc": "Fotografie loc (vechi)", "cerere_doc": "Document încărcat de solicitant", "alt_document": "Alt document",
}
# tipurile propuse primele, pe entitate (restul rămân disponibile)
SUGGESTED: Dict[str, List[str]] = {
    "loc": ["fotografie_loc", "fotografie_monument", "schita", "alt_document"],
    "concesionar": ["carte_identitate", "cerere", "declaratie", "alt_document"],
    "contract": ["contract_concesiune", "act_aditional", "alt_document"],
    "inhumat": ["certificat", "proces_verbal", "fotografie_loc", "alt_document"],
    "plata": ["chitanta", "dovada_plata", "alt_document"],
    "aviz": ["cerere", "aviz_constructie", "schita", "fotografie_loc", "alt_document"],
    "cerere": ["cerere", "carte_identitate", "declaratie", "alt_document"],
}
# copia actului de identitate: doar unde e justificat și cu motiv scris
ID_CARD_ENTITIES = {"concesionar", "cerere", "inhumat"}

MAX_BYTES = int(os.environ.get("CIMITIR_MEDIA_MAX_BYTES", str(40 * 1024 * 1024)))
_MAGIC = {".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",), ".png": (b"\x89PNG\r\n\x1a\n",), ".pdf": (b"%PDF-",)}
_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".pdf": "application/pdf"}


class AttachmentError(ValueError):
    pass


def ensure_columns(cim) -> None:
    """PostgreSQL: coloanele vin din migrația 005. SQLite (mod local): le adăugăm dacă lipsesc (verificat per bază, ieftin)."""
    if getattr(cim, "ENGINE", "") == "postgres":
        return
    conn = cim.get_connection()
    try:
        try:
            conn.execute("SELECT description, original_filename, mime_type, uploaded_by, source FROM documents LIMIT 0")
            return
        except Exception:
            pass
        for col in ("description", "original_filename", "mime_type", "uploaded_by", "source"):
            try:
                conn.execute(f"SELECT {col} FROM documents LIMIT 0")
            except Exception:
                try:
                    conn.execute(f"ALTER TABLE documents ADD COLUMN {col} TEXT")
                except Exception:
                    pass
    finally:
        conn.close()


def safe_name(name: str) -> str:
    base = os.path.basename((name or "").replace("\\", "/"))
    out = "".join(ch if (ch.isalnum() or ch in "._- ") else "_" for ch in base).strip(" .")[:90]
    return out or "document"


def validate_file(filename: str, data: bytes) -> Tuple[str, str]:
    """(nume curățat, mime). Acceptă doar JPEG, PNG, PDF; verifică extensia ȘI semnătura."""
    name = safe_name(filename)
    ext = os.path.splitext(name)[1].lower()
    if ext not in _MAGIC:
        raise AttachmentError("Tip de fișier neacceptat (se acceptă JPEG, PNG sau PDF).")
    if not data:
        raise AttachmentError("Fișierul este gol.")
    if len(data) > MAX_BYTES:
        raise AttachmentError(f"Fișier prea mare (maxim {MAX_BYTES // (1024 * 1024)} MB).")
    if not any(data.startswith(m) for m in _MAGIC[ext]):
        raise AttachmentError("Conținutul fișierului nu corespunde extensiei.")
    return name, _MIME[ext]


def _one(cim, sql: str, params: tuple):
    conn = cim.get_connection()
    try:
        r = conn.execute(sql, params).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def resolve_entity(cim, etype: str, eid: str) -> Optional[Dict[str, str]]:
    """None dacă tipul/entitatea nu există; altfel {type, id, label, title}."""
    spec = ENTITIES.get(etype)
    if not spec:
        return None
    label, table, col, title_cols = spec
    key: Any = str(eid)
    if col == "id":
        try:
            key = int(eid)
        except (TypeError, ValueError):
            return None
    try:
        row = _one(cim, f"SELECT * FROM {table} WHERE {col} = ?", (key,))
    except Exception:
        return None
    if not row:
        return None
    title = " ".join(str(row.get(c) or "") for c in title_cols).strip() or f"#{eid}"
    return {"type": etype, "id": str(eid), "label": label, "title": title}


def _fmt_size(n: Any) -> str:
    try:
        n = float(n or 0)
    except Exception:
        return ""
    return f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / (1024 * 1024):.1f} MB"


def list_attachments(cim, etype: str, eid: str) -> List[Dict[str, Any]]:
    ensure_columns(cim)
    conn = cim.get_connection()
    try:
        rows = [dict(r) for r in conn.execute(
            f"SELECT * FROM documents WHERE entity_type = ? AND entity_id = ? AND {cim.tenant_scope()} ORDER BY id DESC", (etype, str(eid))).fetchall()]
    finally:
        conn.close()
    out = []
    for r in rows:
        fname = r.get("original_filename") or os.path.basename(r.get("file_path") or "") or "document"
        ext = os.path.splitext(fname)[1].lower()
        mime = r.get("mime_type") or _MIME.get(ext) or mimetypes.guess_type(fname)[0] or "application/octet-stream"
        dt = r.get("doc_type") or "alt_document"
        out.append({"id": r["id"], "doc_type": dt, "doc_label": DOC_TYPES.get(dt, dt.replace("_", " ").capitalize()),
                    "title": r.get("caption") or fname, "description": r.get("description") or "", "filename": fname,
                    "mime": mime, "is_image": mime.startswith("image/"), "is_pdf": mime == "application/pdf",
                    "size": _fmt_size(r.get("size")), "uploaded_by": r.get("uploaded_by") or "",
                    "date_added": r.get("date_added") or "", "source": r.get("source") or ""})
    return out


def counts(cim, etype: str) -> Dict[str, int]:
    """entity_id -> număr atașamente (pentru insigne în liste)."""
    conn = cim.get_connection()
    try:
        return {str(r[0]): int(r[1]) for r in conn.execute(
            f"SELECT entity_id, COUNT(*) FROM documents WHERE entity_type = ? AND {cim.tenant_scope()} GROUP BY entity_id", (etype,)).fetchall()}
    except Exception:
        return {}
    finally:
        conn.close()


def add_attachment(cim, etype: str, eid: str, filename: str, data: bytes, doc_type: str, title: str, description: str,
                   actor: str, source: str = "web") -> Dict[str, Any]:
    ensure_columns(cim)
    ent = resolve_entity(cim, etype, eid)
    if not ent:
        raise AttachmentError("Înregistrarea nu există.")
    doc_type = doc_type if doc_type in DOC_TYPES else "alt_document"
    if doc_type == "carte_identitate":
        if etype not in ID_CARD_ENTITIES:
            raise AttachmentError("Copia actului de identitate se poate atașa doar la concesionar, înhumat sau cerere.")
        if len((description or "").strip()) < 5:
            raise AttachmentError("Pentru copia actului de identitate completați motivul în descriere (minim 5 caractere).")
    name, mime = validate_file(filename, data)
    import hashlib
    sha = hashlib.sha256(data).hexdigest()
    tmpdir = tempfile.mkdtemp(prefix="att_")
    try:
        path = os.path.join(tmpdir, name)
        with open(path, "wb") as f:
            f.write(data)
        did = cim.add_document(etype, str(eid), doc_type, path, caption=(title or name)[:200])
        if not did:
            raise AttachmentError("Fișierul nu a putut fi salvat.")
        if getattr(cim, "ENGINE", "") == "postgres":
            conn = cim.get_connection()
            try:
                r = conn.execute("SELECT media_id FROM documents WHERE id = ?", (did,)).fetchone()
            finally:
                conn.close()
            if not (r and r["media_id"]):
                cim.delete_document(did)      # nu lăsăm documente doar pe discul serverului dacă Storage e indisponibil
                raise AttachmentError("Storage indisponibil; fișierul nu a fost salvat. Încercați din nou.")
        conn = cim.get_connection()
        try:
            conn.execute("UPDATE documents SET description = ?, original_filename = ?, mime_type = ?, uploaded_by = ?, source = ?, "
                         "sha256 = ?, size = ? WHERE id = ?", ((description or "").strip()[:1000], name, mime, actor, source, sha, len(data), did))
        finally:
            conn.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return {"id": did, "doc_type": doc_type, "filename": name, "mime": mime, "size": len(data), "sha256": sha, "entity": ent}


def get_attachment(cim, doc_id: int) -> Optional[Dict[str, Any]]:
    ensure_columns(cim)
    row = _one(cim, "SELECT * FROM documents WHERE id = ?", (doc_id,))
    if not row:
        return None
    fname = row.get("original_filename") or os.path.basename(row.get("file_path") or "") or "document"
    ext = os.path.splitext(fname)[1].lower()
    row["filename"] = fname
    row["mime"] = row.get("mime_type") or _MIME.get(ext) or mimetypes.guess_type(fname)[0] or "application/octet-stream"
    return row


def audit_detail(info: Dict[str, Any]) -> str:
    """Doar metadate (fără conținut / căi de storage / secrete)."""
    return json.dumps({k: info.get(k) for k in ("id", "doc_type", "filename", "mime", "size", "sha256") if info.get(k) is not None},
                      ensure_ascii=False)
