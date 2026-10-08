"""Client sincronic pentru extragere date din documente cu Gemini 3 Flash."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv


# Încarcă .env din folderul cimitir-desktop
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)

# Fallback: încearcă și DATA_DIR + variabilele de mediu ale sistemului
EMERGENT_KEY = os.environ.get("EMERGENT_LLM_KEY", "").strip()


class DocumentType:
    BULETIN = "buletin"
    CERTIFICAT_DECES = "certificat_deces"
    ADEVERINTA_ADRESA = "adeverinta_adresa"  # anexa noii CI electronice, conține adresa completă


# ============ SCHEMA / PROMPTS ============

BULETIN_PROMPT = """Ești un expert în extragerea datelor din documente oficiale românești.
Analizează atent imaginea unui BULETIN DE IDENTITATE / CARTE DE IDENTITATE românesc și extrage câmpurile.

Returnează STRICT un obiect JSON valid (fără markdown, fără text în plus), cu următoarea structură:
{
  "document_type": "buletin",
  "fields": {
    "nume":            {"value": "...", "confidence": 0.0-1.0},
    "prenume":         {"value": "...", "confidence": 0.0-1.0},
    "cnp":             {"value": "...", "confidence": 0.0-1.0},
    "serie":           {"value": "...", "confidence": 0.0-1.0},
    "numar":           {"value": "...", "confidence": 0.0-1.0},
    "data_nasterii":   {"value": "DD.MM.YYYY", "confidence": 0.0-1.0},
    "sex":             {"value": "M sau F", "confidence": 0.0-1.0},
    "nationalitate":   {"value": "...", "confidence": 0.0-1.0},
    "adresa":          {"value": "...", "confidence": 0.0-1.0},
    "emitent":         {"value": "...", "confidence": 0.0-1.0},
    "data_eliberarii": {"value": "DD.MM.YYYY", "confidence": 0.0-1.0},
    "valabilitate":    {"value": "DD.MM.YYYY", "confidence": 0.0-1.0}
  },
  "raw_text": "text OCR complet (opțional)"
}

REGULI STRICTE:
- Toate datele calendaristice OBLIGATORIU în format DD.MM.YYYY (ex: 15.03.1975)
- CNP-ul are exact 13 cifre
- Confidence este scor real bazat pe claritate: 0.9+ text foarte clar, 0.6-0.9 lizibil, sub 0.6 dificil de citit
- Dacă un câmp NU se poate citi, pune value="" și confidence=0
- NU inventa date. NU adăuga text în afara obiectului JSON.
"""


CERTIFICAT_DECES_PROMPT = """Ești un expert în extragerea datelor din documente oficiale românești.
Analizează atent imaginea unui CERTIFICAT DE DECES sau ADEVERINȚĂ DE ÎNHUMARE românesc.

Returnează STRICT un obiect JSON valid (fără markdown, fără text în plus), cu structura:
{
  "document_type": "certificat_deces",
  "fields": {
    "nume":                {"value": "...", "confidence": 0.0-1.0},
    "prenume":             {"value": "...", "confidence": 0.0-1.0},
    "cnp":                 {"value": "...", "confidence": 0.0-1.0},
    "data_nasterii":       {"value": "DD.MM.YYYY", "confidence": 0.0-1.0},
    "data_deces":          {"value": "DD.MM.YYYY", "confidence": 0.0-1.0},
    "cauza_deces":         {"value": "...", "confidence": 0.0-1.0},
    "locul_decesului":     {"value": "...", "confidence": 0.0-1.0},
    "sex":                 {"value": "M sau F", "confidence": 0.0-1.0},
    "ultima_adresa":       {"value": "...", "confidence": 0.0-1.0},
    "nr_certificat_deces": {"value": "seria + numărul", "confidence": 0.0-1.0},
    "nr_adeverinta_inhumare": {"value": "numărul adeverinței (dacă e adeverință)", "confidence": 0.0-1.0},
    "data_eliberare":      {"value": "DD.MM.YYYY", "confidence": 0.0-1.0},
    "emitent":             {"value": "...", "confidence": 0.0-1.0}
  },
  "raw_text": "text OCR complet (opțional)"
}

REGULI STRICTE:
- Toate datele calendaristice OBLIGATORIU în format DD.MM.YYYY
- CNP-ul are exact 13 cifre
- Confidence este scor real bazat pe claritate
- Dacă documentul e o ADEVERINȚĂ de înhumare, populează nr_adeverinta_inhumare; dacă e CERTIFICAT de deces, populează nr_certificat_deces
- Dacă un câmp lipsește, pune value="" și confidence=0
- NU inventa date. NU adăuga text în afara JSON-ului.
"""


ADEVERINTA_ADRESA_PROMPT = """Ești un expert în extragerea datelor din documente oficiale românești.
Analizează atent imaginea unei ADEVERINȚE DE ADRESĂ / ANEXĂ CI (documentul separat care însoțește
noua carte de identitate electronică românească — conține adresa de domiciliu ce nu mai apare
pe CI-ul propriu-zis).

Documentul include de regulă: nume, prenume, CNP, seria/numărul CI, adresa completă (stradă,
număr, bloc, scară, apartament, localitate, județ), data eliberării, emitentul (SPCLEP / DEPABD).

Returnează STRICT un obiect JSON valid (fără markdown, fără text în plus), cu structura:
{
  "document_type": "adeverinta_adresa",
  "fields": {
    "nume":            {"value": "...", "confidence": 0.0-1.0},
    "prenume":         {"value": "...", "confidence": 0.0-1.0},
    "cnp":             {"value": "...", "confidence": 0.0-1.0},
    "serie":           {"value": "serie CI din adeverință", "confidence": 0.0-1.0},
    "numar":           {"value": "număr CI", "confidence": 0.0-1.0},
    "adresa":          {"value": "adresa completă", "confidence": 0.0-1.0},
    "localitate":      {"value": "oraș/comună", "confidence": 0.0-1.0},
    "judet":           {"value": "județ", "confidence": 0.0-1.0},
    "data_eliberarii": {"value": "DD.MM.YYYY", "confidence": 0.0-1.0},
    "emitent":         {"value": "SPCLEP/DEPABD emitent", "confidence": 0.0-1.0},
    "nr_adeverinta":   {"value": "numărul adeverinței", "confidence": 0.0-1.0}
  },
  "raw_text": "text OCR complet"
}

REGULI:
- Format dată: DD.MM.YYYY
- CNP: 13 cifre
- Adresa: reconstituită complet dintr-un rând sau mai multe (Str. + număr + bl + sc + et + ap + localitate + județ)
- Nu inventa; câmpurile lipsă → value="", confidence=0
"""


PROMPTS = {
    DocumentType.BULETIN: BULETIN_PROMPT,
    DocumentType.CERTIFICAT_DECES: CERTIFICAT_DECES_PROMPT,
    DocumentType.ADEVERINTA_ADRESA: ADEVERINTA_ADRESA_PROMPT,
}


# ============ UTILS ============

def _clean_json(text: str) -> str:
    """Elimină fence-uri markdown și extrage corpul JSON."""
    text = (text or "").strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return text


def _file_to_base64(path: str) -> tuple[str, str]:
    """Returnează (base64, mime). DOAR pentru imagini (nu PDF)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".png", ".jpg", ".jpeg", ".webp"):
        with open(path, "rb") as f:
            b = f.read()
        mime = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext.lstrip('.')}"
        return base64.b64encode(b).decode("utf-8"), mime
    # Alte formate imagine gestionate de Pillow (tif, bmp, etc.)
    try:
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB")
            import io
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("utf-8"), "image/png"
    except Exception as exc:
        raise RuntimeError(
            f"Format fișier neacceptat ({ext}). Folosește JPG/PNG/WEBP/PDF. Detaliu: {exc}"
        )


def _is_pdf(path: str) -> bool:
    return os.path.splitext(path)[1].lower() == ".pdf"


def _pdf_extract_text(path: str, max_pages: int = 5) -> str:
    """Extrage text din primele `max_pages` pagini ale unui PDF (PyMuPDF).

    Returnează string gol dacă PDF-ul e scanat (fără text digital) sau la eroare.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return ""
    try:
        doc = fitz.open(path)
        texts: list[str] = []
        for i in range(min(doc.page_count, max_pages)):
            page = doc.load_page(i)
            t = page.get_text("text") or ""
            if t.strip():
                texts.append(t)
        doc.close()
        return "\n\n".join(texts).strip()
    except Exception as e:
        print(f"[ai_client] _pdf_extract_text eșuat: {e}")
        return ""


def _pdf_first_page_to_image_b64(path: str, dpi: int = 200) -> tuple[str, str]:
    """Convertește prima pagină PDF într-o imagine PNG base64.

    Folosit când Gemini nu poate primi PDF direct sau ca fallback.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as e:
        raise RuntimeError(
            "PyMuPDF (fitz) nu este instalat. Rulează: pip install PyMuPDF"
        ) from e
    doc = fitz.open(path)
    if doc.page_count == 0:
        doc.close()
        raise RuntimeError("PDF-ul nu conține pagini.")
    page = doc.load_page(0)
    pix = page.get_pixmap(dpi=dpi)
    png_bytes = pix.tobytes("png")
    doc.close()
    return base64.b64encode(png_bytes).decode("utf-8"), "image/png"


# ============ ASYNC CORE ============

async def _extract_async(
    file_path: str,
    document_type: str,
    force_image: bool = False,
) -> dict[str, Any]:
    """Call Gemini 3 Flash cu imagine (JPG/PNG/etc) sau PDF direct.

    Args:
        file_path: calea către fișier (imagine sau PDF)
        document_type: tipul documentului
        force_image: dacă True și e PDF → convertește la imagine înainte (fallback)
    """
    from emergentintegrations.llm.chat import (
        FileContentWithMimeType,
        ImageContent,
        LlmChat,
        UserMessage,
    )

    if not EMERGENT_KEY:
        raise RuntimeError("EMERGENT_LLM_KEY nu este configurată în .env")

    if document_type not in PROMPTS:
        raise ValueError(f"Tip document invalid: {document_type}")

    chat = LlmChat(
        api_key=EMERGENT_KEY,
        session_id=f"scan-{uuid.uuid4().hex[:8]}",
        system_message=PROMPTS[document_type],
    ).with_model("gemini", "gemini-3-flash-preview")

    # Construim contents în funcție de tipul de fișier
    if _is_pdf(file_path) and not force_image:
        # Trimitem PDF-ul direct la Gemini (funcționează și pt text-based, și scanate)
        pdf_content = FileContentWithMimeType(
            file_path=file_path,
            mime_type="application/pdf",
        )
        file_contents = [pdf_content]
    else:
        # Imagine: comprimăm dacă e cazul
        if _is_pdf(file_path):
            b64, mime = _pdf_first_page_to_image_b64(file_path)
        else:
            b64, mime = _file_to_base64(file_path)
            if len(b64) > 6 * 1024 * 1024:
                b64, mime = _compress_and_encode(file_path)
        image = ImageContent(image_base64=b64)
        file_contents = [image]

    msg = UserMessage(
        text="Extrage câmpurile din acest document conform schemei JSON specificate.",
        file_contents=file_contents,
    )

    response = await chat.send_message(msg)
    raw = _clean_json(response if isinstance(response, str) else str(response))

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise RuntimeError(f"Gemini nu a returnat JSON valid. Răspuns: {raw[:400]}")
        data = json.loads(m.group(0))

    return data


# ============ EMERGENT (rezervă, cheie universală) ============

def _extract_emergent_sync(file_path: str, document_type: str) -> dict[str, Any]:
    """Extrage cu Emergent LLM Key (emergentintegrations). Ridică excepție la eșec."""
    loop = asyncio.new_event_loop()
    try:
        try:
            result = loop.run_until_complete(
                _extract_async(file_path, document_type)
            )
        except Exception as e_first:
            # Dacă a eșuat trimiterea PDF direct, încearcă ca imagine (fallback)
            if _is_pdf(file_path):
                result = loop.run_until_complete(
                    _extract_async(file_path, document_type, force_image=True)
                )
            else:
                raise e_first
        result.setdefault("engine", "gemini")
        return result
    finally:
        loop.close()


# ============ STARE MOTOR ============

def active_engine_status() -> dict[str, Any]:
    """Descrie motorul activ pentru indicatorul din interfață.

    Returnează {"engine": "google"|"emergent"|"offline", "label": str, "model": str}
    """
    try:
        from . import config as ai_config
        if ai_config.get_mode() == ai_config.MODE_OFFLINE:
            return {"engine": "offline", "label": "Mod offline", "model": ""}
        if ai_config.get_api_key():
            model = ai_config.get_model()
            suffix = "" if model == ai_config.MODEL_AUTO else f" · {model}"
            return {"engine": "google", "label": f"AI activ (Gemini{suffix})", "model": model}
    except Exception:
        pass
    if EMERGENT_KEY:
        return {"engine": "emergent", "label": "AI activ (Gemini · cheie universală)",
                "model": "gemini-3-flash-preview"}
    return {"engine": "offline", "label": "Mod offline", "model": ""}


# ============ SYNC WRAPPER (pentru PySide6) ============

def extract_document_sync(file_path: str, document_type: str,
                          prefer_offline: bool = False) -> dict[str, Any]:
    """Extrage date dintr-un document. Sincronic, apelabil dintr-un thread worker.

    Ordinea motoarelor:
        1. Dacă prefer_offline=True sau modul din config e „offline" → Tesseract direct
        2. Google Gemini cu cheia proprie din config (`ai_config.get_api_key()`)
        3. Emergent LLM Key (dacă e prezent în .env) ca rezervă
        4. Tesseract offline ca ultimă rezervă

    Rezultat include cheia "engine" = "gemini" | "tesseract".
    """
    from . import config as ai_config

    # 1. Modul offline explicit sau setat din interfață
    if prefer_offline or ai_config.get_mode() == ai_config.MODE_OFFLINE:
        from .offline_ocr import extract_document_offline
        result = extract_document_offline(file_path, document_type)
        result.setdefault("engine", "tesseract")
        return result

    ai_error: Optional[Exception] = None

    # 2. Google Gemini — cheia proprie a utilizatorului
    google_key = ai_config.get_api_key()
    if google_key:
        try:
            from .google_engine import extract_document as _g_extract
            result = _g_extract(
                google_key, file_path, document_type, model=ai_config.get_model()
            )
            result.setdefault("engine", "gemini")
            return result
        except Exception as e:
            ai_error = e
            print(f"[ai_client] Gemini (cheie proprie) a eșuat: "
                  f"{ai_config.mask_key(google_key)} → {e}")

    # 3. Emergent LLM Key (rezervă)
    if EMERGENT_KEY:
        try:
            return _extract_emergent_sync(file_path, document_type)
        except Exception as e:
            ai_error = e
            print(f"[ai_client] Emergent LLM Key a eșuat, trec pe offline: {e}")

    # 4. Fallback la Tesseract
    try:
        from .offline_ocr import extract_document_offline, tesseract_available
        if not tesseract_available():
            if ai_error:
                raise RuntimeError(
                    f"Gemini eșuat ({ai_error}) și Tesseract nu e instalat. "
                    "Instalează cu: apt-get install tesseract-ocr tesseract-ocr-ron"
                )
            raise RuntimeError(
                "Nici Gemini (fără cheie/internet), nici Tesseract nu sunt disponibile. "
                "Adaugă o cheie din ⚙️ Setări AI sau instalează Tesseract."
            )
        result = extract_document_offline(file_path, document_type)
        result.setdefault("engine", "tesseract")
        if ai_error:
            result["_gemini_error"] = str(ai_error)
        return result
    except Exception as e:
        if ai_error:
            raise RuntimeError(
                f"Ambele engine-uri au eșuat. Gemini: {ai_error} | Tesseract: {e}"
            ) from e
        raise


def _compress_and_encode(path: str) -> tuple[str, str]:
    """Recomprima imagine la max 1600px lățime, calitate JPEG 82."""
    from PIL import Image
    import io
    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        if w > 1600:
            new_h = int(h * 1600 / w)
            im = im.resize((1600, new_h), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=82, optimize=True)
        return base64.b64encode(buf.getvalue()).decode("utf-8"), "image/jpeg"
