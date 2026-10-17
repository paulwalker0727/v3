# ruff: noqa: F811
"""Asocierea MAIN (Association + fereastra Qt offscreen) cu aplicația Cloud REALĂ (cimitir_cloud_app.create_app) servită prin HTTP real peste PostgreSQL LOCAL.
Rezultate = LOCAL (nu LIVE/Supabase). Windows/DPAPI: netestat aici (Linux)."""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

import cimitir_bootstrap as bs
import cimitir_cloud_admin as ad
import cimitir_cloud_admin_client as adc
import cimitir_cloud_app as capp
import cimitir_cloud_associate as assoc
import cimitir_cloud_client as client
import cimitir_cloud_control_pg as ccp
import cimitir_field_inbox as fi
import cimitir_field_storage as fs
from test_cloud_admin_pg import EMAIL, PW, TOKEN, Server  # noqa: F401
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_cleanup_race_pg import adm, pgdb  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PostgreSQL local de test indisponibil")


@pytest.fixture()
def ca(renv, pgdb, monkeypatch):
    with adm(pgdb) as c:
        c.execute("TRUNCATE cp_admin_sessions, cp_admin_recovery, cp_admin_credentials, cp_bootstrap_runs, cp_failed_redeems, cp_audit_log, cp_platform_admins, cp_org_roles, "
                  "cp_binding_issuers, device_pairing_codes, device_cemetery_bindings, devices, cemeteries, organizations, identities CASCADE")
        c.execute("UPDATE cp_admin_state SET bootstrap_completed_at=NULL, bootstrap_identity_uid=NULL")
    monkeypatch.setenv("CIMITIR_CP_DATABASE_URL", pgdb["app"])
    monkeypatch.setenv("CIMITIR_CP_ADMIN_DATABASE_URL", pgdb["admin"])
    monkeypatch.setenv("CIMITIR_ALLOW_TEST_SECRETSTORE", "1")
    monkeypatch.setenv(ad.ENV_TOKEN, TOKEN)
    monkeypatch.setenv(ad.ENV_TOKEN_EXPIRES, str(time.time() + 3600))
    renv.cp = ccp.PostgresControlPlane()
    w = World(renv)
    b = w.local(w.org1, name="Craiova TEST")
    renv.cp.create_cemetery(w.owner1, w.org1, b.cemetery_uid, "Craiova TEST")
    svc = fi.FieldInboxService(renv.cp, fs.FieldMediaStore(renv.cp, None, cleanup_url=pgdb["app"]))
    srv = Server(capp.create_app(svc, storage_probe=lambda s: True))
    admin = adc.AdminClient(srv.url)
    r = httpx.post(srv.url + "/api/v1/admin/bootstrap", json={"token": TOKEN, "email": EMAIL, "password": PW, "organization_uid": w.org1, "cemetery_uid": b.cemetery_uid})
    assert r.status_code == 201, r.text
    admin.login(EMAIL, PW)
    yield {"w": w, "b": b, "r": renv, "srv": srv, "admin": admin, "pg": pgdb}
    admin.close()
    srv.stop()


def code(ca, kind="main"):
    return ca["admin"].issue_code(ca["w"].org1, ca["b"].cemetery_uid, kind)["code"]


def make(ca, url=None):
    return assoc.Association(ca["b"], ca["r"].store, ca["r"].inst, url or ca["srv"].url)


def test_url_rules():
    assert assoc.normalize_url("https://x.example.com/") == "https://x.example.com"
    assert assoc.normalize_url("http://127.0.0.1:8083") == "http://127.0.0.1:8083"
    for bad in ("", "ftp://x", "http://cloud.example.com", "https://x.example.com/api", "https://u:p@x.example.com"):
        with pytest.raises(assoc.AssociationError):
            assoc.normalize_url(bad)


def test_full_association_flow_real_app_over_http(ca):
    a, b = make(ca), ca["b"]
    info = a.check_api()
    assert info["database"] == "up" and info["storage"] == "configured" and info["bootstrap_api"] == 1
    with pytest.raises(assoc.AssociationError) as ei:
        a.verify_code("COD-GRESIT-0000")
    assert ei.value.kind == "code"
    c = code(ca)
    v = a.verify_code(c)
    assert v["organization_uid"] == ca["w"].org1 and v["cemetery_status"] == "DRAFT"
    p = a.plan(c, "Craiova TEST")
    assert "Cimitir (UID)" in p["summary"] and bs.LocalRegistry(b).sync_state == "PLANNED"
    with pytest.raises(assoc.AssociationError):
        a.execute(p["plan"], c, confirmed=False)                                   # fără confirmare explicită: nimic nu se întâmplă
    assert bs.LocalRegistry(b).get("cloud_secret_ref") == ""
    res = a.execute(p["plan"], c, confirmed=True)
    assert res["verification"]["sync_enabled"] and set(res["verification"]["checks"].values()) == {"OK"}
    reg = bs.LocalRegistry(b)
    assert reg.sync_state == "BOUND" and reg.get(assoc.SET_UNVERIFIED) == "" and reg.get("cloud_url") == ca["srv"].url
    key = ca["r"].store.get(reg.get("cloud_secret_ref"))
    assert key.startswith("cimitir.") and key.encode() not in Path(b.database).read_bytes()
    assert a.incomplete() is None
    devs = ca["admin"].list_devices(ca["w"].org1)
    assert [(d["kind"], d["state"]) for d in devs] == [("main", "active")]
    with pytest.raises(assoc.AssociationError):                                     # codul consumat nu mai merge (alt registru ar fi respins la fel)
        make(ca).verify_code(c)
    # dezasociere: cheia dispare local și e revocată în cloud
    a.disassociate()
    assert bs.LocalRegistry(b).sync_state == "UNBOUND" and ca["r"].store.get(reg.get("cloud_secret_ref") or "x") is None
    with pytest.raises(Exception):
        ca["r"].cp.authenticate(key)


def test_wrong_server_url_gives_clear_error(ca):
    with pytest.raises(assoc.AssociationError) as ei:
        make(ca, "http://127.0.0.1:9").check_api()                                  # port închis
    assert ei.value.kind == "unreachable"


def _crashing(ca, op):
    """Fabrică de RemoteControlPlane a cărui operație `op` simulează moartea procesului (Crash) după ce serverul a răspuns la pașii dinainte."""
    def factory(base):
        rcp = client.RemoteControlPlane(base, retries=0)
        real = getattr(rcp, op)

        def boom(*a, **k):
            raise bs.Crash("proces întrerupt (test)")
        setattr(rcp, op, boom)
        rcp._real = real
        return rcp
    return factory


def test_interrupted_association_can_be_resumed_without_new_code(ca):
    b = ca["b"]
    c = code(ca)
    a = assoc.Association(b, ca["r"].store, ca["r"].inst, ca["srv"].url, rcp_factory=_crashing(ca, "complete_run"))
    a.verify_code(c) if False else None
    p = a.plan(c, "Craiova TEST")
    with pytest.raises(assoc.AssociationError) as ei:
        a.execute(p["plan"], c, True)
    assert ei.value.kind == "interrupted"
    bs.release_lock(b)                                                              # procesul „a murit”: lock-ul de SO dispare
    reg = bs.LocalRegistry(b)
    assert reg.get(assoc.SET_UNVERIFIED) == "1"                                     # sincronizarea RĂMÂNE blocată
    ok = make(ca)
    info = ok.incomplete()
    assert info and "resume" in info["actions"] and "rollback" in info["actions"]
    res = ok.resume()                                                               # cheia e deja în SecretStore: nu cere cod nou
    assert res["verification"]["sync_enabled"] and reg.sync_state == "BOUND" and reg.get(assoc.SET_UNVERIFIED) == ""


def test_interrupted_association_can_be_aborted_safely_and_redone(ca):
    b = ca["b"]
    c = code(ca)
    a = assoc.Association(b, ca["r"].store, ca["r"].inst, ca["srv"].url, rcp_factory=_crashing(ca, "complete_run"))
    p = a.plan(c, "Craiova TEST")
    with pytest.raises(assoc.AssociationError):
        a.execute(p["plan"], c, True)
    bs.release_lock(b)
    ok = make(ca)
    ref = bs.LocalRegistry(b).get("cloud_secret_ref")
    assert ref and ca["r"].store.get(ref)
    ok.abort()
    reg = bs.LocalRegistry(b)
    assert reg.sync_state == "UNBOUND" and ok.incomplete() is None and reg.get(assoc.SET_UNVERIFIED) == ""
    assert ca["r"].store.get(ref) is None                                           # cheia a fost ștearsă
    assert [d["state"] for d in ca["admin"].list_devices(ca["w"].org1)] == ["revoked"]
    c2 = code(ca)                                                                   # reluare de la zero cu cod nou
    p2 = ok.plan(c2, "Craiova TEST")
    assert ok.execute(p2["plan"], c2, True)["verification"]["sync_enabled"]


def test_verify_failure_keeps_sync_blocked(ca, monkeypatch):
    b = ca["b"]
    c = code(ca)
    a = make(ca)
    p = a.plan(c, "Craiova TEST")
    monkeypatch.setattr(bs, "ping", lambda *x, **k: (_ for _ in ()).throw(bs.BootstrapError("epoch_mismatch")))
    with pytest.raises(assoc.AssociationError) as ei:
        a.execute(p["plan"], c, True)
    assert ei.value.kind == "verify" and bs.LocalRegistry(b).get(assoc.SET_UNVERIFIED) == "1"
    monkeypatch.undo()


# ===================================================================== fereastra Qt (offscreen)
def _pump(qt, cond, timeout=15):
    t = time.time()
    while time.time() - t < timeout:
        qt.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_qt_dialog_end_to_end_offscreen(ca, monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    desk = Path(__file__).resolve().parents[2] / "desktop"
    monkeypatch.syspath_prepend(str(desk))
    monkeypatch.syspath_prepend(str(desk.parent / "backend"))
    from PySide6.QtWidgets import QApplication, QMessageBox
    qt = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    import cloud_associate_window as win
    r = ca["r"]
    dlg = win.CloudAssociateDialog(None, catalog=r.cat, store=r.store, can_manage=lambda: True)
    dlg.cmb_local.setCurrentIndex(dlg.cmb_local.findData(ca["b"].cemetery_uid))
    dlg.ed_url.setText(ca["srv"].url)
    dlg.btn_check.click()
    assert _pump(qt, lambda: "Conexiune verificată" in dlg.txt_summary.toPlainText())
    dlg.ed_code.setText("COD-INVALID")
    dlg.btn_verify.click()
    assert _pump(qt, lambda: "invalid" in dlg.txt_summary.toPlainText().lower())
    assert not dlg.btn_go.isEnabled()
    c = code(ca)
    dlg.ed_code.setText(ad.group_code(c))                                           # cod grupat, cum îl afișează interfața
    dlg.btn_verify.click()
    assert _pump(qt, lambda: "Rezumat operațiune" in dlg.txt_summary.toPlainText())
    assert "Organizație (UID)" in dlg.txt_summary.toPlainText() and not dlg.btn_go.isEnabled()
    dlg.chk_confirm.setChecked(True)
    assert dlg.btn_go.isEnabled()
    dlg.btn_go.click()
    assert _pump(qt, lambda: "Sincronizarea este activă" in dlg.txt_summary.toPlainText(), 40)
    assert bs.LocalRegistry(ca["b"]).sync_state == "BOUND"
    # tab administrator: login, generare cod pentru telefon + MAIN, revocare, dispozitive
    dlg.ed_mail.setText(EMAIL)
    dlg.ed_pass.setText(PW)
    dlg.btn_login.click()
    assert _pump(qt, lambda: dlg.cmb_cem.count() >= 1 and dlg.btn_phone.isEnabled())
    assert dlg.ed_pass.text() == ""                                                 # parola nu rămâne în câmp
    dlg.btn_phone.click()
    assert _pump(qt, lambda: dlg.lbl_code.text() != "—")
    assert len(dlg.lbl_code.text().replace("-", "")) == 20
    dlg.btn_copy.click()
    assert QApplication.clipboard().text() == dlg.lbl_code.text().replace("-", "")
    dlg.btn_revoke_code.click()
    assert _pump(qt, lambda: dlg.lbl_code.text() == "—")
    assert _pump(qt, lambda: dlg.tbl_dev.rowCount() == 1)
    dlg.close()
