"""Sursă comună (MAIN + portal local) pentru valorile și reprezentarea datelor unui loc de veci.

Listele fixe și regula pentru „observații” sunt definite doar aici; desktop-ul (PySide) și portalul web le importă.
Observațiile canonice: `locuri_de_veci.extra_data["observatii"]`; coloana `observatii` este oglindită la scriere
(compatibilitate cu datele vechi / cu ecranul „Panou detalii”).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

LOC_FIELD_OPTIONS: Dict[str, tuple] = {
    "capacitate": ("Simplu", "Dublu", "Cvadrupul"),
    "disponibilitate": ("Ocupat", "Liber", "Posibil liber"),
    "tip": ("Cavou", "Cruce", "Împrejmuire", "Mormânt"),
    "stare": ("Foarte bună", "Bună", "Mai puțin bună"),
    "categorie": ("Categoria I", "Categoria II", "Categoria III"),
}
LOC_SELECT_FIELDS = tuple(LOC_FIELD_OPTIONS)


def options_for(field: str, current: Any = None) -> List[str]:
    """Opțiunile canonice (cu „” primul); o valoare istorică existentă e păstrată ca ultimă opțiune, nu e ștearsă."""
    opts = ["", *LOC_FIELD_OPTIONS[field]]
    cur = str(current).strip() if current is not None else ""
    if cur and cur not in opts:
        opts.append(cur)
    return opts


def is_allowed_value(field: str, value: Any, current: Any = None) -> bool:
    """Valoare acceptată la salvare: goală, canonică sau identică cu cea deja stocată (istorică)."""
    v = str(value).strip() if value is not None else ""
    if not v or v in LOC_FIELD_OPTIONS[field]:
        return True
    return v == (str(current).strip() if current is not None else "")


def loc_observatii(loc: Optional[Dict[str, Any]]) -> str:
    """Observațiile locului: extra_data.observatii dacă cheia există (chiar goală), altfel coloana veche."""
    loc = loc or {}
    extra = loc.get("extra_data")
    if isinstance(extra, dict) and "observatii" in extra:
        return str(extra.get("observatii") or "")
    return str(loc.get("observatii") or "")


def observatii_metadata(loc: Optional[Dict[str, Any]], text: str) -> Dict[str, Any]:
    """Metadate pentru save_loc_metadata care scriu observațiile în ambele locuri, păstrând restul din extra_data."""
    extra = dict((loc or {}).get("extra_data") or {}) if isinstance((loc or {}).get("extra_data"), dict) else {}
    text = (text or "").strip()
    extra["observatii"] = text
    return {"observatii": text, "extra_data": extra}
