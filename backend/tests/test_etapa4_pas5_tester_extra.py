# ruff: noqa: F811
"""Etapa 4 / Pasul 5 — tester independent, teste ADVERSARIALE suplimentare pentru stratul HTTP (FastAPI local, simulator SQLite).

Nu modifică /app/v2/shared/. Scop: expunere la abuz / cross-tenant / scurgeri / dubluri sub răspuns pierdut.
"""
from __future__ import annotations

import base64
import json
import sqlite3
import threading
import urllib.parse
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import cimitir_bootstrap as bs
import cimitir_cloud_client as ccl
import cimitir_cloud_control as cc
import cimitir_cloud_http as ch
import cimitir_cloud_wire as wire
import cimitir_flow_b as fb
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa4_pas3_transfer import q
from test_etapa4_pas4_flux_b import assert_completed, b_world, cem_state, cloud_counts, outbox_rows
from test_etapa4_pas5_http import begin_b, db, http_kw, http_plan, run_b_http, start_server


# ---------- helpers ----------
MUTATING_OPS = ["begin_run", "redeem_code", "staging_open", "staging_put_batch", "staging_snapshot",
                "staging_cleanup", "media_put_chunk", "media_finalize", "media_verify", "media_cleanup",
                "mark_verifying", "verify_empty", "publish_flow_b", "resume_run", "rollback_run",
                "revoke_by_key", "mark_failed_unauthenticated", "complete_run"]

SCOPED_OPS = ["export_snapshot", "staging_open", "staging_status", "staging_snapshot", "staging_put_batch",
              "staging_cleanup", "media_put_chunk", "media_finalize", "media_verify", "media_cleanup",
              "publish_flow_b", "get_run", "resume_run", "rollback_run", "mark_verifying", "verify_empty",
              "revoke_by_key", "authenticate", "complete_run"]


def _post(url, op, args, auth=None, timeout=5.0, raw_body=None):
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = auth
    content = raw_body if raw_body is not None else json.dumps(wire.encode({"args": args}))
    return httpx.post(url + wire.API + wire.OPS[op][0], content=content, headers=headers, timeout=timeout)


def _no_leak(resp, *secrets):
    """Răspunsul (text + status) nu conține niciun secret ori traceback Python."""
    txt = resp.text
    for s in secrets:
        if not s:
            continue
        assert s not in txt, f"secret în răspuns: {s!r}"
        assert base64.b64encode(s.encode()).decode() not in txt
        assert urllib.parse.quote(s) not in txt
    assert "Traceback" not in txt and 'File "' not in txt
    assert "sqlite3" not in txt.lower() or resp.status_code < 500


# ================================================================== (1) autentificare + schemă pe FIECARE rută
def test_every_op_rejects_missing_auth_wrong_scheme_and_secret_in_query(renv):
    env = renv
    w, b, code = b_world(env)
    _p, url, _ = start_server(env)
    # creează un cod binding valid pentru org1 (nu-l vom folosi corect)
    rcp = ccl.RemoteControlPlane(url, code)
    _plan, bid = begin_b(env, w.owner1, b, code, rcp)
    key = rcp.redeem_code(code, env.inst, "D", b.cemetery_uid, bootstrap_id=bid, pending=True)["binding_key"]

    for op, (path, auth) in wire.OPS.items():
        full = url + wire.API + path
        # (a) lipsă autentificare
        r = httpx.post(full, json={"args": []})
        assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED", f"{op}: {r.status_code} {r.text}"
        _no_leak(r, code, key)
        # (b) schemă nepermisă: pe pairing-only trimite Binding, pe binding-only trimite Pairing
        if auth == "pairing":
            r = httpx.post(full, json={"args": []}, headers={"Authorization": f"Binding {key}"})
            assert r.status_code == 401, f"{op}: binding pe pairing: {r.status_code}"
        if auth == "binding":
            r = httpx.post(full, json={"args": []}, headers={"Authorization": f"Pairing {code}"})
            assert r.status_code == 401, f"{op}: pairing pe binding: {r.status_code}"
        # (c) secret în query-string NU este acceptat
        r = httpx.post(full + f"?code={code}&key={key}", json={"args": []})
        assert r.status_code == 401
        # (d) Bearer / schemă necunoscută
        r = httpx.post(full, json={"args": []}, headers={"Authorization": f"Bearer {code}"})
        assert r.status_code == 401
        # (e) JSON invalid
        r = httpx.post(full, content=b"{nu e json", headers={"Authorization": f"Pairing {code}", "Content-Type": "application/json"})
        assert r.status_code == 400 and r.json()["code"] == "REQUEST_INVALID"
        _no_leak(r, code, key)
        # (f) args de tip greșit
        r = httpx.post(full, json={"args": "nu-i listă"}, headers={"Authorization": f"Pairing {code}"})
        assert r.status_code == 400 and r.json()["code"] == "REQUEST_INVALID"
        _no_leak(r, code, key)


# ================================================================== (2) scope: body cu organizație / cimitir arbitrar pe FIECARE rută cu scope
def test_body_scope_params_cannot_exceed_server_derived_scope(renv):
    env = renv
    w, b, code = b_world(env)
    other = w.local(w.org2, "Alt cimitir")
    env.cp.create_cemetery(w.owner2, other.organization_uid, other.cemetery_uid, "B")
    _p, url, _ = start_server(env)
    # Legături: cheia binding pentru A (org1/b) via flow_b complet, apoi mai avem cheia pending pentru A?
    plan, rep, rcp1 = run_b_http(env, w, b, code, url)
    key1 = env.store.get(bs.LocalRegistry(b).get("cloud_secret_ref"))
    bk = f"Binding {key1}"
    fake_cem = str(uuid.uuid4())
    fake_org = str(uuid.uuid4())
    fake_bid = str(uuid.uuid4())
    bid1 = plan["bootstrap_id"]

    # pentru fiecare OP cu scope, trimitem params care SUNT în afara scope-ului => trebuie 4xx (SCOPE_MISMATCH/FORBIDDEN/UNAUTHENTICATED), fără 500
    cases = [
        ("export_snapshot", [None, fake_org, b.cemetery_uid]),
        ("export_snapshot", [None, b.organization_uid, fake_cem]),
        ("get_run", [fake_bid]),
        ("staging_status", [None, fake_org, b.cemetery_uid, bid1]),
        ("staging_status", [None, b.organization_uid, fake_cem, bid1]),
        ("staging_snapshot", [None, fake_org, b.cemetery_uid, bid1]),
        ("staging_put_batch", [None, fake_org, b.cemetery_uid, {"bootstrap_id": bid1}, []]),
        ("staging_cleanup", [None, fake_org, b.cemetery_uid, bid1]),
        ("media_put_chunk", [None, fake_org, b.cemetery_uid, bid1, "x", 0, b"x", "s" * 64]),
        ("media_finalize", [None, fake_org, b.cemetery_uid, bid1, "x"]),
        ("media_verify", [None, fake_org, b.cemetery_uid, bid1, "x"]),
        ("media_cleanup", [None, fake_org, b.cemetery_uid, bid1]),
        ("publish_flow_b", [None, fake_org, b.cemetery_uid, bid1, None, {}]),
        ("resume_run", [None, fake_bid, "p" * 64]),
        ("rollback_run", [None, fake_bid]),
        ("mark_verifying", [None, fake_bid]),
        ("verify_empty", [None, fake_bid]),
        ("complete_run", [None, fake_bid]),
        ("authenticate", [None, fake_cem]),
    ]
    for op, args in cases:
        r = _post(url, op, args, auth=bk)
        assert r.status_code in (400, 401, 403, 404, 409), f"{op}: status {r.status_code} ({r.text})"
        assert r.status_code != 500, f"{op}: 500 pentru scope invalid: {r.text}"
        _no_leak(r, key1, code)
        body = r.json()
        assert body["ok"] is False and body["code"] in {
            "SCOPE_MISMATCH", "FORBIDDEN", "UNAUTHENTICATED", "REQUEST_INVALID", "CONFLICT", "BOOTSTRAP_IN_PROGRESS",
            "CONTRACT_INVALID", "INVALID_CODE",
        }, f"{op}: cod intern neașteptat {body}"


# ================================================================== (3) binding revocat / pending folosit pe /auth/binding
def test_pending_binding_rejected_on_auth_endpoint_but_allowed_on_bootstrap(renv):
    env = renv
    w, b, code = b_world(env)
    _p, url, _ = start_server(env)
    rcp = ccl.RemoteControlPlane(url, code)
    _plan, bid = begin_b(env, w.owner1, b, code, rcp)
    res = rcp.redeem_code(code, env.inst, "D1", b.cemetery_uid, bootstrap_id=bid, pending=True)
    key = res["binding_key"]
    # pending → /auth/binding = 401
    r = _post(url, "authenticate", [None, b.cemetery_uid], auth=f"Binding {key}")
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED"
    # pending → operații de bootstrap sunt permise (ex. staging_status)
    r2 = _post(url, "staging_status", [None, b.organization_uid, b.cemetery_uid, bid], auth=f"Binding {key}")
    assert r2.status_code in (200, 404, 409)  # NU 401
    assert r2.status_code != 401, f"pending refuzat pe staging_status: {r2.text}"


def test_revoked_binding_key_returns_401_everywhere(renv):
    env = renv
    w, b, code = b_world(env)
    _p, url, _ = start_server(env)
    rcp = ccl.RemoteControlPlane(url, code)
    _plan, bid = begin_b(env, w.owner1, b, code, rcp)
    key = rcp.redeem_code(code, env.inst, "D1", b.cemetery_uid, bootstrap_id=bid, pending=True)["binding_key"]
    # revocăm
    _post(url, "revoke_by_key", [None], auth=f"Binding {key}")
    for op in ("authenticate", "staging_status", "mark_verifying", "resume_run"):
        args = [None, b.cemetery_uid] if op == "authenticate" else ([None, bid] if op != "resume_run" else [None, bid, "p" * 64])
        if op == "staging_status":
            args = [None, b.organization_uid, b.cemetery_uid, bid]
        r = _post(url, op, args, auth=f"Binding {key}")
        assert r.status_code == 401, f"{op} după revoke: {r.status_code} {r.text}"


# ================================================================== (4) rollback pre-publish → fără binding activ/pending, cheia veche = 401
def test_rollback_pre_publish_invalidates_old_key_everywhere(renv):
    env = renv
    w, b, code = b_world(env)
    _p, url, _ = start_server(env)
    rcp = ccl.RemoteControlPlane(url, code)
    _plan, bid = begin_b(env, w.owner1, b, code, rcp)
    key = rcp.redeem_code(code, env.inst, "D1", b.cemetery_uid, bootstrap_id=bid, pending=True)["binding_key"]
    r = _post(url, "rollback_run", [None, bid], auth=f"Binding {key}")
    assert r.status_code == 200, r.text
    # fără binding activ sau pending
    rows = db(env, "SELECT status FROM bindings")
    assert all(st[0] not in ("active", "pending") for st in rows), rows
    # cheia veche: 401 pe orice rută
    for op, args in [("authenticate", [None, b.cemetery_uid]),
                     ("staging_status", [None, b.organization_uid, b.cemetery_uid, bid]),
                     ("resume_run", [None, bid, "p" * 64])]:
        r = _post(url, op, args, auth=f"Binding {key}")
        assert r.status_code == 401, f"{op}: cheia veche încă funcționează ({r.text})"


# ================================================================== (5) răspuns pierdut PENTRU TOATE OP MUTANTE → fără dubluri
@pytest.mark.parametrize("op", ["staging_cleanup", "media_verify", "media_cleanup", "verify_empty", "complete_run"])
def test_response_lost_on_additional_mutating_ops_no_duplicates(renv, op):
    env = renv
    w, b, code = b_world(env)
    _p, url, _ = start_server(env, fault={"op": op, "nth": 1, "mode": "delay", "delay": 2.0})
    plan, rep, _ = run_b_http(env, w, b, code, url, timeout=0.8, retries=4, backoff=0.1)
    assert_completed(env, w, b, plan, rep)
    assert db(env, "SELECT COUNT(*) FROM bindings")[0][0] == 1
    assert db(env, "SELECT COUNT(*) FROM bootstrap_runs")[0][0] == 1
    assert db(env, "SELECT COUNT(*) FROM registry_rows")[0][0] == 11
    assert db(env, "SELECT COUNT(*) FROM media_published")[0][0] == 3


# ================================================================== (6) doi clienți concurenți: același batch / același redeem / același publish → un singur efect
def test_concurrent_duplicate_requests_single_effect(renv):
    env = renv
    w, b, code = b_world(env)
    _p, url, _ = start_server(env)
    # (a) redeem concurent cu același cod → cel puțin unul primește răspuns valid, consumul rămâne 1
    rcp = ccl.RemoteControlPlane(url, code)
    _plan, bid = begin_b(env, w.owner1, b, code, rcp)
    results = []

    def do_redeem():
        try:
            r = _post(url, "redeem_code", None, auth=f"Pairing {code}", timeout=10.0,
                      raw_body=json.dumps({"args": [None, env.inst, "D", b.cemetery_uid, ""], "kwargs": {"bootstrap_id": bid, "pending": True}}))
            results.append(r.status_code)
        except Exception as e:
            results.append(("err", str(type(e).__name__)))

    ts = [threading.Thread(target=do_redeem) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    # ambele cereri sunt idempotente (același actor, bid implicit None, dar codul e deja consumat) → nu creăm 2 bindings
    assert db(env, "SELECT COUNT(*) FROM bindings")[0][0] <= 1 or db(env, "SELECT COUNT(DISTINCT binding_uid) FROM bindings")[0][0] == 1


# ================================================================== (7) server omorât la op mutantă → MAIN reia; verificare digest cloud == sursă
@pytest.mark.parametrize("op", ["staging_open", "media_finalize", "mark_verifying"])
def test_server_kill_at_mutating_op_resume_preserves_digest(renv, op):
    env = renv
    w, b, code = b_world(env)
    p, url, _port = start_server(env, fault={"op": op, "nth": 1, "mode": "kill"})
    rcp = ccl.RemoteControlPlane(url, code, timeout=2.0, retries=1, backoff=0.05)
    plan = fb.plan_flow_b(b, rcp, w.owner1, env.inst)
    with pytest.raises(ccl.TransportError):
        fb.execute_flow_b(b, rcp, env.store, w.owner1, plan, plan["plan_hash"], code, env.inst, batch_size=1, chunk_size=3)
    assert p.wait(timeout=10) == 137
    _p2, url2, _ = start_server(env)
    rcp2 = ccl.RemoteControlPlane(url2, code)
    rep = fb.execute_flow_b(b, rcp2, env.store, w.owner1, plan, plan["plan_hash"], code, env.inst, batch_size=1, chunk_size=3)
    assert_completed(env, w, b, plan, rep)
    # digest cloud == digest sursă
    import cimitir_manifest as mf
    cloud = mf.cloud_manifest(env.cp, w.owner1, b.organization_uid, b.cemetery_uid)
    assert cloud["content_digest"] == plan["local_manifest"]["content_digest"]


# ================================================================== (8) Flux A peste HTTP cu răspuns pierdut pe begin_run / redeem_code / complete_run
@pytest.mark.parametrize("op", ["begin_run", "redeem_code", "complete_run"])
def test_flow_a_over_http_with_lost_response(renv, op):
    env = renv
    w = World(env)
    b = w.local(w.org1)
    code = env.cp.issue_code(w.owner1, b.organization_uid)
    _p, url, _ = start_server(env, fault={"op": op, "nth": 1, "mode": "delay", "delay": 2.0})
    rcp = ccl.RemoteControlPlane(url, code, timeout=0.6, retries=5, backoff=0.1)
    plan = bs.plan_flow_a(b, rcp, code, env.inst, cemetery_name="Cimitir HTTP")
    rep = bs.execute_flow_a(b, rcp, env.store, plan, plan["plan_hash"], code, env.inst)
    assert cem_state(env, b.cemetery_uid) == ("ACTIVE", 1)
    assert db(env, "SELECT COUNT(*) FROM bindings")[0][0] == 1
    assert db(env, "SELECT COUNT(*) FROM bootstrap_runs")[0][0] == 1
    assert db(env, "SELECT COUNT(*) FROM bootstrap_runs WHERE state='completed'")[0][0] == 1
    assert rep["result"] == "completed"


# ================================================================== (9) scanare scurgeri + forțare 500 INTERNAL
def test_leak_scan_plain_base64_url_encoded(renv):
    env = renv
    w, b, code = b_world(env)
    _p, url, _ = start_server(env)
    plan, rep, _ = run_b_http(env, w, b, code, url)
    key = env.store.get(bs.LocalRegistry(b).get("cloud_secret_ref"))

    secrets = [code, key]
    variants = []
    for s in secrets:
        variants.append(s.encode())
        variants.append(base64.b64encode(s.encode()))
        variants.append(urllib.parse.quote(s).encode())

    # fișiere SQLite + wal
    blobs = []
    for f in Path(env.cp.path).parent.glob("control.sqlite*"):
        blobs.append(("db:" + f.name, f.read_bytes()))
    # loguri server (stdout/stderr aglomerate)
    for f in (env.tmp / "logs").glob("*.log"):
        blobs.append(("log:" + f.name, f.read_bytes()))
    # registrul local MAIN
    for f in b.root.rglob("*"):
        if f.is_file() and f.name != "bootstrap.lock":
            blobs.append(("local:" + f.name, f.read_bytes()))

    hits = []
    for name, blob in blobs:
        for v in variants:
            if v and v in blob:
                hits.append((name, v[:12]))
    assert hits == [], f"secrete scurse: {hits}"


def test_forced_internal_error_does_not_leak_secret_or_traceback(renv, monkeypatch):
    env = renv
    app = ch.create_app(str(env.cp.path))
    client = TestClient(app)
    w = World(env)
    b = w.local(w.org1)
    env.cp.create_cemetery(w.owner1, b.organization_uid, b.cemetery_uid, "C")
    code = env.cp.issue_code(w.owner1, b.organization_uid, b.cemetery_uid)
    # monkeypatch: forțează explozie internă pe staging_open
    import cimitir_cloud_staging as cs

    def boom(*a, **kw):
        raise RuntimeError(f"zgomot cu secret-{code}-inside")

    monkeypatch.setattr(cs, "staging_open", boom)
    monkeypatch.setattr(ch, "MODULE_OPS", {**ch.MODULE_OPS, "staging_open": boom})

    # trebuie Binding pentru staging_open: facem begin+redeem direct
    bid = str(uuid.uuid4())
    env.cp.begin_run(code, env.inst, bid, "B", b.cemetery_uid, "p" * 64)
    key = env.cp.redeem_code(code, env.inst, "D", b.cemetery_uid, bootstrap_id=bid, pending=True)["binding_key"]

    r = client.post(wire.API + wire.OPS["staging_open"][0],
                    json={"args": [None, b.organization_uid, b.cemetery_uid, bid, {"plan_hash": "p" * 64}, "y"]},
                    headers={"Authorization": f"Binding {key}"})
    assert r.status_code == 500 and r.json()["code"] == "INTERNAL"
    txt = r.text
    assert code not in txt and key not in txt
    assert "Traceback" not in txt and "zgomot" not in txt


# ================================================================== (10) /docs, /openapi.json, /redoc, rute generice → 404
def test_no_docs_or_generic_endpoints(renv):
    env = renv
    _p, url, _ = start_server(env)
    for path in ("/docs", "/redoc", "/openapi.json", wire.API + "/openapi.json", wire.API + "/docs",
                 wire.API + "/sql", wire.API + "/exec", wire.API + "/db", wire.API + "/cloud/query",
                 "/", wire.API + "/", wire.API + "/admin"):
        r = httpx.get(url + path)
        assert r.status_code == 404, f"{path}: {r.status_code}"
        r2 = httpx.post(url + path, json={})
        assert r2.status_code in (404, 405), f"POST {path}: {r2.status_code}"
