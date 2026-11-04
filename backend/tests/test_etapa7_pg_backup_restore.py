# ruff: noqa: F811
"""Etapa 7 — backup/restaurare PostgreSQL REALE (pg_dump -Fc / pg_restore) pe baze locale izolate (CIMITIR_TEST_DATABASE_URL), date fictive.
Verifică relațiile (contract→concesionar, plăți→contract, avize 028, înhumați→loc) și că restaurarea nu atinge baza sursă. NU atinge Supabase."""
from __future__ import annotations

import shutil
import subprocess
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest

from test_postgres import TEST_URL, needs_pg, pg  # noqa: F401

pytestmark = [needs_pg, pytest.mark.skipif(not shutil.which("pg_dump"), reason="pg_dump indisponibil")]
RESTORE_DB = "cimitir_restore_test"


def _admin_url(name):
    p = urlparse(TEST_URL)
    return urlunparse(p._replace(path="/" + name))


@pytest.fixture()
def restore_db():
    admin = _admin_url("postgres")
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{RESTORE_DB}"')
        c.execute(f'CREATE DATABASE "{RESTORE_DB}"')
    yield _admin_url(RESTORE_DB)
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{RESTORE_DB}"')


def _seed(pg):
    pg.save_plot_to_db("A-1", [], "#111")
    cid = pg.save_concesionar({"nume": "Fictiv", "prenume": "Ana"})
    kid = pg.save_contract({"cod_loc": "A-1", "concesionar_id": cid, "numar_contract": "C-1"})
    pg.save_inhumat("A-1", {"nume": "Test"})
    pg.add_plata("A-1", 99.5, "2026-01-01", "taxa", "CH-1", "fictiv", contract_id=kid)
    aid = pg.add_aviz("A-1", "AV-1", "2026-03-01", "bordură", executant="Popa SRL", data_inceput="2026-03-05")
    pg.update_aviz(aid, {"stare": "finalizata", "observatii": "ok"})
    return cid, kid


def _counts(url):
    out = {}
    with psycopg.connect(url) as c:
        for t in ("locuri_de_veci", "concesionari", "contracte", "inhumati", "plati", "amenajari_avize", "schema_migrations"):
            out[t] = c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
    return out


def _dump(tmp_path):
    dump = tmp_path / "cimitir.dump"
    subprocess.run(["pg_dump", "-Fc", "-f", str(dump), TEST_URL], check=True, capture_output=True, timeout=120)
    return dump


def test_pg_dump_restore_roundtrip_keeps_records_relations_and_028_columns(pg, tmp_path, restore_db):
    cid, kid = _seed(pg)
    dump = _dump(tmp_path)
    before = _counts(TEST_URL)
    subprocess.run(["pg_restore", "--no-owner", "-d", restore_db, str(dump)], check=True, capture_output=True, timeout=120)
    assert _counts(restore_db) == before and before["contracte"] == 1 and before["plati"] == 1
    with psycopg.connect(restore_db) as c:
        k = c.execute("SELECT concesionar_id, cod_loc FROM contracte").fetchone()
        assert k[0] == cid and k[1] == "A-1"
        assert c.execute("SELECT count(*) FROM plati WHERE contract_id=%s", (kid,)).fetchone()[0] == 1
        a = c.execute("SELECT executant, data_inceput, stare, observatii FROM amenajari_avize").fetchone()
        assert a == ("Popa SRL", "2026-03-05", "finalizata", "ok")
        assert c.execute("SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal").fetchone()[0] > 0
    assert _counts(TEST_URL) == before


def test_truncated_or_corrupt_dump_is_rejected(pg, tmp_path, restore_db):
    _seed(pg)
    raw = _dump(tmp_path).read_bytes()
    bad = tmp_path / "trunc.dump"
    bad.write_bytes(raw[: len(raw) // 3])
    res = subprocess.run(["pg_restore", "--no-owner", "--exit-on-error", "-d", restore_db, str(bad)], capture_output=True, timeout=120)
    assert res.returncode != 0
    junk = tmp_path / "junk.dump"
    junk.write_bytes(b"nu este un dump")
    assert subprocess.run(["pg_restore", "--list", str(junk)], capture_output=True, timeout=60).returncode != 0


def test_restore_without_clean_does_not_overwrite_existing_rows(pg, tmp_path, restore_db):
    _seed(pg)
    dump = _dump(tmp_path)
    subprocess.run(["pg_restore", "--no-owner", "-d", restore_db, str(dump)], check=True, capture_output=True, timeout=120)
    with psycopg.connect(restore_db, autocommit=True) as c:
        c.execute("UPDATE concesionari SET nume='MODIFICAT-DUPA-RESTAURARE'")
    again = subprocess.run(["pg_restore", "--no-owner", "-d", restore_db, str(dump)], capture_output=True, timeout=120)
    assert again.returncode != 0 or b"already exists" in again.stderr
    with psycopg.connect(restore_db) as c:
        assert c.execute("SELECT nume FROM concesionari").fetchone()[0] == "MODIFICAT-DUPA-RESTAURARE"
