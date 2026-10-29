# ruff: noqa: F811
"""Izolarea copiilor de test și validări de URL pentru asociere (simulator local SQLite; fără PostgreSQL/rețea)."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import cimitir_bootstrap as bs
import cimitir_cloud_associate as assoc
import cimitir_registry as reg
from test_etapa4_pas1 import World, env, flow_a  # noqa: F401


def test_supabase_address_is_rejected_as_backend_url():
    for u in ("https://abcdefghijklmnopqrst.supabase.co", "https://aws-0-eu.pooler.supabase.com", "abcdefghijklmnopqrst.supabase.co"):
        with pytest.raises(assoc.AssociationError) as ei:
            assoc.normalize_url(u)
        assert ei.value.kind == "url" and "Supabase" in str(ei.value)
    assert assoc.normalize_url("http://127.0.0.1:8086") == "http://127.0.0.1:8086"


def test_copied_registry_folder_cannot_reuse_original_binding(env):
    w = World(env)
    b = w.local(w.org1)
    flow_a(env, w, b)
    loc = bs.LocalRegistry(b)
    assert loc.sync_state == "BOUND" and loc.get("bound_root_fp") and bs.sync_allowed(b, env.inst)
    copy_root = Path(str(b.root) + "_COPIE_TEST")
    shutil.copytree(b.root, copy_root)
    clone = reg.Binding(b.organization_uid, b.cemetery_uid, copy_root, b.activation_id)
    assert bs.LocalRegistry(clone).get("cloud_secret_ref") == loc.get("cloud_secret_ref")        # copia moștenește setările…
    assert bs.root_matches(b) and not bs.root_matches(clone)                                   # …dar nu și dreptul de a le folosi
    assert bs.sync_allowed(clone, env.inst) is False and bs.LocalRegistry(clone).sync_state == "REBUILD_REQUIRED"
    assert bs.LocalRegistry(b).sync_state == "BOUND" and bs.sync_allowed(b, env.inst)           # originalul nu e afectat
    with pytest.raises(bs.BootstrapError):
        bs.ping(clone, env.cp, env.store, env.inst)


def test_legacy_binding_without_fingerprint_keeps_working(env):
    w = World(env)
    b = w.local(w.org1)
    flow_a(env, w, b)
    bs.LocalRegistry(b).clear("bound_root_fp")
    assert bs.root_matches(b) and bs.sync_allowed(b, env.inst)


def test_unbind_clears_fingerprint(env):
    w = World(env)
    b = w.local(w.org1)
    flow_a(env, w, b)
    bs.unbind(b, env.cp, env.store, w.owner1)
    assert bs.LocalRegistry(b).get("bound_root_fp") == ""


def test_sync_v2_connection_blocked_for_copied_registry(env, monkeypatch):
    desk = Path(__file__).resolve().parents[2] / "desktop"
    monkeypatch.syspath_prepend(str(desk))
    sv = pytest.importorskip("sync_v2")
    w = World(env)
    b = w.local(w.org1)
    flow_a(env, w, b)
    copy_root = Path(str(b.root) + "_COPIE2")
    shutil.copytree(b.root, copy_root)
    clone = reg.Binding(b.organization_uid, b.cemetery_uid, copy_root, b.activation_id)
    monkeypatch.setattr(reg, "current_binding", lambda: b)
    assert sv._registry_is_original() is True
    monkeypatch.setattr(reg, "current_binding", lambda: clone)
    assert sv._registry_is_original() is False
