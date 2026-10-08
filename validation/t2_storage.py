import hashlib, json, os, re, uuid, requests
from common import conn, get_secrets
S = get_secrets()
H = {"X-Sync-Key": S["sync_key"], "X-Sync-Client": "main-storetest1"}
B = "http://127.0.0.1:8001/api/sync/media"
SUP = re.search(r'SUPABASE_URL="([^"]+)"', open("/app/backend/.env").read()).group(1)
SK = re.search(r'SUPABASE_SECRET_KEY="([^"]+)"', open("/app/backend/.env").read()).group(1)
res = []
def check(name, cond, extra=""):
    res.append((name, bool(cond))); print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))

st = requests.get(f"{B}/state", headers=H, timeout=60).json()
print("   state:", json.dumps(st)[:300])
check("Storage driver = supabase, bucket cimitir-private, ONLINE", st["storage"].get("driver") == "supabase" and "error" not in st["storage"], json.dumps(st["storage"])[:200])
check("raspuns /state fara secrete", SK not in json.dumps(st) and "sb_secret" not in json.dumps(st))

data = os.urandom(300_000) + b"%PDF-1.4 TEST FILE"      # ~300 KB
sha = hashlib.sha256(data).hexdigest()
mid = "TESTMEDIA" + uuid.uuid4().hex[:12]
chunks = [data[i:i + 100_000] for i in range(0, len(data), 100_000)]
for i, c in enumerate(chunks):
    r = requests.put(f"{B}/{mid}/chunks/{i}", data=c, headers=H, timeout=60); assert r.status_code == 200, r.text
meta = {"entity_uid": "TESTENTITY-" + mid, "filename": "TEST_document.pdf", "sha256": sha, "size": len(data), "chunks": len(chunks), "table": "documents",
        "entity_type": "loc", "doc_type": "test", "uploaded_by": "validation"}
# sha gresit -> respins si nimic scris
bad = dict(meta, sha256="0" * 64)
r = requests.post(f"{B}/{mid}/complete", json=bad, headers=H, timeout=60)
check("upload cu SHA256 gresit respins (422 checksum_mismatch)", r.status_code == 422, str(r.status_code))
with conn() as cn:
    check("dupa respingere: niciun metadata in PostgreSQL", cn.execute("select count(*) from media_objects where id=%s", (mid,)).fetchone()[0] == 0)
# bucati lipsa
mid2 = "TESTMEDIA" + uuid.uuid4().hex[:12]
requests.put(f"{B}/{mid2}/chunks/0", data=chunks[0], headers=H, timeout=60)
r = requests.post(f"{B}/{mid2}/complete", json=dict(meta, chunks=3), headers=H, timeout=60)
check("bucati lipsa -> 409 chunks_missing", r.status_code == 409, str(r.status_code))
# upload corect (bucatile mid sunt inca acolo dupa 422? serverul sterge directorul la mismatch -> reincarcam)
for i, c in enumerate(chunks):
    requests.put(f"{B}/{mid}/chunks/{i}", data=c, headers=H, timeout=60)
r = requests.post(f"{B}/{mid}/complete", json=meta, headers=H, timeout=120)
check("UPLOAD prin FastAPI", r.status_code == 200 and r.json().get("ok") and not r.json().get("duplicate"), f"{r.status_code} {r.text[:150]}")
obj = r.json().get("object", {})
print("   obiect:", {k: obj.get(k) for k in ("id", "filename", "mime", "size", "sha256")} )
with conn() as cn:
    row = cn.execute("select filename, mime, size, sha256, storage_path from media_objects where id=%s", (mid,)).fetchone()
check("metadate in PostgreSQL: filename/mime/size/sha256", row and row[0] == "TEST_document.pdf" and row[1] == "application/pdf" and row[2] == len(data) and row[3] == sha, str(row[:4]) if row else "none")
path = row[4]
# a doua complete = duplicate
r = requests.post(f"{B}/{mid}/complete", json=meta, headers=H, timeout=60)
check("complete repetat -> duplicate:true (idempotent)", r.status_code == 200 and r.json().get("duplicate") is True)
# download prin FastAPI
r = requests.get(f"{B}/{mid}", headers=H, timeout=120)
dl = hashlib.sha256(r.content).hexdigest()
check("DOWNLOAD prin FastAPI 200", r.status_code == 200, str(r.status_code))
check("SHA256 download == SHA256 original", dl == sha and r.headers.get("X-Sha256") == sha, f"{dl[:12]} vs {sha[:12]}")
check("MIME si size la download", r.headers.get("content-type", "").startswith("application/pdf") and len(r.content) == len(data) and r.headers.get("Content-Length") == str(len(data)), f"{r.headers.get('content-type')} {len(r.content)}")
check("download fara cheie de sincronizare respins", requests.get(f"{B}/{mid}", timeout=30).status_code in (401, 403, 404))
check("download cu cheie gresita respins", requests.get(f"{B}/{mid}", headers={"X-Sync-Key": "cimitir.gresita", "X-Sync-Client": "x"}, timeout=30).status_code in (401, 403))
# bucket privat: acces anonim direct la Supabase
bk = requests.get(f"{SUP}/storage/v1/bucket/cimitir-private", headers={"apikey": SK, "Authorization": "Bearer " + SK}, timeout=30).json()
check("bucket cimitir-private are public=false", bk.get("public") is False, str(bk.get("public")))
for label, url in (("public URL", f"{SUP}/storage/v1/object/public/cimitir-private/{path}"), ("authenticated URL fara token", f"{SUP}/storage/v1/object/cimitir-private/{path}"),
                   ("authenticated URL cu 'anon' fals", f"{SUP}/storage/v1/object/authenticated/cimitir-private/{path}")):
    rr = requests.get(url, timeout=30)
    check(f"acces ANONIM {label} -> refuzat (nu 200)", rr.status_code in (400, 401, 403, 404) and hashlib.sha256(rr.content).hexdigest() != sha, str(rr.status_code))
# lista fisiere anonim
rr = requests.post(f"{SUP}/storage/v1/object/list/cimitir-private", json={"prefix": "", "limit": 5}, timeout=30)
check("listare anonima bucket refuzata", rr.status_code in (400, 401, 403), str(rr.status_code))
# obiectul exista in storage (cu cheia server) 
rr = requests.get(f"{SUP}/storage/v1/object/cimitir-private/{path}", headers={"apikey": SK, "Authorization": "Bearer " + SK}, timeout=60)
check("obiectul exista in Supabase Storage (verificat cu cheia secreta)", rr.status_code == 200 and hashlib.sha256(rr.content).hexdigest() == sha, str(rr.status_code))
# stergere
r = requests.delete(f"{B}/{mid}", headers=H, timeout=60)
check("STERGERE fisier TEST prin API", r.status_code == 200 and r.json().get("deleted"), r.text[:100])
rr = requests.get(f"{SUP}/storage/v1/object/cimitir-private/{path}", headers={"apikey": SK, "Authorization": "Bearer " + SK}, timeout=60)
check("fisierul nu mai exista in storage", rr.status_code in (400, 404), str(rr.status_code))
with conn() as cn:
    check("metadate TEST sterse din PostgreSQL", cn.execute("select count(*) from media_objects where id=%s", (mid,)).fetchone()[0] == 0)
print("REZUMAT:", sum(ok for _, ok in res), "/", len(res)); print("ESUATE:", [n for n, ok in res if not ok])
