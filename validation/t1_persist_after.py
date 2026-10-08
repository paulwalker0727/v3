import re, requests
from common import conn, get_secrets
B = "http://127.0.0.1:8001"; S = get_secrets()
s = requests.Session()
r = s.post(f"{B}/admin/access/login", data={"source_id": "cimitir", "username": S["user"], "password": S["new_password"]}, allow_redirects=False, timeout=30)
print("login after restart:", r.status_code)
marker = open("/app/validation/.probe_marker").read()
with conn() as c:
    print("own TEST record survives restart:", c.execute("select value from settings where key=%s", (marker,)).fetchone())
r = s.post(B + "/admin/system/persistence/verify", timeout=60); print("app verify:", r.status_code)
txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.text))
print("  says confirmed:", bool(re.search(r"confirmat", txt, re.I)))
with conn() as c:
    print("active probe still present:", c.execute("select count(*) from settings where key='_probe:persistence:active'").fetchone()[0])
    print("result row:", c.execute("select left(value::text,90) from settings where key='_probe:persistence:result'").fetchone())
    # curatare inregistrare proprie
    c.execute("delete from settings where key=%s", (marker,))
    print("own record deleted; exists:", c.execute("select count(*) from settings where key=%s", (marker,)).fetchone()[0])
r = s.post(B + "/admin/system/persistence/cleanup", timeout=60); print("app cleanup:", r.status_code)
