# ruff: noqa: F811
"""Livrarea 2/Token automat — inițializarea primului administrator Cloud din MAIN cu grant semnat (fără BOOTSTRAP_TOKEN în container).
LOCAL-PG+HTTP (aplicația de administrare reală); Windows/DPAPI și Docker real netestate."""
from __future__ import annotations

import logging
import os
import time

import pytest

import cimitir_cloud_admin_client as adc
import cimitir_cloud_init_admin as cia
import cimitir_instance_grant as ig
from test_cloud_admin_pg import EMAIL, PW, TOKEN, api, cw  # noqa: F401
from test_etapa4_pas1 import env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_cleanup_race_pg import adm, pgdb  # noqa: F401

INST = "instanta-test-001"

pg_only = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PostgreSQL local de test indisponibil")


# ---------------------------------------------------------------- unit (fără PostgreSQL)
def _env(pub, inst=INST):
    return {ig.ENV_PUBKEY: pub, ig.ENV_INSTANCE_ID: inst}


def test_grant_valid_expired_wrong_audience_wrong_key_tampered():
    kp, other = ig.generate_keypair(), ig.generate_keypair()
    e = _env(kp["public_key"])
    g = ig.issue_grant(kp["private_key"], INST, ttl=300)
    assert ig.verify_grant(g, e)["exp"] > time.time()
    for bad, why in ((ig.issue_grant(kp["private_key"], INST, ttl=300, now=time.time() - 3000), "expirat"),
                     (ig.issue_grant(kp["private_key"], "alta-instanta"), "instanță"),
                     (ig.issue_grant(other["private_key"], INST), "invalid"),
                     (g[:-4] + ("AAAA" if not g.endswith("AAAA") else "BBBB"), "invalid"),
                     ("grant.abc.def", "invalid"), ("grant.", "invalid")):
        with pytest.raises(ig.GrantError) as ei:
            ig.verify_grant(bad, e)
        assert why in str(ei.value) or "invalid" in str(ei.value)
    with pytest.raises(ig.GrantError):
        ig.verify_grant(g, {})                                                   # neconfigurat pe server


def test_grant_ttl_is_capped():
    kp = ig.generate_keypair()
    g = ig.issue_grant(kp["private_key"], INST, ttl=10 ** 6)
    import base64
    import json
    payload = json.loads(base64.urlsafe_b64decode(g.split(".")[1] + "=="))
    assert payload["exp"] - payload["iat"] == ig.MAX_TTL


# ---------------------------------------------------------------- PG + HTTP
@pytest.fixture()
def gw(cw, monkeypatch):
    monkeypatch.delenv("BOOTSTRAP_TOKEN", raising=False)
    monkeypatch.delenv("BOOTSTRAP_TOKEN_EXPIRES_AT", raising=False)
    kp = ig.generate_keypair()
    monkeypatch.setenv(ig.ENV_PUBKEY, kp["public_key"])
    monkeypatch.setenv(ig.ENV_INSTANCE_ID, INST)
    cw["kp"] = kp
    return cw


@pg_only
def test_status_advertises_grant_and_never_leaks_keys(gw):
    st, j = api(gw, "GET", "/status")
    r = j["result"]
    assert st == 200 and r["initialized"] is False and r["bootstrap_open"] is True and r["bootstrap_methods"] == ["signed_grant"] and r["instance_id"] == INST
    assert gw["kp"]["public_key"] not in str(j) and r["owner_key_fingerprint"] == ig.fingerprint(gw["kp"]["public_key"])


@pg_only
def test_main_initializes_admin_end_to_end_no_manual_token_no_docker(gw, caplog):
    store = gw["renv"].store
    ia = cia.InitAdmin(gw["srv"].url, store)
    s0 = ia.status()
    assert s0["grant_supported"] and not s0["owner_key_present"] and not s0["initialized"]
    with pytest.raises(cia.InitError) as ei:
        ia.initialize(EMAIL, PW, True)                                           # fără cheie de proprietar: nimic nu pleacă spre server
    assert ei.value.kind == "no_key" and api(gw, "GET", "/status")[1]["result"]["initialized"] is False
    with pytest.raises(cia.InitError) as ei:
        ia.initialize(EMAIL, PW, False)
    assert ei.value.kind == "confirm"
    # cheie greșită (alt proprietar) => refuz local, serverul neatins
    wrong = ia.create_owner_key(INST)
    assert wrong["fingerprint"] != ig.fingerprint(gw["kp"]["public_key"])
    with pytest.raises(cia.InitError) as ei:
        ia.initialize(EMAIL, PW, True)
    assert ei.value.kind == "key_mismatch" and api(gw, "GET", "/status")[1]["result"]["initialized"] is False
    with pytest.raises(cia.InitError) as ei:
        ia.create_owner_key(INST)                                                # nu se suprascrie cheia existentă
    assert ei.value.kind == "exists"
    # cheia corectă (importată în SecretStore) => inițializare automată
    store.set(ig.secret_name(INST), gw["kp"]["private_key"])
    assert ia.status()["owner_key_matches_server"]
    caplog.set_level(logging.DEBUG)
    res = ia.initialize(EMAIL, PW, True, gw["w"].org1, "Org 1", gw["b"].cemetery_uid, "Craiova TEST")
    assert res["initialized"] and res["email"] == EMAIL and res["organization_uid"] == gw["w"].org1
    assert PW not in repr(res) and gw["kp"]["private_key"] not in repr(res) and PW not in caplog.text and gw["kp"]["private_key"] not in caplog.text
    cl = adc.AdminClient(gw["srv"].url)
    assert cl.login(EMAIL, PW)["email"] == EMAIL                                 # administratorul funcționează
    cl.close()
    # a doua inițializare și reutilizarea oricărui grant sunt respinse (instalarea are deja administrator)
    with pytest.raises(cia.InitError) as ei:
        ia.initialize("alt@example.com", PW + "x", True)
    assert ei.value.kind == "initialized"
    g2 = ig.issue_grant(gw["kp"]["private_key"], INST, ttl=60)
    st, j = api(gw, "POST", "/bootstrap", json={"token": g2, "email": "alt@example.com", "password": "Parola-Lunga-123!"})
    assert st == 409 and j["code"] == "BOOTSTRAP_CLOSED"
    with adm(gw["pg"]) as c:
        assert c.execute("SELECT count(*) FROM cp_admin_credentials").fetchone()[0] == 1


@pg_only
def test_forged_expired_and_foreign_grants_are_forbidden_and_leave_instance_open(gw):
    other = ig.generate_keypair()
    cases = [ig.issue_grant(other["private_key"], INST),                                           # semnat de altcineva
             ig.issue_grant(gw["kp"]["private_key"], "alta-instanta"),                             # altă instanță
             ig.issue_grant(gw["kp"]["private_key"], INST, ttl=60, now=time.time() - 4000),        # expirat
             "grant.x.y", "token-oarecare-" + "z" * 40]
    for g in cases:
        st, j = api(gw, "POST", "/bootstrap", json={"token": g, "email": EMAIL, "password": PW})
        assert st == 403, (g[:12], st, j)
    assert api(gw, "GET", "/status")[1]["result"]["initialized"] is False


@pg_only
def test_license_alone_cannot_create_admin_and_env_token_still_works_if_configured(gw, monkeypatch):
    """Nimic din licență nu intră în fluxul de grant; tokenul clasic din mediu rămâne o cale separată, neschimbată."""
    monkeypatch.setenv("BOOTSTRAP_TOKEN", TOKEN)
    monkeypatch.setenv("BOOTSTRAP_TOKEN_EXPIRES_AT", str(time.time() + 3600))
    st, j = api(gw, "GET", "/status")
    assert j["result"]["bootstrap_methods"] == ["signed_grant", "env_token"]
    st, j = api(gw, "POST", "/bootstrap", json={"token": TOKEN, "email": EMAIL, "password": PW, "organization_uid": gw["w"].org1, "cemetery_uid": gw["b"].cemetery_uid})
    assert st == 201
    assert api(gw, "GET", "/status")[1]["result"].get("legacy_token_configured") is True      # avertizare: tokenul e încă în mediu (inofensiv, dar de eliminat)


@pg_only
def test_qt_init_admin_tab_end_to_end(gw, monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from pathlib import Path
    desk = Path(__file__).resolve().parents[2] / "desktop"
    monkeypatch.syspath_prepend(str(desk))
    from PySide6.QtWidgets import QApplication
    qt = QApplication.instance() or QApplication([])
    import cloud_init_admin_tab as tabm
    store = gw["renv"].store
    tab = tabm.InitAdminTab(store, lambda: gw["srv"].url, lambda: True)

    def pump(cond, timeout=30):
        t = time.time()
        while time.time() - t < timeout:
            qt.processEvents()
            if cond():
                return True
            time.sleep(0.02)
        return False
    tab.btn_key.click()
    import field_worker
    assert pump(lambda: "Cheie creată" in tab.txt.toPlainText())
    store.set(ig.secret_name(INST), gw["kp"]["private_key"])                                   # cheia corectă (operatorul a pus cheia publică la provisionare)
    tab.btn_status.click()
    assert pump(lambda: "corespunde serverului" in tab.txt.toPlainText())
    tab.ed_org.setText("Parohia TEST Craiova")
    tab.ed_email.setText(EMAIL)
    tab.ed_pw.setText(PW)
    assert not tab.btn_go.isEnabled()
    tab.chk.setChecked(True)
    tab.btn_go.click()
    pump(lambda: "a fost inițializat" in tab.txt.toPlainText(), 8)
    assert "a fost inițializat" in tab.txt.toPlainText(), tab.txt.toPlainText()
    assert tab.ed_pw.text() == "" and PW not in tab.txt.toPlainText()
    assert api(gw, "GET", "/status")[1]["result"]["initialized"] is True
