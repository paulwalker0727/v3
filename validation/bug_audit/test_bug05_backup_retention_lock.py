"""BUG-05 — copii de rezervă: cazuri în care retenția sau programarea raportează „bine” deși protecția promisă lipsește.

05a  Lock rămas (MAIN închis brusc / activitate oprită de Task Scheduler după 2 h): `run_backup_job` întoarce `busy`,
     NU scrie în jurnal, iar `cli` iese cu 0 -> Task Scheduler arată „0x0” și MAIN nu afișează nicio avertizare, deși nu s-a
     făcut nicio copie (până la 2 h, LOCK_STALE_SECONDS). Cauză: shared/cimitir_backup.py:347-349 și :450-451.
05b  Retenția verifică doar „ieftin” cele mai noi `keep_min` copii (`_quick_ok`: fișierele există, fără SHA/integrity_check,
     shared/cimitir_backup.py:217-223, :233). Dacă două copii recente s-au deteriorat pe disc (disc extern defect), retenția
     șterge copiile vechi BUNE -> rămâne o singură copie validă, deși documentația promite „nimic șters dacă cele mai noi nu
     sunt întregi”.
05c  Carantina: dacă redenumirea în `*.invalid` eșuează (fișier ținut deschis de antivirus/Explorer pe Windows), eroarea e
     ignorată (shared/cimitir_backup.py:354-357) și copia nevalidă rămâne cu nume normal + manifest: apare în listă ca cea mai
     nouă copie, poate fi aleasă la restaurare (verify o refuză) și contează la `keep_min` în retenție.
"""
import json
import os
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import cimitir_backup as cb


def _data(tmp_path) -> Path:
    data = tmp_path / "Cimitir"
    data.mkdir()
    conn = sqlite3.connect(data / "cimitir.db")
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO settings VALUES ('x', 'y')")
    conn.commit()
    conn.close()
    return data


def _aged_copy(data: Path, dest: Path, days: int) -> Path:
    copy = cb.backup(data, dest)
    target = dest / f"cimitir-{datetime.now() - timedelta(days=days):%Y%m%d-%H%M%S}"
    copy.rename(target)
    return target


def test_05a_stale_lock_is_reported_not_silent(tmp_path, capsys):
    data = _data(tmp_path)
    dest = tmp_path / "copii"
    dest.mkdir()
    (dest / cb.LOCK_NAME).write_text("", encoding="utf-8")         # lock rămas de la un proces oprit forțat
    os.environ["CIMITIR_DATA_DIR"] = str(data)
    try:
        code = cb.cli(["--data-dir", str(data), "--dest", str(dest)])
    finally:
        os.environ.pop("CIMITIR_DATA_DIR", None)
    copies = cb.list_backups(dest)
    health = cb.health(data, lambda k: str(dest) if k == "backup_dir" else None)
    assert copies or code != 0 or health["level"] != "warning" or cb.read_log(data), (
        f"nicio copie făcută, dar cod ieșire={code}, jurnal gol, stare={health}")
    assert code != 0 or copies, f"Task Scheduler ar vedea succes (cod {code}) fără nicio copie: {capsys.readouterr().out.strip()}"


def test_05b_prune_keeps_min_valid_copies(tmp_path):
    data = _data(tmp_path)
    dest = tmp_path / "copii"
    old_good = [_aged_copy(data, dest, d) for d in (40, 30)]
    damaged = [_aged_copy(data, dest, d) for d in (20, 10)]
    for d in damaged:                                                  # deteriorare pe disc DUPĂ verificarea inițială
        with open(d / "cimitir.db", "r+b") as fh:
            fh.seek(200)
            fh.write(b"\xff" * 3000)
    newest = cb.backup(data, dest)
    assert cb.verify(newest) == [], cb.verify(newest)
    assert all(cb.verify(d) for d in damaged), [cb.verify(d) for d in damaged]
    cb.prune(dest, keep_days=14, keep_min=3)
    valid = [r["name"] for r in cb.list_backups(dest) if not cb.verify(Path(r["path"]))]
    assert len(valid) >= 3 or all(p.exists() for p in old_good), (
        f"retenția a șters copiile vechi valide; au rămas doar {len(valid)} copii valide: {valid}")


def test_05c_failed_quarantine_leaves_invalid_copy_listed(tmp_path, monkeypatch):
    data = _data(tmp_path)
    dest = tmp_path / "copii"
    monkeypatch.setattr(cb, "verify", lambda p: ["lipsește/modificat: cimitir.db"])     # copia creată e stricată
    real_rename = Path.rename

    def busy_rename(self, target):
        if str(target).endswith(".invalid"):
            raise PermissionError("fișier folosit de alt proces (Windows)")
        return real_rename(self, target)
    monkeypatch.setattr(Path, "rename", busy_rename)
    res = cb.run_backup_job(data, {"backup_dir": str(dest)}.get, force=True, trigger="manual")
    assert res["result"] == "failed"
    listed = [r["name"] for r in cb.list_backups(dest)]
    assert not listed, f"copia nevalidă (carantină eșuată) apare în listă ca o copie normală: {listed}; eroare: {res.get('error')}"
