"""P2 §5: erori/întreruperi la backup și restaurare, simulate în foldere temporare (date fictive).

Regula verificată: operațiunea fie reușește complet, fie este anulată fără urme și marcată EXPLICIT ca eșuată (jurnal/excepție);
nicio pierdere silențioasă, sursa nu se modifică.
"""
from __future__ import annotations

import errno
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "shared", _ROOT / "desktop"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import cimitir_backup as bk  # noqa: E402
import cimitir_database as db  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "historic_schemas"


def tree_digest(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file() and not p.name.endswith(("-wal", "-shm"))}


@pytest.fixture()
def world(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(db, "DATA_DIR", str(data))
    previous = db._active_db_path.get()
    db.set_active_db_path(str(data / "cimitir.db"))
    db.init_db()
    db.save_plot_to_db("A-1", [(0, 0), (4, 0), (4, 3)], "#111")
    cid = db.save_concesionar({"nume": "Fictiv", "prenume": "Ana"})
    db.save_contract({"numar_contract": "C-1", "cod_loc": "A-1", "concesionar_id": cid})
    db.add_plata("A-1", 10, "2026-01-02", chitanta_nr="R-1")
    src = tmp_path / "poza.bin"
    src.write_bytes(b"\x89PNG-fictiv")
    db.save_photo("A-1", db.import_media_file(str(src), "loc"))
    yield {"data": data, "tmp": tmp_path}
    db.set_active_db_path(previous)


def settings_for(tmp_path):
    return lambda key: {"backup_destination": str(tmp_path / "backups")}.get(key)


def test_disc_plin_la_copierea_bazei_marcheaza_esec_explicit_fara_copie_partiala(world, monkeypatch):
    before = tree_digest(world["data"])
    real = sqlite3.Connection

    def boom(*a, **k):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(bk.shutil, "copytree", boom)
    dest = world["tmp"] / "backups"
    res = bk.run_backup_job(world["data"], lambda k: {"backup_destination": str(dest)}.get(k), force=True, trigger="manual")
    assert res["result"] == "failed" and "space" in res["error"].lower()
    assert [p for p in dest.glob("cimitir-*")] == [], "copie parțială rămasă"
    assert tree_digest(world["data"]) == before
    log = bk.read_log(world["data"], 5)
    assert log and log[0]["result"] == "failed"


def test_acces_refuzat_la_fisier_media_este_esec_explicit_si_sursa_neatinsa(world, monkeypatch):
    before = tree_digest(world["data"])

    def deny(*a, **k):
        raise PermissionError(errno.EACCES, "Permission denied")
    monkeypatch.setattr(bk.shutil, "copytree", deny)
    dest = world["tmp"] / "backups"
    res = bk.run_backup_job(world["data"], lambda k: {"backup_destination": str(dest)}.get(k), force=True, trigger="manual")
    assert res["result"] == "failed" and "denied" in res["error"].lower()
    assert list(dest.glob("cimitir-*")) == [] and tree_digest(world["data"]) == before


def test_fisier_media_lipsa_din_disc_nu_blocheaza_backup_dar_este_semnalat_de_verificarea_de_integritate(world):
    media = [p for p in (world["data"] / "media").rglob("*") if p.is_file()]
    assert media
    media[0].unlink()
    target = bk.backup(world["data"], world["tmp"] / "backups")
    assert bk.verify(target) == []
    conn = sqlite3.connect(target / "cimitir.db")
    row = conn.execute("SELECT file_path FROM photos").fetchone()
    conn.close()
    assert row and not Path(row[0]).exists(), "fotografia lipsă trebuie să rămână vizibilă în DB (nu se șterge înregistrarea)"


def test_restaurare_cu_disc_plin_readuce_starea_anterioara(world, monkeypatch):
    target = bk.backup(world["data"], world["tmp"] / "backups")
    db.add_plata("A-1", 99, "2026-02-02", chitanta_nr="R-2")
    before = tree_digest(world["data"])
    real = shutil.copy2

    def full(src, dst, *a, **k):
        if str(dst).endswith("cimitir.db"):
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(src, dst, *a, **k)
    monkeypatch.setattr(bk.shutil, "copy2", full)
    with pytest.raises(OSError):
        bk.restore(target, world["data"], force=True)
    monkeypatch.setattr(bk.shutil, "copy2", real)
    assert tree_digest(world["data"]) == before
    assert not list(world["tmp"].glob("data.inainte-de-restaurare-*")), "folderul de siguranță trebuie readus la locul lui"


def test_restaurare_intrerupta_de_utilizator_readuce_starea_anterioara(world, monkeypatch):
    target = bk.backup(world["data"], world["tmp"] / "backups")
    before = tree_digest(world["data"])

    def interrupted(*a, **k):
        raise KeyboardInterrupt()
    monkeypatch.setattr(bk, "_rebase_media_paths", interrupted)
    with pytest.raises(KeyboardInterrupt):
        bk.restore(target, world["data"], force=True)
    assert tree_digest(world["data"]) == before


def test_backup_vechi_cu_schema_veche_se_restaureaza_apoi_se_migreaza_fara_pierderi(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    old = data / "cimitir.db"
    conn = sqlite3.connect(old)
    conn.executescript((FIX / "v1_20b7e92.sql").read_text(encoding="utf-8"))
    conn.execute("INSERT INTO locuri_de_veci (cod_loc, points, color) VALUES ('A-1','[]','#fff')")
    conn.execute("INSERT INTO plati (cod_loc, suma, data_plata) VALUES ('A-1', 33.5, '2020-01-01')")
    conn.commit(); conn.close()
    backup_dir = bk.backup(data, tmp_path / "backups")
    restored = tmp_path / "restaurat"
    bk.restore(backup_dir, restored)
    monkeypatch.setattr(db, "DB_PATH", str(restored / "cimitir.db"))
    db.init_db()
    assert db.total_plati("A-1") == pytest.approx(33.5)
    assert db.load_loc_by_cod("A-1") is not None
    conn = sqlite3.connect(restored / "cimitir.db")
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def test_dupa_restaurare_plata_retrimisa_nu_se_dubleaza(world):
    target = bk.backup(world["data"], world["tmp"] / "backups")
    restored = world["tmp"] / "restaurat"
    bk.restore(target, restored)
    db.set_active_db_path(str(restored / "cimitir.db"))
    pid = db.add_plata("A-1", 10, "2026-01-02", chitanta_nr="R-1")
    assert [p["id"] for p in db.list_plati("A-1")] == [pid] and db.total_plati("A-1") == pytest.approx(10)
