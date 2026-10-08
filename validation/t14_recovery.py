import json
import re
import shutil
import subprocess
import time

import requests

from common import conn, counts, get_secrets

S = get_secrets()
B = "http://127.0.0.1:8001"
ENV = "/app/backend/.env"
res = []


def check(name, cond, extra=""):
    res.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def sup(cmd):
    subprocess.run(["sudo", "supervisorctl", cmd, "backend"], capture_output=True, timeout=90)


def wait_up(n=40):
    for _ in range(n):
        try:
            if requests.get(B + "/api/health", timeout=4).status_code in (200, 503):
                return True
        except requests.RequestException:
            pass
        time.sleep(1.5)
    return False


env_text = open(ENV).read()
dburl = re.search(r'DATABASE_URL="([^"]+)"', env_text).group(1)
dbpw = re.search(r":([^:@/]+)@", dburl).group(1)
skey = re.search(r'SUPABASE_SECRET_KEY="([^"]+)"', env_text).group(1)
LEAKS = [dbpw, skey, "pooler.supabase.com", "txuxkjgftopznvmooexq", "postgresql://", "Traceback", "sb_secret", S["new_password"]]


def leaks(text):
    return [x[:12] + "..." for x in LEAKS if x in text]


def with_env(new_text):
    shutil.copy(ENV, "/app/validation/.env.backup")
    open(ENV, "w").write(new_text)
    sup("restart")
    wait_up()


def restore_env():
    shutil.copy("/app/validation/.env.backup", ENV)
    sup("restart")
    wait_up()


before = counts()
# ---------- restart curat
sup("restart")
check("restart backend prin supervisor", wait_up())
h = requests.get(B + "/api/health", timeout=30).json()
check("dupa restart: /api/health postgres online", h.get("database") == "postgres" and h.get("database_status") == "online", str(h))
sess = requests.Session()
r = sess.post(f"{B}/admin/access/login", data={"source_id": "cimitir", "username": S["user"], "password": S["new_password"]}, allow_redirects=False, timeout=60)
check("dupa restart: login admin", r.status_code == 303)
st = sess.get(B + "/admin/system", timeout=60).text
check("dupa restart: /admin/system se incarca", "PostgreSQL" in st or "POSTGRESQL" in st.upper())
r = requests.get(B + "/api/sync/ping", headers={"X-Sync-Key": S["sync_key"], "X-Sync-Client": "main-6a8f40bd14"}, timeout=30)
check("dupa restart: cheia de sincronizare ramane valida (ping)", r.status_code == 200, str(r.status_code))
check("dupa restart: sesiunea admin anterioara ramane valida (JWT secret stabil)", sess.get(B + "/admin/system", allow_redirects=False, timeout=60).status_code == 200)
tools = requests.get(B + "/api/sync/media/state", headers={"X-Sync-Key": S["sync_key"], "X-Sync-Client": "x"}, timeout=30)
check("dupa restart: Storage ONLINE (media/state)", tools.status_code == 200 and "error" not in tools.json()["storage"], tools.text[:80])
print("   JWT secret: persistent?", __import__("os").path.exists("/app/shared_data/.jwt_secret") or "vezi cimitir_config.jwt_secret()")

try:
    # ---------- DB indisponibila: parola gresita
    with_env(env_text.replace(dbpw, "PAROLA-GRESITA-TEST"))
    outs = {}
    outs["health"] = requests.get(B + "/api/health", timeout=60)
    outs["portal"] = requests.get(B + "/portal", timeout=60)
    outs["map"] = requests.get(B + "/api/public/map", timeout=60)
    outs["public_health"] = requests.get(B + "/api/public/health", timeout=60)
    outs["login"] = requests.post(B + "/admin/access/login", data={"source_id": "cimitir", "username": S["user"], "password": S["new_password"]}, allow_redirects=False, timeout=60)
    outs["sync_ping"] = requests.get(B + "/api/sync/ping", headers={"X-Sync-Key": S["sync_key"], "X-Sync-Client": "x"}, timeout=60)
    outs["push"] = requests.post(B + "/api/sync/entities/push", headers={"X-Sync-Key": S["sync_key"], "X-Sync-Client": "x"}, json={"ops": [{"op_id": "dbdown1", "entity": "concesionari", "uid": "dbdown-uid-1", "action": "upsert", "base_version": 0, "data": {"nume": "TEST-DBDOWN"}}]}, timeout=60)
    for k, r in outs.items():
        print(f"   DB down [{k}]: {r.status_code} {r.text[:110].strip()!r}")
    check("DB indisponibila: sync/harta/health raspund 503 controlat (nu 500)", all(outs[k].status_code == 503 for k in ("map", "public_health", "sync_ping", "push")), str({k: outs[k].status_code for k in outs}))
    check("DB indisponibila: nicio cerere nu returneaza 200 cu date false pentru scriere (push)", outs["push"].status_code >= 400 or outs["push"].json()["results"][0]["status"] in ("retry", "rejected"), str(outs["push"].status_code))
    check("DB indisponibila: raspunsuri controlate (nu 'Traceback' / fara conexiune bruta)", all(r.status_code < 600 and not leaks(r.text) for r in outs.values()), str({k: leaks(r.text) for k, r in outs.items() if leaks(r.text)}))
    check("DB indisponibila: /api/health raporteaza starea (nu crash)", outs["health"].status_code in (200, 503) and outs["health"].json().get("database_status") != "online", outs["health"].text[:100])
    logs = open("/var/log/supervisor/backend.err.log", errors="ignore").read()[-40000:] + open("/var/log/supervisor/backend.out.log", errors="ignore").read()[-40000:]
    check("DB indisponibila: parola gresita/URL nu apar in loguri", not [x for x in ("PAROLA-GRESITA-TEST", dbpw, "pooler.supabase.com:5432/postgres") if x in logs], str([x[:10] for x in ("PAROLA-GRESITA-TEST", dbpw, "pooler.supabase.com:5432/postgres") if x in logs]))
finally:
    restore_env()
h = requests.get(B + "/api/health", timeout=30).json()
check("recuperare: dupa restaurarea configurarii DB revine online fara interventie in date", h.get("database_status") == "online", str(h))
after = counts()
check("recuperare: nicio data corupta/adaugata in timpul indisponibilitatii DB (aceleasi randuri)", {k: v for k, v in after.items() if k not in ("audit_log", "settings", "sync_clients")} == {k: v for k, v in before.items() if k not in ("audit_log", "settings", "sync_clients")}, "")

try:
    # ---------- Storage indisponibil: cheie gresita
    with_env(env_text.replace(skey, "sb_secret_CHEIE-GRESITA-TEST-000000000000"))
    H = {"X-Sync-Key": S["sync_key"], "X-Sync-Client": "main-storedown"}
    mid = "TESTSTOREDOWN0001"
    data = b"%PDF-1.4 TEST storage down"
    sha = __import__("hashlib").sha256(data).hexdigest()
    requests.put(f"{B}/api/sync/media/{mid}/chunks/0", data=data, headers=H, timeout=60)
    r = requests.post(f"{B}/api/sync/media/{mid}/complete", headers=H, timeout=90, json={"entity_uid": "TESTENT", "filename": "TEST_down.pdf", "sha256": sha, "size": len(data), "chunks": 1, "table": "documents", "entity_type": "loc", "doc_type": "test"})
    print("   Storage down [upload]:", r.status_code, r.text[:140])
    check("Storage indisponibil: upload -> 503 controlat", r.status_code == 503, str(r.status_code))
    check("Storage indisponibil: raspunsul nu expune cheia/URL", not leaks(r.text), str(leaks(r.text)))
    with conn() as cn:
        check("Storage indisponibil: nicio inregistrare de metadate orfana", cn.execute("select count(*) from media_objects where id=%s", (mid,)).fetchone()[0] == 0)
    st = requests.get(B + "/api/sync/media/state", headers=H, timeout=60)
    print("   Storage down [state]:", st.status_code, st.text[:140])
    check("Storage indisponibil: /media/state (nu sondeaza live) nu expune cheia", st.status_code in (200, 503) and skey not in st.text and dbpw not in st.text, str(st.json().get("storage", {}).get("error"))[:90])
    sadm = requests.Session()
    sadm.post(f"{B}/admin/access/login", data={"source_id": "cimitir", "username": S["user"], "password": S["new_password"]}, allow_redirects=False, timeout=60)
    ts = sadm.post(B + "/admin/system/test-storage", headers={"Origin": B, "Sec-Fetch-Site": "same-origin"}, timeout=90)
    print("   Storage down [/admin/system/test-storage]:", ts.status_code, re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", ts.text))[:160])
    check("Storage indisponibil: /admin/system/test-storage arata eroare fara secrete", not leaks(ts.text), str(leaks(ts.text)))
    # cerere cetatean cu document cat timp Storage e jos
    cit = requests.Session()
    cit.post(B + "/api/public/account/login", json={"email": "test-validare@example.invalid", "password": "TestParola-Cetatean-2026!"}, timeout=60)
    form = {"nume": "TEST-StorageDown", "prenume": "X", "cod_loc": "TEST-LOC-001", "tip": "concesionare", "privacy_ack": "1"}
    pdf = open("/app/validation/files/TEST_document.pdf", "rb").read()
    rc = cit.post(B + "/api/public/cerere", data=form, files=[("files", ("TEST_sd.pdf", pdf, "application/pdf"))], timeout=120)
    print("   Storage down [cerere cu doc]:", rc.status_code, rc.text[:200])
    cj = rc.json() if rc.headers.get("content-type", "").startswith("application/json") else {}
    check("Storage indisponibil: cerere cu document NU raporteaza fals 'document salvat'", rc.status_code >= 400 or cj.get("documente", 0) == 0 or "respinse" in cj and cj["respinse"], f"{rc.status_code} documente={cj.get('documente')} respinse={cj.get('respinse')}")
    with conn() as cn:
        orf = cn.execute("select count(*) from documents d join cereri_concesionare c on c.id=cast(d.entity_id as int) where d.entity_type='cerere' and c.acked=0 and (d.media_id is null or d.media_id not in (select id from media_objects))").fetchone()[0]
    check("Storage indisponibil: nu raman documente de cerere fara obiect in Storage (date inconsistente)", orf == 0, f"documente orfane: {orf}")
finally:
    restore_env()
st = requests.get(B + "/api/sync/media/state", headers={"X-Sync-Key": S["sync_key"], "X-Sync-Client": "x"}, timeout=60)
check("recuperare: Storage revine ONLINE dupa restaurarea cheii", st.status_code == 200 and "error" not in st.json()["storage"])
print("REZUMAT:", sum(ok for _, ok in res), "/", len(res))
print("ESUATE:", [n for n, ok in res if not ok])
