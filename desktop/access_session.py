"""Sesiunea de acces a operatorului MAIN: drepturile se re-verifică în DB la fiecare operațiune sensibilă.

Astfel revocările primite prin sincronizare și expirarea lease-ului offline se aplică și într-o sesiune deschisă.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import cimitir_auth

_user: Optional[Dict[str, Any]] = None
DENIED_MESSAGES = {
    "invalid": "Utilizator sau parolă greșită.",
    "locked": "Prea multe încercări eșuate. Contul este blocat 5 minute.",
    "inactive": "Contul este dezactivat. Contactează administratorul.",
    "lease_expired": "Accesul local a expirat (fără reconectare la server). Conectează-te la internet și sincronizează.",
    "wrong_cemetery": "Contul nu este atribuit acestui cimitir.",
}


def set_user(user: Optional[Dict[str, Any]]) -> None:
    global _user
    _user = user


def current_user() -> Optional[Dict[str, Any]]:
    return _user


def can(right: str) -> bool:
    return cimitir_auth.can(_user, right)


def can_edit_mode(user: Dict[str, Any]) -> bool:
    return cimitir_auth.RIGHT_EDIT in user.get("rights", [])


def require(right: str, parent=None, action: str = "această operațiune") -> bool:
    if can(right):
        return True
    try:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(parent, "Drepturi insuficiente",
                            f"Nu ai dreptul „{right}” pentru {action}, sau accesul a fost revocat/a expirat.")
    except Exception:
        pass
    return False
