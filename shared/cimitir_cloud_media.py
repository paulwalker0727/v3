"""cimitir_cloud_media.py — încărcarea media în simulatorul cloud (Etapa 4, Pasul 4, Flux B), în STAGING, separat de live.

Obiectele se încarcă pe bucăți (idempotent, reluabil), fiecare cu SHA-256; la final se asamblează și se verifică (nr. bucăți, SHA pe bucată, SHA/mărime totală)
față de rândul media din staging. Duplicatele după SHA (`dedupe_of`) se stochează fizic o singură dată, dar rămân atașamente logice distincte (uid-uri distincte).
Bytes trăiesc în control-plane-ul SQLite (BLOB) ca publicarea media să fie ATOMICĂ împreună cu entitățile; un cloud real ar folosi un object store.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

import cimitir_cloud_control as cc
from cimitir_remote import remotable
import cimitir_cloud_staging as cs
import cimitir_transfer_contract as tc

_DDL = """
CREATE TABLE IF NOT EXISTS staging_media_chunks (bootstrap_id TEXT NOT NULL, cemetery_uid TEXT NOT NULL, uid TEXT NOT NULL, n INTEGER NOT NULL,
  sha256 TEXT NOT NULL, size INTEGER NOT NULL, data BLOB NOT NULL, PRIMARY KEY (bootstrap_id, uid, n));
CREATE TABLE IF NOT EXISTS staging_media_blobs (bootstrap_id TEXT NOT NULL, cemetery_uid TEXT NOT NULL, uid TEXT NOT NULL, sha256 TEXT NOT NULL,
  size INTEGER NOT NULL, chunk_count INTEGER NOT NULL, state TEXT NOT NULL, PRIMARY KEY (bootstrap_id, uid));
CREATE TABLE IF NOT EXISTS media_blobs (cemetery_uid TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL, data BLOB NOT NULL, PRIMARY KEY (cemetery_uid, sha256));
CREATE TABLE IF NOT EXISTS media_published (cemetery_uid TEXT NOT NULL, uid TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL, published_at REAL NOT NULL,
  PRIMARY KEY (cemetery_uid, uid));
"""


class MediaCorrupt(tc.CorruptBatch):
    pass


class MediaNotVerified(tc.TransferBlocked):
    pass


class MediaMissingBlocked(tc.TransferBlocked):
    pass


def _ensure(cp) -> None:
    cs._ensure(cp)
    if getattr(cp, "schema_managed", False):
        return
    c = cp._c()
    try:
        c.executescript(_DDL)
    finally:
        c.close()


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _staged(c, bid: str, cem: str, uid: str):
    r = c.execute("SELECT content FROM staging_rows WHERE bootstrap_id=? AND cemetery_uid=? AND entity_type=? AND uid=?", (bid, cem, tc.MEDIA, uid)).fetchone()
    return json.loads(r["content"]) if r else None


@remotable
def media_put_chunk(cp, actor: str, org: str, cem: str, bid: str, uid: str, n: int, data: bytes, chunk_sha256: str) -> Dict[str, Any]:
    _ensure(cp)
    if _sha(data) != chunk_sha256:
        raise MediaCorrupt("bucata nu corespunde SHA-256 declarat")
    c = cp._c()
    try:
        c.execute("BEGIN IMMEDIATE")
        cp._lock_cemetery(c, cem)
        s = cs._session(cp, c, actor, org, cem, bid)
        cur = c.execute("SELECT data_epoch, status FROM cemeteries WHERE uid=?", (cem,)).fetchone()
        if not cur or cur["status"] != "DRAFT" or cur["data_epoch"] != s["data_epoch"]:
            raise tc.SessionMismatch("starea cloud (epocă/status) s-a schimbat după deschiderea staging-ului")
        row = _staged(c, bid, cem, uid)
        if row is None or not row.get("present") or row.get("dedupe_of"):
            raise tc.ContractError("obiect media necunoscut, lipsă sau duplicat logic (nu se încarcă fizic)")
        chunks = row["chunks"]
        if not (isinstance(n, int) and 1 <= n <= len(chunks)):
            raise tc.ContractError("număr de bucată în afara planului")
        if chunks[n - 1]["sha256"] != chunk_sha256 or chunks[n - 1]["size"] != len(data):
            raise MediaCorrupt("bucata diferă de cea declarată în rândul media")
        prev = c.execute("SELECT sha256 FROM staging_media_chunks WHERE bootstrap_id=? AND uid=? AND n=?", (bid, uid, n)).fetchone()
        if prev:
            if prev["sha256"] != chunk_sha256:
                raise tc.ConflictingBatch(f"bucata {n} există cu alt SHA-256")
            c.execute("COMMIT")
            return {"status": "duplicate"}
        c.execute("INSERT INTO staging_media_chunks VALUES (?,?,?,?,?,?,?)", (bid, cem, uid, n, chunk_sha256, len(data), data))
        c.execute("UPDATE staging_sessions SET last_activity_at=? WHERE bootstrap_id=?", (cp.clock(), bid))
        c.execute("COMMIT")
        return {"status": "stored"}
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        c.close()


def assemble(c, bid: str, uid: str, row: Dict[str, Any]) -> bytes:
    """Asamblează și VERIFICĂ (SHA pe bucată recalculat din bytes-ii stocați, nr. bucăți, SHA/mărime totală). `MediaNotVerified` la orice abatere."""
    expected = row["chunks"]
    got = {r["n"]: r for r in c.execute("SELECT n, sha256, size, data FROM staging_media_chunks WHERE bootstrap_id=? AND uid=?", (bid, uid))}
    if sorted(got) != list(range(1, len(expected) + 1)):
        raise MediaNotVerified(f"{uid}: lipsesc bucăți ({sorted(set(range(1, len(expected) + 1)) - set(got))})")
    parts: List[bytes] = []
    for e in expected:
        r = got[e["n"]]
        blob = bytes(r["data"])
        if _sha(blob) != e["sha256"] or r["sha256"] != e["sha256"] or len(blob) != e["size"]:
            raise MediaNotVerified(f"{uid}: bucata {e['n']} alterată în staging")
        parts.append(blob)
    whole = b"".join(parts)
    if _sha(whole) != row["sha256"] or len(whole) != int(row["size"]):
        raise MediaNotVerified(f"{uid}: SHA-256/mărimea obiectului nu corespund")
    return whole


@remotable
def media_finalize(cp, actor: str, org: str, cem: str, bid: str, uid: str) -> Dict[str, Any]:
    _ensure(cp)
    c = cp._c()
    try:
        c.execute("BEGIN IMMEDIATE")
        cp._lock_cemetery(c, cem)
        cs._session(cp, c, actor, org, cem, bid)
        row = _staged(c, bid, cem, uid)
        if row is None or not row.get("present"):
            raise tc.ContractError("obiect media necunoscut sau lipsă")
        assemble(c, bid, uid, row)
        c.execute("INSERT OR REPLACE INTO staging_media_blobs VALUES (?,?,?,?,?,?,?)", (bid, cem, uid, row["sha256"], int(row["size"]), len(row["chunks"]), "verified"))
        c.execute("COMMIT")
        return {"uid": uid, "verified": True}
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        c.close()


def verify_in_tx(c, bid: str, cem: str) -> Dict[str, Any]:
    """Verificare STRICTĂ (re-citește bytes-ii) a tuturor obiectelor media din staging; nu se bazează pe marcajul `verified`."""
    rows = [(r["uid"], json.loads(r["content"])) for r in c.execute("SELECT uid, content FROM staging_rows WHERE bootstrap_id=? AND cemetery_uid=? AND entity_type=? ORDER BY uid",
                                                                    (bid, cem, tc.MEDIA))]
    verified: Dict[str, str] = {}
    bad: List[Dict[str, str]] = []
    missing: List[str] = []
    for uid, d in rows:
        if not d.get("present"):
            missing.append(uid)
        elif not d.get("dedupe_of"):
            try:
                assemble(c, bid, uid, d)
                verified[uid] = d["sha256"]
            except MediaNotVerified as exc:
                bad.append({"uid": uid, "reason": str(exc)})
    deduped = 0
    for uid, d in rows:
        if d.get("present") and d.get("dedupe_of"):
            deduped += 1
            if verified.get(d["dedupe_of"]) != d["sha256"]:
                bad.append({"uid": uid, "reason": "duplicat logic fără obiect fizic verificat cu același SHA-256"})
    present = [d for _u, d in rows if d.get("present")]
    return {"logical": len(rows), "present": len(present), "missing": missing, "physical_objects": len(verified), "deduped": deduped, "bad": bad,
            "bytes_logical": sum(int(d["size"]) for d in present), "bytes_physical": sum(int(d["size"]) for u, d in rows if u in verified),
            "ok": not bad}


@remotable
def media_verify(cp, actor: str, org: str, cem: str, bid: str) -> Dict[str, Any]:
    _ensure(cp)
    c = cp._c()
    try:
        cs._session(cp, c, actor, org, cem, bid, need_open=False)
        return verify_in_tx(c, bid, cem)
    finally:
        c.close()


@remotable
def media_cleanup(cp, actor: str, org: str, cem: str, bid: str) -> Dict[str, int]:
    """Șterge NUMAI bytes-ii din staging ai acestui bootstrap (idempotent)."""
    _ensure(cp)
    c = cp._c()
    try:
        c.execute("BEGIN IMMEDIATE")
        cp._lock_cemetery(c, cem)
        cs._session(cp, c, actor, org, cem, bid, need_open=False)
        a = c.execute("DELETE FROM staging_media_chunks WHERE bootstrap_id=? AND cemetery_uid=?", (bid, cem)).rowcount
        b = c.execute("DELETE FROM staging_media_blobs WHERE bootstrap_id=? AND cemetery_uid=?", (bid, cem)).rowcount
        c.execute("COMMIT")
        return {"chunks": a, "blobs": b}
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        c.close()
