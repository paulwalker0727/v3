"""Verificare READ-ONLY a fluxului MAIN -> cloud TEST, fără date personale/secrete.

Din rădăcina proiectului (cu Python-ul MAIN instalat):
    py scripts\\diagnose_mobile_v2_local.py
sau indică explicit baza registrului activ:
    py scripts\\diagnose_mobile_v2_local.py --db "C:\\cale\\registru.db"

Nu deschide conexiuni către cloud, nu aplică migrări, nu modifică baza locală.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def discover_database() -> Path:
    sys.path.insert(0, str(ROOT / "desktop"))
    import database as db  # noqa: PLC0415
    return Path(db.current_db_path())


def analyze(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError("Baza registrului nu a fost găsită. Folosește --db pentru baza MAIN corectă.")
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        present = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        tables = {}
        for t in ("locuri_de_veci", "sectoare", "concesionari", "contracte", "inhumati", "plati", "amenajari_avize", "photos", "documents"):
            if t not in present:
                tables[t] = {"present": False}
                continue
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({t})")}
            d = {"present": True, "rows": int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])}
            if "uid" in cols:
                d["without_uid"] = int(conn.execute(f"SELECT COUNT(*) FROM {t} WHERE COALESCE(uid,'')='' ").fetchone()[0])
            if "version" in cols:
                d["never_pushed"] = int(conn.execute(f"SELECT COUNT(*) FROM {t} WHERE COALESCE(version,0)=0").fetchone()[0])
            if t in ("locuri_de_veci", "sectoare") and "points" in cols:
                drawn = 0
                for row in conn.execute(f"SELECT points FROM {t}"):
                    try:
                        p = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                        if isinstance(p, list) and len(p) >= 3 and all(isinstance(x, list) and len(x) >= 2 for x in p):
                            drawn += 1
                    except (TypeError, ValueError):
                        pass
                d["drawn_polygons"] = drawn
            if t in ("locuri_de_veci", "concesionari", "contracte", "inhumati", "plati", "amenajari_avize"):
                d["sync_capture_triggers"] = sum(1 for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=? AND name LIKE 'cs_%'", (t,)))
            tables[t] = d
        pending = {}
        if "cloud_sync_outbox" in present:
            for r in conn.execute("SELECT COALESCE(kind,'nedefinit') AS kind,COALESCE(state,'pending') AS state,COUNT(*) AS n "
                                  "FROM cloud_sync_outbox GROUP BY COALESCE(kind,'nedefinit'),COALESCE(state,'pending')"):
                pending[f"{r['kind']}:{r['state']}"] = int(r["n"])
        settings = {}
        if "settings" in present:
            # Allowlist strict: nu raportăm chei de sincronizare, date personale, host, URL sau erori brut.
            allowed = ("cloud_sync_auto", "cloud_entity_sync_enabled", "cloud_entity_sync", "cloud_sync_last",
                       "cloud_entity_last_pull", "sync_map_version")
            for name in allowed:
                r = conn.execute("SELECT value FROM settings WHERE key=?", (name,)).fetchone()
                settings[name] = str(r[0]) if r and r[0] is not None else None
            r = conn.execute("SELECT value FROM settings WHERE key='cloud_sync_last_error'").fetchone()
            settings["has_last_error"] = bool(r and r[0])
        conclusions = []
        graves = tables.get("locuri_de_veci", {})
        if not graves.get("present"):
            conclusions.append("Lipsește tabelul local locuri_de_veci: verifică registrul MAIN ales.")
        elif graves.get("rows", 0) == 0:
            conclusions.append("Registrul MAIN selectat nu conține locuri de veci.")
        elif graves.get("drawn_polygons", 0) == 0:
            conclusions.append("MAIN conține locuri, dar nu există poligoane în points în acest registru.")
        if settings.get("cloud_entity_sync_enabled") == "0" or settings.get("cloud_entity_sync") == "0":
            conclusions.append("Sincronizarea entităților este dezactivată sau cloud-ul nu confirmă suportul.")
        if settings.get("cloud_sync_auto") != "1":
            conclusions.append("Sincronizarea automată nu este activată în acest registru; folosește sincronizarea manuală din MAIN.")
        if graves.get("never_pushed", 0) and not any(k.startswith("entity:") for k in pending):
            conclusions.append("Sunt locuri neconfirmate de cloud, fără operațiuni entity în outbox: verifică tracking-ul și conexiunea.")
        if any(k.endswith(":conflict") or k.endswith(":error") for k in pending):
            conclusions.append("Există conflicte/erori în coada locală; nu forța suprascrieri automate.")
        return {"registry_filename": path.name, "tables": tables, "outbox": pending, "settings": settings,
                "conclusions": conclusions or ["Nu se poate stabili din baza locală singură dacă datele au ajuns în cloud: compară cu /mobile/data-status."]}
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Diagnostichează local sincronizarea Cimitir Mobile v2; doar citire.")
    ap.add_argument("--db", help="Calea fișierului SQLite pentru registrul MAIN activ")
    ap.add_argument("--output", help="Opțional: raport JSON fără date personale și secrete")
    ns = ap.parse_args()
    try:
        report = analyze(Path(ns.db) if ns.db else discover_database())
    except (FileNotFoundError, sqlite3.Error, OSError) as exc:
        print(f"Verificarea nu a putut fi efectuată: {type(exc).__name__}. Indică baza corectă prin --db.", file=sys.stderr)
        return 2
    data = json.dumps(report, ensure_ascii=False, indent=2)
    print(data)
    if ns.output:
        Path(ns.output).write_text(data + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
