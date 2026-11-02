"""Funcții pure de validare (fără acces la baza de date)."""
from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any, List, Optional, Tuple

MAX_POINTS = 500       # oglindește cimitir_sync_v2.MAX_POINTS (serverul respinge peste limită)
MAX_TEXT = 4000        # oglindește cimitir_sync_v2.MAX_TEXT

_DMY = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T].*)?$")
_COMPACT = re.compile(r"^(\d{2})(\d{2})(\d{4})$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$")


def blank(v: Any) -> bool:
    return v is None or str(v).strip() == ""


def txt(v: Any) -> str:
    return "" if v is None else str(v).strip()


@dataclass(frozen=True)
class PDate:
    raw: str
    fmt: Optional[str]          # 'dmy' | 'iso' | 'compact' | None (nerecunoscut)
    value: Optional[date]       # None dacă data calendaristică nu există

    @property
    def ok(self) -> bool:
        return self.value is not None


def parse_date(raw: Any) -> Optional[PDate]:
    """None dacă valoarea e goală. Formate acceptate de aplicație: zz.ll.aaaa, aaaa-ll-zz, zzllaaaa."""
    s = txt(raw)
    if not s:
        return None
    for rx, fmt, order in ((_DMY, "dmy", (3, 2, 1)), (_ISO, "iso", (1, 2, 3)), (_COMPACT, "compact", (3, 2, 1))):
        m = rx.match(s)
        if m:
            try:
                return PDate(s, fmt, date(int(m.group(order[0])), int(m.group(order[1])), int(m.group(order[2]))))
            except ValueError:
                return PDate(s, fmt, None)
    return PDate(s, None, None)


def norm_name(*parts: Any) -> Tuple[str, ...]:
    s = " ".join(txt(p) for p in parts)
    s = "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")
    return tuple(sorted(t for t in re.split(r"[\s\-,.]+", s.lower()) if t))


def norm_code(v: Any) -> str:
    return re.sub(r"[\s\-_./]+", "", txt(v)).lower()


def valid_email(v: Any) -> bool:
    return bool(_EMAIL.match(txt(v)))


def valid_uuid(v: Any) -> bool:
    return bool(_UUID.match(txt(v)))


# ----------------------------------------------------------------------------- CNP
@dataclass(frozen=True)
class CnpInfo:
    ok: bool
    reason: str = ""
    sex: Optional[str] = None          # 'M' | 'F' | None
    birth: Optional[date] = None


_W = (2, 7, 9, 1, 4, 6, 3, 5, 8, 2, 7, 9)


def cnp_info(raw: Any) -> Optional[CnpInfo]:
    """None dacă lipsește. Nu păstrează/loghează valoarea; întoarce doar proprietăți derivate."""
    s = txt(raw)
    if not s:
        return None
    d = re.sub(r"\s", "", s)
    if not d.isdigit() or len(d) != 13:
        return CnpInfo(False, "format (nu are 13 cifre)")
    n = [int(c) for c in d]
    ctrl = sum(a * b for a, b in zip(n[:12], _W)) % 11
    ctrl = 1 if ctrl == 10 else ctrl
    if ctrl != n[12]:
        return CnpInfo(False, "cifră de control")
    s1 = n[0]
    if s1 == 0:
        return CnpInfo(False, "prima cifră (sex/secol) invalidă")
    sex = "M" if s1 % 2 == 1 else "F"
    century = {1: 1900, 2: 1900, 3: 1800, 4: 1800, 5: 2000, 6: 2000}.get(s1)
    birth = None
    if century:
        try:
            birth = date(century + n[1] * 10 + n[2], n[3] * 10 + n[4], n[5] * 10 + n[6])
        except ValueError:
            return CnpInfo(False, "data nașterii din CNP", sex=sex)
    return CnpInfo(True, "", sex=sex if s1 != 9 else None, birth=birth)


def cnp_key(raw: Any) -> str:
    """Cheie de comparare (doar în memorie, nu apare în rapoarte)."""
    return re.sub(r"\s", "", txt(raw))


# ----------------------------------------------------------------------------- poligoane
def _pt(item: Any) -> Optional[Tuple[float, float]]:
    try:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            x, y = float(item[0]), float(item[1])
        elif isinstance(item, dict):
            xk = next(k for k in ("x", "lng", "lon", "lonE") if k in item)
            yk = next(k for k in ("y", "lat") if k in item)
            x, y = float(item[xk]), float(item[yk])
        else:
            return None
    except (StopIteration, TypeError, ValueError):
        return None
    return (x, y)


def parse_points(raw: Any) -> Tuple[Optional[List[Tuple[float, float]]], str]:
    """(puncte, eroare). puncte=None și eroare goală = nu există poligon (valid: loc fără desen)."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, ""
    try:
        data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except (ValueError, TypeError):
        return None, "JSON invalid"
    if data is None or data == []:
        return None, ""
    if not isinstance(data, list):
        return None, "nu este listă de puncte"
    pts: List[Tuple[float, float]] = []
    for i, it in enumerate(data):
        p = _pt(it)
        if p is None:
            return None, f"punctul {i} nu este pereche numerică"
        if not (math.isfinite(p[0]) and math.isfinite(p[1])):
            return None, f"punctul {i} are coordonate nefinite"
        pts.append(p)
    return pts, ""


def polygon_area2(pts: List[Tuple[float, float]]) -> float:
    s = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        s += x1 * y2 - x2 * y1
    return s


def _orient(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def self_intersects(pts: List[Tuple[float, float]]) -> bool:
    n = len(pts)
    if n < 4:
        return False
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        for j in range(i + 1, n):
            if j == i or (j + 1) % n == i or (i + 1) % n == j:
                continue
            c, d = pts[j], pts[(j + 1) % n]
            o1, o2, o3, o4 = _orient(a, b, c), _orient(a, b, d), _orient(c, d, a), _orient(c, d, b)
            if o1 * o2 < 0 and o3 * o4 < 0:
                return True
    return False


def polygon_signature(pts: List[Tuple[float, float]]) -> Tuple:
    return tuple(sorted((round(x, 6), round(y, 6)) for x, y in pts))
