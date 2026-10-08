"""Verificari finale suplimentare (dupa run_all): monitorizare cu conflict, cerere->MAIN cu potrivire corecta, log DB-down fara host."""
import glob
import hashlib
import json
import os
import re
import subprocess
import time

import requests

from common import conn, get_secrets
from nodelib import N

S = get_secrets()
B = "http://127.0.0.1:8001"
ids = json.load(open("/app/validation/ids.json"))
res = []
ENV = "/app/backend/.env"


def check(name, cond, extra=""):
    res.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def sup(c):
    subprocess.run(["sudo", "supervisorctl", c, "backend"], capture_output=True, timeout=90)


def wait_up():
    for _ in range(40):
        try:
            if requests.get(B + "/api/health", timeout=3).status_code in (200, 503):
                return True
        except requests.RequestException:
            pass
        time.sleep(1.5)


# ---------- C) log-uri fara host cand DB e indisponibila (dupa filtrul de redactare)
LOG = "/var/log/supervisor/backend.err.log"
pos = os.path.getsize(LOG)
env_text = open(ENV).read()
dbpw = re.search(r":([^:@/]+)@", re.search(r'DATABASE_URL="([^"]+)"', env_text).group(1)).group(1)
open("/app/validation/.env.backup2", "w").write(env_text); os.chmod("/app/validation/.env.backup2", 0o600)
try:
    open(ENV, "w").write(env_text.replace(dbpw, "PAROLA-GRESITA-TEST"))
    sup("restart"); wait_up()
    for p in ("/api/public/map", "/api/health", "/portal"):
        requests.get(B + p, timeout=60)
    time.sleep(3)
finally:
    open(ENV, "w").write(open("/app/validation/.env.backup2").read()); os.remove("/app/validation/.env.backup2")
    sup("restart"); wait_up()
new = open(LOG, "rb").read()[pos:].decode(errors="ignore")
leak = [x for x in ("pooler.supabase.com", "txuxkjgftopznvmooexq", "PAROLA-GRESITA-TEST", dbpw, "34.241", "108.128", "52.209", 'user "postgres') if x in new]
print("   linii noi in log in timpul DB-down:", len(new.splitlines()), "| exemplu:", [l[:110] for l in new.splitlines() if "PostgreSQL" in l][:2])
check("log DB indisponibila: fara host, IP-uri, utilizator sau parola (dupa filtru)", not leak, str(leak))
check("recuperare: health online dupa restaurarea .env", requests.get(B + "/api/health", timeout=30).json().get("database_status") == "online")
print("REZUMAT:", sum(ok for _, ok in res), "/", len(res))
print("ESUATE:", [n for n, ok in res if not ok])
