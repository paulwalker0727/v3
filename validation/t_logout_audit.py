"""Teste afectate: Ieșire Securizată (logout server-side) + Audit Complet (locuri, concesionari, contracte, înhumați, plăți)."""
import json
import secrets
import string
import subprocess
import sys

import requests

from common import conn, save_secrets
from nodelib import N

B = "http://127.0.0.1:8001"
SAME = {"Origin": B, "Sec-Fetch-Site": "same-origin"}
res = []


def check(name, cond, extra=""):
    res.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


a = string.ascii_letters + string.digits
TMP = "Tt-" + "".join(secrets.choice(a) for _ in range(18)) + "!9"
NEW = "Nn-" + "".join(secrets.choice(a) for _ in range(18)) + "!7"
USER = "test_tmp_admin"
save_secrets({"user": USER, "tmp_password": TMP, "new_password": NEW})
with conn() as cn:
    cn.execute("delete from users where username=%s", (USER,))
import os  # noqa: E402
subprocess.run([sys.executable, "/app/scripts/create_first_admin.py", "--username", USER, "--password-env", "VAL_TMP_PW"], env=dict(os.environ, VAL_TMP_PW=TMP), capture_output=True, timeout=90)


def login(pw):
    s = requests.Session()
    r = s.post(f"{B}/admin/access/login", data={"source_id": "cimitir", "username": USER, "password": pw}, allow_redirects=False, timeout=30)
    return s, r


# ============================== 1. LOGOUT SERVER-SIDE
s0, r = login(TMP)
s0.post(f"{B}/admin/schimba-parola", data={"old_password": TMP, "new_password": NEW, "confirm_password": NEW}, allow_redirects=False, timeout=30)
s1, r1 = login(NEW)       # sesiunea care va face logout
s2, r2 = login(NEW)       # a doua sesiune (alt dispozitiv): trebuie sa ramana valida
check("login 1 si 2 reusite (redirect fara schimbare parola)", r1.status_code == 303 and r2.status_code == 303 and "schimba-parola" not in r1.headers.get("location", ""))
old_cookie = dict(s1.cookies)
check("acces cu sesiunea 1 inainte de logout: /admin/system 200", s1.get(B + "/admin/system", allow_redirects=False, timeout=60).status_code == 200)
check("acces /admin/cloud 200", s1.get(B + "/admin/cloud", allow_redirects=False, timeout=60).status_code == 200)
lo = s1.post(B + "/admin/logout", headers=SAME, allow_redirects=False, timeout=30)
sc = lo.headers.get("set-cookie", "")
check("logout -> redirect /admin/access", lo.status_code == 303 and "/admin/access" in lo.headers.get("location", ""))
check("logout sterge cookie-ul din browser (Max-Age=0/expirat, path=/, HttpOnly, SameSite=lax)", ("max-age=0" in sc.lower() or "expires=" in sc.lower()) and 'path=/' in sc.lower() and "httponly" in sc.lower() and "samesite=lax" in sc.lower(), sc[:140])
with conn() as cn:
    n = cn.execute("select count(*) from settings where key like 'revoked_session:%%'").fetchone()[0]
check("sesiunea este revocata server-side (inregistrare in baza de date)", n == 1, str(n))
# CSRF: GET nu revoca nimic; POST cross-site respins (403)
sc_ = requests.Session(); sc_.cookies.update(dict(s2.cookies))
g_ = sc_.get(B + "/admin/logout", allow_redirects=False, timeout=30)
x_ = sc_.post(B + "/admin/logout", headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}, allow_redirects=False, timeout=30)
check("CSRF: GET /admin/logout inofensiv, POST cross-site 403, sesiunea ramane valida", g_.status_code == 303 and x_.status_code == 403 and sc_.get(B + "/admin/system", allow_redirects=False, timeout=60).status_code == 200, f"{g_.status_code}/{x_.status_code}")
# reutilizare cookie vechi
s3 = requests.Session()
for k, v in old_cookie.items():
    s3.cookies.set(k, v)
for p in ("/admin/system", "/admin/cloud", "/admin/", "/api/admin/map.json"):
    rr = s3.get(B + p, allow_redirects=False, timeout=60)
    check(f"cookie vechi reutilizat dupa logout: GET {p} respins", rr.status_code in (302, 303, 307, 401, 403) and (rr.status_code in (401, 403) or "access" in rr.headers.get("location", "")), str(rr.status_code))
rr = s3.post(B + "/admin/system/test-db", headers=SAME, allow_redirects=False, timeout=60)
check("cookie vechi reutilizat: POST /admin/system/test-db respins", rr.status_code in (302, 303, 307, 401, 403), str(rr.status_code))
check("cealalta sesiune (alt dispozitiv) ramane valida", s2.get(B + "/admin/system", allow_redirects=False, timeout=60).status_code == 200)
s4, r4 = login(NEW)
check("login nou dupa logout functioneaza", r4.status_code == 303 and s4.get(B + "/admin/system", allow_redirects=False, timeout=60).status_code == 200)
with conn() as cn:
    acts = [r[0] for r in cn.execute("select action from audit_log where action='logout'").fetchall()]
check("logout inregistrat in audit_log", len(acts) == 1, str(acts))
# logout cu cookie deja invalid / fara cookie nu da eroare
check("logout fara cookie -> 303 fara eroare", requests.post(B + "/admin/logout", headers=SAME, allow_redirects=False, timeout=30).status_code == 303)
check("logout repetat cu cookie revocat -> 303 fara eroare", s3.post(B + "/admin/logout", headers=SAME, allow_redirects=False, timeout=30).status_code == 303)

# ============================== 2. AUDIT COMPLET
p = subprocess.run([sys.executable, "/app/scripts/issue_sync_key.py", "--source", "cimitir"], capture_output=True, text=True, timeout=120)
KEY = [l for l in p.stdout.splitlines() if l.startswith("cimitir.")][-1].strip()
S = dict(user=USER, tmp_password=TMP, new_password=NEW, sync_key=KEY)
save_secrets(S)
print("setup A:", N("A", "setup", B, KEY).get("ok"))
CLIENT = N("A", "rawsql", "SELECT value AS v FROM cloud_sync_config WHERE key='client_id'")
loc = N("A", "mkloc", "TEST-AUD-LOC"); conc = N("A", "mkconc", "TEST-AUD-Conc")
ctr = N("A", "mkcontract", "TEST-AUD-CTR", "TEST-AUD-LOC", conc["id"]); inh = N("A", "mkinh", "TEST-AUD-LOC", "TEST-AUD-Inh")
pay = N("A", "mkplata", "TEST-AUD-LOC", "50", "TEST-AUD-CH", ctr["id"])
N("A", "sync", timeout=250)


def audit(entity, uid=None, action=None):
    q, par = "select actor_type, actor, action, target, ts, detail from audit_log where action like %s", [f"{entity}.%"]
    if uid:
        q += " and target=%s"; par.append(f"{entity}:{uid}")
    with conn() as cn:
        return [dict(zip(("actor_type", "actor", "action", "target", "ts", "detail"), r)) for r in cn.execute(q + " order by id", par).fetchall()]


ents = {"locuri_de_veci": loc, "concesionari": conc, "contracte": ctr, "inhumati": inh, "plati": pay}
client_ids = set()
for e, o in ents.items():
    rows = audit(e, o["uid"])
    ok = len(rows) == 1 and rows[0]["action"] == f"{e}.create" and rows[0]["actor_type"] == "sync" and rows[0]["target"] == f"{e}:{o['uid']}"
    d = json.loads(rows[0]["detail"]) if rows else {}
    check(f"audit CREATE {e}: actor(sync/client), action, entity:UID, timestamp, new valori, old=null", ok and d.get("old") is None and d.get("new") and d.get("uid") == o["uid"] and rows[0]["ts"], str(rows[0]["actor"]) if rows else "lipsa")
    client_ids.add(rows[0]["actor"] if rows else "")
check("actor = identificatorul clientului MAIN (acelasi pentru toate)", len(client_ids) == 1 and next(iter(client_ids)).startswith("main-"), str(client_ids))

# sync repetat fara modificari + replay acelasi op_id: niciun rand nou
with conn() as cn:
    before_n = cn.execute("select count(*) from audit_log where action like '%%.create' or action like '%%.update' or action like '%%.delete'").fetchone()[0]
N("A", "sync")
H = {"X-Sync-Key": KEY, "X-Sync-Client": "main-audtest001"}
op = {"op_id": "audop0000000000000000000000001", "entity": "concesionari", "uid": "audit-uid-replay-0001", "action": "upsert", "base_version": 0, "data": {"nume": "TEST-AUD-Replay", "prenume": "R", "telefon": "0700"}}
requests.post(B + "/api/sync/entities/push", json={"ops": [op]}, headers=H, timeout=60); requests.post(B + "/api/sync/entities/push", json={"ops": [op]}, headers=H, timeout=60)
with conn() as cn:
    after_n = cn.execute("select count(*) from audit_log where action like '%%.create' or action like '%%.update' or action like '%%.delete'").fetchone()[0]
check("sync fara schimbari nu adauga audit; replay acelasi op_id adauga exact 1 rand (nu 2)", after_n - before_n == 1, str(after_n - before_n))

# UPDATE: valori vechi / noi, doar campurile schimbate
a_id = lambda t, o: N("A", "rawsql", f"SELECT id FROM {t} WHERE uid='{o['uid']}'")["rows"][0]["id"]  # noqa: E731
N("A", "modify", "locuri_de_veci", a_id("locuri_de_veci", loc), "observatii", "TEST-AUD loc NOU")
N("A", "modify", "concesionari", a_id("concesionari", conc), "telefon", "0799000111")
N("A", "modify", "contracte", a_id("contracte", ctr), "observatii", "TEST-AUD ctr NOU")
N("A", "modify", "inhumati", a_id("inhumati", inh), "observatii", "TEST-AUD inh NOU")
N("A", "modify", "plati", a_id("plati", pay), "observatii", "TEST-AUD plata NOUA")
N("A", "sync", timeout=250)
exp = {"locuri_de_veci": ("observatii", "TEST loc", "TEST-AUD loc NOU"), "concesionari": ("telefon", "0700000000", "0799000111"),
       "contracte": ("observatii", "TEST", "TEST-AUD ctr NOU"), "inhumati": ("observatii", "TEST", "TEST-AUD inh NOU"), "plati": ("observatii", "TEST plata", "TEST-AUD plata NOUA")}
for e, o in ents.items():
    rows = [r for r in audit(e, o["uid"]) if r["action"].endswith(".update")]
    d = json.loads(rows[0]["detail"]) if rows else {}
    col, vold, vnew = exp[e]
    check(f"audit UPDATE {e}: old.{col}='{vold}' -> new.{col}='{vnew}' (doar campul schimbat)", len(rows) == 1 and d["old"] == {col: vold} and d["new"] == {col: vnew}, json.dumps(d.get("old")) + json.dumps(d.get("new")))

# calea panoului admin (web) - actor = utilizatorul
sw, _ = login(NEW)
sw.post(B + "/admin/ui/concesionar", headers=SAME, data={"nume": "TEST-AUD-Web", "prenume": "W", "telefon": "0711"}, allow_redirects=False, timeout=60)
with conn() as cn:
    wid = cn.execute("select id, uid from concesionari where nume='TEST-AUD-Web'").fetchone()
sw.post(B + "/admin/ui/concesionar", headers=SAME, data={"id": str(wid[0]), "nume": "TEST-AUD-Web", "prenume": "W2", "telefon": "0711"}, allow_redirects=False, timeout=60)
sw.post(B + f"/admin/ui/loc/TEST-AUD-LOC/edit", headers=SAME, data={"observatii": "TEST-AUD web loc"}, timeout=60)
sw.post(B + f"/admin/ui/concesionar/{wid[0]}/delete", headers=SAME, allow_redirects=False, timeout=60)
rows = audit("concesionari", wid[1])
check("web: concesionar create/update/delete auditate cu actor = utilizatorul admin", [r["action"] for r in rows] == ["concesionari.create", "concesionari.update", "concesionari.delete"] and all(r["actor"] == USER and r["actor_type"] == "staff" for r in rows), str([(r["action"], r["actor"]) for r in rows]))
d = json.loads(rows[1]["detail"]) if len(rows) > 1 else {}
check("web: update arata prenume W -> W2", d.get("old") == {"prenume": "W"} and d.get("new") == {"prenume": "W2"}, json.dumps(d.get("old")) + json.dumps(d.get("new")))
dd = json.loads(rows[2]["detail"]) if len(rows) > 2 else {}
check("web: delete pastreaza valorile vechi, new=null", dd.get("new") is None and (dd.get("old") or {}).get("nume") == "TEST-AUD-Web")
lr = [r for r in audit("locuri_de_veci", loc["uid"]) if r["actor_type"] == "staff"]
check("web: editare loc auditata (actor staff, valori vechi/noi)", len(lr) == 1 and "observatii" in json.loads(lr[0]["detail"])["new"], str(json.loads(lr[0]["detail"]).get("new")) if lr else "lipsa")

# DELETE prin sync
for t, o in (("plati", pay), ("inhumati", inh), ("contracte", ctr), ("concesionari", conc)):
    N("A", "delete", t, a_id(t, o))
N("A", "sync", timeout=250)
for e, o in list(ents.items())[1:]:
    rows = [r for r in audit(e, o["uid"]) if r["action"].endswith(".delete")]
    d = json.loads(rows[0]["detail"]) if rows else {}
    check(f"audit DELETE {e}: old=valorile sterse, new=null, actor sync", len(rows) == 1 and d.get("new") is None and d.get("old") and rows[0]["actor_type"] == "sync", "")

# fara secrete in audit
with conn() as cn:
    blob = json.dumps(cn.execute("select * from audit_log").fetchall(), default=str)
    dburl = open("/app/backend/.env").read()
bad = [n for n, v in (("sync_key", KEY.split(".", 1)[1]), ("tmp_pw", TMP), ("new_pw", NEW)) if v in blob]
check("audit_log fara parole / cheie de sync", not bad, str(bad))
check("audit_log fara DATABASE_URL / cheie Supabase / JWT", "postgresql://" not in blob and "sb_secret" not in blob and "eyJ" not in blob and "pooler.supabase" not in blob)
print("REZUMAT:", sum(ok for _, ok in res), "/", len(res)); print("ESUATE:", [n for n, ok in res if not ok])
