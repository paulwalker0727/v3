"""cimitir_identity.py — identitatea DURABILĂ local entity ↔ cloud_uid (Etapa 4, Pasul 3.5), pentru Sync V2.

Soluție: tabel separat `cloud_entity_identity` în baza registrului (nu modifică PK-urile aplicației și nu adaugă coloane pe entități; intră în backup-ul
registrului), scoped pe `cemetery_uid`. `local_ref` = cheia primară locală (AUTOINCREMENT, nu se reutilizează) a entității.
Garanții: (cemetery_uid, entity_type, local_ref) are cel mult un cloud_uid ACTIV și (cemetery_uid, entity_type, cloud_uid) cel mult un local_ref ACTIV (indecși unici parțiali).
Promovarea mapping-ului temporar (bootstrap_state/<id>.sqlite) în acest tabel este O SINGURĂ tranzacție SQLite împreună cu rândul-martor din
`cloud_identity_promotions`; după crash, martorul spune dacă promovarea s-a făcut. Media NU se promovează aici (identitatea media se atribuie la publicare).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

import cimitir_entities as ents
import cimitir_manifest as mf
import cimitir_transfer_contract as tc

_DDL = """
CREATE TABLE IF NOT EXISTS cloud_entity_identity (
  cemetery_uid TEXT NOT NULL, entity_type TEXT NOT NULL, local_ref TEXT NOT NULL, cloud_uid TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','retired')),
  source_bootstrap_id TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS cei_active_ref ON cloud_entity_identity (cemetery_uid, entity_type, local_ref) WHERE status='active';
CREATE UNIQUE INDEX IF NOT EXISTS cei_active_uid ON cloud_entity_identity (cemetery_uid, entity_type, cloud_uid) WHERE status='active';
CREATE TABLE IF NOT EXISTS cloud_identity_promotions (
  bootstrap_id TEXT PRIMARY KEY, cemetery_uid TEXT NOT NULL, row_count INTEGER NOT NULL, promoted_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cloud_media_identity (
  cemetery_uid TEXT NOT NULL, local_ref TEXT NOT NULL, cloud_uid TEXT NOT NULL, bootstrap_id TEXT NOT NULL, created_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','active','retired')), PRIMARY KEY (cemetery_uid, local_ref, bootstrap_id));
CREATE UNIQUE INDEX IF NOT EXISTS cmi_active_ref ON cloud_media_identity (cemetery_uid, local_ref) WHERE status='active';
CREATE UNIQUE INDEX IF NOT EXISTS cmi_active_uid ON cloud_media_identity (cemetery_uid, cloud_uid) WHERE status='active';
"""


class IdentityConflict(tc.TransferBlocked):
    pass


def existing_identities(binding) -> Dict[Tuple[str, str], str]:
    """Citire read-only: identitățile active ale cimitirului (gol dacă tabelul nu există încă)."""
    c = mf.ro_connect(binding.database)
    try:
        if "cloud_entity_identity" not in mf._tables(c):
            return {}
        return {(r["entity_type"], r["local_ref"]): r["cloud_uid"] for r in
                c.execute("SELECT entity_type, local_ref, cloud_uid FROM cloud_entity_identity WHERE cemetery_uid=? AND status='active'", (binding.cemetery_uid,))}
    finally:
        c.close()


def existing_media_identities(binding) -> Dict[str, str]:
    """Identitățile media ACTIVE (local_ref `document:<id>` / `photo:<id>` -> cloud_uid); gol dacă tabelul nu există."""
    c = mf.ro_connect(binding.database)
    try:
        if "cloud_media_identity" not in mf._tables(c):
            return {}
        return {r["local_ref"]: r["cloud_uid"] for r in c.execute("SELECT local_ref, cloud_uid FROM cloud_media_identity WHERE cemetery_uid=? AND status='active'", (binding.cemetery_uid,))}
    finally:
        c.close()


def media_identity_rows(binding, bootstrap_id: str) -> Dict[str, Dict[str, str]]:
    c = mf.ro_connect(binding.database)
    try:
        if "cloud_media_identity" not in mf._tables(c):
            return {}
        return {r["local_ref"]: {"cloud_uid": r["cloud_uid"], "status": r["status"]} for r in
                c.execute("SELECT local_ref, cloud_uid, status FROM cloud_media_identity WHERE cemetery_uid=? AND bootstrap_id=?", (binding.cemetery_uid, bootstrap_id))}
    finally:
        c.close()


def reserve_media(binding, bootstrap_id: str, mapping: Dict[Tuple[str, str], str]) -> int:
    """Rezervă (status `pending`) identitatea media ÎNAINTE de publicare; idempotent. Nu devine `active` decât la finalizarea bootstrap-ului."""
    rows = [(r, u) for (e, r), u in mapping.items() if e == tc.MEDIA]
    c = sqlite3.connect(binding.database, timeout=15, isolation_level=None)
    try:
        c.executescript(_DDL)
        c.execute("BEGIN IMMEDIATE")
        now = datetime.now(timezone.utc).isoformat()
        for ref, uid in rows:
            act = c.execute("SELECT cloud_uid FROM cloud_media_identity WHERE cemetery_uid=? AND local_ref=? AND status='active'", (binding.cemetery_uid, ref)).fetchone()
            if act:
                if act[0] != uid:
                    raise IdentityConflict(f"{ref} are deja o identitate media activă diferită")
                continue
            cur = c.execute("SELECT cloud_uid FROM cloud_media_identity WHERE cemetery_uid=? AND local_ref=? AND bootstrap_id=?", (binding.cemetery_uid, ref, bootstrap_id)).fetchone()
            if cur:
                if cur[0] != uid:
                    raise IdentityConflict(f"{ref} are deja o identitate rezervată diferită în acest bootstrap")
                continue
            c.execute("INSERT INTO cloud_media_identity (cemetery_uid, local_ref, cloud_uid, bootstrap_id, created_at) VALUES (?,?,?,?,?)", (binding.cemetery_uid, ref, uid, bootstrap_id, now))
        c.execute("COMMIT")
        return len(rows)
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        c.close()


def discard_pending_media(binding, bootstrap_id: str) -> int:
    """Curățare controlată la rollback: șterge DOAR identitățile `pending` ale acestui bootstrap (niciodată `active`)."""
    c = sqlite3.connect(binding.database, timeout=15, isolation_level=None)
    try:
        c.executescript(_DDL)
        return c.execute("DELETE FROM cloud_media_identity WHERE cemetery_uid=? AND bootstrap_id=? AND status='pending'", (binding.cemetery_uid, bootstrap_id)).rowcount
    finally:
        c.close()


def promotion_status(binding, bootstrap_id: str) -> Dict[str, Any]:
    c = mf.ro_connect(binding.database)
    try:
        if "cloud_identity_promotions" not in mf._tables(c):
            return {"promoted": False}
        r = c.execute("SELECT row_count, promoted_at FROM cloud_identity_promotions WHERE bootstrap_id=? AND cemetery_uid=?", (bootstrap_id, binding.cemetery_uid)).fetchone()
        return {"promoted": True, "row_count": r["row_count"], "promoted_at": r["promoted_at"]} if r else {"promoted": False}
    finally:
        c.close()


def promote(binding, bootstrap_id: str, mapping: Dict[Tuple[str, str], str], source_cemetery_uid: str,
            hook: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Promovare ATOMICĂ și idempotentă. Conflict (alt cloud_uid activ pe aceeași entitate) → rollback complet, nimic promovat."""
    if source_cemetery_uid != binding.cemetery_uid:
        raise IdentityConflict("mapping-ul aparține altui cimitir")
    rows = [(e, r, u) for (e, r), u in mapping.items() if e in ents.ENTITIES]  # media nu se promovează aici
    c = sqlite3.connect(binding.database, timeout=15, isolation_level=None)
    try:
        c.executescript(_DDL)
        c.execute("BEGIN IMMEDIATE")
        done = c.execute("SELECT row_count FROM cloud_identity_promotions WHERE bootstrap_id=?", (bootstrap_id,)).fetchone()
        if done:
            c.execute("COMMIT")
            return {"promoted": True, "already": True, "row_count": done[0]}
        now = datetime.now(timezone.utc).isoformat()
        added = 0
        for e, r, u in rows:
            cur = c.execute("SELECT cloud_uid FROM cloud_entity_identity WHERE cemetery_uid=? AND entity_type=? AND local_ref=? AND status='active'",
                            (binding.cemetery_uid, e, r)).fetchone()
            if cur:
                if cur[0] != u:
                    raise IdentityConflict(f"{e}#{r} are deja cloud_uid activ diferit")
                continue
            try:
                c.execute("INSERT INTO cloud_entity_identity (cemetery_uid, entity_type, local_ref, cloud_uid, source_bootstrap_id, created_at) VALUES (?,?,?,?,?,?)",
                          (binding.cemetery_uid, e, r, u, bootstrap_id, now))
            except sqlite3.IntegrityError:
                raise IdentityConflict(f"cloud_uid {u} este deja asociat altei entități active") from None
            added += 1
        c.execute("UPDATE cloud_media_identity SET status='retired' WHERE cemetery_uid=? AND status='pending' AND bootstrap_id<>? AND local_ref IN "
                  "(SELECT local_ref FROM cloud_media_identity WHERE cemetery_uid=? AND bootstrap_id=?)", (binding.cemetery_uid, bootstrap_id, binding.cemetery_uid, bootstrap_id))
        try:
            c.execute("UPDATE cloud_media_identity SET status='active' WHERE cemetery_uid=? AND bootstrap_id=? AND status='pending'",
                                     (binding.cemetery_uid, bootstrap_id)).rowcount
        except sqlite3.IntegrityError:
            raise IdentityConflict("identitate media activă în conflict") from None
        if hook:
            hook("in_txn")
        c.execute("INSERT INTO cloud_identity_promotions VALUES (?,?,?,?)", (bootstrap_id, binding.cemetery_uid, len(rows), now))
        c.execute("COMMIT")
        if hook:
            hook("after_commit")
        return {"promoted": True, "already": False, "row_count": len(rows), "added": added}
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        c.close()
