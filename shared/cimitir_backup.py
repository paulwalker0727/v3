"""Copii de rezervă și restaurare pentru folderul de date Cimitir (MAIN sau server), partajate de aplicație, portal și scripturi.

- copia bazei folosește API-ul de backup SQLite (consistentă chiar dacă aplicația rulează, WAL); media se copiază integral;
- `manifest.json` conține SHA-256 pentru fiecare fișier; restaurarea verifică manifestul și păstrează o copie de siguranță;
- copiile automate: `run_scheduled` (zilnic, doar dacă ultima copie e mai veche), păstrare N zile;
- restaurare „dintr-un clic": `schedule_restore` pregătește restaurarea, iar la următoarea pornire `apply_pending_restore` o aplică
  înainte de deschiderea bazei (nu se înlocuiește niciodată o bază deschisă).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

PREFIX = "cimitir-"
DEFAULTS = {"backup_enabled": "1", "backup_keep_days": "14", "backup_interval_hours": "24"}


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _db_name(data_dir: Path) -> str:
    """Registru local (<DATA_DIR>/registries/<uid>) -> database.sqlite; instalare clasică -> cimitir.db."""
    return "database.sqlite" if (Path(data_dir) / "registry.json").is_file() else "cimitir.db"


def backup(data_dir: Path, dest_root: Path) -> Path:
    data_dir, dest_root = Path(data_dir).resolve(), Path(dest_root).resolve()
    src_db = data_dir / _db_name(data_dir)
    if not src_db.is_file():
        raise SystemExit(f"Nu există {src_db}")
    target = dest_root / f"cimitir-{datetime.now():%Y%m%d-%H%M%S}"
    while target.exists():
        time.sleep(1)
        target = dest_root / f"cimitir-{datetime.now():%Y%m%d-%H%M%S}"
    target.mkdir(parents=True)
    src = sqlite3.connect(f"file:{src_db.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(target / "cimitir.db")
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    if (data_dir / "media").is_dir():
        shutil.copytree(data_dir / "media", target / "media")
    manifest = {str(p.relative_to(target)).replace("\\", "/"): _sha(p) for p in sorted(target.rglob("*")) if p.is_file()}
    (target / "manifest.json").write_text(json.dumps({"created": datetime.now().isoformat(), "files": manifest}, indent=1), encoding="utf-8")
    return target


def verify(backup_dir: Path) -> list:
    backup_dir = Path(backup_dir).resolve()
    manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))["files"]
    problems = [f"lipsește/modificat: {rel}" for rel, digest in manifest.items()
                if not (backup_dir / rel).is_file() or _sha(backup_dir / rel) != digest]
    try:
        conn = sqlite3.connect(f"file:{(backup_dir / 'cimitir.db').as_posix()}?mode=ro", uri=True)
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            problems.append("integrity_check DB a eșuat")
        conn.close()
    except sqlite3.Error as exc:
        problems.append(f"DB ilizibilă: {exc}")
    return problems


def restore(backup_dir: Path, data_dir: Path, force: bool = False) -> Path:
    backup_dir, data_dir = Path(backup_dir).resolve(), Path(data_dir).resolve()
    problems = verify(backup_dir)
    if problems:
        raise SystemExit("Backup invalid: " + "; ".join(problems))
    if data_dir.exists() and any(data_dir.iterdir()):
        if not force:
            raise SystemExit(f"{data_dir} nu este gol. Folosiți --force (se păstrează o copie de siguranță).")
        safety = data_dir.with_name(data_dir.name + f".inainte-de-restaurare-{datetime.now():%Y%m%d-%H%M%S}")
        shutil.move(str(data_dir), str(safety))
    data_dir.mkdir(parents=True, exist_ok=True)
    if "safety" in locals() and (safety / ".jwt_secret").is_file():
        shutil.copy2(safety / ".jwt_secret", data_dir / ".jwt_secret")
    if "safety" in locals() and (safety / "registry.json").is_file():
        shutil.copy2(safety / "registry.json", data_dir / "registry.json")
    shutil.copy2(backup_dir / "cimitir.db", data_dir / _db_name(data_dir))
    if (backup_dir / "media").is_dir():
        shutil.copytree(backup_dir / "media", data_dir / "media")
    return data_dir


def pending_marker(data_dir: Path) -> Path:
    data_dir = Path(data_dir).resolve()
    return data_dir.with_name(data_dir.name + ".restore-pending.json")


def default_backup_dir(data_dir: Path) -> Path:
    override = os.environ.get("CIMITIR_BACKUP_DIR", "").strip()
    data_dir = Path(data_dir).resolve()
    return Path(override) if override else data_dir.with_name(data_dir.name + "-backups")


def _stamp(path: Path) -> Optional[datetime]:
    try:
        return datetime.strptime(path.name[len(PREFIX):], "%Y%m%d-%H%M%S")
    except ValueError:
        return None


def list_backups(dest_root: Path) -> List[Dict[str, Any]]:
    """Copiile din folder, cele mai noi primele (fără verificare completă: vezi `verify`)."""
    dest_root, rows = Path(dest_root), []
    if not dest_root.is_dir():
        return rows
    for path in dest_root.iterdir():
        created = _stamp(path) if path.is_dir() and path.name.startswith(PREFIX) else None
        if created and (path / "manifest.json").is_file():
            size = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
            rows.append({"name": path.name, "path": str(path), "created": created.isoformat(timespec="seconds"), "size": size})
    return sorted(rows, key=lambda r: r["created"], reverse=True)


def prune(dest_root: Path, keep_days: int, keep_min: int = 3) -> int:
    """Șterge copiile mai vechi de `keep_days`, păstrând mereu cele mai noi `keep_min`. Întoarce câte au fost șterse."""
    limit, removed = datetime.now() - timedelta(days=max(1, keep_days)), 0
    for row in list_backups(dest_root)[keep_min:]:
        if datetime.fromisoformat(row["created"]) < limit:
            shutil.rmtree(row["path"], ignore_errors=True)
            removed += 1
    return removed


def is_due(dest_root: Path, interval_hours: float = 24) -> bool:
    rows = list_backups(dest_root)
    return not rows or datetime.now() - datetime.fromisoformat(rows[0]["created"]) >= timedelta(hours=interval_hours)


def run_scheduled(data_dir: Path, get_setting: Callable[[str], Optional[str]], force: bool = False) -> Optional[Path]:
    """Face o copie dacă e activată și scadentă; aplică retenția. Nu ridică excepții (returnează None la eșec)."""
    def cfg(key: str) -> str:
        return (get_setting(key) or DEFAULTS.get(key, "")).strip()
    try:
        if cfg("backup_enabled") == "0" and not force:
            return None
        dest = Path(cfg("backup_dir")) if cfg("backup_dir") else default_backup_dir(data_dir)
        if not force and not is_due(dest, float(cfg("backup_interval_hours") or 24)):
            return None
        dest.mkdir(parents=True, exist_ok=True)
        target = backup(Path(data_dir), dest)
        prune(dest, int(cfg("backup_keep_days") or 14))
        return target
    except (Exception, SystemExit) as exc:
        print(f"[backup] eșuat: {exc}")
        return None


def schedule_restore(backup_dir: Path, data_dir: Path) -> Path:
    """Verifică copia și o programează pentru aplicare la următoarea pornire."""
    problems = verify(backup_dir)
    if problems:
        raise SystemExit("Copie invalidă: " + "; ".join(problems))
    marker = pending_marker(data_dir)
    marker.write_text(json.dumps({"backup": str(Path(backup_dir).resolve()), "scheduled": datetime.now().isoformat(timespec="seconds")}),
                      encoding="utf-8")
    return marker


def apply_pending_restore(data_dir: Path) -> Optional[str]:
    """Aplică restaurarea programată (înainte de deschiderea bazei). Întoarce un mesaj, sau None dacă nu e nimic de făcut."""
    marker = pending_marker(data_dir)
    if not marker.is_file():
        return None
    try:
        backup_dir = Path(json.loads(marker.read_text(encoding="utf-8"))["backup"])
        restore(backup_dir, Path(data_dir), force=True)
    except (Exception, SystemExit) as exc:
        return f"Restaurarea nu a putut fi aplicată ({exc}). Închide MAIN și portalul, apoi pornește din nou."
    marker.unlink(missing_ok=True)
    return f"Baza a fost restaurată din {backup_dir.name}. Starea anterioară este păstrată lângă folderul de date."
