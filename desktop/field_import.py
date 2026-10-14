"""field_import.py — import LOCAL (explicit, la cererea operatorului) al elementelor Field Inbox ACCEPTED în registrul SQLite activ al MAIN. Fără Qt.

Flux (idempotent, crash-safe; jurnal local `field_import_journal`, o singură înregistrare per `inbox_item_uid`):
  1. item ACCEPTED în cloud + cimitirul itemului == cimitirul registrului activ (altfel: blocat, fără comutare automată de registru)
  2. plan local (grave/țintă existente, mapare pe modelele reale: inhumati / amenajari_avize / documents) — fără scriere
  3. jurnal PREPARING -> descărcare prin URL semnat în staging local (cache-ul registrului) -> mărime + SHA-256 + semnătură -> fișier gata
  4. o singură tranzacție SQLite (BEGIN IMMEDIATE): mută fișierele (os.replace, nume determinist, fără suprascriere cu alt SHA), inserează entitatea/
     documentele, jurnal LOCAL_COMMITTED (același commit — nu există stare „entitate fără jurnal”)
  5. abia apoi ACCEPTED -> IMPORTED în cloud; jurnal COMPLETE. Dacă 5 eșuează (offline / răspuns pierdut) datele locale rămân valide, iar reluarea face DOAR pasul 5.
Nu șterge nimic din cloud. Nu rulează în fundal. Nu salvează/loghează URL-uri semnate sau secrete (mesajele de eroare sunt texte fixe).
UID: entitățile create (defunct / lucrare / aviz) primesc `uid` = uid-ul itemului din inbox (același UUID local și cloud); documentele/pozele se leagă de entitatea țintă.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import database as db
import field_inbox_client as fic

log = logging.getLogger("cimitir.field_import")

JOURNAL = "field_import_journal"
PREPARING, LOCAL_COMMITTED, COMPLETE = "PREPARING", "LOCAL_COMMITTED", "COMPLETE"
MIME_RULES = {"image/jpeg": (b"\xff\xd8\xff", ".jpg"), "image/png": (b"\x89PNG\r\n\x1a\n", ".png"), "application/pdf": (b"%PDF-", ".pdf")}
IMAGE_MIMES = ("image/jpeg", "image/png")
STAGE_DOC_TYPES = {"BEFORE": "lucrare_inainte", "DURING": "lucrare_in_timpul", "AFTER": "lucrare_dupa"}
MAX_FILE = fic.MAX_PREVIEW_BYTES
MAX_FILES = 20
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_DOCTYPE_RE = re.compile(r"^[a-z0-9_]{1,60}$")
_DECEASED_COLS = ("nume", "prenume", "data_nasterii", "data_deces", "data_inhumare", "sex", "observatii")
_READY: set = set()
_LOCKS: Dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


# ------------------------------------------------------------------ erori (texte fixe, fără secrete)
class FieldImportError(Exception):
    code = "IMPORT_ERROR"

    def __init__(self, message: str, code: str = "", fields: Tuple[str, ...] = ()):
        super().__init__(message)
        if code:
            self.code = code
        self.fields = tuple(fields)


class ImportBlocked(FieldImportError):
    code = "BLOCKED"


class WrongRegistry(ImportBlocked):
    code = "WRONG_REGISTRY"


class ImportConflict(FieldImportError):
    code = "CONFLICT"


class IntegrityError(FieldImportError):
    code = "INTEGRITY"


@dataclass
class ImportResult:
    item_uid: str
    state: str                      # COMPLETE | LOCAL_COMMITTED
    cloud_final: bool
    created: bool                   # True dacă acest apel a scris în SQLite
    message: str
    local_entity_uid: str = ""
    item_type: str = ""


@dataclass
class Plan:
    item_uid: str
    item_type: str
    cemetery_uid: str
    grave: Optional[Dict[str, Any]] = None
    entity_table: str = ""                     # inhumati | amenajari_avize (entitate creată)
    entity_row: Dict[str, Any] = field(default_factory=dict)
    entity_defaults: Dict[str, Any] = field(default_factory=dict)
    entity_uid: str = ""                       # local_entity_uid (jurnal)
    existing_id: Optional[int] = None          # entitate identică deja prezentă
    doc_entity: Tuple[str, str] = ("", "")     # (entity_type, entity_id); entity_id "" = entitatea creată acum
    doc_type: str = ""
    caption: str = ""
    nr_evidenta: str = ""
    files_required: bool = False
    image_only: bool = False
    summary: List[Tuple[str, str]] = field(default_factory=list)
    destination: str = ""


# ------------------------------------------------------------------ utilitare
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _uuid(v: Any, what: str = "uid") -> str:
    try:
        return str(uuid.UUID(str(v)))
    except (ValueError, AttributeError, TypeError):
        raise ImportBlocked(f"{what} invalid.", "INVALID_UID") from None


def _same_uuid(a: Any, b: Any) -> bool:
    try:
        return uuid.UUID(str(a)) == uuid.UUID(str(b))
    except (ValueError, AttributeError, TypeError):
        return False


def _variants(ref: Any) -> List[str]:
    s = _s(ref)
    out = [s, s.lower()]
    try:
        u = uuid.UUID(s)
        out += [str(u), u.hex]
    except ValueError:
        pass
    return list(dict.fromkeys(x for x in out if x))


@contextlib.contextmanager
def _pinned():
    try:
        import cimitir_registry
    except ImportError:
        yield
        return
    if cimitir_registry.current_binding() is None:
        yield
        return
    with cimitir_registry.pinned():
        yield


@contextlib.contextmanager
def _item_lock(item_uid: str):
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(item_uid, threading.Lock())
    if not lock.acquire(blocking=False):
        raise ImportBlocked("Importul acestui element este deja în curs.", "IN_PROGRESS")
    try:
        yield
    finally:
        lock.release()


def active_cemetery_uid() -> str:
    """Cimitirul registrului activ: binding-ul registrului (UUID), altfel setările bazei (cemetery_uid / cloud_bound_cemetery). Gol = necunoscut."""
    try:
        import cimitir_registry
        b = cimitir_registry.current_binding()
    except ImportError:
        b = None
    if b is not None:
        return str(b.cemetery_uid)
    for key in ("cemetery_uid", "cloud_bound_cemetery"):
        v = (db.get_setting(key) or "").strip()
        if v:
            return v
    return ""


def _check_cemetery(item: Dict[str, Any]) -> str:
    active = active_cemetery_uid()
    cem = _s(item.get("cemetery_uid"))
    if not active:
        raise WrongRegistry("Registrul activ nu are un cimitir cloud asociat; nu pot verifica apartenența elementului.", "REGISTRY_UNKNOWN")
    if not _same_uuid(cem, active):
        raise WrongRegistry(f"Elementul aparține registrului {cem[:8]}…. Deschide registrul corespunzător pentru import.", "WRONG_REGISTRY")
    return str(uuid.UUID(cem))


# ------------------------------------------------------------------ jurnal local
def ensure_tables() -> None:
    key = db.current_db_path()
    if key in _READY and os.path.exists(key):
        return
    conn = db.get_connection()
    try:
        conn.execute(f"CREATE TABLE IF NOT EXISTS {JOURNAL} (inbox_item_uid TEXT PRIMARY KEY, cemetery_uid TEXT NOT NULL, item_type TEXT NOT NULL, "
                     f"local_entity_uid TEXT, import_state TEXT NOT NULL CHECK (import_state IN ('{PREPARING}','{LOCAL_COMMITTED}','{COMPLETE}')), "
                     "local_refs TEXT, last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        for table in ("inhumati", "amenajari_avize"):                       # aceleași coloane ca entity_sync.ensure_tracking (idempotent)
            for col in ("uid TEXT", "version INTEGER DEFAULT 0", "updated_at TEXT"):
                db._add_column_if_missing(conn, table, col)
            conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{table}_uid ON {table}(uid)")
    finally:
        conn.close()
    _READY.add(key)


def journal_get(item_uid: str) -> Optional[Dict[str, Any]]:
    ensure_tables()
    conn = db.get_connection()
    try:
        r = conn.execute(f"SELECT * FROM {JOURNAL} WHERE inbox_item_uid=?", (item_uid,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def journal_uncommitted_cloud() -> List[Dict[str, Any]]:
    """Importuri comise local al căror status cloud (IMPORTED) nu a fost încă finalizat."""
    ensure_tables()
    conn = db.get_connection()
    try:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {JOURNAL} WHERE import_state=? ORDER BY created_at", (LOCAL_COMMITTED,)).fetchall()]
    finally:
        conn.close()


def _journal_set(conn, item_uid: str, cem: str, item_type: str, entity_uid: str, state: str, refs: Optional[dict] = None, err: str = "") -> None:
    now = _now()
    conn.execute(f"INSERT INTO {JOURNAL} (inbox_item_uid, cemetery_uid, item_type, local_entity_uid, import_state, local_refs, last_error, created_at, updated_at) "
                 "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(inbox_item_uid) DO UPDATE SET import_state=excluded.import_state, "
                 "local_entity_uid=excluded.local_entity_uid, local_refs=COALESCE(excluded.local_refs, local_refs), last_error=excluded.last_error, updated_at=excluded.updated_at",
                 (item_uid, cem, item_type, entity_uid, state, json.dumps(refs, sort_keys=True) if refs is not None else None, err, now, now))


def _journal_preparing(item_uid: str, cem: str, item_type: str, entity_uid: str) -> None:
    conn = db.get_connection()
    try:
        conn.execute(f"INSERT OR IGNORE INTO {JOURNAL} (inbox_item_uid, cemetery_uid, item_type, local_entity_uid, import_state, created_at, updated_at) "
                     "VALUES (?,?,?,?,?,?,?)", (item_uid, cem, item_type, entity_uid, PREPARING, _now(), _now()))
    finally:
        conn.close()


def _journal_drop_preparing(item_uid: str) -> None:
    conn = db.get_connection()
    try:
        conn.execute(f"DELETE FROM {JOURNAL} WHERE inbox_item_uid=? AND import_state=?", (item_uid, PREPARING))
    finally:
        conn.close()


def _journal_complete(item_uid: str) -> None:
    conn = db.get_connection()
    try:
        conn.execute(f"UPDATE {JOURNAL} SET import_state=?, last_error='', updated_at=? WHERE inbox_item_uid=? AND import_state IN (?,?)",
                     (COMPLETE, _now(), item_uid, LOCAL_COMMITTED, COMPLETE))
    finally:
        conn.close()


def _journal_note(item_uid: str, err: str) -> None:
    conn = db.get_connection()
    try:
        conn.execute(f"UPDATE {JOURNAL} SET last_error=?, updated_at=? WHERE inbox_item_uid=?", (err[:60], _now(), item_uid))
    finally:
        conn.close()


# ------------------------------------------------------------------ planificare (doar citire)
def _find_uid(conn, table: str, ref: Any):
    v = _variants(ref)
    if not v:
        return None
    return conn.execute(f"SELECT * FROM {table} WHERE uid IN ({','.join('?' * len(v))})", v).fetchone()


def _grave(conn, ref: Any) -> Dict[str, Any]:
    r = _find_uid(conn, "locuri_de_veci", ref)
    if r is None:
        raise ImportBlocked("Locul de veci (grave_uid) nu există în registrul local activ. Nu creez automat alt loc de veci.", "MISSING_GRAVE")
    return {"id": r["id"], "cod_loc": r["cod_loc"], "uid": r["uid"]}


def _target(conn, ref: Any):
    for table, kind in (("inhumati", "inhumat"), ("amenajari_avize", "aviz"), ("locuri_de_veci", "loc")):
        r = _find_uid(conn, table, ref)
        if r is not None:
            return kind, r
    raise ImportBlocked("Ținta (target_uid) nu există în registrul local. Importă mai întâi elementul de care depinde.", "MISSING_TARGET")


def _norm(v: Any) -> str:
    return _s(v)


def _differs(existing, row: Dict[str, Any]) -> List[str]:
    return [k for k, v in row.items() if k != "uid" and _norm(existing[k]) != _norm(v)]


def _doc_dest(kind: str, row, grave: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    return ("loc", row["cod_loc"]) if kind == "loc" else (kind, str(row["id"]))


def plan_item(conn, item: Dict[str, Any]) -> Plan:
    """Validează itemul față de registrul local și întoarce planul; ridică ImportBlocked / ImportConflict. Nu scrie nimic."""
    t = item.get("item_type")
    uid = _uuid(item.get("uid"), "uid item")
    payload = item.get("payload") or {}
    cem = _uuid(item.get("cemetery_uid"), "cemetery_uid")
    p = Plan(item_uid=uid, item_type=t, cemetery_uid=cem)
    g_ref, t_ref = item.get("grave_uid"), item.get("target_uid")
    if t in ("DECEASED", "WORK", "PERMIT"):
        if not g_ref:
            raise ImportBlocked("Elementul nu are grave_uid: nu pot asocia intrarea unui loc de veci.", "MISSING_GRAVE")
        p.grave = _grave(conn, g_ref)
        p.entity_uid = uid
        p.files_required = False
        if t == "DECEASED":
            p.entity_table, p.doc_entity = "inhumati", ("inhumat", "")
            p.entity_row = {k: _s(payload.get(k)) for k in db._INHUMAT_FIELDS if k not in ("cnp", "data_deshumare", "cauza_deces", "profesie", "religie", "ultima_adresa",
                                                                                          "nr_certificat_deces", "nr_adeverinta_inhumare")}
            p.entity_row["id_loc"] = p.grave["id"]
            p.entity_defaults = {k: "" for k in db._INHUMAT_FIELDS}
            p.destination = f"Înhumați — loc {p.grave['cod_loc']}"
        else:
            p.entity_table, p.doc_entity = "amenajari_avize", ("aviz", "")
            if t == "WORK":
                fin = _s(payload.get("data_sfarsit"))
                p.entity_row = {"cod_loc": p.grave["cod_loc"], "nr_aviz": "", "data_aviz": "", "lucrare": _s(payload.get("descriere")),
                                "stare": "finalizata" if fin else "", "observatii": _s(payload.get("observatii")), "data_finalizare": fin,
                                "executant": _s(payload.get("executant")), "data_inceput": _s(payload.get("data_inceput"))}
                p.destination = f"Amenajări / lucrări — loc {p.grave['cod_loc']}"
            else:
                p.entity_row = {"cod_loc": p.grave["cod_loc"], "nr_aviz": _s(payload.get("nr_aviz")), "data_aviz": _s(payload.get("data_aviz")),
                                "lucrare": _s(payload.get("lucrare")), "stare": "", "observatii": _s(payload.get("observatii")), "data_finalizare": ""}
                p.destination = f"Avize — loc {p.grave['cod_loc']}"
        p.doc_type = "alt_document"
        ex = _find_uid(conn, p.entity_table, uid)
        if ex is not None:
            diff = _differs(ex, p.entity_row)
            if diff:
                raise ImportConflict("În registrul local există deja o entitate cu același UID și date diferite; nu suprascriu. Câmpuri: " + ", ".join(diff), "CONFLICT", tuple(diff))
            p.existing_id = ex["id"]
        elif t == "PERMIT" and p.entity_row["nr_aviz"]:
            dup = conn.execute("SELECT 1 FROM amenajari_avize WHERE cod_loc=? AND nr_aviz=? LIMIT 1", (p.grave["cod_loc"], p.entity_row["nr_aviz"])).fetchone()
            if dup:
                raise ImportConflict("Există deja un aviz cu același număr pe acest loc (UID diferit); nu creez duplicat. Câmp: nr_aviz", "CONFLICT", ("nr_aviz",))
    elif t == "GRAVE_PHOTO":
        ref = g_ref or t_ref
        if not ref:
            raise ImportBlocked("Elementul nu are grave_uid.", "MISSING_GRAVE")
        p.grave = _grave(conn, ref)
        p.entity_uid = _s(p.grave["uid"])
        p.doc_entity, p.doc_type, p.caption = ("loc", p.grave["cod_loc"]), "poza_loc", _s(payload.get("caption"))
        p.files_required = p.image_only = True
        p.destination = f"Fotografii loc {p.grave['cod_loc']}"
    elif t == "WORK_PHOTO":
        stage = item.get("work_stage")
        if stage not in STAGE_DOC_TYPES:
            raise ImportBlocked("work_stage (BEFORE/DURING/AFTER) lipsește sau este invalid; nu import fotografia fără etapă.", "BAD_STAGE")
        if not t_ref:
            raise ImportBlocked("Fotografia de lucrare nu indică lucrarea (target_uid).", "MISSING_TARGET")
        kind, row = _target(conn, t_ref)
        if kind != "aviz":
            raise ImportBlocked("target_uid nu indică o lucrare/aviz.", "BAD_TARGET")
        if g_ref:
            p.grave = _grave(conn, g_ref)
            if row["cod_loc"] != p.grave["cod_loc"]:
                raise ImportBlocked("Lucrarea nu aparține locului de veci indicat.", "TARGET_GRAVE_MISMATCH")
        p.entity_uid = _s(row["uid"])
        p.doc_entity, p.doc_type, p.caption = ("aviz", str(row["id"])), STAGE_DOC_TYPES[stage], _s(payload.get("caption"))
        p.files_required = p.image_only = True
        p.destination = f"Fotografii lucrare #{row['id']} (loc {row['cod_loc']}) — etapa {fic.STAGE_LABELS[stage]}"
    elif t == "DOCUMENT":
        if t_ref:
            kind, row = _target(conn, t_ref)
            if g_ref:
                p.grave = _grave(conn, g_ref)
                bad = (kind == "inhumat" and row["id_loc"] != p.grave["id"]) or (kind == "aviz" and row["cod_loc"] != p.grave["cod_loc"]) \
                    or (kind == "loc" and row["id"] != p.grave["id"])
                if bad:
                    raise ImportBlocked("Ținta documentului nu aparține locului de veci indicat.", "TARGET_GRAVE_MISMATCH")
            p.entity_uid, p.doc_entity = _s(row["uid"]), _doc_dest(kind, row, p.grave)
            label = f"loc {row['cod_loc']}" if kind == "loc" else f"{'înhumat' if kind == 'inhumat' else 'lucrare'} #{row['id']}"
        else:
            p.grave = _grave(conn, g_ref)
            p.entity_uid, p.doc_entity = _s(p.grave["uid"]), ("loc", p.grave["cod_loc"])
            label = f"loc {p.grave['cod_loc']}"
        dt, cap = _s(payload.get("doc_type")), _s(payload.get("caption"))
        if _DOCTYPE_RE.match(dt):
            p.doc_type = dt
        else:
            p.doc_type, cap = "alt_document", (f"[{dt}] {cap}" if cap else f"[{dt}]")
        p.caption, p.nr_evidenta, p.files_required = cap, _s(payload.get("nr_evidenta")), True
        p.destination = f"Documente — {label}"
    else:
        raise ImportBlocked("Tip de element necunoscut.", "BAD_TYPE")
    p.summary = [(fic.FIELD_LABELS.get(k, k), _s(v)) for k, v in payload.items() if _s(v)]
    return p


def validate_files(item: Dict[str, Any], plan: Plan) -> List[Dict[str, Any]]:
    files = list(item.get("files") or [])
    if plan.files_required and not files:
        raise ImportBlocked("Elementul nu are fișier atașat.", "NO_FILE")
    if len(files) > MAX_FILES:
        raise ImportBlocked("Prea multe fișiere atașate.", "TOO_MANY_FILES")
    out, seen = [], set()
    for f in files:
        fuid = _uuid(f.get("uid"), "uid fișier")
        mime, sha, size = _s(f.get("mime")).lower(), _s(f.get("sha256")).lower(), f.get("size")
        if fuid in seen:
            raise ImportBlocked("Fișier atașat duplicat.", "DUP_FILE")
        seen.add(fuid)
        if f.get("status") != "READY":
            raise ImportBlocked("Un fișier atașat nu este încă complet încărcat în cloud.", "FILE_NOT_READY")
        if mime not in MIME_RULES or (plan.image_only and mime not in IMAGE_MIMES):
            raise ImportBlocked("Tip de fișier neacceptat (se acceptă JPEG, PNG" + ("" if plan.image_only else ", PDF") + ").", "BAD_MIME")
        if not _SHA_RE.match(sha) or not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_FILE:
            raise ImportBlocked("Metadatele fișierului (mărime / SHA-256) lipsesc sau sunt invalide.", "BAD_FILE_META")
        out.append({"uid": fuid, "mime": mime, "sha256": sha, "size": size})
    return out


def preview(item: Dict[str, Any], cemetery_name: str = "") -> Dict[str, Any]:
    """Previzualizare înainte de confirmare: tip, registru/cimitir, loc, date principale, nr. fișiere, destinația locală, blocaje. Citire locală."""
    ensure_tables()
    out: Dict[str, Any] = {"type": fic.TYPE_LABELS.get(item.get("item_type"), str(item.get("item_type"))), "blockers": [], "lines": [], "files": len(item.get("files") or []),
                           "destination": "", "grave": "", "registry": "", "can_import": False, "conflict": False}
    stage = item.get("work_stage")
    if stage in fic.STAGE_LABELS:
        out["type"] += f" ({fic.STAGE_LABELS[stage]})"
    out["registry"] = f"{cemetery_name or 'registru activ'} [{active_cemetery_uid()[:8] or '—'}] (element: {_s(item.get('cemetery_uid'))[:8]})"
    try:
        with _pinned():
            _check_cemetery(item)
            conn = db.get_connection()
            try:
                plan = plan_item(conn, item)
            finally:
                conn.close()
            validate_files(item, plan)
        out.update(destination=plan.destination, grave=(plan.grave or {}).get("cod_loc", ""), lines=plan.summary)
        out["can_import"] = True
    except ImportConflict as exc:
        out["blockers"].append(str(exc))
        out["conflict"] = True
    except FieldImportError as exc:
        out["blockers"].append(str(exc))
    except sqlite3.Error:
        out["blockers"].append("Registrul local nu poate fi citit.")
    if not out["lines"]:
        out["lines"] = [(fic.FIELD_LABELS.get(k, k), _s(v)) for k, v in (item.get("payload") or {}).items() if _s(v)]
    return out


def preview_text(pv: Dict[str, Any]) -> str:
    lines = [f"Tip: {pv['type']}", f"Registru / cimitir: {pv['registry']}", f"Loc de veci: {pv['grave'] or '—'}"]
    lines += [f"{k}: {v}" for k, v in pv["lines"]]
    lines += [f"Fișiere atașate: {pv['files']}", f"Destinație locală: {pv['destination'] or '—'}"]
    if pv["blockers"]:
        lines += ["", "IMPORT BLOCAT:"] + [f" - {b}" for b in pv["blockers"]]
    return "\n".join(lines)


# ------------------------------------------------------------------ descărcare + verificare (staging)
def _staging_dir(item_uid: str) -> str:
    return os.path.join(db.cache_dir("field_import_staging"), item_uid)


def _sha_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download_all(client, item_uid: str, files: List[Dict[str, Any]]) -> Dict[str, str]:
    """{file_uid: cale în staging} — fiecare fișier verificat (mărime, SHA-256, semnătură, tip). URL-ul semnat trăiește doar în această funcție."""
    sdir = _staging_dir(item_uid)
    shutil.rmtree(sdir, ignore_errors=True)
    os.makedirs(sdir, exist_ok=True)
    staged: Dict[str, str] = {}
    for f in files:
        info = client.signed_url(item_uid, f["uid"])
        if (info.get("sha256") and _s(info["sha256"]).lower() != f["sha256"]) or (info.get("size") is not None and int(info["size"]) != f["size"]) \
                or (info.get("mime") and _s(info["mime"]).lower() != f["mime"]):
            raise IntegrityError("Metadatele fișierului diferă între listă și URL; import oprit.", "META_MISMATCH")
        data = client.download_signed(str(info.get("url") or ""))
        del info
        if len(data) != f["size"]:
            raise IntegrityError("Mărimea fișierului descărcat nu corespunde; import oprit.", "SIZE_MISMATCH")
        if hashlib.sha256(data).hexdigest() != f["sha256"]:
            raise IntegrityError("SHA-256 al fișierului descărcat nu corespunde; import oprit.", "SHA_MISMATCH")
        if not data.startswith(MIME_RULES[f["mime"]][0]):
            raise IntegrityError("Conținutul fișierului nu corespunde tipului declarat; import oprit.", "CORRUPT_FILE")
        part = os.path.join(sdir, f["uid"] + ".part")
        with open(part, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        final = os.path.join(sdir, f["uid"] + MIME_RULES[f["mime"]][1])
        os.replace(part, final)
        staged[f["uid"]] = final
    return staged


def _atomic_move(src: str, dst: str) -> None:
    try:
        os.replace(src, dst)
    except OSError:
        tmp = dst + ".part"
        shutil.copyfile(src, tmp)
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp, dst)
        os.remove(src)


def _dest_path(entity_type: str, f: Dict[str, Any]) -> str:
    return os.path.join(db._media_dir(entity_type), f"field_{f['uid']}{MIME_RULES[f['mime']][1]}")


def _place_file(entity_type: str, f: Dict[str, Any], src: str) -> str:
    """Nume determinist (field_<file_uid>.<ext>) în folderul media al registrului; fără suprascriere cu alt SHA; aceeași copie reîmprospătată = refolosită."""
    dest = _dest_path(entity_type, f)
    root = os.path.realpath(db.media_root())
    if os.path.commonpath([root, os.path.realpath(dest)]) != root:
        raise IntegrityError("Cale media invalidă.", "PATH")
    if os.path.exists(dest):
        if _sha_file(dest) != f["sha256"]:
            raise IntegrityError("Există deja un fișier local cu același nume și alt SHA-256; nu îl suprascriu.", "MEDIA_EXISTS_DIFFERENT")
        if os.path.exists(src):
            os.remove(src)
        return dest
    _atomic_move(src, dest)
    return dest


# ------------------------------------------------------------------ commit local (o singură tranzacție)
def _commit_local(item: Dict[str, Any], files: List[Dict[str, Any]], staged: Dict[str, str]) -> Dict[str, Any]:
    uid = item["uid"]
    conn = db.get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        placed: List[str] = []
        try:
            j = conn.execute(f"SELECT import_state FROM {JOURNAL} WHERE inbox_item_uid=?", (uid,)).fetchone()
            if j and j["import_state"] in (LOCAL_COMMITTED, COMPLETE):
                conn.execute("ROLLBACK")
                return {"created": False}
            _check_cemetery(item)
            plan = plan_item(conn, item)
            created = False
            if plan.entity_table:
                if plan.existing_id is not None:
                    ent_id = plan.existing_id
                else:
                    row = {**plan.entity_defaults, **plan.entity_row, "uid": plan.entity_uid}
                    ent_id = conn.execute(f"INSERT INTO {plan.entity_table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values())).lastrowid
                    created = True
                doc_entity = (plan.doc_entity[0], str(ent_id))
                entity_ref = {"table": plan.entity_table, "id": ent_id, "created": plan.existing_id is None}
            else:
                doc_entity, entity_ref = plan.doc_entity, {"table": "", "id": plan.doc_entity[1], "created": False}
            doc_ids, paths = [], []
            for f in files:
                expected = _dest_path(doc_entity[0], f)               # nume determinist per fișier cloud: aceeași poză trimisă pe etape diferite = documente diferite
                if conn.execute("SELECT 1 FROM documents WHERE entity_type=? AND entity_id=? AND file_path=?", (*doc_entity, expected)).fetchone():
                    continue
                existed = os.path.exists(expected)
                dest = _place_file(doc_entity[0], f, staged[f["uid"]])
                if not existed:
                    placed.append(dest)
                cur = conn.execute("INSERT INTO documents (entity_type, entity_id, doc_type, file_path, caption, nr_evidenta, date_added, sha256, size) VALUES (?,?,?,?,?,?,?,?,?)",
                                   (*doc_entity, plan.doc_type, dest, plan.caption, plan.nr_evidenta, datetime.now().strftime("%d.%m.%Y %H:%M"), f["sha256"], f["size"]))
                doc_ids.append(cur.lastrowid)
                paths.append(os.path.relpath(dest, db.media_root()))
                created = True
            refs = {"entity": entity_ref, "documents": doc_ids, "files": paths, "doc_entity": list(doc_entity)}
            _journal_set(conn, uid, plan.cemetery_uid, plan.item_type, plan.entity_uid, LOCAL_COMMITTED, refs)
            conn.execute("COMMIT")
            return {"created": created}
        except BaseException as exc:
            with contextlib.suppress(Exception):
                conn.execute("ROLLBACK")
            if isinstance(exc, Exception):                       # eșec obișnuit: fără fișiere orfane; o cădere reală (BaseException) le lasă, reluarea le refolosește
                for path in placed:
                    with contextlib.suppress(OSError):
                        os.remove(path)
            raise
    finally:
        conn.close()


# ------------------------------------------------------------------ finalizare cloud
def _result(item: Dict[str, Any], j: Optional[Dict[str, Any]], state: str, cloud: bool, created: bool, msg: str) -> ImportResult:
    return ImportResult(item_uid=item["uid"], state=state, cloud_final=cloud, created=created, message=msg, item_type=item.get("item_type", ""),
                        local_entity_uid=(j or {}).get("local_entity_uid") or "")


def _finalize_cloud(client, item: Dict[str, Any], created: bool = False) -> ImportResult:
    uid = item["uid"]
    j = journal_get(uid)
    try:
        if item.get("status") == "ACCEPTED":
            client.mark_imported(uid, "ACCEPTED")
        elif item.get("status") != "IMPORTED":
            raise fic.InvalidTransition("Starea din cloud nu permite finalizarea (nici ACCEPTED, nici IMPORTED).")
    except fic.FieldCloudError as exc:
        _journal_note(uid, getattr(exc, "code", "") or type(exc).__name__)
        return _result(item, j, LOCAL_COMMITTED, False, created,
                       "Importat în registrul local. Statusul IMPORTED din cloud nu a putut fi trimis; reîncearcă — nu se dublează nimic." if isinstance(exc, fic.CloudUnavailable)
                       else "Importat în registrul local. Cloud-ul a refuzat finalizarea statusului; reîncearcă după reîmprospătare.")
    _journal_complete(uid)
    return _result(item, journal_get(uid), COMPLETE, True, created, "Importat în registrul local și marcat IMPORTED în cloud.")


def finalize_pending(client) -> List[ImportResult]:
    """Finalizează în cloud (ACCEPTED -> IMPORTED) importurile deja comise local; NU importă nimic nou. Se oprește la prima eroare de rețea."""
    out: List[ImportResult] = []
    with _pinned():
        active = active_cemetery_uid()
        for j in journal_uncommitted_cloud():
            if not _same_uuid(j["cemetery_uid"], active):
                continue
            try:
                with _item_lock(j["inbox_item_uid"]):
                    item = client.get_item(j["inbox_item_uid"])
                    shutil.rmtree(_staging_dir(j["inbox_item_uid"]), ignore_errors=True)
                    if journal_get(j["inbox_item_uid"])["import_state"] == LOCAL_COMMITTED:
                        out.append(_finalize_cloud(client, item))
            except ImportBlocked:
                continue
            except fic.FieldCloudError:
                break
    return out


# ------------------------------------------------------------------ puncte de intrare
def import_item(client, item_uid: str) -> ImportResult:
    """Importă local un item ACCEPTED (acțiune explicită a operatorului). Idempotent: reluarea nu dublează entitatea/media; după commit local face doar finalizarea cloud."""
    item_uid = _uuid(item_uid, "uid item")
    ensure_tables()
    with _pinned(), _item_lock(item_uid):
        j = journal_get(item_uid)
        if j and j["import_state"] == COMPLETE:
            return ImportResult(item_uid, COMPLETE, True, False, "Elementul a fost deja importat.", j["local_entity_uid"] or "", j["item_type"])
        try:
            item = client.get_item(item_uid)
        except fic.FieldCloudError:
            if j and j["import_state"] == LOCAL_COMMITTED:
                return ImportResult(item_uid, LOCAL_COMMITTED, False, False, "Importat local; cloud indisponibil — finalizarea statusului IMPORTED se reia mai târziu.",
                                    j["local_entity_uid"] or "", j["item_type"])
            raise
        _check_cemetery(item)
        if j and j["import_state"] == LOCAL_COMMITTED:
            shutil.rmtree(_staging_dir(item_uid), ignore_errors=True)       # cădere după commit, înainte de curățare
            return _finalize_cloud(client, item)
        status = item.get("status")
        if status == "PENDING":
            raise ImportBlocked("Elementul este PENDING: acceptă-l mai întâi (Acceptă / Acceptă și importă).", "NOT_ACCEPTED")
        if status == "IMPORTED":
            raise ImportBlocked("Elementul este marcat IMPORTED în cloud, dar nu există import local în acest registru.", "IMPORTED_ELSEWHERE")
        if status != "ACCEPTED":
            raise ImportBlocked("Doar elementele ACCEPTED pot fi importate.", "NOT_ACCEPTED")
        conn = db.get_connection()
        try:
            plan = plan_item(conn, item)
        finally:
            conn.close()
        files = validate_files(item, plan)
        _journal_preparing(item_uid, plan.cemetery_uid, plan.item_type, plan.entity_uid)
        try:
            staged = _download_all(client, item_uid, files)
            res = _commit_local(item, files, staged)
        except Exception:                                          # o cădere reală (BaseException/kill) nu trece pe aici: staging rămâne, reluarea îl rescrie
            _journal_drop_preparing(item_uid)
            shutil.rmtree(_staging_dir(item_uid), ignore_errors=True)
            raise
        shutil.rmtree(_staging_dir(item_uid), ignore_errors=True)
        log.info("field import %s %s: LOCAL_COMMITTED", plan.item_type, item_uid)
        return _finalize_cloud(client, item, created=res["created"])


def precheck(item: Dict[str, Any]) -> None:
    """Verificări locale înainte de „Acceptă și importă”: nu acceptă în cloud un item care oricum nu poate fi importat acum."""
    pv = preview(item)
    if not pv["can_import"]:
        raise ImportBlocked(" ".join(pv["blockers"]) or "Import imposibil.", "PRECHECK")


def accept_and_import(client, item_uid: str) -> ImportResult:
    """PENDING -> ACCEPTED (mașina de stare din cloud, neocolită), apoi import local. Pentru un item deja ACCEPTED face doar importul."""
    item_uid = _uuid(item_uid, "uid item")
    item = client.get_item(item_uid)
    if item.get("status") == "PENDING":
        with _pinned():
            _check_cemetery(item)
            precheck(item)
        client.accept(item_uid, "PENDING")
    return import_item(client, item_uid)
