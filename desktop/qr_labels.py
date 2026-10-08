"""
qr_labels.py — Generare QR code + etichete pentru locurile de veci.

Trei moduri de output:
    1. PNG individual pentru un loc: `generate_qr_png(cod_loc, path)`
    2. Batch PNG-uri pentru mai multe locuri: `generate_qr_batch_pngs(cods, dir)`
    3. PDF pe pagină A4 cu grid de etichete: `generate_qr_labels_pdf(cods, path)`

URL-ul din QR este `<qr_base_url>/loc/<cod_loc>` — configurat în settings.
Etichetele conțin QR + text (codul locului + sector/parcela).
"""
from __future__ import annotations

import io
import os
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas as rl_canvas

import database as db
import pdf_reports as pr
from app_logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Font helper pentru PIL (diacritice RO în text)
# ---------------------------------------------------------------------------
def _pil_font(size: int) -> ImageFont.FreeTypeFont:
    """Găsește un font TrueType pentru diacritice RO. Fallback la default."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "fonts", "LiberationSans-Regular.ttf"),
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size=size)
            except Exception:
                logger.exception("Font %s failed to load", p)
    return ImageFont.load_default()


def _pil_font_bold(size: int) -> ImageFont.FreeTypeFont:
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "fonts", "LiberationSans-Bold.ttf"),
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size=size)
            except Exception:
                logger.exception("Font bold %s failed to load", p)
    return _pil_font(size)


# ---------------------------------------------------------------------------
# CORE — construire QR ca PIL Image
# ---------------------------------------------------------------------------
def _make_qr_pil(url: str, box_size: int = 10, border: int = 2) -> Image.Image:
    """Generează un PIL Image cu QR code din URL."""
    import qrcode
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(url)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


def _location_meta(cod_loc: str) -> dict:
    """Încarcă metadatele locului (pentru text pe etichetă)."""
    try:
        return db.load_loc_by_cod(cod_loc) or {}
    except Exception:
        logger.exception("Nu pot încărca metadata pentru %s", cod_loc)
        return {}


def _url_for_loc(cod_loc: str) -> str:
    """URL-ul care va fi codificat în QR (fără date personale)."""
    return pr._build_qr_url("loc", cod_loc)


# ---------------------------------------------------------------------------
# 1) PNG INDIVIDUAL cu QR + text (etichetă completă)
# ---------------------------------------------------------------------------
def make_qr_label_image(cod_loc: str,
                        label_size_px: Tuple[int, int] = (400, 500),
                        include_text: bool = True,
                        title: Optional[str] = None) -> Image.Image:
    """Creează o etichetă PIL: QR sus + text jos (cod, sector, parcela, rând)."""
    W, H = label_size_px
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)

    # margine + titlu (dacă e cazul)
    y = 10
    if title:
        font_title = _pil_font_bold(int(W * 0.055))
        bbox = draw.textbbox((0, 0), title, font=font_title)
        tw = bbox[2] - bbox[0]
        draw.text(((W - tw) // 2, y), title, fill="black", font=font_title)
        y += (bbox[3] - bbox[1]) + 12

    # QR — cât mai mare, dar cu spațiu pentru text jos
    text_area = int(H * 0.28) if include_text else 20
    qr_area = H - y - text_area - 10
    qr_side = min(W - 20, qr_area)

    qr = _make_qr_pil(_url_for_loc(cod_loc), box_size=10, border=2)
    qr = qr.resize((qr_side, qr_side), Image.NEAREST)
    img.paste(qr, ((W - qr_side) // 2, y))
    y += qr_side + 8

    # text sub QR
    if include_text:
        meta = _location_meta(cod_loc)
        font_code = _pil_font_bold(int(W * 0.06))
        font_meta = _pil_font(int(W * 0.038))

        # codul locului — mare, îngroșat
        code_text = cod_loc
        bbox = draw.textbbox((0, 0), code_text, font=font_code)
        tw = bbox[2] - bbox[0]
        draw.text(((W - tw) // 2, y), code_text, fill="black", font=font_code)
        y += (bbox[3] - bbox[1]) + 4

        # detalii sub cod
        details = []
        if meta.get("sector"):
            details.append(f"Sector {meta['sector']}")
        if meta.get("parcela"):
            details.append(f"Parcela {meta['parcela']}")
        if meta.get("rand"):
            details.append(f"Rând {meta['rand']}")
        if meta.get("numar"):
            details.append(f"Nr. {meta['numar']}")

        if details:
            details_text = "  ·  ".join(details)
            bbox = draw.textbbox((0, 0), details_text, font=font_meta)
            tw = bbox[2] - bbox[0]
            # dacă e prea lat, sparg pe 2 rânduri
            if tw > W - 20 and len(details) > 2:
                line1 = "  ·  ".join(details[:2])
                line2 = "  ·  ".join(details[2:])
                for line in (line1, line2):
                    b = draw.textbbox((0, 0), line, font=font_meta)
                    lw = b[2] - b[0]
                    draw.text(((W - lw) // 2, y), line, fill="#333333", font=font_meta)
                    y += (b[3] - b[1]) + 2
            else:
                draw.text(((W - tw) // 2, y), details_text, fill="#333333", font=font_meta)
                y += (bbox[3] - bbox[1]) + 2

    return img


def generate_qr_png(cod_loc: str, output_path: str,
                    label_size_px: Tuple[int, int] = (400, 500),
                    include_text: bool = True,
                    only_qr: bool = False) -> str:
    """
    Salvează PNG cu QR pentru un singur loc.

    Args:
        cod_loc: codul locului (ex: "A-01-05")
        output_path: calea fișierului de ieșire (.png)
        label_size_px: dimensiune în pixeli (lățime, înălțime)
        include_text: dacă adaugă text sub QR
        only_qr: dacă True, salvează DOAR QR-ul (fără text, fără cadru)

    Returns:
        calea completă a fișierului salvat
    """
    if only_qr:
        # doar QR simplu — pătrat, fără text
        side = max(label_size_px)
        img = _make_qr_pil(_url_for_loc(cod_loc), box_size=12, border=2)
        img = img.resize((side, side), Image.NEAREST)
    else:
        img = make_qr_label_image(cod_loc, label_size_px, include_text)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    img.save(output_path, format="PNG", dpi=(300, 300))
    logger.info("QR PNG salvat: %s (%s, only_qr=%s)", output_path, cod_loc, only_qr)
    return output_path


# ---------------------------------------------------------------------------
# 2) BATCH — un PNG per loc într-un folder
# ---------------------------------------------------------------------------
def generate_qr_batch_pngs(cod_locs: List[str],
                           output_dir: str,
                           label_size_px: Tuple[int, int] = (400, 500),
                           include_text: bool = True,
                           only_qr: bool = False) -> List[str]:
    """
    Salvează câte un PNG pentru fiecare loc dintr-o listă.
    Numele fișierului: `qr_<cod_loc>.png` (cu caractere sigure).
    """
    os.makedirs(output_dir, exist_ok=True)
    paths: List[str] = []
    for cod in cod_locs:
        safe = str(cod).replace("/", "_").replace(" ", "_")
        out = os.path.join(output_dir, f"qr_{safe}.png")
        try:
            generate_qr_png(cod, out, label_size_px, include_text, only_qr)
            paths.append(out)
        except Exception:
            logger.exception("Nu am putut genera QR pentru %s", cod)
    logger.info("Batch QR: %d/%d generate în %s", len(paths), len(cod_locs), output_dir)
    return paths


# ---------------------------------------------------------------------------
# 3) PDF pe A4 — grid de etichete gata de tipărit
# ---------------------------------------------------------------------------
# Preseturi comune (Avery-style) — dimensiuni în mm
LABEL_PRESETS = {
    # nume,        cols, rows, label_w, label_h,  margin_left, margin_top,  gap_x, gap_y
    "2x5 (10/pag, 105×59)": (2, 5, 105.0, 59.0, 0.0, 0.0, 0.0, 0.0),
    "3x8 (24/pag, 70×36)":  (3, 8, 70.0, 36.0, 0.0, 8.5, 0.0, 0.0),
    "3x7 (21/pag, 70×42)":  (3, 7, 70.0, 42.0, 0.0, 4.0, 0.0, 0.0),
    "4x10 (40/pag, 48×25)": (4, 10, 48.0, 25.4, 8.0, 21.5, 0.0, 0.0),
    "5x13 (65/pag, 38×21)": (5, 13, 38.1, 21.2, 4.8, 13.0, 2.5, 0.0),
}


def generate_qr_labels_pdf(cod_locs: List[str],
                           output_path: str,
                           preset: str = "3x8 (24/pag, 70×36)",
                           include_text: bool = True,
                           border: bool = True) -> str:
    """
    Generează PDF A4 cu grid de etichete QR (gata de tipărit pe coli adezive).

    Args:
        cod_locs: lista codurilor
        output_path: calea PDF de ieșire
        preset: cheia din LABEL_PRESETS
        include_text: adaugă text lângă QR (cod, sector)
        border: desenează chenar în jurul fiecărei etichete (util pentru tăiat manual)
    """
    if preset not in LABEL_PRESETS:
        raise ValueError(f"Preset necunoscut: {preset}. "
                         f"Disponibile: {list(LABEL_PRESETS.keys())}")
    cols, rows, lw, lh, ml, mt, gx, gy = LABEL_PRESETS[preset]

    c = rl_canvas.Canvas(output_path, pagesize=A4)
    page_w, page_h = A4
    per_page = cols * rows

    total = len(cod_locs)
    if total == 0:
        raise ValueError("Lista de coduri e goală.")

    for idx, cod in enumerate(cod_locs):
        page_idx = idx // per_page
        idx_on_page = idx % per_page
        col = idx_on_page % cols
        row = idx_on_page // cols

        if idx > 0 and idx_on_page == 0:
            c.showPage()

        # coordonate top-left ale etichetei (în mm), reportlab e origin bottom-left
        x_mm = ml + col * (lw + gx)
        y_mm_top = mt + row * (lh + gy)
        x = x_mm * mm
        y_top = page_h - y_mm_top * mm
        y_bot = y_top - lh * mm

        if border:
            c.setStrokeColor(colors.HexColor("#c8ccd4"))
            c.setLineWidth(0.3)
            c.rect(x, y_bot, lw * mm, lh * mm, stroke=1, fill=0)

        _draw_label_at(c, x, y_bot, lw * mm, lh * mm, cod, include_text)

    c.save()
    logger.info("PDF etichete QR generat: %s (%d etichete, preset=%s)",
                output_path, total, preset)
    return output_path


def _draw_label_at(c: rl_canvas.Canvas, x: float, y: float,
                   w: float, h: float, cod_loc: str, include_text: bool) -> None:
    """Desenează o etichetă QR (+ text opțional) într-o cutie dată."""
    pad = 2 * mm

    # QR ca image
    qr = _make_qr_pil(_url_for_loc(cod_loc), box_size=8, border=1)
    buf = io.BytesIO()
    qr.save(buf, format="PNG")
    buf.seek(0)

    from reportlab.lib.utils import ImageReader
    qr_reader = ImageReader(buf)

    if not include_text:
        # QR centrat pe toată eticheta
        side = min(w, h) - 2 * pad
        c.drawImage(qr_reader,
                    x + (w - side) / 2, y + (h - side) / 2,
                    width=side, height=side, mask="auto")
        return

    # cu text: QR în stânga, text în dreapta
    qr_side = h - 2 * pad
    if qr_side > w * 0.55:
        qr_side = w * 0.55 - pad
    c.drawImage(qr_reader, x + pad, y + (h - qr_side) / 2,
                width=qr_side, height=qr_side, mask="auto")

    # zonă text
    tx = x + pad + qr_side + pad
    tw = w - (tx - x) - pad

    meta = _location_meta(cod_loc)

    # code (bold, mai mare)
    font_code = pr.FONT_B
    font_meta = pr.FONT
    code_size = min(11, max(8, int(h / mm * 0.35)))
    meta_size = max(6, code_size - 3)

    c.setFillColor(colors.black)
    c.setFont(font_code, code_size)
    c.drawString(tx, y + h - pad - code_size, cod_loc)

    # linii de metadate
    lines: List[str] = []
    if meta.get("sector"):
        lines.append(f"Sector {meta['sector']}")
    if meta.get("parcela"):
        lines.append(f"Parc. {meta['parcela']}")
    if meta.get("rand"):
        lines.append(f"Rând {meta['rand']}")
    if meta.get("numar"):
        lines.append(f"Nr. {meta['numar']}")

    c.setFillColor(colors.HexColor("#333333"))
    c.setFont(font_meta, meta_size)
    line_h = meta_size + 1
    ty = y + h - pad - code_size - line_h - 1
    for line in lines[:4]:
        c.drawString(tx, ty, line)
        ty -= line_h
        if ty < y + pad:
            break


# ---------------------------------------------------------------------------
# CLI utile
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from app_logging import setup_logging
    setup_logging()

    if len(sys.argv) < 2:
        print("Utilizare:")
        print("  python qr_labels.py png <cod_loc> <out.png> [--only-qr]")
        print("  python qr_labels.py batch <cods_csv> <out_dir>")
        print("  python qr_labels.py pdf <cods_csv> <out.pdf> [preset]")
        print("     Presets disponibile:", list(LABEL_PRESETS.keys()))
        sys.exit(1)

    mode = sys.argv[1]
    if mode == "png":
        only = "--only-qr" in sys.argv
        generate_qr_png(sys.argv[2], sys.argv[3], only_qr=only)
        print(f"OK — {sys.argv[3]}")
    elif mode == "batch":
        cods = [c.strip() for c in sys.argv[2].split(",") if c.strip()]
        paths = generate_qr_batch_pngs(cods, sys.argv[3])
        print(f"OK — {len(paths)} PNG-uri în {sys.argv[3]}")
    elif mode == "pdf":
        cods = [c.strip() for c in sys.argv[2].split(",") if c.strip()]
        preset = sys.argv[4] if len(sys.argv) > 4 else "3x8 (24/pag, 70×36)"
        generate_qr_labels_pdf(cods, sys.argv[3], preset=preset)
        print(f"OK — {sys.argv[3]}")
    else:
        print(f"Mod necunoscut: {mode}")
        sys.exit(1)
