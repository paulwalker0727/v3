"""cimitir_snapshot.py — punctul de tăiere (cut) local ATOMIC pentru Flux B (Etapa 4, Pasul 4).

Sub un lock de scriere SQLite (`BEGIN IMMEDIATE`, MAIN rămâne citibil): (1) copie consistentă a bazei (backup API) + copierea fișierelor media într-un snapshot separat,
(2) verificare: manifestul snapshot-ului == manifestul planului și FK orfane = 0, (3) outbox-ul existent este ARHIVAT (`cloud_sync_outbox_archive`) și pornește un outbox nou
(ID-urile nu se reutilizează), (4) rândul `bootstrap_cuts` — autoritatea cutului — toate în aceeași tranzacție. Un director de snapshot fără rând `bootstrap_cuts` este gunoi
(cădere înainte de COMMIT) și se refolosește/șterge. Transferul B citește DOAR snapshot-ul; ce se modifică în MAIN după cut rămâne în noul outbox, pentru Sync V2.
Rollback: outbox-ul arhivat se restaurează (cu ID-urile originale), alături de intrările noi.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import cimitir_manifest as mf
import cimitir_registry as reg
import cimitir_transfer_contract as tc

_DDL = """
CREATE TABLE IF NOT EXISTS bootstrap_cuts (bootstrap_id TEXT PRIMARY KEY, cemetery_uid TEXT NOT NULL, cut_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'active'
  CHECK (state IN ('active','finalized','rolled_back')), snapshot_digest TEXT NOT NULL, outbox_archived INTEGER NOT NULL, outbox_max_id INTEGER NOT NULL,
  media_count INTEGER NOT NULL, media_bytes INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS cloud_sync_outbox_archive (bootstrap_id TEXT NOT NULL, row_id INTEGER NOT NULL, row_json TEXT NOT NULL, archived_at TEXT NOT NULL,
  PRIMARY KEY (bootstrap_id, row_id));
CREATE TABLE IF NOT EXISTS cloud_sync_outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, tracking_code TEXT, status TEXT, note TEXT, created_at TEXT, op_id TEXT, kind TEXT,
  payload TEXT, attempts INTEGER DEFAULT 0, last_error TEXT, entity TEXT, entity_uid TEXT, action TEXT, base_version INTEGER DEFAULT 0, state TEXT DEFAULT 'pending', conflict TEXT);
"""


class SnapshotError(tc.TransferBlocked):
    pass


def snapshot_binding(binding, bootstrap_id: str) -> reg.Binding:
    return reg.Binding(organization_uid=binding.organization_uid, cemetery_uid=binding.cemetery_uid, root=Path(binding.root) / "bootstrap_snapshots" / bootstrap_id)


def _connect(binding) -> sqlite3.Connection:
    c = sqlite3.connect(binding.database, timeout=15, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout = 15000")
    return c


def cut_info(binding, bootstrap_id: str) -> Optional[Dict[str, Any]]:
    c = mf.ro_connect(binding.database)
    try:
        if "bootstrap_cuts" not in mf._tables(c):
            return None
        r = c.execute("SELECT * FROM bootstrap_cuts WHERE bootstrap_id=? AND cemetery_uid=?", (bootstrap_id, binding.cemetery_uid)).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


def archived_outbox(binding, bootstrap_id: str) -> list:
    c = mf.ro_connect(binding.database)
    try:
        if "cloud_sync_outbox_archive" not in mf._tables(c):
            return []
        return [json.loads(r["row_json"]) for r in c.execute("SELECT row_json FROM cloud_sync_outbox_archive WHERE bootstrap_id=? ORDER BY row_id", (bootstrap_id,))]
    finally:
        c.close()


def _digest(sb) -> str:
    return mf.build_manifest("local", mf.local_snapshot(sb))["content_digest"]


def remove_snapshot(binding, bootstrap_id: str) -> None:
    sb = snapshot_binding(binding, bootstrap_id)
    for p in (sb.root, sb.root.with_name(bootstrap_id + ".tmp")):
        shutil.rmtree(p, ignore_errors=True)
    try:
        sb.root.parent.rmdir()
    except OSError:
        pass


def _copy_media(binding, live: Dict[str, Any], tmp: Path, dconn: sqlite3.Connection) -> None:
    media_dir = Path(binding.media)
    for i, item in enumerate(live["media"]):
        if not item["_path"]:
            continue
        fp = item["_file_path"]
        p = Path(fp)
        src = Path(item["_path"])
        if not p.is_absolute() and ".." not in p.parts and src == media_dir / p:
            dst = tmp / "media" / p
        elif not p.is_absolute() and ".." not in p.parts and src == Path(binding.root) / p:
            dst = tmp / p
        else:
            rel = Path("_ext") / str(i) / item["name"]
            dst = tmp / "media" / rel
            kind, _, rid = item["_local_ref"].partition(":")
            dconn.execute(f"UPDATE {'documents' if kind == 'document' else 'photos'} SET file_path=? WHERE id=?", (rel.as_posix(), int(rid)))
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def take_cut(binding, bootstrap_id: str, expected_digest: str, hook: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Idempotent: dacă cutul există (`active`) întoarce informațiile lui după re-verificarea snapshot-ului; altfel îl execută atomic."""
    hook = hook or (lambda phase: None)
    sb = snapshot_binding(binding, bootstrap_id)
    info = cut_info(binding, bootstrap_id)
    if info:
        if info["state"] != "active":
            raise SnapshotError(f"cutul bootstrap-ului este {info['state']}")
        if not sb.database.exists() or _digest(sb) != expected_digest:
            raise SnapshotError("snapshot-ul cutului lipsește sau a fost alterat")
        return {**info, "reused": True}
    tmp = sb.root.with_name(bootstrap_id + ".tmp")
    remove_snapshot(binding, bootstrap_id)  # gunoi de la o cădere înainte de COMMIT
    c = _connect(binding)
    try:
        c.executescript(_DDL)
        c.execute("BEGIN IMMEDIATE")
        hook("snapshot:locked")
        tmp.mkdir(parents=True)
        src, dst = sqlite3.connect(binding.database, timeout=15), sqlite3.connect(tmp / reg.DB_NAME)
        try:
            src.backup(dst)
        finally:
            src.close()
        live = mf.local_snapshot(binding)
        _copy_media(binding, live, tmp, dst)
        dst.commit()
        dst.close()
        man = mf.build_manifest("local", mf.local_snapshot(reg.Binding(binding.organization_uid, binding.cemetery_uid, tmp)))
        if man["content_digest"] != expected_digest:
            raise tc.SourceDrift("snapshot-ul diferă de manifestul planului (sursa s-a schimbat între plan și tăiere)")
        if man["integrity"]["orphan_total"] or man["integrity"]["fk_check_violations"]:
            raise tc.OrphanFK("snapshot-ul are chei străine invalide")
        sb.root.parent.mkdir(parents=True, exist_ok=True)
        tmp.replace(sb.root)
        now = datetime.now(timezone.utc).isoformat()
        rows = c.execute("SELECT * FROM cloud_sync_outbox ORDER BY id").fetchall()
        for r in rows:
            c.execute("INSERT OR REPLACE INTO cloud_sync_outbox_archive VALUES (?,?,?,?)", (bootstrap_id, r["id"], json.dumps(dict(r), sort_keys=True), now))
        c.execute("DELETE FROM cloud_sync_outbox")
        c.execute("INSERT INTO bootstrap_cuts VALUES (?,?,?,?,?,?,?,?,?)", (bootstrap_id, binding.cemetery_uid, now, "active", man["content_digest"], len(rows),
                                                                             max([r["id"] for r in rows], default=0), man["media"]["count"], man["media"]["bytes"]))
        hook("snapshot:in_txn")
        c.execute("COMMIT")
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        remove_snapshot(binding, bootstrap_id)
        raise
    finally:
        c.close()
    hook("snapshot:after_commit")
    return {**cut_info(binding, bootstrap_id), "reused": False}


def restore_outbox(binding, bootstrap_id: str) -> int:
    """Rollback: readuce în outbox intrările arhivate (ID-urile originale; dacă un ID e ocupat, primește unul nou), păstrând intrările apărute după cut."""
    c = _connect(binding)
    try:
        c.executescript(_DDL)
        c.execute("BEGIN IMMEDIATE")
        cut = c.execute("SELECT state FROM bootstrap_cuts WHERE bootstrap_id=?", (bootstrap_id,)).fetchone()
        if not cut or cut["state"] != "active":
            c.execute("COMMIT")
            return 0
        cols = [r["name"] for r in c.execute("PRAGMA table_info(cloud_sync_outbox)")]
        n = 0
        for r in c.execute("SELECT row_json FROM cloud_sync_outbox_archive WHERE bootstrap_id=? ORDER BY row_id", (bootstrap_id,)).fetchall():
            d = {k: v for k, v in json.loads(r["row_json"]).items() if k in cols}
            try:
                c.execute(f"INSERT INTO cloud_sync_outbox ({','.join(d)}) VALUES ({','.join('?' * len(d))})", list(d.values()))
            except sqlite3.IntegrityError:
                d.pop("id", None)
                c.execute(f"INSERT INTO cloud_sync_outbox ({','.join(d)}) VALUES ({','.join('?' * len(d))})", list(d.values()))
            n += 1
        c.execute("DELETE FROM cloud_sync_outbox_archive WHERE bootstrap_id=?", (bootstrap_id,))
        c.execute("UPDATE bootstrap_cuts SET state='rolled_back' WHERE bootstrap_id=?", (bootstrap_id,))
        c.execute("COMMIT")
        return n
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        c.close()


def finalize_cut(binding, bootstrap_id: str) -> None:
    """După publicare: cutul devine definitiv (arhiva outbox rămâne ca dovadă); idempotent."""
    c = _connect(binding)
    try:
        c.executescript(_DDL)
        c.execute("UPDATE bootstrap_cuts SET state='finalized' WHERE bootstrap_id=? AND state='active'", (bootstrap_id,))
    finally:
        c.close()
    remove_snapshot(binding, bootstrap_id)
