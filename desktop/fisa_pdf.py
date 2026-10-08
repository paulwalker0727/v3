"""
Generator FIȘĂ LOC DE VECI (PDF) — modul comun (web + desktop).

`build_fisa_pdf(cod_loc)` întoarce conținutul PDF (bytes) cu TOATE datele despre
un loc: identificare/poziție, concesionar, contracte, înhumați (cu numere de
evidență), lucrări/avize, plăți și documentele atașate (imaginile sunt încorporate,
PDF-urile sunt listate).
"""
import io
import os
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, HRFlowable,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily

import database as db

PRIMARY = colors.HexColor("#0e5aa7")
MUTED = colors.HexColor("#667085")
LIGHT = colors.HexColor("#eef2f7")
IMG_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")

FONT = "Helvetica"
FONT_B = "Helvetica-Bold"


def _register_fonts():
    """Font Unicode (diacritice RO). Preferă fonturile din pachet, apoi cele de sistem."""
    global FONT, FONT_B
    here = os.path.dirname(os.path.abspath(__file__))
    reg = next((p for p in [
        os.path.join(here, "fonts", "LiberationSans-Regular.ttf"),
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ] if os.path.exists(p)), None)
    bold = next((p for p in [
        os.path.join(here, "fonts", "LiberationSans-Bold.ttf"),
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ] if os.path.exists(p)), None)
    try:
        if reg:
            pdfmetrics.registerFont(TTFont("AppFont", reg))
            FONT = "AppFont"
        if bold:
            pdfmetrics.registerFont(TTFont("AppFont-Bold", bold))
            FONT_B = "AppFont-Bold"
        if FONT == "AppFont":
            registerFontFamily("AppFont", normal="AppFont",
                               bold=(FONT_B if FONT_B == "AppFont-Bold" else "AppFont"),
                               italic="AppFont",
                               boldItalic=(FONT_B if FONT_B == "AppFont-Bold" else "AppFont"))
    except Exception:
        pass


_register_fonts()


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("H1", parent=ss["Heading1"], textColor=PRIMARY, fontSize=18, spaceAfter=2, fontName=FONT_B))
    ss.add(ParagraphStyle("Sub", parent=ss["Normal"], textColor=MUTED, fontSize=9, spaceAfter=8, fontName=FONT))
    ss.add(ParagraphStyle("Sec", parent=ss["Heading2"], textColor=PRIMARY, fontSize=12,
                          spaceBefore=10, spaceAfter=4, fontName=FONT_B))
    ss.add(ParagraphStyle("Cell", parent=ss["Normal"], fontSize=9, leading=12, fontName=FONT))
    ss.add(ParagraphStyle("CellB", parent=ss["Normal"], fontSize=9, leading=12, fontName=FONT_B))
    ss.add(ParagraphStyle("Small", parent=ss["Normal"], fontSize=8, textColor=MUTED, fontName=FONT))
    return ss


def _section(title, ss):
    return [HRFlowable(width="100%", thickness=0.6, color=LIGHT, spaceBefore=6, spaceAfter=2),
            Paragraph(title, ss["Sec"])]


def _kv_table(pairs, ss):
    """pairs: listă de (label, value). Tabel pe 2 coloane de perechi."""
    data = []
    row = []
    for label, value in pairs:
        cell = [Paragraph(f"<font color='#667085'>{label}</font>", ss["Cell"]),
                Paragraph(str(value if value not in (None, "") else "—"), ss["CellB"])]
        row.extend(cell)
        if len(row) == 4:
            data.append(row); row = []
    if row:
        while len(row) < 4:
            row.append("")
        data.append(row)
    if not data:
        return Spacer(1, 1)
    t = Table(data, colWidths=[32 * mm, 60 * mm, 32 * mm, 60 * mm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _grid_table(header, rows, ss, col_widths=None):
    data = [[Paragraph(f"<b>{h}</b>", ss["Cell"]) for h in header]]
    for r in rows:
        data.append([Paragraph(str(c if c not in (None, "") else "—"), ss["Cell"]) for c in r])
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d0d7e2")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _doc_label(dt):
    return {
        "poza_loc": "Fotografie loc", "certificat_deces": "Certificat de deces",
        "adeverinta_inhumare": "Adeverință de înhumare", "foto_lucrare": "Fotografie lucrare",
        "aviz_constructie": "Aviz de construcție", "chitanta": "Chitanță",
        "contract_scan": "Contract scanat", "alt": "Alt document",
    }.get(dt or "", dt or "Document")


def _add_documents(elems, docs, ss, title):
    if not docs:
        return
    elems.append(Paragraph(title, ss["Sec"]))
    images = []
    pdfs = []
    for d in docs:
        path = d.get("file_path", "")
        ext = os.path.splitext(path)[1].lower()
        if ext in IMG_EXT and os.path.exists(path):
            images.append(d)
        else:
            pdfs.append(d)
    # imagini încorporate (grilă 3 pe rând)
    row = []
    cells = []
    for d in images:
        try:
            from PIL import Image as PILImage
            with PILImage.open(d["file_path"]) as pil_img:
                pil_img.load()
            img = Image(d["file_path"])
            iw, ih = img.imageWidth, img.imageHeight
            w = 55 * mm
            h = w * ih / iw if iw else 40 * mm
            if h > 45 * mm:
                h = 45 * mm; w = h * iw / ih if ih else 55 * mm
            img.drawWidth = w; img.drawHeight = h
            cap = _doc_label(d.get("doc_type"))
            if d.get("nr_evidenta"):
                cap += f" · {d['nr_evidenta']}"
            cells.append([img, Paragraph(cap, ss["Small"])])
        except Exception:
            continue
        if len(cells) == 3:
            row.append(cells); cells = []
    if cells:
        while len(cells) < 3:
            cells.append([Paragraph("", ss["Small"])])
        row.append(cells)
    for r in row:
        flat = [[c[0] for c in r], [c[1] if len(c) > 1 else "" for c in r]]
        t = Table(flat, colWidths=[58 * mm, 58 * mm, 58 * mm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
        elems.append(t)
    # pdf-uri listate
    for d in pdfs:
        name = os.path.basename(d.get("file_path", "")) or "document"
        elems.append(Paragraph(f"• {_doc_label(d.get('doc_type'))} (PDF): {name}"
                               + (f" · nr. {d['nr_evidenta']}" if d.get('nr_evidenta') else ""), ss["Cell"]))


def _contract_holder(cod_loc: str, contracte: list):
    if not (contracte and contracte[-1].get("concesionar_id")):
        return None
    try:
        return db.get_concesionar(int(contracte[-1]["concesionar_id"]))
    except Exception:
        return None


def _header_elems(cod_loc: str, ss) -> list:
    try:
        cimitir = db.get_setting("cimitir_nume") or ""
    except Exception:
        cimitir = ""
    sub = f"Generat: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    if cimitir:
        sub = f"Cimitir: {cimitir}   ·   " + sub
    return [Paragraph(f"FIȘĂ LOC DE VECI — {cod_loc}", ss["H1"]), Paragraph(sub, ss["Sub"])]


def _position_elems(loc: dict, ss) -> list:
    return _section("Poziție / Identificare", ss) + [_kv_table([
        ("Sector", loc.get("sector")), ("Parcela", loc.get("parcela")),
        ("Rând", loc.get("rand")), ("Număr", loc.get("numar")),
        ("Tip", loc.get("tip")), ("Categorie", loc.get("categorie")),
        ("Stare", loc.get("stare")), ("Disponibilitate", loc.get("disponibilitate")),
        ("Capacitate", loc.get("capacitate")),
        ("Suprafață", f"{loc.get('suprafata')} m²" if loc.get("suprafata") else ""),
    ], ss)]


def _holder_elems(conces, contracte: list, ss) -> list:
    elems = _section("Concesionar", ss)
    if conces:
        elems.append(_kv_table([
            ("Nume", conces.get("nume")), ("Prenume", conces.get("prenume")),
            ("CNP", conces.get("cnp")), ("BI/CI", conces.get("bi_ci")),
            ("Telefon", conces.get("telefon")), ("Email", conces.get("email")),
            ("Adresă", conces.get("adresa")),
        ], ss))
    elif contracte:
        elems.append(_kv_table([("Titular", contracte[-1].get("owner_fullname"))], ss))
    else:
        elems.append(Paragraph("Niciun concesionar asociat.", ss["Cell"]))
    return elems


def _contracts_elems(contracte: list, ss) -> list:
    elems = _section(f"Contracte ({len(contracte)})", ss)
    if not contracte:
        return elems + [Paragraph("Niciun contract.", ss["Cell"])]
    elems.append(_grid_table(
        ["Nr.", "Data", "Început", "Sfârșit", "Valab.", "Titular"],
        [[c.get("numar_contract"), c.get("data_contract"), c.get("data_inceput"),
          c.get("data_sfarsit"), c.get("valabilitate"), c.get("owner_fullname")] for c in contracte],
        ss, col_widths=[22 * mm, 24 * mm, 24 * mm, 24 * mm, 20 * mm, 50 * mm]))
    for c in contracte:
        _add_documents(elems, db.list_documents("contract", c["id"]), ss,
                       f"Documente contract {c.get('numar_contract') or c['id']}")
    return elems


def _burials_elems(inhumati: list, ss) -> list:
    elems = _section(f"Înhumați ({len(inhumati)})", ss)
    if not inhumati:
        return elems + [Paragraph("Nicio persoană înhumată.", ss["Cell"])]
    elems.append(_grid_table(
        ["Nume", "Prenume", "CNP", "Deces", "Înhumare", "Nr. cert. deces", "Nr. adev. înh."],
        [[i.get("nume"), i.get("prenume"), i.get("cnp"), i.get("data_deces"),
          i.get("data_inhumare"), i.get("nr_certificat_deces"), i.get("nr_adeverinta_inhumare")]
         for i in inhumati], ss,
        col_widths=[26 * mm, 26 * mm, 26 * mm, 20 * mm, 20 * mm, 24 * mm, 24 * mm]))
    for i in inhumati:
        _add_documents(elems, db.list_documents("inhumat", i["id"]), ss,
                       f"Documente — {i.get('nume','')} {i.get('prenume','')}")
    return elems


def _notices_elems(avize: list, ss) -> list:
    elems = _section(f"Lucrări efectuate / Avize ({len(avize)})", ss)
    if not avize:
        return elems + [Paragraph("Nicio lucrare / aviz.", ss["Cell"])]
    elems.append(_grid_table(
        ["Nr. aviz", "Data", "Lucrare"],
        [[a.get("nr"), a.get("data"), a.get("lucrare")] for a in avize],
        ss, col_widths=[30 * mm, 30 * mm, 110 * mm]))
    for a in avize:
        _add_documents(elems, db.list_documents("aviz", a["id"]), ss,
                       f"Documente aviz {a.get('nr') or a['id']}")
    return elems


def _payments_elems(plati: list, total_plati: float, ss) -> list:
    elems = _section(f"Plăți ({len(plati)})", ss)
    if plati:
        elems.append(_grid_table(
            ["Data", "Sumă (lei)", "Tip", "Chitanță"],
            [[p.get("data_plata"), f"{float(p.get('suma') or 0):.2f}", p.get("tip"), p.get("chitanta_nr")]
             for p in plati], ss, col_widths=[35 * mm, 35 * mm, 50 * mm, 50 * mm]))
    elems.append(Paragraph(f"<b>Total încasat: {total_plati:.2f} lei</b>", ss["Cell"]))
    return elems


def build_fisa_pdf(cod_loc: str) -> bytes:
    ss = _styles()
    loc = db.load_loc_by_cod(cod_loc) or {}
    contracte = [c for c in db.load_contracte() if c.get("cod_loc") == cod_loc]
    elems = _header_elems(cod_loc, ss)
    elems += _position_elems(loc, ss)
    elems += _holder_elems(_contract_holder(cod_loc, contracte), contracte, ss)
    elems += _contracts_elems(contracte, ss)
    elems += _burials_elems(db.load_inhumati_for_loc(cod_loc), ss)
    elems += _notices_elems(db.list_avize_for_loc(cod_loc), ss)
    _add_documents(elems, db.list_documents("loc", cod_loc, "poza_loc"), ss, "Fotografii loc")
    elems += _payments_elems(db.list_plati(cod_loc), db.total_plati(cod_loc), ss)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm,
                            title=f"Fișă loc {cod_loc}")
    doc.build(elems)
    return buf.getvalue()
