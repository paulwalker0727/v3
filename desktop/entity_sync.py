"""
entity_sync.py — Etapa 6: MAIN (PySide6 + SQLite) <-> FastAPI <-> PostgreSQL, pe entități.

Schema:  Desktop PySide6 --HTTPS--> FastAPI --> PostgreSQL.   Desktopul NU se conectează direct la PostgreSQL.
SQLite local rămâne: cache (citiri), mod offline, outbox (`cloud_sync_outbox`) și copie locală.

Reutilizează `cloud_sync.py` (cerere HTTPS cu cheia de sincronizare, coada persistentă `cloud_sync_outbox`, op_id,
RetryLater/Rejected). Aici se adaugă doar ce lipsea: captarea modificărilor locale, trimiterea lor pe entități,
descărcarea incrementală și tratarea conflictelor.

Cum funcționează
  * Triggere SQLite (instalate când sincronizarea e configurată) pun în outbox orice INSERT/UPDATE/DELETE local pe
    locuri_de_veci / concesionari / contracte / inhumati / plati. O modificare repetată a aceluiași rând înlocuiește
    operațiunea în așteptare (op_id nou). Modificările aplicate DE la server nu intră în outbox (indicator
    `_sync_applying` în aceeași tranzacție).
  * ONLINE: operațiunile pleacă la `/api/sync/entities/push` cu `op_id` și `base_version` (versiunea serverului pe care
    s-a bazat modificarea). Răspunsul serverului (versiune nouă) actualizează cache-ul SQLite. Citirile importante vin
    de la API: `pull_entities()` aduce incremental (cursor `change_seq` per entitate) rândurile și ștergerile.
  * OFFLINE: nimic nu se pierde — operațiunile rămân în outbox; la revenirea internetului se trimit automat.
  * CONFLICT (alt calculator/panoul web a modificat între timp rândul): serverul NU suprascrie, raportează 409-logic
    (status `conflict` + starea serverului). MAIN păstrează operațiunea ca `conflict` și NU suprascrie nimic local;
    operatorul alege explicit `server` sau `mine` (`resolve_conflict`).
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import cimitir_entities as ents
import cloud_sync as cs
import database as db

APPLYING_KEY = "_sync_applying"
SET_CAPABLE = "cloud_entity_sync"        # '1' serverul suportă / '0' nu suportă / '' necunoscut
SET_ONLINE = "cloud_online"              # '1' ultima cerere a reușit / '0' rețea indisponibilă
SET_LAST_PULL = "cloud_entity_last_pull"
CURSOR_PREFIX = "entity_cursor_"
BATCH = 100
PULL_LIMIT = 500

_READY: set = set()
_NOT_APPLYING = "COALESCE((SELECT value FROM settings WHERE key='_sync_applying'),'')<>'1'"
_NOW = "strftime('%Y-%m-%dT%H:%M:%S','now')"
_OUTBOX_COLS = ("kind, entity, entity_uid, action, base_version, state, op_id, created_at, attempts, "
                "tracking_code, status, note, payload")


def _call(method: str, op: str, **kw):
    """Transport: Sync v2 (cheie Binding, /api/v1/sync-v2) sau serverul vechi (X-Sync-Key, /api/sync/entities)."""
    import sync_v2
    if sync_v2.is_binding():
        return sync_v2.request(method, op, **kw)
    return cs._request(method, f"/entities/{op}", **kw)


# ------------------------------------------------------------------ stare / capabilități
SET_ENABLED = "cloud_entity_sync_enabled"   # '0' = regim vechi (MAIN publică doar harta); implicit activ


def enabled() -> bool:
    return (db.get_setting(SET_ENABLED) or "1") != "0"


def set_enabled(flag: bool) -> None:
    db.set_setting(SET_ENABLED, "1" if flag else "0")
    import media_sync
    if not flag:
        disable_tracking()
        media_sync.disable_tracking()
    else:
        ensure_tracking(force=True)
        media_sync.ensure_tracking(force=True)


def capable() -> Optional[bool]:
    if not enabled():
        return False
    value = (db.get_setting(SET_CAPABLE) or "").strip()
    return None if value == "" else value == "1"


def set_capable(flag: bool) -> None:
    flag = flag and enabled()
    before = capable()
    db.set_setting(SET_CAPABLE, "1" if flag else "0")
    if flag and before is not True:
        ensure_tracking(force=True)
    if not flag:
        disable_tracking()


def set_online(flag: bool) -> None:
    value = "1" if flag else "0"
    if (db.get_setting(SET_ONLINE) or "") != value:
        db.set_setting(SET_ONLINE, value)


def is_online() -> bool:
    return (db.get_setting(SET_ONLINE) or "") == "1"


# ------------------------------------------------------------------ captarea modificărilor locale (triggere)
def _columns(conn, table: str) -> List[str]:
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _table_exists(conn, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _tracked_cols(conn, entity: str) -> List[str]:
    sp, present = ents.ENTITIES[entity], set(_columns(conn, entity))
    return [c for c in list(sp["cols"]) + list(sp["fks"]) if c in present]


def _create_triggers(conn, entity: str) -> None:
    cols = _tracked_cols(conn, entity)
    changed = " OR ".join(f"OLD.{c} IS NOT NEW.{c}" for c in cols)
    for suffix in ("ai", "au", "ad"):
        conn.execute(f"DROP TRIGGER IF EXISTS cs_{entity}_{suffix}")
    conn.execute(f"""CREATE TRIGGER cs_{entity}_ai AFTER INSERT ON {entity} WHEN {_NOT_APPLYING}
BEGIN
  UPDATE {entity} SET uid=lower(hex(randomblob(16))) WHERE id=NEW.id AND (uid IS NULL OR uid='');
  DELETE FROM cloud_sync_outbox WHERE kind='entity' AND entity='{entity}' AND state='pending'
    AND entity_uid=(SELECT uid FROM {entity} WHERE id=NEW.id);
  INSERT INTO cloud_sync_outbox ({_OUTBOX_COLS})
  SELECT 'entity','{entity}',uid,'upsert',0,'pending',lower(hex(randomblob(16))),{_NOW},0,'','','','{{}}'
  FROM {entity} WHERE id=NEW.id;
END""")
    conn.execute(f"""CREATE TRIGGER cs_{entity}_au AFTER UPDATE OF {', '.join(cols)} ON {entity}
WHEN {_NOT_APPLYING} AND NEW.uid IS NOT NULL AND ({changed})
BEGIN
  DELETE FROM cloud_sync_outbox WHERE kind='entity' AND entity='{entity}' AND state='pending' AND entity_uid=NEW.uid;
  INSERT INTO cloud_sync_outbox ({_OUTBOX_COLS})
  VALUES ('entity','{entity}',NEW.uid,'upsert',COALESCE(NEW.version,0),'pending',lower(hex(randomblob(16))),{_NOW},0,'','','','{{}}');
END""")
    conn.execute(f"""CREATE TRIGGER cs_{entity}_ad AFTER DELETE ON {entity} WHEN {_NOT_APPLYING} AND OLD.uid IS NOT NULL
BEGIN
  DELETE FROM cloud_sync_outbox WHERE kind='entity' AND entity='{entity}' AND state='pending' AND entity_uid=OLD.uid;
  INSERT INTO cloud_sync_outbox ({_OUTBOX_COLS})
  SELECT 'entity','{entity}',OLD.uid,'delete',COALESCE(OLD.version,0),'pending',lower(hex(randomblob(16))),{_NOW},0,'','','','{{}}'
  WHERE COALESCE(OLD.version,0)>0;
END""")


def ensure_tracking(force: bool = False) -> bool:
    """Coloane uid/version/updated_at, triggere de captare și (o dată) coada inițială. Idempotent, rapid după prima rulare."""
    if capable() is False:
        return False
    key = db.current_db_path()
    if key in _READY and not force:
        return True
    cs.ensure_tables()
    conn = db.get_connection()
    try:
        if not all(_table_exists(conn, e) for e in ents.ORDER) or not _table_exists(conn, "settings"):
            return False
        for entity in ents.ORDER:
            for col in ("uid TEXT", "version INTEGER DEFAULT 0", "updated_at TEXT"):
                db._add_column_if_missing(conn, entity, col)
            conn.execute(f"UPDATE {entity} SET uid=lower(hex(randomblob(16))) WHERE uid IS NULL OR uid=''")
            conn.execute(f"UPDATE {entity} SET version=0 WHERE version IS NULL")
            conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{entity}_uid ON {entity}(uid)")
            _create_triggers(conn, entity)
    finally:
        conn.close()
    _READY.add(key)
    return True


def disable_tracking() -> None:
    """Serverul nu suportă sincronizarea pe entități (ex. SQLite): fără triggere, fără operațiuni de entitate în coadă."""
    conn = db.get_connection()
    try:
        for entity in ents.ORDER:
            for suffix in ("ai", "au", "ad"):
                conn.execute(f"DROP TRIGGER IF EXISTS cs_{entity}_{suffix}")
        if _table_exists(conn, "cloud_sync_outbox"):
            conn.execute("DELETE FROM cloud_sync_outbox WHERE kind='entity'")
    finally:
        conn.close()
    _READY.discard(db.current_db_path())


def enqueue_bootstrap() -> int:
    """Rândurile care nu au ajuns niciodată pe server (version=0) și n-au operațiune în coadă intră în outbox."""
    if not ensure_tracking():
        return 0
    total = 0
    conn = db.get_connection()
    try:
        for entity in ents.ORDER:
            cur = conn.execute(
                f"INSERT INTO cloud_sync_outbox ({_OUTBOX_COLS}) "
                f"SELECT 'entity','{entity}',uid,'upsert',0,'pending',lower(hex(randomblob(16))),{_NOW},0,'','','','{{}}' "
                f"FROM {entity} WHERE COALESCE(version,0)=0 AND uid IS NOT NULL AND uid NOT IN "
                f"(SELECT entity_uid FROM cloud_sync_outbox WHERE kind='entity' AND entity='{entity}' AND entity_uid IS NOT NULL) "
                f"ORDER BY id")
            total += cur.rowcount or 0
    finally:
        conn.close()
    return total


# ------------------------------------------------------------------ tranzacție „aplic de la server” (fără captare)
class _Applying:
    """BEGIN IMMEDIATE + indicator în aceeași tranzacție: triggerele nu pun în outbox ce vine de la server."""

    def __init__(self):
        self.conn = db.get_connection()

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        self.conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, '1')", (APPLYING_KEY,))
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.conn.execute("DELETE FROM settings WHERE key=?", (APPLYING_KEY,))
                self.conn.execute("COMMIT")
            else:
                self.conn.execute("ROLLBACK")
        finally:
            self.conn.close()
        return False


def _local_id(conn, table: str, uid: Optional[str]) -> Optional[int]:
    if not uid:
        return None
    row = conn.execute(f"SELECT id FROM {table} WHERE uid=?", (uid,)).fetchone()
    return int(row["id"]) if row else None


def _has_open_op(conn, entity: str, uid: str, states=("pending", "conflict")) -> bool:
    marks = ",".join("?" * len(states))
    return conn.execute(f"SELECT 1 FROM cloud_sync_outbox WHERE kind='entity' AND entity=? AND entity_uid=? "
                        f"AND COALESCE(state,'pending') IN ({marks})", (entity, uid, *states)).fetchone() is not None


def _wire_to_local(conn, entity: str, data: Dict[str, Any]) -> Dict[str, Any]:
    sp, present = ents.ENTITIES[entity], set(_columns(conn, entity))
    values: Dict[str, Any] = {}
    for col, kind in sp["cols"].items():
        if col not in present:
            continue
        value = ents.coerce(kind, data.get(col))
        values[col] = json.dumps(value) if kind == "j" else value
    for col, (parent, wire) in sp["fks"].items():
        if col in present:
            values[col] = _local_id(conn, parent, data.get(wire))
    return values


def _apply_remote(conn, entity: str, change: Dict[str, Any], respect_pending: bool = True) -> str:
    """Aplică în cache-ul SQLite o modificare venită de la server (în tranzacția `_Applying`). Întoarce ce s-a făcut."""
    uid, version = change["uid"], int(change.get("version") or 0)
    if respect_pending and _has_open_op(conn, entity, uid):
        return "skipped_local_pending"  # modificarea locală nesincronizată nu se suprascrie; conflictul apare la trimitere
    if change.get("deleted"):
        conn.execute(f"DELETE FROM {entity} WHERE uid=?", (uid,))
        return "deleted"
    values = _wire_to_local(conn, entity, change.get("data") or {})
    stamp = change.get("updated_at")
    row = conn.execute(f"SELECT id, COALESCE(version,0) AS version FROM {entity} WHERE uid=?", (uid,)).fetchone()
    if row is None:
        nk = ents.ENTITIES[entity].get("natural_key")
        if nk and values.get(nk):
            same_key = conn.execute(f"SELECT id, uid FROM {entity} WHERE {nk}=?", (values[nk],)).fetchone()
            if same_key is not None:
                if respect_pending and _has_open_op(conn, entity, same_key["uid"] or ""):
                    return "skipped_local_pending"
                conn.execute(f"UPDATE {entity} SET uid=? WHERE id=?", (uid, same_key["id"]))
                row = {"id": same_key["id"], "version": 0}
    if row is None:
        cols = list(values) + ["uid", "version", "updated_at"]
        conn.execute(f"INSERT INTO {entity} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                     (*values.values(), uid, version, stamp))
        return "inserted"
    if int(row["version"]) >= version and respect_pending:
        return "up_to_date"
    sets = ", ".join(f"{c}=?" for c in list(values) + ["version", "updated_at"])
    conn.execute(f"UPDATE {entity} SET {sets} WHERE uid=?", (*values.values(), version, stamp, uid))
    return "updated"


# ------------------------------------------------------------------ citire rând local -> operațiune
def _read_wire(conn, entity: str, uid: str) -> Optional[Dict[str, Any]]:
    sp = ents.ENTITIES[entity]
    present = set(_columns(conn, entity))
    cols = [c for c in sp["cols"] if c in present]
    fks = "".join(f", (SELECT p.uid FROM {parent} p WHERE p.id=t.{col}) AS {wire}"
                  for col, (parent, wire) in sp["fks"].items() if col in present)
    row = conn.execute(f"SELECT COALESCE(t.version,0) AS _version, {', '.join('t.' + c for c in cols)}{fks} "
                       f"FROM {entity} t WHERE t.uid=?", (uid,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    data = {c: (ents.coerce("j", d[c]) if sp["cols"][c] == "j" else d[c]) for c in cols}
    for wire in ents.fk_wire_names(entity):
        data[wire] = d.get(wire)
    return {"version": int(d["_version"]), "data": data}


def _state_rank(op: Dict[str, Any]) -> Tuple[int, int, int]:
    """Upsert-uri: părinți -> copii; ștergeri după, copii -> părinți."""
    idx = ents.ORDER.index(op["entity"]) if op["entity"] in ents.ORDER else 99
    if op["action"] == "delete":
        return (1, -idx, op["id"])
    return (0, idx, op["id"])


# ------------------------------------------------------------------ trimitere (outbox -> API)
def pending_entity_ops(states=("pending",)) -> List[Dict[str, Any]]:
    cs.ensure_tables()
    marks = ",".join("?" * len(states))
    conn = db.get_connection()
    try:
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM cloud_sync_outbox WHERE kind='entity' AND COALESCE(state,'pending') IN ({marks}) ORDER BY id",
            states)]
    finally:
        conn.close()


def _set_op_state(op_row_id: int, state: str, error: str = "", conflict: Optional[Dict[str, Any]] = None) -> None:
    conn = db.get_connection()
    try:
        conn.execute("UPDATE cloud_sync_outbox SET state=?, attempts=COALESCE(attempts,0)+?, last_error=?, "
                     "conflict=COALESCE(?, conflict) WHERE id=?",
                     (state, 0 if state == "conflict" else 1, error[:300], json.dumps(conflict) if conflict else None, op_row_id))
    finally:
        conn.close()


def _handle_result(op: Dict[str, Any], res: Dict[str, Any]) -> str:
    status = res.get("status")
    if status == "applied":
        if op["action"] == "upsert" and res.get("version") is not None:
            with _Applying() as conn:
                conn.execute(f"UPDATE {op['entity']} SET version=?, updated_at=? WHERE uid=?",
                             (int(res["version"]), res.get("updated_at"), op["entity_uid"]))
        cs._delete_op(op["id"])
        return "applied"
    if status == "conflict":
        _set_op_state(op["id"], "conflict", f"conflict: {res.get('reason')}",
                      {"reason": res.get("reason"), "server_version": res.get("server_version"), "server": res.get("server")})
        return "conflict"
    if status == "retry":
        _set_op_state(op["id"], "pending", str(res.get("reason") or "retry"))
        return "retry"
    _set_op_state(op["id"], "error", str(res.get("reason") or "rejected"))
    return "error"


def flush_entities() -> Tuple[bool, List[str], Dict[str, int]]:
    """Trimite operațiunile de entitate în așteptare. Întoarce (fără_probleme, linii, contoare)."""
    counts = {"applied": 0, "conflict": 0, "retry": 0, "error": 0, "sent": 0}
    if capable() is False or not ensure_tracking():
        return True, [], counts
    enqueue_bootstrap()
    ops = sorted(pending_entity_ops(), key=_state_rank)
    lines: List[str] = []
    for start in range(0, len(ops), BATCH):
        chunk, wire_ops, by_op = ops[start:start + BATCH], [], {}
        conn = db.get_connection()
        try:
            for op in chunk:
                if op["action"] == "delete":
                    wire_ops.append({"op_id": op["op_id"], "entity": op["entity"], "uid": op["entity_uid"],
                                     "action": "delete", "base_version": int(op["base_version"] or 0), "data": {}})
                else:
                    row = _read_wire(conn, op["entity"], op["entity_uid"])
                    if row is None:       # rândul a dispărut între timp (ștergerea are propria operațiune)
                        cs._delete_op(op["id"])
                        continue
                    wire_ops.append({"op_id": op["op_id"], "entity": op["entity"], "uid": op["entity_uid"],
                                     "action": "upsert", "base_version": row["version"], "data": row["data"]})
                by_op[op["op_id"]] = op
        finally:
            conn.close()
        if not wire_ops:
            continue
        try:
            body = _call("POST", "push", json={
                "ops": wire_ops, "publish_burials": cs.get_config()["burials"]}, timeout=120).json()
        except cs.RetryLater as exc:
            for op in chunk:
                _set_op_state(op["id"], "pending", str(exc))
            lines.append(f"Entități amânate (offline): {exc}")
            return False, lines, counts
        except cs.Rejected as exc:
            lines.append(f"Entități refuzate de server: {exc}")
            return False, lines, counts
        counts["sent"] += len(wire_ops)
        for res in body.get("results", []):
            op = by_op.get(res.get("op_id"))
            if op is not None:
                counts[_handle_result(op, res)] += 1
    if counts["sent"]:
        lines.append(f"Entități trimise: {counts['sent']} (confirmate {counts['applied']}, conflicte {counts['conflict']}, "
                     f"de reluat {counts['retry']}, erori {counts['error']})")
    if counts["conflict"]:
        db.set_setting(cs.SET_LAST_ERROR, f"{counts['conflict']} conflict(e) de sincronizare: alege versiunea corectă "
                                          "din Sincronizare cloud → Conflicte.")
    return counts["conflict"] == 0 and counts["error"] == 0 and counts["retry"] == 0, lines, counts


# ------------------------------------------------------------------ descărcare (API -> cache SQLite)
def pull_entities() -> Tuple[bool, str, int]:
    """Aduce incremental modificările serverului (cursor per entitate) și actualizează cache-ul SQLite."""
    if capable() is False or not ensure_tracking():
        return True, "Entități: sincronizare indisponibilă pe acest server.", 0
    changed, skipped = 0, 0
    for entity in ents.ORDER:  # părinții înaintea copiilor
        while True:
            since = int(db.get_setting(CURSOR_PREFIX + entity) or 0)
            try:
                page = _call("GET", "pull", params={"entity": entity, "since": since, "limit": PULL_LIMIT}).json()
            except (cs.RetryLater, cs.Rejected) as exc:
                return False, f"Entități (descărcare): {exc}", changed
            with _Applying() as conn:
                for change in page.get("changes", []):
                    outcome = _apply_remote(conn, entity, change)
                    changed += outcome in ("inserted", "updated", "deleted")
                    skipped += outcome == "skipped_local_pending"
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                             (CURSOR_PREFIX + entity, str(int(page.get("cursor") or since))))
            if not page.get("more"):
                break
    db.set_setting(SET_LAST_PULL, datetime.now().isoformat(timespec="seconds"))
    note = f" ({skipped} păstrate local până la trimitere)" if skipped else ""
    return True, f"Entități descărcate: {changed} modificări{note}", changed


def sync_entities() -> Tuple[bool, str]:
    """Un ciclu complet pe entități: trimite outbox-ul, apoi aduce modificările serverului (citiri proaspete în cache)."""
    ok_push, lines, _counts = flush_entities()
    ok_pull, msg, _n = pull_entities()
    text = "; ".join(lines + [msg]) if lines else msg
    return ok_push and ok_pull, text


def refresh_from_server() -> bool:
    """Pentru UI: când e online, reîmprospătează cache-ul înainte de o citire importantă. Fără excepții; False = offline."""
    try:
        if not cs.is_configured():
            return False
        ok, _msg, _n = pull_entities()
        return ok
    except Exception:
        return False


# ------------------------------------------------------------------ conflicte
def conflict_count() -> int:
    return len(pending_entity_ops(("conflict",)))


def error_count() -> int:
    return len(pending_entity_ops(("error",)))


def status_summary() -> Dict[str, Any]:
    return {"online": is_online(), "pending": len(pending_entity_ops()), "conflicts": conflict_count(),
            "errors": error_count(), "last_pull": db.get_setting(SET_LAST_PULL) or ""}


def list_conflicts() -> List[Dict[str, Any]]:
    out = []
    conn = db.get_connection()
    try:
        for op in pending_entity_ops(("conflict",)):
            info = json.loads(op.get("conflict") or "{}")
            local = _read_wire(conn, op["entity"], op["entity_uid"]) if op["action"] == "upsert" else None
            out.append({"op_id": op["op_id"], "entity": op["entity"], "uid": op["entity_uid"], "action": op["action"],
                        "reason": info.get("reason"), "server_version": info.get("server_version"),
                        "server": (info.get("server") or {}).get("data"), "local": local["data"] if local else None,
                        "local_base_version": local["version"] if local else op["base_version"],
                        "created_at": op["created_at"]})
    finally:
        conn.close()
    return out


def resolve_conflict(op_id: str, keep: str) -> bool:
    """Decizia explicită a operatorului. `server`: păstrează versiunea serverului (modificarea locală se aruncă);
    `mine`: păstrează versiunea locală și o retrimite peste versiunea curentă a serverului."""
    if keep not in ("server", "mine"):
        raise ValueError("keep: server | mine")
    op = next((o for o in pending_entity_ops(("conflict",)) if o["op_id"] == op_id), None)
    if op is None:
        return False
    info = json.loads(op.get("conflict") or "{}")
    server = info.get("server")
    entity, uid = op["entity"], op["entity_uid"]
    if keep == "server":
        with _Applying() as conn:
            if server:
                _apply_remote(conn, entity, {**server, "deleted": False}, respect_pending=False)
            else:                                      # serverul a șters rândul (sau nu îl are)
                conn.execute(f"DELETE FROM {entity} WHERE uid=?", (uid,))
        cs._delete_op(op["id"])
    else:
        server_version = int(info.get("server_version") or 0)
        with _Applying() as conn:
            if info.get("reason") == "natural_key" and server:
                conn.execute(f"UPDATE cloud_sync_outbox SET entity_uid=? WHERE id=?", (server["uid"], op["id"]))
                conn.execute(f"UPDATE {entity} SET uid=?, version=? WHERE uid=?", (server["uid"], server_version, uid))
            elif info.get("reason") == "deleted_on_server":
                conn.execute(f"UPDATE {entity} SET version=0 WHERE uid=?", (uid,))   # se recreează pe server
            elif op["action"] == "upsert":
                conn.execute(f"UPDATE {entity} SET version=? WHERE uid=?", (server_version, uid))
        conn = db.get_connection()
        try:
            import uuid
            conn.execute("UPDATE cloud_sync_outbox SET state='pending', op_id=?, base_version=?, last_error='' WHERE id=?",
                         (str(uuid.uuid4()), server_version if op["action"] == "delete" else 0, op["id"]))
        finally:
            conn.close()
    cs._enqueue("conflict_resolved", {"conflict_op_id": op_id, "resolution": keep})
    return True
