"""Feedback learning — colectează corecțiile utilizatorului și le aplică la scanări viitoare.

Fără antrenare completă Tesseract (necesită sute de sample-uri + ore compute), aplicăm o
strategie pragmatică:

1. Când user-ul CONFIRMĂ / EDITEAZĂ câmpurile extrase, salvăm perechea:
   (raw_ocr_text, câmpul, valoarea_greșită_extrasă, valoarea_corectă_editată)

2. Construim un DICȚIONAR DE CORECȚII local (~/.local/share/Cimitir/ai_training/corrections.json)
   care mapează:
     - patternuri OCR → valori corecte (ex: "1I5O315120025" → "1150315120025")
     - variații de etichete OCR → forma canonică (ex: "N u m e" → "Nume")

3. La fiecare nouă scanare, aplicăm corecțiile PE TEXTUL OCR BRUT înainte de parsare, plus
   pe câmpuri individuale după extragere.

Rezultat: fiecare document scanat + confirmat CREȘTE precizia la scanările următoare.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# Storage location
try:
    import database as _db
    _DATA_DIR = _db.DATA_DIR
except Exception:
    _DATA_DIR = os.path.expanduser("~/.local/share/Cimitir")

TRAIN_DIR = Path(_DATA_DIR) / "ai_training"
TRAIN_DIR.mkdir(parents=True, exist_ok=True)

CORRECTIONS_FILE = TRAIN_DIR / "corrections.json"
SAMPLES_FILE = TRAIN_DIR / "samples.jsonl"
STATS_FILE = TRAIN_DIR / "stats.json"


# ============ LOAD / SAVE ============

def _load_corrections() -> dict:
    if not CORRECTIONS_FILE.exists():
        return {
            "text_replacements": {},         # substring → replacement (pe OCR raw)
            "field_replacements": {},        # {field: {wrong: correct}}
            "cnp_patterns": {},              # CNP corectat: OCR → real
        }
    try:
        return json.loads(CORRECTIONS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"text_replacements": {}, "field_replacements": {}, "cnp_patterns": {}}


def _save_corrections(data: dict) -> None:
    tmp = CORRECTIONS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CORRECTIONS_FILE)


def _load_stats() -> dict:
    if not STATS_FILE.exists():
        return {
            "total_scans": 0,
            "total_corrections": 0,
            "per_field_corrections": {},
            "auto_corrections_applied": 0,
        }
    try:
        return json.loads(STATS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"total_scans": 0, "total_corrections": 0, "per_field_corrections": {}, "auto_corrections_applied": 0}


def _save_stats(stats: dict) -> None:
    STATS_FILE.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")


# ============ RECORD ============

def record_correction(
    raw_ocr_text: str,
    extracted_fields: dict[str, str],
    corrected_fields: dict[str, str],
    document_type: str,
) -> int:
    """Compară câmpurile extrase automat cu cele confirmate/editate.
    Salvează diferențele ca corecții pentru scanări viitoare.

    Returnează numărul de corecții noi înregistrate.
    """
    stats = _load_stats()
    stats["total_scans"] = stats.get("total_scans", 0) + 1

    corrections = _load_corrections()
    field_reps = corrections.setdefault("field_replacements", {})
    text_reps = corrections.setdefault("text_replacements", {})
    cnp_pats = corrections.setdefault("cnp_patterns", {})

    new_count = 0

    for key, real in corrected_fields.items():
        real = (real or "").strip()
        extracted = (extracted_fields.get(key, "") or "").strip()

        if not real or real == extracted:
            continue

        # Salvăm corecția per câmp
        if key not in field_reps:
            field_reps[key] = {}
        if extracted:  # avem o valoare greșită → mapare
            field_reps[key][extracted] = real
        new_count += 1

        # Statistici
        stats.setdefault("per_field_corrections", {})
        stats["per_field_corrections"][key] = stats["per_field_corrections"].get(key, 0) + 1
        stats["total_corrections"] = stats.get("total_corrections", 0) + 1

        # Corecții text-level (pentru câmpuri unde greșeala OCR e la nivel de caracter)
        # Ex: extracted="POPFSCU", real="POPESCU" → adăugăm "POPFSCU" → "POPESCU" text-level
        if extracted and abs(len(real) - len(extracted)) <= 2 and len(real) >= 3:
            text_reps[extracted] = real

        # CNP-uri
        if key == "cnp" and len(real) == 13 and real.isdigit():
            if extracted and extracted != real:
                cnp_pats[extracted] = real

    # Salvăm și sample-ul pentru training viitor (linii JSONL, ușor de citit incremental)
    try:
        with open(SAMPLES_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "timestamp": datetime.now().isoformat(),
                "document_type": document_type,
                "raw_ocr": raw_ocr_text[:5000] if raw_ocr_text else "",
                "extracted": extracted_fields,
                "corrected": corrected_fields,
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass

    _save_corrections(corrections)
    _save_stats(stats)
    return new_count


# ============ APPLY ============

def apply_text_corrections(raw_ocr: str) -> str:
    """Aplică corecțiile text-level pe raw OCR înainte de parsare (best-effort)."""
    if not raw_ocr:
        return raw_ocr
    corrections = _load_corrections()
    text_reps = corrections.get("text_replacements", {})
    if not text_reps:
        return raw_ocr

    out = raw_ocr
    for wrong, right in text_reps.items():
        if not wrong or len(wrong) < 3:
            continue
        # replace numai ca cuvânt whole (evită coliziuni)
        try:
            out = re.sub(rf"\b{re.escape(wrong)}\b", right, out)
        except re.error:
            continue
    return out


def apply_field_corrections(fields: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Aplică corecții pe câmpurile deja extrase.

    Returnează (fields_corectate, număr_corecții_aplicate).
    """
    corrections = _load_corrections()
    field_reps = corrections.get("field_replacements", {})
    cnp_pats = corrections.get("cnp_patterns", {})

    n = 0
    for key, val in list(fields.items()):
        if isinstance(val, dict):
            raw_val = val.get("value", "")
        else:
            raw_val = val

        if not raw_val:
            continue

        # 1. Corecție directă per câmp
        if key in field_reps and raw_val in field_reps[key]:
            new_val = field_reps[key][raw_val]
            if isinstance(val, dict):
                fields[key]["value"] = new_val
                # ridicăm confidence-ul pentru că vine din feedback loop
                fields[key]["confidence"] = min(1.0, val.get("confidence", 0.5) + 0.15)
            else:
                fields[key] = new_val
            n += 1
            continue

        # 2. CNP fuzzy — dacă avem pattern OCR învățat
        if key == "cnp" and raw_val in cnp_pats:
            new_val = cnp_pats[raw_val]
            if isinstance(val, dict):
                fields[key]["value"] = new_val
                fields[key]["confidence"] = min(1.0, val.get("confidence", 0.5) + 0.20)
            else:
                fields[key] = new_val
            n += 1

    if n > 0:
        stats = _load_stats()
        stats["auto_corrections_applied"] = stats.get("auto_corrections_applied", 0) + n
        _save_stats(stats)

    return fields, n


# ============ QUERY ============

def get_stats() -> dict:
    """Statistici pentru dashboard: câte scanări, corecții, per câmp."""
    return _load_stats()


def get_corrections_count() -> dict:
    corrections = _load_corrections()
    return {
        "text_replacements": len(corrections.get("text_replacements", {})),
        "cnp_patterns": len(corrections.get("cnp_patterns", {})),
        "field_replacements": sum(
            len(v) for v in corrections.get("field_replacements", {}).values()
        ),
    }


def reset_learning() -> None:
    """Șterge toate corecțiile învățate (revine la starea inițială)."""
    for f in [CORRECTIONS_FILE, SAMPLES_FILE, STATS_FILE]:
        try:
            f.unlink()
        except FileNotFoundError:
            pass


def export_samples_for_training(output_dir: str) -> int:
    """Exportă sample-urile colectate în format tesstrain-ready.
    Fiecare sample devine: image.tif + image.gt.txt (imaginea trebuie deja salvată separat).

    În versiunea curentă exportăm doar text-level pentru inspecție manuală;
    utilizatorul poate rula ulterior scriptul `training.py` pe imaginile reale.

    Returnează numărul de sample-uri exportate.
    """
    if not SAMPLES_FILE.exists():
        return 0
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(SAMPLES_FILE, "r", encoding="utf-8") as f:
        for line in f:
            try:
                sample = json.loads(line)
            except Exception:
                continue
            sample_id = sample["timestamp"].replace(":", "").replace(".", "")[:19]
            gt_path = out / f"{sample_id}.gt.txt"
            gt_content = "\n".join([
                f"{k}: {v}" for k, v in sample.get("corrected", {}).items() if v
            ])
            gt_path.write_text(gt_content, encoding="utf-8")
            n += 1
    return n
