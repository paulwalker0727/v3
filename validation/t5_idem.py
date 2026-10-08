import json, uuid, requests, concurrent.futures as cf
from common import get_secrets, conn
S = get_secrets(); ids = json.load(open("ids.json"))
H = {"X-Sync-Key": S["sync_key"], "X-Sync-Client": "main-idemtest01"}
URL = "http://127.0.0.1:8001/api/sync/entities/push"
res = []
def check(name, cond, extra=""):
    res.append((name, bool(cond))); print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
def push(op): return requests.post(URL, json={"ops": [op]}, headers=H, timeout=60).json()["results"][0]
def u(): return uuid.uuid4().hex
ops = {
 "concesionari": {"uid": u(), "data": {"nume": "TEST-IDEM-conc", "prenume": "X"}},
 "contracte": {"uid": u(), "data": {"numar_contract": "TEST-IDEM-ctr", "cod_loc": "TEST-LOC-001", "concesionar_uid": ids["conc"]["uid"], "loc_uid": ids["loc"]["uid"]}},
 "inhumati": {"uid": u(), "data": {"nume": "TEST-IDEM-inh", "prenume": "Y", "loc_uid": ids["loc"]["uid"]}},
 "plati": {"uid": u(), "data": {"cod_loc": "TEST-LOC-001", "suma": 99.0, "chitanta_nr": "TEST-IDEM-ch", "contract_uid": ids["ctr"]["uid"]}},
}
keyname = {"concesionari": "nume", "contracte": "numar_contract", "inhumati": "nume", "plati": "chitanta_nr"}
ctrid = {}
for ent, o in ops.items():
    op = {"op_id": u(), "entity": ent, "uid": o["uid"], "action": "upsert", "base_version": 0, "data": o["data"]}
    r1 = push(op); r2 = push(op); r3 = push(op)
    check(f"{ent}: prima trimitere applied/created", r1["status"] == "applied" and r1.get("created"), str(r1)[:120])
    check(f"{ent}: retrimis acelasi op_id -> duplicate, nu 'applied' nou", r2.get("duplicate") is True and r2["status"] == "applied" and r2["version"] == r1["version"] and r3.get("duplicate") is True, str(r2)[:140])
    with conn() as cn:
        n = cn.execute(f"select count(*), max(version) from {ent} where {keyname[ent]}=%s", (o['data'][keyname[ent]],)).fetchone()
        check(f"{ent}: in PostgreSQL exista exact 1 rand dupa 3 trimiteri, versiune 1", n[0] == 1 and n[1] == 1, str(n))
        m = cn.execute("select count(*) from sync_ops where op_id=%s", (op["op_id"],)).fetchone()[0]
        check(f"{ent}: o singura inregistrare sync_ops pentru op_id", m == 1, str(m))
    ops[ent]["op"] = op

# update idempotent: acelasi op_id de UPDATE trimis de 3 ori nu mareste versiunea de mai multe ori
op = ops["concesionari"]["op"]
upd = {"op_id": u(), "entity": "concesionari", "uid": op["uid"], "action": "upsert", "base_version": 1, "data": {"nume": "TEST-IDEM-conc", "prenume": "Y-modificat"}}
r = [push(upd) for _ in range(3)]
with conn() as cn:
    v = cn.execute("select version, prenume from concesionari where uid=%s", (op["uid"],)).fetchone()
check("update retrimis de 3 ori: versiune serverului = 2 (o singura aplicare)", v == (2, "Y-modificat"), str(v))
check("update: raspunsurile 2 si 3 sunt duplicate", r[1].get("duplicate") and r[2].get("duplicate"), str([x['status'] for x in r]))

# delete idempotent
dl = {"op_id": u(), "entity": "plati", "uid": ops["plati"]["uid"], "action": "delete", "base_version": 1, "data": {}}
d = [push(dl) for _ in range(3)]
with conn() as cn:
    n = cn.execute("select count(*) from plati where chitanta_nr='TEST-IDEM-ch'").fetchone()[0]
    t = cn.execute("select count(*) from sync_tombstones where uid=%s", (ops["plati"]["uid"],)).fetchone()[0]
check("delete retrimis de 3 ori: randul sters, un singur tombstone", n == 0 and t == 1, f"rows={n} tombs={t}")

# cursa: 8 cereri simultane, acelasi op_id (creare)
op = {"op_id": u(), "entity": "concesionari", "uid": u(), "action": "upsert", "base_version": 0, "data": {"nume": "TEST-IDEM-RACE", "prenume": "R"}}
with cf.ThreadPoolExecutor(8) as ex:
    rr = list(ex.map(lambda _: push(op), range(8)))
with conn() as cn:
    n = cn.execute("select count(*), max(version) from concesionari where nume='TEST-IDEM-RACE'").fetchone()
sts = sorted({(x["status"], bool(x.get("duplicate")), x.get("reason", "")) for x in rr})
check("cursa 8x acelasi op_id: un singur rand creat, versiune 1", n == (1, 1), str(n))
print("   raspunsuri in cursa:", sts)
check("cursa: toate cele 8 raspunsuri sunt 'applied' (niciunul 'rejected'/'retry'/'conflict')", all(x["status"] == "applied" for x in rr), str(sts))
print("REZUMAT:", sum(ok for _, ok in res), "/", len(res)); print("ESUATE:", [n for n, ok in res if not ok])
