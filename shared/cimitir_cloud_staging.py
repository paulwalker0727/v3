"""cimitir_cloud_staging.py — staging în simulatorul cloud (Etapa 4, Pasul 3), SEPARAT de live.

Tabele proprii `staging_*`; nimic din ele nu este citit de `export_snapshot`/manifestul live. Fiecare rând poartă `bootstrap_id` + `cemetery_uid`;
accesul trece prin sesiune (`bootstrap_id` ↔ organizație ↔ cimitir) și prin permisiunea `bootstrap` (provizionare A/B/C; distinctă de `rebuild` = D/E) în organizație. Nu activează cloud-ul, nu crește epoca,
nu atinge bindings/outbox/live. Cleanup șterge DOAR staging-ul bootstrap-ului dat.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List

import cimitir_cloud_control as cc
from cimitir_remote import remotable
import cimitir_entities as ents
import cimitir_transfer_contract as tc

STALE_AFTER = 24 * 3600  # staging fără activitate de atât timp devine STALE (marcaj; NU se șterge automat)
_DDL = """
CREATE TABLE IF NOT EXISTS staging_sessions (bootstrap_id TEXT PRIMARY KEY, organization_uid TEXT NOT NULL, cemetery_uid TEXT NOT NULL, plan_hash TEXT NOT NULL,
  source_manifest_digest TEXT NOT NULL, schema_version TEXT NOT NULL, data_epoch INTEGER NOT NULL, entity_plan TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'open', created_at REAL NOT NULL, last_activity_at REAL NOT NULL DEFAULT 0, stale_after REAL NOT NULL DEFAULT 86400);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_staging_per_cemetery ON staging_sessions (cemetery_uid) WHERE state='open';
CREATE TABLE IF NOT EXISTS staging_batches (bootstrap_id TEXT NOT NULL, cemetery_uid TEXT NOT NULL, entity_type TEXT NOT NULL, batch_no INTEGER NOT NULL,
  batch_count INTEGER NOT NULL, batch_sha256 TEXT NOT NULL, row_count INTEGER NOT NULL, received_at REAL NOT NULL, PRIMARY KEY (bootstrap_id, entity_type, batch_no));
CREATE TABLE IF NOT EXISTS staging_rows (bootstrap_id TEXT NOT NULL, cemetery_uid TEXT NOT NULL, entity_type TEXT NOT NULL, uid TEXT NOT NULL,
  batch_no INTEGER NOT NULL, version INTEGER NOT NULL DEFAULT 1, content TEXT NOT NULL, PRIMARY KEY (bootstrap_id, entity_type, uid));
"""


def _ensure(cp) -> None:
    c = cp._c()
    try:
        c.executescript(_DDL)
        have = {r["name"] for r in c.execute("PRAGMA table_info(staging_sessions)")}
        for col, ddl in (("last_activity_at", "REAL NOT NULL DEFAULT 0"), ("stale_after", "REAL NOT NULL DEFAULT 86400")):
            if col not in have:
                c.execute(f"ALTER TABLE staging_sessions ADD COLUMN {col} {ddl}")
    finally:
        c.close()


def _session(cp, c, actor: str, org: str, cem: str, bootstrap_id: str, need_open: bool = True) -> sqlite3.Row:
    cp._require(c, actor, "bootstrap", org)
    s = c.execute("SELECT * FROM staging_sessions WHERE bootstrap_id=? AND organization_uid=? AND cemetery_uid=?", (bootstrap_id, org, cem)).fetchone()
    if not s:
        raise cc.ScopeError("bootstrap inexistent")  # alt tenant/alt cimitir ≡ inexistent
    if need_open and s["state"] != "open":
        raise tc.SessionMismatch(f"staging {s['state']}")
    return s


@remotable
def staging_open(cp, actor: str, org: str, cem: str, bootstrap_id: str, session: Dict[str, Any], entity_plan: Dict[str, int]) -> Dict[str, Any]:
    """Deschide (sau re-confirmă, idempotent) sesiunea de staging. `session` = câmpurile SESSION_FIELDS."""
    _ensure(cp)
    for e in entity_plan:
        if e not in tc.ENTITY_ORDER:
            raise tc.ContractError(f"entity_type necunoscut: {e!r}")
    if session["organization_uid"] != org or session["cemetery_uid"] != cem:
        raise tc.SessionMismatch("organizație/cimitir din contract diferă de cele ale cererii")
    c = cp._c()
    try:
        c.execute("BEGIN IMMEDIATE")
        cp._require(c, actor, "bootstrap", org)
        row = c.execute("SELECT status, data_epoch FROM cemeteries WHERE uid=? AND organization_uid=?", (cem, org)).fetchone()
        if not row:
            raise cc.ScopeError("cimitir inexistent")
        if row["status"] != "DRAFT":
            raise tc.SessionMismatch(f"transferul cere cimitir DRAFT în cloud (este {row['status']})")
        if row["data_epoch"] != session["data_epoch"]:
            raise tc.SessionMismatch("epoca cloud s-a schimbat față de plan")
        cur = c.execute("SELECT * FROM staging_sessions WHERE bootstrap_id=?", (bootstrap_id,)).fetchone()
        if cur:
            if cur["organization_uid"] != org or cur["cemetery_uid"] != cem:
                raise cc.ScopeError("bootstrap inexistent")
            diff = [k for k in tc.SESSION_FIELDS if cur[k] != session[k]] + (["entity_plan"] if json.loads(cur["entity_plan"]) != entity_plan else [])
            if diff:
                raise tc.SessionMismatch(f"sesiunea de staging există cu alte valori: {diff}")
            if cur["state"] not in ("open", "stale"):
                raise tc.SessionMismatch(f"staging {cur['state']}")
            try:  # re-confirmare / redeschidere explicită a unui staging STALE (cu aceleași valori)
                c.execute("UPDATE staging_sessions SET state='open', last_activity_at=? WHERE bootstrap_id=?", (cp.clock(), bootstrap_id))
            except sqlite3.IntegrityError:
                raise tc.SessionMismatch("alt transfer este deja activ pe acest cimitir") from None
            c.execute("COMMIT")
            return {"bootstrap_id": bootstrap_id, "reopened": True}
        try:
            c.execute("INSERT INTO staging_sessions (bootstrap_id, organization_uid, cemetery_uid, plan_hash, source_manifest_digest, schema_version, data_epoch,"
                      " entity_plan, created_at, last_activity_at, stale_after) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (bootstrap_id, org, cem, session["plan_hash"], session["source_manifest_digest"], session["schema_version"], session["data_epoch"],
                       json.dumps(entity_plan, sort_keys=True), cp.clock(), cp.clock(), STALE_AFTER))
        except sqlite3.IntegrityError:
            raise tc.SessionMismatch("alt transfer este deja activ pe acest cimitir") from None
        cp._audit(c, actor, "staging_open", org, cem, bootstrap_id=bootstrap_id)
        c.execute("COMMIT")
        return {"bootstrap_id": bootstrap_id, "reopened": False}
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        c.close()


def _complete(c, bootstrap_id: str, entity: str, planned: int) -> bool:
    return c.execute("SELECT COUNT(*) FROM staging_batches WHERE bootstrap_id=? AND entity_type=?", (bootstrap_id, entity)).fetchone()[0] >= planned


@remotable
def staging_put_batch(cp, actor: str, org: str, cem: str, contract: Dict[str, Any], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Scrie un batch în staging (atomic). Întoarce {"status": "stored"|"duplicate"}; orice încălcare = TransferBlocked, fără efecte."""
    _ensure(cp)
    tc.check_shape(contract, rows)
    bid, ent = contract["bootstrap_id"], contract["entity_type"]
    c = cp._c()
    try:
        c.execute("BEGIN IMMEDIATE")
        s = _session(cp, c, actor, org, cem, bid)
        for k in tc.SESSION_FIELDS:
            if contract[k] != s[k]:
                raise tc.SessionMismatch(f"{k} din contract diferă de sesiunea de staging")
        cur = c.execute("SELECT data_epoch, status FROM cemeteries WHERE uid=?", (cem,)).fetchone()
        if not cur or cur["data_epoch"] != s["data_epoch"] or cur["status"] != "DRAFT":
            raise tc.SessionMismatch("starea cloud (epocă/status) s-a schimbat după deschiderea staging-ului")
        plan = json.loads(s["entity_plan"])
        if ent not in plan:
            raise tc.ContractError(f"{ent} nu face parte din planul de transfer")
        if contract["batch_count"] != plan[ent]:
            raise tc.ConflictingBatch("batch_count diferă de planul declarat")
        prev = c.execute("SELECT batch_sha256 FROM staging_batches WHERE bootstrap_id=? AND entity_type=? AND batch_no=?", (bid, ent, contract["batch_no"])).fetchone()
        if prev:
            if prev["batch_sha256"] != contract["batch_sha256"]:
                raise tc.ConflictingBatch(f"batch {ent}#{contract['batch_no']} există cu alt SHA-256")
            c.execute("COMMIT")
            return {"status": "duplicate"}
        for earlier in tc.ENTITY_ORDER[:tc.ENTITY_ORDER.index(ent)]:
            if earlier in plan and not _complete(c, bid, earlier, plan[earlier]):
                raise tc.OrderViolation(f"{ent} nu poate fi trimis înainte ca {earlier} să fie complet (părinți înaintea copiilor)")
        for r in rows:
            if c.execute("SELECT 1 FROM staging_rows WHERE bootstrap_id=? AND entity_type=? AND uid=?", (bid, ent, r["uid"])).fetchone():
                raise tc.ConflictingBatch(f"uid {r['uid']} este deja în staging (alt batch)")
            if ent in ents.ENTITIES:
                for _col, (parent, wire) in ents.ENTITIES[ent]["fks"].items():
                    ref = r["data"].get(wire)
                    if ref and not c.execute("SELECT 1 FROM staging_rows WHERE bootstrap_id=? AND entity_type=? AND uid=?", (bid, parent, ref)).fetchone():
                        raise tc.OrphanFK(f"{ent}.{wire} → {parent} {ref} inexistent în staging")
        c.execute("INSERT INTO staging_batches VALUES (?,?,?,?,?,?,?,?)", (bid, cem, ent, contract["batch_no"], contract["batch_count"], contract["batch_sha256"],
                                                                         len(rows), cp.clock()))
        for r in rows:
            c.execute("INSERT INTO staging_rows VALUES (?,?,?,?,?,?,?)", (bid, cem, ent, r["uid"], contract["batch_no"], int(r.get("version") or 1),
                                                                         json.dumps(r["data"], sort_keys=True)))
        c.execute("UPDATE staging_sessions SET last_activity_at=? WHERE bootstrap_id=?", (cp.clock(), bid))
        c.execute("COMMIT")
        return {"status": "stored"}
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        c.close()


@remotable
def staging_status(cp, actor: str, org: str, cem: str, bootstrap_id: str) -> Dict[str, Any]:
    _ensure(cp)
    c = cp._c()
    try:
        s = _session(cp, c, actor, org, cem, bootstrap_id, need_open=False)
        plan = json.loads(s["entity_plan"])
        got: Dict[str, List[int]] = {}
        for r in c.execute("SELECT entity_type, batch_no FROM staging_batches WHERE bootstrap_id=? AND cemetery_uid=? ORDER BY entity_type, batch_no", (bootstrap_id, cem)):
            got.setdefault(r["entity_type"], []).append(r["batch_no"])
        entities = {e: {"planned": n, "received": got.get(e, []), "missing": [i for i in range(1, n + 1) if i not in got.get(e, [])]} for e, n in plan.items()}
        eligible_at = s["last_activity_at"] + s["stale_after"]
        meta = {"created_at": s["created_at"], "last_activity_at": s["last_activity_at"], "status": s["state"], "stale_after": s["stale_after"],
                "eligible_for_cleanup_at": eligible_at, "eligible_for_cleanup": s["state"] in ("stale", "cleaned") or cp.clock() >= eligible_at}
        return {"state": s["state"], "meta": meta, "entities": entities, "complete": all(not v["missing"] for v in entities.values()),
                "plan_hash": s["plan_hash"], "source_manifest_digest": s["source_manifest_digest"]}
    finally:
        c.close()


@remotable
def staging_snapshot(cp, actor: str, org: str, cem: str, bootstrap_id: str) -> Dict[str, Any]:
    """Instantaneu al STAGING-ULUI în același format ca `export_snapshot` (pentru manifest/verificare); nu apare niciodată în manifestul live."""
    _ensure(cp)
    c = cp._c()
    try:
        s = _session(cp, c, actor, org, cem, bootstrap_id, need_open=False)
        return snapshot_from(c, s, org, cem, bootstrap_id)
    finally:
        c.close()


def snapshot_from(c, s, org: str, cem: str, bootstrap_id: str) -> Dict[str, Any]:
    snap: Dict[str, Any] = {"exists": True, "organization_uid": org, "cemetery_uid": cem, "status": "STAGING", "data_epoch": s["data_epoch"],
                            "schema_version": s["schema_version"], "entities": {}, "media": [], "tombstones": 0, "open_conflicts": 0, "change_seq": None,
                            "app_version": None, "sync_state": None, "outbox": None, "fk_check_violations": None}
    for r in c.execute("SELECT entity_type, uid, version, content FROM staging_rows WHERE bootstrap_id=? AND cemetery_uid=? ORDER BY entity_type, uid", (bootstrap_id, cem)):
        data = json.loads(r["content"])
        if r["entity_type"] == tc.MEDIA:
            snap["media"].append({k: data.get(k) for k in ("kind", "entity_type", "entity_id", "name", "recorded_sha256", "present", "size", "sha256", "status")})
        else:
            sp = ents.ENTITIES[r["entity_type"]]
            snap["entities"].setdefault(r["entity_type"], []).append(
                {"ref": r["uid"], "uid": r["uid"], "version": r["version"], "data": data,
                 "fks": {wire: (parent, data.get(wire)) for _col, (parent, wire) in sp["fks"].items()}})
    return snap


def staging_mark_stale(cp, actor: str, org: str, cem: str) -> List[str]:
    """Marchează STALE sesiunile deschise ale cimitirului fără activitate de `stale_after` secunde. NU șterge nimic."""
    _ensure(cp)
    c = cp._c()
    try:
        c.execute("BEGIN IMMEDIATE")
        cp._require(c, actor, "bootstrap", org)
        ids = [r["bootstrap_id"] for r in c.execute("SELECT bootstrap_id FROM staging_sessions WHERE organization_uid=? AND cemetery_uid=? AND state='open' "
                                                    "AND last_activity_at + stale_after <= ?", (org, cem, cp.clock()))]
        for i in ids:
            c.execute("UPDATE staging_sessions SET state='stale' WHERE bootstrap_id=?", (i,))
        c.execute("COMMIT")
        return ids
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        c.close()


@remotable
def staging_cleanup(cp, actor: str, org: str, cem: str, bootstrap_id: str) -> Dict[str, int]:
    """Șterge NUMAI staging-ul acestui bootstrap (rânduri + batch-uri); sesiunea rămâne `cleaned` (nu se reutilizează bootstrap_id)."""
    _ensure(cp)
    c = cp._c()
    try:
        c.execute("BEGIN IMMEDIATE")
        _session(cp, c, actor, org, cem, bootstrap_id, need_open=False)
        n = c.execute("DELETE FROM staging_rows WHERE bootstrap_id=? AND cemetery_uid=?", (bootstrap_id, cem)).rowcount
        b = c.execute("DELETE FROM staging_batches WHERE bootstrap_id=? AND cemetery_uid=?", (bootstrap_id, cem)).rowcount
        c.execute("UPDATE staging_sessions SET state='cleaned' WHERE bootstrap_id=?", (bootstrap_id,))
        cp._audit(c, actor, "staging_cleanup", org, cem, bootstrap_id=bootstrap_id)
        c.execute("COMMIT")
        return {"rows": n, "batches": b}
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        c.close()
