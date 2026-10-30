# ruff: noqa: F811
"""Mobile v2: pairing din MAIN + permisiuni pe categorii, de la cod până la modificarea ajunsă în MAIN (PostgreSQL local real cu RLS, HTTP real, MAIN real = proces
separat cu SQLite + PySide6 offscreen). Rulează doar cu CIMITIR_TEST_PG_ADMIN_URL. NU atinge Supabase/PROD; date fictive."""
from __future__ import annotations

import os
import time
import uuid

import httpx
import pytest
import requests

import cimitir_cloud_admin as ad
import cimitir_cloud_admin_client as adc
import cimitir_cloud_app as capp
import cimitir_phone_pairing as pp
from test_cloud_admin_pg import EMAIL, PW, TOKEN, Server as AdminServer
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_cleanup_race_pg import adm, pgdb  # noqa: F401
from test_etapa6b_field_storage import sup  # noqa: F401
from test_etapa6c_field_inbox_pg import fw  # noqa: F401
from test_sync_v2_main_e2e import ADD, main_run, pg

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytestmark = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PostgreSQL local de test indisponibil")
API = "/api/v1/sync-v2"
PAIR = "/api/v1/phone-pairing"

UI_PREAMBLE = """
import time
from PySide6.QtWidgets import QApplication
qapp = QApplication.instance() or QApplication([])
def pump(cond, timeout=20):
    t = time.time()
    while time.time() - t < timeout:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.03)
    return False
import mobile_pairing_panel as mpanel
import mobile_permissions_panel as ppanel
from PySide6.QtWidgets import QMessageBox
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
def click_and_settle(panel, btn):
    btn.click()
    return pump(lambda: panel.btn_issue.isEnabled())
"""


def H(key):
    return {"Authorization": f"Binding {key}"}


@pytest.fixture()
def rw(fw, monkeypatch):
    with adm(fw["pg"]) as c:
        c.execute("TRUNCATE sync_v2_ops, sync_v2_conflicts, sync_v2_permissions, sync_v2_runs, sync_v2_audit, sync_v2_issued, sync_v2_counters, sync_v2_map_background, "
                  "sync_tombstones, inhumati, plati, contracte, concesionari, amenajari_avize, locuri_de_veci, cp_admin_sessions, cp_admin_recovery, cp_admin_credentials CASCADE")
        c.execute("UPDATE cp_admin_state SET bootstrap_completed_at=NULL, bootstrap_identity_uid=NULL")
    monkeypatch.setenv(ad.ENV_TOKEN, TOKEN)
    monkeypatch.setenv(ad.ENV_TOKEN_EXPIRES, str(time.time() + 3600))
    svc = fw["svc"]
    srv = AdminServer(capp.create_app(service=svc, origins=[]))            # aplicația reală: toate rutele + PWA /mobile/
    srv2 = AdminServer(capp.create_app(service=svc, origins=[]))           # al doilea backend peste ACEEAȘI bază (ca 8086 + 8087)
    A = fw["T"]["A"]
    http = httpx.Client(base_url=srv.url, timeout=15)
    r = http.post("/api/v1/admin/bootstrap", json={"token": TOKEN, "email": EMAIL, "password": PW, "display_name": "Paroh", "organization_uid": A["org"], "cemetery_uid": A["cem"]})
    assert r.status_code in (201, 409), r.text
    w = {**fw, "srv": srv, "srv2": srv2, "url": srv.url, "url2": srv2.url, "http": http, "data": f"/tmp/mpp-{uuid.uuid4().hex[:6]}"}
    yield w
    http.close()
    srv.stop()
    srv2.stop()


def redeem(w, code, name="Telefon test", url=None, cid="client-abcdef12"):
    r = httpx.post((url or w["url"]) + PAIR + "/redeem", json={"code": code, "device_name": name, "client_id": cid}, timeout=15)
    return r


def status(w, key):
    return httpx.get(w["url"] + API + "/status", headers=H(key), timeout=15).json()["result"]


def push(w, key, ops):
    return httpx.post(w["url"] + API + "/push", json={"ops": ops}, headers=H(key), timeout=15).json()["result"]["results"]


def main_issue_code(w):
    """MAIN (proces separat, PySide6) apasă „Generează cod pentru telefon” în panoul real."""
    return main_run(w, UI_PREAMBLE + """
pn = mpanel.MobilePairingPanel()
out["ok"] = click_and_settle(pn, pn.btn_issue)
out["code"] = pn.lbl_code.text()
out["status"] = pn.lbl_status.text()
out["left"] = pn.lbl_left.text()
out["qr"] = pn.lbl_qr.isVisible() or pn.lbl_qr.pixmap() is not None
out["code_id"] = pn._issued["code_id"] if pn._issued else None
""")


def test_01_main_generates_code_and_phone_pairs_with_limited_rights(rw):
    w = rw
    out = main_issue_code(w)
    assert out["ok"] and len(out["code"].replace("-", "")) == 20 and "Cod generat" in out["status"] and out["left"].startswith("Valabil"), out
    r = redeem(w, out["code"])
    assert r.status_code == 201, r.text
    key, bu = r.json()["result"]["binding_key"], r.json()["result"]["binding_uid"]
    st = status(w, key)
    assert st["device_type"] == "MOBILE" and st["editable"] == [] and st["permissions"]["mode"] == "legacy"
    assert not any(st["permissions"]["matrix"][c][a] for c in st["permissions"]["matrix"] for a in ("add", "edit", "delete"))      # „Doar citire” implicit
    devs = httpx.get(w["url"] + API + "/devices", headers=H(w["T"]["A"]["main"]), timeout=15).json()["result"]["devices"]
    assert any(d["ref"] == bu and d["name"] == "Telefon test" and d["created_at"] for d in devs)


def test_02_code_single_use_expired_revoked_and_wrong_tenant(rw):
    w = rw
    A = w["T"]["A"]
    # refolosit
    c1 = httpx.post(w["url"] + PAIR + "/codes", json={}, headers=H(A["main"]), timeout=15).json()["result"]
    assert redeem(w, c1["code"]).status_code == 201
    assert redeem(w, c1["code"], cid="client-second-1").status_code == 401
    # revocat din MAIN (panoul real): codul nu mai poate fi folosit
    out = main_run(w, UI_PREAMBLE + """
pn = mpanel.MobilePairingPanel()
click_and_settle(pn, pn.btn_issue)
out["code"] = pn.lbl_code.text()
click_and_settle(pn, pn.btn_revoke)
out["after"] = pn.lbl_code.text(); out["status"] = pn.lbl_status.text(); out["left"] = pn.lbl_left.text()
""")
    assert out["after"] == "—" and "revocat" in out["status"].lower()
    assert redeem(w, out["code"], cid="client-third-1").status_code == 401
    # expirat
    c3 = httpx.post(w["url"] + PAIR + "/codes", json={}, headers=H(A["main"]), timeout=15).json()["result"]
    with adm(w["pg"]) as c:
        c.execute("ALTER TABLE device_pairing_codes DISABLE TRIGGER USER")
        c.execute("UPDATE device_pairing_codes SET expires_at = now() - interval '1 minute' WHERE code_hash=%s", (pp._h(c3["code"].replace("-", "")),))
        c.execute("ALTER TABLE device_pairing_codes ENABLE TRIGGER USER")
    assert redeem(w, c3["code"], cid="client-fourth-1").status_code == 401
    listed = httpx.get(w["url"] + PAIR + "/codes", headers=H(A["main"]), timeout=15).json()["result"]
    assert {x["state"] for x in listed} >= {"used", "revoked", "expired"}
    # codul generat pentru ALTĂ organizație/cimitir nu dă acces la datele parohiei A; telefonul B vede doar B
    cb = httpx.post(w["url"] + PAIR + "/codes", json={}, headers=H(w["T"]["B"]["main"]), timeout=15).json()["result"]
    kb = redeem(w, cb["code"], cid="client-fifth-1").json()["result"]
    assert kb["cemetery_uid"] == w["T"]["B"]["cem"] != A["cem"]
    # codul revocat din MAIN-ul B nu poate fi revocat de A (alt cimitir)
    assert httpx.post(w["url"] + PAIR + "/codes/revoke", json={"code_id": cb["code_id"]}, headers=H(A["main"]), timeout=15).status_code == 404
    # telefon: nu poate emite/lista/revoca coduri
    assert httpx.post(w["url"] + PAIR + "/codes", json={}, headers=H(kb["binding_key"]), timeout=15).status_code == 403
    assert httpx.get(w["url"] + PAIR + "/codes", headers=H(kb["binding_key"]), timeout=15).status_code == 403


def test_03_full_flow_grant_edit_sync_back_revoke_and_offline_op_rejected(rw):
    w = rw
    seed = main_run(w, ADD)
    assert seed["rep"]["ok"], seed
    loc = seed["loc_uid"]
    code = main_issue_code(w)["code"]
    paired = redeem(w, code).json()["result"]
    key, ref = paired["binding_key"], paired["binding_uid"]
    row = requests.get(f"{w['url']}{API}/row?entity=locuri_de_veci&uid={loc}", headers=H(key), timeout=15).json()["result"]
    base = {"entity": "locuri_de_veci", "uid": loc, "action": "upsert", "base_version": row["version"]}

    def op(data, **kw):
        return {**base, "op_id": "t-" + uuid.uuid4().hex, "data": {**row["data"], **data}, **kw}

    # 3: telefonul nou NU poate scrie (serverul respinge, nu doar ascunde butoanele)
    r0 = push(w, key, [op({"observatii": "fără drept"})])[0]
    assert r0["status"] == "rejected" and r0["reason"] == "not_authorized"
    assert pg(w, "SELECT observatii FROM locuri_de_veci")[0][0] in (None, "")
    # 4: MAIN acordă „Vede + Adaugă + Editează” la Locuri de veci prin panoul real
    ui = main_run(w, UI_PREAMBLE + f"""
pn = ppanel.MobilePermissionsPanel(); pn.reload()
pn.cmb_dev.setCurrentIndex(pn.cmb_dev.findData("{ref}"))
for a in ("view", "add", "edit"):
    pn._boxes["locuri"][a].setChecked(True)
pn._save()
out["matrix"] = pn.matrix()["locuri"]
""")
    assert ui["matrix"]["edit"] and ui["matrix"]["add"] and not ui["matrix"]["delete"]
    st = status(w, key)
    assert st["permissions"]["mode"] == "matrix" and st["permissions"]["matrix"]["locuri"] == {"view": True, "add": True, "edit": True, "delete": False, "fields": None}
    assert "locuri_de_veci" in st["editable"] and st["permissions"]["matrix"]["plati"]["edit"] is False
    # 6-7: telefonul modifică un loc fictiv; Cloud acceptă
    ok = push(w, key, [op({"observatii": "modificat de pe telefon"})])[0]
    assert ok["status"] == "applied", ok
    assert pg(w, "SELECT observatii FROM locuri_de_veci")[0][0] == "modificat de pe telefon"
    # 8: MAIN primește modificarea prin Sync v2
    back = main_run(w, "rep = cloud_sync.sync_all()\nout['ok'] = rep.get('ok')\nout['obs'] = q(\"SELECT observatii FROM locuri_de_veci WHERE cod_loc='E2E-1'\")")
    assert back["ok"] and back["obs"] == [{"observatii": "modificat de pe telefon"}]
    # 10: operație offline pregătită ÎNAINTE de revocare, trimisă DUPĂ revocare => respinsă
    queued = op({"observatii": "offline după revocare"}, base_version=ok["version"], op_id="off-" + uuid.uuid4().hex)
    rv = main_run(w, UI_PREAMBLE + f"""
pn = ppanel.MobilePermissionsPanel(); pn.reload()
pn.cmb_dev.setCurrentIndex(pn.cmb_dev.findData("{ref}"))
pn._boxes["locuri"]["edit"].setChecked(False)
pn._save()
out["matrix"] = pn.matrix()["locuri"]
""")
    assert rv["matrix"]["edit"] is False and rv["matrix"]["add"] is True
    st2 = status(w, key)
    assert st2["permissions"]["matrix"]["locuri"]["edit"] is False and "locuri_de_veci" in st2["editable"]       # „Adaugă” a rămas
    late = push(w, key, [queued])[0]
    assert late["status"] == "rejected" and late["reason"] == "not_authorized", late
    assert pg(w, "SELECT observatii FROM locuri_de_veci")[0][0] == "modificat de pe telefon"
    again = push(w, key, [queued])[0]                       # retrimiterea aceleiași operații rămâne respinsă
    assert again["status"] == "rejected"
    # auditul MAIN conține acordarea și refuzul
    kinds = main_run(w, "out['k'] = sorted({e['kind'] for e in sync_v2.audit_log(50)})")["k"]
    assert {"permissions_changed", "denied"} <= set(kinds)


def test_04_other_parish_phone_cannot_read_or_write_and_rls(rw):
    w = rw
    A, B = w["T"]["A"], w["T"]["B"]
    seed = main_run(w, ADD)
    loc = seed["loc_uid"]
    kb = redeem(w, httpx.post(w["url"] + PAIR + "/codes", json={}, headers=H(B["main"]), timeout=15).json()["result"]["code"], cid="client-bbbbbb-1").json()["result"]["binding_key"]
    st = status(w, kb)
    assert st["counts"] == {e: 0 for e in st["counts"]}
    r = requests.get(f"{w['url']}{API}/row?entity=locuri_de_veci&uid={loc}", headers=H(kb), timeout=15)
    assert r.status_code == 404
    pulled = requests.get(f"{w['url']}{API}/pull?entity=locuri_de_veci&since=0", headers=H(kb), timeout=15).json()["result"]
    assert not pulled.get("rows")
    row = {"entity": "locuri_de_veci", "uid": loc, "action": "upsert", "base_version": 1, "op_id": "x-" + uuid.uuid4().hex, "data": {"cod_loc": "HACK"}}
    res = push(w, kb, [row])[0]
    assert res["status"] != "applied"
    assert pg(w, "SELECT cod_loc FROM locuri_de_veci")[0][0] == "E2E-1"
    assert requests.get(f"{w['url']}/api/v1/mobile/graves/{loc}", headers=H(kb), timeout=15).status_code in (403, 404)
    # MAIN-ul parohiei B nu vede telefoanele parohiei A și nu le poate configura
    devs = httpx.get(w["url"] + API + "/devices", headers=H(B["main"]), timeout=15).json()["result"]["devices"]
    assert A["phone_b"] not in {d["ref"] for d in devs}
    deny = httpx.put(f"{w['url']}{API}/devices/{A['phone_b']}/matrix", json={"profile": "administrator"}, headers=H(B["main"]), timeout=15)
    assert deny.status_code == 404
    # RLS direct în PostgreSQL, ca rolul aplicației, fără / cu alt context: nicio linie din A
    import psycopg
    with psycopg.connect(w["pg"]["app"], autocommit=True) as c:
        assert c.execute("SELECT count(*) FROM locuri_de_veci").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM sync_v2_permissions").fetchone()[0] == 0
        with pytest.raises(psycopg.Error):
            c.execute("DELETE FROM locuri_de_veci")
            c.execute("INSERT INTO locuri_de_veci (organization_uid, cemetery_uid, uid, cod_loc) VALUES (%s,%s,%s,'X')", (A["org"], A["cem"], uuid.uuid4().hex))


def test_05_instance_identity_old_backend_and_two_backends_same_database(rw):
    w = rw
    A = w["T"]["A"]
    i1 = httpx.get(w["url"] + API + "/instance", headers=H(A["main"]), timeout=15).json()["result"]
    i2 = httpx.get(w["url2"] + API + "/instance", headers=H(A["main"]), timeout=15).json()["result"]
    assert i1["db_fingerprint"] == i2["db_fingerprint"] and {"permissions_matrix", "pairing_codes_by_main"} <= set(i1["capabilities"])
    assert httpx.get(w["url"] + API + "/instance", headers=H(w["T"]["B"]["main"]), timeout=15).json()["result"]["db_fingerprint"] != i1["db_fingerprint"]
    assert httpx.get(w["url"] + API + "/instance", headers=H("cimitir." + str(uuid.uuid4()) + "." + "x" * 40), timeout=15).status_code == 401
    # permisiunile salvate prin backendul 1 sunt aplicate imediat și pe backendul 2 (aceeași bază): două backenduri nu împiedică propagarea
    paired = redeem(w, httpx.post(w["url"] + PAIR + "/codes", json={}, headers=H(A["main"]), timeout=15).json()["result"]["code"], url=w["url2"]).json()["result"]
    r = httpx.put(f"{w['url']}{API}/devices/{paired['binding_uid']}/matrix", json={"profile": "secretar"}, headers=H(A["main"]), timeout=15)
    assert r.status_code == 200
    on2 = httpx.get(w["url2"] + API + "/status", headers=H(paired["binding_key"]), timeout=15).json()["result"]
    assert on2["permissions"]["profile"] == "secretar" and on2["permissions"]["matrix"]["locuri"]["edit"] is True
    # MAIN (UI real) față de un backend vechi (rute inexistente) => mesaj clar în română, nu eroare mută
    from fastapi import FastAPI
    old = AdminServer(FastAPI())
    try:
        msg = main_run({**w, "url": old.url}, UI_PREAMBLE + """
pn = mpanel.MobilePairingPanel()
click_and_settle(pn, pn.btn_issue)
out["status"] = pn.lbl_status.text(); out["code"] = pn.lbl_code.text()
import mobile_pairing as mp
try:
    mp.check_instance(); out["err"] = ""
except mp.PairingProblem as e:
    out["err"] = str(e)
""")
    finally:
        old.stop()
    assert "versiune veche" in msg["err"] and msg["code"] == "—" and "nu a putut fi generat" in msg["status"] and "veche" in msg["status"]
    # cheie MAIN greșită pe backend (alt mediu/alt DB): mesaj explicit despre autentificare
    bad = main_run({**w, "url": w["url"], "T": {"A": {**A, "main": "cimitir." + str(uuid.uuid4()) + "." + "y" * 43}}}, UI_PREAMBLE + """
import mobile_pairing as mp
try:
    mp.check_instance(); out["err"] = ""
except mp.PairingProblem as e:
    out["err"] = str(e)
""")
    assert "nu recunoaște legătura MAIN" in bad["err"]


def test_06_admin_code_path_still_works_and_new_phone_is_read_only(rw):
    w = rw
    A = w["T"]["A"]
    cl = adc.AdminClient(w["url"])
    cl.login(EMAIL, PW)
    res = cl.issue_code(A["org"], A["cem"], "phone")
    paired = redeem(w, res["display"], cid="client-admin-01").json()["result"]
    st = status(w, paired["binding_key"])
    assert st["editable"] == [] and st["permissions"]["mode"] == "legacy"
    cl.close()


# ===================================================================== PWA în browser real (Chromium)
GRANT = UI_PREAMBLE + """
pn = ppanel.MobilePermissionsPanel(); pn.reload()
pn.cmb_dev.setCurrentIndex(pn.cmb_dev.findData("__REF__"))
for a in __ON__:
    pn._boxes["locuri"][a].setChecked(True)
for a in __OFF__:
    pn._boxes["locuri"][a].setChecked(False)
pn._save()
"""


def _grant(w, ref, on=(), off=()):
    main_run(w, GRANT.replace("__REF__", ref).replace("__ON__", repr(list(on))).replace("__OFF__", repr(list(off))))


def test_07_pwa_browser_buttons_follow_rights_and_edit_reaches_main(rw):
    sync_api = pytest.importorskip("playwright.sync_api")
    w = rw
    main_run(w, ADD)
    code = main_issue_code(w)["code"]
    with sync_api.sync_playwright() as p:
        br = p.chromium.launch(args=["--no-sandbox"])
        page = br.new_page(viewport={"width": 1280, "height": 900})
        page.goto(f"{w['url']}/mobile/#pair={code.replace('-', '')}")
        page.wait_for_selector("#perm-pill", timeout=30000)
        assert page.inner_text("#perm-pill").strip() == "Doar citire"                       # comportamentul implicit sigur
        ref = [d for d in httpx.get(w["url"] + API + "/devices", headers=H(w["T"]["A"]["main"]), timeout=15).json()["result"]["devices"] if d["name"] != "t-MOBILE"][0]["ref"]

        def open_grave():
            page.click("[data-view=locuri]")
            page.wait_for_selector(".result[data-grave]", timeout=20000)
            page.click(".result[data-grave]")
            page.wait_for_selector("#sheet.show", timeout=20000)
            page.wait_for_timeout(500)

        def sync():
            page.click("[data-testid=sync-button]")
            page.wait_for_selector("[data-testid=sync-run]", timeout=10000)
            page.click("[data-testid=sync-run]")
            page.wait_for_timeout(2500)
            page.keyboard.press("Escape")
            page.evaluate("document.getElementById('sheet')?.classList.remove('show')")

        open_grave()
        assert page.locator("[data-testid=edit-grave]").count() == 0 and page.locator("[data-testid=delete-grave]").count() == 0
        page.evaluate("document.getElementById('sheet').classList.remove('show')")
        # MAIN acordă Vede + Adaugă + Editează; la următoarea sincronizare telefonul afișează butonul
        _grant(w, ref, on=("view", "add", "edit"))
        sync()
        assert page.inner_text("#perm-pill").strip() != "Doar citire"
        open_grave()
        assert page.locator("[data-testid=edit-grave]").count() == 1 and page.locator("[data-testid=delete-grave]").count() == 0
        page.click("[data-testid=edit-grave]")
        page.wait_for_selector("[data-testid=field-observatii]", timeout=10000)
        page.fill("[data-testid=field-observatii]", "din browserul telefonului")
        page.click("#rec-save")
        page.wait_for_timeout(3500)
        assert pg(w, "SELECT observatii FROM locuri_de_veci")[0][0] == "din browserul telefonului"          # Cloud a acceptat
        back = main_run(w, "rep = cloud_sync.sync_all()\nout['obs'] = q(\"SELECT observatii FROM locuri_de_veci WHERE cod_loc='E2E-1'\")")
        assert back["obs"] == [{"observatii": "din browserul telefonului"}]                                      # MAIN a primit modificarea
        # MAIN revocă „Editează”: după sincronizare butonul dispare, iar serverul respinge oricum
        page.evaluate("document.getElementById('sheet')?.classList.remove('show')")
        _grant(w, ref, off=("edit",))
        sync()
        open_grave()
        assert page.locator("[data-testid=edit-grave]").count() == 0
        br.close()


def test_08_admin_tab_in_main_generates_phone_code_with_clear_messages(rw):
    """Varianta cu administrator Cloud (email + parolă) din fereastra de asociere: cod valabil, mesaje clare la erori, nimic mut."""
    w = rw
    out = main_run(w, UI_PREAMBLE + f"""
import cloud_associate_window as caw
dlg = caw.CloudAssociateDialog(can_manage=lambda: True)
dlg.ed_url.setText({w['url']!r})
# fără autentificare: mesaj vizibil, nu buton mut
dlg.btn_phone.setEnabled(True); dlg._issue("phone")
out["no_login"] = dlg.lbl_adm.text()
dlg.ed_mail.setText("gresit@example.com"); dlg.ed_pass.setText("parola-gresita-123")
dlg.btn_login.click(); pump(lambda: "eșuată" in dlg.lbl_adm.text(), 15)
out["bad_login"] = dlg.lbl_adm.text()
dlg.ed_mail.setText({EMAIL!r}); dlg.ed_pass.setText({PW!r})
dlg.btn_login.click(); pump(lambda: dlg.lbl_adm.text().startswith("Autentificat"), 15)
out["login"] = dlg.lbl_adm.text()
out["cem"] = dlg.cmb_cem.count()
dlg.btn_phone.click(); pump(lambda: dlg.lbl_code.text() != "—", 15)
out["code"] = dlg.lbl_code.text(); out["left"] = dlg.lbl_left.text()
""")
    assert "Autentifică-te" in out["no_login"] and "eșuată" in out["bad_login"] and out["login"].startswith("Autentificat")
    assert len(out["code"].replace("-", "")) == 20 and out["left"].startswith("Valabil"), out
    assert redeem(w, out["code"], cid="client-adm-ui-1").status_code == 201


def test_09_update_verifier_script_identity_old_wrong_key(rw, monkeypatch, capsys):
    import sys
    from pathlib import Path
    from fastapi import FastAPI
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import verify_backend_instance as vbi
    key = rw["T"]["A"]["main"]
    a, b = vbi.inspect_backend(rw["url"], key), vbi.inspect_backend(rw["url2"], key)
    assert a["status"] == b["status"] == "ok" and a["db_fingerprint"] == b["db_fingerprint"] and vbi.verdict([a, b]) == 0
    old = AdminServer(FastAPI())
    try:
        o = vbi.inspect_backend(old.url, key)
    finally:
        old.stop()
    assert o["status"] in ("old", "unreachable", "error") and vbi.verdict([a, o]) != 0
    other = vbi.inspect_backend(rw["url"], rw["T"]["B"]["main"])           # altă bază/alt cimitir: amprentă diferită => nu se face switch
    assert other["status"] == "ok" and vbi.verdict([a, other]) == 3
    assert vbi.inspect_backend(rw["url"], "cimitir." + str(uuid.uuid4()) + "." + "z" * 43)["status"] == "auth_rejected"
    monkeypatch.setenv("CIMITIR_MAIN_KEY", key)
    assert vbi.main(["--url", rw["url"], "--url", rw["url2"]]) == 0
    printed = capsys.readouterr().out
    assert key not in printed and "ACEEAȘI bază" in printed


def test_10_revoked_codes_do_not_count_toward_open_code_limit(rw):
    A = rw["T"]["A"]
    issued = [httpx.post(rw["url"] + PAIR + "/codes", json={}, headers=H(A["main"]), timeout=15) for _ in range(pp.MAX_OPEN_CODES)]
    assert all(r.status_code == 201 for r in issued)
    assert httpx.post(rw["url"] + PAIR + "/codes", json={}, headers=H(A["main"]), timeout=15).status_code == 429
    cid = issued[0].json()["result"]["code_id"]
    assert httpx.post(rw["url"] + PAIR + "/codes/revoke", json={"code_id": cid}, headers=H(A["main"]), timeout=15).status_code == 200
    assert httpx.post(rw["url"] + PAIR + "/codes", json={}, headers=H(A["main"]), timeout=15).status_code == 201       # „Reînnoiește” funcționează după revocare
