#!/usr/bin/env python3
"""Verificare sincronizare REALĂ Mac <-> Windows pe un mediu Cloud TEST, cu date FICTIVE (marcate SYNCTEST-…). Rulează identic pe Mac și pe Windows.

Premise (făcute de tine, nu de script): fiecare calculator are o bază LOCALĂ GOALĂ, separată de cea de lucru (alt CIMITIR_DATA_DIR) și este asociat din
aplicație („Asociază cu Cloud") la ACELAȘI cimitir din mediul Cloud TEST. Scriptul nu citește și nu afișează chei; folosește legătura salvată de aplicație.

Protecții (refuz înainte de orice scriere): URL-ul configurat trebuie să fie IDENTIC cu --test-url și să nu conțină proiectul de producție; este necesar
--confirm-test-cloud; baza locală trebuie să nu conțină alte date decât cele SYNCTEST (altfel refuz, ca să nu urce date reale; --allow-existing-data le urcă explicit).

Scenariu complet (în ordine):
  Mac:      python scripts/macos/verify_cross_sync.py --role mac     --phase write       --test-url URL --confirm-test-cloud
  Windows:  python scripts\\macos\\verify_cross_sync.py --role windows --phase check       --test-url URL --confirm-test-cloud   # vede datele Mac?
  Windows:  ... --role windows --phase write
  Mac:      ... --role mac     --phase check                                              # vede datele Windows?
  Offline:  Mac fără internet: --phase write-local  (rămân în coadă) -> reconectezi -> --phase flush -> Windows: --phase check
  Final:    --phase cleanup (șterge doar datele SYNCTEST proprii; ștergerile se propagă ca tombstone)
Cod de ieșire: 0 = PASS, 1 = verificare eșuată, 2 = configurare/protecție (nu s-a scris nimic).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "desktop", ROOT / "shared"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

PREFIX = "SYNCTEST"
ROLES = ("mac", "windows")
PHASES = ("write", "write-local", "flush", "check", "cleanup")


class Refused(Exception):
    pass


def _norm(url: str) -> str:
    return (url or "").strip().rstrip("/").lower()


def marker(role: str, kind: str, tag: str = "") -> str:
    return f"{PREFIX}-{role.upper()}-{kind}" + (f"-{tag}" if tag else "")


def check_target(test_url: str, configured_url: str, has_key: bool, confirmed: bool) -> None:
    import cimitir_test_guard as guard
    if not confirmed:
        raise Refused("lipsește --confirm-test-cloud")
    if not test_url:
        raise Refused("lipsește --test-url (adresa Cloud TEST așteptată)")
    if guard.has_protected_ref(test_url) or guard.has_protected_ref(configured_url):
        raise Refused("adresa indică proiectul de producție (blocat permanent)")
    if not configured_url or not has_key:
        raise Refused("aplicația nu este asociată cu un Cloud (lipsește adresa sau legătura MAIN)")
    if _norm(configured_url) != _norm(test_url):
        raise Refused("adresa configurată în aplicație diferă de --test-url; nu scriu nimic")


def foreign_rows(conn) -> int:
    """Rânduri care NU sunt date SYNCTEST (loc/concesionar/contract/înhumat/plată/aviz)."""
    q = {"locuri_de_veci": "cod_loc", "concesionari": "nume", "contracte": "numar_contract"}
    n = 0
    for table, col in q.items():
        n += conn.execute(f"SELECT COUNT(*) FROM {table} WHERE COALESCE({col},'') NOT LIKE ?", (PREFIX + "-%",)).fetchone()[0]
    for table in ("inhumati", "plati", "amenajari_avize"):
        n += conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return n


def _rows(db, sql: str, params: tuple = ()) -> List[dict]:
    conn = db.get_connection()
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def own_markers(db, role: str) -> Dict[str, List[dict]]:
    like = f"{PREFIX}-{role.upper()}-%"
    return {"loc": _rows(db, "SELECT id, cod_loc FROM locuri_de_veci WHERE cod_loc LIKE ?", (like,)),
            "concesionar": _rows(db, "SELECT id, nume FROM concesionari WHERE nume LIKE ?", (like,)),
            "contract": _rows(db, "SELECT id, numar_contract FROM contracte WHERE numar_contract LIKE ?", (like,))}


def create_markers(db, role: str, tag: Optional[str] = None) -> Dict[str, object]:
    tag = tag or time.strftime("%Y%m%d%H%M%S")
    cod, nume, nr = marker(role, "LOC", tag), marker(role, "CONC", tag), marker(role, "CTR", tag)
    if db.save_plot_to_db(cod, [(0, 0), (10, 0), (10, 10), (0, 10)], "#ff8800") is None:
        raise RuntimeError("nu am putut crea locul de test")
    cid = db.save_concesionar({"nume": nume, "prenume": "Fictiv", "cnp": "", "adresa": "Date fictive de test"})
    ctr = db.save_contract({"numar_contract": nr, "cod_loc": cod, "concesionar_id": cid})
    if not (cid and ctr):
        raise RuntimeError("nu am putut crea concesionarul/contractul de test")
    return {"tag": tag, "cod_loc": cod, "concesionar": nume, "contract": nr}


def verify_other_side(db, other_role: str) -> Dict[str, object]:
    """Datele SYNCTEST ale celuilalt calculator, văzute local, cu legăturile contract -> loc/concesionar rezolvate."""
    like = f"{PREFIX}-{other_role.upper()}-%"
    locs = _rows(db, "SELECT id, cod_loc FROM locuri_de_veci WHERE cod_loc LIKE ?", (like,))
    conc = _rows(db, "SELECT id, nume FROM concesionari WHERE nume LIKE ?", (like,))
    ctr = _rows(db, "SELECT c.numar_contract, c.id_loc, c.concesionar_id, l.cod_loc AS loc_cod, k.nume AS conc_nume FROM contracte c "
                    "LEFT JOIN locuri_de_veci l ON l.id=c.id_loc LEFT JOIN concesionari k ON k.id=c.concesionar_id WHERE c.numar_contract LIKE ?", (like,))
    linked = [c for c in ctr if (c["loc_cod"] or "").startswith(like[:-1]) and (c["conc_nume"] or "").startswith(like[:-1])]
    return {"locuri": len(locs), "concesionari": len(conc), "contracte": len(ctr), "contracte_legate": len(linked),
            "ok": bool(locs and conc and ctr and len(linked) == len(ctr))}


def sync_state(es) -> Dict[str, int]:
    ops = es.pending_entity_ops(("pending", "conflict", "error"))
    return {"in_asteptare": sum(1 for o in ops if (o.get("state") or "pending") == "pending"),
            "conflicte": sum(1 for o in ops if o.get("state") == "conflict"), "erori": sum(1 for o in ops if o.get("state") == "error")}


def run_phase(phase: str, role: str, db, es, allow_existing: bool = False, log: Callable[[str], None] = print) -> int:
    other = "windows" if role == "mac" else "mac"
    db.init_db()
    db.set_setting("cloud_entity_sync", db.get_setting("cloud_entity_sync") or "1")
    conn = db.get_connection()
    try:
        foreign = foreign_rows(conn)
    finally:
        conn.close()
    if foreign and phase in ("write", "write-local", "flush") and not allow_existing:
        raise Refused(f"baza locală conține {foreign} înregistrări care nu sunt SYNCTEST; folosește o bază goală de test (sau --allow-existing-data)")
    es.ensure_tracking(force=True)
    code = 0
    if phase in ("write", "write-local"):
        info = create_markers(db, role)
        log(f"[{role}] create local: {info}")
    if phase in ("write", "flush"):
        ok, lines, counts = es.flush_entities()
        for line in lines:
            log(f"  {line}")
        st = sync_state(es)
        log(f"[{role}] trimitere: ok={ok} {counts} coadă={st}")
        code = 0 if ok and not any(st.values()) else 1
    if phase == "write-local":
        log(f"[{role}] modificări păstrate local în coadă: {sync_state(es)} (rulează --phase flush când revine conexiunea)")
    if phase == "check":
        ok, line, changed = es.pull_entities()
        log(f"[{role}] descărcare: ok={ok} modificări={changed} {line}")
        res = verify_other_side(db, other)
        st = sync_state(es)
        log(f"[{role}] date {other.upper()} văzute aici: {res}; coada proprie: {st}")
        code = 0 if ok and res["ok"] and not st["conflicte"] and not st["erori"] else 1
    if phase == "cleanup":
        mine = own_markers(db, role)
        conn = db.get_connection()
        try:
            for table, col in (("contracte", "numar_contract"), ("concesionari", "nume"), ("locuri_de_veci", "cod_loc")):
                conn.execute(f"DELETE FROM {table} WHERE {col} LIKE ?", (f"{PREFIX}-{role.upper()}-%",))
            conn.commit()
        finally:
            conn.close()
        ok, _lines, _c = es.flush_entities()
        st = sync_state(es)
        log(f"[{role}] curățare: șterse local {sum(len(v) for v in mine.values())} rânduri; trimise ca ștergeri: ok={ok} coadă={st}")
        code = 0 if ok and not any(st.values()) else 1
    log("REZULTAT: " + ("PASS" if code == 0 else "EȘUAT"))
    return code


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--role", required=True, choices=ROLES)
    ap.add_argument("--phase", required=True, choices=PHASES)
    ap.add_argument("--test-url", default="")
    ap.add_argument("--confirm-test-cloud", action="store_true")
    ap.add_argument("--allow-existing-data", action="store_true")
    args = ap.parse_args(argv)
    os.chdir(ROOT / "desktop")
    import database as db
    import entity_sync as es
    import sync_v2
    try:
        url, key, _src = sync_v2.connection()
        check_target(args.test_url, url, bool(key), args.confirm_test_cloud)
        print(f"Cloud TEST: {url} | baza locală: {db.current_db_path()}")
        return run_phase(args.phase, args.role, db, es, args.allow_existing_data)
    except Refused as exc:
        print(f"REFUZAT: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
