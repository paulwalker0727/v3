# ruff: noqa: F811
"""P0 — asocierea MAIN ↔ Cloud pentru un registru local CU DATE (Flux B), prin aplicația Cloud REALĂ servită prin HTTP real peste PostgreSQL LOCAL.
Rezultate = LOCAL (nu LIVE/Supabase). Windows/DPAPI: netestat aici (Linux). Reproduce eroarea „Flux A indisponibil: decizia pentru starea curentă este B”."""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

import pytest

import cimitir_bootstrap as bs
import cimitir_cloud_admin as ad
import cimitir_cloud_associate as assoc
import cimitir_cloud_client as client
from test_cloud_associate_pg import ca, code, make  # noqa: F401
from test_cloud_admin_pg import EMAIL, PW  # noqa: F401
from test_etapa4_pas1 import env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa4_pas2_manifest import DATA, add_file, put_local, sql
from test_etapa6b_cleanup_race_pg import adm, pgdb  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PostgreSQL local de test indisponibil")


@pytest.fixture()
def cb(ca):
    """Registrul local primește date fictive + fișiere; cloud-ul rămâne DRAFT și gol (starea reală din raportul utilizatorului)."""
    b = ca["b"]
    put_local(b, DATA)
    add_file(b, "a.jpg", b"AAAA")
    add_file(b, "c.bin", b"CCCCCCC")
    ca["base"] = cloud_counts(ca)
    return ca


def local_fingerprint(b):
    c = sqlite3.connect(b.database)
    try:
        out = {}
        for t in ("locuri_de_veci", "concesionari", "contracte", "inhumati", "plati", "amenajari_avize", "documents"):
            out[t] = sorted(repr(r) for r in c.execute(f"SELECT * FROM {t}"))
        return out
    finally:
        c.close()


def cloud_counts(ca):
    with adm(ca["pg"]) as c:
        return {"orgs": c.execute("SELECT count(*) FROM organizations").fetchone()[0], "cems": c.execute("SELECT count(*) FROM cemeteries").fetchone()[0]}


def test_reported_error_is_reproduced_by_flux_a_only_and_fixed(cb, monkeypatch):
    """Înainte de corecție plan() chema doar Flux A și arunca „Flux A indisponibil … B”. Acum verify_code anunță fluxul B, iar plan() construiește planul B."""
    a = make(cb)
    c = code(cb)
    v = a.verify_code(c)
    assert v["flow"] == "B" and v["decision"] == "B" and v["local_classification"] == "nonempty" and v["cloud_classification"] == "empty"
    with pytest.raises(bs.BlockedFlow) as ei:                                   # Flux A rămâne refuzat pentru registru cu date (nu e slăbit)
        bs.plan_flow_a(cb["b"], client.RemoteControlPlane(cb["srv"].url, c), c, cb["r"].inst)
    assert "Flux A indisponibil" in str(ei.value)
    p = a.plan(c, "Craiova TEST")
    assert p["flow"] == "B" and p["plan"]["kind"] == "B"
    assert "Date de încărcat" in p["summary"] and "Fișiere media" in p["summary"] and "Flux B" in p["summary"]["Operațiune"]
    assert bs.LocalRegistry(cb["b"]).sync_state in ("UNBOUND", "PLANNED")        # nimic legat înainte de confirmare


def test_flux_b_end_to_end_request_approval_code_verify_associate_restart(cb):
    b, ca_ = cb["b"], cb
    before = local_fingerprint(b)
    counts0 = cloud_counts(cb)
    a = make(cb)
    # cerere de înregistrare (MAIN neasociat) -> aprobare administrator Cloud -> cod
    with pytest.raises(assoc.AssociationError):
        a.request_registration("Org", "Craiova TEST", confirmed=False)
    r = a.request_registration("Org 1", "Craiova TEST", True)
    assert r["short_id"] and a.pending_request() == r["request_id"]
    assert a.complete_registration("Craiova TEST")["state"] == "pending"
    pend = ca_["admin"].list_requests()
    assert len(pend) == 1
    ca_["admin"].decide_request(pend[0]["request_id"] if "request_id" in pend[0] else pend[0]["id"], True)
    res = a.complete_registration("Craiova TEST")
    assert res["state"] == "associated" and "Sincronizarea este activă" in res["message"]
    reg = bs.LocalRegistry(b)
    assert reg.sync_state == "BOUND" and reg.get(assoc.SET_UNVERIFIED) == "" and a.pending_request() is None
    key = ca_["r"].store.get(reg.get("cloud_secret_ref"))
    assert key and key.startswith("cimitir.") and key.encode() not in Path(b.database).read_bytes()
    # identitatea și datele locale rămân neschimbate (UID-uri originale), fără organizații/cimitire duplicate
    assert local_fingerprint(b) == before
    assert reg.get("cloud_url") == ca_["srv"].url
    assert cloud_counts(cb) == counts0
    me = ca_["admin"].me()
    assert [o["organization_uid"] for o in me["organizations"]] == [b.organization_uid]
    assert [c["uid"] for o in me["organizations"] for c in o["cemeteries"]] == [b.cemetery_uid]
    assert [c["status"] for o in me["organizations"] for c in o["cemeteries"]] == ["ACTIVE"]
    devs = ca_["admin"].list_devices(b.organization_uid)
    assert [(d["kind"], d["state"]) for d in devs] == [("main", "active")]
    # „repornire”: obiecte noi, aceeași bază SQLite + același SecretStore => asocierea persistă
    a2 = assoc.Association(ca_["r"].cat.binding_for(b.cemetery_uid), ca_["r"].store, ca_["r"].inst, ca_["srv"].url)
    assert a2.local.sync_state == "BOUND" and a2.incomplete() is None
    assert a2.verify()["sync_enabled"]
    # codul consumat nu mai poate fi reutilizat și o a doua asociere e refuzată (deja asociat)
    c2 = code(cb)
    with pytest.raises(assoc.AssociationError) as ei:
        a2.plan(c2)
    assert ei.value.kind == "blocked" and "deja asociat" in str(ei.value)
    assert cloud_counts(cb) == counts0


def test_flux_b_direct_code_flow_and_cloud_has_data(cb):
    b = cb["b"]
    a = make(cb)
    c = code(cb)
    p = a.plan(c, "Craiova TEST")
    with pytest.raises(assoc.AssociationError) as ei:
        a.execute(p["plan"], c, confirmed=False)
    assert ei.value.kind == "confirm" and bs.LocalRegistry(b).get("cloud_secret_ref") == ""
    res = a.execute(p["plan"], c, confirmed=True)
    rep = res["report"]
    assert rep["kind"] == "B" and rep["post_publish_verified"] is True and rep["cloud_rows"] == sum(rep["entity_counts"].values())
    assert rep["entity_counts"]["locuri_de_veci"] == 3 and rep["media"]["missing"] == 0
    assert set(res["verification"]["checks"].values()) == {"OK"}
    assert bs.LocalRegistry(b).sync_state == "BOUND"


# ===================================================================== coduri: expirat / folosit / revocat / altă organizație / alt cimitir
def test_used_code_is_rejected(cb):
    a = make(cb)
    c = code(cb)
    p = a.plan(c, "Craiova TEST")
    a.execute(p["plan"], c, True)
    other = make(cb)
    other.local.put(sync_state="UNBOUND")
    with pytest.raises(assoc.AssociationError) as ei:
        other.verify_code(c)
    assert ei.value.kind == "code"


def test_revoked_code_is_rejected(cb):
    a, c_issued = make(cb), cb["admin"].issue_code(cb["b"].organization_uid, cb["b"].cemetery_uid, "main")
    cb["admin"].revoke_code(cb["b"].organization_uid, c_issued["code_id"])
    with pytest.raises(assoc.AssociationError) as ei:
        a.verify_code(c_issued["code"])
    assert ei.value.kind == "code"
    with pytest.raises(assoc.AssociationError):
        a.plan(c_issued["code"])
    assert bs.LocalRegistry(cb["b"]).get("cloud_secret_ref") == "" and cloud_counts(cb) == cb["base"]


def test_expired_code_is_rejected(cb):
    a = make(cb)
    issued = cb["admin"].issue_code(cb["b"].organization_uid, cb["b"].cemetery_uid, "main")
    with adm(cb["pg"]) as c:
        c.execute("UPDATE device_pairing_codes SET expires_at = now() - interval '5 seconds'")
    with pytest.raises(assoc.AssociationError) as ei:
        a.verify_code(issued["code"])
    assert ei.value.kind == "code"


def test_code_of_another_organization_is_rejected_and_nothing_changes(cb):
    w, b = cb["w"], cb["b"]
    cem2 = str(uuid.uuid4())
    cb["r"].cp.create_cemetery(w.owner2, w.org2, cem2, "Alt cimitir")
    foreign = cb["r"].cp.issue_code(w.owner2, w.org2, cem2)
    a = make(cb)
    before = (local_fingerprint(b), cloud_counts(cb))
    with pytest.raises(assoc.AssociationError) as ei:
        a.verify_code(foreign)
    assert ei.value.kind in ("code", "scope")
    with pytest.raises(assoc.AssociationError):
        a.plan(foreign)
    assert (local_fingerprint(b), cloud_counts(cb)) == before
    assert bs.LocalRegistry(b).get("cloud_secret_ref") == "" and cb["admin"].list_devices(b.organization_uid) == []


def test_code_for_another_cemetery_of_same_org_cannot_pair_this_registry(cb):
    w, b = cb["w"], cb["b"]
    cem2 = str(uuid.uuid4())
    cb["r"].cp.create_cemetery(w.owner1, w.org1, cem2, "Cimitir 2")
    c2 = cb["admin"].issue_code(w.org1, cem2, "main")["code"]
    a = make(cb)
    with pytest.raises(assoc.AssociationError):
        a.plan(c2)
    assert bs.LocalRegistry(b).get("cloud_secret_ref") == "" and bs.LocalRegistry(b).sync_state in ("UNBOUND", "PLANNED")
    assert cb["admin"].list_devices(w.org1) == []


def test_phone_code_cannot_pair_main(cb):
    c = cb["admin"].issue_code(cb["b"].organization_uid, cb["b"].cemetery_uid, "phone")["code"]
    a = make(cb)
    with pytest.raises(assoc.AssociationError):
        p = a.plan(c)
        a.execute(p["plan"], c, True)
    assert bs.LocalRegistry(cb["b"]).sync_state != "BOUND"


# ===================================================================== întrerupere / reluare / anulare / rollback pe Flux B
def _crashing(op):
    def factory(base):
        rcp = client.RemoteControlPlane(base, retries=0)

        real = rcp.remote_call

        def remote_call(name, args, kwargs):
            if name == op:
                raise bs.Crash("proces întrerupt (test)")
            return real(name, args, kwargs)
        rcp.remote_call = remote_call
        return rcp
    return factory


def test_flux_b_crash_before_publish_resume_without_new_code(cb):
    b = cb["b"]
    c = code(cb)
    a = assoc.Association(b, cb["r"].store, cb["r"].inst, cb["srv"].url, rcp_factory=_crashing("media_put_chunk"))
    p = a.plan(c, "Craiova TEST")
    with pytest.raises(assoc.AssociationError) as ei:
        a.execute(p["plan"], c, True)
    assert ei.value.kind == "interrupted"
    bs.release_lock(b)
    ok = make(cb)
    info = ok.incomplete()
    assert info and info["kind"] == "B" and "resume" in info["actions"]
    res = ok.resume()
    assert res["report"]["kind"] == "B" and res["verification"]["sync_enabled"] and bs.LocalRegistry(b).sync_state == "BOUND"
    assert cloud_counts(cb) == cb["base"]


def test_flux_b_abort_restores_clean_state_and_retry_works_with_new_code(cb):
    b = cb["b"]
    before = local_fingerprint(b)
    c = code(cb)
    a = assoc.Association(b, cb["r"].store, cb["r"].inst, cb["srv"].url, rcp_factory=_crashing("media_put_chunk"))
    p = a.plan(c, "Craiova TEST")
    with pytest.raises(assoc.AssociationError):
        a.execute(p["plan"], c, True)
    bs.release_lock(b)
    ref = bs.LocalRegistry(b).get("cloud_secret_ref")
    assert ref and cb["r"].store.get(ref)
    ok = make(cb)
    ok.abort()
    reg = bs.LocalRegistry(b)
    assert reg.sync_state == "UNBOUND" and ok.incomplete() is None and reg.get(assoc.SET_UNVERIFIED) == "" and cb["r"].store.get(ref) is None
    assert local_fingerprint(b) == before
    assert [d["state"] for d in cb["admin"].list_devices(b.organization_uid)] == ["revoked"]
    c2 = code(cb)
    p2 = ok.plan(c2, "Craiova TEST")
    assert ok.execute(p2["plan"], c2, True)["verification"]["sync_enabled"]
    assert cloud_counts(cb) == cb["base"]


def test_flux_b_failure_before_publish_rolls_back_fully(cb, monkeypatch):
    """O eroare (nu cădere de proces) înainte de publicare => rollback complet: legătura revocată, secret șters, local neschimbat, fără rămășițe de plan."""
    b = cb["b"]
    before = local_fingerprint(b)
    a = make(cb)
    c = code(cb)
    p = a.plan(c, "Craiova TEST")
    import cimitir_flow_b as fb

    def boom(*x, **k):
        raise fb.VerificationFailed("verificare simulată eșuată")
    monkeypatch.setattr(fb, "verify_before_publish", boom)
    with pytest.raises(assoc.AssociationError) as ei:
        a.execute(p["plan"], c, True)
    assert ei.value.kind == "failed" and "verificare simulată" in str(ei.value)
    reg = bs.LocalRegistry(b)
    assert reg.sync_state == "UNBOUND" and reg.get("cloud_secret_ref") == "" and reg.get(assoc.SET_UNVERIFIED) == "" and reg.get(assoc.SET_PENDING_PLAN) == ""
    assert local_fingerprint(b) == before and a.incomplete() is None
    assert cloud_counts(cb) == cb["base"]
    assert "cimitir." not in repr(ei.value) and c not in str(ei.value)


def test_missing_media_requires_explicit_acceptance(cb):
    b = cb["b"]
    sql(b, "INSERT INTO documents (entity_type, entity_id, doc_type, file_path) VALUES ('loc','A-1','poza','lipsa.jpg')")
    a, c = make(cb), code(cb)
    p = a.plan(c, "Craiova TEST")
    assert p["plan"]["media_missing"] == ["lipsa.jpg"] and "LIPSĂ" in p["summary"]["Fișiere media"]
    with pytest.raises(assoc.AssociationError) as ei:
        a.execute(p["plan"], c, True)
    assert ei.value.kind == "media_missing" and bs.LocalRegistry(b).sync_state != "BOUND"
    res = a.execute(p["plan"], c, True, accept_missing_media=True)
    assert bs.LocalRegistry(b).sync_state == "BOUND_MEDIA_PENDING" and res["verification"]["sync_enabled"]


# ===================================================================== Flux A separat (registru gol) nu e confundat cu B
def test_flux_a_still_chosen_for_empty_registry_and_not_confused_with_b(ca):
    a, c = make(ca), code(ca)
    assert a.verify_code(c)["flow"] == "A"
    p = a.plan(c, "Craiova TEST")
    assert p["flow"] == "A" and p["plan"]["kind"] == "A" and "Flux A" in p["summary"]["Operațiune"]
    res = a.execute(p["plan"], c, True)
    assert res["report"]["kind"] == "A" and bs.LocalRegistry(ca["b"]).sync_state == "BOUND"


def test_cloud_with_data_and_local_with_data_is_blocked_without_changes(cb):
    """Registru local cu date + cloud cu date diferite => refuz explicit (fără îmbinare automată)."""
    a, c = make(cb), code(cb)
    p = a.plan(c, "Craiova TEST")
    a.execute(p["plan"], c, True)                                                # cloud primește datele
    a.disassociate()                                                             # local rămâne cu date, cloud rămâne cu date
    c2 = code(cb)
    v = make(cb).verify_code(c2)
    assert v["flow"] is None and v["decision"] in ("LINK", "BLOCKED")
    with pytest.raises(assoc.AssociationError) as ei:
        make(cb).plan(c2)
    assert ei.value.kind == "blocked" and "îmbinare automată" in str(ei.value)
    assert cloud_counts(cb) == cb["base"]


def test_http_flow_does_not_leak_secrets_in_messages_or_logs(cb, caplog):
    a, c = make(cb), code(cb)
    p = a.plan(c, "Craiova TEST")
    res = a.execute(p["plan"], c, True)
    key = cb["r"].store.get(bs.LocalRegistry(cb["b"]).get("cloud_secret_ref"))
    blob = repr(res) + repr(p["summary"]) + caplog.text
    assert key not in blob and c not in blob and ad.group_code(c) not in blob
    raw = Path(cb["b"].database).read_bytes()
    assert c.encode() not in raw and ad.group_code(c).encode() not in raw and key.encode() not in raw       # nici codul, nici cheia în SQLite (inclusiv planul salvat)
    assert "CNP" not in json.dumps(p["plan"]) and "1800101000001" not in json.dumps(p["plan"])                # planul păstrat nu conține date personale


# ===================================================================== fereastra Qt (offscreen) — exact scenariul raportat: registru cu date + „Verifică codul”
def _pump(qt, cond, timeout=40):
    t = time.time()
    while time.time() - t < timeout:
        qt.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_qt_dialog_verify_code_then_associate_flux_b(cb, monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    desk = Path(__file__).resolve().parents[2] / "desktop"
    monkeypatch.syspath_prepend(str(desk))
    monkeypatch.syspath_prepend(str(desk.parent / "backend"))
    from PySide6.QtWidgets import QApplication
    qt = QApplication.instance() or QApplication([])
    import cloud_associate_window as win
    r = cb["r"]
    dlg = win.CloudAssociateDialog(None, catalog=r.cat, store=r.store, can_manage=lambda: True)
    dlg.cmb_local.setCurrentIndex(dlg.cmb_local.findData(cb["b"].cemetery_uid))
    dlg.ed_url.setText(cb["srv"].url)
    c = code(cb)
    dlg.ed_code.setText(ad.group_code(c))
    dlg.btn_verify.click()
    assert _pump(qt, lambda: "Rezumat operațiune" in dlg.txt_summary.toPlainText())
    txt = dlg.txt_summary.toPlainText()
    assert "Flux A indisponibil" not in txt and "Flux B" in txt and "Date de încărcat" in txt
    assert not dlg.btn_go.isEnabled() and not dlg.chk_media.isVisible()
    dlg.chk_confirm.setChecked(True)
    assert dlg.btn_go.isEnabled()
    dlg.btn_go.click()
    assert _pump(qt, lambda: "Sincronizarea este activă" in dlg.txt_summary.toPlainText())
    assert bs.LocalRegistry(cb["b"]).sync_state == "BOUND"
    assert c not in dlg.txt_summary.toPlainText()
    dlg.close()
