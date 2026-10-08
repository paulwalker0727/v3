"""
store.py — FACADE peste stratul de date. Permite selectarea sursei de date
(mai multe baze SQLite = mai multe cimitire, SAU MongoDB) per-request.

Motoare: SQLite (implicit), PostgreSQL (CIMITIR_DB_ENGINE=postgres + DATABASE_URL), MongoDB (LEGACY/DEPRECATED, dezactivat implicit, opt-in cu CIMITIR_ENABLE_MONGO=1; vezi ETAPA10_ELIMINARE_MONGO.md).

Modul de folosire:
    import store as cim
    cim.bind_source(src)      # setează sursa activă pe contextul curent
    cim.list_locuri()         # rutat automat către SQLite sau Mongo

Dispatch-ul se face prin __getattr__: orice funcție necunoscută este preluată
din modulul motorului activ (`database` pentru sqlite, `mongo_store` pentru mongo).
"""
from __future__ import annotations

import os
import json
import contextvars
from typing import Optional, List, Dict, Any

import cimitir_config
import database

DATA_DIR = database.DATA_DIR
_SOURCES_FILE = os.path.join(DATA_DIR, "sources.json")

def _postgres():
    """Importă postgres_store doar la nevoie (psycopg nu e necesar pentru SQLite)."""
    import postgres_store
    return postgres_store


def _mongo():
    """LEGACY/DEPRECATED. Importă mongo_store doar la nevoie (pymongo nu e necesar pentru SQLite)."""
    import mongo_store
    return mongo_store


# Motorul activ: valoarea per-context (bind_source) dacă există, altfel motorul procesului din mediu
# (CIMITIR_DB_ENGINE) — așa firele de fundal (joburi) folosesc și ele PostgreSQL.
_engine_ctx: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar("_engine", default=None)
_source_ctx: "contextvars.ContextVar[dict]" = contextvars.ContextVar("_source", default=None)

POSTGRES_SOURCE = {"id": "cimitir", "name": "Cimitir (PostgreSQL)", "engine": "postgres"}
MONGO_SOURCE = {"id": "mongo", "name": "Bază de date MongoDB", "engine": "mongo",
                "db_name": os.environ.get("DB_NAME", "cimitir_mongo")}


# ---------------------------------------------------------------- registry
def _scan_sqlite_sources() -> List[dict]:
    import cimitir_registry
    binding = cimitir_registry.current_binding()
    if binding is not None:  # registre locale: o singură sursă = registrul activ (id "cimitir" păstrat pentru compatibilitate)
        return [{"id": "cimitir", "name": "Cimitir", "engine": "sqlite", "path": str(binding.database)}]
    out = []
    try:
        for fn in sorted(os.listdir(DATA_DIR)):
            if fn.endswith(".db") and not fn.endswith("-wal") and not fn.endswith("-shm"):
                sid = os.path.splitext(fn)[0]
                out.append({"id": sid, "name": sid, "engine": "sqlite",
                            "path": os.path.join(DATA_DIR, fn)})
    except Exception:
        pass
    return out


def process_engine() -> str:
    """Motorul configurat pentru proces: 'postgres' sau 'sqlite' (CIMITIR_DB_ENGINE)."""
    return cimitir_config.db_engine()


def list_sources() -> List[dict]:
    """PostgreSQL (o singură bază) SAU fișierele SQLite din folderul de date; + MongoDB doar cu CIMITIR_ENABLE_MONGO=1."""
    srcs = [dict(POSTGRES_SOURCE)] if process_engine() == "postgres" else _scan_sqlite_sources()
    if cimitir_config.mongo_enabled():
        srcs.append(dict(MONGO_SOURCE))
    return srcs


def get_source(source_id: str) -> Optional[dict]:
    for s in list_sources():
        if s["id"] == source_id:
            return s
    return None


def create_sqlite_source(name: str) -> dict:
    if process_engine() == "postgres":
        raise RuntimeError("Crearea de baze SQLite nu este disponibilă când CIMITIR_DB_ENGINE=postgres.")
    safe = "".join(c for c in (name or "").strip() if c.isalnum() or c in ("-", "_", " ")).strip().replace(" ", "_")
    if not safe:
        safe = "cimitir_nou"
    path = os.path.join(DATA_DIR, f"{safe}.db")
    n = 1
    while os.path.exists(path):
        path = os.path.join(DATA_DIR, f"{safe}_{n}.db")
        n += 1
    src = {"id": os.path.splitext(os.path.basename(path))[0], "name": name or safe,
           "engine": "sqlite", "path": path}
    ensure_initialized(src)
    bind_source(src)
    database.set_setting("cimitir_nume", name or safe)
    return src


# ---------------------------------------------------------------- binding
def bind_source(source: dict) -> None:
    _source_ctx.set(source)
    if source and source.get("engine") == "mongo":
        _engine_ctx.set("mongo")
        _mongo().set_active_db_name(source.get("db_name") or "cimitir_mongo")
    elif (source and source.get("engine") == "postgres") or process_engine() == "postgres":
        _engine_ctx.set("postgres")
    else:
        _engine_ctx.set("sqlite")
        import cimitir_registry
        if source and source.get("path") and cimitir_registry.current_binding() is None:
            database.set_active_db_path(source["path"])


def bind_source_id(source_id: str) -> Optional[dict]:
    src = get_source(source_id)
    if src:
        bind_source(src)
    return src


def current_source() -> Optional[dict]:
    return _source_ctx.get()


def active_engine() -> str:
    return _engine_ctx.get() or process_engine()


def _mod():
    eng = active_engine()
    if eng == "mongo":
        return _mongo()
    if eng == "postgres":
        return _postgres()
    return database


def ensure_initialized(source: dict) -> None:
    bind_source(source)
    _mod().init_db()  # PostgreSQL: aplică migrațiile lipsă (cache per proces)
    try:
        import auth
        auth.init_auth()
    except Exception:
        pass


def describe_source(source: dict) -> dict:
    """Metadate pentru cardul din selectorul de baze de date."""
    info = {"id": source["id"], "engine": source["engine"], "name": source.get("name") or source["id"],
            "available": True, "locuri": 0, "contracte": 0, "cereri_noi": 0, "error": ""}
    try:
        if source["engine"] == "mongo":
            if not _mongo().ping():
                info["available"] = False
                info["error"] = "MongoDB indisponibil"
                return info
        elif source["engine"] == "postgres":
            if not _postgres().ping():
                info["available"] = False
                info["error"] = "PostgreSQL indisponibil"
                return info
        ensure_initialized(source)
        info["name"] = _mod().get_setting("cimitir_nume") or info["name"]
        info["locuri"] = len(_mod().list_locuri())
        info["contracte"] = len(_mod().load_contracte())
        info["cereri_noi"] = _mod().count_cereri_noi()
    except Exception as e:
        info["available"] = False
        info["error"] = str(e)
    return info


# ---------------------------------------------------------------- dispatch
def __getattr__(name: str):
    """Rutează orice funcție de date către motorul activ."""
    mod = _mod()
    try:
        return getattr(mod, name)
    except AttributeError:
        raise AttributeError(f"store nu are atributul '{name}'")
