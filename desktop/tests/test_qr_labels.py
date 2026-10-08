"""
tests/test_qr_labels.py — Test standalone pentru modulul qr_labels.

Testează:
    - PNG individual (cu text și doar QR)
    - Batch PNG (mai multe locuri)
    - PDF etichete A4 (grid tipăribil)
    - Toate presetele de etichete
"""
import os
import sys
import tempfile
import shutil
from datetime import date

_TMP = tempfile.mkdtemp(prefix="cimitir_test_qr_")
os.environ["CIMITIR_DATA_DIR"] = _TMP
os.environ["CIMITIR_LOG_DIR"] = os.path.join(_TMP, "logs")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
import qr_labels as ql
from app_logging import setup_logging

setup_logging()


def _seed():
    """Populează DB cu câteva locuri."""
    db.init_db()
    # 12 locuri, 2 sectoare
    locs = []
    for sec in ("A", "B"):
        for p in range(1, 4):
            for n in range(1, 3):
                cod = f"{sec}-{p:02d}-{n:02d}"
                db.save_plot_to_db(cod, [(0, 0), (5, 0), (5, 5), (0, 5)], "#3388FF")
                db.save_loc_metadata(cod, {
                    "sector": sec, "parcela": str(p), "rand": str(n), "numar": str(n),
                    "stare": "amenajat", "tip": "familial",
                })
                locs.append(cod)
    db.set_setting("qr_base_url", "https://cimitir.test.ro")
    return locs


def _assert_png(path: str, name: str, min_size: int = 500):
    assert os.path.exists(path), f"{name}: fișier lipsă"
    size = os.path.getsize(path)
    assert size > min_size, f"{name}: fișier prea mic ({size} bytes)"
    with open(path, "rb") as f:
        head = f.read(8)
    assert head.startswith(b"\x89PNG"), f"{name}: nu e PNG valid"
    print(f"  ✓ {name}: {size:,} bytes")


def _assert_pdf(path: str, name: str, min_size: int = 2000):
    assert os.path.exists(path), f"{name}: fișier lipsă"
    size = os.path.getsize(path)
    assert size > min_size, f"{name}: fișier prea mic ({size} bytes)"
    with open(path, "rb") as f:
        head = f.read(5)
    assert head == b"%PDF-", f"{name}: nu e PDF valid"
    print(f"  ✓ {name}: {size:,} bytes")


def run():
    print("=" * 70)
    print("TEST QR LABELS — cimitir-desktop")
    print(f"Data dir: {_TMP}")
    print("=" * 70)

    locs = _seed()
    print(f"[seed] {len(locs)} locuri: {locs[:3]}...{locs[-1]}")

    out_dir = os.path.join(_TMP, "out")
    os.makedirs(out_dir, exist_ok=True)

    # 1. PNG individual cu text
    p1 = os.path.join(out_dir, "qr_A-01-01_full.png")
    ql.generate_qr_png("A-01-01", p1, include_text=True, only_qr=False)
    _assert_png(p1, "PNG individual (cu text)")

    # 2. PNG individual doar QR
    p2 = os.path.join(out_dir, "qr_A-01-01_only.png")
    ql.generate_qr_png("A-01-01", p2, include_text=False, only_qr=True)
    _assert_png(p2, "PNG individual (doar QR)")

    # 3. Batch — 5 locuri
    batch_dir = os.path.join(out_dir, "batch")
    paths = ql.generate_qr_batch_pngs(locs[:5], batch_dir, include_text=True)
    assert len(paths) == 5, f"așteptam 5 PNG, am primit {len(paths)}"
    for p in paths:
        _assert_png(p, os.path.basename(p))

    # 4. PDF etichete — toate presetele
    for preset in ql.LABEL_PRESETS:
        safe = preset.replace("/", "-").replace("×", "x").replace(" ", "_").replace("(", "").replace(")", "")
        pdf_path = os.path.join(out_dir, f"labels_{safe}.pdf")
        ql.generate_qr_labels_pdf(locs, pdf_path, preset=preset, include_text=True)
        _assert_pdf(pdf_path, f"PDF etichete [{preset}]")

    # 5. PDF fără text (doar QR pe grid)
    pdf_notext = os.path.join(out_dir, "labels_notext.pdf")
    ql.generate_qr_labels_pdf(locs[:6], pdf_notext,
                              preset="2x5 (10/pag, 105×59)",
                              include_text=False, border=False)
    _assert_pdf(pdf_notext, "PDF etichete fără text")

    # 6. Erori așteptate
    print("\n[erori așteptate]")
    try:
        ql.generate_qr_labels_pdf(locs, os.path.join(out_dir, "err.pdf"),
                                  preset="INEXISTENT")
        raise AssertionError("Ar fi trebuit să dea ValueError")
    except ValueError as e:
        print(f"  ✓ preset invalid → {e}")

    try:
        ql.generate_qr_labels_pdf([], os.path.join(out_dir, "err2.pdf"))
        raise AssertionError("Ar fi trebuit să dea ValueError pentru listă goală")
    except ValueError as e:
        print(f"  ✓ listă goală → {e}")

    # 7. Verificare URL în QR
    print("\n[verificare URL în QR]")
    import pdf_reports as pr
    assert pr.get_qr_base_url() == "https://cimitir.test.ro"
    url = pr._build_qr_url("loc", "A-01-01")
    assert url == "https://cimitir.test.ro/loc/A-01-01", url
    print(f"  ✓ URL corect: {url}")

    print("\n" + "=" * 70)
    print(f"✅ TOATE TESTELE PASS. Fișiere generate în:")
    print(f"   {out_dir}")
    print("=" * 70)
    total = 0
    for root, _, files in os.walk(out_dir):
        for f in sorted(files):
            p = os.path.join(root, f)
            print(f"   {os.path.relpath(p, out_dir):40s}  {os.path.getsize(p):>8,} bytes")
            total += 1
    print(f"\nTotal: {total} fișiere generate.")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
