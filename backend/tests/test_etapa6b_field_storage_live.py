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
PRE: dict = {}        # snapshot al obiectelor Storage preexistente (înainte de rulare)
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
    pw2 = secrets.token_urlsafe(48)                      # worker de cleanup (016): login membru al rolului intern cimitir_storage_cleanup_exec (doar EXECUTE pe funcțiile de cleanup); NU e cimitir_cp_app
    with psycopg.connect(admin, autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_roles WHERE rolname='cimitir_cleanup_test'").fetchone():
            c.execute("CREATE ROLE cimitir_cleanup_test LOGIN NOSUPERUSER NOBYPASSRLS IN ROLE cimitir_storage_cleanup_exec")
        v2 = c.pgconn.encrypt_password(pw2.encode(), b"cimitir_cleanup_test", b"scram-sha-256").decode()
        c.execute(sql.SQL("ALTER ROLE cimitir_cleanup_test PASSWORD {}").format(sql.Literal(v2)))
    cleanup = u._replace(netloc=f"cimitir_cleanup_test.{REF}:{pw2}@{u.hostname}:{u.port}").geturl()
    os.environ["CIMITIR_CP_DATABASE_URL"], os.environ["CIMITIR_CP_ADMIN_DATABASE_URL"] = app, admin
    os.environ["CIMITIR_CP_CLEANUP_DATABASE_URL"] = cleanup
    os.environ["CIMITIR_ALLOW_TEST_SECRETSTORE"] = "1"
    store = st.SupabaseStorage(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"], BUCKET)
    yield {"admin": admin, "app": app, "cleanup": cleanup, "storage": store}
    with psycopg.connect(admin, autocommit=True) as c:                    # rolul de login de test nu rămâne pe TEST după rulare
        c.execute("DROP ROLE IF EXISTS cimitir_cleanup_test")
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


def walk(store, prefix=""):
    """Toate obiectele din bucket (recursiv): [(cale, mărime)]."""
    out, stack = [], [prefix]
    while stack:
        p, off = stack.pop(), 0
        while True:
            page = store._req("POST", f"{store.base}/object/list/{store.bucket}", "list", json={"prefix": p, "limit": 100, "offset": off}).json()
            for o in page:
                full = f"{p}/{o['name']}" if p else o["name"]
                if o.get("id"):
                    out.append((full, (o.get("metadata") or {}).get("size")))
                else:
                    stack.append(full)
            if len(page) < 100:
                break
            off += 100
    return sorted(out)


def aconn(live):
    return psycopg.connect(live["admin"], autocommit=True)


def wconn():
    return psycopg.connect(os.environ["CIMITIR_CP_CLEANUP_DATABASE_URL"], autocommit=True)


def st_of(live, t, uid):
    with aconn(live) as c:
        r = c.execute("SELECT status FROM cp_storage_objects WHERE cemetery_uid=%s AND uid=%s", (t["cem"], uid)).fetchone()
        return r[0] if r else None


def age_row(live, t, uid, secs=7200):
    with aconn(live) as c:
        c.execute("UPDATE cp_storage_objects SET updated_at = now() - make_interval(secs => %s) WHERE cemetery_uid=%s AND uid=%s", (secs, t["cem"], uid))


def claim(w, t, token=None, older=3600, limit=100, lease=900):
    token = token or str(uuid.uuid4())
    return token, w.execute("SELECT out_uid, out_storage_path, out_prev_status FROM cp_storage_cleanup_claim(%s,%s,%s,%s,%s,%s)", (t["org"], t["cem"], token, older, limit, lease)).fetchall()


def staged(live, store, t, put=True, data=None):
    """Rând STAGING (PENDING) îmbătrânit; opțional obiectul deja scris în Storage (ca după o întrerupere)."""
    data = data or _pdf()
    uid = str(uuid.uuid4())
    store.begin_upload(t["key"], t["cem"], uid, "r.pdf", "application/pdf", len(data), sha(data))
    if put:
        live["storage"].put_bytes(fs.storage_path(t["org"], t["cem"], uid), data, "application/pdf")
    age_row(live, t, uid)
    return uid, data


READY_CAS = "UPDATE cp_storage_objects SET status='READY', ready_at=now(), updated_at=now() WHERE cemetery_uid=%s AND uid=%s AND status IN ('PENDING','UPLOADED')"


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
    PRE["objects"] = walk(s)                                                        # obiectele preexistente: trebuie să rămână intacte
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


def test_01b_size_limit_25_mib_exact_ok_and_over_limit_rejected(live):
    cp, store = mk(live)
    t = T["A"]
    data = b"%PDF-" + os.urandom(fs.MAX_BYTES - 5)
    assert len(data) == fs.MAX_BYTES == 25 * 1024 * 1024
    uid, rep = up(store, t, data, "application/pdf", name="max.pdf")
    assert rep["status"] == "READY" and live["storage"].stat(fs.storage_path(t["org"], t["cem"], uid))["size"] == fs.MAX_BYTES
    store.delete(t["key"], t["cem"], uid)                                           # eliberez 25 MiB din bucket
    assert live["storage"].stat(fs.storage_path(t["org"], t["cem"], uid)) is None
    with pytest.raises(fs.BadContent):
        store.begin_upload(t["key"], t["cem"], str(uuid.uuid4()), "big.pdf", "application/pdf", fs.MAX_BYTES + 1, "a" * 64)
    RES["LIMIT 25 MiB"] = "PASS"


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


# ============================================================ 016 — HIGH 1: autorizare cleanup (rol separat, funcții înguste)
def test_08_high1_cleanup_authorization(live):
    cp, store = mk(live)
    A, B = T["A"], T["B"]
    s = live["storage"]
    leak = 0
    a_uid, a_data = staged(live, store, A)
    b_uid, b_data = staged(live, store, B)
    a_ready, _ = up(store, A, _pdf(), "application/pdf")
    b_ready, _ = up(store, B, _pdf(), "application/pdf")
    a_imp, _ = up(store, A, _pdf(), "application/pdf")
    with aconn(live) as c:
        c.execute("UPDATE cp_storage_objects SET status='IMPORTED' WHERE cemetery_uid=%s AND uid=%s", (A["cem"], a_imp))
    age_row(live, A, a_ready), age_row(live, A, a_imp), age_row(live, B, b_ready)
    with wconn() as w:
        # org A nu poate opera pe cimitirul lui B (și invers): perechea (org, cimitir) e verificată de DB
        for org, cem in ((A["org"], B["cem"]), (B["org"], A["cem"])):
            for sql_, args in (("SELECT * FROM cp_storage_cleanup_claim(%s,%s,%s,3600,10,900)", (org, cem, str(uuid.uuid4()))),
                               ("SELECT cp_storage_cleanup_claim_object(%s,%s,%s,%s,0,99999)", (org, cem, b_uid, str(uuid.uuid4()))),
                               ("SELECT cp_storage_cleanup_finish(%s,%s,%s,%s)", (org, cem, b_uid, str(uuid.uuid4()))),
                               ("SELECT cp_storage_cleanup_release(%s,%s,%s,%s)", (org, cem, b_uid, str(uuid.uuid4())))):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    w.execute(sql_, args)
        # claim pe A ia DOAR STAGING-ul lui A: nici B, nici READY, nici IMPORTED
        tok, rows = claim(w, A)
        got = {r[0] for r in rows}
        leak += len(got & {b_uid, b_ready}) + len(got & {a_ready, a_imp})
        assert got == {a_uid} and all(r[1] == fs.storage_path(A["org"], A["cem"], r[0]) for r in rows)
        # claim_object nu poate revendica READY/IMPORTED/rând existent; uid de-al lui B nu atinge B
        for u in (a_ready, a_imp, a_uid):
            assert w.execute("SELECT cp_storage_cleanup_claim_object(%s,%s,%s,%s,0,99999)", (A["org"], A["cem"], u, str(uuid.uuid4()))).fetchone()[0] is None
        # path arbitrar: nicio funcție nu primește cale; nume invalide/neuuid nu se revendică
        for bad in ("../../x", "field/x/y/z", "not-a-uuid", ""):
            assert w.execute("SELECT cp_storage_cleanup_claim_object(%s,%s,%s,%s,0,99999)", (A["org"], A["cem"], bad, str(uuid.uuid4()))).fetchone()[0] is None
        # ștergerea doar după claim valid: finish cu token greșit = false
        assert w.execute("SELECT cp_storage_cleanup_finish(%s,%s,%s,%s)", (A["org"], A["cem"], a_uid, str(uuid.uuid4()))).fetchone()[0] is False
        assert st_of(live, A, a_uid) == "CLEANING"
        # al doilea worker nu poate revendica același obiect
        with wconn() as w2:
            _, rows2 = claim(w2, A)
            assert rows2 == []
            assert w2.execute("SELECT cp_storage_cleanup_finish(%s,%s,%s,%s)", (A["org"], A["cem"], a_uid, tok)).fetchone()[0] is True      # token valid (orice conexiune a rolului) închide
        s.delete(fs.storage_path(A["org"], A["cem"], a_uid))
    assert st_of(live, A, a_uid) == "DELETED" and a_uid not in objects_in(live, A)
    # tenantul B și obiectele READY/IMPORTED rămân neatinse
    nb, na = objects_in(live, B), objects_in(live, A)
    leak += (b_uid not in nb) + (b_ready not in nb) + (a_ready not in na) + (a_imp not in na)
    assert st_of(live, B, b_uid) == "PENDING" and st_of(live, B, b_ready) == "READY" and st_of(live, A, a_imp) == "IMPORTED" and st_of(live, A, a_ready) == "READY"
    # prin FieldMediaStore: cheia lui A nu poate rula cleanup/delete pe B; clientul normal (cimitir_cp_app) nu poate invoca cleanup
    for fn in (lambda: store.cleanup_orphans(A["key"], B["cem"], older_than_s=3600), lambda: store.delete(A["key"], B["cem"], b_uid),
               lambda: store.cleanup_orphans(B["key"], A["cem"], older_than_s=3600), lambda: store.delete(B["key"], A["cem"], a_ready)):
        with pytest.raises(cc.ScopeError):
            fn()
    ca = ctxc(live, org=A["org"], cems=A["cem"])
    for sql_, args in (("SELECT * FROM cp_storage_cleanup_claim(%s,%s,%s,3600,10,900)", (A["org"], A["cem"], str(uuid.uuid4()))),
                       ("SELECT cp_storage_cleanup_claim_object(%s,%s,%s,%s,0,99999)", (A["org"], A["cem"], a_ready, str(uuid.uuid4())))):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            ca.execute(sql_, args)
    assert ca.execute("UPDATE cp_storage_objects SET status='CLEANING', cleanup_token=%s, cleanup_prev_status='PENDING', cleanup_claimed_at=now() WHERE uid=%s", (str(uuid.uuid4()), b_uid)).rowcount == 0   # RLS: rândul lui B nu e vizibil
    with pytest.raises(psycopg.errors.CheckViolation):                                  # pe propriul rând, aplicația nu poate forța CLEANING
        ca.execute("UPDATE cp_storage_objects SET status='CLEANING', cleanup_token=%s, cleanup_prev_status='PENDING', cleanup_claimed_at=now() WHERE uid=%s", (str(uuid.uuid4()), a_ready))
    with pytest.raises(fs.Conflict):
        store.delete(A["key"], A["cem"], a_imp)                                         # IMPORTED nu se șterge
    # obiecte în afara prefixului / nume ne-UUID în prefix nu sunt șterse de cleanup
    outside, junk = f"field/{B['org']}/{B['cem']}/not-a-uuid.pdf", f"field/{A['org']}/{A['cem']}/not-a-uuid.pdf"
    for k in (outside, junk):
        s.put_bytes(k, _pdf(), "application/pdf")
    store.cleanup_orphans(A["key"], A["cem"], older_than_s=3600)
    assert s.stat(outside) is not None and s.stat(junk) is not None
    s.delete(outside), s.delete(junk)
    RES["CROSS TENANT CLEANUP LEAKAGE"] = leak
    RES["HIGH 1 LIVE"] = "PASS" if leak == 0 else "FAIL"
    assert leak == 0


def test_09_high2_scenario_A_finalize_wins_cleanup_loses(live):
    import threading
    cp, store = mk(live)
    A = T["A"]
    s = live["storage"]
    rounds = 6
    for _ in range(rounds):
        uid, data = staged(live, store, A, put=True)
        path = fs.storage_path(A["org"], A["cem"], uid)
        up_conn = ctxc(live, org=A["org"], cems=A["cem"])
        up_conn.execute("BEGIN")
        assert up_conn.execute(READY_CAS, (A["cem"], uid)).rowcount == 1               # finalize a câștigat (tranzacție reală, necomisă)
        res = {}

        def cleanup():
            with wconn() as w:
                res["rows"] = claim(w, A)[1]

        th = threading.Thread(target=cleanup)
        th.start()
        th.join(60)
        assert not th.is_alive() and uid not in [r[0] for r in res["rows"]]            # cleanup pierde (rândul e blocat/READY)
        up_conn.execute("COMMIT")
        with wconn() as w:
            assert uid not in [r[0] for r in claim(w, A)[1]]                            # nici după commit
        assert st_of(live, A, uid) == "READY" and s.stat(path)["size"] == len(data)
        assert requests.get(store.download_url(A["key"], A["cem"], uid)["url"], timeout=30).content == data
    RES["SCENARIO A rounds"] = rounds


def test_10_high2_scenario_B_cleanup_wins_finalize_refused(live):
    import threading
    cp, store = mk(live)
    A = T["A"]
    s = live["storage"]
    rounds = 6
    for _ in range(rounds):
        uid, data = staged(live, store, A, put=False)
        path = fs.storage_path(A["org"], A["cem"], uid)
        res = {}
        w = wconn()
        w.execute("BEGIN")
        assert [r[0] for r in claim(w, A)[1]] == [uid]                                  # cleanup a revendicat atomic (necomis)

        def finalize():
            with ctxc(live, org=A["org"], cems=A["cem"]) as c:
                res["rc"] = c.execute(READY_CAS, (A["cem"], uid)).rowcount            # se blochează pe rândul CLEANING necomis

        th = threading.Thread(target=finalize)
        th.start()
        time.sleep(1.0)
        assert th.is_alive()                                                            # finalize chiar așteaptă lacătul (nu a citit o stare veche)
        w.execute("COMMIT")
        th.join(60)
        assert res["rc"] == 0 and st_of(live, A, uid) == "CLEANING"                    # refuzat controlat, rândul NU e READY
        with pytest.raises(fs.Conflict):                                                # și prin API: upload pe rând revendicat -> Conflict, nimic scris
            store.upload(A["key"], A["cem"], uid, data)
        assert s.stat(path) is None
        with pytest.raises(psycopg.errors.CheckViolation):
            aconn(live).execute("UPDATE cp_storage_objects SET status='READY' WHERE cemetery_uid=%s AND uid=%s", (A["cem"], uid))
        with aconn(live) as c:
            token = c.execute("SELECT cleanup_token FROM cp_storage_objects WHERE cemetery_uid=%s AND uid=%s", (A["cem"], uid)).fetchone()[0]
        s.delete(path)
        assert w.execute("SELECT cp_storage_cleanup_finish(%s,%s,%s,%s)", (A["org"], A["cem"], uid, str(token))).fetchone()[0] is True      # cleanup termină
        w.close()
        assert st_of(live, A, uid) == "DELETED" and s.stat(path) is None
        with pytest.raises(fs.NotReady):
            store.download_url(A["key"], A["cem"], uid)
    RES["SCENARIO B rounds"] = rounds


def test_11_high2_race_stress_and_double_worker(live):
    import threading
    cp, store = mk(live)
    A = T["A"]
    s = live["storage"]
    wins = {"finalize": 0, "cleanup": 0, "none": 0}
    rounds = 24
    for _ in range(rounds):
        uid, data = staged(live, store, A, put=True)
        path = fs.storage_path(A["org"], A["cem"], uid)
        c_up, c_cl = ctxc(live, org=A["org"], cems=A["cem"]), wconn()               # conexiuni deschise ÎNAINTE de barieră: se întrec doar instrucțiunile
        bar, out = threading.Barrier(2), {}

        def up_side():
            bar.wait()
            out["up"] = c_up.execute(READY_CAS, (A["cem"], uid)).rowcount

        def cl_side():
            bar.wait()
            out["cl"] = [r[0] for r in claim(c_cl, A)[1]].count(uid)

        th = [threading.Thread(target=up_side), threading.Thread(target=cl_side)]
        [x.start() for x in th]
        [x.join(60) for x in th]
        assert out["up"] + out["cl"] <= 1, out                                          # niciodată ambele
        final = st_of(live, A, uid)
        if out["up"]:
            wins["finalize"] += 1
            assert final == "READY" and s.stat(path)["size"] == len(data)                # READY => fișier prezent
        elif out["cl"]:
            wins["cleanup"] += 1
            assert final == "CLEANING"
            with aconn(live) as c:
                token = c.execute("SELECT cleanup_token FROM cp_storage_objects WHERE cemetery_uid=%s AND uid=%s", (A["cem"], uid)).fetchone()[0]
            s.delete(path)
            assert c_cl.execute("SELECT cp_storage_cleanup_finish(%s,%s,%s,%s)", (A["org"], A["cem"], uid, str(token))).fetchone()[0] is True
            assert st_of(live, A, uid) == "DELETED" and s.stat(path) is None
        else:
            wins["none"] += 1
            raise AssertionError(("nicio parte nu a câștigat", out, final))
        c_up.close(), c_cl.close()
    RES["RACE STRESS"] = {"rounds": rounds, **wins}
    # doi workers de cleanup, simultan, pe același set: seturi disjuncte, unirea = tot
    ids = {staged(live, store, A, put=False)[0] for _ in range(12)}
    conns, got, bar = [wconn() for _ in range(3)], {}, threading.Barrier(3)

    def worker(n):
        bar.wait()
        got[n] = {r[0] for r in claim(conns[n], A, limit=12)[1]}

    th = [threading.Thread(target=worker, args=(n,)) for n in range(3)]
    [x.start() for x in th]
    [x.join(60) for x in th]
    sets = list(got.values())
    assert set().union(*sets) == ids and sum(len(x) for x in sets) == len(ids)
    for c in conns:
        c.close()
    RES["CLEANUP CLAIM DOUBLE-WORKER"] = "PASS"
    # curăț rândurile revendicate în această probă (obiecte inexistente în Storage)
    with aconn(live) as c:
        c.execute("UPDATE cp_storage_objects SET status='DELETED', error='test' WHERE cemetery_uid=%s AND status='CLEANING'", (A["cem"],))
    RES["HIGH 2 LIVE"] = "PASS"
    RES["CLEANUP VS READY RACE"] = "PASS"


def test_12_store_level_cleanup_vs_upload_end_state(live):
    import threading
    cp, store = mk(live)
    A = T["A"]
    s = live["storage"]
    for _ in range(6):
        data = _pdf()
        uid = str(uuid.uuid4())
        store.begin_upload(A["key"], A["cem"], uid, "u.pdf", "application/pdf", len(data), sha(data))
        age_row(live, A, uid)
        bar, out = threading.Barrier(2), {}

        def up_side():
            bar.wait()
            try:
                out["up"] = store.upload(A["key"], A["cem"], uid, data)["status"]
            except fs.Conflict:
                out["up"] = "conflict"

        def cl_side():
            bar.wait()
            out["cl"] = store.cleanup_orphans(A["key"], A["cem"], older_than_s=3600)

        th = [threading.Thread(target=up_side), threading.Thread(target=cl_side)]
        [x.start() for x in th]
        [x.join(120) for x in th]
        store.cleanup_orphans(A["key"], A["cem"], older_than_s=0 if out["up"] == "conflict" else 3600)     # convergență: rămășițele (dacă există) sunt curățate
        final, present = st_of(live, A, uid), s.stat(fs.storage_path(A["org"], A["cem"], uid)) is not None
        if final == "READY":
            assert out["up"] == "READY" and present                                      # niciodată READY + fișier lipsă
        else:
            assert final == "DELETED" and out["up"] == "conflict" and not present, (final, out, present)


def test_13_no_ready_object_was_ever_deleted_by_cleanup(live):
    s, bad = live["storage"], 0
    with aconn(live) as c:
        rows = c.execute("SELECT cemetery_uid::text, uid, storage_path, size FROM cp_storage_objects WHERE status='READY' AND cemetery_uid = ANY(%s::uuid[])", ([T["A"]["cem"], T["B"]["cem"]],)).fetchall()
    for _cem, _uid, path, size in rows:
        info = s.stat(path)
        bad += (info is None or info["size"] != size)
    RES["READY OBJECT ACCIDENTAL DELETE"] = bad
    RES["READY rows checked"] = len(rows)
    assert len(rows) >= 6 and bad == 0


def test_99_role_still_restricted_and_summary(live):
    c = psycopg.connect(live["app"], autocommit=True)
    assert c.execute("select rolsuper, rolbypassrls from pg_roles where rolname=current_user").fetchone() == (False, False)
    with aconn(live) as a:
        assert a.execute("SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname='cimitir_storage_cleanup'").fetchone() == (False,) * 5
        assert not a.execute("SELECT has_table_privilege('cimitir_cp_app','cp_storage_objects','DELETE')").fetchone()[0]
    # curăț DOAR ce a creat rularea curentă (prefixele/cimitirele create de test_00) și verific obiectele preexistente
    s = live["storage"]
    for t in T.values():
        for name in objects_in(live, t):
            s.delete(f"field/{t['org']}/{t['cem']}/{name}")
    with aconn(live) as a:
        a.execute("DELETE FROM cp_storage_objects WHERE cemetery_uid = ANY(%s::uuid[])", ([T["A"]["cem"], T["B"]["cem"]],))
    after = walk(s)
    RES["PREEXISTING STORAGE OBJECTS"] = {"before": len(PRE["objects"]), "after": len(after), "intact": after == PRE["objects"]}
    assert after == PRE["objects"], (PRE["objects"], after)
    RES["6B STORAGE"] = "PASS"
