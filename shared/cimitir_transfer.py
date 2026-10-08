"""cimitir_transfer.py — infrastructura MAIN → Cloud (Etapa 4, Pasul 3): UID mapping persistat, batch-uri idempotente, staging, resume, media simulat.

NU este Flux B: nu activează cloud-ul, nu face swap, nu crește epoca, nu schimbă bindings/outbox, nu modifică registrul live (nici măcar nu-i atribuie uid-uri).
Singura scriere locală este `registries/<uid>/bootstrap_state/<bootstrap_id>.sqlite` (mapping UID + metadate), în afara bazei live și a backup-urilor de registru.

UID-uri: `uid = uid-ul local existent` dacă rândul îl are; altfel `uuid5(NS, bootstrap_id|cemetery_uid|entity|ref_local)`. Se generează O SINGURĂ DATĂ, la începutul
transferului, pentru toate rândurile, și se persistă; resume citește mapping-ul și NU regenerează nimic. Mapping-ul se păstrează până la finalizarea bootstrap-ului.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import cimitir_cloud_staging as cs
import cimitir_entities as ents
import cimitir_identity as ident
import cimitir_manifest as mf
import cimitir_preflight as pf
import cimitir_transfer_contract as tc

NS = uuid.UUID("c1a0b0de-0000-4000-8000-000000000003")
DEFAULT_BATCH_SIZE = 200
DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024


def derive_uid(bootstrap_id: str, cemetery_uid: str, entity: str, ref: Any) -> str:
    return str(uuid.uuid5(NS, f"{bootstrap_id}|{cemetery_uid}|{entity}|{ref}"))


class StateStore:
    """Starea locală a unui transfer (mapping UID + meta), într-un SQLite separat de registrul live."""

    def __init__(self, binding, bootstrap_id: str):
        self.path = Path(binding.root) / "bootstrap_state" / f"{bootstrap_id}.sqlite"

    def exists(self) -> bool:
        """Adevărat doar pentru o stare COMPLETĂ (meta scrisă); un fișier rămas parțial după o cădere în `init` nu contează."""
        if not self.path.exists():
            return False
        c = sqlite3.connect(self.path, timeout=15)
        try:
            return c.execute("SELECT COUNT(*) FROM meta").fetchone()[0] > 0
        except sqlite3.Error:
            return False
        finally:
            c.close()

    def _c(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.path, timeout=15)
        c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS uid_map (entity TEXT NOT NULL, ref TEXT NOT NULL, uid TEXT NOT NULL, PRIMARY KEY (entity, ref))")
        return c

    def init(self, meta: Dict[str, Any], mapping: Dict[Tuple[str, str], str]) -> None:
        c = self._c()
        try:
            with c:
                c.executemany("INSERT INTO meta VALUES (?,?)", [(k, json.dumps(v, sort_keys=True)) for k, v in meta.items()])
                c.executemany("INSERT INTO uid_map VALUES (?,?,?)", [(e, r, u) for (e, r), u in mapping.items()])
        finally:
            c.close()

    def meta(self) -> Dict[str, Any]:
        c = self._c()
        try:
            return {k: json.loads(v) for k, v in c.execute("SELECT key, value FROM meta")}
        finally:
            c.close()

    def mapping(self) -> Dict[Tuple[str, str], str]:
        c = self._c()
        try:
            return {(e, r): u for e, r, u in c.execute("SELECT entity, ref, uid FROM uid_map")}
        finally:
            c.close()

    def delete(self) -> None:
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(str(self.path) + suffix).unlink(missing_ok=True)
        try:
            self.path.parent.rmdir()
        except OSError:
            pass


def _ref(entity: str, r: Dict[str, Any]) -> str:
    return str(r["ref"])


def canonicalize(snap: Dict[str, Any]) -> Dict[str, Any]:
    """Ordine CANONICĂ înaintea partiționării: rânduri sortate după (hash de conținut, cheie locală), media după (tip, nume, sha, entitate); independentă de
    ordinea fizică întoarsă de SQLite. Delimitarea batch-urilor se face pe această ordine."""
    hash_by_ref: Dict[str, Dict[Any, str]] = {}
    entities: Dict[str, List[Dict[str, Any]]] = {}
    for entity in ents.ORDER:
        rows = snap["entities"].get(entity, [])
        mf.entity_summary(entity, rows, hash_by_ref)
        h = hash_by_ref[entity]
        entities[entity] = sorted(rows, key=lambda r: (h[r["ref"]], str(r["ref"]).zfill(20)))
    media = sorted(snap["media"], key=lambda i: (i["kind"], i["name"], i.get("sha256") or "", i.get("entity_type") or "", str(i.get("entity_id") or ""),
                                                 i.get("recorded_sha256") or "", i.get("_local_ref") or ""))
    return {**snap, "entities": entities, "media": media}


def _build_mapping(bootstrap_id: str, cem: str, snap: Dict[str, Any], existing: Optional[Dict[Tuple[str, str], str]] = None,
                   existing_media: Optional[Dict[str, str]] = None) -> Dict[Tuple[str, str], str]:
    """Prioritate: identitate durabilă existentă (continuitate) > uid local existent > derivare deterministă.
    Media: cheia este referința locală DURABILĂ (`document:<id>` / `photo:<id>`), nu numele fișierului și nu poziția în listă."""
    existing = existing or {}
    existing_media = existing_media or {}
    m: Dict[Tuple[str, str], str] = {}
    for entity in ents.ORDER:
        for r in snap["entities"].get(entity, []):
            m[(entity, _ref(entity, r))] = existing.get((entity, _ref(entity, r))) or r["uid"] or derive_uid(bootstrap_id, cem, entity, r["ref"])
    for item in snap["media"]:
        ref = item["_local_ref"]
        m[(tc.MEDIA, ref)] = existing_media.get(ref) or derive_uid(bootstrap_id, cem, tc.MEDIA, ref)
    return m


def _plan_batches(snap: Dict[str, Any], batch_size: int) -> Dict[str, int]:
    plan: Dict[str, int] = {}
    for entity in ents.ORDER:
        n = len(snap["entities"].get(entity, []))
        if n:
            plan[entity] = -(-n // batch_size)
    if snap["media"]:
        plan[tc.MEDIA] = -(-len(snap["media"]) // batch_size)
    return plan


class Transfer:
    def __init__(self, binding, cp, actor: str, state: StateStore, meta: Dict[str, Any], source=None):
        self.binding, self.cp, self.actor, self.state, self.meta = binding, cp, actor, state, meta
        self.source = source or binding  # sursa citită: registrul live SAU un snapshot consistent (Flux B)
        self.bootstrap_id = meta["bootstrap_id"]
        self.session = {k: meta[k] for k in tc.SESSION_FIELDS}

    # ------------------------------------------------------------ sursa (citire) și drift
    def _drift_check(self) -> Dict[str, Any]:
        if self.binding.cemetery_uid != self.meta["cemetery_uid"] or self.binding.organization_uid != self.meta["organization_uid"]:
            raise tc.SessionMismatch("alt cemetery_uid/organizație decât cele ale transferului")
        snap = mf.local_snapshot(self.source)
        digest = mf.build_manifest("local", snap)["content_digest"]
        if digest != self.meta["source_manifest_digest"]:
            raise tc.SourceDrift("sursa locală s-a schimbat față de manifestul planului; generează un plan nou")
        return snap

    def _wire_entity_rows(self, entity: str, snap: Dict[str, Any], mapping: Dict[Tuple[str, str], str]) -> List[Dict[str, Any]]:
        sp, out = ents.ENTITIES[entity], []
        for r in snap["entities"].get(entity, []):
            data = {col: ents.coerce(kind, r["data"].get(col)) for col, kind in sp["cols"].items()}
            for wire, (parent, pref) in r["fks"].items():
                if pref in (None, ""):
                    data[wire] = None
                elif (parent, str(pref)) in mapping:
                    data[wire] = mapping[(parent, str(pref))]
                else:
                    raise tc.OrphanFK(f"sursa are FK orfan: {entity}.{wire} → {parent} {pref}")
            out.append({"uid": mapping[(entity, _ref(entity, r))], "version": 1, "data": data})
        return out

    def _wire_media_rows(self, snap: Dict[str, Any], mapping: Dict[Tuple[str, str], str]) -> List[Dict[str, Any]]:
        out, first_by_sha = [], {}
        chunk = int(self.meta["chunk_size"])
        for item in snap["media"]:
            uid = mapping[(tc.MEDIA, item["_local_ref"])]
            chunks: List[Dict[str, Any]] = []
            if item["present"]:
                whole = hashlib.sha256()
                with open(item["_path"], "rb") as fh:
                    n = 0
                    for block in iter(lambda: fh.read(chunk), b""):
                        n += 1
                        whole.update(block)
                        chunks.append({"n": n, "sha256": hashlib.sha256(block).hexdigest(), "size": len(block)})
                if whole.hexdigest() != item["sha256"]:
                    raise tc.SourceDrift(f"fișier media modificat în timpul transferului: {item['name']}")
            dedupe = first_by_sha.setdefault(item["sha256"], uid) if item["present"] else None
            data = {k: item.get(k) for k in ("kind", "entity_type", "entity_id", "name", "recorded_sha256", "present", "size", "sha256", "status")}
            data.update(chunks=chunks, chunk_count=len(chunks), dedupe_of=None if dedupe in (None, uid) else dedupe, published=False)
            out.append({"uid": uid, "version": 1, "data": data})
        return out

    def _batches(self, snap: Dict[str, Any], mapping: Optional[Dict[Tuple[str, str], str]] = None) -> List[Tuple[str, int, int, List[Dict[str, Any]]]]:
        snap = canonicalize(snap)
        mapping = mapping if mapping is not None else self.state.mapping()
        size = int(self.meta["batch_size"])
        plan = self.meta["entity_plan"]
        out = []
        for entity in tc.ENTITY_ORDER:
            if entity not in plan:
                continue
            rows = self._wire_media_rows(snap, mapping) if entity == tc.MEDIA else self._wire_entity_rows(entity, snap, mapping)
            count = plan[entity]
            if -(-len(rows) // size) != count:
                raise tc.SourceDrift("numărul de batch-uri nu mai corespunde planului")
            for no in range(1, count + 1):
                out.append((entity, no, count, rows[(no - 1) * size: no * size]))
        expected = self.meta.get("batch_digests")
        if expected is not None:
            got: Dict[str, List[str]] = {}
            for entity, _no, _count, rows in out:
                got.setdefault(entity, []).append(tc.batch_sha256(rows))
            if got != expected:
                raise tc.SourceDrift("partiționarea în batch-uri nu mai coincide cu cea a planului (sursă schimbată sau mapping alterat)")
        return out

    # ------------------------------------------------------------ rulare / resume
    def open_staging(self) -> Dict[str, Any]:
        return cs.staging_open(self.cp, self.actor, self.meta["organization_uid"], self.meta["cemetery_uid"], self.bootstrap_id, self.session, self.meta["entity_plan"])

    def status(self) -> Dict[str, Any]:
        return cs.staging_status(self.cp, self.actor, self.meta["organization_uid"], self.meta["cemetery_uid"], self.bootstrap_id)

    def send(self, entity: str, batch_no: int, snap: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Trimite (sau retrimite — idempotent) un batch anume."""
        snap = snap or self._drift_check()
        for ent, no, count, rows in self._batches(snap):
            if ent == entity and no == batch_no:
                contract = tc.make_contract(self.session, self.bootstrap_id, ent, no, count, rows)
                return cs.staging_put_batch(self.cp, self.actor, self.meta["organization_uid"], self.meta["cemetery_uid"], contract, rows)
        raise tc.ContractError(f"batch inexistent: {entity}#{batch_no}")

    def run(self, after_batch: Optional[Callable[[str, int], None]] = None, hook: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        hook = hook or (lambda phase: None)
        snap = self._drift_check()
        self.open_staging()
        done = self.status()
        stored = skipped = 0
        for entity, no, count, rows in self._batches(snap):
            if no in done["entities"][entity]["received"]:
                skipped += 1
                continue
            contract = tc.make_contract(self.session, self.bootstrap_id, entity, no, count, rows)
            hook(f"before:{entity}:{no}")
            res = cs.staging_put_batch(self.cp, self.actor, self.meta["organization_uid"], self.meta["cemetery_uid"], contract, rows)
            stored += res["status"] == "stored"
            hook(f"after:{entity}:{no}")
            if after_batch:
                after_batch(entity, no)
        hook("before_verify")
        self._drift_check()  # sursa nu s-a schimbat nici în timpul transferului
        report = {"bootstrap_id": self.bootstrap_id, "stored": stored, "skipped_already_staged": skipped, "status": self.status()}
        report.update(self.verify())
        report["media"] = self.media_report(snap)
        return report

    def verify(self) -> Dict[str, Any]:
        snap = cs.staging_snapshot(self.cp, self.actor, self.meta["organization_uid"], self.meta["cemetery_uid"], self.bootstrap_id)
        m = mf.build_manifest("staging", snap)
        complete = self.status()["complete"]
        return {"staging_content_digest": m["content_digest"], "verified": bool(complete and m["content_digest"] == self.meta["source_manifest_digest"]),
                "staging_rows": m["total_rows"], "staging_orphans": m["integrity"]["orphan_total"]}

    def upload_media(self, hook: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        """Încarcă în simulator (pe bucăți, idempotent, reluabil) fișierele media PREZENTE; duplicatele după SHA se încarcă fizic o singură dată."""
        import cimitir_cloud_media as cm
        hook = hook or (lambda phase: None)
        snap = canonicalize(self._drift_check())
        rows = self._wire_media_rows(snap, self.state.mapping())
        sent = skipped = objects = 0
        org, cem = self.meta["organization_uid"], self.meta["cemetery_uid"]
        for item, row in zip(snap["media"], rows):
            d = row["data"]
            if not d["present"] or d["dedupe_of"]:
                continue
            objects += 1
            with open(item["_path"], "rb") as fh:
                for ch in d["chunks"]:
                    block = fh.read(int(self.meta["chunk_size"]))
                    hook(f"media:before_chunk:{ch['n']}")
                    res = cm.media_put_chunk(self.cp, self.actor, org, cem, self.bootstrap_id, row["uid"], ch["n"], block, ch["sha256"])
                    sent += res["status"] == "stored"
                    skipped += res["status"] == "duplicate"
                    hook(f"media:after_chunk:{ch['n']}")
            cm.media_finalize(self.cp, self.actor, org, cem, self.bootstrap_id, row["uid"])
        return {"objects": objects, "chunks_sent": sent, "chunks_already_present": skipped}

    def media_report(self, snap: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        snap = snap or mf.local_snapshot(self.source)
        s = mf.media_summary(snap["media"])
        return {"count": s["count"], "bytes": s["bytes"], "digest": s["digest"], "missing": [i["name"] for i in s["items"] if not i["present"]],
                "duplicates": s["duplicates"], "unique_objects": len({i["sha256"] for i in s["items"] if i["present"]}), "published": False, "uploaded": False}


def start_transfer(binding, cp, actor: str, plan: Dict[str, Any], confirm_hash: str, bootstrap_id: str, batch_size: int = DEFAULT_BATCH_SIZE,
                   chunk_size: int = DEFAULT_CHUNK_SIZE, clock: Callable[[], float] = time.time, source=None) -> Transfer:
    """Pornește (sau re-pornește idempotent) un transfer DOAR dintr-un plan valid și neexpirat cu decizia B. Nu scrie nimic în live."""
    uuid.UUID(bootstrap_id)
    if source is None:  # cu `source` (snapshot Flux B) poarta planului a fost trecută de orchestrator, SUB lock, înaintea tăierii
        pf.gate_execution(plan, binding, cp, actor, confirm_hash, clock, allowed=("B",))
    elif confirm_hash != plan["plan_hash"] or plan["decision"] != "B":
        raise tc.SessionMismatch("plan neconfirmat sau cu altă decizie decât B")
    state = StateStore(binding, bootstrap_id)
    if state.exists():
        meta = state.meta()
        if meta["plan_hash"] != plan["plan_hash"]:
            raise tc.SessionMismatch("plan_hash diferă de cel al transferului existent")
        return Transfer(binding, cp, actor, state, meta, source)
    state.delete()  # rest parțial al unui `init` întrerupt
    snap = mf.local_snapshot(source or binding)
    if mf.build_manifest("local", snap)["content_digest"] != plan["local_manifest"]["content_digest"]:
        raise tc.SourceDrift("sursa locală diferă de manifestul planului")
    snap = canonicalize(snap)
    meta = {"bootstrap_id": bootstrap_id, "plan_hash": plan["plan_hash"], "organization_uid": binding.organization_uid, "cemetery_uid": binding.cemetery_uid,
            "source_manifest_digest": plan["local_manifest"]["content_digest"], "schema_version": plan["local_manifest"]["schema_version"],
            "data_epoch": plan["cloud_manifest"]["data_epoch"] or 1, "batch_size": batch_size, "chunk_size": chunk_size,
            "entity_plan": _plan_batches(snap, batch_size)}
    mapping = _build_mapping(bootstrap_id, binding.cemetery_uid, snap, ident.existing_identities(binding), ident.existing_media_identities(binding))
    digests: Dict[str, List[str]] = {}
    for entity, _no, _count, rows in Transfer(binding, cp, actor, state, meta, source)._batches(snap, mapping):
        digests.setdefault(entity, []).append(tc.batch_sha256(rows))
    meta["batch_digests"] = digests
    state.init(meta, mapping)
    return Transfer(binding, cp, actor, state, meta, source)


def resume_transfer(binding, cp, actor: str, bootstrap_id: str, plan_hash: str, source=None) -> Transfer:
    """Reia un transfer întrerupt (alt proces/altă instanță): citește mapping-ul persistat; refuză plan/cimitir/sursă schimbate."""
    state = StateStore(binding, bootstrap_id)
    if not state.exists():
        raise tc.SessionMismatch("nu există stare locală pentru acest bootstrap în acest registru")
    meta = state.meta()
    if meta["plan_hash"] != plan_hash:
        raise tc.SessionMismatch("plan_hash diferă de cel al transferului")
    t = Transfer(binding, cp, actor, state, meta, source)
    t._drift_check()
    return t


def abort_transfer(binding, cp, actor: str, bootstrap_id: str) -> Dict[str, int]:
    """Anulare: șterge staging-ul acestui bootstrap (cloud) și starea locală. Live rămâne neatins."""
    out = cs.staging_cleanup(cp, actor, binding.organization_uid, binding.cemetery_uid, bootstrap_id)
    StateStore(binding, bootstrap_id).delete()
    return out


def promote_transfer(binding, bootstrap_id: str, bootstrap_completed: bool, hook: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Promovează atomic mapping-ul temporar în identitatea durabilă (`cloud_entity_identity`). Apelat de Flux B DUPĂ finalizarea bootstrap-ului.
    Idempotent: dacă martorul de promovare există (ex. crash după commit), nu face nimic."""
    if not bootstrap_completed:
        raise tc.TransferBlocked("identitatea se promovează doar după finalizarea bootstrap-ului")
    status = ident.promotion_status(binding, bootstrap_id)
    if status["promoted"]:
        return {"promoted": True, "already": True, "row_count": status["row_count"]}
    state = StateStore(binding, bootstrap_id)
    if not state.exists():
        raise tc.TransferBlocked("nu există nici mapping temporar, nici martor de promovare")
    meta = state.meta()
    return ident.promote(binding, bootstrap_id, state.mapping(), meta["cemetery_uid"], hook=hook)


def release_local_state(binding, bootstrap_id: str, bootstrap_completed: bool) -> None:
    """Mapping-ul temporar se șterge DOAR după finalizarea bootstrap-ului ȘI după promovarea lui în identitatea durabilă."""
    if not bootstrap_completed:
        raise tc.TransferBlocked("mapping-ul se păstrează până la finalizarea bootstrap-ului")
    if not ident.promotion_status(binding, bootstrap_id)["promoted"]:
        raise tc.TransferBlocked("mapping-ul nu a fost promovat în identitatea durabilă; nu se șterge")
    StateStore(binding, bootstrap_id).delete()
