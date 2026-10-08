"""Proiecția publică a hărții: listă explicită de câmpuri, disponibilitate, scară/coordonate.

- Coordonatele sunt locale (pixeli din imaginea de fundal, origine colț stânga-sus). NU sunt GPS.
- Suprafețe/dimensiuni calculate se publică doar dacă `px_per_meter` este valid (calibrat).
- Disponibilitatea pentru concesionare vine DOAR din câmpul explicit `disponibilitate`; înhumările
  și expirarea contractelor nu o schimbă.
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from typing import Any, Dict, List, Optional

import cimitir_db_router as db

COORDINATE_SYSTEM = {"type": "local_px", "origin": "top-left of background image", "gps": False}
PUBLIC_PLOT_FIELDS = ("uid", "cod_loc", "points", "color", "sector", "parcela", "rand", "numar", "tip", "categorie",
                      "capacitate", "suprafata_declarata", "disponibilitate", "are_inhumari")
PUBLIC_SECTOR_FIELDS = ("id", "points", "color")
PUBLIC_BURIAL_FIELDS = ("cod_loc", "nume", "prenume", "an_nastere", "an_deces")
AVAILABILITY = ("disponibil", "rezervat", "indisponibil")
PPM_RANGE = (0.01, 10000.0)


def availability(loc: Dict[str, Any]) -> str:
    disp = str(loc.get("disponibilitate") or "").strip().lower()
    stare = str(loc.get("stare") or "").strip().lower()
    if "rezerv" in disp or "rezerv" in stare:
        return "rezervat"
    if disp in ("liber", "disponibil"):
        return "disponibil"
    return "indisponibil"


def parse_ppm(value: Any) -> Optional[float]:
    try:
        ppm = float(value)
    except (TypeError, ValueError):
        return None
    return ppm if math.isfinite(ppm) and PPM_RANGE[0] <= ppm <= PPM_RANGE[1] else None


def _points(raw: Any) -> List[List[float]]:
    try:
        pts = json.loads(raw) if isinstance(raw, str) else (raw or [])
        return [[float(p[0]), float(p[1])] for p in pts]
    except (TypeError, ValueError, IndexError, KeyError):
        return []


def polygon_area(points: List[List[float]]) -> float:
    n = len(points)
    return abs(sum(points[i][0] * points[(i + 1) % n][1] - points[(i + 1) % n][0] * points[i][1] for i in range(n))) / 2 if n >= 3 else 0.0


def geometry_metrics(points: List[List[float]], ppm: Optional[float]) -> Dict[str, Any]:
    if not ppm or len(points) < 3:
        return {"suprafata_m2_calculata": None, "laturi_m": None}
    sides = [math.dist(points[i], points[(i + 1) % len(points)]) / ppm for i in range(len(points))]
    return {"suprafata_m2_calculata": round(polygon_area(points) / ppm ** 2, 2), "laturi_m": [round(s, 2) for s in sides]}


def ensure_uids() -> None:
    conn = db.get_connection()
    try:
        for (rid,) in conn.execute("SELECT id FROM locuri_de_veci WHERE uid IS NULL OR uid=''").fetchall():
            conn.execute("UPDATE locuri_de_veci SET uid=? WHERE id=?", (str(uuid.uuid4()), rid))
    finally:
        conn.close()


def build_snapshot(include_burials: bool = False) -> Dict[str, Any]:
    """Proiecția publică a bazei locale (MAIN). Nu conține nicio dată personală."""
    ensure_uids()
    conn = db.get_connection()
    try:
        locs = [dict(r) for r in conn.execute(
            "SELECT uid, cod_loc, points, color, sector, parcela, rand, numar, tip, categorie, capacitate, suprafata, "
            f"disponibilitate, stare FROM locuri_de_veci WHERE cod_loc IS NOT NULL AND cod_loc<>'' AND {db.tenant_scope()} ORDER BY cod_loc").fetchall()]
        burial_codes = {r["cod_loc"] for r in conn.execute(
            f"SELECT DISTINCT l.cod_loc FROM inhumati i JOIN locuri_de_veci l ON l.id=i.id_loc WHERE {db.tenant_scope('l')}").fetchall()}
    finally:
        conn.close()
    plots = []
    for loc in locs:
        pts = _points(loc["points"])
        if not pts:
            continue
        plots.append({"uid": loc["uid"], "cod_loc": loc["cod_loc"], "points": pts, "color": loc["color"] or "",
                      "sector": loc["sector"] or "", "parcela": loc["parcela"] or "", "rand": loc["rand"] or "",
                      "numar": loc["numar"] or "", "tip": loc["tip"] or "", "categorie": loc["categorie"] or "",
                      "capacitate": loc["capacitate"] or "",
                      "suprafata_declarata": loc["suprafata"] if loc["suprafata"] not in (None, "") else None,
                      "disponibilitate": availability(loc), "are_inhumari": 1 if loc["cod_loc"] in burial_codes else 0})
    sectors = [{"id": s["id"], "points": _points(s["points"]), "color": s.get("color") or ""} for s in db.load_all_sectors()]
    burials = []
    if include_burials:
        for i in db.load_all_inhumati():
            if (i.get("cod_loc") or "").strip():
                burials.append({"cod_loc": i["cod_loc"], "nume": i.get("nume") or "", "prenume": i.get("prenume") or "",
                                "an_nastere": (i.get("data_nasterii") or "")[-4:], "an_deces": (i.get("data_deces") or "")[-4:]})
    ppm = parse_ppm(db.get_setting("px_per_meter"))
    return {"scale": {"px_per_meter": ppm, "calibrated": ppm is not None}, "plots": plots,
            "sectors": [s for s in sectors if s["points"]], "burials": burials, "publish_burials": bool(include_burials)}


def snapshot_hash(snapshot: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def sanitize_snapshot(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Server: păstrează DOAR câmpurile din listele explicite; orice altceva este ignorat."""
    plots = []
    for p in payload.get("plots") or []:
        clean = {k: p.get(k) for k in PUBLIC_PLOT_FIELDS}
        if not clean["uid"] or not clean["cod_loc"] or len(_points(clean["points"])) < 3:
            continue
        clean["points"] = _points(clean["points"])
        clean["disponibilitate"] = clean["disponibilitate"] if clean["disponibilitate"] in AVAILABILITY else "indisponibil"
        plots.append(clean)
    sectors = [{k: s.get(k) for k in PUBLIC_SECTOR_FIELDS} for s in payload.get("sectors") or [] if s.get("id")]
    burials = [{k: str(b.get(k) or "")[:80] for k in PUBLIC_BURIAL_FIELDS} for b in payload.get("burials") or []] \
        if payload.get("publish_burials") else []
    ppm = parse_ppm((payload.get("scale") or {}).get("px_per_meter"))
    return {"scale": {"px_per_meter": ppm, "calibrated": ppm is not None}, "plots": plots, "sectors": sectors,
            "burials": burials, "publish_burials": bool(payload.get("publish_burials"))}


def public_map() -> Dict[str, Any]:
    """Răspunsul `/api/public/map`: poligoane, sectoare, fundal, scară, coduri, dimensiuni, disponibilitate."""
    ppm = parse_ppm(db.get_setting("px_per_meter"))
    plots = []
    for r in db.map_rows():
        pts = _points(r["points"])
        if not pts:
            continue
        avail = availability(dict(r))
        plots.append({"cod_loc": r["cod_loc"], "points": pts, "color": r.get("color") or "", "sector": r.get("sector") or "",
                      "disponibilitate": avail, "disponibil_concesionare": avail == "disponibil",
                      "suprafata_m2_declarata": r.get("suprafata") or None, **geometry_metrics(pts, ppm)})
    sectors = [{"id": s["id"], "points": _points(s["points"]), "color": s.get("color") or ""} for s in db.sector_rows()]
    bg = None
    if background_image(db) is not None:
        if True:
            bg = {"url": "/api/public/map/background", "width": int(db.get_setting("map_bg_w") or 0),
                  "height": int(db.get_setting("map_bg_h") or 0)}
    return {"coordinate_system": COORDINATE_SYSTEM,
            "scale": {"px_per_meter": ppm, "calibrated": ppm is not None,
                      "note": None if ppm else "Scară necalibrată: dimensiunile nu sunt publicate."},
            "map_version": int(db.get_setting("sync_map_version") or 0),
            "background": bg, "sectors": [s for s in sectors if s["points"]], "plots": plots}


_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def background_image(db) -> tuple | None:
    import base64
    import os
    b64 = db.get_setting("sync_map_image_b64")
    if b64:
        return base64.b64decode(b64), db.get_setting("sync_map_image_mime") or "image/png"
    path = (db.get_setting("map_path") or "").strip()
    if path and os.path.isfile(path):
        with open(path, "rb") as handle:
            return handle.read(), _MIME.get(os.path.splitext(path)[1].lower(), "image/png")
    return None
