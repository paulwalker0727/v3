"""P2 §4: migrarea SQLite peste scheme istorice SINTETICE (DDL din istoricul Git + o schemă ipotetică minimală).

Nu se folosesc date reale: rândurile sunt fictive, bazele sunt în tmp_path. Fiecare schemă istorică este
populată, migrată de două ori (idempotență) și comparată: numărul de rânduri, conținutul coloanelor existente,
integrity_check și foreign_key_check.
"""
import os
import sqlite3
import sys
from pathlib import Path

import pytest

DESKTOP = Path(__file__).resolve().parents[1]
SHARED = DESKTOP.parent / "shared"
for p in (str(DESKTOP), str(SHARED)):
    if p not in sys.path:
        sys.path.insert(0, p)
FIX = Path(__file__).parent / "fixtures" / "historic_schemas"
SCHEMAS = sorted(p.name for p in FIX.glob("*.sql"))

import database as db  # noqa: E402

SEED = {
    "locuri_de_veci": [{"cod_loc": "A-01-01", "points": "[[0,0],[1,0],[1,1]]", "color": "#fff", "sector": "A", "parcela": "1", "rand": "1", "numar": "1", "suprafata": 6.0},
                       {"cod_loc": "A-01-02", "points": "[]", "color": "#fff", "sector": "A"}],
    "concesionari": [{"nume": "Popescu", "prenume": "Ana", "cnp": "2000000000001", "adresa": "Str. Fictivă 1"}],
    "contracte": [{"numar_contract": "C-1", "data_contract": "01.01.2015", "data_inceput": "01.01.2015", "data_sfarsit": "01.01.2045",
                   "concesionar_id": 1, "id_loc": 1, "observatii": "ț ș ă î â"}],
    "inhumati": [{"nume": "Ionescu", "prenume": "Ion", "data_inhumare": "02.02.2016", "id_loc": 1},
                 {"nume": "Ionescu", "prenume": "Maria", "data_inhumare": "03.03.2018", "id_loc": 1}],
    "plati": [{"cod_loc": "A-01-01", "suma": 250.5, "data_plata": "2015-01-02", "tip": "concesiune", "chitanta_nr": "R-1"}],
    "photos": [{"cod_loc": "A-01-01", "file_path": "media/poza.jpg"}],
    "amenajari_avize": [{"cod_loc": "A-01-01", "nr_aviz": "AV-1", "data_aviz": "2016-05-05"}],
    "documents": [{"entity_type": "loc", "entity_id": "A-01-01", "file_path": "media/doc.pdf"}],
    "settings": [{"key": "oras", "value": "Fictiv"}],
    "users": [{"username": "op", "password_hash": "x", "role": "user", "created_at": "2020-01-01"}],
}
ORDER = ["locuri_de_veci", "concesionari", "contracte", "inhumati", "plati", "photos", "amenajari_avize", "documents", "settings", "users"]


def _cols(conn, t):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]


def build(path, schema_file):
    conn = sqlite3.connect(path)
    conn.executescript((FIX / schema_file).read_text(encoding="utf-8"))
    conn.execute("PRAGMA foreign_keys=ON")
    for t in ORDER:
        have = set(_cols(conn, t))
        for row in SEED[t]:
            r = {k: v for k, v in row.items() if k in have}
            conn.execute(f"INSERT INTO {t} ({','.join(r)}) VALUES ({','.join('?' * len(r))})", tuple(r.values()))
    conn.commit()
    conn.close()


def snapshot(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    out = {}
    for t in ORDER:
        out[t] = [dict(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1")]
    conn.close()
    return out


@pytest.fixture
def mkdb(tmp_path, monkeypatch):
    def _make(schema):
        path = str(tmp_path / f"{schema}.db")
        build(path, schema)
        monkeypatch.setattr(db, "DB_PATH", path)
        monkeypatch.setattr(db, "_active_db_path", db._active_db_path)
        return path
    return _make


@pytest.mark.parametrize("schema", SCHEMAS)
def test_migrare_pastreaza_datele_si_e_idempotenta(schema, mkdb, capsys):
    path = mkdb(schema)
    before = snapshot(path)
    db.init_db()
    after1 = snapshot(path)
    db.init_db()
    after2 = snapshot(path)
    conn = sqlite3.connect(path)
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()
    assert integrity == "ok" and fk == []
    counts_b = {t: len(r) for t, r in before.items()}
    counts_a = {t: len(r) for t, r in after1.items()}
    with capsys.disabled():
        print(f"\n[MIGRARE {schema}] integrity={integrity} fk_violations={len(fk)} inainte={counts_b} dupa={counts_a}")
    assert {t: n for t, n in counts_b.items() if t != 'settings'} == {t: n for t, n in counts_a.items() if t != 'settings'}
    assert counts_a['settings'] >= counts_b['settings']
    for t in [x for x in ORDER if x != 'settings']:
        for rb, ra in zip(before[t], after1[t]):
            for k, v in rb.items():
                if v is not None:
                    assert ra[k] == v, (schema, t, k)
    for t in ("contracte", "inhumati", "locuri_de_veci", "plati"):
        a2 = [{k: v for k, v in r.items() if k not in ("uid",)} for r in after2[t]]
        a1 = [{k: v for k, v in r.items() if k not in ("uid",)} for r in after1[t]]
        assert a1 == a2
    assert [r["uid"] for r in after1["locuri_de_veci"]] == [r["uid"] for r in after2["locuri_de_veci"]]


@pytest.mark.parametrize("schema", SCHEMAS)
def test_dupa_migrare_fluxul_contract_plata_inhumat_functioneaza(schema, mkdb):
    mkdb(schema)
    db.init_db()
    pid = db.add_plata("A-01-01", 10, "2024-01-01", contract_id=1)
    assert pid, "add_plata trebuie să funcționeze și pe baze migrate din scheme vechi"
    assert db.get_contract(1)["numar_contract"] == "C-1"
    assert len(db.load_inhumati_for_loc("A-01-01")) == 2
    assert db.total_plati("A-01-01") == pytest.approx(260.5)
    assert any(p["contract_id"] == 1 for p in db.list_plati("A-01-01"))


class _FaultyConn:
    """Proxy peste sqlite3.Connection: eșuează la al N-lea ALTER TABLE (simulează disc plin)."""

    def __init__(self, real, fail_on_alter=1, message="database or disk is full"):
        self._real, self._n, self._msg, self._seen = real, fail_on_alter, message, 0

    def execute(self, sql, *a):
        if sql.lstrip().upper().startswith("ALTER TABLE"):
            self._seen += 1
            if self._seen == self._n:
                raise sqlite3.OperationalError(self._msg)
        return self._real.execute(sql, *a)

    def cursor(self):
        return self._real.cursor()

    def __getattr__(self, name):
        return getattr(self._real, name)


def _schema_sql(path):
    conn = sqlite3.connect(path)
    try:
        return sorted(r[0] for r in conn.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"))
    finally:
        conn.close()


@pytest.mark.parametrize("fail_on", [1, 2, 4])
def test_disc_plin_in_timpul_migrarii_nu_este_ignorat_si_se_anuleaza(mkdb, monkeypatch, fail_on):
    path = mkdb("v1_20b7e92.sql")
    schema_before, data_before = _schema_sql(path), snapshot(path)
    real = db.get_connection
    monkeypatch.setattr(db, "get_connection", lambda: _FaultyConn(real(), fail_on_alter=fail_on))
    with pytest.raises(sqlite3.OperationalError, match="disk is full"):
        db.init_db()
    monkeypatch.setattr(db, "get_connection", real)
    assert _schema_sql(path) == schema_before, "migrare parțială rămasă după eroare"
    assert snapshot(path) == data_before
    db.init_db()
    assert snapshot(path)["contracte"][0]["numar_contract"] == "C-1"


def test_baza_blocata_la_migrare_da_eroare_explicita_fara_modificari(mkdb, monkeypatch):
    path = mkdb("v1_20b7e92.sql")
    schema_before = _schema_sql(path)
    real = db.get_connection

    def short():
        c = real()
        c.execute("PRAGMA busy_timeout = 100")
        return c
    monkeypatch.setattr(db, "get_connection", short)
    blocker = sqlite3.connect(path, isolation_level=None)
    blocker.execute("PRAGMA journal_mode=WAL")
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked|busy"):
            db.init_db()
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
    assert _schema_sql(path) == schema_before
    db.init_db()


def test_baza_corupta_nu_este_suprascrisa(tmp_path, monkeypatch):
    path = tmp_path / "corupt.db"
    path.write_bytes(b"NU ESTE SQLITE" * 200)
    raw = path.read_bytes()
    monkeypatch.setattr(db, "DB_PATH", str(path))
    with pytest.raises(sqlite3.DatabaseError):
        db.init_db()
    assert path.read_bytes() == raw


def _snapshots(path):
    folder = Path(path).parent / db.PRE_MIGRATION_DIR
    return sorted(folder.glob("*.bak")) if folder.is_dir() else []


def test_migrarea_unei_baze_vechi_face_inainte_copie_consistenta(mkdb):
    path = mkdb("v1_20b7e92.sql")
    before = snapshot(path)
    db.init_db()
    snaps = _snapshots(path)
    assert len(snaps) == 1
    conn = sqlite3.connect(snaps[0])
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert "executant" not in [r[1] for r in conn.execute("PRAGMA table_info(amenajari_avize)")], "copia trebuie să fie schema veche"
    assert conn.execute("SELECT COUNT(*) FROM plati").fetchone()[0] == len(before["plati"])
    conn.close()
    db.init_db()
    assert len(_snapshots(path)) == 1, "a doua pornire (nimic de migrat) nu mai face copii"


def test_baza_noua_sau_deja_la_zi_nu_face_copii(tmp_path, monkeypatch, mkdb):
    fresh = tmp_path / "fresh" / "cimitir.db"
    fresh.parent.mkdir()
    monkeypatch.setattr(db, "DB_PATH", str(fresh))
    db.init_db()
    assert _snapshots(fresh) == []
    path = mkdb("v3_current_3f05ae5.sql")
    db.init_db()
    assert _snapshots(path) == []


def test_copia_dinainte_de_migrare_esuata_blocheaza_migrarea_fara_modificari(mkdb, monkeypatch):
    path = mkdb("v1_20b7e92.sql")
    schema_before, data_before = _schema_sql(path), snapshot(path)
    real = db.get_connection

    class NoSpace(_FaultyConn):
        def backup(self, *a, **k):
            raise OSError(28, "No space left on device")
    monkeypatch.setattr(db, "get_connection", lambda: NoSpace(real(), fail_on_alter=10_000))
    with pytest.raises(OSError, match="No space"):
        db.init_db()
    monkeypatch.setattr(db, "get_connection", real)
    assert _schema_sql(path) == schema_before and snapshot(path) == data_before
    assert _snapshots(path) == [], "copie parțială rămasă"


def test_se_pastreaza_doar_ultimele_copii_de_migrare(mkdb):
    path = mkdb("v1_20b7e92.sql")
    folder = Path(path).parent / db.PRE_MIGRATION_DIR
    folder.mkdir()
    for i in range(8):
        (folder / f"{Path(path).name}.2020010{i}-000000.bak").write_bytes(b"x")
    db.init_db()
    assert len(_snapshots(path)) == db.PRE_MIGRATION_KEEP
