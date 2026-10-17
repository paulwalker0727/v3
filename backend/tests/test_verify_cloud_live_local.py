# ruff: noqa: F811
"""Rulează scripts/verify_cloud_live.py (scriptul LIVE) împotriva stivei LOCALE (aplicația Cloud reală, uvicorn, PostgreSQL local, Supabase Storage SIMULAT).
Rezultatul este LOCAL — nu înlocuiește rularea pe Supabase TEST."""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

import cimitir_cloud_admin as ad
import cimitir_cloud_app as capp
from test_cloud_admin_pg import EMAIL, PW, Server  # noqa: F401
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_cleanup_race_pg import adm, pgdb  # noqa: F401
from test_etapa6b_field_storage import sup  # noqa: F401
from test_etapa6c_field_inbox_pg import fw  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PostgreSQL local de test indisponibil")
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_cloud_live.py"


def test_live_script_passes_against_local_stack(fw):
    org_a, org_b = fw["T"]["A"]["org"], fw["T"]["B"]["org"]
    with adm(fw["pg"]) as c:
        owner = c.execute("SELECT identity_uid FROM cp_org_roles WHERE organization_uid=%s AND role='owner'", (org_a,)).fetchone()[0]
        c.execute("TRUNCATE cp_admin_credentials CASCADE")
        c.execute("INSERT INTO cp_admin_credentials (identity_uid, email, display_name, password_hash) VALUES (%s,%s,'Owner',%s)", (owner, EMAIL, ad.hash_password(PW)))
    srv = Server(capp.create_app(fw["svc"], storage_probe=lambda s: True))
    try:
        env_ = {**os.environ, "CLOUD_URL": srv.url, "CIMITIR_LIVE_ADMIN_EMAIL": EMAIL, "CIMITIR_LIVE_ADMIN_PASSWORD": PW, "CIMITIR_LIVE_ORG_UID": org_a,
                "CIMITIR_LIVE_OTHER_ORG_UID": org_b, "CIMITIR_LIVE_CEMETERY_UID": fw["T"]["A"]["cem"], "CIMITIR_DB_ENGINE": "sqlite", "CIMITIR_MODE": "local"}
        r = subprocess.run([sys.executable, str(SCRIPT), "--yes-write-test-data", "--label", "LOCAL"], env=env_, capture_output=True, text=True, timeout=180)
    finally:
        srv.stop()
    out = r.stdout + r.stderr
    print(out)
    assert "[FAIL]" not in out and r.returncode == 0, out[-2500:]
    assert "SKIP=0" in out and "PASS=" in out
    assert uuid.UUID(org_a)
