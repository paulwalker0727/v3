"""
Modul de licențiere Cimitir Desktop — partea CLIENT.

Conține doar codul care se DISTRIBUIE alături de aplicație:
    validator          — verificare Ed25519 + HWID + load/save licență
    activation_dialog  — fereastra PySide6 de activare (upload fișier .lic)

Generatorul de licențe (care folosește cheia PRIVATĂ) este SEPARAT în
`tools/license_manager.py` — NU se distribuie clienților.

Utilizare din main.py:
    from license import ensure_valid_license
    ok, info = ensure_valid_license(app)
    if not ok:
        sys.exit(1)
"""
from .validator import (
    ensure_valid_license,
    verifica_licenta_curenta,
    verifica_fisier,
    salveaza_licenta,
    LICENSE_FILE,
    LICENSE_DIR,
    get_hwid,
    get_nume_pc,
    RezultatVerificare,
)

__all__ = [
    "ensure_valid_license",
    "verifica_licenta_curenta",
    "verifica_fisier",
    "salveaza_licenta",
    "LICENSE_FILE",
    "LICENSE_DIR",
    "get_hwid",
    "get_nume_pc",
    "RezultatVerificare",
]
