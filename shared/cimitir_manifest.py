"""cimitir_manifest.py — manifest canonic (Etapa 4, Pasul 2): aceeași structură pentru MAIN (SQLite local) și cloud (simulator local).

Doar CITIRE (conexiuni `immutable`/`mode=ro`): nu creează tabele, nu atribuie uid-uri, nu golește outbox. Nicio dependență de rețea.

Canonicalizare (deterministă):
- fiecare rând → JSON canonic (coloane normalizate cu aceleași reguli ca sincronizarea: None≡'' la text, float rotunjit la 6, JSON canonic);
- cheile străine intră ca *hash de conținut al părintelui* (nu id local, nu uid): două baze cu aceleași date au aceleași digest-uri chiar dacă
  id-urile/uid-urile diferă; un FK către un părinte inexistent intră ca `ORPHAN` și se raportează;
- `id`, `uid`, `version`, `updated_at`/sync_version NU intră în digestul de conținut (sunt metadate de sincronizare, nu date reale);
  `uid_digest` (uid|conținut) se calculează separat, doar dacă toate rândurile au uid;
- digest per entitate = SHA-256 peste lista SORTATĂ a hash-urilor de rând (ordinea fizică nu contează);
- `generated_at` este metadată: exclus din `stable_digest`.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import cimitir_entities as ents

MANIFEST_VERSION = 1
PROTOCOL_VERSION = "entity-sync-v1"
LOCAL_SCHEMA_VERSION = "local-v1"
EMPTY, METADATA_ONLY, NONEMPTY = "EMPTY", "METADATA_ONLY", "NONEMPTY"


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ canonicalizare pe entități
def _norm_cols(entity: str, data: Dict[str, Any]) -> Dict[str, Any]:
    sp = ents.ENTITIES[entity]
    return {col: ents._norm(kind, ents.coerce(kind, data.get(col))) for col, kind in sp["cols"].items()}


def entity_summary(entity: str, rows: List[Dict[str, Any]], hash_by_ref: Dict[str, Dict[Any, str]]) -> Dict[str, Any]:
    """rows: {"ref","uid","version","data","fks":{wire:(parent_entity, parent_ref|None)}}. Completează `hash_by_ref[entity]`."""
    hashes: List[str] = []
    uid_lines: List[str] = []
    orphans = 0
    with_uid = 0
    max_version = 0
    refs = hash_by_ref.setdefault(entity, {})
    for r in rows:
        fk_hashes = {}
        for wire, (parent, pref) in (r.get("fks") or {}).items():
            if pref in (None, ""):
                fk_hashes[wire] = None
            elif pref in hash_by_ref.get(parent, {}):
                fk_hashes[wire] = hash_by_ref[parent][pref]
            else:
                fk_hashes[wire] = "ORPHAN"
                orphans += 1
        h = sha({"e": entity, "d": _norm_cols(entity, r["data"]), "f": fk_hashes})
        refs[r["ref"]] = h
        hashes.append(h)
        if r.get("uid"):
            with_uid += 1
            uid_lines.append(f"{r['uid']}|{h}")
        max_version = max(max_version, int(r.get("version") or 0))
    hashes.sort()
    return {"count": len(rows), "digest": sha(hashes), "uid_digest": sha(sorted(uid_lines)) if rows and with_uid == len(rows) else None,
            "uid_coverage": with_uid, "max_version": max_version, "orphans": orphans}


def media_summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    items = sorted(({k: v for k, v in i.items() if not k.startswith("_")} for i in items),  # cheile `_*` (ex. `_path`) nu intră în manifest
                   key=lambda i: (i["kind"], i["name"], i.get("sha256") or "", i.get("entity_type") or "", str(i.get("entity_id") or "")))
    lines, present_by_hash, total, missing, mismatch = [], {}, 0, 0, 0
    for i in items:
        if i["present"]:
            lines.append(f"{i['sha256']}|{i['size']}|{i['name']}")
            present_by_hash[i["sha256"]] = present_by_hash.get(i["sha256"], 0) + 1
            total += int(i["size"] or 0)
        else:
            lines.append(f"MISSING|{i.get('recorded_sha256') or ''}|{i['name']}")
            missing += 1
        if i.get("status") in ("hash_mismatch", "size_mismatch"):
            mismatch += 1
    lines.sort()
    return {"count": len(items), "bytes": total, "digest": sha(lines), "missing": missing, "mismatch": mismatch,
            "duplicates": [{"sha256": h, "count": n} for h, n in sorted(present_by_hash.items()) if n > 1], "items": items}


def classify(entities_rows: int, media_count: int, outbox_total: int, open_conflicts: int, tombstones: int) -> str:
    if entities_rows or media_count:
        return NONEMPTY
    if outbox_total or open_conflicts or tombstones:
        return METADATA_ONLY
    return EMPTY


def stable_digest(manifest: Dict[str, Any]) -> str:
    return sha({k: v for k, v in manifest.items() if k not in ("generated_at", "stable_digest")})


def build_manifest(side: str, snap: Dict[str, Any]) -> Dict[str, Any]:
    """snap: instantaneu brut (vezi `local_snapshot` / `LocalControlPlane.export_snapshot`) → manifest cu `stable_digest`."""
    hash_by_ref: Dict[str, Dict[Any, str]] = {}
    entities: Dict[str, Any] = {}
    orphans: Dict[str, int] = {}
    for entity in ents.ORDER:
        summary = entity_summary(entity, snap["entities"].get(entity, []), hash_by_ref)
        orphans[entity] = summary.pop("orphans")
        entities[entity] = summary
    media = media_summary(snap["media"])
    total_rows = sum(e["count"] for e in entities.values())
    outbox = snap.get("outbox")
    m: Dict[str, Any] = {
        "manifest_version": MANIFEST_VERSION, "side": side, "organization_uid": snap["organization_uid"], "cemetery_uid": snap["cemetery_uid"],
        "exists": bool(snap["exists"]), "data_epoch": snap.get("data_epoch"), "schema_version": snap["schema_version"],
        "app_version": snap.get("app_version"), "protocol_version": PROTOCOL_VERSION, "cemetery_status": snap.get("status"),
        "sync_state": snap.get("sync_state"), "entities": entities, "total_rows": total_rows,
        "entities_digest": sha({k: v["digest"] for k, v in entities.items()}),
        "media": media, "content_digest": None,
        "integrity": {"orphans": orphans, "orphan_total": sum(orphans.values()), "fk_check_violations": snap.get("fk_check_violations")},
        "outbox": outbox, "open_conflicts": int(snap.get("open_conflicts") or 0), "tombstones": int(snap.get("tombstones") or 0),
        "change_seq": snap.get("change_seq"),
    }
    m["content_digest"] = sha({"entities": m["entities_digest"], "media": media["digest"]})
    m["classification"] = classify(total_rows, media["count"], (outbox or {}).get("total", 0), m["open_conflicts"], m["tombstones"])
    m["generated_at"] = datetime.now(timezone.utc).isoformat()
    m["stable_digest"] = stable_digest(m)
    return m


def diff_manifests(a: Dict[str, Any], b: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for k in sorted(set(a) | set(b)):
        if k in ("generated_at", "stable_digest", "entities", "media", "integrity"):
            continue
        if a.get(k) != b.get(k):
            out.append(k)
    for e in ents.ORDER:
        if a["entities"][e] != b["entities"][e]:
            out.append(f"entities.{e}")
    if a["media"]["digest"] != b["media"]["digest"] or a["media"]["items"] != b["media"]["items"]:
        out.append("media")
    if a["integrity"] != b["integrity"]:
        out.append("integrity")
    return out


# ------------------------------------------------------------------ citire locală (read-only)
def ro_connect(path: Path) -> sqlite3.Connection:
    """`immutable=1` dacă nu există WAL cu conținut (zero fișiere create/atinse); altfel `mode=ro` (WAL activ al unei aplicații vii)."""
    p = Path(path).resolve()
    wal = Path(str(p) + "-wal")
    mode = "immutable=1" if (not wal.exists() or wal.stat().st_size == 0) else "mode=ro"
    c = sqlite3.connect(f"{p.as_uri()}?{mode}", uri=True, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def _tables(c) -> set:
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _cols(c, table: str) -> set:
    return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


def _resolve_media(binding, file_path: str) -> Optional[Path]:
    if not file_path:
        return None
    p = Path(file_path)
    for cand in ([p] if p.is_absolute() else [Path(binding.media) / p, Path(binding.root) / p]):
        if cand.is_file():
            return cand
    return None


def _media_item(binding, kind: str, entity_type: str, entity_id: Any, file_path: str, rec_sha: Optional[str], rec_size: Any, local_ref: str = "") -> Dict[str, Any]:
    name = (file_path or "").replace("\\", "/").rsplit("/", 1)[-1] or "fisier"
    f = _resolve_media(binding, file_path or "")
    item = {"kind": kind, "entity_type": entity_type or "", "entity_id": str(entity_id if entity_id is not None else ""), "name": name,
            "recorded_sha256": rec_sha or None, "present": f is not None, "size": None, "sha256": None, "status": "missing",
            "_path": str(f) if f is not None else None, "_local_ref": local_ref, "_file_path": file_path or ""}
    if f is not None:
        item["size"], item["sha256"] = f.stat().st_size, file_sha256(f)
        item["status"] = "ok"
        if rec_sha and rec_sha != item["sha256"]:
            item["status"] = "hash_mismatch"
        elif rec_size not in (None, "") and int(rec_size) != item["size"]:
            item["status"] = "size_mismatch"
    return item


def local_snapshot(binding) -> Dict[str, Any]:
    import cimitir_config
    c = ro_connect(binding.database)
    try:
        tables = _tables(c)
        snap: Dict[str, Any] = {"exists": True, "organization_uid": binding.organization_uid, "cemetery_uid": binding.cemetery_uid, "status": None,
                                "schema_version": LOCAL_SCHEMA_VERSION, "entities": {}, "media": [], "tombstones": 0, "open_conflicts": 0,
                                "app_version": cimitir_config.app_version()}
        settings = {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM settings")} if "settings" in tables else {}
        snap["sync_state"] = settings.get("sync_state") or "UNBOUND"
        ep, seq = settings.get("sync_epoch"), settings.get("last_change_seq")
        snap["data_epoch"] = int(ep) if ep and str(ep).isdigit() else None
        snap["change_seq"] = int(seq) if seq and str(seq).isdigit() else None
        for entity in ents.ORDER:
            if entity not in tables:
                continue
            sp, present = ents.ENTITIES[entity], _cols(c, entity)
            data_cols = [col for col in sp["cols"] if col in present]
            fk_cols = [col for col in sp["fks"] if col in present]
            sel = ["id"] + [x for x in ("uid", "version") if x in present] + data_cols + fk_cols
            rows = []
            for r in c.execute(f"SELECT {', '.join(sel)} FROM {entity} ORDER BY id"):
                d = dict(r)
                rows.append({"ref": d["id"], "uid": d.get("uid") or None, "version": d.get("version") or 0,
                             "data": {col: d.get(col) for col in sp["cols"]},
                             "fks": {sp["fks"][col][1]: (sp["fks"][col][0], d.get(col)) for col in fk_cols}})
            snap["entities"][entity] = rows
        if "documents" in tables:
            pc = _cols(c, "documents")
            for r in c.execute(f"SELECT id, entity_type, entity_id, file_path{', sha256' if 'sha256' in pc else ''}{', size' if 'size' in pc else ''} FROM documents ORDER BY id"):
                d = dict(r)
                snap["media"].append(_media_item(binding, "document", d["entity_type"], d["entity_id"], d["file_path"], d.get("sha256"), d.get("size"), f"document:{d['id']}"))
        if "photos" in tables:
            for r in c.execute("SELECT id, cod_loc, file_path FROM photos ORDER BY id"):
                snap["media"].append(_media_item(binding, "photo", "locuri_de_veci", r["cod_loc"], r["file_path"], None, None, f"photo:{r['id']}"))
        by = {"pending": 0, "conflict": 0, "error": 0}
        total = 0
        if "cloud_sync_outbox" in tables:
            total = c.execute("SELECT COUNT(*) FROM cloud_sync_outbox").fetchone()[0]
            if "state" in _cols(c, "cloud_sync_outbox"):
                for r in c.execute("SELECT COALESCE(state,'pending') AS s, COUNT(*) AS n FROM cloud_sync_outbox GROUP BY 1"):
                    if r["s"] in by:
                        by[r["s"]] = r["n"]
            else:
                by["pending"] = total
        snap["outbox"] = {"total": total, **by}
        snap["open_conflicts"] = by["conflict"]
        snap["fk_check_violations"] = len(c.execute("PRAGMA foreign_key_check").fetchall())
        return snap
    finally:
        c.close()


def local_manifest(binding) -> Dict[str, Any]:
    return build_manifest("local", local_snapshot(binding))


# ------------------------------------------------------------------ citire cloud (simulator, read-only)
def cloud_snapshot(cp, actor: str, organization_uid: str, cemetery_uid: str) -> Dict[str, Any]:
    snap = dict(cp.export_snapshot(actor, organization_uid, cemetery_uid))
    snap.update(app_version=None, sync_state=None, outbox=None, fk_check_violations=None)
    entities: Dict[str, List[Dict[str, Any]]] = {}
    for entity, rows in snap["entities"].items():
        if entity not in ents.ENTITIES:
            continue
        sp = ents.ENTITIES[entity]
        entities[entity] = [{"ref": r["uid"], "uid": r["uid"], "version": r["version"], "data": r["data"],
                             "fks": {wire: (parent, r["data"].get(wire)) for _col, (parent, wire) in sp["fks"].items()}} for r in rows]
    snap["entities"] = entities
    snap["media"] = [{"kind": m["kind"], "entity_type": m["entity_type"] or "", "entity_id": m["entity_id"] or "", "name": m["name"], "recorded_sha256": m["sha256"],
                      "present": m["present"], "size": m["size"] if m["present"] else None, "sha256": m["sha256"] if m["present"] else None,
                      "status": "ok" if m["present"] else "missing"} for m in snap["media"]]
    return snap


def cloud_manifest(cp, actor: str, organization_uid: str, cemetery_uid: str) -> Dict[str, Any]:
    return build_manifest("cloud", cloud_snapshot(cp, actor, organization_uid, cemetery_uid))
