"""Curatare completa date TEST din cloud (DB + Storage) si de pe disc. Nu sterge schema, nu sterge configuratia (cemetery_id)."""
import json
import os
import re
import shutil
import sys

import requests

from common import conn, tables

DRY = "--dry" in sys.argv
env = open("/app/backend/.env").read()
SK = re.search(r'SUPABASE_SECRET_KEY="([^"]+)"', env).group(1)
SUP = re.search(r'SUPABASE_URL="([^"]+)"', env).group(1)
H = {"apikey": SK, "Authorization": "Bearer " + SK}
KEEP_TABLES = {"schema_migrations", "settings"}
KEEP_SETTINGS = {"cemetery_id"}


def walk(prefix=""):
    r = requests.post(f"{SUP}/storage/v1/object/list/cimitir-private", headers=H, json={"prefix": prefix, "limit": 1000}, timeout=60).json()
    out = []
    for it in r:
        if it.get("id") is None:
            out += walk(prefix + it["name"] + "/")
        else:
            out.append((prefix + it["name"], it.get("created_at") or ""))
    return out


report = {}
with conn() as cn:
    known = {r[0] for r in cn.execute("select storage_path from media_objects").fetchall()}
    cerere_codes = {r[0] for r in cn.execute("select cod_urmarire from cereri_concesionare").fetchall()}
    objs = walk()
    mine, foreign = [], []
    SESSION_START = "2026-10-04T20:37"      # obiectele create inainte de aceasta sesiune nu se ating
    for o, created in objs:
        if created >= SESSION_START:
            mine.append(o)
        else:
            foreign.append(o)
    report["storage_total"], report["storage_test"], report["storage_nerecunoscute_pastrate"] = len(objs), len(mine), foreign
    if not DRY:
        for o in mine:
            r = requests.delete(f"{SUP}/storage/v1/object/cimitir-private/{o}", headers=H, timeout=60)
            assert r.status_code in (200, 204), (o, r.status_code)
    tabs = [t for t in tables(cn) if t not in KEEP_TABLES]
    before = {t: cn.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tabs}
    report["randuri_sterse"] = {t: n for t, n in before.items() if n}
    if not DRY:
        cn.execute("TRUNCATE " + ", ".join(f'"{t}"' for t in tabs) + " RESTART IDENTITY CASCADE")
        cn.execute("DELETE FROM settings WHERE key <> ALL(%s)", (list(KEEP_SETTINGS),))

if not DRY:
    for p in ("/app/validation/nodes", "/app/validation/ids.json", "/app/validation/conf_ids.json", "/app/validation/media_ids.json", "/app/validation/req_code.txt",
              "/app/validation/mon.txt", "/app/validation/last_backup_dir.txt", "/app/validation/live_counts.json"):
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        elif os.path.exists(p):
            os.remove(p)
    for d in os.listdir("/app/shared_data-backups") if os.path.isdir("/app/shared_data-backups") else []:
        if d != "cimitir-20261002-205231":      # singurul folder din repo; restul sunt create de testele mele
            shutil.rmtree(f"/app/shared_data-backups/{d}", ignore_errors=True)
    media = "/app/shared_data/media"
    if os.path.isdir(media):
        shutil.rmtree(media, ignore_errors=True)
    for d in ("media_cache",):
        shutil.rmtree(f"/app/shared_data/{d}", ignore_errors=True)
print(json.dumps(report, indent=1, default=str))
if not DRY:
    with conn() as cn:
        left = {t: cn.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tables(cn)}
    print("dupa curatare, tabele cu randuri:", {t: n for t, n in left.items() if n})
    print("setari ramase:", [r[0] for r in conn().execute("select key from settings").fetchall()])
    print("bucket obiecte ramase:", len(walk()))
