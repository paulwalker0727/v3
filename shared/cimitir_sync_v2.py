"""cimitir_sync_v2.py — Sync v2: MAIN (SQLite, Windows) <-> Cloud TEST (PostgreSQL) <-> Mobile v2.

Autentificare exclusiv `Binding <cheie>`; organizația și cimitirul vin din binding (niciodată din payload), iar RLS (cimitir_cp_app) este al doilea strat.
Protocol (același format pe fir ca Etapa 6, `cimitir_entities`): push idempotent (op_id) cu base_version, pull incremental (change_seq), conflicte, jurnal.

Politica de conflict (decizie utilizator): MAIN câștigă.
  * scriere MAIN cu base_version depășit  -> se aplică, iar starea suprascrisă rămâne în `sync_v2_conflicts` (resolution=main_wins), recuperabilă;
  * scriere MOBILE cu base_version depășit -> NU se aplică; conflict `open`, vizibil pe telefon și în MAIN (keep_main | accept_incoming).
Telefonul poate scrie doar entitățile bifate din MAIN (`sync_v2_permissions`, implicit nimic) și nu poate șterge.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import psycopg

import cimitir_cloud_control as cc
import cimitir_entities as ents

MAX_OPS = 200
MAX_PULL = 1000
MAX_OP_BYTES = 64 * 1024
MAX_TEXT = 4000
MAX_POINTS = 500
RESOLUTIONS = ("keep_main", "accept_incoming", "restore_previous")

# Permisiuni configurabile (aplicate pe SERVER): capabilități -> registre. Rolurile sunt doar șabloane de capabilități; MAIN poate personaliza oricând.
CAPS: Dict[str, str] = {
    "view_map": "Vizualizare hartă", "read_registers": "Citire evidențe (concesionari, înhumați, contracte, avize)", "read_finance": "Citire plăți și taxe",
    "edit_graves": "Adăugare/modificare locuri", "register_burials": "Înregistrare înhumări", "manage_contracts": "Administrare contracte și concesionari",
    "register_payments": "Înregistrare plăți", "upload_documents": "Încărcare documente și fotografii", "request_permits": "Solicitări de avize pentru lucrări",
    "approve_requests": "Aprobare solicitări", "manage_users": "Administrare utilizatori/dispozitive", "export_print": "Export și tipărire"}
ROLE_PRESETS: Dict[str, Dict[str, Any]] = {
    "administrator": {"label": "Administrator", "caps": list(CAPS)},
    "preot_paroh": {"label": "Preot paroh", "caps": ["view_map", "read_registers", "read_finance", "edit_graves", "register_burials", "manage_contracts", "register_payments",
                                                      "upload_documents", "request_permits", "approve_requests", "export_print"]},
    "operator": {"label": "Operator", "caps": ["view_map", "read_registers", "read_finance", "edit_graves", "register_burials", "register_payments", "upload_documents", "request_permits"]},
    "limitat": {"label": "Utilizator cu acces limitat", "caps": ["view_map", "read_registers"]},
}
READ_CAPS = {"locuri_de_veci": ("view_map", "read_registers"), "concesionari": ("read_registers",), "contracte": ("read_registers",),
             "inhumati": ("read_registers",), "plati": ("read_finance",), "amenajari_avize": ("read_registers",)}
WRITE_CAP = {"locuri_de_veci": "edit_graves", "concesionari": "manage_contracts", "contracte": "manage_contracts", "inhumati": "register_burials",
             "plati": "register_payments", "amenajari_avize": "request_permits"}
CAP_PREFIX, ROLE_PREFIX = "cap:", "role:"


def _jsonable(v: Any) -> Any:
    return v.isoformat() if isinstance(v, (datetime, date)) else v


def _loads(v: Any, default=None):
    if v is None:
        return default
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default


def _select_sql(entity: str) -> str:
    sp = ents.ENTITIES[entity]
    cols = ", ".join(f"t.{c}" for c in sp["cols"])
    fks = "".join(f", (SELECT p.uid FROM {parent} p WHERE p.id=t.{col}) AS {wire}" for col, (parent, wire) in sp["fks"].items())
    return (f"SELECT t.id, t.uid, t.version, t.change_seq, t.modified_at, {cols}{fks} FROM {entity} t "
            "WHERE t.organization_uid=CAST(? AS uuid) AND t.cemetery_uid=CAST(? AS uuid)")


def _wire(entity: str, row) -> Dict[str, Any]:
    d = {k: row[k] for k in row.keys()}
    sp = ents.ENTITIES[entity]
    data = {c: (ents.coerce("j", d[c]) if kind == "j" else _jsonable(d[c])) for c, kind in sp["cols"].items()}
    for w in ents.fk_wire_names(entity):
        data[w] = d.get(w)
    return {"uid": d["uid"], "version": int(d["version"] or 0), "seq": int(d["change_seq"] or 0),
            "updated_at": _jsonable(d["modified_at"]), "deleted": False, "data": data}


def _db_value(kind: str, value: Any) -> Any:
    return json.dumps(value if value is not None else []) if kind == "j" else value


def caps_of(c, a: Dict[str, Any]) -> Optional[set]:
    """None = fără rol atribuit (compatibil: citire totală, scriere după registrele bifate). MAIN are mereu toate capabilitățile."""
    if a["device_type"] == "MAIN":
        return set(CAPS)
    try:
        rows = c.execute("SELECT entity, can_edit FROM sync_v2_permissions WHERE binding_uid=CAST(? AS uuid) AND entity LIKE 'cap:%'", (a["binding_uid"],)).fetchall()
    except Exception as exc:  # noqa: BLE001
        if "sync_v2_permissions" in str(exc):      # migrarea 022 încă neaplicată: Mobile v2 read-only (etapa 020) continuă să funcționeze
            return None
        raise
    return {r["entity"][len(CAP_PREFIX):] for r in rows if r["can_edit"]} if rows else None


def readable_entities(c, a: Dict[str, Any]) -> List[str]:
    caps = caps_of(c, a)
    return list(ents.ORDER) if caps is None else [e for e in ents.ORDER if any(k in caps for k in READ_CAPS[e])]


class SyncV2Service:
    def __init__(self, inbox):
        self.inbox, self.cp = inbox, inbox.cp

    # ------------------------------------------------------------------ autorizare
    def _auth(self, binding_key: str) -> Dict[str, Any]:
        a = self.inbox.authenticate(binding_key)
        if a.get("device_type") not in ("MAIN", "MOBILE"):
            raise cc.PermissionDenied("dispozitiv neacceptat")
        self.cp._t.ctx["device_type"] = a["device_type"]    # RLS: ștergerea în registre este permisă doar pentru MAIN
        return a

    def _need_main(self, a: Dict[str, Any]) -> None:
        if a["device_type"] != "MAIN":
            raise cc.PermissionDenied("operația cere un dispozitiv MAIN")

    @staticmethod
    def _scope(a: Dict[str, Any]) -> tuple:
        return (a["organization_uid"], a["cemetery_uid"])

    def _editable(self, c, a: Dict[str, Any]) -> List[str]:
        if a["device_type"] == "MAIN":
            return list(ents.ORDER)
        rows = c.execute("SELECT entity FROM sync_v2_permissions WHERE binding_uid=CAST(? AS uuid) AND can_edit", (a["binding_uid"],)).fetchall()
        return [r["entity"] for r in rows if r["entity"] in ents.ENTITIES]

    def _need_read(self, c, a: Dict[str, Any], entity: str) -> None:
        if entity not in readable_entities(c, a):
            raise cc.PermissionDenied("nu ai dreptul de citire pentru acest registru")

    # ------------------------------------------------------------------ jurnal
    def _run(self, c, a, direction: str, **n) -> None:
        c.execute("INSERT INTO sync_v2_runs (organization_uid,cemetery_uid,binding_uid,device_type,direction,received,applied,conflicts,rejected,retries,duplicates,pulled) "
                  "VALUES (CAST(? AS uuid),CAST(? AS uuid),CAST(? AS uuid),?,?,?,?,?,?,?,?,?)",
                  (*self._scope(a), a["binding_uid"], a["device_type"], direction, n.get("received", 0), n.get("applied", 0), n.get("conflicts", 0),
                   n.get("rejected", 0), n.get("retries", 0), n.get("duplicates", 0), n.get("pulled", 0)))

    # ------------------------------------------------------------------ citire
    def _fetch(self, c, a, entity: str, uid: str, lock: bool = False):
        return c.execute(_select_sql(entity) + " AND t.uid=?" + (" FOR UPDATE OF t" if lock else ""), (*self._scope(a), uid)).fetchone()

    def status(self, binding_key: str) -> Dict[str, Any]:
        a = self._auth(binding_key)
        c = self.cp._c()
        try:
            sc = self._scope(a)
            counts, seqs = {}, {}
            visible = readable_entities(c, a)
            for e in visible:
                r = c.execute(f"SELECT COUNT(*) AS n, COALESCE(MAX(change_seq),0) AS m FROM {e} WHERE organization_uid=CAST(? AS uuid) AND cemetery_uid=CAST(? AS uuid)", sc).fetchone()
                counts[e], seqs[e] = int(r["n"]), int(r["m"])
            mine = "" if a["device_type"] == "MAIN" else " AND origin_binding_uid=CAST(? AS uuid)"
            params = sc if not mine else (*sc, a["binding_uid"])
            open_n = int(c.execute("SELECT COUNT(*) AS n FROM sync_v2_conflicts WHERE organization_uid=CAST(? AS uuid) AND cemetery_uid=CAST(? AS uuid) "
                                   f"AND resolution='open'{mine}", params).fetchone()["n"])
            last = c.execute("SELECT direction, applied, conflicts, rejected, created_at FROM sync_v2_runs WHERE cemetery_uid=CAST(? AS uuid) "
                             "AND binding_uid=CAST(? AS uuid) ORDER BY id DESC LIMIT 1", (a["cemetery_uid"], a["binding_uid"])).fetchone()
            return {"device_type": a["device_type"], "counts": counts, "max_seq": seqs, "open_conflicts": open_n,
                    "editable": self._editable(c, a), "entities": visible, "role": self._role(c, a),
                    "capabilities": sorted(caps_of(c, a) if caps_of(c, a) is not None else CAPS),
                    "last_run": {k: _jsonable(last[k]) for k in last.keys()} if last else None}
        finally:
            c.close()

    def get_row(self, binding_key: str, entity: str, uid: str) -> Dict[str, Any]:
        a = self._auth(binding_key)
        if entity not in ents.ENTITIES:
            raise ValueError("entitate necunoscută")
        c = self.cp._c()
        try:
            self._need_read(c, a, entity)
            r = self._fetch(c, a, entity, str(uid)[:80])
            if r is None:
                raise cc.ScopeError("înregistrare inexistentă")
            return _wire(entity, r)
        finally:
            c.close()

    def pull(self, binding_key: str, entity: str, since: int = 0, limit: int = 500) -> Dict[str, Any]:
        a = self._auth(binding_key)
        if entity not in ents.ENTITIES:
            raise ValueError("entitate necunoscută")
        since, limit = max(0, int(since)), max(1, min(int(limit), MAX_PULL))
        c = self.cp._c()
        try:
            self._need_read(c, a, entity)
            sc = self._scope(a)
            rows = c.execute(_select_sql(entity) + " AND t.change_seq > ? ORDER BY t.change_seq LIMIT ?", (*sc, since, limit + 1)).fetchall()
            tombs = c.execute("SELECT uid, version, change_seq FROM sync_tombstones WHERE entity=? AND change_seq > ? AND organization_uid=CAST(? AS uuid) "
                              "AND cemetery_uid=CAST(? AS uuid) ORDER BY change_seq LIMIT ?", (entity, since, *sc, limit + 1)).fetchall()
            changes = [_wire(entity, r) for r in rows]
            changes += [{"uid": t["uid"], "version": int(t["version"]), "seq": int(t["change_seq"]), "deleted": True, "data": None} for t in tombs]
            changes.sort(key=lambda x: x["seq"])
            more = len(changes) > limit
            changes = changes[:limit]
            if changes:
                self._run(c, a, "pull", pulled=len(changes))
            return {"entity": entity, "changes": changes, "cursor": changes[-1]["seq"] if changes else since, "more": more}
        finally:
            c.close()

    # ------------------------------------------------------------------ push
    @staticmethod
    def _validate(raw: Any) -> Dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError("operațiune invalidă")
        op_id, entity, uid, action = (str(raw.get(k) or "") for k in ("op_id", "entity", "uid", "action"))
        if not (8 <= len(op_id) <= 80) or not uid or len(uid) > 80 or entity not in ents.ENTITIES or action not in ents.VALID_ACTIONS:
            raise ValueError("op_id/uid/entity/action invalide")
        base = int(raw.get("base_version") or 0)
        if base < 0:
            raise ValueError("base_version invalid")
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        if len(json.dumps(data, default=str)) > MAX_OP_BYTES:
            raise ValueError("date prea mari")
        return {"op_id": op_id, "entity": entity, "uid": uid, "action": action, "base_version": base, "data": data}

    @staticmethod
    def _check_data(entity: str, data: Dict[str, Any]) -> None:
        for col, kind in ents.ENTITIES[entity]["cols"].items():
            v = data.get(col)
            if kind == "t" and isinstance(v, str) and len(v) > MAX_TEXT:
                raise ValueError(f"{col} prea lung")
            if kind == "j" and isinstance(v, list) and len(v) > MAX_POINTS:
                raise ValueError(f"{col} prea mare")

    def push(self, binding_key: str, raw_ops: Any) -> Dict[str, Any]:
        a = self._auth(binding_key)
        if not isinstance(raw_ops, list) or not raw_ops or len(raw_ops) > MAX_OPS:
            raise ValueError(f"ops: listă de 1..{MAX_OPS}")
        c = self.cp._c()
        try:
            editable = set(self._editable(c, a))
            results = [self._process(c, a, raw, editable) for raw in raw_ops]
            n = {s: sum(1 for r in results if r["status"] == s) for s in ("applied", "conflict", "rejected", "retry")}
            self._run(c, a, "push", received=len(results), applied=n["applied"], conflicts=n["conflict"], rejected=n["rejected"], retries=n["retry"],
                      duplicates=sum(1 for r in results if r.get("duplicate")))
            return {"results": results}
        finally:
            c.close()

    def _process(self, c, a, raw: Any, editable: set) -> Dict[str, Any]:
        try:
            op = self._validate(raw)
        except (ValueError, TypeError) as exc:
            return {"op_id": str(raw.get("op_id") if isinstance(raw, dict) else ""), "status": "rejected", "reason": str(exc)}
        sc = self._scope(a)
        prev = c.execute("SELECT response FROM sync_v2_ops WHERE cemetery_uid=CAST(? AS uuid) AND op_id=?", (a["cemetery_uid"], op["op_id"])).fetchone()
        if prev is not None:
            return {**_loads(prev["response"], {}), "duplicate": True}
        try:
            c.execute("BEGIN")
            try:
                result = self._decide(c, a, op, editable)
                result.update(op_id=op["op_id"], entity=op["entity"], uid=op["uid"])
                if result["status"] in ("applied", "conflict") or (result["status"] == "rejected" and result.get("reason") != "not_authorized"):
                    c.execute("INSERT INTO sync_v2_ops (op_id,organization_uid,cemetery_uid,binding_uid,device_type,entity,entity_uid,action,base_version,status,new_version,reason,response) "
                              "VALUES (?,CAST(? AS uuid),CAST(? AS uuid),CAST(? AS uuid),?,?,?,?,?,?,?,?,CAST(? AS jsonb))",
                              (op["op_id"], *sc, a["binding_uid"], a["device_type"], op["entity"], op["uid"], op["action"], op["base_version"], result["status"],
                               result.get("version"), result.get("reason"), json.dumps(result, default=str)))
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
            return result
        except sqlite3.IntegrityError as exc:
            again = c.execute("SELECT response FROM sync_v2_ops WHERE cemetery_uid=CAST(? AS uuid) AND op_id=?", (a["cemetery_uid"], op["op_id"])).fetchone()
            if again is not None:
                return {**_loads(again["response"], {}), "duplicate": True}
            return {"op_id": op["op_id"], "status": "rejected", "reason": "date_invalide", "entity": op["entity"], "uid": op["uid"], "detail": str(exc)[:120]}
        except psycopg.errors.DataError:
            return {"op_id": op["op_id"], "status": "rejected", "reason": "date_invalide", "entity": op["entity"], "uid": op["uid"]}
        except psycopg.Error as exc:
            return {"op_id": op["op_id"], "status": "retry", "reason": f"server_error:{type(exc).__name__}", "entity": op["entity"], "uid": op["uid"]}

    # ------------------------------------------------------------------ decizia pentru o operațiune
    def _record_conflict(self, c, a, op, reason: str, server: Optional[Dict[str, Any]], resolution: str = "open") -> int:
        r = c.execute(
            "INSERT INTO sync_v2_conflicts (organization_uid,cemetery_uid,op_id,entity,entity_uid,reason,origin_binding_uid,origin_device_type,base_version,"
            "server_version,incoming,server,resolution,resolved_at) VALUES (CAST(? AS uuid),CAST(? AS uuid),?,?,?,?,CAST(? AS uuid),?,?,?,CAST(? AS jsonb),CAST(? AS jsonb),?,"
            "CASE WHEN ?='open' THEN NULL ELSE now() END) ON CONFLICT (cemetery_uid, op_id) DO UPDATE SET reason=EXCLUDED.reason RETURNING id",
            (*self._scope(a), op["op_id"], op["entity"], op["uid"], reason, a["binding_uid"], a["device_type"], op["base_version"],
             server["version"] if server else None, json.dumps(op.get("data") or {}, default=str), json.dumps(server, default=str) if server else None,
             resolution, resolution)).fetchone()
        return int(r["id"])

    def _decide(self, c, a, op, editable: set) -> Dict[str, Any]:
        entity, uid, action, base = op["entity"], op["uid"], op["action"], op["base_version"]
        is_main = a["device_type"] == "MAIN"
        if entity not in editable:
            return {"status": "rejected", "reason": "not_authorized"}
        if action == "delete" and not is_main:
            return {"status": "rejected", "reason": "delete_not_allowed"}
        existing = self._fetch(c, a, entity, uid, lock=True)
        server = _wire(entity, existing) if existing is not None else None

        def stale(reason: str):
            """Versiune depășită: MAIN câștigă (se aplică, starea veche se păstrează în jurnal); telefonul nu suprascrie (conflict deschis)."""
            if is_main:
                self._record_conflict(c, a, op, reason, server, "main_wins")
                return None
            cid = self._record_conflict(c, a, op, reason, server)
            return {"status": "conflict", "reason": reason, "server_version": server["version"] if server else None, "server": server, "conflict_id": cid}

        if action == "delete":
            if existing is None:
                return {"status": "applied", "version": base, "deleted": True}
            if server["version"] != base:
                res = stale("version_mismatch")
                if res:
                    return res
            c.execute(f"DELETE FROM {entity} WHERE uid=? AND organization_uid=CAST(? AS uuid) AND cemetery_uid=CAST(? AS uuid)", (uid, *self._scope(a)))
            t = c.execute("SELECT version FROM sync_tombstones WHERE entity=? AND uid=?", (entity, uid)).fetchone()
            return {"status": "applied", "version": int(t["version"]) if t else base + 1, "deleted": True}

        self._check_data(entity, op["data"])
        data = ents.clean_data(entity, op["data"])
        fk_values, missing = self._resolve_fks(c, a, entity, data)
        if missing:
            return {"status": "retry", "reason": missing}
        sp = ents.ENTITIES[entity]

        if existing is None:
            tomb = c.execute("SELECT version FROM sync_tombstones WHERE entity=? AND uid=? AND organization_uid=CAST(? AS uuid) AND cemetery_uid=CAST(? AS uuid)",
                             (entity, uid, *self._scope(a))).fetchone()
            if tomb is not None and base > 0:
                res = stale("deleted_on_server")
                if res:
                    return res
            nk = sp.get("natural_key")
            if nk and data.get(nk):
                other = c.execute(_select_sql(entity) + f" AND t.{nk}=?", (*self._scope(a), data[nk])).fetchone()
                if other is not None:     # alt UID, aceeași cheie naturală: niciodată duplicat; revizuire explicită
                    snap = _wire(entity, other)
                    cid = self._record_conflict(c, a, op, "natural_key", snap)
                    return {"status": "conflict", "reason": "natural_key", "server_version": snap["version"], "server": snap, "conflict_id": cid}
            return self._write(c, a, entity, uid, data, fk_values, insert=True)

        if ents.same_data(entity, data, server["data"]):
            return {"status": "applied", "version": server["version"], "updated_at": server["updated_at"], "adopted": True}
        if server["version"] != base:
            res = stale("version_mismatch")
            if res:
                return res
        return self._write(c, a, entity, uid, data, fk_values, insert=False)

    def _resolve_fks(self, c, a, entity: str, data: Dict[str, Any]):
        values: Dict[str, Any] = {}
        for col, (parent, wire_name) in ents.ENTITIES[entity]["fks"].items():
            uid = data.get(wire_name)
            if not uid:
                values[col] = None
                continue
            r = c.execute(f"SELECT id FROM {parent} WHERE uid=? AND organization_uid=CAST(? AS uuid) AND cemetery_uid=CAST(? AS uuid)", (uid, *self._scope(a))).fetchone()
            if r is None:
                return None, f"missing_parent:{parent}"
            values[col] = int(r["id"])
        return values, None

    def _write(self, c, a, entity, uid, data, fk_values, insert: bool) -> Dict[str, Any]:
        sp = ents.ENTITIES[entity]
        cols = list(sp["cols"]) + list(fk_values)
        vals = [_db_value(sp["cols"][k], data[k]) for k in sp["cols"]] + list(fk_values.values())
        marks = ["CAST(? AS jsonb)" if sp["cols"].get(k) == "j" else "?" for k in cols]
        if insert:
            row = c.execute(f"INSERT INTO {entity} (uid, organization_uid, cemetery_uid, {', '.join(cols)}) VALUES (?, CAST(? AS uuid), CAST(? AS uuid), {', '.join(marks)}) "
                            "RETURNING version, modified_at", (uid, *self._scope(a), *vals)).fetchone()
            res = {"status": "applied", "version": int(row["version"]), "updated_at": _jsonable(row["modified_at"]), "created": True}
        else:
            sets = ", ".join(f"{k} = {m}" for k, m in zip(cols, marks))
            row = c.execute(f"UPDATE {entity} SET {sets} WHERE uid=? AND organization_uid=CAST(? AS uuid) AND cemetery_uid=CAST(? AS uuid) RETURNING version, modified_at",
                            (*vals, uid, *self._scope(a))).fetchone()
            res = {"status": "applied", "version": int(row["version"]), "updated_at": _jsonable(row["modified_at"])}
        if entity == "inhumati" and a["device_type"] != "MAIN":
            self._sync_grave_flag(c, a, fk_values.get("id_loc"))
        return res

    def _sync_grave_flag(self, c, a, loc_id: Optional[int]) -> None:
        """Un înhumat creat/mutat de pe telefon marchează locul ca având înhumări (altfel harta îl arată „disponibil"); fără bump dacă valoarea e deja corectă."""
        if not loc_id:
            return
        c.execute("UPDATE locuri_de_veci SET are_inhumari=1 WHERE id=? AND COALESCE(are_inhumari,0)<>1 AND organization_uid=CAST(? AS uuid) AND cemetery_uid=CAST(? AS uuid)",
                  (loc_id, *self._scope(a)))

    # ------------------------------------------------------------------ conflicte
    def conflicts(self, binding_key: str, only_open: bool = True, limit: int = 100) -> Dict[str, Any]:
        a = self._auth(binding_key)
        c = self.cp._c()
        try:
            sql = ("SELECT id, op_id, entity, entity_uid, reason, origin_device_type, base_version, server_version, incoming, server, resolution, created_at, resolved_at "
                   "FROM sync_v2_conflicts WHERE organization_uid=CAST(? AS uuid) AND cemetery_uid=CAST(? AS uuid)")
            params: list = list(self._scope(a))
            if a["device_type"] != "MAIN":
                sql += " AND origin_binding_uid=CAST(? AS uuid)"
                params.append(a["binding_uid"])
            if only_open:
                sql += " AND resolution='open'"
            else:
                sql += " AND resolution IN ('open','main_wins')"
            rows = c.execute(sql + " ORDER BY id DESC LIMIT ?", (*params, max(1, min(int(limit), 500)))).fetchall()
            out = []
            for r in rows:
                d = {k: _jsonable(r[k]) for k in r.keys()}
                d["incoming"], d["server"] = _loads(d["incoming"]), _loads(d["server"])
                out.append(d)
            return {"conflicts": out}
        finally:
            c.close()

    def resolve(self, binding_key: str, conflict_id: int, resolution: str) -> Dict[str, Any]:
        a = self._auth(binding_key)
        self._need_main(a)
        if resolution not in RESOLUTIONS:
            raise ValueError("resolution invalid")
        c = self.cp._c()
        try:
            sc = self._scope(a)
            c.execute("BEGIN")
            try:
                r = c.execute("SELECT id, op_id, entity, entity_uid, resolution, incoming, server FROM sync_v2_conflicts WHERE id=? AND organization_uid=CAST(? AS uuid) "
                              "AND cemetery_uid=CAST(? AS uuid) FOR UPDATE", (int(conflict_id), *sc)).fetchone()
                if r is None:
                    raise cc.ScopeError("conflict inexistent")
                state = r["resolution"]
                entity, uid = r["entity"], r["entity_uid"]
                if resolution == "keep_main":
                    if state not in ("open", "main_wins"):
                        raise ValueError("conflict deja rezolvat")
                    final = "kept_main" if state == "open" else "reviewed"
                else:
                    if (resolution == "accept_incoming") != (state == "open") or (resolution == "restore_previous" and state != "main_wins"):
                        raise ValueError("rezoluție invalidă pentru starea conflictului")
                    snap = _loads(r["incoming"], {}) if resolution == "accept_incoming" else (_loads(r["server"], {}) or {}).get("data") or {}
                    data = ents.clean_data(entity, snap)
                    fk_values, missing = self._resolve_fks(c, a, entity, data)
                    if missing:
                        raise ValueError("părintele lipsește")
                    cur = self._fetch(c, a, entity, uid, lock=True)
                    self._write(c, a, entity, uid, data, fk_values, insert=cur is None)
                    final = "accepted_incoming" if resolution == "accept_incoming" else "restored_previous"
                c.execute("UPDATE sync_v2_conflicts SET resolution=?, resolved_at=now() WHERE id=?", (final, int(conflict_id)))
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
            return {"id": int(conflict_id), "resolution": final}
        finally:
            c.close()

    # ------------------------------------------------------------------ dispozitive / permisiuni (doar MAIN)
    def devices(self, binding_key: str) -> Dict[str, Any]:
        a = self._auth(binding_key)
        self._need_main(a)
        c = self.cp._c()
        try:
            rows = c.execute("SELECT b.binding_uid, d.device_name, d.device_type FROM device_cemetery_bindings b JOIN devices d ON d.device_uid=b.device_uid "
                             "AND d.organization_uid=b.organization_uid WHERE b.organization_uid=CAST(? AS uuid) AND b.cemetery_uid=CAST(? AS uuid) AND b.active "
                             "AND b.revoked_at IS NULL AND d.device_type='MOBILE' ORDER BY d.device_name", self._scope(a)).fetchall()
            perms = {}
            for p in c.execute("SELECT binding_uid, entity FROM sync_v2_permissions WHERE can_edit AND entity NOT LIKE 'cap:%' AND entity NOT LIKE 'role:%' AND organization_uid=CAST(? AS uuid) AND cemetery_uid=CAST(? AS uuid)", self._scope(a)).fetchall():
                perms.setdefault(p["binding_uid"], []).append(p["entity"])
            roles = {p["binding_uid"]: p["entity"][len(ROLE_PREFIX):] for p in c.execute(
                "SELECT binding_uid, entity FROM sync_v2_permissions WHERE can_edit AND entity LIKE 'role:%' AND organization_uid=CAST(? AS uuid) AND cemetery_uid=CAST(? AS uuid)", self._scope(a)).fetchall()}
            return {"entities": list(ents.ORDER), "roles": {k: v["label"] for k, v in ROLE_PRESETS.items()},
                    "devices": [{"ref": r["binding_uid"], "name": r["device_name"], "editable": sorted(perms.get(r["binding_uid"], [])), "role": roles.get(r["binding_uid"])} for r in rows]}
        finally:
            c.close()

    def set_permissions(self, binding_key: str, ref: str, entities: Any) -> Dict[str, Any]:
        a = self._auth(binding_key)
        self._need_main(a)
        if not isinstance(entities, list) or any(e not in ents.ENTITIES for e in entities):
            raise ValueError("entități invalide")
        ref = str(uuid.UUID(str(ref)))
        c = self.cp._c()
        try:
            ok = c.execute("SELECT 1 FROM device_cemetery_bindings b JOIN devices d ON d.device_uid=b.device_uid AND d.organization_uid=b.organization_uid "
                           "WHERE b.binding_uid=CAST(? AS uuid) AND b.organization_uid=CAST(? AS uuid) AND b.cemetery_uid=CAST(? AS uuid) AND d.device_type='MOBILE'",
                           (ref, *self._scope(a))).fetchone()
            if ok is None:
                raise cc.ScopeError("dispozitiv inexistent")
            c.execute("BEGIN")
            try:
                for e in ents.ORDER:
                    c.execute("INSERT INTO sync_v2_permissions (organization_uid,cemetery_uid,binding_uid,entity,can_edit) VALUES (CAST(? AS uuid),CAST(? AS uuid),CAST(? AS uuid),?,?) "
                              "ON CONFLICT (binding_uid, entity) DO UPDATE SET can_edit=EXCLUDED.can_edit, updated_at=now()", (*self._scope(a), ref, e, e in entities))
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
            return {"editable": [e for e in ents.ORDER if e in entities]}
        finally:
            c.close()

    def put_sectors(self, binding_key: str, sectors: Any) -> Dict[str, Any]:
        """MAIN -> cloud: sectoarele (poligon + culoare) după eticheta lor. Upsert pe (cimitir, id); nu șterge nimic."""
        a = self._auth(binding_key)
        self._need_main(a)
        if not isinstance(sectors, list) or len(sectors) > 500:
            raise ValueError("lista de sectoare invalidă")
        clean = []
        for x in sectors:
            sid = str((x or {}).get("id") or "").strip()[:80]
            pts = (x or {}).get("points")
            if not sid or not isinstance(pts, list) or len(json.dumps(pts)) > 200_000:
                raise ValueError("sector invalid")
            clean.append((sid, json.dumps(pts), str((x or {}).get("color") or "")[:40] or None))
        c = self.cp._c()
        try:
            c.execute("BEGIN")
            try:
                for sid, pts, color in clean:
                    c.execute("INSERT INTO sectoare (id, organization_uid, cemetery_uid, points, color) VALUES (?, CAST(? AS uuid), CAST(? AS uuid), CAST(? AS jsonb), ?) "
                              "ON CONFLICT (cemetery_uid, id) DO UPDATE SET points=EXCLUDED.points, color=EXCLUDED.color", (sid, *self._scope(a), pts, color))
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
            return {"sectors": len(clean)}
        finally:
            c.close()

    def _role(self, c, a) -> Optional[str]:
        if a["device_type"] == "MAIN":
            return "administrator"
        r = c.execute("SELECT entity FROM sync_v2_permissions WHERE binding_uid=CAST(? AS uuid) AND entity LIKE 'role:%' AND can_edit", (a["binding_uid"],)).fetchone()
        return r["entity"][len(ROLE_PREFIX):] if r else None

    def roles(self, binding_key: str) -> Dict[str, Any]:
        self._auth(binding_key)
        return {"capabilities": CAPS, "roles": {k: {"label": v["label"], "capabilities": v["caps"]} for k, v in ROLE_PRESETS.items()}}

    def set_capabilities(self, binding_key: str, ref: str, role: Optional[str], capabilities: Any) -> Dict[str, Any]:
        """Doar MAIN. `role` = șablon; `capabilities` = listă explicită (rol „personalizat"). Se derivă și registrele editabile (aplicate pe server la push)."""
        a = self._auth(binding_key)
        self._need_main(a)
        if role is not None and role not in ROLE_PRESETS:
            raise ValueError("rol necunoscut")
        caps = list(ROLE_PRESETS[role]["caps"]) if capabilities is None and role else capabilities
        if not isinstance(caps, list) or any(k not in CAPS for k in caps):
            raise ValueError("capabilități invalide")
        label = role if capabilities is None else "personalizat"
        editable = [e for e in ents.ORDER if WRITE_CAP[e] in caps]
        ref = str(uuid.UUID(str(ref)))
        c = self.cp._c()
        try:
            ok = c.execute("SELECT 1 FROM device_cemetery_bindings b JOIN devices d ON d.device_uid=b.device_uid AND d.organization_uid=b.organization_uid "
                           "WHERE b.binding_uid=CAST(? AS uuid) AND b.organization_uid=CAST(? AS uuid) AND b.cemetery_uid=CAST(? AS uuid) AND d.device_type='MOBILE'",
                           (ref, *self._scope(a))).fetchone()
            if ok is None:
                raise cc.ScopeError("dispozitiv inexistent")
            rows = [(CAP_PREFIX + k, k in caps) for k in CAPS] + [(ROLE_PREFIX + k, k == label) for k in (*ROLE_PRESETS, "personalizat")] + [(e, e in editable) for e in ents.ORDER]
            c.execute("BEGIN")
            try:
                for ent, flag in rows:
                    c.execute("INSERT INTO sync_v2_permissions (organization_uid,cemetery_uid,binding_uid,entity,can_edit) VALUES (CAST(? AS uuid),CAST(? AS uuid),CAST(? AS uuid),?,?) "
                              "ON CONFLICT (binding_uid, entity) DO UPDATE SET can_edit=EXCLUDED.can_edit, updated_at=now()", (*self._scope(a), ref, ent, flag))
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
            return {"role": label, "capabilities": sorted(caps), "editable": editable}
        finally:
            c.close()

    def history(self, binding_key: str, entity: str, uid: str, limit: int = 100) -> Dict[str, Any]:
        """Istoricul modificărilor unui rând (și, pentru un loc, al înhumaților/contractelor/plăților lui) din jurnalul sync_v2_ops; fără date personale în clar."""
        a = self._auth(binding_key)
        if entity not in ents.ENTITIES:
            raise ValueError("entitate necunoscută")
        c = self.cp._c()
        try:
            self._need_read(c, a, entity)
            sc, uid = self._scope(a), str(uid)[:80]
            pairs = [(entity, uid)]
            if entity == "locuri_de_veci":
                for child, via in (("inhumati", "id_loc"), ("contracte", "id_loc")):
                    pairs += [(child, r["uid"]) for r in c.execute(f"SELECT uid FROM {child} WHERE {via}=(SELECT id FROM locuri_de_veci WHERE uid=? AND organization_uid=CAST(? AS uuid) AND cemetery_uid=CAST(? AS uuid))", (uid, *sc)).fetchall()]
                pairs += [("plati", r["uid"]) for r in c.execute("SELECT p.uid FROM plati p JOIN contracte k ON k.id=p.contract_id WHERE k.id_loc=(SELECT id FROM locuri_de_veci WHERE uid=? AND organization_uid=CAST(? AS uuid) AND cemetery_uid=CAST(? AS uuid))", (uid, *sc)).fetchall()]
            allowed = set(readable_entities(c, a))
            pairs = [(e, u) for e, u in pairs if e in allowed]
            if not pairs:
                return {"events": []}
            marks = ",".join("(?,?)" for _ in pairs)
            flat = [x for p in pairs for x in p]
            rows = c.execute("SELECT o.entity, o.entity_uid, o.action, o.status, o.reason, o.new_version, o.device_type, o.created_at, "
                             "(SELECT d.device_name FROM device_cemetery_bindings b JOIN devices d ON d.device_uid=b.device_uid WHERE b.binding_uid=o.binding_uid) AS device "
                             f"FROM sync_v2_ops o WHERE o.organization_uid=CAST(? AS uuid) AND o.cemetery_uid=CAST(? AS uuid) AND (o.entity, o.entity_uid) IN ({marks}) "
                             "ORDER BY o.created_at DESC LIMIT ?", (*sc, *flat, max(1, min(int(limit), 300)))).fetchall()
            return {"events": [{k: _jsonable(r[k]) for k in r.keys()} for r in rows]}
        finally:
            c.close()

    def runs(self, binding_key: str, limit: int = 50) -> Dict[str, Any]:
        a = self._auth(binding_key)
        self._need_main(a)
        c = self.cp._c()
        try:
            rows = c.execute("SELECT r.direction, r.device_type, r.received, r.applied, r.conflicts, r.rejected, r.retries, r.duplicates, r.pulled, r.created_at, "
                             "(SELECT d.device_name FROM device_cemetery_bindings b JOIN devices d ON d.device_uid=b.device_uid WHERE b.binding_uid=r.binding_uid) AS device "
                             "FROM sync_v2_runs r WHERE r.organization_uid=CAST(? AS uuid) AND r.cemetery_uid=CAST(? AS uuid) ORDER BY r.id DESC LIMIT ?",
                             (*self._scope(a), max(1, min(int(limit), 200)))).fetchall()
            return {"runs": [{k: _jsonable(r[k]) for k in r.keys()} for r in rows]}
        finally:
            c.close()
