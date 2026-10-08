"""
web_public.py — Portalul PUBLIC al cimitirului (fără autentificare).

Se montează peste aceeași aplicație FastAPI din `web_server.py` și folosește
EXACT aceeași bază de date (`cimitir.db`, prin `database.py`).

Oferă:
    - căutare publică a locurilor de veci (după nume decedat / localizare / cod)
    - hartă interactivă (SVG, desenată local din poligoanele `points` — fără hărți externe)
    - fișă publică a unui loc de veci (DOAR date publicabile — fără CNP / adresă / telefon)
    - formular „Cerere de concesionare" cu anexare documente scanate
      → salvate în tabelul `cereri_concesionare` + `documents(entity_type='cerere')`

Rutele publice se înregistrează cu `register(app)` ÎNAINTE de mount-ul StaticFiles
din `web_server.py`, ca să aibă prioritate față de aplicația React a operatorului.

Confidențialitate: CNP, adresa și telefonul persoanelor NU sunt expuse public,
indiferent de context.
"""
from __future__ import annotations

import html as _html
from typing import List, Optional

from fastapi import FastAPI, APIRouter, HTTPException, Form, File, UploadFile, Query, Request
from fastapi.responses import HTMLResponse

import store as cim
import cimitir_public
import cimitir_requests
import citizen_api
import portal_ratelimit
import json
from datetime import datetime

# extensii acceptate pentru documentele scanate
_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif", ".pdf",
                ".tif", ".tiff", ".bmp", ".gif"}
_MAX_FILE_BYTES = 25 * 1024 * 1024  # 25 MB / fișier (local, fără limite de proxy)
_MAX_FILES = 10
_MAX_TEXT_CHARS = 2000

_STATUS_COLORS = {"liber": "#16a34a", "rezervat": "#f59e0b", "indisponibil": "#6b7280"}
_STATUS_LABELS = {"liber": "Disponibil pentru concesionare", "rezervat": "Rezervat", "indisponibil": "Indisponibil pentru concesionare"}
_AVAIL_TO_STATUS = {"disponibil": "liber", "rezervat": "rezervat", "indisponibil": "indisponibil"}


def _plot_status(loc: dict) -> str:
    """Disponibilitate pentru concesionare; independentă de înhumări și de expirarea contractelor."""
    return _AVAIL_TO_STATUS[cimitir_public.availability(loc)]


def _e(v) -> str:
    """Escape HTML sigur."""
    return _html.escape("" if v is None else str(v))


# =============================================================================
# API PUBLIC (JSON) — fără autentificare
# =============================================================================
public_api = APIRouter(prefix="/api/public")


@public_api.get("/health")
def public_health():
    """Verificare de pornire: fără date, doar dacă baza răspunde."""
    import cimitir_config
    import cimitir_registry
    if cimitir_registry.current_binding() is not None or cimitir_registry.legacy_fallback_allowed(cimitir_config.data_dir()):
        cim.count_cereri_noi()  # fără registru deschis în MAIN nu atingem (și nu creăm) nicio bază
    return {"ok": True, "instance": cimitir_config.instance_id()}


def _full_name(inh: dict) -> str:
    return f"{inh.get('nume', '')} {inh.get('prenume', '')}".strip()


def _search_row(loc: dict) -> dict:
    return {
        "cod_loc": loc["cod_loc"],
        "sector": loc.get("sector") or "",
        "parcela": loc.get("parcela") or "",
        "rand": loc.get("rand") or "",
        "numar": loc.get("numar") or "",
        "status": _plot_status(loc),
        "inhumati": [],
    }


def _add_found(results: dict, loc: dict) -> None:
    cod = loc.get("cod_loc")
    if cod and cod not in results:
        results[cod] = _search_row(loc)


def _search_by_location(results: dict, q: str) -> None:
    for field in ("cod_loc", "sector", "parcela", "rand", "numar"):
        for loc in cim.search_locuri({field: q}):
            _add_found(results, loc)


def burials_public() -> bool:
    """Înhumații apar public doar dacă operatorul a bifat publicarea. Înainte de sincronizarea pe entități (Etapa 6) serverul
    avea doar înhumații publicați, deci comportamentul vechi (afișare) rămâne; după, serverul are TOT registrul și
    decide indicatorul `publish_burials` trimis de MAIN."""
    if (cim.get_setting("entity_sync_active") or "") == "1":
        return (cim.get_setting("publish_burials") or "") == "1"
    return True


def _search_by_deceased(results: dict, q: str) -> None:
    if not burials_public():
        return
    for key in ("nume", "prenume"):
        for inh in cim.load_all_inhumati({key: q}):
            cod = inh.get("cod_loc")
            loc = cim.load_loc_by_cod(cod) if cod else None
            if not loc:
                continue
            _add_found(results, loc)
            name = _full_name(inh)
            if name and name not in results[cod]["inhumati"]:
                results[cod]["inhumati"].append(name)


def _fill_deceased_names(results: dict) -> None:
    if not burials_public():
        return
    for cod, item in results.items():
        if item["inhumati"]:
            continue
        item["inhumati"].extend(name for name in (_full_name(inh) for inh in cim.load_inhumati_for_loc(cod)) if name)


@public_api.get("/search")
def public_search(q: str = "", limit: int = Query(60, ge=1, le=200)):
    """
    Căutare liberă: potrivește locuri după cod/sector/parcelă/rând/număr
    ȘI după numele decedaților înhumați. Returnează locuri unice.
    """
    q = (q or "").strip()
    results: dict = {}
    if q:
        _search_by_location(results, q)
        _search_by_deceased(results, q)
    else:
        for loc in cim.list_locuri()[:limit]:
            _add_found(results, loc)
    _fill_deceased_names(results)
    return {"count": len(results), "results": list(results.values())[:limit]}


@public_api.get("/map")
def public_map():
    """Hartă reală: poligoane, sectoare, fundal, scară, coduri, dimensiuni (doar cu scară validă), disponibilitate."""
    data = cimitir_public.public_map()
    for plot in data["plots"]:
        plot["status"] = _AVAIL_TO_STATUS[plot["disponibilitate"]]
        plot["color"] = _STATUS_COLORS[plot["status"]]
    return data


@public_api.get("/map/background")
def public_map_background():
    from fastapi.responses import Response
    img = cimitir_public.background_image(cim)
    if img is None:
        raise HTTPException(status_code=404, detail="Fără fundal")
    return Response(content=img[0], media_type=img[1])


def _public_loc_payload(cod_loc: str) -> Optional[dict]:
    loc = cim.load_loc_by_cod(cod_loc)
    if not loc:
        return None
    inhumati = []
    for inh in (cim.load_inhumati_for_loc(cod_loc) if burials_public() else []):
        nume = f"{inh.get('nume', '')} {inh.get('prenume', '')}".strip()
        an_n = (inh.get("data_nasterii") or "")[-4:] if inh.get("data_nasterii") else ""
        an_d = (inh.get("data_deces") or "")[-4:] if inh.get("data_deces") else ""
        inhumati.append({
            "nume": nume,
            "an_nastere": an_n,
            "an_deces": an_d,
            "data_deces": inh.get("data_deces") or "",
            "data_inhumare": inh.get("data_inhumare") or "",
        })
    st = _plot_status(loc)
    return {
        "cod_loc": loc.get("cod_loc") or "",
        "sector": loc.get("sector") or "",
        "parcela": loc.get("parcela") or "",
        "rand": loc.get("rand") or "",
        "numar": loc.get("numar") or "",
        "suprafata": loc.get("suprafata") or "",
        "capacitate": loc.get("capacitate") or "",
        "tip": loc.get("tip") or "",
        "categorie": loc.get("categorie") or "",
        "status": st,
        "status_label": _STATUS_LABELS.get(st, st),
        "disponibilitate": cimitir_public.availability(loc),
        "are_inhumari": bool(inhumati) or bool(loc.get("are_inhumari")),
        **cimitir_public.geometry_metrics(cimitir_public._points(loc.get("points")), cimitir_public.parse_ppm(cim.get_setting("px_per_meter"))),
        "inhumati": inhumati,
    }


@public_api.get("/loc/{cod_loc}")
def public_loc(cod_loc: str):
    data = _public_loc_payload(cod_loc)
    if not data:
        raise HTTPException(status_code=404, detail="Locul nu există")
    return data


@public_api.post("/cerere")
async def public_cerere(
    request: Request,
    nume: str = Form(""),
    prenume: str = Form(""),
    cnp: str = Form(""),
    bi_ci: str = Form(""),
    adresa: str = Form(""),
    telefon: str = Form(""),
    email: str = Form(""),
    grad_rudenie: str = Form(""),
    observatii: str = Form(""),
    cod_loc: str = Form(""),
    tip: str = Form("concesionare"),
    nume_decedat: str = Form(""),
    prenume_decedat: str = Form(""),
    data_deces: str = Form(""),
    privacy_ack: str = Form(""),
    files: List[UploadFile] = File(default=[]),
):
    """Cerere de concesionare / actualizare înhumat + documente validate. Nu acordă automat concesiunea."""
    portal_ratelimit.enforce(request, "cerere")
    citizen = citizen_api.current_citizen(request)
    applicant = {"nume": nume, "prenume": prenume, "cnp": cnp, "bi_ci": bi_ci, "adresa": adresa, "telefon": telefon,
                 "email": email, "grad_rudenie": grad_rudenie, "observatii": observatii, "cod_loc": cod_loc}
    deceased = {"nume_decedat": nume_decedat, "prenume_decedat": prenume_decedat, "data_deces": data_deces}
    _check_submission_allowed(citizen, privacy_ack)
    _validate_cerere_input(tip, {**applicant, **deceased}, len(files or []))
    cod_loc = cod_loc.strip()
    applicant["cod_loc"] = cod_loc
    _check_target(tip, cod_loc, cnp, nume_decedat)

    cerere_id = cim.add_cerere_concesionare(applicant)
    if not cerere_id:
        raise HTTPException(status_code=500, detail="Nu am putut salva cererea.")
    _store_cerere_meta(cerere_id, citizen, tip, deceased, privacy_ack == "1")
    saved_docs, rejected = await citizen_api.save_documents(cerere_id, files)
    tracking = cim.get_cerere_concesionare(cerere_id) or {}
    code = tracking.get("cod_urmarire", "")
    cimitir_requests.audit("citizen" if citizen else "anonymous", citizen["email"] if citizen else "", "request_submitted", code,
                           f"{tip} loc={cod_loc} docs={saved_docs}")
    return {"id": cerere_id, "cod_urmarire": code, "documente": saved_docs, "respinse": rejected, "tip": tip,
            "mesaj": "Cererea a fost trimisă. Vă mulțumim!"}


def _check_submission_allowed(citizen: Optional[dict], privacy_ack: str) -> None:
    if not citizen_api.require_account():
        return
    if not citizen:
        raise HTTPException(status_code=401, detail="Autentificați-vă în contul de cetățean pentru a depune o cerere.")
    if privacy_ack != "1":
        raise HTTPException(status_code=422, detail="Confirmați că ați luat cunoștință de informarea privind prelucrarea datelor.")


def _validate_cerere_input(tip: str, texts: dict, file_count: int) -> None:
    if tip not in cimitir_requests.REQUEST_TYPES:
        raise HTTPException(status_code=422, detail="Tip de cerere necunoscut.")
    if not (texts["nume"] or "").strip() or not (texts["prenume"] or "").strip():
        raise HTTPException(status_code=400, detail="Numele și prenumele sunt obligatorii.")
    if any(len(v or "") > _MAX_TEXT_CHARS for v in texts.values()):
        raise HTTPException(status_code=413, detail="Un câmp este prea lung.")
    if (cim.get_setting("portal_requests_enabled") or "1") != "1":
        raise HTTPException(status_code=503, detail="Depunerea online este dezactivată temporar.")
    if file_count > _MAX_FILES:
        raise HTTPException(status_code=413, detail=f"Maxim {_MAX_FILES} fișiere.")


def _check_target(tip: str, cod_loc: str, cnp: str, nume_decedat: str) -> None:
    if tip == "concesionare" and cod_loc:
        target = cim.load_loc_by_cod(cod_loc)
        if target and cimitir_public.availability(target) != "disponibil":
            raise HTTPException(status_code=409, detail="Locul nu este disponibil pentru concesionare.")
    if tip == "actualizare_inhumat":
        _check_holder(cod_loc, cnp, nume_decedat)


def _store_cerere_meta(cerere_id: int, citizen: Optional[dict], tip: str, deceased: dict, acknowledged: bool) -> None:
    payload = json.dumps(deceased) if tip == "actualizare_inhumat" else None
    conn = cim.get_connection()
    try:
        conn.execute("UPDATE cereri_concesionare SET citizen_id=?, tip=?, payload=?, privacy_version=?, privacy_ack_at=? WHERE id=?",
                     (citizen["id"] if citizen else None, tip, payload,
                      citizen_api.privacy_info()["version"] if acknowledged else None,
                      datetime.now().isoformat(timespec="seconds") if acknowledged else None, cerere_id))
    finally:
        conn.close()


_TRACK_LABELS = citizen_api.LABELS


def _check_holder(cod_loc: str, cnp: str, nume_decedat: str) -> None:
    """Solicitările privind o concesiune existentă cer drept asupra locului (titular, verificat prin hash cu sare)."""
    if not cod_loc or not (nume_decedat or "").strip() or len("".join(ch for ch in cnp if ch.isdigit())) != 13:
        raise HTTPException(status_code=422, detail="Pentru actualizarea evidenței sunt necesare: cod loc, CNP titular, numele persoanei înhumate.")
    conn = cim.get_connection()
    try:
        total = conn.execute(f"SELECT COUNT(*) AS n FROM loc_holders WHERE {cim.tenant_scope()}").fetchone()["n"]
        salt = cim.get_setting("entitlement_salt") or ""
        ok = conn.execute(f"SELECT 1 FROM loc_holders WHERE cod_loc=? AND holder_hash=? AND {cim.tenant_scope()}", (cod_loc, cimitir_requests.holder_hash(salt, cnp))).fetchone()
    finally:
        conn.close()
    if not total:
        raise HTTPException(status_code=409, detail="Verificarea titularului nu este disponibilă momentan.")
    if not ok:
        raise HTTPException(status_code=403, detail="Nu figurați ca titular al acestui loc.")


@public_api.get("/cerere/{cod_urmarire}")
def public_cerere_status(cod_urmarire: str):
    """Doar starea cererii, după cod de urmărire. Nu întoarce date personale."""
    row = cim.get_cerere_tracking(cod_urmarire)
    if not row:
        raise HTTPException(status_code=404, detail="Cod de urmărire necunoscut")
    return {"cod_urmarire": row["cod_urmarire"], "status": row["status"],
            "status_label": _TRACK_LABELS.get(row["status"], row["status"]),
            "depusa_la": row["created_at"], "actualizata_la": row["reviewed_at"]}


# =============================================================================
# PAGINI HTML PUBLICE — portal modern, fără zonă de administrare
# =============================================================================
_CSS = r"""
.plotpanel{margin-top:14px;padding:16px;border:1px solid #d8dfda;border-radius:14px;background:#f8faf9}.plothead{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}.plothead h3{margin:0}.plotpanel .meta{margin:10px 0}
:root{--ink:#17211b;--muted:#66736b;--line:#e5e9e5;--paper:#fff;--brand:#244d3d;--gold:#b58a46;--shadow:0 18px 50px rgba(30,55,43,.10)}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;font-family:Inter,"Segoe UI",Arial,sans-serif;color:var(--ink);background:#f7f8f6;line-height:1.5}a{text-decoration:none;color:inherit}button,input,textarea{font:inherit}
.shell{min-height:100vh;background:radial-gradient(circle at 88% 8%,#e8efe9 0,transparent 27%),linear-gradient(180deg,#fbfcfa 0,#f4f6f3 100%)}
.nav{position:sticky;top:0;z-index:20;background:rgba(255,255,255,.90);backdrop-filter:blur(14px);border-bottom:1px solid rgba(36,77,61,.10)}.navin{max-width:1180px;margin:auto;height:76px;padding:0 22px;display:flex;align-items:center;gap:24px}.brand{display:flex;align-items:center;gap:12px;margin-right:auto;min-width:0}.crest{width:42px;height:42px;border-radius:14px;display:grid;place-items:center;background:linear-gradient(145deg,var(--brand),#18372b);color:#fff;box-shadow:0 8px 18px rgba(36,77,61,.22);font-size:21px}.brandtext{min-width:0}.brandname{font-weight:800;font-size:16px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.brandsub{color:var(--muted);font-size:11px;letter-spacing:.08em;text-transform:uppercase}.navlinks{display:flex;align-items:center;gap:6px}.navlinks a{padding:10px 13px;border-radius:10px;font-size:13px;font-weight:700;color:#536159}.navlinks a:hover,.navlinks a.active{background:#edf2ee;color:var(--brand)}.navcta{background:var(--brand)!important;color:white!important;padding:11px 15px!important;box-shadow:0 7px 16px rgba(36,77,61,.15)}
.hero{max-width:1180px;margin:0 auto;padding:70px 22px 38px;display:grid;grid-template-columns:1.15fr .85fr;gap:42px;align-items:center}.eyebrow{display:inline-flex;align-items:center;gap:8px;background:#edf3ef;color:var(--brand);border:1px solid #d9e4dc;border-radius:999px;padding:7px 11px;font-size:12px;font-weight:800;letter-spacing:.05em;text-transform:uppercase}.hero h1{font-family:Georgia,"Times New Roman",serif;font-size:clamp(40px,5.5vw,68px);line-height:1.01;letter-spacing:-.035em;margin:18px 0;font-weight:500}.hero h1 span{color:var(--brand)}.lead{font-size:18px;color:#58645d;max-width:680px;margin:0 0 28px}.heroactions{display:flex;gap:10px;flex-wrap:wrap}.btn{border:0;border-radius:12px;padding:13px 18px;font-weight:800;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;gap:8px}.btn.primary{background:var(--brand);color:#fff;box-shadow:0 10px 24px rgba(36,77,61,.18)}.btn.primary:hover{background:#1d4033}.btn.secondary{background:#fff;color:var(--brand);border:1px solid #dbe2dc}.btn.ghost{background:#eef3ef;color:var(--brand)}.heroaside{background:linear-gradient(145deg,#244d3d,#18372b);color:#fff;border-radius:28px;padding:28px;box-shadow:0 28px 60px rgba(28,54,42,.20);position:relative;overflow:hidden}.heroaside:after{content:"";position:absolute;width:220px;height:220px;border-radius:50%;right:-90px;top:-80px;background:rgba(255,255,255,.06)}.heroaside h3{margin:0 0 6px;font-size:21px}.heroaside p{margin:0;color:#d9e5de;font-size:14px}.quickgrid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:22px}.quick{background:rgba(255,255,255,.09);border:1px solid rgba(255,255,255,.12);border-radius:15px;padding:15px}.quick b{display:block;font-size:14px}.quick span{font-size:11px;color:#c8d7cf}
.wrap{max-width:1180px;margin:0 auto;padding:20px 22px 64px}.sectiontitle{display:flex;align-items:end;justify-content:space-between;gap:18px;margin:12px 0 18px}.sectiontitle h2{font-size:27px;margin:0}.sectiontitle p{margin:0;color:var(--muted);font-size:14px}.card{background:var(--paper);border:1px solid var(--line);border-radius:20px;box-shadow:var(--shadow)}.searchcard{padding:22px}.searchbar{display:grid;grid-template-columns:1fr auto;gap:10px}.searchbar input{height:52px;border:1px solid #dbe1dc;border-radius:13px;padding:0 16px;outline:none;background:#fbfcfb}.searchbar input:focus{border-color:#789986;box-shadow:0 0 0 4px rgba(36,77,61,.08)}.results{margin-top:16px;display:grid;gap:10px}.result{padding:15px 16px;border:1px solid #e5e9e5;border-radius:14px;display:flex;justify-content:space-between;gap:16px;align-items:center;transition:.15s;background:#fff}.result:hover{transform:translateY(-1px);box-shadow:0 10px 24px rgba(32,52,41,.07);border-color:#cdd8d0}.resultmain b{font-size:16px}.small{color:var(--muted);font-size:12px}.pill{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:5px 9px;font-size:10px;font-weight:900;text-transform:uppercase;letter-spacing:.05em}.pill.liber{background:#e7f7ec;color:#16733a}.pill.indisponibil{background:#efefef;color:#555}.pill.rezervat{background:#fff4da;color:#8a5d08}.featuregrid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:28px}.feature{padding:20px}.icon{width:42px;height:42px;border-radius:12px;background:#edf3ef;color:var(--brand);display:grid;place-items:center;font-size:19px;margin-bottom:14px}.feature h3{font-size:15px;margin:0 0 6px}.feature p{font-size:12px;color:var(--muted);margin:0}
.mapcard{padding:16px}.maptop{padding:7px 7px 15px;display:flex;justify-content:space-between;align-items:center;gap:12px}.maptop h1{font-size:25px;margin:0}.maptop p{margin:2px 0 0;color:var(--muted);font-size:13px}#map-svg{width:100%;height:68vh;min-height:480px;background:linear-gradient(180deg,#fafcf9,#f1f4f0);border:1px solid #e1e6e1;border-radius:16px;touch-action:none}.legend{display:flex;gap:16px;flex-wrap:wrap;padding:13px 6px 4px;font-size:12px;color:#536159}.legend span{display:flex;align-items:center;gap:6px}.dot{width:10px;height:10px;border-radius:50%}
.formcard{max-width:820px;margin:0 auto;padding:28px}.formhead{text-align:center;max-width:650px;margin:0 auto 24px}.formhead h1{font-family:Georgia,"Times New Roman",serif;font-size:36px;font-weight:500;margin:0 0 8px}.formhead p{margin:0;color:var(--muted)}.formsection{border-top:1px solid var(--line);padding-top:20px;margin-top:20px}.formsection h3{font-size:14px;margin:0 0 12px;color:var(--brand)}.row{display:grid;grid-template-columns:1fr 1fr;gap:13px}label{display:block;font-size:12px;font-weight:800;color:#4c5951;margin:10px 0 5px}input,textarea{width:100%;border:1px solid #d8dfda;border-radius:11px;padding:11px 12px;background:#fbfcfb;outline:none}input:focus,textarea:focus{border-color:#71917f;box-shadow:0 0 0 4px rgba(36,77,61,.07)}.filebox{border:1px dashed #b9c8be;background:#f7faf7;border-radius:13px;padding:15px}.notice{padding:13px 14px;border-radius:12px;margin-top:14px;font-size:13px}.ok{background:#e8f6ec;color:#176b37;border:1px solid #bee0c8}.err{background:#fdecec;color:#9d2b2b;border:1px solid #f2c6c6}
.locgrid{display:grid;grid-template-columns:.9fr 1.1fr;gap:18px}.locmain,.locside{padding:24px}.loctitle{display:flex;align-items:center;justify-content:space-between;gap:12px}.loctitle h1{margin:0;font-size:28px}.meta{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:18px}.metaitem{background:#f7f9f7;border-radius:12px;padding:12px}.metaitem span{display:block;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.06em}.metaitem b{font-size:14px}.person{border:1px solid #e6eae6;background:#fbfcfb;border-radius:12px;padding:12px;margin-top:9px}.person strong{font-size:14px}.footer{border-top:1px solid #e2e6e2;padding:26px 22px;color:#7c877f;font-size:11px;text-align:center;background:#fff}
@media(max-width:860px){.navlinks a:not(.navcta){display:none}.hero{grid-template-columns:1fr;padding-top:44px}.heroaside{display:none}.featuregrid{grid-template-columns:1fr}.locgrid{grid-template-columns:1fr}.row{grid-template-columns:1fr}.searchbar{grid-template-columns:1fr}.searchbar .btn{width:100%}.sectiontitle{align-items:start;flex-direction:column}.navin{height:68px}.brandsub{display:none}.wrap{padding-left:14px;padding-right:14px}.hero{padding-left:16px;padding-right:16px}.formcard{padding:19px}}
"""

def _cim_name() -> str:
    try: return cim.get_setting("cimitir_nume") or "Cimitir"
    except Exception: return "Cimitir"

def _wrap(title: str, body: str, active: str = "") -> str:
    name=_cim_name()
    def nav(label,href,key,cta=False):
        cl="active" if key==active else ""
        if cta: cl=(cl+" navcta").strip()
        return f'<a class="{cl}" href="{href}">{label}</a>'
    return f'''<!doctype html><html lang="ro"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex"><title>{_e(title)} — {_e(name)}</title><style>{_CSS}</style></head><body><div class="shell"><header class="nav"><div class="navin"><a class="brand" href="/portal"><span class="crest">✦</span><span class="brandtext"><span class="brandname">{_e(name)}</span><span class="brandsub">Portal public</span></span></a><nav class="navlinks">{nav('Acasă','/portal','home')}{nav('Hartă','/portal/harta','map')}{nav('Cerere concesionare','/portal/cerere','cerere',True)}</nav></div></header>{body}<footer class="footer">Portal public · Datele cu caracter personal ale concesionarilor nu sunt afișate public.</footer></div></body></html>'''

def _status_pill(st:str)->str:
    return f'<span class="pill {_e(st)}">{_e(_STATUS_LABELS.get(st,st.title()))}</span>'

def _home_page() -> str:
    body='''<section class="hero"><div><span class="eyebrow">Evidență digitală</span><h1>Găsește ușor un <span>loc de veci</span></h1><p class="lead">Caută după numele persoanei înhumate sau după codul și localizarea locului. Poți consulta harta și depune online o cerere de concesionare.</p><div class="heroactions"><a class="btn primary" href="#cautare">Caută un loc</a><a class="btn secondary" href="/portal/harta">Deschide harta</a></div></div><aside class="heroaside"><h3>Servicii disponibile online</h3><p>Informațiile publice esențiale, într-un singur loc.</p><div class="quickgrid"><div class="quick"><b>Căutare rapidă</b><span>după nume sau localizare</span></div><div class="quick"><b>Hartă interactivă</b><span>vizualizare locuri</span></div><div class="quick"><b>Fișă publică</b><span>detalii despre loc</span></div><div class="quick"><b>Cerere online</b><span>concesionare + documente</span></div></div></aside></section><main class="wrap"><div class="sectiontitle" id="cautare"><div><h2>Căutare în evidență</h2><p>Introdu un nume, cod de loc, sector, parcelă, rând sau număr.</p></div></div><section class="card searchcard"><div class="searchbar"><input id="q" placeholder="Ex: Popescu Ion sau A-03-12" autofocus><button class="btn primary" onclick="doSearch()">Caută</button></div><div id="results" class="results"></div></section><section class="featuregrid"><article class="card feature"><div class="icon">⌕</div><h3>Căutare protejată</h3><p>Sunt afișate doar informațiile publicabile, fără CNP, adresă sau telefon.</p></article><article class="card feature"><div class="icon">▦</div><h3>Hartă clară</h3><p>Identifică vizual locurile și starea lor direct pe planul cimitirului.</p></article><article class="card feature"><div class="icon">✎</div><h3>Cerere de concesionare</h3><p>Completează formularul online și atașează documentele necesare.</p></article></section></main><script>
const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));async function doSearch(){const q=document.getElementById('q').value.trim(),box=document.getElementById('results');box.innerHTML='<div class="small">Se caută…</div>';try{const r=await fetch('/api/public/search?q='+encodeURIComponent(q));const d=await r.json();if(!d.results.length){box.innerHTML='<div class="small">Nu am găsit rezultate pentru această căutare.</div>';return}box.innerHTML=d.results.map(x=>{const loc=[x.sector&&('Sector '+x.sector),x.parcela&&('Parcela '+x.parcela),x.rand&&('Rând '+x.rand),x.numar&&('Nr. '+x.numar)].filter(Boolean).join(' · ');const ppl=x.inhumati?.length?'<div class="small">Înhumați: '+x.inhumati.map(esc).join(', ')+'</div>':'';return '<a class="result" href="/portal/loc/'+encodeURIComponent(x.cod_loc)+'"><div class="resultmain"><b>'+esc(x.cod_loc)+'</b><div class="small">'+esc(loc||'Localizare indisponibilă')+'</div>'+ppl+'</div><span class="pill '+esc(x.status)+'">'+esc(x.status)+'</span></a>'}).join('')}catch(e){box.innerHTML='<div class="notice err">Nu am putut efectua căutarea.</div>'}}document.getElementById('q').addEventListener('keydown',e=>{if(e.key==='Enter')doSearch()});</script>'''
    return _wrap("Portal public",body,"home")

def _map_page() -> str:
    body='''<main class="wrap"><section class="card mapcard"><div class="maptop"><div><h1>Harta cimitirului</h1><p>Click pe un loc pentru detalii. Folosește rotița pentru zoom și trage harta pentru deplasare.</p></div><a class="btn ghost" href="/portal">Căutare</a></div><svg id="map-svg"></svg><div class="legend"><span><i class="dot" style="background:#16a34a"></i>Disponibil</span><span><i class="dot" style="background:#f59e0b"></i>Rezervat</span><span><i class="dot" style="background:#6b7280"></i>Indisponibil</span></div><p class="small" id="scale-note"></p><div class="plotpanel" id="plot-panel" data-testid="plot-panel" hidden></div></section></main><script>
const SVGNS='http://www.w3.org/2000/svg';let vb={x:0,y:0,w:100,h:100},moved=false,ox=0,oy=0,mapScale=null,selEl=null,labels=null;const LBL={disponibil:'Disponibil',rezervat:'Rezervat',indisponibil:'Indisponibil'};function esc(t){return String(t??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}function fm(n){return Number(n).toLocaleString('ro-RO',{minimumFractionDigits:2,maximumFractionDigits:2})}function selectPlot(o,pg){const svg=document.getElementById('map-svg');if(selEl){selEl.setAttribute('stroke','#fff');selEl.setAttribute('stroke-width',.75)}selEl=pg;pg.setAttribute('stroke','#0f172a');pg.setAttribute('stroke-width',2.5);if(labels)labels.remove();labels=document.createElementNS(SVGNS,'g');const pts=o.points||[],sides=o.laturi_m;if(sides&&pts.length===sides.length){const fs=vb.w/70;pts.forEach((a,i)=>{const b=pts[(i+1)%pts.length],ax=a[0]??a.x,ay=a[1]??a.y,bx=b[0]??b.x,by=b[1]??b.y,t=document.createElementNS(SVGNS,'text');t.setAttribute('x',(ax+bx)/2);t.setAttribute('y',(ay+by)/2);t.setAttribute('font-size',fs);t.setAttribute('text-anchor','middle');t.setAttribute('fill','#0f172a');t.setAttribute('stroke','#fff');t.setAttribute('stroke-width',fs/5);t.setAttribute('paint-order','stroke');t.setAttribute('pointer-events','none');t.textContent=fm(sides[i])+' m';labels.appendChild(t)})}svg.appendChild(labels);const st=o.disponibilitate||'',rows=[['Sector',o.sector],['Stare',LBL[st]||st]];if(o.suprafata_m2_calculata!=null)rows.push(['Suprafață (din plan)',fm(o.suprafata_m2_calculata)+' m²']);if(o.suprafata_m2_declarata)rows.push(['Suprafață declarată',esc(o.suprafata_m2_declarata)+' m²']);const meas=sides?'<div class="small" data-testid="plot-sides">Laturi: '+sides.map(x=>fm(x)+' m').join(' · ')+'</div>':'';const note=mapScale&&mapScale.calibrated?'Măsurătorile sunt calculate din plan la scara calibrată ('+fm(mapScale.px_per_meter)+' px/m) și sunt orientative; nu înlocuiesc măsurătoarea oficială.':'Scara planului nu este calibrată, deci suprafața și laturile nu pot fi afișate.';const pn=document.getElementById('plot-panel');pn.hidden=false;pn.innerHTML='<div class="plothead"><h3 data-testid="plot-code">'+esc(o.cod_loc)+'</h3><a class="btn primary" data-testid="plot-details-link" href="/portal/loc/'+encodeURIComponent(o.cod_loc)+'">Detalii și cerere</a></div><div class="meta">'+rows.filter(r=>r[1]).map(r=>'<div class="metaitem"><span>'+r[0]+'</span><b data-testid="plot-'+r[0].toLowerCase().replace(/[^a-z]+/g,'-')+'">'+esc(r[1])+'</b></div>').join('')+'</div>'+meas+'<p class="small" data-testid="plot-scale-note">'+note+'</p>'}async function loadMap(){const svg=document.getElementById('map-svg'),r=await fetch('/api/public/map'),data=await r.json();let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;[...(data.sectors||[]),...(data.plots||[])].forEach(o=>(o.points||[]).forEach(p=>{const x=p[0]??p.x,y=p[1]??p.y;minX=Math.min(minX,x);minY=Math.min(minY,y);maxX=Math.max(maxX,x);maxY=Math.max(maxY,y)}));if(data.background&&data.background.width){minX=Math.min(minX,0);minY=Math.min(minY,0);maxX=Math.max(maxX,data.background.width);maxY=Math.max(maxY,data.background.height)}const sc=data.scale||{};mapScale=sc;document.getElementById('scale-note').textContent=(sc.calibrated?'Scară: '+sc.px_per_meter.toFixed(2)+' px/m':'Scară necalibrată: dimensiunile nu sunt afișate')+' · coordonate locale ale planului (nu GPS)';if(!isFinite(minX)){svg.innerHTML='<text x="10" y="24" fill="#667">Harta nu are date.</text>';return}const pad=(maxX-minX+maxY-minY)*.03+10;vb={x:minX-pad,y:minY-pad,w:maxX-minX+2*pad,h:maxY-minY+2*pad};setVB(svg);if(data.background&&data.background.url){const im=document.createElementNS(SVGNS,'image');im.setAttribute('href',data.background.url);im.setAttribute('x',0);im.setAttribute('y',0);im.setAttribute('width',data.background.width);im.setAttribute('height',data.background.height);svg.appendChild(im)}function poly(o,fill,stroke,sw,link){const pts=(o.points||[]).map(p=>(p[0]??p.x)+','+(p[1]??p.y)).join(' ');let el=document.createElementNS(SVGNS,'polygon');let pg=el;if(link){pg=document.createElementNS(SVGNS,'polygon');pg.style.cursor='pointer';pg.setAttribute('data-testid','plot-'+link);pg.addEventListener('click',()=>{if(!moved)selectPlot(o,pg)});const t=document.createElementNS(SVGNS,'title');t.textContent=link;pg.appendChild(t);el=pg}pg.setAttribute('points',pts);pg.setAttribute('fill',fill);pg.setAttribute('stroke',stroke);pg.setAttribute('stroke-width',sw);pg.setAttribute('vector-effect','non-scaling-stroke');svg.appendChild(el)}(data.sectors||[]).forEach(s=>poly(s,'rgba(36,77,61,.035)',s.color||'#8ea097',1.4));(data.plots||[]).forEach(p=>poly(p,p.color,'#fff',.75,p.cod_loc));enable(svg)}function setVB(svg){svg.setAttribute('viewBox',`${vb.x} ${vb.y} ${vb.w} ${vb.h}`)}function enable(svg){let drag=false,sx=0,sy=0;svg.addEventListener('wheel',e=>{e.preventDefault();const f=e.deltaY<0?.84:1.16,r=svg.getBoundingClientRect(),mx=vb.x+(e.clientX-r.left)/r.width*vb.w,my=vb.y+(e.clientY-r.top)/r.height*vb.h;vb.x=mx-(mx-vb.x)*f;vb.y=my-(my-vb.y)*f;vb.w*=f;vb.h*=f;setVB(svg)},{passive:false});svg.addEventListener('pointerdown',e=>{drag=true;moved=false;sx=e.clientX;sy=e.clientY;ox=e.clientX;oy=e.clientY});window.addEventListener('pointerup',()=>drag=false);window.addEventListener('pointermove',e=>{if(!drag)return;if(Math.hypot(e.clientX-ox,e.clientY-oy)>4)moved=true;const r=svg.getBoundingClientRect();vb.x-=(e.clientX-sx)/r.width*vb.w;vb.y-=(e.clientY-sy)/r.height*vb.h;sx=e.clientX;sy=e.clientY;setVB(svg)})}loadMap();</script>'''
    return _wrap("Hartă",body,"map")

def _loc_page(cod_loc: str) -> str:
    d=_public_loc_payload(cod_loc)
    if not d:return _wrap("Loc negăsit",'<main class="wrap"><section class="card locmain"><h1>Loc negăsit</h1><p class="small">Codul solicitat nu există în evidență.</p><a class="btn secondary" href="/portal">Înapoi</a></section></main>')
    pairs=[]
    for lbl,key in [("Sector","sector"),("Parcelă","parcela"),("Rând","rand"),("Număr","numar"),("Suprafață","suprafata"),("Capacitate","capacitate"),("Tip","tip"),("Categorie","categorie")]:
        v=d.get(key)
        if v not in (None,""):pairs.append(f'<div class="metaitem"><span>{_e(lbl)}</span><b>{_e(v)}'+(' m²' if key=='suprafata' else '')+'</b></div>')
    if d.get("suprafata_m2_calculata") is not None:
        pairs.append(f'<div class="metaitem"><span>Suprafață (din plan)</span><b>{d["suprafata_m2_calculata"]} m²</b></div>')
        pairs.append(f'<div class="metaitem"><span>Laturi</span><b>{" · ".join(str(x)+" m" for x in d["laturi_m"])}</b></div>')
    ppl=''.join(f'<div class="person"><strong>{_e(p.get("nume") or "—")}</strong><div class="small">{_e(" – ".join(x for x in [p.get("an_nastere"),p.get("an_deces")] if x))}</div></div>' for p in d['inhumati']) or '<p class="small">Nicio persoană înhumată afișată public.</p>'
    body=f'<main class="wrap"><div class="locgrid"><section class="card locmain"><div class="loctitle"><h1>{_e(d["cod_loc"])}</h1>{_status_pill(d["status"])}</div><div class="meta">{"".join(pairs)}</div><div style="margin-top:20px;display:flex;gap:9px;flex-wrap:wrap"><a class="btn primary" href="/portal/cerere?cod_loc={_e(d["cod_loc"])}">Solicită concesionarea</a><a class="btn secondary" href="/portal/harta">Vezi harta</a></div></section><aside class="card locside"><h3 style="margin-top:0">Persoane înhumate</h3>{ppl}<p class="small" style="margin-top:16px">Din considerente de confidențialitate sunt afișate doar informațiile publicabile.</p></aside></div></main>'
    return _wrap(d['cod_loc'],body)

def _cerere_page(cod_loc: str = "") -> str:
    cod=_e(cod_loc)
    body=f'''<main class="wrap"><section class="card formcard"><div class="formhead"><span class="eyebrow">Solicitare online</span><h1>Cerere de concesionare</h1><p>Completează datele solicitantului și, dacă este cazul, atașează documentele justificative. Cererea ajunge direct în evidența aplicației MAIN.</p></div><form id="frm" onsubmit="return false"><div class="formsection"><h3>Tipul cererii</h3><select name="tip" id="tip" style="width:100%;padding:11px;border-radius:11px;border:1px solid #d8dfda"><option value="concesionare">Concesionare loc de veci</option><option value="actualizare_inhumat">Actualizare evidență înhumați (concesiune existentă — doar titular)</option></select></div><div class="formsection"><h3>Locul solicitat</h3><label>Cod loc de veci</label><input name="cod_loc" value="{cod}" placeholder="Ex: A-03-12 (opțional)"></div><div class="formsection"><h3>Date solicitant</h3><div class="row"><div><label>Nume *</label><input name="nume" required></div><div><label>Prenume *</label><input name="prenume" required></div></div><div class="row"><div><label>CNP</label><input name="cnp"></div><div><label>Serie / nr. CI</label><input name="bi_ci"></div></div><label>Adresă</label><input name="adresa"><div class="row"><div><label>Telefon</label><input name="telefon"></div><div><label>Email</label><input name="email" type="email"></div></div></div><div class="formsection"><h3>Detalii cerere</h3><label>Grad de rudenie cu persoana înhumată</label><input name="grad_rudenie" placeholder="Ex: fiu, fiică, soț/soție"><label>Observații</label><textarea name="observatii" rows="4"></textarea></div><div class="formsection"><h3>Persoana înhumată (doar pentru actualizare evidență)</h3><div class="row"><div><label>Nume</label><input name="nume_decedat"></div><div><label>Prenume</label><input name="prenume_decedat"></div></div><label>Data decesului</label><input name="data_deces" placeholder="zz.ll.aaaa"></div><div class="formsection"><h3>Documente</h3><div class="filebox"><label style="margin-top:0">Atașează documente (PDF, JPG, PNG)</label><input type="file" id="files" multiple accept="image/*,.pdf"><div class="small">Poți selecta mai multe fișiere. PDF, JPG sau PNG, maxim 10 MB pentru fiecare fișier.</div></div></div><div class="notice" style="background:#f7faf7;border:1px solid #d9e4dc"><label style="display:flex;gap:8px;align-items:flex-start;font-weight:600"><input type="checkbox" id="priv" style="width:auto;margin-top:3px"><span style="display:block">Am luat cunoștință de <a href="/portal/prelucrare-date" target="_blank" style="text-decoration:underline">informarea privind prelucrarea datelor</a>. Depunerea cererii nu acordă automat concesiunea. <a href="/portal/cont" style="text-decoration:underline">Contul meu</a></span></label></div><div style="margin-top:22px"><button class="btn primary" id="send" onclick="submitForm()">Trimite cererea</button></div><div id="msg"></div></form></section></main><script>
async function submitForm(){{const f=document.getElementById('frm'),msg=document.getElementById('msg'),btn=document.getElementById('send');msg.innerHTML='';if(!f.nume.value.trim()||!f.prenume.value.trim()){{msg.innerHTML='<div class="notice err">Numele și prenumele sunt obligatorii.</div>';return}}const fd=new FormData();['cod_loc','nume','prenume','cnp','bi_ci','adresa','telefon','email','grad_rudenie','observatii','tip','nume_decedat','prenume_decedat','data_deces'].forEach(k=>fd.append(k,f[k]?.value||''));fd.append('privacy_ack',document.getElementById('priv').checked?'1':'0');[...document.getElementById('files').files].forEach(x=>fd.append('files',x));btn.disabled=true;btn.textContent='Se trimite…';try{{const r=await fetch('/api/public/cerere',{{method:'POST',body:fd}}),d=await r.json();if(r.status===401){{msg.innerHTML='<div class="notice err">Autentificați-vă în <a href="/portal/cont">contul de cetățean</a> pentru a depune cererea.</div>';return}}if(!r.ok)throw new Error(d.detail||'Eroare la trimitere');msg.innerHTML='<div class="notice ok"><b>Cererea a fost înregistrată.</b><br>Cod de urmărire: <b>'+d.cod_urmarire+'</b> · Documente încărcate: '+d.documente+(d.respinse&&d.respinse.length?'<br>Respinse: '+d.respinse.join('; '):'')+'<br>Depunerea nu acordă automat concesiunea; urmăriți starea în <a href="/portal/cont">contul dumneavoastră</a>.</div>';f.reset()}}catch(e){{msg.innerHTML='<div class="notice err">'+e.message+'</div>'}}finally{{btn.disabled=false;btn.textContent='Trimite cererea'}}}}</script>'''
    return _wrap("Cerere concesionare",body,"cerere")

_ACCOUNT_JS = r"""
const $=id=>document.getElementById(id);const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
async function api(m,p,b){const r=await fetch('/api/public/account'+p,{method:m,headers:b&&!(b instanceof FormData)?{'Content-Type':'application/json'}:{},body:b instanceof FormData?b:(b?JSON.stringify(b):undefined)});let d={};try{d=await r.json()}catch(e){}return {ok:r.ok,status:r.status,d}}
async function auth(kind){const r=await api('POST','/'+kind,{email:$('em').value,password:$('pw').value,full_name:$('fn').value});if(!r.ok){$('amsg').innerHTML='<div class="notice err">'+esc(r.d.detail||'Eroare')+'</div>';return}load()}
async function logout(){await api('POST','/logout');load()}
function forgotForm(){$('box').innerHTML='<h3>Resetarea parolei</h3><label>Email-ul contului</label><input id="fe" type="email"><div style="margin-top:14px"><button class="btn primary" onclick="sendForgot()">Trimite link de resetare</button> <a class="btn ghost" href="/portal/cont">Înapoi</a></div>'}
async function sendForgot(){const r=await api('POST','/forgot',{email:$('fe').value});$('amsg').innerHTML='<div class="notice '+(r.ok?'ok':'err')+'">'+esc(r.ok?r.d.message:(r.d.detail||'Eroare'))+'</div>'}
function resetForm(t){history.replaceState(null,'','/portal/cont');$('box').innerHTML='<h3>Parolă nouă</h3><label>Parola nouă (minim 10 caractere)</label><input id="np" type="password"><div style="margin-top:14px"><button class="btn primary" onclick="doReset(\''+t+'\')">Salvează parola</button></div>'}
async function doReset(t){const r=await api('POST','/reset',{token:t,password:$('np').value});if(!r.ok){$('amsg').innerHTML='<div class="notice err">'+esc(r.d.detail||'Eroare')+'</div>';return}history.replaceState(null,'','/portal/cont');$('amsg').innerHTML='<div class="notice ok">Parola a fost schimbată. Vă puteți autentifica.</div>';load()}
async function complete(code){const fd=new FormData();fd.append('mesaj',$('m_'+code).value);[...$('f_'+code).files].forEach(f=>fd.append('files',f));const r=await api('POST','/cereri/'+code+'/completari',fd);$('amsg').innerHTML='<div class="notice '+(r.ok?'ok':'err')+'">'+esc(r.ok?'Completarea a fost trimisă.':(r.d.detail||'Eroare'))+'</div>';load()}
async function load(){const me=await api('GET','/me');if(!me.ok){$('box').innerHTML='<h3>Autentificare / cont nou</h3><label>Email</label><input id="em" type="email"><label>Nume (opțional)</label><input id="fn"><label>Parolă (minim 10 caractere)</label><input id="pw" type="password"><div style="margin-top:14px;display:flex;gap:10px"><button class="btn primary" onclick="auth(\'login\')">Autentificare</button><button class="btn secondary" onclick="auth(\'register\')">Creează cont</button></div><p style="margin-top:12px"><a href="#" onclick="forgotForm();return false" style="text-decoration:underline" id="forgot-link">Ai uitat parola?</a></p>';return}
const r=await api('GET','/cereri');let h='<div style="display:flex;justify-content:space-between"><b>'+esc(me.d.email)+'</b><button class="btn ghost" onclick="logout()">Ieșire</button></div><h3>Cererile mele</h3>';
for(const c of r.d.cereri){const det=(await api('GET','/cereri/'+c.cod_urmarire)).d;h+='<div class="person"><b>'+esc(c.cod_urmarire)+'</b> · '+esc(c.tip)+' · loc '+esc(c.cod_loc||'—')+'<br><span class="pill '+(c.status==='validat'?'liber':'rezervat')+'">'+esc(c.status_label)+'</span>'+(c.mesaj?'<div class="small">Mesaj de la instituție: '+esc(c.mesaj)+'</div>':'')+'<div class="small">'+(det.documente||[]).map(d=>d.disponibil_pentru_descarcare?'<a href="/api/public/account/cereri/'+c.cod_urmarire+'/documents/'+d.id+'">'+esc(d.nume)+'</a>':esc(d.nume)+' (preluat de instituție)').join(' · ')+'</div>'+(c.status==='completari'?'<label>Mesaj</label><textarea id="m_'+c.cod_urmarire+'"></textarea><input type="file" id="f_'+c.cod_urmarire+'" multiple><button class="btn primary" style="margin-top:8px" onclick="complete(\''+c.cod_urmarire+'\')">Trimite completarea</button>':'')+'</div>'}
$('box').innerHTML=h+(r.d.cereri.length?'':'<p class="small">Nu aveți cereri.</p>')+'<a class="btn secondary" style="margin-top:12px" href="/portal/cerere">Cerere nouă</a>'}
const resetToken=new URLSearchParams(location.search).get('reset');if(resetToken){resetForm(resetToken)}else{load()}"""


def _account_page() -> str:
    return _wrap("Contul meu", '<main class="wrap"><section class="card formcard"><div class="formhead"><h1>Contul meu</h1><p>Vedeți doar cererile și documentele dumneavoastră.</p></div><div id="amsg"></div><div id="box"></div></section></main><script>' + _ACCOUNT_JS + '</script>', "cerere")


def _privacy_page() -> str:
    p = citizen_api.privacy_info()
    return _wrap("Prelucrarea datelor", f'<main class="wrap"><section class="card formcard"><h1>Informare privind prelucrarea datelor</h1><p>{_e(p["text"])}</p><p class="small">Termene de păstrare configurate: cereri aprobate {p["retention_days_closed"]} zile, cereri respinse {p["retention_days_rejected"]} zile după închidere. Versiunea informării: {_e(p["version"])}.</p></section></main>')


def register(app: FastAPI) -> None:
    app.include_router(public_api)
    app.include_router(citizen_api.router)
    app.include_router(citizen_api.privacy_router)
    @app.get("/portal/cont", response_class=HTMLResponse)
    def portal_account(): return HTMLResponse(_account_page())
    @app.get("/portal/prelucrare-date", response_class=HTMLResponse)
    def portal_privacy(): return HTMLResponse(_privacy_page())
    @app.get("/portal", response_class=HTMLResponse)
    def portal_home(): return HTMLResponse(_home_page())
    @app.get("/portal/harta", response_class=HTMLResponse)
    def portal_map(): return HTMLResponse(_map_page())
    @app.get("/portal/cerere", response_class=HTMLResponse)
    def portal_cerere(cod_loc: str = ""): return HTMLResponse(_cerere_page(cod_loc))
    @app.get("/portal/loc/{cod_loc}", response_class=HTMLResponse)
    def portal_loc(cod_loc: str): return HTMLResponse(_loc_page(cod_loc))
