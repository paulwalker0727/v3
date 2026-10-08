# contract_generator.py
"""
Generator automat pentru contractele de concesiune.

Funcționalitate:
    - Ia un contract_id din DB
    - Colectează TOATE datele necesare (contract + concesionar + loc de veci)
    - Deschide template-ul Word (`templates/contract_template.docx`)
    - Înlocuiește placeholder-ele {{...}} cu datele reale
    - Salvează într-un fișier .docx nou (nume: `Contract_<numar>_<owner>.docx`)

Placeholder-ele suportate (identice cu template-ul actual):
    {{contract_number}}, {{contract_date}}, {{owner_fullname}}, {{owner_address}},
    {{owner_id_card}}, {{owner_cnp}}, {{owner_phone}}, {{owner_email}},
    {{cemetery_parcel}}, {{cemetery_row}}, {{cemetery_tomb}}, {{tomb_surface_m2}},
    {{owner_fullname_2}}

Robust:
    - Înlocuiește în paragrafe ȘI în celule de tabel (recursiv)
    - Rezolvă problema python-docx cu placeholder-uri „sparte" între runs
      (rescrie textul în primul run și golește restul, păstrând stilul primului)
"""

import os
import re
import unicodedata
from datetime import datetime
from typing import Dict, Optional, List, Any

import database as db


try:
    from docx import Document
    _DOCX_AVAILABLE = True
except ImportError:
    Document = None
    _DOCX_AVAILABLE = False


# =============================================================================
# HELPERS: REPLACERS ROBUSTI
# =============================================================================
_PLACEHOLDER_RE = re.compile(r"\{\{\s*[a-zA-Z0-9_]+\s*\}\}")


def _replace_in_paragraph(paragraph, mapping: Dict[str, str]) -> bool:
    """
    Înlocuiește placeholder-ele în paragraf.

    python-docx sparge deseori un placeholder în runs multiple
    (ex: `{{owner_`, `fullname}}`). Solutia:
        1. Concatenăm textul din toate runs → verificăm dacă are placeholder
        2. Dacă da, aplicăm toate înlocuirile pe textul concatenat
        3. Punem rezultatul întreg în primul run și golim celelalte runs
           (păstrează stilul primului run — suficient pentru contracte)
    """
    if not paragraph.runs:
        return False

    full_text = "".join(run.text for run in paragraph.runs)
    if "{{" not in full_text:
        return False

    new_text = full_text
    for key, value in mapping.items():
        pattern = "{{" + key + "}}"
        if pattern in new_text:
            new_text = new_text.replace(pattern, str(value) if value is not None else "")

    if new_text == full_text:
        return False

    # rescriere: primul run păstrează stilul, restul se golesc
    paragraph.runs[0].text = new_text
    for run in paragraph.runs[1:]:
        run.text = ""
    return True


def _replace_in_cell(cell, mapping: Dict[str, str]):
    for paragraph in cell.paragraphs:
        _replace_in_paragraph(paragraph, mapping)
    # nested tables
    for tbl in cell.tables:
        for row in tbl.rows:
            for c in row.cells:
                _replace_in_cell(c, mapping)


def _apply_mapping(document, mapping: Dict[str, str]):
    # paragrafele principale (corpul documentului)
    for paragraph in document.paragraphs:
        _replace_in_paragraph(paragraph, mapping)

    # tabele
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                _replace_in_cell(cell, mapping)

    # header / footer
    for section in document.sections:
        for hf in (section.header, section.footer):
            if hf is None:
                continue
            for paragraph in hf.paragraphs:
                _replace_in_paragraph(paragraph, mapping)


# =============================================================================
# COLECTAREA DATELOR
# =============================================================================
def _fmt(v: Any) -> str:
    """Converteste orice valoare la string safe (None → '')."""
    if v is None:
        return ""
    return str(v)


def _slugify(text: str, max_len: int = 40) -> str:
    """Curăță un string pentru a fi folosit în nume de fișier."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip()
    text = re.sub(r"[-\s]+", "_", text)
    return text[:max_len]


def _build_mapping(contract: Dict[str, Any]) -> Dict[str, str]:
    """
    Construiește dict-ul de înlocuire pentru template.
    Ordinea de preferință a datelor:
        1. Câmpuri owner_* stocate direct în contract (introduse manual)
        2. Fallback pe date din tabelul `concesionari` (via concesionar_id)
        3. Câmpuri cemetery_* din contract; fallback pe locuri_de_veci (via cod_loc)
    """
    # ---- date owner ----
    owner_fullname = _fmt(contract.get("owner_fullname"))
    owner_address = _fmt(contract.get("owner_address"))
    owner_id_card = _fmt(contract.get("owner_id_card_serie_number"))
    owner_cnp = _fmt(contract.get("owner_cnp"))
    owner_phone = _fmt(contract.get("owner_phone"))
    owner_email = _fmt(contract.get("owner_email"))

    # fallback la concesionar
    concesionar_id = contract.get("concesionar_id")
    if concesionar_id:
        conc = db.get_concesionar(concesionar_id) or {}
        if not owner_fullname:
            nume = _fmt(conc.get("nume"))
            prenume = _fmt(conc.get("prenume"))
            owner_fullname = f"{nume} {prenume}".strip()
        if not owner_address:
            owner_address = _fmt(conc.get("adresa"))
        if not owner_id_card:
            owner_id_card = _fmt(conc.get("bi_ci"))
        if not owner_cnp:
            owner_cnp = _fmt(conc.get("cnp"))
        if not owner_phone:
            owner_phone = _fmt(conc.get("telefon"))
        if not owner_email:
            owner_email = _fmt(conc.get("email"))

    # ---- date cimitir ----
    cod_loc = _fmt(contract.get("cod_loc"))
    parcela = _fmt(contract.get("cemetery_parcel"))
    rand = _fmt(contract.get("cemetery_row"))
    numar = _fmt(contract.get("cemetery_tomb"))
    suprafata = contract.get("tomb_surface_m2") or ""

    if cod_loc and (not parcela or not rand or not numar or not suprafata):
        loc = db.load_loc_by_cod(cod_loc)
        if loc:
            if not parcela:
                parcela = _fmt(loc.get("parcela"))
            if not rand:
                rand = _fmt(loc.get("rand"))
            if not numar:
                numar = _fmt(loc.get("numar"))
            if not suprafata:
                suprafata = loc.get("suprafata") or ""

    if isinstance(suprafata, float):
        supra_str = f"{suprafata:.2f}"
    else:
        supra_str = _fmt(suprafata)

    return {
        "contract_number": _fmt(contract.get("numar_contract")),
        "contract_date":   _fmt(contract.get("data_contract")),
        "owner_fullname":  owner_fullname,
        "owner_fullname_2": owner_fullname,          # apare a doua oară la semnătură
        "owner_address":   owner_address,
        "owner_id_card":   owner_id_card,
        "owner_cnp":       owner_cnp,
        "owner_phone":     owner_phone,
        "owner_email":     owner_email,
        "cemetery_parcel": parcela,
        "cemetery_row":    rand,
        "cemetery_tomb":   numar,
        "tomb_surface_m2": supra_str,
    }


# =============================================================================
# API PUBLIC
# =============================================================================
def get_missing_placeholders(mapping: Dict[str, str]) -> List[str]:
    """Returnează lista cheilor cu valoare goală (util pentru avertizare)."""
    return [k for k, v in mapping.items() if not v]


def generate_contract(contract_id: int,
                      template_path: str,
                      out_path: str) -> Optional[Dict[str, Any]]:
    """
    Generează contractul completat.

    Returns:
        {
            "path": <str, absolute output path>,
            "missing": [<placeholders goale>],
            "mapping": {<all keys>: <values>},
        }
        sau None dacă a eșuat.
    """
    if not _DOCX_AVAILABLE:
        print("[contract_generator] python-docx nu este instalat.")
        return None

    contract = db.get_contract(contract_id)
    if not contract:
        print(f"[contract_generator] Contract {contract_id} nu există.")
        return None

    if not os.path.isfile(template_path):
        print(f"[contract_generator] Template lipsă: {template_path}")
        return None

    mapping = _build_mapping(contract)

    try:
        doc = Document(template_path)
    except Exception as e:
        print(f"[contract_generator] Eroare deschidere template: {e}")
        return None

    _apply_mapping(doc, mapping)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    try:
        doc.save(out_path)
    except Exception as e:
        print(f"[contract_generator] Eroare salvare: {e}")
        return None

    return {
        "path": os.path.abspath(out_path),
        "missing": get_missing_placeholders(mapping),
        "mapping": mapping,
    }


def build_default_filename(contract: Dict[str, Any]) -> str:
    """Nume implicit: Contract_<numar>_<owner>_<YYYYMMDD>.docx"""
    numar = _slugify(_fmt(contract.get("numar_contract"))) or f"ID{contract.get('id','')}"
    # Preferăm owner_fullname din contract, altfel derivăm din concesionar
    owner = _slugify(_fmt(contract.get("owner_fullname")))
    if not owner:
        cid = contract.get("concesionar_id")
        if cid:
            conc = db.get_concesionar(cid) or {}
            full = f"{_fmt(conc.get('nume'))} {_fmt(conc.get('prenume'))}".strip()
            owner = _slugify(full)
    if not owner:
        owner = "concesionar"
    stamp = datetime.now().strftime("%Y%m%d")
    return f"Contract_{numar}_{owner}_{stamp}.docx"
