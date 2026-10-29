# ruff: noqa: F811
"""Livrarea 2 — izolarea organizațiilor la nivel de bază de date (RLS), cu rolul de runtime al aplicației Cloud (NOSUPERUSER/NOBYPASSRLS), după o încărcare reală Flux B.
LOCAL-PG (PostgreSQL local, migrările 001–026). Nu este Supabase real."""
from __future__ import annotations

import os
import uuid

import psycopg
import pytest

from test_cloud_associate_flux_b_pg import cb  # noqa: F401
from test_cloud_associate_pg import ca, code, make  # noqa: F401
from test_cloud_admin_pg import EMAIL, PW  # noqa: F401
from test_etapa4_pas1 import env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_cleanup_race_pg import adm, app, pgdb  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PostgreSQL local de test indisponibil")

TABLES = ("cp_registry_rows", "cp_registry_row_data", "cp_media_objects", "cp_audit_log")


def _count(c, t):
    return c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]


def test_runtime_role_is_not_superuser_and_cannot_bypass_rls(cb):
    with app(cb["pg"]) as c:
        r = c.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user").fetchone()
    assert r == (False, False)
    with adm(cb["pg"]) as c:
        for t in TABLES + ("organizations", "cemeteries", "devices"):
            assert c.execute("SELECT relrowsecurity FROM pg_class WHERE relname=%s", (t,)).fetchone()[0] is True, t


def test_tenant_data_visible_only_in_its_own_context_after_flux_b_upload(cb):
    a, c0 = make(cb), code(cb)
    p = a.plan(c0, "Craiova TEST")
    a.execute(p["plan"], c0, True)
    org1, cem1, org2 = cb["b"].organization_uid, cb["b"].cemetery_uid, cb["w"].org2
    cem2 = str(uuid.uuid4())
    cb["r"].cp.create_cemetery(cb["w"].owner2, org2, cem2, "Cimitir org 2")
    with adm(cb["pg"]) as c:
        total = {t: _count(c, t) for t in TABLES}
    assert total["cp_registry_rows"] > 0 and total["cp_media_objects"] > 0
    with app(cb["pg"]) as c:                                                   # fără context: nimic
        assert {t: _count(c, t) for t in TABLES[:3]} == {t: 0 for t in TABLES[:3]}
    with app(cb["pg"], org2, cem2) as c:                                       # altă organizație: nimic din org1
        assert _count(c, "cp_registry_rows") == 0 and _count(c, "cp_media_objects") == 0
        assert c.execute("SELECT count(*) FROM cp_registry_rows WHERE cemetery_uid=%s", (cem1,)).fetchone()[0] == 0
    with app(cb["pg"], org2, cem1) as c:                                       # organizație străină + cimitirul altuia: tot nimic
        assert _count(c, "cp_registry_rows") == 0
    with app(cb["pg"], org1, cem1) as c:                                       # contextul corect: exact datele încărcate
        assert _count(c, "cp_registry_rows") == total["cp_registry_rows"] and _count(c, "cp_media_objects") == total["cp_media_objects"]


def test_cross_tenant_writes_and_updates_are_denied_by_policy(cb):
    a, c0 = make(cb), code(cb)
    a.execute(a.plan(c0, "Craiova TEST")["plan"], c0, True)
    org1, cem1, org2 = cb["b"].organization_uid, cb["b"].cemetery_uid, cb["w"].org2
    cem2 = str(uuid.uuid4())
    cb["r"].cp.create_cemetery(cb["w"].owner2, org2, cem2, "Cimitir org 2")
    with adm(cb["pg"]) as c:
        before = _count(c, "cp_registry_rows")
    with app(cb["pg"], org2, cem2) as c:
        with pytest.raises(psycopg.errors.Error):                              # INSERT în cimitirul altei organizații
            c.execute("INSERT INTO cp_registry_rows (organization_uid, cemetery_uid, entity, uid) VALUES (%s,%s,'locuri_de_veci',%s)", (org1, cem1, str(uuid.uuid4())))
        assert c.execute("UPDATE cp_registry_rows SET entity=entity").rowcount == 0     # UPDATE/DELETE nu văd rândurile altora
        assert c.execute("DELETE FROM cp_registry_rows").rowcount == 0
    with adm(cb["pg"]) as c:
        assert _count(c, "cp_registry_rows") == before
