"""
qr_custom_generator.py — Generator QR cu date PERSONALIZATE embedate în cod.

Diferența față de `qr_labels.py`:
    - `qr_labels.py` generează QR care conține un URL (link către pagina publică).
    - Acest modul generează QR care conține DIRECT datele selectate de utilizator
      (JSON sau text plain), fără link către internet.

Utilizatorul bifează per-cerere câmpurile pe care le vrea în codul QR
(concesionar, CNP, date contract, înhumați etc.) și obține un cod QR care,
scanat cu orice app, afișează exact datele alese — util pentru administrare
internă, arhivă, verificare rapidă în teren.

Formate de encoding:
    - JSON  → structurat, ideal pentru import în alte sisteme
    - TEXT  → linie cu linie, ușor de citit uman direct din camera telefonului

⚠️  Datele care includ CNP, adresă, telefon sunt SENSIBILE.
    Utilizatorul își asumă responsabilitatea când le include în QR (QR-ul
    poate fi citit de oricine îl scanează). Există un avertisment vizual
    când sunt bifate astfel de câmpuri.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import secrets
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

import database as db
from app_logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# HMAC — semnătură anti-falsificare pentru datele din QR
# ---------------------------------------------------------------------------
# Secretul e generat automat o singură dată și salvat în DB.settings.
# Rămâne pe calculatorul care ține baza de date; QR-urile emise pot fi verificate
# doar cu aceeași bază de date (același secret).
HMAC_SECRET_KEY = "qr_custom_hmac_secret"
SIG_LENGTH_HEX = 16  # 64 biți — suficient pentru anti-tamper (nu criptografie de stat)
SIG_MARKER = "— Semnătură"
JSON_SIG_KEY = "_sig"
JSON_GEN_KEY = "_gen"


def get_hmac_secret() -> str:
    """Returnează secretul HMAC persistent (îl generează la prima utilizare)."""
    try:
        s = db.get_setting(HMAC_SECRET_KEY)
    except Exception:
        s = None
    if not s:
        s = secrets.token_hex(32)
        try:
            db.set_setting(HMAC_SECRET_KEY, s)
            logger.info("QR HMAC secret generat și salvat")
        except Exception:
            logger.exception("Nu am putut salva HMAC secret")
    return s


def rotate_hmac_secret() -> str:
    """
    Generează un secret nou (invalidează TOATE QR-urile emise anterior).
    De folosit doar dacă bănuiești o compromitere.
    """
    new_secret = secrets.token_hex(32)
    db.set_setting(HMAC_SECRET_KEY, new_secret)
    logger.warning("QR HMAC secret ROTIT — toate QR-urile vechi au devenit invalide")
    return new_secret


def _compute_sig(payload_bytes: bytes) -> str:
    """HMAC-SHA256 trunchiat la SIG_LENGTH_HEX caractere hex."""
    secret = get_hmac_secret().encode("utf-8")
    mac = hmac.new(secret, payload_bytes, hashlib.sha256).hexdigest()
    return mac[:SIG_LENGTH_HEX]


def _now_iso_min() -> str:
    """Timestamp scurt UTC: YYYY-MM-DDTHH:MM (fără secunde/tz)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")


# ---------------------------------------------------------------------------
# Definirea câmpurilor DISPONIBILE pentru includere în QR
# ---------------------------------------------------------------------------
# Fiecare câmp are:
#   - label       : eticheta afișată în UI + în output text
#   - group       : secțiunea din UI (Loc / Titular / Contract / Înhumați)
#   - source      : "loc" | "concesionar" | "contract" | "inhumati"
#   - sensitive   : True → e date cu caracter personal (CNP, adresă, telefon)
FIELDS: Dict[str, Dict[str, Any]] = {
    # ---- LOC DE VECI ----
    "cod_loc":         {"label": "Cod loc",          "group": "Loc de veci",  "source": "loc",         "sensitive": False},
    "sector":          {"label": "Sector",           "group": "Loc de veci",  "source": "loc",         "sensitive": False},
    "parcela":         {"label": "Parcelă",          "group": "Loc de veci",  "source": "loc",         "sensitive": False},
    "rand":            {"label": "Rând",             "group": "Loc de veci",  "source": "loc",         "sensitive": False},
    "numar":           {"label": "Număr",            "group": "Loc de veci",  "source": "loc",         "sensitive": False},
    "suprafata":       {"label": "Suprafață (m²)",   "group": "Loc de veci",  "source": "loc",         "sensitive": False},
    "capacitate":      {"label": "Capacitate",       "group": "Loc de veci",  "source": "loc",         "sensitive": False},
    "tip":             {"label": "Tip loc",          "group": "Loc de veci",  "source": "loc",         "sensitive": False},
    "categorie":       {"label": "Categorie",        "group": "Loc de veci",  "source": "loc",         "sensitive": False},
    "stare":           {"label": "Stare",            "group": "Loc de veci",  "source": "loc",         "sensitive": False},
    "disponibilitate": {"label": "Disponibilitate",  "group": "Loc de veci",  "source": "loc",         "sensitive": False},
    "nr_vechi":        {"label": "Nr. vechi",        "group": "Loc de veci",  "source": "loc",         "sensitive": False},
    "numar_cf":        {"label": "Nr. CF",           "group": "Loc de veci",  "source": "loc",         "sensitive": False},

    # ---- CONCESIONAR / TITULAR ----
    "conc_nume":       {"label": "Nume titular",      "group": "Titular / Concesionar", "source": "concesionar", "sensitive": False},
    "conc_prenume":    {"label": "Prenume titular",   "group": "Titular / Concesionar", "source": "concesionar", "sensitive": False},
    "conc_cnp":        {"label": "CNP titular",       "group": "Titular / Concesionar", "source": "concesionar", "sensitive": True},
    "conc_bi_ci":      {"label": "Serie/nr. BI/CI",   "group": "Titular / Concesionar", "source": "concesionar", "sensitive": True},
    "conc_adresa":     {"label": "Adresă titular",    "group": "Titular / Concesionar", "source": "concesionar", "sensitive": True},
    "conc_telefon":    {"label": "Telefon titular",   "group": "Titular / Concesionar", "source": "concesionar", "sensitive": True},
    "conc_email":      {"label": "Email titular",     "group": "Titular / Concesionar", "source": "concesionar", "sensitive": True},

    # ---- CONTRACT ----
    "contract_nr":     {"label": "Nr. contract",       "group": "Contract concesiune", "source": "contract", "sensitive": False},
    "contract_data":   {"label": "Data contract",      "group": "Contract concesiune", "source": "contract", "sensitive": False},
    "contract_start":  {"label": "Început valabilitate", "group": "Contract concesiune", "source": "contract", "sensitive": False},
    "contract_end":    {"label": "Sfârșit valabilitate", "group": "Contract concesiune", "source": "contract", "sensitive": False},
    "contract_status": {"label": "Status contract (valabil/expirat)", "group": "Contract concesiune", "source": "contract", "sensitive": False},
    "contract_valab":  {"label": "Durată valabilitate", "group": "Contract concesiune", "source": "contract", "sensitive": False},

    # ---- ÎNHUMAȚI ----
    "inh_lista":       {"label": "Lista înhumaților (nume + prenume)",  "group": "Înhumați", "source": "inhumati", "sensitive": False},
    "inh_data_nastere": {"label": "Data nașterii",                       "group": "Înhumați", "source": "inhumati", "sensitive": False},
    "inh_data_deces":  {"label": "Data decesului",                      "group": "Înhumați", "source": "inhumati", "sensitive": False},
    "inh_data_inhumare": {"label": "Data înhumării",                    "group": "Înhumați", "source": "inhumati", "sensitive": False},
    "inh_cnp":         {"label": "CNP înhumat",                         "group": "Înhumați", "source": "inhumati", "sensitive": True},
    "inh_profesie":    {"label": "Profesie",                            "group": "Înhumați", "source": "inhumati", "sensitive": False},
    "inh_religie":     {"label": "Religie",                             "group": "Înhumați", "source": "inhumati", "sensitive": False},
}


GROUP_ORDER = [
    "Loc de veci",
    "Titular / Concesionar",
    "Contract concesiune",
    "Înhumați",
]


DEFAULT_KEYS = [
    "cod_loc", "sector", "parcela", "rand", "numar",
    "conc_nume", "conc_prenume",
    "contract_nr", "contract_data",
    "inh_lista",
]


SETTING_KEY = "qr_custom_default_fields"


# ---------------------------------------------------------------------------
# Persistă/citește lista implicită din DB.settings
# ---------------------------------------------------------------------------
def get_saved_default_keys() -> List[str]:
    try:
        v = db.get_setting(SETTING_KEY)
    except Exception:
        v = None
    if v is None:
        return list(DEFAULT_KEYS)
    keys = [k.strip() for k in v.split(",") if k.strip() and k.strip() in FIELDS]
    return keys or list(DEFAULT_KEYS)


def save_default_keys(keys: List[str]) -> None:
    valid = [k for k in keys if k in FIELDS]
    try:
        db.set_setting(SETTING_KEY, ",".join(valid))
    except Exception:
        logger.exception("Nu am putut salva %s", SETTING_KEY)


# ---------------------------------------------------------------------------
# Extrage payload-ul pentru un loc, filtrat pe cheile bifate
# ---------------------------------------------------------------------------
def build_payload(cod_loc: str, keys: List[str]) -> Dict[str, Any]:
    """
    Returnează un dict cu datele selectate pentru locul dat.
    Structura de rezultat:
        {
            "cod_loc": "...",
            "loc": { ... },
            "concesionar": { ... },
            "contract": { ... },
            "inhumati": [ { ... }, ... ],
        }
    Doar cheile bifate produc conținut. Secțiunile goale sunt eliminate.
    """
    loc = db.load_loc_by_cod(cod_loc) or {}
    result: Dict[str, Any] = {"cod_loc": cod_loc}

    # -------- LOC --------
    loc_out: Dict[str, Any] = {}
    for k, meta in FIELDS.items():
        if meta["source"] != "loc" or k not in keys:
            continue
        v = loc.get(k)
        if v not in (None, "", 0) or (isinstance(v, (int, float)) and v == 0 and k == "suprafata"):
            loc_out[meta["label"]] = v
    if loc_out:
        result["loc"] = loc_out

    # -------- CONTRACT & CONCESIONAR --------
    active_contract: Dict[str, Any] = {}
    concesionar: Dict[str, Any] = {}
    try:
        contracts = [c for c in db.load_contracte() if c.get("cod_loc") == cod_loc]
        if contracts:
            active_contract = sorted(
                contracts,
                key=lambda c: str(c.get("data_contract") or ""),
                reverse=True,
            )[0]
            cid = active_contract.get("concesionar_id")
            if cid:
                try:
                    concesionar = db.get_concesionar(int(cid)) or {}
                except Exception:
                    concesionar = {}
    except Exception:
        logger.exception("Eroare la încărcarea contractului pentru %s", cod_loc)

    conc_out: Dict[str, Any] = {}
    conc_map = {
        "conc_nume": "nume", "conc_prenume": "prenume", "conc_cnp": "cnp",
        "conc_bi_ci": "bi_ci", "conc_adresa": "adresa",
        "conc_telefon": "telefon", "conc_email": "email",
    }
    for k, dbk in conc_map.items():
        if k in keys and concesionar.get(dbk):
            conc_out[FIELDS[k]["label"]] = concesionar[dbk]
    if conc_out:
        result["concesionar"] = conc_out

    contract_out: Dict[str, Any] = {}
    contract_map = {
        "contract_nr": "numar_contract",
        "contract_data": "data_contract",
        "contract_start": "data_inceput",
        "contract_end": "data_sfarsit",
        "contract_valab": "valabilitate",
    }
    for k, dbk in contract_map.items():
        if k in keys and active_contract.get(dbk):
            contract_out[FIELDS[k]["label"]] = active_contract[dbk]
    if "contract_status" in keys and active_contract:
        end_str = str(active_contract.get("data_sfarsit") or "")[:10]
        status = "necunoscut"
        try:
            end_dt = datetime.strptime(end_str, "%Y-%m-%d").date()
            status = "Valabil" if end_dt >= date.today() else "Expirat"
        except Exception:
            pass
        contract_out[FIELDS["contract_status"]["label"]] = status
    if contract_out:
        result["contract"] = contract_out

    # -------- ÎNHUMAȚI --------
    inh_show_list = "inh_lista" in keys
    inh_keys_dbmap = {
        "inh_data_nastere": "data_nasterii",
        "inh_data_deces": "data_deces",
        "inh_data_inhumare": "data_inhumare",
        "inh_cnp": "cnp",
        "inh_profesie": "profesie",
        "inh_religie": "religie",
    }
    any_inh_key = inh_show_list or any(k in keys for k in inh_keys_dbmap.keys())
    if any_inh_key:
        entries: List[Dict[str, Any]] = []
        try:
            inhumati = db.load_inhumati_for_loc(cod_loc) or []
        except Exception:
            logger.exception("Eroare la încărcarea înhumaților pentru %s", cod_loc)
            inhumati = []
        for inh in inhumati:
            entry: Dict[str, Any] = {}
            if inh_show_list:
                nume = f"{inh.get('nume', '')} {inh.get('prenume', '')}".strip()
                if nume:
                    entry[FIELDS["inh_lista"]["label"]] = nume
            for k, dbk in inh_keys_dbmap.items():
                if k in keys and inh.get(dbk):
                    entry[FIELDS[k]["label"]] = inh[dbk]
            if entry:
                entries.append(entry)
        if entries:
            result["inhumati"] = entries

    return result


# ---------------------------------------------------------------------------
# Encoding text/JSON  (cu suport pentru semnătură HMAC)
# ---------------------------------------------------------------------------
def payload_to_text(payload: Dict[str, Any], sign: bool = False) -> str:
    """Formatare human-readable: linii cu 'Eticheta: valoare' grupate.

    Dacă `sign=True`, adaugă la sfârșit o secțiune '— Semnătură' cu
    HMAC-SHA256 trunchiat + data generării, ca datele să nu poată fi falsificate.
    """
    lines: List[str] = []
    lines.append(f"Loc de veci: {payload.get('cod_loc', '')}")

    section_order = [("loc", "— Loc"), ("concesionar", "— Titular"),
                     ("contract", "— Contract"), ("inhumati", "— Înhumați")]

    for skey, header in section_order:
        val = payload.get(skey)
        if not val:
            continue
        lines.append(header)
        if skey == "inhumati":
            for i, entry in enumerate(val, 1):
                lines.append(f"  {i}.")
                for k, v in entry.items():
                    lines.append(f"     {k}: {v}")
        else:
            for k, v in val.items():
                lines.append(f"  {k}: {v}")

    text = "\n".join(lines)

    if sign:
        gen = _now_iso_min()
        # semnăm textul curent + generation timestamp (nu semnătura însăși)
        to_sign = f"{text}\n{gen}".encode("utf-8")
        sig = _compute_sig(to_sign)
        text = f"{text}\n{SIG_MARKER}\n  sig: {sig}\n  gen: {gen}"

    return text


def payload_to_json(payload: Dict[str, Any], sign: bool = False) -> str:
    """JSON compact al payload-ului. Cu `sign=True`, adaugă _sig + _gen."""
    if sign:
        # semnăm reprezentarea deterministă (sortată) a payload-ului
        gen = _now_iso_min()
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"))
        to_sign = f"{canonical}|{gen}".encode("utf-8")
        sig = _compute_sig(to_sign)
        out = dict(payload)
        out[JSON_GEN_KEY] = gen
        out[JSON_SIG_KEY] = sig
        return json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Verificare semnătură (pt. un QR scanat / lipit ca text)
# ---------------------------------------------------------------------------
def verify_signed(content: str) -> Dict[str, Any]:
    """
    Verifică conținutul unui QR (text sau JSON) și întoarce:
        {
            "signed": True/False,           # dacă avea secțiunea de semnătură
            "valid":  True/False,           # dacă semnătura se potrivește cu secretul curent
            "format": "text" | "json",
            "gen":    "YYYY-MM-DDTHH:MM"    # data generării, dacă disponibilă
            "sig_present": "abcdef..."       # semnătura găsită
            "sig_expected": "abcdef..."      # semnătura recomputată
            "payload": ...                   # payload-ul fără semnătură (dict pt JSON, str pt text)
            "error":  str                   # motiv dacă `valid=False`
        }
    Semnătura invalidă poate însemna:
        - datele au fost modificate după emitere
        - QR-ul a fost emis cu alt secret (alt cimitir sau după rotație)
    """
    result: Dict[str, Any] = {
        "signed": False, "valid": False, "format": "unknown",
        "gen": "", "sig_present": "", "sig_expected": "",
        "payload": None, "error": "",
    }
    if not content or not content.strip():
        result["error"] = "Conținut gol."
        return result

    stripped = content.strip()

    # ---- JSON path ----
    if stripped.startswith("{"):
        result["format"] = "json"
        try:
            data = json.loads(stripped)
        except Exception as e:
            result["error"] = f"JSON invalid: {e}"
            return result
        if JSON_SIG_KEY not in data or JSON_GEN_KEY not in data:
            result["error"] = "QR-ul nu conține o semnătură."
            result["payload"] = data
            return result
        result["signed"] = True
        result["sig_present"] = str(data[JSON_SIG_KEY])
        result["gen"] = str(data[JSON_GEN_KEY])
        payload = {k: v for k, v in data.items() if k not in (JSON_SIG_KEY, JSON_GEN_KEY)}
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"))
        expected = _compute_sig(f"{canonical}|{result['gen']}".encode("utf-8"))
        result["sig_expected"] = expected
        result["payload"] = payload
        result["valid"] = hmac.compare_digest(expected, result["sig_present"])
        if not result["valid"]:
            result["error"] = ("Semnătura NU se potrivește. "
                               "Datele au fost modificate SAU QR-ul a fost emis "
                               "cu alt secret (alt cimitir / după rotație).")
        return result

    # ---- TEXT path ----
    result["format"] = "text"
    marker = f"\n{SIG_MARKER}\n"
    if marker not in stripped:
        result["error"] = "QR-ul nu conține o semnătură."
        result["payload"] = stripped
        return result
    body, sig_block = stripped.rsplit(marker, 1)
    result["signed"] = True
    result["payload"] = body
    sig_val = ""
    gen_val = ""
    for line in sig_block.splitlines():
        s = line.strip()
        if s.startswith("sig:"):
            sig_val = s[4:].strip()
        elif s.startswith("gen:"):
            gen_val = s[4:].strip()
    if not sig_val or not gen_val:
        result["error"] = "Semnătura este malformată (lipsește sig: sau gen:)."
        return result
    result["sig_present"] = sig_val
    result["gen"] = gen_val
    expected = _compute_sig(f"{body}\n{gen_val}".encode("utf-8"))
    result["sig_expected"] = expected
    result["valid"] = hmac.compare_digest(expected, sig_val)
    if not result["valid"]:
        result["error"] = ("Semnătura NU se potrivește. "
                           "Datele au fost modificate SAU QR-ul a fost emis "
                           "cu alt secret (alt cimitir / după rotație).")
    return result


# ---------------------------------------------------------------------------
# Generare QR image (PIL) — cu adaptare automată de versiune
# ---------------------------------------------------------------------------
def make_qr_image(data: str, box_size: int = 10, border: int = 2,
                  ec_level: str = "M") -> Image.Image:
    """
    Codifică `data` într-un QR PIL Image.
    ec_level: L / M / Q / H → toleranță la deteriorare (mai mare = QR mai dens).
    """
    import qrcode
    level_map = {
        "L": qrcode.constants.ERROR_CORRECT_L,
        "M": qrcode.constants.ERROR_CORRECT_M,
        "Q": qrcode.constants.ERROR_CORRECT_Q,
        "H": qrcode.constants.ERROR_CORRECT_H,
    }
    qr = qrcode.QRCode(
        version=None,  # auto — versiune 40 max
        error_correction=level_map.get(ec_level, qrcode.constants.ERROR_CORRECT_M),
        box_size=box_size,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


def _pil_font(size: int) -> ImageFont.FreeTypeFont:
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
                pass
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
                pass
    return _pil_font(size)


def make_qr_label_image(cod_loc: str, qr_data: str,
                        include_text_below: bool = True,
                        title: Optional[str] = None,
                        size_px: Tuple[int, int] = (600, 720),
                        ec_level: str = "M") -> Image.Image:
    """Compune o etichetă completă: (opțional) titlu, QR mare, sub el codul locului."""
    W, H = size_px
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)

    y = 12
    if title:
        f_title = _pil_font_bold(int(W * 0.048))
        bbox = draw.textbbox((0, 0), title, font=f_title)
        tw = bbox[2] - bbox[0]
        draw.text(((W - tw) // 2, y), title, fill="black", font=f_title)
        y += (bbox[3] - bbox[1]) + 10

    text_reserve = int(H * 0.14) if include_text_below else 20
    qr_side = min(W - 24, H - y - text_reserve - 20)

    qr = make_qr_image(qr_data, box_size=10, border=2, ec_level=ec_level)
    qr = qr.resize((qr_side, qr_side), Image.NEAREST)
    img.paste(qr, ((W - qr_side) // 2, y))
    y += qr_side + 10

    if include_text_below:
        f_code = _pil_font_bold(int(W * 0.06))
        f_info = _pil_font(int(W * 0.032))
        bbox = draw.textbbox((0, 0), cod_loc, font=f_code)
        tw = bbox[2] - bbox[0]
        draw.text(((W - tw) // 2, y), cod_loc, fill="black", font=f_code)
        y += (bbox[3] - bbox[1]) + 6

        info = "Datele sunt embedate direct în cod QR"
        bbox = draw.textbbox((0, 0), info, font=f_info)
        tw = bbox[2] - bbox[0]
        draw.text(((W - tw) // 2, y), info, fill="#555555", font=f_info)

    return img


def generate_and_save_png(cod_loc: str, keys: List[str], output_path: str,
                          format: str = "text",
                          ec_level: str = "M",
                          include_text_below: bool = True,
                          title: Optional[str] = None,
                          size_px: Tuple[int, int] = (600, 720),
                          sign: bool = False) -> Tuple[str, str]:
    """
    Generează QR embedat (JSON sau TEXT) și îl salvează ca PNG.

    Args:
        sign: dacă True, adaugă semnătură HMAC-SHA256 (anti-falsificare).

    Return: (output_path, encoded_data) — pentru inspecție.
    """
    payload = build_payload(cod_loc, keys)
    if format == "json":
        data = payload_to_json(payload, sign=sign)
    else:
        data = payload_to_text(payload, sign=sign)

    img = make_qr_label_image(cod_loc, data, include_text_below=include_text_below,
                              title=title, size_px=size_px, ec_level=ec_level)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    img.save(output_path, format="PNG", dpi=(300, 300))
    logger.info("QR custom PNG salvat: %s (cod=%s, chei=%d, format=%s, sign=%s)",
                output_path, cod_loc, len(keys), format, sign)
    return output_path, data


def build_qr_pixmap(cod_loc: str, keys: List[str], format: str = "text",
                    ec_level: str = "M", side_px: int = 300,
                    sign: bool = False) -> Tuple[bytes, str, int]:
    """
    Pentru preview live în UI: întoarce (png_bytes, data_encoded, data_length).
    """
    payload = build_payload(cod_loc, keys)
    if format == "json":
        data = payload_to_json(payload, sign=sign)
    else:
        data = payload_to_text(payload, sign=sign)
    img = make_qr_image(data, box_size=8, border=2, ec_level=ec_level)
    img = img.resize((side_px, side_px), Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), data, len(data.encode("utf-8"))
