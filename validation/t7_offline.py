import hashlib
import json
import re
import subprocess
import time

import requests

from common import conn, get_secrets
from nodelib import N

S = get_secrets()
ids = json.load(open("/app/validation/ids.json"))
SUP = re.search(r'SUPABASE_URL="([^"]+)"', open("/app/backend/.env").read()).group(1)
SK = re.search(r'SUPABASE_SECRET_KEY="([^"]+)"', open("/app/backend/.env").read()).group(1)
res = []
PHOTO, DOC = "/app/validation/files/TEST_photo.png", "/app/validation/files/TEST_document.pdf"


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def check(name, cond, extra=""):
    res.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def sup(cmd):
    subprocess.run(["sudo", "supervisorctl", cmd, "backend"], capture_output=True, timeout=60)


def wait_up():
    for _ in range(40):
        try:
            if requests.get("http://127.0.0.1:8001/api/health", timeout=3).status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(1.5)
    return False


try:
    base = N("A", "sync")
    check("A online si sincronizat (punct de plecare)", base.get("ok") and base.get("pending") == 0, str(base.get("pending")))
    sup("stop")
    time.sleep(2)
    try:
        requests.get("http://127.0.0.1:8001/api/health", timeout=3)
        up = True
    except requests.RequestException:
        up = False
    check("conexiunea a fost intrerupta (backend oprit)", not up)

    # ---- MAIN A OFFLINE
    a_loc = N("A", "rawsql", f"SELECT id FROM locuri_de_veci WHERE uid='{ids['loc']['uid']}'")["rows"][0]["id"]
    N("A", "modify", "locuri_de_veci", a_loc, "observatii", "TEST-OFFLINE loc modificat")
    conc = N("A", "mkconc", "TEST-OFF-Conc")
    ctr = N("A", "mkcontract", "TEST-OFF-CTR", "TEST-LOC-001", conc["id"])
    pay = N("A", "mkplata", "TEST-LOC-001", "75", "TEST-OFF-CH", ctr["id"])
    inh = N("A", "mkinh", "TEST-LOC-001", "TEST-OFF-Inh")
    ph = N("A", "addphoto", "TEST-LOC-001", PHOTO)
    dc = N("A", "adddoc", "loc", "TEST-LOC-001", "TEST", DOC)
    check("offline: modificari create fara blocaj", all("error" not in x for x in (conc, ctr, pay, inh, ph, dc)), str([ph, dc]))
    t0 = time.time()
    off = N("A", "sync", timeout=170)
    dt = time.time() - t0
    check("offline: sync nu se blocheaza (revine controlat) si raporteaza ne-ok", off.get("ok") is False and dt < 60, f"{dt:.1f}s")
    d1 = N("A", "dump")
    f1 = N("A", "docs")
    check("offline: datele noi sunt vizibile local", len(d1["concesionari"]) >= 2 and any(c["numar_contract"] == "TEST-OFF-CTR" for c in d1["contracte"]) and any(p["chitanta_nr"] == "TEST-OFF-CH" for p in d1["plati"]) and any(i["nume"] == "TEST-OFF-Inh" for i in d1["inhumati"]))
    pend = [o for o in d1["outbox"] if o["state"] in ("pending", "error")]
    check("offline: operatiile sunt in outbox (>=5 entitati + 2 media)", len([o for o in pend if o["kind"] == "entity"]) >= 5 and len([o for o in d1["outbox"] if o["kind"] != "entity"]) >= 2, str([(o["kind"], o["entity"], o["state"]) for o in d1["outbox"]]))
    mine = [x for x in f1["files"] if x["path"] and "TEST" in (x["path"] or "")]
    check("offline: fotografia si documentul salvate local in folderul media, existente", len(mine) == 2 and all(x["exists"] for x in mine), str([(x["table"], x["exists"], (x["path"] or "")[-40:]) for x in mine]))
    check("offline: documentul local are SHA256 identic cu sursa", {x["sha_local"] for x in mine} == {sha(PHOTO), sha(DOC)})
    ops_before = sorted(o["op_id"] for o in d1["outbox"])

    # ---- inchide complet MAIN, redeschide tot offline (procese noi)
    d2, f2 = N("A", "dump"), N("A", "docs")
    check("redeschidere offline: aceleasi date locale", [c["uid"] for c in d2["concesionari"]] == [c["uid"] for c in d1["concesionari"]] and len(d2["contracte"]) == len(d1["contracte"]))
    check("redeschidere offline: outbox intact (aceleasi op_id)", sorted(o["op_id"] for o in d2["outbox"]) == ops_before, f"{len(ops_before)} operatii")
    check("redeschidere offline: documentele exista, SHA256 neschimbat", {x["sha_local"] for x in f2["files"] if x["path"] and "TEST" in x["path"]} == {sha(PHOTO), sha(DOC)})
    op_ids_entities = [o["op_id"] for o in d2["outbox"] if o["kind"] == "entity"]

    # ---- reconectare
    sup("start")
    check("backend repornit", wait_up())
    s1 = N("A", "sync", timeout=250)
    print("   sync lines:", [x for x in s1.get("lines", []) if "ntit" in x or "ocument" in x])
    check("reconectare: sincronizare automata ok, 0 operatii restante", s1.get("ok") and s1.get("pending") == 0, f"ok={s1.get('ok')} pending={s1.get('pending')}")
    d3, f3 = N("A", "dump"), N("A", "docs")
    check("outbox gol dupa confirmare", len(d3["outbox"]) == 0, str(len(d3["outbox"])))
    with conn() as cn:
        k = cn.execute("select k.nume, l.cod_loc from contracte c join concesionari k on k.id=c.concesionar_id join locuri_de_veci l on l.id=c.id_loc where c.numar_contract='TEST-OFF-CTR'").fetchone()
        p = cn.execute("select c.numar_contract, p.suma from plati p join contracte c on c.id=p.contract_id where p.chitanta_nr='TEST-OFF-CH'").fetchone()
        i = cn.execute("select l.cod_loc from inhumati i join locuri_de_veci l on l.id=i.id_loc where i.nume='TEST-OFF-Inh'").fetchone()
        o = cn.execute("select observatii from locuri_de_veci where uid=%s", (ids["loc"]["uid"],)).fetchone()
        check("PG: contract->concesionar+loc (ordine sigura parinte-inainte-de-copil)", k == ("TEST-OFF-Conc", "TEST-LOC-001"), str(k))
        check("PG: plata->contract, inhumat->loc, modificarea locului", p == ("TEST-OFF-CTR", 75.0) and i and i[0] == "TEST-LOC-001" and o[0] == "TEST-OFFLINE loc modificat", f"{p} {i} {o}")
        cnt = cn.execute("select (select count(*) from contracte where numar_contract='TEST-OFF-CTR'),(select count(*) from plati where chitanta_nr='TEST-OFF-CH'),(select count(*) from concesionari where nume='TEST-OFF-Conc'),(select count(*) from inhumati where nume='TEST-OFF-Inh')").fetchone()
        check("PG: zero duplicate dupa reluare", cnt == (1, 1, 1, 1), str(cnt))
        ops = cn.execute("select count(*) from sync_ops where op_id = any(%s)", (op_ids_entities,)).fetchone()[0]
        check("PG: op_id-urile din outbox respectate (inregistrate in sync_ops)", ops == len(op_ids_entities), f"{ops}/{len(op_ids_entities)}")
        mo = cn.execute("select id, filename, size, sha256, storage_path from media_objects where entity_uid like %s or filename like 'TEST%%' order by filename", (ids["loc"]["uid"],)).fetchall()
    check("documentele au ajuns in metadate PostgreSQL (2 obiecte)", len(mo) == 2, str([(m[1], m[2]) for m in mo]))
    local = {x["sha_local"]: x for x in f3["files"] if x["path"] and "TEST" in x["path"]}
    for m in mo:
        r = requests.get(f"{SUP}/storage/v1/object/cimitir-private/{m[4]}", headers={"apikey": SK, "Authorization": "Bearer " + SK}, timeout=60)
        remote_sha = hashlib.sha256(r.content).hexdigest()
        check(f"Supabase Storage: {m[1]} exista, SHA256 remote == SHA256 local == metadate", r.status_code == 200 and remote_sha == m[3] and m[3] in local, f"{r.status_code}")
    check("cache/fisiere locale raman utilizabile dupa sync", all(x["exists"] for x in f3["files"] if x["path"] and "TEST" in x["path"]))
    json.dump({"media": [m[0] for m in mo], "paths": [m[4] for m in mo]}, open("/app/validation/media_ids.json", "w"))

    # ---- Pas 8: MAIN B descarca din Storage prin FastAPI
    sb = N("B", "sync", timeout=250)
    check("B sync ok", sb.get("ok"), str([x for x in sb.get("lines", []) if "ocument" in x]))
    fb = N("B", "docs")
    mine_b = [x for x in fb["files"] if x["media_id"] in [m[0] for m in mo]]
    check("B are randurile de documente (metadate) dupa sync", len(mine_b) == 2, str(len(mine_b)))
    doc_b = [x for x in mine_b if x["table"] == "documents"][0]
    print("   B document dupa sync (prefetch):", {"exists": doc_b["exists"], "path": (doc_b["path"] or "")[-60:]})
    if doc_b["exists"]:
        N("B", "rm", doc_b["path"])   # simulam „documentul nu exista local" (prefetch dezactivat)
    fb2 = N("B", "docs")
    gone = [x for x in fb2["files"] if x["media_id"] == doc_b["media_id"]][0]
    check("B: documentul NU exista local inainte de deschidere", not gone["exists"])
    e1 = N("B", "ensure", doc_b["media_id"], "TEST_document.pdf")
    check("B: deschidere -> descarcat prin FastAPI din Supabase Storage in cache", e1.get("exists") and "media_cache" in (e1.get("path") or ""), str(e1.get("path"))[-60:])
    check("B: SHA256 document din cache == original", e1.get("exists") and sha(e1["path"]) == sha(DOC))
    sup("stop")
    time.sleep(2)
    e2 = N("B", "ensure", doc_b["media_id"], "TEST_document.pdf")
    check("B: internet oprit, acelasi document se deschide din cache offline", e2.get("exists") and e2.get("path") == e1.get("path") and sha(e2["path"]) == sha(DOC), str(e2.get("path"))[-60:])
    N("B", "rm", e2["path"])
    e3 = N("B", "ensure", doc_b["media_id"], "TEST_document.pdf")
    check("control negativ: offline + fara cache -> indisponibil controlat (fara crash)", e3.get("path") is None and "error" not in e3, str(e3)[:100])
finally:
    sup("start")
    wait_up()
print("REZUMAT:", sum(ok for _, ok in res), "/", len(res))
print("ESUATE:", [n for n, ok in res if not ok])
