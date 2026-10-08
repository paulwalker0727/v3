import json
from nodelib import N
from common import conn

def show(tag, r): print(tag, json.dumps(r)[:300])
res = []
def check(name, cond, extra=""):
    res.append((name, bool(cond))); print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))

# bootstrap initial pe baze curate
ra, rb = N("A", "sync"), N("B", "sync")
check("bootstrap A (baza locala curata) fara erori", ra.get("ok"), str(ra.get("lines"))[:200])
check("bootstrap B (baza locala curata) fara erori", rb.get("ok"), str(rb.get("lines"))[:200])

# A creeaza datele TEST
loc = N("A", "mkloc", "TEST-LOC-001"); conc = N("A", "mkconc", "TEST-Concesionar-A")
ctr = N("A", "mkcontract", "TEST-CTR-001", "TEST-LOC-001", conc["id"])
inh = N("A", "mkinh", "TEST-LOC-001", "TEST-Inhumat-A")
pay = N("A", "mkplata", "TEST-LOC-001", "150.50", "TEST-CH-001", ctr["id"])
show("A created:", {"loc": loc, "conc": conc, "ctr": ctr, "inh": inh, "pay": pay})
da0 = N("A", "dump")
check("A are 5 operatii in outbox (pending)", sum(1 for o in da0["outbox"] if o["state"] == "pending" and o["entity_uid"] in (loc["uid"], conc["uid"], ctr["uid"], inh["uid"], pay["uid"])) == 5,
      str([(o["entity"], o["state"]) for o in da0["outbox"]]))
s = N("A", "sync"); check("A sync ok, outbox gol", s.get("ok") and s.get("pending") == 0, str(s))
da = N("A", "dump")
sb = N("B", "sync"); check("B sync ok", sb.get("ok"), str(sb.get("lines"))[:300])
db_ = N("B", "dump")

def uids(d, t): return sorted(r["uid"] for r in d[t])
for t in ("locuri_de_veci", "concesionari", "contracte", "inhumati", "plati"):
    check(f"B a primit {t}: aceleasi UID-uri ca A", uids(da, t) == uids(db_, t) and len(uids(db_, t)) == 1, f"{len(uids(db_,t))} randuri")
    check(f"{t}: aceeasi versiune A/B", [r["version"] for r in da[t]] == [r["version"] for r in db_[t]], f"A={[r['version'] for r in da[t]]} B={[r['version'] for r in db_[t]]}")
c, i, p = db_["contracte"][0], db_["inhumati"][0], db_["plati"][0]
check("B: contract -> concesionar corect (FK)", c["concesionar_uid"] == conc["uid"], "")
check("B: contract -> loc corect (FK)", c["loc_uid"] == loc["uid"], "")
check("B: inhumat -> loc corect", i["loc_uid"] == loc["uid"], "")
check("B: plata -> loc corect + suma", p["cod_loc"] == "TEST-LOC-001" and abs(p["suma"] - 150.5) < 1e-9, str(p["suma"]))
check("B: statusul locului (disponibilitate) identic cu A", db_["locuri_de_veci"][0]["disponibilitate"] == da["locuri_de_veci"][0]["disponibilitate"], db_["locuri_de_veci"][0]["disponibilitate"])
check("B: outbox gol (nimic reflectat inapoi)", all(o["state"] != "pending" for o in db_["outbox"]), str([(o['entity'],o['state']) for o in db_['outbox']]))

# server PostgreSQL: zero duplicate, FK
with conn() as cn:
    for t, key in (("locuri_de_veci", "cod_loc"), ("concesionari", "nume"), ("contracte", "numar_contract"), ("inhumati", "nume"), ("plati", "chitanta_nr")):
        n = cn.execute(f"select count(*), count(distinct uid) from {t} where {key} like 'TEST%%'").fetchone()
        check(f"PG {t}: {n[0]} rand(uri) TEST, zero duplicate", n[0] == 1 and n[1] == 1, str(n))
    r = cn.execute("select k.nume, l.cod_loc from contracte c join concesionari k on k.id=c.concesionar_id join locuri_de_veci l on l.id=c.id_loc where c.numar_contract='TEST-CTR-001'").fetchone()
    check("PG FK contract->concesionar+loc rezolvate", r == ("TEST-Concesionar-A", "TEST-LOC-001"), str(r))
    r = cn.execute("select l.cod_loc from inhumati i join locuri_de_veci l on l.id=i.id_loc where i.nume='TEST-Inhumat-A'").fetchone()
    check("PG FK inhumat->loc", r and r[0] == "TEST-LOC-001", str(r))
    r = cn.execute("select c.numar_contract from plati p join contracte c on c.id=p.contract_id where p.chitanta_nr='TEST-CH-001'").fetchone()
    check("PG FK plata->contract", r and r[0] == "TEST-CTR-001", str(r))
json.dump({"loc": loc, "conc": conc, "ctr": ctr, "inh": inh, "pay": pay}, open("/app/validation/ids.json", "w"))
print("REZUMAT:", sum(ok for _, ok in res), "/", len(res)); print("ESUATE:", [n for n, ok in res if not ok])
