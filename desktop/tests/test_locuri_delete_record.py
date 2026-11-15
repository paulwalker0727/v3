"""Regression for undefined db in LocuriWindow.delete_record; temporary fictional SQLite."""
from types import SimpleNamespace

import pytest
from test_p4_interconectare import env, db, dump


def window(env):
    from PySide6.QtWidgets import QWidget
    from locuri_window import LocuriWindow
    emitted, calls = [], []
    def delete(cod):
        calls.append(cod)
        return db.delete_plot(cod)
    parent = QWidget()
    parent.actions = SimpleNamespace(search_locuri=db.search_locuri, load_loc_by_cod=db.load_loc_by_cod,
                                     delete_plot_from_db=delete)
    parent.signals = SimpleNamespace(data_changed=SimpleNamespace(emit=lambda *args: emitted.append(args)))
    w = LocuriWindow(parent)
    w.table.selectRow(next(i for i in range(w.table.rowCount()) if w.table.item(i, 1).text() == "A-01"))
    return parent, w, emitted, calls


def test_delete_empty_plot_emits_refresh_only_after_success(env):
    parent, w, emitted, calls = window(env)
    # Invoke directly so an exception in the Qt slot cannot be swallowed.
    w.delete_record()
    assert calls == ["A-01"] and emitted == [("loc", "A-01")]
    assert not db.load_loc_by_cod("A-01")
    assert w.table.rowCount() == 2
    assert env.msgs["information"]


@pytest.mark.parametrize("dependency", ["contract", "payment"])
def test_plot_with_history_is_preserved_before_confirmation(env, dependency):
    if dependency == "contract":
        db.save_contract({"numar_contract": "TEST", "cod_loc": "A-01", "concesionar_id": env.ion})
    else:
        db.add_plata("A-01", 10, "2026-01-01", "taxa", "TEST")
    parent, w, emitted, calls = window(env)
    before = dump("locuri_de_veci")
    w.delete_record()
    assert dump("locuri_de_veci") == before
    assert not calls and not emitted and not env.msgs["question"]
    assert "istoric" in env.msgs["warning"][-1]


def test_cancel_delete_keeps_plot(env):
    from PySide6.QtWidgets import QMessageBox
    env.mp.setattr(QMessageBox, "question", lambda *a: QMessageBox.No)
    parent, w, emitted, calls = window(env)
    w.delete_record()
    assert db.load_loc_by_cod("A-01") and not emitted and not calls


def test_database_refusal_uses_db_message_without_name_error(env):
    parent, w, emitted, calls = window(env)
    db.set_access_guard(lambda right: False)
    w.delete_record()
    assert db.load_loc_by_cod("A-01")
    assert calls == ["A-01"] and not emitted
    assert env.msgs["warning"][-1] == db.last_refusal()
    assert not env.msgs["information"] and w.table.rowCount() == 3


def test_no_selection_does_not_delete(env):
    parent, w, emitted, calls = window(env)
    w.table.setCurrentCell(-1, -1)
    w.delete_record()
    assert not calls and not emitted and env.msgs["warning"]
