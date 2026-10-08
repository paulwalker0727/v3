"""Generează ghidul PDF pentru client (instalare + primii pași + cheia AI).

Rulare:
    python tools/make_client_guide.py

Rezultat:
    docs/Ghid_Instalare_Cimitir.pdf
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(HERE)
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    HRFlowable,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from pdf_reports import FONT, FONT_B  # reutilizează fonturile cu diacritice  # noqa: E402

try:
    from security.config import APP_VERSION
except Exception:
    APP_VERSION = "1.0.0"

INK = colors.HexColor("#0f172a")
MUTED = colors.HexColor("#475569")
ACCENT = colors.HexColor("#0ea5e9")
GREEN = colors.HexColor("#16a34a")
WARN = colors.HexColor("#b45309")
LINE = colors.HexColor("#cbd5e1")

_ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=_ss["Title"], fontName=FONT_B, fontSize=24,
                    textColor=INK, spaceAfter=4, alignment=0, leading=28)
SUB = ParagraphStyle("SUB", parent=_ss["Normal"], fontName=FONT, fontSize=11,
                     textColor=MUTED, spaceAfter=18, leading=15)
H2 = ParagraphStyle("H2", parent=_ss["Heading2"], fontName=FONT_B, fontSize=14,
                    textColor=ACCENT, spaceBefore=16, spaceAfter=6, leading=18)
H3 = ParagraphStyle("H3", parent=_ss["Heading3"], fontName=FONT_B, fontSize=11,
                    textColor=INK, spaceBefore=10, spaceAfter=4, leading=14)
BODY = ParagraphStyle("BODY", parent=_ss["Normal"], fontName=FONT, fontSize=10,
                      textColor=INK, leading=15, spaceAfter=6)
NOTE = ParagraphStyle("NOTE", parent=BODY, fontName=FONT, fontSize=9.5,
                      textColor=WARN, leading=14)
MONO = ParagraphStyle("MONO", parent=BODY, fontName="Courier", fontSize=9,
                      textColor=INK, backColor=colors.HexColor("#f1f5f9"),
                      borderPadding=5, leading=13, spaceAfter=8)


def bullets(items: list, style=BODY) -> ListFlowable:
    return ListFlowable(
        [ListItem(Paragraph(t, style), leftIndent=12) for t in items],
        bulletType="bullet", start="•", leftIndent=14, bulletFontName=FONT,
        bulletFontSize=10, spaceAfter=6,
    )


def steps(items: list) -> ListFlowable:
    return ListFlowable(
        [ListItem(Paragraph(t, BODY), leftIndent=14) for t in items],
        bulletType="1", leftIndent=18, bulletFontName=FONT_B, bulletFontSize=10,
        spaceAfter=6,
    )


def info_table(rows: list) -> Table:
    table = Table([[Paragraph(f"<b>{k}</b>", BODY), Paragraph(v, BODY)] for k, v in rows],
                  colWidths=[52 * mm, 108 * mm])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return table


def build_story() -> list:
    s: list = []
    s.append(Paragraph("Cimitir Desktop", H1))
    s.append(Paragraph(
        f"Ghid de instalare și primii pași · versiunea {APP_VERSION}", SUB))
    s.append(HRFlowable(width="100%", color=LINE, thickness=1, spaceAfter=12))

    # ---------------- 1. Instalare ----------------
    s.append(Paragraph("1. Instalarea aplicației", H2))
    s.append(Paragraph(
        "Ai primit unul din următoarele fișiere. Alege varianta care ți se potrivește:", BODY))
    s.append(info_table([
        ("CimitirSetup-….exe",
         "<b>Recomandat.</b> Instalator complet: creează scurtătură pe Desktop și în "
         "meniul Start, iar dezinstalarea se face din Panou de Control."),
        ("Cimitir-portabil.exe",
         "Un singur fișier, nu se instalează. Îl poți ține pe un stick USB și îl "
         "pornești de pe orice calculator Windows. Pornirea durează 5-15 secunde."),
    ]))
    s.append(Spacer(1, 8))
    s.append(Paragraph("Pași pentru instalator", H3))
    s.append(steps([
        "Dublu-click pe <b>CimitirSetup-….exe</b>.",
        "Dacă Windows afișează «Editor necunoscut», apasă <b>Informații suplimentare</b> "
        "→ <b>Executați oricum</b>. Este normal pentru aplicațiile fără certificat "
        "digital și nu înseamnă că fișierul e periculos.",
        "Acceptă licența, apasă <b>Instalează</b> și așteaptă 1-2 minute.",
        "Bifează <b>Pornește Cimitir</b> la final.",
    ]))
    s.append(Paragraph(
        "Cerințe: Windows 10 sau 11 pe 64 de biți, aproximativ 700 MB liberi pe disc. "
        "Nu ai nevoie de Python sau alte programe instalate.", NOTE))

    # ---------------- 2. Unde stau datele ----------------
    s.append(Paragraph("2. Unde sunt păstrate datele tale", H2))
    s.append(Paragraph(
        "Datele NU se pierd niciodată la reinstalare sau la trecerea la o versiune nouă, "
        "pentru că sunt ținute separat de programul propriu-zis:", BODY))
    s.append(info_table([
        ("%LOCALAPPDATA%\\Cimitir",
         "Baza de date (<font face='Courier'>cimitir.db</font>), documentele scanate "
         "și fotografiile."),
        ("%APPDATA%\\CimitirDesktop",
         "Licența și cheia AI (criptată)."),
    ]))
    s.append(Paragraph(
        "Sfat: fă periodic o copie a folderului <b>%LOCALAPPDATA%\\Cimitir</b> pe un stick "
        "sau în cloud. Poți lipi această cale direct în bara de adrese din Explorer.", NOTE))

    s.append(PageBreak())

    # ---------------- 3. Primii pași ----------------
    s.append(Paragraph("3. Primii pași după instalare", H2))

    s.append(Paragraph("3.1. Activarea licenței", H3))
    s.append(Paragraph(
        "La prima pornire, aplicația cere o licență. Se deschide fereastra de activare, "
        "care afișează <b>codul calculatorului (HWID)</b>.", BODY))
    s.append(steps([
        "Copiază codul HWID afișat în fereastră.",
        "Trimite-l furnizorului aplicației.",
        "Primești un fișier / cod de licență; îl lipești în fereastra de activare și "
        "apeși <b>Activează</b>.",
    ]))
    s.append(Paragraph(
        "Licența este legată de calculatorul pe care ai activat-o. Dacă schimbi "
        "calculatorul, cere o licență nouă.", NOTE))

    s.append(Paragraph("3.2. Alegerea modului de pornire", H3))
    s.append(Paragraph(
        "După activare apare o fereastră cu două opțiuni:", BODY))
    s.append(bullets([
        "<b>Doar local</b> — aplicația rulează numai pe acest calculator. "
        "Alege asta dacă lucrezi singur.",
        "<b>Server local (rețea)</b> — pornește un server în rețeaua ta; alți "
        "calculatoare sau telefonul pot deschide aplicația în browser la adresa afișată. "
        "Util și pentru scanarea documentelor cu telefonul.",
    ]))

    s.append(Paragraph("3.3. Conectarea (login)", H3))
    s.append(Paragraph("Conturile implicite la prima pornire sunt:", BODY))
    s.append(info_table([
        ("Administrator", "utilizator <b>admin</b> · parolă <b>admin123</b> — "
                          "acces complet: editare hartă, contracte, rapoarte, setări."),
        ("Operator", "utilizator <b>user</b> · parolă <b>user123</b> — "
                     "vizualizare și operațiuni zilnice, fără editarea hărții."),
    ]))
    s.append(Paragraph(
        "IMPORTANT: schimbă ambele parole imediat după prima conectare.", NOTE))

    s.append(PageBreak())

    # ---------------- 4. Cheia AI ----------------
    s.append(Paragraph("4. Configurarea cheii AI (Google Gemini)", H2))
    s.append(Paragraph(
        "Modulul <b>AI Scanare</b> citește automat buletine, certificate de deces și "
        "adeverințe de adresă, completând singur câmpurile. Pentru asta are nevoie de o "
        "cheie gratuită de la Google AI Studio.", BODY))

    s.append(Paragraph("4.1. Obținerea cheii (o singură dată, ~2 minute)", H3))
    s.append(steps([
        "Deschide în browser <b>https://aistudio.google.com/apikey</b>.",
        "Conectează-te cu un cont Google.",
        "Apasă <b>Create API key</b> (Creează cheie API).",
        "Copiază cheia afișată. Arată ca un șir lung de litere și cifre.",
    ]))
    s.append(Paragraph(
        "Cheia este personală — nu o trimite nimănui și nu o publica. În aplicație este "
        "salvată criptat și legată de calculatorul tău.", NOTE))

    s.append(Paragraph("4.2. Introducerea cheii în aplicație", H3))
    s.append(steps([
        "În aplicație, deschide <b>AI Scanare</b> și apasă <b>⚙️ Setări AI</b> "
        "(butonul există și în ferestrele de scanare).",
        "Lipește cheia în câmpul <b>Cheie API Google Gemini</b>.",
        "Apasă <b>Testează cheia</b> — trebuie să apară «Cheie validă».",
        "Lasă <b>Model</b> pe <b>auto</b> (aplicația alege singură cel mai bun model).",
        "Asigură-te că modul este <b>AI</b>, nu <b>offline</b>, apoi apasă <b>Salvează</b>.",
    ]))
    s.append(Paragraph(
        "În fereastra AI Scanare vei vedea indicatorul de stare:", BODY))
    s.append(info_table([
        ("🟢 AI activ (Gemini)", "Totul e în regulă, extragerea automată funcționează."),
        ("🟡 Mod offline", "Nu e configurată nicio cheie sau ai ales intenționat modul "
                          "offline. Câmpurile se completează manual."),
        ("🔴 Cheie AI invalidă", "Cheia a expirat sau a fost ștearsă. Apasă butonul "
                                 "<b>Repară</b> de lângă indicator și introdu o cheie nouă."),
    ]))

    s.append(Paragraph("4.3. Scanarea unui document", H3))
    s.append(steps([
        "În <b>AI Scanare</b>, alege tipul de document: Buletin CI, Certificat de deces "
        "sau Adeverință de adresă.",
        "Încarcă o poză sau un PDF (sau fotografiază cu telefonul folosind codul QR).",
        "Verifică valorile completate automat — cele cu încredere scăzută sunt marcate; "
        "corectează dacă e nevoie.",
        "Apasă <b>Confirmă</b>: se creează concesionarul sau inhumatul, iar documentul "
        "se atașează la fișă.",
    ]))
    s.append(Paragraph(
        "Toate scanările rămân în <b>Istoric scanări</b>, de unde poți vedea detaliile "
        "extrase, deschide fișa creată, re-scana documentul sau exporta lista în Excel.",
        BODY))

    # ---------------- 5. Probleme ----------------
    s.append(Paragraph("5. Probleme frecvente", H2))
    s.append(info_table([
        ("Windows blochează instalarea",
         "Apasă «Informații suplimentare» → «Executați oricum». Aplicația nu are "
         "certificat digital, de aceea apare avertismentul."),
        ("Aplicația nu pornește",
         "Repornește calculatorul și încearcă din nou. Dacă persistă, trimite "
         "furnizorului fișierele din <font face='Courier'>%LOCALAPPDATA%\\Cimitir\\logs</font>."),
        ("Scanarea nu completează câmpurile",
         "Verifică indicatorul de stare AI. Dacă e roșu, reintrodu cheia. Dacă e galben, "
         "configurează cheia din ⚙️ Setări AI."),
        ("Poza iese neclară",
         "Fotografiază documentul așezat drept, pe o suprafață uniformă, cu lumină bună "
         "și fără umbre sau reflexii."),
        ("Vreau OCR fără internet",
         "Instalează separat Tesseract OCR (cu limba română), apoi comută pe modul "
         "offline din ⚙️ Setări AI."),
    ]))

    s.append(Spacer(1, 14))
    s.append(HRFlowable(width="100%", color=LINE, thickness=1, spaceAfter=8))
    s.append(Paragraph(
        f"Cimitir Desktop {APP_VERSION} · document generat automat "
        "(tools/make_client_guide.py)",
        ParagraphStyle("F", parent=BODY, fontSize=8.5, textColor=MUTED)))
    return s


def main() -> str:
    out_dir = os.path.join(APP_DIR, "docs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "Ghid_Instalare_Cimitir.pdf")
    doc = SimpleDocTemplate(
        out_path, pagesize=A4,
        leftMargin=22 * mm, rightMargin=22 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"Cimitir Desktop {APP_VERSION} — Ghid de instalare",
        author="Cimitir Desktop",
    )
    doc.build(build_story())
    print(f"[ghid] Generat: {out_path}")
    return out_path


if __name__ == "__main__":
    main()
