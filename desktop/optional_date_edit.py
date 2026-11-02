"""Câmp de dată care poate fi „fără dată” și care nu pierde valorile existente (F-01).

`value_str()` întoarce: textul original dacă ziua nu a fost schimbată de utilizator (nu rescriem istoricul pentru format),
`zz.ll.aaaa` dacă utilizatorul a ales o zi, și textul nerecunoscut neschimbat; niciodată data curentă sau sentinelul.
"""
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QDateEdit

import cimitir_dates as dates

BLANK_DATE = QDate(1800, 1, 1)  # valoare internă „fără dată”; NU se salvează niciodată


class OptionalDateEdit(QDateEdit):
    """QDateEdit care poate reprezenta „fără dată”. `value_str()` întoarce "" când nu există dată (niciodată sentinelul)."""

    def __init__(self):
        super().__init__()
        self.setDisplayFormat("dd.MM.yyyy")
        self.setCalendarPopup(True)
        self.setMinimumDate(BLANK_DATE)
        self.setSpecialValueText("fără dată")
        self._raw = ""
        self._orig = ""
        self.set_blank()
        self.dateChanged.connect(self._on_user_change)

    def _on_user_change(self, _d):
        self._raw = ""
        self.setSpecialValueText("fără dată")

    def is_blank(self) -> bool:
        return self.date() == BLANK_DATE

    def set_blank(self):
        self._raw = ""
        self._orig = ""
        self.setSpecialValueText("fără dată")
        self.setDate(BLANK_DATE)

    def set_from_text(self, value):
        """Setează din textul din DB (zz.ll.aaaa, aaaa-ll-zz, zzllaaaa); un text nerecunoscut e păstrat neschimbat (nu se pierde la Save)."""
        value = str(value or "").strip()
        if not value:
            self.set_blank()
            return
        parsed = dates.parse_date(value)
        if parsed is not None and parsed.year > BLANK_DATE.year():
            self.setDate(QDate(parsed.year, parsed.month, parsed.day))
            self._raw = ""
            self._orig = value
            return
        self.set_blank()
        self._raw = value
        self.setSpecialValueText(value)

    def value_str(self) -> str:
        if self.is_blank():
            return self._raw
        if self._orig and dates.same_day(self._orig, self.date().toString("yyyy-MM-dd")):
            return self._orig
        return self.date().toString("dd.MM.yyyy")

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and (event.modifiers() & Qt.ControlModifier or self.is_blank()):
            self.set_blank()
            return
        super().keyPressEvent(event)
