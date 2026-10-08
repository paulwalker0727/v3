"""
Teste pentru:
    1. Fix bug: harta mică + zoom blocat după login (map_view.py)
    2. Feature nou: Generator QR Personalizat (qr_custom_generator.py, qr_custom_dialog.py)

Rulare:
    cd /app/cimitir-desktop
    QT_QPA_PLATFORM=offscreen CIMITIR_DATA_DIR=/tmp/cimitir_test_data \
        python -m pytest tests/test_bugfix_and_qrcustom.py -v
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CIMITIR_DATA_DIR", tempfile.mkdtemp(prefix="cimitir_test_"))

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest  # noqa: E402
from PIL import Image  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture: seed DB
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module", autouse=True)
def _seed():
    import database as db
    db.init_db()
    db.save_plot_to_db("A-01-05", [(10, 10), (50, 10), (50, 50), (10, 50)], "#0078FF")
    db.save_loc_metadata("A-01-05", {
        "sector": "A", "parcela": "1", "rand": "5", "numar": "3",
        "suprafata": 4.5, "capacitate": "2", "tip": "Simplu",
        "stare": "Ocupat", "categorie": "Individual",
        "disponibilitate": "Ocupat",
    })
    cid = db.save_concesionar({
        "nume": "Popescu", "prenume": "Ion", "cnp": "1801201123456",
        "adresa": "Str. Test 5", "telefon": "0721234567",
        "email": "ion@ex.ro", "bi_ci": "BB123456",
    })
    db.save_contract({
        "cod_loc": "A-01-05", "numar_contract": "CT/2020/123",
        "data_contract": "2020-05-15", "data_inceput": "2020-05-15",
        "data_sfarsit": "2027-05-15", "valabilitate": "7 ani",
        "concesionar_id": cid,
    })
    db.save_inhumat("A-01-05", {
        "nume": "Popescu", "prenume": "Maria",
        "data_nasterii": "1945-01-01", "data_deces": "2018-03-20",
        "data_inhumare": "2018-03-22", "profesie": "Profesoara",
    })
    yield


# ---------------------------------------------------------------------------
# 1) FIX BUG — harta mică + zoom blocat
# ---------------------------------------------------------------------------
class TestMapZoomBugFix:
    """Verificăm fix-ul pentru bug-ul „harta apare mică și nu pot face zoom"."""

    def _mkmap(self, path: str, w: int = 5000, h: int = 3000):
        Image.new("RGB", (w, h), (200, 220, 240)).save(path)

    def _new_view(self):
        # importuri lazy — QApplication trebuie creat înainte
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        from map_view import CemeteryMapView
        return CemeteryMapView(), app

    def test_constants_are_safe(self):
        v, _ = self._new_view()
        assert v._min_scale == 0.01, "min_scale trebuie coborât la 0.01 pentru a nu bloca zoom-ul"
        assert v._max_scale == 30.0
        assert v._min_viewport_for_fit == 200, "avem prag pt viewport prea mic"

    def test_try_fit_ignores_tiny_viewport_without_force(self):
        v, _ = self._new_view()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            self._mkmap(f.name)
            v.resize(100, 100)  # viewport mic (ca la login în splitter)
            v.load_map_file(f.name)
        # pending_fit ar trebui să rămână True (viewport prea mic)
        # SAU să fie deja aplicat cu scale >= min_scale
        s = v.transform().m11()
        assert s > 0
        # Chiar dacă a fost aplicat, scale-ul NU trebuie să fie sub min_scale
        assert s >= v._min_scale, f"scale {s} sub min_scale {v._min_scale} — blochează zoom-ul!"

    def test_force_fit_always_produces_zoomable_scale(self):
        v, _ = self._new_view()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            self._mkmap(f.name, 5000, 3000)
            v.resize(100, 100)
            v.load_map_file(f.name)
            v._pending_fit = True  # pentru certitudine
            v._try_fit_content(force=True)
        s = v.transform().m11()
        assert s >= v._min_scale, f"scale {s} sub minim!"
        # și un zoom-in ulterior trebuie să crească scale-ul
        v.scale(1.15, 1.15)
        s2 = v.transform().m11()
        assert s2 > s, "zoom-in trebuie să funcționeze după force fit"

    def test_fit_manual_works_at_normal_size(self):
        v, _ = self._new_view()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            self._mkmap(f.name)
            v.resize(1500, 850)
            v.load_map_file(f.name)
            v.fit_to_content()
        s = v.transform().m11()
        assert 0.05 < s < 5.0, f"scale rezultat {s} — trebuie să fie normal"

    def test_wheel_event_does_not_lock_when_out_of_range(self):
        """
        Reproducerea exactă a bug-ului raportat: dacă scale-ul curent e sub
        min_scale (fit greșit), wheelEvent VECHI bloca și zoom-in. FIX: acum
        permite direcția care aduce scale-ul înapoi în interval.
        """
        v, _ = self._new_view()
        # Forțăm o stare degenerată: scale < min_scale
        v.scale(0.001, 0.001)  # scale extrem de mic (sub 0.01)
        old = v.transform().m11()
        assert old < v._min_scale

        # simulare wheel-in
        from PySide6.QtCore import Qt, QPointF, QPoint
        from PySide6.QtGui import QWheelEvent
        ev = QWheelEvent(
            QPointF(50, 50), QPointF(50, 50),
            QPoint(0, 0), QPoint(0, 120),  # angleDelta.y() > 0 -> zoom in
            Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False,
        )
        v.wheelEvent(ev)
        new = v.transform().m11()
        assert new > old, f"zoom-in trebuie să funcționeze chiar și cu scale mic (era {old}, acum {new})"


# ---------------------------------------------------------------------------
# 2) FEATURE — QR Custom Generator
# ---------------------------------------------------------------------------
class TestQrCustomGenerator:

    def test_fields_defined(self):
        import qr_custom_generator as qcg
        # câmpurile-cheie trebuie să existe
        assert "cod_loc" in qcg.FIELDS
        assert "conc_cnp" in qcg.FIELDS
        assert qcg.FIELDS["conc_cnp"]["sensitive"] is True
        assert qcg.FIELDS["cod_loc"]["sensitive"] is False
        assert "contract_nr" in qcg.FIELDS
        assert "inh_lista" in qcg.FIELDS

    def test_build_payload_default(self):
        import qr_custom_generator as qcg
        p = qcg.build_payload("A-01-05", qcg.DEFAULT_KEYS)
        assert p["cod_loc"] == "A-01-05"
        assert "loc" in p
        assert p["loc"]["Sector"] == "A"
        assert "concesionar" in p
        assert p["concesionar"]["Nume titular"] == "Popescu"
        assert "contract" in p
        assert "inhumati" in p

    def test_build_payload_with_sensitive(self):
        import qr_custom_generator as qcg
        p = qcg.build_payload("A-01-05", ["cod_loc", "conc_cnp", "conc_telefon"])
        assert p["concesionar"]["CNP titular"] == "1801201123456"
        assert p["concesionar"]["Telefon titular"] == "0721234567"

    def test_build_payload_empty_when_no_keys(self):
        import qr_custom_generator as qcg
        p = qcg.build_payload("A-01-05", [])
        assert p["cod_loc"] == "A-01-05"
        assert "loc" not in p
        assert "concesionar" not in p

    def test_payload_to_text_readable(self):
        import qr_custom_generator as qcg
        p = qcg.build_payload("A-01-05", qcg.DEFAULT_KEYS)
        t = qcg.payload_to_text(p)
        assert "Loc de veci: A-01-05" in t
        assert "Popescu" in t
        assert "— Loc" in t

    def test_payload_to_json_valid(self):
        import json
        import qr_custom_generator as qcg
        p = qcg.build_payload("A-01-05", qcg.DEFAULT_KEYS)
        s = qcg.payload_to_json(p)
        parsed = json.loads(s)
        assert parsed["cod_loc"] == "A-01-05"

    def test_generate_and_save_png(self):
        import qr_custom_generator as qcg
        out = os.path.join(tempfile.gettempdir(), "test_qr_out.png")
        p, encoded = qcg.generate_and_save_png(
            "A-01-05", qcg.DEFAULT_KEYS, out, format="text")
        assert os.path.exists(p)
        assert os.path.getsize(p) > 500
        assert len(encoded) > 50

    def test_default_keys_persistence(self):
        import qr_custom_generator as qcg
        keys = ["cod_loc", "sector", "conc_nume"]
        qcg.save_default_keys(keys)
        loaded = qcg.get_saved_default_keys()
        assert set(loaded) == set(keys)

    def test_build_qr_pixmap_size_info(self):
        import qr_custom_generator as qcg
        png, data, byte_len = qcg.build_qr_pixmap(
            "A-01-05", qcg.DEFAULT_KEYS, format="text", side_px=250)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert byte_len == len(data.encode("utf-8"))


# ---------------------------------------------------------------------------
# 2b) FEATURE — Semnătură HMAC (anti-falsificare)
# ---------------------------------------------------------------------------
class TestHmacSignature:

    def test_secret_is_persistent(self):
        import qr_custom_generator as qcg
        s1 = qcg.get_hmac_secret()
        s2 = qcg.get_hmac_secret()
        assert s1 == s2
        assert len(s1) == 64  # 32 bytes hex

    def test_text_signed_verify_valid(self):
        import qr_custom_generator as qcg
        p = qcg.build_payload("A-01-05", qcg.DEFAULT_KEYS)
        signed = qcg.payload_to_text(p, sign=True)
        assert qcg.SIG_MARKER in signed
        assert "sig:" in signed
        assert "gen:" in signed
        res = qcg.verify_signed(signed)
        assert res["signed"] is True
        assert res["valid"] is True
        assert res["format"] == "text"
        assert res["gen"]  # ISO timestamp existent

    def test_text_signed_verify_tampered(self):
        """Modificăm datele → verificarea trebuie să eșueze."""
        import qr_custom_generator as qcg
        p = qcg.build_payload("A-01-05", qcg.DEFAULT_KEYS)
        signed = qcg.payload_to_text(p, sign=True)
        tampered = signed.replace("Popescu", "Ionescu", 1)
        res = qcg.verify_signed(tampered)
        assert res["signed"] is True
        assert res["valid"] is False
        assert "modificate" in res["error"].lower() or "nu se potrivește" in res["error"].lower()

    def test_json_signed_verify_valid(self):
        import qr_custom_generator as qcg
        p = qcg.build_payload("A-01-05", qcg.DEFAULT_KEYS)
        signed = qcg.payload_to_json(p, sign=True)
        assert qcg.JSON_SIG_KEY in signed
        assert qcg.JSON_GEN_KEY in signed
        res = qcg.verify_signed(signed)
        assert res["signed"] is True
        assert res["valid"] is True
        assert res["format"] == "json"

    def test_json_signed_verify_tampered(self):
        import qr_custom_generator as qcg, json
        p = qcg.build_payload("A-01-05", qcg.DEFAULT_KEYS)
        signed = qcg.payload_to_json(p, sign=True)
        d = json.loads(signed)
        d["cod_loc"] = "X-99-99"  # falsificăm codul
        tampered = json.dumps(d, ensure_ascii=False, separators=(",", ":"))
        res = qcg.verify_signed(tampered)
        assert res["valid"] is False

    def test_unsigned_qr_detected(self):
        import qr_custom_generator as qcg
        p = qcg.build_payload("A-01-05", qcg.DEFAULT_KEYS)
        unsigned = qcg.payload_to_text(p, sign=False)
        res = qcg.verify_signed(unsigned)
        assert res["signed"] is False
        assert res["valid"] is False

    def test_generate_and_save_png_signed(self):
        import qr_custom_generator as qcg
        out = os.path.join(tempfile.gettempdir(), "test_qr_signed.png")
        p, encoded = qcg.generate_and_save_png(
            "A-01-05", qcg.DEFAULT_KEYS, out, format="text", sign=True)
        assert os.path.exists(p)
        assert qcg.SIG_MARKER in encoded
        # verifică
        res = qcg.verify_signed(encoded)
        assert res["valid"] is True

    def test_rotate_secret_invalidates_old_qrs(self):
        import qr_custom_generator as qcg
        p = qcg.build_payload("A-01-05", ["cod_loc", "sector"])
        old_signed = qcg.payload_to_text(p, sign=True)
        assert qcg.verify_signed(old_signed)["valid"] is True
        # rotim secretul
        qcg.rotate_hmac_secret()
        # QR-ul vechi devine invalid
        assert qcg.verify_signed(old_signed)["valid"] is False
        # QR-ul NOU cu secretul rotit e valid
        new_signed = qcg.payload_to_text(p, sign=True)
        assert qcg.verify_signed(new_signed)["valid"] is True


# ---------------------------------------------------------------------------
# 3) UI — Dialog integration
# ---------------------------------------------------------------------------
class TestQrCustomDialog:
    def test_dialog_instantiation_and_presets(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        from qr_custom_dialog import QrCustomDialog
        import qr_custom_generator as qcg

        dlg = QrCustomDialog()
        # trebuie să conțină checkbox pentru fiecare câmp definit
        for k in qcg.FIELDS.keys():
            assert k in dlg._checkboxes, f"lipsește checkbox pentru {k}"

        # test preset „min"
        dlg._apply_preset("min")
        checked = [k for k, cb in dlg._checkboxes.items() if cb.isChecked()]
        assert "cod_loc" in checked
        assert "conc_cnp" not in checked

        # test preset „all"
        dlg._apply_preset("all")
        checked = [k for k, cb in dlg._checkboxes.items() if cb.isChecked()]
        assert set(checked) == set(qcg.FIELDS.keys())

        # test preset „full" (fără sensitive)
        dlg._apply_preset("full")
        checked = [k for k, cb in dlg._checkboxes.items() if cb.isChecked()]
        for k in checked:
            assert qcg.FIELDS[k]["sensitive"] is False


# ---------------------------------------------------------------------------
# 4) UI Menu — verificare integrare toolbar
# ---------------------------------------------------------------------------
class TestMenuIntegration:

    def test_open_qr_custom_dialog_method_exists(self):
        import inspect
        with open(os.path.join(_ROOT, "ui_mainwindow.py"), encoding="utf-8") as f:
            src = f.read()
        assert "open_qr_custom_dialog" in src
        assert "act_qr_custom" in src
        assert "🎫 QR Personalizat" in src
        # verifică că QrCustomDialog e importat corect
        assert "from qr_custom_dialog import QrCustomDialog" in src

    def test_map_view_has_new_features(self):
        with open(os.path.join(_ROOT, "map_view.py"), encoding="utf-8") as f:
            src = f.read()
        assert "_min_viewport_for_fit" in src
        assert "def resizeEvent" in src
        assert "force: bool = False" in src
