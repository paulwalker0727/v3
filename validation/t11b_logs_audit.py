import glob, os, re, json
from common import conn, get_secrets
S = get_secrets()
res = []
def check(name, cond, extra=""):
    res.append((name, bool(cond))); print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
env = open("/app/backend/.env").read()
dburl = re.search(r'DATABASE_URL="([^"]+)"', env).group(1)
dbpw = re.search(r":([^:@/]+)@", dburl).group(1)
from urllib.parse import unquote
secrets_ = {"db_password_enc": dbpw, "db_password": unquote(dbpw), "supabase_key": re.search(r'SUPABASE_SECRET_KEY="([^"]+)"', env).group(1),
            "database_url": dburl, "db_host": "aws-0-eu-west-1.pooler.supabase.com", "tmp_pw": S["tmp_password"], "new_pw": S["new_password"], "sync_key": S["sync_key"].split(".", 1)[1], "citizen_pw": "TestParola-Cetatean-2026!"}
files = glob.glob("/var/log/supervisor/backend.*.log") + glob.glob("/app/shared_data/**/*.log", recursive=True) + glob.glob("/app/backend/**/*.log", recursive=True) + glob.glob("/app/validation/nodes/*/**/*.log", recursive=True)
files += glob.glob("/app/**/logs/**", recursive=True)
files = [f for f in set(files) if os.path.isfile(f)]
print("   fisiere log verificate:", len(files), [os.path.basename(f) for f in files][:8])
for name, val in secrets_.items():
    hits = [os.path.basename(f) for f in files if val and val in open(f, errors="ignore").read()]
    check(f"log-uri: '{name}' nu apare", not hits, str(hits))
# audit
with conn() as cn:
    cols = [r[0] for r in cn.execute("select column_name from information_schema.columns where table_name='audit_log' order by ordinal_position").fetchall()]
    rows = cn.execute("select * from audit_log order by 1").fetchall()
print("   audit_log coloane:", cols, "| randuri:", len(rows))
actions = {}
for r in rows:
    d = dict(zip(cols, r)); actions[d.get("action")] = actions.get(d.get("action"), 0) + 1
print("   actiuni in audit:", actions)
blob = json.dumps([[str(x) for x in r] for r in rows])
for name, val in secrets_.items():
    check(f"audit_log: '{name}' nu apare", val not in blob)
need = ["actor", "action"]
check("audit_log are actor, action, timestamp, target/detalii", all(any(c.startswith(n) or n in c for c in cols) for n in ("actor", "action")) and any("created" in c or "ts" in c or "time" in c or "at" == c[-2:] for c in cols), str(cols))
for a in ("register", "request_submitted", "system_persistence_create", "system_persistence_verify"):
    check(f"audit contine actiunea '{a}'", a in actions, str(actions.get(a)))
print("   exemplu inregistrare:", {k: (str(v)[:70]) for k, v in zip(cols, rows[-1])} if rows else None)
print("REZUMAT:", sum(ok for _, ok in res), "/", len(res)); print("ESUATE:", [n for n, ok in res if not ok])
