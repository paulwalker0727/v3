"""Adaptor: autentificarea și drepturile sunt în shared/cimitir_auth.py (unic pentru desktop și backend)."""
from __future__ import annotations

import sys
from pathlib import Path

_SHARED = Path(__file__).resolve().parents[1] / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

import cimitir_auth as _shared  # noqa: E402

sys.modules[__name__] = _shared
