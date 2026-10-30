"""Panoul MAIN „Asociere telefon” (Qt offscreen, server simulat prin monkeypatch): fiecare acțiune are rezultat vizibil, erorile sunt în română. NU verifică pe Windows real."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "desktop", ROOT / "shared"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture()
def env(monkeypatch):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import cloud_sync
    import mobile_pairing as mp
    import mobile_pairing_panel as pnl
    import sync_v2
    state = {"url": "http://127.0.0.1:9", "key": "cimitir.00000000-0000-0000-0000-000000000000." + "k" * 43, "calls": [], "fail": None, "caps": ["permissions_matrix", "pairing_codes_by_main"]}

    class R:
        def __init__(self, v):
            self.v = v

        def json(self):
            return self.v

    def fake_request(method, op, prefix=sync_v2.API, **kw):
        state["calls"].append((method, prefix, op))
        if state["fail"]:
            raise state["fail"]
        if op == "instance":
            return R({"release": "r1", "db_fingerprint": "abc", "device_type": "MAIN", "capabilities": state["caps"]})
        if op == "codes" and method == "POST":
            return R({"code": "ABCDE-FGHJK-MNPQR-STUVW", "code_id": "0123456789abcdef", "ttl": int(kw["json"]["ttl"])})
        if op == "codes/revoke":
            return R({"revoked": True})
        raise AssertionError(op)

    monkeypatch.setattr(sync_v2, "connection", lambda: (state["url"], state["key"], "registry"))
    monkeypatch.setattr(sync_v2, "request", fake_request)
    monkeypatch.setattr(sync_v2, "list_devices", lambda: {"profiles": {}, "devices": [{"ref": "r1", "name": "Telefon Ion", "matrix": None, "profile": None, "editable": [],
                                                                                          "created_at": "2026-01-01T10:00:00", "last_seen_at": None}]})
    monkeypatch.setattr(pnl.QMessageBox, "question", staticmethod(lambda *a, **k: pnl.QMessageBox.Yes))

    def settle(panel):
        end = time.time() + 10
        while time.time() < end:
            app.processEvents()
            if panel.btn_issue.isEnabled():
                return
            time.sleep(0.02)
        raise AssertionError("panoul a rămas ocupat")
    return {"state": state, "pnl": pnl, "mp": mp, "cs": cloud_sync, "settle": settle, "app": app}


def test_issue_shows_code_countdown_and_unconfigured_message(env):
    pn = env["pnl"].MobilePairingPanel()
    pn.btn_issue.click()
    env["settle"](pn)
    assert pn.lbl_code.text() == "ABCDE-FGHJK-MNPQR-STUVW" and pn.lbl_left.text().startswith("Valabil") and "Cod generat" in pn.lbl_status.text()
    pn._issued["deadline"] = time.time() - 1                      # expirare: codul dispare, mesaj clar
    pn._tick()
    assert pn.lbl_code.text() == "—" and "expirat" in pn.lbl_left.text() and not pn.btn_revoke.isEnabled()
    env["state"]["url"] = ""
    pn.btn_issue.click()
    env["settle"](pn)
    assert "Nu este configurat niciun Cloud" in pn.lbl_status.text() and pn.lbl_code.text() == "—"
    env["state"]["url"], env["state"]["key"] = "http://127.0.0.1:9", ""
    pn.btn_issue.click()
    env["settle"](pn)
    assert "nu este asociat" in pn.lbl_status.text()


def test_errors_are_explained_in_romanian_and_never_silent(env):
    cs, pn = env["cs"], env["pnl"].MobilePairingPanel()
    for exc, needle in ((cs.Rejected(401, "x"), "nu recunoaște legătura MAIN"), (cs.Rejected(429, "x"), "Prea multe"), (cs.RetryLater("timeout"), "nu răspunde"),
                        (cs.Rejected(404, "Serverul Cloud nu are această funcție"), "versiune veche")):
        env["state"]["fail"] = exc
        pn.btn_issue.click()
        env["settle"](pn)
        assert needle in pn.lbl_status.text() and "nu a putut fi generat" in pn.lbl_status.text(), pn.lbl_status.text()
    env["state"]["fail"] = None
    env["state"]["caps"] = ["sync_v2"]                              # migrări lipsă pe bază
    pn.btn_issue.click()
    env["settle"](pn)
    assert "migrările necesare" in pn.lbl_status.text()


def test_renew_revokes_old_and_issues_new_and_revoke_clears(env):
    pn = env["pnl"].MobilePairingPanel()
    pn.btn_issue.click()
    env["settle"](pn)
    pn.btn_renew.click()
    env["settle"](pn)
    ops = [c[2] for c in env["state"]["calls"]]
    assert "codes/revoke" in ops and ops.count("codes") == 2 and pn.lbl_code.text() != "—"
    pn.btn_revoke.click()
    env["settle"](pn)
    assert pn.lbl_code.text() == "—" and "revocat" in pn.lbl_status.text()


def test_phone_rights_summary_defaults_to_read_only(env):
    mp = env["mp"]
    assert mp.rights_summary({"matrix": None, "editable": []}) == "Doar citire (implicit)"
    ro = {"locuri": {"view": True, "add": False, "edit": False, "delete": False}}
    assert mp.rights_summary({"matrix": ro, "profile": "doar_consultare"}).startswith("Doar citire")
    rw = {"locuri": {"view": True, "add": True, "edit": True, "delete": False}}
    assert mp.rights_summary({"matrix": rw, "profile": "secretar"}, {"secretar": "Secretar"}) == "Secretar"
    pn = env["pnl"].MobilePairingPanel()
    pn.reload()
    env["settle"](pn)
    assert pn.table.rowCount() == 1 and "Doar citire" in pn.table.item(0, 3).text() and "Backend verificat" in pn.lbl_server.text()
    pn.table.selectRow(0)
    got = []
    pn.permissions_requested.connect(got.append)
    pn.btn_rights.click()
    assert got == ["r1"]
