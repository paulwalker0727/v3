import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))
from cimitir_mobile_registry import validate_test_polygon_document


def document():
    payload = {"format": "cimitir-mobile-v2-polygon-diagnostic-v1", "source": "MAIN-local-test",
               "plots": [{"uid": "a" * 32, "cod_loc": "T-1", "points": [[0, 0], [1, 0], [0, 1]]}]}
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"manifest": {"format": payload["format"], "count": 1, "sha256": digest}, "payload": payload}


def test_valid():
    assert validate_test_polygon_document(document())["cod_loc"] == "T-1"


def test_hash_tampered():
    d = document(); d["payload"]["plots"][0]["cod_loc"] = "tampered"
    with pytest.raises(ValueError): validate_test_polygon_document(d)


def test_multiple_rejected():
    d = document(); d["payload"]["plots"].append(d["payload"]["plots"][0])
    with pytest.raises(ValueError): validate_test_polygon_document(d)


def test_invalid_coords():
    d = document(); d["payload"]["plots"][0]["points"] = [[0, 0], [True, 1], [0, 1]]
    payload = d["payload"]
    d["manifest"]["sha256"] = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with pytest.raises(ValueError): validate_test_polygon_document(d)
