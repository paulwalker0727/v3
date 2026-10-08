"""Etapa 4 / Pasul 1 — identitate, pairing, scope Organization Owner, binding device×cemetery, SecretStore/DPAPI, stări bootstrap, plan_hash,
Flux A (MAIN gol ↔ cloud gol). Doar foldere temporare + control-plane SQLite local. Nu atinge Supabase, Render, migrările 008–013, Banovița.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "shared") not in sys.path:
    sys.path.insert(0, str(ROOT / "shared"))

LEAKS = []  # accesuri cross-tenant reușite (trebuie să rămână 0)


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def fake_protect(b: bytes) -> bytes:
    return bytes(x ^ 0x5A for x in b[::-1]) + b"DPAPI-FAKE"


def fake_unprotect(b: bytes) -> bytes:
    assert b.endswith(b"DPAPI-FAKE")
    return bytes(x ^ 0x5A for x in b[:-10])[::-1]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    for k, v in {"CIMITIR_DATA_DIR": str(data), "CIMITIR_IGNORE_PROJECT_ENV": "1", "CIMITIR_DB_ENGINE": "sqlite",
                 "CIMITIR_MODE": "local", "DATABASE_URL": "", "CIMITIR_JOBS": "0"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CIMITIR_REGISTRY_UID", raising=False)
    monkeypatch.delenv("CIMITIR_COMMERCIAL", raising=False)
    monkeypatch.setenv("CIMITIR_ALLOW_TEST_SECRETSTORE", "1")
    before = set(sys.modules)
    import cimitir_registry as reg
    import cimitir_cloud_control as cc
    import cimitir_bootstrap as bs
    import cimitir_secretstore as ss
    reg.deactivate()
    reg._listeners.clear()
    reg._initialized.clear()
    cat = reg.RegistryCatalog(data)
    clock = Clock()
    cp = cc.LocalControlPlane(tmp_path / "cloud" / "control.sqlite", clock=clock)
    store = ss.TestEncryptedSecretStore(tmp_path / "secrets")
    yield SimpleNamespace(data=data, tmp=tmp_path, reg=reg, cc=cc, bs=bs, ss=ss, cat=cat, cp=cp, store=store, clock=clock,
                          inst=bs.installation_uid(cat))
    reg.deactivate()
    reg._listeners.clear()
    reg._initialized.clear()
    for name in set(sys.modules) - before:
        mod = sys.modules.get(name)
        if str(getattr(mod, "__file__", "") or "").startswith(str(ROOT)):
            sys.modules.pop(name, None)


class World:
    """Platform Admin + 2 organizații (O1, O2) cu Owner propriu; câte un registru local gol per organizație."""

    def __init__(self, env):
        e, cc = env, env.cc
        self.env = env
        self.pa = e.cp.create_principal("platform", platform_admin=True)
        self.owner1, self.owner2 = e.cp.create_principal("owner1"), e.cp.create_principal("owner2")
        self.org1, self.org2 = str(uuid.uuid4()), str(uuid.uuid4())
        e.cp.create_organization(self.pa, self.org1, "Org 1", first_owner=self.owner1)
        e.cp.create_organization(self.pa, self.org2, "Org 2", first_owner=self.owner2)

    def local(self, org_uid=None, name="Cimitir"):
        """Creează o organizație locală cu UID dat (sau nouă) și un cimitir; întoarce binding pregătit."""
        e = self.env
        cat = e.cat
        import sqlite3 as _s
        if org_uid:
            with cat._conn() as c:
                if not c.execute("SELECT 1 FROM organizations WHERE uid=?", (org_uid,)).fetchone():
                    c.execute("INSERT INTO organizations (uid, org_type, official_name, display_name, active) VALUES (?,?,?,?,1)",
                              (org_uid, "parohie", "Org", "Org"))
            cem = cat.create_cemetery(org_uid, name)
        else:
            org = cat.create_organization("parohie", "Org local")
            cem = cat.create_cemetery(org, name)
        b = e.cat.binding_for(cem)
        e.reg.prepare_registry_db(b)
        return b


def add_plot(binding, cod="A-1"):
    c = sqlite3.connect(binding.database)
    c.execute("INSERT INTO locuri_de_veci (cod_loc) VALUES (?)", (cod,)) if _has_col(c, "locuri_de_veci", "cod_loc") else None
    c.commit()
    c.close()


def _has_col(c, table, col):
    return any(r[1] == col for r in c.execute(f"PRAGMA table_info({table})"))


def insert_any_row(binding, table="locuri_de_veci"):
    c = sqlite3.connect(binding.database)
    cols = [(r[1], r[2], r[3], r[4]) for r in c.execute(f"PRAGMA table_info({table})")]
    names, vals = [], []
    for n, t, notnull, default in cols:
        if n == "id" or (default is not None):
            continue
        if notnull:
            names.append(n)
            vals.append(1 if "INT" in (t or "").upper() or "REAL" in (t or "").upper() else f"x-{uuid.uuid4().hex[:6]}")
    c.execute(f"INSERT INTO {table} ({','.join(names)}) VALUES ({','.join('?' * len(vals))})" if names else f"INSERT INTO {table} DEFAULT VALUES", vals)
    c.commit()
    c.close()


def flow_a(env, w, b, org_uid=None, owner=None, hook=None):
    org = org_uid or b.organization_uid
    code = env.cp.issue_code(owner or w.owner1, org)
    plan = env.bs.plan_flow_a(b, env.cp, code, env.inst, cemetery_name="Cimitir A", clock=env.clock)
    rep = env.bs.execute_flow_a(b, env.cp, env.store, plan, plan["plan_hash"], code, env.inst, clock=env.clock, hook=hook)
    return code, plan, rep


# ================================================================== SECRETSTORE / DPAPI
def test_secretstore_dpapi_wrapper_logic_with_injected_fake_protect(env):
    """Doar logica învelișului DPAPI (injectare protect/unprotect FALSE, NU criptare reală); DPAPI real = test Windows."""
    s = env.ss.DpapiSecretStore(env.tmp / "fake-dpapi", protect=fake_protect, unprotect=fake_unprotect)
    s.set("binding/abc", "cimitir.abc.SUPERSECRET")
    assert s.get("binding/abc") == "cimitir.abc.SUPERSECRET" and s.exists("binding/abc")
    s.delete("binding/abc")
    assert s.get("binding/abc") is None


def test_secretstore_test_backend_roundtrip_is_really_encrypted(env):
    s = env.store
    s.set("binding/abc", "cimitir.abc.SUPERSECRET")
    assert s.get("binding/abc") == "cimitir.abc.SUPERSECRET" and s.exists("binding/abc")
    blob = next(Path(s.root).glob("*.enc")).read_bytes()
    assert b"SUPERSECRET" not in blob and b"cimitir.abc" not in blob
    import base64
    assert b"SUPERSECRET" not in base64.urlsafe_b64decode(blob)  # nu e base64 simplu: Fernet = AES+HMAC
    assert not [t for t in (env.tmp / "secrets").glob("*") if t.suffix != ".enc"]
    s.delete("binding/abc")
    assert s.get("binding/abc") is None


def test_secretstore_names_validated(env):
    for bad in ("../x", "a b", "", "a/../b", "x" * 300):
        with pytest.raises(env.ss.SecretStoreError):
            env.store.set(bad, "v")


def test_secretstore_default_requires_explicit_backend_off_windows(env, monkeypatch):
    monkeypatch.delenv("CIMITIR_SECRET_STORE", raising=False)
    if sys.platform != "win32":
        with pytest.raises(env.ss.SecretStoreUnavailable):
            env.ss.default_store(env.data)
        with pytest.raises(env.ss.SecretStoreUnavailable):
            env.ss.DpapiSecretStore(env.tmp / "s")
    monkeypatch.setenv("CIMITIR_SECRET_STORE", "test-encrypted")
    st = env.ss.default_store(env.data)
    assert isinstance(st, env.ss.TestEncryptedSecretStore) and st.root == env.data / "secrets"
    monkeypatch.setenv("CIMITIR_SECRET_STORE", "dev-file")  # backend-ul base64 a fost ELIMINAT
    with pytest.raises(env.ss.SecretStoreUnavailable):
        env.ss.default_store(env.data)
    monkeypatch.setenv("CIMITIR_SECRET_STORE", "bogus")
    with pytest.raises(env.ss.SecretStoreUnavailable):
        env.ss.default_store(env.data)
    assert not hasattr(env.ss, "DevFileSecretStore") and "import base64" not in Path(env.ss.__file__).read_text(encoding="utf-8")


def test_secretstore_test_backends_forbidden_in_commercial_and_without_opt_in(env, monkeypatch):
    for bad in ("CIMITIR_COMMERCIAL",):
        monkeypatch.setenv(bad, "1")
        with pytest.raises(env.ss.SecretStoreUnavailable):
            env.ss.TestEncryptedSecretStore(env.tmp / "d")
        with pytest.raises(env.ss.SecretStoreUnavailable):
            env.ss.MemorySecretStore()
        for backend in ("test-encrypted", "memory"):
            monkeypatch.setenv("CIMITIR_SECRET_STORE", backend)
            with pytest.raises(env.ss.SecretStoreUnavailable):
                env.ss.default_store(env.data)
    monkeypatch.delenv("CIMITIR_COMMERCIAL")
    monkeypatch.delenv("CIMITIR_ALLOW_TEST_SECRETSTORE")
    with pytest.raises(env.ss.SecretStoreUnavailable):
        env.ss.TestEncryptedSecretStore(env.tmp / "d")
    monkeypatch.setenv("CIMITIR_ALLOW_TEST_SECRETSTORE", "1")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(env.ss.SecretStoreUnavailable):
        env.ss.TestEncryptedSecretStore(env.tmp / "d")


def test_secretstore_test_backend_key_is_ephemeral_not_on_disk(env):
    s1 = env.ss.TestEncryptedSecretStore(env.tmp / "eph")
    s1.set("binding/k", "VALOARE-SECRETA")
    s2 = env.ss.TestEncryptedSecretStore(env.tmp / "eph")  # altă cheie efemeră -> nedecriptabil
    with pytest.raises(env.ss.SecretStoreError):
        s2.get("binding/k")
    s3 = env.ss.TestEncryptedSecretStore(env.tmp / "eph", key=s1.key)  # cheia transmisă explicit unui proces copil
    assert s3.get("binding/k") == "VALOARE-SECRETA"
    for f in (env.tmp / "eph").glob("*"):
        assert s1.key not in f.read_bytes() and b"VALOARE-SECRETA" not in f.read_bytes()


def test_secretstore_memory_backend_persists_nothing(env):
    m = env.ss.MemorySecretStore()
    m.set("binding/m", "v")
    assert m.get("binding/m") == "v" and not list((env.tmp).rglob("*m*.enc"))


def test_secretstore_corrupt_blob_is_error_not_garbage(env):
    env.store.set("binding/x", "v")
    next(Path(env.store.root).glob("*.enc")).write_bytes(b"garbage")
    with pytest.raises(env.ss.SecretStoreError):
        env.store.get("binding/x")


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI real: doar pe Windows (se validează prin SMOKE pe Windows)")
def test_secretstore_real_dpapi_windows(env):
    st = env.ss.DpapiSecretStore(env.tmp / "realdpapi")
    st.set("binding/w", "cheie-secreta-xyz")
    assert st.get("binding/w") == "cheie-secreta-xyz"
    assert b"cheie-secreta-xyz" not in next(Path(st.root).glob("*.bin")).read_bytes()


# ================================================================== ROLURI / ORGANIZATION OWNER SCOPE
def test_only_platform_admin_creates_organizations(env):
    pa, owner = env.cp.create_principal("pa", True), env.cp.create_principal("o")
    with pytest.raises(env.cc.PermissionDenied):
        env.cp.create_organization(owner, str(uuid.uuid4()), "X")
    org = str(uuid.uuid4())
    env.cp.create_organization(pa, org, "X", first_owner=owner)
    with pytest.raises(env.cc.ConflictError):
        env.cp.create_organization(pa, org, "X2")


def test_owner_creates_cemetery_only_in_own_org_without_platform_admin(env):
    w = World(env)
    cem = str(uuid.uuid4())
    env.cp.create_cemetery(w.owner1, w.org1, cem, "C1")
    with pytest.raises(env.cc.PermissionDenied):
        env.cp.create_cemetery(w.owner1, w.org2, str(uuid.uuid4()), "intruziune")
    with pytest.raises(env.cc.PermissionDenied):
        env.cp.create_cemetery(w.pa, w.org1, str(uuid.uuid4()), "PA nu e Owner")  # Platform Admin ≠ Organization Owner
    with pytest.raises(env.cc.ConflictError):
        env.cp.create_cemetery(w.owner1, w.org1, cem, "dublu")
    with pytest.raises(env.cc.ConflictError):
        env.cp.create_cemetery(w.owner2, w.org2, cem, "UID din altă organizație")


def test_roles_admin_and_rebuild_cannot_create_or_issue(env):
    w = World(env)
    adm, reb = env.cp.create_principal("adm"), env.cp.create_principal("reb")
    env.cp.grant_role(w.owner1, adm, w.org1, env.cc.ADMIN)
    env.cp.grant_role(w.owner1, reb, w.org1, env.cc.REBUILD)
    for p in (adm, reb):
        with pytest.raises(env.cc.PermissionDenied):
            env.cp.create_cemetery(p, w.org1, str(uuid.uuid4()), "x")
        with pytest.raises(env.cc.PermissionDenied):
            env.cp.issue_code(p, w.org1)
    assert env.cp.can(reb, "rebuild", w.org1) and not env.cp.can(adm, "rebuild", w.org1)
    assert not env.cp.can(w.owner2, "rebuild", w.org1) and not env.cp.can(w.pa, "issue_code", w.org1)
    with pytest.raises(env.cc.PermissionDenied):
        env.cp.grant_role(w.owner1, adm, w.org1, env.cc.OWNER)  # Owner nou = Platform Admin
    with pytest.raises(env.cc.PermissionDenied):
        env.cp.grant_role(w.owner2, adm, w.org1, env.cc.ADMIN)


def test_cross_org_inspect_and_code_are_scope_errors_not_info_leaks(env):
    w = World(env)
    cem1 = str(uuid.uuid4())
    env.cp.create_cemetery(w.owner1, w.org1, cem1, "C1")
    with pytest.raises(env.cc.ScopeError) as e1:
        env.cp.issue_code(w.owner2, w.org2, cem1)
    with pytest.raises(env.cc.ScopeError) as e2:
        env.cp.issue_code(w.owner2, w.org2, str(uuid.uuid4()))
    assert str(e1.value) == str(e2.value)  # existent-în-altă-org ≡ inexistent
    assert env.cp.inspect_as(w.owner2, w.org2, cem1) == env.cp.inspect_as(w.owner2, w.org2, str(uuid.uuid4()))
    assert env.cp.inspect_as(w.owner2, w.org2, cem1)["exists"] is False
    with pytest.raises(env.cc.PermissionDenied):
        env.cp.inspect_as(w.owner2, w.org1, cem1)


# ================================================================== PAIRING
def test_pairing_code_hash_only_expiry_single_use(env):
    w = World(env)
    cem = str(uuid.uuid4())
    env.cp.create_cemetery(w.owner1, w.org1, cem, "C1")
    code = env.cp.issue_code(w.owner1, w.org1, cem)
    c = sqlite3.connect(env.cp.path)
    dump = " ".join(str(r) for r in c.execute("SELECT * FROM pairing_codes"))
    c.close()
    assert code not in dump
    res = env.cp.redeem_code(code, env.inst, "PC1", cem)
    assert res["binding_key"].startswith("cimitir.") and res["sync_epoch"] == 1 and res["last_change_seq"] == 0
    with pytest.raises(env.cc.CodeError):
        env.cp.redeem_code(code, env.inst, "PC1", cem)
    code2 = env.cp.issue_code(w.owner1, w.org1, cem)
    env.clock.t += env.cc.CODE_TTL + 1
    with pytest.raises(env.cc.CodeError):
        env.cp.redeem_code(code2, env.inst, "PC1", cem)


def test_pairing_brute_force_lockout(env):
    w = World(env)
    cem = str(uuid.uuid4())
    env.cp.create_cemetery(w.owner1, w.org1, cem, "C1")
    good = env.cp.issue_code(w.owner1, w.org1, cem)
    for _ in range(env.cc.MAX_FAILED_REDEEMS):
        with pytest.raises(env.cc.CodeError):
            env.cp.redeem_code("greșit", env.inst, "PC1", cem)
    with pytest.raises(env.cc.CodeError):
        env.cp.redeem_code(good, env.inst, "PC1", cem)  # blocat chiar și cu codul bun
    res = env.cp.redeem_code(good, "alta-instalare", "PC2", cem)  # altă instalare nu e blocată
    assert res["binding_key"]


def test_pairing_code_scoped_to_cemetery_and_org(env):
    w = World(env)
    c1, c2, c_other = (str(uuid.uuid4()) for _ in range(3))
    env.cp.create_cemetery(w.owner1, w.org1, c1, "C1")
    env.cp.create_cemetery(w.owner1, w.org1, c2, "C2")
    env.cp.create_cemetery(w.owner2, w.org2, c_other, "Alt")
    code = env.cp.issue_code(w.owner1, w.org1, c1)
    with pytest.raises(env.cc.ScopeError):
        env.cp.redeem_code(code, env.inst, "PC", c2)
    prov = env.cp.issue_code(w.owner1, w.org1)  # provizionare
    with pytest.raises(env.cc.ScopeError):
        env.cp.redeem_code(prov, env.inst, "PC", c_other)  # cimitir din altă organizație
    res = env.cp.redeem_code(prov, env.inst, "PC", str(uuid.uuid4()), "Nou propus de MAIN")
    assert res["organization_uid"] == w.org1


# ================================================================== BINDING device × cemetery
def test_binding_key_only_valid_for_its_cemetery_and_hash_only(env):
    w = World(env)
    c1, c2 = str(uuid.uuid4()), str(uuid.uuid4())
    env.cp.create_cemetery(w.owner1, w.org1, c1, "C1")
    env.cp.create_cemetery(w.owner1, w.org1, c2, "C2")
    r1 = env.cp.redeem_code(env.cp.issue_code(w.owner1, w.org1, c1), env.inst, "PC", c1)
    r2 = env.cp.redeem_code(env.cp.issue_code(w.owner1, w.org1, c2), env.inst, "PC", c2)
    assert r1["device_uid"] == r2["device_uid"] and r1["binding_uid"] != r2["binding_uid"] and r1["binding_key"] != r2["binding_key"]
    assert env.cp.authenticate(r1["binding_key"], c1)["cemetery_uid"] == c1
    with pytest.raises(env.cc.ScopeError):
        env.cp.authenticate(r1["binding_key"], c2)
    with pytest.raises(env.cc.ScopeError):
        env.cp.authenticate(r1["binding_key"] + "x", c1)
    c = sqlite3.connect(env.cp.path)
    assert r1["binding_key"] not in " ".join(str(x) for x in c.execute("SELECT * FROM bindings"))
    c.close()
    with pytest.raises(env.cc.PermissionDenied):
        env.cp.revoke_binding(w.owner2, w.org1, r1["binding_uid"])
    env.cp.revoke_binding(w.owner1, w.org1, r1["binding_uid"])
    with pytest.raises(env.cc.ScopeError):
        env.cp.authenticate(r1["binding_key"], c1)
    assert env.cp.authenticate(r2["binding_key"], c2)


# ================================================================== STĂRI BOOTSTRAP (cloud)
def test_bootstrap_run_states_single_active_and_transitions(env):
    w = World(env)
    cem = str(uuid.uuid4())
    env.cp.create_cemetery(w.owner1, w.org1, cem, "C1")
    code, code_b = env.cp.issue_code(w.owner1, w.org1, cem), env.cp.issue_code(w.owner1, w.org1, cem)
    b1, b2 = str(uuid.uuid4()), str(uuid.uuid4())
    run = env.cp.begin_run(code, env.inst, b1, "A", cem, "h1")
    assert run["state"] == "planned"
    assert env.cp.begin_run(code, env.inst, b1, "A", cem, "h1")["state"] == "planned"  # idempotent
    with pytest.raises(env.cc.ConflictError):
        env.cp.begin_run(code, env.inst, b1, "A", cem, "ALT-HASH")
    with pytest.raises(env.cc.BootstrapLocked):
        env.cp.begin_run(code_b, "alt-pc", b2, "A", cem, "h2")
    res = env.cp.redeem_code(code, env.inst, "PC", cem, bootstrap_id=b1)
    assert env.cp.get_run(b1)["state"] == "running"
    with pytest.raises(env.cc.ConflictError):
        env.cp.complete_run(res["binding_key"], b1)  # running -> completed interzis (trebuie verifying)
    assert env.cp.mark_verifying(res["binding_key"], b1)["state"] == "verifying"
    assert env.cp.mark_verifying(res["binding_key"], b1)["state"] == "verifying"
    assert env.cp.complete_run(res["binding_key"], b1)["state"] == "completed"
    assert env.cp.inspect_as(w.owner1, w.org1, cem)["status"] == "ACTIVE"
    assert env.cp.begin_run(code_b, "alt-pc", b2, "A", cem, "h2")["state"] == "planned"  # după completed se poate un nou run


def test_planned_run_expires_and_does_not_block(env):
    w = World(env)
    cem = str(uuid.uuid4())
    env.cp.create_cemetery(w.owner1, w.org1, cem, "C1")
    c1, c2 = env.cp.issue_code(w.owner1, w.org1, cem), env.cp.issue_code(w.owner1, w.org1, cem)
    env.cp.begin_run(c1, env.inst, "b-old", "A", cem, "h")
    env.clock.t += env.cc.PLAN_TTL + 5
    run = env.cp.begin_run(c2, "x", "b-new", "A", cem, "h2")
    assert run["state"] == "planned" and env.cp.get_run("b-old")["state"] == "rolled_back"


# ================================================================== PLAN_HASH
def test_plan_hash_deterministic_and_sensitive(env):
    w = World(env)
    b = w.local(w.org1)
    code = env.cp.issue_code(w.owner1, w.org1)
    p1 = env.bs.plan_flow_a(b, env.cp, code, env.inst, clock=env.clock)
    p2 = env.bs.plan_flow_a(b, env.cp, code, env.inst, clock=env.clock)
    assert p1["plan_hash"] == p2["plan_hash"] and p1["bootstrap_id"] != p2["bootstrap_id"] and re.fullmatch(r"[0-9a-f]{64}", p1["plan_hash"])
    f = dict(p1["fields"])
    h = env.bs._plan_hash
    assert h(f) == p1["plan_hash"]
    for k, v in (("installation_uid", "alta"), ("epoch", 2), ("organization_uid", w.org2), ("cemetery_uid", str(uuid.uuid4())),
                 ("kind", "B"), ("app_version", "9.9"), ("source_digest", {"x": "y"})):
        assert h({**f, k: v}) != p1["plan_hash"], k
    assert h({**f, "target": {**f["target"], "rows": 3}}) != p1["plan_hash"]


def test_plan_confirm_must_match_and_expires(env):
    w = World(env)
    b = w.local(w.org1)
    code = env.cp.issue_code(w.owner1, w.org1)
    plan = env.bs.plan_flow_a(b, env.cp, code, env.inst, clock=env.clock)
    with pytest.raises(env.bs.PlanError):
        env.bs.execute_flow_a(b, env.cp, env.store, plan, "0" * 64, code, env.inst, clock=env.clock)
    assert env.bs.LocalRegistry(b).sync_state == "PLANNED"
    env.clock.t += env.bs.PLAN_TTL + 1
    with pytest.raises(env.bs.PlanError):
        env.bs.execute_flow_a(b, env.cp, env.store, plan, plan["plan_hash"], code, env.inst, clock=env.clock)
    assert env.bs.LocalRegistry(b).sync_state == "UNBOUND"
    assert env.bs.LocalRegistry(b).run_get(plan["bootstrap_id"])["state"] == "rolled_back"
    assert not sqlite3.connect(env.cp.path).execute("SELECT * FROM devices").fetchall()


def test_plan_toctou_local_changed_between_preflight_and_confirm(env):
    w = World(env)
    b = w.local(w.org1)
    code = env.cp.issue_code(w.owner1, w.org1)
    plan = env.bs.plan_flow_a(b, env.cp, code, env.inst, clock=env.clock)
    insert_any_row(b)
    with pytest.raises(env.bs.BlockedFlow):  # registrul nu mai e gol -> preflight-ul de reconfirmare refuză
        env.bs.execute_flow_a(b, env.cp, env.store, plan, plan["plan_hash"], code, env.inst, clock=env.clock)
    c = sqlite3.connect(env.cp.path)
    assert not c.execute("SELECT * FROM bindings").fetchall() and not c.execute("SELECT * FROM bootstrap_runs").fetchall()
    c.close()


def test_plan_toctou_cloud_changed_between_preflight_and_confirm(env):
    w = World(env)
    b = w.local(w.org1)
    code = env.cp.issue_code(w.owner1, w.org1)
    plan = env.bs.plan_flow_a(b, env.cp, code, env.inst, clock=env.clock)
    env.cp.create_cemetery(w.owner1, w.org1, b.cemetery_uid, "creat între timp")  # target se schimbă (exists False -> True)
    with pytest.raises(env.bs.PlanError):
        env.bs.execute_flow_a(b, env.cp, env.store, plan, plan["plan_hash"], code, env.inst, clock=env.clock)
    assert env.bs.LocalRegistry(b).sync_state == "UNBOUND"


# ================================================================== FLUX A
def test_flow_a_empty_to_empty_end_to_end(env):
    w = World(env)
    b = w.local(w.org1)
    L = env.bs.LocalRegistry(b)
    assert L.sync_state == "UNBOUND" and L.inspect()["classification"] == "empty"
    code = env.cp.issue_code(w.owner1, w.org1)
    plan = env.bs.plan_flow_a(b, env.cp, code, env.inst, "Cimitir A", clock=env.clock)
    assert L.sync_state == "PLANNED" and L.run_get(plan["bootstrap_id"])["state"] == "planned"
    rep = env.bs.execute_flow_a(b, env.cp, env.store, plan, plan["plan_hash"], code, env.inst, clock=env.clock)
    assert rep["result"] == "completed" and rep["local_rows"] == 0 and rep["cloud_rows"] == 0 and rep["sync_epoch"] == 1
    assert L.sync_state == "BOUND" and L.get("bound_installation_uid") == env.inst and L.get("sync_epoch") == "1"
    ref = L.get("cloud_secret_ref")
    assert ref == f"binding/{L.get('cloud_binding_uid')}" and env.store.get(ref).startswith("cimitir.")
    ins = env.cp.inspect_as(w.owner1, w.org1, b.cemetery_uid)
    assert ins["status"] == "ACTIVE" and ins["data_epoch"] == 1 and ins["rows"] == 0
    assert env.cp.get_run(plan["bootstrap_id"])["state"] == "completed" and L.run_get(plan["bootstrap_id"])["state"] == "completed"
    assert not env.bs.lock_held(b) and env.bs.lock_info(b) is None
    assert env.bs.sync_allowed(b, env.inst) and env.bs.ping(b, env.cp, env.store, env.inst)["sync_epoch"] == 1
    c = sqlite3.connect(env.cp.path)
    assert c.execute("SELECT COUNT(*) FROM audit_log WHERE action='redeem_code'").fetchone()[0] == 1
    c.close()
    # idempotent: reexecutarea unui plan complet întoarce același raport
    assert env.bs.execute_flow_a(b, env.cp, env.store, plan, plan["plan_hash"], code, env.inst, clock=env.clock) == rep


def test_flow_a_key_never_in_registry_sqlite_logs_or_reports(env):
    w = World(env)
    b = w.local(w.org1)
    _, plan, rep = flow_a(env, w, b)
    key = env.store.get(env.bs.LocalRegistry(b).get("cloud_secret_ref"))
    secret_part = key.split(".")[-1]
    hits = []
    for root in (env.data / "registries", env.data / "registry_catalog.sqlite"):
        files = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
        for p in files:
            raw = p.read_bytes()
            if secret_part.encode() in raw or key.encode() in raw:
                hits.append(str(p))
    assert hits == []
    assert secret_part not in json.dumps(rep) and secret_part not in json.dumps(plan)
    for p in Path(env.store.root).glob("*"):
        assert secret_part.encode() not in p.read_bytes()
    # backup obișnuit al registrului nu conține cheia
    bk = env.tmp / "bk.sqlite"
    src = sqlite3.connect(b.database)
    dst = sqlite3.connect(bk)
    src.backup(dst)
    dst.close()
    src.close()
    assert secret_part.encode() not in bk.read_bytes()


@pytest.mark.parametrize("crash_at", ["begin", "after_begin", "after_secret", "redeemed", "verifying", "before_complete"])
def test_flow_a_resume_after_crash_at_every_phase(env, crash_at):
    w = World(env)
    b = w.local(w.org1)
    code = env.cp.issue_code(w.owner1, w.org1)
    plan = env.bs.plan_flow_a(b, env.cp, code, env.inst, clock=env.clock)

    def boom(phase):
        if phase == crash_at:
            raise env.bs.Crash(phase)

    with pytest.raises(env.bs.Crash):
        env.bs.execute_flow_a(b, env.cp, env.store, plan, plan["plan_hash"], code, env.inst, clock=env.clock, hook=boom)
    L = env.bs.LocalRegistry(b)
    assert L.sync_state == "BOOTSTRAPPING" and env.bs.lock_held(b)
    assert L.run_get(plan["bootstrap_id"])["state"] == "running"
    with pytest.raises(env.cc.BootstrapLocked):  # alt plan pe același registru e blocat de lock
        env.bs.acquire_lock(b, str(uuid.uuid4()))
    rep = env.bs.execute_flow_a(b, env.cp, env.store, plan, plan["plan_hash"], code, env.inst, clock=env.clock)
    assert rep["result"] == "completed" and L.sync_state == "BOUND"
    c = sqlite3.connect(env.cp.path)
    assert c.execute("SELECT COUNT(*) FROM bindings WHERE status='active'").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM cemeteries").fetchone()[0] == 1
    c.close()
    assert env.bs.ping(b, env.cp, env.store, env.inst)


def test_flow_a_failure_rolls_back_cloud_local_and_secret_then_retry_works(env):
    w = World(env)
    b = w.local(w.org1)
    code = env.cp.issue_code(w.owner1, w.org1)
    plan = env.bs.plan_flow_a(b, env.cp, code, env.inst, clock=env.clock)

    def fail(phase):
        if phase == "verifying":
            raise RuntimeError("eroare simulată")

    with pytest.raises(RuntimeError):
        env.bs.execute_flow_a(b, env.cp, env.store, plan, plan["plan_hash"], code, env.inst, clock=env.clock, hook=fail)
    L = env.bs.LocalRegistry(b)
    assert L.sync_state == "UNBOUND" and L.get("cloud_secret_ref") == "" and L.get("cloud_binding_uid") == ""
    assert not list(Path(env.store.root).glob("*")) and not env.bs.lock_held(b) and env.bs.lock_info(b) is None
    assert env.cp.get_run(plan["bootstrap_id"])["state"] == "rolled_back"
    c = sqlite3.connect(env.cp.path)
    assert c.execute("SELECT COUNT(*) FROM bindings WHERE status='active'").fetchone()[0] == 0
    assert c.execute("SELECT status FROM cemeteries").fetchone()[0] == "DRAFT"
    c.close()
    assert L.inspect()["rows"] == 0  # baza locală neatinsă
    _, _, rep = flow_a(env, w, b)  # reîncercare cu cod nou
    assert rep["result"] == "completed"


def test_flow_a_explicit_abort_after_crash(env):
    w = World(env)
    b = w.local(w.org1)
    code = env.cp.issue_code(w.owner1, w.org1)
    plan = env.bs.plan_flow_a(b, env.cp, code, env.inst, clock=env.clock)
    with pytest.raises(env.bs.Crash):
        env.bs.execute_flow_a(b, env.cp, env.store, plan, plan["plan_hash"], code, env.inst, clock=env.clock,
                              hook=lambda ph: (_ for _ in ()).throw(env.bs.Crash()) if ph == "redeemed" else None)
    env.bs.abort_flow(b, env.cp, env.store, plan["bootstrap_id"], env.inst)
    L = env.bs.LocalRegistry(b)
    assert L.sync_state == "UNBOUND" and not list(Path(env.store.root).glob("*")) and not env.bs.lock_held(b) and env.bs.lock_info(b) is None
    assert env.cp.get_run(plan["bootstrap_id"])["state"] == "rolled_back"


def test_flow_a_never_overwrites_nonempty_targets(env):
    w = World(env)
    # local nevid -> B (nu A)
    b = w.local(w.org1)
    insert_any_row(b)
    code = env.cp.issue_code(w.owner1, w.org1)
    with pytest.raises(env.bs.BlockedFlow) as e:
        env.bs.plan_flow_a(b, env.cp, code, env.inst, clock=env.clock)
    assert e.value.decision == "B" and env.bs.LocalRegistry(b).sync_state == "UNBOUND"
    # cloud nevid -> C
    b2 = w.local(w.org1, "C2")
    env.cp.create_cemetery(w.owner1, w.org1, b2.cemetery_uid, "C2")
    c = sqlite3.connect(env.cp.path)
    c.execute("INSERT INTO registry_rows VALUES (?,?,?)", (b2.cemetery_uid, "locuri_de_veci", "u1"))
    c.commit()
    c.close()
    with pytest.raises(env.bs.BlockedFlow) as e:
        env.bs.plan_flow_a(b2, env.cp, env.cp.issue_code(w.owner1, w.org1, b2.cemetery_uid), env.inst, clock=env.clock)
    assert e.value.decision == "C"
    # ambele nevide -> blocat
    b3 = w.local(w.org1, "C3")
    insert_any_row(b3)
    env.cp.create_cemetery(w.owner1, w.org1, b3.cemetery_uid, "C3")
    c = sqlite3.connect(env.cp.path)
    c.execute("INSERT INTO registry_rows VALUES (?,?,?)", (b3.cemetery_uid, "locuri_de_veci", "u2"))
    c.commit()
    c.close()
    with pytest.raises(env.bs.BlockedFlow) as e:
        env.bs.plan_flow_a(b3, env.cp, env.cp.issue_code(w.owner1, w.org1, b3.cemetery_uid), env.inst, clock=env.clock)
    assert e.value.decision == "BLOCKED"
    c = sqlite3.connect(env.cp.path)
    assert c.execute("SELECT COUNT(*) FROM registry_rows").fetchone()[0] == 2  # nimic șters/suprascris în cloud
    assert not c.execute("SELECT * FROM bindings").fetchall()
    c.close()


def test_flow_a_blocks_when_bound_and_cloud_emptied_alarm(env):
    w = World(env)
    b = w.local(w.org1)
    flow_a(env, w, b)
    assert env.bs.decide_flow("nonempty", "empty", "BOUND") == "BLOCKED_ALARM"
    with pytest.raises(env.bs.BlockedFlow) as e:
        env.bs.plan_flow_a(b, env.cp, env.cp.issue_code(w.owner1, w.org1, b.cemetery_uid), env.inst, clock=env.clock)
    assert e.value.decision == "ALREADY_BOUND"


def test_decision_matrix(env):
    d = env.bs.decide_flow
    assert d("empty", "empty") == "A" and d("nonempty", "empty") == "B" and d("empty", "nonempty") == "C"
    assert d("nonempty", "nonempty", digests_equal=True) == "LINK" and d("nonempty", "nonempty") == "BLOCKED"
    assert d("empty", "metadata_only") == "C" and d("metadata_only", "empty") == "B"
    assert d("empty", "empty", "REBUILD_REQUIRED") == "REBUILD" and d("nonempty", "empty", "BOUND") == "BLOCKED_ALARM"


def test_flow_a_code_from_other_org_refused(env):
    w = World(env)
    b = w.local(w.org1)
    code = env.cp.issue_code(w.owner2, w.org2)
    with pytest.raises(env.bs.BlockedFlow) as e:
        env.bs.plan_flow_a(b, env.cp, code, env.inst, clock=env.clock)
    assert e.value.decision == "SCOPE"


def test_flow_a_cemetery_uid_in_other_org_in_cloud_refused(env):
    w = World(env)
    b = w.local(w.org1)
    env.cp.create_cemetery(w.owner2, w.org2, b.cemetery_uid, "uid furat în altă org")
    with pytest.raises(env.cc.ScopeError):
        env.bs.plan_flow_a(b, env.cp, env.cp.issue_code(w.owner1, w.org1), env.inst, clock=env.clock)


def test_concurrent_second_bootstrap_on_same_registry_blocked(env):
    w = World(env)
    b = w.local(w.org1)
    code = env.cp.issue_code(w.owner1, w.org1)
    plan = env.bs.plan_flow_a(b, env.cp, code, env.inst, clock=env.clock)
    with pytest.raises(env.bs.Crash):
        env.bs.execute_flow_a(b, env.cp, env.store, plan, plan["plan_hash"], code, env.inst, clock=env.clock,
                              hook=lambda ph: (_ for _ in ()).throw(env.bs.Crash()) if ph == "after_begin" else None)
    code2 = env.cp.issue_code(w.owner1, w.org1)
    with pytest.raises(env.cc.BootstrapLocked):
        env.bs.acquire_lock(b, "alt")
    with pytest.raises(env.cc.BootstrapLocked):
        env.cp.begin_run(code2, "alt-pc", str(uuid.uuid4()), "A", b.cemetery_uid, "h")


# ================================================================== CLONĂ / EPOCĂ / local-first
def test_clone_detection_disables_sync_but_local_stays_usable(env):
    w = World(env)
    b = w.local(w.org1)
    flow_a(env, w, b)
    assert env.bs.sync_allowed(b, env.inst)
    assert not env.bs.sync_allowed(b, "alt-pc-uid")
    L = env.bs.LocalRegistry(b)
    assert L.sync_state == "REBUILD_REQUIRED"
    insert_any_row(b)  # editarea locală continuă
    assert L.inspect()["rows"] == 1
    with pytest.raises(env.bs.BootstrapError):
        env.bs.ping(b, env.cp, env.store, "alt-pc-uid")


def test_epoch_mismatch_moves_to_rebuild_required(env):
    w = World(env)
    b = w.local(w.org1)
    flow_a(env, w, b)
    c = sqlite3.connect(env.cp.path)
    c.execute("UPDATE bindings SET sync_epoch=2")
    c.commit()
    c.close()
    with pytest.raises(env.bs.BootstrapError, match="epoch_mismatch"):
        env.bs.ping(b, env.cp, env.store, env.inst)
    assert env.bs.LocalRegistry(b).sync_state == "REBUILD_REQUIRED"


def test_unbind_revokes_and_cleans_without_data_loss(env):
    w = World(env)
    b = w.local(w.org1)
    flow_a(env, w, b)
    L = env.bs.LocalRegistry(b)
    ref, key = L.get("cloud_secret_ref"), env.store.get(L.get("cloud_secret_ref"))
    env.bs.unbind(b, env.cp, env.store, w.owner1)
    assert L.sync_state == "UNBOUND" and env.store.get(ref) is None and L.get("cloud_binding_uid") == ""
    with pytest.raises(env.cc.ScopeError):
        env.cp.authenticate(key, b.cemetery_uid)
    assert env.cp.inspect_as(w.owner1, w.org1, b.cemetery_uid)["status"] == "ACTIVE"


def test_local_first_flow_a_works_without_network(env):
    """Control-plane local = zero rețea: modulele nu importă clienți HTTP/DB de rețea și nu menționează medii reale."""
    for name in ("cimitir_bootstrap.py", "cimitir_cloud_control.py", "cimitir_secretstore.py"):
        src = (ROOT / "shared" / name).read_text(encoding="utf-8")
        assert not re.search(r"^\s*(import|from)\s+(requests|httpx|urllib\.request|socket|psycopg|supabase|aiohttp)", src, re.M), name
        assert not re.search(r"supabase\.co|onrender\.com|postgres(ql)?://", src), name


# ================================================================== CROSS-TENANT
def test_cross_tenant_leakage_zero(env):
    w = World(env)
    b1, b2 = w.local(w.org1, "C1"), w.local(w.org2, "C2")
    _, p1, _ = flow_a(env, w, b1, owner=w.owner1)
    _, p2, _ = flow_a(env, w, b2, owner=w.owner2)
    k1 = env.store.get(env.bs.LocalRegistry(b1).get("cloud_secret_ref"))
    k2 = env.store.get(env.bs.LocalRegistry(b2).get("cloud_secret_ref"))

    def attempt(fn, expected):
        try:
            fn()
        except expected:
            return
        except Exception:  # alt tip de eroare înseamnă totuși refuz, dar contează să nu fie succes
            return
        LEAKS.append(fn)

    E = (env.cc.ScopeError, env.cc.PermissionDenied, env.cc.CodeError, env.cc.ConflictError)
    attempt(lambda: env.cp.authenticate(k1, b2.cemetery_uid), E)
    attempt(lambda: env.cp.authenticate(k2, b1.cemetery_uid), E)
    ghost = env.cp.inspect_as(w.owner1, w.org1, str(uuid.uuid4()))
    if env.cp.inspect_as(w.owner1, w.org1, b2.cemetery_uid) != ghost:
        LEAKS.append("inspect")
    attempt(lambda: env.cp.inspect_as(w.owner2, w.org1, b1.cemetery_uid), E)
    attempt(lambda: env.cp.issue_code(w.owner1, w.org2, b2.cemetery_uid), E)
    attempt(lambda: env.cp.issue_code(w.owner1, w.org1, b2.cemetery_uid), E)
    attempt(lambda: env.cp.redeem_code(env.cp.issue_code(w.owner1, w.org1), env.inst, "PC", b2.cemetery_uid), E)
    attempt(lambda: env.cp.revoke_binding(w.owner1, w.org1, env.bs.LocalRegistry(b2).get("cloud_binding_uid")), E)
    attempt(lambda: env.cp.revoke_binding(w.owner2, w.org1, env.bs.LocalRegistry(b1).get("cloud_binding_uid")), E)
    attempt(lambda: env.cp.mark_verifying(k1, p2["bootstrap_id"]), E)
    attempt(lambda: env.cp.rollback_run(k2, p1["bootstrap_id"]), E)
    attempt(lambda: env.cp.create_cemetery(w.owner1, w.org2, str(uuid.uuid4()), "x"), E)
    attempt(lambda: env.cp.grant_role(w.owner1, w.owner1, w.org2, env.cc.ADMIN), E)
    assert env.bs.ping(b1, env.cp, env.store, env.inst)["sync_epoch"] == 1
    assert env.bs.ping(b2, env.cp, env.store, env.inst)["sync_epoch"] == 1
    assert env.cp.inspect_as(w.owner1, w.org1, b1.cemetery_uid)["status"] == "ACTIVE"
    assert env.cp.inspect_as(w.owner2, w.org2, b2.cemetery_uid)["status"] == "ACTIVE"
    print(f"CROSS_TENANT_LEAKS={len(LEAKS)}")
    assert len(LEAKS) == 0
