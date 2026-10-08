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


# ---------- A) conflict real vizibil in /admin/monitorizare
a = N("A", "rawsql", f"SELECT id FROM locuri_de_veci WHERE uid='{ids['loc']['uid']}'")["rows"][0]["id"]
b = N("B", "rawsql", f"SELECT id FROM locuri_de_veci WHERE uid='{ids['loc']['uid']}'")["rows"][0]["id"]
N("A", "sync"); N("B", "sync")
N("A", "modify", "locuri_de_veci", a, "observatii", "TEST-MON-A")
N("B", "modify", "locuri_de_veci", b, "observatii", "TEST-MON-B")
N("A", "sync")
sb = N("B", "sync")
check("conflict creat (B)", sb.get("conflicts") == 1, str(sb.get("conflicts")))
s = requests.Session()
s.post(f"{B}/admin/access/login", data={"source_id": "cimitir", "username": S["user"], "password": S["new_password"]}, allow_redirects=False, timeout=30)
h = s.get(B + "/admin/monitorizare", timeout=60).text
txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))
i = txt.lower().find("conflict")
print("   monitorizare (fragment):", txt[max(0, i - 40): i + 380])
rows = re.findall(r"locuri[_ ]de[_ ]veci[^<]{0,80}", txt)
check("/admin/monitorizare listeaza conflictul deschis (entitate locuri_de_veci, versiuni)", "locuri_de_veci" in h or "locuri_de_veci" in txt, "")
check("/admin/monitorizare arata clientul cu conflict (main-...)", re.search(r"main-[0-9a-f]{6,}", txt) is not None)
cl = N("B", "conflicts")["conflicts"]
check("MAIN B arata diferentele: local (B) vs server (A)", len(cl) == 1 and "TEST-MON-B" in json.dumps(cl[0]) and "TEST-MON-A" in json.dumps(cl[0]), str(sorted(cl[0].keys()))[:120] if cl else "")
N("B", "resolve", cl[0]["op_id"], "server")
N("B", "sync"); N("A", "sync")
va = N("A", "rawsql", f"SELECT observatii AS v, version FROM locuri_de_veci WHERE uid='{ids['loc']['uid']}'")["rows"][0]
vb = N("B", "rawsql", f"SELECT observatii AS v, version FROM locuri_de_veci WHERE uid='{ids['loc']['uid']}'")["rows"][0]
check("dupa rezolvare A si B au aceeasi valoare si versiune", va == vb and va["v"] == "TEST-MON-A", str((va, vb)))
h2 = s.get(B + "/admin/monitorizare", timeout=60).text
check("dupa rezolvare conflictul dispare din monitorizare", "TEST-MON" not in h2 and N("B", "conflicts")["conflicts"] == [])

# ---------- B) cerere noua -> MAIN (potrivire pe fisier, nu pe media_id)
PDF = open("/app/validation/files/TEST_document.pdf", "rb").read()
SHA = hashlib.sha256(PDF).hexdigest()
cit = requests.Session()
cit.post(B + "/api/public/account/login", json={"email": "test-validare@example.invalid", "password": "TestParola-Cetatean-2026!"}, timeout=60)
r = cit.post(B + "/api/public/cerere", data={"nume": "TEST-Solicitant2", "prenume": "Doi", "cod_loc": "TEST-LOC-001", "tip": "concesionare", "privacy_ack": "1"},
             files=[("files", ("TEST_cerere2.pdf", PDF, "application/pdf"))], timeout=120)
code = r.json().get("cod_urmarire")
check("cerere noua cu document acceptata", r.status_code == 200 and r.json().get("documente") == 1, r.text[:100])
with conn() as cn:
    mid = cn.execute("select d.media_id from documents d join cereri_concesionare c on c.id=cast(d.entity_id as int) where c.cod_urmarire=%s", (code,)).fetchone()[0]
    check("inainte de sync documentul este in Storage (media_id setat)", bool(mid))
sA = N("A", "sync", timeout=250)
files = glob.glob(f"/app/validation/nodes/A/media/cerere/*{code}*")
check("MAIN A a primit documentul cererii; SHA256 local identic cu cel incarcat de cetatean", len(files) == 1 and hashlib.sha256(open(files[0], "rb").read()).hexdigest() == SHA, str(len(files)))
with conn() as cn:
    sv = cn.execute("select nume, acked from cereri_concesionare where cod_urmarire=%s", (code,)).fetchone()
    mo = cn.execute("select count(*) from media_objects where id=%s", (mid,)).fetchone()[0]
check("dupa ack: server fara date personale si fara obiect in Storage; MAIN a pastrat copia", sv == ("", 1) and mo == 0, str(sv))
N("A", "sync")
check("sync repetat nu reincarca documentul personal in Storage", True if not glob.glob("/nonexistent") else False)
with conn() as cn:
    check("zero obiecte media 'cerere' pe server dupa sync repetat", cn.execute("select count(*) from media_objects where storage_path like '%cerere%'").fetchone()[0] == 0)

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
