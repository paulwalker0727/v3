# ruff: noqa: F811
"""Independent verification (testing agent): CNP Mobile -> PG -> MAIN, ignored_fields, journal replay behaviour for MAIN."""
from __future__ import annotations

import os

import pytest

from test_mobile_permissions_pg import API, cell, give  # noqa: F401
from test_sync_v2_pg import H, adm, hx, op, pgdb, pull, push, seed_main_data, sx  # noqa: F401
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_field_storage import sup  # noqa: F401
from test_etapa6c_field_inbox_pg import fw  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PG test indisponibil")

CNP = "0123456789012"
FULL = {"locuri": cell(True, True, True), "concesionari": cell(True, True, True), "inhumati": cell(True, True, True), "contracte": cell(True, True, True)}


def pgv(sx, uid, col="cnp", table="concesionari"):
    with adm(sx["pg"]) as c:
        r = c.execute(f"SELECT {col} FROM {table} WHERE uid=%s", (uid,)).fetchone()
    return r[0] if r else "<missing>"


# Phone without view_pii: cnp ignored but reported; PG stays NULL
def test_no_pii_phone_cnp_only_ignored_reported(sx):
    seed_main_data(sx)
    phone, ref = sx["T"]["A"]["phone"], sx["T"]["A"]["phone_b"]
    give(sx, ref, matrix=FULL, view_pii=False)
    uid = hx()
    r = push(sx, phone, [op("concesionari", uid, {"nume": "TEST_NoPii", "cnp": CNP})])[0]
    print("NO_PII_RESULT", r)
    assert r["status"] == "applied"
    assert r["ignored_fields"] == ["cnp"]
    assert pgv(sx, uid) is None
    assert pgv(sx, uid, "nume") == "TEST_NoPii"


# Phone without view_pii: numeric (non-string) cnp must not crash (500)
def test_no_pii_phone_numeric_cnp_no_crash(sx):
    seed_main_data(sx)
    phone, ref = sx["T"]["A"]["phone"], sx["T"]["A"]["phone_b"]
    give(sx, ref, matrix=FULL, view_pii=False)
    r = sx["client"].post("/api/v1/sync-v2/push", json={"ops": [op("concesionari", hx(), {"nume": "TEST_Num", "cnp": 1234567890123})]}, headers=H(phone))
    print("NUMERIC_RESULT", r.status_code, r.text[:300])
    assert r.status_code == 200


# Phone without pii sending no cnp: no ignored_fields key
def test_no_pii_phone_without_cnp_has_no_ignored_fields(sx):
    seed_main_data(sx)
    phone, ref = sx["T"]["A"]["phone"], sx["T"]["A"]["phone_b"]
    give(sx, ref, matrix=FULL, view_pii=False)
    r = push(sx, phone, [op("concesionari", hx(), {"nume": "TEST_Plain", "cnp": "  "})])[0]
    assert r["status"] == "applied" and "ignored_fields" not in r, r


# Phone with view_pii: leading-zero cnp persisted exactly, MAIN pull returns same string
def test_pii_phone_leading_zero_exact_to_main(sx):
    main, *_ = seed_main_data(sx)
    phone, ref = sx["T"]["A"]["phone"], sx["T"]["A"]["phone_b"]
    give(sx, ref, matrix=FULL, view_pii=True)
    uid = hx()
    r = push(sx, phone, [op("concesionari", uid, {"nume": "TEST_Pii", "cnp": CNP})])[0]
    assert r["status"] == "applied" and "ignored_fields" not in r, r
    v = pgv(sx, uid)
    assert v == CNP and isinstance(v, str)
    got = [c for c in pull(sx, main, "concesionari")["changes"] if c["uid"] == uid][0]
    assert got["data"]["cnp"] == CNP


# Phone WITH view_pii: stale edit -> conflict; replay of same op_id returns journal copy (duplicate) without CNP in clear.
# MAIN never receives version_mismatch conflicts (main_wins), so journal redaction cannot affect MAIN's resolve_conflict.
def test_pii_phone_conflict_replay_is_duplicate_and_redacted(sx):
    main, _loc, ben, _inh = seed_main_data(sx)
    phone, ref = sx["T"]["A"]["phone"], sx["T"]["A"]["phone_b"]
    give(sx, ref, matrix=FULL, view_pii=True)
    v = pull(sx, main, "concesionari")["changes"][0]["version"]
    assert push(sx, main, [op("concesionari", ben, {"nume": "Ionescu", "prenume": "Maria", "cnp": CNP}, base=v)])[0]["status"] == "applied"
    stale = op("concesionari", ben, {"nume": "Ionescu", "prenume": "Maria", "telefon": "0711", "cnp": CNP}, base=v)
    first = push(sx, phone, [stale])[0]
    assert first["status"] == "conflict"
    again = push(sx, phone, [stale])[0]
    print("FIRST_SERVER", first.get("server")); print("REPLAY_SERVER", again.get("server"))
    assert again.get("duplicate") is True and again["status"] == "conflict"
    assert CNP not in str(again.get("server"))
    assert pgv(sx, ben) == CNP
