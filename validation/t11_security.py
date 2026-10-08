import re
import subprocess

import requests

from common import conn, get_secrets

B = "http://127.0.0.1:8001"
S = get_secrets()
res = []
src = open("/app/backend/server.py").read()


def check(name, cond, extra=""):
    res.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def fill(p):
    return re.sub(r"\{[^}]+\}", "999999", p)


gets = [fill(p) for p in re.findall(r'@admin\.get\("([^"]*)"', src)]
posts = [fill(p) for p in re.findall(r'@admin\.post\("([^"]*)"', src)]
public_ok = {"/access", "/access/login", "/logout", "/schimba-parola"}
gets = ["/admin" + p for p in gets if p not in public_ok]
posts = ["/admin" + p for p in posts if p not in public_ok]
anon = requests.Session()
bad = []
for p in gets:
    r = anon.get(B + p, allow_redirects=False, timeout=60)
    if r.status_code not in (301, 302, 303, 307, 401, 403):
        bad.append((p, r.status_code))
check(f"anonim: {len(gets)} rute GET /admin* protejate (redirect/401/403)", not bad, str(bad))
bad = []
for p in posts:
    r = anon.post(B + p, data={"name": "TEST", "cod_loc": "TEST-LOC-001"}, files={"file": ("x.png", b"x")}, allow_redirects=False, timeout=60)
    if r.status_code not in (301, 302, 303, 307, 401, 403, 404, 405):
        bad.append((p, r.status_code))
check(f"anonim: {len(posts)} rute POST /admin* protejate", not bad, str(bad))
r = anon.get(B + "/api/admin/map.json", allow_redirects=False, timeout=30)
check("anonim: /api/admin/map.json refuzat", r.status_code in (401, 403, 302, 303, 307), str(r.status_code))

# ---- API sync fara cheie / cheie gresita
sync_paths = ["/api/sync/ping", "/api/sync/entities/pull?entity=plati", "/api/sync/entities/conflicts", "/api/sync/media/state", "/api/sync/media", "/api/sync/media/TESTMEDIA00000000"]
bad = [(p, requests.get(B + p, timeout=30).status_code) for p in sync_paths if requests.get(B + p, timeout=30).status_code not in (401, 403, 404, 429)]
check("anonim: API /api/sync/* (GET) refuzat fara cheie", not bad, str(bad))
r = requests.post(B + "/api/sync/entities/push", json={"ops": [{"op_id": "x", "entity": "plati", "uid": "y", "action": "upsert", "data": {}}]}, timeout=30)
check("anonim: POST /api/sync/entities/push refuzat", r.status_code in (401, 403, 429), str(r.status_code))
r = requests.delete(B + "/api/sync/media/TESTMEDIA00000000", timeout=30)
check("anonim: DELETE /api/sync/media refuzat", r.status_code in (401, 403, 429), str(r.status_code))

# ---- privilegii Supabase pentru anon / authenticated (catalog Postgres) + RLS
with conn() as cn:
    tabs = [t[0] for t in cn.execute("select tablename from pg_tables where schemaname='public'").fetchall()]
    rls = dict(cn.execute("select c.relname, c.relrowsecurity from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relkind='r'").fetchall())
    roles = [r[0] for r in cn.execute("select rolname from pg_roles where rolname in ('anon','authenticated')").fetchall()]
    privs = []
    for role in roles:
        for t in tabs:
            for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                if cn.execute("select has_table_privilege(%s, %s, %s)", (role, f"public.{t}", priv)).fetchone()[0]:
                    privs.append((role, t, priv))
    seqp = []
    for role in roles:
        for sq in cn.execute("select sequencename from pg_sequences where schemaname='public'").fetchall():
            if cn.execute("select has_sequence_privilege(%s, %s, 'USAGE')", (role, f"public.{sq[0]}")).fetchone()[0]:
                seqp.append((role, sq[0]))
check(f"RLS activ pe toate cele {len(tabs)} tabele", all(rls.get(t) for t in tabs), str([t for t in tabs if not rls.get(t)]))
check("roluri anon/authenticated exista si NU au niciun drept direct SELECT/INSERT/UPDATE/DELETE", roles and not privs, f"roles={roles} privs={privs[:5]}")
check("anon/authenticated fara USAGE pe secvente", not seqp, str(seqp[:3]))
SUPURL = re.search(r'SUPABASE_URL="([^"]+)"', open("/app/backend/.env").read()).group(1)
for label, hdr in (("fara cheie", {}), ("cheie falsa", {"apikey": "sb_publishable_FALSE000000000000000000", "Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.e30.x"})):
    for t in ("users", "locuri_de_veci"):
        r1 = requests.get(f"{SUPURL}/rest/v1/{t}?select=*", headers=hdr, timeout=30)
        r2 = requests.post(f"{SUPURL}/rest/v1/{t}", headers={**hdr, "Content-Type": "application/json"}, json={}, timeout=30)
        r3 = requests.delete(f"{SUPURL}/rest/v1/{t}?id=gt.0", headers=hdr, timeout=30)
        check(f"Supabase REST {label}: /{t} GET/POST/DELETE refuzate", all(x.status_code in (401, 403) for x in (r1, r2, r3)), str([r1.status_code, r2.status_code, r3.status_code]))

# ---- CSRF / origine (cu sesiune admin valida)
s = requests.Session()
s.post(f"{B}/admin/access/login", data={"source_id": "cimitir", "username": S["user"], "password": S["new_password"]}, allow_redirects=False, timeout=30)
EV = {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}
for p in ("/admin/system/test-db", "/admin/system/persistence/create", "/admin/cloud/test-postgres"):
    r = s.post(B + p, headers=EV, data={}, allow_redirects=False, timeout=60)
    check(f"CSRF: POST cross-origin {p} -> 403", r.status_code == 403, str(r.status_code))
r = s.post(B + "/admin/system/test-db", headers={"Origin": B, "Sec-Fetch-Site": "same-origin"}, data={}, allow_redirects=False, timeout=60)
check("CSRF: POST same-origin /admin/system/test-db -> 200 (functioneaza)", r.status_code == 200, str(r.status_code))
unprot = []
for p in ("/admin/ui/user", "/admin/ui/settings", "/admin/ui/concesionar", "/admin/ui/loc/TEST-LOC-001/edit"):
    r = s.post(B + p, headers=EV, data={}, allow_redirects=False, timeout=60)
    if r.status_code != 403:
        unprot.append((p, r.status_code))
check("CSRF: POST cross-origin pe rutele /admin/ui/* -> 403", not unprot, f"neprotejate explicit: {unprot}")
# cookie flags
r = requests.post(f"{B}/admin/access/login", data={"source_id": "cimitir", "username": S["user"], "password": S["new_password"]}, allow_redirects=False, timeout=30)
ck = r.headers.get("set-cookie", "")
check("cookie sesiune admin: HttpOnly", "HttpOnly" in ck)
check("cookie sesiune admin: SameSite=lax", "samesite=lax" in ck.lower())
check("cookie sesiune admin: Secure (cerut in mod online/HTTPS)", "secure" in ck.lower(), "lipseste" if "secure" not in ck.lower() else "")
r = requests.post(f"{B}/admin/access/login", data={"source_id": "cimitir", "username": S["user"], "password": S["new_password"]}, headers={"X-Forwarded-Proto": "https"}, allow_redirects=False, timeout=30)
check("cookie sesiune admin: Secure cand cererea vine prin HTTPS (X-Forwarded-Proto)", "secure" in r.headers.get("set-cookie", "").lower())

# ---- limitare login personal
sess = requests.Session()
codes = [sess.post(f"{B}/admin/access/login", data={"source_id": "cimitir", "username": S["user"], "password": f"gresita-{i}-Zz!"}, allow_redirects=False, timeout=30) for i in range(6)]
msgs = [("blocat" in c.text.lower()) for c in codes]
check("login personal: dupa 5 parole gresite contul se blocheaza", msgs[-1] and not msgs[0], str(msgs))
r = sess.post(f"{B}/admin/access/login", data={"source_id": "cimitir", "username": S["user"], "password": S["new_password"]}, allow_redirects=False, timeout=30)
check("login personal: parola CORECTA e respinsa cat timp contul e blocat", r.status_code == 200 and "blocat" in r.text.lower(), str(r.status_code))
with conn() as cn:  # deblocare tehnica (artefact de test)
    cn.execute("delete from settings where key like 'loginfail:%%' ")
    cn.execute("delete from settings where key like 'rl:%%' ")

# ---- erori / injectii
for label, url in (("search injectie SQL", "/api/public/search?q=%27%3B%20DROP%20TABLE%20users%3B--"), ("loc cu caractere speciale", "/api/public/loc/%27%22%3C%3E%00"),
                   ("traversare cale", "/api/public/loc/..%2F..%2Fetc%2Fpasswd"), ("map/background", "/api/public/map/background")):
    r = requests.get(B + url, timeout=30)
    leak = [x for x in ("Traceback", "psycopg", "postgresql://", "pooler.supabase", "sb_secret") if x in r.text]
    check(f"erori controlate: {label} (fara 500/stack trace)", r.status_code < 500 and not leak, f"{r.status_code} {leak}")
with conn() as cn:
    check("tabela users intacta dupa injectie SQL", cn.execute("select count(*) from users").fetchone()[0] >= 1)
print("REZUMAT:", sum(ok for _, ok in res), "/", len(res))
print("ESUATE:", [n for n, ok in res if not ok])
subprocess.run(["true"])
