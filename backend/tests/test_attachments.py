"""Arhiva generală de atașamente (JPEG/PNG/PDF) pe orice entitate + permisiuni. Rulează pe SQLite."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from test_etapa1_audit import _load_server

FORM = {"source_id": "cimitir"}
PW = "Parola-op-12345"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PDF = b"%PDF-1.4\n" + b"0" * 64


@pytest.fixture()
def web(tmp_path):
    server = _load_server(tmp_path / "data")
    import cimitir_auth
    import database as db
    db.init_db()
    cimitir_auth.init_auth()
    cimitir_auth.create_user("op", PW, "operator")
    cimitir_auth.create_user("viewer", PW, "user")
    db.save_plot_to_db("T-01", [(0, 0), (40, 0), (40, 30), (0, 30)], "#22c55e")
    db.save_loc_metadata("T-01", {"sector": "A", "parcela": "1", "rand": "1", "numar": "1", "disponibilitate": "Liber"})
    cid = db.save_concesionar({"nume": "Popa", "prenume": "Maria", "cnp": "1800101123456"})
    ctr = db.save_contract({"numar_contract": "C-1", "cod_loc": "T-01", "concesionar_id": cid, "data_sfarsit": "2099-01-01"})
    iid = db.save_inhumat("T-01", {"nume": "Ionescu", "prenume": "Vasile", "data_deces": "02.02.2020"})
    conn = db.get_connection()
    pay = db.add_plata("T-01", 50.0, "2026-01-01", "taxa", "CH-9", "")
    return server, TestClient(server.app), {"loc": "T-01", "concesionar": cid, "contract": ctr, "inhumat": iid, "plata": pay}, db


def _client(server, user):
    c = TestClient(server.app)
    r = c.post("/admin/access/login", data={"username": user, "password": PW, **FORM}, follow_redirects=False)
    assert r.status_code == 303 and "schimba-parola" not in r.headers["location"], r.headers
    return c


def _up(c, etype, eid, name, data, **form):
    return c.post(f"/admin/att/{etype}/{eid}", files=[("files", (name, data, "application/octet-stream"))],
                  data={"doc_type": "alt_document", **form})


def test_attachments_on_every_entity_type_list_download_delete(web):
    server, _, ids, db = web
    c = _client(server, "op")
    for etype in ("loc", "concesionar", "contract", "inhumat", "plata"):
        eid = ids[etype]
        r = _up(c, etype, eid, "chitanta_123.png", PNG, doc_type="chitanta", title="Chitanta 123", description="dovada")
        assert r.status_code == 200, (etype, r.text[:300])
        assert "chitanta_123.png" in r.text and "Chitanță" in r.text
        lst = c.get(f"/admin/att/{etype}/{eid}")
        assert lst.status_code == 200 and 'data-testid="att-item"' in lst.text
        rows = db.get_connection().execute("SELECT id, doc_type, original_filename, mime_type, uploaded_by, size, sha256, source FROM documents "
                                           "WHERE entity_type=? AND entity_id=?", (etype, str(eid))).fetchall()
        assert len(rows) == 1
        d = dict(rows[0])
        assert d["doc_type"] == "chitanta" and d["original_filename"] == "chitanta_123.png" and d["mime_type"] == "image/png"
        assert d["uploaded_by"] == "op" and d["size"] == len(PNG) and len(d["sha256"]) == 64
        f = c.get(f"/admin/attfile/{d['id']}")
        assert f.status_code == 200 and f.content == PNG and f.headers["content-type"] == "image/png"
        assert f.headers["x-content-type-options"] == "nosniff" and "no-store" in f.headers["cache-control"]
        assert "attachment" in c.get(f"/admin/attfile/{d['id']}?download=1").headers["content-disposition"]
        dele = c.post(f"/admin/attdel/{d['id']}")
        assert dele.status_code == 200 and 'data-testid="att-item"' not in dele.text
        assert c.get(f"/admin/attfile/{d['id']}").status_code == 404


def test_formats_validation_pdf_jpeg_png_only(web):
    server, _, ids, db = web
    c = _client(server, "op")
    assert _up(c, "contract", ids["contract"], "contract_semnat.pdf", PDF, doc_type="contract_concesiune").status_code == 200
    assert _up(c, "contract", ids["contract"], "poza.JPG", JPG).status_code == 200
    for name, data in (("virus.exe", b"MZ" + b"0" * 50), ("fals.pdf", PNG), ("gol.png", b""), ("script.html", b"<script>"),
                       ("../../etc/passwd.png", b"nu e png")):
        r = _up(c, "contract", ids["contract"], name, data)
        assert r.status_code == 422 and 'data-testid="att-error"' in r.text, name
    names = [r["original_filename"] for r in db.get_connection().execute(
        "SELECT original_filename FROM documents WHERE entity_type='contract'").fetchall()]
    assert sorted(names) == ["contract_semnat.pdf", "poza.JPG"]
    # nume cu traversare de cale nu iese din folder (se păstrează doar numele curățat)
    r = _up(c, "contract", ids["contract"], "../../x.png", PNG)
    assert r.status_code == 200 and "x.png" in r.text and ".." not in r.text.split("att-name")[1][:80]


def test_id_card_requires_justification_and_allowed_entities(web):
    server, _, ids, _ = web
    c = _client(server, "op")
    r = _up(c, "concesionar", ids["concesionar"], "ci.jpg", JPG, doc_type="carte_identitate")
    assert r.status_code == 422 and "motivul" in r.text
    assert _up(c, "concesionar", ids["concesionar"], "ci.jpg", JPG, doc_type="carte_identitate", description="verificare identitate").status_code == 200
    r = _up(c, "plata", ids["plata"], "ci.jpg", JPG, doc_type="carte_identitate", description="motiv valid aici")
    assert r.status_code == 422 and "doar la concesionar" in r.text


def test_permissions_unknown_entities_and_audit(web):
    server, anon, ids, db = web
    assert anon.get(f"/admin/att/loc/T-01", follow_redirects=False).status_code in (401, 403)
    assert anon.post("/admin/att/loc/T-01", files=[("files", ("a.png", PNG))]).status_code in (401, 403)
    v = _client(server, "viewer")
    page = v.get("/admin/att/loc/T-01")
    assert page.status_code == 200 and 'data-testid="att-form"' not in page.text      # fără drept de scriere: fără formular
    assert _up(v, "loc", "T-01", "a.png", PNG).status_code == 403
    c = _client(server, "op")
    assert c.get("/admin/att/loc/NU-EXISTA").status_code == 404
    assert c.get("/admin/att/contract/99999").status_code == 404
    assert c.get("/admin/att/necunoscut/1").status_code == 404
    assert _up(c, "loc", "T-01", "a.png", PNG).status_code == 200
    did = db.get_connection().execute("SELECT id FROM documents WHERE entity_type='loc'").fetchone()["id"]
    assert v.get(f"/admin/attfile/{did}").status_code == 200                           # citire permisă cu view
    assert v.post(f"/admin/attdel/{did}").status_code == 403
    c.post(f"/admin/attdel/{did}")
    rows = [dict(r) for r in db.get_connection().execute("SELECT actor, action, target, detail FROM audit_log WHERE action LIKE 'attachment.%'").fetchall()] \
        if _has_audit(db) else []
    if rows:
        assert [r["action"] for r in rows] == ["attachment.add", "attachment.delete"] and all(r["actor"] == "op" for r in rows)
        blob = json.dumps(rows)
        assert "storage" not in blob.lower() and "token" not in blob.lower()


def _has_audit(db) -> bool:
    try:
        db.get_connection().execute("SELECT 1 FROM audit_log LIMIT 1")
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ căutare unificată + pagini mobile
def test_unified_search_finds_persons_owners_codes_contracts_payments(web):
    server, anon, ids, db = web
    assert anon.get("/admin/cauta", follow_redirects=False).status_code in (302, 303, 307)
    v = _client(server, "viewer")                                          # căutarea e permisă cu simplu `view`
    page = v.get("/admin/cauta")
    assert page.status_code == 200 and 'data-testid="search-input"' in page.text
    r = v.get("/admin/cauta?q=ionescu", headers={"HX-Request": "true"})
    assert 'data-testid="res-inhumat-' in r.text and "<html" not in r.text      # fragment HTMX
    assert "att-open-inhumat-" in r.text
    assert 'data-testid="res-conces-' in v.get("/admin/cauta?q=popa").text
    assert "Popă" not in v.get("/admin/cauta?q=popa").text
    assert 'data-testid="res-conces-' in v.get("/admin/cauta?q=1800101123456").text          # după CNP
    assert 'data-testid="res-loc-T-01"' in v.get("/admin/cauta?q=t-01").text                  # după cod loc
    assert 'data-testid="res-contract-' in v.get("/admin/cauta?q=C-1").text
    assert 'data-testid="res-plata-' in v.get("/admin/cauta?q=CH-9").text
    assert 'data-testid="search-short"' in v.get("/admin/cauta?q=a").text
    assert 'data-testid="search-empty"' in v.get("/admin/cauta?q=zzzzqq").text
    assert "<script>alert" not in v.get("/admin/cauta?q=%3Cscript%3Ealert(1)%3C/script%3E").text   # fără XSS reflectat


def test_mobile_pages_render_with_attachment_entry_points(web):
    server, _, ids, _ = web
    c = _client(server, "op")
    home = c.get("/admin/")
    assert 'data-testid="bottom-nav"' in home.text and 'id="att-sheet"' in home.text and "viewport-fit=cover" in home.text
    for path, tid in (("/admin/contracte", "att-open-contract-"), ("/admin/plati", "att-open-plata-"),
                      ("/admin/inhumati", "att-open-inhumat-"), ("/admin/concesionari", "att-open-concesionar-")):
        r = c.get(path)
        assert r.status_code == 200 and tid in r.text, path
    assert c.get("/admin/locuri").status_code == 200 and c.get("/admin/cereri").status_code == 200
    drawer = c.get("/admin/ui/loc/T-01")
    assert drawer.status_code == 200 and 'hx-get="/admin/att/loc/T-01"' in drawer.text
    for k in ("att-open-inhumat-", "att-open-contract-", "att-open-plata-"):
        assert k in drawer.text, k
    assert c.get("/admin/harta").status_code == 200


# ------------------------------------------------------------------ Faza 3: contract din web, PDF-uri, istoric
def _form_contract(**kw):
    d = {"numar_contract": "WEB-1", "data_contract": "05.10.2026", "data_sfarsit": "05.10.2056", "concesionar_id": "", "nume": "Nou", "prenume": "Titular",
         "cnp": "1900101123456", "telefon": "0700111222", "adresa": "Str. X 1", "observatii": ""}
    d.update(kw)
    return d


def test_contract_creation_from_web_needs_approve_and_validates(web):
    server, _, ids, db = web
    v = _client(server, "viewer")
    assert v.post("/admin/ui/loc/T-01/contract", data=_form_contract()).status_code == 403
    assert "contract-add-toggle" not in v.get("/admin/ui/loc/T-01").text and "contract-no-right" in v.get("/admin/ui/loc/T-01").text
    # al doilea loc, liber
    db.save_plot_to_db("T-02", [(50, 0), (90, 0), (90, 30), (50, 30)], "#22c55e")
    db.save_loc_metadata("T-02", {"sector": "A", "parcela": "1", "rand": "1", "numar": "2", "disponibilitate": "Liber"})
    c = _client(server, "op")                                    # operatorul are `approve`
    assert "contract-add-toggle" in c.get("/admin/ui/loc/T-02").text
    for bad, msg in (({"numar_contract": ""}, "obligatoriu"), ({"nume": ""}, "concesionar"), ({"data_sfarsit": "31.31.2050"}, "nu este validă"), ({"data_contract": "azi"}, "Data contractului"), ({"data_inceput": "99.99.2020"}, "Data de început"),
                     ({"data_sfarsit": "01.01.2000"}, "înaintea"), ({"concesionar_id": "99999"}, "nu există"), ({"numar_contract": "C-1"}, "Există deja")):
        r = c.post("/admin/ui/loc/T-02/contract", data=_form_contract(**bad))
        assert r.status_code == 422 and bad_msg(r, msg), (bad, r.text[:200])
    # loc cu contract activ
    r = c.post("/admin/ui/loc/T-01/contract", data=_form_contract(numar_contract="WEB-X"))
    assert r.status_code == 422 and "contract activ" in r.text
    assert c.post("/admin/ui/loc/NU-EXISTA/contract", data=_form_contract()).status_code == 404
    ok = c.post("/admin/ui/loc/T-02/contract", data=_form_contract())
    assert ok.status_code == 200 and "WEB-1" in ok.text and "pdf-contract-" in ok.text
    saved = [x for x in db.load_contracte() if x["numar_contract"] == "WEB-1"][0]
    assert saved["cod_loc"] == "T-02" and saved["owner_fullname"] == "Nou Titular" and saved["owner_cnp"] == "1900101123456" and saved["data_sfarsit"] == "05.10.2056"
    assert saved["concesionar_id"]
    # cu concesionar existent
    db.save_plot_to_db("T-03", [(100, 0), (140, 0), (140, 30), (100, 30)], "#22c55e")
    db.save_loc_metadata("T-03", {"sector": "A", "parcela": "1", "rand": "1", "numar": "3", "disponibilitate": "Liber"})
    ok2 = c.post("/admin/ui/loc/T-03/contract", data=_form_contract(numar_contract="WEB-2", nume="", concesionar_id=str(ids["concesionar"])))
    assert ok2.status_code == 200
    assert [x for x in db.load_contracte() if x["numar_contract"] == "WEB-2"][0]["owner_fullname"] == "Popa Maria"


def bad_msg(resp, msg: str) -> bool:
    return msg in resp.text


def test_pdf_documents_generate_and_are_protected(web):
    server, anon, ids, db = web
    c = _client(server, "viewer")                                          # simplul `view` poate genera documente
    for path in (f"/admin/pdf/contract/{ids['contract']}", f"/admin/pdf/chitanta/{ids['plata']}", f"/admin/pdf/adeverinta/{ids['inhumat']}",
                 "/admin/pdf/expirari?days=90", "/admin/pdf/raport-financiar", "/admin/pdf/registru-inhumari?start=2020-01-01&end=2030-12-31",
                 "/admin/loc/T-01/fisa.pdf"):
        r = c.get(path)
        assert r.status_code == 200, (path, r.status_code, r.text[:200])
        assert r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF-") and len(r.content) > 800, path
        assert "inline" in r.headers["content-disposition"] and "no-store" in r.headers["cache-control"]
    assert "attachment" in c.get(f"/admin/pdf/contract/{ids['contract']}?download=1").headers["content-disposition"]
    assert c.get("/admin/pdf/contract/99999").status_code == 404
    assert c.get("/admin/pdf/chitanta/99999").status_code == 404
    assert c.get("/admin/pdf/adeverinta/99999").status_code == 404
    assert c.get("/admin/pdf/raport-financiar?start=abc").status_code == 422
    assert anon.get(f"/admin/pdf/contract/{ids['contract']}", follow_redirects=False).status_code in (401, 403)
    assert 'data-testid="pdf-reports-card"' in c.get("/admin/rapoarte").text
    drawer = _client(server, "op").get("/admin/ui/loc/T-01").text
    for k in ("pdf-contract-", "pdf-chitanta-", "pdf-adeverinta-"):
        assert k in drawer, k


def test_entity_history_shows_changes_and_requires_approve(web):
    server, _, ids, db = web
    op = _client(server, "op")
    assert op.post(f"/admin/att/contract/{ids['contract']}", files=[("files", ("c.pdf", PDF, "application/pdf"))], data={"doc_type": "contract_concesiune"}).status_code == 200
    h = op.get(f"/admin/istoric/contract/{ids['contract']}")
    assert h.status_code == 200 and 'data-testid="history-panel"' in h.text
    assert "attachment.add" in h.text and "op" in h.text and 'data-testid="history-item"' in h.text
    assert "history-open" in op.get(f"/admin/att/contract/{ids['contract']}").text
    v = _client(server, "viewer")
    assert v.get(f"/admin/istoric/contract/{ids['contract']}").status_code == 403
    assert "history-open" not in v.get(f"/admin/att/contract/{ids['contract']}").text
    assert op.get("/admin/istoric/contract/99999").status_code == 404
    assert op.get("/admin/istoric/necunoscut/1").status_code == 404
    assert "Nicio modificare" in op.get(f"/admin/istoric/plata/{ids['plata']}").text


def test_contract_form_keeps_values_after_validation_error(web):
    server, _, ids, db = web
    c = _client(server, "op")
    r = c.post("/admin/ui/loc/T-01/contract", data=_form_contract(numar_contract="KEEP-9", nume="Pastrat", telefon="0799888777"))
    assert r.status_code == 422
    assert 'value="KEEP-9"' in r.text and 'value="Pastrat"' in r.text and 'value="0799888777"' in r.text
    assert "const_tab" not in r.text and "t-contracte" in r.text            # revine pe tab-ul Contracte


# ------------------------------------------------------------------ Scanare din telefon (AI propune, omul confirmă)
FAKE_CI = {"document_type": "buletin", "engine": "gemini", "fields": {
    "nume": {"value": "Popescu", "confidence": 0.98}, "prenume": {"value": "Ioana", "confidence": 0.95},
    "cnp": {"value": "2900202123456", "confidence": 0.5},                       # încredere mică -> nepreselectat
    "serie": {"value": "XB", "confidence": 0.9}, "numar": {"value": "123456", "confidence": 0.9},
    "adresa": {"value": "Str. Nouă 5, Cluj", "confidence": 0.9}}}
FAKE_CERT = {"document_type": "certificat_deces", "engine": "gemini", "fields": {
    "nume": {"value": "Ionescu", "confidence": 0.99}, "prenume": {"value": "Vasile", "confidence": 0.99},
    "data_deces": {"value": "03.03.2021", "confidence": 0.97}, "sex": {"value": "M", "confidence": 0.9},
    "nr_certificat_deces": {"value": "DC 998877", "confidence": 0.9}}}


@pytest.fixture()
def ai(monkeypatch):
    import ai_extract
    state = {"result": FAKE_CI, "calls": 0, "fail": False, "ready": True}

    def fake(path, kind):
        state["calls"] += 1
        if state["fail"]:
            raise RuntimeError("serviciu indisponibil")
        return state["result"]
    monkeypatch.setattr(ai_extract, "extract_document", fake)
    monkeypatch.setattr(ai_extract, "engine_status", lambda: {"available": state["ready"], "engine": "gemini", "model": "x"})
    return state


def _scan(c, etype, eid, kind, name="ci.jpg", data=JPG):
    # ca în browser: două câmpuri `file` (cameră + fișier), primul rămâne gol
    return c.post(f"/admin/att/{etype}/{eid}/scan", files=[("file", ("", b"", "application/octet-stream")), ("file", (name, data, "image/jpeg"))], data={"kind": kind})


def _docs(db, etype, eid):
    return [dict(r) for r in db.get_connection().execute("SELECT * FROM documents WHERE entity_type=? AND entity_id=?", (etype, str(eid))).fetchall()]


def test_scan_saves_original_first_and_applies_only_confirmed_fields(web, ai):
    server, _, ids, db = web
    c = _client(server, "op")
    cid = ids["concesionar"]
    panel = c.get(f"/admin/att/concesionar/{cid}")
    assert 'data-testid="scan-start-form"' in panel.text and "Carte de identitate" in panel.text and "Certificat de deces" not in panel.text
    r = _scan(c, "concesionar", cid, "buletin")
    assert r.status_code == 200 and 'data-testid="scan-review"' in r.text and 'data-testid="scan-original-saved"' in r.text
    docs = _docs(db, "concesionar", cid)
    assert len(docs) == 1 and docs[0]["doc_type"] == "carte_identitate" and docs[0]["source"] == "scan" and "confirmat" in docs[0]["description"]
    assert docs[0]["sha256"] and docs[0]["size"] == len(JPG)
    # propunerea NU a modificat încă nimic
    cur = db.get_concesionar(cid)
    assert cur["nume"] == "Popa" and cur["prenume"] == "Maria" and not cur.get("bi_ci")
    # câmpul cu încredere mică nu e preselectat, cele sigure da
    assert 'data-testid="scan-apply-nume" value="1" checked' in r.text.replace("\n", " ") or "scan-apply-nume" in r.text
    import re as _re
    assert not _re.search(r'name="apply_cnp"[^>]*checked', r.text) and _re.search(r'name="apply_nume"[^>]*checked', r.text)
    assert "XB 123456" in r.text
    # confirmare: nume (editat de om) + bi_ci; prenume/adresă/cnp rămân nebifate => neschimbate
    ap = c.post(f"/admin/att/concesionar/{cid}/scan/apply", data={
        "kind": "buletin", "attachment_id": str(docs[0]["id"]),
        "apply_nume": "1", "field_nume": "Popescu-Editat", "apply_bi_ci": "1", "field_bi_ci": "XB 123456",
        "field_prenume": "Ioana", "field_cnp": "2900202123456", "field_adresa": "Str. Nouă 5, Cluj"})
    assert ap.status_code == 200 and 'data-testid="att-notice"' in ap.text
    cur = db.get_concesionar(cid)
    assert cur["nume"] == "Popescu-Editat" and cur["bi_ci"] == "XB 123456" and cur["prenume"] == "Maria" and cur["cnp"] == "1800101123456"
    assert len(_docs(db, "concesionar", cid)) == 1                      # originalul a rămas, nu s-a dublat/înlocuit


def test_scan_validation_cross_entity_and_failures(web, ai):
    server, _, ids, db = web
    c = _client(server, "op")
    cid, iid = ids["concesionar"], ids["inhumat"]
    # tip de scanare nepermis pe entitate => nimic salvat
    r = _scan(c, "concesionar", cid, "certificat_deces")
    assert r.status_code == 422 and not _docs(db, "concesionar", cid) and ai["calls"] == 0
    # fișier invalid => nimic salvat, AI neapelat
    assert _scan(c, "concesionar", cid, "buletin", "virus.exe", b"MZ0000").status_code == 422 and not _docs(db, "concesionar", cid) and ai["calls"] == 0
    # eșec AI: originalul rămâne arhivat, entitatea neatinsă
    ai["fail"] = True
    r = _scan(c, "concesionar", cid, "buletin")
    assert r.status_code == 200 and "Originalul a fost salvat" in r.text and len(_docs(db, "concesionar", cid)) == 1
    assert db.get_concesionar(cid)["nume"] == "Popa"
    ai["fail"] = False
    # certificat deces pe înhumat
    ai["result"] = FAKE_CERT
    r = _scan(c, "inhumat", iid, "certificat_deces")
    assert 'data-testid="scan-review"' in r.text and "DC 998877" in r.text
    att = _docs(db, "inhumat", iid)[0]
    assert att["doc_type"] == "certificat"
    # valori invalide la confirmare => 422, nimic modificat, valorile editate rămân în formular
    bad = c.post(f"/admin/att/inhumat/{iid}/scan/apply", data={"kind": "certificat_deces", "attachment_id": str(att["id"]),
                 "apply_data_deces": "1", "field_data_deces": "31.31.2021", "apply_nume": "1", "field_nume": "X"})
    assert bad.status_code == 422 and "zz.ll.aaaa" in bad.text and 'value="31.31.2021"' in bad.text
    assert db.get_inhumat(iid)["nume"] == "Ionescu"
    bad = c.post(f"/admin/att/inhumat/{iid}/scan/apply", data={"kind": "certificat_deces", "attachment_id": str(att["id"]), "apply_cnp": "1", "field_cnp": "123"})
    assert bad.status_code == 422 and "13 cifre" in bad.text
    # atașament al altei entități => respins
    other = _docs(db, "concesionar", cid)[0]
    assert c.post(f"/admin/att/inhumat/{iid}/scan/apply", data={"kind": "certificat_deces", "attachment_id": str(other["id"]), "apply_nume": "1", "field_nume": "Hack"}).status_code == 422
    assert c.post(f"/admin/att/inhumat/{iid}/scan/apply", data={"kind": "certificat_deces", "attachment_id": "99999", "apply_nume": "1", "field_nume": "Hack"}).status_code == 422
    assert db.get_inhumat(iid)["nume"] == "Ionescu"
    # confirmare validă: câmpuri nebifate nu se ating
    ok = c.post(f"/admin/att/inhumat/{iid}/scan/apply", data={"kind": "certificat_deces", "attachment_id": str(att["id"]),
                "apply_data_deces": "1", "field_data_deces": "03.03.2021", "apply_nr_certificat_deces": "1", "field_nr_certificat_deces": "DC 998877", "field_nume": "NuSeAplica"})
    assert ok.status_code == 200
    cur = db.get_inhumat(iid)
    assert cur["data_deces"] == "03.03.2021" and cur["nr_certificat_deces"] == "DC 998877" and cur["nume"] == "Ionescu"
    # nimic bifat => nicio modificare
    none = c.post(f"/admin/att/inhumat/{iid}/scan/apply", data={"kind": "certificat_deces", "attachment_id": str(att["id"]), "field_nume": "X"})
    assert none.status_code == 200 and "nicio modificare" in none.text.lower()


def test_scan_permissions_and_ai_unavailable(web, ai):
    server, anon, ids, db = web
    cid = ids["concesionar"]
    v = _client(server, "viewer")
    assert 'data-testid="scan-start-form"' not in v.get(f"/admin/att/concesionar/{cid}").text
    assert _scan(v, "concesionar", cid, "buletin").status_code == 403
    assert v.post(f"/admin/att/concesionar/{cid}/scan/apply", data={"kind": "buletin", "attachment_id": "1"}).status_code == 403
    assert anon.post(f"/admin/att/concesionar/{cid}/scan", files=[("file", ("a.jpg", JPG))], data={"kind": "buletin"}).status_code in (401, 403)
    assert _scan(_client(server, "op"), "concesionar", 99999, "buletin").status_code == 404
    ai["ready"] = False
    c = _client(server, "op")
    p = c.get(f"/admin/att/concesionar/{cid}")
    assert 'data-testid="scan-unavailable"' in p.text and 'data-testid="scan-camera-input"' not in p.text
    assert 'data-testid="att-camera-input"' in p.text and "Fă poză" in p.text            # încărcarea simplă rămâne disponibilă
    # entități fără extracție AI (contract/chitanță/aviz) nu au formular de scanare, doar arhivare foto
    assert 'data-testid="scan-start-form"' not in c.get(f"/admin/att/contract/{ids['contract']}").text


def test_scan_ai_failure_never_leaks_provider_details(web, ai, monkeypatch):
    import ai_extract
    server, _, ids, db = web

    def boom(path, kind):
        raise RuntimeError("Budget has been exceeded! Key=db1sh40tr95c73eef3r0 (sk-...moBQ) Current cost: 0.006")
    monkeypatch.setattr(ai_extract, "extract_document", boom)
    c = _client(server, "op")
    r = _scan(c, "concesionar", ids["concesionar"], "buletin")
    assert r.status_code == 200 and "Originalul a fost salvat" in r.text and "limita de utilizare" in r.text
    assert "db1sh40" not in r.text and "sk-" not in r.text and "0.006" not in r.text
    assert len(_docs(db, "concesionar", ids["concesionar"])) == 1                      # originalul rămâne
    h = c.get(f"/admin/istoric/concesionar/{ids['concesionar']}")
    assert h.status_code == 200 and "db1sh40" not in h.text and "sk-" not in h.text
