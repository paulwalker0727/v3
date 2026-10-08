"""
tests/test_qr_custom_and_map.py — pytest suite for:
  * map_view.CemeteryMapView zoom-recovery fix (harta mica la login)
  * qr_custom_generator (build_payload / encoders / QR png / settings)
  * qr_custom_dialog.QrCustomDialog (instantiation, checkboxes, presets)
  * ui_mainwindow toolbar action "QR Personalizat"
"""
import os
import sys
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="cimitir_test_qrcustom_")
os.environ["CIMITIR_DATA_DIR"] = _TMP
os.environ["CIMITIR_LOG_DIR"] = os.path.join(_TMP, "logs")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402
import qr_custom_generator as qcg  # noqa: E402

COD = "TEST_A-01-01"


# ---------------------------------------------------------------- fixtures
@pytest.fixture(scope="session", autouse=True)
def seed_db():
    db.init_db()
    db.save_plot_to_db(COD, [(0, 0), (5, 0), (5, 5), (0, 5)], "#3388FF")
    db.save_loc_metadata(COD, {
        "sector": "A", "parcela": "1", "rand": "1", "numar": "1",
        "suprafata": 7.5, "capacitate": 2, "tip": "simplu",
        "categorie": "concesionat", "stare": "bun",
        "disponibilitate": "ocupat", "nr_vechi": "V12", "numar_cf": "CF999",
    })
    cid = db.save_concesionar({
        "nume": "TESTPOPESCU", "prenume": "Ion", "cnp": "1800101123456",
        "bi_ci": "XT123456", "adresa": "Str. Test 1", "telefon": "0700000000",
        "email": "test@example.test",
    })
    assert cid, "save_concesionar failed"
    ctr = db.save_contract({
        "cod_loc": COD, "numar_contract": "TEST-777",
        "data_contract": "2020-05-10", "data_inceput": "2020-05-10",
        "data_sfarsit": "2045-05-10", "valabilitate": "25 ani",
        "concesionar_id": cid,
    })
    assert ctr, "save_contract failed"
    inh = db.save_inhumat(COD, {
        "nume": "TESTPOPESCU", "prenume": "Maria", "cnp": "2500101123456",
        "data_nasterii": "1950-01-01", "data_deces": "2021-02-03",
        "data_inhumare": "2021-02-05", "profesie": "profesor",
        "religie": "ortodox",
    })
    assert inh, "save_inhumat failed"
    yield
    db.delete_plot(COD)


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    return app


# ---------------------------------------------------- qr_custom_generator
class TestBuildPayload:
    def test_all_sections_present(self):
        keys = list(qcg.FIELDS.keys())
        p = qcg.build_payload(COD, keys)
        assert p["cod_loc"] == COD
        for section in ("loc", "concesionar", "contract", "inhumati"):
            assert section in p, f"missing section {section}"
        assert p["loc"]["Sector"] == "A"
        assert p["concesionar"]["Nume titular"] == "TESTPOPESCU"
        assert p["concesionar"]["CNP titular"] == "1800101123456"
        assert p["contract"]["Nr. contract"] == "TEST-777"
        assert p["contract"]["Status contract (valabil/expirat)"] == "Valabil"
        assert isinstance(p["inhumati"], list) and len(p["inhumati"]) == 1
        assert p["inhumati"][0]["Lista înhumaților (nume + prenume)"] == "TESTPOPESCU Maria"
        assert p["inhumati"][0]["Data decesului"] == "2021-02-03"

    def test_only_selected_keys(self):
        p = qcg.build_payload(COD, ["cod_loc", "sector"])
        assert "concesionar" not in p and "contract" not in p and "inhumati" not in p
        assert set(p["loc"].keys()) == {"Cod loc", "Sector"}

    def test_empty_keys(self):
        p = qcg.build_payload(COD, [])
        assert p == {"cod_loc": COD}

    def test_unknown_loc_does_not_crash(self):
        p = qcg.build_payload("TEST_NOPE-99", list(qcg.FIELDS.keys()))
        assert p["cod_loc"] == "TEST_NOPE-99"
        assert "loc" not in p or p["loc"] == {}


class TestEncoders:
    def test_text_output(self):
        p = qcg.build_payload(COD, list(qcg.FIELDS.keys()))
        txt = qcg.payload_to_text(p)
        assert txt.startswith(f"Loc de veci: {COD}")
        for h in ("— Loc", "— Titular", "— Contract", "— Înhumați"):
            assert h in txt
        assert "TESTPOPESCU" in txt

    def test_json_output(self):
        import json
        p = qcg.build_payload(COD, qcg.DEFAULT_KEYS)
        s = qcg.payload_to_json(p)
        back = json.loads(s)
        assert back == p


class TestQrImages:
    def test_make_qr_image_levels(self):
        for lvl in ("L", "M", "Q", "H"):
            img = qcg.make_qr_image("hello TEST", ec_level=lvl)
            assert img.mode == "RGB" and img.size[0] > 0

    def test_generate_and_save_png_text_and_json(self, tmp_path):
        from PIL import Image
        for fmt in ("text", "json"):
            out = str(tmp_path / f"qr_{fmt}.png")
            path, data = qcg.generate_and_save_png(
                COD, qcg.DEFAULT_KEYS, out, format=fmt, title="TEST")
            assert os.path.exists(path) and os.path.getsize(path) > 1000
            im = Image.open(path)
            im.verify()
            assert data and COD in data

    def test_qr_decodes_back(self):
        """Verify QR is machine readable (if a decoder is available)."""
        payload = qcg.build_payload(COD, ["cod_loc", "sector", "conc_nume"])
        data = qcg.payload_to_text(payload)
        img = qcg.make_qr_image(data)
        try:
            from pyzbar.pyzbar import decode  # type: ignore
        except Exception:
            pytest.skip("no QR decoder installed (pyzbar)")
        res = decode(img)
        assert res and res[0].data.decode("utf-8") == data

    def test_build_qr_pixmap(self):
        png, data, length = qcg.build_qr_pixmap(COD, qcg.DEFAULT_KEYS, format="json")
        assert png[:4] == b"\x89PNG"
        assert length == len(data.encode("utf-8")) > 0

    def test_large_payload_all_fields(self, tmp_path):
        """Full field selection must still encode (QR capacity check)."""
        out = str(tmp_path / "qr_all.png")
        path, data = qcg.generate_and_save_png(
            COD, list(qcg.FIELDS.keys()), out, format="text", ec_level="H")
        assert os.path.exists(path)
        assert len(data) > 100


class TestSettingsPersistence:
    def test_default_keys_roundtrip(self):
        db.set_setting(qcg.SETTING_KEY, "")
        assert qcg.get_saved_default_keys() == list(qcg.DEFAULT_KEYS)
        qcg.save_default_keys(["cod_loc", "conc_cnp", "bogus_key"])
        got = qcg.get_saved_default_keys()
        assert got == ["cod_loc", "conc_cnp"]
        assert db.get_setting(qcg.SETTING_KEY) == "cod_loc,conc_cnp"
        # empty save falls back to DEFAULT_KEYS
        qcg.save_default_keys([])
        assert qcg.get_saved_default_keys() == list(qcg.DEFAULT_KEYS)


# --------------------------------------------------------------- map_view
class TestMapViewZoomFix:
    def test_constants(self, qapp):
        from map_view import CemeteryMapView
        v = CemeteryMapView()
        assert v._min_scale == 0.01
        assert v._max_scale == 30.0
        assert v._min_viewport_for_fit == 200

    def test_small_viewport_then_zoom_works(self, qapp, tmp_path):
        """Reproduce login scenario: tiny viewport + huge map -> zoom must work."""
        from PySide6.QtGui import QPixmap
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtGui import QWheelEvent
        from map_view import CemeteryMapView

        big = tmp_path / "big.png"
        pm = QPixmap(5000, 3000)
        pm.fill(Qt.gray)
        assert pm.save(str(big))

        v = CemeteryMapView()
        v.resize(100, 100)
        v.load_map_file(str(big))
        vp = v.viewport()
        print(f"viewport after resize(100,100): {vp.width()}x{vp.height()}, "
              f"pending_fit={v._pending_fit}, scale={v.transform().m11()}")
        if vp.width() < v._min_viewport_for_fit or vp.height() < v._min_viewport_for_fit:
            # sync attempt must be skipped (viewport under threshold, no force)
            assert v._pending_fit is True, "fit should stay pending for tiny viewport"

        v._pending_fit = True
        v._try_fit_content(force=True)
        s = v.transform().m11()
        assert s >= v._min_scale, f"scale {s} below min_scale -> zoom would be blocked"

        def wheel(delta):
            ev = QWheelEvent(QPointF(50, 50), v.mapToGlobal(QPoint(50, 50)),
                             QPoint(0, 0), QPoint(0, delta),
                             Qt.NoButton, Qt.NoModifier,
                             Qt.NoScrollPhase, False)
            v.wheelEvent(ev)

        before = v.transform().m11()
        wheel(120)
        after = v.transform().m11()
        assert after > before, f"zoom-in did not increase scale ({before} -> {after})"

        # zoom-out then zoom-in again (recovery policy)
        for _ in range(30):
            wheel(-120)
        low = v.transform().m11()
        wheel(120)
        assert v.transform().m11() > low, "zoom-in blocked after deep zoom-out"

    def test_zoom_out_clamped_but_recoverable(self, qapp, tmp_path):
        from PySide6.QtGui import QPixmap, QWheelEvent
        from PySide6.QtCore import QPoint, QPointF, Qt
        from map_view import CemeteryMapView
        p = tmp_path / "m.png"
        pm = QPixmap(800, 600)
        pm.fill(Qt.white)
        pm.save(str(p))
        v = CemeteryMapView()
        v.resize(900, 700)
        v.load_map_file(str(p))
        v._try_fit_content(force=True)
        assert not v._pending_fit and v._did_initial_fit

        def wheel(delta):
            v.wheelEvent(QWheelEvent(QPointF(10, 10), v.mapToGlobal(QPoint(10, 10)),
                                     QPoint(0, 0), QPoint(0, delta),
                                     Qt.NoButton, Qt.NoModifier,
                                     Qt.NoScrollPhase, False))
        for _ in range(60):
            wheel(-120)
        assert v.transform().m11() > 0
        for _ in range(60):
            wheel(120)
        assert v.transform().m11() <= v._max_scale * v._zoom_factor

    def test_fit_to_content_forces(self, qapp, tmp_path):
        from PySide6.QtGui import QPixmap
        from PySide6.QtCore import Qt
        from map_view import CemeteryMapView
        p = tmp_path / "m2.png"
        pm = QPixmap(4000, 2500)
        pm.fill(Qt.darkGray)
        pm.save(str(p))
        v = CemeteryMapView()
        v.resize(120, 120)
        v.load_map_file(str(p))
        v.fit_to_content()
        assert v._did_initial_fit is True
        assert v.transform().m11() >= v._min_scale


# ------------------------------------------------------- qr_custom_dialog
class TestQrCustomDialog:
    def test_instantiate_and_presets(self, qapp):
        from qr_custom_dialog import QrCustomDialog
        d = QrCustomDialog(current_user="admin")
        assert len(d._checkboxes) == len(qcg.FIELDS)

        d._apply_preset("all")
        assert set(d._current_keys()) == set(qcg.FIELDS.keys())

        d._apply_preset("min")
        assert set(d._current_keys()) == {"cod_loc", "sector", "parcela", "rand", "numar"}

        d._apply_preset("default")
        assert set(d._current_keys()) == set(qcg.DEFAULT_KEYS)

        d._apply_preset("full")
        sens = {k for k, m in qcg.FIELDS.items() if m.get("sensitive")}
        assert not (set(d._current_keys()) & sens)

        d._set_all(False)
        assert d._current_keys() == []

    def test_preview_refresh_with_real_loc(self, qapp):
        from qr_custom_dialog import QrCustomDialog
        d = QrCustomDialog(current_user="admin")
        d._preview_cod = COD
        d._apply_preset("all")   # triggers _refresh_preview internally
        d._refresh_preview()

    def test_export_current_writes_png(self, qapp, tmp_path, monkeypatch):
        from qr_custom_dialog import QrCustomDialog
        d = QrCustomDialog(current_user="admin")
        d._preview_cod = COD
        d._apply_preset("default")
        out = str(tmp_path / "dialog_export.png")
        import qr_custom_dialog as mod
        monkeypatch.setattr(mod.QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: (out, "PNG")))
        monkeypatch.setattr(mod.QMessageBox, "information",
                            staticmethod(lambda *a, **k: None))
        monkeypatch.setattr(mod.QMessageBox, "warning",
                            staticmethod(lambda *a, **k: None))
        # NOTE: _export_current ends with a modal QMessageBox.question ->
        # must be patched, otherwise the call blocks forever in a headless run.
        monkeypatch.setattr(mod.QMessageBox, "question",
                            staticmethod(lambda *a, **k: mod.QMessageBox.No))
        monkeypatch.setattr(d, "_open_path", lambda *a, **k: None)
        d._export_current()
        assert os.path.exists(out), "dialog export did not create PNG"


# --------------------------------------------------------- ui_mainwindow
class TestMainWindowIntegration:
    def test_action_and_method_exist(self):
        src = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "ui_mainwindow.py"), encoding="utf-8").read()
        assert "QR Personalizat" in src
        assert "act_qr_custom.triggered.connect(self.open_qr_custom_dialog)" in src
        assert "def open_qr_custom_dialog(self)" in src

    def test_import_compiles(self):
        import py_compile
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for f in ("ui_mainwindow.py", "qr_custom_dialog.py",
                  "qr_custom_generator.py", "map_view.py"):
            py_compile.compile(os.path.join(base, f), doraise=True)
