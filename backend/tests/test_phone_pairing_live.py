# ruff: noqa: F811
"""LIVE (sărit fără CIMITIR_LIVE_TESTS + CIMITIR_TEST_DATABASE_URL + SUPABASE_*). Phone Pairing v1 pe Supabase TEST REAL (ref nhhczdmcocbobptogvsw), ca `cimitir_cp_app` (fără BYPASSRLS).
Producția (txuxkjgftopznvmooexq) e blocată de fixture-ul `live` și de gărzile din cimitir_cloud_control_pg / FieldInboxService / cimitir_test_guard.
Migrarea 019 se aplică DOAR dacă CIMITIR_APPLY_MIGRATION_019=1 (după ce testele locale au trecut și ref-ul a fost confirmat); altfel testul cere ca ea să fie deja aplicată.
Toate datele create (organizații/cimitire/dispozitive/coduri/itemuri) aparțin tenanților de test ai acestui run și sunt curățate la final."""
from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import cimitir_bootstrap as bs
import cimitir_cloud_control as cc
import cimitir_field_inbox as fi
import cimitir_phone_pairing as pp
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_field_storage_live import aconn, live, mk as mk_stores, penv  # noqa: F401
from test_etapa6c_field_inbox_pg import PAY, seed_binding

MIG = Path(__file__).resolve().parents[1] / "cimitir" / "pg_migrations" / "019_phone_pairing.sql"
pytestmark = pytest.mark.skipif(not all(os.environ.get(k) for k in ("CIMITIR_LIVE_TESTS", "CIMITIR_TEST_DATABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_BUCKET")),
                                reason="Supabase TEST (DB + Storage) nu este configurat")
T: dict = {}


@pytest.fixture(scope="module", autouse=True)
def _migration(live):
    assert os.environ["CIMITIR_TEST_PROJECT_REF"] == "nhhczdmcocbobptogvsw" and "txuxkjgftopznvmooexq" not in live["admin"]       # confirmare ref ÎNAINTE de orice write
    with aconn(live) as c:
        if os.environ.get("CIMITIR_APPLY_MIGRATION_019") == "1":
            c.execute(MIG.read_text(encoding="utf-8"))
        assert c.execute("SELECT to_regclass('cp_pairing_attempts') IS NOT NULL").fetchone()[0], "migrarea 019 nu este aplicată pe TEST"
    yield
    with aconn(live) as c:                                   # curăță doar datele tenantelor create de acest run
        c.execute("SET session_replication_role = replica")
        for t in T.values():
            c.execute("DELETE FROM cp_field_inbox_items WHERE cemetery_uid=%s", (t["cem"],))
            c.execute("DELETE FROM device_pairing_codes WHERE cemetery_uid=%s", (t["cem"],))
        c.execute("DELETE FROM cp_pairing_attempts WHERE bucket LIKE 'ip:%' AND bucket IN (SELECT bucket FROM cp_pairing_attempts WHERE window_start > now() - interval '2 hours')")


@pytest.fixture()
def svc(live):
    cp, media = mk_stores(live)
    inbox = fi.FieldInboxService(cp, media)
    return pp.PhonePairingService(inbox), inbox


def test_00_setup(penv, live):
    w = World(penv)
    for tag, org, owner in (("A", w.org1, w.owner1), ("B", w.org2, w.owner2)):
        b = w.local(org, name=f"PP-TEST {tag}")
        code = penv.cp.issue_code(owner, org)
        plan = bs.plan_flow_a(b, penv.cp, code, penv.inst, cemetery_name=f"PP-TEST {tag}")
        bs.execute_flow_a(b, penv.cp, penv.store, plan, plan["plan_hash"], code, penv.inst)
        main = penv.store.get(bs.LocalRegistry(b).get("cloud_secret_ref"))
        T[tag] = {"org": org, "cem": b.cemetery_uid, "main": main}
    a2 = str(uuid.uuid4())
    with aconn(live) as c:
        c.execute("INSERT INTO cemeteries (uid, organization_uid, name, internal_code, status, data_epoch) VALUES (%s,%s,'PP-TEST A2',%s,'DRAFT',1)", (a2, T["A"]["org"], a2))
    T["A2"] = {"org": T["A"]["org"], "cem": a2}
    T["A2"]["main"], _ = seed_binding({"admin": live["admin"]}, T["A"]["org"], a2, "MAIN")


def test_01_valid_used_wrong_replay_and_hash_only(svc, live):
    s, inbox = svc
    code = s.issue_code(T["A"]["main"])["code"]
    res = s.redeem(code, "Telefon live", source="198.51.100.1")
    assert res["cemetery_uid"] == T["A"]["cem"] and inbox.authenticate(res["binding_key"])["device_type"] == "MOBILE"
    it = inbox.create_item(res["binding_key"], T["A"]["cem"], "live-" + uuid.uuid4().hex, "WORK", dict(PAY["WORK"]), grave_uid="G")["item"]
    assert inbox.accept(T["A"]["main"], T["A"]["cem"], it["uid"])["item"]["status"] == "ACCEPTED"
    for bad in (code, pp.display_code(pp.new_code()), "x"):
        with pytest.raises(pp.PairingRejected):
            s.redeem(bad, "x", source="198.51.100.2")
    with aconn(live) as c:
        dump = "".join(r[0] for r in c.execute("SELECT t::text FROM device_pairing_codes t").fetchall() + c.execute("SELECT t::text FROM device_cemetery_bindings t").fetchall())
    assert pp.normalize_code(code) not in dump and res["binding_key"].split(".", 2)[2] not in dump
    T["A"]["phone"], T["A"]["phone_b"] = res["binding_key"], res["binding_uid"]


def test_02_expired_cross_tenant_cross_cemetery_revoked(svc, live):
    s, inbox = svc
    code = s.issue_code(T["A"]["main"])["code"]
    with aconn(live) as c:
        c.execute("SET session_replication_role = replica")
        c.execute("UPDATE device_pairing_codes SET expires_at = now() - interval '1 minute' WHERE code_hash=%s", (pp._h(pp.normalize_code(code)),))
    with pytest.raises(pp.PairingRejected):
        s.redeem(code, "x", source="198.51.100.3")
    a = s.redeem(s.issue_code(T["A"]["main"])["code"], "A", source="198.51.100.4")
    b = s.redeem(s.issue_code(T["B"]["main"])["code"], "B", source="198.51.100.5")
    a2 = s.redeem(s.issue_code(T["A2"]["main"])["code"], "A2", source="198.51.100.6")
    for key, cem in ((a["binding_key"], T["B"]["cem"]), (a["binding_key"], T["A2"]["cem"]), (b["binding_key"], T["A"]["cem"]), (a2["binding_key"], T["A"]["cem"])):
        with pytest.raises(cc.ScopeError):
            inbox.list_items(key, cem)
    for main, target in ((T["B"]["main"], a["binding_uid"]), (T["A2"]["main"], a["binding_uid"]), (T["A"]["main"], b["binding_uid"])):
        with pytest.raises(cc.ScopeError):
            s.revoke_phone(main, target)
    s.revoke_phone(T["A"]["main"], a["binding_uid"])
    with pytest.raises(cc.ScopeError):
        inbox.authenticate(a["binding_key"])
    with pytest.raises(cc.PermissionDenied):
        inbox.accept(b["binding_key"], T["B"]["cem"], inbox.create_item(b["binding_key"], T["B"]["cem"], "live-" + uuid.uuid4().hex, "WORK", dict(PAY["WORK"]), grave_uid="G")["item"]["uid"])


def test_03_concurrent_redeem_and_rate_limit(svc, live):
    s, _ = svc
    code = s.issue_code(T["A"]["main"])["code"]

    def go(i):
        try:
            return s.redeem(code, f"c{i}", source=f"203.0.113.{i}")
        except pp.PairingRejected:
            return None
    with ThreadPoolExecutor(12) as ex:
        assert len([r for r in ex.map(go, range(12)) if r]) == 1
    src = "192.0.2." + str(uuid.uuid4().int % 200 + 1)
    out = []
    for _ in range(pp.RATE_LIMIT + 2):
        try:
            s.redeem(pp.display_code(pp.new_code()), "x", source=src)
        except pp.PairingRejected:
            out.append("r")
        except pp.RateLimited:
            out.append("l")
    assert out.count("r") == pp.RATE_LIMIT and out.count("l") == 2


def test_04_phone_privileges_own_items_only_secrets_absent_and_inbox_regression(svc, live, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    s, inbox = svc
    tA = T["A"]
    code1, code2 = s.issue_code(tA["main"])["code"], s.issue_code(tA["main"])["code"]
    p1, p2 = s.redeem(code1, "P1", source="198.51.100.40"), s.redeem(code2, "P2", source="198.51.100.41")
    i1 = inbox.create_item(p1["binding_key"], tA["cem"], "live-" + uuid.uuid4().hex, "WORK", dict(PAY["WORK"]), grave_uid="G")["item"]
    i2 = inbox.create_item(p2["binding_key"], tA["cem"], "live-" + uuid.uuid4().hex, "WORK", dict(PAY["WORK"]), grave_uid="G")["item"]
    assert [x["uid"] for x in inbox.list_items(p1["binding_key"], tA["cem"])] == [i1["uid"]]
    with pytest.raises(cc.ScopeError):
        inbox.get_item(p1["binding_key"], tA["cem"], i2["uid"])
    for call in (lambda: inbox.accept(p1["binding_key"], tA["cem"], i1["uid"]), lambda: inbox.reject(p1["binding_key"], tA["cem"], i1["uid"], "x"),
                 lambda: inbox.mark_imported(p1["binding_key"], tA["cem"], i1["uid"]), lambda: s.issue_code(p1["binding_key"]), lambda: s.list_phones(p1["binding_key"]),
                 lambda: s.revoke_phone(p1["binding_key"], p2["binding_uid"])):
        with pytest.raises(cc.PermissionDenied):
            call()
    assert {x["uid"] for x in inbox.list_items(tA["main"], tA["cem"], "PENDING") if x["uid"] in (i1["uid"], i2["uid"])} == {i1["uid"], i2["uid"]}
    assert inbox.accept(tA["main"], tA["cem"], i1["uid"])["item"]["status"] == "ACCEPTED"
    assert inbox.reject(tA["main"], tA["cem"], i2["uid"], "motiv")["item"]["status"] == "REJECTED"
    assert inbox.mark_imported(tA["main"], tA["cem"], i1["uid"])["item"]["status"] == "IMPORTED"
    with aconn(live) as c:
        assert c.execute("SELECT count(*) FROM cp_binding_issuers WHERE binding_uid IN (%s,%s)", (p1["binding_uid"], p2["binding_uid"])).fetchone()[0] == 0
        dump = "".join(r[0] for tbl in ("device_pairing_codes", "device_cemetery_bindings", "devices", "cp_audit_log", "cp_pairing_attempts", "cp_field_inbox_items")
                       for r in c.execute(f"SELECT t::text FROM {tbl} t").fetchall())
    needles = [pp.normalize_code(code1), pp.normalize_code(code2), p1["binding_key"], p2["binding_key"], p1["binding_key"].split(".", 2)[2], p2["binding_key"].split(".", 2)[2]]
    assert not any(n in dump for n in needles) and not any(n in caplog.text for n in needles)
    assert "phone_paired" in dump and "phone_code_issued" in dump and "phone_revoked" in dump
