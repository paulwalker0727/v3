# ruff: noqa: F811, F403, F401
"""Rulează suita Etapelor 7-9 (PII, documente, circuit MAIN->PG->Mobile->PG->MAIN, izolare/RLS, e2e MAIN, PWA) pe PostgreSQL-ul LIVE al proiectului Supabase TEST (rol runtime cimitir_cp_app, RLS activ).

Doar cu: CIMITIR_LIVE_TESTS=1, CIMITIR_TEST_PROJECT_REF=<ref>, CIMITIR_CP_ADMIN_DATABASE_URL (postgres.<ref>), CIMITIR_CP_DATABASE_URL (cimitir_cp_app.<ref>) și un ref NEprotejat.
Ce este LIVE: PostgreSQL + RLS + rolul runtime. Ce NU este LIVE: Storage-ul (server Supabase Storage SIMULAT în memorie, `sup`) — vezi raportul. ATENȚIE: testele golesc (TRUNCATE) tabelele proiectului TEST; rulează-le înainte de seed-ul fictiv."""
from __future__ import annotations

import os

import pytest

import cimitir_test_guard as guard

ADMIN = os.environ.get("CIMITIR_CP_ADMIN_DATABASE_URL", "")
APP = os.environ.get("CIMITIR_CP_DATABASE_URL", "")
REF = os.environ.get("CIMITIR_TEST_PROJECT_REF", "")
ok = bool(os.environ.get("CIMITIR_LIVE_TESTS") == "1" and REF and REF not in guard.PROTECTED_PROJECT_REFS and f".{REF}" in ADMIN and f".{REF}" in APP and "supabase.com" in ADMIN)
pytestmark = pytest.mark.skipif(not ok, reason="Supabase TEST indisponibil (setează CIMITIR_LIVE_TESTS=1 și variabilele CIMITIR_*)")


from test_etapa789_docs_main_e2e import *
from test_etapa789_docs_main_e2e import dworld
from test_etapa789_pii_docs_pg import *
from test_etapa789_pii_docs_pg import dx
from test_etapa789_pwa_photos import *
from test_etapa789_pwa_photos import web


@pytest.fixture(scope="module")
def pgdb():
    yield {"name": "supabase-test", "admin": ADMIN, "app": APP, "worker": APP}
