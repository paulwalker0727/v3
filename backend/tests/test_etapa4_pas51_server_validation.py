# ruff: noqa: F811
"""Etapa 4 / Pasul 5.1 — validarea planului PE SERVER (FastAPI local, proces separat, simulator SQLite). Fără Supabase/PostgreSQL/Render/producție.

Cereri directe care ocolesc MAIN: plan lipsă/falsificat/expirat, bootstrap_id invalid, alt tenant/instalare, drift sursă/țintă, stare greșită, capabilitate lipsă
=> refuz cu cod HTTP stabil și ZERO scrieri în baza cloud (amprenta completă a bazei rămâne identică)."""
from __future__ import annotations

import copy
import hashlib
import sqlite3
import time
import uuid

import httpx
import pytest

import cimitir_bootstrap as bs
import cimitir_cloud_client as ccl
import cimitir_cloud_control as cc
import cimitir_cloud_http as ch
import cimitir_cloud_wire as wire
import cimitir_entities as ents
import cimitir_flow_b as fb
import cimitir_manifest as mf
import cimitir_plan_guard as pg
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa4_pas4_flux_b import b_world, cem_state
from test_etapa4_pas5_http import db, start_server


# ------------------------------------------------------------------ utilitare
def fingerprint(env) -> str:
    """Amprenta COMPLETĂ a bazei cloud (toate tabelele, toate rândurile): orice scriere o schimbă."""
    c = sqlite3.connect(env.cp.path)
    try:
        h = hashlib.sha256()
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            h.update(t.encode())
            for row in sorted(repr(r) for r in c.execute(f'SELECT * FROM "{t}"')):
                h.update(row.encode())
        return h.hexdigest()
    finally:
        c.close()


def post(url, op, args, auth, kwargs=None):
    return httpx.post(url + wire.API + wire.OPS[op][0], json=wire.encode({"args": args, "kwargs": kwargs or {}}), headers={"Authorization": auth}, timeout=10)


def refused(env, r, status, code, before):
    assert (r.status_code, r.json().get("code")) == (status, code), r.text
    assert fingerprint(env) == before, "scriere în baza cloud înainte/în locul validării"


def begin_args(env, plan, cem, bid=None, kind="B", plan_hash=None, inst=None):
    return [None, inst or env.inst, bid or plan["bootstrap_id"], kind, cem, plan_hash or plan["plan_hash"]]


@pytest.fixture()
def bsrv(renv):
    """Lume B (MAIN nevid, cloud DRAFT gol) + server cu ceas controlabil; planul e făcut de MAIN (citire), dar NU executat."""
    env = renv
    w, b, code = b_world(env)
    clock_file = env.tmp / "server_clock_offset"
    clock_file.write_text("0")
    _p, url, _ = start_server(env, clock_file=clock_file)
    rcp = ccl.RemoteControlPlane(url, code)
    plan = fb.plan_flow_b(b, rcp, w.owner1, env.inst)
    return env, w, b, code, url, rcp, plan, clock_file


def rehash(claim):
    claim["fields"]["local"] = claim["local_manifest"]["stable_digest"] = mf.stable_digest(claim["local_manifest"])
    return mf.sha(claim["fields"])


# ================================================================== CERERI DIRECTE (OCOLESC MAIN) LA begin_run
CASES = {
    "no_plan": lambda env, plan, cl: (begin_args(env, plan, plan["cemetery_uid"]), None, 400, "PLAN_INVALID"),
    "plan_hash_forged": lambda env, plan, cl: (begin_args(env, plan, plan["cemetery_uid"], plan_hash="f" * 64), cl, 400, "PLAN_INVALID"),
    "fields_tampered": lambda env, plan, cl: (cl["fields"].update(cloud="0" * 64) or begin_args(env, plan, plan["cemetery_uid"]), cl, 400, "PLAN_INVALID"),
    "rehashed_forged_target": lambda env, plan, cl: (cl["fields"].update(cloud="0" * 64) or begin_args(env, plan, plan["cemetery_uid"], plan_hash=mf.sha(cl["fields"])),
                                                     cl, 409, "TARGET_DRIFT"),
    "expired": lambda env, plan, cl: (cl.update(created_at=time.time() - 700, expires_at=time.time() - 100) or begin_args(env, plan, plan["cemetery_uid"]), cl, 410, "PLAN_EXPIRED"),
    "ttl_too_long": lambda env, plan, cl: (cl.update(expires_at=cl["created_at"] + 3600) or begin_args(env, plan, plan["cemetery_uid"]), cl, 400, "PLAN_INVALID"),
    "created_in_future": lambda env, plan, cl: (cl.update(created_at=time.time() + 3600, expires_at=time.time() + 3900) or begin_args(env, plan, plan["cemetery_uid"]),
                                                cl, 400, "PLAN_INVALID"),
    "bootstrap_id_not_uuid": lambda env, plan, cl: (begin_args(env, plan, plan["cemetery_uid"], bid="nu-e-uuid"), cl, 400, "PLAN_INVALID"),
    "kind_mismatch": lambda env, plan, cl: (begin_args(env, plan, plan["cemetery_uid"], kind="A"), cl, 400, "PLAN_INVALID"),
    "other_installation": lambda env, plan, cl: (begin_args(env, plan, plan["cemetery_uid"], inst=str(uuid.uuid4())), cl, 400, "PLAN_INVALID"),
    "other_cemetery": lambda env, plan, cl: (begin_args(env, plan, str(uuid.uuid4())), cl, 404, "SCOPE_MISMATCH"),
    "source_manifest_tampered": lambda env, plan, cl: (cl["local_manifest"].update(content_digest="0" * 64) or begin_args(env, plan, plan["cemetery_uid"]),
                                                       cl, 400, "PLAN_INVALID"),
    "rehashed_empty_source_for_b": lambda env, plan, cl: (cl["local_manifest"].update(classification=mf.EMPTY) or
                                                          begin_args(env, plan, plan["cemetery_uid"], plan_hash=rehash(cl)), cl, 409, "PLAN_BLOCKED"),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_direct_begin_run_bypassing_main_is_refused_with_zero_writes(bsrv, case):
    env, w, b, code, url, rcp, plan, _ = bsrv
    claim = copy.deepcopy(pg.claim_for(plan))
    args, cl, status, err = CASES[case](env, plan, claim)
    before = fingerprint(env)
    r = post(url, "begin_run", args, f"Pairing {code}", {"plan": cl} if cl is not None else None)
    refused(env, r, status, err, before)
    assert db(env, "SELECT COUNT(*) FROM bootstrap_runs")[0][0] == 0 and db(env, "SELECT COUNT(*) FROM bindings")[0][0] == 0


def test_target_drift_between_plan_and_begin_is_detected_by_server(bsrv):
    env, w, b, code, url, rcp, plan, _ = bsrv
    env.cp.sim_put_row(b.cemetery_uid, ents.ORDER[0], str(uuid.uuid4()), {})  # ținta se schimbă după plan (alt actor scrie în cloud)
    before = fingerprint(env)
    r = post(url, "begin_run", begin_args(env, plan, b.cemetery_uid), f"Pairing {code}", {"plan": pg.claim_for(plan)})
    refused(env, r, 409, "TARGET_DRIFT", before)


def test_valid_plan_without_bootstrap_capability_is_forbidden(bsrv):
    env, w, b, code, url, rcp, plan, _ = bsrv
    reb = env.cp.create_principal("rebuild-only")
    env.cp.grant_role(w.owner1, reb, w.org1, cc.REBUILD)
    c = sqlite3.connect(env.cp.path)
    c.execute("UPDATE pairing_codes SET issued_by=? WHERE code_hash=?", (reb, cc._h(code)))
    c.commit()
    c.close()
    before = fingerprint(env)
    refused(env, post(url, "begin_run", begin_args(env, plan, b.cemetery_uid), f"Pairing {code}", {"plan": pg.claim_for(plan)}), 403, "FORBIDDEN", before)


# ================================================================== EXPIRARE PE CEASUL SERVERULUI (nu al MAIN)
def test_server_clock_expiry_refuses_planned_run_and_main_rolls_back(bsrv):
    env, w, b, code, url, rcp, plan, clock_file = bsrv
    clock_file.write_text(str(pg.PLAN_TTL + 100))  # MAIN crede că planul e proaspăt; ceasul serverului spune altceva
    before = fingerprint(env)
    with pytest.raises(pg.PlanExpired):  # 410 => aceeași excepție în MAIN
        fb.execute_flow_b(b, rcp, env.store, w.owner1, plan, plan["plan_hash"], code, env.inst, batch_size=2, chunk_size=3)
    assert fingerprint(env) == before and db(env, "SELECT COUNT(*) FROM bootstrap_runs")[0][0] == 0
    assert bs.LocalRegistry(b).run_get(plan["bootstrap_id"])["state"] == "rolled_back" and bs.LocalRegistry(b).sync_state == "UNBOUND"


def test_planned_run_expires_on_server_for_pair_and_begin_retry(bsrv):
    env, w, b, code, url, rcp, plan, clock_file = bsrv
    rcp.begin_run(code, env.inst, plan["bootstrap_id"], "B", b.cemetery_uid, plan["plan_hash"], plan=pg.claim_for(plan))
    assert db(env, "SELECT state FROM bootstrap_runs") == [("planned",)]
    clock_file.write_text(str(pg.PLAN_TTL + 5))
    before = fingerprint(env)
    refused(env, post(url, "redeem_code", [None, env.inst, "MAIN", b.cemetery_uid, ""], f"Pairing {code}", {"bootstrap_id": plan["bootstrap_id"], "pending": True}),
            410, "PLAN_EXPIRED", before)
    refused(env, post(url, "begin_run", begin_args(env, plan, b.cemetery_uid), f"Pairing {code}", {"plan": pg.claim_for(plan)}), 410, "PLAN_EXPIRED", before)
    assert db(env, "SELECT COUNT(*) FROM bindings")[0][0] == 0 and db(env, "SELECT COUNT(*) FROM devices")[0][0] == 0


# ================================================================== PAIRING DIRECT (fără bootstrap valid)
def test_direct_pairing_outside_a_valid_bootstrap_is_refused(bsrv):
    env, w, b, code, url, rcp, plan, _ = bsrv
    P = f"Pairing {code}"
    pair = [None, env.inst, "MAIN", b.cemetery_uid, ""]
    before = fingerprint(env)
    refused(env, post(url, "redeem_code", pair, P), 400, "PLAN_INVALID", before)                                           # fără bootstrap_id
    refused(env, post(url, "redeem_code", pair, P, {"bootstrap_id": str(uuid.uuid4()), "pending": True}), 404, "SCOPE_MISMATCH", before)  # bootstrap inexistent
    rcp.begin_run(code, env.inst, plan["bootstrap_id"], "B", b.cemetery_uid, plan["plan_hash"], plan=pg.claim_for(plan))
    bid = plan["bootstrap_id"]
    before = fingerprint(env)
    refused(env, post(url, "redeem_code", pair, P, {"bootstrap_id": bid, "pending": False}), 400, "PLAN_INVALID", before)  # B cere binding pending
    other_inst = [None, str(uuid.uuid4()), "MAIN", b.cemetery_uid, ""]
    refused(env, post(url, "redeem_code", other_inst, P, {"bootstrap_id": bid, "pending": True}), 400, "PLAN_INVALID", before)  # altă instalare
    assert post(url, "mark_failed_unauthenticated", [bid, env.inst, "abandon"], P).status_code == 200
    before = fingerprint(env)
    refused(env, post(url, "redeem_code", pair, P, {"bootstrap_id": bid, "pending": True}), 409, "CONFLICT", before)    # run închis
    assert db(env, "SELECT COUNT(*) FROM bindings")[0][0] == 0


# ================================================================== OPERAȚII DE TRANSFER DIRECTE (cu cheie de binding reală)
def test_direct_transfer_ops_require_same_plan_source_and_state(bsrv):
    env, w, b, code, url, rcp, plan, _ = bsrv
    bid = plan["bootstrap_id"]
    rcp.begin_run(code, env.inst, bid, "B", b.cemetery_uid, plan["plan_hash"], plan=pg.claim_for(plan))
    key = rcp.redeem_code(code, env.inst, "MAIN", b.cemetery_uid, bootstrap_id=bid, pending=True)["binding_key"]
    B = f"Binding {key}"
    org, cem = b.organization_uid, b.cemetery_uid
    src = plan["local_manifest"]["content_digest"]
    session = {"plan_hash": plan["plan_hash"], "organization_uid": org, "cemetery_uid": cem, "source_manifest_digest": src, "schema_version": "x", "data_epoch": 1}
    before = fingerprint(env)
    refused(env, post(url, "staging_open", [None, org, cem, bid, {**session, "plan_hash": "f" * 64}, {}], B), 400, "PLAN_INVALID", before)
    refused(env, post(url, "staging_open", [None, org, cem, bid, {**session, "source_manifest_digest": "0" * 64}, {}], B), 409, "SOURCE_DRIFT", before)
    refused(env, post(url, "staging_open", [None, org, cem, str(uuid.uuid4()), session, {}], B), 404, "SCOPE_MISMATCH", before)
    refused(env, post(url, "staging_put_batch", [None, org, cem, {**session, "bootstrap_id": str(uuid.uuid4())}, []], B), 404, "SCOPE_MISMATCH", before)
    expect = {**fb._expect(plan), "plan_hash": "f" * 64}
    refused(env, post(url, "publish_flow_b", [None, org, cem, bid, None, expect], B), 400, "PLAN_INVALID", before)
    refused(env, post(url, "publish_flow_b", [None, org, cem, bid, None, {**fb._expect(plan), "source_manifest_digest": "0" * 64}], B), 409, "SOURCE_DRIFT", before)
    assert post(url, "mark_verifying", [None, bid], B).status_code == 200
    before = fingerprint(env)
    refused(env, post(url, "complete_run", [None, bid], B), 409, "CONFLICT", before)  # B nu se poate „completa” fără publish atomic
    assert cem_state(env, cem) == ("DRAFT", 1) and db(env, "SELECT status FROM bindings") == [("pending",)]


def test_flow_a_server_side_checks_target_source_and_completion(renv):
    env = renv
    w = World(env)
    b = w.local(w.org1)
    code = env.cp.issue_code(w.owner1, b.organization_uid)
    _p, url, _ = start_server(env)
    rcp = ccl.RemoteControlPlane(url, code)
    plan = bs.plan_flow_a(b, rcp, code, env.inst, cemetery_name="A")
    P = f"Pairing {code}"
    forged = copy.deepcopy(pg.claim_for(plan))
    forged["fields"]["source_digest"] = {t: mf.sha([1]) for t in forged["fields"]["source_digest"]}  # sursă nevidă, hash recalculat
    before = fingerprint(env)
    refused(env, post(url, "begin_run", begin_args(env, plan, b.cemetery_uid, kind="A", plan_hash=mf.sha(forged["fields"])), P, {"plan": forged}),
            409, "PLAN_BLOCKED", before)
    env.cp.create_cemetery(w.owner1, b.organization_uid, b.cemetery_uid, "creat între plan și begin")
    env.cp.sim_set_status(b.cemetery_uid, "ACTIVE")
    before = fingerprint(env)
    refused(env, post(url, "begin_run", begin_args(env, plan, b.cemetery_uid, kind="A"), P, {"plan": pg.claim_for(plan)}), 409, "TARGET_DRIFT", before)
    honest = copy.deepcopy(pg.claim_for(plan))  # falsificare „consistentă”: ținta declarată = cea reală (ACTIVE + gol) => fluxul A tot e refuzat
    honest["fields"]["target"] = {"exists": True, "status": "ACTIVE", "rows": 0, "tombstones": 0}
    refused(env, post(url, "begin_run", begin_args(env, plan, b.cemetery_uid, kind="A", plan_hash=mf.sha(honest["fields"])), P, {"plan": honest}),
            409, "PLAN_BLOCKED", before)
    assert db(env, "SELECT COUNT(*) FROM bootstrap_runs")[0][0] == 0


def test_flow_a_complete_refused_if_target_no_longer_empty(renv):
    env = renv
    w = World(env)
    b = w.local(w.org1)
    code = env.cp.issue_code(w.owner1, b.organization_uid)
    _p, url, _ = start_server(env)
    rcp = ccl.RemoteControlPlane(url, code)
    plan = bs.plan_flow_a(b, rcp, code, env.inst)

    def hook(phase):
        if phase == "before_complete":
            env.cp.sim_put_row(b.cemetery_uid, ents.ORDER[0], str(uuid.uuid4()), {})  # cineva scrie în cloud după verificarea MAIN

    with pytest.raises(pg.TargetDrift):
        bs.execute_flow_a(b, rcp, env.store, plan, plan["plan_hash"], code, env.inst, hook=hook)
    assert cem_state(env, b.cemetery_uid)[0] == "DRAFT" and db(env, "SELECT state FROM bootstrap_runs") == [("rolled_back",)]
    assert not db(env, "SELECT 1 FROM bindings WHERE status='active'")


# ================================================================== IDEMPOTENȚĂ / CONTRACT
def test_begin_retry_with_same_valid_plan_is_idempotent(bsrv):
    env, w, b, code, url, rcp, plan, _ = bsrv
    args, kw = begin_args(env, plan, b.cemetery_uid), {"plan": pg.claim_for(plan)}
    r1, r2 = post(url, "begin_run", args, f"Pairing {code}", kw), post(url, "begin_run", args, f"Pairing {code}", kw)
    assert r1.status_code == r2.status_code == 200 and r1.json()["result"]["bootstrap_id"] == r2.json()["result"]["bootstrap_id"]
    assert db(env, "SELECT COUNT(*) FROM bootstrap_runs")[0][0] == 1
    before = fingerprint(env)
    refused(env, post(url, "begin_run", begin_args(env, plan, b.cemetery_uid, plan_hash="e" * 64), f"Pairing {code}", kw), 400, "PLAN_INVALID", before)
    rep = fb.execute_flow_b(b, rcp, env.store, w.owner1, plan, plan["plan_hash"], code, env.inst, batch_size=2, chunk_size=3)  # MAIN continuă același plan
    assert rep["result"] == "completed" and cem_state(env, b.cemetery_uid) == ("ACTIVE", 1) and db(env, "SELECT COUNT(*) FROM bootstrap_runs")[0][0] == 1


@pytest.mark.parametrize("exc,status,code", [(pg.PlanExpired("x"), 410, "PLAN_EXPIRED"), (pg.TargetDrift("x"), 409, "TARGET_DRIFT"),
                                             (pg.PlanBlocked("x"), 409, "PLAN_BLOCKED"), (pg.PlanRejected("x"), 400, "PLAN_INVALID")])
def test_plan_error_codes_are_stable_and_roundtrip(exc, status, code):
    r = ch._error(exc, "")
    assert r.status_code == status and wire.CODE_TO_CLASS[code] is type(exc)
