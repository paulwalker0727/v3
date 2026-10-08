"""cimitir_registry_adopt.py — adoptarea unei instalații vechi (DATA_DIR/cimitir.db + media + settings) într-un registru nou.

Nimic nu se mută fără confirmare explicită:
  1. `detect_legacy()`    — descrie ce există (înregistrări, fișiere media, setări);
  2. `plan_adoption()`    — DRY-RUN: text "Am găsit registru existent / N înregistrări / M fișiere media / Va fi mutat în <uid>";
  3. `adopt_legacy(..., confirm=True)` — copiază în registru (API-ul de backup SQLite, sigur cu WAL), verifică numărătorile,
     abia apoi mută originalele în DATA_DIR/legacy_adopted_<cemetery_uid>/ (nu se șterge nimic).
Dacă o verificare eșuează, registrul țintă parțial se curăță, iar instalația veche rămâne neatinsă.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import cimitir_registry as reg

LEGACY_DB = "cimitir.db"
SKIP_COUNT_TABLES = ("sqlite_sequence",)


class AdoptionError(reg.RegistryError):
    pass


class AdoptionNotConfirmed(AdoptionError):
    pass


def _count_files(root: Path) -> int:
    return sum(1 for p in root.rglob("*") if p.is_file()) if root.is_dir() else 0


def _table_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        out[name] = int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
    return out


def detect_legacy(data_dir: Any = None) -> Optional[Dict[str, Any]]:
    """Descrie instalația veche din DATA_DIR (read-only). None dacă nu există sau a fost deja adoptată."""
    base = reg._data_dir(data_dir)
    db = base / LEGACY_DB
    if not db.is_file():
        return None
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        counts = _table_counts(conn)
        try:
            row = conn.execute("SELECT value FROM settings WHERE key='cimitir_nume'").fetchone()
            name = row[0] if row else ""
            settings = int(conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0])
        except sqlite3.Error:
            name, settings = "", 0
    finally:
        conn.close()
    records = sum(n for t, n in counts.items() if t not in ("settings", "users") and not t.startswith("cloud_sync_"))
    media_files = _count_files(base / "media")
    if records == 0 and media_files == 0:  # bază goală (doar conturi/setări implicite): nu e nimic de adoptat
        return None
    return {"data_dir": str(base), "database": str(db), "tables": counts, "records": records, "settings": settings,
            "media_files": media_files, "cache_files": _count_files(base / "media_cache"),
            "cemetery_name": name, "size_bytes": db.stat().st_size}


def plan_adoption(cemetery_uid: str, data_dir: Any = None, catalog: Optional[reg.RegistryCatalog] = None) -> Dict[str, Any]:
    """DRY-RUN: nu scrie nimic. Întoarce {'found': bool, 'lines': [...], 'legacy': {...}}."""
    legacy = detect_legacy(data_dir)
    if legacy is None:
        return {"found": False, "lines": ["Nu există nicio instalație veche (cimitir.db) de adoptat."], "legacy": None}
    cat = catalog or reg.RegistryCatalog(data_dir)
    cem = cat.get_cemetery(cemetery_uid)
    if not cem:
        raise AdoptionError("Cimitirul țintă nu există în catalog.")
    target = cat.registry_root(cem["uid"])
    lines = ["Am găsit registru existent",
             f"{legacy['records']} înregistrări",
             f"{legacy['media_files']} fișiere media",
             f"Va fi mutat în {cem['uid']}",
             f"Țintă: {target}",
             "Nimic nu se mută până nu confirmi."]
    return {"found": True, "lines": lines, "legacy": legacy, "target": str(target), "cemetery_uid": cem["uid"]}


def _rewrite_paths(conn: sqlite3.Connection, mapping: List[tuple]) -> int:
    changed = 0
    for table in ("documents", "photos"):
        try:
            rows = conn.execute(f"SELECT id, file_path FROM {table} WHERE file_path IS NOT NULL AND file_path <> ''").fetchall()
        except sqlite3.OperationalError:
            continue
        for rid, fp in rows:
            for src, dst in mapping:
                try:
                    if os.path.commonpath([os.path.realpath(src), os.path.realpath(fp)]) == os.path.realpath(src):
                        rel = os.path.relpath(os.path.realpath(fp), os.path.realpath(src))
                        conn.execute(f"UPDATE {table} SET file_path=? WHERE id=?", (str(Path(dst) / rel), rid))
                        changed += 1
                        break
                except ValueError:
                    continue
    conn.commit()
    return changed


def adopt_legacy(cemetery_uid: str, confirm: bool = False, data_dir: Any = None,
                 catalog: Optional[reg.RegistryCatalog] = None) -> Dict[str, Any]:
    """Aplică adoptarea. Fără `confirm=True` refuză (nu atinge nimic)."""
    if confirm is not True:
        raise AdoptionNotConfirmed("Adoptarea cere confirmare explicită; rulează întâi planul (dry-run).")
    plan = plan_adoption(cemetery_uid, data_dir, catalog)
    if not plan["found"]:
        raise AdoptionError(plan["lines"][0])
    cat = catalog or reg.RegistryCatalog(data_dir)
    legacy = plan["legacy"]
    base = Path(legacy["data_dir"])
    binding = cat.binding_for(cemetery_uid)
    if binding.database.exists():
        raise AdoptionError("Registrul țintă are deja o bază de date; adoptarea ar suprascrie date.")
    binding.ensure_dirs()
    src_db = Path(legacy["database"])
    created: List[Path] = []
    dst = None
    try:
        src = sqlite3.connect(f"file:{src_db.as_posix()}?mode=ro", uri=True)
        dst = sqlite3.connect(binding.database)
        try:
            src.backup(dst)
        finally:
            src.close()
        created.append(binding.database)
        mapping = []
        for sub, target in (("media", binding.media), ("media_cache", binding.cache / "media_cache")):
            if (base / sub).is_dir():
                shutil.copytree(base / sub, target, dirs_exist_ok=True)
                mapping.append((str(base / sub), str(target)))
        _rewrite_paths(dst, mapping)
        counts_new = _table_counts(dst)
        problems = [f"{t}: {n} -> {counts_new.get(t)}" for t, n in legacy["tables"].items() if counts_new.get(t) != n]
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            problems.append("integrity_check")
        if _count_files(binding.media) != legacy["media_files"]:
            problems.append("fișiere media")
        dst.close()
        if problems:
            raise AdoptionError("Verificarea a eșuat: " + "; ".join(problems))
        reg.prepare_registry_db(binding)  # leagă baza de (organizație, cimitir)
    except Exception:
        if dst is not None:
            try:
                dst.close()
            except sqlite3.Error:
                pass
        for p in (binding.database, Path(str(binding.database) + "-wal"), Path(str(binding.database) + "-shm")):
            p.unlink(missing_ok=True)
        shutil.rmtree(binding.media, ignore_errors=True)
        shutil.rmtree(binding.cache / "media_cache", ignore_errors=True)
        binding.ensure_dirs()
        raise
    archive = base / f"legacy_adopted_{binding.cemetery_uid}"
    archive.mkdir(parents=True, exist_ok=True)
    for name in (LEGACY_DB, LEGACY_DB + "-wal", LEGACY_DB + "-shm", "media", "media_cache"):
        if (base / name).exists():
            shutil.move(str(base / name), str(archive / name))
    (archive / "adoption.json").write_text(json.dumps(
        {"cemetery_uid": binding.cemetery_uid, "organization_uid": binding.organization_uid, "records": legacy["records"],
         "media_files": legacy["media_files"], "adopted_at": reg._now()}, indent=1), encoding="utf-8")
    return {"cemetery_uid": binding.cemetery_uid, "records": legacy["records"], "media_files": legacy["media_files"],
            "archive": str(archive), "database": str(binding.database)}
