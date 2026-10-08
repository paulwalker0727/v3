import json
from nodelib import N
from common import conn, get_secrets
S = get_secrets()
res = []
def check(name, cond, extra=""):
    res.append((name, bool(cond))); print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
URL = "http://127.0.0.1:8001"
c1 = N("A", "mkconc", "TEST-TOMB-1"); c2 = N("A", "mkconc", "TEST-TOMB-2")
N("A", "sync"); N("B", "sync")
b1 = N("B", "rawsql", f"SELECT id,version FROM concesionari WHERE uid='{c1['uid']}'")["rows"]; b2 = N("B", "rawsql", f"SELECT id,version FROM concesionari WHERE uid='{c2['uid']}'")["rows"]
check("B are copia ambelor obiecte", len(b1) == 1 and len(b2) == 1)
# B offline (adresa moarta)
N("B", "seturl", "http://127.0.0.1:9")
off = N("B", "sync"); check("B offline: sync esueaza controlat (fara crash)", off.get("ok") is False, str(off.get("lines"))[:120])
# B modifica offline obiectul 2 (copie veche modificata)
N("B", "modify", "concesionari", b2[0]["id"], "telefon", "0799999999")
# A sterge ambele obiecte si sincronizeaza
a1 = N("A", "rawsql", f"SELECT id FROM concesionari WHERE uid='{c1['uid']}'")["rows"][0]["id"]; a2 = N("A", "rawsql", f"SELECT id FROM concesionari WHERE uid='{c2['uid']}'")["rows"][0]["id"]
check("A sterge obiect 1", N("A", "delete", "concesionari", a1).get("ok")); check("A sterge obiect 2", N("A", "delete", "concesionari", a2).get("ok"))
sa = N("A", "sync"); check("A sync stergeri ok", sa.get("ok"), str(sa.get("lines"))[:150])
with conn() as cn:
    n = cn.execute("select count(*) from concesionari where nume like 'TEST-TOMB%'").fetchone()[0]
    t = cn.execute("select count(*) from sync_tombstones where uid in (%s,%s)", (c1["uid"], c2["uid"])).fetchone()[0]
    tv = cn.execute("select version from sync_tombstones where uid=%s", (c1["uid"],)).fetchone()
check("PG: obiectele sterse, 2 tombstone-uri create", n == 0 and t == 2, f"rows={n} tombs={t} ver={tv}")
# B revine online
N("B", "seturl", URL)
sb = N("B", "sync"); print("   B sync lines:", [l for l in sb.get("lines", []) if "ntit" in l])
r1 = N("B", "rawsql", f"SELECT id FROM concesionari WHERE uid='{c1['uid']}'")["rows"]
r2 = N("B", "rawsql", f"SELECT id,telefon FROM concesionari WHERE uid='{c2['uid']}'")["rows"]
check("obiect 1 (copie veche nemodificata) NU reapare pe B; sters local", len(r1) == 0, str(r1))
with conn() as cn:
    n = cn.execute("select count(*) from concesionari where nume like 'TEST-TOMB%'").fetchone()[0]
check("PG: nimic nu a reaparut pe server dupa revenirea lui B (zero 'inviere')", n == 0, str(n))
cf = [c for c in N("B", "conflicts")["conflicts"] if c["uid"] == c2["uid"]]
check("obiect 2 (modificat offline de B, sters de A): conflict 'deleted_on_server' sau sters, NU recreat silentios", (len(cf) == 1 and cf[0]["reason"] == "deleted_on_server") or (len(r2) == 0), f"conflicts={[c['reason'] for c in cf]} local={r2}")
if cf:
    print("   conflict:", cf[0]["reason"], "| B pastreaza local:", r2)
    r = N("B", "resolve", cf[0]["op_id"], "server"); check("B alege versiunea serverului (sters)", r.get("ok"), str(r))
    N("B", "sync")
    r2 = N("B", "rawsql", f"SELECT id FROM concesionari WHERE uid='{c2['uid']}'")["rows"]
    check("dupa rezolvare obiect 2 sters si pe B", len(r2) == 0, str(r2))
# MAIN C nou (instalare curata) nu primeste obiectele sterse
N("C", "setup", URL, S["sync_key"]); sc = N("C", "sync")
rc = N("C", "rawsql", "SELECT count(*) AS n FROM concesionari WHERE nume LIKE 'TEST-TOMB%'")["rows"][0]["n"]
check("MAIN C nou: nu primeste obiectele sterse", sc.get("ok") and rc == 0, str(rc))
dc = N("C", "dump")
check("MAIN C nou: primeste obiectele TEST existente (loc/concesionar/contract/inhumat/plata)", all(len(dc[t]) >= 1 for t in ("locuri_de_veci", "concesionari", "contracte", "inhumati", "plati")), str({t: len(dc[t]) for t in ('locuri_de_veci','concesionari','contracte','inhumati','plati')}))
# repornire sync repetata: obiectele sterse nu reapar
for n_ in ("A", "B", "C"): N(n_, "sync")
with conn() as cn:
    check("dupa sync repetat pe 3 noduri: tot zero obiecte TEST-TOMB pe server", cn.execute("select count(*) from concesionari where nume like 'TEST-TOMB%'").fetchone()[0] == 0)
print("REZUMAT:", sum(ok for _, ok in res), "/", len(res)); print("ESUATE:", [n for n, ok in res if not ok])
