"""Pasul B – instrumentul de verificare a evidențelor (strict citire): corectitudine, idempotență, absența efectelor secundare, confidențialitate.
Date 100% sintetice, într-un SQLite temporar creat cu schema reală (shared/cimitir_database.init_db). Nu atinge Supabase/PROD."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))
import cimitir_database as sdb  # noqa: E402
import cimitir_integrity as ci  # noqa: E402
from cimitir_integrity import checks_records, model, util  # noqa: E402

TODAY = date(2026, 6, 1)
CNP_W = (2, 7, 9, 1, 4, 6, 3, 5, 8, 2, 7, 9)


def make_cnp(s, yy, mm, dd, jj=40, nnn=123):
    d = f"{s}{yy:02d}{mm:02d}{dd:02d}{jj:02d}{nnn:03d}"
    c = sum(int(a) * b for a, b in zip(d, CNP_W)) % 11
    return d + str(1 if c == 10 else c)


CNP_A, CNP_B, CNP_C, CNP_D, CNP_E = (make_cnp(1, 80, 1, 1), make_cnp(2, 85, 5, 5, nnn=124), make_cnp(1, 70, 3, 3, nnn=125),
                                     make_cnp(2, 60, 7, 7, nnn=126), make_cnp(1, 55, 9, 9, nnn=127))
CI_SERIE = "XZ123456"
PHONE = "0722123456"
SENSITIVE = [CNP_A, CNP_B, CNP_C, CNP_D, CNP_E, CI_SERIE, PHONE, "0733999888", "secret.person@example.com", "Strada Secreta 99"]
SQUARE = json.dumps([[0, 0], [10, 0], [10, 10], [0, 10]])


def sq(x, y):
    return json.dumps([[x, y], [x + 5, y], [x + 5, y + 5], [x, y + 5]])


def new_db(tmp_path, name="t.db") -> str:
    path = str(tmp_path / name)
    sdb.set_active_db_path(path)
    sdb.init_db()
    c = sqlite3.connect(path)
    for t in ("concesionari", "contracte", "inhumati", "plati", "amenajari_avize"):      # ca după desktop/entity_sync.ensure_tracking
        c.execute(f"ALTER TABLE {t} ADD COLUMN uid TEXT")
    c.commit()
    c.close()
    return path


class Raw:
    def __init__(self, path):
        self.c = sqlite3.connect(path, isolation_level=None)
        self.c.execute("PRAGMA foreign_keys=OFF")

    def ins(self, table, **kw):
        cols = ", ".join(kw)
        return self.c.execute(f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' for _ in kw)})", tuple(kw.values())).lastrowid

    def sql(self, q, a=()):
        return self.c.execute(q, a)

    def close(self):
        self.c.close()


def codes(rep, **flt):
    return {(i.code, i.entity, i.record_id) for i in rep.issues if all(getattr(i, k) == v for k, v in flt.items())}


def has(rep, code, entity, rid):
    return (code, entity, str(rid)) in codes(rep)


def run(path, **kw):
    return ci.run_on_file(path, today=TODAY, **kw)


# ----------------------------------------------------------------------------- bază curată
@pytest.fixture()
def clean_db(tmp_path):
    path = new_db(tmp_path, "clean.db")
    r = Raw(path)
    r.ins("sectoare", id="S1", points=SQUARE, color="#fff")
    a1 = r.ins("locuri_de_veci", cod_loc="A-1", points=sq(0, 0), sector="S1", parcela="1", rand="1", numar="1", ocupat=1, are_inhumari=1, uid="u-a1")
    a2 = r.ins("locuri_de_veci", cod_loc="A-2", points=sq(50, 0), sector="S1", parcela="1", rand="1", numar="2", uid="u-a2")
    c1 = r.ins("concesionari", nume="Popescu", prenume="Ion", cnp=CNP_A, telefon=PHONE, uid="u-c1")
    k1 = r.ins("contracte", numar_contract="1", data_contract="01.01.2020", data_inceput="01.01.2020", data_sfarsit="01.01.2030", concesionar_id=c1, id_loc=a1, cod_loc="A-1", owner_fullname="Popescu Ion", owner_cnp=CNP_A, uid="u-k1")
    r.ins("contracte", numar_contract="2", data_contract="01.01.2021", data_inceput="01.01.2021", data_sfarsit="01.01.2031", concesionar_id=c1, id_loc=a2, cod_loc="A-2", uid="u-k2")
    r.ins("inhumati", nume="Popescu", prenume="Maria", data_nasterii="01.01.1940", data_deces="01.02.2020", data_inhumare="04.02.2020", id_loc=a1, uid="u-i1")
    r.ins("amenajari_avize", cod_loc="A-1", nr_aviz="5", data_aviz="01.03.2021", lucrare="gard", stare="finalizata", executant="Firma", data_inceput="02.03.2021", data_finalizare="10.03.2021", uid="u-av1")
    r.ins("plati", cod_loc="A-1", contract_id=k1, suma=100.0, data_plata="01.01.2020", tip="Concesiune", chitanta_nr="c1", uid="u-p1")
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 test")
    r.ins("documents", entity_type="loc", entity_id="A-1", doc_type="poza_loc", file_path=str(f), size=f.stat().st_size, sha256=hashlib.sha256(f.read_bytes()).hexdigest())
    r.close()
    return path


def test_clean_database_has_no_critical_or_error_and_valid_cases_are_not_flagged(clean_db):
    rep = run(clean_db)
    bad = [(i.code, i.entity, i.record_id) for i in rep.issues if i.severity in ("critical", "error")]
    assert bad == [] and rep.meta["check_errors"] == []
    # persoana legată legitim de DOUĂ locuri (un concesionar, două contracte) NU este duplicat
    assert not any(i.code in ("CON004", "CON005", "CON006", "CON007") for i in rep.issues)
    # relații opționale valide: înhumat fără contract, loc fără poligon etc. nu sunt probleme
    assert not any(i.code in ("INH003", "INH004", "CTR001", "CTR004") for i in rep.issues)
    assert {i.code for i in rep.issues} <= {"FIN000", "CON008", "CTR014"}, [(i.code, i.description) for i in rep.issues]


# ----------------------------------------------------------------------------- bază cu defecte
@pytest.fixture()
def bad_db(tmp_path):
    path = new_db(tmp_path, "t.db")
    r = Raw(path)
    R = {}
    r.ins("sectoare", id="S1", points=SQUARE, color="#fff")
    r.ins("sectoare", id="S2", points="[[0,0],[1,1]]", color="#fff")
    R["sec_bad"] = "S2"
    L = lambda **kw: r.ins("locuri_de_veci", **kw)  # noqa: E731
    R["ok1"] = L(cod_loc="A-1", points=sq(0, 0), sector="S1", parcela="1", rand="1", numar="1", capacitate="1", uid="u1")
    R["ok2"] = L(cod_loc="A-2", points=sq(50, 0), sector="S1", parcela="1", rand="1", numar="2", uid="u2")
    R["nocode"] = L(cod_loc=None, uid="u3")
    R["space"] = L(cod_loc="B-1 ", uid="u4")
    R["case"] = L(cod_loc="b-1", uid="u5")
    R["json"] = L(cod_loc="P-1", points="not json", uid="u6")
    R["shape"] = L(cod_loc="P-2", points="[[1,2],[3]]", uid="u7")
    R["line"] = L(cod_loc="P-3", points="[[0,0],[1,1]]", uid="u8")
    R["bowtie"] = L(cod_loc="P-4", points="[[0,0],[10,10],[10,0],[0,10]]", uid="u9")
    R["same1"] = L(cod_loc="P-5", points=sq(100, 100), uid="u10")
    R["same2"] = L(cod_loc="P-6", points=sq(100, 100), uid="u11")
    R["big"] = L(cod_loc="P-7", points=json.dumps([[200 + 50 * math.cos(t / 501 * 6.2832), 200 + 50 * math.sin(t / 501 * 6.2832)] for t in range(501)]), uid="u12")
    R["sector"] = L(cod_loc="S-1", sector="S9", uid="u13")
    R["pos1"] = L(cod_loc="Q-1", sector="S1", parcela="9", rand="9", numar="9", uid="u14")
    R["pos2"] = L(cod_loc="Q-2", sector="S1", parcela="9", rand="9", numar="9", uid="u15")
    R["occ"] = L(cod_loc="O-1", ocupat=0, are_inhumari=0, uid="u16")
    R["flag"] = L(cod_loc="O-2", are_inhumari=1, uid="u17")
    R["sup"] = L(cod_loc="N-1", suprafata=-5, extra_data="{bad", uid="u18")
    R["long"] = L(cod_loc="N-2", observatii="x" * 4001, uid="u19")
    R["nouid"] = L(cod_loc="N-3")
    R["nopoly"] = L(cod_loc="N-4", uid="u20")
    C = lambda **kw: r.ins("concesionari", **kw)  # noqa: E731
    R["c1"] = C(nume="Popescu", prenume="Ion", cnp=CNP_A, uid="c1")
    R["g1a"] = C(nume="Ionescu", prenume="Maria", cnp=CNP_B, uid="c2")
    R["g1b"] = C(nume="Ionescu", prenume="Maria", cnp=CNP_B, uid="c3")
    R["g2a"] = C(nume="Marin", prenume="Dan", cnp=CNP_C, uid="c4")
    R["g2b"] = C(nume="DAN", prenume="marin", cnp=CNP_C, uid="c5")
    R["g3a"] = C(nume="Radu", prenume="Paul", cnp=CNP_D, uid="c6")
    R["g3b"] = C(nume="Stan", prenume="Lia", cnp=CNP_D, uid="c7")
    R["h1"] = C(nume="Georgescu", prenume="Vlad", cnp=CNP_A[:12] + "0", uid="c8")
    R["h2"] = C(nume="Georgescu", prenume="Vlad", cnp=CNP_E, uid="c9")
    R["s1"] = C(nume="Dumitru", prenume="Ana", telefon="0733999888", uid="c10")
    R["s2"] = C(nume="Dumitru", prenume="Ana", telefon="0733 999 888", uid="c11")
    R["emptyc"] = C(nume="", prenume=None, uid="c12")
    R["badcnp"] = C(nume="Zamfir", prenume="Eva", cnp="123", email="nu-e-email", uid="c13")
    R["empty2"] = C(nume="Fara", prenume="Date", uid="c14")
    K = lambda **kw: r.ins("contracte", **kw)  # noqa: E731
    ok1, ok2, c1 = R["ok1"], R["ok2"], R["c1"]
    R["k_ok"] = K(numar_contract="1", data_contract="01.01.2020", concesionar_id=c1, id_loc=ok1, cod_loc="A-1", owner_fullname="Popescu Ion", uid="k1")
    R["k_noc"] = K(numar_contract="2", id_loc=ok1, cod_loc="A-1", uid="k2")
    R["k_own"] = K(numar_contract="3", id_loc=ok1, cod_loc="A-1", owner_fullname="Cineva Anume", uid="k3")
    R["k_dc"] = K(numar_contract="4", concesionar_id=9999, id_loc=ok1, cod_loc="A-1", uid="k4")
    R["k_noloc"] = K(numar_contract="5", concesionar_id=c1, uid="k5")
    R["k_dl"] = K(numar_contract="6", concesionar_id=c1, id_loc=9999, cod_loc="A-1", uid="k6")
    R["k_mis"] = K(numar_contract="7", concesionar_id=c1, id_loc=ok2, cod_loc="A-1", uid="k7")
    R["k_idnull"] = K(numar_contract="8", concesionar_id=c1, cod_loc="A-1", uid="k8")
    R["k_idnull_bad"] = K(numar_contract="9", concesionar_id=c1, cod_loc="ZZZ", uid="k9")
    R["k_nonum"] = K(numar_contract="", concesionar_id=c1, id_loc=ok1, cod_loc="A-1", uid="k10")
    R["k_d1"] = K(numar_contract="77", data_contract="01.01.2020", concesionar_id=c1, id_loc=ok1, cod_loc="A-1", uid="k11")
    R["k_d2"] = K(numar_contract="77", data_contract="01.01.2020", concesionar_id=c1, id_loc=ok1, cod_loc="A-1", uid="k12")
    R["k_per"] = K(numar_contract="10", data_inceput="01.01.2025", data_sfarsit="01.01.2020", concesionar_id=c1, id_loc=ok1, cod_loc="A-1", uid="k13")
    R["k_ov1"] = K(numar_contract="11", data_inceput="01.01.2020", data_sfarsit="01.01.2030", concesionar_id=R["h1"], id_loc=ok2, cod_loc="A-2", uid="k14")
    R["k_ov2"] = K(numar_contract="12", data_inceput="01.01.2025", data_sfarsit="01.01.2035", concesionar_id=R["h2"], id_loc=ok2, cod_loc="A-2", uid="k15")
    R["k_nm"] = K(numar_contract="13", concesionar_id=c1, id_loc=ok1, cod_loc="A-1", owner_fullname="Altcineva Total", owner_cnp=CNP_B, uid="k16")
    R["k_parc"] = K(numar_contract="14", concesionar_id=c1, id_loc=ok1, cod_loc="A-1", cemetery_parcel="77", uid="k17")
    R["k_long"] = K(numar_contract="15", concesionar_id=c1, id_loc=ok1, cod_loc="A-1", observatii="y" * 4001, uid="k18")
    R["k_iso"] = K(numar_contract="16", data_contract="2020-01-31", data_inceput="31.02.2020", concesionar_id=c1, id_loc=ok1, cod_loc="A-1", uid="k19")
    I = lambda **kw: r.ins("inhumati", **kw)  # noqa: E731
    R["i_ok"] = I(nume="Popescu", prenume="Maria", data_nasterii="01.01.1940", data_deces="01.02.2020", id_loc=ok1, uid="i1")
    R["i_noloc"] = I(nume="A", prenume="B", uid="i2")
    R["i_dl"] = I(nume="A", prenume="B", id_loc=9999, uid="i3")
    R["i_dc"] = I(nume="A", prenume="C", id_loc=ok1, contract_id=9999, uid="i4")
    R["i_co"] = I(nume="A", prenume="D", id_loc=ok1, contract_id=R["k_mis"], uid="i5")
    R["i_nn"] = I(nume="", prenume="", id_loc=ok1, uid="i6")
    R["i_dates"] = I(nume="B", prenume="A", data_nasterii="01.01.2000", data_deces="01.01.1990", data_inhumare="01.01.1980", data_deshumare="01.01.1970", id_loc=ok2, uid="i7")
    R["i_inv"] = I(nume="B", prenume="B", data_deces="99.99.2020", id_loc=ok2, uid="i8")
    R["i_fut"] = I(nume="B", prenume="C", data_deces="01.01.2030", id_loc=ok2, uid="i9")
    R["i_c1"] = I(nume="C", prenume="A", cnp=CNP_A, id_loc=ok2, uid="i10")
    R["i_c2"] = I(nume="C", prenume="B", cnp=CNP_A, id_loc=ok2, uid="i11")
    R["i_d1"] = I(nume="Dup", prenume="Lic", data_deces="05.05.2015", id_loc=ok2, uid="i12")
    R["i_d2"] = I(nume="Dup", prenume="Lic", data_deces="05.05.2015", id_loc=ok1, uid="i13")
    R["i_bad"] = I(nume="E", prenume="A", cnp="1234567890123", id_loc=ok1, uid="i14")
    R["i_mis"] = I(nume="E", prenume="B", cnp=CNP_A, data_nasterii="02.02.1999", sex="F", id_loc=R["nopoly"], uid="i15")
    R["i_long"] = I(nume="E", prenume="C", observatii="z" * 4001, id_loc=ok1, uid="i16")
    R["cap_free"] = L(cod_loc="O-3", capacitate="4", uid="u21")
    R["disp_occ"] = L(cod_loc="O-4", disponibilitate="Ocupat", uid="u22")
    I(nume="G", prenume="A", id_loc=R["cap_free"], uid="i19")
    I(nume="G", prenume="B", id_loc=R["disp_occ"], uid="i20")
    R["i_occ"] = I(nume="F", prenume="A", id_loc=R["occ"], uid="i18")
    R["i_iso"] = I(nume="E", prenume="D", data_deces="2020-05-05", id_loc=R["nopoly"], uid="i17")
    A = lambda **kw: r.ins("amenajari_avize", **kw)  # noqa: E731
    R["a_ok"] = A(cod_loc="A-1", nr_aviz="1", data_aviz="01.03.2021", uid="a1")
    R["a_dang"] = A(cod_loc="NU-EXISTA", nr_aviz="2", uid="a2")
    R["a_nonr"] = A(cod_loc="A-1", nr_aviz="", uid="a3")
    R["a_d1"] = A(cod_loc="A-1", nr_aviz="9", uid="a4")
    R["a_d2"] = A(cod_loc="A-1", nr_aviz="9", uid="a5")
    R["a_fin"] = A(cod_loc="A-1", nr_aviz="3", data_inceput="10.03.2021", data_finalizare="01.03.2021", stare="finalizata", uid="a6")
    R["a_st"] = A(cod_loc="A-1", nr_aviz="4", stare="altceva", uid="a7")
    R["a_inc"] = A(cod_loc="A-1", nr_aviz="5", stare="planificata", data_finalizare="01.03.2021", uid="a8")
    R["a_dt"] = A(cod_loc="A-1", nr_aviz="6", data_aviz="31.02.2021", uid="a9")
    R["a_long"] = A(cod_loc="A-1", nr_aviz="7", observatii="w" * 4001, uid="a10")
    R["a_fin2"] = A(cod_loc="A-1", nr_aviz="8", stare="finalizata", uid="a11")
    R["a_fin3"] = A(cod_loc="A-1", nr_aviz="10", data_finalizare="01.03.2021", uid="a12")
    f = tmp_path / "real.pdf"
    f.write_bytes(b"0123456789")
    h_ok = hashlib.sha256(b"0123456789").hexdigest()
    D = lambda **kw: r.ins("documents", **kw)  # noqa: E731
    R["d_ok"] = D(entity_type="loc", entity_id="A-1", doc_type="poza_loc", file_path=str(f), size=10, sha256=h_ok)
    R["d_et"] = D(entity_type="xyz", entity_id="1", doc_type="x", file_path=str(f))
    R["d_dang"] = D(entity_type="inhumat", entity_id="9999", doc_type="x", file_path=str(f))
    R["d_nofile"] = D(entity_type="loc", entity_id="A-1", doc_type="x", file_path="")
    R["d_miss"] = D(entity_type="loc", entity_id="A-1", doc_type="x", file_path=str(tmp_path / "lipsa.pdf"))
    R["d_media"] = D(entity_type="loc", entity_id="A-1", doc_type="x", file_path=str(tmp_path / "lipsa2.pdf"), media_id="m1")
    R["d_notype"] = D(entity_type="loc", entity_id="A-1", doc_type="", file_path=str(f))
    R["d_dup1"] = D(entity_type="loc", entity_id="A-2", doc_type="x", file_path=str(f), sha256=h_ok)
    R["d_dup2"] = D(entity_type="loc", entity_id="A-2", doc_type="y", file_path=str(f), sha256=h_ok)
    R["d_wrong"] = D(entity_type="aviz", entity_id=str(R["a_ok"]), doc_type="poza_loc", file_path=str(f))
    R["d_size"] = D(entity_type="loc", entity_id="A-1", doc_type="x", file_path=str(f), size=999)
    R["d_numeric"] = D(entity_type="loc", entity_id=str(ok1), doc_type="x", file_path=str(f))
    R["d_hash"] = D(entity_type="loc", entity_id="A-1", doc_type="x", file_path=str(f), size=10, sha256="0" * 64)
    P = lambda **kw: r.ins("photos", **kw)  # noqa: E731
    R["ph_dang"] = P(cod_loc="NU-EXISTA", file_path=str(f))
    R["ph_c1"] = P(cod_loc="A-2", file_path=str(f), is_cover=1)
    R["ph_c2"] = P(cod_loc="A-2", file_path=str(f), is_cover=1)
    Pl = lambda **kw: r.ins("plati", **kw)  # noqa: E731
    R["p_ok"] = Pl(cod_loc="A-1", contract_id=R["k_ok"], suma=50.0, data_plata="01.01.2020", tip="T", chitanta_nr="r1")
    R["p_none"] = Pl(suma=10.0, data_plata="01.01.2020")
    R["p_dl"] = Pl(cod_loc="NU-EXISTA", suma=10.0, data_plata="01.01.2020")
    R["p_dc"] = Pl(cod_loc="A-1", contract_id=9999, suma=10.0, data_plata="01.01.2020")
    R["p_mis"] = Pl(cod_loc="A-2", contract_id=R["k_ok"], suma=10.0, data_plata="01.01.2020")
    R["p_zero"] = Pl(cod_loc="A-1", suma=0, data_plata="01.01.2020")
    R["p_nan"] = Pl(cod_loc="A-1", suma="abc", data_plata="01.01.2020")
    R["p_neg"] = Pl(cod_loc="A-1", suma=-20.0, data_plata="01.01.2020")
    R["p_date"] = Pl(cod_loc="A-1", suma=5.0, data_plata="32.13.2020")
    R["p_fut"] = Pl(cod_loc="A-1", suma=5.0, data_plata="01.01.2031")
    R["p_d1"] = Pl(cod_loc="A-1", suma=7.0, data_plata="02.02.2020", tip="X", chitanta_nr="r9")
    R["p_d2"] = Pl(cod_loc="A-1", suma=7.0, data_plata="02.02.2020", tip="X", chitanta_nr="r9")
    R["p_r1"] = Pl(cod_loc="A-1", suma=8.0, data_plata="03.03.2020", chitanta_nr="r55")
    R["p_r2"] = Pl(cod_loc="A-2", suma=9.0, data_plata="04.04.2020", chitanta_nr="r55")
    R["p_iso"] = Pl(cod_loc="A-1", suma=5.0, data_plata="2020-02-02")
    r.sql("UPDATE plati SET uid='dup' WHERE id IN (?,?)", (R["p_d1"], R["p_d2"]))
    r.close()
    return path, R


@pytest.fixture()
def bad_report(bad_db):
    path, R = bad_db
    return run(path, deep=True), R


EXPECTED = [  # (cod, entitate, cheie în R)
    ("LOC001", "locuri_de_veci", "nocode"), ("LOC002", "locuri_de_veci", "space"), ("LOC003", "locuri_de_veci", "nouid"), ("LOC004", "locuri_de_veci", "sector"),
    ("LOC005", "locuri_de_veci", "json"), ("LOC005", "locuri_de_veci", "shape"), ("LOC006", "locuri_de_veci", "line"), ("LOC006", "locuri_de_veci", "bowtie"),
    ("LOC007", "locuri_de_veci", "same1"), ("LOC007", "locuri_de_veci", "same2"), ("LOC008", "locuri_de_veci", "big"), ("LOC009", "locuri_de_veci", "space"),
    ("LOC009", "locuri_de_veci", "case"), ("LOC009", "locuri_de_veci", "pos1"), ("LOC009", "locuri_de_veci", "pos2"), ("LOC010", "locuri_de_veci", "occ"),
    ("LOC011", "locuri_de_veci", "sup"), ("LOC012", "locuri_de_veci", "flag"), ("LOC013", "locuri_de_veci", "long"), ("SEC001", "sectoare", "sec_bad"),
    ("CON001", "concesionari", "emptyc"), ("CON002", "concesionari", "empty2"), ("CON003", "concesionari", "badcnp"), ("CON004", "concesionari", "g1a"),
    ("CON004", "concesionari", "g1b"), ("CON004", "concesionari", "g2a"), ("CON004", "concesionari", "g2b"), ("CON005", "concesionari", "h1"),
    ("CON006", "concesionari", "s1"), ("CON006", "concesionari", "s2"), ("CON007", "concesionari", "g3a"), ("CON007", "concesionari", "g3b"),
    ("CON008", "concesionari", "empty2"), ("CON009", "concesionari", "badcnp"),
    ("CTR001", "contracte", "k_noc"), ("CTR002", "contracte", "k_dc"), ("CTR003", "contracte", "k_own"), ("CTR004", "contracte", "k_noloc"),
    ("CTR005", "contracte", "k_dl"), ("CTR006", "contracte", "k_mis"), ("CTR006", "contracte", "k_idnull"), ("CTR006", "contracte", "k_idnull_bad"),
    ("CTR007", "contracte", "k_nonum"), ("CTR008", "contracte", "k_d1"), ("CTR009", "contracte", "k_d2"), ("CTR010", "contracte", "k_per"),
    ("CTR011", "contracte", "k_ov1"), ("CTR011", "contracte", "k_ov2"), ("CTR012", "contracte", "k_nm"), ("CTR013", "contracte", "k_nm"),
    ("CTR014", "contracte", "k_parc"), ("CTR015", "contracte", "k_long"), ("DAT001", "contracte", "k_iso"), ("DAT002", "contracte", "k_iso"),
    ("INH001", "inhumati", "i_noloc"), ("INH002", "inhumati", "i_dl"), ("INH003", "inhumati", "i_dc"), ("INH004", "inhumati", "i_co"),
    ("INH005", "inhumati", "i_nn"), ("INH006", "inhumati", "i_dates"), ("INH007", "inhumati", "i_inv"), ("INH007", "inhumati", "i_fut"),
    ("INH008", "inhumati", "i_c1"), ("INH008", "inhumati", "i_c2"), ("INH009", "inhumati", "i_d1"), ("INH009", "inhumati", "i_d2"),
    ("INH010", "locuri_de_veci", "ok1"), ("INH011", "inhumati", "i_bad"), ("INH012", "inhumati", "i_mis"), ("INH013", "inhumati", "i_long"),
    ("DAT001", "inhumati", "i_iso"),
    ("AVZ001", "amenajari_avize", "a_dang"), ("AVZ002", "amenajari_avize", "a_nonr"), ("AVZ003", "amenajari_avize", "a_d1"), ("AVZ004", "amenajari_avize", "a_fin"),
    ("AVZ005", "amenajari_avize", "a_st"), ("AVZ006", "amenajari_avize", "a_inc"), ("AVZ006", "amenajari_avize", "a_fin2"), ("AVZ006", "amenajari_avize", "a_fin3"),
    ("AVZ007", "amenajari_avize", "a_dt"), ("AVZ008", "amenajari_avize", "a_long"),
    ("DOC001", "documents", "d_et"), ("DOC002", "documents", "d_dang"), ("DOC003", "documents", "d_nofile"), ("DOC004", "documents", "d_miss"),
    ("DOC005", "documents", "d_media"), ("DOC006", "documents", "d_notype"), ("DOC007", "documents", "d_dup1"), ("DOC008", "documents", "d_wrong"),
    ("DOC009", "documents", "d_size"), ("DOC010", "photos", "ph_dang"), ("DOC011", "photos", "ph_c1"), ("DOC012", "documents", "d_numeric"),
    ("DOC013", "documents", "d_hash"),
    ("PLT001", "plati", "p_none"), ("PLT002", "plati", "p_dl"), ("PLT003", "plati", "p_dc"), ("PLT004", "plati", "p_mis"), ("PLT005", "plati", "p_zero"),
    ("PLT006", "plati", "p_nan"), ("PLT007", "plati", "p_neg"), ("PLT008", "plati", "p_date"), ("PLT008", "plati", "p_fut"), ("PLT009", "plati", "p_d1"),
    ("PLT010", "plati", "p_r1"), ("DAT001", "plati", "p_iso"), ("SYN002", "plati", "p_d1"),
]


@pytest.mark.parametrize("code,entity,key", EXPECTED, ids=[f"{c}-{k}" for c, _e, k in EXPECTED])
def test_defect_is_detected_on_the_right_record(bad_report, code, entity, key):
    rep, R = bad_report
    assert has(rep, code, entity, R[key]), [(i.code, i.entity, i.record_id) for i in rep.issues if i.code == code]


def test_every_catalog_code_is_exercised(bad_report):
    rep, _ = bad_report
    seen = {i.code for i in rep.issues}
    never = set(model.CATALOG) - seen - {"META001", "META002"}      # META* sunt testate separat (schemă veche / plafon)
    assert not never, f"coduri neexercitate de testul cu defecte: {sorted(never)}"


def test_legitimate_cases_are_not_flagged(bad_report):
    rep, R = bad_report
    assert not has(rep, "CON004", "concesionari", R["c1"]) and not has(rep, "CON005", "concesionari", R["c1"])
    assert not has(rep, "CON004", "concesionari", R["h1"]) and not has(rep, "CON006", "concesionari", R["h1"])    # același nume, CNP-uri diferite: doar informativ
    assert has(rep, "CON005", "concesionari", R["h1"]) and next(i for i in rep.issues if i.code == "CON005" and i.record_id == str(R["h1"])).severity == "info"
    assert not any(i.entity == "contracte" and i.record_id == str(R["k_ok"]) and i.severity in ("critical", "error") for i in rep.issues)
    assert not has(rep, "CTR001", "contracte", R["k_own"])                                   # titular doar în text = avertisment (CTR003), nu eroare
    assert next(i for i in rep.issues if i.code == "CTR003").severity == "warning"
    assert not any(i.code.startswith("INH") and i.record_id == str(R["i_ok"]) and i.entity == "inhumati" for i in rep.issues)  # înhumat fără contract = relație opțională
    assert not has(rep, "LOC010", "locuri_de_veci", R["cap_free"]) and not has(rep, "LOC010", "locuri_de_veci", R["disp_occ"])   # capacitate liberă / stare 'Ocupat' = legitim
    assert not has(rep, "LOC005", "locuri_de_veci", R["nopoly"]) and not has(rep, "LOC006", "locuri_de_veci", R["nopoly"])   # loc fără poligon e valid
    assert not any(i.entity == "amenajari_avize" and i.record_id == str(R["a_ok"]) for i in rep.issues)


def test_severities_follow_the_catalog_and_critical_means_broken_reference(bad_report):
    rep, R = bad_report
    sev = {(i.code, i.record_id): i.severity for i in rep.issues}
    assert sev[("CTR006", str(R["k_idnull_bad"]))] == "critical" and sev[("CTR006", str(R["k_idnull"]))] == "error"
    assert sev[("LOC001", str(R["nocode"]))] == "critical" and sev[("INH002", str(R["i_dl"]))] == "critical"
    assert sev[("AVZ001", str(R["a_dang"]))] == "critical" and sev[("PLT003", str(R["p_dc"]))] == "critical"
    assert all(i.severity in model.SEVERITIES and i.code in model.CATALOG for i in rep.issues)


def test_each_issue_carries_all_required_fields(bad_report):
    rep, _ = bad_report
    for d in rep.to_dict()["issues"]:
        for k in ("code", "severity", "entity", "record_id", "description", "evidence", "recommendation", "auto_fixable_future"):
            assert k in d
        assert d["description"] and d["recommendation"] and isinstance(d["evidence"], dict) and isinstance(d["auto_fixable_future"], bool)
    s = rep.summary()
    assert s["total"] == sum(s["by_severity"].values()) == len(rep.issues)
    assert rep.meta["check_errors"] == []


# ----------------------------------------------------------------------------- strict citire
def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_database_file_is_byte_identical_after_run_and_runs_are_idempotent(bad_db):
    path, _ = bad_db
    before, mtime = sha(path), os.stat(path).st_mtime_ns
    r1, r2 = run(path), run(path)
    assert sha(path) == before and os.stat(path).st_mtime_ns == mtime
    assert r1.meta["db_unchanged"] and r2.meta["db_unchanged"]
    key = lambda rep: [(i.code, i.severity, i.entity, i.record_id, i.description, json.dumps(i.evidence, sort_keys=True)) for i in rep.issues]  # noqa: E731
    assert key(r1) == key(r2) and r1.summary() == r2.summary()
    assert sorted(p.name for p in Path(path).parent.iterdir() if p.name.startswith("t.db")) == ["t.db"]      # nicio fișier -wal/-shm creat


def test_wal_database_is_read_without_modification(tmp_path):
    path = new_db(tmp_path, "w.db")
    c = sqlite3.connect(path)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("INSERT INTO locuri_de_veci (cod_loc, uid) VALUES ('W-1','w1')")
    c.commit()
    assert Path(path + "-wal").exists()
    snap = lambda: {p.name: sha(p) for p in tmp_path.glob("w.db*") if not p.name.endswith("-shm")}  # noqa: E731
    before = snap()
    rep = run(path)
    assert rep.meta["row_counts"]["locuri_de_veci"] == 1                       # a văzut și datele care sunt doar în jurnalul WAL
    assert snap() == before
    c.close()


def test_connection_refuses_every_kind_of_write(bad_db):
    path, _ = bad_db
    conn = ci.open_readonly(path)
    for stmt in ("INSERT INTO plati (suma) VALUES (1)", "UPDATE plati SET suma=0", "DELETE FROM plati", "DROP TABLE plati", "ALTER TABLE plati ADD COLUMN x TEXT",
                 "CREATE TABLE t (a)", "PRAGMA user_version=9", "PRAGMA journal_mode=DELETE", "VACUUM", "ATTACH DATABASE ':memory:' AS m", "CREATE TEMP TABLE z (a)"):
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(stmt)
    assert conn.execute("SELECT COUNT(*) FROM plati").fetchone()[0] > 0
    conn.close()


def test_borrowed_writable_connection_cannot_be_written_during_run_and_is_restored(bad_db, monkeypatch):
    path, _ = bad_db
    before = sha(path)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    orig = checks_records.check_financiar

    def evil(c):
        with pytest.raises(sqlite3.DatabaseError):
            c.conn.execute("DELETE FROM plati")
        with pytest.raises(sqlite3.DatabaseError):
            c.conn.execute("UPDATE locuri_de_veci SET cod_loc='X'")
        orig(c)

    monkeypatch.setattr(ci.runner, "STEPS", [(a, b, evil if a == "financiar" else f) for a, b, f in ci.STEPS])
    rep = ci.run_checks(conn, today=TODAY, categories=["financiar"])
    assert rep.meta["check_errors"] == []
    conn.execute("UPDATE plati SET suma=suma")          # autorizatorul a fost scos la final
    conn.close()
    assert sha(path) == before


def test_missing_database_is_not_created(tmp_path):
    with pytest.raises(FileNotFoundError):
        ci.run_on_file(str(tmp_path / "nu_exista.db"))
    assert not (tmp_path / "nu_exista.db").exists()


def test_no_write_sql_anywhere_in_the_package():
    src = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "shared" / "cimitir_integrity").glob("*.py"))
    for m in re.finditer(r"""(?:execute|executemany|executescript)\(\s*f?["']{1,3}\s*([A-Za-z]+)""", src):
        assert m.group(1).upper() in ("SELECT", "PRAGMA"), m.group(0)
    assert not re.search(r"(?m)^[^#\n]*[\"'](INSERT INTO|UPDATE \w+ SET|DELETE FROM|DROP |ALTER TABLE|CREATE TABLE)", src)


# ----------------------------------------------------------------------------- confidențialitate
def test_reports_never_contain_sensitive_values(bad_report, tmp_path):
    rep, _ = bad_report
    out = ci.write_all(rep, str(tmp_path / "out"))
    blob = "\n".join(Path(p).read_text(encoding="utf-8-sig") for p in out.values())
    for s in SENSITIVE:
        assert s not in blob, f"valoare sensibilă în raport: {s[:3]}***"
    for name in ("Popescu", "Ionescu", "Georgescu", "Dumitru"):
        assert name not in blob, f"nume de persoană în raport: {name}"
    assert not re.search(r"(?<!\d)\d{13}(?!\d)", blob)


def test_redaction_safety_net_masks_cnp_and_ci():
    assert "1800101221144" not in model.redact("cnp 1800101221144 ok") and "[CNP-omis]" in model.redact("1800101221144")
    assert "AB123456" not in model.redact("serie AB123456") and "[CI-omis]" in model.redact("AB 123456")
    assert model.redact_obj({"a": ["1800101221144"]}) == {"a": ["[CNP-omis]"]}


# ----------------------------------------------------------------------------- export, plafon, schemă veche
def test_exports_are_valid_and_csv_is_injection_safe(bad_report, tmp_path):
    rep, _ = bad_report
    paths = ci.write_all(rep, str(tmp_path / "o"))
    d = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert d["summary"]["total"] == len(d["issues"]) and d["meta"]["db_unchanged"] is True
    import csv
    rows = list(csv.DictReader(Path(paths["csv"]).read_text(encoding="utf-8-sig").splitlines()))
    assert len(rows) == len(d["issues"]) and {"code", "severity", "entity", "record_id", "recommendation"} <= set(rows[0])
    from cimitir_integrity import report
    assert report._cell("=HYPERLINK(1)").startswith("'") and report._cell("@x").startswith("'") and report._cell("ok") == "ok"
    assert "Raport verificare evidențe" in Path(paths["md"]).read_text(encoding="utf-8")


def test_per_check_cap_truncates_listing_but_not_the_summary(bad_db):
    path, _ = bad_db
    full, capped = run(path), run(path, max_per_code=2)
    assert capped.summary()["total"] == full.summary()["total"] + sum(1 for i in capped.issues if i.code == "META002")
    assert sum(1 for i in capped.issues if i.code == "CTR006") == 2 and any(i.code == "META002" for i in capped.issues)
    assert capped.summary()["by_code"]["CTR006"] == full.summary()["by_code"]["CTR006"] == 3


def test_include_info_false_hides_only_info(bad_db):
    path, _ = bad_db
    rep = run(path, include_info=False)
    assert not any(i.severity == "info" for i in rep.issues) and any(i.severity == "warning" for i in rep.issues)


def test_old_schema_without_optional_tables_columns_does_not_crash(tmp_path):
    path = str(tmp_path / "old.db")
    c = sqlite3.connect(path)
    c.executescript("CREATE TABLE locuri_de_veci (id INTEGER PRIMARY KEY, cod_loc TEXT); INSERT INTO locuri_de_veci (cod_loc) VALUES ('A-1');"
                    "CREATE TABLE amenajari_avize (id INTEGER PRIMARY KEY, cod_loc TEXT NOT NULL, nr_aviz TEXT NOT NULL, data_aviz TEXT);"
                    "INSERT INTO amenajari_avize (cod_loc, nr_aviz) VALUES ('A-1','1');")
    c.close()
    rep = run(path)
    assert rep.meta["check_errors"] == [] and any(i.code == "META001" for i in rep.issues)
    assert not any(i.entity == "amenajari_avize" and i.severity in ("critical", "error") for i in rep.issues)


def test_empty_database_is_ok(tmp_path):
    path = new_db(tmp_path, "e.db")
    rep = run(path)
    assert rep.meta["check_errors"] == [] and rep.summary()["by_severity"]["critical"] == 0


def test_progress_and_cancel_hooks(clean_db):
    seen = []
    rep = ci.run_on_file(clean_db, today=TODAY, progress=lambda i, n, label: seen.append((i, n, label)))
    assert seen[0][0] == 0 and seen[-1][0] == seen[-1][1] == len(ci.STEPS)
    rep2 = ci.run_on_file(clean_db, today=TODAY, cancel=lambda: True)
    assert "Anulat de utilizator" in rep2.meta["check_errors"] and rep.meta["check_errors"] == []


# ----------------------------------------------------------------------------- coerență cu restul proiectului
def test_constants_match_the_sync_layer():
    import cimitir_entities as ents
    assert checks_records.AVIZ_STARI == tuple(ents.AVIZ_STARI)
    try:
        import cimitir_sync_v2 as sv
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"cimitir_sync_v2 indisponibil: {exc}")
    assert util.MAX_POINTS == sv.MAX_POINTS and util.MAX_TEXT == sv.MAX_TEXT


def test_every_code_used_in_source_exists_in_catalog():
    src = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "shared" / "cimitir_integrity").glob("checks_*.py"))
    used = set(re.findall(r'"([A-Z]{3,4}\d{3})"', src))
    assert used <= set(model.CATALOG), sorted(used - set(model.CATALOG))


def test_cnp_validation_rules():
    assert util.cnp_info(CNP_A).ok and util.cnp_info(CNP_A).sex == "M" and util.cnp_info(CNP_A).birth == date(1980, 1, 1)
    assert util.cnp_info(CNP_B).sex == "F" and util.cnp_info(CNP_B[:12] + str((int(CNP_B[12]) + 1) % 10)).ok is False
    assert util.cnp_info("123").reason.startswith("format") and util.cnp_info("") is None and util.cnp_info("   ") is None
    assert util.cnp_info(make_cnp(5, 10, 2, 30)).ok is False      # 30 februarie


def test_date_parsing_and_name_normalization():
    assert util.parse_date("05.03.2026").ok and util.parse_date("2026-03-05").fmt == "iso" and util.parse_date("05032026").fmt == "compact"
    assert util.parse_date("31.02.2026").ok is False and util.parse_date("abc").fmt is None and util.parse_date("  ") is None
    assert util.norm_name("Ștefan", "Țâră") == util.norm_name("tara", "stefan ") == ("stefan", "tara")
    assert util.norm_name("Ion-Vasile", "Popescu") == util.norm_name("popescu", "ion vasile")


def test_polygon_helpers():
    assert util.parse_points("[]") == (None, "") and util.parse_points(None) == (None, "") and util.parse_points("{")[1]
    pts, _ = util.parse_points(SQUARE)
    assert abs(util.polygon_area2(pts)) == 200 and not util.self_intersects(pts)
    assert util.self_intersects([(0, 0), (10, 10), (10, 0), (0, 10)])
    assert util.parse_points('[{"lat":1,"lng":2},{"lat":2,"lng":2},{"lat":2,"lng":3}]')[0] is not None
    assert util.parse_points('[[1,"x"]]')[1]


# ----------------------------------------------------------------------------- CLI
def test_cli_exit_codes_and_outputs(bad_db, clean_db, tmp_path, capsys):
    sys.path.insert(0, str(ROOT / "tools"))
    import verificare_evidente as cli
    assert cli.main(["--db", bad_db[0], "--out", str(tmp_path / "cli")]) == 1
    assert (tmp_path / "cli" / "verificare_evidente.json").exists()
    assert cli.main(["--db", str(tmp_path / "lipsa.db")]) == 2
    assert cli.main(["--db", clean_db, "--only", "nu_exista"]) == 2
    assert cli.main(["--db", clean_db, "--only", "locuri,plati", "--no-info"]) == 0


def test_documentation_lists_every_check_code():
    doc = (ROOT / "docs" / "VERIFICARE_EVIDENTE.md").read_text(encoding="utf-8")
    missing = [c for c in model.CATALOG if f"| {c} |" not in doc]
    assert not missing, f"coduri nedocumentate: {missing}"
