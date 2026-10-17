"""cimitir_qr.py — link/QR de asociere pentru telefon: `<adresa-cloud>/mobile/#pair=<cod>`.

Codul stă în fragmentul URL (după `#`): browserul nu îl trimite serverului și nu apare în jurnale de acces; PWA îl scoate din bara de adrese la prima citire.
"""
from __future__ import annotations

import base64
import io
import re
from typing import Optional
from urllib.parse import urlparse

_CODE_RE = re.compile(r"^[A-Za-z0-9-]{10,40}$")


def pair_url(base_url: str, code: str) -> str:
    """URL-ul de asociere pentru QR; '' dacă adresa Cloud nu e un URL http(s) valid sau codul are formă neașteptată."""
    p = urlparse((base_url or "").strip())
    if p.scheme not in ("http", "https") or not p.hostname or p.username or not _CODE_RE.match(code or ""):
        return ""
    return f"{p.scheme}://{p.netloc}/mobile/#pair={code}"


def _qr(text: str):
    import segno
    return segno.make(text, error="m", micro=False)


def png_bytes(text: str, scale: int = 6) -> bytes:
    buf = io.BytesIO()
    _qr(text).save(buf, kind="png", scale=scale, border=3)
    return buf.getvalue()


def svg_data_uri(text: str) -> Optional[str]:
    """data:image/svg+xml;base64,… (None dacă generatorul QR nu e instalat)."""
    try:
        buf = io.BytesIO()
        _qr(text).save(buf, kind="svg", scale=6, border=3, xmldecl=False, svgns=True, nl=False)
    except ImportError:
        return None
    return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
