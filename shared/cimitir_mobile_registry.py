"""cimitir_mobile_registry.py — citire profesională pentru Cimitir Mobile v2.

Acest strat este intenționat READ-ONLY în prima etapă. Autentificarea se face exclusiv
cu cheia de binding existentă (`Authorization: Binding <cheie>`), iar organizația și
cimitirul sunt deduse din binding. Interogările rulează cu rolul runtime
`cimitir_cp_app`, sub RLS; nu acceptă tenant/cemetery din client.

Scrierile (contracte, plăți, înhumați etc.) vor fi introduse separat, printr-un protocol
idempotent cu versiuni/outbox, după ce fluxurile și permisiunile sunt stabilizate.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List

import cimitir_cloud_control as cc
import cimitir_sync_v2 as sv

SEARCH_LIMIT_MAX = 25
MAP_LIMIT_MAX = 10000


def _json(value: Any, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return default


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _row(row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()} if row is not None else {}


def validate_test_polygon_document(document: Any) -> Dict[str, Any]:
    """Validează formatul exportului MAIN, fără a decide tenantul din payload."""
    import hashlib
    import math
    if not isinstance(document, dict):
        raise ValueError("export invalid")
    manifest, payload = document.get("manifest"), document.get("payload")
    if not isinstance(manifest, dict) or not isinstance(payload, dict):
        raise ValueError("manifest invalid")
    if payload.get("format") != "cimitir-mobile-v2-polygon-diagnostic-v1" or payload.get("source") != "MAIN-local-test":
        raise ValueError("format invalid")
    plots = payload.get("plots")
    if not isinstance(plots, list) or len(plots) != 1 or manifest.get("count") != 1 or manifest.get("format") != payload["format"]:
        raise ValueError("se acceptă un singur poligon")
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if digest != manifest.get("sha256"):
        raise ValueError("hash diferit")
    plot = plots[0]
    if not isinstance(plot, dict) or not set(plot).issubset({"uid", "points", "cod_loc", "sector", "parcela", "rand", "numar"}):
        raise ValueError("câmpuri neacceptate")
    try:
        uuid.UUID(str(plot["uid"]))
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ValueError("identificator invalid") from None
    code = plot.get("cod_loc")
    if not isinstance(code, str) or not (1 <= len(code.strip()) <= 120):
        raise ValueError("cod loc invalid")
    points = plot.get("points")
    if not isinstance(points, list) or not (3 <= len(points) <= 500):
        raise ValueError("poligon invalid")
    for pt in points:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2 or not all(type(x) in (int, float) and math.isfinite(x) for x in pt[:2]):
            raise ValueError("coordonate invalide")
    for key in ("sector", "parcela", "rand", "numar"):
        if plot.get(key) is not None and (not isinstance(plot[key], str) or len(plot[key]) > 120):
            raise ValueError("metadate invalide")
    return plot


class MobileRegistryService:
    """API de citire peste registrele PostgreSQL live, scoped prin binding + RLS."""

    def __init__(self, inbox):
        self.inbox, self.cp = inbox, inbox.cp

    def _auth(self, binding_key: str) -> Dict[str, Any]:
        a = self.inbox.authenticate(binding_key)
        if a.get("device_type") not in ("MAIN", "MOBILE"):
            raise cc.PermissionDenied("dispozitiv neacceptat")
        # authenticate() setează contextul cp.* pe thread; _c() îl propagă pe conexiunea nouă.
        return a

    def import_test_polygon(self, binding_key: str, document: Any) -> Dict[str, Any]:
        """Import pilot exclusiv MAIN, fără UPDATE/DELETE și fără alegerea tenantului de către client."""
        import os
        if os.getenv("CIMITIR_MOBILE_TEST_POLYGON_IMPORT") != "1":
            raise cc.PermissionDenied("importul de test este dezactivat")
        a = self._auth(binding_key)
        if a.get("device_type") != "MAIN":
            raise cc.PermissionDenied("doar dispozitivul MAIN poate importa")
        plot = validate_test_polygon_document(document)
        c = self.cp._c()
        try:
            # Constrângeri UNIQUE pe UID / (cemetery_uid, cod_loc) împiedică duplicatele.
            # Nu folosim UPSERT: dacă există deja un loc, refuzăm să îl suprascriem.
            c.execute("INSERT INTO locuri_de_veci (uid,cod_loc,points,sector,parcela,rand,numar,organization_uid,cemetery_uid) "
                      "VALUES (?,?,CAST(? AS jsonb),?,?,?,?,CAST(? AS uuid),CAST(? AS uuid))",
                      (str(plot["uid"]), plot["cod_loc"], json.dumps(plot["points"]),
                       plot.get("sector"), plot.get("parcela"), plot.get("rand"), plot.get("numar"),
                       a["organization_uid"], a["cemetery_uid"]))
            return {"imported": 1, "code": plot["cod_loc"]}
        finally:
            c.close()

    def bootstrap(self, binding_key: str) -> Dict[str, Any]:
        a = self._auth(binding_key)
        c = self.cp._c()
        try:
            cem = c.execute("SELECT uid, name, county, locality, address FROM cemeteries WHERE uid=?", (a["cemetery_uid"],)).fetchone()
            org = c.execute("SELECT uid, official_name, display_name FROM organizations WHERE uid=?", (a["organization_uid"],)).fetchone()
            dev = c.execute("SELECT d.device_name, d.device_type FROM device_cemetery_bindings b "
                            "JOIN devices d ON d.device_uid=b.device_uid AND d.organization_uid=b.organization_uid "
                            "WHERE b.binding_uid=? AND b.organization_uid=?",
                            (a["binding_uid"], a["organization_uid"])).fetchone()
            # Permisiunile granulare per dispozitiv vin în etapa următoare. Lista aceasta reprezintă doar capabilitățile
            # UI disponibile în Mobile v2; endpointurile curente sunt toate READ-ONLY.
            modules = ["dashboard", "search", "map", "graves", "concessionaires", "burials", "contracts", "payments"]
            return {
                "organization": _row(org),
                "cemetery": _row(cem),
                "device": _row(dev),
                "device_type": a.get("device_type"),
                "binding_uid": a.get("binding_uid"),
                "modules": modules,
                "mode": "read_only_v1",
            }
        finally:
            c.close()

    def summary(self, binding_key: str) -> Dict[str, Any]:
        self._auth(binding_key)
        c = self.cp._c()
        try:
            def count(table: str) -> int:
                return int(c.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])

            row = c.execute(
                "SELECT COUNT(*) FILTER (WHERE COALESCE(ocupat,0)<>0 OR COALESCE(are_inhumari,0)<>0) AS occupied, "
                "COUNT(*) FILTER (WHERE COALESCE(ocupat,0)=0 AND COALESCE(are_inhumari,0)=0) AS available "
                "FROM locuri_de_veci"
            ).fetchone()
            return {
                "graves": count("locuri_de_veci"),
                "concessionaires": count("concesionari"),
                "burials": count("inhumati"),
                "contracts": count("contracte"),
                "payments": count("plati"),
                "occupied": int(row["occupied"] or 0),
                "available": int(row["available"] or 0),
            }
        finally:
            c.close()

    def search(self, binding_key: str, query: str, limit: int = 12) -> Dict[str, List[Dict[str, Any]]]:
        a = self._auth(binding_key)
        q = (query or "").strip()
        if len(q) < 2:
            return {"graves": [], "concessionaires": [], "burials": [], "contracts": []}
        limit = max(1, min(int(limit), SEARCH_LIMIT_MAX))
        like = f"%{q}%"
        c = self.cp._c()
        try:
            graves = [dict(uid=r["uid"], code=_text(r["cod_loc"]), sector=_text(r["sector"]), parcel=_text(r["parcela"]),
                           row=_text(r["rand"]), number=_text(r["numar"]), availability=_text(r["disponibilitate"]),
                           state=_text(r["stare"]), occupied=bool(r["ocupat"] or r["are_inhumari"]))
                      for r in c.execute(
                          "SELECT uid,cod_loc,sector,parcela,rand,numar,disponibilitate,stare,ocupat,are_inhumari "
                          "FROM locuri_de_veci WHERE cod_loc ILIKE ? OR COALESCE(nr_vechi,'') ILIKE ? "
                          "OR COALESCE(sector,'') ILIKE ? OR COALESCE(parcela,'') ILIKE ? ORDER BY cod_loc LIMIT ?",
                          (like, like, like, like, limit)).fetchall()]

            concessionaires = [dict(uid=r["uid"], name=(f"{_text(r['nume'])} {_text(r['prenume'])}").strip(), phone=_text(r["telefon"]),
                                     email=_text(r["email"]), address=_text(r["adresa"]), grave_code=_text(r["cod_loc"]), grave_uid=_text(r["grave_uid"]))
                                for r in c.execute(
                                    "SELECT x.uid,x.nume,x.prenume,x.telefon,x.email,x.adresa,l.uid AS grave_uid,l.cod_loc "
                                    "FROM concesionari x LEFT JOIN contracte ct ON ct.concesionar_id=x.id "
                                    "LEFT JOIN locuri_de_veci l ON l.id=ct.id_loc "
                                    "WHERE COALESCE(x.nume,'') ILIKE ? OR COALESCE(x.prenume,'') ILIKE ? "
                                    "OR (COALESCE(x.nume,'') || ' ' || COALESCE(x.prenume,'')) ILIKE ? "
                                    "OR COALESCE(x.telefon,'') ILIKE ? OR COALESCE(x.cnp,'') ILIKE ? "
                                    "ORDER BY x.nume,x.prenume LIMIT ?", (like, like, like, like, like, limit)).fetchall()]

            burials = [dict(uid=r["uid"], name=(f"{_text(r['nume'])} {_text(r['prenume'])}").strip(), death_date=_text(r["data_deces"]),
                            burial_date=_text(r["data_inhumare"]), grave_uid=_text(r["grave_uid"]), grave_code=_text(r["cod_loc"]))
                       for r in c.execute(
                           "SELECT i.uid,i.nume,i.prenume,i.data_deces,i.data_inhumare,l.uid AS grave_uid,l.cod_loc "
                           "FROM inhumati i LEFT JOIN locuri_de_veci l ON l.id=i.id_loc "
                           "WHERE COALESCE(i.nume,'') ILIKE ? OR COALESCE(i.prenume,'') ILIKE ? "
                           "OR (COALESCE(i.nume,'') || ' ' || COALESCE(i.prenume,'')) ILIKE ? OR COALESCE(i.cnp,'') ILIKE ? "
                           "OR COALESCE(l.cod_loc,'') ILIKE ? ORDER BY i.nume,i.prenume LIMIT ?", (like, like, like, like, like, limit)).fetchall()]

            contracts = [dict(uid=r["uid"], number=_text(r["numar_contract"]), start_date=_text(r["data_contract"]),
                              end_date=_text(r["data_sfarsit"]), grave_uid=_text(r["grave_uid"]), grave_code=_text(r["cod_loc"]),
                              concessionaire_uid=_text(r["concessionaire_uid"]), concessionaire=(f"{_text(r['nume'])} {_text(r['prenume'])}").strip())
                         for r in c.execute(
                             "SELECT ct.uid,ct.version,ct.numar_contract,ct.data_contract,ct.data_sfarsit,l.uid AS grave_uid,COALESCE(l.cod_loc,ct.cod_loc) AS cod_loc,"
                             "x.uid AS concessionaire_uid,x.nume,x.prenume FROM contracte ct "
                             "LEFT JOIN locuri_de_veci l ON l.id=ct.id_loc LEFT JOIN concesionari x ON x.id=ct.concesionar_id "
                             "WHERE COALESCE(ct.numar_contract,'') ILIKE ? OR COALESCE(ct.cod_loc,'') ILIKE ? "
                             "OR (COALESCE(x.nume,'') || ' ' || COALESCE(x.prenume,'')) ILIKE ? "
                             "ORDER BY ct.id DESC LIMIT ?", (like, like, like, limit)).fetchall()]
            vis = sv.readable_entities(c, a)      # permisiuni aplicate pe server (rol/capabilități)
            return {"graves": graves if "locuri_de_veci" in vis else [], "concessionaires": concessionaires if "concesionari" in vis else [],
                    "burials": burials if "inhumati" in vis else [], "contracts": contracts if "contracte" in vis else []}
        finally:
            c.close()

    def map_data(self, binding_key: str, limit: int = MAP_LIMIT_MAX) -> Dict[str, Any]:
        a = self._auth(binding_key)
        limit = max(1, min(int(limit), MAP_LIMIT_MAX))
        c = self.cp._c()
        try:
            if "locuri_de_veci" not in sv.readable_entities(c, a):
                return {"plots": [], "sectors": [], "truncated": False}
            plots = []
            for r in c.execute(
                "SELECT uid,cod_loc,points,sector,parcela,rand,numar,disponibilitate,stare,ocupat,are_inhumari "
                "FROM locuri_de_veci ORDER BY cod_loc LIMIT ?", (limit,)).fetchall():
                plots.append({"uid": r["uid"], "code": _text(r["cod_loc"]), "points": _json(r["points"], []), "sector": _text(r["sector"]),
                              "parcel": _text(r["parcela"]), "row": _text(r["rand"]), "number": _text(r["numar"]),
                              "availability": _text(r["disponibilitate"]), "state": _text(r["stare"]),
                              "occupied": bool(r["ocupat"] or r["are_inhumari"])})
            sectors = []
            for r in c.execute("SELECT uid,id,points,color FROM sectoare ORDER BY id").fetchall():
                sectors.append({"uid": r["uid"], "name": _text(r["id"]), "points": _json(r["points"], []), "color": _text(r["color"])})
            return {"plots": plots, "sectors": sectors, "truncated": len(plots) >= limit}
        finally:
            c.close()

    def data_status(self, binding_key: str) -> Dict[str, Any]:
        """Diagnostic READ-ONLY, limitat de binding/RLS. Returnează doar numărători, fără date personale sau UUID-uri.

        Importul bootstrap din CP și registrul live (Sync v1) sunt două zone DISTINCTE în schema actuală.
        Un telefon asociat poate vedea zero locuri chiar dacă cp_registry_row_data are date: este nevoie de
        materializare explicită către registrele live, nu de re-pairing sau de copierea brută a SQLite-ului.
        """
        a = self._auth(binding_key)
        c = self.cp._c()
        try:
            scope = (a["organization_uid"], a["cemetery_uid"])
            live = {}
            for table, label in (("locuri_de_veci", "graves"), ("concesionari", "concessionaires"),
                                 ("contracte", "contracts"), ("inhumati", "burials"), ("plati", "payments"),
                                 ("amenajari_avize", "permits"), ("sectoare", "sectors")):
                r = c.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE organization_uid=? AND cemetery_uid=?", scope).fetchone()
                live[label] = int(r["n"] or 0)
            # JSONB points e deja validat în momentul sincronizării. Aici verificăm dacă există poligoane reale.
            r = c.execute("SELECT COUNT(*) AS n FROM locuri_de_veci "
                          "WHERE organization_uid=? AND cemetery_uid=? AND jsonb_array_length(points)>=3", scope).fetchone()
            live["drawn_graves"] = int(r["n"] or 0)
            staged = {}
            for r in c.execute("SELECT entity, COUNT(*) AS n FROM cp_registry_row_data "
                               "WHERE organization_uid=? AND cemetery_uid=? GROUP BY entity", scope).fetchall():
                staged[_text(r["entity"])] = int(r["n"] or 0)
            imported = sum(staged.values())
            if live["graves"] == 0:
                state = "imported_not_materialized" if imported else "empty_live_registry"
            elif live["drawn_graves"] == 0:
                state = "no_drawn_geometry"
            else:
                state = "live_data_available"
            return {"state": state, "live": live, "bootstrap_rows": imported, "bootstrap_by_entity": staged}
        finally:
            c.close()

    def grave_detail(self, binding_key: str, grave_uid: str) -> Dict[str, Any]:
        a = self._auth(binding_key)
        # MAIN creează atât UID-uri hex32 (SQLite randomblob), cât și UUID-uri canonice cu cratime.
        # Nu normalizăm obligatoriu la UUID36: în PostgreSQL coloana UID este TEXT și trebuie păstrată exact.
        try:
            parsed = uuid.UUID(str(grave_uid))
        except (ValueError, TypeError, AttributeError):
            raise cc.ScopeError("loc inexistent") from None
        candidates = tuple(dict.fromkeys((str(grave_uid), parsed.hex, str(parsed))))
        placeholders = ",".join("?" for _ in candidates)
        c = self.cp._c()
        try:
            g = c.execute(
                "SELECT uid,version,cod_loc,points,sector,parcela,rand,numar,capacitate,disponibilitate,tip,stare,categorie,suprafata,nr_vechi,numar_cf,"
                f"ocupat,observatii,are_inhumari FROM locuri_de_veci WHERE uid IN ({placeholders}) "
                "ORDER BY CASE WHEN uid=? THEN 0 ELSE 1 END LIMIT 1", (*candidates, str(grave_uid))).fetchone()
            if not g:
                raise cc.ScopeError("loc inexistent")
            grave_uid = g["uid"]  # Referințele pentru contracte/plăți/înhumați folosesc exact UID-ul stocat.
            grave = _row(g)
            grave["points"] = _json(grave.get("points"), [])
            grave["occupied"] = bool(grave.pop("ocupat", 0) or grave.get("are_inhumari"))

            contracts = [_row(r) for r in c.execute(
                "SELECT ct.uid,ct.version,ct.numar_contract,ct.data_contract,ct.data_inceput,ct.data_sfarsit,ct.valabilitate,ct.observatii,"
                "x.uid AS concessionaire_uid,x.version AS concessionaire_version,x.nume,x.prenume,x.telefon,x.email,x.adresa "
                "FROM contracte ct LEFT JOIN concesionari x ON x.id=ct.concesionar_id WHERE ct.id_loc=(SELECT id FROM locuri_de_veci WHERE uid=?) "
                "ORDER BY ct.id DESC", (grave_uid,)).fetchall()]
            burials = [_row(r) for r in c.execute(
                "SELECT uid,version,nume,prenume,data_nasterii,data_deces,data_inhumare,data_deshumare,sex,observatii,nr_certificat_deces,nr_adeverinta_inhumare "
                "FROM inhumati WHERE id_loc=(SELECT id FROM locuri_de_veci WHERE uid=?) ORDER BY id DESC", (grave_uid,)).fetchall()]
            payments = [_row(r) for r in c.execute(
                "SELECT p.uid,p.suma,p.data_plata,p.tip,p.chitanta_nr,p.observatii FROM plati p "
                "WHERE p.cod_loc=? OR p.contract_id IN (SELECT id FROM contracte WHERE id_loc=(SELECT id FROM locuri_de_veci WHERE uid=?)) "
                "ORDER BY p.id DESC", (grave["cod_loc"], grave_uid)).fetchall()]
            permits = [_row(r) for r in c.execute(
                "SELECT uid,nr_aviz,data_aviz,lucrare FROM amenajari_avize WHERE cod_loc=? ORDER BY id DESC",
                (grave["cod_loc"],)).fetchall()]
            vis = sv.readable_entities(c, a)      # permisiuni aplicate pe server (rol/capabilități)
            if "locuri_de_veci" not in vis:
                raise cc.PermissionDenied("nu ai dreptul de citire pentru acest registru")
            return {"grave": grave, "contracts": contracts if "contracte" in vis else [], "burials": burials if "inhumati" in vis else [],
                    "payments": payments if "plati" in vis else [], "permits": permits if "amenajari_avize" in vis else []}
        finally:
            c.close()
