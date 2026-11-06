"""P2 §7: benchmark pe baze SINTETICE (date fictive, folder temporar). Nu atinge date reale.

Rulare:  python scripts/p2_benchmark.py [--sizes 1000 10000 50000] [--json out.json]
Măsoară (ms) funcțiile din stratul de date folosite de ferestrele Locuri, Concesionari, Înhumați, Contracte, Hartă, Istoric, documente PDF.
"""
import argparse
import json
import os
import random
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("shared", "desktop"):
    sys.path.insert(0, str(ROOT / sub))


def build(path: str, n: int) -> None:
    import database as db
    db.DB_PATH = path
    db.init_db()
    rnd = random.Random(7)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    pts = "[[0,0],[4,0],[4,3],[0,3]]"
    conn.executemany("INSERT INTO locuri_de_veci (cod_loc, points, color, sector, parcela, rand, numar, suprafata, uid) VALUES (?,?,?,?,?,?,?,?,?)",
                     [(f"S{i % 20}-{i // 20:05d}", pts, "#fff", f"S{i % 20}", str(i % 50), str(i % 30), str(i), 6.0, f"uid-{i}") for i in range(n)])
    conn.executemany("INSERT INTO concesionari (nume, prenume, cnp, adresa) VALUES (?,?,?,?)",
                     [(f"Nume{i:06d}", f"Prenume{i % 500}", f"{2000000000000 + i}", f"Str. Fictivă {i}") for i in range(n)])
    conn.executemany("INSERT INTO contracte (numar_contract, data_contract, data_inceput, data_sfarsit, concesionar_id, id_loc, cod_loc, owner_fullname) VALUES (?,?,?,?,?,?,?,?)",
                     [(f"{i}/2020", "01.01.2020", "01.01.2020", f"01.01.{2025 + i % 30}", i + 1, i + 1, f"S{i % 20}-{i // 20:05d}", f"Nume{i:06d}") for i in range(n)])
    conn.executemany("INSERT INTO inhumati (nume, prenume, cnp, data_inhumare, id_loc) VALUES (?,?,?,?,?)",
                     [(f"Defunct{i:06d}", f"P{i % 300}", f"{1000000000000 + i}", "02.02.2015", (i % n) + 1) for i in range(int(n * 1.5))])
    conn.executemany("INSERT INTO plati (cod_loc, contract_id, suma, data_plata, chitanta_nr) VALUES (?,?,?,?,?)",
                     [(f"S{i % 20}-{i // 20:05d}", i + 1, 100.0, "2020-01-01", f"R{i}") for i in range(n)])
    conn.commit()
    conn.close()


def timeit(fn, repeat=3):
    runs = []
    for _ in range(repeat):
        t = time.perf_counter()
        fn()
        runs.append((time.perf_counter() - t) * 1000)
    return round(statistics.median(runs), 1)


def measure(path: str, n: int) -> dict:
    import database as db
    import entity_links  # noqa: F401
    db.DB_PATH = path
    res = {"n_locuri": n}
    res["pornire_init_db"] = timeit(db.init_db)
    res["fereastra_locuri_list_locuri"] = timeit(db.list_locuri)
    res["fereastra_concesionari_load"] = timeit(db.load_concesionari)
    res["cautare_persoana_nume_inhumat"] = timeit(lambda: db.load_all_inhumati({"nume": f"Defunct{n // 2:06d}"}))
    res["cautare_persoana_cnp_inhumat"] = timeit(lambda: db.load_all_inhumati({"cnp": f"{1000000000000 + n // 2}"}))
    res["cautare_contract_dupa_numar"] = timeit(lambda: db.load_contracte(f"WHERE numar_contract = '{n // 2}/2020'"))
    res["contracte_toate"] = timeit(db.load_contracte)
    res["harta_load_poligoane"] = timeit(db.map_rows)
    res["istoric_plati_loc"] = timeit(lambda: db.list_plati(f"S1-{n // 40:05d}"))
    res["total_plati_global"] = timeit(db.total_plati)
    res["contracte_care_expira"] = timeit(lambda: db.expiring_contracts(365))
    res["inhumati_pentru_loc"] = timeit(lambda: db.load_inhumati_for_loc(f"S1-{n // 40:05d}"))
    try:
        import fisa_pdf
        res["generare_fisa_pdf_loc"] = timeit(lambda: fisa_pdf.build_fisa_pdf(f"S1-{n // 40:05d}"), 2)
    except Exception as exc:  # noqa: BLE001
        res["generare_fisa_pdf_loc"] = f"eroare: {exc}"
    try:
        import pdf_reports
        res["generare_contract_pdf"] = timeit(lambda: pdf_reports.render_contract_pdf(n // 2), 2)
        res["raport_contracte_expirate_pdf"] = timeit(lambda: pdf_reports.render_expiring_contracts_pdf(365), 1)
    except Exception as exc:  # noqa: BLE001
        res["generare_contract_pdf"] = f"eroare: {exc}"
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=[1000, 10000, 50000])
    ap.add_argument("--json")
    a = ap.parse_args()
    os.environ.setdefault("CIMITIR_DB_ENGINE", "sqlite")
    out = []
    for n in a.sizes:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CIMITIR_DATA_DIR"] = tmp
            path = str(Path(tmp) / "bench.db")
            t = time.perf_counter()
            build(path, n)
            r = measure(path, n)
            r["build_s"] = round(time.perf_counter() - t, 1)
            r["db_mb"] = round(Path(path).stat().st_size / 1e6, 1)
            out.append(r)
            print(json.dumps(r, ensure_ascii=False), flush=True)
    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
