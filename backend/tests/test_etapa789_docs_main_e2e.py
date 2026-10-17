# ruff: noqa: F811
"""E2E real al documentelor: MAIN (SQLite + desktop, proces separat) -> HTTP real -> PostgreSQL local (rol cimitir_cp_app) -> Mobile -> înapoi în MAIN.
Storage = Supabase SIMULAT local (NU LIVE). Rulează doar cu CIMITIR_TEST_PG_ADMIN_URL."""
from __future__ import annotations

import hashlib
import os
import socket
import uuid

import pytest
import requests
from fastapi import FastAPI

import cimitir_mobile_http as mih
import cimitir_mobile_registry as mr
import cimitir_sync_v2 as sv
import cimitir_sync_v2_docs as sd
import cimitir_sync_v2_http as svh
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_cleanup_race_pg import adm, pgdb  # noqa: F401
from test_etapa6b_field_storage import sup  # noqa: F401
from test_etapa6c_field_inbox_pg import fw  # noqa: F401
from test_sync_v2_main_e2e import RUN, H, Server, main_run, pg

pytestmark = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PostgreSQL local de test indisponibil")
PNG = b"\x89PNG\r\n\x1a\n" + b"foto-main" * 50
PDF = b"%PDF-1.4\ncontract scanat\n%%EOF"
PHONE_PNG = b"\x89PNG\r\n\x1a\n" + b"foto-telefon" * 40


@pytest.fixture()
def dworld(fw):
    with adm(fw["pg"]) as c:
        c.execute("TRUNCATE sync_v2_documents, sync_v2_ops, sync_v2_conflicts, sync_v2_permissions, sync_v2_runs, sync_tombstones, inhumati, plati, contracte, concesionari, "
                  "amenajari_avize, locuri_de_veci CASCADE")
    sync = sv.SyncV2Service(fw["svc"])
    app = FastAPI()
    app.include_router(svh.create_router(sync, sd.DocumentService(sync, fw["sup"])))
    app.include_router(mih.create_router(mr.MobileRegistryService(fw["svc"])))
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = Server(app, port); srv.start()
    yield {**fw, "srv": srv, "url": f"http://127.0.0.1:{port}", "data": f"/tmp/syncv2d-{RUN}-{uuid.uuid4().hex[:4]}", "client": None}
    srv.stop()


def docs_of(w, key):
    r = requests.get(f"{w['url']}/api/v1/sync-v2/documents", headers=H(key), timeout=10)
    assert r.status_code == 200, r.text
    return r.json()["result"]["documents"]


SETUP = """
import os
d = os.path.join(os.environ['CIMITIR_DATA_DIR'], 'files'); os.makedirs(d, exist_ok=True)
png = os.path.join(d, 'foto.png'); open(png, 'wb').write(PNG)
png2 = os.path.join(d, 'copie.png'); open(png2, 'wb').write(PNG)
pdf = os.path.join(d, 'contract.pdf'); open(pdf, 'wb').write(PDF)
idc = os.path.join(d, 'ci.pdf'); open(idc, 'wb').write(PDF + b'ci')
bad = os.path.join(d, 'fals.png'); open(bad, 'wb').write(b'MZ nu e imagine')
loc = q("INSERT INTO locuri_de_veci (cod_loc, points, sector) VALUES (?,?,?)", ("DOC-1", json.dumps([[0,0],[10,0],[10,10],[0,10],[0,0]]), "S1"))
cid = q("INSERT INTO concesionari (nume, prenume, cnp) VALUES (?,?,?)", ("Ionescu", "Maria", "1800101123456"))
q("INSERT INTO photos (cod_loc, file_path, caption) VALUES (?,?,?)", ("DOC-1", png, "Foto loc"))
q("INSERT INTO photos (cod_loc, file_path, caption) VALUES (?,?,?)", ("DOC-1", png2, "Aceeași poză, copie"))
q("INSERT INTO documents (entity_type, entity_id, doc_type, file_path, caption) VALUES (?,?,?,?,?)", ("concesionar", str(cid), "contract_concesiune", pdf, "Contract"))
q("INSERT INTO documents (entity_type, entity_id, doc_type, file_path, caption) VALUES (?,?,?,?,?)", ("concesionar", str(cid), "carte_identitate", idc, "CI"))
q("INSERT INTO photos (cod_loc, file_path, caption) VALUES (?,?,?)", ("DOC-1", bad, "Fals"))
rep = cloud_sync.sync_all()
out["rep"] = {k: rep.get(k) for k in ("ok", "state", "pending", "lines")}
out["loc_uid"] = q("SELECT uid FROM locuri_de_veci WHERE cod_loc='DOC-1'")[0]["uid"]
try:
    out["states"] = q("SELECT status, count(*) AS n FROM sync_v2_docs_state GROUP BY status")
except Exception:
    out["states"] = []
"""


def test_main_documents_reach_cloud_and_phone_then_phone_photo_returns_to_main(dworld):
    w = dworld
    main, phone, phone_b = w["T"]["A"]["main"], w["T"]["A"]["phone"], w["T"]["A"]["phone_b"]
    out = main_run(w, f"PNG={PNG!r}; PDF={PDF!r}\n" + SETUP)
    st = {r["status"]: r["n"] for r in out["states"]}
    assert st.get("done") == 2 and st.get("duplicate") == 1 and st.get("skipped") == 1 and st.get("error") == 1, out
    # în cloud: foto + contract, fără actul de identitate, fără duplicat, fără fișierul fals
    cloud = docs_of(w, main)
    assert sorted(d["doc_type"] for d in cloud) == ["contract_concesiune", "fotografie_loc"]
    assert not any("ci.pdf" in d["filename"] for d in cloud)
    # telefonul (rol limitat) vede fotografia locului, fără contractul concesionarului? (concesionari = read_registers => da)
    phone_docs = docs_of(w, phone)
    assert {d["sha256"] for d in phone_docs} == {hashlib.sha256(PNG).hexdigest(), hashlib.sha256(PDF).hexdigest()}
    f = requests.get(f"{w['url']}/api/v1/sync-v2/documents/{[d for d in phone_docs if d['kind'] == 'photo'][0]['uid']}/file", headers=H(phone), timeout=10)
    assert f.status_code == 200 and f.content == PNG
    # a doua sincronizare nu retrimite nimic
    again = main_run(w, "rep = cloud_sync.sync_all(); out['rep'] = rep.get('ok'); out['pushed'] = (rep.get('documents') or {}).get('push')")
    assert again["rep"] is False and again["pushed"]["sent"] == 0 and again["pushed"]["error"] == 1      # doar fișierul fals rămâne raportat; nimic retrimis
    assert len(docs_of(w, main)) == 2
    # telefonul încarcă o fotografie (cu drept) -> MAIN o primește
    r = requests.put(f"{w['url']}/api/v1/sync-v2/devices/{phone_b}/capabilities", json={"capabilities": ["view_map", "read_registers", "upload_documents"]}, headers=H(main), timeout=10)
    assert r.status_code == 200, r.text
    uid = uuid.uuid4().hex
    body = {"uid": uid, "entity": "locuri_de_veci", "entity_uid": out["loc_uid"], "doc_type": "fotografie_loc", "kind": "photo", "filename": "telefon.png", "mime": "image/png",
            "size": len(PHONE_PNG), "sha256": hashlib.sha256(PHONE_PNG).hexdigest(), "caption": "Din teren"}
    assert requests.post(f"{w['url']}/api/v1/sync-v2/documents/declare", json=body, headers=H(phone), timeout=10).status_code == 200
    assert requests.put(f"{w['url']}/api/v1/sync-v2/documents/{uid}/content", data=PHONE_PNG, headers=H(phone), timeout=10).status_code == 200
    back = main_run(w, "rep = cloud_sync.sync_all(); out['ok'] = rep.get('ok')\n"
                       "rows = q(\"SELECT cod_loc, file_path, caption, media_id FROM photos WHERE media_id=?\", (%r,))\n"
                       "out['rows'] = rows; out['bytes'] = open(rows[0]['file_path'],'rb').read().hex() if rows else ''\n"
                       "out['outbox'] = q(\"SELECT count(*) AS n FROM cloud_sync_outbox WHERE kind='media' AND entity_uid=?\", (%r,))[0]['n']" % (uid, uid))
    assert len(back["rows"]) == 1 and back["rows"][0]["cod_loc"] == "DOC-1" and bytes.fromhex(back["bytes"]) == PHONE_PNG
    assert back["outbox"] == 0                                                   # primirea nu generează retrimitere (fără ecou)
    assert len(docs_of(w, main)) == 3 and pg(w, "SELECT count(*) FROM sync_v2_documents")[0][0] == 3
    # ștergere locală în MAIN -> ștergere logică în cloud, octeții rămân
    gone = main_run(w, "q(\"DELETE FROM documents WHERE doc_type='contract_concesiune'\")\nrep = cloud_sync.sync_all(); out['ok'] = rep.get('ok')")
    assert pg(w, "SELECT count(*) FROM sync_v2_documents WHERE status='DELETED'")[0][0] == 1
    assert len(w["sup"].list_prefix(f"docs/{w['T']['A']['org']}/{w['T']['A']['cem']}")) == 3


def test_offline_main_keeps_documents_pending_then_resumes(dworld):
    w = dworld
    w["srv"].stop()
    out = main_run(w, f"PNG={PNG!r}; PDF={PDF!r}\n" + SETUP)
    assert out["rep"]["ok"] is False
    w["srv"].start()
    ok = main_run(w, "rep = cloud_sync.sync_all(); out['d'] = rep.get('documents')")
    assert ok["d"]["push"]["sent"] == 2
    assert pg(w, "SELECT count(*) FROM sync_v2_documents WHERE status='READY'")[0][0] == 2
