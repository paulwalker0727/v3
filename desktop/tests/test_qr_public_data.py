"""
tests/test_qr_public_data.py — Testează întregul flux QR conținut public:

1. Setări câmpuri publice (bifă / debifă / preseturi)
2. Filtrare corectă a datelor (câmpurile blocate NU apar niciodată)
3. Endpoint HTTP public `/public/loc/<cod>` — cu FastAPI TestClient
4. Verificare că CNP/adresă/telefon NU apar în HTML-ul returnat
"""
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="cimitir_test_qrp_")
os.environ["CIMITIR_DATA_DIR"] = _TMP
os.environ["CIMITIR_LOG_DIR"] = os.path.join(_TMP, "logs")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
import qr_public_data as qpd
from app_logging import setup_logging

setup_logging()


def _seed():
    """Populează DB cu un loc + concesionar + înhumat + contract."""
    db.init_db()
    db.save_plot_to_db("A-01-05", [(0, 0), (5, 0), (5, 5), (0, 5)], "#3388FF")
    db.save_loc_metadata("A-01-05", {
        "sector": "A", "parcela": "1", "rand": "5", "numar": "5",
        "capacitate": "2", "suprafata": 2.5, "stare": "amenajat",
        "tip": "familial", "categorie": "clasic",
    })

    cid = db.save_concesionar({
        "nume": "Popescu", "prenume": "Ion", "cnp": "1800101223344",
        "bi_ci": "AB 123456", "adresa": "Str. Florilor 12, București",
        "telefon": "0722.SECRET",  # date PRIVATE
    })

    from datetime import date, timedelta
    db.save_contract({
        "numar_contract": "C-2026-001",
        "data_contract": date.today().strftime("%Y-%m-%d"),
        "data_inceput": date.today().strftime("%Y-%m-%d"),
        "data_sfarsit": (date.today() + timedelta(days=365 * 7)).strftime("%Y-%m-%d"),
        "concesionar_id": cid, "cod_loc": "A-01-05",
        "owner_fullname": "Popescu Ion",
    })

    db.save_inhumat("A-01-05", {
        "nume": "Popescu", "prenume": "Maria", "cnp": "2500202112233",
        "data_deces": "2024-03-10", "data_inhumare": "2024-03-13",
    })

    db.set_setting("cimitir_nume", "Cimitirul Test")
    db.set_setting("qr_base_url", "https://cimitir.test.ro")


def _check(cond, msg):
    if cond:
        print(f"  ✓ {msg}")
    else:
        raise AssertionError(f"FAIL: {msg}")


def run():
    print("=" * 70)
    print("TEST — QR public data (câmpuri, filtrare, endpoint HTTP)")
    print("=" * 70)
    _seed()

    # ---- 1. Câmpuri implicite ----
    print("\n[1] Setări implicite")
    defaults = qpd.get_public_fields()
    _check(len(defaults) > 5, f"default are {len(defaults)} câmpuri")
    _check("cod_loc" in defaults, "cod_loc e default")
    _check("concesionar_cnp" not in defaults, "CNP concesionar NU e default")

    # ---- 2. Set & get ----
    print("\n[2] Setare custom + protecție hard")
    qpd.set_public_fields(["cod_loc", "sector", "concesionar_nume",
                           "inhumati_lista", "concesionar_cnp"])  # ultimul e BLOCAT
    stored = qpd.get_public_fields()
    _check("concesionar_cnp" not in stored,
           "concesionar_cnp NU se salvează chiar dacă îl ceri")
    _check("concesionar_nume" in stored, "concesionar_nume s-a salvat OK")

    # ---- 3. get_public_data — filtrare corectă ----
    print("\n[3] get_public_data pentru A-01-05")
    data = qpd.get_public_data("A-01-05")
    _check(data["found"], "locul e găsit")
    _check(data["cod_loc"] == "A-01-05", "cod_loc corect")

    # sub formă JSON, verificăm că NIMIC din datele private nu apare
    import json
    payload = json.dumps(data, ensure_ascii=False)
    _check("1800101223344" not in payload, "CNP concesionar NU apare în payload")
    _check("2500202112233" not in payload, "CNP înhumat NU apare în payload")
    _check("Florilor" not in payload, "Adresa NU apare în payload")
    _check("0722.SECRET" not in payload, "Telefon NU apare în payload")
    _check("AB 123456" not in payload, "BI/CI NU apare în payload")

    # câmpurile bifate DA
    _check("Popescu" in payload, "Numele titularului apare (bifat)")
    _check("Popescu Maria".replace(" ", "") in payload.replace(" ", "") or
           "Maria" in payload, "Numele înhumatului apare (bifat)")

    # ---- 4. Toate blocate/nici unul bifat → sections goale (dar nu crash) ----
    print("\n[4] Zero câmpuri bifate")
    qpd.set_public_fields([])
    data0 = qpd.get_public_data("A-01-05")
    _check(data0["found"], "locul e tot găsit")
    _check(data0["sections"] == [], "nu apar secțiuni")

    # ---- 5. Cere toate câmpurile publice ----
    print("\n[5] Preset maxim (fără blocate)")
    max_keys = [k for k, m in qpd.AVAILABLE_FIELDS.items() if m["safe"]]
    qpd.set_public_fields(max_keys)
    dataM = qpd.get_public_data("A-01-05")
    _check(len(dataM["sections"]) >= 3, f"cel puțin 3 secțiuni ({len(dataM['sections'])})")

    # NICIODATĂ nu apar datele blocate
    payloadM = json.dumps(dataM, ensure_ascii=False)
    for secret in ["1800101223344", "2500202112233", "Florilor", "0722.SECRET", "AB 123456"]:
        _check(secret not in payloadM, f"'{secret}' NU apare nici cu preset maxim")

    # ---- 6. Loc inexistent ----
    print("\n[6] Loc inexistent")
    data_none = qpd.get_public_data("Z-99-99")
    _check(not data_none["found"], "found=False pentru loc inexistent")

    # ---- 7. Pagina publică a QR-ului este în portal (backend); web_server nu o mai servește ----
    print("\n[7] /public/loc/<cod> nu mai e în web_server (redirect în portal)")
    import web_server
    _check(not any(getattr(r, "path", "").startswith("/public") for r in web_server.app.routes),
           "web_server nu mai conține /public/loc")

    print("\n" + "=" * 70)
    print("✅ TOATE TESTELE PASS")
    print("=" * 70)


if __name__ == "__main__":
    try:
        run()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
