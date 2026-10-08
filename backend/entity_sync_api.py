"""Sincronizare pe entități (Etapa 6): `/api/sync/entities/*`. PostgreSQL = sursa principală.

Reutilizează autentificarea, evidența clienților și `sync_ops` din `sync_api.py` (cheie `X-Sync-Key`, `op_id`).

  POST /api/sync/entities/push      {ops:[{op_id, entity, uid, action:upsert|delete, base_version, data}]}
      -> results:[{op_id, status: applied|conflict|retry|rejected, version, updated_at, duplicate, reason, server}]
  GET  /api/sync/entities/pull      ?entity=&since=&limit=   -> changes incrementale (rânduri + ștergeri), cursor
  GET  /api/sync/entities/conflicts ?open=1                   -> conflictele detectate de server
  POST /api/sync/entities/conflicts/{op_id}/resolve           -> operatorul a decis (server|mine)

Reguli:
  * `base_version` = versiunea serverului pe care clientul și-a bazat modificarea. Dacă rândul de pe server are altă
    versiune (altcineva l-a modificat între timp) => CONFLICT: nimic nu se suprascrie, cazul se înregistrează în
    `sync_conflicts` și se întoarce starea serverului.
  * Idempotent: răspunsul fiecărui `op_id` se memorează în `sync_ops.response`; o reluare întoarce același răspuns.
  * Versiunea și `change_seq` sunt puse de triggere PostgreSQL (003_entity_sync.sql), deci și editările din panoul web
    (/admin) sunt versionate și ajung la clienți.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Request
from psycopg import errors as psycopg_errors

import cimitir_config
import cimitir_entities as ents
import audit_entities
import store as cim
import sync_api

router = APIRouter(prefix="/api/sync/entities")
MAX_OPS = 200
MAX_PULL = 1000


def _guard(request: Request) -> str:
    sid = sync_api._auth(request)
    if cimitir_config.db_engine() != "postgres":
        raise HTTPException(status_code=501, detail="Sincronizarea pe entități cere serverul pe PostgreSQL.")
    return sid


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


# ------------------------------------------------------------------ citire / serializare
def _select_sql(entity: str) -> str:
    sp = ents.ENTITIES[entity]
    cols = ", ".join(f"t.{c}" for c in sp["cols"])
    fks = "".join(f", (SELECT p.uid FROM {parent} p WHERE p.id = t.{col}) AS {wire}"
                  for col, (parent, wire) in sp["fks"].items())
    return f"SELECT t.id, t.uid, t.version, t.change_seq, t.modified_at, {cols}{fks} FROM {entity} t WHERE {cim.tenant_scope('t')}"


def _row_to_wire(entity: str, row) -> Dict[str, Any]:
    d = dict(row)
    data = {c: _jsonable(d.get(c)) for c in ents.ENTITIES[entity]["cols"]}
    for wire in ents.fk_wire_names(entity):
        data[wire] = d.get(wire)
    return {"uid": d["uid"], "version": int(d["version"] or 0), "seq": int(d["change_seq"] or 0),
            "updated_at": _jsonable(d["modified_at"]), "deleted": False, "data": data}


def _fetch(conn, entity: str, uid: str, lock: bool = False):
    sql = _select_sql(entity) + " AND t.uid = ?" + (" FOR UPDATE OF t" if lock else "")
    return conn.execute(sql, (uid,)).fetchone()


def _tombstone(conn, entity: str, uid: str):
    return conn.execute(f"SELECT version, change_seq FROM sync_tombstones WHERE entity=? AND uid=? AND {cim.tenant_scope()}",
                        (entity, uid)).fetchone()


def _resolve_fks(conn, entity: str, data: Dict[str, Any]):
    """uid părinte -> id local al serverului. Întoarce (valori, None) sau (None, motiv) dacă părintele lipsește."""
    values: Dict[str, Any] = {}
    for col, (parent, wire) in ents.ENTITIES[entity]["fks"].items():
        uid = data.get(wire)
        if not uid:
            values[col] = None
            continue
        row = conn.execute(f"SELECT id FROM {parent} WHERE uid=? AND {cim.tenant_scope()}", (uid,)).fetchone()
        if row is None:
            return None, f"missing_parent:{parent}:{uid}"
        values[col] = int(row["id"])
    return values, None


def _db_value(kind: str, value: Any) -> Any:
    return json.dumps(value if value is not None else []) if kind == "j" else value


# ------------------------------------------------------------------ aplicarea unei operațiuni
def _result(op_id: str, status: str, **extra: Any) -> Dict[str, Any]:
    return {"op_id": op_id, "status": status, **extra}


def _record_conflict(conn, op: Dict[str, Any], client_id: str, reason: str, server: Optional[Dict[str, Any]],
                     server_version: Optional[int], client_data: Optional[Dict[str, Any]]) -> None:
    conn.execute(
        "INSERT INTO sync_conflicts (op_id, entity, uid, action, reason, client_id, base_version, server_version, "
        "client_data, server_data) VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT (op_id) DO NOTHING",
        (op["op_id"], op["entity"], op["uid"], op["action"], reason, client_id, op["base_version"], server_version,
         json.dumps(client_data) if client_data is not None else None,
         json.dumps(server, default=str) if server is not None else None))


def _last_writer_is(conn, client_id: str, entity: str, uid: str, version: int) -> bool:
    """True dacă versiunea curentă a rândului a fost produsă chiar de acest client (op confirmat). Atunci modificarea nouă
    continuă lucrul propriu (de ex. confirmarea anterioară s-a pierdut pe drum): nu există niciun alt autor, deci nu e conflict."""
    if not client_id or client_id == "necunoscut":
        return False
    row = conn.execute(
        "SELECT 1 FROM sync_ops WHERE kind='entity' AND client_id=? AND response IS NOT NULL "
        "AND (response::jsonb->>'entity')=? AND (response::jsonb->>'uid')=? AND (response::jsonb->>'status')='applied' "
        "AND (response::jsonb->>'version')::int = ? LIMIT 1", (client_id, entity, uid, version)).fetchone()
    return row is not None


def _apply(conn, op: Dict[str, Any], client_id: str) -> Dict[str, Any]:
    entity, uid, action, base = op["entity"], op["uid"], op["action"], op["base_version"]
    sp = ents.ENTITIES[entity]
    existing = _fetch(conn, entity, uid, lock=True)
    server = _row_to_wire(entity, existing) if existing is not None else None

    def conflict(reason: str, version: Optional[int], snap: Optional[Dict[str, Any]]):
        _record_conflict(conn, op, client_id, reason, snap, version, op.get("data"))
        return _result(op["op_id"], "conflict", reason=reason, server_version=version, server=snap)

    if action == "delete":
        if existing is None:
            tomb = _tombstone(conn, entity, uid)
            return _result(op["op_id"], "applied", version=int(tomb["version"]) if tomb else base, deleted=True)
        if server["version"] != base and not _last_writer_is(conn, client_id, entity, uid, server["version"]):
            return conflict("version_mismatch", server["version"], server)
        conn.execute(f"DELETE FROM {entity} WHERE uid=? AND {cim.tenant_scope()}", (uid,))
        tomb = _tombstone(conn, entity, uid)
        return _result(op["op_id"], "applied", version=int(tomb["version"]) if tomb else base + 1, deleted=True)

    data = ents.clean_data(entity, op.get("data") or {})
    fk_values, missing = _resolve_fks(conn, entity, data)
    if missing:
        return _result(op["op_id"], "retry", reason=missing)

    if existing is None:
        tomb = _tombstone(conn, entity, uid)
        if tomb is not None and base > 0:
            return conflict("deleted_on_server", int(tomb["version"]), None)
        nk = sp.get("natural_key")
        if nk and data.get(nk):
            other = conn.execute(_select_sql(entity) + f" AND t.{nk} = ?", (data[nk],)).fetchone()
            if other is not None:
                snap = _row_to_wire(entity, other)
                return conflict("natural_key", snap["version"], snap)
        cols = list(sp["cols"]) + list(fk_values)
        vals = [_db_value(sp["cols"][c], data[c]) for c in sp["cols"]] + list(fk_values.values())
        marks = ["CAST(? AS jsonb)" if sp["cols"].get(c) == "j" else "?" for c in cols]
        row = conn.execute(f"INSERT INTO {entity} (uid, {', '.join(cols)}) VALUES (?, {', '.join(marks)}) "
                           "RETURNING version, modified_at", (uid, *vals)).fetchone()
        return _result(op["op_id"], "applied", version=int(row["version"]), updated_at=_jsonable(row["modified_at"]),
                       created=True)

    # rând existent
    if ents.same_data(entity, data, server["data"]):   # conținut identic: nimic de scris, clientul adoptă versiunea serverului
        return _result(op["op_id"], "applied", version=server["version"], updated_at=server["updated_at"], adopted=True)
    if server["version"] != base and not _last_writer_is(conn, client_id, entity, uid, server["version"]):
        return conflict("already_exists" if base == 0 else "version_mismatch", server["version"], server)
    sets = [f"{c} = {'CAST(? AS jsonb)' if sp['cols'][c] == 'j' else '?'}" for c in sp["cols"]]
    sets += [f"{c} = ?" for c in fk_values]
    vals = [_db_value(sp["cols"][c], data[c]) for c in sp["cols"]] + list(fk_values.values())
    row = conn.execute(f"UPDATE {entity} SET {', '.join(sets)} WHERE uid = ? AND {cim.tenant_scope()} RETURNING version, modified_at",
                       (*vals, uid)).fetchone()
    return _result(op["op_id"], "applied", version=int(row["version"]), updated_at=_jsonable(row["modified_at"]))


def _validate(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("operațiune invalidă")
    op_id, entity, uid = str(raw.get("op_id") or ""), str(raw.get("entity") or ""), str(raw.get("uid") or "")
    action = str(raw.get("action") or "")
    if not op_id or not uid or entity not in ents.ENTITIES or action not in ents.VALID_ACTIONS:
        raise ValueError("op_id/uid/entity/action invalide")
    try:
        base = int(raw.get("base_version") or 0)
    except (TypeError, ValueError):
        raise ValueError("base_version invalid")
    if base < 0:
        raise ValueError("base_version invalid")
    return {"op_id": op_id[:80], "entity": entity, "uid": uid[:80], "action": action, "base_version": base,
            "data": raw.get("data") if isinstance(raw.get("data"), dict) else {}}


def process_op(raw: Any, client_id: str) -> Dict[str, Any]:
    """Aplică O operațiune, atomic și idempotent. Nu ridică excepții de business (le întoarce ca status)."""
    try:
        op = _validate(raw)
    except ValueError as exc:
        return _result(str((raw or {}).get("op_id") if isinstance(raw, dict) else ""), "rejected", reason=str(exc))
    conn = cim.get_connection()
    try:
        prev = conn.execute("SELECT response FROM sync_ops WHERE op_id=?", (op["op_id"],)).fetchone()
        if prev is not None and prev["response"]:
            return {**json.loads(prev["response"]), "duplicate": True}
        conn.execute("BEGIN")
        try:
            _before = _fetch(conn, op["entity"], op["uid"], lock=True)
            _old = _row_to_wire(op["entity"], _before)["data"] if _before is not None else None
            result = _apply(conn, op, client_id)
            result["entity"], result["uid"] = op["entity"], op["uid"]
            if result["status"] == "applied" and not result.get("adopted"):
                try:    # audit în aceeași tranzacție (atomic cu modificarea); nu blochează operațiunea dacă eșuează
                    _after = _fetch(conn, op["entity"], op["uid"])
                    _new = _row_to_wire(op["entity"], _after)["data"] if _after is not None else None
                    audit_entities.record(conn, "sync", client_id, op["entity"], op["uid"], _old, _new, result.get("version"))
                except psycopg_errors.Error:
                    raise
                except Exception:  # noqa: BLE001
                    pass
            if result["status"] in ("applied", "conflict"):
                conn.execute(
                    "INSERT INTO sync_ops (op_id, kind, applied_at, result, response, client_id) VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT (op_id) DO UPDATE SET response = EXCLUDED.response",
                    (op["op_id"], "entity", sync_api._now_iso(), f"{op['entity']}:{op['action']}:{result['status']}",
                     json.dumps(result, default=str), client_id))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return result
    except (psycopg_errors.IntegrityError, psycopg_errors.DataError) as exc:
        if isinstance(exc, psycopg_errors.UniqueViolation):
            # cursă: aceeași operațiune (același op_id) rulează simultan în altă cerere. PostgreSQL ne-a blocat pe indexul
            # unic până la commit-ul câștigătorului, deci răspunsul lui e deja memorat: îl întoarcem ca „duplicate".
            try:
                prev = conn.execute("SELECT response FROM sync_ops WHERE op_id=?", (op["op_id"],)).fetchone()
                if prev is not None and prev["response"]:
                    return {**json.loads(prev["response"]), "duplicate": True}
            except Exception:  # noqa: BLE001 - dacă nici citirea nu merge, cădem pe comportamentul vechi
                pass
        # date pe care PostgreSQL le refuză definitiv (NOT NULL, tip, lungime): reluarea n-ar ajuta => „rejected"
        return _result(op["op_id"], "rejected", reason=f"date_invalide:{type(exc).__name__}")
    except Exception as exc:  # eroare neașteptată: clientul reîncearcă mai târziu
        return _result(op["op_id"], "retry", reason=f"server_error:{type(exc).__name__}")
    finally:
        conn.close()


# ------------------------------------------------------------------ endpointuri
@router.post("/push")
def push(request: Request, payload: Dict[str, Any] = Body(...)):
    _guard(request)
    ops = payload.get("ops")
    if not isinstance(ops, list) or not ops or len(ops) > MAX_OPS:
        raise HTTPException(status_code=422, detail=f"ops: listă de 1..{MAX_OPS} operațiuni")
    client_id = sync_api.client_id_of(request)
    if "publish_burials" in payload:
        cim.set_setting("publish_burials", "1" if payload.get("publish_burials") else "0")
    cim.set_setting("entity_sync_active", "1")
    results = [process_op(op, client_id) for op in ops]
    sync_api._save_client(client_id, last_seen=sync_api._now_iso(),
                          last_entity_push=sync_api._now_iso(),
                          entity_conflicts=sum(1 for r in results if r["status"] == "conflict"))
    return {"ok": True, "results": results}


@router.get("/pull")
def pull(request: Request, entity: str, since: int = 0, limit: int = 500):
    _guard(request)
    if entity not in ents.ENTITIES:
        raise HTTPException(status_code=422, detail="entitate necunoscută")
    limit = max(1, min(int(limit), MAX_PULL))
    conn = cim.get_connection()
    try:
        rows = conn.execute(_select_sql(entity) + " AND t.change_seq > ? ORDER BY t.change_seq LIMIT ?",
                            (since, limit + 1)).fetchall()
        tombs = conn.execute(f"SELECT uid, version, change_seq FROM sync_tombstones WHERE entity=? AND change_seq > ? AND {cim.tenant_scope()} "
                             "ORDER BY change_seq LIMIT ?", (entity, since, limit + 1)).fetchall()
    finally:
        conn.close()
    changes = [_row_to_wire(entity, r) for r in rows]
    changes += [{"uid": t["uid"], "version": int(t["version"]), "seq": int(t["change_seq"]), "deleted": True, "data": None}
                for t in tombs]
    changes.sort(key=lambda c: c["seq"])
    more = len(changes) > limit
    changes = changes[:limit]
    cursor = changes[-1]["seq"] if changes else since
    sync_api._save_client(sync_api.client_id_of(request), last_seen=sync_api._now_iso())
    return {"entity": entity, "changes": changes, "cursor": cursor, "more": more}


def open_conflicts(limit: int = 200) -> List[Dict[str, Any]]:
    conn = cim.get_connection()
    try:
        rows = conn.execute(
            "SELECT id, op_id, entity, uid, action, reason, client_id, base_version, server_version, client_data, "
            "server_data, created_at, resolved_at, resolution FROM sync_conflicts WHERE resolved_at IS NULL "
            f"AND {cim.tenant_scope()} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = {k: _jsonable(v) for k, v in dict(r).items()}
        for key in ("client_data", "server_data"):
            try:
                d[key] = json.loads(d[key]) if d.get(key) else None
            except ValueError:
                pass
        out.append(d)
    return out


@router.get("/conflicts")
def conflicts(request: Request):
    _guard(request)
    return {"conflicts": open_conflicts()}


@router.post("/conflicts/{op_id}/resolve")
def resolve_conflict(request: Request, op_id: str, payload: Dict[str, Any] = Body(default={})):
    _guard(request)
    resolution = str(payload.get("resolution") or "")[:20]
    if resolution not in ("server", "mine"):
        raise HTTPException(status_code=422, detail="resolution: server | mine")
    conn = cim.get_connection()
    try:
        row = conn.execute("UPDATE sync_conflicts SET resolved_at = now(), resolution = ? WHERE op_id = ? "
                           f"AND resolved_at IS NULL AND {cim.tenant_scope()} RETURNING id", (resolution, op_id)).fetchone()
    finally:
        conn.close()
    return {"ok": True, "resolved": row is not None}


@router.get("/state")
def state(request: Request):
    _guard(request)
    conn = cim.get_connection()
    try:
        counts = {e: int(conn.execute(f"SELECT COUNT(*) AS n FROM {e} WHERE {cim.tenant_scope()}").fetchone()["n"]) for e in ents.ORDER}
        seq = {e: int(conn.execute(f"SELECT COALESCE(MAX(change_seq),0) AS m FROM {e} WHERE {cim.tenant_scope()}").fetchone()["m"]) for e in ents.ORDER}
        open_n = int(conn.execute(f"SELECT COUNT(*) AS n FROM sync_conflicts WHERE resolved_at IS NULL AND {cim.tenant_scope()}").fetchone()["n"])
    finally:
        conn.close()
    return {"entities": ents.ORDER, "counts": counts, "max_seq": seq, "open_conflicts": open_n}
