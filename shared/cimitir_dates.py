"""Conversii de dată pentru MAIN (zz.ll.aaaa), Cloud și Mobile (aaaa-ll-zz) — F-01.

Reguli (comune tuturor apelanților):
  * Formatul canonic de transport/sincronizare este ISO `YYYY-MM-DD`; MAIN își păstrează afișarea `zz.ll.aaaa`.
  * O valoare goală rămâne goală; o valoare pe care nu o putem interpreta rămâne EXACT ca atare. Nu se înlocuiește niciodată
    cu data curentă și nu se golește.
  * Conversia modifică doar forma textului, niciodată ziua calendaristică. Valorile cu oră (`2026-03-05 10:00`) nu sunt
    convertite (s-ar pierde ora); sunt doar interpretabile pentru comparații.
Modul pur (fără baze de date), identic pentru desktop, backend și teste.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional

_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_ISO_TIME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?$")
_DMY = re.compile(r"^(\d{1,2})([./-])(\d{1,2})\2(\d{4})$")
_COMPACT = re.compile(r"^(\d{2})(\d{2})(\d{4})$")


def _mk(y: str, m: str, d: str) -> Optional[date]:
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def parse_date(value: Any, allow_time: bool = False) -> Optional[date]:
    """Ziua calendaristică reprezentată de `value`, sau None dacă e goală / neinterpretabilă / inexistentă (ex. 31.02.2026)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date() if allow_time else None
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    m = _ISO.match(s)
    if m:
        return _mk(m.group(1), m.group(2), m.group(3))
    m = _DMY.match(s)
    if m:
        return _mk(m.group(4), m.group(3), m.group(1))
    m = _COMPACT.match(s)
    if m:
        return _mk(m.group(3), m.group(2), m.group(1))
    if allow_time:
        m = _ISO_TIME.match(s)
        if m:
            return _mk(m.group(1), m.group(2), m.group(3))
    return None


def is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def is_iso(value: Any) -> bool:
    return isinstance(value, str) and parse_date(value) is not None and bool(_ISO.match(value.strip()))


def to_iso(value: Any) -> Any:
    """`YYYY-MM-DD` dacă valoarea este o dată calendaristică validă; altfel valoarea NEMODIFICATĂ (goală, nerecunoscută, cu oră)."""
    d = parse_date(value)
    return d.isoformat() if d is not None else value


def to_ro(value: Any) -> Any:
    """`DD.MM.YYYY` dacă valoarea este o dată calendaristică validă; altfel valoarea NEMODIFICATĂ."""
    d = parse_date(value)
    return d.strftime("%d.%m.%Y") if d is not None else value


def same_day(a: Any, b: Any) -> bool:
    da, db = parse_date(a), parse_date(b)
    return da is not None and da == db


def for_local(existing_local: Any, incoming: Any) -> Any:
    """Valoarea de scris în MAIN pentru o dată sosită din Cloud.
    - aceeași zi ca ce există local → se păstrează textul local (nu rescriem istoricul doar pentru format);
    - dată validă → `zz.ll.aaaa` (formatul MAIN);
    - goală/neinterpretabilă → exact cum a sosit."""
    if parse_date(incoming) is None:
        return incoming
    if same_day(existing_local, incoming):
        return existing_local
    return to_ro(incoming)


def days_until(value: Any, today: Optional[date] = None) -> Optional[int]:
    """Zile până la `value` (negativ = trecut); None dacă data nu poate fi interpretată (nu se ghicește)."""
    d = parse_date(value, allow_time=True)
    if d is None:
        return None
    return (d - (today or date.today())).days
