"""Scanare document din telefon: originalul se arhivează întotdeauna; AI-ul doar PROPUNE date, iar utilizatorul le confirmă.

Reutilizează infrastructura AI existentă (Gemini prin emergentintegrations, prompturile din MAIN) — `cimitir/ai_extract.py`.
Tipuri fără extracție (contract, chitanță, aviz): se arhivează doar fotografia, prin panoul de atașamente.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# kind -> (etichetă, doc_type în arhivă, titlu, {entity_type: [(câmp, etichetă)]})
KINDS: Dict[str, Dict[str, Any]] = {
    "buletin": {
        "label": "Carte de identitate", "doc_type": "carte_identitate", "title": "Carte de identitate (scanare)",
        "fields": {
            "concesionar": [("nume", "Nume"), ("prenume", "Prenume"), ("cnp", "CNP"), ("bi_ci", "Serie / nr. CI"), ("adresa", "Adresă")],
            "inhumat": [("nume", "Nume"), ("prenume", "Prenume"), ("cnp", "CNP"), ("data_nasterii", "Data nașterii"), ("sex", "Sex")],
        }},
    "adeverinta_adresa": {
        "label": "Adeverință de adresă", "doc_type": "alt_document", "title": "Adeverință de adresă (scanare)",
        "fields": {"concesionar": [("nume", "Nume"), ("prenume", "Prenume"), ("cnp", "CNP"), ("bi_ci", "Serie / nr. CI"), ("adresa", "Adresă")]}},
    "certificat_deces": {
        "label": "Certificat de deces", "doc_type": "certificat", "title": "Certificat de deces (scanare)",
        "fields": {"inhumat": [("nume", "Nume"), ("prenume", "Prenume"), ("cnp", "CNP"), ("data_nasterii", "Data nașterii"),
                                 ("data_deces", "Data decesului"), ("cauza_deces", "Cauza decesului"), ("sex", "Sex"),
                                 ("ultima_adresa", "Ultima adresă"), ("nr_certificat_deces", "Nr. certificat deces"),
                                 ("nr_adeverinta_inhumare", "Nr. adeverință înhumare")]}},
}
AUTO_DESCRIPTION = "Scanare AI pentru completarea datelor; propunerea a fost confirmată de operator înainte de salvare."
LOW_CONFIDENCE = 0.6
_DATE_FIELDS = {"data_nasterii", "data_deces"}


def kinds_for(etype: str) -> List[Tuple[str, str]]:
    return [(k, v["label"]) for k, v in KINDS.items() if etype in v["fields"]]


def allowed(kind: str, etype: str) -> bool:
    return kind in KINDS and etype in KINDS[kind]["fields"]


def _val(fields: Dict[str, Any], name: str) -> Tuple[str, float]:
    f = fields.get(name) or {}
    if not isinstance(f, dict):
        return str(f or "").strip(), 0.5
    try:
        conf = float(f.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    return str(f.get("value") or "").strip(), max(0.0, min(conf, 1.0))


def _extracted(kind: str, ext: Dict[str, Any], name: str) -> Tuple[str, float]:
    fields = ext.get("fields") or {}
    if name == "bi_ci":                       # serie + număr => "XX 123456"
        s, cs = _val(fields, "serie")
        n, cn = _val(fields, "numar")
        return (f"{s} {n}".strip(), min(cs or cn, cn or cs) if (s and n) else (cs or cn))
    return _val(fields, name)


def propose(kind: str, etype: str, current: Dict[str, Any], ext: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for name, label in KINDS[kind]["fields"][etype]:
        value, conf = _extracted(kind, ext, name)
        cur = str(current.get(name) or "").strip()
        rows.append({"field": name, "label": label, "current": cur, "value": value, "confidence": round(conf, 2),
                     "low": bool(value) and conf < LOW_CONFIDENCE, "differs": bool(value) and value != cur,
                     # preselectat doar dacă există valoare cu încredere suficientă și diferă de cea actuală
                     "checked": bool(value) and conf >= LOW_CONFIDENCE and value != cur})
    return rows


def validate_value(name: str, value: str) -> Optional[str]:
    """Mesaj de eroare sau None."""
    v = value.strip()
    if len(v) > 300:
        return f"{name}: valoare prea lungă."
    if name == "cnp" and v and not re.fullmatch(r"\d{13}", v):
        return "CNP-ul trebuie să aibă exact 13 cifre."
    if name in _DATE_FIELDS and v:
        try:
            datetime.strptime(v, "%d.%m.%Y")
        except ValueError:
            return f"{name}: data trebuie să fie în format zz.ll.aaaa."
    if name == "sex" and v and v.upper() not in ("M", "F"):
        return "Sex: M sau F."
    return None
