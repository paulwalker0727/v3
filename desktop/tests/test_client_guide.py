"""Teste pentru generatorul de ghid PDF (iteration 4)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _run_guide() -> Path:
    """Rulează tools/make_client_guide.py și întoarce calea PDF-ului."""
    script = PROJECT_ROOT / "tools" / "make_client_guide.py"
    assert script.exists(), f"Scriptul lipsește: {script}"
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(PROJECT_ROOT),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, (
        f"Scriptul a eșuat: rc={result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    return PROJECT_ROOT / "docs" / "Ghid_Instalare_Cimitir.pdf"


class TestClientGuidePDF:
    def test_generates_valid_pdf(self):
        out = _run_guide()
        assert out.exists(), "PDF-ul nu a fost generat"
        size = out.stat().st_size
        assert size > 10 * 1024, f"PDF prea mic ({size} bytes)"
        with open(out, "rb") as fh:
            head = fh.read(4)
        assert head == b"%PDF", f"Header PDF invalid: {head!r}"

    def test_multi_page(self):
        """Se așteaptă cel puțin 3 pagini."""
        out = _run_guide()
        # Încercare cu PyPDF2 / pypdf dacă e disponibil
        pages = None
        try:
            from pypdf import PdfReader  # type: ignore
            pages = len(PdfReader(str(out)).pages)
        except Exception:
            try:
                from PyPDF2 import PdfReader  # type: ignore
                pages = len(PdfReader(str(out)).pages)
            except Exception:
                pytest.skip("Nici pypdf, nici PyPDF2 nu sunt instalate — sar peste numărare pagini")
        assert pages is not None and pages >= 3, f"Prea puține pagini: {pages}"

    def test_idempotent_regeneration(self):
        first = _run_guide()
        assert first.exists()
        s1 = first.stat().st_size
        # a doua rulare nu trebuie să eșueze și trebuie să suprascrie
        second = _run_guide()
        assert second.exists()
        s2 = second.stat().st_size
        # aceeași dimensiune aproximativă (timestamp intern poate diferi
        # cu câțiva octeți în funcție de dată, dar structura e identică)
        assert abs(s1 - s2) < 4096, (
            f"Regenerarea produce dimensiuni foarte diferite: {s1} vs {s2}"
        )
