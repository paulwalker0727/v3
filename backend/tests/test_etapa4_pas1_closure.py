"""Etapa 4 / Pasul 1 — ÎNCHIDERE: procese reale (lock între procese, kill -9 + resume/rollback, offline la nivel de kernel), ACTIVE+gol fail-closed,
SecretStore de test fără secret în SQLite/loguri/backup. Numai foldere temporare + control-plane SQLite local. Nu atinge Supabase/Render/Banovița.
DPAPI real și `_pid_alive` pe Windows NU se pot rula aici: vezi `scripts/windows_verify_step1.py` (rulat pe Windows).
"""
from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from test_etapa4_pas1 import World, env, insert_any_row  # noqa: F401  (fixture `env` reutilizat)

HERE = Path(__file__).resolve().parent
WORKER = HERE / "etapa4_worker.py"
sys.path.insert(0, str(HERE))
PHASES = ["begin", "after_begin", "after_redeem", "after_secret", "redeemed", "verifying", "before_complete", "after_cloud_complete"]


# ------------------------------------------------------------------ infrastructură
@pytest.fixture()
def renv(env, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("CIMITIR_SECRET_STORE", "test-encrypted")
    monkeypatch.setenv("CIMITIR_TEST_SECRET_KEY", Fernet.generate_key().decode())
    env.cp = env.cc.LocalControlPlane(env.tmp / "cloud" / "control.sqlite")  # ceas real (procesele copil nu pot folosi ceasul fals)
    env.store = env.ss.default_store(env.data)
    env.procs = []
    yield env
    for p in env.procs:
        if p.poll() is None:
            p.kill()
            p.wait()


def make_plan(env, w, b, owner=None, org=None, name="Cimitir A"):
    code = env.cp.issue_code(owner or w.owner1, org or b.organization_uid)
    plan = env.bs.plan_flow_a(b, env.cp, code, env.inst, cemetery_name=name)
    f = env.tmp / f"plan-{plan['bootstrap_id']}.json"
    f.write_text(json.dumps({"plan": plan, "code": code}), encoding="utf-8")
    return plan, code, f


def spawn(env, tag, b, action="execute", plan_file=None, **kw):
    args = {"action": action, "data_dir": str(env.data), "cloud_db": str(env.cp.path), "cemetery_uid": b.cemetery_uid,
            "plan_file": str(plan_file) if plan_file else None, "marker_dir": str(env.tmp / "markers"), "tag": tag, **kw}
    (env.tmp / "logs").mkdir(exist_ok=True)
    err = open(env.tmp / "logs" / f"{tag}.err.log", "w")
    p = subprocess.Popen([sys.executable, str(WORKER), json.dumps(args)], stdout=subprocess.PIPE, stderr=err, env=os.environ.copy(), text=True)
    p._tag, p._env = tag, env
    env.procs.append(p)
    return p


def finish(p, timeout=90):
    out, _ = p.communicate(timeout=timeout)
    (p._env.tmp / "logs" / f"{p._tag}.out.log").write_text(out or "", encoding="utf-8")
    res = None
    for line in (out or "").splitlines():
        if line.startswith("RESULT "):
            res = json.loads(line[7:])
    return p.returncode, res


def marker(env, tag, phase):
    return env.tmp / "markers" / f"{tag}.{phase}"


def wait_marker(env, tag, phase, timeout=60):
    end = time.time() + timeout
    while not marker(env, tag, phase).exists():
        assert time.time() < end, f"marker {tag}.{phase} lipsă"
        time.sleep(0.02)


def kill9(p):
    os.kill(p.pid, signal.SIGKILL)
    p.wait()
    assert p.returncode == -signal.SIGKILL


def q(path, sql, *args):
    c = sqlite3.connect(path)
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def secret_of(env, b):
    L = env.bs.LocalRegistry(b)
    ref = L.get("cloud_secret_ref")
    return env.store.get(ref) if ref else None


def assert_no_secret_anywhere(env, secret):
    """Secretul (și base64-ul lui) nu apare în NICIUN fișier de sub folderul de test: SQLite (+WAL), loguri, backup-uri, control-plane, secrets/."""
    import base64
    needles = [secret.encode(), base64.b64encode(secret.encode()), base64.urlsafe_b64encode(secret.encode())]
    seen = 0
    for f in env.tmp.rglob("*"):
        if f.is_file() and not f.name.startswith("plan-"):
            blob = f.read_bytes()
            seen += 1
            for n in needles:
                assert n not in blob, f"secret în {f}"
    assert seen > 5


def assert_coherent_bound(env, b, plan, org_catalog_before):
    L = env.bs.LocalRegistry(b)
    cem = b.cemetery_uid
    assert L.sync_state == "BOUND" and L.get("bound_installation_uid") == env.inst
    runs = q(b.database, "SELECT state FROM bootstrap_runs WHERE bootstrap_id=?", plan["bootstrap_id"])
    assert runs == [("completed",)]
    assert not q(b.database, "SELECT 1 FROM bootstrap_runs WHERE state IN ('running')")
    cdb = env.cp.path
    assert q(cdb, "SELECT COUNT(*) FROM cemeteries WHERE uid=?", cem) == [(1,)]
    assert q(cdb, "SELECT COUNT(*) FROM organizations WHERE uid=?", b.organization_uid) == [(1,)]
    assert q(cdb, "SELECT COUNT(*) FROM devices WHERE organization_uid=? AND installation_uid=?", b.organization_uid, env.inst) == [(1,)]
    assert q(cdb, "SELECT COUNT(*) FROM bindings WHERE cemetery_uid=?", cem) == [(1,)]
    assert q(cdb, "SELECT status FROM bindings WHERE cemetery_uid=?", cem) == [("active",)]
    assert q(cdb, "SELECT state FROM bootstrap_runs WHERE cemetery_uid=?", cem) == [("completed",)]
    assert q(cdb, "SELECT status, data_epoch FROM cemeteries WHERE uid=?", cem) == [("ACTIVE", 1)]
    assert (len(env.cat.list_organizations(include_inactive=True)), len(env.cat.list_cemeteries(include_archived=True))) == org_catalog_before
    key = secret_of(env, b)
    assert key, "secretul s-a pierdut"
    auth = env.cp.authenticate(key, cem)
    assert auth["binding_uid"] == L.get("cloud_binding_uid")
    assert not env.bs.lock_held(b) and env.bs.lock_info(b) is None
    return key


def catalog_counts(env):
    return len(env.cat.list_organizations(include_inactive=True)), len(env.cat.list_cemeteries(include_archived=True))


# ================================================================== 3. LOCK ÎNTRE PROCESE
def test_cross_process_lock_exactly_one_holder_second_refused(renv):
    env = renv
    w = World(env)
    b = w.local(w.org1)
    before = catalog_counts(env)
    p1, _, f1 = make_plan(env, w, b)
    p2, _, f2 = make_plan(env, w, b)
    rel = env.tmp / "release"
    w1 = spawn(env, "w1", b, plan_file=f1, pause_at="begin", release_file=str(rel))
    wait_marker(env, "w1", "begin")
    st = finish(spawn(env, "st", b, action="status"))[1]
    assert st["lock_held"] is True and st["lock_info"]["pid"] == w1.pid and st["incomplete"]["holder_alive"] is True and st["incomplete"]["actions"] == []
    code2, r2 = finish(spawn(env, "w2", b, plan_file=f2))
    assert code2 == 3 and r2["kind"] == "locked", r2
    assert q(b.database, "SELECT state FROM bootstrap_runs WHERE bootstrap_id=?", p2["bootstrap_id"]) == [("rolled_back",)]
    assert env.bs.LocalRegistry(b).sync_state == "BOOTSTRAPPING"  # refuzul nu a atins starea comună
    rel.write_text("go")
    code1, r1 = finish(w1)
    assert code1 == 0 and r1["ok"], r1
    key = assert_coherent_bound(env, b, p1, before)
    assert q(env.cp.path, "SELECT COUNT(*) FROM pairing_codes WHERE used_at IS NOT NULL") == [(1,)]  # codul celui refuzat nu a fost consumat
    assert_no_secret_anywhere(env, key)


@pytest.mark.parametrize("round_no", range(3))
def test_cross_process_simultaneous_start_exactly_one_acquires(renv, round_no):
    env = renv
    w = World(env)
    b = w.local(w.org1)
    p1, _, f1 = make_plan(env, w, b)
    p2, _, f2 = make_plan(env, w, b)
    go = env.tmp / "go"
    a = spawn(env, "a", b, plan_file=f1, go_file=str(go), sleep_at="begin", sleep_s=2.0)
    c = spawn(env, "c", b, plan_file=f2, go_file=str(go), sleep_at="begin", sleep_s=2.0)
    time.sleep(1.5)  # ambele au importat și așteaptă bariera
    go.write_text("go")
    ra, rc = finish(a), finish(c)
    results = [ra, rc]
    assert sorted(r[0] for r in results) == [0, 3], results
    winner = next(r[1] for r in results if r[0] == 0)
    loser = next(r[1] for r in results if r[0] == 3)
    assert loser["kind"] == "locked"
    assert q(env.cp.path, "SELECT COUNT(*) FROM bindings") == [(1,)] and q(env.cp.path, "SELECT COUNT(*) FROM devices") == [(1,)]
    assert env.bs.LocalRegistry(b).sync_state == "BOUND" and winner["report"]["result"] == "completed"
    assert q(b.database, "SELECT COUNT(*) FROM bootstrap_runs WHERE state='completed'") == [(1,)]


def test_cross_process_different_cemeteries_proceed_independently(renv):
    env = renv
    w = World(env)
    ba, bb, bc = w.local(w.org1, "A"), w.local(w.org1, "B"), w.local(w.org2, "C")
    before = catalog_counts(env)
    pa, _, fa = make_plan(env, w, ba)
    pb, _, fb = make_plan(env, w, bb)
    pc, _, fc = make_plan(env, w, bc, owner=w.owner2)
    # fiecare proces așteaptă ca CELĂLALT să fi ajuns în after_begin => dovedește execuție simultană (altfel: TimeoutError)
    wa = spawn(env, "A", ba, plan_file=fa, sync_at="after_begin", peer_marker=str(marker(env, "B", "after_begin")))
    wb = spawn(env, "B", bb, plan_file=fb, sync_at="after_begin", peer_marker=str(marker(env, "C", "after_begin")))
    wc = spawn(env, "C", bc, plan_file=fc, sync_at="after_begin", peer_marker=str(marker(env, "A", "after_begin")))
    res = [finish(x) for x in (wa, wb, wc)]
    assert [r[0] for r in res] == [0, 0, 0], res
    ka, kb, kc = (assert_coherent_bound(env, b_, p_, catalog_counts(env)) for b_, p_ in ((ba, pa), (bb, pb), (bc, pc)))
    assert len({ka, kb, kc}) == 3
    assert q(env.cp.path, "SELECT COUNT(DISTINCT device_uid) FROM bindings WHERE organization_uid=?", w.org1) == [(1,)]  # același PC, 2 cimitire => 1 device
    assert q(env.cp.path, "SELECT COUNT(*) FROM bindings") == [(3,)]
    for key, other in ((ka, bb), (ka, bc), (kb, ba), (kc, ba)):  # cheia unui cimitir nu deschide altul
        with pytest.raises(env.cc.ScopeError):
            env.cp.authenticate(key, other.cemetery_uid)
    assert before == catalog_counts(env)
    for k in (ka, kb, kc):
        assert_no_secret_anywhere(env, k)


# ================================================================== 4. CRASH (kill -9) + RESUME / ROLLBACK REAL
@pytest.mark.parametrize("phase", PHASES)
def test_real_kill9_then_detect_and_resume(renv, phase):
    env = renv
    w = World(env)
    b = w.local(w.org1)
    before = catalog_counts(env)
    p1, _, f1 = make_plan(env, w, b)
    p2, _, f2 = make_plan(env, w, b)  # plan concurent (alt cod) — nu are voie să pornească peste bootstrap-ul căzut
    victim = spawn(env, "v", b, plan_file=f1, crash_at=phase)
    wait_marker(env, "v", phase)
    kill9(victim)
    st = finish(spawn(env, "st", b, action="status"))[1]
    inc = st["incomplete"]
    assert inc and inc["bootstrap_id"] == p1["bootstrap_id"] and inc["state"] == "running"
    assert inc["holder_alive"] is False and inc["actions"] == ["resume", "rollback"] and st["lock_held"] is False
    with pytest.raises(env.bs.BlockedFlow) as bf:
        env.bs.plan_flow_a(b, env.cp, env.cp.issue_code(w.owner1, b.organization_uid), env.inst)
    assert bf.value.decision == "INCOMPLETE_BOOTSTRAP"
    code2, r2 = finish(spawn(env, "intruder", b, plan_file=f2))  # alt plan peste bootstrap-ul căzut => refuz, fără să-i strice secretul/legătura
    assert code2 == 3 and r2["kind"] == "blocked" and r2["decision"] == "INCOMPLETE_BOOTSTRAP", r2
    secret_before = secret_of(env, b)
    code, r = finish(spawn(env, "resume", b, plan_file=f1))
    assert code == 0 and r["report"]["result"] == "completed", r
    key = assert_coherent_bound(env, b, p1, before)
    if secret_before and phase not in ("after_redeem",):
        assert key == secret_before or phase in ("begin", "after_begin")  # secretul existent nu se rotește la resume
    assert q(env.cp.path, "SELECT COUNT(*) FROM pairing_codes WHERE used_at IS NOT NULL") == [(1,)]
    # idempotență: reluarea unui plan completat întoarce același raport, fără efecte noi
    code3, r3 = finish(spawn(env, "again", b, plan_file=f1))
    assert code3 == 0 and r3["report"] == r["report"]
    assert q(env.cp.path, "SELECT COUNT(*) FROM bindings") == [(1,)] and q(env.cp.path, "SELECT COUNT(*) FROM audit_log WHERE action='redeem_code'")[0][0] <= 2
    assert_no_secret_anywhere(env, key)


@pytest.mark.parametrize("phase", PHASES)
def test_real_kill9_then_rollback_is_coherent_and_retry_works(renv, phase):
    env = renv
    w = World(env)
    b = w.local(w.org1)
    before = catalog_counts(env)
    p1, _, f1 = make_plan(env, w, b)
    victim = spawn(env, "v", b, plan_file=f1, crash_at=phase)
    wait_marker(env, "v", phase)
    secret_mid = secret_of(env, b)
    kill9(victim)
    code, r = finish(spawn(env, "abort", b, action="abort", bootstrap_id=p1["bootstrap_id"]))
    assert code == 0, r
    L = env.bs.LocalRegistry(b)
    assert L.sync_state == "UNBOUND" and not L.get("cloud_secret_ref") and not L.get("cloud_binding_uid")
    assert q(b.database, "SELECT state FROM bootstrap_runs WHERE bootstrap_id=?", p1["bootstrap_id"]) == [("rolled_back",)]
    assert not list(Path(env.store.root).glob("*"))  # secretul nu mai există nicăieri
    assert not q(env.cp.path, "SELECT 1 FROM bindings WHERE cemetery_uid=? AND status='active'", b.cemetery_uid)
    assert not q(env.cp.path, "SELECT 1 FROM bootstrap_runs WHERE cemetery_uid=? AND state IN ('planned','running','verifying')", b.cemetery_uid)
    assert q(env.cp.path, "SELECT COUNT(*) FROM cemeteries WHERE uid=?", b.cemetery_uid)[0][0] <= 1
    assert not env.bs.lock_held(b) and catalog_counts(env) == before
    if secret_mid:
        assert_no_secret_anywhere(env, secret_mid)
    cloud_status = q(env.cp.path, "SELECT status FROM cemeteries WHERE uid=?", b.cemetery_uid)
    p3, _, f3 = None, None, None
    if cloud_status == [("ACTIVE",)]:  # cădere după `completed` în cloud: rollback = dezlegare; ACTIVE+gol cere aprobare (fail-closed)
        with pytest.raises(env.bs.BlockedFlow) as bf:
            env.bs.plan_flow_a(b, env.cp, env.cp.issue_code(w.owner1, b.organization_uid), env.inst)
        assert bf.value.decision == "BLOCKED_ACTIVE_EMPTY"
        return
    p3, _, f3 = make_plan(env, w, b)
    code, r = finish(spawn(env, "retry", b, plan_file=f3))
    assert code == 0, r
    key = secret_of(env, b)
    assert q(env.cp.path, "SELECT COUNT(*) FROM cemeteries WHERE uid=?", b.cemetery_uid) == [(1,)]
    assert q(env.cp.path, "SELECT COUNT(*) FROM bindings WHERE cemetery_uid=? AND status='active'", b.cemetery_uid) == [(1,)]
    assert q(env.cp.path, "SELECT COUNT(*) FROM devices") == [(1,)]
    assert env.bs.LocalRegistry(b).sync_state == "BOUND" and key and catalog_counts(env) == before


def test_resume_does_not_depend_on_expired_pairing_code_after_redeem(renv):
    env = renv
    w = World(env)
    b = w.local(w.org1)
    p1, code, f1 = make_plan(env, w, b)
    victim = spawn(env, "v", b, plan_file=f1, crash_at="verifying")
    wait_marker(env, "v", "verifying")
    kill9(victim)
    c = sqlite3.connect(env.cp.path)
    c.execute("UPDATE pairing_codes SET expires_at=1")  # codul a expirat între cădere și resume
    c.commit()
    c.close()
    rc, r = finish(spawn(env, "resume", b, plan_file=f1))
    assert rc == 0 and r["report"]["result"] == "completed", r


# ================================================================== 5. OFFLINE REAL
def test_offline_real_flow_a_in_kernel_blocked_network_process(renv):
    env = renv
    w = World(env)
    b = w.local(w.org1)
    before = catalog_counts(env)
    p1, _, f1 = make_plan(env, w, b)
    rc, r = finish(spawn(env, "offline", b, plan_file=f1, no_network=True))
    if r and r["net"].get("installed") is False:
        pytest.skip("seccomp indisponibil pe această platformă")
    assert r["net"]["installed"] is True
    assert all(v.startswith("blocked:") for v in r["net"]["probe"].values()), r["net"]  # nicio conexiune (nici loopback) nu reușește
    assert rc == 0 and r["report"]["result"] == "completed", r
    key = assert_coherent_bound(env, b, p1, before)
    insert_any_row(b)  # MAIN local continuă să scrie
    assert env.bs.LocalRegistry(b).inspect()["rows"] == 1
    assert_no_secret_anywhere(env, key)


def test_offline_real_crash_resume_and_status_without_network(renv):
    env = renv
    w = World(env)
    b = w.local(w.org1)
    before = catalog_counts(env)
    p1, _, f1 = make_plan(env, w, b)
    victim = spawn(env, "v", b, plan_file=f1, crash_at="after_secret", no_network=True)
    wait_marker(env, "v", "after_secret")
    kill9(victim)
    st = finish(spawn(env, "st", b, action="status", no_network=True))[1]
    if st["net"].get("installed") is False:
        pytest.skip("seccomp indisponibil")
    assert st["incomplete"]["actions"] == ["resume", "rollback"]
    rc, r = finish(spawn(env, "resume", b, plan_file=f1, no_network=True))
    assert rc == 0, r
    assert_coherent_bound(env, b, p1, before)


def test_offline_in_process_zero_network_attempts_whole_step1(renv, monkeypatch):
    import socket
    env = renv
    attempts = []

    def deny(*a, **k):
        attempts.append(a)
        raise OSError("rețea indisponibilă (test)")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    w = World(env)
    b = w.local(w.org1)
    p1, code, _ = make_plan(env, w, b)
    rep = env.bs.execute_flow_a(b, env.cp, env.store, p1, p1["plan_hash"], code, env.inst)
    assert rep["result"] == "completed" and env.bs.ping(b, env.cp, env.store, env.inst)["sync_epoch"] == 1
    env.bs.unbind(b, env.cp, env.store, w.owner1)
    assert attempts == []


# ================================================================== 7. ACTIVE + GOL (fail-closed până la aprobarea regulii)
def _no_side_effects(env, b, code):
    L = env.bs.LocalRegistry(b)
    assert L.sync_state == "UNBOUND" and not q(b.database, "SELECT 1 FROM bootstrap_runs")
    assert not L.get("cloud_secret_ref") and not list(Path(env.store.root).glob("*")) if Path(env.store.root).exists() else True
    assert not q(env.cp.path, "SELECT 1 FROM bindings") and not q(env.cp.path, "SELECT 1 FROM bootstrap_runs") and not q(env.cp.path, "SELECT 1 FROM devices")
    assert q(env.cp.path, "SELECT COUNT(*) FROM pairing_codes WHERE used_at IS NOT NULL") == [(0,)]
    assert not q(env.cp.path, "SELECT 1 FROM failed_redeems")


def test_decide_flow_active_empty_is_not_new(env):
    d = env.bs.decide_flow
    assert d("empty", "empty", "UNBOUND", cloud_status=None) == "A"
    assert d("empty", "empty", "UNBOUND", cloud_status="DRAFT") == "A"
    assert d("empty", "empty", "UNBOUND", cloud_status="ACTIVE") == "BLOCKED_ACTIVE_EMPTY"
    assert d("empty", "empty", "UNBOUND") == "A"  # cimitir inexistent (provizionare)
    assert d("nonempty", "empty", "UNBOUND", cloud_status="ACTIVE") == "B"  # B nu e implementat/modificat în Pasul 1


def test_draft_empty_allows_flow_a_and_nonexistent_allowed(env):
    w = World(env)
    b1, b2 = w.local(w.org1, "draft"), w.local(w.org1, "nou")
    env.cp.create_cemetery(w.owner1, w.org1, b1.cemetery_uid, "Draft")
    assert q(env.cp.path, "SELECT status FROM cemeteries WHERE uid=?", b1.cemetery_uid) == [("DRAFT",)]
    for b in (b1, b2):  # b1: DRAFT+gol; b2: inexistent în cloud (cod de provizionare)
        code = env.cp.issue_code(w.owner1, w.org1, b.cemetery_uid if b is b1 else None)
        plan = env.bs.plan_flow_a(b, env.cp, code, env.inst, clock=env.clock)
        assert env.bs.execute_flow_a(b, env.cp, env.store, plan, plan["plan_hash"], code, env.inst, clock=env.clock)["result"] == "completed"


def test_active_empty_refused_without_any_side_effect(env):
    w = World(env)
    b = w.local(w.org1)
    env.cp.create_cemetery(w.owner1, w.org1, b.cemetery_uid, "Gol dar ACTIVE")
    c = sqlite3.connect(env.cp.path)
    c.execute("UPDATE cemeteries SET status='ACTIVE' WHERE uid=?", (b.cemetery_uid,))
    c.commit()
    c.close()
    code = env.cp.issue_code(w.owner1, w.org1, b.cemetery_uid)
    with pytest.raises(env.bs.BlockedFlow) as bf:
        env.bs.plan_flow_a(b, env.cp, code, env.inst, clock=env.clock)
    assert bf.value.decision == "BLOCKED_ACTIVE_EMPTY" and "aprobar" in str(bf.value)
    _no_side_effects(env, b, code)


def test_active_empty_after_unbind_is_still_refused(env):
    w = World(env)
    b = w.local(w.org1)
    code = env.cp.issue_code(w.owner1, w.org1)
    plan = env.bs.plan_flow_a(b, env.cp, code, env.inst, clock=env.clock)
    env.bs.execute_flow_a(b, env.cp, env.store, plan, plan["plan_hash"], code, env.inst, clock=env.clock)
    env.bs.unbind(b, env.cp, env.store, w.owner1)
    assert q(env.cp.path, "SELECT status FROM cemeteries WHERE uid=?", b.cemetery_uid) == [("ACTIVE",)]
    with pytest.raises(env.bs.BlockedFlow) as bf:
        env.bs.plan_flow_a(b, env.cp, env.cp.issue_code(w.owner1, w.org1, b.cemetery_uid), env.inst, clock=env.clock)
    assert bf.value.decision == "BLOCKED_ACTIVE_EMPTY"


def test_cloud_turning_active_between_plan_and_confirm_is_refused(env):
    w = World(env)
    b = w.local(w.org1)
    env.cp.create_cemetery(w.owner1, w.org1, b.cemetery_uid, "Draft")
    code = env.cp.issue_code(w.owner1, w.org1, b.cemetery_uid)
    plan = env.bs.plan_flow_a(b, env.cp, code, env.inst, clock=env.clock)
    c = sqlite3.connect(env.cp.path)
    c.execute("UPDATE cemeteries SET status='ACTIVE'")
    c.commit()
    c.close()
    with pytest.raises((env.bs.BlockedFlow, env.bs.PlanError)):
        env.bs.execute_flow_a(b, env.cp, env.store, plan, plan["plan_hash"], code, env.inst, clock=env.clock)
    assert not q(env.cp.path, "SELECT 1 FROM bindings") and env.bs.LocalRegistry(b).sync_state == "UNBOUND"


# ================================================================== 2. SecretStore de test: secretul nu ajunge în SQLite/loguri/backup
def test_secret_not_in_sqlite_logs_backup_or_cloud_with_encrypted_test_store(renv):
    from cimitir_backup import backup, verify
    env = renv
    w = World(env)
    b = w.local(w.org1)
    p1, code, f1 = make_plan(env, w, b)
    rc, r = finish(spawn(env, "x", b, plan_file=f1))
    assert rc == 0
    key = secret_of(env, b)
    bk = backup(b.root, env.tmp / "backups")
    assert verify(bk) == []
    assert any(f.is_file() for f in bk.rglob("*"))
    assert_no_secret_anywhere(env, key)
    enc = list(Path(env.store.root).glob("*.enc"))
    assert len(enc) == 1 and not [f for f in Path(env.store.root).glob("*") if f.suffix != ".enc"]
    dump = " ".join(map(str, q(env.cp.path, "SELECT * FROM bindings")))
    assert key not in dump and env.cc._h(key) in dump  # în cloud doar hash
    rep = json.dumps(r)
    assert key not in rep


# ================================================================== _pid_alive + Windows real (skip în afara Windows)
def test_pid_alive_current_platform(env):
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert env.bs._pid_alive(os.getpid()) and env.bs._pid_alive(p.pid)
        assert not env.bs._pid_alive(0) and not env.bs._pid_alive(-3) and not env.bs._pid_alive(999_999_999)
        p.kill()
        deadline = time.time() + 10
        while env.bs._pid_alive(p.pid) and time.time() < deadline:
            time.sleep(0.05)
        assert not env.bs._pid_alive(p.pid)  # mort (zombie inclus) înainte de wait()
        p.wait()
        assert not env.bs._pid_alive(p.pid)
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI real + _pid_alive Windows: doar pe Windows (scripts/windows_verify_step1.py)")
def test_windows_real_dpapi_rotate_isolation_and_pid(env):
    st = env.ss.DpapiSecretStore(env.tmp / "realdpapi")
    st.set("binding/A", "secret-A-1")
    st.set("binding/B", "secret-B")
    st.set("binding/A", "secret-A-2")
    assert st.get("binding/A") == "secret-A-2" and st.get("binding/B") == "secret-B"
    assert all(b"secret-" not in f.read_bytes() for f in Path(st.root).glob("*.bin"))
    st.delete("binding/A")
    assert st.get("binding/A") is None and st.get("binding/B") == "secret-B"
    test_pid_alive_current_platform(env)
