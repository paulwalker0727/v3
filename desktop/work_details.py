"""Lucrări / avize: etichete și grupare pe etape a documentelor, comune pentru MAIN admin și MAIN operator (fără Qt)."""
from typing import Any, Dict, List, Tuple

STAGES: Tuple[Tuple[str, str], ...] = (
    ("lucrare_inainte", "ÎNAINTE"),
    ("lucrare_in_timpul", "ÎN TIMPUL LUCRĂRII"),
    ("lucrare_dupa", "DUPĂ"),
)
STAGE_TYPES = tuple(k for k, _ in STAGES)
OTHER_LABEL = "Alte fotografii / documente (necategorizate)"
STARI = {"planificata": "Planificată", "in_lucru": "În lucru", "finalizata": "Finalizată"}


def stare_label(av: Dict[str, Any]) -> str:
    s = str(av.get("stare") or "").strip()
    return STARI.get(s, s) if s else "Nespecificată"


def finalizare_label(av: Dict[str, Any]) -> str:
    return str(av.get("data_finalizare") or "").strip()


def split_docs(docs: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Grupează documentele unei lucrări: cele 3 etape + „other" (foto_lucrare vechi, fără categorie, acte)."""
    out: Dict[str, List[Dict[str, Any]]] = {k: [] for k in STAGE_TYPES}
    out["other"] = []
    for d in docs:
        out[d.get("doc_type") if d.get("doc_type") in STAGE_TYPES else "other"].append(d)
    return out
