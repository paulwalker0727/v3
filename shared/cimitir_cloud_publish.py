"""cimitir_cloud_publish.py — publicarea ATOMICĂ Flux B în simulatorul cloud (Etapa 4, Pasul 4).

O SINGURĂ tranzacție (`BEGIN IMMEDIATE`): re-verifică ținta (DRAFT, goală, epoca 1), planul, sesiunea de staging, completitudinea, digestul staging == digestul sursei,
FK orfane = 0, media (re-citită și verificată), apoi publică entitățile, metadatele media + bytes-ii, setează cimitirul ACTIVE cu `data_epoch = 1`, confirmă binding-ul
device × cemetery, marchează bootstrap-ul `completed` și curăță staging-ul. Orice eșec înainte de COMMIT = nimic publicat. Idempotent după COMMIT (`already`).
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

import cimitir_cloud_control as cc
from cimitir_remote import remotable
import cimitir_cloud_media as cm
import cimitir_cloud_staging as cs
import cimitir_entities as ents
import cimitir_manifest as mf
import cimitir_transfer_contract as tc

INITIAL_EPOCH = 1


class PublishBlocked(tc.TransferBlocked):
    pass


def _verify_target(cp, c, org: str, cem: str, expect: Dict[str, Any]) -> Dict[str, Any]:
    """Manifestul țintă = starea cloud LIVE; trebuie calculabil și egal cu sursa (digest + numărători), altfel nimic nu devine `completed`."""
    try:
        m = mf.build_manifest("cloud", mf.normalize_cloud_snapshot(cp._snapshot_on(c, org, cem)))
    except Exception as exc:  # noqa: BLE001
        raise PublishBlocked(f"manifestul țintă nu poate fi calculat: {type(exc).__name__}") from exc
    counts = {e: m["entities"][e]["count"] for e in ents.ORDER}
    if m["content_digest"] != expect["source_manifest_digest"] or counts != expect["entity_counts"] or m["media"]["count"] != expect["media_count"]:
        diff = [k for k, bad in (("digest", m["content_digest"] != expect["source_manifest_digest"]), ("entity_counts", counts != expect["entity_counts"]),
                                 ("media_count", m["media"]["count"] != expect["media_count"])) if bad]
        raise PublishBlocked(f"manifestul țintă diferă de manifestul sursei: {','.join(diff)}")
    return mf.summary(m)


@remotable
def publish_flow_b(cp, actor: str, org: str, cem: str, bootstrap_id: str, binding_key: str, expect: Dict[str, Any], accept_missing_media: bool = False,
                   hook: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """`expect`: plan_hash, source_manifest_digest, entity_counts, media_count, data_epoch."""
    hook = hook or (lambda phase: None)
    cm._ensure(cp)
    out: Dict[str, Any] = {}
    c = cp._c()
    try:
        c.execute("BEGIN IMMEDIATE")
        cp._lock_cemetery(c, cem)
        cp._require(c, actor, "bootstrap", org)
        run = c.execute("SELECT * FROM bootstrap_runs WHERE bootstrap_id=? AND cemetery_uid=? AND organization_uid=?", (bootstrap_id, cem, org)).fetchone()
        if not run:
            raise cc.ScopeError("bootstrap inexistent")
        if run["state"] == "completed":
            _verify_target(cp, c, org, cem, expect)  # retry după publish: re-verifică idempotent ținta LIVE, fără al doilea publish
            c.execute("COMMIT")
            return {"published": True, "already": True}
        if run["kind"] != "B" or run["plan_hash"] != expect["plan_hash"] or run["state"] != "verifying":
            raise cc.ConflictError(f"bootstrap {run['state']}/{run['kind']} nu poate fi publicat")
        b = cp.authenticate(binding_key, allow_pending=True)
        if b["binding_uid"] != run["binding_uid"] or b["cemetery_uid"] != cem or b["organization_uid"] != org:
            raise cc.ScopeError("legătură invalidă pentru acest bootstrap")
        cem_row = c.execute("SELECT status, data_epoch FROM cemeteries WHERE uid=? AND organization_uid=?", (cem, org)).fetchone()
        if not cem_row:
            raise cc.ScopeError("cimitir inexistent")
        if cem_row["status"] != "DRAFT" or cem_row["data_epoch"] != INITIAL_EPOCH or expect["data_epoch"] != INITIAL_EPOCH:
            raise PublishBlocked(f"ținta trebuie să fie DRAFT cu epoca {INITIAL_EPOCH} (este {cem_row['status']}/{cem_row['data_epoch']})")
        target = cp._state_of(c, cem)
        n_media = c.execute("SELECT COUNT(*) FROM media_objects WHERE cemetery_uid=?", (cem,)).fetchone()[0]
        n_conf = c.execute("SELECT COUNT(*) FROM open_conflicts WHERE cemetery_uid=?", (cem,)).fetchone()[0]
        if target["rows"] or target["tombstones"] or n_media or n_conf:
            raise PublishBlocked("ținta nu mai este goală: nu se suprascrie nimic")
        s = cs._session(cp, c, actor, org, cem, bootstrap_id)
        if s["plan_hash"] != expect["plan_hash"] or s["source_manifest_digest"] != expect["source_manifest_digest"]:
            raise tc.SessionMismatch("staging aparține altui plan/manifest")
        plan = json.loads(s["entity_plan"])
        for ent, n in plan.items():
            if not cs._complete(c, bootstrap_id, ent, n):
                raise PublishBlocked(f"staging incomplet pentru {ent}")
        snap = cs.snapshot_from(c, s, org, cem, bootstrap_id)
        m = mf.build_manifest("staging", snap)
        if m["content_digest"] != expect["source_manifest_digest"]:
            raise PublishBlocked("digestul staging diferă de cel al sursei")
        if m["integrity"]["orphan_total"] != 0:
            raise PublishBlocked("FK orfane în staging")
        counts = {e: m["entities"][e]["count"] for e in ents.ORDER}
        if counts != expect["entity_counts"] or m["media"]["count"] != expect["media_count"]:
            raise PublishBlocked("numărătorile din staging diferă de cele ale sursei")
        mrep = cm.verify_in_tx(c, bootstrap_id, cem)
        if not mrep["ok"]:
            raise cm.MediaNotVerified(f"media neverificată: {mrep['bad']}")
        if mrep["missing"] and not accept_missing_media:
            raise cm.MediaMissingBlocked(f"{len(mrep['missing'])} fișiere media lipsă pe sursă; publicarea cere confirmare explicită")
        now = cp.clock()
        seq = 0
        rows = [(r["entity_type"], r["uid"], r["version"], r["content"]) for r in
                c.execute("SELECT entity_type, uid, version, content FROM staging_rows WHERE bootstrap_id=? AND cemetery_uid=? AND entity_type<>? ORDER BY entity_type, uid",
                          (bootstrap_id, cem, tc.MEDIA))]
        for ent, uid, version, content in rows:
            seq += 1
            c.execute("INSERT INTO registry_rows (cemetery_uid, entity, uid) VALUES (?,?,?)", (cem, ent, uid))
            c.execute("INSERT INTO registry_row_data (cemetery_uid, entity, uid, version, content, change_seq) VALUES (?,?,?,?,?,?)", (cem, ent, uid, version, content, seq))
        published = 0
        for r in c.execute("SELECT uid, content FROM staging_rows WHERE bootstrap_id=? AND cemetery_uid=? AND entity_type=? ORDER BY uid", (bootstrap_id, cem, tc.MEDIA)).fetchall():
            d = json.loads(r["content"])
            present = bool(d.get("present"))
            c.execute("INSERT INTO media_objects (cemetery_uid, uid, kind, entity_type, entity_id, name, size, sha256, present) VALUES (?,?,?,?,?,?,?,?,?)",
                      (cem, r["uid"], d["kind"], d["entity_type"], d["entity_id"], d["name"], int(d["size"] or 0) if present else 0,
                       d["sha256"] if present else (d.get("recorded_sha256") or ""), 1 if present else 0))
            if present:
                if not c.execute("SELECT 1 FROM media_blobs WHERE cemetery_uid=? AND sha256=?", (cem, d["sha256"])).fetchone():
                    owner = r["uid"] if not d.get("dedupe_of") else d["dedupe_of"]
                    od = d if owner == r["uid"] else json.loads(c.execute("SELECT content FROM staging_rows WHERE bootstrap_id=? AND uid=? AND entity_type=?", (bootstrap_id, owner, tc.MEDIA)).fetchone()["content"])
                    c.execute("INSERT INTO media_blobs VALUES (?,?,?,?)", (cem, d["sha256"], int(d["size"]), cm.assemble(c, bootstrap_id, owner, od)))
                c.execute("INSERT INTO media_published VALUES (?,?,?,?,?)", (cem, r["uid"], d["sha256"], int(d["size"]), now))
                published += 1
        hook("publish:in_txn")
        c.execute("UPDATE cemeteries SET status='ACTIVE', data_epoch=? WHERE uid=? AND status='DRAFT'", (INITIAL_EPOCH, cem))
        cp.activate_binding(c, b["binding_uid"], INITIAL_EPOCH)
        target = _verify_target(cp, c, org, cem, expect)
        cp._record_manifest_target(c, bootstrap_id, target)
        c.execute("UPDATE bootstrap_runs SET state='completed', finished_at=?, error=NULL, checkpoint=? WHERE bootstrap_id=?",
                  (now, json.dumps({"phase": "published", "media_missing": mrep["missing"], "accepted_missing_media": bool(mrep["missing"] and accept_missing_media)}, sort_keys=True), bootstrap_id))
        c.execute("DELETE FROM staging_rows WHERE bootstrap_id=? AND cemetery_uid=?", (bootstrap_id, cem))
        c.execute("DELETE FROM staging_batches WHERE bootstrap_id=? AND cemetery_uid=?", (bootstrap_id, cem))
        c.execute("DELETE FROM staging_media_chunks WHERE bootstrap_id=? AND cemetery_uid=?", (bootstrap_id, cem))
        c.execute("DELETE FROM staging_media_blobs WHERE bootstrap_id=? AND cemetery_uid=?", (bootstrap_id, cem))
        c.execute("UPDATE staging_sessions SET state='published' WHERE bootstrap_id=?", (bootstrap_id,))
        cp._audit(c, actor, "publish_flow_b", org, cem, bootstrap_id=bootstrap_id, rows=len(rows), media=len(mrep["missing"]) + mrep["present"], epoch=INITIAL_EPOCH,
                  missing_media=mrep["missing"], accepted_missing_media=bool(mrep["missing"] and accept_missing_media))
        c.execute("COMMIT")
        out = {"published": True, "already": False, "rows": len(rows), "media_logical": mrep["logical"], "media_present": published, "media_missing": mrep["missing"],
                "physical_objects": mrep["physical_objects"], "epoch": INITIAL_EPOCH}
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        c.close()
    hook("publish:after_commit")
    return out
