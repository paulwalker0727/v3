# ruff: noqa: F811
"""Etapa 4 / Pasul 5.1 — tester independent, teste ADVERSARIALE suplimentare pentru validarea PE SERVER a planului.

Scop: expunere la bypass-uri noi, NEcoperite în suita de bază:
- claim valid dar pentru alt scope (alt org / alt cimitir)
- replay al unui claim valid cu bootstrap_id nou după un bootstrap deja finalizat
- plan_hash dintr-un plan X cu claim-ul (fields) dintr-un plan Y
- staging/media writes după rollback explicit
- publish cu cheia altui binding (alt scope)
- tipuri malformate / exotice ale claim-ului (list, string, nested, foarte mare, cu caractere non-ASCII)
- timestamps negative / NaN / infinit / stringuri

NU modifică /app/shared/. Toate testele folosesc simulatorul SQLite + FastAPI local.
"""
from __future__ import annotations

import copy
import time
import uuid

import httpx
import pytest

import cimitir_cloud_client as ccl
import cimitir_cloud_wire as wire
import cimitir_flow_b as fb
import cimitir_manifest as mf
import cimitir_plan_guard as pg
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa4_pas4_flux_b import DATA, b_world, cem_state, cloud_counts, put_local
from test_etapa4_pas5_http import begin_b, db, start_server
from test_etapa4_pas51_server_validation import begin_args, fingerprint, post


# ================================================================== REPLAY / CROSS-SCOPE CU CLAIM VALID
def test_valid_claim_of_other_org_cannot_begin_in_our_cemetery(renv):
    """Claim-ul (fields+local_manifest+plan_hash) e perfect valid pentru cimitirul B al org2, dar codul folosit la auth e al org1 pe cimitirul A.
    Serverul trebuie să respingă pe baza scope-ului codului de pairing (SCOPE_MISMATCH), fără nicio scriere."""
    env = renv
    w, bA, codeA = b_world(env)
    bB = w.local(w.org2, "Cimitir B")
    env.cp.create_cemetery(w.owner2, bB.organization_uid, bB.cemetery_uid, "B")
    put_local(bB, DATA)
    codeB = env.cp.issue_code(w.owner2, bB.organization_uid, bB.cemetery_uid)
    _p, url, _ = start_server(env)
    rcpB = ccl.RemoteControlPlane(url, codeB)
    planB = fb.plan_flow_b(bB, rcpB, w.owner2, env.inst)
    claimB = pg.claim_for(planB)
    before = fingerprint(env)
    # încercăm begin pe codul lui A, dar cu claim-ul lui B și plan_hash-ul lui B pe cimitirul lui B
    r = post(url, "begin_run", [None, env.inst, planB["bootstrap_id"], "B", bB.cemetery_uid, planB["plan_hash"]],
             f"Pairing {codeA}", {"plan": claimB})
    assert r.status_code in (403, 404) and r.json()["code"] in ("SCOPE_MISMATCH", "FORBIDDEN"), r.text
    assert fingerprint(env) == before
    # și cu scope-ul = cimitirul lui A dar claim-ul lui B (fields îl trădează): PLAN_INVALID / SCOPE_MISMATCH
    r2 = post(url, "begin_run", [None, env.inst, planB["bootstrap_id"], "B", bA.cemetery_uid, planB["plan_hash"]],
              f"Pairing {codeA}", {"plan": claimB})
    assert r2.status_code in (400, 404) and r2.json()["code"] in ("PLAN_INVALID", "SCOPE_MISMATCH"), r2.text
    assert fingerprint(env) == before and db(env, "SELECT COUNT(*) FROM bootstrap_runs")[0][0] == 0


def test_claim_replay_with_new_bootstrap_id_after_completed_bootstrap(renv):
    """După ce un bootstrap B s-a finalizat (binding activ, cloud ACTIVE), retrimiterea aceluiași claim cu un bootstrap_id nou (ca și cum ar fi plan proaspăt)
    nu trebuie să mai inițieze un alt bootstrap: codul e consumat (401) sau ținta e ACTIVE (TARGET_DRIFT / PLAN_BLOCKED)."""
    env = renv
    w, b, code = b_world(env)
    _p, url, _ = start_server(env)
    rcp = ccl.RemoteControlPlane(url, code)
    plan = fb.plan_flow_b(b, rcp, w.owner1, env.inst)
    fb.execute_flow_b(b, rcp, env.store, w.owner1, plan, plan["plan_hash"], code, env.inst, batch_size=2, chunk_size=3)
    assert cem_state(env, b.cemetery_uid) == ("ACTIVE", 1)
    # retrimitem claim-ul VECHI cu bootstrap_id NOU: codul original e consumat
    r = post(url, "begin_run", [None, env.inst, str(uuid.uuid4()), "B", b.cemetery_uid, plan["plan_hash"]],
             f"Pairing {code}", {"plan": pg.claim_for(plan)})
    assert r.status_code == 401, r.text
    # emitem un cod proaspăt (same scope); același claim trebuie respins fiindcă ținta nu mai e goală/DRAFT (sau clasificarea cloud e NONEMPTY)
    code2 = env.cp.issue_code(w.owner1, b.organization_uid, b.cemetery_uid)
    before = fingerprint(env)
    r2 = post(url, "begin_run", [None, env.inst, str(uuid.uuid4()), "B", b.cemetery_uid, plan["plan_hash"]],
              f"Pairing {code2}", {"plan": pg.claim_for(plan)})
    assert r2.status_code == 409 and r2.json()["code"] in ("TARGET_DRIFT", "PLAN_BLOCKED"), r2.text
    assert fingerprint(env) == before
    assert db(env, "SELECT COUNT(*) FROM bootstrap_runs")[0][0] == 1


# ================================================================== PLAN X cu PLAN_HASH DIN PLAN Y
def test_mix_claim_of_plan_x_with_plan_hash_of_plan_y(renv):
    """Două planuri distincte X și Y (fields diferite). Clientul trimite fields-ul lui X dar plan_hash-ul lui Y.
    Trebuie respins: PLAN_INVALID (sha(fields) != declared plan_hash)."""
    env = renv
    w, b, code = b_world(env)
    _p, url, _ = start_server(env)
    rcp = ccl.RemoteControlPlane(url, code)
    planX = fb.plan_flow_b(b, rcp, w.owner1, env.inst)
    # construim planY ca o copie cu fields modificate (ex: data_epoch diferit)
    claimX = pg.claim_for(planX)
    fieldsY = {**claimX["fields"], "data_epoch": (claimX["fields"].get("data_epoch", 1) or 1) + 42}
    planY_hash = mf.sha(fieldsY)
    assert planY_hash != planX["plan_hash"]
    before = fingerprint(env)
    # fields = X dar plan_hash = Y
    r = post(url, "begin_run", [None, env.inst, str(uuid.uuid4()), "B", b.cemetery_uid, planY_hash],
             f"Pairing {code}", {"plan": claimX})
    assert r.status_code == 400 and r.json()["code"] == "PLAN_INVALID", r.text
    assert fingerprint(env) == before and db(env, "SELECT COUNT(*) FROM bootstrap_runs")[0][0] == 0


# ================================================================== STAGING / MEDIA / PUBLISH DUPĂ ROLLBACK
def test_staging_and_media_writes_after_rollback_are_refused(renv):
    """După rollback_run, cheia de binding încă există (va fi revocată), însă orice scriere pe staging/media/publish trebuie refuzată."""
    env = renv
    w, b, code = b_world(env)
    _p, url, _ = start_server(env)
    rcp = ccl.RemoteControlPlane(url, code)
    plan, bid = begin_b(env, w.owner1, b, code, rcp)
    key = rcp.redeem_code(code, env.inst, "MAIN", b.cemetery_uid, bootstrap_id=bid, pending=True)["binding_key"]
    B = f"Binding {key}"
    # rollback explicit
    rb = httpx.post(url + wire.API + wire.OPS["rollback_run"][0], json=wire.encode({"args": [None, bid], "kwargs": {}}), headers={"Authorization": B}, timeout=10)
    assert rb.status_code == 200, rb.text
    assert db(env, "SELECT state FROM bootstrap_runs") == [("rolled_back",)]
    before = fingerprint(env)
    org, cem, src = b.organization_uid, b.cemetery_uid, plan["local_manifest"]["content_digest"]
    session = {"plan_hash": plan["plan_hash"], "organization_uid": org, "cemetery_uid": cem, "source_manifest_digest": src, "schema_version": "x", "data_epoch": 1}
    # orice scriere pe binding-ul rolled_back trebuie să fie 401 (cheie revocată) sau 409 (state greșit)
    for op, args in [("staging_open", [None, org, cem, bid, session, {}]),
                     ("staging_put_batch", [None, org, cem, {**session, "bootstrap_id": bid}, []]),
                     ("media_put_chunk", [None, org, cem, bid, "obj", 0, b""]),
                     ("media_finalize", [None, org, cem, bid, "obj", "d" * 64, 0]),
                     ("publish_flow_b", [None, org, cem, bid, None, fb._expect(plan)]),
                     ("mark_verifying", [None, bid])]:
        r = post(url, op, args, B)
        assert r.status_code in (401, 409) and r.json()["code"] in ("UNAUTHENTICATED", "CONFLICT", "SCOPE_MISMATCH", "PLAN_INVALID"), (op, r.text)
    assert fingerprint(env) == before
    assert cem_state(env, cem) == ("DRAFT", 1) and cloud_counts(env, cem)["rows"] == 0


# ================================================================== PUBLISH CU CHEIA ALTUI BINDING
def test_publish_with_other_bindings_key_refused_crossbinding(renv):
    """Avem două bootstrap-uri B pe aceeași instalare, dar pe cimitire diferite. Cheia binding-ului A folosită pentru publish/staging pe binding-ul B => 404/403."""
    env = renv
    w, bA, codeA = b_world(env)
    bB = w.local(w.org2, "Cim B")
    env.cp.create_cemetery(w.owner2, bB.organization_uid, bB.cemetery_uid, "B")
    put_local(bB, DATA)
    codeB = env.cp.issue_code(w.owner2, bB.organization_uid, bB.cemetery_uid)
    _p, url, _ = start_server(env)
    rcpA = ccl.RemoteControlPlane(url, codeA)
    rcpB = ccl.RemoteControlPlane(url, codeB)
    planA, bidA = begin_b(env, w.owner1, bA, codeA, rcpA)
    keyA = rcpA.redeem_code(codeA, env.inst, "MAIN-A", bA.cemetery_uid, bootstrap_id=bidA, pending=True)["binding_key"]
    planB, bidB = begin_b(env, w.owner2, bB, codeB, rcpB)
    _keyB = rcpB.redeem_code(codeB, env.inst, "MAIN-B", bB.cemetery_uid, bootstrap_id=bidB, pending=True)["binding_key"]
    BA = f"Binding {keyA}"
    before = fingerprint(env)
    # publish pentru binding-ul B folosind cheia lui A: 404 SCOPE_MISMATCH
    r = post(url, "publish_flow_b", [None, bB.organization_uid, bB.cemetery_uid, bidB, None, fb._expect(planB)], BA)
    assert r.status_code in (403, 404) and r.json()["code"] in ("SCOPE_MISMATCH", "FORBIDDEN"), r.text
    # staging_put_batch cu bootstrap-ul lui B dar cheia lui A
    r2 = post(url, "staging_put_batch", [None, bB.organization_uid, bB.cemetery_uid,
                                         {"plan_hash": planB["plan_hash"], "organization_uid": bB.organization_uid, "cemetery_uid": bB.cemetery_uid,
                                          "source_manifest_digest": planB["local_manifest"]["content_digest"], "schema_version": "x", "data_epoch": 1,
                                          "bootstrap_id": bidB}, []], BA)
    assert r2.status_code in (403, 404) and r2.json()["code"] in ("SCOPE_MISMATCH", "FORBIDDEN"), r2.text
    assert fingerprint(env) == before


# ================================================================== TIPURI MALFORMATE / EXOTICE PE CLAIM
MALFORMED_CLAIMS = [
    ("list_claim", [1, 2, 3]),
    ("string_claim", "nu e dict"),
    ("int_claim", 42),
    ("null_claim", None),
    ("bool_claim", True),
    ("empty_dict", {}),
    ("fields_as_list", {"kind": "B", "fields": [1, 2], "created_at": 0, "expires_at": 999999999999}),
    ("fields_as_string", {"kind": "B", "fields": "xxx", "created_at": 0, "expires_at": 999999999999}),
    ("kind_mismatch_in_claim", {"kind": "A", "fields": {}, "created_at": 0, "expires_at": 999999999999}),
    ("created_at_string", {"kind": "B", "fields": {}, "created_at": "0", "expires_at": 1}),
    ("expires_at_bool", {"kind": "B", "fields": {}, "created_at": 0, "expires_at": True}),
    ("ttl_negative", {"kind": "B", "fields": {}, "created_at": 1000, "expires_at": 500}),
    ("huge_fields", {"kind": "B", "fields": {"garbage": "A" * 100000}, "created_at": 0, "expires_at": 999999999999}),
    ("nested_fields", {"kind": "B", "fields": {"x": {"y": {"z": [1, {"a": "b"}]}}}, "created_at": 0, "expires_at": 999999999999}),
    ("unicode_fields", {"kind": "B", "fields": {"org": "org-ţ-ă-💀"}, "created_at": 0, "expires_at": 999999999999}),
]


@pytest.mark.parametrize("name,claim", MALFORMED_CLAIMS, ids=[c[0] for c in MALFORMED_CLAIMS])
def test_malformed_claim_types_refused_without_writes(renv, name, claim):
    env = renv
    w, b, code = b_world(env)
    _p, url, _ = start_server(env)
    before = fingerprint(env)
    r = post(url, "begin_run", [None, env.inst, str(uuid.uuid4()), "B", b.cemetery_uid, "a" * 64],
             f"Pairing {code}", {"plan": claim})
    assert r.status_code in (400, 404, 409, 422) and r.json().get("ok") is False, (name, r.text)
    assert r.json().get("code") in ("PLAN_INVALID", "SCOPE_MISMATCH", "REQUEST_INVALID", "CONTRACT_INVALID"), (name, r.text)
    assert fingerprint(env) == before
    assert db(env, "SELECT COUNT(*) FROM bootstrap_runs")[0][0] == 0


# ================================================================== TIMESTAMPS NEGATIVE / NaN / INFINIT
TS_CASES = [
    ("negative_created", lambda now: {"created_at": -1e9, "expires_at": -1e9 + 100}),
    ("negative_expires", lambda now: {"created_at": now, "expires_at": -1}),
    ("nan_created", lambda now: {"created_at": float("nan"), "expires_at": now + 100}),
    ("nan_expires", lambda now: {"created_at": now, "expires_at": float("nan")}),
    ("inf_expires", lambda now: {"created_at": now, "expires_at": float("inf")}),
    ("neg_inf_created", lambda now: {"created_at": float("-inf"), "expires_at": now + 100}),
    ("zero_window", lambda now: {"created_at": now, "expires_at": now}),  # expirat (now > expires)
    ("far_future", lambda now: {"created_at": now + 10 ** 12, "expires_at": now + 10 ** 12 + 100}),
]


@pytest.mark.parametrize("name,maker", TS_CASES, ids=[c[0] for c in TS_CASES])
def test_bizarre_timestamps_refused(renv, name, maker):
    env = renv
    w, b, code = b_world(env)
    _p, url, _ = start_server(env)
    rcp = ccl.RemoteControlPlane(url, code)
    plan = fb.plan_flow_b(b, rcp, w.owner1, env.inst)
    claim = copy.deepcopy(pg.claim_for(plan))
    ts = maker(time.time())
    # putem avea NaN/Inf care nu e serializabil JSON standard: filtrăm acele cazuri
    try:
        import json as _j
        _j.dumps(ts, allow_nan=False)
        skippable = False
    except (ValueError, TypeError):
        skippable = True
    claim["created_at"] = ts["created_at"]
    claim["expires_at"] = ts["expires_at"]
    before = fingerprint(env)
    try:
        r = post(url, "begin_run", begin_args(env, plan, b.cemetery_uid), f"Pairing {code}", {"plan": claim})
    except Exception as e:
        # NaN/Inf — httpx/json pot refuza la client. Acceptăm asta ca „refuz înainte de scriere”.
        assert skippable, f"excepție neașteptată: {e}"
        assert fingerprint(env) == before
        return
    assert r.status_code in (400, 410) and r.json()["code"] in ("PLAN_INVALID", "PLAN_EXPIRED", "REQUEST_INVALID"), (name, r.text)
    assert fingerprint(env) == before


# ================================================================== SCOPE DIN FIELDS INJECTAT (fields.cemetery_uid != scope)
def test_fields_scope_injection_refused(renv):
    """Claim-ul declară fields.organization_uid / fields.cemetery_uid pentru un alt cimitir, chiar dacă URL-ul și codul sunt pentru A."""
    env = renv
    w, b, code = b_world(env)
    _p, url, _ = start_server(env)
    rcp = ccl.RemoteControlPlane(url, code)
    plan = fb.plan_flow_b(b, rcp, w.owner1, env.inst)
    before = fingerprint(env)
    for key, bad in [("organization_uid", str(uuid.uuid4())), ("cemetery_uid", str(uuid.uuid4())), ("installation_uid", str(uuid.uuid4()))]:
        claim = copy.deepcopy(pg.claim_for(plan))
        claim["fields"][key] = bad
        # recalculăm hash-ul ca atacatorul să nu fie prins pe sha mismatch; vrem să vedem că scope-ul oprește
        bad_hash = mf.sha(claim["fields"])
        r = post(url, "begin_run", [None, env.inst, plan["bootstrap_id"], "B", b.cemetery_uid, bad_hash], f"Pairing {code}", {"plan": claim})
        assert r.status_code in (400, 404) and r.json()["code"] in ("PLAN_INVALID", "SCOPE_MISMATCH"), (key, r.text)
    assert fingerprint(env) == before


# ================================================================== KWARGS LIPSĂ / NEDICȚIONAR
def test_begin_without_kwargs_is_refused(renv):
    env = renv
    w, b, code = b_world(env)
    _p, url, _ = start_server(env)
    before = fingerprint(env)
    # fără kwargs deloc
    r = httpx.post(url + wire.API + wire.OPS["begin_run"][0], json={"args": [None, env.inst, str(uuid.uuid4()), "B", b.cemetery_uid, "a" * 64]},
                   headers={"Authorization": f"Pairing {code}"}, timeout=10)
    assert r.status_code == 400 and r.json()["code"] == "PLAN_INVALID", r.text
    assert fingerprint(env) == before


# ================================================================== REDEEM BOOTSTRAP_ID NON-UUID / NEGATIV / HUGE
# NB: cu `[1,2]` ca bootstrap_id serverul întoarce 500 INTERNAL (nu 400) — vezi raportul; secretul NU se scurge, însă idealul ar fi REQUEST_INVALID.
@pytest.mark.parametrize("bid", ["", "nu-e-uuid", "../etc/passwd", "A" * 2000, 12345, None, True])
def test_redeem_with_invalid_bootstrap_id_types(renv, bid):
    env = renv
    w, b, code = b_world(env)
    _p, url, _ = start_server(env)
    before = fingerprint(env)
    r = post(url, "redeem_code", [None, env.inst, "MAIN", b.cemetery_uid, ""], f"Pairing {code}",
             {"bootstrap_id": bid, "pending": True})
    assert r.status_code in (400, 404) and r.json()["code"] in ("PLAN_INVALID", "SCOPE_MISMATCH", "REQUEST_INVALID", "CONTRACT_INVALID"), r.text
    assert fingerprint(env) == before


def test_redeem_with_list_bootstrap_id_does_not_leak_or_write(renv):
    """Bug raportat de tester (500 la bootstrap_id=[1,2]) — corectat: tipul bootstrap_id se validează înaintea oricărei interogări => 400 PLAN_INVALID."""
    env = renv
    w, b, code = b_world(env)
    _p, url, _ = start_server(env)
    before = fingerprint(env)
    for op, args, kw in (("redeem_code", [None, env.inst, "MAIN", b.cemetery_uid, ""], {"bootstrap_id": [1, 2], "pending": True}),
                         ("get_run", [[1, 2]], {}), ("mark_failed_unauthenticated", [{"x": 1}, env.inst, "e"], {})):
        r = post(url, op, args, f"Pairing {code}", kw)
        assert fingerprint(env) == before, "scriere la input malformat"
        assert code not in r.text and "Traceback" not in r.text
        assert (r.status_code, r.json()["code"]) == (400, "PLAN_INVALID"), (op, r.text)


# ================================================================== CEAS SERVER: CREAT ÎN VIITOR peste skew
def test_claim_created_too_far_in_future_refused(renv):
    env = renv
    w, b, code = b_world(env)
    _p, url, _ = start_server(env)
    rcp = ccl.RemoteControlPlane(url, code)
    plan = fb.plan_flow_b(b, rcp, w.owner1, env.inst)
    claim = copy.deepcopy(pg.claim_for(plan))
    now = time.time()
    claim["created_at"] = now + pg.MAX_CLOCK_SKEW + 60
    claim["expires_at"] = claim["created_at"] + 10
    before = fingerprint(env)
    r = post(url, "begin_run", begin_args(env, plan, b.cemetery_uid), f"Pairing {code}", {"plan": claim})
    assert r.status_code in (400, 410) and r.json()["code"] in ("PLAN_INVALID", "PLAN_EXPIRED"), r.text
    assert fingerprint(env) == before
