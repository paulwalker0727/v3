import json, re, requests
from nodelib import N
from common import conn, get_secrets
ids = json.load(open("ids.json")); S = get_secrets()
res = []
def check(name, cond, extra=""):
    res.append((name, bool(cond))); print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
key = {"locuri_de_veci": ("loc", "observatii"), "concesionari": ("conc", "telefon"), "contracte": ("ctr", "observatii")}
lid = {}
for t in key:
    ua = ids[key[t][0]]["uid"]
    lid[t] = {"A": N("A", "rawsql", f"SELECT id,version FROM {t} WHERE uid='{ua}'")["rows"][0], "B": N("B", "rawsql", f"SELECT id,version FROM {t} WHERE uid='{ua}'")["rows"][0]}
    check(f"{t}: A si B pornesc din aceeasi versiune", lid[t]["A"]["version"] == lid[t]["B"]["version"] == 2, str((lid[t]['A']['version'], lid[t]['B']['version'])))
# modificari concurente
for t, (k, col) in key.items():
    N("A", "modify", t, lid[t]["A"]["id"], col, f"TEST-VAL-A-{k}")
    N("B", "modify", t, lid[t]["B"]["id"], col, f"TEST-VAL-B-{k}")
sa = N("A", "sync"); check("A sync ok (primul, fara conflict)", sa.get("ok") and sa.get("conflicts") == 0, str(sa.get("conflicts")))
sb = N("B", "sync"); check("B sync detecteaza conflicte (3)", sb.get("conflicts") == 3 and not sb.get("ok"), f"conflicts={sb.get('conflicts')}")
print("   B lines:", [l for l in sb.get("lines", []) if "ntit" in l][:2])
# datele ambelor parti pastrate
with conn() as cn:
    for t, (k, col) in key.items():
        srv = cn.execute(f"select {col}, version from {t} where uid=%s", (ids[k]["uid"],)).fetchone()
        check(f"{t}: serverul pastreaza datele lui A (nu last-write-wins)", srv == (f"TEST-VAL-A-{k}", 3), str(srv))
        loc_b = N("B", "rawsql", f"SELECT {col} AS v, version FROM {t} WHERE uid='{ids[k]['uid']}'")["rows"][0]
        check(f"{t}: B pastreaza local datele lui B (nepierdute)", loc_b["v"] == f"TEST-VAL-B-{k}", str(loc_b))
    rows = cn.execute("select entity, reason, base_version, server_version, client_data::text, server_data::text, resolved_at from sync_conflicts where resolved_at is null").fetchall()
    check("sync_conflicts: 3 conflicte deschise, motiv version_mismatch", len(rows) == 3 and all(r[1] == "version_mismatch" and r[2] == 2 and r[3] == 3 for r in rows), str([(r[0], r[1], r[2], r[3]) for r in rows]))
    for r in rows:
        k = {"locuri_de_veci": "loc", "concesionari": "conc", "contracte": "ctr"}[r[0]]
        check(f"conflict {r[0]}: client_data contine B, server_data contine A (diferente vizibile)", f"TEST-VAL-B-{k}" in r[4] and f"TEST-VAL-A-{k}" in r[5])
cl = N("B", "conflicts")["conflicts"]
check("MAIN B listeaza 3 conflicte", len(cl) == 3, str([c.get("entity") for c in cl]))
print("   exemplu conflict MAIN:", json.dumps(cl[0], ensure_ascii=False)[:400])
# /admin/monitorizare
s = requests.Session(); s.post("http://127.0.0.1:8001/admin/access/login", data={"source_id": "cimitir", "username": S["user"], "password": S["new_password"]}, allow_redirects=False)
h = s.get("http://127.0.0.1:8001/admin/monitorizare", timeout=60).text
txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))
check("/admin/monitorizare afiseaza conflictele", "conflict" in txt.lower() and "TEST-VAL-A" in h or "TEST-VAL-B" in h, "")
for kw in ("TEST-VAL-A-loc", "TEST-VAL-B-loc", "main-"):
    print(f"   monitorizare contine '{kw}':", kw in h)
open("mon.txt", "w").write(txt[:3000])
json.dump({t: lid[t] for t in lid}, open("conf_ids.json", "w"))
print("REZUMAT:", sum(ok for _, ok in res), "/", len(res)); print("ESUATE:", [n for n, ok in res if not ok])
