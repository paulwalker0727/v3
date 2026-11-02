"""Redactarea datelor personale (CNP, act de identitate) înainte de scrierea în jurnalul de audit — F-02.

Se aplică NUMAI la scrierea noilor înregistrări de audit. Datele operaționale (concesionari, contracte, înhumați) nu sunt atinse,
iar jurnalul istoric nu este rescris. Nu este criptare: valorile sensibile sunt înlocuite cu `REDACTED`; cheia (câmpul modificat),
actorul, acțiunea, ținta și momentul rămân în jurnal.

Reguli:
  * după NUMELE câmpului (cnp, owner_cnp, bi_ci, owner_id_card_*, carte_identitate, serie_ci, ci …): valoarea nevidă devine REDACTED;
  * după CONȚINUT, în orice text: 13 cifre consecutive în formă de CNP (sex/secol 1-9) devin REDACTED_CNP; `cheie=valoare` / `"cheie": "valoare"`
    cu cheie sensibilă în mesaje de eroare sau text liber; seria/nr. CI precedate de „CI/BI/serie/carte de identitate" devin REDACTED_CI;
  * JSON (dict/listă sau text JSON) se parcurge recursiv, inclusiv valorile vechi/noi și JSON imbricat în text (ex. `extra_data`).
Limită cunoscută: o serie/nr. de CI fără nume de câmp sau cuvânt-cheie în text liber (ex. „XY123456” singur) nu poate fi deosebită de un număr de contract
și nu este redactată; cheile structurate sunt întotdeauna acoperite.
"""
from __future__ import annotations

import json
import re
from typing import Any

REDACTED = "[REDACTAT]"
REDACTED_CNP = "[CNP REDACTAT]"
REDACTED_CI = "[CI REDACTAT]"

_KEY_RX = re.compile(r"cnp|bi_ci|id_?card|carte_?identitate|act_?identitate|serie_?(?:nr|numar)|(?:^|[_\s.-])(?:ci|bi)(?:$|[_\s.-])", re.I)
_CNP_RX = re.compile(r"(?<!\d)[1-9]\d{12}(?!\d)")
_CI_CTX_RX = re.compile(r"(?i)\b((?:ci|bi|serie|seria|carte\s+de\s+identitate|act\s+de\s+identitate)\b[\s:=.-]*)([A-Za-z]{2}\s?-?\s?\d{6})\b")
_KV_RX = re.compile(r"""(?ix)
    ( ["']? [a-z0-9_.]*(?:cnp|bi_ci|id_?card|carte_?identitate|act_?identitate|serie_?(?:nr|numar))[a-z0-9_.]* ["']? \s* [:=] \s* )
    ( "[^"]*" | '[^']*' | \(?[^\s,;}\])]+\)? )""")
_DETAIL_KV_RX = re.compile(r"(?i)(\(\s*[a-z0-9_]*(?:cnp|bi_ci|id_?card)[a-z0-9_]*\s*\)\s*=\s*)\(([^)]*)\)")


def is_sensitive_key(key: Any) -> bool:
    return bool(_KEY_RX.search(str(key or "")))


def redact_text(text: Any) -> str:
    """Redactează modelele de CNP/CI din text liber (mesaje de eroare, detalii)."""
    s = "" if text is None else str(text)
    s = _DETAIL_KV_RX.sub(lambda m: m.group(1) + "(" + REDACTED + ")", s)       # psycopg: Key (cnp)=(1800…) already exists
    s = _KV_RX.sub(lambda m: m.group(1) + (f'"{REDACTED}"' if m.group(2)[:1] in "\"'" else REDACTED), s)
    s = _CI_CTX_RX.sub(lambda m: m.group(1) + REDACTED_CI, s)
    return _CNP_RX.sub(REDACTED_CNP, s)


def _is_json_text(s: str) -> bool:
    t = s.lstrip()
    return len(t) >= 2 and t[0] in "{[" and t[-1] in "}]"


def redact_value(key: Any, value: Any) -> Any:
    """Valoarea unui câmp numit `key`: sensibil după nume → REDACTED (golul rămâne gol); altfel recursiv / după conținut."""
    if value is None or value == "":
        return value
    if is_sensitive_key(key):
        return REDACTED
    if isinstance(value, dict):
        return {k: redact_value(k, v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(key, v) for v in value]
    if isinstance(value, str):
        if _is_json_text(value):
            try:
                return json.dumps(redact_value(key, json.loads(value)), ensure_ascii=False)
            except ValueError:
                pass
        return redact_text(value)
    return value


def redact_obj(obj: Any) -> Any:
    """Redactează un dict/listă de audit (valori vechi/noi, snapshot-uri) fără a-i modifica structura."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {k: redact_value(k, v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) if isinstance(v, (dict, list, tuple)) else redact_value("", v) for v in obj]
    return redact_value("", obj)


def redact_detail(detail: Any) -> str:
    """Detaliu text/JSON pentru jurnal: JSON → recursiv, altfel după conținut."""
    if detail is None:
        return ""
    if isinstance(detail, (dict, list, tuple)):
        return json.dumps(redact_obj(detail), ensure_ascii=False, default=str)
    s = str(detail)
    if _is_json_text(s):
        try:
            return json.dumps(redact_obj(json.loads(s)), ensure_ascii=False, default=str)
        except ValueError:
            pass
    return redact_text(s)
