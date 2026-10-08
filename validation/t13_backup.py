import hashlib, json, re, requests, subprocess, os, glob
from common import conn, get_secrets
S = get_secrets(); B = "http://127.0.0.1:8001"
res = []
def check(name, cond, extra=""):
    res.append((name, bool(cond))); print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
s = requests.Session()
s.post(f"{B}/admin/access/login", data={"source_id": "cimitir", "username": S["user"], "password": S["new_password"]}, allow_redirects=False, timeout=30)
before = set(glob.glob("/app/shared_data-backups/*"))
r = s.post(B + "/admin/system/backup", headers={"Origin": B, "Sec-Fetch-Site": "same-origin"}, timeout=300)
txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.text))
print("   raspuns:", txt[:260])
check("Backup acum (pg_dump) prin /admin/system/backup reuseste", r.status_code == 200 and "Copie creată" in txt and "pg_dump" in txt, str(r.status_code))
check("raspunsul backup fara parola/URL", not any(x in r.text for x in ("sb_secret", "pooler.supabase.com", "postgresql://")))
new = sorted(set(glob.glob("/app/shared_data-backups/*")) - before)
check("s-a creat un folder de backup nou", len(new) == 1, str(new))
d = new[0]
man = json.load(open(d + "/manifest.json"))
dump = d + "/database.dump"
sha = hashlib.sha256(open(dump, "rb").read()).hexdigest()
check("manifest.json: SHA256 database.dump corect (fisier necorupt)", man["files"]["database.dump"] == sha and man["engine"] == "postgres", f"{os.path.getsize(dump)} B")
pr = subprocess.run(["/usr/lib/postgresql/17/bin/pg_restore", "--list", dump], capture_output=True, text=True)
tables = re.findall(r"TABLE DATA public (\w+)", pr.stdout)
check("pg_restore --list citeste dump-ul (arhiva valida) si contine tabelele", pr.returncode == 0 and len(tables) >= 30, f"{len(tables)} tabele cu date")
check("dump contine metadatele Storage (media_objects) si sync_ops/audit", all(t in tables for t in ("media_objects", "sync_ops", "audit_log", "users", "documents")))
open("/app/validation/last_backup_dir.txt", "w").write(d)
with conn() as cn:
    live = {t: cn.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tables}
json.dump(live, open("/app/validation/live_counts.json", "w"))
print("REZUMAT:", sum(ok for _, ok in res), "/", len(res)); print("ESUATE:", [n for n, ok in res if not ok])
