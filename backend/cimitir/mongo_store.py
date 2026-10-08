"""
mongo_store.py — LEGACY / DEPRECATED (Etapa 10): neutilizat, dezactivat implicit (CIMITIR_ENABLE_MONGO=0).
PostgreSQL este motorul online; acest modul se șterge după criteriile din ETAPA10_ELIMINARE_MONGO.md. Nu adăuga funcționalități.

mongo_store.py — implementare MongoDB a stratului de date, oglindind API-ul din
`database.py` (SQLite). Folosită ca sursă alternativă selectabilă din portal.

Fișierele (documente/poze) rămân pe disc (prin database.import_media_file);
în Mongo se păstrează doar calea + metadatele, exact ca în SQLite.
"""
from __future__ import annotations

import os
import re
import json
import sqlite3
import contextvars
import warnings
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from pymongo import MongoClient

import database as _sql  # pentru import_media_file + seed din SQLite

warnings.warn("mongo_store este DEPRECATED (se va elimina); folosește PostgreSQL.", DeprecationWarning, stacklevel=2)

_MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
_client: Optional[MongoClient] = None
_db_name_ctx: "contextvars.ContextVar[str]" = contextvars.ContextVar("_mongo_db_name", default="cimitir_mongo")


def set_active_db_name(name: str) -> None:
    _db_name_ctx.set(name or "cimitir_mongo")


def _client_get() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(_MONGO_URL, serverSelectionTimeoutMS=4000)
    return _client


def _db():
    return _client_get()[_db_name_ctx.get()]


def _next_id(name: str) -> int:
    doc = _db().counters.find_one_and_update(
        {"_id": name}, {"$inc": {"seq": 1}}, upsert=True, return_document=True)
    return int(doc["seq"])


def _clean(doc: Optional[dict]) -> Dict[str, Any]:
    if not doc:
        return {}
    doc = dict(doc)
    doc.pop("_id", None)
    for k, v in list(doc.items()):
        if v is None:
            doc[k] = ""
    return doc


def _contains(field_val, q: str) -> bool:
    return q.lower() in str(field_val or "").lower()


# =============================================================================
# INIT + SEED (din SQLite demo la prima folosire)
# =============================================================================
_MONGO_COLLS = ["locuri_de_veci", "sectoare", "concesionari", "contracte", "inhumati",
                "plati", "amenajari_avize", "documents", "cereri_concesionare",
                "settings", "users", "counters"]


def ping() -> bool:
    try:
        _client_get().admin.command("ping")
        return True
    except Exception:
        return False


def init_db() -> None:
    """Creează colecțiile + indexuri."""
    db = _db()
    db.locuri_de_veci.create_index("cod_loc", unique=True)
    db.inhumati.create_index("cod_loc")
    db.contracte.create_index("cod_loc")
    db.plati.create_index("cod_loc")


def init_auth() -> None:
    """Seed conturi implicite (admin/admin123, user/user123) dacă lipsesc."""
    if _db().users.count_documents({}) == 0:
        try:
            from security.password_kdf import hash_password as _h
        except Exception:
            from auth import _hash as _h  # type: ignore
        now = datetime.now().isoformat(timespec="seconds")
        for u, p, r in [("admin", "admin123", "admin"), ("user", "user123", "user")]:
            _db().users.insert_one({"id": _next_id("users"), "username": u,
                                    "password_hash": _h(p), "role": r,
                                    "created_at": now, "last_login": None})


def _seed_from_sqlite(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    db = _db()
    table_map = {
        "locuri_de_veci": "locuri_de_veci", "sectoare": "sectoare",
        "concesionari": "concesionari", "contracte": "contracte",
        "inhumati": "inhumati", "plati": "plati",
        "amenajari_avize": "amenajari_avize", "documents": "documents",
        "cereri_concesionare": "cereri_concesionare", "settings": "settings",
    }
    for tbl, coll in table_map.items():
        try:
            rows = conn.execute(f"SELECT * FROM {tbl}").fetchall()
        except Exception:
            continue
        maxid = 0
        for r in rows:
            d = dict(r)
            if "id" in d and isinstance(d["id"], int):
                maxid = max(maxid, d["id"])
            db[coll].insert_one(d)
        if maxid:
            db.counters.update_one({"_id": coll}, {"$set": {"seq": maxid}}, upsert=True)
    conn.close()


# =============================================================================
# LOCURI
# =============================================================================
def map_rows() -> List[Dict[str, Any]]:
    return [_clean(d) for d in _db().locuri_de_veci.find({}, {
        "cod_loc": 1, "points": 1, "color": 1, "sector": 1, "parcela": 1, "rand": 1,
        "numar": 1, "stare": 1, "disponibilitate": 1, "ocupat": 1, "tip": 1,
        "capacitate": 1, "suprafata": 1})]


def sector_rows() -> List[Dict[str, Any]]:
    return [_clean(d) for d in _db().sectoare.find({}, {"id": 1, "points": 1, "color": 1})]


def load_loc_by_cod(cod_loc: str) -> Optional[Dict[str, Any]]:
    d = _db().locuri_de_veci.find_one({"cod_loc": cod_loc})
    return _clean(d) if d else None


def get_loc_id_by_cod(cod_loc: str) -> Optional[int]:
    d = _db().locuri_de_veci.find_one({"cod_loc": cod_loc}, {"id": 1})
    return d.get("id") if d else None


def list_locuri() -> List[Dict[str, Any]]:
    return [_clean(d) for d in _db().locuri_de_veci.find().sort("cod_loc", 1)]


def search_locuri(filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    filters = filters or {}
    out = []
    for d in _db().locuri_de_veci.find():
        ok = all(_contains(d.get(f), str(v)) for f, v in filters.items())
        if ok:
            out.append(_clean(d))
    return out


def save_plot_to_db(cod_loc: str, points, color: str) -> Optional[int]:
    db = _db()
    pts = json.dumps(points) if not isinstance(points, str) else points
    ex = db.locuri_de_veci.find_one({"cod_loc": cod_loc})
    if ex:
        db.locuri_de_veci.update_one({"cod_loc": cod_loc}, {"$set": {"points": pts, "color": color}})
        return ex.get("id")
    nid = _next_id("locuri_de_veci")
    db.locuri_de_veci.insert_one({"id": nid, "cod_loc": cod_loc, "points": pts, "color": color, "ocupat": 0})
    return nid


def save_loc_metadata(cod_loc: str, metadata: Dict[str, Any]) -> bool:
    db = _db()
    if not db.locuri_de_veci.find_one({"cod_loc": cod_loc}):
        db.locuri_de_veci.insert_one({"id": _next_id("locuri_de_veci"), "cod_loc": cod_loc})
    db.locuri_de_veci.update_one({"cod_loc": cod_loc}, {"$set": dict(metadata)})
    return True


def delete_plot(cod_loc: str) -> bool:
    return _db().locuri_de_veci.delete_one({"cod_loc": cod_loc}).deleted_count > 0


# =============================================================================
# SECTOARE
# =============================================================================
def load_all_sectors() -> List[Dict[str, Any]]:
    return [_clean(d) for d in _db().sectoare.find()]


def save_sector_to_db(sector_id, points, color: str) -> bool:
    db = _db()
    pts = json.dumps(points) if not isinstance(points, str) else points
    db.sectoare.update_one({"id": sector_id}, {"$set": {"points": pts, "color": color}}, upsert=True)
    return True


def delete_sector_from_db(sector_id) -> bool:
    return _db().sectoare.delete_one({"id": sector_id}).deleted_count > 0


# =============================================================================
# CONCESIONARI
# =============================================================================
def load_concesionari(_filters=None) -> List[Dict[str, Any]]:
    return [_clean(d) for d in _db().concesionari.find().sort("nume", 1)]


def get_concesionar(concesionar_id: int) -> Optional[Dict[str, Any]]:
    d = _db().concesionari.find_one({"id": int(concesionar_id)})
    return _clean(d) if d else None


def save_concesionar(data: Dict[str, Any], concesionar_id: Optional[int] = None) -> Optional[int]:
    db = _db()
    data = {k: v for k, v in data.items() if k != "id"}
    if concesionar_id:
        db.concesionari.update_one({"id": int(concesionar_id)}, {"$set": data})
        return int(concesionar_id)
    nid = _next_id("concesionari")
    db.concesionari.insert_one({"id": nid, **data})
    return nid


def delete_concesionar(concesionar_id: int) -> bool:
    return _db().concesionari.delete_one({"id": int(concesionar_id)}).deleted_count > 0


# =============================================================================
# CONTRACTE
# =============================================================================
def load_contracte(extra_where: str = "") -> List[Dict[str, Any]]:
    return [_clean(d) for d in _db().contracte.find().sort("id", 1)]


def get_contract(contract_id: int) -> Optional[Dict[str, Any]]:
    d = _db().contracte.find_one({"id": int(contract_id)})
    return _clean(d) if d else None


def save_contract(data: Dict[str, Any], contract_id: Optional[int] = None) -> Optional[int]:
    db = _db()
    data = {k: v for k, v in data.items() if k != "id"}
    if contract_id:
        db.contracte.update_one({"id": int(contract_id)}, {"$set": data})
        return int(contract_id)
    nid = _next_id("contracte")
    db.contracte.insert_one({"id": nid, **data})
    return nid


def delete_contract(contract_id: int) -> bool:
    return _db().contracte.delete_one({"id": int(contract_id)}).deleted_count > 0


def _parse_date(s: str):
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:len(fmt) + 2] if "T" in fmt else s, fmt)
        except Exception:
            continue
    return None


def expiring_contracts(days: int = 30) -> List[Dict[str, Any]]:
    limit = datetime.now() + timedelta(days=int(days))
    out = []
    for d in _db().contracte.find():
        raw = d.get("data_sfarsit") or ""
        dt = _parse_date(raw)
        if dt and dt <= limit:
            c = _clean(d)
            c["_expira"] = raw
            c["_date_col"] = "data_sfarsit"
            out.append(c)
    out.sort(key=lambda x: x.get("_expira") or "")
    return out


# =============================================================================
# INHUMATI
# =============================================================================
def save_inhumat(cod_loc: str, data: Dict[str, Any], inhumat_id: Optional[int] = None) -> Optional[int]:
    db = _db()
    data = {k: v for k, v in data.items() if k != "id"}
    data["cod_loc"] = cod_loc
    if inhumat_id:
        db.inhumati.update_one({"id": int(inhumat_id)}, {"$set": data})
        return int(inhumat_id)
    nid = _next_id("inhumati")
    db.inhumati.insert_one({"id": nid, **data})
    return nid


def load_inhumati_for_loc(cod_loc: str) -> List[Dict[str, Any]]:
    return [_clean(d) for d in _db().inhumati.find({"cod_loc": cod_loc}).sort("id", 1)]


def load_all_inhumati(filters: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    filters = filters or {}
    out = []
    for d in _db().inhumati.find():
        if all(_contains(d.get(f), str(v)) for f, v in filters.items()):
            out.append(_clean(d))
    return out


def get_inhumat(inhumat_id: int) -> Optional[Dict[str, Any]]:
    d = _db().inhumati.find_one({"id": int(inhumat_id)})
    return _clean(d) if d else None


def delete_inhumat(inhumat_id: int) -> bool:
    return _db().inhumati.delete_one({"id": int(inhumat_id)}).deleted_count > 0


# =============================================================================
# AVIZE
# =============================================================================
def list_avize_for_loc(cod_loc: str) -> List[Dict[str, Any]]:
    return [_clean(d) for d in _db().amenajari_avize.find({"cod_loc": cod_loc}).sort("id", 1)]


def add_aviz(cod_loc: str, nr: str, data_aviz: Optional[str], lucrare: str = "") -> Optional[int]:
    nid = _next_id("amenajari_avize")
    _db().amenajari_avize.insert_one({"id": nid, "cod_loc": cod_loc, "nr_aviz": nr,
                                      "data_aviz": data_aviz or "", "lucrare": lucrare,
                                      "created_at": datetime.now().strftime("%d.%m.%Y %H:%M")})
    return nid


def delete_aviz(aviz_id: int) -> bool:
    return _db().amenajari_avize.delete_one({"id": int(aviz_id)}).deleted_count > 0


# =============================================================================
# PLATI
# =============================================================================
def add_plata(cod_loc, suma, data_plata, tip="", chitanta_nr="", observatii="", contract_id=None) -> Optional[int]:
    nid = _next_id("plati")
    _db().plati.insert_one({"id": nid, "cod_loc": cod_loc, "contract_id": contract_id,
                            "suma": float(suma or 0), "data_plata": data_plata, "tip": tip,
                            "chitanta_nr": chitanta_nr, "observatii": observatii,
                            "created_at": datetime.now().strftime("%d.%m.%Y %H:%M")})
    return nid


def list_plati(cod_loc: Optional[str] = None) -> List[Dict[str, Any]]:
    q = {"cod_loc": cod_loc} if cod_loc else {}
    return [_clean(d) for d in _db().plati.find(q).sort("id", -1)]


def delete_plata(plata_id: int) -> bool:
    return _db().plati.delete_one({"id": int(plata_id)}).deleted_count > 0


def total_plati(cod_loc: Optional[str] = None) -> float:
    q = {"cod_loc": cod_loc} if cod_loc else {}
    return float(sum(float(d.get("suma") or 0) for d in _db().plati.find(q, {"suma": 1})))


# =============================================================================
# DOCUMENTS
# =============================================================================
def add_document(entity_type: str, entity_id: Any, doc_type: str, src_path: str,
                 caption: str = "", nr_evidenta: str = "", copy: bool = True) -> Optional[int]:
    path = _sql.import_media_file(src_path, entity_type) if copy else src_path
    if not path:
        return None
    nid = _next_id("documents")
    _db().documents.insert_one({"id": nid, "entity_type": entity_type, "entity_id": str(entity_id),
                                "doc_type": doc_type or "", "file_path": path, "caption": caption or "",
                                "nr_evidenta": nr_evidenta or "",
                                "date_added": datetime.now().strftime("%d.%m.%Y %H:%M")})
    return nid


def list_documents(entity_type: str, entity_id: Any, doc_type: Optional[str] = None) -> List[Dict[str, Any]]:
    q = {"entity_type": entity_type, "entity_id": str(entity_id)}
    if doc_type:
        q["doc_type"] = doc_type
    return [_clean(d) for d in _db().documents.find(q).sort("id", 1)]


def delete_document(doc_id: int) -> bool:
    d = _db().documents.find_one({"id": int(doc_id)})
    if d and d.get("file_path") and os.path.exists(d["file_path"]):
        try:
            os.remove(d["file_path"])
        except Exception:
            pass
    return _db().documents.delete_one({"id": int(doc_id)}).deleted_count > 0


def get_document_path(doc_id: int) -> Optional[str]:
    d = _db().documents.find_one({"id": int(doc_id)}, {"file_path": 1})
    return d.get("file_path") if d else None


# =============================================================================
# SETTINGS
# =============================================================================
def get_setting(key: str) -> Optional[str]:
    d = _db().settings.find_one({"key": key})
    return d.get("value") if d else None


def set_setting(key: str, value: str) -> None:
    _db().settings.update_one({"key": key}, {"$set": {"key": key, "value": value}}, upsert=True)


# =============================================================================
# CERERI CONCESIONARE
# =============================================================================
_CERERE_FIELDS = ["cod_loc", "nume", "prenume", "cnp", "bi_ci", "adresa",
                  "telefon", "email", "grad_rudenie", "observatii"]


def add_cerere_concesionare(data: Dict[str, Any]) -> Optional[int]:
    nid = _next_id("cereri_concesionare")
    doc = {"id": nid, "created_at": datetime.now().strftime("%d.%m.%Y %H:%M"), "status": "nou",
           "reviewed_at": "", "review_note": "", "concesionar_id": None}
    for k in _CERERE_FIELDS:
        v = data.get(k) or ""
        doc[k] = v.strip() if isinstance(v, str) else v
    _db().cereri_concesionare.insert_one(doc)
    return nid


def list_cereri_concesionare(status: Optional[str] = None) -> List[Dict[str, Any]]:
    q = {"status": status} if status else {}
    return [_clean(d) for d in _db().cereri_concesionare.find(q).sort("id", -1)]


def get_cerere_concesionare(cerere_id: int) -> Optional[Dict[str, Any]]:
    d = _db().cereri_concesionare.find_one({"id": int(cerere_id)})
    return _clean(d) if d else None


def set_cerere_status(cerere_id: int, status: str, review_note: str = "",
                      concesionar_id: Optional[int] = None) -> bool:
    r = _db().cereri_concesionare.update_one({"id": int(cerere_id)}, {"$set": {
        "status": status, "review_note": review_note or "", "concesionar_id": concesionar_id,
        "reviewed_at": datetime.now().strftime("%d.%m.%Y %H:%M")}})
    return r.matched_count > 0


def count_cereri_noi() -> int:
    return int(_db().cereri_concesionare.count_documents({"status": "nou"}))


# =============================================================================
# USERS
# =============================================================================
def user_get(username: str) -> Optional[Dict[str, Any]]:
    d = _db().users.find_one({"username": (username or "").strip()})
    if not d:
        return None
    return {"id": d["id"], "username": d["username"], "password_hash": d["password_hash"], "role": d["role"]}


def user_touch_login(user_id: int) -> None:
    _db().users.update_one({"id": int(user_id)}, {"$set": {"last_login": datetime.now().isoformat(timespec="seconds")}})


def user_set_hash(user_id: int, password_hash: str) -> bool:
    return _db().users.update_one({"id": int(user_id)}, {"$set": {"password_hash": password_hash}}).matched_count > 0


def users_list() -> List[Dict[str, Any]]:
    return [{"id": d["id"], "username": d["username"], "role": d["role"],
             "created_at": d.get("created_at", ""), "last_login": d.get("last_login")}
            for d in _db().users.find().sort("username", 1)]


def users_count() -> int:
    return int(_db().users.count_documents({}))


def user_insert(username: str, password_hash: str, role: str, created_at: str) -> Optional[int]:
    if _db().users.find_one({"username": username.strip()}):
        return None
    nid = _next_id("users")
    _db().users.insert_one({"id": nid, "username": username.strip(), "password_hash": password_hash,
                            "role": role, "created_at": created_at, "last_login": None})
    return nid


def user_delete(user_id: int) -> bool:
    return _db().users.delete_one({"id": int(user_id)}).deleted_count > 0
