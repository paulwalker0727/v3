"""Extragere AI din documente fotografiate (buletin, certificat de deces, adeverință de adresă) — versiunea de SERVER.

Portat din `desktop/ai_scanare/ai_client.py` (aceleași prompturi); fără Qt/Tesseract/setări desktop.
Motor, în ordine: (1) `emergentintegrations` + `EMERGENT_LLM_KEY` (Emergent); (2) SDK-ul oficial `google-genai` + `GEMINI_API_KEY`
(modelul din `GEMINI_MODEL`, implicit `gemini-3.8-flash`) — portabil, ex. Render; (3) niciunul => „indisponibil".
Cheile se citesc din mediul serverului și nu ajung niciodată la telefon.
Rezultatul este doar o PROPUNERE de date: nimic nu se salvează în entități fără confirmarea utilizatorului.
"""
from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Optional

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
EMERGENT_MODEL = "gemini-3-flash-preview"



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

    if not _emergent_key():
        raise RuntimeError("EMERGENT_LLM_KEY nu este configurată în .env")

    if document_type not in PROMPTS:
        raise ValueError(f"Tip document invalid: {document_type}")

    chat = LlmChat(
        api_key=_emergent_key(),
        session_id=f"scan-{uuid.uuid4().hex[:8]}",
        system_message=PROMPTS[document_type],
    ).with_model("gemini", EMERGENT_MODEL)

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


# ============ MOTOR GEMINI DIRECT (google-genai) ============

def _gemini_key() -> str:
    return (os.environ.get("GEMINI_API_KEY") or "").strip()


def _gemini_model() -> str:
    return (os.environ.get("GEMINI_MODEL") or "").strip() or DEFAULT_GEMINI_MODEL


def _gemini_client():
    from google import genai  # import lazy: serverul pornește și fără pachet
    return genai.Client(api_key=_gemini_key())


def _gemini_part(file_path: str, force_image: bool):
    """Part pentru Gemini: PDF direct (bytes inline) sau imagine (comprimată dacă e mare)."""
    from google.genai import types
    if _is_pdf(file_path) and not force_image:
        with open(file_path, "rb") as f:
            return types.Part.from_bytes(data=f.read(), mime_type="application/pdf")
    if _is_pdf(file_path):
        b64, mime = _pdf_first_page_to_image_b64(file_path)
    else:
        b64, mime = _file_to_base64(file_path)
        if len(b64) > 6 * 1024 * 1024:
            b64, mime = _compress_and_encode(file_path)
    return types.Part.from_bytes(data=base64.b64decode(b64), mime_type=mime)


def _parse_json_response(text: str) -> dict[str, Any]:
    raw = _clean_json(text)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise RuntimeError(f"Gemini nu a returnat JSON valid. Răspuns: {raw[:400]}")
        return json.loads(m.group(0))


def _extract_gemini(file_path: str, document_type: str, force_image: bool = False) -> dict[str, Any]:
    """Gemini direct prin `google-genai` (generate_content: imagine/PDF inline + system_instruction)."""
    from google.genai import types

    if document_type not in PROMPTS:
        raise ValueError(f"Tip document invalid: {document_type}")
    client = _gemini_client()
    response = client.models.generate_content(
        model=_gemini_model(),
        contents=[
            _gemini_part(file_path, force_image),
            "Extrage câmpurile din acest document conform schemei JSON specificate.",
        ],
        config=types.GenerateContentConfig(
            system_instruction=PROMPTS[document_type],
            temperature=0,
            response_mime_type="application/json",
        ),
    )
    return _parse_json_response(getattr(response, "text", "") or "")


# ============ API server ============

def _emergent_key() -> str:
    return (os.environ.get("EMERGENT_LLM_KEY") or "").strip()


def _emergent_importable() -> bool:
    try:
        return importlib.util.find_spec("emergentintegrations") is not None
    except (ImportError, ValueError):
        return False


def _gemini_importable() -> bool:
    try:
        return importlib.util.find_spec("google.genai") is not None
    except (ImportError, ValueError):
        return False


def _active_engine() -> str:
    """'emergent' | 'gemini' | '' (indisponibil)."""
    if _emergent_key() and _emergent_importable():
        return "emergent"
    if _gemini_key() and _gemini_importable():
        return "gemini"
    return ""


def engine_status() -> dict[str, Any]:
    engine = _active_engine()
    if engine == "emergent":
        return {"available": True, "engine": "gemini", "model": EMERGENT_MODEL, "provider": "emergent"}
    if engine == "gemini":
        return {"available": True, "engine": "gemini", "model": _gemini_model(), "provider": "google-genai"}
    return {"available": False, "engine": "none", "model": ""}


def extract_document(file_path: str, document_type: str) -> dict[str, Any]:
    """Sincron (se rulează în thread-pool). Ridică RuntimeError/ValueError cu mesaj clar la eșec."""
    if document_type not in PROMPTS:
        raise ValueError(f"Tip document invalid: {document_type}")
    engine = _active_engine()
    if not engine:
        raise RuntimeError("Extragerea automată nu este configurată pe server (lipsește cheia AI).")

    if engine == "gemini":
        try:
            result = _extract_gemini(file_path, document_type)
        except Exception:
            if _is_pdf(file_path):          # fallback: PDF -> imagine
                result = _extract_gemini(file_path, document_type, force_image=True)
            else:
                raise
        result.setdefault("engine", "gemini")
        return result

    loop = asyncio.new_event_loop()
    try:
        try:
            result = loop.run_until_complete(_extract_async(file_path, document_type))
        except Exception:
            if _is_pdf(file_path):          # fallback: PDF -> imagine
                result = loop.run_until_complete(_extract_async(file_path, document_type, force_image=True))
            else:
                raise
        result.setdefault("engine", "gemini")
        return result
    finally:
        loop.close()
