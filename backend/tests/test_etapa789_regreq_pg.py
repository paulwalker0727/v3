# ruff: noqa: F811
"""Cereri de înregistrare MAIN → /admin/ (confirmare) → cod MAIN automat → asociere; + instrumentul de inventariere/arhivare a datelor TEST. PostgreSQL LOCAL real; nu e LIVE."""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import httpx
import pytest

import cimitir_bootstrap as bs
import cimitir_cloud_admin_client as adc
import cimitir_cloud_associate as assoc
from test_cloud_associate_pg import ca, make  # noqa: F401
from test_etapa789_provision_pg import _new_local, _second_admin  # noqa: F401
from test_cloud_admin_pg import EMAIL, PW  # noqa: F401
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_cleanup_race_pg import adm, pgdb  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PostgreSQL local de test indisponibil")
ROOT = Path(__file__).resolve().parents[2]


def _assoc(ca, b):
    return assoc.Association(b, ca["r"].store, ca["r"].inst, ca["srv"].url)


def test_main_request_admin_confirms_main_completes_automatically(ca):
    b2, org = _new_local(ca)
    a = _assoc(ca, b2)
    with pytest.raises(assoc.AssociationError) as ei:
        a.request_registration("ParohiaTest", "test", confirmed=False)
    assert ei.value.kind == "confirm" and not [q for q in ca["admin"].list_requests() if q["cemetery_uid"] == b2.cemetery_uid]
    r = a.request_registration("ParohiaTest", "test", True)
    assert len(r["short_id"]) == 8 and a.pending_request() == r["request_id"]
    assert a.registry_in_cloud(ca["admin"])["action"] == "provision_all"                       # cererea NU a creat nimic
    assert a.complete_registration("test")["state"] == "pending"
    reqs = ca["admin"].list_requests()
    assert [(q["organization_uid"], q["cemetery_uid"], q["organization_exists"], q["cemetery_exists"]) for q in reqs if q["cemetery_uid"] == b2.cemetery_uid] == [(org, b2.cemetery_uid, False, False)]
    assert a.complete_registration("test")["state"] == "pending"
    res = ca["admin"].decide_request(r["request_id"], True)
    assert res["organization_created"] and res["cemetery_created"] and res["organization_uid"] == org
    out = a.complete_registration("test")                                                      # MAIN continuă singur: cod → plan → asociere → verificare
    assert out["state"] == "associated" and out["verification"]["sync_enabled"]
    assert bs.LocalRegistry(b2).sync_state == "BOUND" and a.pending_request() is None
    assert all(q["cemetery_uid"] != b2.cemetery_uid for q in ca["admin"].list_requests())


def test_secret_and_code_not_stored_and_code_issued_once(ca):
    b2, org = _new_local(ca)
    a = _assoc(ca, b2)
    r = a.request_registration("ParohiaTest", "test", True)
    secret = ca["r"].store.get(f"regreq/{r['request_id']}")
    ca["admin"].decide_request(r["request_id"], True)
    with adm(ca["pg"]) as c:
        row = c.execute("SELECT secret_hash, state FROM cp_registry_requests WHERE request_id=%s", (r["request_id"],)).fetchone()
        assert row[0] != secret and len(row[0]) == 64 and row[1] == "approved"
        cols = [x[0] for x in c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='cp_registry_requests'").fetchall()]
        assert not any("code" in x for x in cols)
    cl = adc.AdminClient(ca["srv"].url)
    first = cl.registration_status(r["request_id"], secret)
    assert first["state"] == "approved" and len(first["code"]) == 20
    assert cl.registration_status(r["request_id"], secret)["state"] == "consumed" and "code" not in cl.registration_status(r["request_id"], secret)
    with pytest.raises(adc.AdminApiError) as ei:
        cl.registration_status(r["request_id"], "x" * 40)                                      # secret greșit
    assert ei.value.status == 404
    cl.close()


def test_duplicates_conflicts_and_limits(ca):
    b2, org = _new_local(ca)
    cl = adc.AdminClient(ca["srv"].url)
    s = "s" * 40
    cl.request_registration(s, org, "P", b2.cemetery_uid, "c")
    with pytest.raises(adc.AdminApiError) as e1:
        cl.request_registration("t" * 40, org, "P", b2.cemetery_uid, "c")                      # același cimitir, cerere deja în așteptare
    assert e1.value.status == 409
    with pytest.raises(adc.AdminApiError) as e2:
        cl.request_registration("u" * 40, str(uuid.uuid4()), "Altă", ca["b"].cemetery_uid, "furat")   # UID de cimitir existent în altă organizație
    assert e2.value.status == 400
    with pytest.raises(adc.AdminApiError) as e3:
        cl.request_registration("v" * 40, str(uuid.uuid4()), "", str(uuid.uuid4()), "x")       # organizație nouă fără nume
    assert e3.value.status == 400
    with pytest.raises(adc.AdminApiError) as e4:
        cl.request_registration("scurt", org, "P", str(uuid.uuid4()), "x")
    assert e4.value.status == 400
    with pytest.raises(adc.AdminApiError) as e5:
        cl.list_requests()                                                                      # lista cere sesiune de administrator
    assert e5.value.status == 401
    ca["admin"].decide_request(ca["admin"].list_requests()[0]["request_id"], False)           # respingere eliberează cimitirul
    cl.request_registration("w" * 40, org, "P", b2.cemetery_uid, "c")
    cl.close()


def test_visibility_follows_ownership_and_rls(ca):
    w = ca["w"]
    owner2 = _second_admin(ca, "owner", w.org1)                                                # Owner în org1, nu administratorul instalării
    adm_only = _second_admin(ca, "admin", w.org1)                                              # doar ADMIN în org1
    cl = adc.AdminClient(ca["srv"].url)
    new_org, new_cem = str(uuid.uuid4()), str(uuid.uuid4())
    cl.request_registration("a" * 40, new_org, "Nouă", new_cem, "nou")                         # organizație nouă
    cem_in_org1 = str(uuid.uuid4())
    cl.request_registration("b" * 40, w.org1, "Org 1", cem_in_org1, "cimitir nou în org1")     # organizație existentă
    mine = {new_cem, cem_in_org1}
    assert {q["cemetery_uid"] for q in ca["admin"].list_requests()} >= mine                    # administratorul instalării: ambele
    assert {q["cemetery_uid"] for q in owner2.list_requests()} & mine == {cem_in_org1}         # Owner: doar cererea din organizația lui
    assert new_org not in {q["organization_uid"] for q in owner2.list_requests()}
    assert adm_only.list_requests() == []                                                       # simplu ADMIN: nimic
    rid_new = [q["request_id"] for q in ca["admin"].list_requests() if q["cemetery_uid"] == new_cem][0]
    with pytest.raises(adc.AdminApiError) as ei:
        owner2.decide_request(rid_new, True)                                                    # nu poate crea organizații noi
    assert ei.value.status in (403, 404)
    with pytest.raises(adc.AdminApiError) as e2:
        adm_only.decide_request([q for q in ca["admin"].list_requests() if q["cemetery_uid"] == cem_in_org1][0]["request_id"], True)
    assert e2.value.status in (403, 404)
    assert owner2.decide_request([q for q in owner2.list_requests() if q["cemetery_uid"] == cem_in_org1][0]["request_id"], True)["cemetery_created"]
    me = ca["admin"].me()
    assert [c["uid"] for o in me["organizations"] if o["organization_uid"] == w.org1 for c in o["cemeteries"]].count(cem_in_org1) == 1
    with pytest.raises(adc.AdminApiError):
        ca["admin"].decide_request(str(uuid.uuid4()), True)                                     # cerere inexistentă
    with pytest.raises(adc.AdminApiError) as eb:
        ca["admin"]._req("POST", "/registry-requests/decide", {"request_id": rid_new, "approve": "false"})   # „approve” trebuie să fie boolean real
    assert eb.value.status == 400
    owner2.close(); adm_only.close(); cl.close()


def test_rejected_request_tells_main_and_clears_state(ca):
    b2, org = _new_local(ca)
    a = _assoc(ca, b2)
    r = a.request_registration("P", "c", True)
    ca["admin"].decide_request(r["request_id"], False)
    with pytest.raises(assoc.AssociationError) as ei:
        a.complete_registration("c")
    assert ei.value.kind == "request_rejected" and a.pending_request() is None
    assert a.registry_in_cloud(ca["admin"])["action"] == "provision_all"                      # nimic creat


def test_qt_request_button_flow_offscreen(ca, monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    desk = ROOT / "desktop"
    monkeypatch.syspath_prepend(str(desk))
    monkeypatch.syspath_prepend(str(ROOT / "backend"))
    from PySide6.QtWidgets import QApplication
    from test_cloud_associate_pg import _pump
    qt = QApplication.instance() or QApplication([])
    import cloud_associate_window as win
    b2, org = _new_local(ca)
    dlg = win.CloudAssociateDialog(None, catalog=ca["r"].cat, store=ca["r"].store, can_manage=lambda: True)
    dlg.cmb_local.setCurrentIndex(dlg.cmb_local.findData(b2.cemetery_uid))
    dlg.ed_url.setText(ca["srv"].url)
    dlg.btn_req.click()
    assert "confirmarea" in dlg.txt_summary.toPlainText() and not [q for q in ca["admin"].list_requests() if q["cemetery_uid"] == b2.cemetery_uid]   # fără bifă nu se trimite
    dlg.chk_req.setChecked(True)
    dlg.btn_req.click()
    assert _pump(qt, lambda: "Cerere trimisă" in dlg.txt_summary.toPlainText())
    assert dlg.btn_req_cancel.isEnabled() and not dlg.btn_req.isEnabled()
    rid = [q for q in ca["admin"].list_requests() if q["cemetery_uid"] == b2.cemetery_uid][0]["request_id"]
    dlg._req_poll()
    _pump(qt, lambda: False, 1)
    ca["admin"].decide_request(rid, True)
    dlg._req_busy = False
    dlg._req_poll()
    assert _pump(qt, lambda: "Sincronizarea este activă" in dlg.txt_summary.toPlainText(), 60)
    assert bs.LocalRegistry(b2).sync_state == "BOUND" and dlg.btn_req.isEnabled()
    dlg.close()


def _inv(args, env_extra=None, pg=None):
    e = dict(os.environ, CIMITIR_CP_ADMIN_DATABASE_URL=pg["admin"])
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "test_data_inventory.py"), *args], capture_output=True, text=True, env=e, timeout=60)
    return r.returncode, r.stdout + r.stderr


def test_test_data_tool_preview_confirm_and_protection(ca):
    pg = ca["pg"]
    b2, org = _new_local(ca)
    ca["admin"].provision(org, "LIVE-PROVISION-20260101-000000", b2.cemetery_uid, "test")
    real_cem = ca["b"].cemetery_uid
    rc, out = _inv(["inventory"], pg=pg)
    assert rc == 0 and f"[FICTIV ] LIVE-PROVISION-20260101-000000 / test" in out and f"[PROTEJAT] " in out and real_cem in out
    rc, out = _inv(["archive", "--cemetery", real_cem], pg=pg)                                   # registru real: refuzat chiar cu UID explicit
    assert rc == 2 and "REFUZAT" in out
    rc, out = _inv(["archive", "--cemetery", b2.cemetery_uid], pg=pg)                            # previzualizare: nu scrie
    assert rc == 0 and "PREVIZUALIZARE" in out and "ARCHIVED" in out and "--confirm ARHIVEAZA-" in out
    with adm(pg) as c:
        assert c.execute("SELECT status FROM cemeteries WHERE uid=%s", (b2.cemetery_uid,)).fetchone()[0] == "DRAFT"
    tok = out.split("--confirm ")[1].split()[0]
    rc, out = _inv(["archive", "--cemetery", b2.cemetery_uid, "--apply", "--confirm", "ARHIVEAZA-gresit"], pg=pg)
    assert rc == 2
    with adm(pg) as c:
        assert c.execute("SELECT status FROM cemeteries WHERE uid=%s", (b2.cemetery_uid,)).fetchone()[0] == "DRAFT"
    rc, out = _inv(["archive", "--cemetery", b2.cemetery_uid, "--apply", "--confirm", tok], pg=pg)
    assert rc == 0 and "arhivate" in out
    with adm(pg) as c:
        assert c.execute("SELECT status FROM cemeteries WHERE uid=%s", (b2.cemetery_uid,)).fetchone()[0] == "ARCHIVED"
        assert c.execute("SELECT status FROM cemeteries WHERE uid=%s", (real_cem,)).fetchone()[0] != "ARCHIVED"
        assert c.execute("SELECT count(*) FROM organizations").fetchone()[0] >= 2                # nimic șters
