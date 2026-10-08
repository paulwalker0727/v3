"""Verificare CNP duplicat între concesionari / inhumati înainte de salvare."""
from __future__ import annotations

from typing import Any, Optional

import database as db


def find_concesionar_by_cnp(cnp: str) -> Optional[dict[str, Any]]:
    if not cnp or len(cnp) < 6:
        return None
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT id, nume, prenume, cnp, bi_ci, adresa, telefon, email, observatii "
            "FROM concesionari WHERE cnp = ? LIMIT 1",
            (cnp,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def find_inhumat_by_cnp(cnp: str) -> Optional[dict[str, Any]]:
    if not cnp or len(cnp) < 6:
        return None
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT i.id, i.nume, i.prenume, i.cnp, i.data_deces, i.data_inhumare, "
            "l.cod_loc FROM inhumati i "
            "LEFT JOIN locuri_de_veci l ON i.id_loc = l.id "
            "WHERE i.cnp = ? LIMIT 1",
            (cnp,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_concesionar(concesionar_id: int, data: dict) -> bool:
    try:
        db.save_concesionar(data, concesionar_id=concesionar_id)
        return True
    except Exception as e:
        print(f"[update_concesionar] {e}")
        return False


def update_inhumat(inhumat_id: int, cod_loc: str, data: dict) -> bool:
    try:
        db.save_inhumat(cod_loc, data, inhumat_id=inhumat_id)
        return True
    except Exception as e:
        print(f"[update_inhumat] {e}")
        return False
