"""Preprocesare imagini pentru creșterea preciziei OCR (Tesseract).

Pipeline:
  1. Auto-rotate (Tesseract OSD)
  2. Convert to grayscale
  3. Deskew (Hough-line detection cu OpenCV)
  4. Upscale la min 2000 px lățime dacă e mai mic
  5. CLAHE contrast enhancement (histogram equalization adaptiv)
  6. Denoise (bilateral filter — păstrează muchiile)
  7. Adaptive threshold (Sauvola / Otsu)
  8. Sharpen ușor

Returnează un PIL Image nou (RGB), gata de OCR.
"""
from __future__ import annotations

import io
import os
from typing import Optional

import numpy as np
from PIL import Image, ImageEnhance, ImageOps


# ============ HELPERS ============

def _pil_to_cv(img: Image.Image) -> np.ndarray:
    """PIL RGB/L → cv2 BGR/GRAY numpy array."""
    import cv2
    arr = np.array(img)
    if img.mode == "L":
        return arr
    if img.mode == "RGB":
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    if img.mode == "RGBA":
        return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
    return arr


def _cv_to_pil(arr: np.ndarray) -> Image.Image:
    """cv2 array → PIL Image."""
    import cv2
    if arr.ndim == 2:
        return Image.fromarray(arr, mode="L")
    return Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB), mode="RGB")


# ============ STAGES ============

def auto_rotate_osd(img: Image.Image) -> Image.Image:
    """Detectează rotația (0/90/180/270) folosind Tesseract OSD."""
    try:
        import pytesseract
        osd = pytesseract.image_to_osd(img, config="--psm 0", output_type=pytesseract.Output.DICT)
        rot = int(osd.get("rotate", 0))
        if rot and rot % 360 != 0:
            return img.rotate(-rot, expand=True, resample=Image.BICUBIC)
    except Exception:
        pass
    return img


def deskew_hough(img: Image.Image, max_angle: float = 8.0) -> Image.Image:
    """Corecție skew mic (±8°) folosind Hough-line detection."""
    import cv2
    gray = _pil_to_cv(img.convert("L"))
    # binar pentru detecție linii
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=100,
                            minLineLength=int(gray.shape[1] * 0.4), maxLineGap=20)
    if lines is None:
        return img

    angles = []
    for x1, y1, x2, y2 in lines[:, 0]:
        if x2 == x1:
            continue
        angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        if -max_angle <= angle <= max_angle:
            angles.append(angle)
    if not angles:
        return img

    skew = float(np.median(angles))
    if abs(skew) < 0.3:  # ignoră skew imperceptibil
        return img
    return img.rotate(-skew, expand=True, resample=Image.BICUBIC, fillcolor=(255, 255, 255))


def upscale_if_small(img: Image.Image, target_width: int = 2000) -> Image.Image:
    """Upscale la target_width dacă imaginea e mai mică — OCR e mult mai bun pe rezoluție mare."""
    w, h = img.size
    if w >= target_width:
        return img
    ratio = target_width / w
    new_h = int(h * ratio)
    return img.resize((target_width, new_h), Image.LANCZOS)


def clahe_contrast(img: Image.Image, clip: float = 2.5, tile: int = 8) -> Image.Image:
    """CLAHE adaptiv — mult mai bun ca autocontrast simplu pe scanuri neomogene."""
    import cv2
    gray = _pil_to_cv(img.convert("L"))
    clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(tile, tile))
    out = clahe.apply(gray)
    return _cv_to_pil(out)


def denoise(img: Image.Image) -> Image.Image:
    """Bilateral filter — reduce zgomot păstrând muchiile de text."""
    import cv2
    arr = _pil_to_cv(img.convert("L"))
    filtered = cv2.bilateralFilter(arr, d=5, sigmaColor=25, sigmaSpace=25)
    return _cv_to_pil(filtered)


def sharpen(img: Image.Image, amount: float = 1.3) -> Image.Image:
    """Sharpen ușor cu ImageEnhance (mai controlat decât UnsharpMask)."""
    return ImageEnhance.Sharpness(img).enhance(amount)


def adaptive_threshold(img: Image.Image, method: str = "sauvola") -> Image.Image:
    """Threshold adaptiv (Sauvola sau Otsu). Sauvola e mai bun pe iluminare neomogenă."""
    import cv2
    gray = _pil_to_cv(img.convert("L"))
    if method == "otsu":
        _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    else:
        # Sauvola aproximat via adaptiveThreshold (Gaussian, block 31, C=15)
        bw = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY,
            blockSize=31, C=15
        )
    return _cv_to_pil(bw)


# ============ PIPELINE ============

def preprocess_for_ocr(
    img: Image.Image,
    *,
    do_auto_rotate: bool = True,
    do_deskew: bool = True,
    do_upscale: bool = True,
    do_clahe: bool = True,
    do_denoise: bool = True,
    do_sharpen: bool = True,
    do_threshold: bool = False,  # threshold poate strica textul colorat/subțire → dezactivat implicit
) -> Image.Image:
    """Aplică pipeline-ul complet de preprocesare."""
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    if do_auto_rotate:
        img = auto_rotate_osd(img)
    if do_deskew:
        try:
            img = deskew_hough(img)
        except Exception:
            pass
    if do_upscale:
        img = upscale_if_small(img, target_width=2000)
    if do_clahe:
        img = clahe_contrast(img)
    if do_denoise:
        img = denoise(img)
    if do_sharpen:
        img = sharpen(img, amount=1.4)
    if do_threshold:
        img = adaptive_threshold(img, method="sauvola")
    return img


def preprocess_file(path: str, save_debug: bool = False) -> Image.Image:
    """Deschide fișierul și aplică pipeline. Dacă save_debug=True, salvează imaginea prelucrată."""
    with Image.open(path) as im:
        im.load()
        img = im.copy()
    processed = preprocess_for_ocr(img)
    if save_debug:
        debug_path = os.path.splitext(path)[0] + "_preprocessed.jpg"
        processed.convert("RGB").save(debug_path, "JPEG", quality=90)
    return processed
