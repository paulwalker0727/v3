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


_gated: list = []          # [weakref(widget), right, dezactivat_de_gate]


def gate(widget, right: str) -> None:
    """Dezactivează un control dacă utilizatorul autentificat nu are dreptul (UI) și îl ține sincronizat la acordări/revocări.
    Verificarea reală rămâne în funcțiile care scriu: un buton rămas activ nu poate executa o operațiune interzisă."""
    import weakref
    _gated.append([weakref.ref(widget), right, False])
    _apply_gate(_gated[-1], _user is None or can(right))


def _apply_gate(entry, allowed: bool) -> None:
    w = entry[0]()
    if w is None:
        return
    try:
        if not allowed and w.isEnabled():
            w.setEnabled(False)
            w.setToolTip(f"Nu ai dreptul „{entry[1]}” (configurat de administrator).")
            entry[2] = True
        elif allowed and entry[2]:
            w.setEnabled(True)
            w.setToolTip("")
            entry[2] = False
    except RuntimeError:       # widget C++ distrus
        entry[0] = lambda: None


def refresh_gates() -> None:
    """Re-evaluează toate controalele gate-uite (drepturi acordate/retrase în sesiune deschisă)."""
    global _gated
    _gated = [e for e in _gated if e[0]() is not None]
    if _user is None or not _gated:
        return
    fresh = cimitir_auth.get_session_user(_user["id"], _user.get("cemetery_id") or None)
    rights = set(fresh["rights"]) if fresh else set()
    for e in _gated:
        _apply_gate(e, e[1] in rights)


def start_auto_refresh(interval_ms: int = 5000):
    from PySide6.QtCore import QTimer
    t = QTimer()
    t.setInterval(interval_ms)
    t.timeout.connect(refresh_gates)
    t.start()
    globals()["_timer"] = t
    return t


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
