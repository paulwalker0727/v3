"""Fila „G. Provizionare” din Centrul Cloud (Qt offscreen): conexiune în SecretStore, listă instanțe, înregistrare cu cheia proprietarului în SecretStore, fără secrete în câmpuri după folosire."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="cimitir_test_prov_tab_")
os.environ["CIMITIR_DATA_DIR"] = _TMP
os.environ["CIMITIR_LOG_DIR"] = os.path.join(_TMP, "logs")
DESKTOP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DESKTOP))
sys.path.insert(0, str(DESKTOP.parent / "shared"))

import pytest  # noqa: E402

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

import cimitir_instance_grant as ig  # noqa: E402
import cimitir_secretstore as ss  # noqa: E402
import cloud_provision_tab as cpt  # noqa: E402

_app = QApplication.instance() or QApplication([])


class FakeClient:
    calls = []

    def __init__(self, url, token):
        self.url, self.token = url, token

    def list_instances(self):
        return [{"slug": "par-a", "parish_name": "Parohia A", "domain": "a.example.ro", "status": "running", "release": "v1", "environment": "TEST"}]

    def register(self, slug, parish, domain, owner_public_key="", generate_owner_key=False):
        FakeClient.calls.append((slug, parish, domain, generate_owner_key, self.token))
        return {"slug": slug, "owner_private_key_ONCE": "PRIVATE-KEY-VALUE", "owner_fingerprint": "abcd"}


def _wait(cond, ms=3000):
    import time
    end = time.time() + ms / 1000
    while time.time() < end and not cond():
        _app.processEvents()
        time.sleep(0.01)
    return cond()


def _tab():
    store = ss.DpapiSecretStore(Path(_TMP) / "sec", lambda b: b[::-1], lambda b: b[::-1])
    return cpt.ProvisionTab(store, lambda: True, client_factory=FakeClient), store


def test_connection_saved_in_secretstore_not_in_field():
    t, store = _tab()
    t.ed_url.setText("https://prov.exemplu.ro")
    t.ed_token.setText("cpv_tokensecret")
    t.save_connection()
    assert store.get(cpt.TOKEN_SECRET) == "cpv_tokensecret" and t.ed_token.text() == ""


def test_insecure_url_refused_and_list_shown():
    t, store = _tab()
    t.ed_url.setText("http://evil.example.com")
    store.set(cpt.TOKEN_SECRET, "cpv_x")
    t.refresh()
    assert "HTTPS" in t.txt.toPlainText() and t.table.rowCount() == 0
    t.ed_url.setText("https://prov.exemplu.ro")
    t.refresh()
    assert _wait(lambda: t.table.rowCount() == 1) and t.table.item(0, 2).text() == "Activă (sănătoasă)"


def test_register_puts_private_key_only_in_secretstore():
    t, store = _tab()
    store.set(cpt.TOKEN_SECRET, "cpv_x")
    t.ed_url.setText("https://prov.exemplu.ro")
    t.ed_slug.setText("par-b")
    t.ed_parish.setText("Parohia B")
    t.ed_domain.setText("b.example.ro")
    t.register_new()
    assert _wait(lambda: store.get(ig.secret_name("par-b")) == "PRIVATE-KEY-VALUE")
    assert "PRIVATE-KEY-VALUE" not in t.txt.toPlainText() and FakeClient.calls[-1][3] is True


def test_guard_blocks_when_not_admin():
    store = ss.DpapiSecretStore(Path(_TMP) / "sec2", lambda b: b[::-1], lambda b: b[::-1])
    t = cpt.ProvisionTab(store, lambda: False, client_factory=FakeClient)
    t.ed_url.setText("https://prov.exemplu.ro")
    store.set(cpt.TOKEN_SECRET, "cpv_x")
    before = len(FakeClient.calls)
    t.ed_slug.setText("x-y-z")
    t.register_new()
    assert len(FakeClient.calls) == before
