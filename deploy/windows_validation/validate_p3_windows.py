"""validate_p3_windows.py — validare P3 pe Windows, 100% SINTETICĂ (folder temporar). Raport PASS / FAIL / MANUAL / NEEXECUTAT.

Nu citește și nu scrie datele reale: setează CIMITIR_DATA_DIR într-un folder temporar. Nu se conectează la Cloud/Supabase.
Rulare (din rădăcina repository-ului, cu Python-ul aplicației):  python deploy\\windows_validation\\validate_p3_windows.py [--json raport.json]
Pe alt sistem decât Windows, verificările specifice Windows sunt marcate NEEXECUTAT.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PASS, FAIL, MANUAL, NEEXEC = "PASS", "FAIL", "MANUAL", "NEEXECUTAT"
rows = []


def add(cid, status, title, detail=""):
    rows.append({"id": cid, "status": status, "title": title, "detail": detail})
    print(f"[{status}] {cid} {title}" + (f" — {detail}" if detail else ""))


def check(cid, title, fn):
    try:
        detail = fn()
        add(cid, PASS, title, detail or "")
    except Exception as exc:  # noqa: BLE001
        add(cid, FAIL, title, f"{type(exc).__name__}: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    tmp = tempfile.mkdtemp(prefix="cimitir_p3_")
    os.environ.update(CIMITIR_DATA_DIR=tmp, CIMITIR_DB_ENGINE="sqlite", CIMITIR_MODE="local", DATABASE_URL="",
                      CIMITIR_ALLOW_DEFAULT_USERS="1", QT_QPA_PLATFORM=os.environ.get("QT_QPA_PLATFORM", "offscreen"))
    for p in (ROOT / "desktop", ROOT / "shared"):
        sys.path.insert(0, str(p))
    is_win = sys.platform == "win32"
    add("W00", PASS if is_win else NEEXEC, "Sistem de operare Windows", sys.platform)

    import database as db
    db.DB_PATH = str(Path(tmp) / "cimitir ț test.db")      # cale cu spații și diacritice
    check("W01", "init_db pe cale cu spații/diacritice", lambda: db.init_db())

    import cimitir_auth as auth
    import access_session

    def roles():
        auth.init_auth()
        uid = auth.create_user("usr_w", "Parola-test-123", auth.ROLE_USER)
        access_session.set_user(auth.get_session_user(uid))
        db.set_access_guard(access_session.can)
        db.save_plot_to_db("W-1", [(0, 0), (1, 0), (1, 1)], "#fff")
        assert db.save_inhumat("W-1", {"nume": "Test"}), "user nu poate adăuga"
        iid = db.save_inhumat("W-1", {"nume": "Test2"})
        assert db.save_inhumat("W-1", {"nume": "X"}, inhumat_id=iid) is None, "user poate modifica"
        assert db.delete_inhumat(iid) is False, "user poate șterge"
        db.set_access_guard(None)
        access_session.set_user(None)
    check("W02", "Roluri: user = consultare + adăugare", roles)

    def snapshot_contract():
        cid = db.save_concesionar({"nume": "Popescu", "prenume": "Ana", "cnp": "2000000000001"})
        k = db.save_contract({"numar_contract": "W-1", "cod_loc": "W-1", "concesionar_id": cid,
                              "owner_fullname": "Popescu Ana", "owner_cnp": "2000000000001"})
        db.save_concesionar({"nume": "Schimbat", "cnp": "9"}, concesionar_id=cid)
        db.save_contract({"numar_contract": "W-1", "cod_loc": "W-1", "concesionar_id": cid, "observatii": "x"}, contract_id=k)
        c = db.get_contract(k)
        assert c["owner_fullname"] == "Popescu Ana" and c["owner_cnp"] == "2000000000001"
        return k
    k = None
    try:
        k = snapshot_contract()
        add("W03", PASS, "Instantaneul owner_* se păstrează la editare")
    except Exception as exc:  # noqa: BLE001
        add("W03", FAIL, "Instantaneul owner_* se păstrează la editare", str(exc))

    def pay_and_pdf():
        import pdf_reports as pr
        pid = db.add_plata("W-1", 100, "2024-01-01", "taxă", "W-R1", "obs <b>neînchis & ț", contract_id=k)
        assert pid and pr.render_receipt_pdf(pid)[:4] == b"%PDF"
        assert pr.render_contract_pdf(k)[:4] == b"%PDF"
        out = Path(tmp) / "chitanță test ț.pdf"
        out.write_bytes(pr.render_receipt_pdf(pid))
        return str(out)
    check("W04", "Plată pe contract + PDF cu diacritice/markup, salvat pe cale Windows", pay_and_pdf)

    def word():
        import contract_generator as cg
        tpl = ROOT / "desktop" / "templates" / "contract_template.docx"
        out = Path(tmp) / "contract ț.docx"
        assert cg.generate_contract(k, str(tpl), str(out)) and out.stat().st_size > 1000
    check("W05", "Contract Word generat", word)

    def migrate_hist():
        fixtures = ROOT / "desktop" / "tests" / "fixtures" / "historic_schemas"
        for f in sorted(fixtures.glob("*.sql")):
            p = Path(tmp) / f"mig_{f.stem}.db"
            conn = sqlite3.connect(p)
            conn.executescript(f.read_text(encoding="utf-8"))
            conn.close()
            old = db.DB_PATH
            db.DB_PATH = str(p)
            try:
                db.init_db()
                db.init_db()
                c = sqlite3.connect(p)
                assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                c.close()
            finally:
                db.DB_PATH = old
        return f"{len(list(fixtures.glob('*.sql')))} scheme istorice sintetice"
    check("W06", "Migrare + a doua rulare pe scheme istorice sintetice (copii în temp)", migrate_hist)

    def qthread():
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        import field_worker
        assert field_worker.wait_for_tasks(15000)
    check("W07", "Închidere cu QThread: wait_for_tasks fără task blocat", qthread)

    def sched():
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        import backup_window
        cmd = backup_window.scheduler_command("03:00")
        assert cmd.startswith("schtasks /Create") and "/SC DAILY /ST 03:00" in cmd, cmd
        return cmd[:160]
    check("W08", "Comanda Task Scheduler construită corect (doar construire, nu se creează)", sched)
    add("W09", MANUAL, "Backup programat: creați taskul din MAIN → Copii de rezervă → Programare; verificați în Task Scheduler că rulează și produce fișier",
        "Nu creez task în sistem din script.")
    add("W10", MANUAL, "Pornire MAIN, autentificare, hartă, contract salvat/redeschis, plată, PDF/Word din interfață, închidere cu operațiune activă",
        "Pași în docs/P3_VALIDARE_WINDOWS.md.")
    add("W11", MANUAL, "Actualizare instalare existentă pe date sintetice (copie a folderului de date)", "Vezi docs/P3_VALIDARE_WINDOWS.md §3.")

    if args.json:
        Path(args.json).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [r for r in rows if r["status"] == FAIL]
    print(f"\nTotal: {len(rows)} | PASS {sum(r['status'] == PASS for r in rows)} | FAIL {len(failed)} | "
          f"MANUAL {sum(r['status'] == MANUAL for r in rows)} | NEEXECUTAT {sum(r['status'] == NEEXEC for r in rows)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
