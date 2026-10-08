"""Specificația comună a entităților sincronizate MAIN (SQLite) <-> server (PostgreSQL) — Etapa 6.

Folosită și de desktop (`cloud_sync.py`) și de server (`entity_sync.py`), ca formatul pe fir să fie identic.

Pe fir (JSON), o entitate este:
    {"uid": "...", "version": N, "updated_at": "...", "data": {<coloane>, "<fk>_uid": "<uid părinte>"|null}}
Cheile străine locale/serverului (id numeric) NU circulă niciodată: se traduc în uid-ul părintelui.

Tipuri de coloane: t=text (implicit), f=float, i=int, j=JSON (listă/dict, în SQLite text JSON, în PostgreSQL JSONB).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

# Ordinea contează: părinții înaintea copiilor (la descărcare și la trimitere).
ORDER: List[str] = ["locuri_de_veci", "concesionari", "contracte", "inhumati", "plati", "amenajari_avize"]

# coloane de date (fără id/uid/version/updated_at/FK)
ENTITIES: Dict[str, Dict[str, Any]] = {
    "locuri_de_veci": {
        "cols": {"cod_loc": "t", "points": "j", "color": "t", "sector": "t", "parcela": "t", "rand": "t", "numar": "t",
                 "capacitate": "t", "disponibilitate": "t", "tip": "t", "stare": "t", "categorie": "t", "suprafata": "f",
                 "nr_vechi": "t", "numar_cf": "t", "ocupat": "i", "observatii": "t", "extra_data": "t",
                 "are_inhumari": "i"},
        "fks": {},
        "natural_key": "cod_loc",
    },
    "concesionari": {
        "cols": {"nume": "t", "prenume": "t", "cnp": "t", "bi_ci": "t", "adresa": "t", "telefon": "t", "email": "t",
                 "observatii": "t"},
        "fks": {},
    },
    "contracte": {
        "cols": {"numar_contract": "t", "data_contract": "t", "data_inceput": "t", "data_sfarsit": "t", "cod_loc": "t",
                 "valabilitate": "t", "observatii": "t", "extra_data": "t", "owner_fullname": "t",
                 "owner_fullname_2": "t", "owner_address": "t", "owner_id_card_serie_number": "t", "owner_cnp": "t",
                 "owner_phone": "t", "owner_email": "t", "cemetery_parcel": "t", "cemetery_row": "t",
                 "cemetery_tomb": "t", "tomb_surface_m2": "f", "sector": "t"},
        "fks": {"concesionar_id": ("concesionari", "concesionar_uid"), "id_loc": ("locuri_de_veci", "loc_uid")},
    },
    "inhumati": {
        "cols": {"nume": "t", "prenume": "t", "cnp": "t", "data_nasterii": "t", "data_deces": "t",
                 "data_inhumare": "t", "data_deshumare": "t", "sex": "t", "cauza_deces": "t", "profesie": "t",
                 "religie": "t", "ultima_adresa": "t", "observatii": "t", "nr_certificat_deces": "t",
                 "nr_adeverinta_inhumare": "t"},
        "fks": {"id_loc": ("locuri_de_veci", "loc_uid"), "contract_id": ("contracte", "contract_uid")},
    },
    "plati": {
        "cols": {"cod_loc": "t", "suma": "f", "data_plata": "t", "tip": "t", "chitanta_nr": "t", "observatii": "t",
                 "created_at": "t"},
        "fks": {"contract_id": ("contracte", "contract_uid")},
    },
    # Pasul 6: avizele de construcție/amenajare se sincronizează ca restul entităților (altfel serverul nu le-ar avea
    # și telefonul n-ar putea atașa documente la un aviz). Legătura cu locul: coloana text `cod_loc` (ca la plăți).
    "amenajari_avize": {
        "cols": {"cod_loc": "t", "nr_aviz": "t", "data_aviz": "t", "lucrare": "t", "created_at": "t"},
        "fks": {},
    },
}

VALID_ACTIONS = ("upsert", "delete")


def spec(entity: str) -> Dict[str, Any]:
    if entity not in ENTITIES:
        raise KeyError(entity)
    return ENTITIES[entity]


def fk_wire_names(entity: str) -> List[str]:
    return [wire for (_t, wire) in ENTITIES[entity]["fks"].values()]


def coerce(kind: str, value: Any) -> Any:
    """Normalizează o valoare la tipul coloanei ('' / None -> None pentru numerice)."""
    if kind in ("f", "i"):
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        try:
            number = float(str(value).replace(",", ".")) if isinstance(value, str) else float(value)
        except (TypeError, ValueError):
            return None
        return int(number) if kind == "i" else number
    if kind == "j":
        if value is None or value == "":
            return []
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                return []
        return value
    return None if value is None else str(value)


def clean_data(entity: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Doar coloanele cunoscute + FK-urile (uid), cu tipurile normalizate. Orice alt câmp este ignorat."""
    sp = ENTITIES[entity]
    out: Dict[str, Any] = {col: coerce(kind, data.get(col)) for col, kind in sp["cols"].items()}
    for wire in fk_wire_names(entity):
        value = data.get(wire)
        out[wire] = str(value) if value else None
    return out


def _norm(kind: str, value: Any) -> Any:
    if kind == "j":
        return json.dumps(value if value is not None else [], sort_keys=True, separators=(",", ":"))
    if kind == "f":
        return None if value is None else round(float(value), 6)
    if kind == "i":
        return None if value is None else int(value)
    return "" if value is None else str(value)


def same_data(entity: str, a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Compară două seturi de date (normalizat: None == '' la text, JSON canonic, float rotunjit)."""
    sp = ENTITIES[entity]
    for col, kind in sp["cols"].items():
        if _norm(kind, coerce(kind, a.get(col))) != _norm(kind, coerce(kind, b.get(col))):
            return False
    for wire in fk_wire_names(entity):
        if (a.get(wire) or None) != (b.get(wire) or None):
            return False
    return True


def changed_fields(entity: str, a: Dict[str, Any], b: Dict[str, Any]) -> List[str]:
    sp = ENTITIES[entity]
    out = [c for c, k in sp["cols"].items() if _norm(k, coerce(k, a.get(c))) != _norm(k, coerce(k, b.get(c)))]
    out += [w for w in fk_wire_names(entity) if (a.get(w) or None) != (b.get(w) or None)]
    return out
