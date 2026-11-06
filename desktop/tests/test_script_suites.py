"""Suitele scrise ca scripturi (`run()` + `__main__`, fără funcții `test_*`) nu erau colectate de pytest ("no tests ran") — deci nu rulau niciodată.
Acest fișier le rulează în subproces (izolat: scriptul își setează propriul CIMITIR_DATA_DIR) și pică dacă ies cu cod != 0."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
SCRIPTS = ["test_qr_labels", "test_qr_public_data", "test_pdf_reports", "test_license", "test_qr_scanning"]


def _zbar_error():
    try:
        from pyzbar.pyzbar import decode  # noqa: F401
        return ""
    except ImportError as exc:
        return str(exc)


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_suite_exits_zero(name):
    if name == "test_qr_scanning" and _zbar_error():
        pytest.skip(f"decodor QR indisponibil ({_zbar_error()}); vezi docs/QR_DEPENDENTE.md")
    env = {k: v for k, v in os.environ.items() if not k.startswith("CIMITIR_")}
    env["QT_QPA_PLATFORM"] = "offscreen"
    p = subprocess.run([sys.executable, str(TESTS / f"{name}.py")], env=env, cwd=str(TESTS.parent), capture_output=True, text=True, timeout=240)
    assert p.returncode == 0, (p.stdout[-2000:] + p.stderr[-2000:])
