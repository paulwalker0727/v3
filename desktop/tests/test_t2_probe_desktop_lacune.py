"""T2 (iteration_2): probe pentru lacune NOI / fluxuri legitime după garda de drepturi. SQLite sintetic în tmp_path; fără rețea."""
import sys
from pathlib import Path

import pytest

DESKTOP = Path(__file__).resolve().parents[1]
SHARED = DESKTOP.parent / "shared"
for p in (str(DESKTOP), str(SHARED)):
    if p not in sys.path:
        sys.path.insert(0, p)

import database as db  # noqa: E402
import cimitir_auth as auth  # noqa: E402
import access_session  # noqa: E402

POLY = [(0, 0), (10, 0), (10, 10), (0, 10)]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_audit_actor", "")
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "cimitir.db"))
    db.init_db()
    auth.init_auth()
    db.set_access_guard(None)
    db.save_plot_to_db("A-1", POLY, "#fff")

    def login(rights_list, role="operator"):
        uid = auth.create_user(f"t{len(auth.list_users())}", "Parola-test-123", role, rights=rights_list)
        access_session.set_user(auth.get_session_user(uid))
        db.set_access_guard(access_session.can)
        return uid
    yield type("E", (), {"login": staticmethod(login), "tmp": tmp_path})
    db.set_access_guard(None)
    access_session.set_user(None)


def _count(sql, args=()):
    c = db.get_connection()
    try:
        return c.execute(sql, args).fetchone()[0]
    finally:
        c.close()


# (b) legitimate flows: new plot + metadata (assign_plot_code_window / generate_plots_for_row pattern)
@pytest.mark.parametrize("rights,role", [([], "operator"), (["edit", "approve"], "operator"), (["edit"], "operator"),
                                          (["cfg", "add"], "operator"), ([], "admin")])
def test_loc_nou_cu_metadate_flux_legitim(env, rights, role):
    env.login(rights, role)
    assert db.save_plot_to_db("N-1", POLY, "#0078FF")
    with db.granted("edit"):
        assert db.save_loc_metadata("N-1", {"sector": "S", "rand": "1", "numar": "1"})
        assert db.save_extra_data("N-1", {"observatii": "x"})
    assert (db.load_loc_by_cod("N-1") or {}).get("sector") == "S"


@pytest.mark.parametrize("rights,role", [([], "operator"), (["edit"], "operator"), ([], "admin")])
def test_geometrie_sector_rand_flux_legitim(env, rights, role):
    env.login(rights, role)
    assert db.save_plot_to_db("A-1", [(0, 0), (20, 0), (20, 20)], "#000")
    assert db.save_sector_to_db("SX", POLY, "#123456")
    assert db.save_sector_to_db("SX", POLY, "#654321")
    r1 = db.add_row("S1", "Rând 1", 1, [(0, 0), (5, 0)], 10, 10)
    db.shift_rows_order("S1", 1, 1)
    r0 = db.add_row("S1", "Rând nou", 1, [(0, 0), (5, 0)], 10, 10)
    assert r1 and r0
    assert sorted(r["order_index"] for r in db.list_rows("S1")) == [1, 2]


# (c) legacy 'approve' only: no add/edit/delete in MAIN, but approve works
def test_legacy_approve_only(env):
    env.login(["approve"])
    assert not db.save_plot_to_db("N-2", POLY, "#000")
    assert not db.save_loc_metadata("A-1", {"sector": "Q"})
    assert not db.save_sector_to_db("SY", POLY, "#000")
    cid = None
    with db.system_operation():
        cid = db.add_cerere_concesionare({"nume": "Pop", "prenume": "Ion", "cod_loc": "A-1"})
    assert cid
    assert db.set_cerere_status(cid, "respins", "nota")


# (b) cloud sync: add_document under system_operation works for a view-only session
def test_system_operation_add_document_view_only(env, tmp_path):
    env.login(["cfg"])
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 test")
    assert db.add_document("loc", "A-1", "alt", str(f), copy=True) in (None, False, 0)
    with db.system_operation():
        assert db.add_document("loc", "A-1", "alt", str(f), copy=True)


# (b) cereri online approve: documents under granted('add') for legacy 'approve' account
def test_aprobare_cerere_documente_granted(env, tmp_path):
    env.login(["approve"])
    f = tmp_path / "ci.jpg"
    f.write_bytes(b"\xff\xd8\xff\xe0fakejpg")
    with db.system_operation():
        cid = db.save_concesionar({"nume": "Pop", "prenume": "Ion"})
    with db.granted("add"):
        assert db.add_document("concesionar", cid, "buletin", str(f), copy=True)


# NEW GAP probe: "Continuă pe telefon" (MAIN) issues a mobile upload token without checking `add`
def test_continua_pe_telefon_cere_add(env, monkeypatch):
    import mobile_upload
    calls = []

    def fake_request_token(entity_type, entity_id, issued_by="main", *a, **k):
        calls.append((entity_type, entity_id, issued_by))
        raise RuntimeError("stop-after-issue")
    monkeypatch.setattr(mobile_upload, "request_token", fake_request_token)
    env.login(["cfg"])                     # view-only
    assert not access_session.can("add")
    import mobile_upload_dialog
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    try:
        mobile_upload_dialog.PhoneUploadDialog(None, "concesionar", 1, "x")
    except RuntimeError:
        pass
    print("token requested by view-only user:", calls)
    assert calls == [], "view-only user could request a phone-upload token (no `add` check, issued_by='main')"
