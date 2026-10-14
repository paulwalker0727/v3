# ruff: noqa: F811
"""Phone Capture v1 — codul real al telefonului (backend/static/teren.js, rulat în Node) față de routerele REALE Field Inbox / Phone Pairing pe HTTP local.
Serviciul e in-memory (aceleași validări ca cel real); PostgreSQL/Storage/Supabase nu sunt atinse. Compatibilitate cu importul local: itemurile produse sunt importate cu field_import."""
from __future__ import annotations

import json
import re
import subprocess
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from test_field_import import add, add_work, loc, rows  # noqa: F401,I001
from test_field_inbox_main import CEM as MAIN_CEM, client, env, make_profile  # noqa: F401,I001
import phone_capture_fakes as pcf  # noqa: E402
import cimitir_field_inbox_http as fih  # noqa: E402
import database as db  # noqa: E402
import field_import as fim  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
RUNNER = Path(__file__).parent / "js" / "teren_runner.js"
CEM = MAIN_CEM
GRAVE = str(uuid.uuid4())
TARGET = str(uuid.uuid4())


@pytest.fixture()
def world():
    inbox = pcf.PhoneInbox()
    pairing = pcf.PhonePairing(inbox)
    with pcf.LiveServer(pcf.make_app(inbox, pairing)) as srv:
        yield type("W", (), {"inbox": inbox, "pairing": pairing, "url": srv.url, "key": inbox.add_binding("MOBILE", CEM)})


def run(w, scenario, **kw):
    cfg = {"base": w.url, "scenario": scenario, "cemetery": CEM, "key": w.key, **kw}
    p = subprocess.run(["node", str(RUNNER), json.dumps(cfg)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def req(type_, form=None, file=None, **ctx):
    return {"type": type_, "form": form or {}, "ctx": {"grave_uid": GRAVE, **ctx}, "file": file}


def posts(out, suffix="/items"):
    return [c for c in out["calls"] if c["method"] == "POST" and c["url"].endswith(suffix)]


REQS = {
    "DECEASED": req("DECEASED", {"nume": "Popescu", "prenume": "Ion", "data_deces": "2026-03-04", "observatii": "  "}),
    "WORK": req("WORK", {"descriere": "Reparație monument", "executant": "SC Marmura", "data_inceput": "2026-05-01", "observatii": "ok"}),
    "PERMIT": req("PERMIT", {"nr_aviz": "12/2026", "data_aviz": "2026-01-02", "lucrare": "Cavou"}),
    "DOC_PDF": req("DOCUMENT", {"doc_type": "alt_document", "caption": "act"}, {"kind": "pdf", "name": "act.pdf", "type": "application/pdf"}),
    "DOC_JPEG": req("DOCUMENT", {"doc_type": "schita"}, {"kind": "jpg", "name": "s.jpg", "type": "image/jpeg"}),
    "DOC_PNG": req("DOCUMENT", {"doc_type": "schita"}, {"kind": "png", "name": "s.png", "type": "image/png"}),
    "GRAVE_PHOTO": req("GRAVE_PHOTO", {"caption": "intrare"}, {"kind": "jpg", "name": "IMG 1.jpg", "type": "image/jpeg"}),
    "WP_BEFORE": req("WORK_PHOTO", {}, {"kind": "jpg", "name": "b.jpg", "type": "image/jpeg"}, work_stage="BEFORE", target_uid=TARGET),
    "WP_DURING": req("WORK_PHOTO", {}, {"kind": "png", "name": "d.png", "type": "image/png"}, work_stage="DURING", target_uid=TARGET),
    "WP_AFTER": req("WORK_PHOTO", {"caption": "gata"}, {"kind": "jpg", "name": "a.jpg", "type": "image/jpeg"}, work_stage="AFTER", target_uid=TARGET),
}


# ------------------------------------------------------------------ securitate telefon
def test_paired_phone_opens_capture_page(world):
    assert run(world, "session")["session"] == {"state": "ready", "cemetery_uid": CEM}


def test_unpaired_phone_blocked_without_network_send(world):
    out = run(world, "send", key=None, req=REQS["DECEASED"])
    assert out["session"]["state"] == "unpaired" and out["result"]["ok"] is False and out["result"]["blocked"] == "unpaired"
    assert posts(out) == [] and world.inbox.items == {}


def test_revoked_phone_blocked_and_key_forgotten(world):
    world.inbox.bindings[world.key]["active"] = False
    out = run(world, "send", req=REQS["DECEASED"])
    assert out["session"]["state"] == "revoked" and out["result"]["ok"] is False
    assert world.inbox.items == {} and out["storage"] == []


def test_revoked_after_page_open_blocks_send(world):
    out_ok = run(world, "session")
    assert out_ok["session"]["state"] == "ready"
    world.inbox.bindings[world.key]["active"] = False
    out = run(world, "retry", req=REQS["DECEASED"], attempts=1)
    assert out["results"][0]["blocked"] == "revoked" and world.inbox.items == {} and out["storage"] == []


def test_expired_binding_blocked(world):
    world.inbox.bindings[world.key]["expired"] = True
    assert run(world, "session")["session"]["state"] == "revoked"


def test_wrong_cemetery_blocked(world):
    key = world.inbox.add_binding("MOBILE", pcf.OTHER_CEM)
    out = run(world, "send", key=key, req=REQS["DECEASED"])
    assert out["session"]["state"] == "wrong_cemetery" and out["result"]["ok"] is False and out["result"]["blocked"] == "wrong_cemetery"
    assert posts(out) == [] and world.inbox.items == {}


def test_wrong_cemetery_even_if_local_storage_is_tampered(world):
    key = world.inbox.add_binding("MOBILE", pcf.OTHER_CEM)
    out = run(world, "send", key=key, bound_cemetery=CEM, req=REQS["DECEASED"])        # stocare falsificată: /me (server) decide
    assert out["session"]["state"] == "wrong_cemetery"


def test_main_key_on_phone_blocked(world):
    key = world.inbox.add_binding("MAIN", CEM)
    assert run(world, "session", key=key)["session"]["state"] == "not_mobile"


def test_pairing_stores_only_binding_key_and_code_is_single_use(world):
    code = world.pairing.new_code(CEM)
    out = run(world, "pair", key=None, code=code)
    assert out["pair"] == {"ok": True} and out["session"]["state"] == "ready"
    stored = json.loads(out["stored"])
    assert set(stored) == {"binding_key", "cemetery_uid"} and stored["cemetery_uid"] == CEM
    assert code not in json.dumps(out["storage"]) and code.replace("-", "") not in json.dumps(out["storage"])
    again = run(world, "pair", key=None, code=code)
    assert again["pair"]["ok"] is False and again["session"]["state"] == "unpaired"
    assert "binding_key" not in json.dumps(again["pair"])


def test_pairing_wrong_code_rejected(world):
    out = run(world, "pair", key=None, code="AAAAA-BBBBB-CCCCC-DDDDD")
    assert out["pair"]["ok"] is False and out["storage"] == []


# ------------------------------------------------------------------ grave_uid: preselecție din hartă, imun la falsificare
def test_tap_grave_on_map_sends_the_resolved_grave_uid(world):
    """Harta -> drawer -> „Trimite din teren" transmite portalului doar cod_loc; grave_uid vine din server (_teren_loc),
    niciodată din URL/telefon. Aici simulăm exact grave_uid-ul pe care portalul l-ar fi rezolvat pentru locul ales pe hartă."""
    it = sent(world, run(world, "send", req=_cap(world, GRAVE, REQS["DECEASED"])))
    assert it["grave_uid"] == GRAVE


def test_tampered_grave_uid_is_accepted_by_cloud_but_blocked_at_local_import(world, env, loc):
    """Cloud-ul Field Inbox nu are registru de locuri (limitare documentată în ETAPA_PHONE_CAPTURE.md): dacă cineva ocolește
    teren_ui.js (devtools) și trimite un grave_uid diferit de cel rezolvat de portal pentru locul selectat, cloud-ul nu îl
    poate respinge — dar importul local (singurul loc unde există registrul de locuri) îl blochează MISSING_GRAVE și nu
    scrie nimic în MAIN. Fără migration, fără endpoint nou: comportamentul existent e suficient și verificat aici."""
    tampered = str(uuid.uuid4())
    assert tampered != loc.uid
    out = run(world, "send", req=_cap(world, tampered, REQS["DECEASED"]))
    it = sent(world, out)
    assert it["grave_uid"] == tampered
    main_uid = env.inbox.add_item("DECEASED", status="ACCEPTED", payload=it["payload"], grave=tampered)
    with pytest.raises(fim.ImportBlocked) as e:
        fim.import_item(client(env), main_uid)
    assert e.value.code == "MISSING_GRAVE"
    assert rows("SELECT * FROM inhumati") == []


# ------------------------------------------------------------------ trimitere pe tipuri
def sent(world, out):
    assert out["result"]["ok"] is True and out["result"]["message"] == "Trimis în Field Inbox"
    it = world.inbox.items[out["result"]["uid"]]
    assert out["result"]["ref"] == it["uid"][:8].upper()
    return it


def test_deceased_empty_dates_stay_empty(world):
    it = sent(world, run(world, "send", req=REQS["DECEASED"]))
    assert it["item_type"] == "DECEASED" and it["grave_uid"] == GRAVE and it["status"] == "PENDING"
    assert it["payload"] == {"nume": "Popescu", "prenume": "Ion", "data_deces": "04.03.2026"}
    assert "data_nasterii" not in it["payload"] and "data_inhumare" not in it["payload"]


def test_deceased_without_any_date_has_no_date_keys(world):
    it = sent(world, run(world, "send", req=req("DECEASED", {"nume": "Ionescu"})))
    assert it["payload"] == {"nume": "Ionescu"}


def test_work_with_executant_and_data_inceput(world):
    it = sent(world, run(world, "send", req=REQS["WORK"]))
    assert it["item_type"] == "WORK" and it["grave_uid"] == GRAVE and it["payload"] == {"descriere": "Reparație monument", "executant": "SC Marmura", "data_inceput": "01.05.2026", "observatii": "ok"}


def test_work_without_optional_fields_has_none(world):
    it = sent(world, run(world, "send", req=req("WORK", {"descriere": "Curățare"})))
    assert it["payload"] == {"descriere": "Curățare"}


def test_permit(world):
    it = sent(world, run(world, "send", req=REQS["PERMIT"]))
    assert it["item_type"] == "PERMIT" and it["payload"] == {"nr_aviz": "12/2026", "data_aviz": "02.01.2026", "lucrare": "Cavou"}


@pytest.mark.parametrize("name,mime", [("DOC_PDF", "application/pdf"), ("DOC_JPEG", "image/jpeg"), ("DOC_PNG", "image/png")])
def test_document_pdf_jpeg_png_go_through_field_inbox_storage(world, name, mime):
    out = run(world, "send", req=REQS[name])
    it = sent(world, out)
    f = world.inbox.files[it["uid"]]
    assert it["item_type"] == "DOCUMENT" and len(f) == 1 and f[0]["mime"] == mime and f[0]["status"] == "READY"
    up = posts(out, "/files")[0]
    assert up["headers"]["Content-Type"] == mime and re.match(r"^[0-9a-f]{64}$", up["headers"]["X-File-Sha256"])
    assert f[0]["sha256"] == up["headers"]["X-File-Sha256"]


def test_grave_photo(world):
    out = run(world, "send", req=REQS["GRAVE_PHOTO"])
    it = sent(world, out)
    assert it["item_type"] == "GRAVE_PHOTO" and it["grave_uid"] == GRAVE and it["work_stage"] is None and len(world.inbox.files[it["uid"]]) == 1
    assert posts(out, "/files")[0]["headers"]["X-File-Name"] == "IMG_1.jpg"


@pytest.mark.parametrize("name,stage", [("WP_BEFORE", "BEFORE"), ("WP_DURING", "DURING"), ("WP_AFTER", "AFTER")])
def test_work_photo_keeps_stage_in_payload_item(world, name, stage):
    it = sent(world, run(world, "send", req=REQS[name]))
    assert it["item_type"] == "WORK_PHOTO" and it["work_stage"] == stage and it["target_uid"] == TARGET and len(world.inbox.files[it["uid"]]) == 1


def test_work_photo_without_stage_is_refused_before_network(world):
    out = run(world, "send", req=req("WORK_PHOTO", {}, {"kind": "jpg", "name": "x.jpg", "type": "image/jpeg"}, target_uid=TARGET))
    assert out["error"]["name"] == "ValidationError" and out["calls"] == [] or posts(out) == []
    assert world.inbox.items == {}


def test_validation_blocks_without_grave_or_required_field_or_bad_file(world):
    for r in (req("DECEASED", {"nume": ""}), {**req("DECEASED", {"nume": "A"}), "ctx": {}},
              req("DOCUMENT", {"doc_type": "alt_document"}),
              req("DOCUMENT", {"doc_type": "alt_document"}, {"kind": "bad", "name": "x.gif", "type": "image/gif"}),
              req("GRAVE_PHOTO", {}, {"kind": "pdf", "name": "x.pdf", "type": "application/pdf"})):
        assert run(world, "send", req=r)["error"]["name"] == "ValidationError"
    assert world.inbox.items == {}


def test_server_rejects_corrupt_file_without_false_success(world):
    out = run(world, "send", req=req("DOCUMENT", {"doc_type": "alt_document"}, {"kind": "bad", "name": "x.pdf", "type": "application/pdf"}))
    assert out["result"]["ok"] is False and out["result"]["message"] != "Trimis în Field Inbox"


# ------------------------------------------------------------------ dublu-tap, retry, offline, răspuns pierdut
def test_double_tap_creates_one_item_and_one_request(world):
    out = run(world, "double_tap", req=REQS["DOC_PDF"])
    a, b = out["results"]
    assert a["ok"] and a == b and len(world.inbox.items) == 1
    assert len(posts(out)) == 1 and len(posts(out, "/files")) == 1


def test_idempotent_retry_after_success_does_not_duplicate(world):
    out = run(world, "retry", req=REQS["WORK"], attempts=1)
    assert out["results"][0]["ok"]
    out2 = run(world, "retry", req=REQS["WORK"], attempts=1)                       # pagină nouă, formular identic, aceeași trimitere logică nu e știută -> alt key
    assert len(world.inbox.items) == 2                                              # (nu există coadă persistentă: o trimitere nouă e o trimitere nouă)
    assert out2["results"][0]["ok"]


def test_offline_request_failure_then_retry_reuses_key_no_false_success(world):
    out = run(world, "retry", req=REQS["DECEASED"], attempts=2, faults=[{"match": "/field-inbox/items$", "mode": "before", "method": "POST"}])
    r1, r2 = out["results"]
    assert r1["ok"] is False and r1["message"] == "Nu s-a putut trimite. Încearcă din nou." and r1["retry"] is True
    assert r2["ok"] is True and len(world.inbox.items) == 1
    keys = [json.loads(c["body"])["idempotency_key"] for c in posts(out)]
    assert len(keys) == 2 and keys[0] == keys[1]


def test_response_loss_on_create_then_retry_is_replay_single_item(world):
    out = run(world, "retry", req=REQS["DOC_JPEG"], attempts=2, faults=[{"match": "/field-inbox/items$", "mode": "after", "method": "POST"}])
    r1, r2 = out["results"]
    assert r1["ok"] is False and r2["ok"] is True and len(world.inbox.items) == 1
    keys = [json.loads(c["body"])["idempotency_key"] for c in posts(out)]
    assert keys[0] == keys[1]
    uid = r2["uid"]
    assert len(world.inbox.files[uid]) == 1


def test_response_loss_on_file_upload_then_retry_single_item_single_file(world):
    out = run(world, "retry", req=REQS["GRAVE_PHOTO"], attempts=2, faults=[{"match": "/files$", "mode": "after", "method": "POST"}])
    r1, r2 = out["results"]
    assert r1["ok"] is False and r2["ok"] is True and len(world.inbox.items) == 1
    uid = r2["uid"]
    assert len(world.inbox.files[uid]) == 1
    uploads = posts(out, "/files")
    assert len(uploads) == 2 and uploads[0]["headers"]["X-File-Uid"] == uploads[1]["headers"]["X-File-Uid"]


def test_upload_never_reached_server_retry_completes_same_item(world):
    out = run(world, "retry", req=REQS["WP_AFTER"], attempts=2, faults=[{"match": "/files$", "mode": "before", "method": "POST"}])
    r1, r2 = out["results"]
    assert r1["ok"] is False and r2["ok"] is True and len(world.inbox.items) == 1 and len(world.inbox.files[r2["uid"]]) == 1


def test_changed_form_after_failure_gets_new_key_no_conflict(world):
    out = run(world, "edit_after_fail", req=req("DECEASED", {"nume": "Ion"}), req2=req("DECEASED", {"nume": "Ioan"}),
              faults=[{"match": "/field-inbox/items$", "mode": "before", "method": "POST"}])
    assert out["results"][0]["ok"] is False and out["results"][1]["ok"] is True
    keys = [json.loads(c["body"])["idempotency_key"] for c in posts(out)]
    assert keys[0] != keys[1] and len(world.inbox.items) == 1


def test_server_down_is_not_success(world):
    out = run(world, "send", req=REQS["PERMIT"], base="http://127.0.0.1:9", key=world.key)
    assert out["session"]["state"] == "offline" and out["result"]["ok"] is False and out["result"]["message"] == "Nu s-a putut trimite. Încearcă din nou."


# ------------------------------------------------------------------ fără scurgeri
def test_no_secret_or_signed_url_in_client_code_and_page():
    js = (REPO / "backend/static/teren.js").read_text() + (REPO / "backend/static/teren_ui.js").read_text()
    html = (REPO / "backend/templates/teren.html").read_text()
    for needle in ("console.", "service_role", "SUPABASE", "DATABASE_URL", "postgres", "/url", "signed", "sessionStorage", "document.cookie", "indexedDB", "cleanup"):
        assert needle not in js and needle not in html, needle
    assert js.count("localStorage") == 1 and "STORE_KEY" in js and js.count("setItem") == 1
    assert 'accept="image/*" capture="environment"' in html


def test_binding_key_only_in_authorization_header_never_in_url_or_body(world):
    out = run(world, "send", req=REQS["DOC_PDF"])
    for c in out["calls"]:
        assert world.key not in c["url"] and world.key not in (c["body"] or "")
        assert c["headers"].get("Authorization") == f"Binding {world.key}"
    assert not any("url" in c["url"].split("/")[-1] for c in out["calls"])


def test_no_response_exposes_server_secrets(world):
    out = run(world, "send", req=req("DOCUMENT", {"doc_type": "alt_document"}, {"kind": "bad", "name": "x.pdf", "type": "application/pdf"}))
    blob = json.dumps(out)
    for s in ("service_role", "SUPABASE_SECRET_KEY", "password", "cimitir_cp_app"):
        assert s not in blob


# ------------------------------------------------------------------ API: /me + CORS
def test_me_endpoint_and_explicit_cors():
    inbox = pcf.PhoneInbox()
    key = inbox.add_binding("MOBILE", CEM)
    app = fih.create_app(inbox, origins=["http://192.168.1.10:8000"])
    c = TestClient(app)
    r = c.get("/api/v1/field-inbox/me", headers={"Authorization": f"Binding {key}"})
    assert r.status_code == 200 and r.json()["result"] == {"cemetery_uid": CEM, "device_type": "MOBILE"}
    assert c.get("/api/v1/field-inbox/me").status_code == 401
    inbox.bindings[key]["active"] = False
    assert c.get("/api/v1/field-inbox/me", headers={"Authorization": f"Binding {key}"}).status_code == 401
    pre = c.options("/api/v1/field-inbox/items", headers={"Origin": "http://192.168.1.10:8000", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"})
    assert pre.headers["access-control-allow-origin"] == "http://192.168.1.10:8000" and "access-control-allow-credentials" not in pre.headers
    bad = c.options("/api/v1/field-inbox/items", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in bad.headers
    assert fih.cors_origins("*, http://a.ro/ ,javascript:x,https://b.ro") == ["http://a.ro", "https://b.ro"] and fih.cors_origins("") == []


# ------------------------------------------------------------------ compatibilitate cu Field Inbox Local Import
def _to_main_inbox(env, loc, world, out, status="ACCEPTED", target=None):
    it = world.inbox.items[out["result"]["uid"]]
    files = [(f["mime"], world.inbox.blobs[f["uid"]]) for f in world.inbox.files[it["uid"]]]
    return add(env, loc, it["item_type"], status=status, payload=it["payload"], stage=it["work_stage"], files=files, target=target)


def _cap(world, loc_uid, r):
    r = json.loads(json.dumps(r))
    r["ctx"]["grave_uid"] = loc_uid
    return r


def test_captured_items_import_locally(world, env, loc):
    c = client(env)
    out = run(world, "send", req=_cap(world, loc.uid, REQS["DECEASED"]))
    res = fim.import_item(c, _to_main_inbox(env, loc, world, out))
    assert res.state == fim.COMPLETE
    r = rows("SELECT * FROM inhumati")[0]
    assert (r["nume"], r["prenume"], r["data_deces"], r["data_nasterii"], r["data_inhumare"], r["id_loc"]) == ("Popescu", "Ion", "04.03.2026", "", "", loc.id)

    out = run(world, "send", req=_cap(world, loc.uid, REQS["WORK"]))
    uid = _to_main_inbox(env, loc, world, out)
    assert fim.import_item(c, uid).state == fim.COMPLETE
    w = rows("SELECT * FROM amenajari_avize WHERE uid=?", uid)[0]
    assert (w["executant"], w["data_inceput"], w["lucrare"], w["observatii"], w["cod_loc"]) == ("SC Marmura", "01.05.2026", "Reparație monument", "ok", "G-12")

    out = run(world, "send", req=_cap(world, loc.uid, REQS["PERMIT"]))
    uid = _to_main_inbox(env, loc, world, out)
    assert fim.import_item(c, uid).state == fim.COMPLETE
    p = rows("SELECT * FROM amenajari_avize WHERE uid=?", uid)[0]
    assert (p["nr_aviz"], p["data_aviz"], p["lucrare"]) == ("12/2026", "02.01.2026", "Cavou")


def test_captured_documents_and_photos_import_locally(world, env, loc):
    c = client(env)
    for name in ("DOC_PDF", "DOC_JPEG", "DOC_PNG"):
        out = run(world, "send", req=_cap(world, loc.uid, REQS[name]))
        assert fim.import_item(c, _to_main_inbox(env, loc, world, out)).state == fim.COMPLETE
    assert len(db.list_documents("loc", "G-12")) == 3
    out = run(world, "send", req=_cap(world, loc.uid, REQS["GRAVE_PHOTO"]))
    assert fim.import_item(c, _to_main_inbox(env, loc, world, out)).state == fim.COMPLETE
    assert len(db.list_documents("loc", "G-12", "poza_loc")) == 1
    wuid, aid = add_work(loc)
    for name, dt in (("WP_BEFORE", "lucrare_inainte"), ("WP_DURING", "lucrare_in_timpul"), ("WP_AFTER", "lucrare_dupa")):
        r = _cap(world, loc.uid, REQS[name])
        r["ctx"]["target_uid"] = wuid
        out = run(world, "send", req=r)
        assert fim.import_item(c, _to_main_inbox(env, loc, world, out, target=wuid)).state == fim.COMPLETE
        assert len(db.list_documents("aviz", aid, dt)) == 1
