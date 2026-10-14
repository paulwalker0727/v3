"""MAIN: Field Inbox + Phone Pairing peste profilul Cloud activ. Fără rețea reală: routerele REALE (cimitir_field_inbox_http / cimitir_phone_pairing_http)
rulează in-process peste un serviciu in-memory cu aceleași reguli de stare; PostgreSQL/Storage LIVE nu sunt atinse. PRODUCTION blocat."""
from __future__ import annotations

import hashlib
import logging
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="cimitir_test_field_inbox_main_")
os.environ["CIMITIR_DATA_DIR"] = _TMP
os.environ["CIMITIR_LOG_DIR"] = os.path.join(_TMP, "logs")
os.environ.setdefault("CIMITIR_DB_ENGINE", "sqlite")
os.environ.setdefault("CIMITIR_MODE", "local")
os.environ.setdefault("DATABASE_URL", "")

DESKTOP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DESKTOP))
sys.path.insert(0, str(DESKTOP.parent / "shared"))

import pytest  # noqa: E402
import requests  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import cimitir_cloud_control as cc  # noqa: E402
import cimitir_cloud_control_pg as ccp  # noqa: E402
import cimitir_field_inbox as fi  # noqa: E402
import cimitir_field_inbox_http as fih  # noqa: E402
import cimitir_phone_pairing as pp  # noqa: E402
import cimitir_phone_pairing_http as pph  # noqa: E402
import cimitir_secretstore as ss  # noqa: E402
import cloud_profiles as cprof  # noqa: E402
import database as db  # noqa: E402
import field_inbox_client as fic  # noqa: E402

BASE = "https://cloud-test.example.ro"
CEM = str(uuid.uuid4())
ORG = str(uuid.uuid4())
MAIN_BUID = str(uuid.uuid4())
MAIN_KEY = f"cimitir.{MAIN_BUID}." + "m" * 43
PHONE_BUID = str(uuid.uuid4())
DB_PW = "S3cr3t-Parola-DB-9981"
SUPA_SECRET = "sb_secret_" + "Z" * 30
SIGNED_TOKEN = "SIGNEDTOKEN" + "q" * 20
JPEG = b"\xff\xd8\xff\xe0" + b"j" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 64
PDF = b"%PDF-1.4\n" + b"d" * 64
ALL_SECRETS = (MAIN_KEY, DB_PW, SUPA_SECRET)


# ------------------------------------------------------------------ servicii in-memory (aceleași reguli de stare ca FieldInboxService)
class FakeInbox:
    def __init__(self):
        self.items = {}
        self.files = {}
        self.blobs = {}
        self.phones = []
        self.codes = {}

    def add_item(self, item_type="DECEASED", status="PENDING", payload=None, stage=None, files=(), created_by=PHONE_BUID, grave="G-12"):
        uid = str(uuid.uuid4())
        self.items[uid] = {"uid": uid, "organization_uid": ORG, "cemetery_uid": CEM, "item_type": item_type, "work_stage": stage, "target_uid": None,
                           "grave_uid": grave, "payload": payload or {"nume": "Popescu", "prenume": "Ion"}, "status": status, "idempotency_key": "k" * 20,
                           "created_at": "2026-05-01T10:30:00+00:00", "created_by": created_by, "reviewed_at": None, "reviewed_by": None,
                           "imported_at": None, "imported_by": None, "rejection_reason": None}
        self.files[uid] = []
        for mime, data in files:
            fuid = str(uuid.uuid4())
            self.blobs[fuid] = data
            self.files[uid].append({"uid": fuid, "filename": "f." + mime.split("/")[-1], "mime": mime, "size": len(data),
                                    "sha256": hashlib.sha256(data).hexdigest(), "status": "READY", "kind": "photo"})
        return uid

    def authenticate(self, key, cemetery_uid=None):
        if key != MAIN_KEY:
            raise cc.ScopeError("legătură invalidă")
        return {"binding_uid": MAIN_BUID, "organization_uid": ORG, "cemetery_uid": CEM, "device_type": "MAIN", "main": True}

    def list_items(self, key, cem, status=None, item_type=None, limit=50, offset=0):
        rows = [dict(i) for i in self.items.values() if (not status or i["status"] == status) and (not item_type or i["item_type"] == item_type)]
        return rows[offset:offset + limit]

    def get_item(self, key, cem, uid):
        if uid not in self.items:
            raise cc.ScopeError("item inexistent")
        return {**self.items[uid], "files": list(self.files[uid])}

    def file_url(self, key, cem, uid, fuid, ttl=60):
        it = self.items.get(uid)
        if not it or not any(f["uid"] == fuid for f in self.files[uid]):
            raise cc.ScopeError("fișier inexistent")
        if it["status"] not in fi.URL_STATUSES:
            raise fi.fs.NotReady("itemul nu permite descărcarea")
        f = next(f for f in self.files[uid] if f["uid"] == fuid)
        return {"url": f"https://storage.example.ro/object/sign/{fuid}?token={SIGNED_TOKEN}", "expires_in": min(ttl, 300), "sha256": f["sha256"],
                "size": f["size"], "mime": f["mime"]}

    def _transition(self, uid, to, reason=None):
        it = self.items.get(uid)
        if not it:
            raise cc.ScopeError("item inexistent")
        if it["status"] == to:
            return {"item": dict(it), "changed": False}
        if it["status"] != fi.TRANSITIONS[to]:
            raise fi.InvalidTransition(f"tranziția {it['status']} -> {to} nu este permisă")
        it["status"] = to
        it["reviewed_by"] = MAIN_BUID
        if reason:
            it["rejection_reason"] = reason
        return {"item": dict(it), "changed": True}

    def accept(self, key, cem, uid):
        return self._transition(uid, "ACCEPTED")

    def reject(self, key, cem, uid, reason):
        if not isinstance(reason, str) or not reason.strip():
            raise fi.InvalidItem("motivul respingerii este obligatoriu")
        return self._transition(uid, "REJECTED", reason.strip())

    def mark_imported(self, key, cem, uid):
        return self._transition(uid, "IMPORTED")


class FakePairing:
    def __init__(self, inbox: FakeInbox):
        self.inbox = inbox
        self.raw_codes = []

    def issue_code(self, key, ttl=600):
        self.inbox.authenticate(key)
        ttl = min(max(pp.MIN_TTL, int(ttl)), pp.MAX_TTL)
        code = pp.new_code()
        self.raw_codes.append(code)
        self.inbox.codes[hashlib.sha256(code.encode()).hexdigest()] = time.time() + ttl
        return {"code": pp.display_code(code), "expires_at": time.time() + ttl, "ttl": ttl, "organization_uid": ORG, "cemetery_uid": CEM}

    def list_phones(self, key):
        self.inbox.authenticate(key)
        return [dict(p) for p in self.inbox.phones]

    def revoke_phone(self, key, buid):
        self.inbox.authenticate(key)
        for p in self.inbox.phones:
            if p["binding_uid"] == buid:
                p["active"], p["revoked_at"] = False, "2026-05-02T08:00:00+00:00"
                return {"binding_uid": buid, "revoked": True}
        raise cc.ScopeError("legătură inexistentă")


class AsgiHttp:
    """Adaptor `requests`-like: API-ul cloud ruleazã in-process (routerele reale); URL-urile semnate se servesc dintr-un dicționar; înregistrează cererile."""

    def __init__(self, inbox: FakeInbox, pairing: FakePairing):
        app = FastAPI()
        app.include_router(fih.create_router(inbox))
        app.include_router(pph.create_router(pairing))
        self.client = TestClient(app, base_url=BASE)
        self.inbox = inbox
        self.calls = []
        self.storage_override = {}
        self.fail = None

    def request(self, method, url, headers=None, params=None, json=None, timeout=None, allow_redirects=True):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "params": params, "json": json})
        if self.fail:
            raise self.fail
        if url.startswith("https://storage.example.ro/"):
            fuid = url.split("/sign/")[1].split("?")[0]
            body = self.storage_override.get(fuid, self.inbox.blobs.get(fuid, b""))
            return SimpleNamespace(status_code=200, content=body)
        return self.client.request(method, url, headers=headers, params=params, json=json)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "cimitir.db"))
    db.init_db()
    st = ss.DpapiSecretStore(tmp_path / "secrets", lambda b: b"F:" + bytes(reversed(b)), lambda b: bytes(reversed(b[2:])))
    monkeypatch.setattr(cprof, "get_store", lambda: st)
    inbox = FakeInbox()
    pairing = FakePairing(inbox)
    inbox.phones = [{"binding_uid": PHONE_BUID, "device_uid": str(uuid.uuid4()), "device_name": "Telefon Ion", "created_at": "2026-05-01T09:00:00+00:00",
                     "last_seen_at": "2026-05-01T11:00:00+00:00", "revoked_at": None, "active": True}]
    http = AsgiHttp(inbox, pairing)
    return SimpleNamespace(store=st, inbox=inbox, pairing=pairing, http=http)


def make_profile(env, environment="TEST", key=MAIN_KEY, url=BASE, active=True, secrets=True):
    assert environment != "PRODUCTION", "PRODUCTION este blocat în teste"
    assert not any(ref in url for ref in ccp.PROTECTED_PROJECT_REFS)
    fields = {"name": "Cloud test", "environment": environment, "api_base_url": url, "project_ref": "", "supabase_url": "", "db_host": "", "db_port": "5432",
              "db_name": "", "db_user": "", "sslmode": "require", "storage_bucket": ""}
    sec = {"sync_key": key}
    p = cprof.create_profile(fields, sec, env.store)
    if active:
        cprof.activate_profile(p.id)
    return p


def client(env):
    c, _p = fic.connect(env.store, env.http)
    return c


# ------------------------------------------------------------------ profil Cloud
def test_no_active_profile_is_not_configured(env):
    with pytest.raises(fic.NotConfigured) as e:
        fic.connect(env.store, env.http)
    assert str(e.value) == "Cloud neconfigurat"
    assert env.http.calls == []


def test_inactive_profile_is_not_configured(env):
    make_profile(env, active=False)
    with pytest.raises(fic.NotConfigured):
        fic.connect(env.store, env.http)


def test_active_test_profile_builds_client_with_binding_key(env):
    make_profile(env)
    c, prof = fic.connect(env.store, env.http)
    assert prof.environment == "TEST" and prof.is_active
    c.list_items("PENDING")
    h = env.http.calls[0]["headers"]
    assert h["Authorization"] == f"Binding {MAIN_KEY}"
    assert env.http.calls[0]["url"] == BASE + "/api/v1/field-inbox/items"


def test_legacy_two_part_sync_key_is_not_a_binding_key(env):
    make_profile(env, key="cimitir." + "a" * 43)
    with pytest.raises(fic.NotConfigured):
        fic.connect(env.store, env.http)


def test_profile_without_key_or_url_not_configured(env):
    make_profile(env, key="", secrets=False)
    with pytest.raises(fic.NotConfigured):
        fic.connect(env.store, env.http)


def test_binding_key_format_is_accepted_by_profile_validation():
    import cloud_sync
    assert cloud_sync.validate_key(MAIN_KEY) == ""
    assert cloud_sync.validate_key(f"cimitir.{MAIN_BUID}.short") != ""


def test_api_offline_is_cloud_unavailable_not_crash(env):
    make_profile(env)
    env.http.fail = requests.ConnectionError("dns " + MAIN_KEY)
    c = client(env)
    for call in (lambda: c.count_pending(), lambda: c.list_items("PENDING"), lambda: c.list_phones(), lambda: c.issue_pairing_code()):
        with pytest.raises(fic.CloudUnavailable) as e:
            call()
        assert str(e.value) == "Cloud indisponibil" and MAIN_KEY not in str(e.value)


def test_server_5xx_and_redirect_are_unavailable(env):
    make_profile(env)
    c = client(env)
    for code in (502, 503, 504, 302):
        env.http.request = lambda *a, _c=code, **k: SimpleNamespace(status_code=_c, json=lambda: {})
        with pytest.raises(fic.CloudUnavailable):
            c.list_items()


# ------------------------------------------------------------------ Field Inbox: count / listă / preview
def test_pending_count(env):
    make_profile(env)
    for _ in range(3):
        env.inbox.add_item()
    env.inbox.add_item(status="ACCEPTED")
    assert client(env).count_pending() == (3, False)
    assert fic.new_items_message(3) == "Ai 3 elemente noi din teren" and fic.toolbar_label(3) == "📱 Elemente din teren [3]"
    assert fic.new_items_message(1) == "Ai 1 element nou din teren" and fic.toolbar_label(0) == "📱 Elemente din teren"


def test_pending_count_pages_and_cap(env, monkeypatch):
    make_profile(env)
    for _ in range(5):
        env.inbox.add_item()
    monkeypatch.setattr(fic, "MAX_PAGE", 2)
    monkeypatch.setattr(fic, "MAX_COUNT", 4)
    assert client(env).count_pending() == (4, True)
    assert fic.toolbar_label(4, True) == "📱 Elemente din teren [4+]"


def test_list_items_filters_and_default_pending(env):
    make_profile(env)
    for st in ("PENDING", "PENDING", "ACCEPTED", "REJECTED", "IMPORTED"):
        env.inbox.add_item(status=st)
    c = client(env)
    assert [len(c.list_items(s)) for s in fic.STATUSES] == [2, 1, 1, 1]
    assert len(c.list_items()) == 5
    with pytest.raises(ValueError):
        c.list_items("DELETED")
    rows = [fic.item_row(i, fic.phone_names(c.list_phones())) for i in c.list_items("PENDING")]
    assert rows[0]["type"] == "Defunct" and rows[0]["place"] == "G-12" and rows[0]["device"] == "Telefon Ion" and rows[0]["status"] == "În așteptare"
    assert rows[0]["date"] == "2026-05-01 10:30"


def test_item_preview_for_all_types(env):
    make_profile(env)
    payloads = {"DECEASED": {"nume": "Popescu"}, "WORK": {"descriere": "Curățare"}, "PERMIT": {"nr_aviz": "12/2026"}, "DOCUMENT": {"doc_type": "act"},
                "GRAVE_PHOTO": {}, "WORK_PHOTO": {}}
    c = client(env)
    for t, payload in payloads.items():
        files = [("image/jpeg", JPEG)] if t in ("GRAVE_PHOTO", "WORK_PHOTO") else [("application/pdf", PDF)] if t == "DOCUMENT" else []
        uid = env.inbox.add_item(t, payload=payload, stage="AFTER" if t == "WORK_PHOTO" else None, files=files)
        pv = fic.item_preview(c.get_item(uid), fic.phone_names(c.list_phones()))
        assert pv["title"] == fic.TYPE_LABELS[t] and pv["can_review"] is True
        assert ("Loc / grave_uid", "G-12") in pv["lines"] and ("Dispozitiv", "Telefon Ion") in pv["lines"]
        assert len(pv["files"]) == len(files) and all(f["previewable"] for f in pv["files"])


@pytest.mark.parametrize("stage", ["BEFORE", "DURING", "AFTER"])
def test_work_photo_stage_preview(env, stage):
    make_profile(env)
    uid = env.inbox.add_item("WORK_PHOTO", payload={}, stage=stage, files=[("image/png", PNG)])
    pv = fic.item_preview(client(env).get_item(uid))
    assert pv["stage"] == fic.STAGE_LABELS[stage]
    assert stage in ("BEFORE", "DURING", "AFTER") and fic.item_row(client(env).get_item(uid))["type"].endswith(f"({fic.STAGE_LABELS[stage]})")


def test_preview_not_reviewable_when_not_pending(env):
    make_profile(env)
    uid = env.inbox.add_item(status="REJECTED")
    pv = fic.item_preview(client(env).get_item(uid))
    assert pv["can_review"] is False


# ------------------------------------------------------------------ atașamente cu URL semnat
@pytest.mark.parametrize("mime,data", [("image/jpeg", JPEG), ("image/png", PNG), ("application/pdf", PDF)])
def test_signed_attachment_fetch(env, mime, data):
    make_profile(env)
    uid = env.inbox.add_item("DOCUMENT", files=[(mime, data)], payload={"doc_type": "x"})
    fuid = env.inbox.files[uid][0]["uid"]
    got, got_mime = client(env).fetch_attachment(uid, fuid)
    assert got == data and got_mime == mime
    storage_call = env.http.calls[-1]
    assert storage_call["url"].startswith("https://storage.example.ro/") and "Authorization" not in storage_call["headers"]
    assert env.http.calls[-2]["url"].endswith(f"/files/{fuid}/url")


def test_signed_attachment_integrity_and_type_rejected(env):
    make_profile(env)
    uid = env.inbox.add_item("DOCUMENT", files=[("image/jpeg", JPEG)], payload={"doc_type": "x"})
    fuid = env.inbox.files[uid][0]["uid"]
    c = client(env)
    env.http.storage_override[fuid] = JPEG[:-1] + b"X"          # SHA-256 diferit
    with pytest.raises(fic.ApiError):
        c.fetch_attachment(uid, fuid)
    env.http.storage_override[fuid] = b"<html>" + b"x" * 70      # fără semnătură JPEG, aceeași mărime nu contează
    with pytest.raises(fic.ApiError):
        c.fetch_attachment(uid, fuid)
    env.http.storage_override[fuid] = b""
    with pytest.raises(fic.ApiError):
        c.fetch_attachment(uid, fuid)


def test_signed_url_not_exposed_or_persisted(env, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    make_profile(env)
    uid = env.inbox.add_item("GRAVE_PHOTO", payload={}, files=[("image/jpeg", JPEG)])
    c = client(env)
    c.fetch_attachment(uid, env.inbox.files[uid][0]["uid"])
    assert SIGNED_TOKEN not in caplog.text and SIGNED_TOKEN not in repr(c) and SIGNED_TOKEN not in repr(fic.item_preview(c.get_item(uid)))
    assert SIGNED_TOKEN not in repr(c.get_item(uid))
    blob = b"".join(p.read_bytes() for p in Path(tmp_path).rglob("*") if p.is_file())
    assert SIGNED_TOKEN.encode() not in blob


def test_attachment_unavailable_after_review(env):
    make_profile(env)
    uid = env.inbox.add_item("GRAVE_PHOTO", payload={}, status="REJECTED", files=[("image/jpeg", JPEG)])
    with pytest.raises(fic.ApiError) as e:
        client(env).fetch_attachment(uid, env.inbox.files[uid][0]["uid"])
    assert e.value.code == "NOT_READY"


# ------------------------------------------------------------------ Acceptă / Respinge / tranziții
def test_accept(env):
    make_profile(env)
    uid = env.inbox.add_item()
    res = client(env).accept(uid, "PENDING")
    assert res["item"]["status"] == "ACCEPTED" and env.inbox.items[uid]["status"] == "ACCEPTED"


def test_reject_with_reason_and_default_reason(env):
    make_profile(env)
    a, b = env.inbox.add_item(), env.inbox.add_item()
    c = client(env)
    assert c.reject(a, "date greșite", "PENDING")["item"]["rejection_reason"] == "date greșite"
    assert c.reject(b, "   ", "PENDING")["item"]["rejection_reason"] == fic.DEFAULT_REJECT_REASON
    assert env.inbox.items[b]["status"] == "REJECTED"


def test_invalid_transitions_blocked_client_and_server(env):
    make_profile(env)
    c = client(env)
    rej = env.inbox.add_item(status="REJECTED")
    imp = env.inbox.add_item(status="IMPORTED")
    acc = env.inbox.add_item(status="ACCEPTED")
    for uid, st in ((rej, "REJECTED"), (imp, "IMPORTED"), (acc, "ACCEPTED")):
        with pytest.raises(fic.InvalidTransition):
            c.accept(uid, st)
        with pytest.raises(fic.InvalidTransition):
            c.reject(uid, "x", st)
    assert not hasattr(c, "imported") and fic.UI_TRANSITIONS == {"PENDING": ("ACCEPTED", "REJECTED")}
    for st in ("PENDING", "REJECTED"):
        with pytest.raises(fic.InvalidTransition):
            c.mark_imported(acc, st)           # IMPORTED se cere doar din ACCEPTED, și doar de field_import după commit-ul local
    assert env.http.calls == [] or all("/accept" not in x["url"] and "/reject" not in x["url"] for x in env.http.calls)
    # stare veche în UI: serverul decide -> INVALID_TRANSITION, fără a modifica itemul
    with pytest.raises(fic.InvalidTransition) as e:
        c.accept(rej, "PENDING")
    assert e.value.code == "INVALID_TRANSITION" and env.inbox.items[rej]["status"] == "REJECTED"
    with pytest.raises(fic.InvalidTransition):
        c.reject(imp, "x", "PENDING")
    assert env.inbox.items[imp]["status"] == "IMPORTED"


def test_unknown_item_and_bad_key_errors(env):
    make_profile(env)
    with pytest.raises(fic.ApiError) as e:
        client(env).get_item(str(uuid.uuid4()))
    assert e.value.code == "SCOPE_MISMATCH"
    bad = fic.FieldCloudClient(BASE, f"cimitir.{uuid.uuid4()}." + "x" * 43, http=env.http)
    with pytest.raises(fic.ApiError) as e:
        bad.list_items()
    assert e.value.code == "UNAUTHENTICATED"


# ------------------------------------------------------------------ Phone Pairing
def test_pairing_code_generation_hash_only_not_logged(env, caplog):
    caplog.set_level(logging.DEBUG)
    make_profile(env)
    res = client(env).issue_pairing_code()
    raw = env.pairing.raw_codes[-1]
    assert res["code"] == pp.display_code(raw) and res["ttl"] == 600 and res["cemetery_uid"] == CEM
    assert raw not in caplog.text and res["code"] not in caplog.text
    assert raw not in repr(env.inbox.codes) and hashlib.sha256(raw.encode()).hexdigest() in env.inbox.codes
    assert env.http.calls[-1]["json"] == {"ttl": 600}


def test_pairing_code_ttl_clamped(env):
    make_profile(env)
    assert client(env).issue_pairing_code(10_000)["ttl"] == pp.MAX_TTL


def test_pairing_code_expiry_display():
    now = 1000.0
    assert fic.format_expiry(now + 125, now) == "Expiră în 02:05"
    assert fic.format_expiry(now + 5, now) == "Expiră în 00:05"
    assert fic.format_expiry(now, now) == "Expirat" and fic.format_expiry(now - 30, now) == "Expirat"


def test_device_list(env):
    make_profile(env)
    env.inbox.phones.append({"binding_uid": str(uuid.uuid4()), "device_uid": str(uuid.uuid4()), "device_name": "Vechi", "created_at": "2026-04-01T09:00:00+00:00",
                             "last_seen_at": None, "revoked_at": "2026-04-02T09:00:00+00:00", "active": False})
    rows = [fic.phone_row(p, "Cimitirul Central") for p in client(env).list_phones()]
    assert [r["name"] for r in rows] == ["Telefon Ion", "Vechi"]
    assert rows[0]["state"] == "Activ" and rows[0]["revocable"] and rows[0]["cemetery"] == "Cimitirul Central" and rows[0]["paired"] == "2026-05-01 09:00"
    assert rows[1]["state"] == "Revocat" and not rows[1]["revocable"] and rows[1]["last_seen"] == "—"


def test_device_revoke(env):
    make_profile(env)
    c = client(env)
    assert c.revoke_phone(PHONE_BUID) == {"binding_uid": PHONE_BUID, "revoked": True}
    assert fic.phone_row(c.list_phones()[0])["state"] == "Revocat"
    with pytest.raises(fic.ApiError) as e:
        c.revoke_phone(str(uuid.uuid4()))
    assert e.value.code == "SCOPE_MISMATCH"


# ------------------------------------------------------------------ secrete
def test_no_db_or_service_secrets_in_http_or_logs(env, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    fields = {"name": "Adv", "environment": "TEST", "api_base_url": BASE, "project_ref": "abcdefghijklmnopqrst", "supabase_url": "https://abcdefghijklmnopqrst.supabase.co",
              "db_host": "aws-0-eu.pooler.supabase.com", "db_port": "5432", "db_name": "postgres", "db_user": "postgres.abcdefghijklmnopqrst", "sslmode": "require",
              "storage_bucket": "cimitir-private"}
    p = cprof.create_profile(fields, {"sync_key": MAIN_KEY, "db_password": DB_PW, "supabase_secret_key": SUPA_SECRET}, env.store)
    cprof.activate_profile(p.id)
    uid = env.inbox.add_item("GRAVE_PHOTO", payload={}, files=[("image/jpeg", JPEG)])
    c = client(env)
    c.count_pending(); c.get_item(uid); c.fetch_attachment(uid, env.inbox.files[uid][0]["uid"]); c.accept(uid, "PENDING")
    c.issue_pairing_code(); c.list_phones(); c.revoke_phone(PHONE_BUID)
    sent = repr(env.http.calls)
    for s in (DB_PW, SUPA_SECRET, "service_role", "SUPABASE_SECRET_KEY"):
        assert s not in sent
    for call in env.http.calls:
        if call["url"].startswith(BASE):
            assert set(call["headers"]) == {"Authorization", "Accept"}
    out = capsys.readouterr()
    for s in ALL_SECRETS:
        assert s not in caplog.text and s not in out.out + out.err and s not in repr(c) and s not in str(c.__dict__.get("_base"))


def test_errors_never_contain_key(env, caplog):
    make_profile(env)
    c = client(env)
    env.http.fail = requests.Timeout(MAIN_KEY)
    with pytest.raises(fic.CloudUnavailable) as e:
        c.list_items()
    assert MAIN_KEY not in str(e.value) and MAIN_KEY not in caplog.text and MAIN_KEY not in repr(e.value)
    env.http.fail = None
    with pytest.raises(fic.ApiError) as e2:
        fic.FieldCloudClient(BASE, f"cimitir.{uuid.uuid4()}." + "k" * 43, http=env.http).list_items()
    assert "k" * 43 not in str(e2.value)


def test_constants_match_backend_contract():
    assert fic.ITEM_TYPES == fi.ITEM_TYPES and fic.STATUSES == fi.STATUSES and fic.WORK_STAGES == fi.WORK_STAGES
    assert fic.INBOX == fih.PREFIX and fic.PAIRING == pph.PREFIX
    assert fic.MAX_REASON == 500 and set(fic.PREVIEW_MIMES) == set(fi.fs.MAGIC)


# ------------------------------------------------------------------ UI (PySide6 offscreen)
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def pump(qapp, cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    qapp.processEvents()
    return cond()


def test_ui_inbox_window_list_preview_accept_reject(env, qapp, monkeypatch):
    import field_inbox_window as w
    make_profile(env)
    a = env.inbox.add_item("WORK_PHOTO", payload={}, stage="BEFORE", files=[("image/png", _png_bytes())])
    b = env.inbox.add_item("DECEASED")
    env.inbox.add_item(status="ACCEPTED")
    win = w.FieldInboxWindow(client_factory=lambda: client(env), can_review=lambda: True)
    assert win.current_status() == "PENDING"
    assert pump(qapp, lambda: win.table.rowCount() == 2)
    assert win.table.columnCount() == 6 and not win.btn_accept.isEnabled()
    win.table.selectRow(0)
    assert pump(qapp, lambda: win.btn_accept.isEnabled() and "Foto lucrare" in win.txt_detail.toPlainText())
    assert "ÎNAINTE" in win.lbl_stage.text()
    assert pump(qapp, lambda: not win.lbl_preview.pixmap().isNull())
    monkeypatch.setattr(w.QInputDialog, "getText", staticmethod(lambda *a, **k: ("poză neclară", True)))
    win.btn_reject.click()
    assert pump(qapp, lambda: env.inbox.items[a]["status"] == "REJECTED" and win.table.rowCount() == 1)
    assert env.inbox.items[a]["rejection_reason"] == "poză neclară"
    win.table.selectRow(0)
    assert pump(qapp, lambda: win.btn_accept.isEnabled())
    win.btn_accept.click()
    assert pump(qapp, lambda: env.inbox.items[b]["status"] == "ACCEPTED" and win.table.rowCount() == 0)
    win.cmb_status.setCurrentIndex(fic.STATUSES.index("REJECTED"))
    assert pump(qapp, lambda: win.table.rowCount() == 1)
    win.table.selectRow(0)
    assert pump(qapp, lambda: "Respins" in win.txt_detail.toPlainText())
    assert not win.btn_accept.isEnabled() and not win.btn_reject.isEnabled()
    win.close()


def _png_bytes():
    from PySide6.QtCore import QBuffer, QByteArray
    from PySide6.QtGui import QImage
    img = QImage(8, 8, QImage.Format_RGB32)
    img.fill(0xFF0000)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def test_ui_inbox_offline_and_not_configured(env, qapp):
    import field_inbox_window as w
    env.http.fail = requests.ConnectionError("down")
    make_profile(env)
    win = w.FieldInboxWindow(client_factory=lambda: client(env))
    assert pump(qapp, lambda: win.lbl_state.text() == "Cloud indisponibil")
    assert win.table.rowCount() == 0 and not win.btn_config.isVisible()
    win.close()
    win2 = w.FieldInboxWindow(client_factory=lambda: fic.connect(env.store, None)[0] if False else (_ for _ in ()).throw(fic.NotConfigured()))
    assert pump(qapp, lambda: win2.lbl_state.text() == "Cloud neconfigurat")
    assert not win2.btn_config.isHidden()
    assert win2.btn_config.text() == "Configurează Cloud/Server"
    win2.close()


def test_ui_devices_dialog_code_list_revoke(env, qapp, monkeypatch):
    import field_devices_window as d
    make_profile(env)
    dlg = d.FieldDevicesDialog(client_factory=lambda: client(env), can_manage=lambda: True)
    assert pump(qapp, lambda: dlg.table.rowCount() == 1)
    assert dlg.table.item(0, 0).text() == "Telefon Ion" and dlg.table.item(0, 4).text() == "Activ"
    dlg.btn_code.click()
    assert pump(qapp, lambda: dlg.lbl_code.text() != "—")
    assert dlg.lbl_code.text() == pp.display_code(env.pairing.raw_codes[-1]) and dlg.lbl_expiry.text().startswith("Expiră în")
    dlg._deadline = time.time() - 1                                # expirare: codul dispare din ecran
    dlg._tick()
    assert dlg.lbl_expiry.text() == "Expirat" and dlg.lbl_code.text() == "—"
    dlg.table.selectRow(0)
    assert dlg.btn_revoke.isEnabled()
    monkeypatch.setattr(d.QMessageBox, "question", staticmethod(lambda *a, **k: d.QMessageBox.Yes))
    dlg.btn_revoke.click()
    assert pump(qapp, lambda: env.inbox.phones[0]["active"] is False and dlg.table.item(0, 4).text() == "Revocat")
    dlg.table.selectRow(0)
    assert not dlg.btn_revoke.isEnabled()
    dlg.close()


def test_ui_widgets_show_no_secrets(env, qapp):
    import field_devices_window as d
    import field_inbox_window as w
    make_profile(env)
    env.inbox.add_item()
    win, dlg = w.FieldInboxWindow(client_factory=lambda: client(env)), d.FieldDevicesDialog(client_factory=lambda: client(env))
    pump(qapp, lambda: win.table.rowCount() == 1 and dlg.table.rowCount() == 1)
    texts = []
    for top in (win, dlg):
        for lab in top.findChildren(QtWidgets.QLabel):
            texts.append(lab.text())
        for ed in top.findChildren(QtWidgets.QPlainTextEdit):
            texts.append(ed.toPlainText())
    blob = " ".join(texts)
    for s in ALL_SECRETS:
        assert s not in blob
    win.close()
    dlg.close()


def test_main_badge_startup_never_blocks(env, qapp):
    try:
        import ui_mainwindow as um
    except Exception as exc:  # pragma: no cover - mediu fără dependențe GUI complete
        pytest.skip(f"ui_mainwindow nu poate fi importat aici: {type(exc).__name__}")
    from PySide6.QtGui import QAction
    msgs = []
    fake = SimpleNamespace(_field_busy=False, _field_last=None, act_field=QAction("x"), statusBar=lambda: SimpleNamespace(showMessage=lambda m, t=0: msgs.append(m)))
    make_profile(env)
    env.inbox.add_item(); env.inbox.add_item()
    monkey = fic.connect
    fic.connect = lambda *a, **k: monkey(env.store, env.http)
    try:
        um.CemeteryUI.refresh_field_badge(fake, announce=True)
        assert pump(qapp, lambda: not fake._field_busy and fake._field_last == 2)
        assert fake.act_field.text() == "📱 Elemente din teren [2]" and msgs == ["📱 Ai 2 elemente noi din teren"]
        env.http.fail = requests.ConnectionError("x")
        um.CemeteryUI.refresh_field_badge(fake, announce=True)
        assert pump(qapp, lambda: not fake._field_busy and fake.act_field.text() == "📱 Elemente din teren")
        assert fake.act_field.toolTip() == "Cloud indisponibil"
        cprof.deactivate_profile(cprof.active_profile().id)
        um.CemeteryUI.refresh_field_badge(fake, announce=True)
        assert pump(qapp, lambda: not fake._field_busy and fake.act_field.toolTip() == "Cloud neconfigurat")
    finally:
        fic.connect = monkey
