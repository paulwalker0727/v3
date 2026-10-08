"""Motor Google Gemini (cheie proprie `AQ.`/`AIza`) prin SDK-ul oficial `google-genai`.

Responsabilități:
  * validarea formei cheii
  * listarea modelelor disponibile pe cont
  * auto-selectarea primului model care răspunde (fără erori 404/429)
  * extragerea câmpurilor dintr-o imagine / PDF, cu răspuns JSON

Cheia se ia din `ai_scanare.config` (fallback pe variabile de mediu). Nu se
folosește niciun EMERGENT_LLM_KEY aici — acela rămâne în `ai_client` ca rezervă.
"""
from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ordinea contează: primul model vizibil pentru cheie care răspunde la un test
# minim este cel ales automat.
PREFERRED_MODELS: List[str] = [
    "gemini-flash-latest",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]

_SUPPORTED_MIME = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "image/heic",
    "image/heif",
}


class GeminiError(RuntimeError):
    """Eroare prietenoasă din motorul Gemini (mesaj gata de afișat)."""


# ============ UTILS ============

def validate_key_shape(api_key: str) -> str:
    key = (api_key or "").strip()
    if not key:
        raise GeminiError("Introdu o cheie API Gemini.")
    if not (key.startswith("AQ.") or key.startswith("AIza")):
        raise GeminiError(
            "Cheia nu pare o cheie AI Studio (aștept prefix AQ. sau AIza)."
        )
    return key


def model_id(name: str) -> str:
    """`models/foo` -> `foo` (generarea vrea forma scurtă)."""
    return name.split("/")[-1] if name else name


def _clean_json(text: str) -> str:
    text = (text or "").strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return text


def _client(api_key: str):
    from google import genai  # import lazy: nu blochează pornirea dacă lipsește
    return genai.Client(api_key=validate_key_shape(api_key))


# ============ MODELE ============

def available_generate_models(client) -> List[str]:
    """Modelele vizibile pe cont care suportă generateContent."""
    result: List[str] = []
    for model in client.models.list():
        actions = getattr(model, "supported_actions", None) or []
        if not actions:
            actions = getattr(model, "supported_generation_methods", None) or []
        if "generateContent" in actions or "generate_content" in actions:
            name = getattr(model, "name", "")
            if name:
                mid = model_id(name)
                if mid not in result:
                    result.append(mid)
    return result


def _ordered_candidates(visible: List[str], prefer: Optional[str] = None) -> List[str]:
    ordered: List[str] = []
    # 1. modelul cerut explicit (dacă e vizibil)
    if prefer and prefer not in ("auto", "", None):
        p = model_id(prefer)
        if p in visible:
            ordered.append(p)
    # 2. lista de preferințe
    for m in PREFERRED_MODELS:
        if m in visible and m not in ordered:
            ordered.append(m)
    # 3. familii (ex: gemini-flash* dacă alias-ul exact nu apare)
    for m in PREFERRED_MODELS:
        family = m.replace("-latest", "")
        for v in visible:
            if v.startswith(family) and v not in ordered:
                ordered.append(v)
    # 4. orice altceva vizibil, ca ultimă instanță
    for v in visible:
        if v not in ordered:
            ordered.append(v)
    return ordered


def choose_working_model(client, prefer: Optional[str] = None) -> str:
    """Alege primul model care trece un test minim (`OK`)."""
    from google.genai import types
    visible = available_generate_models(client)
    if not visible:
        raise GeminiError("Cheia nu expune niciun model cu generateContent.")

    errors: List[str] = []
    for name in _ordered_candidates(visible, prefer):
        try:
            resp = client.models.generate_content(
                model=name,
                contents="Reply with exactly OK.",
                config=types.GenerateContentConfig(temperature=0, max_output_tokens=8),
            )
            if (getattr(resp, "text", "") or "").strip():
                return name
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {type(exc).__name__}")

    raise GeminiError(
        "Niciun model preferat nu a răspuns. "
        + ("Detalii: " + "; ".join(errors[:4]) if errors else "")
    )


def list_available_models(api_key: str) -> List[str]:
    """Returnează modelele generative vizibile, ordonate după preferință."""
    client = _client(api_key)
    visible = available_generate_models(client)
    return _ordered_candidates(visible)


def test_key(api_key: str) -> Dict[str, Any]:
    """Apel scurt de validare. Returnează {ok, model, message}."""
    try:
        client = _client(api_key)
        model = choose_working_model(client)
        return {"ok": True, "model": model, "message": f"Cheie validă · model {model}"}
    except GeminiError as e:
        return {"ok": False, "model": None, "message": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "model": None, "message": _friendly_error(e)}


def _friendly_error(exc: Exception) -> str:
    msg = str(exc)
    low = msg.lower()
    if "api key not valid" in low or "api_key_invalid" in low or "invalid" in low and "key" in low:
        return "Cheie invalidă sau revocată."
    if "quota" in low or "429" in low or "resource_exhausted" in low:
        return "Cotă/limită depășită (429). Încearcă mai târziu sau alt model."
    if "permission" in low or "403" in low:
        return "Cheia nu are permisiune pentru Gemini API (403)."
    if "not found" in low or "404" in low:
        return "Model indisponibil (404). Se va alege automat altul."
    if "deadline" in low or "timeout" in low or "connection" in low or "network" in low:
        return "Fără conexiune la Gemini (verifică internetul)."
    return f"Eroare Gemini: {type(exc).__name__}: {msg[:200]}"


# ============ EXTRAGERE DOCUMENT ============

def _guess_mime(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or ""
    if mime in _SUPPORTED_MIME:
        return mime
    ext = path.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    if ext == ".webp":
        return "image/webp"
    if ext == ".pdf":
        return "application/pdf"
    return mime or "application/octet-stream"


def extract_document(
    api_key: str,
    file_path: str,
    document_type: str,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Trimite imaginea/PDF la Gemini și întoarce dict-ul de câmpuri (JSON parsat).

    `model` poate fi None/"auto" (auto-selectare) sau un id de model explicit.
    """
    from google.genai import types
    from .ai_client import PROMPTS  # import lazy => fără circular import

    if document_type not in PROMPTS:
        raise GeminiError(f"Tip document invalid: {document_type}")

    path = Path(file_path)
    if not path.is_file():
        raise GeminiError(f"Fișier inexistent: {file_path}")

    mime = _guess_mime(path)
    if mime not in _SUPPORTED_MIME:
        raise GeminiError(f"Format neacceptat de Gemini: {mime or path.suffix}")

    client = _client(api_key)
    chosen = None if (model in (None, "", "auto")) else model_id(model)
    chosen = choose_working_model(client, prefer=chosen)

    uploaded = client.files.upload(file=str(path), config={"mime_type": mime})
    try:
        resp = client.models.generate_content(
            model=chosen,
            contents=[
                uploaded,
                "Extrage câmpurile din acest document conform schemei JSON "
                "specificate. Returnează DOAR JSON valid, fără markdown.",
            ],
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
                system_instruction=PROMPTS[document_type],
            ),
        )
    finally:
        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            pass

    raw = _clean_json(getattr(resp, "text", "") or "")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise GeminiError(f"Gemini nu a returnat JSON valid. Răspuns: {raw[:300]}")
        data = json.loads(m.group(0))

    data.setdefault("engine", "gemini")
    data["model"] = chosen
    return data


__all__ = [
    "PREFERRED_MODELS", "GeminiError",
    "validate_key_shape", "model_id",
    "available_generate_models", "choose_working_model",
    "list_available_models", "test_key", "extract_document",
]
