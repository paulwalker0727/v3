"""Offline OCR fallback folosind Tesseract — activ când Gemini nu răspunde
sau când utilizatorul cere explicit „doar offline".

Suportă:
    - Buletin de identitate (CI) — extragere prin regex + heuristici din text OCR RO
    - Certificat de deces / adeverință de înhumare — idem

Returnează același format ca `ai_client.extract_document_sync`:
    {
      "document_type": "buletin" | "certificat_deces",
      "fields": {"nume": {"value": "...", "confidence": 0.0-1.0}, ...},
      "raw_text": "text OCR complet",
      "engine": "tesseract"   # marker suplimentar pentru offline
    }
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Any, Optional

from .ai_client import DocumentType


# =============== DEPENDENCY DETECTION ===============

def tesseract_available() -> bool:
    """Verifică dacă Tesseract e instalat pe sistem."""
    if shutil.which("tesseract") is None:
        return False
    try:
        import pytesseract  # noqa: F401
        return True
    except ImportError:
        return False


def tesseract_langs() -> list[str]:
    """Returnează listă de limbi Tesseract disponibile."""
    try:
        out = subprocess.check_output(["tesseract", "--list-langs"], text=True, timeout=5)
        return [l.strip() for l in out.splitlines()[1:] if l.strip()]
    except Exception:
        return []


def has_romanian() -> bool:
    return "ron" in tesseract_langs()


# =============== OCR CORE ===============

# PSM = Page Segmentation Mode. Diferite documente merg mai bine cu PSM-uri diferite.
#   3  = auto (default)
#   4  = single column of text of variable sizes  (ideal pt buletin)
#   6  = uniform block of text                    (ideal pt certificate)
#   11 = sparse text                              (fallback la layout ciudat)
_PSM_MODES = [6, 4, 3, 11]


def _run_tesseract(img, lang: str, psm: int) -> tuple[str, float]:
    """Rulează Tesseract cu un PSM anume. Returnează (text, avg_conf)."""
    import pytesseract
    config = f"--oem 3 --psm {psm}"
    text = pytesseract.image_to_string(img, lang=lang, config=config)
    try:
        data = pytesseract.image_to_data(
            img, lang=lang, config=config, output_type=pytesseract.Output.DICT
        )
        confs = [int(c) for c in data["conf"] if str(c) != "-1"]
        avg = (sum(confs) / len(confs) / 100.0) if confs else 0.5
    except Exception:
        avg = 0.5
    return text, max(0.0, min(1.0, avg))


def _ocr_image(path: str) -> tuple[str, float]:
    """OCR complet cu preprocesare + multi-PSM. Returnează (best_text, best_conf).

    Suportă imagini și PDF (PDF → prima pagină convertită cu PyMuPDF).
    """
    from PIL import Image

    lang = "ron+eng" if has_romanian() else "eng"
    # Folosește modelul custom fine-tuned dacă există
    try:
        from .training import preferred_language
        lang = preferred_language()
    except Exception:
        pass

    # Dacă e PDF, convertim prima pagină în PIL Image
    ext = os.path.splitext(path)[1].lower()
    src_img = None
    if ext == ".pdf":
        try:
            import fitz  # PyMuPDF
            import io
            doc = fitz.open(path)
            if doc.page_count == 0:
                doc.close()
                raise RuntimeError("PDF gol")
            page = doc.load_page(0)
            pix = page.get_pixmap(dpi=250)
            png_bytes = pix.tobytes("png")
            doc.close()
            src_img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        except Exception as e:
            print(f"[ocr] conversie PDF→imagine eșuată: {e}")
            raise RuntimeError(f"Nu pot converti PDF-ul: {e}") from e
    else:
        try:
            with Image.open(path) as im:
                im.load()
                src_img = im.copy()
        except Exception as e:
            raise RuntimeError(f"Nu pot deschide imaginea: {e}") from e

    # Preprocesare
    try:
        from .image_preprocessing import preprocess_for_ocr
        img = preprocess_for_ocr(src_img)
    except Exception as e:
        print(f"[ocr] preprocesare eșuată, folosesc imaginea originală: {e}")
        img = src_img.convert("RGB") if src_img else None

    if img is None:
        raise RuntimeError("Imagine indisponibilă pentru OCR.")

    # Multi-PSM: rulăm mai multe moduri și alegem rezultatul „cel mai bogat"
    best_text = ""
    best_conf = 0.0
    best_score = -1.0

    def _score(text: str, conf: float) -> float:
        # scor bazat pe: prezență CNP, date, câmpuri identificabile + confidence
        has_cnp = bool(re.search(r"\b\d{13}\b", text))
        n_dates = len(re.findall(r"\d{1,2}[.\-/ ]\d{1,2}[.\-/ ]\d{4}", text))
        has_seria = bool(re.search(r"SERIA?\s+[A-Z]{2}", text, re.IGNORECASE))
        has_labels = len(re.findall(
            r"\b(Nume|Prenume|CNP|Domiciliu|Data\s*nasterii|Data\s*deces|Cauza)\b",
            text, re.IGNORECASE,
        ))
        return (
            conf
            + (0.20 if has_cnp else 0)
            + (0.05 * min(n_dates, 3))
            + (0.10 if has_seria else 0)
            + (0.05 * min(has_labels, 6))
        )

    for psm in _PSM_MODES:
        try:
            text, conf = _run_tesseract(img, lang, psm)
            sc = _score(text, conf)
            if sc > best_score:
                best_text = text
                best_conf = conf
                best_score = sc
        except Exception as e:
            print(f"[ocr] PSM {psm} eșuat: {e}")
            continue

    return best_text, best_conf


# =============== REGEX PATTERNS RO ===============

# CNP: permite câteva confuzii tipice OCR (O→0, l/I→1, S→5)
def _fix_ocr_digits(s: str) -> str:
    """Corectează confuzii tipice OCR pentru text numeric."""
    return (s.replace("O", "0").replace("o", "0")
             .replace("l", "1").replace("I", "1")
             .replace("S", "5").replace("B", "8")
             .replace(" ", ""))


RE_CNP = re.compile(r"\b([1-9]\d{12})\b")
RE_CNP_FUZZY = re.compile(r"\b([1-9OoIlSB\d]{13})\b")  # cu confuzii OCR
RE_SERIE_NR = re.compile(r"SERIA?\s*([A-Z]{2})\s*(?:NR\.?|NUM[AĂ]R\.?|№)?\s*[-·]?\s*(\d{6})", re.IGNORECASE)
RE_SERIE_NR_ALT = re.compile(r"\b([A-Z]{2})\s*[-·]?\s*(\d{6})\b")  # fallback fără cuvinte
RE_SERIE_NR_FUZZY = re.compile(r"\b([A-Z]{2})\s*[-·]?\s*([\dOolI]{6})\b")
RE_DATE_DMY = re.compile(r"\b(\d{1,2})[.\-/ ](\d{1,2})[.\-/ ](\d{4})\b")
RE_DATE_ROMANIAN_MONTHS = re.compile(
    r"\b(\d{1,2})\s+(ianuarie|februarie|martie|aprilie|mai|iunie|iulie|august|septembrie|octombrie|noiembrie|decembrie)\s+(\d{4})\b",
    re.IGNORECASE,
)

ROMANIAN_MONTHS = {
    "ianuarie": "01", "februarie": "02", "martie": "03", "aprilie": "04",
    "mai": "05", "iunie": "06", "iulie": "07", "august": "08",
    "septembrie": "09", "octombrie": "10", "noiembrie": "11", "decembrie": "12",
}


def _find_cnp(text: str) -> str:
    """Găsește CNP-ul cu tolerance la confuzii OCR."""
    m = RE_CNP.search(text)
    if m:
        return m.group(1)
    # încercăm cu confuzii
    m = RE_CNP_FUZZY.search(text)
    if m:
        candidate = _fix_ocr_digits(m.group(1))
        if len(candidate) == 13 and candidate.isdigit() and candidate[0] in "123456":
            return candidate
    # ultimă șansă: caută 13 caractere consecutive care ar putea fi CNP
    for m in re.finditer(r"[1-6OoIlSB\d]{13}", text):
        cand = _fix_ocr_digits(m.group(0))
        if len(cand) == 13 and cand.isdigit() and cand[0] in "123456":
            return cand
    return ""


def _find_serie_nr(text: str) -> tuple[str, str]:
    """Găsește seria (2 litere) și numărul (6 cifre)."""
    m = RE_SERIE_NR.search(text)
    if m:
        return m.group(1), m.group(2)
    m = RE_SERIE_NR_FUZZY.search(text)
    if m:
        num = _fix_ocr_digits(m.group(2))
        if num.isdigit() and len(num) == 6:
            return m.group(1), num
    return "", ""


def _normalize_date(day: str, month: str, year: str) -> str:
    """→ DD.MM.YYYY."""
    return f"{int(day):02d}.{int(month):02d}.{int(year):04d}"


def _find_after(text: str, labels: list[str], max_chars: int = 120) -> str:
    """Returnează primul text (până la newline sau limit) de după oricare din label-uri.

    Suportă și PSM 11 (sparse): label pe o linie și valoarea pe următoarea.
    """
    # 1. Same-line pattern
    for lbl in labels:
        m = re.search(
            rf"{re.escape(lbl)}\s*[:.\-]?\s*([^\n]+)",
            text, re.IGNORECASE,
        )
        if m:
            val = m.group(1).strip()
            val = re.split(
                r"\b(CNP|Nume|Prenume|Sex|Nationalitate|Emis|Domiciliu|"
                r"Serie|Cetatenia|Adresa|Adresă|Data|Loc\s*nastere|Naționalitate|"
                r"Valabil|Emisde|SPCLEP)\b",
                val, maxsplit=1,
            )[0]
            val = val.strip(" .,:;-")
            if val and len(val) <= max_chars:
                return val

    # 2. Next-line fallback (PSM 11)
    lines = [l.strip() for l in text.splitlines()]
    for i, ln in enumerate(lines):
        for lbl in labels:
            if re.match(rf"^{re.escape(lbl)}\s*[:.\-]?\s*$", ln, re.IGNORECASE):
                for j in range(i + 1, min(i + 4, len(lines))):
                    nxt = lines[j].strip(" .,:;-")
                    if nxt and not re.match(
                        r"^(CNP|Nume|Prenume|Sex|Nationalitate|Emis|Domiciliu|"
                        r"Serie|Cetatenia|Adresa|Adresă|Data|Loc\s*nastere|"
                        r"Valabil|SPCLEP|SERIA|NR)\b",
                        nxt, re.IGNORECASE,
                    ):
                        return nxt[:max_chars]
    return ""


def _dates_near_label(text: str, labels: list[str]) -> str:
    """Găsește o dată care apare după un label (același rând sau următorul)."""
    # Same-line
    for lbl in labels:
        m = re.search(
            rf"{re.escape(lbl)}[^\n]{{0,80}}?(\d{{1,2}}[.\-/ ]\d{{1,2}}[.\-/ ]\d{{4}})",
            text, re.IGNORECASE,
        )
        if m:
            parts = re.split(r"[.\-/ ]", m.group(1))
            if len(parts) == 3:
                return _normalize_date(parts[0], parts[1], parts[2])
    # Next-line (PSM 11)
    lines = [l.strip() for l in text.splitlines()]
    for i, ln in enumerate(lines):
        for lbl in labels:
            if re.match(rf"^{re.escape(lbl)}\s*[:.\-]?\s*$", ln, re.IGNORECASE):
                for j in range(i + 1, min(i + 4, len(lines))):
                    m = RE_DATE_DMY.search(lines[j])
                    if m:
                        return _normalize_date(m.group(1), m.group(2), m.group(3))
    return ""


def _extract_first_date(text: str) -> str:
    m = RE_DATE_DMY.search(text)
    if m:
        return _normalize_date(m.group(1), m.group(2), m.group(3))
    m2 = RE_DATE_ROMANIAN_MONTHS.search(text)
    if m2:
        month = ROMANIAN_MONTHS.get(m2.group(2).lower(), "01")
        return _normalize_date(m2.group(1), month, m2.group(3))
    return ""


def _all_dates(text: str) -> list[str]:
    """Toate datele găsite (în ordine de apariție), format DD.MM.YYYY."""
    dates = []
    for m in RE_DATE_DMY.finditer(text):
        dates.append(_normalize_date(m.group(1), m.group(2), m.group(3)))
    for m in RE_DATE_ROMANIAN_MONTHS.finditer(text):
        month = ROMANIAN_MONTHS.get(m.group(2).lower(), "01")
        dates.append(_normalize_date(m.group(1), month, m.group(3)))
    return dates


def _dates_near_label_STUB_unused(text: str, labels: list[str]) -> str:
    """(Deprecat — folosește _dates_near_label din secțiunea find_after)."""
    return ""


# =============== PARSERE PE TIP ===============

def _parse_buletin(text: str, base_conf: float) -> dict:
    """Extrage câmpuri buletin CI din text OCR."""
    fields: dict[str, dict[str, Any]] = {}

    def _put(key: str, value: str, conf: Optional[float] = None):
        c = base_conf if conf is None else conf
        if not value:
            c = 0.0
        fields[key] = {"value": value, "confidence": round(c, 2)}

    # CNP (cu toleranță OCR)
    cnp = _find_cnp(text)
    _put("cnp", cnp, base_conf if cnp else 0.0)

    # Serie + Număr (cu toleranță OCR)
    serie, numar = _find_serie_nr(text)
    _put("serie", serie, base_conf if serie else 0.0)
    _put("numar", numar, base_conf if numar else 0.0)

    # Nume / Prenume — încearcă mai multe variații (Nume/Nom/Last name)
    nume = _find_after(text, ["Nume", "NUME", "Last Name", "Nom", "Name/Nume"])
    prenume = _find_after(text, ["Prenume", "PRENUME", "First Name", "Prenom", "Given Name"])
    nume = re.sub(r"\s+", " ", nume).strip()
    prenume = re.sub(r"\s+", " ", prenume).strip()
    # dacă nume conține și prenume (Tesseract uneori le pune pe același rând), încearcă split
    if nume and not prenume and " " in nume:
        parts = nume.split(None, 1)
        if len(parts) == 2 and len(parts[0]) >= 3:
            nume, prenume = parts[0], parts[1]
    _put("nume", nume, base_conf * 0.9 if nume else 0.0)
    _put("prenume", prenume, base_conf * 0.9 if prenume else 0.0)

    # Sex — permite S, F, B (unele confuzii OCR M→B)
    m = re.search(r"\bSex[uxl]*[:.\-\s/]*([MFBmfb])\b", text)
    sex_val = ""
    if m:
        s = m.group(1).upper()
        sex_val = "M" if s == "B" else s
    _put("sex", sex_val, base_conf if sex_val else 0.0)

    # Naționalitate
    nat = _find_after(text, ["Nationalitate", "Cetatenia", "Nationality", "Naționalitate"])
    if nat and len(nat) > 40:  # cleanup — uneori include text prea mult
        nat = nat.split()[0]
    _put("nationalitate", nat, base_conf * 0.7 if nat else 0.0)

    # Adresa
    adresa = _find_after(text, ["Domiciliu", "Adresa", "Adresă", "Address"], max_chars=200)
    _put("adresa", adresa, base_conf * 0.7 if adresa else 0.0)

    # Emitent
    emitent = _find_after(text, ["Emis de", "Emitent", "Issuing", "SPCLEP"])
    if not emitent and "SPCLEP" in text:
        m = re.search(r"SPCLEP\s+([A-ZĂÎÂȘȚa-zăîâșț\-\s]{2,40})", text)
        emitent = m.group(0).strip() if m else ""
    _put("emitent", emitent, base_conf * 0.7 if emitent else 0.0)

    # Data nașterii
    dob = _dates_near_label(text, ["Data nasterii", "Nascut", "DOB", "Data naster", "Data nașterii"])
    if not dob:
        # fallback: cea mai veche dată din text (nașterea e de obicei cea mai veche)
        all_d = _all_dates(text)
        if all_d:
            all_d_sorted = sorted(all_d, key=lambda d: d[-4:])  # sort by year
            dob = all_d_sorted[0]
    _put("data_nasterii", dob, base_conf * 0.7 if dob else 0.0)

    # Data eliberării
    elib = _dates_near_label(text, ["Data emiterii", "Emis la", "Data eliberarii",
                                     "Data eliberării", "Issued", "Data emit"])
    _put("data_eliberarii", elib, base_conf * 0.7 if elib else 0.0)

    # Valabilitate
    valab = _dates_near_label(text, ["Valabil pana la", "Valabilitate", "Valid pana",
                                      "Valabil până la", "Expiry", "Expiră"])
    _put("valabilitate", valab, base_conf * 0.7 if valab else 0.0)

    return fields


def _parse_certificat_deces(text: str, base_conf: float) -> dict:
    """Extrage câmpuri certificat deces / adeverință inhumare."""
    fields: dict[str, dict[str, Any]] = {}

    def _put(key: str, value: str, conf: Optional[float] = None):
        c = base_conf if conf is None else conf
        if not value:
            c = 0.0
        fields[key] = {"value": value, "confidence": round(c, 2)}

    # CNP cu toleranță OCR
    cnp = _find_cnp(text)
    _put("cnp", cnp, base_conf if cnp else 0.0)

    # Nr certificat / adeverință (cu cleanup pentru text care continuă pe rândul următor)
    m = re.search(r"Seria\s*(?:si\s*nr\.?|și\s*nr\.?)?\s*[:.\-]?\s*([A-Z]{1,3}\s*\d{4,8})", text, re.IGNORECASE)
    nr_cert = m.group(1).strip() if m else ""
    if "adeverint" in text.lower() or "adeverință" in text.lower():
        _put("nr_adeverinta_inhumare", nr_cert, base_conf * 0.7 if nr_cert else 0.0)
        _put("nr_certificat_deces", "", 0.0)
    else:
        _put("nr_certificat_deces", nr_cert, base_conf * 0.7 if nr_cert else 0.0)
        _put("nr_adeverinta_inhumare", "", 0.0)

    # Nume/Prenume
    nume = _find_after(text, ["Numele", "Nume", "Last Name"])
    prenume = _find_after(text, ["Prenumele", "Prenume", "First Name"])
    nume = re.sub(r"\s+", " ", nume).strip()
    prenume = re.sub(r"\s+", " ", prenume).strip()
    if nume and not prenume and " " in nume:
        parts = nume.split(None, 1)
        if len(parts) == 2 and len(parts[0]) >= 3:
            nume, prenume = parts[0], parts[1]
    _put("nume", nume, base_conf * 0.9 if nume else 0.0)
    _put("prenume", prenume, base_conf * 0.9 if prenume else 0.0)

    # Sex (permite variații: Sex/Sexul/Sexul persoanei)
    m = re.search(r"\bSex(?:ul)?[:.\-\s/]+([MFBmfb])\b", text)
    sex_val = ""
    if m:
        s = m.group(1).upper()
        sex_val = "M" if s == "B" else s
    _put("sex", sex_val, base_conf if sex_val else 0.0)

    # Date importante
    _put("data_nasterii",
         _dates_near_label(text, ["Data nasterii", "Nascut", "Data nașterii"]),
         base_conf * 0.7)
    _put("data_deces",
         _dates_near_label(text, ["Data decesului", "Data deces", "Decedat la", "Data morții"]),
         base_conf * 0.7)
    _put("data_eliberare",
         _dates_near_label(text, ["Data eliberarii", "Data eliberării", "Eliberat la", "Emis la"]),
         base_conf * 0.7)

    # Locul decesului
    loc = _find_after(text, ["Locul decesului", "Loc deces", "Decedat la", "Locul morții"], max_chars=150)
    _put("locul_decesului", loc, base_conf * 0.6 if loc else 0.0)

    # Cauza
    cauza = _find_after(text, ["Cauza decesului", "Cauza", "Cauza deces", "Cauza morții"], max_chars=200)
    _put("cauza_deces", cauza, base_conf * 0.6 if cauza else 0.0)

    # Ultima adresa
    ultima_adr = _find_after(
        text,
        ["Ultimul domiciliu", "Ultima adresa", "Ultima adresă", "Domiciliu"],
        max_chars=200,
    )
    _put("ultima_adresa", ultima_adr, base_conf * 0.6 if ultima_adr else 0.0)

    # Emitent
    emitent = _find_after(text, ["Emis de", "Emitent", "Primaria", "Primăria"], max_chars=120)
    _put("emitent", emitent, base_conf * 0.6 if emitent else 0.0)

    return fields


def _parse_adeverinta_adresa(text: str, base_conf: float) -> dict:
    """Extrage câmpuri din adeverința de adresă (anexa CI electronic)."""
    fields: dict[str, dict[str, Any]] = {}

    def _put(key: str, value: str, conf: Optional[float] = None):
        c = base_conf if conf is None else conf
        if not value:
            c = 0.0
        fields[key] = {"value": value, "confidence": round(c, 2)}

    cnp = _find_cnp(text)
    _put("cnp", cnp, base_conf if cnp else 0.0)

    serie, numar = _find_serie_nr(text)
    _put("serie", serie, base_conf if serie else 0.0)
    _put("numar", numar, base_conf if numar else 0.0)

    nume = _find_after(text, ["Nume", "Numele", "Last Name"])
    prenume = _find_after(text, ["Prenume", "Prenumele", "First Name"])
    _put("nume", re.sub(r"\s+", " ", nume).strip(), base_conf * 0.9 if nume else 0.0)
    _put("prenume", re.sub(r"\s+", " ", prenume).strip(), base_conf * 0.9 if prenume else 0.0)

    adresa = _find_after(text, ["Adresa", "Adresă", "Domiciliu"], max_chars=250)
    _put("adresa", adresa, base_conf * 0.7 if adresa else 0.0)

    localitate = _find_after(text, ["Localitate", "Localitatea", "Oras", "Oraș", "Municipiu", "Comuna"], max_chars=80)
    _put("localitate", localitate, base_conf * 0.6 if localitate else 0.0)

    judet = _find_after(text, ["Judet", "Județ", "Judetul", "Județul"], max_chars=50)
    _put("judet", judet, base_conf * 0.6 if judet else 0.0)

    elib = _dates_near_label(text, ["Data eliberarii", "Data eliberării", "Emis la"])
    _put("data_eliberarii", elib, base_conf * 0.7 if elib else 0.0)

    emitent = _find_after(text, ["Emis de", "Emitent", "SPCLEP", "DEPABD"], max_chars=100)
    if not emitent and ("SPCLEP" in text or "DEPABD" in text):
        m = re.search(r"(SPCLEP|DEPABD)\s+([A-ZĂÎÂȘȚa-zăîâșț\-\s]{2,40})", text)
        emitent = m.group(0).strip() if m else ""
    _put("emitent", emitent, base_conf * 0.6 if emitent else 0.0)

    nr_ad = _find_after(text, ["Nr", "Numar adeverinta", "Număr adeverință"], max_chars=30)
    _put("nr_adeverinta", nr_ad, base_conf * 0.5 if nr_ad else 0.0)

    return fields


# =============== PUBLIC API ===============

def extract_document_offline(file_path: str, document_type: str) -> dict[str, Any]:
    """Extrage câmpuri offline folosind Tesseract + regex.

    Aplică feedback_learning.apply_text_corrections pe raw OCR și
    feedback_learning.apply_field_corrections pe rezultate — creștere de precizie
    din corecțiile utilizatorului anterioare.
    """
    if not tesseract_available():
        raise RuntimeError(
            "Tesseract nu este instalat pe acest sistem. "
            "Instalează cu: apt-get install tesseract-ocr tesseract-ocr-ron"
        )
    if not os.path.exists(file_path):
        raise FileNotFoundError(file_path)

    text, avg_conf = _ocr_image(file_path)

    # Aplică corecții text-level învățate din feedback
    try:
        from .feedback_learning import apply_text_corrections
        text = apply_text_corrections(text)
    except Exception:
        pass

    if document_type == DocumentType.BULETIN:
        fields = _parse_buletin(text, avg_conf)
    elif document_type == DocumentType.ADEVERINTA_ADRESA:
        fields = _parse_adeverinta_adresa(text, avg_conf)
    else:
        fields = _parse_certificat_deces(text, avg_conf)

    # Aplică corecții pe câmpuri
    try:
        from .feedback_learning import apply_field_corrections
        fields, n = apply_field_corrections(fields)
        if n > 0:
            print(f"[offline_ocr] Aplicate {n} corecții din feedback learning")
    except Exception:
        pass

    return {
        "document_type": document_type,
        "fields": fields,
        "raw_text": text,
        "engine": "tesseract",
    }
