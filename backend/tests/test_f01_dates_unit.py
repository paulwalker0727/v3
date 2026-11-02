"""F-01 — conversii de dată (MAIN zz.ll.aaaa <-> Cloud/Mobile aaaa-ll-zz), în ambele sensuri, compatibile cu datele istorice.
Teste pure (fără bază de date, fără rețea)."""
from __future__ import annotations

from datetime import date

import pytest

import cimitir_dates as d
import cimitir_entities as ents


# ------------------------------------------------------------------ parse / to_iso / to_ro
@pytest.mark.parametrize("raw,iso", [
    ("05.03.2026", "2026-03-05"), ("5.3.2026", "2026-03-05"), ("2026-03-05", "2026-03-05"),
    ("05/03/2026", "2026-03-05"), ("05-03-2026", "2026-03-05"), ("05032026", "2026-03-05"),
    ("  05.03.2026 ", "2026-03-05"), ("29.02.2024", "2024-02-29"), ("31.12.1999", "1999-12-31"),
])
def test_to_iso_accepts_all_historic_formats(raw, iso):
    assert d.to_iso(raw) == iso
    assert d.parse_date(raw) == date.fromisoformat(iso)


@pytest.mark.parametrize("raw", ["31.02.2026", "29.02.2025", "00.01.2026", "32.01.2026", "2026-13-01", "2026-02-30", "circa 1940",
                                 "05.03.26", "2026/03/05", "05.03-2026", "abc", "1.1", "2026-03-05 10:00", "20260305"])
def test_unparseable_or_non_date_value_is_preserved_exactly(raw):
    assert d.to_iso(raw) == raw          # nu se golește, nu devine data curentă, nu se „repară"
    assert d.to_ro(raw) == raw
    assert d.parse_date(raw) is None


def test_blank_values_stay_blank_and_none_stays_none():
    assert d.to_iso("") == "" and d.to_iso(None) is None and d.to_iso("   ") == "   "
    assert d.to_ro("") == "" and d.to_ro(None) is None


def test_roundtrip_both_directions_for_every_day_of_two_years():
    day = date(2023, 1, 1)
    while day < date(2025, 1, 1):
        ro, iso = day.strftime("%d.%m.%Y"), day.isoformat()
        assert d.to_iso(ro) == iso and d.to_ro(iso) == ro
        assert d.to_ro(d.to_iso(ro)) == ro and d.to_iso(d.to_ro(iso)) == iso
        day = date.fromordinal(day.toordinal() + 1)


def test_datetime_values_are_not_truncated_but_interpretable():
    assert d.to_iso("2026-03-05T10:20:30") == "2026-03-05T10:20:30"      # ora nu se pierde
    assert d.parse_date("2026-03-05T10:20:30", allow_time=True) == date(2026, 3, 5)
    assert d.parse_date("2026-03-05 10:20", allow_time=True) == date(2026, 3, 5)
    assert d.parse_date("2026-03-05T10:20:30Z", allow_time=True) == date(2026, 3, 5)
    assert d.parse_date("2026-03-05T10:20:30", allow_time=False) is None


def test_for_local_keeps_same_day_text_and_otherwise_uses_main_format():
    assert d.for_local("2026-03-05", "2026-03-05") == "2026-03-05"       # aceeași zi, format vechi ISO local: neatins
    assert d.for_local("05.03.2026", "2026-03-05") == "05.03.2026"
    assert d.for_local("5.3.2026", "2026-03-05") == "5.3.2026"
    assert d.for_local("01.01.2020", "2026-03-05") == "05.03.2026"        # ziua s-a schimbat în Cloud
    assert d.for_local(None, "2026-03-05") == "05.03.2026"                # înregistrare nouă
    assert d.for_local("05.03.2026", "") == ""                            # golit deliberat în Cloud
    assert d.for_local("05.03.2026", "circa 1940") == "circa 1940"        # nerecunoscut: neschimbat
    assert d.for_local("05.03.2026", None) is None


def test_days_until_never_guesses():
    assert d.days_until("10.03.2026", date(2026, 3, 5)) == 5
    assert d.days_until("2026-03-01", date(2026, 3, 5)) == -4
    assert d.days_until("nu-e-data", date(2026, 3, 5)) is None
    assert d.days_until("", date(2026, 3, 5)) is None


# ------------------------------------------------------------------ cimitir_entities
def test_date_cols_are_real_columns_of_each_entity():
    for entity, cols in ents.DATE_COLS.items():
        assert set(cols) <= set(ents.ENTITIES[entity]["cols"]), entity
    assert set(ents.DATE_COLS) == {"contracte", "inhumati", "plati", "amenajari_avize"}
    assert "created_at" not in ents.date_cols("plati") and "created_at" not in ents.date_cols("amenajari_avize")


def test_clean_data_normalizes_every_date_column_of_every_entity_to_iso():
    for entity, cols in ents.DATE_COLS.items():
        ro = {c: "05.03.2026" for c in cols}
        assert {c: ents.clean_data(entity, ro)[c] for c in cols} == {c: "2026-03-05" for c in cols}, entity


def test_clean_data_keeps_invalid_blank_and_absent_dates():
    out = ents.clean_data("contracte", {"numar_contract": "C1", "data_contract": "31.02.2026", "data_inceput": "", "data_sfarsit": None})
    assert out["data_contract"] == "31.02.2026" and out["data_inceput"] == "" and out["data_sfarsit"] is None
    av = ents.clean_data("amenajari_avize", {"nr_aviz": "1", "data_aviz": "05.03.2026"})     # coloane opționale absente rămân absente
    assert av["data_aviz"] == "2026-03-05" and "data_finalizare" not in av and "data_inceput" not in av


def test_same_data_and_changed_fields_treat_equivalent_formats_as_equal_but_detect_real_changes():
    a = {"data_contract": "05.03.2026", "data_sfarsit": "2036-03-05", "numar_contract": "C1"}
    b = {"data_contract": "2026-03-05", "data_sfarsit": "05.03.2036", "numar_contract": "C1"}
    assert ents.same_data("contracte", a, b) and ents.changed_fields("contracte", a, b) == []
    b["data_sfarsit"] = "06.03.2036"
    assert not ents.same_data("contracte", a, b) and ents.changed_fields("contracte", a, b) == ["data_sfarsit"]
    c = {"data_contract": "31.02.2026"}      # invalid vs. valid: diferit (nu se declară egale)
    assert not ents.same_data("contracte", c, {"data_contract": "2026-02-28"})


def test_canon_dates_does_not_mutate_input():
    src = {"data_plata": "05.03.2026", "suma": 10}
    out = ents.canon_dates("plati", src)
    assert out["data_plata"] == "2026-03-05" and src["data_plata"] == "05.03.2026"


@pytest.mark.parametrize("entity,col", [(e, c) for e, cs in ents.DATE_COLS.items() for c in cs])
def test_idempotent_normalization(entity, col):
    once = ents.clean_data(entity, {col: "5.3.2026"})
    twice = ents.clean_data(entity, once)
    assert once[col] == twice[col] == "2026-03-05"
