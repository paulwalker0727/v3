"""Paragraph tolerant la text introdus de utilizatori (nume, observații) care conține markup invalid (ex. «<b>x»).

reportlab interpretează Paragraph ca mini-XML; un tag neînchis oprește generarea documentului. La eroare de parsare textul este
reparat fără a pierde informație: tagurile de formatare folosite de aplicație (<b>, <i>, <br/>...) care sunt echilibrate rămân, iar orice
`<`, `>`, `&` sau tag nepereche/necunoscut devine text literal vizibil. Ultima soluție: tot textul escapat.
"""
import re
from xml.sax.saxutils import escape

from reportlab.platypus import Paragraph as _Paragraph

_OK_TAGS = ("b", "i", "u", "strong", "em", "br", "font", "sub", "super", "strike", "a", "para")
_TOKEN = re.compile(r"(</?(?:%s)(?:\s[^<>]*)?/?>)" % "|".join(_OK_TAGS), re.I)
_ENTITY = re.compile(r"&(?:amp|lt|gt|quot|apos|nbsp|#\d+|#x[0-9a-fA-F]+);")
_TAG_NAME = re.compile(r"</?([A-Za-z]+)")


def _esc_text(txt: str) -> str:
    out, last = [], 0
    for m in _ENTITY.finditer(txt):
        out.append(escape(txt[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(escape(txt[last:]))
    return "".join(out)


def repair_markup(text) -> str:
    """Păstrează tagurile echilibrate din lista permisă; restul (inclusiv `<`, `>`, `&` izolate) devine text literal."""
    parts = _TOKEN.split(str(text))
    tags = {}
    stack = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            continue
        name = _TAG_NAME.match(part).group(1).lower()
        if part.endswith("/>") or name == "br":
            tags[i] = True
        elif part.startswith("</"):
            if stack and stack[-1][1] == name:
                tags[i] = True
                tags[stack.pop()[0]] = True
        else:
            stack.append((i, name))
    out = []
    for i, part in enumerate(parts):
        out.append(_esc_text(part) if i % 2 == 0 or not tags.get(i) else part)
    return "".join(out)


class Paragraph(_Paragraph):
    """Paragraph reportlab care nu mai oprește documentul la markup invalid din text introdus de utilizator."""

    def __init__(self, text, style=None, *args, **kwargs):
        try:
            super().__init__(text, style, *args, **kwargs)
            return
        except ValueError:
            pass
        try:
            super().__init__(repair_markup(text), style, *args, **kwargs)
        except ValueError:
            super().__init__(escape(str(text)), style, *args, **kwargs)
