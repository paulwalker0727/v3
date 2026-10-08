"""
qr_public_data.py — Configurare & preluare a datelor publice pentru pagina QR.

Când cineva scanează QR-ul unui loc de veci, poate vedea DOAR câmpurile pe care
tu le-ai bifat în „Setări conținut QR" (câmpurile sensibile — CNP, adresă,
telefon — NU sunt niciodată expuse, indiferent de setări).

Setări persistate în DB.settings ca `qr_public_fields` (CSV cu chei).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import database as db
from app_logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Definirea câmpurilor DISPONIBILE (grupate pe secțiuni)
# ---------------------------------------------------------------------------
# Fiecare cheie are:
#   - label (etichetă afișată în UI + pe pagina publică)
#   - group (secțiune la care aparține)
#   - source (de unde vine data: "loc" / "concesionar" / "inhumati")
#   - safe (True = poate fi afișat public — False înseamnă că e blocat hard)
AVAILABLE_FIELDS: Dict[str, Dict[str, Any]] = {
    # ---- Loc de veci ----
    "cod_loc":     {"label": "Cod loc",         "group": "Localizare", "source": "loc", "safe": True},
    "sector":      {"label": "Sector",          "group": "Localizare", "source": "loc", "safe": True},
    "parcela":     {"label": "Parcelă",         "group": "Localizare", "source": "loc", "safe": True},
    "rand":        {"label": "Rând",            "group": "Localizare", "source": "loc", "safe": True},
    "numar":       {"label": "Număr",           "group": "Localizare", "source": "loc", "safe": True},
    "suprafata":   {"label": "Suprafață (m²)",  "group": "Detalii loc", "source": "loc", "safe": True},
    "capacitate":  {"label": "Capacitate",      "group": "Detalii loc", "source": "loc", "safe": True},
    "disponibilitate": {"label": "Disponibilitate", "group": "Detalii loc", "source": "loc", "safe": True},
    "tip":         {"label": "Tip loc",         "group": "Detalii loc", "source": "loc", "safe": True},
    "stare":       {"label": "Stare",           "group": "Detalii loc", "source": "loc", "safe": True},
    "categorie":   {"label": "Categorie",       "group": "Detalii loc", "source": "loc", "safe": True},

    # ---- Concesionar (public doar numele, NICIODATĂ CNP / adresă / telefon) ----
    "concesionar_nume": {"label": "Nume titular", "group": "Titular", "source": "concesionar", "safe": True},

    # ---- Înhumați (public doar numele + data deces / înhumare) ----
    "inhumati_lista":         {"label": "Lista celor înhumați (nume + prenume)",
                               "group": "Persoane înhumate", "source": "inhumati", "safe": True},
    "inhumati_data_deces":    {"label": "Data decesului",
                               "group": "Persoane înhumate", "source": "inhumati", "safe": True},
    "inhumati_data_inhumare": {"label": "Data înhumării",
                               "group": "Persoane înhumate", "source": "inhumati", "safe": True},

    # ---- Contract (public doar dacă e valabil sau nu — fără nr. contract, sume) ----
    "contract_status": {"label": "Status contract (valabil/expirat)",
                        "group": "Contract", "source": "contract", "safe": True},

    # ---- Memorial / notă publică ----
    "nota_memorial": {"label": "Notă memorial (text public setat manual)",
                      "group": "Memorial", "source": "loc", "safe": True},

    # ---- CÂMPURI BLOCATE (safe=False) — NU pot fi expuse niciodată public ----
    "concesionar_cnp":     {"label": "CNP titular",        "group": "🔒 Blocate", "source": "concesionar", "safe": False},
    "concesionar_adresa":  {"label": "Adresă titular",     "group": "🔒 Blocate", "source": "concesionar", "safe": False},
    "concesionar_telefon": {"label": "Telefon titular",    "group": "🔒 Blocate", "source": "concesionar", "safe": False},
    "inhumati_cnp":        {"label": "CNP înhumați",       "group": "🔒 Blocate", "source": "inhumati", "safe": False},
}


# Setările implicite (fields bifate default la instalare)
DEFAULT_PUBLIC_FIELDS = [
    "cod_loc", "sector", "parcela", "rand", "numar",
    "tip", "stare", "categorie",
    "inhumati_lista", "inhumati_data_deces", "inhumati_data_inhumare",
    "nota_memorial",
]


def get_public_fields() -> List[str]:
    """Returnează lista de chei bifate ca publice (din settings)."""
    try:
        val = db.get_setting("qr_public_fields")
        if val is None:
            return list(DEFAULT_PUBLIC_FIELDS)
        # val poate fi șir gol → utilizatorul a debifat conștient tot
        keys = [k.strip() for k in val.split(",") if k.strip()]
        # elimină câmpurile blocate (safety net)
        return [k for k in keys if AVAILABLE_FIELDS.get(k, {}).get("safe", False)]
    except Exception:
        logger.exception("Nu am putut citi qr_public_fields")
        return list(DEFAULT_PUBLIC_FIELDS)


def set_public_fields(keys: List[str]) -> None:
    """Salvează lista de câmpuri publice (auto-filtrată de câmpurile blocate)."""
    safe_keys = [k for k in keys if AVAILABLE_FIELDS.get(k, {}).get("safe", False)]
    db.set_setting("qr_public_fields", ",".join(safe_keys))
    logger.info("qr_public_fields salvat: %s", safe_keys)


def get_public_data(cod_loc: str) -> Dict[str, Any]:
    """
    Construiește dict-ul cu datele PUBLICE pentru un loc de veci,
    respectând setările din `qr_public_fields`. Nu conține niciodată date
    marcate `safe=False`.

    Returnează structură:
    {
        "cod_loc": "A-01-05",
        "sections": [
            {"title": "Localizare", "fields": [{"label":"Sector","value":"A"}, ...]},
            {"title": "Persoane înhumate", "fields": [...]},
            ...
        ]
    }
    """
    keys = get_public_fields()
    loc = db.load_loc_by_cod(cod_loc) or {}
    if not loc:
        return {"cod_loc": cod_loc, "found": False, "sections": []}

    concesionar_data, contract_status = _load_titular(cod_loc)
    sections = _field_sections(keys, loc, concesionar_data, contract_status)
    sections.extend(_inhumati_sections(keys, _load_inhumati(cod_loc)))
    return {"cod_loc": cod_loc, "found": True, "sections": sections}


def _contract_status(contract: Dict[str, Any]) -> str:
    from datetime import date, datetime
    try:
        end_dt = datetime.strptime(str(contract.get("data_sfarsit") or "")[:10], "%Y-%m-%d").date()
    except Exception:
        return "necunoscut"
    return "Valabil" if end_dt >= date.today() else "Expirat"


def _load_titular(cod_loc: str) -> Tuple[Dict[str, Any], Optional[str]]:
    """Titularul și starea contractului cel mai recent al locului (gol dacă nu există contract)."""
    try:
        loc_contracts = [c for c in db.load_contracte() if c.get("cod_loc") == cod_loc]
        if not loc_contracts:
            return {}, None
        active = max(loc_contracts, key=lambda c: str(c.get("data_contract") or ""))
        status = _contract_status(active)
        cid = active.get("concesionar_id")
        if not cid:
            return {}, status
        try:
            return db.get_concesionar(int(cid)) or {}, status
        except Exception:
            logger.exception("Concesionar %s invalid", cid)
            return {}, status
    except Exception:
        logger.exception("Eroare la încărcare contract pentru %s", cod_loc)
        return {}, None


def _load_inhumati(cod_loc: str) -> List[Dict[str, Any]]:
    try:
        return db.load_inhumati_for_loc(cod_loc) or []
    except Exception:
        logger.exception("Eroare la încărcare înhumați pentru %s", cod_loc)
        return []


_SECTION_ORDER = ["Localizare", "Detalii loc", "Titular", "Contract", "Memorial"]


def _field_sections(keys: List[str], loc: Dict[str, Any], concesionar: Dict[str, Any],
                    contract_status: Optional[str]) -> List[Dict[str, Any]]:
    used_groups: Dict[str, List[Dict[str, Any]]] = {}
    for key in keys:
        meta = AVAILABLE_FIELDS.get(key)
        if not meta or not meta["safe"] or meta["group"] == "Persoane înhumate":
            continue
        value = _resolve_field(key, meta, loc, concesionar, contract_status)
        if value not in (None, "", "—"):
            used_groups.setdefault(meta["group"], []).append({"label": meta["label"], "value": value})
    return [{"title": name, "fields": used_groups[name]} for name in _SECTION_ORDER if used_groups.get(name)]


def _inhumat_entry(inh: Dict[str, Any], show_name: bool, show_death: bool, show_burial: bool) -> Dict[str, Any]:
    entry: Dict[str, Any] = {}
    if show_name:
        entry["nume"] = f"{inh.get('nume', '')} {inh.get('prenume', '')}".strip() or "—"
    if show_death and inh.get("data_deces"):
        entry["data_deces"] = inh["data_deces"]
    if show_burial and inh.get("data_inhumare"):
        entry["data_inhumare"] = inh["data_inhumare"]
    return entry


def _inhumati_sections(keys: List[str], inhumati: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    flags = ("inhumati_lista" in keys, "inhumati_data_deces" in keys, "inhumati_data_inhumare" in keys)
    if not inhumati or not any(flags):
        return []
    entries = [e for e in (_inhumat_entry(inh, *flags) for inh in inhumati) if e]
    return [{"title": "Persoane înhumate", "entries": entries}] if entries else []


def _resolve_field(key: str, meta: dict, loc: dict,
                   concesionar: dict, contract_status: Optional[str]) -> Any:
    """Extrage valoarea unui câmp în funcție de sursa lui."""
    if meta["source"] == "loc":
        if key == "nota_memorial":
            # notă publică din settings per-loc (dacă nu există, întoarcem None)
            try:
                return db.get_setting(f"nota_memorial_{loc.get('cod_loc', '')}") or None
            except Exception:
                return None
        return loc.get(key)
    if meta["source"] == "concesionar":
        if key == "concesionar_nume":
            n = f"{concesionar.get('nume', '')} {concesionar.get('prenume', '')}".strip()
            return n or None
        return None
    if meta["source"] == "contract":
        if key == "contract_status":
            return contract_status
    return None
