# ruff: noqa: F811
"""Portal local pe telefon (LAN): aceleași date/media ca MAIN — hartă, dosar loc, foto pe categorii, înhumați, lucrări, registre.

Folosește numai foldere temporare. Nu atinge Supabase.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_etapa1_audit import _load_server
from test_etapa3_registries import add_plot, env, make_world  # noqa: F401

PW = "Parola-op-12345"
PHONE = {"user-agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Mobile Safari/537.36"}
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PDF = b"%PDF-1.4\n" + b"0" * 64


def _login(server, user="op"):
    c = TestClient(server.app, headers=PHONE)
    r = c.post("/admin/access/login", data={"username": user, "password": PW, "source_id": "cimitir"}, follow_redirects=False)
    assert r.status_code == 303, r.text[:200]
    return c, r.headers["location"]


def _up(c, etype, eid, doc_type, data=JPG, name="poza.jpg"):
    return c.post(f"/admin/att/{etype}/{eid}", files=[("files", (name, data, "application/octet-stream"))], data={"doc_type": doc_type})


@pytest.fixture()
def phone(tmp_path):
    server = _load_server(tmp_path / "data")
    import cimitir_auth
    import database as db
    db.init_db()
    cimitir_auth.init_auth()
    cimitir_auth.create_user("op", PW, "operator")
    cimitir_auth.create_user("viewer", PW, "user")
    db.save_plot_to_db("T-10", [(0, 0), (40, 0), (40, 30), (0, 30)], "#22c55e")
    db.save_loc_metadata("T-10", {"sector": "A", "parcela": "1", "rand": "1", "numar": "10", "disponibilitate": "Liber"})
    return server, db, tmp_path


def test_phone_flow_map_loc_photo_inhumat_certificat_lucrare_same_db_and_media(phone):
    server, db, tmp = phone
    c, landing = _login(server)
    assert landing == "/admin/harta", "pe telefon login-ul duce direct la hartă"
    m = c.get("/api/admin/map.json").json()
    assert [p["cod_loc"] for p in m["plots"]] == ["T-10"]
    d = c.get("/admin/ui/loc/T-10").text
    for tid in ("back-to-map-btn", "tab-lucrari", "tab-docs", "photo-loc-T-10-poza_loc"):
        assert tid in d

    # 1) fotografie GENERAL a locului -> aceeași listă pe care o citește MAIN (poza_loc) + fișier în media registrului
    r = _up(c, "loc", "T-10", "poza_loc")
    assert r.status_code == 200 and "Fotografie salvată" in r.text
    docs = db.list_documents("loc", "T-10", "poza_loc")
    assert len(docs) == 1 and Path(docs[0]["file_path"]).is_file()
    assert Path(db.media_root()) in Path(docs[0]["file_path"]).parents

    # 2) înhumat: adăugare, editare (câmpurile neprimite rămân), foto + certificat de înhumare
    assert c.post("/admin/ui/loc/T-10/inhumat", data={"nume": "Popescu", "prenume": "Ion", "data_deces": "01.02.2024",
                                                       "religie": "ortodox"}).status_code == 200
    iid = db.load_inhumati_for_loc("T-10")[0]["id"]
    r = c.post(f"/admin/ui/inhumat/{iid}/edit", data={"prenume": "Ioan", "data_inhumare": "04.02.2024"})
    assert r.status_code == 200
    row = db.get_inhumat(iid)
    assert (row["nume"], row["prenume"], row["data_inhumare"], row["data_deces"], row["religie"]) == \
        ("Popescu", "Ioan", "04.02.2024", "01.02.2024", "ortodox")
    assert _up(c, "inhumat", iid, "certificat_inhumare", PDF, "cert.pdf").status_code == 200
    assert "Fotografie salvată" in _up(c, "inhumat", iid, "fotografie_inhumat").text
    types = sorted(x["doc_type"] for x in db.list_documents("inhumat", iid))
    assert types == ["certificat_inhumare", "fotografie_inhumat"]

    # 3) lucrare: creare, foto ÎNAINTE / DUPĂ, finalizare
    assert c.post("/admin/ui/loc/T-10/aviz", data={"lucrare": "Reparație monument", "nr": "A-5", "data": "10.03.2025",
                                                    "stare": "in_lucru", "observatii": "ciment"}).status_code == 200
    av = db.list_avize_for_loc("T-10")
    assert len(av) == 1 and (av[0]["lucrare"], av[0]["stare"], av[0]["observatii"]) == ("Reparație monument", "in_lucru", "ciment")
    aid = av[0]["id"]
    assert _up(c, "aviz", aid, "lucrare_inainte").status_code == 200
    assert _up(c, "aviz", aid, "lucrare_dupa").status_code == 200
    assert c.post(f"/admin/ui/aviz/{aid}/edit", data={"stare": "finalizata", "observatii": "gata"}).status_code == 200
    a = db.get_aviz(aid)
    assert a["stare"] == "finalizata" and a["observatii"] == "gata" and a["data_finalizare"] and a["nr"] == "A-5"
    drawer = c.get("/admin/ui/loc/T-10").text
    assert "ÎNAINTE (1)" in drawer and "ÎN TIMPUL (0)" in drawer and "DUPĂ (1)" in drawer
    assert sorted(x["doc_type"] for x in db.list_documents("aviz", aid)) == ["lucrare_dupa", "lucrare_inainte"]
    assert c.post(f"/admin/ui/aviz/{aid}/edit", data={"stare": "in_lucru"}).status_code == 200
    assert db.get_aviz(aid)["data_finalizare"] == ""

    # categoria se poate alege din start (link din dosar) și panoul o preselectează
    sel = c.get(f"/admin/att/aviz/{aid}?doc_type=lucrare_in_timpul").text
    assert '<option value="lucrare_in_timpul" selected>' in sel and 'data-testid="cat-lucrare_dupa"' in sel
    assert 'capture="environment"' in sel and "Fă poză" in sel and "Alege din galerie" in sel

    # 4) persistență: repornire portal = aceeași bază + aceleași fișiere
    server2 = _load_server(tmp / "data")
    c2, _ = _login(server2)
    d2 = c2.get("/admin/ui/loc/T-10").text
    assert "Reparație monument" in d2 and "Ioan" in d2 and "ÎNAINTE (1)" in d2
    assert len(db.list_documents("loc", "T-10", "poza_loc")) == 1


def test_viewer_cannot_modify_and_anonymous_is_refused(phone):
    server, db, _ = phone
    db.add_aviz("T-10", "1", "01.01.2025", "X")
    aid = db.list_avize_for_loc("T-10")[0]["id"]
    db.save_inhumat("T-10", {"nume": "A", "prenume": "B"})
    iid = db.load_inhumati_for_loc("T-10")[0]["id"]
    v, _ = _login(server, "viewer")
    assert v.post(f"/admin/ui/aviz/{aid}/edit", data={"stare": "finalizata"}).status_code == 403
    assert v.post(f"/admin/ui/inhumat/{iid}/edit", data={"nume": "Z"}).status_code == 403
    assert _up(v, "aviz", aid, "lucrare_dupa").status_code == 403
    anon = TestClient(server.app, headers=PHONE)
    assert anon.post(f"/admin/ui/aviz/{aid}/edit", data={"stare": "finalizata"}, follow_redirects=False).status_code in (401, 303, 403)
    assert anon.get("/api/admin/map.json").status_code == 401
    assert db.get_aviz(aid)["stare"] in ("", None) and db.get_inhumat(iid)["nume"] == "A"


def test_default_password_accounts_listed_before_lan(phone):
    _, _, _ = phone
    import cimitir_auth
    assert "admin" in cimitir_auth.default_password_accounts() and "op" not in cimitir_auth.default_password_accounts()


def test_phone_works_only_on_active_registry_no_leak_and_media_in_registry(env):
    reg, db = env.reg, env.db
    w = make_world(env)
    import cimitir_auth
    for key in ("P_A", "M_A"):
        reg.activate(w[key], env.data, catalog=env.cat)
        db.init_db()
        cimitir_auth.init_auth()
        cimitir_auth.create_user("op", PW, "operator")
        add_plot(db, "R-10")
    reg.activate(w["P_A"], env.data, catalog=env.cat)
    reg.deactivate()                                       # portalul e alt proces: urmează registrul activ din catalog
    server = _load_server(env.data)
    reg.activate(w["P_A"], env.data, catalog=env.cat)
    reg.deactivate()
    c, _ = _login(server)
    assert c.post("/admin/ui/loc/R-10/aviz", data={"lucrare": "Lucrare A", "stare": "planificata"}).status_code == 200
    assert _up(c, "loc", "R-10", "poza_loc").status_code == 200
    b_a = reg.activate(w["P_A"], env.data, catalog=env.cat)
    assert [x["lucrare"] for x in db.list_avize_for_loc("R-10")] == ["Lucrare A"]
    photo = Path(db.list_documents("loc", "R-10", "poza_loc")[0]["file_path"])
    assert b_a.contains(photo) and photo.is_file()
    reg.deactivate()
    b_m = reg.activate(w["M_A"], env.data, catalog=env.cat)
    assert db.list_avize_for_loc("R-10") == [] and db.list_documents("loc", "R-10", "poza_loc") == []
    reg.deactivate()                                       # MAIN comută pe M_A: portalul îl urmează fără repornire
    c2, _ = _login(server)
    assert "Lucrare A" not in c2.get("/admin/ui/loc/R-10").text
    assert c2.post("/admin/ui/loc/R-10/aviz", data={"lucrare": "Lucrare M"}).status_code == 200
    reg.activate(w["M_A"], env.data, catalog=env.cat)
    assert [x["lucrare"] for x in db.list_avize_for_loc("R-10")] == ["Lucrare M"]
    assert not b_m.contains(photo)
