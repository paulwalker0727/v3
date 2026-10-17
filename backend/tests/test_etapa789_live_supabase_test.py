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
STORAGE_LIVE = bool(os.environ.get("SUPABASE_SECRET_KEY") and os.environ.get("SUPABASE_BUCKET") and os.environ.get("SUPABASE_URL", "").startswith(f"https://{REF}.supabase.co"))
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


if STORAGE_LIVE:
    import cimitir_storage as _st

    @pytest.fixture()
    def sup():
        """Storage REAL (Supabase Storage al proiectului TEST, bucket privat); înlocuiește simulatorul din testele locale."""
        s = _st.SupabaseStorage(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"], os.environ["SUPABASE_BUCKET"])
        assert s.auth_ok(), "cheia Supabase nu autentifică"
        s.ensure_bucket()
        assert (s.bucket_info() or {}).get("public") is False, "bucketul trebuie să fie PRIVAT"
        return s


@pytest.mark.skipif(not STORAGE_LIVE, reason="SUPABASE_SECRET_KEY/SUPABASE_BUCKET lipsesc: Storage LIVE NETESTAT")
def test_live_storage_private_bucket_integrity_and_unauthorized_access():
    import hashlib
    import uuid

    import requests

    import cimitir_storage as st
    s = st.SupabaseStorage(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"], os.environ["SUPABASE_BUCKET"])
    assert s.auth_ok()
    assert s.ensure_bucket() in ("exists", "created")
    info = s.bucket_info()
    assert info["public"] is False
    key = f"docs/{uuid.uuid4()}/{uuid.uuid4()}/{uuid.uuid4().hex}"
    data = b"%PDF-1.4\nlive storage test\n%%EOF"
    s.put_bytes(key, data, "application/pdf")
    assert s.stat(key)["size"] == len(data)
    assert hashlib.sha256(b"".join(s.open_stream(key))).hexdigest() == hashlib.sha256(data).hexdigest()
    with pytest.raises(st.AlreadyExists):
        s.put_bytes(key, b"%PDF-altceva", "application/pdf", upsert=False)          # nu se suprascrie niciodată
    assert b"".join(s.open_stream(key)) == data
    # acces neautorizat: fără cheie, sau pe ruta publică a unui bucket privat
    base = os.environ["SUPABASE_URL"].rstrip("/") + "/storage/v1/object"
    bucket = os.environ["SUPABASE_BUCKET"]
    for url in (f"{base}/public/{bucket}/{key}", f"{base}/authenticated/{bucket}/{key}", f"{base}/{bucket}/{key}"):
        r = requests.get(url, timeout=30)
        assert r.status_code in (400, 401, 403, 404) and data not in r.content, (url.split("/object/")[0], r.status_code)
    r = requests.get(f"{base}/authenticated/{bucket}/{key}", headers={"apikey": "sb_publishable_fals"}, timeout=30)
    assert r.status_code in (400, 401, 403) and data not in r.content
    url = s.signed_url(key, 60)                                                     # URL semnat scurt: funcționează, dar nu conține cheia secretă
    assert os.environ["SUPABASE_SECRET_KEY"] not in url and requests.get(url, timeout=30).content == data
    s.delete(key)
    assert s.stat(key) is None
