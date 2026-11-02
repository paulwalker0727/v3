# ruff: noqa: F811
"""Pasul A — avize complete (stare, executant, data_inceput, data_finalizare, observatii): MAIN <-> Cloud <-> Mobile + migrarea 028.
PostgreSQL LOCAL real (CIMITIR_TEST_PG_ADMIN_URL, altfel SKIP). NU atinge Supabase."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest
import requests

import cimitir_entities as ents
import cimitir_sync_v2 as sv
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_cleanup_race_pg import adm, pgdb  # noqa: F401
from test_etapa6b_field_storage import sup  # noqa: F401
from test_etapa6c_field_inbox_pg import fw  # noqa: F401
from test_sync_v2_main_e2e import ADD, H as HK, main_run, mobile_grave, pg, world  # noqa: F401
from test_sync_v2_pg import H, count, hx, op, pull, push, seed_main_data, sx  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PostgreSQL local de test indisponibil")
API = "/api/v1/sync-v2"
MIG = Path(__file__).resolve().parents[1] / "cimitir" / "pg_migrations"
ROLL = Path(__file__).resolve().parents[1] / "cimitir" / "pg_rollbacks"
NEW = ("stare", "executant", "data_inceput", "data_finalizare", "observatii")
FULL = {"cod_loc": "T-1", "nr_aviz": "AV-1", "data_aviz": "2026-03-03", "lucrare": "bordură", "stare": "in_lucru", "executant": "Popa SRL",
        "data_inceput": "2026-03-05", "data_finalizare": "2026-03-20", "observatii": "fără probleme"}
OLD = {k: v for k, v in FULL.items() if k not in NEW}


def avizrow(sx, uid):
    with adm(sx["pg"]) as c:
        r = c.execute("SELECT stare, executant, data_inceput, data_finalizare, observatii, lucrare, version FROM amenajari_avize WHERE uid=%s", (uid,)).fetchone()
    return r


def grant_operator(sx, key):
    ref = sx["T"]["A"]["phone_b"]
    r = sx["client"].put(f"{API}/devices/{ref}/capabilities", json={"role": "operator"}, headers=H(key))
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- specificație / compat (fără PG)
def test_spec_has_new_columns_and_old_client_data_never_blanks_them():
    assert set(NEW) <= set(ents.ENTITIES["amenajari_avize"]["cols"]) and ents.optional_cols("amenajari_avize") == NEW
    cleaned = ents.clean_data("amenajari_avize", OLD)               # client vechi: cheile noi lipsesc -> rămân absente (nu „None”)
    assert not (set(NEW) & set(cleaned)) and cleaned["lucrare"] == "bordură"
    assert ents.same_data("amenajari_avize", OLD, {**OLD, "stare": "in_lucru"}) and ents.changed_fields("amenajari_avize", OLD, FULL) == []
    assert ents.changed_fields("amenajari_avize", FULL, {**FULL, "stare": "finalizata"}) == ["stare"]
    assert ents.unsupported_values("amenajari_avize", {**OLD, "stare": "", "executant": None, "observatii": "  "}, set()) == []
    assert ents.unsupported_values("amenajari_avize", FULL, {"stare"}) == ["executant", "data_inceput", "data_finalizare", "observatii"]


# ---------------------------------------------------------------- MAIN -> Cloud -> Mobile și invers (HTTP real, PG real)
def test_main_creates_full_aviz_mobile_sees_all_fields(sx):
    key, loc, *_ = seed_main_data(sx)
    uid = hx()
    assert push(sx, key, [op("amenajari_avize", uid, FULL)])[0]["status"] == "applied"
    assert avizrow(sx, uid)[:5] == tuple(FULL[k] for k in NEW)
    phone = sx["T"]["A"]["phone"]
    ch = pull(sx, phone, "amenajari_avize")["changes"]
    assert len(ch) == 1 and all(ch[0]["data"][k] == FULL[k] for k in NEW)
    permit = sx["mob"].grave_detail(phone, loc)["permits"][0]
    assert all(permit[k] == FULL[k] for k in (*NEW, "nr_aviz", "lucrare"))
    st = sx["client"].get(f"{API}/status", headers=H(phone)).json()["result"]
    assert st["features"] == {"avize_extended": True}


def test_mobile_edits_aviz_main_receives_and_history(sx):
    key, loc, *_ = seed_main_data(sx)
    uid, phone = hx(), sx["T"]["A"]["phone"]
    push(sx, key, [op("amenajari_avize", uid, FULL)])
    edit = {**FULL, "stare": "finalizata", "executant": "Ionescu SRL", "data_finalizare": "2026-03-25", "observatii": "recepționat"}
    assert push(sx, phone, [op("amenajari_avize", uid, edit, base=1)])[0]["reason"] == "not_authorized"      # fără drept: permisiunile existente se respectă
    assert avizrow(sx, uid)[0] == "in_lucru"
    grant_operator(sx, key)
    r = push(sx, phone, [op("amenajari_avize", uid, edit, base=1)])[0]
    assert r["status"] == "applied" and r["version"] == 2, r
    got = pull(sx, key, "amenajari_avize")["changes"][0]["data"]
    assert all(got[k] == edit[k] for k in NEW)
    # un telefon nou (aviz creat pe telefon) ajunge complet în cloud și apoi în MAIN
    new = hx()
    assert push(sx, phone, [op("amenajari_avize", new, {**FULL, "nr_aviz": "AV-2", "stare": "planificata"})])[0]["status"] == "applied"
    assert {c["data"]["nr_aviz"]: c["data"]["stare"] for c in pull(sx, key, "amenajari_avize")["changes"]} == {"AV-1": "finalizata", "AV-2": "planificata"}


def test_field_level_permission_blocks_new_fields_when_not_granted(sx):
    key, loc, *_ = seed_main_data(sx)
    uid, phone, ref = hx(), sx["T"]["A"]["phone"], sx["T"]["A"]["phone_b"]
    push(sx, key, [op("amenajari_avize", uid, FULL)])
    m = {c: {"view": True, "add": True, "edit": True, "delete": False, "fields": None} for c in
         ("locuri", "harta", "concesionari", "inhumati", "contracte", "plati", "avize", "fisiere")}
    m["avize"]["fields"] = ["nr_aviz", "data_aviz", "lucrare", "observatii"]      # fără stare/executant/date
    assert sx["client"].put(f"{API}/devices/{ref}/matrix", json={"matrix": m}, headers=H(key)).status_code == 200
    bad = push(sx, phone, [op("amenajari_avize", uid, {**FULL, "stare": "finalizata"}, base=1)])[0]
    assert bad["status"] == "rejected" and bad["reason"] == "field_not_allowed" and "stare" in str(bad)
    ok = push(sx, phone, [op("amenajari_avize", uid, {**FULL, "observatii": "doar observații"}, base=1)])[0]
    assert ok["status"] == "applied" and avizrow(sx, uid)[0] == "in_lucru" and avizrow(sx, uid)[4] == "doar observații"
    assert sx["client"].put(f"{API}/devices/{ref}/matrix", json={"profile": "doar_consultare"}, headers=H(key)).status_code == 200
    assert push(sx, phone, [op("amenajari_avize", uid, {**FULL, "stare": "planificata"}, base=2)])[0]["status"] == "rejected"
    assert pull(sx, phone, "amenajari_avize")["changes"][0]["data"]["executant"] == "Popa SRL"           # citirea rămâne


def test_old_avize_without_new_fields_and_old_clients_do_not_blank_values(sx):
    key, *_ = seed_main_data(sx)
    old_uid, full_uid = hx(), hx()
    push(sx, key, [op("amenajari_avize", old_uid, OLD), op("amenajari_avize", full_uid, FULL)])
    assert avizrow(sx, old_uid)[:5] == (None,) * 5
    d = [c for c in pull(sx, key, "amenajari_avize")["changes"] if c["uid"] == old_uid][0]["data"]
    assert d["lucrare"] == "bordură" and all(d[k] is None for k in NEW)
    # MAIN vechi (nu știe câmpurile noi) editează doar lucrarea: valorile din cloud rămân
    r = push(sx, key, [op("amenajari_avize", full_uid, {**OLD, "lucrare": "bordură + soclu"}, base=1)])[0]
    assert r["status"] == "applied"
    row = avizrow(sx, full_uid)
    assert row[:5] == tuple(FULL[k] for k in NEW) and row[5] == "bordură + soclu"
    # aviz vechi completat ulterior
    assert push(sx, key, [op("amenajari_avize", old_uid, {**FULL, "nr_aviz": "AV-1", "stare": "planificata"}, base=1)])[0]["status"] == "applied"
    assert avizrow(sx, old_uid)[0] == "planificata"


def test_empty_values_none_and_explicit_clearing(sx):
    key, *_ = seed_main_data(sx)
    uid = hx()
    assert push(sx, key, [op("amenajari_avize", uid, {**OLD, "stare": "", "executant": None, "data_inceput": "", "observatii": ""})])[0]["status"] == "applied"
    assert avizrow(sx, uid)[0] in ("", None)
    push(sx, key, [op("amenajari_avize", uid, FULL, base=1)])
    assert avizrow(sx, uid)[:5] == tuple(FULL[k] for k in NEW)
    r = push(sx, key, [op("amenajari_avize", uid, {**FULL, "stare": "", "executant": "", "data_inceput": None, "data_finalizare": None, "observatii": ""}, base=2)])[0]
    assert r["status"] == "applied"
    assert all(v in ("", None) for v in avizrow(sx, uid)[:5])                      # golirea explicită este permisă (≠ cheie lipsă)
    assert count(sx, "amenajari_avize") == 1


def test_conflict_main_wins_and_mobile_blocked_on_new_fields(sx):
    key, *_ = seed_main_data(sx)
    uid, phone = hx(), sx["T"]["A"]["phone"]
    push(sx, key, [op("amenajari_avize", uid, FULL)])
    grant_operator(sx, key)
    assert push(sx, phone, [op("amenajari_avize", uid, {**FULL, "executant": "Telefon SRL"}, base=1)])[0]["version"] == 2
    r = push(sx, key, [op("amenajari_avize", uid, {**FULL, "stare": "finalizata"}, base=1)])[0]            # MAIN pe bază veche: MAIN câștigă
    assert r["status"] == "applied" and r["version"] == 3
    mw = [c for c in sx["client"].get(f"{API}/conflicts?all=1", headers=H(key)).json()["result"]["conflicts"] if c["resolution"] == "main_wins"][0]
    assert mw["server"]["data"]["executant"] == "Telefon SRL"                       # starea telefonului rămâne în jurnal, nu se pierde
    c = push(sx, phone, [op("amenajari_avize", uid, {**FULL, "observatii": "x"}, base=2)])[0]
    assert c["status"] == "conflict" and c["reason"] == "version_mismatch"
    assert avizrow(sx, uid)[4] == "fără probleme" and avizrow(sx, uid)[0] == "finalizata"


def test_idempotent_replay_no_duplicate_aviz(sx):
    key, *_ = seed_main_data(sx)
    uid = hx()
    o = op("amenajari_avize", uid, FULL, op_id="aviz-fixed-1")
    a, b = push(sx, key, [o])[0], push(sx, key, [o])[0]
    assert b.get("duplicate") and a["status"] == b["status"] == "applied" and count(sx, "amenajari_avize") == 1


# ---------------------------------------------------------------- server fără 028 (rollback): raportare explicită, fără pierdere
def test_server_without_028_reports_incompatibility_and_loses_nothing(sx):
    key, loc, *_ = seed_main_data(sx)
    uid, phone = hx(), sx["T"]["A"]["phone"]
    push(sx, key, [op("amenajari_avize", uid, FULL)])
    before = avizrow(sx, uid)
    roll = (ROLL / "028_avize_complete_ROLLBACK.sql").read_text(encoding="utf-8")
    with adm(sx["pg"]) as c:
        c.execute(roll)
    try:
        st = sx["client"].get(f"{API}/status", headers=H(phone)).json()["result"]
        assert st["features"] == {"avize_extended": False}
        r = push(sx, key, [op("amenajari_avize", uid, {**FULL, "stare": "finalizata"}, base=1)])[0]
        assert r["status"] == "rejected" and r["reason"] == "schema_incompatible" and r["fields"] == list(NEW) and "028" in r["detail"]
        legacy = push(sx, key, [op("amenajari_avize", uid, {**OLD, "lucrare": "alt text"}, base=1)])[0]      # fără câmpuri noi: funcționează
        assert legacy["status"] == "applied"
        wire = [c for c in pull(sx, phone, "amenajari_avize")["changes"] if c["uid"] == uid][0]["data"]
        assert not (set(NEW) & set(wire)) and wire["lucrare"] == "alt text"      # cheia lipsă = necunoscut (clientul nu golește local)
        assert "stare" not in sx["mob"].grave_detail(phone, loc)["permits"][0]
        new = push(sx, key, [op("amenajari_avize", hx(), {**OLD, "nr_aviz": "AV-9", "stare": ""})])[0]      # valori goale: nimic de pierdut
        assert new["status"] == "applied"
        assert avizrow(sx, uid)[:5] == before[:5]                                    # valorile vechi sunt intacte în bază
        assert sx["client"].get(f"{API}/history?entity=amenajari_avize&uid={uid}", headers=H(key)).status_code in (200, 400, 404)
    finally:
        with adm(sx["pg"]) as c:
            c.execute((MIG / "028_avize_complete.sql").read_text(encoding="utf-8"))
    r = push(sx, key, [op("amenajari_avize", uid, {**FULL, "stare": "finalizata"}, base=2)])[0]       # după reaplicare aceeași operațiune trece
    assert r["status"] == "applied" and avizrow(sx, uid)[0] == "finalizata"


# ---------------------------------------------------------------- migrarea 028 pe PG nou: lanț, reaplicare, rollback nedistructiv
def _fresh_db(admin_url, name):
    with psycopg.connect(admin_url, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        c.execute(f'CREATE DATABASE "{name}"')


def _apply_files(url, files):
    with psycopg.connect(url, autocommit=True) as c:
        for f in files:
            c.execute(f.read_text(encoding="utf-8"))


def test_migration_028_fresh_chain_reapply_and_nondestructive_rollback():
    admin = os.environ["CIMITIR_TEST_PG_ADMIN_URL"]
    name = "mig028_" + uuid.uuid4().hex[:6]
    _fresh_db(admin, name)
    url = admin.rsplit("/", 1)[0] + "/" + name
    try:
        files = sorted(MIG.glob("*.sql"))
        assert [int(f.name[:3]) for f in files] == list(range(1, 29))
        # 001–027
        _apply_files(url, [f for f in files if int(f.name[:3]) <= 27])
        with psycopg.connect(url, autocommit=True) as c:
            org, cem = str(uuid.uuid4()), str(uuid.uuid4())
            c.execute("INSERT INTO organizations (uid, organization_type, official_name, display_name) VALUES (%s,'OTHER','M028','M028')", (org,))
            c.execute("INSERT INTO cemeteries (uid, organization_uid, name, internal_code, status, data_epoch) VALUES (%s,%s,'M028',%s,'DRAFT',1)", (cem, org, cem))
            c.execute("INSERT INTO locuri_de_veci (uid, organization_uid, cemetery_uid, cod_loc, points) VALUES (%s,%s,%s,'X-1','[]'::jsonb)", (hx(), org, cem))
            c.execute("INSERT INTO amenajari_avize (uid, organization_uid, cemetery_uid, cod_loc, nr_aviz, lucrare) VALUES (%s,%s,%s,'X-1','V-1','veche')", (hx(), org, cem))
            ver0 = c.execute("SELECT version, change_seq FROM amenajari_avize").fetchone()
        _apply_files(url, [MIG / "028_avize_complete.sql"])
        _apply_files(url, [MIG / "028_avize_complete.sql"])                               # reaplicare: idempotentă
        with psycopg.connect(url, autocommit=True) as c:
            cols = {r[0]: r[1] for r in c.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='amenajari_avize'")}
            assert all(cols[k] == "text" for k in NEW)
            assert c.execute("SELECT version, change_seq, stare FROM amenajari_avize").fetchone() == (*ver0, None)        # rândurile vechi neatinse, fără backfill
            priv = lambda p: [c.execute("SELECT has_column_privilege('cimitir_cp_app','amenajari_avize',%s,%s)", (k, p)).fetchone()[0] for k in NEW]  # noqa: E731
            assert all(priv("INSERT")) and all(priv("UPDATE"))
            c.execute("UPDATE amenajari_avize SET stare='in_lucru', observatii='păstrat'")
        _apply_files(url, [ROLL / "028_avize_complete_ROLLBACK.sql"])
        _apply_files(url, [ROLL / "028_avize_complete_ROLLBACK.sql"])                      # rollback repetat: fără eroare
        with psycopg.connect(url, autocommit=True) as c:
            assert c.execute("SELECT stare, observatii FROM amenajari_avize").fetchone() == ("in_lucru", "păstrat")        # nicio pierdere de date
            assert not any(priv("UPDATE")) and not any(priv("INSERT"))
            assert c.execute("SELECT count(*) FROM information_schema.columns WHERE table_name='amenajari_avize' AND column_name = ANY(%s)", (list(NEW),)).fetchone()[0] == 5
        _apply_files(url, [MIG / "028_avize_complete.sql"])                               # reaplicare după rollback
        with psycopg.connect(url, autocommit=True) as c:
            assert all(priv("UPDATE")) and c.execute("SELECT stare FROM amenajari_avize").fetchone()[0] == "in_lucru"
    finally:
        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


# ---------------------------------------------------------------- MAIN real (SQLite, proces separat) <-> Cloud <-> Mobile
def test_main_sqlite_create_edit_sync_to_mobile_and_back(world):
    w = world
    out = main_run(w, ADD + """
q("INSERT INTO amenajari_avize (cod_loc, nr_aviz, data_aviz, lucrare, stare, executant, data_inceput, data_finalizare, observatii) VALUES (?,?,?,?,?,?,?,?,?)",
  ("E2E-1", "AV-7", "2026-03-03", "monument", "in_lucru", "Popa SRL", "2026-03-05", "2026-03-20", "obs MAIN"))
q("INSERT INTO amenajari_avize (cod_loc, nr_aviz, data_aviz, lucrare) VALUES (?,?,?,?)", ("E2E-1", "AV-OLD", "2026-01-01", "vechi fără câmpuri noi"))
rep = cloud_sync.sync_all()
out["rep2"] = {k: rep.get(k) for k in ("ok", "errors", "pending", "conflicts")}
out["uid"] = q("SELECT uid FROM amenajari_avize WHERE nr_aviz='AV-7'")[0]["uid"]
""")
    assert out["rep2"]["ok"] and out["rep2"]["pending"] == 0 and out["rep2"]["errors"] == 0, out
    d = mobile_grave(w, out["loc_uid"])
    by = {p["nr_aviz"]: p for p in d["permits"]}
    assert (by["AV-7"]["stare"], by["AV-7"]["executant"], by["AV-7"]["data_inceput"], by["AV-7"]["data_finalizare"], by["AV-7"]["observatii"]) == \
           ("in_lucru", "Popa SRL", "2026-03-05", "2026-03-20", "obs MAIN")
    assert by["AV-OLD"]["stare"] in (None, "")
    # Mobile modifică -> MAIN primește
    key = w["T"]["A"]["main"]
    assert requests.put(f"{w['url']}{API}/devices/{w['T']['A']['phone_b']}/capabilities", json={"role": "operator"}, headers=HK(key), timeout=10).status_code == 200
    r = requests.post(f"{w['url']}{API}/push", headers=HK(w["T"]["A"]["phone"]), timeout=10, json={"ops": [
        {"op_id": "m-" + hx(), "entity": "amenajari_avize", "uid": out["uid"], "action": "upsert", "base_version": 1,
         "data": {"cod_loc": "E2E-1", "nr_aviz": "AV-7", "data_aviz": "2026-03-03", "lucrare": "monument", "stare": "finalizata", "executant": "Popa SRL",
                  "data_inceput": "2026-03-05", "data_finalizare": "2026-03-28", "observatii": "predat de pe telefon"}}]})
    assert r.json()["result"]["results"][0]["status"] == "applied", r.text
    back = main_run(w, """
rep = cloud_sync.sync_all()
out["rep"] = {k: rep.get(k) for k in ("ok", "errors", "pending", "conflicts")}
out["row"] = q("SELECT stare, executant, data_inceput, data_finalizare, observatii, lucrare FROM amenajari_avize WHERE nr_aviz='AV-7'")[0]
out["old"] = q("SELECT stare, observatii FROM amenajari_avize WHERE nr_aviz='AV-OLD'")[0]
out["n"] = q("SELECT count(*) AS n FROM amenajari_avize")[0]["n"]
""")
    assert back["rep"]["ok"] and back["rep"]["errors"] == 0 and back["n"] == 2, back
    assert back["row"] == {"stare": "finalizata", "executant": "Popa SRL", "data_inceput": "2026-03-05",
                           # F-01: ziua schimbată în Cloud/Mobile (ISO) se scrie în MAIN în formatul MAIN (zz.ll.aaaa); ziua neschimbată își păstrează textul local
                           "data_finalizare": "28.03.2026", "observatii": "predat de pe telefon", "lucrare": "monument"}
    assert back["old"] == {"stare": None, "observatii": None} or back["old"]["stare"] in (None, "")
    assert pg(w, "SELECT count(*) FROM amenajari_avize")[0][0] == 2


def test_main_offline_queue_then_recovery_and_backfill(world):
    w = world
    out = main_run(w, ADD + """
q("INSERT INTO amenajari_avize (cod_loc, nr_aviz, data_aviz, lucrare) VALUES (?,?,?,?)", ("E2E-1", "AV-3", "2026-03-03", "soclu"))
rep = cloud_sync.sync_all()
good = {"url": __import__("cloud_sync").load_config() if hasattr(cloud_sync, "load_config") else None}
""")
    key, url = w["T"]["A"]["main"], w["url"]
    off = main_run(w, f"""
cloud_sync.save_config("http://127.0.0.1:9", {key!r}, False, False)
q("UPDATE amenajari_avize SET stare='planificata', executant='Offline SRL', observatii='creat offline' WHERE nr_aviz='AV-3'")
rep = cloud_sync.sync_all()
out["off"] = {{k: rep.get(k) for k in ("ok", "pending")}}
cloud_sync.save_config({url!r}, {key!r}, False, False)
rep = cloud_sync.sync_all()
out["on"] = {{k: rep.get(k) for k in ("ok", "pending", "errors")}}
""")
    assert off["off"]["ok"] is False and off["off"]["pending"] >= 1, off                  # offline: operațiunea rămâne în coadă
    assert off["on"]["ok"] and off["on"]["pending"] == 0 and off["on"]["errors"] == 0, off
    row = pg(w, "SELECT stare, executant, observatii FROM amenajari_avize WHERE nr_aviz='AV-3'")[0]
    assert tuple(row) == ("planificata", "Offline SRL", "creat offline") and pg(w, "SELECT count(*) FROM amenajari_avize")[0][0] == 1
    # backfill: aviz deja sincronizat, cu valori în câmpurile noi puse înaintea actualizării (fără operațiune în coadă) -> se trimit o singură dată
    bf = main_run(w, f"""
cloud_sync.save_config({url!r}, {key!r}, False, False)
q("DROP TRIGGER IF EXISTS cs_amenajari_avize_au")
q("UPDATE amenajari_avize SET stare='finalizata', observatii='valoare dinainte de actualizare' WHERE nr_aviz='AV-3'")
q("DELETE FROM cloud_sync_outbox")
q("DELETE FROM settings WHERE key='entity_sync_avize_ext_backfill'")
entity_sync._READY.clear()
entity_sync.ensure_tracking()
out["queued"] = q("SELECT count(*) AS n FROM cloud_sync_outbox WHERE entity='amenajari_avize' AND state='pending'")[0]["n"]
rep = cloud_sync.sync_all()
out["rep"] = {{k: rep.get(k) for k in ("ok", "pending", "errors")}}
entity_sync._READY.clear(); entity_sync.ensure_tracking()
out["queued_again"] = q("SELECT count(*) AS n FROM cloud_sync_outbox WHERE entity='amenajari_avize' AND state='pending'")[0]["n"]
""")
    assert bf["queued"] == 1 and bf["rep"]["ok"] and bf["queued_again"] == 0, bf
    assert tuple(pg(w, "SELECT stare, observatii FROM amenajari_avize WHERE nr_aviz='AV-3'")[0]) == ("finalizata", "valoare dinainte de actualizare")


def test_main_reports_incompatible_server_without_028_and_keeps_values(world):
    w = world
    key, url = w["T"]["A"]["main"], w["url"]
    main_run(w, ADD)
    with adm(w["pg"]) as c:
        c.execute((ROLL / "028_avize_complete_ROLLBACK.sql").read_text(encoding="utf-8"))
    try:
        out = main_run(w, f"""
q("INSERT INTO amenajari_avize (cod_loc, nr_aviz, data_aviz, lucrare, stare, executant) VALUES (?,?,?,?,?,?)", ("E2E-1", "AV-INC", "2026-03-03", "cruce", "in_lucru", "Cruce SRL"))
rep = cloud_sync.sync_all()
out["rep"] = {{k: rep.get(k) for k in ("ok", "pending", "errors")}}
out["msg"] = " | ".join(str(x) for x in (rep.get("lines") or rep.get("message") or rep.get("messages") or []))
out["local"] = q("SELECT stare, executant FROM amenajari_avize WHERE nr_aviz='AV-INC'")[0]
out["queue"] = q("SELECT state, last_error FROM cloud_sync_outbox WHERE entity='amenajari_avize'")
""")
        assert out["local"] == {"stare": "in_lucru", "executant": "Cruce SRL"}                    # valoarea locală nu se atinge
        assert out["queue"] and all(q["state"] == "pending" and "028" in (q["last_error"] or "") or "schema_incompatible" in (q["last_error"] or "") for q in out["queue"]), out
        assert pg(w, "SELECT count(*) FROM amenajari_avize")[0][0] == 0
    finally:
        with adm(w["pg"]) as c:
            c.execute((MIG / "028_avize_complete.sql").read_text(encoding="utf-8"))
    ok = main_run(w, f"""
rep = cloud_sync.sync_all()
out["rep"] = {{k: rep.get(k) for k in ("ok", "pending", "errors")}}
""")
    assert ok["rep"]["ok"] and ok["rep"]["pending"] == 0, ok
    assert tuple(pg(w, "SELECT stare, executant FROM amenajari_avize WHERE nr_aviz='AV-INC'")[0]) == ("in_lucru", "Cruce SRL")       # trimis automat după actualizarea serverului


def test_sv_available_optional_is_a_pure_function_of_schema(sx):
    c = sx["svc2"].cp._c()
    try:
        assert sv.available_optional(c, "amenajari_avize") == frozenset(NEW)
        assert sv.available_optional(c, "inhumati") == frozenset()
    finally:
        c.close()
