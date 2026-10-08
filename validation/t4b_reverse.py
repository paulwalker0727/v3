import json, requests
from nodelib import N
from common import conn
ids = json.load(open("/app/validation/ids.json"))
res = []
def check(name, cond, extra=""):
    res.append((name, bool(cond))); print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))

# B modifica fiecare entitate (id local pe B poate diferi; luam din dump B dupa uid)
db_ = N("B", "dump")
loc_b = db_["locuri_de_veci"][0]["id"]; conc_b = db_["concesionari"][0]["id"]; ctr_b = db_["contracte"][0]["id"]
inh_b = db_["inhumati"][0]["id"]; pay_b = db_["plati"][0]["id"]
N("B", "modify", "locuri_de_veci", loc_b, "observatii", "TEST loc modificat de B")
N("B", "modify", "concesionari", conc_b, "telefon", "0711111111")
N("B", "modify", "contracte", ctr_b, "observatii", "TEST contract modificat de B")
N("B", "modify", "inhumati", inh_b, "observatii", "TEST inhumat modificat de B")
N("B", "modify", "plati", pay_b, "observatii", "TEST plata modificata de B")
sb = N("B", "sync"); check("B sync (modificari) ok", sb.get("ok") and sb.get("pending") == 0, str(sb.get("lines"))[1:200])
sa = N("A", "sync"); check("A sync ok", sa.get("ok"), str(sa.get("lines"))[1:200])
da, db2 = N("A", "dump"), N("B", "dump")
expect = {"locuri_de_veci": ("observatii", "TEST loc modificat de B"), "concesionari": ("telefon", "0711111111"),
          "contracte": ("observatii", "TEST contract modificat de B"), "inhumati": None, "plati": ("observatii", "TEST plata modificata de B")}
# valorile din dump: pentru inhumati/ plati observatii nu sunt in dump toate; verificam prin rawsql
for t, col, val in (("locuri_de_veci", "observatii", "TEST loc modificat de B"), ("concesionari", "telefon", "0711111111"),
                    ("contracte", "observatii", "TEST contract modificat de B"), ("inhumati", "observatii", "TEST inhumat modificat de B"),
                    ("plati", "observatii", "TEST plata modificata de B")):
    ra = N("A", "rawsql", f"SELECT {col} AS v, version FROM {t} WHERE uid='{ids[{'locuri_de_veci':'loc','concesionari':'conc','contracte':'ctr','inhumati':'inh','plati':'pay'}[t]]['uid']}'")["rows"][0]
    check(f"A a primit modificarea B pe {t}", ra["v"] == val and ra["version"] == 2, f"v={ra['version']}")
for t in ("locuri_de_veci", "concesionari", "contracte", "inhumati", "plati"):
    check(f"{t}: A si B ajung la aceeasi versiune, un singur rand", [r["version"] for r in da[t]] == [r["version"] for r in db2[t]] and len(da[t]) == 1)
with conn() as cn:
    for t in ("locuri_de_veci", "concesionari", "contracte", "inhumati", "plati"):
        n = cn.execute(f"select version from {t} where uid=%s", (ids[{'locuri_de_veci':'loc','concesionari':'conc','contracte':'ctr','inhumati':'inh','plati':'pay'}[t]]["uid"],)).fetchone()
        check(f"PG {t}: versiune server = 2", n and n[0] == 2, str(n))
print("REZUMAT:", sum(ok for _, ok in res), "/", len(res)); print("ESUATE:", [n for n, ok in res if not ok])
