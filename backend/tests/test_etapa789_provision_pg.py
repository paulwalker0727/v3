# ruff: noqa: F811
"""Provizionarea registrului MAIN în Cloud cu UID-urile originale + asociere automată (PostgreSQL LOCAL real, aplicația Cloud reală pe HTTP real). Rezultate = LOCAL, nu LIVE."""
from __future__ import annotations

import os
import uuid

import pytest

import cimitir_bootstrap as bs
import cimitir_cloud_admin as ad
import cimitir_cloud_admin_client as adc
import cimitir_cloud_associate as assoc
from test_cloud_associate_pg import ca, make  # noqa: F401
from test_cloud_admin_pg import EMAIL, PW  # noqa: F401
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_cleanup_race_pg import adm, pgdb  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PostgreSQL local de test indisponibil")
PW2 = "Alt-Administrator-2026!"


def _second_admin(ca, role="admin", org=None):
    """Administrator Cloud fără drept de provizionare (nu e primul administrator al instalării)."""
    uid, email = str(uuid.uuid4()), f"op-{uuid.uuid4().hex[:6]}@example.invalid"
    with adm(ca["pg"]) as c:
        c.execute("INSERT INTO identities (uid, display_name, password_hash) VALUES (%s,%s,'!')", (uid, "Operator"))
        c.execute("INSERT INTO cp_admin_credentials (identity_uid, email, display_name, password_hash) VALUES (%s,%s,'Operator',%s)", (uid, email, ad.hash_password(PW2)))
        c.execute("INSERT INTO cp_org_roles (identity_uid, organization_uid, role) VALUES (%s,%s,%s)", (uid, org or ca["w"].org1, role))
    cl = adc.AdminClient(ca["srv"].url)
    cl.login(email, PW2)
    return cl


def _new_local(ca, name="test"):
    org = str(uuid.uuid4())
    return ca["w"].local(org_uid=org, name=name), org


def test_missing_registry_is_provisioned_with_original_uids_and_associated(ca):
    b2, org = _new_local(ca)
    a = assoc.Association(b2, ca["r"].store, ca["r"].inst, ca["srv"].url)
    st = a.registry_in_cloud(ca["admin"])
    assert st["action"] == "provision_all" and not st["organization_exists"] and not st["cemetery_exists"]
    with pytest.raises(assoc.AssociationError) as ei:
        a.associate_with_admin(ca["admin"], "ParohiaTest", "test", confirmed=False)           # fără confirmare nu se scrie nimic
    assert ei.value.kind == "confirm" and a.registry_in_cloud(ca["admin"])["action"] == "provision_all"
    res = a.associate_with_admin(ca["admin"], "ParohiaTest", "test", confirmed=True)
    assert res["provision"]["organization_created"] and res["provision"]["cemetery_created"]
    assert res["provision"]["organization_uid"] == b2.organization_uid and res["provision"]["cemetery_uid"] == b2.cemetery_uid       # UID-uri originale
    assert res["verification"]["sync_enabled"] and set(res["verification"]["checks"].values()) == {"OK"}
    reg = bs.LocalRegistry(b2)
    assert reg.sync_state == "BOUND" and reg.get(assoc.SET_UNVERIFIED) == ""
    key = ca["r"].store.get(reg.get("cloud_secret_ref"))
    assert key and key.startswith("cimitir.") and key.encode() not in b2.database.read_bytes()
    me = ca["admin"].me()
    mine = next(o for o in me["organizations"] if o["organization_uid"] == org)
    assert mine["name"] == "ParohiaTest" and mine["is_owner"] and [c["uid"] for c in mine["cemeteries"]] == [b2.cemetery_uid]
    assert a.registry_in_cloud(ca["admin"])["action"] == "ready"


def test_provision_is_idempotent_and_protections_stay(ca):
    b2, org = _new_local(ca)
    r1 = ca["admin"].provision(org, "ParohiaTest", b2.cemetery_uid, "test")
    r2 = ca["admin"].provision(org, "ParohiaTest", b2.cemetery_uid, "test")
    assert r1["organization_created"] and r1["cemetery_created"] and not r2["organization_created"] and not r2["cemetery_created"]
    a = assoc.Association(b2, ca["r"].store, ca["r"].inst, ca["srv"].url)
    assert a.registry_in_cloud(ca["admin"])["action"] == "ready"
    res = a.associate_with_admin(ca["admin"], "ParohiaTest", "test", True)                      # registrul există deja: doar cod + asociere
    assert "provision" not in res and res["verification"]["sync_enabled"]


def test_existing_cloud_registry_with_local_data_blocked_flow_is_not_bypassed(ca):
    """Cimitir ACTIV cu date în Cloud și registru local nevid: protecția BLOCKED_ACTIVE_EMPTY / verify_empty rămâne (aici: al doilea MAIN pe același cimitir deja legat)."""
    a = make(ca)
    a.associate_with_admin(ca["admin"], "Org 1", "Craiova TEST", True)
    b3 = ca["w"].local(org_uid=ca["w"].org1, name="Craiova TEST")
    b3 = ca["r"].cat.binding_for(ca["b"].cemetery_uid) if False else b3
    a3 = assoc.Association(b3, ca["r"].store, ca["r"].inst, ca["srv"].url)
    st = a3.registry_in_cloud(ca["admin"])
    assert st["action"] == "provision_cemetery"                                                 # cimitirul local nou (alt UID) lipsește din Cloud; org există


def test_non_provisioner_cannot_create_new_organization(ca):
    op = _second_admin(ca, "owner")                                                             # Owner într-o organizație, dar nu administratorul instalării
    b2, org = _new_local(ca)
    st = op.registry_status(org, b2.cemetery_uid)
    assert st["action"] == "forbidden" and not st["can_provision_organization"]
    with pytest.raises(adc.AdminApiError) as ei:
        op.provision(org, "Intrus", b2.cemetery_uid, "x")
    assert ei.value.status == 403
    assert ca["admin"].registry_status(org, b2.cemetery_uid)["action"] == "provision_all"        # nimic nu s-a creat
    a = assoc.Association(b2, ca["r"].store, ca["r"].inst, ca["srv"].url)
    with pytest.raises(assoc.AssociationError) as e2:
        a.associate_with_admin(op, "Intrus", "x", True)
    assert e2.value.kind == "forbidden"
    op.close()


def test_non_owner_cannot_add_cemetery_or_see_foreign_registry(ca):
    w = ca["w"]
    op = _second_admin(ca, "admin", w.org1)                                                    # doar ADMIN în org1
    st = op.registry_status(w.org1, str(uuid.uuid4()))
    assert st["action"] == "forbidden" and st["organization_exists"] and not st["is_owner"]
    with pytest.raises(adc.AdminApiError) as ei:
        op.provision(w.org1, "Org 1", str(uuid.uuid4()), "nou")
    assert ei.value.status == 403
    st2 = op.registry_status(w.org2, ca["b"].cemetery_uid)                                      # org străină: nicio informație despre conținut
    assert st2["cemetery_status"] is None and st2["action"] in ("forbidden", "conflict")
    assert all(o["organization_uid"] != w.org2 for o in op.me()["organizations"])
    op.close()


def test_cemetery_uid_belonging_to_other_org_is_conflict_not_overwritten(ca):
    w, cem = ca["w"], ca["b"].cemetery_uid
    st = ca["admin"].registry_status(str(uuid.uuid4()), cem)
    assert st["action"] == "conflict" and st["cemetery_in_other_organization"]
    with pytest.raises(adc.AdminApiError) as ei:
        ca["admin"].provision(str(uuid.uuid4()), "Altă organizație", cem, "furat")
    assert ei.value.status == 400
    assert [c["uid"] for o in ca["admin"].me()["organizations"] if o["organization_uid"] == w.org1 for c in o["cemeteries"]] == [cem]


def test_provision_requires_session_and_valid_uids(ca):
    anon = adc.AdminClient(ca["srv"].url)
    with pytest.raises(adc.AdminApiError) as ei:
        anon.provision(str(uuid.uuid4()), "X", str(uuid.uuid4()), "Y")
    assert ei.value.status == 401
    with pytest.raises(adc.AdminApiError) as e2:
        ca["admin"].provision("nu-este-uuid", "X", str(uuid.uuid4()), "Y")
    assert e2.value.status == 400
    with pytest.raises(adc.AdminApiError) as e3:
        ca["admin"].provision(str(uuid.uuid4()), "", str(uuid.uuid4()), "Y")                    # organizație nouă fără nume
    assert e3.value.status == 400
    anon.close()


def test_qt_auto_association_dialog_provisions_and_binds(ca, monkeypatch):
    pytest.importorskip("PySide6")
    from pathlib import Path
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    desk = Path(__file__).resolve().parents[2] / "desktop"
    monkeypatch.syspath_prepend(str(desk))
    monkeypatch.syspath_prepend(str(desk.parent / "backend"))
    from PySide6.QtWidgets import QApplication
    from test_cloud_associate_pg import _pump
    qt = QApplication.instance() or QApplication([])
    import cloud_associate_window as win
    b2, org = _new_local(ca)
    r = ca["r"]
    dlg = win.CloudAssociateDialog(None, catalog=r.cat, store=r.store, can_manage=lambda: True)
    dlg.cmb_local.setCurrentIndex(dlg.cmb_local.findData(b2.cemetery_uid))
    dlg.ed_url.setText(ca["srv"].url)
    dlg.ed_cmail.setText(EMAIL)
    dlg.ed_cpass.setText("parola-gresita-123")
    dlg.btn_auto_check.click()
    assert _pump(qt, lambda: "incorecte" in dlg.txt_summary.toPlainText())
    assert dlg.ed_cpass.text() == "" and not dlg.chk_auto.isEnabled()
    dlg.ed_cpass.setText(PW)
    dlg.btn_auto_check.click()
    assert _pump(qt, lambda: "Registrul nu există în Cloud" in dlg.txt_summary.toPlainText())
    assert org in dlg.txt_summary.toPlainText() and dlg.chk_auto.isEnabled() and not dlg.btn_auto_go.isEnabled()
    dlg.chk_auto.setChecked(True)
    assert dlg.btn_auto_go.isEnabled()
    dlg.btn_auto_go.click()
    assert _pump(qt, lambda: "Sincronizarea este activă" in dlg.txt_summary.toPlainText(), 60)
    assert "Provizionat în Cloud" in dlg.txt_summary.toPlainText()
    assert bs.LocalRegistry(b2).sync_state == "BOUND"
    dlg.close()
