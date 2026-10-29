"""Panoul MAIN „Permisiuni Mobile" (Qt offscreen, cu server simulat): bife legate, câmpuri editabile, profiluri. NU verifică pe Windows real."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "desktop", ROOT / "shared"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import cimitir_mobile_perms as mp  # noqa: E402


@pytest.fixture()
def panel(monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    import sync_v2
    import mobile_permissions_panel as pnl
    calls = {}
    meta = {"categories": mp.CATEGORIES, "actions": mp.ACTION_LABELS, "fields": {k: mp.category_fields(k) for k in mp.CATEGORIES},
            "profiles": {k: {"label": v["label"], "matrix": mp.normalize(v["matrix"]), "custom": False} for k, v in mp.PROFILES.items()}}
    monkeypatch.setattr(sync_v2, "permission_profiles", lambda: meta)
    monkeypatch.setattr(sync_v2, "list_devices", lambda: {"devices": [{"ref": "r1", "name": "Telefon Preot", "matrix": None, "profile": None, "view_pii": False}]})
    monkeypatch.setattr(sync_v2, "set_device_matrix", lambda ref, profile=None, matrix=None, view_pii=False: calls.update(ref=ref, profile=profile, matrix=matrix, pii=view_pii) or {})
    monkeypatch.setattr(sync_v2, "save_permission_profile", lambda k, l, m: calls.update(profile_key=k, label=l, pmatrix=m) or {})
    monkeypatch.setattr(pnl.QMessageBox, "information", lambda *a, **k: None)
    pn = pnl.MobilePermissionsPanel()
    pn.reload()
    pn._calls = calls
    return pn


def test_table_has_8_categories_x_4_actions_and_couples_checks(panel):
    assert panel.table.rowCount() == 8 and panel.table.columnCount() == 6
    b = panel._boxes["locuri"]
    b["edit"].setChecked(True)
    assert b["view"].isChecked()                 # Editează cere Vede
    b["view"].setChecked(False)
    assert not b["edit"].isChecked() and not b["add"].isChecked() and not b["delete"].isChecked()


def test_profile_applies_and_save_sends_matrix_with_fields(panel):
    panel.cmb_prof.setCurrentIndex(panel.cmb_prof.findData("personal_cimitir"))
    panel._apply_profile()
    m = panel.matrix()
    assert m["locuri"]["edit"] and set(m["locuri"]["fields"]) == {"observatii", "stare", "disponibilitate", "ocupat"} and not m["plati"]["view"]
    panel._fields["harta"] = ["color"]                      # câmpuri editabile restrânse (coordonatele blocate)
    panel._boxes["harta"]["edit"].setChecked(True)
    panel.chk_pii.setChecked(False)
    panel._save()
    sent = panel._calls
    assert sent["ref"] == "r1" and sent["matrix"]["harta"]["fields"] == ["color"] and "observatii" in sent["matrix"]["locuri"]["fields"]
    mp.normalize(sent["matrix"])                             # matricea trimisă este acceptată de validarea serverului


def test_save_custom_profile(panel, monkeypatch):
    import mobile_permissions_panel as pnl
    monkeypatch.setattr(pnl.QInputDialog, "getText", staticmethod(lambda *a, **k: ("Gestionar nou", True)))
    panel._boxes["plati"]["view"].setChecked(True)
    panel._save_profile()
    assert panel._calls["profile_key"] == "gestionar_nou" and panel._calls["pmatrix"]["plati"]["view"] is True
