"""field_import_history.py — „Istoric importuri din teren”: citire locală a jurnalului `field_import_journal` + acțiuni sigure. Fără Qt.

Nu creează alt sistem de import și nu modifică mecanismul crash-safe din field_import.py (îl reutilizează):
  - lista / detaliile: SQLite local, fără rețea (funcționează offline); starea cloud se cere separat, într-un worker, și poate lipsi;
  - „Finalizează în cloud” (LOCAL_COMMITTED): DOAR ACCEPTED -> IMPORTED în cloud (sau jurnal COMPLETE dacă cloud-ul e deja IMPORTED); nu reimportă SQLite, nu copiază media;
  - „Reia importul” (PREPARING): numai după verificări (registru, cimitir, ACCEPTED, fără conflict, media/staging valide); altfel nu modifică nimic și întoarce motivele.
Nu afișează / nu persistă URL-uri semnate sau secrete: niciun URL semnat nu este cerut aici; textele afișate trec prin `redact()`.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import database as db
import field_import as fim
import field_inbox_client as fic

log = logging.getLogger("cimitir.field_import_history")

F_ALL, F_COMPLETE, F_COMMITTED, F_PREPARING, F_ERROR = "ALL", "COMPLETE", "LOCAL_COMMITTED", "PREPARING", "ERROR"
FILTERS = (F_ALL, F_COMPLETE, F_COMMITTED, F_PREPARING, F_ERROR)
FILTER_LABELS = {F_ALL: "Toate", F_COMPLETE: "COMPLETE", F_COMMITTED: "LOCAL_COMMITTED", F_PREPARING: "PREPARING", F_ERROR: "Cu eroare"}
CLOUD_UNAVAILABLE = "indisponibil"
MAX_ROWS = 1000

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_KEY_RE = re.compile(r"(cimitir\.[0-9a-fA-F-]{8,}\.\S+|sb_secret_\S+|Binding\s+\S+|(?:token|signature|apikey|password|secret)=\S+)", re.IGNORECASE)
_KINDS = {"inhumati": "inhumat", "amenajari_avize": "aviz"}
_PAYLOAD_COLS = {
    "inhumat": ("nume", "prenume", "data_nasterii", "data_deces", "data_inhumare", "sex", "observatii"),
    "aviz": ("nr_aviz", "data_aviz", "lucrare", "stare", "data_finalizare", "observatii"),
}
_LOCAL_LABELS = {"nr_aviz": "Nr. aviz", "stare": "Stare", "data_finalizare": "Data finalizare", "doc_type": "Tip document", "caption": "Descriere",
                 "nr_evidenta": "Nr. evidență"}
_DEST = {"DECEASED": "Înhumați", "WORK": "Amenajări / lucrări", "PERMIT": "Avize", "DOCUMENT": "Documente", "GRAVE_PHOTO": "Fotografii loc",
         "WORK_PHOTO": "Fotografii lucrare"}


def redact(text: Any) -> str:
    """Text sigur de afișat: URL-uri (inclusiv semnate) și chei/secrete sunt ascunse."""
    s = "" if text is None else str(text)
    return _KEY_RE.sub("***", _URL_RE.sub("[URL ascuns]", s))


@dataclass
class ResumeVerdict:
    can_resume: bool
    reasons: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ citire locală
def _refs(j: dict[str, Any]) -> dict[str, Any]:
    try:
        v = json.loads(j.get("local_refs") or "{}")
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def _resolve(conn, j: dict[str, Any], refs: dict[str, Any]):
    """(kind, row) al entității locale (inhumat | aviz | loc) sau ('', None) dacă nu mai există."""
    ent = refs.get("entity") or {}
    table = ent.get("table")
    if table in _KINDS and isinstance(ent.get("id"), int):
        r = conn.execute(f"SELECT * FROM {table} WHERE id=?", (ent["id"],)).fetchone()
        if r is not None:
            return _KINDS[table], r
    uid = (j.get("local_entity_uid") or "").strip()
    if uid:
        for table, kind in (("inhumati", "inhumat"), ("amenajari_avize", "aviz"), ("locuri_de_veci", "loc")):
            r = fim._find_uid(conn, table, uid)
            if r is not None:
                return kind, r
    return "", None


def _cod_loc(conn, kind: str, row) -> str:
    if row is None:
        return ""
    if kind == "inhumat":
        g = conn.execute("SELECT cod_loc FROM locuri_de_veci WHERE id=?", (row["id_loc"],)).fetchone()
        return g["cod_loc"] if g else ""
    return row["cod_loc"] or ""


def _grave_uid(conn, cod: str) -> str:
    if not cod:
        return ""
    g = conn.execute("SELECT uid FROM locuri_de_veci WHERE cod_loc=?", (cod,)).fetchone()
    return (g["uid"] or "") if g else ""


def _label(k: str) -> str:
    return fic.FIELD_LABELS.get(k) or _LOCAL_LABELS.get(k, k)


def _row(conn, j: dict[str, Any], cemetery_name: str, active: str) -> dict[str, Any]:
    refs = _refs(j)
    kind, ent = _resolve(conn, j, refs)
    cod = _cod_loc(conn, kind, ent)
    cem = j["cemetery_uid"]
    registry = (cemetery_name + " " if cemetery_name and fim._same_uuid(cem, active) else "") + f"[{cem[:8]}]"
    err = redact(j.get("last_error") or "")
    return {"inbox_item_uid": j["inbox_item_uid"], "created_at": j["created_at"], "item_type": j["item_type"], "type": fic.TYPE_LABELS.get(j["item_type"], j["item_type"]),
            "cemetery_uid": cem, "registry": registry.strip(), "cod_loc": cod, "grave_uid": _grave_uid(conn, cod), "local_entity_uid": j.get("local_entity_uid") or "",
            "state": j["import_state"], "files": len(refs.get("documents") or []), "last_error": err, "last_attempt": j["updated_at"], "has_error": bool(err)}


def list_history(flt: str = F_ALL, cemetery_name: str = "", limit: int = MAX_ROWS) -> list[dict[str, Any]]:
    """Rândurile jurnalului local (cele mai noi primele). Citire locală: nu face apeluri de rețea."""
    if flt not in FILTERS:
        raise ValueError("filtru invalid")
    fim.ensure_tables()
    where, args = "", []
    if flt in (F_COMPLETE, F_COMMITTED, F_PREPARING):
        where, args = " WHERE import_state=?", [flt]
    elif flt == F_ERROR:
        where = " WHERE COALESCE(last_error,'')<>''"
    conn = db.get_connection()
    try:
        active = fim.active_cemetery_uid()
        js = [dict(r) for r in conn.execute(f"SELECT * FROM {fim.JOURNAL}{where} ORDER BY created_at DESC, inbox_item_uid LIMIT ?", (*args, max(1, int(limit)))).fetchall()]
        return [_row(conn, j, cemetery_name, active) for j in js]
    finally:
        conn.close()


def local_details(item_uid: str, cemetery_name: str = "") -> dict[str, Any] | None:
    """Detalii exclusiv locale: jurnal, entitate, payload local relevant, fișiere/media (cale relativă, SHA-256 salvat), țintă de navigare."""
    fim.ensure_tables()
    conn = db.get_connection()
    try:
        r = conn.execute(f"SELECT * FROM {fim.JOURNAL} WHERE inbox_item_uid=?", (item_uid,)).fetchone()
        if r is None:
            return None
        j = dict(r)
        refs = _refs(j)
        row = _row(conn, j, cemetery_name, fim.active_cemetery_uid())
        kind, ent = _resolve(conn, j, refs)
        payload: list[Any] = []
        if ent is not None and kind in _PAYLOAD_COLS:
            payload = [(_label(k), redact(ent[k])) for k in _PAYLOAD_COLS[kind] if str(ent[k] or "").strip()]
        docs: list[dict[str, Any]] = []
        ids = [i for i in (refs.get("documents") or []) if isinstance(i, int)]
        if ids:
            q = ",".join("?" * len(ids))
            root = os.path.realpath(db.media_root())
            for d in conn.execute(f"SELECT id, entity_type, entity_id, doc_type, caption, nr_evidenta, file_path, sha256, size FROM documents WHERE id IN ({q}) ORDER BY id", ids):
                path = d["file_path"] or ""
                rel = os.path.relpath(os.path.realpath(path), root) if path and os.path.realpath(path).startswith(root) else os.path.basename(path)
                docs.append({"document_id": d["id"], "doc_type": d["doc_type"], "path": redact(rel), "sha256": d["sha256"] or "", "size": d["size"], "exists": bool(path) and os.path.isfile(path)})
            if not payload and docs:
                first = conn.execute("SELECT doc_type, caption, nr_evidenta FROM documents WHERE id=?", (ids[0],)).fetchone()
                payload = [(_label(k), redact(first[k])) for k in ("doc_type", "caption", "nr_evidenta") if first and str(first[k] or "").strip()]
        dest = _DEST.get(j["item_type"], "") + (f" — loc {row['cod_loc']}" if row["cod_loc"] else "")
        nav = {"kind": j["item_type"], "cod_loc": row["cod_loc"], "label": dest} if ent is not None and row["cod_loc"] else None
        if j["import_state"] == fim.PREPARING:
            local_state = "Nimic comis local (import neterminat)."
        else:
            local_state = ("Entitate locală: prezentă" if ent is not None else "Entitate locală: LIPSĂ") + \
                          (f"; fișiere prezente: {sum(1 for d in docs if d['exists'])}/{len(docs)}" if docs else "")
        return {**row, "updated_at": j["updated_at"], "payload": payload, "destination": dest, "entity_kind": kind, "entity_id": ent["id"] if ent is not None else None,
                "files_detail": docs, "navigation": nav, "local_state": local_state,
                "cloud_status": CLOUD_UNAVAILABLE}
    finally:
        conn.close()


def navigation_target(item_uid: str) -> dict[str, Any] | None:
    d = local_details(item_uid)
    return d["navigation"] if d else None


# ------------------------------------------------------------------ stare cloud (rulează în worker; niciun URL semnat)
def _safe_item(item: dict[str, Any]) -> dict[str, Any]:
    return {"status": str(item.get("status") or ""), "item_type": item.get("item_type"), "cemetery_uid": item.get("cemetery_uid"),
            "payload": [(fic.FIELD_LABELS.get(k, k), redact(v)) for k, v in (item.get("payload") or {}).items() if str(v or "").strip()],
            "files": [{"filename": redact(f.get("filename") or "fișier"), "mime": f.get("mime"), "size": f.get("size"), "sha256": f.get("sha256") or ""}
                      for f in item.get("files") or []]}


def cloud_snapshot(client_factory: Callable[[], Any], item_uid: str) -> dict[str, Any]:
    """{'available','status','item','verdict'}. Orice eșec (offline, neconfigurat, server) = status 'indisponibil'; nu ridică excepții."""
    out: dict[str, Any] = {"available": False, "status": CLOUD_UNAVAILABLE, "item": None, "verdict": None}
    try:
        client = client_factory()
        item = client.get_item(item_uid)
    except Exception as exc:  # noqa: BLE001
        log.debug("stare cloud indisponibilă (%s)", type(exc).__name__)
        return out
    out.update(available=True, status=str(item.get("status") or CLOUD_UNAVAILABLE), item=_safe_item(item))
    j = fim.journal_get(item_uid)
    if j and j["import_state"] == fim.PREPARING:
        out["verdict"] = check_resume(client, item_uid, item)
    return out


# ------------------------------------------------------------------ Finalizează în cloud (doar ACCEPTED -> IMPORTED)
def finalize_cloud(client, item_uid: str) -> fim.ImportResult:
    """Pentru LOCAL_COMMITTED: NU atinge SQLite/media. Cloud ACCEPTED -> IMPORTED; cloud deja IMPORTED -> jurnal COMPLETE. Idempotent."""
    uid = fim._uuid(item_uid, "uid item")
    fim.ensure_tables()
    with fim._pinned(), fim._item_lock(uid):
        j = fim.journal_get(uid)
        if j is None:
            raise fim.ImportBlocked("Nu există import local înregistrat pentru acest element.", "NOT_IN_JOURNAL")
        done = fim.ImportResult(uid, fim.COMPLETE, True, False, "Elementul este deja finalizat.", j["local_entity_uid"] or "", j["item_type"])
        if j["import_state"] == fim.COMPLETE:
            return done
        if j["import_state"] != fim.LOCAL_COMMITTED:
            raise fim.ImportBlocked("Importul local nu este comis (PREPARING). Folosește „Reia importul”.", "NOT_LOCAL_COMMITTED")
        fim._check_cemetery({"cemetery_uid": j["cemetery_uid"]})
        try:
            item = client.get_item(uid)
        except fic.FieldCloudError as exc:
            fim._journal_note(uid, getattr(exc, "code", "") or type(exc).__name__)
            return fim.ImportResult(uid, fim.LOCAL_COMMITTED, False, False, "Cloud indisponibil — importul local este valid; finalizarea se poate reîncerca.",
                                    j["local_entity_uid"] or "", j["item_type"])
        if not fim._same_uuid(item.get("cemetery_uid"), j["cemetery_uid"]):
            raise fim.WrongRegistry("Elementul din cloud aparține altui cimitir decât cel al importului local.", "WRONG_REGISTRY")
        if item.get("status") not in ("ACCEPTED", "IMPORTED"):
            fim._journal_note(uid, "CLOUD_" + str(item.get("status") or "UNKNOWN")[:20])
            return fim.ImportResult(uid, fim.LOCAL_COMMITTED, False, False, f"Starea din cloud ({item.get('status')}) nu permite finalizarea; nu s-a modificat nimic.",
                                    j["local_entity_uid"] or "", j["item_type"])
        shutil.rmtree(fim._staging_dir(uid), ignore_errors=True)
        return fim._finalize_cloud(client, item)


# ------------------------------------------------------------------ Reia importul (PREPARING)
def check_resume(client, item_uid: str, item: dict[str, Any] | None = None) -> ResumeVerdict:
    """Verifică dacă un import PREPARING poate fi reluat în siguranță. Nu scrie nimic."""
    uid = fim._uuid(item_uid, "uid item")
    bad: list[str] = []
    j = fim.journal_get(uid)
    if j is None or j["import_state"] != fim.PREPARING:
        return ResumeVerdict(False, ["Elementul nu este în starea PREPARING."])
    with fim._pinned():
        try:
            fim._check_cemetery({"cemetery_uid": j["cemetery_uid"]})
        except fim.FieldImportError as exc:
            return ResumeVerdict(False, [f"Registru: {exc}"])
        if item is None:
            try:
                item = client.get_item(uid)
            except Exception:  # noqa: BLE001
                return ResumeVerdict(False, ["Cloud indisponibil: nu pot verifica starea elementului."])
        if not fim._same_uuid(item.get("cemetery_uid"), j["cemetery_uid"]):
            bad.append("Cimitirul elementului din cloud diferă de cel al jurnalului local.")
        if item.get("status") != "ACCEPTED":
            bad.append(f"Elementul nu este ACCEPTED în cloud (este {item.get('status')}).")
        if bad:
            return ResumeVerdict(False, bad)
        conn = db.get_connection()
        try:
            plan = fim.plan_item(conn, item)
        except fim.ImportConflict as exc:
            return ResumeVerdict(False, [f"Conflict local: {exc}"])
        except fim.FieldImportError as exc:
            return ResumeVerdict(False, [str(exc)])
        finally:
            conn.close()
        if j["local_entity_uid"] and plan.entity_uid and not fim._same_uuid(j["local_entity_uid"], plan.entity_uid):
            bad.append("Entitatea locală din jurnal nu corespunde planului curent.")
        try:
            files = fim.validate_files(item, plan)
        except fim.FieldImportError as exc:
            return ResumeVerdict(False, [f"Media: {exc}"])
        for f in files:
            dest = fim._dest_path(plan.doc_entity[0], f)
            if os.path.exists(dest) and fim._sha_file(dest) != f["sha256"]:
                bad.append("Există deja un fișier local cu același nume și alt SHA-256; nu îl suprascriu.")
        sdir = fim._staging_dir(uid)
        if os.path.lexists(sdir):
            allowed = {f["uid"] + ext for f in files for ext in (".part", fim.MIME_RULES[f["mime"]][1])}
            if os.path.islink(sdir) or not os.path.isdir(sdir) or any(n not in allowed for n in os.listdir(sdir)):
                bad.append("Zona de staging conține fișiere neașteptate; nu reiau importul.")
    return ResumeVerdict(not bad, bad)


def resume_import(client, item_uid: str) -> fim.ImportResult:
    """„Reia importul”: refuză (fără modificări) dacă verificările eșuează; altfel folosește recovery-ul existent (field_import.import_item)."""
    uid = fim._uuid(item_uid, "uid item")
    verdict = check_resume(client, uid)
    if not verdict.can_resume:
        raise fim.ImportBlocked(" ".join(verdict.reasons) or "Reluare nesigură.", "RESUME_UNSAFE")
    before = fim.journal_get(uid)
    try:
        return fim.import_item(client, uid)
    except Exception as exc:
        code = getattr(exc, "code", "") or type(exc).__name__
        if code != "IN_PROGRESS" and before is not None:
            with fim._pinned():
                if fim.journal_get(uid) is None:           # import_item a eliminat rândul PREPARING (nimic comis): îl păstrăm vizibil, cu codul erorii
                    fim._journal_preparing(uid, before["cemetery_uid"], before["item_type"], before["local_entity_uid"] or "")
                fim._journal_note(uid, str(code))
        raise
