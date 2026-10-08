"""Helper comun pentru validare. NU afiseaza secrete."""
import os, sys, re, json, hashlib, secrets
from pathlib import Path
ROOT = Path("/app")
for p in (ROOT/"shared", ROOT/"backend"/"cimitir", ROOT/"backend", ROOT/"desktop"):
    if str(p) not in sys.path: sys.path.insert(0, str(p))
import cimitir_config
cimitir_config.load_env()
import psycopg

def conn():
    return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15)

def tables(c):
    cur = c.execute("select table_name from information_schema.tables where table_schema='public' order by 1")
    return [r[0] for r in cur.fetchall()]

def counts():
    out = {}
    with conn() as c:
        for t in tables(c):
            out[t] = c.execute(f'select count(*) from "{t}"').fetchone()[0]
    return out

def scrub(s):
    s = str(s)
    for k in ("DATABASE_URL", "SUPABASE_SECRET_KEY"):
        v = os.environ.get(k, "")
        if v: s = s.replace(v, "***")
    pw = re.findall(r":([^:@/]+)@", os.environ.get("DATABASE_URL", ""))
    for p in pw: s = s.replace(p, "***")
    return s

SECRETS = ROOT/"validation"/".secrets.json"
def get_secrets():
    return json.loads(SECRETS.read_text()) if SECRETS.exists() else {}
def save_secrets(d):
    SECRETS.write_text(json.dumps(d)); os.chmod(SECRETS, 0o600)
