"""Regresii Mobile v2: UID-urile hex32 și diferența dintre bootstrap generic / registrul live."""
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))
from cimitir_mobile_registry import MobileRegistryService
import cimitir_cloud_control as cc

ORG = "11111111-1111-4111-8111-111111111111"
CEM = "22222222-2222-4222-8222-222222222222"


class FakeCP:
    def __init__(self, p):
        self.p = p

    def _c(self):
        c = sqlite3.connect(self.p)
        c.row_factory = sqlite3.Row
        c.create_function("jsonb_array_length", 1, lambda v: len(__import__("json").loads(v or "[]")))
        return c


class FakeInbox:
    def __init__(self, cp):
        self.cp = cp

    def authenticate(self, key):
        assert key == "test-binding"
        return {"organization_uid": ORG, "cemetery_uid": CEM, "device_type": "MOBILE", "binding_uid": "test"}


@pytest.fixture
def service(tmp_path):
    path = tmp_path / "db.sqlite"
    conn = sqlite3.connect(path)
    for table in ("locuri_de_veci", "concesionari", "contracte", "inhumati", "plati", "amenajari_avize", "sectoare"):
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, uid TEXT, organization_uid TEXT, cemetery_uid TEXT)")
    for table, sql in (
        ("locuri_de_veci", "cod_loc TEXT, points TEXT, sector TEXT, parcela TEXT, rand TEXT, numar TEXT, capacitate TEXT, disponibilitate TEXT, tip TEXT, stare TEXT, categorie TEXT, suprafata REAL, nr_vechi TEXT, numar_cf TEXT, ocupat INTEGER, observatii TEXT, are_inhumari INTEGER"),
        ("contracte", "id_loc INTEGER, numar_contract TEXT, data_contract TEXT, data_inceput TEXT, data_sfarsit TEXT, valabilitate TEXT, observatii TEXT, concesionar_id INTEGER"),
        ("concesionari", "nume TEXT, prenume TEXT, telefon TEXT, email TEXT, adresa TEXT"),
        ("inhumati", "id_loc INTEGER, nume TEXT, prenume TEXT, data_nasterii TEXT, data_deces TEXT, data_inhumare TEXT, data_deshumare TEXT, sex TEXT, observatii TEXT, nr_certificat_deces TEXT, nr_adeverinta_inhumare TEXT"),
        ("plati", "cod_loc TEXT, contract_id INTEGER, suma REAL, data_plata TEXT, tip TEXT, chitanta_nr TEXT, observatii TEXT"),
        ("amenajari_avize", "cod_loc TEXT, nr_aviz TEXT, data_aviz TEXT, lucrare TEXT"),
    ):
        for col in sql.split(", "):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
    conn.execute("CREATE TABLE cp_registry_row_data (entity TEXT, organization_uid TEXT, cemetery_uid TEXT, uid TEXT)")
    conn.commit()
    conn.close()
    return MobileRegistryService(FakeInbox(FakeCP(path)))


def test_bootstrap_imported_but_not_materialized(service):
    c = service.cp._c()
    c.execute("INSERT INTO cp_registry_row_data VALUES ('locuri_de_veci',?,?,?)", (ORG, CEM, 'source-uid'))
    c.commit()
    c.close()
    d = service.data_status('test-binding')
    assert d['state'] == 'imported_not_materialized'
    assert d['live']['graves'] == 0
    assert d['bootstrap_rows'] == 1
    assert 'uid' not in d['live']


def test_drawn_live_grave_with_hex32_uid_opens_without_conversion(service):
    raw = uuid.uuid4().hex  # exact formatul generate de MAIN/SQLite
    conn = service.cp._c()
    conn.execute("INSERT INTO locuri_de_veci (uid,organization_uid,cemetery_uid,cod_loc,points,ocupat,are_inhumari) "
                 "VALUES (?,?,?,?,?,0,0)", (raw, ORG, CEM, 'A-1', '[[0,0],[10,0],[0,10]]'))
    conn.commit()
    conn.close()
    assert service.data_status('test-binding')['state'] == 'live_data_available'
    assert service.grave_detail('test-binding', raw)['grave']['cod_loc'] == 'A-1'
    # Și UID-ul cu cratime oferit de o integrare diferită trebuie să găsească același rând TEXT.
    assert service.grave_detail('test-binding', str(uuid.UUID(raw)))['grave']['uid'] == raw


def test_invalid_grave_uid_rejected(service):
    with pytest.raises(cc.ScopeError):
        service.grave_detail('test-binding', 'not-an-id')


def test_tenant_counts_scoped_to_binding(service):
    c = service.cp._c()
    c.execute("INSERT INTO locuri_de_veci (uid,organization_uid,cemetery_uid,cod_loc,points) VALUES (?,?,?,?,?)",
              (uuid.uuid4().hex, ORG, str(uuid.uuid4()), 'OTHER', '[[0,0],[1,0],[0,1]]'))
    c.commit()
    c.close()
    assert service.data_status('test-binding')['live']['graves'] == 0
