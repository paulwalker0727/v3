import re, requests, subprocess, time, uuid
from common import conn, get_secrets, scrub
import os
B = "http://127.0.0.1:8001"
S = get_secrets()
s = requests.Session()
r = s.post(f"{B}/admin/access/login", data={"source_id": "cimitir", "username": S["user"], "password": S["new_password"]}, allow_redirects=False, timeout=30)
assert r.status_code == 303, r.status_code

def sysflag(html):
    lvl = re.search(r'data-testid="sys-prod-level"[^>]*data-level="([^"]+)"', html) or re.search(r'data-level="([^"]+)"', html)
    return lvl.group(1) if lvl else None

h = s.get(B + "/admin/system", timeout=60).text
print("level before:", sysflag(h))
txt = re.sub(r"<[^>]+>", " ", h); txt = re.sub(r"\s+", " ", txt)
for kw in ("POSTGRESQL CLOUD", "SUPABASE", "NECONFIRMAT", "PERSISTENT CONFIRMAT", "GATA PENTRU PRODUC", "NU ESTE GATA"):
    print(f"  contains '{kw}':", kw.lower() in txt.lower())
leaks = [x for x in ("pooler.supabase.com", "txuxkjgftopznvmooexq", "sb_secret_", "postgresql://") if x in h]
print("  leaks in /admin/system:", leaks or "none")

# 1) inregistrare tehnica TEST proprie in settings
marker = "_probe:TEST_persist:" + uuid.uuid4().hex[:8]
with conn() as c:
    c.execute("insert into settings(key,value) values(%s,%s)", (marker, "TEST-PERSIST-VALUE"))
    print("own probe inserted:", c.execute("select value from settings where key=%s", (marker,)).fetchone())
# 2) proba din aplicatie
r = s.post(B + "/admin/system/persistence/create", timeout=60); print("app probe create:", r.status_code)
with conn() as c:
    print("app probe active row:", c.execute("select count(*) from settings where key='_probe:persistence:active'").fetchone()[0])
open("/app/validation/.probe_marker", "w").write(marker)
