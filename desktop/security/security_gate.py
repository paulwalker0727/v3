"""
security/security_gate.py — Punctul central chemat din main.py.

Flow la pornire:
    1. Verifică integritatea fișierelor (dacă ENFORCE_INTEGRITY=True)
    2. Încarcă / validează licența (cache local + validare online opțională)
    3. Dacă lipsește licența → afișează fereastra de activare (PySide6)
    4. Loghează evenimentul în audit
    5. Returnează payload-ul licenței spre aplicație

Utilizare în main.py:
    from security import run_security_gate, SecurityError
    try:
        license_info = run_security_gate()
    except SecurityError as e:
        show_error_and_exit(str(e))
"""
from typing import Any, Dict

from .integrity import check_integrity, IntegrityError
from .license_client import (
    LicenseError, ActivationRequired,
    activate_license, validate_license, get_current_license_info,
)
from . import audit_log


class SecurityError(Exception):
    """Ridicat când aplicația nu poate porni (integritate/licență/etc)."""


def _show_activation_dialog() -> Dict[str, Any]:
    """
    Afișează fereastra de activare (Qt). Returnează payload-ul licenței la succes.
    Ridică SecurityError dacă utilizatorul închide fereastra fără activare.

    Importul de PySide6 e late — modulul poate rula și fără Qt (test unit).
    """
    try:
        from PySide6.QtWidgets import QApplication, QDialog
        import sys
    except ImportError:
        raise SecurityError("PySide6 nu este instalat. Nu pot afișa fereastra de activare.")

    from .activation_window import ActivationWindow

    # dacă nu există QApplication încă (rulare foarte devreme), o creăm
    app = QApplication.instance() or QApplication(sys.argv)
    dlg = ActivationWindow()
    if dlg.exec() == QDialog.Accepted and dlg.license_payload:
        return dlg.license_payload
    raise SecurityError("Activarea licenței a fost anulată de utilizator.")


def run_security_gate() -> Dict[str, Any]:
    """
    Pornește gate-ul de securitate. Returnează info despre licența curentă.
    Ridică SecurityError dacă ceva nu e ok.
    """
    # 1) Integritate
    try:
        check_integrity()
    except IntegrityError as e:
        audit_log.log("system", "integrity_failed", meta={"error": str(e)})
        raise SecurityError(f"Verificare integritate eșuată:\n\n{e}")

    # 2) Licență — încearcă validare cache
    try:
        payload = validate_license(check_online=True)
        audit_log.log("system", "license_valid", meta={"key": payload.get("license_key")})
        return get_current_license_info() or payload
    except ActivationRequired:
        # user trebuie să introducă o cheie
        info = _show_activation_dialog()
        audit_log.log("system", "license_activated", meta={"key": info.get("license_key")})
        return get_current_license_info() or info.get("payload", {})
    except LicenseError as e:
        audit_log.log("system", "license_invalid", meta={"error": str(e)})
        # oferim posibilitatea de re-activare
        try:
            info = _show_activation_dialog()
            audit_log.log("system", "license_reactivated", meta={"key": info.get("license_key")})
            return get_current_license_info() or info.get("payload", {})
        except SecurityError:
            raise SecurityError(f"Licența nu este validă:\n\n{e}")
