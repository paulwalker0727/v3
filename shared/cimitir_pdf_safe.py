"""Paragraph tolerant la text introdus de utilizatori (nume, observații) care conține markup invalid (ex. «<b>x»).

reportlab interpretează Paragraph ca mini-XML; un tag neînchis oprește generarea contractului/chitanței. La eroare de parsare se
reîncearcă textul fără taguri, cu caracterele speciale escapate: documentul se tipărește mereu, iar textul utilizatorului rămâne vizibil.
"""
import re
from xml.sax.saxutils import escape

from reportlab.platypus import Paragraph as _Paragraph

_TAG = re.compile(r"</?[A-Za-z][^>]*>")


class Paragraph(_Paragraph):
    """Paragraph reportlab care nu mai oprește documentul la markup invalid din text introdus de utilizator."""

    def __init__(self, text, style=None, *args, **kwargs):
        try:
            super().__init__(text, style, *args, **kwargs)
        except ValueError:
            super().__init__(escape(_TAG.sub("", str(text))), style, *args, **kwargs)
