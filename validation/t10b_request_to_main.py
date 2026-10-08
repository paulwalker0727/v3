"""Pas 10b - cerere cetatean -> MAIN: MAIN descarca cererea + documentul, apoi 'ack' => serverul sterge datele personale si obiectul din Storage."""
import hashlib
import re

import requests

from common import conn
from nodelib import N

res = []
PDF = open("/app/validation/files/TEST_document.pdf", "rb").read()
SHA = hashlib.sha256(PDF).hexdigest()
code = open("/app/validation/req_code.txt").read().strip()
env = open("/app/backend/.env").read()
SK = re.search(r'SUPABASE_SECRET_KEY="([^"]+)"', env).group(1)
SUP = re.search(r'SUPABASE_URL="([^"]+)"', env).group(1)


def check(name, cond, extra=""):
    res.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


with conn() as cn:
    row = cn.execute("select d.media_id, m.storage_path, c.nume from documents d join cereri_concesionare c on c.id=cast(d.entity_id as int) left join media_objects m on m.id=d.media_id where d.entity_type='cerere' and c.cod_urmarire=%s", (code,)).fetchone()
check("inainte de sync: cererea are date personale si documentul este in Storage", row and row[0] and row[2] == "TEST-Solicitant", str(row[:1]) if row else "lipsa")
media_id, path = row[0], row[1]
s = N("A", "sync", timeout=250)
print("   sync lines:", [x for x in s.get("lines", []) if "ere" in x][:3])
check("MAIN A sync ok", s.get("ok"), str(s.get("conflicts")))
loc = N("A", "rawsql", f"SELECT cod_urmarire, nume FROM cereri_concesionare WHERE cod_urmarire='{code}'")["rows"]
check("MAIN A a primit cererea (cu datele personale)", len(loc) == 1 and loc[0]["nume"] == "TEST-Solicitant", str(loc))
docs = N("A", "docs")["files"]
mine = [d for d in docs if d["table"] == "documents" and d["media_id"] == media_id]
print("   documente MAIN pentru cerere:", [(d["exists"], (d["path"] or "")[-30:]) for d in mine])
if mine and not mine[0]["exists"]:
    e = N("A", "ensure", media_id, "TEST_cerere.pdf")
    mine = [dict(mine[0], exists=e.get("exists"), sha_local=__import__("hashlib").sha256(open(e["path"], "rb").read()).hexdigest() if e.get("exists") else None)]
check("MAIN A are documentul cererii local, SHA256 identic cu cel trimis de cetatean", mine and mine[0]["exists"] and mine[0]["sha_local"] == SHA, "")
with conn() as cn:
    srv = cn.execute("select nume, acked from cereri_concesionare where cod_urmarire=%s", (code,)).fetchone()
    mo = cn.execute("select count(*) from media_objects where id=%s", (media_id,)).fetchone()[0]
check("dupa ack: serverul a sters datele personale (nume gol) si a marcat acked", srv and srv[0] == "" and srv[1] == 1, str(srv))
check("dupa ack: metadatele media sterse de pe server", mo == 0)
r = requests.get(f"{SUP}/storage/v1/object/cimitir-private/{path}", headers={"apikey": SK, "Authorization": "Bearer " + SK}, timeout=60)
check("dupa ack: obiectul a fost sters si din Supabase Storage (date personale)", r.status_code in (400, 404), str(r.status_code))
tr = requests.get(f"http://127.0.0.1:8001/api/public/cerere/{code}", timeout=30)
check("urmarirea cererii functioneaza in continuare dupa ack", tr.status_code == 200 and tr.json().get("status"), tr.text[:100])
print("REZUMAT:", sum(ok for _, ok in res), "/", len(res))
print("ESUATE:", [n for n, ok in res if not ok])
