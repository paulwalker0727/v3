"""Etapa 7 — backup/restaurare REALE pe baze SQLite locale izolate (tmp), cu date fictive: contracte, concesionari, înhumați, plăți, avize,
documente/fotografii (fișiere reale), relațiile dintre ele; restaurare incompletă, fișiere corupte, prevenirea suprascrierii accidentale."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import sys

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "shared", _ROOT / "desktop"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import cimitir_backup as bk
import cimitir_database as db

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8cfc0f01f0005000101a5f645400000000049454e44ae426082")
TABLES = ("locuri_de_veci", "concesionari", "contracte", "inhumati", "plati", "amenajari_avize", "documents", "photos")


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rows(db_file, table):
    c = sqlite3.connect(db_file)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(f"SELECT * FROM {table} ORDER BY id")] if table != "locuri_de_veci" \
            else [dict(r) for r in c.execute("SELECT * FROM locuri_de_veci ORDER BY cod_loc")]
    finally:
        c.close()


@pytest.fixture()
def world(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(db, "DATA_DIR", str(data))
    previous = db._active_db_path.get()
    db.set_active_db_path(str(data / "cimitir.db"))
    db.init_db()
    db.save_plot_to_db("A-1", [(0, 0), (4, 0), (4, 3)], "#111")
    db.save_plot_to_db("A-2", [(5, 0), (9, 0), (9, 3)], "#222")
    cid = db.save_concesionar({"nume": "Fictiv", "prenume": "Ana", "cnp": "0000000000000", "telefon": "0700000000"})
    kid = db.save_contract({"numar_contract": "C-1", "cod_loc": "A-1", "concesionar_id": cid, "data_sfarsit": "2099-01-01"})
    iid = db.save_inhumat("A-1", {"nume": "Test", "prenume": "Inhumat", "data_deces": "01.02.2020"})
    db.add_plata("A-1", 120.5, "2026-01-02", "taxa", "CH-1", "fictiv", contract_id=kid)
    aid = db.add_aviz("A-1", "AV-1", "2026-03-01", "bordură", executant="Popa SRL", data_inceput="2026-03-05")
    db.update_aviz(aid, {"stare": "in_lucru", "observatii": "ok"})
    src = tmp_path / "poza.png"
    src.write_bytes(PNG)
    db.save_photo("A-1", db.import_media_file(str(src), "loc"))
    doc = db.add_document("inhumat", iid, "certificat", str(src), caption="cert")
    assert cid and kid and iid and aid and doc
    yield {"data": data, "tmp": tmp_path, "cid": cid, "kid": kid, "iid": iid, "aid": aid}
    db.set_active_db_path(previous)


def test_full_backup_restore_keeps_all_records_relations_and_media_bytes(world):
    data, tmp = world["data"], world["tmp"]
    dest = tmp / "backups"
    snap = bk.backup(data, dest)
    assert bk.verify(snap) == [] and (snap / "manifest.json").is_file()
    target = tmp / "restored"
    bk.restore(snap, target)
    for t in TABLES:
        assert len(_rows(target / "cimitir.db", t)) == len(_rows(data / "cimitir.db", t)) >= 1, t
    con = sqlite3.connect(target / "cimitir.db")
    con.row_factory = sqlite3.Row
    k = con.execute("SELECT * FROM contracte").fetchone()
    assert k["cod_loc"] == "A-1" and k["concesionar_id"] == world["cid"]
    assert con.execute("SELECT COUNT(*) FROM plati WHERE contract_id=?", (world["kid"],)).fetchone()[0] == 1
    av = con.execute("SELECT * FROM amenajari_avize").fetchone()
    assert (av["executant"], av["stare"], av["data_inceput"], av["observatii"]) == ("Popa SRL", "in_lucru", "2026-03-05", "ok")
    for tbl in ("documents", "photos"):
        for row in con.execute(f"SELECT file_path FROM {tbl}"):
            assert Path(row["file_path"]).is_file() and _digest(row["file_path"]) == hashlib.sha256(PNG).hexdigest()
            assert str(target) in row["file_path"], "căile media trebuie să indice folderul restaurat"
    con.close()
    from cimitir_integrity import run_on_file
    rep = run_on_file(str(target / "cimitir.db"))
    assert not [i for i in rep.issues if getattr(i, "severity", "") in ("critic", "critical", "eroare")]


def test_backup_does_not_modify_source_and_works_while_db_is_open(world):
    data = world["data"]
    before = _digest(data / "cimitir.db")
    conn = sqlite3.connect(data / "cimitir.db")
    conn.execute("SELECT 1 FROM locuri_de_veci").fetchall()
    snap = bk.backup(data, world["tmp"] / "b")
    conn.close()
    assert bk.verify(snap) == [] and _digest(data / "cimitir.db") == before


def test_incomplete_backup_missing_media_file_or_manifest_is_rejected_and_nothing_changes(world):
    data, tmp = world["data"], world["tmp"]
    snap = bk.backup(data, tmp / "b")
    victim = next((snap / "media").rglob("*.png"))
    victim.unlink()
    assert any("lipsește" in p for p in bk.verify(snap))
    target = tmp / "t1"
    with pytest.raises(SystemExit):
        bk.restore(snap, target)
    assert not target.exists()
    snap2 = bk.backup(data, tmp / "b2")
    (snap2 / "manifest.json").unlink()
    assert bk.verify(snap2) and bk.list_backups(tmp / "b2") == []
    with pytest.raises(SystemExit):
        bk.restore(snap2, tmp / "t2")


def test_corrupted_database_file_and_corrupt_manifest_are_detected(world):
    data, tmp = world["data"], world["tmp"]
    snap = bk.backup(data, tmp / "b")
    raw = bytearray((snap / "cimitir.db").read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    (snap / "cimitir.db").write_bytes(bytes(raw))
    assert any("cimitir.db" in p for p in bk.verify(snap))
    with pytest.raises(SystemExit):
        bk.restore(snap, tmp / "t")
    with pytest.raises(SystemExit):
        bk.schedule_restore(snap, tmp / "t")
    snap2 = bk.backup(data, tmp / "b2")
    (snap2 / "manifest.json").write_text("{nu e json", encoding="utf-8")
    assert bk.verify(snap2) == ["manifest.json lipsește sau este corupt"]
    snap3 = bk.backup(data, tmp / "b3")
    (snap3 / "manifest.json").write_text(json.dumps({"files": {"../../etc/passwd": "x", "cimitir.db": "y"}}), encoding="utf-8")
    assert any("nesigură" in p for p in bk.verify(snap3))


def test_restore_never_overwrites_without_force_and_keeps_safety_copy(world):
    data, tmp = world["data"], world["tmp"]
    snap = bk.backup(data, tmp / "b")
    db.set_setting("cimitir_nume", "Modificat după backup")
    marker = _digest(data / "cimitir.db")
    with pytest.raises(SystemExit):
        bk.restore(snap, data)
    assert _digest(data / "cimitir.db") == marker
    bk.restore(snap, data, force=True)
    safety = [p for p in tmp.iterdir() if ".inainte-de-restaurare-" in p.name]
    assert len(safety) == 1 and _digest(safety[0] / "cimitir.db") == marker
    bk.restore(snap, data, force=True)
    assert len([p for p in tmp.iterdir() if ".inainte-de-restaurare-" in p.name]) == 2


def test_failure_in_the_middle_of_restore_brings_back_the_previous_state(world, monkeypatch):
    data, tmp = world["data"], world["tmp"]
    snap = bk.backup(data, tmp / "b")
    marker = _digest(data / "cimitir.db")
    real = shutil.copytree

    def boom(*a, **k):
        raise OSError("disc plin (simulat)")
    monkeypatch.setattr(bk.shutil, "copytree", boom)
    with pytest.raises(OSError):
        bk.restore(snap, data, force=True)
    monkeypatch.setattr(bk.shutil, "copytree", real)
    assert _digest(data / "cimitir.db") == marker and any((data / "media").rglob("*.png"))
    assert not [p for p in tmp.iterdir() if ".inainte-de-restaurare-" in p.name]


def test_failed_backup_leaves_no_partial_copy(world, monkeypatch):
    dest = world["tmp"] / "b"
    monkeypatch.setattr(bk.shutil, "copytree", lambda *a, **k: (_ for _ in ()).throw(OSError("simulat")))
    with pytest.raises(OSError):
        bk.backup(world["data"], dest)
    assert bk.list_backups(dest) == [] and not any(dest.iterdir())


def test_pending_restore_applies_once_and_reports_invalid_copy(world):
    data, tmp = world["data"], world["tmp"]
    snap = bk.backup(data, tmp / "b")
    db.set_setting("cimitir_nume", "Stricat")
    bk.schedule_restore(snap, data)
    msg = bk.apply_pending_restore(data)
    assert msg and "restaurată" in msg and bk.apply_pending_restore(data) is None
    assert not bk.pending_marker(data).exists()
    bad = bk.backup(data, tmp / "b2")
    bk.schedule_restore(bad, data)
    (bad / "cimitir.db").write_bytes(b"corupt")
    msg = bk.apply_pending_restore(data)
    assert "nu a putut fi aplicată" in msg and bk.pending_marker(data).exists()
    assert sqlite3.connect(data / "cimitir.db").execute("PRAGMA integrity_check").fetchone()[0] == "ok"
