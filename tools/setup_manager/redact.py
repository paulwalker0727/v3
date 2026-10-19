"""Mascare centralizată a secretelor: orice text care iese din Setup Manager (UI, loguri, rapoarte) trece prin `redact`."""
from __future__ import annotations

import re
import threading
from typing import Any
from urllib.parse import quote

MASK = "***"
_lock = threading.Lock()
_secrets: set = set()

_PATTERNS = (
    (re.compile(r"(postgres(?:ql)?(?:\+\w+)?://[^:/@\s]+:)[^@\s]+@", re.I), r"\1" + MASK + "@"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"), MASK),
    (re.compile(r"\bsb_(?:secret|publishable)_[A-Za-z0-9_-]+"), MASK),
    (re.compile(r"(?i)\b(password|passwd|pwd|parol[aă]|cheie|secret|token|api[_-]?key|authorization)([\"']?\s*[=:]\s*[\"']?(?:Bearer\s+)?)([^\s\"'&;,]+)"), r"\1\2" + MASK),
)


def register(value: str) -> None:
    """Înregistrează o valoare secretă (și forma URL-encoded) pentru mascare exactă."""
    v = (value or "").strip()
    if len(v) < 4:
        return
    with _lock:
        _secrets.add(v)
        _secrets.add(quote(v, safe=""))


def clear() -> None:
    with _lock:
        _secrets.clear()


def redact(text: Any) -> str:
    s = "" if text is None else str(text)
    with _lock:
        known = sorted(_secrets, key=len, reverse=True)
    for v in known:
        s = s.replace(v, MASK)
    for rx, rep in _PATTERNS:
        s = rx.sub(rep, s)
    return s


def redact_obj(o: Any) -> Any:
    if isinstance(o, str):
        return redact(o)
    if isinstance(o, dict):
        return {k: redact_obj(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [redact_obj(v) for v in o]
    return o
