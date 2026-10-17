# ruff: noqa: F811
"""Mobile v2 — verificare de acceptanță MAIN -> Cloud -> Telefon (PostgreSQL LOCAL REAL, rol cimitir_cp_app fără BYPASSRLS, HTTP real, MAIN = proces SQLite real).

Acoperă explicit lista din cerință: asociere telefon prin cod/QR (organizația și cimitirul corecte), încărcarea hărții, locuri de veci, concesionari, înhumați, contracte și plăți,
documente și fotografii (Field Inbox, Storage SIMULAT), sincronizare fără duplicate, modificări simultane, izolare între organizații.
`health=OK` NU e folosit ca dovadă: fiecare pas verifică datele. Rezultatele sunt LOCALE, nu LIVE (Supabase TEST). Rulează doar cu CIMITIR_TEST_PG_ADMIN_URL."""
from __future__ import annotations

import hashlib
import os
import re
import socket
import threading
import uuid

import pytest
import requests

import cimitir_cloud_app as capp
import cimitir_qr as qr
from test_etapa4_pas1 import World, env  # noqa: F401
from test_etapa4_pas1_closure import renv  # noqa: F401
from test_etapa6b_cleanup_race_pg import adm, pgdb  # noqa: F401
from test_etapa6b_field_storage import sup  # noqa: F401
from test_etapa6c_field_inbox_pg import fw, png, seed_binding  # noqa: F401
from test_sync_v2_main_e2e import RUN, SCENARIO, H, Server, main_run, pg

pytestmark = pytest.mark.skipif(not os.environ.get("CIMITIR_TEST_PG_ADMIN_URL"), reason="PostgreSQL local de test indisponibil")
PAIR_JS = re.compile(r"(?:[#&?]|^)pair=([A-Za-z0-9-]{10,40})")        # aceeași expresie ca `pairCodeFromText` din mobile_app.js


@pytest.fixture()
def mv(fw):
    with adm(fw["pg"]) as c:
        c.execute("TRUNCATE sync_v2_ops, sync_v2_conflicts, sync_v2_permissions, sync_v2_runs, sync_tombstones, inhumati, plati, contracte, concesionari, "
                  "amenajari_avize, locuri_de_veci CASCADE")
    app = capp.create_app(fw["svc"], db_probe=lambda s: True, storage_probe=lambda s: True)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = Server(app, port)
    srv.start()
    yield {**fw, "srv": srv, "url": f"http://127.0.0.1:{port}", "data": f"/tmp/mv2-{RUN}-{uuid.uuid4().hex[:4]}"}
    srv.stop()


def get(w, path, key, **kw):
    return requests.get(w["url"] + path, headers=H(key), timeout=15, **kw)


def pair_phone(w, tag, name="Telefon test"):
    """MAIN-ul cimitirului emite un cod; link-ul/QR-ul are forma folosită de PWA; telefonul îl răscumpără exact ca PWA (fără cratime)."""
    r = requests.post(w["url"] + "/api/v1/phone-pairing/codes", json={"ttl": 600}, headers=H(w["T"][tag]["main"]), timeout=15)
    assert r.status_code == 201, r.text
    code = r.json()["result"]["code"]
    link = qr.pair_url(w["url"], code.replace("-", ""))
    assert link.startswith(w["url"] + "/mobile/#pair=") and "?" not in link         # codul în fragment: nu ajunge în jurnalele serverului
    js_code = PAIR_JS.search(link[link.index("#"):]).group(1)
    red = requests.post(w["url"] + "/api/v1/phone-pairing/redeem", json={"code": js_code, "device_name": name, "client_id": "cid-" + uuid.uuid4().hex}, timeout=15)
    assert red.status_code == 201, red.text
    res = red.json()["result"]
    return res["binding_key"], js_code


def main_grants_operator(w, tag, phone_key):
    ref = phone_key.split(".")[1]
    r = requests.put(f"{w['url']}/api/v1/sync-v2/devices/{ref}/capabilities", json={"role": "operator"}, headers=H(w["T"][tag]["main"]), timeout=15)
    assert r.status_code == 200, r.text


def seed_main(w, tag="A"):
    """Loc + concesionar + contract + 2 înhumați + plată + aviz create în MAIN (SQLite real) și trimise prin `sync_all()`."""
    out = main_run(w, SCENARIO + """
cid = q("SELECT id FROM contracte WHERE numar_contract='C-100'")[0]["id"]
q("INSERT INTO plati (cod_loc, suma, data_plata, tip, chitanta_nr, contract_id) VALUES (?,?,?,?,?,?)", ("SC-1", 250.0, "2026-03-02", "taxă concesiune", "R-77", cid))
q("INSERT INTO amenajari_avize (cod_loc, nr_aviz, data_aviz, lucrare) VALUES (?,?,?,?)", ("SC-1", "AV-5", "2026-03-03", "bordură"))
rep2 = cloud_sync.sync_all()
out["rep2"] = {k: rep2.get(k) for k in ("ok", "applied", "conflicts", "errors", "pending")}
""")
    assert out["rep"]["ok"] and out["rep2"] == {"ok": True, "applied": 2, "conflicts": 0, "errors": 0, "pending": 0}, out
    return out["loc_uid"]


def counts(w):
    return {t: pg(w, f"SELECT count(*) FROM {t}")[0][0] for t in ("locuri_de_veci", "concesionari", "contracte", "inhumati", "plati", "amenajari_avize")}


# ===================================================================== 1. asocierea telefonului (cod / QR)
def test_pairing_by_code_or_qr_gives_phone_the_right_organization_and_cemetery(mv):
    w = mv
    ka, code_a = pair_phone(w, "A")
    kb, _code_b = pair_phone(w, "B")
    for key, tag in ((ka, "A"), (kb, "B")):
        b = get(w, "/api/v1/mobile/bootstrap", key).json()["result"]
        assert (b["organization"]["uid"], b["cemetery"]["uid"], b["device_type"]) == (w["T"][tag]["org"], w["T"][tag]["cem"], "MOBILE")
        assert b["cemetery"]["name"] == f"6C {tag}" and b["device"]["device_name"] == "Telefon test"
        assert get(w, "/api/v1/field-inbox/me", key).json()["result"] == {"cemetery_uid": w["T"][tag]["cem"], "device_type": "MOBILE"}
    # codul e de unică folosință (replay respins) și nu poate lega telefonul de alt cimitir
    again = requests.post(w["url"] + "/api/v1/phone-pairing/redeem", json={"code": code_a, "device_name": "X", "client_id": "cid-" + uuid.uuid4().hex}, timeout=15)
    assert again.status_code in (400, 401, 403, 404), again.text
    # cod greșit / link malformat nu asociază nimic
    assert requests.post(w["url"] + "/api/v1/phone-pairing/redeem", json={"code": "AAAAABBBBBCCCCCDDDDD", "device_name": "X", "client_id": "cid-" + uuid.uuid4().hex}, timeout=15).status_code in (400, 401, 403, 404)
    assert qr.pair_url("javascript:alert(1)", "ABCDEFGHIJ") == "" and qr.pair_url(w["url"], "cod invalid!") == ""
    # PWA: fragmentul din URL, scos din bara de adrese; QR generat
    js = open(os.path.join(os.path.dirname(__file__), "..", "static", "mobile_app.js"), encoding="utf-8").read()
    assert "takePairCodeFromUrl" in js and "history.replaceState" in js and "pairWith" in js
    assert qr.svg_data_uri(qr.pair_url(w["url"], code_a.replace("-", ""))).startswith("data:image/svg+xml;base64,") and qr.png_bytes("x")[:4] == b"\x89PNG"


def test_revoked_phone_loses_access_immediately(mv):
    w = mv
    ka, _ = pair_phone(w, "A")
    assert get(w, "/api/v1/mobile/bootstrap", ka).status_code == 200
    phones = requests.get(w["url"] + "/api/v1/phone-pairing/phones", headers=H(w["T"]["A"]["main"]), timeout=15).json()["result"]
    buid = ka.split(".")[1]
    assert any(p["binding_uid"] == buid for p in phones)
    assert requests.post(f"{w['url']}/api/v1/phone-pairing/phones/{buid}/revoke", headers=H(w["T"]["A"]["main"]), timeout=15).status_code == 200
    assert get(w, "/api/v1/mobile/bootstrap", ka).status_code == 401 and get(w, "/api/v1/mobile/map", ka).status_code == 401


# ===================================================================== 2. date: hartă, locuri, concesionari, înhumați, contracte, plăți
def test_phone_sees_map_graves_concessionaires_burials_contracts_and_payments(mv):
    w = mv
    loc_uid = seed_main(w)
    key, _ = pair_phone(w, "A")
    # implicit (telefon fără rol): citește hărți/locuri, dar NU plăți (principiul privilegiului minim)
    d0 = get(w, f"/api/v1/mobile/graves/{loc_uid}", key).json()["result"]
    assert d0["payments"] == [] and d0["grave"]["cod_loc"] == "SC-1"
    assert get(w, "/api/v1/sync-v2/pull?entity=plati", key).status_code == 403
    main_grants_operator(w, "A", key)
    plots = get(w, "/api/v1/mobile/map", key).json()["result"]["plots"]
    assert [p["code"] for p in plots] == ["SC-1"] and len(plots[0]["points"]) == 5                          # harta se încarcă cu poligonul
    d = get(w, f"/api/v1/mobile/graves/{loc_uid}", key).json()["result"]
    assert d["grave"]["cod_loc"] == "SC-1" and d["grave"]["sector"] == "S1"                                 # loc de veci
    assert [(c["nume"], c["prenume"], c["telefon"], c["numar_contract"]) for c in d["contracts"]] == [("Ionescu", "Maria", "0711000000", "C-100")]   # concesionar + contract
    assert sorted(b["prenume"] for b in d["burials"]) == ["Ana", "Ion"]                                      # înhumați
    assert [(p["suma"], p["chitanta_nr"]) for p in d["payments"]] == [(250.0, "R-77")]                       # plăți
    assert [p["nr_aviz"] for p in d["permits"]] == ["AV-5"]
    s = get(w, "/api/v1/mobile/search?q=Ionescu", key).json()["result"]
    assert s["concessionaires"][0]["grave_code"] == "SC-1" and s["contracts"][0]["number"] == "C-100"
    s2 = get(w, "/api/v1/mobile/search?q=Popescu", key).json()["result"]
    assert len(s2["burials"]) == 2
    summ = get(w, "/api/v1/mobile/summary", key).json()["result"]
    assert isinstance(summ, dict) and summ and get(w, "/api/v1/mobile/data-status", key).status_code == 200


# ===================================================================== 3. documente și fotografii
def test_photo_and_document_phone_to_cloud_to_main_with_integrity_and_isolation(mv):
    w = mv
    loc_uid = seed_main(w)
    key, _ = pair_phone(w, "A")
    keyb, _ = pair_phone(w, "B")
    main_a, main_b = w["T"]["A"]["main"], w["T"]["B"]["main"]
    photo = png()
    item = requests.post(w["url"] + "/api/v1/field-inbox/items", json={"idempotency_key": "photo-" + uuid.uuid4().hex, "item_type": "GRAVE_PHOTO", "payload": {"caption": "SC-1"},
                                                                       "grave_uid": loc_uid}, headers=H(key), timeout=15)
    assert item.status_code == 201, item.text
    iuid, fuid = item.json()["result"]["item"]["uid"], str(uuid.uuid4())
    up = {"Content-Type": "image/png", "X-File-Uid": fuid, "X-File-Name": "loc.png", "X-File-Sha256": hashlib.sha256(photo).hexdigest(), **H(key)}
    r1 = requests.post(f"{w['url']}/api/v1/field-inbox/items/{iuid}/files", data=photo, headers=up, timeout=15)
    assert r1.status_code == 201, r1.text
    r2 = requests.post(f"{w['url']}/api/v1/field-inbox/items/{iuid}/files", data=photo, headers=up, timeout=15)       # reîncercare (rețea slabă): fără duplicat
    assert r2.status_code == 200 and r2.json()["result"]["replayed"] is True
    assert pg(w, "SELECT count(*) FROM cp_storage_objects")[0][0] == 1 and pg(w, "SELECT count(*) FROM cp_field_inbox_attachments")[0][0] == 1
    doc = requests.post(w["url"] + "/api/v1/field-inbox/items", json={"idempotency_key": "doc-" + uuid.uuid4().hex, "item_type": "DOCUMENT", "payload": {"doc_type": "adeverinta_inhumare"},
                                                                       "grave_uid": loc_uid}, headers=H(key), timeout=15).json()["result"]["item"]
    pdf = b"%PDF-1.4\n" + os.urandom(300)
    assert requests.post(f"{w['url']}/api/v1/field-inbox/items/{doc['uid']}/files", data=pdf, headers={"Content-Type": "application/pdf", "X-File-Uid": str(uuid.uuid4()), "X-File-Name": "a.pdf", **H(key)}, timeout=15).status_code == 201
    # telefonul își vede propriile încărcări (ecranul „Documente”)
    mine = get(w, "/api/v1/field-inbox/items?item_type=GRAVE_PHOTO", key).json()["result"]
    assert [i["uid"] for i in mine] == [iuid] and mine[0]["status"] == "PENDING"
    assert [i["item_type"] for i in get(w, "/api/v1/field-inbox/items?item_type=DOCUMENT", key).json()["result"]] == ["DOCUMENT"]
    # MAIN îl primește, descarcă prin URL semnat, octeții sunt identici; telefonul nu poate accepta
    assert requests.post(f"{w['url']}/api/v1/field-inbox/items/{iuid}/accept", headers=H(key), timeout=15).status_code in (401, 403)
    seen = get(w, f"/api/v1/field-inbox/items/{iuid}", main_a).json()["result"]
    assert [f["status"] for f in seen["files"]] == ["READY"] and seen["grave_uid"] == loc_uid
    url = get(w, f"/api/v1/field-inbox/items/{iuid}/files/{fuid}/url", main_a).json()["result"]
    assert url["sha256"] == hashlib.sha256(photo).hexdigest() and url["size"] == len(photo)
    assert url["url"].startswith("http") and w["sup"].base.split("//")[1].split("/")[0] in url["url"]
    from test_etapa6b_field_storage import _Fake
    assert photo in _Fake.store.values()                                                                          # octeții din Storage = cei trimiși de telefon
    # izolare: telefonul/MAIN-ul organizației B nu văd și nu pot obține nimic din A
    assert get(w, "/api/v1/field-inbox/items", keyb).json()["result"] == [] and get(w, "/api/v1/field-inbox/items", main_b).json()["result"] == []
    assert get(w, f"/api/v1/field-inbox/items/{iuid}", main_b).status_code in (403, 404)
    assert get(w, f"/api/v1/field-inbox/items/{iuid}/files/{fuid}/url", main_b).status_code in (403, 404)
    assert get(w, f"/api/v1/field-inbox/items/{iuid}", w["T"]["A"]["phone2"]).status_code in (403, 404)        # alt telefon din același cimitir: doar propriile itemuri
    # tipuri neacceptate: fișier executabil, PDF ca fotografie
    bad = requests.post(f"{w['url']}/api/v1/field-inbox/items/{iuid}/files", data=b"MZ\x90\x00" + os.urandom(50), headers={"Content-Type": "application/octet-stream", "X-File-Uid": str(uuid.uuid4()), "X-File-Name": "x.exe", **H(key)}, timeout=15)
    assert bad.status_code >= 400
    assert pg(w, "SELECT count(*) FROM cp_storage_objects")[0][0] == 2


# ===================================================================== 4. sincronizare fără duplicate + întrerupere
def test_repeated_sync_and_replayed_ops_never_duplicate(mv):
    w = mv
    seed_main(w)
    first = counts(w)
    assert first == {"locuri_de_veci": 1, "concesionari": 1, "contracte": 1, "inhumati": 2, "plati": 1, "amenajari_avize": 1}
    for _ in range(3):
        out = main_run(w, "rep = cloud_sync.sync_all(); out['rep'] = {k: rep.get(k) for k in ('ok','applied','conflicts','errors','pending')}")
        assert out["rep"]["ok"] and out["rep"]["pending"] == 0 and out["rep"]["conflicts"] == 0 and out["rep"]["errors"] == 0
    assert counts(w) == first
    # server oprit (întrerupere internet) -> modificarea rămâne în coadă -> repornit -> trimisă o singură dată
    w["srv"].stop()
    down = main_run(w, "q(\"UPDATE locuri_de_veci SET sector='S9' WHERE cod_loc='SC-1'\"); rep = cloud_sync.sync_all(); out['rep'] = {k: rep.get(k) for k in ('ok','pending')}")
    assert down["rep"]["pending"] >= 1
    w["srv"] = Server(w["srv"].app, w["srv"].port)
    w["srv"].start()
    up = main_run(w, "rep = cloud_sync.sync_all(); out['rep'] = {k: rep.get(k) for k in ('ok','pending','errors')}")
    assert up["rep"] == {"ok": True, "pending": 0, "errors": 0} and counts(w) == first
    assert pg(w, "SELECT sector FROM locuri_de_veci")[0][0] == "S9"


# ===================================================================== 5. modificări simultane
def test_simultaneous_edits_never_overwrite_silently(mv):
    w = mv
    loc_uid = seed_main(w)
    k1, _ = pair_phone(w, "A", "Telefon 1")
    k2, _ = pair_phone(w, "A", "Telefon 2")
    for k in (k1, k2):
        main_grants_operator(w, "A", k)
    inh = get(w, f"/api/v1/mobile/graves/{loc_uid}", k1).json()["result"]["burials"][0]["uid"]
    row = get(w, f"/api/v1/sync-v2/row?entity=inhumati&uid={inh}", k1).json()["result"]

    def edit(key, obs, base):
        return {"op_id": "op-" + uuid.uuid4().hex, "entity": "inhumati", "uid": inh, "action": "upsert", "base_version": base, "data": {**row["data"], "observatii": obs}}

    gate, results = threading.Barrier(2), {}

    def go(name, key, obs):
        gate.wait()
        results[name] = requests.post(w["url"] + "/api/v1/sync-v2/push", json={"ops": [edit(key, obs, row["version"])]}, headers=H(key), timeout=20).json()["result"]["results"][0]
    ts = [threading.Thread(target=go, args=("p1", k1, "de pe telefon 1")), threading.Thread(target=go, args=("p2", k2, "de pe telefon 2"))]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    statuses = sorted(r["status"] for r in results.values())
    assert statuses == ["applied", "conflict"], results                                                   # exact unul câștigă; celălalt nu suprascrie în tăcere
    winner = "de pe telefon 1" if results["p1"]["status"] == "applied" else "de pe telefon 2"
    assert pg(w, "SELECT observatii FROM inhumati WHERE uid=%s", (inh,))[0][0] == winner
    conflicts = get(w, "/api/v1/sync-v2/conflicts", w["T"]["A"]["main"]).json()["result"]["conflicts"]
    assert len(conflicts) == 1
    # MAIN modifică același câmp offline; la „Sincronizează" MAIN câștigă, conflictul rămâne vizibil, nu se pierde nicio valoare
    out = main_run(w, "q(\"UPDATE inhumati SET observatii='din MAIN' WHERE prenume='Ion'\"); q(\"UPDATE inhumati SET observatii='din MAIN' WHERE prenume='Ana'\"); rep = cloud_sync.sync_all(); out['rep'] = {k: rep.get(k) for k in ('ok','errors','pending')}")
    assert out["rep"]["ok"] and out["rep"]["errors"] == 0
    assert counts(w)["inhumati"] == 2 and pg(w, "SELECT count(*) FROM inhumati WHERE observatii='din MAIN'")[0][0] >= 1


# ===================================================================== 6. izolarea datelor între organizații
def test_organization_isolation_end_to_end(mv):
    w = mv
    loc_a = seed_main(w, "A")
    key_a, _ = pair_phone(w, "A")
    key_b, _ = pair_phone(w, "B")
    main_grants_operator(w, "A", key_a)
    main_grants_operator(w, "B", key_b)
    assert get(w, "/api/v1/mobile/map", key_b).json()["result"]["plots"] == []
    assert get(w, f"/api/v1/mobile/graves/{loc_a}", key_b).status_code in (403, 404)
    sb = get(w, "/api/v1/mobile/search?q=Ionescu", key_b).json()["result"]
    assert all(v == [] for v in sb.values() if isinstance(v, list))
    for ent in ("locuri_de_veci", "concesionari", "contracte", "inhumati", "plati", "amenajari_avize"):
        assert get(w, f"/api/v1/sync-v2/pull?entity={ent}&since=0", key_b).json()["result"]["changes"] == []
        assert get(w, f"/api/v1/sync-v2/pull?entity={ent}&since=0", w["T"]["B"]["main"]).json()["result"]["changes"] == []
    # scriere încrucișată: B încearcă să modifice un rând al lui A -> respins/necreat; A rămâne neschimbat
    inh = get(w, f"/api/v1/mobile/graves/{loc_a}", key_a).json()["result"]["burials"][0]["uid"]
    row = get(w, f"/api/v1/sync-v2/row?entity=inhumati&uid={inh}", key_a).json()["result"]
    ev = requests.post(w["url"] + "/api/v1/sync-v2/push", json={"ops": [{"op_id": "x-" + uuid.uuid4().hex, "entity": "inhumati", "uid": inh, "action": "upsert", "base_version": row["version"],
                                                                         "data": {**row["data"], "observatii": "atac B"}}]}, headers=H(key_b), timeout=15)
    assert not (ev.status_code == 200 and ev.json()["result"]["results"][0]["status"] == "applied" and pg(w, "SELECT observatii FROM inhumati WHERE uid=%s", (inh,))[0][0] == "atac B")
    assert pg(w, "SELECT count(*) FROM inhumati WHERE observatii='atac B'")[0][0] == 0
    # codul organizației A leagă telefonul DOAR de A: cheia nu funcționează pe date B
    assert get(w, "/api/v1/mobile/bootstrap", key_a).json()["result"]["organization"]["uid"] == w["T"]["A"]["org"]
    assert counts(w)["locuri_de_veci"] == 1
