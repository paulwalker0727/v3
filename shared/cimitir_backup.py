"""Copii de rezervă și restaurare pentru folderul de date Cimitir (MAIN sau server), partajate de aplicație, portal și scripturi.

- copia bazei folosește API-ul de backup SQLite (consistentă chiar dacă aplicația rulează, WAL); media se copiază integral;
- `manifest.json` conține SHA-256 pentru fiecare fișier; restaurarea verifică manifestul și păstrează o copie de siguranță;
- copiile automate: `run_backup_job` / `run_scheduled` (la interval configurabil, doar dacă ultima copie e mai veche), fiecare copie este
  verificată imediat (SHA-256 + integrity_check); o copie nevalidă este pusă în carantină (`*.invalid`) și raportată ca EȘEC;
- jurnal JSONL (`<folder-date>.backup-log.jsonl`): data, rezultat, locație, mărime, eroare; `health` dă avertizarea vizibilă în MAIN;
- retenție: păstrare N zile, dar niciodată sub `MIN_KEEP_FLOOR` copii, nu se șterge nimic dacă nu există copii valide mai noi și nu se
  șterg copiile marcate „protejat”; restaurarea rămâne MANUALĂ (nu e apelată niciodată de programare);
- rulare programată fără interfață: `cli` (Planificatorul de activități Windows: `Cimitir.exe --backup-run` / `scripts/backup_scheduled.py`);
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
DEFAULTS = {"backup_enabled": "1", "backup_keep_days": "14", "backup_interval_hours": "24", "backup_time": "02:00", "backup_keep_min": "3"}
MIN_KEEP_FLOOR = 3
PROTECT_MARK = "PROTEJAT"
LOCK_NAME = ".backup.lock"
LOCK_STALE_SECONDS = 2 * 3600
LOG_SUFFIX = ".backup-log.jsonl"
LOG_MAX_LINES = 500
RETRY_AFTER_FAILURE_MIN = 60


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
    try:
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
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)       # o copie incompletă nu rămâne pe disc (nu apare în listă și nu poate fi restaurată)
        raise
    return target


def verify(backup_dir: Path) -> list:
    """Lista problemelor (goală = copie validă). Nu ridică excepții pentru manifest lipsă/corupt sau intrări nesigure."""
    backup_dir = Path(backup_dir).resolve()
    try:
        manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))["files"]
        if not isinstance(manifest, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in manifest.items()):
            raise ValueError("format")
    except (OSError, ValueError, KeyError, TypeError):
        return ["manifest.json lipsește sau este corupt"]
    problems = []
    if "cimitir.db" not in manifest:
        problems.append("manifestul nu conține cimitir.db")
    for rel, digest in manifest.items():
        path = (backup_dir / rel).resolve()
        if backup_dir not in path.parents:
            problems.append(f"cale nesigură în manifest: {rel}")
        elif not path.is_file() or _sha(path) != digest:
            problems.append(f"lipsește/modificat: {rel}")
    if not (backup_dir / "cimitir.db").is_file():
        return problems
    try:
        conn = sqlite3.connect(f"file:{(backup_dir / 'cimitir.db').as_posix()}?mode=ro", uri=True)
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            problems.append("integrity_check DB a eșuat")
        conn.close()
    except sqlite3.Error as exc:
        problems.append(f"DB ilizibilă: {exc}")
    return problems


MEDIA_PATH_COLUMNS = (("documents", "file_path"), ("photos", "file_path"))


def _reset_cloud_client_id(db_file: Path) -> None:
    """BUG-04: imediat după restaurare, MAIN trebuie să arate serverului un client NOU.
    Altfel, `_last_writer_is` (backend/entity_sync_api.py) acceptă `base_version` învechit (pentru că ultimul
    scriitor pe server este același `cloud_client_id`) și prima editare locală suprascrie în tăcere modificările
    altor calculatoare făcute între copie și restaurare. Setăm un `restored_at` ca urmă de audit.
    Dacă baza nu are tabela `settings`, o creăm (schema MAIN: key TEXT PRIMARY KEY, value TEXT).
    Nu ridicăm excepții: o eroare aici nu trebuie să blocheze restaurarea (rollback-ul s-a încheiat).
    """
    try:
        conn = sqlite3.connect(db_file)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('cloud_client_id', '')")
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('restored_at', ?)",
                         (datetime.now().isoformat(timespec="seconds"),))
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass


def _rebase_media_paths(db_file: Path, media_dir: Path) -> int:
    """Căile absolute din documents/photos (…/media/<tip>/<fișier>) se mută pe folderul media restaurat, DOAR dacă fișierul există acolo
    (restaurare pe alt calculator / alt folder, chiar dacă originalul mai există). Nu atinge căile externe sau fără corespondent. Întoarce numărul de căi actualizate."""
    changed = 0
    conn = sqlite3.connect(db_file)
    try:
        for table, col in MEDIA_PATH_COLUMNS:
            try:
                rows = conn.execute(f"SELECT id, {col} FROM {table}").fetchall()
            except sqlite3.Error:
                continue
            for rid, path in rows:
                if not path or str(path).replace("\\", "/").startswith(str(media_dir).replace("\\", "/") + "/"):
                    continue
                norm = str(path).replace("\\", "/")
                idx = norm.lower().rfind("/media/")
                if idx < 0:
                    continue
                candidate = media_dir / norm[idx + len("/media/"):]
                if candidate.is_file():
                    conn.execute(f"UPDATE {table} SET {col} = ? WHERE id = ?", (str(candidate), rid))
                    changed += 1
        conn.commit()
    finally:
        conn.close()
    return changed


def restore(backup_dir: Path, data_dir: Path, force: bool = False) -> Path:
    """Restaurare sigură: verifică copia, păstrează starea curentă lângă folder (`.inainte-de-restaurare-*`) și, dacă restaurarea eșuează
    la jumătate, readuce starea anterioară. Nu suprascrie niciodată un folder nevid fără `force`."""
    backup_dir, data_dir = Path(backup_dir).resolve(), Path(data_dir).resolve()
    problems = verify(backup_dir)
    if problems:
        raise SystemExit("Backup invalid: " + "; ".join(problems))
    safety: Optional[Path] = None
    if data_dir.exists() and any(data_dir.iterdir()):
        if not force:
            raise SystemExit(f"{data_dir} nu este gol. Folosiți --force (se păstrează o copie de siguranță).")
        safety = data_dir.with_name(data_dir.name + f".inainte-de-restaurare-{datetime.now():%Y%m%d-%H%M%S}")
        n = 0
        while safety.exists():
            n += 1
            safety = data_dir.with_name(data_dir.name + f".inainte-de-restaurare-{datetime.now():%Y%m%d-%H%M%S}-{n}")
        shutil.move(str(data_dir), str(safety))
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        for keep in (".jwt_secret", "registry.json"):
            if safety is not None and (safety / keep).is_file():
                shutil.copy2(safety / keep, data_dir / keep)
        shutil.copy2(backup_dir / "cimitir.db", data_dir / _db_name(data_dir))
        if (backup_dir / "media").is_dir():
            shutil.copytree(backup_dir / "media", data_dir / "media")
        _rebase_media_paths(data_dir / _db_name(data_dir), data_dir / "media")
        _reset_cloud_client_id(data_dir / _db_name(data_dir))      # BUG-04: după restaurare, MAIN devine client nou -> serverul raportează conflictul (nu suprascrie orbește)
    except BaseException:
        shutil.rmtree(data_dir, ignore_errors=True)
        if safety is not None:
            shutil.move(str(safety), str(data_dir))      # starea anterioară revine intactă
        raise
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


def is_protected(backup_dir: Path) -> bool:
    return (Path(backup_dir) / PROTECT_MARK).is_file()


def set_protected(backup_dir: Path, on: bool = True) -> None:
    """Marchează/demarchează o copie ca protejată: retenția nu o șterge niciodată."""
    marker = Path(backup_dir) / PROTECT_MARK
    if on:
        marker.write_text("Copie protejată: nu este ștearsă de retenția automată.\n", encoding="utf-8")
    else:
        marker.unlink(missing_ok=True)


def _quick_ok(backup_dir: Path) -> bool:
    """Verificare ieftină (fără SHA): manifestul se citește și toate fișierele din el există."""
    try:
        manifest = json.loads((Path(backup_dir) / "manifest.json").read_text(encoding="utf-8"))["files"]
        return "cimitir.db" in manifest and all((Path(backup_dir) / rel).is_file() for rel in manifest)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def prune(dest_root: Path, keep_days: int, keep_min: int = MIN_KEEP_FLOOR) -> int:
    """Șterge copiile mai vechi de `keep_days`. Protecții: se păstrează mereu cele mai noi `keep_min` copii (cel puțin 1; programarea din MAIN impune minimum MIN_KEEP_FLOOR);
    nu se șterge nimic dacă acestea nu sunt toate întregi; copiile marcate „protejat” și legăturile simbolice nu se șterg niciodată;
    se atinge doar `cimitir-AAAAMMZZ-HHMMSS` cu manifest, direct în folderul de copii. Întoarce câte au fost șterse."""
    keep_min = max(1, int(keep_min))
    limit, removed = datetime.now() - timedelta(days=max(1, int(keep_days))), 0
    rows = list_backups(dest_root)
    if not all(_quick_ok(Path(r["path"])) for r in rows[:keep_min]):
        return 0
    root = Path(dest_root).resolve()
    for row in rows[keep_min:]:
        path = Path(row["path"])
        if path.is_symlink() or path.resolve().parent != root or is_protected(path):
            continue
        if datetime.fromisoformat(row["created"]) < limit:
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    return removed


def is_due(dest_root: Path, interval_hours: float = 24) -> bool:
    rows = list_backups(dest_root)
    return not rows or datetime.now() - datetime.fromisoformat(rows[0]["created"]) >= timedelta(hours=interval_hours)


# ---------------------------------------------------------------- jurnal
def log_path(data_dir: Path) -> Path:
    data_dir = Path(data_dir).resolve()
    return data_dir.with_name(data_dir.name + LOG_SUFFIX)


def log_event(data_dir: Path, entry: Dict[str, Any]) -> None:
    """Adaugă o linie în jurnal (în afara folderului de date și a folderului de copii: rămâne chiar dacă discul de copii lipsește). Nu ridică excepții."""
    try:
        path = log_path(data_dir)
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        lines.append(json.dumps(entry, ensure_ascii=False))
        path.write_text("\n".join(lines[-LOG_MAX_LINES:]) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"[backup] jurnalul nu a putut fi scris: {exc}")


def read_log(data_dir: Path, limit: int = 100) -> List[Dict[str, Any]]:
    """Cele mai noi intrări primele; liniile corupte sunt ignorate."""
    path, rows = log_path(data_dir), []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in reversed(lines):
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
        if len(rows) >= limit:
            break
    return rows


class _Lock:
    """Evită două copii simultane (aplicația + Planificatorul Windows). Un lock mai vechi de 2 ore se consideră abandonat."""

    def __init__(self, dest: Path):
        self.path = Path(dest) / LOCK_NAME
        self.held = False

    def __enter__(self):
        for _ in range(2):
            try:
                os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                self.held = True
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > LOCK_STALE_SECONDS:
                        self.path.unlink()
                        continue
                except OSError:
                    pass
                break
        return self

    def __exit__(self, *exc):
        if self.held:
            self.path.unlink(missing_ok=True)


def _cfg(get_setting: Callable[[str], Optional[str]], key: str) -> str:
    try:
        value = get_setting(key)
    except Exception:
        value = None
    return (value or DEFAULTS.get(key, "")).strip()


def backup_destination(data_dir: Path, get_setting: Callable[[str], Optional[str]]) -> Path:
    custom = _cfg(get_setting, "backup_dir")
    return Path(custom) if custom else default_backup_dir(data_dir)


def run_backup_job(data_dir: Path, get_setting: Callable[[str], Optional[str]], force: bool = False, trigger: str = "app") -> Dict[str, Any]:
    """Copie + verificare + retenție + jurnal. Nu ridică excepții. `result`: ok | failed (jurnalizate) | skipped | disabled | busy (nejurnalizate)."""
    data_dir = Path(data_dir)
    if _cfg(get_setting, "backup_enabled") == "0" and not force:
        return {"result": "disabled"}
    dest = backup_destination(data_dir, get_setting)
    if not force and trigger == "app":
        last = read_log(data_dir, 1)
        try:
            if last and last[0].get("result") == "failed" and datetime.now() - datetime.fromisoformat(last[0]["time"]) < timedelta(minutes=RETRY_AFTER_FAILURE_MIN):
                return {"result": "skipped"}       # nu umple jurnalul cu eșecuri repetate la fiecare verificare din MAIN (30 min)
        except (KeyError, ValueError):
            pass
    try:
        if not force and not is_due(dest, float(_cfg(get_setting, "backup_interval_hours") or 24)):
            return {"result": "skipped"}
    except (ValueError, OSError):
        pass
    entry: Dict[str, Any] = {"time": datetime.now().isoformat(timespec="seconds"), "trigger": trigger, "result": "failed", "location": str(dest)}
    try:
        dest.mkdir(parents=True, exist_ok=True)
        with _Lock(dest) as lock:
            if not lock.held:
                return {"result": "busy"}
            target = backup(data_dir, dest)
            problems = verify(target)
            if problems:
                bad = target.with_name(target.name + ".invalid")
                try:
                    target.rename(bad)
                except OSError:
                    pass
                raise RuntimeError("Copia creată nu a trecut verificarea (pusă în carantină: " + bad.name + "): " + "; ".join(problems[:3]))
            files = json.loads((target / "manifest.json").read_text(encoding="utf-8"))["files"]
            entry.update(result="ok", backup=target.name, path=str(target), location=str(target), verified=True, files=len(files),
                         size=sum(p.stat().st_size for p in target.rglob("*") if p.is_file()))
            try:
                entry["pruned"] = prune(dest, int(_cfg(get_setting, "backup_keep_days") or 14),
                                    max(MIN_KEEP_FLOOR, int(_cfg(get_setting, "backup_keep_min") or MIN_KEEP_FLOOR)))
            except (OSError, ValueError) as exc:
                entry["prune_error"] = str(exc)
    except (Exception, SystemExit) as exc:
        entry.update(result="failed", error=str(exc) or exc.__class__.__name__)
    log_event(data_dir, entry)
    return entry


def run_scheduled(data_dir: Path, get_setting: Callable[[str], Optional[str]], force: bool = False) -> Optional[Path]:
    """Face o copie dacă e activată și scadentă; aplică retenția. Nu ridică excepții (returnează None la eșec sau dacă nu era scadentă)."""
    result = run_backup_job(data_dir, get_setting, force=force, trigger="manual" if force else "app")
    if result["result"] == "failed":
        print(f"[backup] eșuat: {result.get('error')}")
    return Path(result["path"]) if result["result"] == "ok" else None


def health(data_dir: Path, get_setting: Callable[[str], Optional[str]], now: Optional[datetime] = None) -> Dict[str, str]:
    """Starea pentru avertizarea din MAIN: level = ok | warning | error | off, plus mesaj. Eșecul cel mai recent rămâne „error” până la o copie reușită."""
    now = now or datetime.now()
    events = read_log(data_dir, 200)
    last_ok = next((e for e in events if e.get("result") == "ok"), None)
    try:
        newest = list_backups(backup_destination(data_dir, get_setting))
    except OSError:
        newest = []
    last_ok_time = datetime.fromisoformat(last_ok["time"]) if last_ok else (datetime.fromisoformat(newest[0]["created"]) if newest else None)
    if events and events[0].get("result") == "failed":
        return {"level": "error", "message": f"Ultima copie de rezervă a EȘUAT ({events[0].get('time', '?').replace('T', ' ')}): {events[0].get('error', 'eroare necunoscută')}"}
    if _cfg(get_setting, "backup_enabled") == "0":
        return {"level": "off", "message": "Copia automată de rezervă este dezactivată."}
    if last_ok_time is None:
        return {"level": "warning", "message": "Nu există nicio copie de rezervă. Fă una acum."}
    try:
        interval = float(_cfg(get_setting, "backup_interval_hours") or 24)
    except ValueError:
        interval = 24.0
    age = now - last_ok_time
    if age > timedelta(hours=2 * interval + 1):
        return {"level": "warning", "message": f"Ultima copie reușită este veche de {int(age.total_seconds() // 3600)} ore ({last_ok_time:%Y-%m-%d %H:%M}). Verifică programarea."}
    return {"level": "ok", "message": f"Ultima copie reușită: {last_ok_time:%Y-%m-%d %H:%M}."}


def _settings_reader(data_dir: Path) -> Callable[[str], Optional[str]]:
    """Citește setările din baza MAIN, doar în citire, fără interfață (rulare programată)."""
    db_file = Path(data_dir) / _db_name(Path(data_dir))

    def get(key: str) -> Optional[str]:
        try:
            conn = sqlite3.connect(f"file:{db_file.as_posix()}?mode=ro", uri=True, timeout=15)
            try:
                row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            finally:
                conn.close()
            return row[0] if row else None
        except sqlite3.Error:
            return None
    return get


def cli(argv: Optional[List[str]] = None) -> int:
    """Punct de intrare fără interfață pentru Planificatorul Windows: 0 = reușit/nimic de făcut, 1 = EȘEC (vizibil în Task Scheduler și în MAIN)."""
    import argparse
    parser = argparse.ArgumentParser(prog="backup-run", description="Copie de rezervă programată (verificată, jurnalizată). Nu restaurează niciodată.")
    parser.add_argument("--data-dir", help="folderul de date (implicit CIMITIR_DATA_DIR)")
    parser.add_argument("--dest", help="folderul de copii (suprascrie setarea din MAIN)")
    parser.add_argument("--force", action="store_true", help="face copia chiar dacă ultima este recentă sau copia automată este dezactivată")
    parser.add_argument("--status", action="store_true", help="doar afișează starea")
    args = parser.parse_args(argv)
    if args.data_dir:
        os.environ["CIMITIR_DATA_DIR"] = str(Path(args.data_dir).resolve())
    import cimitir_config
    work = Path(cimitir_config.working_dir())
    base = _settings_reader(work)
    get = (lambda key: args.dest if key == "backup_dir" and args.dest else base(key))
    if args.status:
        state = health(work, get)
        print(f"[{state['level']}] {state['message']}")
        return 1 if state["level"] == "error" else 0
    result = run_backup_job(work, get, force=args.force, trigger="windows-task")
    if result["result"] == "ok":
        print(f"[backup] OK: {result['path']} ({result['files']} fișiere, verificat)")
        return 0
    if result["result"] == "failed":
        print(f"[backup] EȘEC: {result.get('error')}")
        return 1
    print(f"[backup] {result['result']}: nimic de făcut")
    return 0


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
