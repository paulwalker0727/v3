import importlib.util
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('polygon_export', ROOT / 'scripts' / 'export_mobile_v2_test_polygon.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def test_single_polygon_export(tmp_path):
    db = tmp_path / 'main.sqlite'
    conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE locuri_de_veci(uid TEXT, points TEXT, cod_loc TEXT)')
    conn.execute('INSERT INTO locuri_de_veci VALUES (?,?,?)', ('a'*32, '[[10,20],[30,20],[30,40]]', 'T-1'))
    conn.commit(); conn.close()
    doc = mod.extract(db)
    assert doc['manifest']['count'] == 1
    assert doc['payload']['plots'][0]['points'][1] == [30,20]
    assert len(doc['manifest']['sha256']) == 64

def test_empty_polygon_fails(tmp_path):
    db = tmp_path / 'main.sqlite'
    conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE locuri_de_veci(uid TEXT, points TEXT)')
    conn.execute('INSERT INTO locuri_de_veci VALUES (?,?)', ('a'*32, '[]'))
    conn.commit(); conn.close()
    try:
        mod.extract(db)
    except ValueError as e:
        assert 'exact un poligon' in str(e)
    else:
        assert False

def test_invalid_nonfinite_geometry():
    try:
        mod.normalized_points([[0,0],[1,2],[float('nan'),1]])
    except ValueError:
        pass
    else:
        assert False
