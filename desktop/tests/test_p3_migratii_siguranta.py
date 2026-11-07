"""P3-C §7: siguranța actualizării bazei SQLite — proces oprit în mijlocul migrării, copie întreruptă/incompletă, folder nescriibil,
actualizare repetată, căi Windows. Doar copii sintetice în tmp_path; nu se atinge nicio bază reală."""
import os
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from test_p2_migratii_istorice import _schema_sql, _snapshots, build, db, snapshot, mkdb  # noqa: F401  (fixture mkdb)

DESKTOP = Path(__file__).resolve().parents[1]
SHARED = DESKTOP.parent / "shared"


def test_proces_oprit_in_mijlocul_migrarii_lasa_baza_neschimbata_si_migrarea_se_reia(tmp_path):
    path = str(tmp_path / "v1.db")
    build(path, "v1_20b7e92.sql")
    schema_before, data_before = _schema_sql(path), snapshot(path)
    script = textwrap.dedent(f"""
        import sys, time
        sys.path[:0] = [r"{DESKTOP}", r"{SHARED}"]
        import database as db
        db.DB_PATH = r"{path}"
        real = db._add_column_if_missing
        n = {{"i": 0}}
        def slow(*a, **k):
            real(*a, **k)
            n["i"] += 1
            if n["i"] == 3:
                print("MID", flush=True)
                time.sleep(60)
        db._add_column_if_missing = slow
        db.init_db()
    """)
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True,
                            env={**os.environ, "CIMITIR_DB_ENGINE": "sqlite", "DATABASE_URL": "", "CIMITIR_MODE": "local"})
    try:
        line = proc.stdout.readline()
        assert line.strip() == "MID", line
        proc.kill()                      # oprire bruscă (pe Windows = TerminateProcess)
        proc.wait(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()
    assert _schema_sql(path) == schema_before, "schemă pe jumătate migrată după oprirea procesului"
    assert snapshot(path) == data_before
    db.DB_PATH = path
    db.init_db()
    assert snapshot(path)["contracte"][0]["numar_contract"] == "C-1"
    db.init_db()


def test_copie_intrerupta_nu_devine_backup_valid_si_este_curatata(mkdb):  # noqa: F811
    path = mkdb("v1_20b7e92.sql")
    folder = Path(path).parent / db.PRE_MIGRATION_DIR
    folder.mkdir()
    stale = folder / f"{Path(path).name}.20200101-000000.bak.tmp"
    stale.write_bytes(b"SQLite format 3\x00" + b"\x00" * 50)      # copie întreruptă la un restart anterior
    db.init_db()
    assert not stale.exists()
    snaps = _snapshots(path)
    assert len(snaps) == 1 and all(not p.name.endswith(".tmp") for p in snaps)
    conn = sqlite3.connect(snaps[0])
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def test_copia_esuata_la_mijloc_nu_lasa_niciun_fisier_bak_sau_tmp(mkdb, monkeypatch):  # noqa: F811
    path = mkdb("v1_20b7e92.sql")
    data_before = snapshot(path)
    real = db.get_connection

    class Boom:
        def __init__(self, conn):
            self._c = conn

        def backup(self, dst, *a, **k):
            self._c.backup(dst, *a, **k)
            raise KeyboardInterrupt()          # întrerupere după copiere, înainte de redenumire

        def __getattr__(self, n):
            return getattr(self._c, n)
    monkeypatch.setattr(db, "get_connection", lambda: Boom(real()))
    with pytest.raises(KeyboardInterrupt):
        db.init_db()
    folder = Path(path).parent / db.PRE_MIGRATION_DIR
    assert not list(folder.glob("*")), "rămas fișier de copie după întrerupere"
    monkeypatch.setattr(db, "get_connection", real)
    assert snapshot(path) == data_before


def test_folder_pentru_copii_nescriibil_opreste_migrarea_fara_modificari(mkdb, monkeypatch):  # noqa: F811
    path = mkdb("v1_20b7e92.sql")
    schema_before, data_before = _schema_sql(path), snapshot(path)

    def denied(*a, **k):
        raise PermissionError(13, "Access is denied")
    monkeypatch.setattr(os, "makedirs", denied)
    with pytest.raises(PermissionError):
        db.init_db()
    monkeypatch.undo()
    assert _schema_sql(path) == schema_before and snapshot(path) == data_before


def test_actualizare_repetata_peste_baza_deja_migrata_nu_multiplica_copiile(mkdb):  # noqa: F811
    path = mkdb("v1_20b7e92.sql")
    for _ in range(4):
        db.init_db()
    assert len(_snapshots(path)) == 1


def test_calea_bazei_cu_spatii_si_diacritice_functioneaza_la_migrare_si_copie(tmp_path, monkeypatch):
    folder = tmp_path / "Date Cimitir ț ș ă î â"
    folder.mkdir()
    path = str(folder / "cimitir ț.db")
    build(path, "v1_20b7e92.sql")
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    snaps = _snapshots(path)
    assert len(snaps) == 1 and "ț" in snaps[0].name


def test_copia_pre_migrare_poate_fi_restaurata_si_aplicatia_migreaza_din_nou(mkdb, tmp_path, monkeypatch):  # noqa: F811
    path = mkdb("v2_53ceda3.sql")
    before = snapshot(path)
    db.init_db()
    snap = _snapshots(path)[0]
    restored = tmp_path / "restaurat.db"
    restored.write_bytes(snap.read_bytes())
    monkeypatch.setattr(db, "DB_PATH", str(restored))
    db.init_db()
    after = snapshot(str(restored))
    assert after["plati"] == before["plati"] and after["contracte"][0]["numar_contract"] == "C-1"


@pytest.mark.skipif(sys.platform == "win32", reason="chmod nu restricționează scrierea pe Windows; testul Linux simulează PermissionError")
def test_baza_doar_citire_nu_este_modificata(tmp_path, monkeypatch):
    if os.geteuid() == 0:
        pytest.skip("rulează ca root: chmod nu restricționează scrierea")
    path = str(tmp_path / "ro.db")
    build(path, "v1_20b7e92.sql")
    data_before = snapshot(path)
    os.chmod(path, 0o444)
    os.chmod(tmp_path, 0o555)
    try:
        monkeypatch.setattr(db, "DB_PATH", path)
        with pytest.raises((sqlite3.OperationalError, PermissionError)):
            db.init_db()
    finally:
        os.chmod(tmp_path, 0o755)
        os.chmod(path, 0o644)
    assert snapshot(path) == data_before
