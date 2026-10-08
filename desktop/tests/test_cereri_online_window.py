"""
tests/test_cereri_online_window.py — Fereastra desktop „Cereri online" (offscreen).

Verifică:
1. Fereastra se încarcă și listează cererile din `cereri_concesionare`
2. La „Validează" se creează concesionarul din datele cererii (fără reintroducere)
3. Legarea automată de loc: se creează un contract draft legat de cod_loc
4. După validare, statusul cererii devine 'validat' și count_cereri_noi scade
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="cimitir_test_cereri_win_")
os.environ["CIMITIR_DATA_DIR"] = _TMP
os.environ["CIMITIR_LOG_DIR"] = os.path.join(_TMP, "logs")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import pytest
import database as db
from PySide6.QtWidgets import QApplication, QMessageBox


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    return a


@pytest.fixture()
def seeded():
    db.init_db()
    import access_session
    import cimitir_auth
    cimitir_auth.init_auth()
    user, _ = cimitir_auth.authenticate_ex("admin", "admin123")
    access_session.set_user(user)
    conn = db.get_connection()
    conn.execute("INSERT OR IGNORE INTO locuri_de_veci (cod_loc, points, sector, parcela, rand, numar, disponibilitate, ocupat) "
                 "VALUES ('A-9-9', ?, 'A', '9', '9', '9', 'liber', 0)",
                 (json.dumps([[0, 0], [5, 0], [5, 5], [0, 5]]),))
    conn.commit()
    conn.close()
    cid = db.add_cerere_concesionare({
        "nume": "Georgescu", "prenume": "Ana", "cnp": "2950101223344",
        "bi_ci": "AB 999888", "adresa": "Str. Lalelelor 3", "telefon": "0733111222",
        "email": "ana@x.ro", "grad_rudenie": "soție", "observatii": "urgent",
        "cod_loc": "A-9-9",
    })
    # atașează un document fals
    tmpf = os.path.join(_TMP, "ci_fake.png")
    with open(tmpf, "wb") as f:
        f.write(b"\x89PNG-fake")
    db.add_document("cerere", cid, "cerere_doc", tmpf, caption="CI Ana")
    return cid


def test_window_lists_cereri(app, seeded):
    from cereri_online_window import CereriOnlineWindow
    w = CereriOnlineWindow(parent=None)
    # filtrul implicit = 'nou' → cererea trebuie să apară
    assert w.table.rowCount() >= 1
    ids = [w.table.item(r, 0).data(256 + 0) for r in range(w.table.rowCount())]  # Qt.UserRole == 256
    assert seeded in ids
    w.deleteLater()


def test_validate_creates_concesionar_and_contract(app, seeded, monkeypatch):
    from cereri_online_window import CereriOnlineWindow
    # evită dialogurile modale
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

    before = db.count_cereri_noi()
    w = CereriOnlineWindow(parent=None)
    w._validate(seeded)

    cerere = db.get_cerere_concesionare(seeded)
    assert cerere["status"] == "validat"
    assert cerere["concesionar_id"]
    conc = db.get_concesionar(cerere["concesionar_id"])
    assert conc["nume"] == "Georgescu" and conc["cnp"] == "2950101223344"

    # contract draft legat de locul solicitat
    contracte = [c for c in db.load_contracte() if c.get("cod_loc") == "A-9-9"
                 and c.get("concesionar_id") == cerere["concesionar_id"]]
    assert contracte, "Trebuia creat un contract draft legat de loc"

    # documentele scanate din cerere au fost copiate în dosarul concesionarului + contract
    conc_docs = db.list_documents("concesionar", cerere["concesionar_id"])
    assert any(d.get("doc_type") == "buletin" for d in conc_docs)
    assert db.list_documents("contract", contracte[0]["id"]), "Documentul trebuia copiat și la contract"

    assert db.count_cereri_noi() == before - 1
    w.deleteLater()


def test_loc_availability(app, seeded):
    from cereri_online_window import CereriOnlineWindow
    w = CereriOnlineWindow(parent=None)
    av = w._loc_availability("A-9-9")
    assert av["exists"] is True
    assert av["status"] in ("liber", "ocupat", "rezervat")
    assert w._loc_availability("ZZZ-000")["exists"] is False
    w.deleteLater()


def test_reject_sets_status(app, seeded, monkeypatch):
    from cereri_online_window import CereriOnlineWindow
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("motiv test", True))
    w = CereriOnlineWindow(parent=None)
    w._reject(seeded)
    assert db.get_cerere_concesionare(seeded)["status"] == "respins"
    w.deleteLater()


def test_operator_without_approve_right_cannot_decide(app, seeded, monkeypatch):
    import access_session
    import cimitir_auth
    from cereri_online_window import CereriOnlineWindow
    from PySide6.QtWidgets import QInputDialog
    cimitir_auth.create_user("doar_editare", "parola-lunga-1", "operator", rights=["edit"])
    user, _ = cimitir_auth.authenticate_ex("doar_editare", "parola-lunga-1")
    access_session.set_user(user)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a))
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("motiv", True))
    w = CereriOnlineWindow(parent=None)
    w._reject(seeded)
    w._validate(seeded)
    assert warned and db.get_cerere_concesionare(seeded)["status"] == "nou"
    w.deleteLater()


def test_request_more_sets_completari_and_reject_requires_reason(app, seeded, monkeypatch):
    from cereri_online_window import CereriOnlineWindow
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Lipsește CI", True))
    w = CereriOnlineWindow(parent=None)
    w._request_more(seeded)
    assert db.get_cerere_concesionare(seeded)["status"] == "completari"
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("  ", True))
    w._reject(seeded)
    assert db.get_cerere_concesionare(seeded)["status"] == "completari"
    w.deleteLater()
