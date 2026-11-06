"""P2 §1-2: integritatea fluxului Concesionar → Loc → Contract → Plată → Istoric și Loc ↔ Înhumați.

Fiecare test a fost scris întâi pentru a reproduce un defect confirmat (vezi docs/RAPORT_P2_AUDIT_OPERATIONAL.md).
Date 100% fictive, bază SQLite în tmp_path.
"""
import json
import math
import sqlite3
import sys
from pathlib import Path

import pytest

DESKTOP = Path(__file__).resolve().parents[1]
SHARED = DESKTOP.parent / "shared"
for p in (str(DESKTOP), str(SHARED)):
    if p not in sys.path:
        sys.path.insert(0, p)

import database as db  # noqa: E402


@pytest.fixture
def d(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_audit_actor", "")
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "cimitir.db"))
    db.init_db()
    return db


def count(table, where="1=1", args=()):
    c = db.get_connection()
    try:
        return c.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", args).fetchone()[0]
    finally:
        c.close()


def mk(d):
    d.save_plot_to_db("A-1", [(0, 0), (1, 0), (1, 1)], "#fff")
    d.save_plot_to_db("A-2", [(2, 0), (3, 0), (3, 1)], "#fff")
    c1 = d.save_concesionar({"nume": "Popescu", "prenume": "Ana", "cnp": "2000000000001"})
    c2 = d.save_concesionar({"nume": "Ionescu", "prenume": "Dan", "cnp": "1000000000002"})
    return c1, c2


# ---------------------------------------------------------------- PLĂȚI
def test_plata_refuzata_pentru_loc_inexistent(d):
    mk(d)
    assert d.add_plata("NU-EXISTA", 100, "2024-01-01") is None
    assert count("plati") == 0
    assert "nu există" in d.last_refusal()


@pytest.mark.parametrize("suma", [0, -5, float("nan"), float("inf"), "abc", None, ""])
def test_plata_refuzata_pentru_suma_invalida(d, suma):
    mk(d)
    assert d.add_plata("A-1", suma, "2024-01-01") is None
    assert count("plati") == 0


def test_plata_nu_se_leaga_de_contractul_altui_loc_sau_inexistent(d):
    c1, _ = mk(d)
    k = d.save_contract({"numar_contract": "C-1", "cod_loc": "A-2", "concesionar_id": c1})
    assert d.add_plata("A-1", 50, "2024-01-01", contract_id=k) is None
    assert d.add_plata("A-1", 50, "2024-01-01", contract_id=9999) is None
    assert count("plati") == 0
    pid = d.add_plata("A-2", 50, "2024-01-01", contract_id=k)
    assert pid and d.list_plati("A-2")[0]["contract_id"] == k


def test_retrimiterea_aceleiasi_chitante_nu_dubleaza_plata(d):
    mk(d)
    a = d.add_plata("A-1", 120.5, "2024-02-02", "concesiune", "R-77")
    b = d.add_plata("A-1", 120.5, "2024-02-02", "concesiune", "R-77")
    assert a == b and count("plati") == 1 and d.total_plati("A-1") == pytest.approx(120.5)
    c = d.add_plata("A-1", 120.5, "2024-02-02", "concesiune", "R-78")
    assert c != a and count("plati") == 2
    assert d.total_plati("A-1") == pytest.approx(241.0)


def test_plati_partiale_si_total_pe_loc(d):
    mk(d)
    for s in (0.1, 0.2, 99.7):
        assert d.add_plata("A-1", s, "2024-03-03", chitanta_nr=f"R-{s}")
    d.add_plata("A-2", 10, "2024-03-03")
    assert d.total_plati("A-1") == pytest.approx(100.0)
    assert d.total_plati() == pytest.approx(110.0)
    assert not math.isnan(d.total_plati("NU-EXISTA")) and d.total_plati("NU-EXISTA") == 0


def test_stergerea_unei_plati_nu_atinge_celelalte(d):
    mk(d)
    p1 = d.add_plata("A-1", 10, "2024-01-01", chitanta_nr="1")
    p2 = d.add_plata("A-1", 20, "2024-01-02", chitanta_nr="2")
    assert d.delete_plata(p1)
    assert [p["id"] for p in d.list_plati("A-1")] == [p2]


# ---------------------------------------------------------------- CONTRACTE
def test_contract_cu_plati_sau_inhumati_nu_se_sterge(d):
    c1, _ = mk(d)
    k = d.save_contract({"numar_contract": "C-1", "cod_loc": "A-1", "concesionar_id": c1})
    d.add_plata("A-1", 10, "2024-01-01", contract_id=k)
    assert d.delete_contract(k) is False
    assert count("contracte") == 1 and count("plati", "contract_id=?", (k,)) == 1
    assert "plat" in d.last_refusal()
    k2 = d.save_contract({"numar_contract": "C-2", "cod_loc": "A-2", "concesionar_id": c1})
    assert d.delete_contract(k2) is True


def test_contract_cu_inhumat_asociat_nu_se_sterge(d):
    c1, _ = mk(d)
    k = d.save_contract({"numar_contract": "C-1", "cod_loc": "A-1", "concesionar_id": c1})
    i = d.save_inhumat("A-1", {"nume": "X", "prenume": "Y"})
    conn = d.get_connection(); conn.execute("UPDATE inhumati SET contract_id=? WHERE id=?", (k, i)); conn.close()
    assert d.delete_contract(k) is False
    assert d.get_inhumat(i)["contract_id"] == k


def test_concesionar_cu_contracte_nu_se_sterge(d):
    c1, c2 = mk(d)
    k = d.save_contract({"numar_contract": "C-1", "cod_loc": "A-1", "concesionar_id": c1})
    assert d.delete_concesionar(c1) is False
    assert d.get_contract(k)["concesionar_id"] == c1
    assert d.delete_concesionar(c2) is True


def test_editarea_contractului_pastreaza_valabilitatea_si_extra_data(d):
    c1, _ = mk(d)
    k = d.save_contract({"numar_contract": "C-1", "cod_loc": "A-1", "concesionar_id": c1, "valabilitate": "perpetuă",
                         "extra_data": {"sursa": "cloud"}, "data_inceput": "01.01.2010"})
    form = {"numar_contract": "C-1b", "cod_loc": "A-1", "concesionar_id": c1, "data_inceput": "01.01.2010", "extra_data": None,
            "observatii": "ț ș ă î â"}
    assert d.save_contract(form, contract_id=k) == k
    got = d.get_contract(k)
    assert got["numar_contract"] == "C-1b" and got["valabilitate"] == "perpetuă"
    assert got["extra_data"] == {"sursa": "cloud"} and got["observatii"] == "ț ș ă î â"
    assert count("contracte") == 1


def test_editarea_unui_contract_inexistent_nu_pretinde_succes(d):
    c1, _ = mk(d)
    assert d.save_contract({"numar_contract": "X", "concesionar_id": c1}, contract_id=424242) is None
    assert d.save_concesionar({"nume": "X"}, concesionar_id=424242) is None
    assert count("contracte") == 0 and count("concesionari") == 2


def test_contracte_succesive_pe_acelasi_loc_raman_independente(d):
    c1, c2 = mk(d)
    k1 = d.save_contract({"numar_contract": "C-1", "cod_loc": "A-1", "concesionar_id": c1, "data_inceput": "01.01.2000", "data_sfarsit": "01.01.2010"})
    before = d.get_contract(k1)
    k2 = d.save_contract({"numar_contract": "C-2", "cod_loc": "A-1", "concesionar_id": c2, "data_inceput": "02.01.2010", "data_sfarsit": "01.01.2040"})
    assert k1 != k2 and d.get_contract(k1) == before
    assert d.get_contract(k2)["concesionar_id"] == c2
    expired = [c["id"] for c in d.expiring_contracts(30)]
    assert k1 in expired and k2 not in expired


# ---------------------------------------------------------------- ÎNHUMAȚI
def test_mai_multi_inhumati_in_acelasi_loc_si_istoric(d):
    mk(d)
    ids = [d.save_inhumat("A-1", {"nume": f"N{i}", "prenume": "P", "data_inhumare": f"0{i}.01.2020"}) for i in (1, 2, 3)]
    got = d.load_inhumati_for_loc("A-1")
    assert sorted(r["id"] for r in got) == sorted(ids) and all(r["cod_loc"] == "A-1" for r in got)
    assert d.load_inhumati_for_loc("A-2") == []


def test_editarea_inhumatului_nu_dubleaza_si_nu_sterge_campuri_absente(d):
    mk(d)
    i = d.save_inhumat("A-1", {"nume": "Pop", "prenume": "Ion", "data_inhumare": "05.05.2005", "religie": "ortodox",
                               "nr_adeverinta_inhumare": "AD-9", "observatii": "nota veche"})
    assert d.save_inhumat("A-1", {"nume": "Pop", "prenume": "Ion", "cauza_deces": "x"}, inhumat_id=i) == i
    got = d.get_inhumat(i)
    assert count("inhumati") == 1
    assert got["data_inhumare"] == "05.05.2005" and got["religie"] == "ortodox"
    assert got["nr_adeverinta_inhumare"] == "AD-9" and got["observatii"] == "nota veche" and got["cauza_deces"] == "x"


def test_campurile_trimise_explicit_gol_se_golesc(d):
    mk(d)
    i = d.save_inhumat("A-1", {"nume": "Pop", "prenume": "Ion", "religie": "ortodox"})
    d.save_inhumat("A-1", {"nume": "Pop", "prenume": "Ion", "religie": ""}, inhumat_id=i)
    assert d.get_inhumat(i)["religie"] == ""


def test_inhumat_asociat_dupa_id_la_locul_corect_si_mutare(d):
    mk(d)
    i = d.save_inhumat("A-1", {"nume": "Pop", "prenume": "Ion"})
    d.save_inhumat("A-2", {"nume": "Pop", "prenume": "Ion"}, inhumat_id=i)
    assert d.load_inhumati_for_loc("A-1") == [] and [r["id"] for r in d.load_inhumati_for_loc("A-2")] == [i]
    assert count("inhumati") == 1


def test_inhumat_pe_loc_inexistent_sau_id_inexistent_nu_creeaza_nimic(d):
    mk(d)
    assert d.save_inhumat("NU-EXISTA", {"nume": "A", "prenume": "B"}) is None
    assert d.save_inhumat("A-1", {"nume": "A", "prenume": "B"}, inhumat_id=777) is None
    assert count("inhumati") == 0


def test_scanare_ai_actualizeaza_doar_valorile_extrase(d):
    """Reproduce BUG: actualizarea din certificat de deces golea data înhumării, religia, profesia etc."""
    from ai_scanare import duplicate_check as dc
    mk(d)
    i = d.save_inhumat("A-1", {"nume": "Pop", "prenume": "Ion", "cnp": "1500101123456", "data_inhumare": "05.05.2005",
                               "data_deshumare": "01.01.2030", "profesie": "tâmplar", "religie": "ortodox", "observatii": "nota veche"})
    existing = dc.find_inhumat_by_cnp("1500101123456")
    assert existing["observatii"] == "nota veche"
    extracted = {"nume": "Pop", "prenume": "Ion", "data_deces": "01.05.2005", "cauza_deces": "", "sex": ""}
    updated = {k: extracted.get(k) for k in ("nume", "prenume", "data_nasterii", "data_deces", "sex", "cauza_deces",
                                              "ultima_adresa", "nr_certificat_deces") if extracted.get(k)}
    updated["cnp"] = "1500101123456"
    updated["observatii"] = (existing["observatii"] + " | Actualizat din scanare AI").strip(" |")
    assert dc.update_inhumat(i, existing["cod_loc"], updated) is True
    got = d.get_inhumat(i)
    assert (got["data_inhumare"], got["data_deshumare"], got["profesie"], got["religie"]) == ("05.05.2005", "01.01.2030", "tâmplar", "ortodox")
    assert got["data_deces"] == "01.05.2005" and got["observatii"] == "nota veche | Actualizat din scanare AI"
    assert dc.update_inhumat(99999, "A-1", updated) is False


# ---------------------------------------------------------------- ȘTERGERE LOC
@pytest.mark.parametrize("what", ["inhumat", "contract", "plata", "foto", "aviz", "document"])
def test_loc_cu_istoric_nu_se_sterge_si_nu_pierde_nimic(d, what, tmp_path):
    c1, _ = mk(d)
    if what == "inhumat":
        d.save_inhumat("A-1", {"nume": "A", "prenume": "B"})
    elif what == "contract":
        d.save_contract({"numar_contract": "C", "cod_loc": "A-1", "concesionar_id": c1})
    elif what == "plata":
        d.add_plata("A-1", 5, "2024-01-01")
    elif what == "foto":
        f = tmp_path / "p.jpg"; f.write_bytes(b"x")
        d.save_photo("A-1", str(f))
    elif what == "aviz":
        d.add_aviz("A-1", "AV-1", "2024-01-01")
    else:
        f = tmp_path / "d.pdf"; f.write_bytes(b"%PDF")
        d.add_document("loc", "A-1", "act", str(f))
    snap = {t: count(t) for t in ("locuri_de_veci", "inhumati", "contracte", "plati", "photos", "amenajari_avize", "documents")}
    assert d.delete_plot("A-1") is False
    assert d.delete_plot_blockers("A-1")
    assert {t: count(t) for t in snap} == snap
    assert d.load_loc_by_cod("A-1") is not None


def test_loc_gol_se_poate_sterge(d):
    mk(d)
    assert d.delete_plot_blockers("A-2") == "" and d.delete_plot("A-2") is True
    assert d.load_loc_by_cod("A-2") is None and d.load_loc_by_cod("A-1") is not None


# ---------------------------------------------------------------- JURNAL DE AUDIT
def _audit_rows():
    c = db.get_connection()
    try:
        return [dict(r) for r in c.execute("SELECT * FROM audit_log ORDER BY id")]
    finally:
        c.close()


def test_operatiunile_importante_sunt_jurnalizate_fara_date_personale(d):
    d.set_audit_actor("operator1")
    c1, _ = mk(d)
    k = d.save_contract({"numar_contract": "C-9", "cod_loc": "A-1", "concesionar_id": c1, "owner_cnp": "2000000000001",
                         "owner_fullname": "Popescu Ana"})
    d.save_contract({"numar_contract": "C-9b", "cod_loc": "A-1", "concesionar_id": c1}, contract_id=k)
    i = d.save_inhumat("A-1", {"nume": "Ionescu", "prenume": "Vasile", "cnp": "1500101123456"})
    p = d.add_plata("A-1", 75.25, "2024-01-01", chitanta_nr="R-5")
    d.delete_plata(p)
    d.delete_inhumat(i)
    rows = _audit_rows()
    actions = [r["action"] for r in rows]
    assert actions == ["contract.create", "contract.update", "inhumat.create", "plata.create", "plata.delete", "inhumat.delete"]
    assert all(r["actor"] == "operator1" and r["ts"] for r in rows)
    blob = json.dumps(rows, ensure_ascii=False)
    for pii in ("Popescu", "Ana", "Ionescu", "Vasile", "2000000000001", "1500101123456"):
        assert pii not in blob, f"date personale în jurnal: {pii}"
    deleted = [r for r in rows if r["action"] == "plata.delete"][0]
    assert "suma=75.25" in deleted["detail"] and "R-5" in deleted["detail"]


def test_fara_utilizator_MAIN_autentificat_nu_se_scrie_jurnal_local(d, monkeypatch):
    monkeypatch.setattr(db, "_audit_actor", "")
    mk(d)
    p = d.add_plata("A-1", 5, "2024-01-01")
    d.delete_plata(p)
    assert _audit_rows() == []


def test_operatiunile_refuzate_nu_scriu_in_jurnal(d):
    d.set_audit_actor("op")
    mk(d)
    d.add_plata("NU-EXISTA", 5, "2024-01-01")
    d.delete_plot("NU-EXISTA")
    assert _audit_rows() == []
