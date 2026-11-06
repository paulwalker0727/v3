"""Date sensibile (CNP/CI) și poligoane: reguli de permisiuni impuse de SERVER, testate local pe funcțiile pure (fără PostgreSQL, fără rețea, date fictive).
NU înlocuiesc testele pe PostgreSQL/Cloud TEST (`test_mobile_permissions_pg.py`, `test_etapa789_pii_docs_pg.py`): acelea rămân de rulat în TEST."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

import cimitir_entities as ents  # noqa: E402
import cimitir_mobile_perms as mp  # noqa: E402
import cimitir_sync_v2 as sv  # noqa: E402

IDENTITY = re.compile(r"cnp|bi_ci|id_card|serie|buletin|passport|pasaport|identit", re.I)


def phone(matrix, pii):
    return {"device_type": "PHONE", "_mx": (matrix, pii)}      # `_mx` = matricea efectivă, citită o dată per cerere


def matrix(**over):
    base = {c: {"view": True, "add": False, "edit": False, "delete": False, "fields": None} for c in mp.CATEGORIES}
    for cat, cell in over.items():
        base[cat] = {**base[cat], **cell}
    return mp.normalize(base)


def test_every_identity_like_column_in_synced_entities_is_declared_sensitive():
    missing = [(e, c) for e, sp in ents.ENTITIES.items() for c in sp["cols"] if IDENTITY.search(c) and c not in sv.SENSITIVE.get(e, ())]
    assert missing == [], f"coloane de identitate nesocotite ca sensibile: {missing}"


def test_phone_without_view_pii_cannot_see_cnp_ci_but_with_it_can():
    for entity, cols in sv.SENSITIVE.items():
        assert set(cols) <= sv.hidden_for(None, phone(matrix(), False), entity)
        assert not (set(cols) & sv.hidden_for(None, phone(matrix(), True), entity))


def test_main_device_always_sees_everything():
    main = {"device_type": "MAIN", "_mx": (mp.full_matrix(), True)}
    assert all(sv.hidden_for(None, main, e) == set() for e in ents.ORDER)


def test_wire_redaction_removes_sensitive_values_from_server_payload_and_conflict_copies():
    w = {"uid": "u", "version": 3, "data": {"nume": "Ion", "cnp": "1800101000000", "bi_ci": "AB123456"}}
    red = sv.redact_wire("concesionari", w)
    assert red["data"] == {"nume": "Ion"} and w["data"]["cnp"] == "1800101000000", "originalul nu se modifică"
    assert sv.drop_wire(w, {"cnp"})["data"] == {"nume": "Ion", "bi_ci": "AB123456"}


def test_revocation_applies_on_next_request_because_matrix_is_read_per_request():
    before = phone(matrix(), True)
    after = phone(matrix(), False)          # alt `a` = cerere nouă după revocare din MAIN
    assert not (set(sv.SENSITIVE["inhumati"]) & sv.hidden_for(None, before, "inhumati"))
    assert set(sv.SENSITIVE["inhumati"]) <= sv.hidden_for(None, after, "inhumati")


def test_phone_without_map_view_does_not_receive_polygon_columns():
    m = matrix(harta={"view": False})
    assert {"points", "color"} <= sv.hidden_for(None, phone(m, False), "locuri_de_veci")


def test_writing_polygon_requires_map_permission_and_cannot_clear_it_silently():
    server = {"cod_loc": "L-1", "points": [[0, 0], [1, 0], [1, 1]], "color": "#000"}
    edit_locuri_only = matrix(locuri={"edit": True})
    changed = {**server, "points": [[0, 0], [2, 0], [2, 2]]}
    assert mp.authorize_write(edit_locuri_only, "locuri_de_veci", "update", server, changed) == "not_authorized:harta_edit"
    cleared = {**server, "points": []}
    assert mp.authorize_write(matrix(harta={"edit": True}), "locuri_de_veci", "update", server, cleared) == "not_authorized:harta_delete"
    assert mp.authorize_write(matrix(harta={"edit": True}), "locuri_de_veci", "update", server, changed) is None
    assert mp.authorize_write(edit_locuri_only, "locuri_de_veci", "insert", None, {"cod_loc": "N", "points": [[0, 0]]}) == "not_authorized:add"
    assert mp.authorize_write(matrix(locuri={"add": True}), "locuri_de_veci", "insert", None, {"cod_loc": "N", "points": [[0, 0]]}) == "not_authorized:harta_add"


def test_read_only_profile_cannot_write_any_entity():
    ro = matrix()      # doar „Vede"
    for entity in ents.ORDER:
        assert mp.authorize_write(ro, entity, "insert", None, {"cod_loc": "X"}) == "not_authorized:add"
        assert mp.authorize_write(ro, entity, "delete", {}, {}) == "not_authorized:delete"
