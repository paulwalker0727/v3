"""Nod MAIN headless (sincronizare reala a desktopului, fara GUI Qt).

    python node.py A <cmd> [args...]     -> date in /app/validation/nodes/A  (SQLite propriu)
Raspunde mereu cu o linie JSON pe stdout.
"""
import json
import os
import sys

NAME = sys.argv[1]
CMD = sys.argv[2]
ARGS = sys.argv[3:]
DATA = f"/app/validation/nodes/{NAME}"
os.makedirs(DATA, exist_ok=True)
os.environ["CIMITIR_DATA_DIR"] = DATA
os.environ["CIMITIR_MODE"] = "local"
os.environ["CIMITIR_DB_ENGINE"] = "sqlite"
os.environ["DATABASE_URL"] = ""
os.environ["CIMITIR_IGNORE_PROJECT_ENV"] = "1"
for p in ("/app/shared", "/app/desktop"):
    sys.path.insert(0, p)

import cloud_sync as cs  # noqa: E402
import database as db  # noqa: E402
import entity_sync as es  # noqa: E402
import media_sync as ms  # noqa: E402

db.init_db()
cs.ensure_tables()


def out(**kw):
    print("@@" + json.dumps(kw, default=str, ensure_ascii=False))


def rows(sql, params=()):
    c = db.get_connection()
    try:
        return [dict(r) for r in c.execute(sql, params).fetchall()]
    finally:
        c.close()


def dump():
    es.ensure_tracking()
    d = {
        "locuri_de_veci": rows("SELECT id,uid,version,cod_loc,disponibilitate,observatii,sector FROM locuri_de_veci WHERE cod_loc LIKE 'TEST%' ORDER BY cod_loc"),
        "concesionari": rows("SELECT id,uid,version,nume,prenume,telefon FROM concesionari WHERE nume LIKE 'TEST%' ORDER BY nume"),
        "contracte": rows("SELECT c.id,c.uid,c.version,c.numar_contract,c.cod_loc,c.observatii,k.uid AS concesionar_uid,l.uid AS loc_uid "
                          "FROM contracte c LEFT JOIN concesionari k ON k.id=c.concesionar_id LEFT JOIN locuri_de_veci l ON l.id=c.id_loc "
                          "WHERE c.numar_contract LIKE 'TEST%' ORDER BY c.numar_contract"),
        "inhumati": rows("SELECT i.id,i.uid,i.version,i.nume,i.prenume,l.uid AS loc_uid,l.cod_loc FROM inhumati i LEFT JOIN locuri_de_veci l ON l.id=i.id_loc "
                         "WHERE i.nume LIKE 'TEST%' ORDER BY i.nume"),
        "plati": rows("SELECT p.id,p.uid,p.version,p.suma,p.cod_loc,p.chitanta_nr,p.observatii FROM plati p WHERE p.chitanta_nr LIKE 'TEST%' ORDER BY p.chitanta_nr"),
    }
    d["outbox"] = rows("SELECT kind,entity,entity_uid,action,base_version,state,op_id,attempts,note FROM cloud_sync_outbox ORDER BY id")
    return d


def uid_of(table, local_id):
    r = rows(f"SELECT uid FROM {table} WHERE id=?", (int(local_id),))
    return r[0]["uid"] if r else None


try:
    if CMD == "setup":
        url, key = ARGS[0], ARGS[1]
        cs.save_config(url, key, False, False)
        ok, msg = cs.ping()
        es.ensure_tracking()
        out(ok=ok, msg=msg, client=cs.client_id())
    elif CMD == "seturl":
        cfg = cs.get_config()
        cs.save_config(ARGS[0], cfg["key"], False, False)
        out(ok=True)
    elif CMD == "sync":
        rep = cs.sync_all()
        out(ok=rep.get("ok"), lines=rep.get("lines"), pending=rep.get("pending"), conflicts=es.conflict_count())
    elif CMD == "bootstrap":
        out(queued=es.enqueue_bootstrap())
    elif CMD == "mkloc":
        code = ARGS[0]
        pts = [(10.0, 10.0), (60.0, 10.0), (60.0, 40.0), (10.0, 40.0)]
        lid = db.save_plot_to_db(code, pts, "#22aa55")
        db.save_loc_metadata(code, {"sector": "TEST-S", "parcela": "T1", "rand": "1", "numar": "1", "disponibilitate": "liber",
                                    "observatii": "TEST loc"})
        out(id=lid, uid=uid_of("locuri_de_veci", lid))
    elif CMD == "mkconc":
        cid = db.save_concesionar({"nume": ARGS[0], "prenume": "Prenume", "telefon": "0700000000", "observatii": "TEST"})
        out(id=cid, uid=uid_of("concesionari", cid))
    elif CMD == "mkcontract":
        num, cod, cid = ARGS[0], ARGS[1], int(ARGS[2])
        kid = db.save_contract({"numar_contract": num, "data_contract": "2026-10-01", "data_inceput": "2026-10-01",
                                "data_sfarsit": "2036-10-01", "cod_loc": cod, "concesionar_id": cid, "observatii": "TEST"})
        out(id=kid, uid=uid_of("contracte", kid))
    elif CMD == "mkinh":
        iid = db.save_inhumat(ARGS[0], {"nume": ARGS[1], "prenume": "Defunct", "data_deces": "2026-09-30",
                                        "data_inhumare": "2026-10-02", "observatii": "TEST"})
        out(id=iid, uid=uid_of("inhumati", iid))
    elif CMD == "mkplata":
        pid = db.add_plata(ARGS[0], float(ARGS[1]), "2026-10-03", "taxa", ARGS[2], "TEST plata", int(ARGS[3]) if len(ARGS) > 3 and ARGS[3] != "-" else None)
        out(id=pid, uid=uid_of("plati", pid))
    elif CMD == "modify":
        table, local_id, col, val = ARGS[0], int(ARGS[1]), ARGS[2], ARGS[3]
        c = db.get_connection()
        try:
            c.execute(f"UPDATE {table} SET {col}=? WHERE id=?", (val, local_id))
        finally:
            c.close()
        out(ok=True)
    elif CMD == "delete":
        table, local_id = ARGS[0], int(ARGS[1])
        fn = {"locuri_de_veci": None, "concesionari": db.delete_concesionar, "contracte": db.delete_contract,
              "inhumati": db.delete_inhumat, "plati": db.delete_plata}[table]
        if fn:
            res = fn(local_id)
        else:
            cod = rows("SELECT cod_loc FROM locuri_de_veci WHERE id=?", (local_id,))[0]["cod_loc"]
            res = db.delete_plot(cod)
        out(ok=bool(res))
    elif CMD == "dump":
        out(**dump())
    elif CMD == "conflicts":
        out(conflicts=es.list_conflicts())
    elif CMD == "resolve":
        out(ok=es.resolve_conflict(ARGS[0], ARGS[1]))
    elif CMD == "status":
        out(summary=es.status_summary(), pending=cs.pending_count(), online=es.is_online())
    elif CMD == "addphoto":     # cod_loc src_path  (ca in UI: copiaza in folderul media, apoi inregistreaza)
        dest = db.import_media_file(ARGS[1], "photos")
        pid = db.save_photo(ARGS[0], dest, {"caption": "TEST photo"})
        out(id=pid, path=dest)
    elif CMD == "adddoc":       # entity_type entity_id doc_type src_path
        did = db.add_document(ARGS[0], ARGS[1], ARGS[2], ARGS[3], caption="TEST doc")
        out(id=did)
    elif CMD == "docs":
        import hashlib
        info = []
        for tb in ("documents", "photos"):
            for r in rows(f"SELECT id, media_id, file_path{', sha256, size' if tb == 'documents' else ''} FROM {tb}"):
                p = r["file_path"]
                ex = bool(p and os.path.isfile(p))
                sha = hashlib.sha256(open(p, "rb").read()).hexdigest() if ex else None
                info.append({"table": tb, "id": r["id"], "media_id": r["media_id"], "path": p, "exists": ex, "sha_local": sha, "sha_db": r.get("sha256"), "size": os.path.getsize(p) if ex else None})
        out(files=info, data_dir=DATA, media_dir_exists=os.path.isdir(os.path.join(DATA, "media")))
    elif CMD == "ensure":       # media_id [hint]  -> deschide documentul (aduce din storage in cache daca lipseste)
        p = ms.ensure_local(ARGS[0], ARGS[1] if len(ARGS) > 1 else "")
        out(path=p, exists=bool(p and os.path.isfile(p)))
    elif CMD == "rm":
        os.remove(ARGS[0]); out(ok=True)
    elif CMD == "rawsql":
        out(rows=rows(ARGS[0]))
    elif CMD == "datadir":
        out(path=DATA)
    else:
        out(error="unknown cmd " + CMD)
except Exception as exc:  # noqa: BLE001
    import traceback
    out(error=f"{type(exc).__name__}: {exc}", tb=traceback.format_exc()[-800:])
