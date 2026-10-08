import hashlib
import io
import json
import re

import requests

from common import conn

B = "http://127.0.0.1:8001"
SK = re.search(r'SUPABASE_SECRET_KEY="([^"]+)"', open("/app/backend/.env").read()).group(1)
SUP = re.search(r'SUPABASE_URL="([^"]+)"', open("/app/backend/.env").read()).group(1)
ids = json.load(open("/app/validation/ids.json"))
res = []
PDF = open("/app/validation/files/TEST_document.pdf", "rb").read()


def check(name, cond, extra=""):
    res.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


anon = requests.Session()
# ---- pagini
for p in ("/portal", "/portal/harta", "/portal/cerere", "/portal/loc/TEST-LOC-001", "/portal/cont", "/portal/prelucrare-date", "/api/public/health", "/api/public/privacy"):
    r = anon.get(B + p, timeout=60)
    check(f"anonim: GET {p} -> 200", r.status_code == 200, str(r.status_code))
r = anon.get(B + "/portal/loc/NU-EXISTA-123", timeout=30)
check("anonim: loc inexistent -> pagina 'Loc negasit' (nu 500)", r.status_code in (200, 404) and "Loc negăsit" in r.text, str(r.status_code))

# ---- harta publica
m = anon.get(B + "/api/public/map", timeout=60).json()
plots = {p["cod_loc"]: p for p in m.get("plots", [])}
check("harta publica contine TEST-LOC-001 cu poligon si status", "TEST-LOC-001" in plots and plots["TEST-LOC-001"].get("points") and plots["TEST-LOC-001"].get("disponibilitate"), str(plots.get("TEST-LOC-001", {}).get("disponibilitate")))
mj = json.dumps(m, ensure_ascii=False)
PRIVATE = ["TEST-Concesionar-A", "0711111111", "TEST-CTR-001", "TEST-CH-001", "TEST-Inhumat-A", "TEST-OFF-Inh", "0700000000", "cnp", "owner_", "password", "sync_key"]
leak = [x for x in PRIVATE if x.lower() in mj.lower()]
check("harta publica: fara date private (concesionar, telefon, contract, plati, inhumati)", not leak, str(leak))
print("   chei expuse per loc:", sorted(plots["TEST-LOC-001"].keys()))

# ---- cautare + fisa loc + publicare inhumati
s = anon.get(B + "/api/public/search", params={"q": "TEST-LOC-001"}, timeout=60).json()
check("cautare dupa cod loc gaseste locul", s["count"] >= 1 and any(r["cod_loc"] == "TEST-LOC-001" for r in s["results"]), str(s["count"]))
loc = anon.get(B + "/api/public/loc/TEST-LOC-001", timeout=60).json()
lj = json.dumps(loc, ensure_ascii=False)
check("fisa loc publica: fara date private", not [x for x in PRIVATE if x.lower() in lj.lower() and x.lower() not in ("cnp",)], str([x for x in PRIVATE if x.lower() in lj.lower()]))
with conn() as cn:
    pb = cn.execute("select value from settings where key='publish_burials'").fetchone()
check("publish_burials setat pe OFF/necreat pe server (implicit privat)", pb is None or pb[0] == "0", str(pb))
sd = anon.get(B + "/api/public/search", params={"q": "TEST-Inhumat-A"}, timeout=60).json()
check("publicare OFF: numele inhumatului NU apare in cautare publica", sd["count"] == 0 and "TEST-Inhumat" not in json.dumps(sd), str(sd["count"]))
check("publicare OFF: numele inhumatului NU apare in fisa loc", "TEST-Inhumat" not in lj)
with conn() as cn:
    cn.execute("insert into settings(key,value) values('publish_burials','1') on conflict (key) do update set value='1'")
sd = anon.get(B + "/api/public/search", params={"q": "TEST-Inhumat-A"}, timeout=60).json()
loc2 = json.dumps(anon.get(B + "/api/public/loc/TEST-LOC-001", timeout=60).json(), ensure_ascii=False)
check("publicare ON: numele inhumatului apare in cautare/fisa", sd["count"] >= 1 or "TEST-Inhumat" in loc2, f"search={sd['count']} fisa={'TEST-Inhumat' in loc2}")
check("publicare ON: totusi nu apar CNP/telefon/concesionar", not [x for x in ("TEST-Concesionar-A", "0711111111", "TEST-CTR-001") if x in loc2])
with conn() as cn:
    cn.execute("delete from settings where key='publish_burials'")

# ---- cerere anonima (cont obligatoriu)
form = {"nume": "TEST-Solicitant", "prenume": "Validare", "email": "test-validare@example.invalid", "telefon": "0700111222", "cod_loc": "TEST-LOC-001", "tip": "concesionare", "privacy_ack": "1"}
r = anon.post(B + "/api/public/cerere", data=form, timeout=60)
check("cerere anonima fara cont -> 401 (cont de cetatean obligatoriu)", r.status_code == 401, f"{r.status_code} {r.text[:80]}")

# ---- cont cetatean TEST + cerere
cit = requests.Session()
EM, PW = "test-validare@example.invalid", "TestParola-Cetatean-2026!"
r = cit.post(B + "/api/public/account/register", json={"email": EM, "password": PW, "full_name": "TEST Validare"}, timeout=60)
check("inregistrare cont cetatean TEST", r.status_code == 200, f"{r.status_code} {r.text[:80]}")
r = cit.post(B + "/api/public/account/register", json={"email": "x@example.invalid", "password": "scurta"}, timeout=60)
check("parola cetatean < 10 caractere respinsa (422)", r.status_code == 422, str(r.status_code))
files = [("files", ("TEST_cerere.pdf", PDF, "application/pdf")),
         ("files", ("virus.exe", b"MZ\x90\x00 TEST", "application/octet-stream")),
         ("files", ("fals.pdf", b"acesta nu este un pdf TEST", "application/pdf")),
         ("files", ("mare.pdf", b"%PDF-1.4\n" + b"0" * (11 * 1024 * 1024), "application/pdf"))]
r = cit.post(B + "/api/public/cerere", data=form, files=files, timeout=120)
check("cerere cu cont: acceptata (200) si cod de urmarire", r.status_code == 200 and r.json().get("cod_urmarire"), f"{r.status_code} {r.text[:200]}")
j = r.json() if r.status_code == 200 else {}
code = j.get("cod_urmarire", "")
check("doar documentul valid salvat (1), celelalte 3 respinse", j.get("documente") == 1 and len(j.get("respinse", [])) == 3, f"saved={j.get('documente')} rejected={j.get('respinse')}")
rej = " | ".join(j.get("respinse", []))
check("motive: tip invalid, semnatura gresita, prea mare", ("virus.exe" in rej) and ("fals.pdf" in rej) and ("mare.pdf" in rej), rej[:250])
# urmarire
tr = anon.get(B + f"/api/public/cerere/{code}", timeout=60)
check("urmarire cerere cu cod (anonim) -> 200 + status", tr.status_code == 200 and tr.json().get("status"), f"{tr.status_code} {tr.text[:150]}")
check("urmarirea nu expune date personale ale solicitantului", not any(x in tr.text for x in ("0700111222", "example.invalid", "TEST-Solicitant")), tr.text[:200])
check("urmarire cu cod inexistent -> 404", anon.get(B + "/api/public/cerere/COD-INEXISTENT-1", timeout=30).status_code == 404)
# documentul in storage
with conn() as cn:
    d = cn.execute("select d.id, d.media_id, d.sha256, d.size from documents d join cereri_concesionare c on c.id=cast(d.entity_id as int) where d.entity_type='cerere' and c.cod_urmarire=%s", (code,)).fetchall()
    check("documentul cererii are inregistrare cu sha256", len(d) == 1 and d[0][2] == hashlib.sha256(PDF).hexdigest(), str(d))
    mo = cn.execute("select storage_path, sha256, size from media_objects where id=%s", (d[0][1],)).fetchone() if d and d[0][1] else None
check("documentul cererii e in media_objects (Storage)", mo is not None and mo[1] == hashlib.sha256(PDF).hexdigest(), str(mo[1][:12]) if mo else "lipsa")
if mo:
    rr = requests.get(f"{SUP}/storage/v1/object/cimitir-private/{mo[0]}", headers={"apikey": SK, "Authorization": "Bearer " + SK}, timeout=60)
    check("documentul cererii exista in Supabase Storage (bucket privat), SHA256 corect", rr.status_code == 200 and hashlib.sha256(rr.content).hexdigest() == hashlib.sha256(PDF).hexdigest(), str(rr.status_code))
    ra = requests.get(f"{SUP}/storage/v1/object/public/cimitir-private/{mo[0]}", timeout=30)
    check("documentul cererii NU e accesibil anonim direct in Storage", ra.status_code != 200, str(ra.status_code))
    rd = cit.get(B + f"/api/public/account/cereri/{code}/documents/{d[0][0]}", timeout=60)
    check("proprietarul cererii poate descarca documentul (prin FastAPI), SHA256 corect", rd.status_code == 200 and hashlib.sha256(rd.content).hexdigest() == hashlib.sha256(PDF).hexdigest(), str(rd.status_code))
    rd2 = anon.get(B + f"/api/public/account/cereri/{code}/documents/{d[0][0]}", timeout=60)
    check("anonim NU poate descarca documentul cererii", rd2.status_code in (401, 403, 404), str(rd2.status_code))
    other = requests.Session()
    other.post(B + "/api/public/account/register", json={"email": "test-alt@example.invalid", "password": "AltParola-Cetatean-2026!"}, timeout=60)
    rd3 = other.get(B + f"/api/public/account/cereri/{code}/documents/{d[0][0]}", timeout=60)
    check("alt cetatean NU poate descarca documentul (izolare)", rd3.status_code in (401, 403, 404), str(rd3.status_code))
open("/app/validation/req_code.txt", "w").write(code)

# ---- rate limiting
codes = []
for i in range(4):
    codes.append(anon.post(B + "/api/public/account/forgot", json={"email": "rl-test@example.invalid"}, timeout=60).status_code)
check("rate limit 'forgot_email' (3/ora): a 4-a cerere -> 429", codes[-1] == 429 and 429 not in codes[:3], str(codes))
sts = [anon.post(B + "/api/public/cerere", data={}, timeout=60).status_code for _ in range(22)]
check("rate limit 'cerere' (20/ora/IP): dupa 20 cereri -> 429", 429 in sts and sts.index(429) <= 20, f"primul 429 la #{sts.index(429)+1 if 429 in sts else None}")
r = anon.post(B + "/api/public/cerere", data={}, timeout=60)
check("raspunsul 429 contine Retry-After", r.status_code == 429 and r.headers.get("Retry-After"), str(r.headers.get("Retry-After")))
fl = [requests.post(B + "/api/public/account/login", json={"email": "test-validare@example.invalid", "password": "gresita-gresita"}, timeout=60).status_code for _ in range(7)]
check("login cetatean: blocare dupa 5 esecuri (429)", 429 in fl and fl.index(429) <= 6, str(fl))
print("REZUMAT:", sum(ok for _, ok in res), "/", len(res))
print("ESUATE:", [n for n, ok in res if not ok])
