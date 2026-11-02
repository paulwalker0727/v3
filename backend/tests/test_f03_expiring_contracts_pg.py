# ruff: noqa: F811
"""F-03 — aceeași logică `expiring_contracts()` în PostgreSQL (panoul web/Cloud) și în SQLite: formate DD.MM.YYYY / YYYY-MM-DD, limite, date invalide.
Rulează doar cu CIMITIR_TEST_DATABASE_URL (baza locală de test; altfel SKIP). Date sintetice; ziua curentă fixată la 2026-03-10."""
from __future__ import annotations

import datetime

from test_postgres import needs_pg, pg, sq  # noqa: F401

CASES = [("RO-EXP", "01.03.2026"), ("ISO-EXP", "2026-03-01"), ("RO-AZI", "10.03.2026"), ("ISO-AZI", "2026-03-10"),
         ("RO-LIM", "09.04.2026"), ("ISO-LIM", "2026-04-09"), ("RO-DUPA", "10.04.2026"), ("ISO-DUPA", "2026-04-10"),
         ("BAD-FEB", "31.02.2026"), ("BAD-TXT", "nu e data"), ("BLANK", ""), ("TIME", "2026-03-02T10:00:00"), ("ONE-DIGIT", "5.3.2026")]


class FakeDate(datetime.date):
    @classmethod
    def today(cls):
        return cls(2026, 3, 10)


def _fill(store):
    store.save_plot_to_db("A-1", [(0, 0), (4, 0), (4, 3)], "#111")
    for nr, end in CASES:
        store.save_contract({"cod_loc": "A-1", "numar_contract": nr, "data_sfarsit": end})


def _nrs(store, days):
    return [c["numar_contract"] for c in store.expiring_contracts(days)]


@needs_pg
def test_postgres_and_sqlite_agree_and_follow_the_rules(pg, sq, monkeypatch):
    monkeypatch.setattr(pg, "date", FakeDate)
    monkeypatch.setattr(sq, "date", FakeDate)
    _fill(pg)
    _fill(sq)
    for days in (0, 5, 30, 31, 9999):
        a, b = _nrs(pg, days), _nrs(sq, days)
        assert sorted(a) == sorted(b), (days, a, b)
    assert sorted(_nrs(pg, 0)) == ["ISO-AZI", "ISO-EXP", "ONE-DIGIT", "RO-AZI", "RO-EXP", "TIME"]
    assert sorted(_nrs(pg, 30)) == sorted(_nrs(pg, 0) + ["ISO-LIM", "RO-LIM"])
    assert "RO-DUPA" not in _nrs(pg, 30) and "RO-DUPA" in _nrs(pg, 31)
    assert not {"BAD-FEB", "BAD-TXT", "BLANK"} & set(_nrs(pg, 99999))
    rows = pg.expiring_contracts(0)
    assert [r["numar_contract"] for r in rows][0] in ("RO-EXP", "ISO-EXP")      # cel mai vechi primul
    first = rows[0]
    assert first["_expira"] == "2026-03-01" and first["_expira_raw"] in ("01.03.2026", "2026-03-01") and first["_date_col"] == "data_sfarsit"
    stored = {r["numar_contract"]: r["data_sfarsit"] for r in pg.load_contracte()}
    assert stored["BAD-FEB"] == "31.02.2026" and stored["RO-EXP"] == "01.03.2026"        # datele istorice nu s-au modificat
