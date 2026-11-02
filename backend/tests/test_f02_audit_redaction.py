# ruff: noqa: F811
"""F-02 — CNP și datele CI nu mai ajung în clar în NOILE înregistrări de audit (date sintetice, baze izolate).
Nu testează și nu modifică jurnalul istoric; datele operaționale rămân în formatul lor."""
from __future__ import annotations

import json
import os

import pytest
from test_etapa1_audit import _load_server

import cimitir_redact as rd

CNP = "1800101123456"
CNP2 = "2951231654321"
CI = "XY123456"
CI2 = "AB654321"


# ------------------------------------------------------------------ modulul de redactare
def test_sensitive_key_names():
    for k in ("cnp", "owner_cnp", "bi_ci", "owner_id_card_serie_number", "owner_id_card", "carte_identitate", "serie_ci", "nr_ci", "ci", "CNP", "serie_numar"):
        assert rd.is_sensitive_key(k), k
    for k in ("nume", "observatii", "cod_loc", "cimitir", "numar_contract", "telefon", "email", "suma", "special", "decizie", "data_deces"):
        assert not rd.is_sensitive_key(k), k


def test_value_redaction_by_key_keeps_empty_and_none():
    assert rd.redact_value("cnp", CNP) == rd.REDACTED
    assert rd.redact_value("owner_cnp", CNP) == rd.REDACTED
    assert rd.redact_value("bi_ci", CI) == rd.REDACTED
    assert rd.redact_value("owner_id_card_serie_number", CI) == rd.REDACTED
    assert rd.redact_value("cnp", "") == "" and rd.redact_value("cnp", None) is None
    assert rd.redact_value("nume", "Popa") == "Popa"


def test_text_redaction_patterns_and_error_messages():
    assert CNP not in rd.redact_text(f"CNP {CNP} duplicat") and rd.REDACTED_CNP in rd.redact_text(f"x{CNP}")[1:]
    assert "123456" not in rd.redact_text("act de identitate: XY123456") and "CI REDACTAT" in rd.redact_text("seria XY123456")
    assert CNP not in rd.redact_text(f'DETAIL:  Key (cnp)=({CNP}) already exists.')
    assert CNP not in rd.redact_text(f"owner_cnp={CNP}, nume=Popa") and "nume=Popa" in rd.redact_text(f"owner_cnp={CNP}, nume=Popa")
    assert CI not in rd.redact_text(f"bi_ci: {CI}") and CI not in rd.redact_text(f'{{"owner_id_card_serie_number": "{CI}"}}')
    assert rd.redact_text("loc=A-12 nr=CT-123456 suma=250") == "loc=A-12 nr=CT-123456 suma=250"      # fără fals-pozitive pe numere obișnuite
    assert rd.redact_text("telefon 0712345678") == "telefon 0712345678"
    assert rd.redact_text(None) == ""


def test_json_structures_and_nested_json_text():
    obj = {"entity": "concesionari", "old": {"cnp": CNP, "nume": "A"}, "new": [{"owner_cnp": CNP2}, {"x": f"cnp {CNP}"}],
           "extra_data": json.dumps({"bi_ci": CI, "ok": 1})}
    out = json.dumps(rd.redact_obj(obj), ensure_ascii=False)
    for secret in (CNP, CNP2, CI):
        assert secret not in out, secret
    red = rd.redact_obj(obj)
    assert red["old"]["nume"] == "A" and red["entity"] == "concesionari" and set(red["old"]) == {"cnp", "nume"}
    assert json.loads(red["extra_data"])["ok"] == 1
    assert obj["old"]["cnp"] == CNP            # intrarea nu este modificată


def test_redact_detail_for_json_text_plain_text_and_non_str():
    assert CNP not in rd.redact_detail(json.dumps({"fields": ["cnp"], "cnp": CNP}))
    assert json.loads(rd.redact_detail(json.dumps({"kind": "x", "n": 3}))) == {"kind": "x", "n": 3}
    assert CNP not in rd.redact_detail(f"eroare la {CNP}")
    assert rd.redact_detail(None) == "" and rd.redact_detail(12) == "12"
    assert CNP not in rd.redact_detail({"cnp": CNP})
    assert rd.redact_detail("{nu e json " + CNP) .count(CNP) == 0


# ------------------------------------------------------------------ scrierea în audit_log (SQLite izolat)
@pytest.fixture()
def db(tmp_path):
    _load_server(tmp_path / "data")
    import database
    database.init_db()
    return database


def _audit_rows(db):
    conn = db.get_connection()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()]
    finally:
        conn.close()


def _whole_journal(db) -> str:
    return json.dumps(_audit_rows(db), ensure_ascii=False)


def test_entity_audit_record_masks_old_and_new_but_keeps_who_what_when_and_changed_field(db):
    import audit_entities as ae
    conn = db.get_connection()
    try:
        row = {"id": 1, "uid": "u1", "version": 1, "nume": "Popa", "cnp": CNP, "bi_ci": CI, "adresa": "Str. X"}
        assert ae.record(conn, "sync", "MAIN-1", "concesionari", "u1", None, row, 1)                                           # create
        assert ae.record(conn, "sync", "MAIN-1", "concesionari", "u1", row, {**row, "cnp": CNP2, "version": 2}, 2)           # DOAR CNP s-a schimbat
        assert ae.record(conn, "staff", "op", "concesionari", "u1", {**row, "cnp": CNP2}, {**row, "cnp": CNP2, "bi_ci": CI2, "nume": "Popa M"}, 3)
        assert not ae.record(conn, "sync", "MAIN-1", "concesionari", "u1", row, dict(row), 1)                                    # nicio schimbare: nimic scris
        assert ae.record(conn, "sync", "MAIN-1", "concesionari", "u1", row, None, 4)                                             # delete
        conn.commit() if hasattr(conn, "commit") else None
    finally:
        conn.close()
    rows = _audit_rows(db)
    blob = _whole_journal(db)
    for secret in (CNP, CNP2, CI, CI2):
        assert secret not in blob, secret
    assert [r["action"] for r in rows] == ["concesionari.create", "concesionari.update", "concesionari.update", "concesionari.delete"]
    assert all(r["actor"] and r["ts"] and r["target"] == "concesionari:u1" for r in rows)
    d = [json.loads(r["detail"]) for r in rows]
    assert d[0]["old"] is None and d[0]["new"]["cnp"] == rd.REDACTED and d[0]["new"]["nume"] == "Popa" and d[0]["new"]["adresa"] == "Str. X"
    assert d[1]["new"] == {"cnp": rd.REDACTED} and d[1]["old"] == {"cnp": rd.REDACTED}, "modificarea doar a CNP rămâne vizibilă ca „câmp modificat”"
    assert set(d[2]["new"]) == {"bi_ci", "nume"} and d[2]["new"]["nume"] == "Popa M" and d[2]["new"]["bi_ci"] == rd.REDACTED
    assert d[3]["new"] is None and d[3]["old"]["cnp"] == rd.REDACTED


def test_contract_owner_fields_and_extra_data_are_masked(db):
    import audit_entities as ae
    conn = db.get_connection()
    try:
        c = {"numar_contract": "C-1", "owner_cnp": CNP, "owner_id_card_serie_number": CI, "owner_fullname": "Popa Maria",
             "extra_data": json.dumps({"cnp_sot": CNP2, "nota": "ok"}), "observatii": f"titular CNP {CNP2}"}
        ae.record(conn, "sync", "MAIN-1", "contracte", "c1", None, c, 1)
    finally:
        conn.close()
    blob = _whole_journal(db)
    for secret in (CNP, CNP2, CI):
        assert secret not in blob
    d = json.loads(_audit_rows(db)[0]["detail"])["new"]
    assert d["numar_contract"] == "C-1" and d["owner_fullname"] == "Popa Maria" and d["owner_cnp"] == rd.REDACTED


def test_free_text_audit_calls_are_redacted_error_messages_included(db):
    import cimitir_requests as rq
    rq.audit("staff", "op", "scan.confirm", "concesionari:7", json.dumps({"kind": "ci", "cnp": CNP, "fields": ["cnp", "bi_ci"]}))
    rq.audit("citizen", "a@b.ro", "password_reset_email_failed", "", f"SMTP: destinatar invalid; cerere pentru CNP {CNP2}")
    rq.audit("sync", "cloud", "request_imported", f"REQ-{CNP}", "tip=concesionare bi_ci=" + CI)
    rq.audit("staff", "op", "db_error", "", f'DETAIL:  Key (cnp)=({CNP}) already exists. owner_id_card_serie_number={CI2}')
    rq.audit("staff", "op", "web_create_contract", "contract:5", "loc=A-1 nr=C-9 concesionar=12")                       # nemodificat
    blob = _whole_journal(db)
    for secret in (CNP, CNP2, CI, CI2):
        assert secret not in blob, secret
    rows = _audit_rows(db)
    assert rows[-1]["detail"] == "loc=A-1 nr=C-9 concesionar=12" and rows[-1]["target"] == "contract:5" and rows[-1]["actor"] == "op"
    assert json.loads(rows[0]["detail"])["fields"] == ["cnp", "bi_ci"], "numele câmpurilor modificate se păstrează"
    assert all(r["ts"] for r in rows)


def test_operational_data_is_not_modified_by_audit(db):
    import audit_entities as ae
    cid = db.save_concesionar({"nume": "Popa", "prenume": "Maria", "cnp": CNP, "bi_ci": CI})
    conn = db.get_connection()
    try:
        before = dict(conn.execute("SELECT * FROM concesionari WHERE id=?", (cid,)).fetchone())
        ae.record(conn, "sync", "MAIN-1", "concesionari", "u1", None, before, 1)
        after = dict(conn.execute("SELECT * FROM concesionari WHERE id=?", (cid,)).fetchone())
    finally:
        conn.close()
    assert after == before and after["cnp"] == CNP and after["bi_ci"] == CI


def test_existing_journal_rows_are_never_rewritten(db):
    """Jurnalul istoric nu este atins: rândurile deja scrise (chiar cu CNP) rămân identice după scrieri noi (remedierea se face doar prin procedura aprobată)."""
    conn = db.get_connection()
    try:
        conn.execute("INSERT INTO audit_log (ts, actor_type, actor, action, target, detail) VALUES ('2025-01-01T00:00:00','sync','old','concesionari.create','concesionari:x',?)",
                     (json.dumps({"new": {"cnp": CNP}}),))
    finally:
        conn.close()
    import cimitir_requests as rq
    rq.audit("staff", "op", "x", "", "nou")
    first = _audit_rows(db)[0]
    assert CNP in first["detail"] and first["actor"] == "old"


# ------------------------------------------------------------------ Cloud PostgreSQL (sync v2 audit)
pg_only = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PostgreSQL local de test indisponibil")


try:
    from test_etapa4_pas1 import World, env  # noqa: F401
    from test_etapa4_pas1_closure import renv  # noqa: F401
    from test_etapa6b_cleanup_race_pg import adm, pgdb  # noqa: F401
    from test_etapa6b_field_storage import sup  # noqa: F401
    from test_etapa6c_field_inbox_pg import fw  # noqa: F401
    from test_sync_v2_pg import H, hx, op, push, seed_main_data, sx  # noqa: F401
except Exception:  # pragma: no cover
    sx = None


@pg_only
def test_sync_v2_delete_audit_snapshot_is_redacted_but_operational_row_was_complete(sx):
    key, loc, ben, _inh = seed_main_data(sx)
    cid = hx()
    assert push(sx, key, [op("concesionari", cid, {"nume": "Test", "prenume": "S", "cnp": CNP, "bi_ci": CI})])[0]["status"] == "applied"
    with adm(sx["pg"]) as c:
        row = c.execute("SELECT cnp, bi_ci FROM concesionari WHERE uid=%s", (cid,)).fetchone()
    assert tuple(row) == (CNP, CI), "datele operaționale rămân în clar și neschimbate"
    phone = sx["T"]["A"]["phone"]       # ștergerea de pe telefon (cu permisiunea „Șterge") este singura care se auditează în sync_v2_audit, cu snapshot-ul „before"
    m = {c: {"view": True, "add": True, "edit": True, "delete": True, "fields": None} for c in
         ("locuri", "harta", "concesionari", "inhumati", "contracte", "plati", "avize", "fisiere")}
    r = sx["client"].put(f"/api/v1/sync-v2/devices/{sx['T']['A']['phone_b']}/matrix", json={"matrix": m}, headers=H(key))
    assert r.status_code == 200, r.text
    res = push(sx, phone, [op("concesionari", cid, {}, base=1, action="delete")])[0]
    assert res["status"] == "applied", res
    with adm(sx["pg"]) as c:
        details = [json.dumps(r[0], ensure_ascii=False) for r in c.execute("SELECT detail FROM sync_v2_audit").fetchall()]
        kinds = [r[0] for r in c.execute("SELECT kind FROM sync_v2_audit").fetchall()]
    assert "delete" in kinds
    blob = " ".join(details)
    assert CNP not in blob and CI not in blob
    assert any('"before"' in d and "Test" in d for d in details), "snapshot-ul util (nume) se păstrează, doar CNP/CI sunt redactate"
