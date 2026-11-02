#!/usr/bin/env python
"""Verificare evidențe (Pasul B) — STRICT CITIRE. Exemplu (PowerShell):

    python tools\\verificare_evidente.py --db "C:\\CIMITIR\\data\\cimitir.db" --out "C:\\Temp\\raport" [--deep] [--no-info]

Nu scrie în baza de date și nu corectează nimic. Cod de ieșire: 0 = fără erori/critice, 1 = există erori/critice, 2 = eroare de utilizare.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
import cimitir_integrity as ci  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Verificare evidențe CIMITIR (doar citire).")
    ap.add_argument("--db", required=True, help="Calea fișierului SQLite al MAIN (se deschide read-only; copia de lucru este recomandată).")
    ap.add_argument("--out", help="Folder pentru raport (JSON, CSV, Markdown). Fără --out se afișează doar sumarul.")
    ap.add_argument("--media-root", help="Folder de bază pentru căi relative de fișiere (implicit: căile din baza de date).")
    ap.add_argument("--deep", action="store_true", help="Recalculează SHA-256 al fișierelor existente (citește fișierele; mai lent).")
    ap.add_argument("--no-info", action="store_true", help="Omite problemele de severitate „informație”.")
    ap.add_argument("--max-per-check", type=int, default=5000, help="Plafon de probleme listate per verificare (sumarul rămâne complet).")
    ap.add_argument("--only", help="Categorii separate prin virgulă: " + ",".join(ci.CATEGORIES))
    a = ap.parse_args(argv)
    try:
        cats = [x.strip() for x in a.only.split(",")] if a.only else None
        if cats and any(c not in ci.CATEGORIES for c in cats):
            print("Categorie necunoscută în --only.", file=sys.stderr)
            return 2

        def prog(i, n, label):
            print(f"[{i}/{n}] {label}", file=sys.stderr)

        rep = ci.run_on_file(a.db, deep=a.deep, media_root=a.media_root, include_info=not a.no_info, max_per_code=a.max_per_check,
                             categories=cats, progress=prog)
    except (OSError, ValueError) as exc:
        print(f"Eroare: {exc}", file=sys.stderr)
        return 2
    s = rep.summary()
    print("Sumar:", ", ".join(f"{ci.model.SEVERITY_RO[k]}={s['by_severity'][k]}" for k in ci.SEVERITIES), f"| total={s['total']}")
    print("Baza nemodificată:", rep.meta["db_unchanged"])
    if rep.meta["check_errors"]:
        print("Erori interne:", *rep.meta["check_errors"], sep="\n  ", file=sys.stderr)
    if a.out:
        for k, p in ci.write_all(rep, a.out).items():
            print(f"{k}: {p}")
    return 1 if (s["by_severity"]["critical"] or s["by_severity"]["error"]) else 0


if __name__ == "__main__":
    sys.exit(main())
