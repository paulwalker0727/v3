"""Association corrections: fictional SQLite only; no cloud or production data."""
import pytest
from test_p4_interconectare import env, dump, db, links


def seed(env):
    values = {"concesionar_id": env.ion, "cod_loc": "A-01", "owner_fullname": "Titular istoric",
              "owner_cnp": "2000000000001", "cemetery_parcel": "ISTORIC", "extra_data": {"nota": "istoric"}}
    ids = [db.save_contract(dict(values, numar_contract=f"C-{i}")) for i in range(2)]
    other = db.save_contract(dict(values, numar_contract="ALT-TITULAR", concesionar_id=env.maria))
    other_place = db.save_contract(dict(values, numar_contract="ALT-LOC", cod_loc="A-02"))
    db.add_plata("A-01", 10, "2026-01-01", "taxa", "TEST", contract_id=ids[0])
    return ids, other, other_place


@pytest.mark.parametrize("destination", [None, "A-03", "A-02"])
def test_correction_changes_only_link_and_preserves_other_records(env, destination):
    ids, other, other_place = seed(env)
    before = dump("contracte")
    people, plots, payments = dump("concesionari"), dump("locuri_de_veci"), dump("plati")
    assert db.change_concesionar_loc(env.ion, "A-01", destination, ids, "corectie fictiva")
    after = dump("contracte")
    for a, b in zip(after, before):
        if a["id"] in ids:
            assert a["cod_loc"] == (destination or "")
            assert a["id_loc"] == (db.load_loc_by_cod(destination)["id"] if destination else None)
            assert {k: v for k, v in a.items() if k not in ("cod_loc", "id_loc")} == {k: v for k, v in b.items() if k not in ("cod_loc", "id_loc")}
        else:
            assert a == b
    assert dump("concesionari") == people and dump("locuri_de_veci") == plots and dump("plati") == payments
    assert "A-01" not in {r["cod_loc"] for r in links.locuri_for_concesionar(env.ion)}
    assert "A-01" in {r["cod_loc"] for r in links.locuri_for_concesionar(env.maria)}
    assert len(links.contracts_for_concesionar(env.ion)) == 3
    c = db.get_connection()
    logs = c.execute("SELECT target, detail FROM audit_log WHERE action IN ('contract.replace_loc','contract.unlink_loc')").fetchall()
    c.close()
    assert {r["target"] for r in logs} == {f"contract:{i}" for i in ids}
    assert all("A-01" in r["detail"] for r in logs)


@pytest.mark.parametrize("case", ["stale", "missing", "same", "denied", "rollback"])
def test_invalid_or_failed_change_is_atomic(env, case):
    ids, _, _ = seed(env)
    expected, destination = ids, "A-03"
    if case == "stale":
        expected = ids[:1]
    elif case == "missing":
        destination = "MISSING"
    elif case == "same":
        destination = "A-01"
    elif case == "denied":
        db.set_access_guard(lambda right: False)
    elif case == "rollback":
        def fail(*args):
            raise RuntimeError("simulated failure after update")
        env.mp.setattr(db, "_audit", fail)
    before = dump("contracte")
    assert not db.change_concesionar_loc(env.ion, "A-01", destination, expected)
    assert dump("contracte") == before


def test_legacy_code_only_link_and_id_precedence(env):
    ids, _, _ = seed(env)
    c = db.get_connection()
    c.execute("UPDATE contracte SET id_loc=NULL WHERE id=?", (ids[0],))
    c.execute("UPDATE contracte SET cod_loc='STALE-CODE' WHERE id=?", (ids[1],))
    c.close()
    assert db.change_concesionar_loc(env.ion, "A-01", None, ids)
    assert all(not db.get_contract(i)["cod_loc"] for i in ids)


@pytest.mark.parametrize("replace", [False, True])
def test_buttons_operate_on_selected_place_and_refresh(env, replace):
    from PySide6.QtWidgets import QDialog
    import entity_links_ui as ui
    ids, _, _ = seed(env)
    env.login("admin")
    env.mp.setattr(ui.PickDialog, "exec", lambda self: QDialog.Accepted)
    env.mp.setattr(ui.PickDialog, "selected_row", lambda self: {"cod_loc": "A-03"})
    d = ui.LinkedRecordsDialog(None, None, "concesionar", env.ion)
    d.table.selectRow(next(i for i,r in enumerate(d._rows) if r["cod_loc"] == "A-01"))
    assert d.btn_replace.isEnabled() and d.btn_unlink.isEnabled()
    (d.btn_replace if replace else d.btn_unlink).click()
    assert all(db.get_contract(i)["cod_loc"] == ("A-03" if replace else "") for i in ids)
    assert "A-01" not in {r["cod_loc"] for r in d._rows}


@pytest.mark.parametrize("cancel_at", ["picker", "confirmation", "reason"])
def test_cancel_never_changes_data(env, cancel_at):
    from PySide6.QtWidgets import QDialog, QMessageBox, QInputDialog
    import entity_links_ui as ui
    seed(env)
    env.login("admin")
    env.mp.setattr(ui.PickDialog, "exec", lambda self: QDialog.Rejected if cancel_at == "picker" else QDialog.Accepted)
    env.mp.setattr(ui.PickDialog, "selected_row", lambda self: {"cod_loc": "A-03"})
    if cancel_at == "confirmation":
        env.mp.setattr(QMessageBox, "question", lambda *a: QMessageBox.No)
    if cancel_at == "reason":
        env.mp.setattr(QInputDialog, "getText", lambda *a: ("", False))
    before = dump("contracte")
    d = ui.LinkedRecordsDialog(None, None, "concesionar", env.ion)
    d.btn_replace.click()
    assert dump("contracte") == before


def test_empty_selection_and_read_only_user_disable_actions(env):
    import entity_links_ui as ui
    d = ui.LinkedRecordsDialog(None, None, "concesionar", env.ion)
    assert not d.btn_replace.isEnabled() and not d.btn_unlink.isEnabled()
    seed(env)
    env.login("viewer")
    d.reload()
    assert not d.btn_replace.isEnabled() and not d.btn_unlink.isEnabled()
    before = dump("contracte")
    d.change_selected_loc(False)
    assert dump("contracte") == before


def test_stale_confirmation_is_rejected_in_ui(env):
    from PySide6.QtWidgets import QMessageBox
    import entity_links_ui as ui
    seed(env)
    env.login("admin")
    d = ui.LinkedRecordsDialog(None, None, "concesionar", env.ion)
    def concurrent_contract(*args):
        db.save_contract({"numar_contract": "NOU", "concesionar_id": env.ion, "cod_loc": "A-01"})
        return QMessageBox.Yes
    env.mp.setattr(QMessageBox, "question", concurrent_contract)
    before = dump("contracte")
    d.btn_unlink.click()
    assert dump("contracte")[:len(before)] == before
    assert env.msgs["warning"]


@pytest.mark.parametrize("destination", [None, "A-03"])
def test_documents_keep_historical_snapshot_after_link_change(env, tmp_path, destination):
    from docx import Document
    import fitz
    import contract_generator as cg
    import pdf_reports
    from test_p4_interconectare import DESKTOP
    ids, _, _ = seed(env)
    assert db.change_concesionar_loc(env.ion, "A-01", destination, ids)
    k = ids[0]
    before = db.get_contract(k)
    output = tmp_path / "istoric.docx"
    assert cg.generate_contract(k, str(DESKTOP / "templates" / "contract_template.docx"), str(output))
    document = Document(output)
    text = "\n".join([p.text for p in document.paragraphs] + [c.text for t in document.tables for r in t.rows for c in r.cells])
    assert "Titular istoric" in text and "ISTORIC" in text
    with fitz.open(stream=pdf_reports.render_contract_pdf(k), filetype="pdf") as pdf:
        assert "Titular istoric" in "\n".join(p.get_text() for p in pdf)
    assert db.get_contract(k) == before
