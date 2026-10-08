import json
from nodelib import N
from common import conn
ids = json.load(open("ids.json"))
res = []
def check(name, cond, extra=""):
    res.append((name, bool(cond))); print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
cl = {c["entity"]: c for c in N("B", "conflicts")["conflicts"]}
print("   campuri conflict MAIN:", sorted(cl["locuri_de_veci"].keys()))
choice = {"locuri_de_veci": "mine", "concesionari": "server", "contracte": "mine"}
for ent, keep in choice.items():
    r = N("B", "resolve", cl[ent]["op_id"], keep); check(f"B rezolva {ent} cu '{keep}'", r.get("ok"), str(r))
for i in range(2):
    sb = N("B", "sync"); sa = N("A", "sync")
check("dupa rezolvare B sync fara conflicte", sb.get("conflicts") == 0, str(sb.get("conflicts")))
check("A sync ok", sa.get("ok"), "")
cols = {"locuri_de_veci": ("loc", "observatii"), "concesionari": ("conc", "telefon"), "contracte": ("ctr", "observatii")}
expected = {"locuri_de_veci": "TEST-VAL-B-loc", "concesionari": "TEST-VAL-A-conc", "contracte": "TEST-VAL-B-ctr"}
with conn() as cn:
    for t, (k, col) in cols.items():
        u = ids[k]["uid"]
        a = N("A", "rawsql", f"SELECT {col} AS v, version FROM {t} WHERE uid='{u}'")["rows"][0]
        b = N("B", "rawsql", f"SELECT {col} AS v, version FROM {t} WHERE uid='{u}'")["rows"][0]
        s = cn.execute(f"select {col}, version from {t} where uid=%s", (u,)).fetchone()
        check(f"{t}: A, B si server convergente pe '{expected[t]}' si aceeasi versiune", a["v"] == b["v"] == s[0] == expected[t] and a["version"] == b["version"] == s[1], f"A={a['version']} B={b['version']} srv={s[1]}")
    n = cn.execute("select count(*) from sync_conflicts where resolved_at is null").fetchone()[0]
    check("PG: niciun conflict deschis ramas", n == 0, str(n))
    r = cn.execute("select resolution, count(*) from sync_conflicts group by 1").fetchall(); print("   rezolutii:", r)
print("REZUMAT:", sum(ok for _, ok in res), "/", len(res)); print("ESUATE:", [n for n, ok in res if not ok])
