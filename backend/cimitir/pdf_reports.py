"""
pdf_reports.py — Rapoarte PDF pentru Cimitir Desktop
=====================================================

Modul independent (fără Qt), poate fi apelat din desktop, web_server sau CLI.

Rapoarte disponibile:
    - render_contract_pdf(contract_id)           → contract de concesiune (PDF)
    - render_receipt_pdf(plata_id)               → chitanță plată (PDF)
    - render_burial_certificate_pdf(inhumat_id)  → adeverință de înhumare (PDF)
    - render_expiring_contracts_pdf(days)        → situație contracte expirate/expirând
    - render_financial_report_pdf(start, end)    → raport financiar pe perioadă
    - render_burials_register_pdf(start, end)    → registru înhumați pe perioadă

Toate întorc bytes (conținut PDF). Pentru salvare pe disc folosește helper-ul
`save_pdf(bytes_data, path)`.
"""
from __future__ import annotations

import io
import os
from datetime import datetime, date
from typing import Optional, List, Dict, Any, Tuple

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, PageBreak, Image,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily

import store as db
from app_logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# QR CODE — opțional pe fiecare raport
# ---------------------------------------------------------------------------
DEFAULT_QR_BASE_URL = "https://cimitir.local"


def get_qr_base_url() -> str:
    """URL-ul de bază pentru QR-uri (din settings, altfel default)."""
    try:
        val = db.get_setting("qr_base_url")
        if val:
            return val.rstrip("/")
    except Exception:
        logger.exception("Nu am putut citi qr_base_url din settings")
    return DEFAULT_QR_BASE_URL


def _build_qr_url(kind: str, ref: str) -> str:
    """Construiește URL-ul care va fi codificat în QR (fără date personale).

    Convenție:
        - kind='loc'      → /public/loc/<cod_loc>  (pagina publică servită de web_server)
        - kind='contract' → /contract/<numar>
        - kind='plata'    → /plata/<chitanta_nr>
        - kind='rapoarte' → /rapoarte/<slug>
    """
    base = get_qr_base_url()
    ref_safe = str(ref).strip().replace(" ", "_") if ref else "unknown"
    # locurile de veci merg pe endpoint-ul public
    if kind == "loc":
        return f"{base}/public/loc/{ref_safe}"
    return f"{base}/{kind}/{ref_safe}"


def _generate_qr_image(url: str, size_mm: float = 25) -> Optional[Image]:
    """Generează un flowable Image cu QR code din URL. None dacă librăria lipsește."""
    try:
        import qrcode  # noqa: WPS433
        img_buf = io.BytesIO()
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=6,
            border=2,
        )
        qr.add_data(url)
        qr.make(fit=True)
        pil = qr.make_image(fill_color="black", back_color="white")
        pil.save(img_buf, format="PNG")
        img_buf.seek(0)
        img = Image(img_buf)
        img.drawWidth = size_mm * mm
        img.drawHeight = size_mm * mm
        return img
    except ImportError:
        logger.warning("Librăria 'qrcode' nu e instalată — omit QR code")
        return None
    except Exception:
        logger.exception("Nu am putut genera QR pentru URL: %s", url)
        return None


def _qr_block(ss, kind: str, ref: str, caption: str = "Scanează pentru detalii") -> List[Any]:
    """Bloc reutilizabil: QR + URL text + caption, aliniat centrat sub semnături."""
    url = _build_qr_url(kind, ref)
    qr_img = _generate_qr_image(url, size_mm=28)
    if qr_img is None:
        return []

    row = Table(
        [[qr_img, [
            Paragraph(f"<b>{caption}</b>", ss["Small"]),
            Paragraph(f"<font color='#0e5aa7'>{url}</font>", ss["Small"]),
            Paragraph("Verificare document online", ss["Small"]),
        ]]],
        colWidths=[30 * mm, 100 * mm],
    )
    row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (1, 0), (1, 0), 8),
    ]))
    return [Spacer(1, 10), row]

# ---------------------------------------------------------------------------
# CULORI / STIL
# ---------------------------------------------------------------------------
PRIMARY = colors.HexColor("#0e5aa7")
MUTED = colors.HexColor("#667085")
LIGHT = colors.HexColor("#eef2f7")
BORDER = colors.HexColor("#d0d7e2")
ACCENT = colors.HexColor("#0f9d58")
DANGER = colors.HexColor("#c0392b")

FONT = "Helvetica"
FONT_B = "Helvetica-Bold"


def _register_fonts() -> None:
    """Font Unicode pentru diacritice românești."""
    global FONT, FONT_B
    here = os.path.dirname(os.path.abspath(__file__))
    reg = next((p for p in [
        os.path.join(here, "fonts", "LiberationSans-Regular.ttf"),
        os.path.join(here, "..", "fonts", "LiberationSans-Regular.ttf"),
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ] if os.path.exists(p)), None)
    bold = next((p for p in [
        os.path.join(here, "fonts", "LiberationSans-Bold.ttf"),
        os.path.join(here, "..", "fonts", "LiberationSans-Bold.ttf"),
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
            registerFontFamily(
                "AppFont", normal="AppFont",
                bold=(FONT_B if FONT_B == "AppFont-Bold" else "AppFont"),
                italic="AppFont",
                boldItalic=(FONT_B if FONT_B == "AppFont-Bold" else "AppFont"),
            )
    except Exception:
        logger.exception("Nu am putut înregistra font-urile UTF-8")


_register_fonts()


# ---------------------------------------------------------------------------
# STILURI COMUNE
# ---------------------------------------------------------------------------
def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("H1", parent=ss["Heading1"], textColor=PRIMARY,
                          fontSize=18, spaceAfter=2, fontName=FONT_B))
    ss.add(ParagraphStyle("H2", parent=ss["Heading2"], textColor=PRIMARY,
                          fontSize=13, spaceBefore=10, spaceAfter=4, fontName=FONT_B))
    ss.add(ParagraphStyle("Sub", parent=ss["Normal"], textColor=MUTED,
                          fontSize=9, spaceAfter=8, fontName=FONT))
    ss.add(ParagraphStyle("Body", parent=ss["Normal"], fontSize=10, leading=14, fontName=FONT))
    ss.add(ParagraphStyle("BodyB", parent=ss["Normal"], fontSize=10, leading=14, fontName=FONT_B))
    ss.add(ParagraphStyle("Cell", parent=ss["Normal"], fontSize=9, leading=12, fontName=FONT))
    ss.add(ParagraphStyle("CellB", parent=ss["Normal"], fontSize=9, leading=12, fontName=FONT_B))
    ss.add(ParagraphStyle("Small", parent=ss["Normal"], fontSize=8, textColor=MUTED, fontName=FONT))
    ss.add(ParagraphStyle("Center", parent=ss["Normal"], fontSize=10, alignment=1, fontName=FONT))
    ss.add(ParagraphStyle("CenterB", parent=ss["Normal"], fontSize=12, alignment=1, fontName=FONT_B))
    return ss


def _org_header(ss) -> List[Any]:
    """Header cu numele cimitirului / operatorului (din settings)."""
    try:
        cimitir = db.get_setting("cimitir_nume") or ""
    except Exception:
        cimitir = ""
    try:
        operator = db.get_setting("operator_nume") or ""
    except Exception:
        operator = ""

    parts = []
    if operator:
        parts.append(Paragraph(f"<b>{operator}</b>", ss["Body"]))
    if cimitir:
        parts.append(Paragraph(f"Cimitir: <b>{cimitir}</b>", ss["Body"]))
    if parts:
        parts.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=6))
    return parts


def _kv_table(pairs: List[Tuple[str, Any]], ss, col_widths=None) -> Table:
    data = []
    for label, value in pairs:
        data.append([
            Paragraph(f"<font color='#667085'>{label}</font>", ss["Cell"]),
            Paragraph(str(value if value not in (None, "") else "—"), ss["CellB"]),
        ])
    t = Table(data, colWidths=col_widths or [50 * mm, 120 * mm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _grid_table(header: List[str], rows: List[List[Any]], ss,
                col_widths=None, zebra: bool = True) -> Table:
    data = [[Paragraph(f"<b>{h}</b>", ss["Cell"]) for h in header]]
    for r in rows:
        data.append([Paragraph(str(c if c not in (None, "") else "—"), ss["Cell"]) for c in r])
    t = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]
    if zebra:
        for i in range(1, len(data)):
            if i % 2 == 0:
                style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fafbfc")))
    t.setStyle(TableStyle(style))
    return t


def _doc(buf: io.BytesIO, title: str = "Raport") -> SimpleDocTemplate:
    return SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm,
        title=title,
    )


def _footer(ss, actor: Optional[str] = None) -> List[Any]:
    txt = f"Generat: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    if actor:
        txt += f"   ·   Operator: {actor}"
    return [Spacer(1, 6),
            HRFlowable(width="100%", thickness=0.4, color=BORDER),
            Paragraph(txt, ss["Small"])]


def _fmt_money(v) -> str:
    try:
        return f"{float(v or 0):,.2f} lei".replace(",", " ")
    except Exception:
        return "0,00 lei"


def _fmt_date(v) -> str:
    if not v:
        return "—"
    s = str(v).strip()
    for fmt_in in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt_in).strftime("%d.%m.%Y")
        except Exception:
            continue
    return s


def _in_range(v: Any, start: Optional[str], end: Optional[str]) -> bool:
    """Verifică dacă un string de dată (multiple formate) e în intervalul [start, end]."""
    if not v:
        return False
    for fmt_in in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            d = datetime.strptime(str(v).strip(), fmt_in).date()
            break
        except Exception:
            continue
    else:
        return False
    if start:
        try:
            if d < datetime.strptime(start, "%Y-%m-%d").date():
                return False
        except Exception:
            pass
    if end:
        try:
            if d > datetime.strptime(end, "%Y-%m-%d").date():
                return False
        except Exception:
            pass
    return True


def save_pdf(data: bytes, path: str) -> str:
    """Salvează bytes-urile PDF pe disc și întoarce calea completă."""
    with open(path, "wb") as f:
        f.write(data)
    logger.info("PDF salvat: %s (%.1f KB)", path, len(data) / 1024)
    return path


# ===========================================================================
# 1) CONTRACT DE CONCESIUNE
# ===========================================================================
def render_contract_pdf(contract_id: int, include_qr: bool = False) -> bytes:
    """Contract de concesiune complet, gata de tipărit și semnat.

    Args:
        contract_id: ID-ul contractului
        include_qr: dacă True, adaugă QR code cu URL public la finalul contractului
    """
    contract = db.get_contract(contract_id)
    if not contract:
        raise ValueError(f"Contractul {contract_id} nu există.")

    concesionar = None
    if contract.get("concesionar_id"):
        try:
            concesionar = db.get_concesionar(int(contract["concesionar_id"]))
        except Exception:
            logger.exception("Concesionar id=%s invalid", contract.get("concesionar_id"))

    loc = None
    if contract.get("cod_loc"):
        loc = db.load_loc_by_cod(contract["cod_loc"])

    ss = _styles()
    elems: List[Any] = []
    elems += _org_header(ss)
    elems.append(Paragraph(
        f"CONTRACT DE CONCESIUNE nr. {contract.get('numar_contract') or contract_id}", ss["H1"]))
    elems.append(Paragraph(
        f"Încheiat astăzi, {_fmt_date(contract.get('data_contract'))}", ss["Sub"]))

    # Părțile contractante
    elems.append(Paragraph("PĂRȚILE CONTRACTANTE", ss["H2"]))
    concedent = ""
    try:
        concedent = db.get_setting("operator_nume") or "________________________"
    except Exception:
        concedent = "________________________"
    elems.append(Paragraph(
        f"<b>Concedent:</b> {concedent}, prin administrator, și", ss["Body"]))

    titular = (contract.get("owner_fullname")
               or (f"{concesionar.get('nume','')} {concesionar.get('prenume','')}".strip()
                   if concesionar else "________________________"))
    cnp = contract.get("owner_cnp") or (concesionar.get("cnp") if concesionar else "") or "____________"
    adresa = contract.get("owner_address") or (concesionar.get("adresa") if concesionar else "") or "____________"
    bici = contract.get("owner_id_card_serie_number") or (
        concesionar.get("bi_ci") if concesionar else "") or "____________"

    elems.append(Paragraph(
        f"<b>Concesionar:</b> {titular}, CNP {cnp}, "
        f"BI/CI seria/nr. {bici}, cu domiciliul în {adresa}.", ss["Body"]))
    if contract.get("owner_fullname_2"):
        elems.append(Paragraph(f"<b>Co-concesionar:</b> {contract['owner_fullname_2']}", ss["Body"]))
    elems.append(Spacer(1, 6))

    # Obiectul contractului
    elems.append(Paragraph("OBIECTUL CONTRACTULUI", ss["H2"]))
    surf = contract.get("tomb_surface_m2") or (loc.get("suprafata") if loc else "")
    obj_txt = (
        f"Concedentul dă în concesiune Concesionarului locul de veci cu codul "
        f"<b>{contract.get('cod_loc') or '—'}</b>, situat în sectorul "
        f"<b>{contract.get('sector') or '—'}</b>, parcela <b>{contract.get('cemetery_parcel') or '—'}</b>, "
        f"rândul <b>{contract.get('cemetery_row') or '—'}</b>, numărul <b>{contract.get('cemetery_tomb') or '—'}</b>, "
        f"având suprafața de <b>{surf or '—'}</b> m²."
    )
    elems.append(Paragraph(obj_txt, ss["Body"]))
    elems.append(Spacer(1, 4))

    # Durata + valabilitate
    elems.append(Paragraph("DURATA CONTRACTULUI", ss["H2"]))
    elems.append(_kv_table([
        ("Data începerii", _fmt_date(contract.get("data_inceput"))),
        ("Data încheierii", _fmt_date(contract.get("data_sfarsit"))),
        ("Valabilitate", contract.get("valabilitate") or "—"),
    ], ss))

    # Alte clauze
    elems.append(Paragraph("CLAUZE GENERALE", ss["H2"]))
    clauze = [
        "1. Concesionarul se obligă să întrețină locul de veci și să respecte "
        "regulamentul de ordine interioară al cimitirului.",
        "2. Este interzisă înstrăinarea locului de veci fără acordul concedentului.",
        "3. Neîndeplinirea obligațiilor financiare atrage rezilierea contractului "
        "și trecerea locului la dispoziția administratorului.",
        "4. Orice modificare a prezentului contract se face prin act adițional scris.",
    ]
    for c in clauze:
        elems.append(Paragraph(c, ss["Body"]))

    if contract.get("observatii"):
        elems.append(Spacer(1, 4))
        elems.append(Paragraph("OBSERVAȚII", ss["H2"]))
        elems.append(Paragraph(str(contract["observatii"]), ss["Body"]))

    # Semnături
    elems.append(Spacer(1, 30))
    sig = Table([
        [Paragraph("<b>Concedent</b>", ss["Center"]),
         Paragraph("<b>Concesionar</b>", ss["Center"])],
        [Paragraph("_____________________", ss["Center"]),
         Paragraph("_____________________", ss["Center"])],
        [Paragraph(concedent or "", ss["Small"]),
         Paragraph(titular, ss["Small"])],
    ], colWidths=[85 * mm, 85 * mm])
    sig.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 1), (-1, 1), 20),
    ]))
    elems.append(sig)

    if include_qr:
        ref = contract.get("numar_contract") or str(contract_id)
        elems += _qr_block(ss, "contract", ref,
                           caption="Verifică contractul online")

    elems += _footer(ss)

    buf = io.BytesIO()
    _doc(buf, title=f"Contract {contract.get('numar_contract') or contract_id}").build(elems)
    logger.info("Contract PDF generat: contract_id=%s (qr=%s)", contract_id, include_qr)
    return buf.getvalue()


# ===========================================================================
# 2) CHITANȚĂ PLATĂ
# ===========================================================================
def _get_plata(plata_id: int) -> Optional[Dict[str, Any]]:
    conn = db.get_connection()
    try:
        row = conn.execute("SELECT * FROM plati WHERE id = ?", (plata_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def render_receipt_pdf(plata_id: int, include_qr: bool = False) -> bytes:
    """Chitanță (dovadă plată) — format A5-like pe A4.

    Args:
        plata_id: ID-ul plății
        include_qr: dacă True, adaugă QR code cu URL public pentru verificare
    """
    plata = _get_plata(plata_id)
    if not plata:
        raise ValueError(f"Plata {plata_id} nu există.")

    contract = None
    if plata.get("contract_id"):
        contract = db.get_contract(int(plata["contract_id"]))
    concesionar = None
    if contract and contract.get("concesionar_id"):
        try:
            concesionar = db.get_concesionar(int(contract["concesionar_id"]))
        except Exception:
            logger.exception("Concesionar invalid pentru contract %s", contract.get("id"))

    ss = _styles()
    elems: List[Any] = []
    elems += _org_header(ss)

    elems.append(Paragraph(
        f"CHITANȚĂ Nr. {plata.get('chitanta_nr') or plata_id}", ss["H1"]))
    elems.append(Paragraph(
        f"Data emiterii: {_fmt_date(plata.get('data_plata'))}", ss["Sub"]))

    titular = (contract.get("owner_fullname") if contract else "") or (
        f"{concesionar.get('nume','')} {concesionar.get('prenume','')}".strip()
        if concesionar else "________________________")

    elems.append(Paragraph(
        f"Am primit de la <b>{titular}</b> suma de "
        f"<b>{_fmt_money(plata.get('suma'))}</b>, reprezentând "
        f"<b>{plata.get('tip') or 'plată concesiune'}</b> "
        f"pentru locul de veci cod <b>{plata.get('cod_loc') or '—'}</b>"
        + (f", contract nr. <b>{contract.get('numar_contract')}</b>" if contract and contract.get('numar_contract') else "")
        + ".", ss["Body"]))

    if plata.get("observatii"):
        elems.append(Spacer(1, 6))
        elems.append(Paragraph(f"<i>Observații:</i> {plata['observatii']}", ss["Small"]))

    elems.append(Spacer(1, 30))
    sig = Table([
        [Paragraph("<b>Casier</b>", ss["Center"]),
         Paragraph("<b>Plătitor</b>", ss["Center"])],
        [Paragraph("_____________________", ss["Center"]),
         Paragraph("_____________________", ss["Center"])],
    ], colWidths=[85 * mm, 85 * mm])
    sig.setStyle(TableStyle([("TOPPADDING", (0, 1), (-1, 1), 20)]))
    elems.append(sig)

    if include_qr:
        ref = plata.get("chitanta_nr") or str(plata_id)
        elems += _qr_block(ss, "plata", ref, caption="Verifică plata online")

    elems += _footer(ss)

    buf = io.BytesIO()
    _doc(buf, title=f"Chitanță {plata.get('chitanta_nr') or plata_id}").build(elems)
    logger.info("Chitanță PDF generată: plata_id=%s (qr=%s)", plata_id, include_qr)
    return buf.getvalue()


# ===========================================================================
# 3) ADEVERINȚĂ DE ÎNHUMARE
# ===========================================================================
def render_burial_certificate_pdf(inhumat_id: int, include_qr: bool = False) -> bytes:
    """Adeverință oficială de înhumare pentru o persoană.

    Args:
        inhumat_id: ID-ul înregistrării înhumatului
        include_qr: dacă True, adaugă QR code cu URL public spre locul de veci
                    (fără date personale — doar codul locului, pentru vizitatori)
    """
    inh = db.get_inhumat(inhumat_id)
    if not inh:
        raise ValueError(f"Înhumatul {inhumat_id} nu există.")

    cod_loc = inh.get("cod_loc") or ""
    loc = db.load_loc_by_cod(cod_loc) if cod_loc else {}

    ss = _styles()
    elems: List[Any] = []
    elems += _org_header(ss)

    elems.append(Paragraph(f"ADEVERINȚĂ DE ÎNHUMARE Nr. "
                           f"{inh.get('nr_adeverinta_inhumare') or inhumat_id}", ss["H1"]))
    elems.append(Paragraph(
        f"Emisă la data de {datetime.now().strftime('%d.%m.%Y')}", ss["Sub"]))

    nume_complet = f"{inh.get('nume','')} {inh.get('prenume','')}".strip() or "________________________"
    txt = (
        f"Prin prezenta se atestă că defunctul(a) <b>{nume_complet}</b>, "
        f"CNP <b>{inh.get('cnp') or '____________'}</b>, "
        f"născut(ă) la data de <b>{_fmt_date(inh.get('data_nasterii'))}</b>, "
        f"decedat(ă) la data de <b>{_fmt_date(inh.get('data_deces'))}</b>"
        + (f" (cauza: {inh.get('cauza_deces')})" if inh.get('cauza_deces') else "")
        + f", a fost înhumat(ă) la data de <b>{_fmt_date(inh.get('data_inhumare'))}</b> "
        f"în locul de veci având codul <b>{cod_loc or '—'}</b>, "
    )
    if loc:
        txt += (
            f"situat în sectorul <b>{loc.get('sector') or '—'}</b>, "
            f"parcela <b>{loc.get('parcela') or '—'}</b>, "
            f"rândul <b>{loc.get('rand') or '—'}</b>, "
            f"numărul <b>{loc.get('numar') or '—'}</b>."
        )
    else:
        txt += "administrat de această unitate."

    elems.append(Paragraph(txt, ss["Body"]))

    if inh.get("nr_certificat_deces"):
        elems.append(Spacer(1, 4))
        elems.append(Paragraph(
            f"Certificat de deces nr. <b>{inh['nr_certificat_deces']}</b>.", ss["Body"]))

    elems.append(Spacer(1, 6))
    elems.append(Paragraph(
        "Prezenta adeverință a fost emisă pentru a servi la orice se cuvine.", ss["Body"]))

    # Semnături
    elems.append(Spacer(1, 30))
    sig = Table([
        [Paragraph("<b>Administrator cimitir</b>", ss["Center"]),
         Paragraph("<b>Ștampilă</b>", ss["Center"])],
        [Paragraph("_____________________", ss["Center"]),
         Paragraph("L.S.", ss["Center"])],
    ], colWidths=[85 * mm, 85 * mm])
    sig.setStyle(TableStyle([("TOPPADDING", (0, 1), (-1, 1), 25)]))
    elems.append(sig)

    if include_qr:
        # QR-ul duce spre locul de veci (public, fără date personale)
        ref = cod_loc or str(inhumat_id)
        elems += _qr_block(ss, "loc", ref,
                           caption="Găsește locul pe hartă (QR public)")

    elems += _footer(ss)

    buf = io.BytesIO()
    _doc(buf, title=f"Adeverință înhumare {nume_complet}").build(elems)
    logger.info("Adeverință înhumare PDF generată: inhumat_id=%s (qr=%s)", inhumat_id, include_qr)
    return buf.getvalue()


# ===========================================================================
# 4) SITUAȚIE CONTRACTE EXPIRATE / CARE EXPIRĂ
# ===========================================================================
def render_expiring_contracts_pdf(days: int = 30, include_qr: bool = False) -> bytes:
    """Raport: contracte expirate sau care expiră în următoarele `days` zile."""
    rows = db.expiring_contracts(days=days)
    ss = _styles()
    elems: List[Any] = []
    elems += _org_header(ss)

    elems.append(Paragraph(
        f"SITUAȚIE CONTRACTE — Expirate sau în {days} zile", ss["H1"]))
    elems.append(Paragraph(
        f"Data raportului: {datetime.now().strftime('%d.%m.%Y')}   ·   "
        f"Total înregistrări: <b>{len(rows)}</b>", ss["Sub"]))

    if not rows:
        elems.append(Paragraph(
            "Nu există contracte care să se încadreze în perioada selectată.", ss["Body"]))
    else:
        today = date.today()
        table_rows = []
        for c in rows:
            expira = c.get("_expira") or c.get("data_sfarsit") or ""
            status = "?"
            try:
                d = datetime.strptime(str(expira)[:10], "%Y-%m-%d").date()
                if d < today:
                    delta = (today - d).days
                    status = f"EXPIRAT ({delta} zile)"
                else:
                    delta = (d - today).days
                    status = f"expiră în {delta} zile"
            except Exception:
                pass
            table_rows.append([
                c.get("numar_contract") or "—",
                _fmt_date(c.get("data_contract")),
                _fmt_date(expira),
                status,
                c.get("cod_loc") or "—",
                c.get("owner_fullname") or "—",
            ])
        elems.append(_grid_table(
            ["Nr. contract", "Data contract", "Data expirare", "Status", "Cod loc", "Titular"],
            table_rows, ss,
            col_widths=[25 * mm, 25 * mm, 25 * mm, 35 * mm, 25 * mm, 45 * mm],
        ))

    if include_qr:
        elems += _qr_block(ss, "rapoarte", f"expirate-{days}zile",
                           caption="Portal cimitir")

    elems += _footer(ss)
    buf = io.BytesIO()
    _doc(buf, title="Situație contracte expirate").build(elems)
    logger.info("Raport contracte expirate PDF generat: %d rânduri (qr=%s)", len(rows), include_qr)
    return buf.getvalue()


# ===========================================================================
# 5) RAPORT FINANCIAR (încasări pe perioadă)
# ===========================================================================
def render_financial_report_pdf(start: Optional[str] = None,
                                end: Optional[str] = None,
                                sector: Optional[str] = None,
                                include_qr: bool = False) -> bytes:
    """
    Raport financiar: totale încasări pe perioadă (start, end în format YYYY-MM-DD).
    Opțional filtrat pe sector.
    """
    all_plati = db.list_plati()
    plati = [p for p in all_plati if _in_range(p.get("data_plata"), start, end)]

    # filtru sector: după cod_loc → sector
    if sector:
        by_cod = {}
        for p in plati:
            cod = p.get("cod_loc")
            if not cod:
                continue
            if cod not in by_cod:
                loc = db.load_loc_by_cod(cod) or {}
                by_cod[cod] = loc.get("sector", "") or ""
            p["_sector"] = by_cod[cod]
        plati = [p for p in plati if p.get("_sector") == sector]

    total = sum(float(p.get("suma") or 0) for p in plati)

    # totale pe tip de plată
    by_tip: Dict[str, float] = {}
    for p in plati:
        tip = p.get("tip") or "—"
        by_tip[tip] = by_tip.get(tip, 0.0) + float(p.get("suma") or 0)

    ss = _styles()
    elems: List[Any] = []
    elems += _org_header(ss)

    elems.append(Paragraph("RAPORT FINANCIAR — Încasări", ss["H1"]))
    sub = f"Perioada: {start or 'de la începuturi'}  →  {end or 'la zi'}"
    if sector:
        sub += f"   ·   Sector: {sector}"
    elems.append(Paragraph(sub, ss["Sub"]))

    # sumar
    elems.append(Paragraph("Sumar", ss["H2"]))
    elems.append(_kv_table([
        ("Total încasări", _fmt_money(total)),
        ("Număr plăți", str(len(plati))),
        ("Perioada", f"{start or '—'}  →  {end or '—'}"),
        ("Sector", sector or "toate"),
    ], ss))

    # detaliere pe tip
    if by_tip:
        elems.append(Paragraph("Defalcare pe tip de plată", ss["H2"]))
        elems.append(_grid_table(
            ["Tip plată", "Sumă totală", "Nr. plăți"],
            [[tip, _fmt_money(val),
              str(sum(1 for p in plati if (p.get("tip") or "—") == tip))]
             for tip, val in sorted(by_tip.items(), key=lambda kv: -kv[1])],
            ss, col_widths=[70 * mm, 50 * mm, 50 * mm],
        ))

    # listă detaliată
    if plati:
        elems.append(Paragraph("Detaliere plăți", ss["H2"]))
        table_rows = []
        for p in sorted(plati, key=lambda x: str(x.get("data_plata") or ""), reverse=True):
            table_rows.append([
                _fmt_date(p.get("data_plata")),
                p.get("chitanta_nr") or "—",
                p.get("cod_loc") or "—",
                p.get("tip") or "—",
                _fmt_money(p.get("suma")),
            ])
        elems.append(_grid_table(
            ["Data", "Chitanță", "Cod loc", "Tip", "Sumă"],
            table_rows, ss,
            col_widths=[25 * mm, 30 * mm, 25 * mm, 45 * mm, 35 * mm],
        ))
    else:
        elems.append(Paragraph("Nu există plăți în perioada selectată.", ss["Body"]))

    if include_qr:
        ref = f"{start or 'inceput'}_{end or 'azi'}"
        elems += _qr_block(ss, "rapoarte", f"financiar-{ref}",
                           caption="Portal cimitir")

    elems += _footer(ss)
    buf = io.BytesIO()
    _doc(buf, title="Raport financiar").build(elems)
    logger.info("Raport financiar PDF generat: %d plăți, total %.2f lei (qr=%s)",
                len(plati), total, include_qr)
    return buf.getvalue()


# ===========================================================================
# 6) REGISTRU ÎNHUMAȚI (pe perioadă)
# ===========================================================================
def render_burials_register_pdf(start: Optional[str] = None,
                                end: Optional[str] = None,
                                include_qr: bool = False) -> bytes:
    """Registrul înhumaților dintr-o perioadă (după data înhumării)."""
    all_inh = db.load_all_inhumati()
    rows = [i for i in all_inh if _in_range(i.get("data_inhumare"), start, end)]
    rows.sort(key=lambda x: str(x.get("data_inhumare") or ""))

    ss = _styles()
    elems: List[Any] = []
    elems += _org_header(ss)

    elems.append(Paragraph("REGISTRU ÎNHUMAȚI", ss["H1"]))
    elems.append(Paragraph(
        f"Perioada: {start or 'de la începuturi'}  →  {end or 'la zi'}   ·   "
        f"Total: <b>{len(rows)}</b>", ss["Sub"]))

    if not rows:
        elems.append(Paragraph("Nu există înregistrări în perioada selectată.", ss["Body"]))
    else:
        table_rows = []
        for i in rows:
            table_rows.append([
                _fmt_date(i.get("data_inhumare")),
                f"{i.get('nume','')} {i.get('prenume','')}".strip() or "—",
                i.get("cnp") or "—",
                _fmt_date(i.get("data_deces")),
                i.get("cod_loc") or "—",
                i.get("nr_certificat_deces") or "—",
                i.get("nr_adeverinta_inhumare") or "—",
            ])
        elems.append(_grid_table(
            ["Data înhumare", "Nume și prenume", "CNP", "Data deces",
             "Cod loc", "Nr. cert. deces", "Nr. adev."],
            table_rows, ss,
            col_widths=[22 * mm, 40 * mm, 25 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm],
        ))

    if include_qr:
        ref = f"{start or 'inceput'}_{end or 'azi'}"
        elems += _qr_block(ss, "rapoarte", f"registru-{ref}",
                           caption="Portal cimitir")

    elems += _footer(ss)
    buf = io.BytesIO()
    _doc(buf, title="Registru înhumați").build(elems)
    logger.info("Registru înhumați PDF generat: %d înregistrări (qr=%s)", len(rows), include_qr)
    return buf.getvalue()


# ===========================================================================
# CLI (util pentru testare / integrare shell)
# ===========================================================================
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Utilizare: python pdf_reports.py <report> <output.pdf> [args...]")
        print("Rapoarte: contract <id>, receipt <id>, burial_cert <id>,")
        print("          expiring <days>, financial <start> <end>, register <start> <end>")
        sys.exit(1)

    kind = sys.argv[1]
    out = sys.argv[2]
    args = [a for a in sys.argv[3:] if a != "--qr"]
    include_qr = "--qr" in sys.argv[3:]
    from app_logging import setup_logging
    setup_logging()

    if kind == "contract":
        data = render_contract_pdf(int(args[0]), include_qr=include_qr)
    elif kind == "receipt":
        data = render_receipt_pdf(int(args[0]), include_qr=include_qr)
    elif kind == "burial_cert":
        data = render_burial_certificate_pdf(int(args[0]), include_qr=include_qr)
    elif kind == "expiring":
        data = render_expiring_contracts_pdf(int(args[0]) if args else 30,
                                             include_qr=include_qr)
    elif kind == "financial":
        data = render_financial_report_pdf(args[0] if len(args) > 0 else None,
                                           args[1] if len(args) > 1 else None,
                                           include_qr=include_qr)
    elif kind == "register":
        data = render_burials_register_pdf(args[0] if len(args) > 0 else None,
                                           args[1] if len(args) > 1 else None,
                                           include_qr=include_qr)
    else:
        print(f"Raport necunoscut: {kind}")
        sys.exit(1)

    save_pdf(data, out)
    print(f"OK — {out}" + (" (cu QR)" if include_qr else ""))
