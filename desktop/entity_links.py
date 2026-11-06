"""Legături Locuri de veci <-> Concesionari <-> Contracte (strat de date, fără UI).

Asocierea reală din schema SQLite există DOAR prin `contracte` (concesionar_id -> concesionari.id,
id_loc/cod_loc -> locuri_de_veci). Cardinalitate: un loc poate avea mai multe contracte (istoric) și mai mulți
concesionari; un concesionar poate avea mai multe contracte/locuri. Funcțiile de aici doar CITESC; nu scriu,
nu șterg și nu unifică persoane.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

import database as db


def _norm_cnp(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _norm_name(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def find_concesionar_candidates(data: Dict[str, Any], rows: Iterable[Dict[str, Any]],
                                exclude_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Persoane existente care ar putea fi aceeași persoană: CNP identic (nevid) sau același nume+prenume.
    Doar propune; decizia rămâne la utilizator (nu se unifică niciodată automat pe baza numelui)."""
    cnp = _norm_cnp(data.get("cnp"))
    nume, prenume = _norm_name(data.get("nume")), _norm_name(data.get("prenume"))
    out = []
    for r in rows or []:
        if exclude_id is not None and r.get("id") == exclude_id:
            continue
        by_cnp = bool(cnp) and _norm_cnp(r.get("cnp")) == cnp
        by_name = bool(nume or prenume) and _norm_name(r.get("nume")) == nume and _norm_name(r.get("prenume")) == prenume
        if by_cnp or by_name:
            out.append(r)
    return out


_CONTRACT_SQL = """
    SELECT c.id AS contract_id, c.numar_contract, c.data_contract, c.data_inceput, c.data_sfarsit,
           c.concesionar_id, c.id_loc, c.cod_loc AS contract_cod_loc,
           l.id AS loc_id, l.cod_loc AS cod_loc, l.sector, l.parcela, l.rand, l.numar,
           p.nume, p.prenume, p.cnp
    FROM contracte c
    LEFT JOIN locuri_de_veci l ON (l.id = c.id_loc OR (c.id_loc IS NULL AND l.cod_loc = c.cod_loc))
    LEFT JOIN concesionari p ON p.id = c.concesionar_id
"""
_ORDER = " ORDER BY COALESCE(c.data_inceput, ''), c.id"


def _rows(where: str, params: tuple) -> List[Dict[str, Any]]:
    conn = db.get_connection()
    try:
        return [dict(r) for r in conn.execute(_CONTRACT_SQL + where + _ORDER, params).fetchall()]
    finally:
        conn.close()


def contracts_for_loc(cod_loc: str) -> List[Dict[str, Any]]:
    """Istoricul complet al contractelor locului (toate, vechi și noi), după id real sau cod."""
    return _rows("WHERE l.cod_loc = ? OR c.cod_loc = ?", (cod_loc, cod_loc))


def contracts_for_concesionar(concesionar_id: int) -> List[Dict[str, Any]]:
    return _rows("WHERE c.concesionar_id = ?", (concesionar_id,))


def concesionari_for_loc(cod_loc: str) -> List[Dict[str, Any]]:
    """Concesionarii asociați locului (distincți după id real), cu numărul de contracte."""
    grouped: Dict[Any, Dict[str, Any]] = {}
    for r in contracts_for_loc(cod_loc):
        cid = r["concesionar_id"]
        if cid is None:
            continue
        g = grouped.setdefault(cid, {"id": cid, "nume": r["nume"], "prenume": r["prenume"], "cnp": r["cnp"], "contracte": 0})
        g["contracte"] += 1
    return list(grouped.values())


def locuri_for_concesionar(concesionar_id: int) -> List[Dict[str, Any]]:
    """Locurile asociate concesionarului (distincte), cu numărul de contracte."""
    grouped: Dict[Any, Dict[str, Any]] = {}
    for r in contracts_for_concesionar(concesionar_id):
        code = r["cod_loc"] or r["contract_cod_loc"]
        if not code:
            continue
        g = grouped.setdefault(code, {"cod_loc": code, "id": r["loc_id"], "sector": r["sector"], "parcela": r["parcela"],
                                      "rand": r["rand"], "numar": r["numar"], "contracte": 0})
        g["contracte"] += 1
    return list(grouped.values())


def loc_info_by_concesionar() -> Dict[int, Dict[str, str]]:
    """{concesionar_id: {cod_loc, sector, parcela, rand}} — valori distincte unite cu ', ' (pentru coloane/filtre)."""
    acc: Dict[int, Dict[str, List[str]]] = {}
    for r in _rows("WHERE c.concesionar_id IS NOT NULL", ()):
        slot = acc.setdefault(r["concesionar_id"], {"cod_loc": [], "sector": [], "parcela": [], "rand": []})
        for key, val in (("cod_loc", r["cod_loc"] or r["contract_cod_loc"]), ("sector", r["sector"]),
                         ("parcela", r["parcela"]), ("rand", r["rand"])):
            if val and str(val) not in slot[key]:
                slot[key].append(str(val))
    return {cid: {k: ", ".join(v) for k, v in d.items()} for cid, d in acc.items()}
