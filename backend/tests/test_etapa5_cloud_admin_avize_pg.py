# ruff: noqa: F811
"""Etapa 5 — Cloud admin (postgres_store) cu câmpurile avizelor din migrarea 028, pe PostgreSQL LOCAL (CIMITIR_TEST_DATABASE_URL, altfel SKIP).
Detectare compatibilă: cu 028 -> toate câmpurile; fără 028 (coloane lipsă) -> mod compatibil, fără erori și fără pierderi pe nr/data/lucrare."""
from __future__ import annotations

import pytest

from test_postgres import needs_pg, pg  # noqa: F401

pytestmark = needs_pg
FULL = {"stare": "in_lucru", "executant": "Popa SRL", "data_inceput": "2026-03-05", "data_finalizare": "2026-03-20", "observatii": "fără probleme"}


@pytest.fixture(autouse=True)
def _plot(pg):
    pg.save_plot_to_db("A-1", [], "#111")


def test_extended_detected_and_roundtrip_all_fields(pg):
    assert pg.avize_extended() is True and set(pg.avize_optional_cols(refresh=True)) == set(FULL)
    aid = pg.add_aviz("A-1", "12", "2026-03-01", "bordură", executant="  Ionescu  ", data_inceput="2026-03-02")
    assert aid
    row = pg.get_aviz(aid)
    assert row["executant"] == "Ionescu" and row["data_inceput"] == "2026-03-02" and row["stare"] == "" and row["lucrare"] == "bordură"
    assert pg.update_aviz(aid, {**FULL, "nr": "13"})
    row = pg.get_aviz(aid)
    assert row["nr"] == "13" and all(row[k] == v for k, v in FULL.items())
    assert [a["id"] for a in pg.list_avize_for_loc("A-1")] == [aid] and pg.list_avize_for_loc("A-1")[0]["observatii"] == "fără probleme"


def test_partial_update_keeps_other_fields(pg):
    aid = pg.add_aviz("A-1", "1", None, "x")
    pg.update_aviz(aid, FULL)
    pg.update_aviz(aid, {"stare": "finalizata"})
    row = pg.get_aviz(aid)
    assert row["stare"] == "finalizata" and row["executant"] == "Popa SRL" and row["observatii"] == "fără probleme"
    assert pg.update_aviz(aid, {"necunoscut": "x"}) is False


def test_without_028_columns_compat_mode_no_errors_no_loss(pg):
    with pg.get_connection() as c:
        for col in FULL:
            c.execute(f"ALTER TABLE amenajari_avize DROP COLUMN IF EXISTS {col}")
    assert pg.avize_optional_cols(refresh=True) == frozenset() and pg.avize_extended() is False
    aid = pg.add_aviz("A-1", "7", "2026-01-01", "gard", executant="ignorat", data_inceput="2026-01-02")
    assert aid
    assert pg.update_aviz(aid, {**FULL, "lucrare": "gard nou"})          # câmpurile 028 sunt ignorate, restul se scrie
    assert pg.update_aviz(aid, FULL) is False                              # nimic de scris -> False, fără excepție
    row = pg.get_aviz(aid)
    assert row["lucrare"] == "gard nou" and row["nr"] == "7" and row["stare"] == "" and row["executant"] == ""
    assert pg.list_avize_for_loc("A-1")[0]["id"] == aid


def test_partial_028_is_not_extended(pg):
    with pg.get_connection() as c:
        c.execute("ALTER TABLE amenajari_avize DROP COLUMN observatii")
    assert pg.avize_optional_cols(refresh=True) == frozenset({"stare", "executant", "data_inceput", "data_finalizare"})
    assert pg.avize_extended() is False
    aid = pg.add_aviz("A-1", "7", None, "x", executant="E")
    pg.update_aviz(aid, {**FULL})
    row = pg.get_aviz(aid)
    assert row["executant"] == "Popa SRL" and row["observatii"] == ""
