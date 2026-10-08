"""
tests/test_pdf_reports.py — teste end-to-end pentru modulul pdf_reports.

Rulare:
    cd cimitir-desktop
    python tests/test_pdf_reports.py

Testează:
    - contract PDF
    - chitanță PDF
    - adeverință de înhumare PDF
    - situație contracte expirate PDF
    - raport financiar PDF
    - registru înhumați PDF
"""
import os
import sys
import tempfile
import shutil
from datetime import date, timedelta

# rulăm cu date izolat, într-un folder temporar
_TMP = tempfile.mkdtemp(prefix="cimitir_test_pdf_")
os.environ["CIMITIR_DATA_DIR"] = _TMP
os.environ["CIMITIR_LOG_DIR"] = os.path.join(_TMP, "logs")

# adăugăm folderul părinte în path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
import pdf_reports as pr
from app_logging import setup_logging

setup_logging()


def _seed():
    """Populează DB-ul cu date minimale pentru rapoarte."""
    db.init_db()

    # loc de veci
    db.save_plot_to_db("A-01-05", [(0, 0), (10, 0), (10, 10), (0, 10)], "#0078FF")
    db.save_loc_metadata("A-01-05", {
        "sector": "A", "parcela": "1", "rand": "5", "numar": "5",
        "capacitate": "2", "disponibilitate": "1 loc liber",
        "tip": "familial", "stare": "amenajat", "categorie": "clasic",
        "suprafata": 2.5,
    })

    # concesionar
    cid = db.save_concesionar({
        "nume": "Popescu", "prenume": "Ion", "cnp": "1800101223344",
        "bi_ci": "AB 123456", "adresa": "Str. Florilor 12, București",
        "telefon": "0722.123.456", "email": "ion@test.ro",
    })

    # contracte: unul valabil, unul expirat, unul care expiră curând
    today = date.today()
    contract_id = db.save_contract({
        "numar_contract": "C-2026-001",
        "data_contract": today.strftime("%Y-%m-%d"),
        "data_inceput": today.strftime("%Y-%m-%d"),
        "data_sfarsit": (today + timedelta(days=365 * 7)).strftime("%Y-%m-%d"),
        "concesionar_id": cid, "cod_loc": "A-01-05",
        "owner_fullname": "Popescu Ion", "owner_cnp": "1800101223344",
        "owner_address": "Str. Florilor 12, București",
        "owner_id_card_serie_number": "AB 123456",
        "valabilitate": "7 ani",
    })

    expired_id = db.save_contract({
        "numar_contract": "C-2019-042",
        "data_contract": (today - timedelta(days=800)).strftime("%Y-%m-%d"),
        "data_inceput": (today - timedelta(days=800)).strftime("%Y-%m-%d"),
        "data_sfarsit": (today - timedelta(days=30)).strftime("%Y-%m-%d"),
        "cod_loc": "A-01-05", "owner_fullname": "Ionescu Vasile",
    })

    expiring_id = db.save_contract({
        "numar_contract": "C-2020-100",
        "data_contract": (today - timedelta(days=365)).strftime("%Y-%m-%d"),
        "data_inceput": (today - timedelta(days=365)).strftime("%Y-%m-%d"),
        "data_sfarsit": (today + timedelta(days=20)).strftime("%Y-%m-%d"),
        "cod_loc": "A-01-05", "owner_fullname": "Marin Elena",
    })

    # înhumat
    inh_id = db.save_inhumat("A-01-05", {
        "nume": "Popescu", "prenume": "Maria", "cnp": "2500202112233",
        "data_nasterii": "1950-05-15", "data_deces": "2024-03-10",
        "data_inhumare": "2024-03-13", "sex": "F",
        "cauza_deces": "bătrânețe", "religie": "ortodoxă",
        "ultima_adresa": "Str. Florilor 12, București",
        "nr_certificat_deces": "CD-12345/2024",
        "nr_adeverinta_inhumare": "AI-6789/2024",
    })

    # plăți
    p1 = db.add_plata("A-01-05", 500.0, "2024-01-15", tip="taxa anuală",
                      chitanta_nr="CH-100", observatii="prima plată",
                      contract_id=contract_id)
    p2 = db.add_plata("A-01-05", 1500.0, "2024-06-20", tip="concesiune",
                      chitanta_nr="CH-101", contract_id=contract_id)
    p3 = db.add_plata("A-01-05", 200.0, "2025-11-05", tip="întreținere",
                      chitanta_nr="CH-102", contract_id=contract_id)

    # setări
    db.set_setting("cimitir_nume", "Cimitirul Central Test")
    db.set_setting("operator_nume", "Primăria Comunei Test")

    return {
        "contract_id": contract_id,
        "expired_id": expired_id,
        "expiring_id": expiring_id,
        "inhumat_id": inh_id,
        "plata_id": p1,
        "cod_loc": "A-01-05",
    }


def _assert_pdf(data: bytes, name: str, min_size: int = 800):
    assert isinstance(data, bytes), f"{name}: nu-i bytes"
    assert data.startswith(b"%PDF-"), f"{name}: header PDF invalid"
    assert len(data) > min_size, f"{name}: prea mic ({len(data)} bytes)"
    print(f"  ✓ {name}: {len(data):,} bytes")


def run():
    print("=" * 70)
    print("TEST PDF REPORTS — cimitir-desktop")
    print(f"Data dir: {_TMP}")
    print("=" * 70)

    ids = _seed()
    print(f"[seed] contract={ids['contract_id']}, inhumat={ids['inhumat_id']}, "
          f"plata={ids['plata_id']}")

    out_dir = os.path.join(_TMP, "out")
    os.makedirs(out_dir, exist_ok=True)

    # 1. Contract PDF (fără QR)
    data = pr.render_contract_pdf(ids["contract_id"])
    _assert_pdf(data, "contract (fără QR)")
    pr.save_pdf(data, os.path.join(out_dir, "contract.pdf"))

    # 1b. Contract PDF (cu QR)
    data_qr = pr.render_contract_pdf(ids["contract_id"], include_qr=True)
    _assert_pdf(data_qr, "contract (cu QR)")
    assert len(data_qr) > len(data), "PDF-ul cu QR ar trebui să fie mai mare"
    pr.save_pdf(data_qr, os.path.join(out_dir, "contract_qr.pdf"))

    # 2. Chitanță PDF (cu QR)
    data = pr.render_receipt_pdf(ids["plata_id"], include_qr=True)
    _assert_pdf(data, "chitanță (cu QR)")
    pr.save_pdf(data, os.path.join(out_dir, "chitanta_qr.pdf"))

    # 3. Adeverință înhumare PDF (cu QR)
    data = pr.render_burial_certificate_pdf(ids["inhumat_id"], include_qr=True)
    _assert_pdf(data, "adeverință înhumare (cu QR)")
    pr.save_pdf(data, os.path.join(out_dir, "adeverinta_qr.pdf"))

    # 4. Situație contracte expirate PDF
    data = pr.render_expiring_contracts_pdf(days=45, include_qr=True)
    _assert_pdf(data, "contracte expirate (cu QR)")
    pr.save_pdf(data, os.path.join(out_dir, "expirate.pdf"))

    # 5. Raport financiar PDF
    data = pr.render_financial_report_pdf(start="2024-01-01", end="2026-12-31",
                                          include_qr=True)
    _assert_pdf(data, "raport financiar (cu QR)")
    pr.save_pdf(data, os.path.join(out_dir, "financiar.pdf"))

    # 6. Registru înhumați PDF
    data = pr.render_burials_register_pdf(start="2024-01-01", end="2025-12-31",
                                          include_qr=True)
    _assert_pdf(data, "registru înhumați (cu QR)")
    pr.save_pdf(data, os.path.join(out_dir, "registru.pdf"))

    # 6b. Test setare custom qr_base_url
    db.set_setting("qr_base_url", "https://portal.cimitir-test.ro")
    assert pr.get_qr_base_url() == "https://portal.cimitir-test.ro"
    print("  ✓ setare qr_base_url funcționează")

    # 7. Erori așteptate
    print("\n[erori așteptate]")
    try:
        pr.render_contract_pdf(99999)
        raise AssertionError("Ar fi trebuit să dea ValueError")
    except ValueError as e:
        print(f"  ✓ contract inexistent → {e}")
    try:
        pr.render_receipt_pdf(99999)
        raise AssertionError("Ar fi trebuit să dea ValueError")
    except ValueError as e:
        print(f"  ✓ plată inexistentă → {e}")
    try:
        pr.render_burial_certificate_pdf(99999)
        raise AssertionError("Ar fi trebuit să dea ValueError")
    except ValueError as e:
        print(f"  ✓ înhumat inexistent → {e}")

    print("\n" + "=" * 70)
    print(f"✅ TOATE TESTELE PASS. Fișiere PDF: {out_dir}")
    print("=" * 70)
    for f in sorted(os.listdir(out_dir)):
        p = os.path.join(out_dir, f)
        print(f"  {f:25s}  {os.path.getsize(p):>8,} bytes")

    return out_dir


if __name__ == "__main__":
    try:
        out = run()
        print(f"\nPoți verifica fișierele în: {out}")
        # NU ștergem TMP — utilizatorul poate deschide PDF-urile
    except Exception as e:
        import traceback
        traceback.print_exc()
        shutil.rmtree(_TMP, ignore_errors=True)
        sys.exit(1)
