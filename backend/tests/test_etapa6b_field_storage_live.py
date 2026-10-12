# ruff: noqa: F811
"""LIVE (sărit fără CIMITIR_LIVE_TESTS + CIMITIR_TEST_DATABASE_URL + SUPABASE_*). 6B pe Supabase TEST REAL: Storage privat `cimitir-private-test` + metadata în PostgreSQL (cp_storage_objects), ca `cimitir_cp_app` (fără BYPASSRLS).
Doar proiectul TEST; parola cimitir_cp_app e generată în memorie; cheia Storage vine doar din environment."""
from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend" / "tests"), str(ROOT / "shared"), str(ROOT / "backend" / "cimitir")]

import psycopg  # noqa: E402
import pytest  # noqa: E402
import requests  # noqa: E402
from psycopg import sql  # noqa: E402

import cimitir_bootstrap as bs  # noqa: E402
import cimitir_cloud_control as cc  # noqa: E402
import cimitir_cloud_control_pg as ccp  # noqa: E402
import cimitir_field_storage as fs  # noqa: E402
import cimitir_storage as st  # noqa: E402
from test_etapa4_pas1 import World, env  # noqa: E402,F401
from test_etapa4_pas1_closure import renv  # noqa: E402,F401

REF, PROD = "nhhczdmcocbobptogvsw", "txuxkjgftopznvmooexq"
BUCKET = "cimitir-private-test"
pytestmark = pytest.mark.skipif(not all(os.environ.get(k) for k in ("CIMITIR_LIVE_TESTS", "CIMITIR_TEST_DATABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_BUCKET")),
                                reason="Supabase TEST (DB + Storage) nu este configurat")
RES: dict = {}
T: dict = {}          # tenants A/B
SECRET_SEEN: list = []


def _png():
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 120, 200)).save(b, "PNG")
    return b.getvalue()


def _jpeg():
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (48, 48), (200, 30, 30)).save(b, "JPEG")
    return b.getvalue()


def _pdf():
    return b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n" + os.urandom(2048) + b"\n%%EOF\n"


def sha(b):
    return hashlib.sha256(b).hexdigest()


@pytest.fixture(scope="session")
def live():
    for k, v in os.environ.items():
        assert PROD not in (v or "").lower() or not (k.startswith(("CIMITIR_", "SUPABASE", "DATABASE")) or "URL" in k), k
    admin = os.environ["CIMITIR_TEST_DATABASE_URL"]
    u = urlparse(admin)
    assert PROD not in admin and u.username.endswith("." + REF) and "pooler.supabase.com" in u.hostname and os.environ["CIMITIR_TEST_PROJECT_REF"] == REF
    assert os.environ["SUPABASE_URL"] == f"https://{REF}.supabase.co" and os.environ["SUPABASE_BUCKET"] == BUCKET
    pw = secrets.token_urlsafe(48)
    with psycopg.connect(admin, autocommit=True) as c:
        v = c.pgconn.encrypt_password(pw.encode(), b"cimitir_cp_app", b"scram-sha-256").decode()
        c.execute(sql.SQL("ALTER ROLE cimitir_cp_app PASSWORD {}").format(sql.Literal(v)))
    app = u._replace(netloc=f"cimitir_cp_app.{REF}:{pw}@{u.hostname}:{u.port}").geturl()
    pw2 = secrets.token_urlsafe(48)                      # worker de cleanup (016): login membru al rolului intern cimitir_storage_cleanup; NU e cimitir_cp_app
    with psycopg.connect(admin, autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_roles WHERE rolname='cimitir_cleanup_test'").fetchone():
            c.execute("CREATE ROLE cimitir_cleanup_test LOGIN NOSUPERUSER NOBYPASSRLS IN ROLE cimitir_storage_cleanup")
        v2 = c.pgconn.encrypt_password(pw2.encode(), b"cimitir_cleanup_test", b"scram-sha-256").decode()
        c.execute(sql.SQL("ALTER ROLE cimitir_cleanup_test PASSWORD {}").format(sql.Literal(v2)))
    cleanup = u._replace(netloc=f"cimitir_cleanup_test.{REF}:{pw2}@{u.hostname}:{u.port}").geturl()
    os.environ["CIMITIR_CP_DATABASE_URL"], os.environ["CIMITIR_CP_ADMIN_DATABASE_URL"] = app, admin
    os.environ["CIMITIR_CP_CLEANUP_DATABASE_URL"] = cleanup
    os.environ["CIMITIR_ALLOW_TEST_SECRETSTORE"] = "1"
    store = st.SupabaseStorage(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"], BUCKET)
    yield {"admin": admin, "app": app, "storage": store}
    if os.environ.get("LIVE_REPORT"):
        open(os.environ["LIVE_REPORT"], "w").write(json.dumps({"RES": RES, "tenants": {k: {x: y for x, y in v.items() if x in ("org", "cem")} for k, v in T.items()}}, indent=1, default=str))


@pytest.fixture()
def penv(renv, live):
    renv.pg = live
    renv.cp = ccp.PostgresControlPlane(url=live["app"], admin_url=live["admin"])
    return renv


def mk(live):
    cp = ccp.PostgresControlPlane(url=live["app"], admin_url=live["admin"])
    return cp, fs.FieldMediaStore(cp, live["storage"], cleanup_url=os.environ["CIMITIR_CP_CLEANUP_DATABASE_URL"])


def ctxc(live, **ctx):
    c = psycopg.connect(live["app"], autocommit=True)
    for k, v in ctx.items():
        c.execute("SELECT set_config(%s, %s, false)", (f"cp.{k}", v))
    return c


def objects_in(live, t):
    return [o["name"] for o in live["storage"].list_prefix(f"field/{t['org']}/{t['cem']}")]


def up(store, t, data, mime, kind="document", name="f", uid=None):
    uid = uid or str(uuid.uuid4())
    store.begin_upload(t["key"], t["cem"], uid, name, mime, len(data), sha(data), kind)
    return uid, store.upload(t["key"], t["cem"], uid, data)


# ============================================================ setup: 2 tenante reale (Flow A) + bucket privat
def test_00_setup_tenants_and_private_bucket(penv, live):
    env = penv
    w = World(env)
    for tag, org, owner in (("A", w.org1, w.owner1), ("B", w.org2, w.owner2)):
        b = w.local(org, name=f"6B-TEST {tag}")
        code = env.cp.issue_code(owner, org)
        plan = bs.plan_flow_a(b, env.cp, code, env.inst, cemetery_name=f"6B-TEST {tag}")
        bs.execute_flow_a(b, env.cp, env.store, plan, plan["plan_hash"], code, env.inst)
        key = env.store.get(bs.LocalRegistry(b).get("cloud_secret_ref"))
        T[tag] = {"org": org, "cem": b.cemetery_uid, "key": key}
    json.dump({k: {"org": v["org"], "cem": v["cem"]} for k, v in T.items()}, open(os.environ.get("LIVE_MANIFEST", os.devnull), "w"))
    s = live["storage"]
    info = s.bucket_info()
    assert info["public"] is False and info["id"] == BUCKET
    SECRET_SEEN.append(os.environ["SUPABASE_SECRET_KEY"])
    # acces public / anonim refuzat
    probe = f"field/{T['A']['org']}/{T['A']['cem']}/{uuid.uuid4()}"
    s.put_bytes(probe, _pdf(), "application/pdf")
    base = s.base
    pub = requests.get(f"{base}/object/public/{BUCKET}/{probe}", timeout=20)
    anon = requests.get(f"{base}/object/authenticated/{BUCKET}/{probe}", timeout=20)
    anon2 = requests.get(f"{base}/object/{BUCKET}/{probe}", timeout=20)
    s.delete(probe)
    RES["PUBLIC STORAGE ACCESS"] = "NO" if all(r.status_code >= 400 for r in (pub, anon, anon2)) else "YES"
    RES["BUCKET PRIVATE"] = "PASS" if info["public"] is False and RES["PUBLIC STORAGE ACCESS"] == "NO" else "FAIL"
    assert RES["BUCKET PRIVATE"] == "PASS", (pub.status_code, anon.status_code, anon2.status_code)


# ============================================================ PDF / JPEG / PNG + SHA-256
@pytest.mark.parametrize("mime,maker,kind", [("application/pdf", _pdf, "document"), ("image/jpeg", _jpeg, "photo"), ("image/png", _png, "photo")])
def test_01_formats_roundtrip_via_signed_url(live, mime, maker, kind):
    cp, store = mk(live)
    t = T["A"]
    data = maker()
    uid, rep = up(store, t, data, mime, kind, f"doc.{mime.split('/')[1]}")
    assert rep["status"] == "READY" and rep["sha256"] == sha(data)
    info = store.download_url(t["key"], t["cem"], uid, 60)
    SECRET_SEEN.append(json.dumps(info))
    got = requests.get(info["url"], timeout=30)                      # fără niciun header de autentificare
    assert got.status_code == 200 and got.content == data and sha(got.content) == rep["sha256"]
    path = fs.storage_path(t["org"], t["cem"], uid)
    assert live["storage"].stat(path)["size"] == len(data)
    RES.setdefault("PDF/JPEG/PNG", []).append(f"{mime}: PASS")


def test_02_sha256_wrong_and_bad_content_never_reach_storage(live):
    cp, store = mk(live)
    t = T["A"]
    data = _pdf()
    uid = str(uuid.uuid4())
    store.begin_upload(t["key"], t["cem"], uid, "x.pdf", "application/pdf", len(data), "0" * 64)
    with pytest.raises(fs.HashMismatch):
        store.upload(t["key"], t["cem"], uid, data)
    assert store.status(t["key"], t["cem"], uid)["status"] == "FAILED"
    assert live["storage"].stat(fs.storage_path(t["org"], t["cem"], uid)) is None            # nimic scris
    # hash corect pe același uid (declarație nouă) = alt uid; pe același uid, declarația diferită e conflict
    with pytest.raises(fs.Conflict):
        store.begin_upload(t["key"], t["cem"], uid, "x.pdf", "application/pdf", len(data), sha(data))
    uid2, rep = up(store, t, data, "application/pdf")
    assert rep["status"] == "READY"
    # semnătură greșită (PNG declarat PDF), tip neacceptat, hash malformat, prea mare
    png = _png()
    u3 = str(uuid.uuid4())
    store.begin_upload(t["key"], t["cem"], u3, "x.pdf", "application/pdf", len(png), sha(png))
    with pytest.raises(fs.BadContent):
        store.upload(t["key"], t["cem"], u3, png)
    assert live["storage"].stat(fs.storage_path(t["org"], t["cem"], u3)) is None
    for bad in (dict(mime="text/html", size=10, sha256="a" * 64), dict(mime="application/pdf", size=10, sha256="zz"), dict(mime="application/pdf", size=fs.MAX_BYTES + 1, sha256="a" * 64)):
        with pytest.raises(fs.BadContent):
            store.begin_upload(t["key"], t["cem"], str(uuid.uuid4()), "x", bad["mime"], bad["size"], bad["sha256"])
    with pytest.raises(fs.HashMismatch):                      # conținut alterat față de declarație (aceeași mărime)
        u4 = str(uuid.uuid4())
        d = _pdf()
        store.begin_upload(t["key"], t["cem"], u4, "x.pdf", "application/pdf", len(d), sha(d))
        store.upload(t["key"], t["cem"], u4, d[:-1] + bytes([d[-1] ^ 1]))
    RES["SHA256"] = "PASS"


# ============================================================ idempotență, response loss, întrerupere + resume
def test_03_idempotent_retry_and_response_loss(live):
    cp, store = mk(live)
    t = T["A"]
    data = _jpeg()
    uid = str(uuid.uuid4())
    r1 = store.begin_upload(t["key"], t["cem"], uid, "p.jpg", "image/jpeg", len(data), sha(data), "photo")
    r2 = store.begin_upload(t["key"], t["cem"], uid, "p.jpg", "image/jpeg", len(data), sha(data), "photo")
    assert r1 == r2 and r1["status"] == "PENDING"
    a = store.upload(t["key"], t["cem"], uid, data)
    # response loss: clientul n-a primit răspunsul și repetă tot fluxul
    store.begin_upload(t["key"], t["cem"], uid, "p.jpg", "image/jpeg", len(data), sha(data), "photo")
    b = store.upload(t["key"], t["cem"], uid, data)
    assert a == b and b["status"] == "READY"
    rows = ctxc(live, org=t["org"], cems=t["cem"]).execute("SELECT COUNT(*) FROM cp_storage_objects WHERE uid=%s", (uid,)).fetchone()[0]
    assert rows == 1 and objects_in(live, t).count(uid) == 1
    assert requests.get(store.download_url(t["key"], t["cem"], uid)["url"], timeout=30).content == data
    RES["IDEMPOTENT RETRY"] = "PASS"
    RES["RESPONSE LOSS"] = "PASS"


def test_04_interruption_and_resume(live):
    cp, store = mk(live)
    t = T["A"]
    data = _pdf()
    uid = str(uuid.uuid4())
    store.begin_upload(t["key"], t["cem"], uid, "i.pdf", "application/pdf", len(data), sha(data))
    with pytest.raises(fs.Interrupted):
        store.upload(t["key"], t["cem"], uid, data, _crash_after_storage=True)
    path = fs.storage_path(t["org"], t["cem"], uid)
    assert store.status(t["key"], t["cem"], uid)["status"] == "PENDING" and live["storage"].stat(path) is not None
    with pytest.raises(fs.NotReady):
        store.download_url(t["key"], t["cem"], uid)
    rep = store.upload(t["key"], t["cem"], uid, data)                       # resume: obiectul există -> verificat -> READY
    assert rep["status"] == "READY" and objects_in(live, t).count(uid) == 1
    assert requests.get(store.download_url(t["key"], t["cem"], uid)["url"], timeout=30).content == data
    # resume cu obiect existent ALTERAT în Storage -> refuz (Conflict), nu se acceptă conținut străin
    d2 = _pdf()
    u2 = str(uuid.uuid4())
    store.begin_upload(t["key"], t["cem"], u2, "j.pdf", "application/pdf", len(d2), sha(d2))
    live["storage"].put_bytes(fs.storage_path(t["org"], t["cem"], u2), _pdf()[:len(d2)].ljust(len(d2), b"x"), "application/pdf")
    with pytest.raises(fs.Conflict):
        store.upload(t["key"], t["cem"], u2, d2)
    assert store.status(t["key"], t["cem"], u2)["status"] == "FAILED"
    RES["INTERRUPTION/RESUME"] = "PASS"


# ============================================================ orfani / staging
def test_05_orphan_cleanup_is_tenant_scoped(live):
    cp, store = mk(live)
    A, B = T["A"], T["B"]
    s = live["storage"]
    ready_uid, _ = up(store, A, _pdf(), "application/pdf")                     # READY: nu se atinge
    o_a = str(uuid.uuid4())
    s.put_bytes(fs.storage_path(A["org"], A["cem"], o_a), _pdf(), "application/pdf")   # obiect orfan (fără rând) în A
    o_b = str(uuid.uuid4())
    s.put_bytes(fs.storage_path(B["org"], B["cem"], o_b), _pdf(), "application/pdf")   # orfan în B
    d = _pdf()
    crash = str(uuid.uuid4())
    store.begin_upload(A["key"], A["cem"], crash, "c.pdf", "application/pdf", len(d), sha(d))
    with pytest.raises(fs.Interrupted):
        store.upload(A["key"], A["cem"], crash, d, _crash_after_storage=True)           # rând PENDING + obiect
    nothing = str(uuid.uuid4())
    store.begin_upload(A["key"], A["cem"], nothing, "n.pdf", "application/pdf", len(d), sha(d))   # rând PENDING fără obiect
    time.sleep(1.5)
    expected_stale = ctxc(live, org=A["org"], cems=A["cem"]).execute("SELECT COUNT(*) FROM cp_storage_objects WHERE cemetery_uid=%s AND status IN ('PENDING','UPLOADED','FAILED')", (A["cem"],)).fetchone()[0]
    assert expected_stale >= 2                                                         # include rândurile abandonate/eșuate din testele anterioare
    rep = store.cleanup_orphans(A["key"], A["cem"], older_than_s=1)
    assert rep == {"stale_rows": expected_stale, "orphan_objects": 1}, rep
    na = objects_in(live, A)
    assert ready_uid in na and o_a not in na and crash not in na
    assert store.status(A["key"], A["cem"], crash)["status"] == "DELETED" and store.status(A["key"], A["cem"], nothing)["status"] == "DELETED"
    assert o_b in objects_in(live, B)                                                  # cleanup-ul lui A nu atinge B
    assert store.cleanup_orphans(A["key"], A["cem"], older_than_s=1) == {"stale_rows": 0, "orphan_objects": 0}   # idempotent
    rep_b = store.cleanup_orphans(B["key"], B["cem"], older_than_s=1)
    assert rep_b["orphan_objects"] == 1 and o_b not in objects_in(live, B)
    RES["ORPHAN CLEANUP"] = "PASS"


# ============================================================ izolare tenant
def test_06_cross_tenant_storage_isolation(live):
    cp, store = mk(live)
    A, B = T["A"], T["B"]
    a_data = _pdf()
    a_uid, _ = up(store, A, a_data, "application/pdf")
    leaks = 0
    with pytest.raises(cc.ScopeError):                                  # cheia lui B pe cimitirul lui A
        store.status(B["key"], A["cem"], a_uid)
    with pytest.raises(cc.ScopeError):
        store.begin_upload(B["key"], A["cem"], str(uuid.uuid4()), "x.pdf", "application/pdf", 5, "a" * 64)
    assert store.status(B["key"], B["cem"], a_uid) is None               # uid-ul lui A nu e vizibil lui B
    for fn in (lambda: store.download_url(B["key"], B["cem"], a_uid), lambda: store.delete(B["key"], B["cem"], a_uid)):
        with pytest.raises(cc.ScopeError):
            fn()
    # B încarcă același uid în cimitirul lui: cale diferită, A rămâne neatins
    b_data = _pdf()
    store.begin_upload(B["key"], B["cem"], a_uid, "b.pdf", "application/pdf", len(b_data), sha(b_data))
    store.upload(B["key"], B["cem"], a_uid, b_data)
    assert requests.get(store.download_url(A["key"], A["cem"], a_uid)["url"], timeout=30).content == a_data
    assert requests.get(store.download_url(B["key"], B["cem"], a_uid)["url"], timeout=30).content == b_data
    # SQL direct ca cimitir_cp_app, context B
    cb = ctxc(live, org=B["org"], cems=B["cem"])
    leaks += cb.execute("SELECT COUNT(*) FROM cp_storage_objects WHERE cemetery_uid=%s", (A["cem"],)).fetchone()[0]
    assert cb.execute("UPDATE cp_storage_objects SET status='DELETED' WHERE cemetery_uid=%s", (A["cem"],)).rowcount == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege):          # 016: cimitir_cp_app nu mai are DELETE (cleanup = rol/funcții separate)
        cb.execute("DELETE FROM cp_storage_objects WHERE cemetery_uid=%s", (A["cem"],))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        cb.execute("SELECT * FROM cp_storage_cleanup_claim(%s,%s,%s,0,10,900)", (A["org"], A["cem"], str(uuid.uuid4())))
    with pytest.raises(psycopg.errors.Error):
        cb.execute("INSERT INTO cp_storage_objects (cemetery_uid, uid, organization_uid, kind, filename, mime, size, sha256, storage_path, status) "
                   "VALUES (%s,%s,%s,'document','x','application/pdf',1,%s,%s,'READY')", (A["cem"], str(uuid.uuid4()), A["org"], "a" * 64, "field/x/y/z"))
    ca = ctxc(live, org=A["org"], cems=A["cem"])
    leaks += ca.execute("SELECT COUNT(*) FROM cp_storage_objects WHERE cemetery_uid=%s", (B["cem"],)).fetchone()[0]
    assert not ctxc(live).execute("SELECT 1 FROM cp_storage_objects LIMIT 1").fetchone()          # fără context: nimic
    # obiectele în Storage: prefixele sunt separate pe tenant
    assert all(o not in objects_in(live, B) for o in [n for n in objects_in(live, A) if n != a_uid])
    RES["CROSS TENANT STORAGE LEAKAGE"] = leaks
    assert leaks == 0


# ============================================================ signed URL scurt + secrete
def test_07_signed_url_short_lived_and_no_secret_exposure(live):
    cp, store = mk(live)
    A = T["A"]
    data = _png()
    uid, _ = up(store, A, data, "image/png", "photo")
    capped = store.download_url(A["key"], A["cem"], uid, ttl=99999)
    assert capped["expires_in"] == fs.MAX_SIGNED_TTL
    short = store.download_url(A["key"], A["cem"], uid, ttl=2)
    assert requests.get(short["url"], timeout=20).status_code == 200
    time.sleep(4)
    assert requests.get(short["url"], timeout=20).status_code >= 400                  # expirat
    SECRET_SEEN.append(json.dumps([capped, short]))
    key = os.environ["SUPABASE_SECRET_KEY"]
    for blob in SECRET_SEEN[1:] + [short["url"], capped["url"]]:
        assert key not in blob and "sb_secret" not in blob and "apikey" not in blob.lower()
    # erorile către client nu conțin cheia
    try:
        store.download_url(A["key"], A["cem"], str(uuid.uuid4()))
    except Exception as e:  # noqa: BLE001
        assert key not in str(e)
    bad_store = st.SupabaseStorage(os.environ["SUPABASE_URL"], key, BUCKET)
    bad_store.headers = {"apikey": "sb_secret_gresita"}
    try:
        bad_store.stat("field/x")
    except Exception as e:  # noqa: BLE001
        assert key not in str(e)
    # semnat pentru obiect șters / ne-READY: refuz
    store.delete(A["key"], A["cem"], uid)
    with pytest.raises(fs.NotReady):
        store.download_url(A["key"], A["cem"], uid)
    assert live["storage"].stat(fs.storage_path(A["org"], A["cem"], uid)) is None
    RES["SERVICE ROLE EXPOSED"] = "NO"
    RES["SIGNED URL SHORT"] = "PASS"


def test_99_role_still_restricted_and_summary(live):
    c = psycopg.connect(live["app"], autocommit=True)
    assert c.execute("select rolsuper, rolbypassrls from pg_roles where rolname=current_user").fetchone() == (False, False)
    RES["6B STORAGE"] = "PASS"
