"""Audit complet pentru locuri, concesionari, contracte, înhumați și plăți.

Fiecare creare / modificare / ștergere produce un rând în `audit_log`:
    actor_type, actor      -> cine (client MAIN pentru sincronizare, utilizatorul pentru panoul web)
    action                 -> <entitate>.create | <entitate>.update | <entitate>.delete
    target                 -> <entitate>:<uid>
    ts                     -> momentul
    detail (JSON)          -> {"entity","uid","version","old":{...},"new":{...}}
                              la modificare: doar câmpurile schimbate; la creare old=null; la ștergere new=null.
Nu se înregistrează niciodată parole, token-uri, chei, JWT, DATABASE_URL sau conținut de documente (câmpurile cu astfel de nume
sunt eliminate, iar valorile lungi sunt trunchiate).
"""
from __future__ import annotations

import contextvars
import functools
import inspect
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, Optional

log = logging.getLogger("cimitir.audit")

# (actor_type, actor) pentru cererea curentă; setat de current_user() pentru panoul web
ACTOR: contextvars.ContextVar = contextvars.ContextVar("cimitir_audit_actor", default=("server", "necunoscut"))

_SENSITIVE = re.compile(r"password|passwd|parola|token|secret|jwt|database_url|api_?key|authorization|cookie|content|continut|blob|bytes", re.I)
_SKIP = {"id", "change_seq", "modified_at", "created_at", "updated_at", "uid"}
_MAX_VALUE = 500


def clean(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    out: Dict[str, Any] = {}
    for k, v in dict(row).items():
        if k in _SKIP or _SENSITIVE.search(str(k)):
            continue
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False, default=str)
        elif v is not None and not isinstance(v, (int, float, bool, str)):
            v = str(v)
        if isinstance(v, str) and len(v) > _MAX_VALUE:
            v = v[:_MAX_VALUE] + "…"
        out[k] = v
    return out


def _diff(old: Optional[dict], new: Optional[dict]):
    if old is None or new is None:
        return old, new
    keys = [k for k in set(old) | set(new) if old.get(k) != new.get(k) and k != "version"]
    return {k: old.get(k) for k in sorted(keys)}, {k: new.get(k) for k in sorted(keys)}


def record(conn, actor_type: str, actor: str, entity: str, uid: str, old: Optional[dict], new: Optional[dict],
           version: Optional[int] = None) -> bool:
    """Scrie un rând de audit în `conn` (aceeași tranzacție ca modificarea, dacă `conn` e într-una)."""
    old, new = clean(old), clean(new)
    if old is None and new is None:
        return False
    action = "create" if old is None else ("delete" if new is None else "update")
    if action == "update":
        old, new = _diff(old, new)
        if not new:
            return False        # nicio schimbare efectivă
    detail = json.dumps({"entity": entity, "uid": uid, "version": version, "old": old, "new": new}, ensure_ascii=False, default=str)
    conn.execute("INSERT INTO audit_log (ts, actor_type, actor, action, target, detail) VALUES (?,?,?,?,?,?)",
                 (datetime.now().isoformat(timespec="seconds"), actor_type, str(actor)[:80], f"{entity}.{action}", f"{entity}:{uid}"[:120], detail))
    return True


# ---------------------------------------------------------------- panoul web (funcțiile din postgres_store)
# nume funcție -> (tabel, (coloană, parametru) înainte, (coloană, parametru|'@result') după)
_SPECS = {
    "save_concesionar": ("concesionari", ("id", "concesionar_id"), ("id", "@result")),
    "delete_concesionar": ("concesionari", ("id", "concesionar_id"), None),
    "save_contract": ("contracte", ("id", "contract_id"), ("id", "@result")),
    "delete_contract": ("contracte", ("id", "contract_id"), None),
    "save_inhumat": ("inhumati", ("id", "inhumat_id"), ("id", "@result")),
    "delete_inhumat": ("inhumati", ("id", "inhumat_id"), None),
    "add_plata": ("plati", None, ("id", "@result")),
    "delete_plata": ("plati", ("id", "plata_id"), None),
    "save_plot_to_db": ("locuri_de_veci", ("cod_loc", "cod_loc"), ("cod_loc", "cod_loc")),
    "save_loc_metadata": ("locuri_de_veci", ("cod_loc", "cod_loc"), ("cod_loc", "cod_loc")),
    "delete_plot": ("locuri_de_veci", ("cod_loc", "cod_loc"), None),
}


def _row(ps, table: str, col: str, val: Any) -> Optional[Dict[str, Any]]:
    if val is None:
        return None
    conn = ps.get_connection()
    try:
        r = conn.execute(f"SELECT * FROM {table} WHERE {col} = ?", (val,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def _wrap(ps, name: str, spec) -> None:
    table, pre, post = spec
    orig = getattr(ps, name)
    sig = inspect.signature(orig)

    @functools.wraps(orig)
    def inner(*args, **kwargs):
        try:
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            before = _row(ps, table, pre[0], bound.arguments.get(pre[1])) if pre else None
        except Exception:  # noqa: BLE001 - auditul nu trebuie să blocheze operațiunea
            bound, before = None, None
        result = orig(*args, **kwargs)
        try:
            if bound is None or (not result and result != 0 and post is not None and post[1] == "@result"):
                return result
            after = None
            if post:
                key = result if post[1] == "@result" else bound.arguments.get(post[1])
                after = _row(ps, table, post[0], key)
            if before is None and after is None:
                return result
            if post is None and not result:       # ștergere eșuată
                return result
            row = after or before
            actor_type, actor = ACTOR.get()
            conn = ps.get_connection()
            try:
                record(conn, actor_type, actor, table, row.get("uid", ""), before, after, (after or {}).get("version"))
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            log.warning("audit entitate eșuat (%s): %s", name, type(exc).__name__)
        return result

    setattr(ps, name, inner)


def install() -> bool:
    """Activează auditul pentru modificările făcute din panoul web (funcțiile PostgreSQL). Idempotent."""
    try:
        import postgres_store as ps
    except Exception:  # noqa: BLE001 - mod SQLite fără psycopg
        return False
    for name, spec in _SPECS.items():
        fn = getattr(ps, name, None)
        if fn is None or getattr(fn, "_audited", False):
            continue
        _wrap(ps, name, spec)
        getattr(ps, name)._audited = True
    return True
