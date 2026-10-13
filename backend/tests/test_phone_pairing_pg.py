# ruff: noqa: F811
"""Phone Pairing v1 (migration 019) pe PostgreSQL LOCAL REAL (rol cimitir_cp_app fără BYPASSRLS; fire/conexiuni reale), Storage = Supabase simulat.
Rulează doar cu CIMITIR_TEST_PG_ADMIN_URL=postgresql://<superuser>@127.0.0.1:<port>/postgres (altfel SKIP). NU atinge Supabase."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import cimitir_cloud_control as cc
import cimitir_field_inbox as fi
import cimitir_field_inbox_http as fih
import cimitir_phone_pairing as pp
import cimitir_phone_pairing_http as pph
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_cleanup_race_pg import adm, app as appconn, pgdb  # noqa: F401
from test_etapa6b_field_storage import sup  # noqa: F401
from test_etapa6c_field_inbox_pg import PAY, fw, seed_binding  # noqa: F401

ADMIN = os.environ.get("CIMITIR_TEST_PG_ADMIN_URL", "")
pytestmark = pytest.mark.skipif(not ADMIN, reason="PostgreSQL local de test indisponibil")
MIG = Path(__file__).resolve().parents[1] / "cimitir" / "pg_migrations" / "019_phone_pairing.sql"


@pytest.fixture()
def pw(fw):
    with adm(fw["pg"]) as c:
        c.execute("TRUNCATE cp_pairing_attempts")
    svc = pp.PhonePairingService(fw["svc"])
    app = FastAPI()
    app.include_router(fih.create_router(fw["svc"]))
    app.include_router(pph.create_router(svc))
    return {**fw, "pp": svc, "client": TestClient(app)}


def issue(pw, tag="A", **kw):
    return pw["pp"].issue_code(pw["T"][tag]["main"], **kw)["code"]


def pair(pw, tag="A", name="Telefon test", source="10.0.0.1", **kw):
    return pw["pp"].redeem(issue(pw, tag), name, source=source, **kw)


def sqlrow(pw, sql, args=()):
    with adm(pw["pg"]) as c:
        return c.execute(sql, args).fetchall()


def hsh(code):
    return hashlib.sha256(pp.normalize_code(code).encode()).hexdigest()


def H(key):
    return {"Authorization": f"Binding {key}"}


def make_item(pw, key, tag="A", key_="k-"):
    t = pw["T"][tag]
    return pw["svc"].create_item(key, t["cem"], key_ + uuid.uuid4().hex, "WORK", dict(PAY["WORK"]), grave_uid="G-1")["item"]


# ===================================================================== 1. pairing valid
def test_valid_pairing_end_to_end_and_field_inbox_compat(pw):
    t = pw["T"]["A"]
    issued = pw["pp"].issue_code(t["main"])
    assert re.fullmatch(r"([A-Z2-9]{5}-){3}[A-Z2-9]{5}", issued["code"]) and issued["ttl"] == pp.DEFAULT_TTL and issued["cemetery_uid"] == t["cem"]
    res = pw["pp"].redeem(issued["code"], "Telefon Ion", source="10.1.1.1")
    assert res["device_type"] == "MOBILE" and res["cemetery_uid"] == t["cem"] and res["organization_uid"] == t["org"]
    assert res["binding_key"].startswith(f"cimitir.{res['binding_uid']}.")
    a = pw["svc"].authenticate(res["binding_key"])
    assert a["device_type"] == "MOBILE" and a["main"] is False and a["cemetery_uid"] == t["cem"]
    # Field Inbox v1 neschimbat: telefonul creează, MAIN vede/aprobă
    it = make_item(pw, res["binding_key"])
    assert pw["svc"].get_item(t["main"], t["cem"], it["uid"])["created_by"] == res["binding_uid"]
    assert pw["svc"].accept(t["main"], t["cem"], it["uid"])["item"]["status"] == "ACCEPTED"
    assert any(p["binding_uid"] == res["binding_uid"] and p["active"] and p["device_name"] == "Telefon Ion" for p in pw["pp"].list_phones(t["main"]))
    with adm(pw["pg"]) as c:
        d = c.execute("SELECT device_type, active FROM devices WHERE device_uid=%s", (res["device_uid"],)).fetchone()
        assert d == ("MOBILE", True)
        assert c.execute("SELECT count(*) FROM cp_binding_issuers WHERE binding_uid=%s", (res["binding_uid"],)).fetchone()[0] == 0


def test_code_and_key_are_stored_only_as_hash_and_never_leak(pw, caplog):
    caplog.set_level(logging.DEBUG)
    code = issue(pw)
    res = pw["pp"].redeem(code, "x", source="10.2.2.2")
    secret = res["binding_key"].split(".", 2)[2]
    row = sqlrow(pw, "SELECT code_hash, used_at, used_by_binding_uid FROM device_pairing_codes WHERE device_type='MOBILE'")
    assert row[0][0] == hsh(code) and row[0][1] is not None and str(row[0][2]) == res["binding_uid"]
    needles = {pp.normalize_code(code), code, secret, res["binding_key"]}
    dump = ""
    for tbl in ("device_pairing_codes", "device_cemetery_bindings", "devices", "cp_audit_log", "cp_pairing_attempts", "cp_field_inbox_items"):
        dump += "\n".join(r[0] for r in sqlrow(pw, f"SELECT t::text FROM {tbl} t"))
    assert not any(n in dump for n in needles)
    assert not any(n in caplog.text for n in needles)
    assert "phone_paired" in dump and "phone_code_issued" in dump
    assert sqlrow(pw, "SELECT sync_key_hash FROM device_cemetery_bindings WHERE binding_uid=%s", (res["binding_uid"],))[0][0] == cc._h(res["binding_key"])


def test_ttl_is_capped_and_open_codes_limited(pw):
    t = pw["T"]["A"]
    assert pw["pp"].issue_code(t["main"], ttl=99999)["ttl"] == pp.MAX_TTL
    assert pw["pp"].issue_code(t["main"], ttl=1)["ttl"] == pp.MIN_TTL
    for _ in range(pp.MAX_OPEN_CODES - 2):
        pw["pp"].issue_code(t["main"])
    with pytest.raises(pp.TooManyOpenCodes):
        pw["pp"].issue_code(t["main"])
    pw["pp"].issue_code(pw["T"]["B"]["main"])                 # plafonul e per cimitir


# ===================================================================== 2. expirat / folosit / greșit / replay
def test_expired_code_rejected_and_not_consumed(pw):
    code = issue(pw)
    with adm(pw["pg"]) as c:
        c.execute("SET session_replication_role = replica")        # ocolește trigger-ul de imutabilitate doar în test, ca să simulăm trecerea timpului
        c.execute("UPDATE device_pairing_codes SET expires_at = now() - interval '1 minute' WHERE code_hash=%s", (hsh(code),))
    with pytest.raises(pp.PairingRejected, match="invalid, expirat sau deja folosit"):
        pw["pp"].redeem(code, "x", source="10.3.3.3")
    assert sqlrow(pw, "SELECT used_at FROM device_pairing_codes WHERE code_hash=%s", (hsh(code),))[0][0] is None
    assert sqlrow(pw, "SELECT count(*) FROM devices WHERE created_by='phone-pairing'")[0][0] == 0


def test_used_code_rejected_and_replay_creates_nothing(pw):
    code = issue(pw)
    first = pw["pp"].redeem(code, "x", source="10.4.4.4")
    for variant in (code, code.replace("-", ""), code.lower(), f" {code} "):
        with pytest.raises(pp.PairingRejected):
            pw["pp"].redeem(variant, "alt", source="10.4.4.5")
    assert sqlrow(pw, "SELECT count(*) FROM devices WHERE created_by='phone-pairing'")[0][0] == 1
    assert sqlrow(pw, "SELECT count(*) FROM device_cemetery_bindings WHERE device_uid IN (SELECT device_uid FROM devices WHERE created_by='phone-pairing')")[0][0] == 1
    assert pw["svc"].authenticate(first["binding_key"])["binding_uid"] == first["binding_uid"]


def test_wrong_malformed_and_foreign_type_codes_rejected_with_same_message(pw):
    t = pw["T"]["A"]
    msgs = set()
    bad = [pp.display_code(pp.new_code()), "", "short", None, 12345, "A" * 200, "0O1IL" * 4, pp.display_code(pp.new_code()).replace("-", "'; DROP TABLE devices;--")]
    for i, b in enumerate(bad):
        with pytest.raises(pp.PairingRejected) as e:
            pw["pp"].redeem(b, "x", source=f"10.5.5.{i}")
        msgs.add(str(e.value))
    assert msgs == {pp.GENERIC_REJECTION}
    main_code = pp.new_code()                                       # cod de tip MAIN valid, dar NU se poate folosi ca telefon
    with adm(pw["pg"]) as c:
        c.execute("INSERT INTO device_pairing_codes (code_hash, organization_uid, cemetery_uid, device_type, expires_at) VALUES (%s,%s,%s,'MAIN', now() + interval '5 minutes')",
                  (pp._h(main_code), t["org"], t["cem"]))
    with pytest.raises(pp.PairingRejected):
        pw["pp"].redeem(main_code, "x", source="10.5.5.99")
    assert sqlrow(pw, "SELECT used_at FROM device_pairing_codes WHERE code_hash=%s", (pp._h(main_code),))[0][0] is None


def test_existing_main_redeem_cannot_consume_a_phone_code(pw):
    code = issue(pw)
    t = pw["T"]["A"]
    with pytest.raises(cc.CodeError):
        pw["cp"].redeem_code(code, str(uuid.uuid4()), "PC", t["cem"])
    assert pw["cp"].code_row(code) is None
    assert sqlrow(pw, "SELECT used_at FROM device_pairing_codes WHERE code_hash=%s", (hsh(code),))[0][0] is None
    assert sqlrow(pw, "SELECT count(*) FROM devices WHERE device_type='MAIN' AND device_name='PC'")[0][0] == 0


# ===================================================================== 3. concurență
def test_concurrent_redeem_of_same_code_has_exactly_one_winner(pw):
    code = issue(pw)

    def go(i):
        try:
            return pw["pp"].redeem(code, f"c{i}", source=f"10.6.6.{i}")
        except pp.PairingRejected:
            return None
    with ThreadPoolExecutor(16) as ex:
        res = list(ex.map(go, range(16)))
    assert len([r for r in res if r]) == 1
    assert sqlrow(pw, "SELECT count(*) FROM devices WHERE created_by='phone-pairing'")[0][0] == 1
    assert sqlrow(pw, "SELECT count(*) FROM device_cemetery_bindings WHERE device_uid IN (SELECT device_uid FROM devices WHERE created_by='phone-pairing')")[0][0] == 1
    assert sqlrow(pw, "SELECT used_by_binding_uid FROM device_pairing_codes WHERE code_hash=%s", (hsh(code),))[0][0] is not None


def test_concurrent_issue_respects_open_code_cap(pw):
    t = pw["T"]["A"]

    def go(_):
        try:
            pw["pp"].issue_code(t["main"])
            return True
        except pp.TooManyOpenCodes:
            return False
    with ThreadPoolExecutor(20) as ex:
        ok = sum(ex.map(go, range(20)))
    assert ok == pp.MAX_OPEN_CODES


# ===================================================================== 4. rate limiting
def test_rate_limit_hard_cap_under_concurrency(pw):
    def go(_):
        try:
            pw["pp"].redeem(pp.display_code(pp.new_code()), "x", source="9.9.9.9")
            return "ok"
        except pp.PairingRejected:
            return "rejected"
        except pp.RateLimited as e:
            assert e.retry_after >= 1
            return "limited"
    with ThreadPoolExecutor(30) as ex:
        res = list(ex.map(go, range(30)))
    assert res.count("rejected") == pp.RATE_LIMIT and res.count("limited") == 30 - pp.RATE_LIMIT


def test_rate_limited_source_cannot_use_even_a_valid_code_others_unaffected(pw):
    for _ in range(pp.RATE_LIMIT):
        with pytest.raises(pp.PairingRejected):
            pw["pp"].redeem(pp.display_code(pp.new_code()), "x", source="8.8.8.8")
    code = issue(pw)
    with pytest.raises(pp.RateLimited):
        pw["pp"].redeem(code, "x", source="8.8.8.8")
    assert sqlrow(pw, "SELECT used_at FROM device_pairing_codes WHERE code_hash=%s", (hsh(code),))[0][0] is None
    assert pw["pp"].redeem(code, "x", source="8.8.4.4")["device_type"] == "MOBILE"           # altă sursă: neafectată
    with adm(pw["pg"]) as c:                                                                 # fereastra expiră -> contorul se resetează
        c.execute("UPDATE cp_pairing_attempts SET window_start = now() - interval '1 hour'")
    with pytest.raises(pp.PairingRejected):
        pw["pp"].redeem(pp.display_code(pp.new_code()), "x", source="8.8.8.8")


def test_client_id_bucket_blocks_rotating_ips_and_success_refunds(pw):
    cid = "install-" + uuid.uuid4().hex
    for i in range(pp.RATE_LIMIT):
        with pytest.raises(pp.PairingRejected):
            pw["pp"].redeem(pp.display_code(pp.new_code()), "x", client_id=cid, source=f"7.7.7.{i}")
    with pytest.raises(pp.RateLimited):
        pw["pp"].redeem(issue(pw), "x", client_id=cid, source="7.7.7.200")
    for _ in range(3):
        with pytest.raises(pp.PairingRejected):
            pw["pp"].redeem(pp.display_code(pp.new_code()), "x", source="6.6.6.6")
    pw["pp"].redeem(issue(pw), "x", source="6.6.6.6")
    assert sqlrow(pw, "SELECT attempts FROM cp_pairing_attempts WHERE bucket=%s", ("ip:" + pp._h("ip|6.6.6.6")[:40],))[0][0] == 3


def test_pairing_attempts_table_is_rls_scoped_and_app_cannot_read_other_buckets(pw):
    with pytest.raises(pp.PairingRejected):
        pw["pp"].redeem(pp.display_code(pp.new_code()), "x", source="5.5.5.5")
    t = pw["T"]["A"]
    with appconn(pw["pg"], t["org"], t["cem"]) as c:
        assert c.execute("SELECT count(*) FROM cp_pairing_attempts").fetchone()[0] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("DELETE FROM cp_pairing_attempts")


# ===================================================================== 5. izolare tenant / cimitir
def test_cross_tenant_isolation(pw):
    tA, tB = pw["T"]["A"], pw["T"]["B"]
    a = pair(pw, "A")
    b = pair(pw, "B", source="10.0.0.2")
    assert (a["organization_uid"], a["cemetery_uid"]) == (tA["org"], tA["cem"]) and (b["organization_uid"], b["cemetery_uid"]) == (tB["org"], tB["cem"])
    itB = make_item(pw, b["binding_key"], "B")
    itA = make_item(pw, a["binding_key"], "A")
    svc = pw["svc"]
    for call in (lambda: svc.create_item(a["binding_key"], tB["cem"], "k-" + uuid.uuid4().hex, "WORK", dict(PAY["WORK"]), grave_uid="G"),
                 lambda: svc.list_items(a["binding_key"], tB["cem"]), lambda: svc.get_item(a["binding_key"], tB["cem"], itB["uid"]),
                 lambda: svc.get_item(a["binding_key"], tA["cem"], itB["uid"]), lambda: svc.get_item(b["binding_key"], tB["cem"], itA["uid"])):
        with pytest.raises(cc.ScopeError):
            call()
    assert [x["uid"] for x in svc.list_items(a["binding_key"], tA["cem"])] == [itA["uid"]]
    assert a["binding_uid"] in {p["binding_uid"] for p in pw["pp"].list_phones(tA["main"])}
    assert b["binding_uid"] not in {p["binding_uid"] for p in pw["pp"].list_phones(tA["main"])}
    assert a["binding_uid"] not in {p["binding_uid"] for p in pw["pp"].list_phones(tB["main"])}
    for who, target in ((tB["main"], a["binding_uid"]), (tA["main"], b["binding_uid"])):
        with pytest.raises(cc.ScopeError):
            pw["pp"].revoke_phone(who, target)
    assert pw["svc"].authenticate(a["binding_key"]) and pw["svc"].authenticate(b["binding_key"])


def test_cross_cemetery_isolation_same_organization(pw):
    tA, tA2 = pw["T"]["A"], pw["T"]["A2"]
    a = pair(pw, "A")
    a2 = pair(pw, "A2", source="10.0.0.3")
    assert a2["cemetery_uid"] == tA2["cem"] and a2["organization_uid"] == tA["org"]
    with pytest.raises(cc.ScopeError):
        pw["svc"].create_item(a["binding_key"], tA2["cem"], "k-" + uuid.uuid4().hex, "WORK", dict(PAY["WORK"]), grave_uid="G")
    with pytest.raises(cc.ScopeError):
        pw["svc"].list_items(a2["binding_key"], tA["cem"])
    with pytest.raises(cc.ScopeError):
        pw["pp"].revoke_phone(tA2["main"], a["binding_uid"])
    with pytest.raises(cc.ScopeError):
        pw["pp"].revoke_phone(tA["main"], a2["binding_uid"])
    assert a["binding_uid"] not in {p["binding_uid"] for p in pw["pp"].list_phones(tA2["main"])}
    assert pw["svc"].authenticate(a["binding_key"]) and pw["svc"].authenticate(a2["binding_key"])


def test_tenant_comes_only_from_the_code_not_from_the_request(pw):
    tA, tB = pw["T"]["A"], pw["T"]["B"]
    code = issue(pw, "A")
    r = pw["client"].post(pph.PREFIX + "/redeem", json={"code": code, "device_name": "x", "organization_uid": tB["org"], "cemetery_uid": tB["cem"]})
    assert r.status_code == 201 and r.json()["result"]["cemetery_uid"] == tA["cem"] and r.json()["result"]["organization_uid"] == tA["org"]


# ===================================================================== 6. revocare
def test_revoked_device_is_dead_everywhere_and_revocation_is_permanent(pw):
    t = pw["T"]["A"]
    a = pair(pw)
    it = make_item(pw, a["binding_key"])
    assert pw["pp"].revoke_phone(t["main"], a["binding_uid"])["revoked"] is True
    assert pw["pp"].revoke_phone(t["main"], a["binding_uid"])["revoked"] is True               # idempotent
    for call in (lambda: pw["svc"].authenticate(a["binding_key"]), lambda: make_item(pw, a["binding_key"]), lambda: pw["svc"].list_items(a["binding_key"], t["cem"])):
        with pytest.raises(cc.ScopeError):
            call()
    r = pw["client"].get(fih.PREFIX + "/items", headers=H(a["binding_key"]))
    assert r.status_code == 401
    assert pw["svc"].get_item(t["main"], t["cem"], it["uid"])["status"] == "PENDING"            # datele deja trimise rămân pentru MAIN
    assert [p for p in pw["pp"].list_phones(t["main"]) if p["binding_uid"] == a["binding_uid"]][0]["active"] is False
    with adm(pw["pg"]) as c:                                                                      # DB: o legătură revocată nu se reactivează
        for sql in ("UPDATE device_cemetery_bindings SET revoked_at=NULL WHERE binding_uid=%s", "UPDATE device_cemetery_bindings SET active=true WHERE binding_uid=%s",
                    "UPDATE device_cemetery_bindings SET sync_key_hash=repeat('a',64) WHERE binding_uid=%s"):
            with pytest.raises(psycopg.errors.CheckViolation):
                c.execute(sql, (a["binding_uid"],))
    b = pair(pw, source="10.0.0.9")                                                               # re-pairing explicit, cu cod nou -> legătură nouă
    assert b["binding_uid"] != a["binding_uid"] and pw["svc"].authenticate(b["binding_key"])
    with pytest.raises(cc.ScopeError):
        pw["svc"].authenticate(a["binding_key"])


def test_only_main_can_revoke_only_phones(pw):
    t = pw["T"]["A"]
    a, other = pair(pw), pair(pw, source="10.0.0.8")
    for call in (lambda: pw["pp"].revoke_phone(a["binding_key"], other["binding_uid"]), lambda: pw["pp"].issue_code(a["binding_key"]), lambda: pw["pp"].list_phones(a["binding_key"])):
        with pytest.raises(cc.PermissionDenied):
            call()
    for target in (t["main_b"], str(uuid.uuid4()), "not-a-uuid", "' OR 1=1 --"):                 # nici MAIN-ul propriu, nici inexistent
        with pytest.raises(cc.ScopeError):
            pw["pp"].revoke_phone(t["main"], target)
    assert pw["svc"].authenticate(t["main"]) and pw["svc"].authenticate(other["binding_key"])


# ===================================================================== 7. privilegii minime ale telefonului
def test_paired_phone_has_no_elevated_privileges(pw):
    t = pw["T"]["A"]
    a, other = pair(pw), pair(pw, source="10.0.0.7")
    mine, theirs = make_item(pw, a["binding_key"]), make_item(pw, other["binding_key"])
    svc = pw["svc"]
    for call in (lambda: svc.accept(a["binding_key"], t["cem"], mine["uid"]), lambda: svc.reject(a["binding_key"], t["cem"], mine["uid"], "x"),
                 lambda: svc.mark_imported(a["binding_key"], t["cem"], mine["uid"])):
        with pytest.raises(cc.PermissionDenied):
            call()
    with pytest.raises(cc.ScopeError):
        svc.get_item(a["binding_key"], t["cem"], theirs["uid"])
    assert [x["uid"] for x in svc.list_items(a["binding_key"], t["cem"])] == [mine["uid"]]
    assert {x["uid"] for x in svc.list_items(t["main"], t["cem"])} == {mine["uid"], theirs["uid"]}
    r = pw["client"]
    assert r.post(pph.PREFIX + "/codes", headers=H(a["binding_key"])).status_code == 403
    assert r.post(fih.PREFIX + f"/items/{mine['uid']}/accept", headers=H(a["binding_key"])).status_code == 403
    assert r.get(pph.PREFIX + "/phones", headers=H(a["binding_key"])).status_code == 403
    # DB: nici direct în SQL (rol aplicație, contextul legăturii telefonului) nu se poate auto-aproba, nici deveni emitent de bootstrap
    with appconn(pw["pg"], t["org"], t["cem"]) as c:
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("UPDATE cp_field_inbox_items SET status='ACCEPTED', reviewed_at=now(), reviewed_by=%s WHERE uid=%s", (a["binding_uid"], mine["uid"]))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("DELETE FROM device_pairing_codes")
    with adm(pw["pg"]) as c:
        ident = str(uuid.uuid4())
        c.execute("INSERT INTO identities (uid, display_name, password_hash) VALUES (%s,'t','!')", (ident,))
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("INSERT INTO cp_binding_issuers (binding_uid, organization_uid, principal_uid) VALUES (%s,%s,%s)", (a["binding_uid"], t["org"], ident))


def test_pairing_requires_main_and_main_only_for_issuing(pw):
    t = pw["T"]["A"]
    for bad in (None, "", "garbage", t["phone"], "cimitir." + str(uuid.uuid4()) + ".x"):
        with pytest.raises((cc.ScopeError, cc.PermissionDenied)):
            pw["pp"].issue_code(bad)
    assert sqlrow(pw, "SELECT count(*) FROM device_pairing_codes WHERE device_type='MOBILE'")[0][0] == 0


# ===================================================================== 8. garduri DB pe coduri MOBILE
def test_db_guards_on_mobile_codes(pw):
    t = pw["T"]["A"]
    ins = ("INSERT INTO device_pairing_codes (code_hash, organization_uid, cemetery_uid, device_type, expires_at) VALUES (%s,%s,%s,%s, now() + make_interval(mins => %s))")
    ok_hash = pp._h(pp.new_code())
    with adm(pw["pg"]) as c:
        for args in ((ok_hash, t["org"], t["cem"], "MOBILE", 120), ("RAWCODE", t["org"], t["cem"], "MOBILE", 5), (ok_hash, t["org"], t["cem"], "TABLET", 5)):
            with pytest.raises(psycopg.errors.CheckViolation):
                c.execute(ins, args)
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("INSERT INTO device_pairing_codes (code_hash, organization_uid, cemetery_uid, device_type, expires_at) VALUES (%s,%s,NULL,'MOBILE', now() + interval '5 minutes')",
                      (ok_hash, t["org"]))
    code = issue(pw)
    pair_res = pw["pp"].redeem(code, "x", source="10.9.9.9")
    with adm(pw["pg"]) as c:
        for sql in ("UPDATE device_pairing_codes SET used_at=NULL WHERE code_hash=%s", "UPDATE device_pairing_codes SET expires_at = now() + interval '20 minutes' WHERE code_hash=%s",
                    "UPDATE device_pairing_codes SET cemetery_uid=%s WHERE code_hash=%s"):
            with pytest.raises(psycopg.errors.CheckViolation):
                c.execute(sql, (hsh(code),) if sql.count("%s") == 1 else (pw["T"]["A2"]["cem"], hsh(code)))
    assert pair_res["cemetery_uid"] == t["cem"]


# ===================================================================== 9. transport HTTP
def test_http_flow_headers_errors_and_no_echo(pw):
    t, cl = pw["T"]["A"], pw["client"]
    assert cl.post(pph.PREFIX + "/codes").status_code == 401
    assert cl.post(pph.PREFIX + "/codes", headers=H("garbage")).status_code == 401
    r = cl.post(pph.PREFIX + "/codes", headers=H(t["main"]), json={"ttl": 120})
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store" and r.json()["result"]["ttl"] == 120
    code = r.json()["result"]["code"]
    assert cl.post(pph.PREFIX + "/codes", headers=H(t["main"]), json={"ttl": "9"}).status_code == 400
    assert cl.post(pph.PREFIX + "/redeem", content=b"{not json", headers={"content-type": "application/json"}).status_code == 400
    assert cl.post(pph.PREFIX + "/redeem", json=["x"]).status_code == 400
    assert cl.post(pph.PREFIX + "/redeem", content=b"x" * 10000).status_code in (400, 415)
    ok = cl.post(pph.PREFIX + "/redeem", json={"code": code, "device_name": "T1"})
    assert ok.status_code == 201 and ok.headers["cache-control"] == "no-store"
    key = ok.json()["result"]["binding_key"]
    again = cl.post(pph.PREFIX + "/redeem", json={"code": code})
    assert again.status_code == 401 and again.json()["code"] == "PAIRING_REJECTED" and code not in again.text and code.replace("-", "") not in again.text
    assert cl.post(fih.PREFIX + "/items", headers=H(key), json={"idempotency_key": "k-" + uuid.uuid4().hex, "item_type": "WORK", "payload": PAY["WORK"], "grave_uid": "G"}).status_code == 201
    listed = cl.get(pph.PREFIX + "/phones", headers=H(t["main"])).json()["result"]
    assert "binding_key" not in str(listed) and "sync_key_hash" not in str(listed)
    buid = ok.json()["result"]["binding_uid"]
    assert cl.post(pph.PREFIX + f"/phones/{buid}/revoke", headers=H(t["main"])).status_code == 200
    assert cl.post(pph.PREFIX + f"/phones/{uuid.uuid4()}/revoke", headers=H(t["main"])).status_code == 404
    assert cl.get(fih.PREFIX + "/items", headers=H(key)).status_code == 401


def test_http_rate_limit_ignores_spoofed_forwarded_for_unless_trusted(pw, monkeypatch):
    cl = pw["client"]
    for i in range(pp.RATE_LIMIT):
        assert cl.post(pph.PREFIX + "/redeem", json={"code": pp.display_code(pp.new_code())}, headers={"X-Forwarded-For": f"1.2.3.{i}"}).status_code == 401
    r = cl.post(pph.PREFIX + "/redeem", json={"code": pp.display_code(pp.new_code())}, headers={"X-Forwarded-For": "1.2.3.250"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1 and r.json()["code"] == "RATE_LIMITED"
    monkeypatch.setenv("CIMITIR_PAIRING_TRUSTED_PROXIES", "1")                          # în spatele unui proxy de încredere: se ia IP-ul real
    assert cl.post(pph.PREFIX + "/redeem", json={"code": pp.display_code(pp.new_code())}, headers={"X-Forwarded-For": "203.0.113.77"}).status_code == 401


def test_create_app_exposes_pairing_next_to_field_inbox(pw, monkeypatch):
    app = fih.create_app(pw["svc"])
    cl = TestClient(app)
    code = cl.post(pph.PREFIX + "/codes", headers=H(pw["T"]["A"]["main"])).json()["result"]["code"]
    assert cl.post(pph.PREFIX + "/redeem", json={"code": code}).status_code == 201
    assert cl.get(wire_health()).status_code == 200


def wire_health():
    return fih.wire.API + "/field-inbox-health"


def test_migration_019_is_idempotent_and_additive(pw):
    sql = MIG.read_text(encoding="utf-8")
    assert "public" not in re.sub(r"--.*", "", sql) and "DROP TABLE" not in sql.upper() and "DELETE FROM" not in sql.upper()
    with adm(pw["pg"]) as c:
        c.execute(sql)
        c.execute(sql)
        assert c.execute("SELECT count(*) FROM pg_trigger WHERE tgname IN ('cp_mobile_pairing_guard','cp_binding_revocation_guard','cp_binding_issuers_no_mobile')").fetchone()[0] == 3
    assert fi.STATUSES == ("PENDING", "ACCEPTED", "REJECTED", "IMPORTED")
