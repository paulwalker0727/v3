"""Verificări: înhumați, avize, documente/fotografii, plăți, sincronizare, finanțe. Doar SELECT; nicio corecție."""
from __future__ import annotations

import hashlib
import math
import os
from collections import defaultdict

from .checks_places import date_fmt, text_limits
from .context import Ctx
from .util import MAX_TEXT, blank, cnp_info, cnp_key, norm_name, parse_date, txt  # noqa: F401

AVIZ_STARI = ("planificata", "in_lucru", "finalizata")        # oglindește cimitir_entities.AVIZ_STARI
INH_TEXT_COLS = ("nume", "prenume", "cnp", "data_nasterii", "data_deces", "data_inhumare", "data_deshumare", "sex", "cauza_deces", "profesie",
                 "religie", "ultima_adresa", "observatii", "nr_certificat_deces", "nr_adeverinta_inhumare")
AVZ_TEXT_COLS = ("cod_loc", "nr_aviz", "data_aviz", "lucrare", "stare", "executant", "data_inceput", "data_finalizare", "observatii")
ENTITY_TABLES = {"loc": ("locuri_de_veci", "cod_loc"), "inhumat": ("inhumati", "id"), "concesionar": ("concesionari", "id"),
                 "contract": ("contracte", "id"), "aviz": ("amenajari_avize", "id"), "cerere": ("cereri_concesionare", "id"), "plata": ("plati", "id")}
DOC_TYPE_ENTITY = {"poza_loc": {"loc"}, "foto_lucrare": {"aviz"}, "lucrare_inainte": {"aviz"}, "lucrare_in_timpul": {"aviz"},
                   "lucrare_dupa": {"aviz"}, "contract_scan": {"contract"}, "certificat_deces": {"inhumat"}}


def _sex(v) -> str:
    s = txt(v).lower()
    return "M" if s in ("m", "masculin", "b", "barbat", "bărbat") else "F" if s in ("f", "feminin", "femeie") else ""


def check_inhumati(c: Ctx) -> None:
    if not c.need("inhumati", "id", "id_loc"):
        return
    rows = c.q("SELECT * FROM inhumati")
    locs = {r["id"]: r for r in c.q("SELECT id, cod_loc, capacitate FROM locuri_de_veci")} if c.has("locuri_de_veci", "id") else {}
    ctr = {r["id"]: r for r in c.q("SELECT id, id_loc FROM contracte")} if c.has("contracte", "id") else {}
    by_cnp, by_dup, per_loc = defaultdict(list), defaultdict(list), defaultdict(int)
    for r in rows:
        lid = r.get("id_loc")
        ref = {"uid": r.get("uid"), "cod_loc": locs.get(lid, {}).get("cod_loc") if lid is not None else None}
        if lid is None:
            c.add("INH001", "inhumati", r["id"], "id_loc nul.", {}, **ref)
        elif locs and lid not in locs:
            c.add("INH002", "inhumati", r["id"], f"id_loc={lid} nu există.", {"id_loc": lid}, **ref)
        else:
            per_loc[lid] += 1
        cid = r.get("contract_id")
        if cid is not None:
            if c.has("contracte", "id") and cid not in ctr:
                c.add("INH003", "inhumati", r["id"], f"contract_id={cid} nu există.", {"contract_id": cid}, **ref)
            elif cid in ctr and ctr[cid].get("id_loc") is not None and lid is not None and ctr[cid]["id_loc"] != lid:
                c.add("INH004", "inhumati", r["id"], f"Contractul {cid} aparține locului {ctr[cid]['id_loc']}, înhumatul este la locul {lid}.", {"contract_id": cid, "loc_contract": ctr[cid]["id_loc"], "loc_inhumat": lid}, **ref)
        nm = norm_name(r.get("nume"), r.get("prenume"))
        if not nm:
            c.add("INH005", "inhumati", r["id"], "Nume și prenume goale.", {}, **ref)
        pn, pd, pi, pe = (parse_date(r.get(k)) for k in ("data_nasterii", "data_deces", "data_inhumare", "data_deshumare"))
        for col, p in (("data_nasterii", pn), ("data_deces", pd), ("data_inhumare", pi), ("data_deshumare", pe)):
            if p is not None and not p.ok:
                c.add("INH007", "inhumati", r["id"], f"{col} nu este o dată validă.", {"camp": col, "lungime": len(p.raw)}, **ref)
        date_fmt(c, "inhumati", r, ("data_nasterii", "data_deces", "data_inhumare", "data_deshumare"), invalid_code="")
        for col, p in (("data_nasterii", pn), ("data_deces", pd)):
            if p and p.ok and p.value > c.today:
                c.add("INH007", "inhumati", r["id"], f"{col} este în viitor.", {"camp": col, "data": p.raw}, **ref)
        pairs = (("data_nasterii", pn, "data_deces", pd), ("data_deces", pd, "data_inhumare", pi), ("data_inhumare", pi, "data_deshumare", pe))
        for a, pa, b, pb in pairs:
            if pa and pb and pa.ok and pb.ok and pb.value < pa.value:
                c.add("INH006", "inhumati", r["id"], f"{b} este înaintea {a}.", {a: pa.raw, b: pb.raw}, **ref)
        if pn and pd and pn.ok and pd.ok and (pd.value - pn.value).days > 365 * 125 and pd.value >= pn.value:
            c.add("INH007", "inhumati", r["id"], "Vârstă la deces de peste 125 de ani.", {"ani": (pd.value - pn.value).days // 365}, **ref)
        info = cnp_info(r.get("cnp"))
        if info is not None:
            by_cnp[cnp_key(r.get("cnp"))].append(r)
            if not info.ok:
                c.add("INH011", "inhumati", r["id"], f"CNP invalid: {info.reason}.", {"motiv": info.reason}, **ref)
            else:
                bad = []
                if info.birth and pn and pn.ok and pn.value != info.birth:
                    bad.append("data nașterii")
                sx = _sex(r.get("sex"))
                if info.sex and sx and sx != info.sex:
                    bad.append("sex")
                if bad:
                    c.add("INH012", "inhumati", r["id"], f"Nu corespund CNP-ului: {', '.join(bad)} (valorile omise).", {"campuri": bad}, **ref)
        if nm and pd is not None:
            by_dup[(nm, txt(r.get("data_deces")))].append(r)
    for grp in by_cnp.values():
        if len(grp) > 1:
            ids = [str(x["id"]) for x in grp][:20]
            for x in grp:
                c.add("INH008", "inhumati", x["id"], f"Același CNP la {len(grp)} înhumați (valoarea omisă).", {"id_uri_grup": ids}, uid=x.get("uid"))
    for grp in by_dup.values():
        if len(grp) > 1:
            ids = [str(x["id"]) for x in grp][:20]
            for x in grp:
                c.add("INH009", "inhumati", x["id"], f"Același nume și aceeași dată a decesului la {len(grp)} înhumați.", {"id_uri_grup": ids, "acelasi_loc": len({y.get("id_loc") for y in grp}) == 1}, uid=x.get("uid"))
    for lid, n in per_loc.items():
        cap = txt(locs.get(lid, {}).get("capacitate"))
        if cap.isdigit() and n > int(cap):
            c.add("INH010", "locuri_de_veci", lid, f"{n} înhumați la un loc cu capacitatea {cap}.", {"inhumati": n, "capacitate": int(cap)}, cod_loc=locs[lid].get("cod_loc"))
    text_limits(c, "inhumati", "INH013", "inhumati", INH_TEXT_COLS, rows)


def check_avize(c: Ctx) -> None:
    if not c.need("amenajari_avize", "id", "cod_loc"):
        return
    rows = c.q("SELECT * FROM amenajari_avize")
    cods = {txt(r["cod_loc"]) for r in c.q("SELECT cod_loc FROM locuri_de_veci")} if c.has("locuri_de_veci", "cod_loc") else None
    seen = defaultdict(list)
    for r in rows:
        cod = txt(r.get("cod_loc"))
        ref = {"uid": r.get("uid"), "cod_loc": cod or None}
        if not cod or (cods is not None and cod not in cods):
            c.add("AVZ001", "amenajari_avize", r["id"], "cod_loc " + ("gol." if not cod else "nu corespunde niciunui loc."), {}, **ref)
        nr = txt(r.get("nr_aviz"))
        if not nr:
            c.add("AVZ002", "amenajari_avize", r["id"], "nr_aviz gol.", {}, **ref)
        else:
            seen[(cod, nr.lower())].append(r)
        pa, pi, pf = parse_date(r.get("data_aviz")), parse_date(r.get("data_inceput")), parse_date(r.get("data_finalizare"))
        for col, p in (("data_aviz", pa), ("data_inceput", pi), ("data_finalizare", pf)):
            if p is not None and not p.ok:
                c.add("AVZ007", "amenajari_avize", r["id"], f"{col} nu este o dată validă.", {"camp": col, "lungime": len(p.raw)}, **ref)
        date_fmt(c, "amenajari_avize", r, ("data_aviz", "data_inceput", "data_finalizare"), invalid_code="")
        if pi and pf and pi.ok and pf.ok and pf.value < pi.value:
            c.add("AVZ004", "amenajari_avize", r["id"], "data_finalizare este înaintea data_inceput.", {"data_inceput": pi.raw, "data_finalizare": pf.raw}, **ref)
        st = txt(r.get("stare"))
        if st and st not in AVIZ_STARI:
            c.add("AVZ005", "amenajari_avize", r["id"], f"Stare '{st[:30]}' în afara valorilor standard.", {"stare": st[:30]}, **ref)
        elif st == "planificata" and not blank(r.get("data_finalizare")):
            c.add("AVZ006", "amenajari_avize", r["id"], "Stare 'planificata' dar are dată de finalizare.", {"stare": st}, **ref)
        elif st == "in_lucru" and not blank(r.get("data_finalizare")):
            c.add("AVZ006", "amenajari_avize", r["id"], "Stare 'in_lucru' dar are dată de finalizare.", {"stare": st}, **ref)
        elif st == "finalizata" and blank(r.get("data_finalizare")):
            c.add("AVZ006", "amenajari_avize", r["id"], "Stare 'finalizata' fără dată de finalizare.", {"stare": st}, severity="warning", **ref)
        elif st == "finalizata" and pf and pf.ok and pf.value > c.today:
            c.add("AVZ006", "amenajari_avize", r["id"], "Stare 'finalizata' dar data finalizării este în viitor.", {"data_finalizare": pf.raw}, **ref)
        elif not st and not blank(r.get("data_finalizare")):
            c.add("AVZ006", "amenajari_avize", r["id"], "Are dată de finalizare dar starea este necompletată.", {}, **ref)
    for grp in seen.values():
        if len(grp) > 1:
            ids = [str(x["id"]) for x in grp][:20]
            for x in grp:
                c.add("AVZ003", "amenajari_avize", x["id"], f"Același număr de aviz repetat la același loc ({len(grp)} ori).", {"id_uri_grup": ids}, uid=x.get("uid"), cod_loc=x.get("cod_loc"))
    text_limits(c, "amenajari_avize", "AVZ008", "amenajari_avize", AVZ_TEXT_COLS, rows)


def _file_state(c: Ctx, r: dict, kind: str, entity: str):
    path, mid = txt(r.get("file_path")), txt(r.get("media_id")) if "media_id" in r else ""
    if not path and not mid:
        c.add("DOC003", entity, r["id"], f"{kind} fără file_path și fără media_id.", {})
        return
    exists = bool(path) and os.path.isfile(path if os.path.isabs(path) or not c.media_root else os.path.join(c.media_root, path))
    if not exists:
        if mid:
            c.add("DOC005", entity, r["id"], f"{kind}: fișierul nu e pe disc; are media_id (copie în storage).", {"media_id_prezent": True})
        else:
            c.add("DOC004", entity, r["id"], f"{kind}: fișierul referențiat nu există pe disc ({os.path.basename(path)[:60]}).", {"fisier": os.path.basename(path)[:60]})
        return
    real = path if os.path.isabs(path) or not c.media_root else os.path.join(c.media_root, path)
    size = r.get("size")
    if size not in (None, "") and str(size).isdigit() and os.path.getsize(real) != int(size):
        c.add("DOC009", entity, r["id"], f"{kind}: dimensiune pe disc {os.path.getsize(real)} ≠ înregistrată {size}.", {"disc": os.path.getsize(real), "inregistrat": int(size)})
    sha = txt(r.get("sha256")).lower() if "sha256" in r else ""
    if c.deep and sha:
        h = hashlib.sha256()
        with open(real, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != sha:
            c.add("DOC013", entity, r["id"], f"{kind}: sha256 calculat diferă de cel înregistrat.", {})


def check_documente(c: Ctx) -> None:
    if c.need("documents", "id", "entity_type", "entity_id"):
        existing = {}
        for et, (tbl, col) in ENTITY_TABLES.items():
            existing[et] = {str(r[0]) for r in c.conn.execute(f"SELECT {col} FROM {tbl}").fetchall()} if c.has(tbl, col) else None
        loc_ids = {str(r["id"]) for r in c.q("SELECT id FROM locuri_de_veci")} if c.has("locuri_de_veci", "id") else set()
        by_hash = defaultdict(list)
        for r in c.q("SELECT * FROM documents"):
            et, eid = txt(r.get("entity_type")), txt(r.get("entity_id"))
            cod = eid if et == "loc" else None
            if et not in ENTITY_TABLES:
                c.add("DOC001", "documents", r["id"], f"entity_type='{et[:30]}'.", {"entity_type": et[:30]}, cod_loc=cod)
            elif existing.get(et) is not None and eid not in existing[et]:
                if et == "loc" and eid in loc_ids:
                    c.add("DOC012", "documents", r["id"], "entity_id pare id numeric de loc, nu cod_loc.", {"entity_id": eid[:20]})
                else:
                    c.add("DOC002", "documents", r["id"], f"Entitatea '{et}' cu id '{eid[:30]}' nu există.", {"entity_type": et, "entity_id": eid[:30]}, cod_loc=cod)
            dt = txt(r.get("doc_type"))
            if not dt:
                c.add("DOC006", "documents", r["id"], "doc_type gol.", {"camp": "doc_type"}, cod_loc=cod)
            elif dt in DOC_TYPE_ENTITY and et in ENTITY_TABLES and et not in DOC_TYPE_ENTITY[dt]:
                c.add("DOC008", "documents", r["id"], f"doc_type='{dt}' este asociat entității '{et}' (așteptat: {', '.join(sorted(DOC_TYPE_ENTITY[dt]))}).", {"doc_type": dt, "entity_type": et}, cod_loc=cod)
            _file_state(c, r, "Document", "documents")
            if not blank(r.get("sha256")):
                by_hash[(et, eid, txt(r.get("sha256")).lower())].append(r)
        for grp in by_hash.values():
            if len(grp) > 1:
                ids = [str(x["id"]) for x in grp][:20]
                for x in grp:
                    c.add("DOC007", "documents", x["id"], f"Același sha256 de {len(grp)} ori la aceeași entitate.", {"id_uri_grup": ids})
    if c.need("photos", "id", "cod_loc"):
        cods = {txt(r["cod_loc"]) for r in c.q("SELECT cod_loc FROM locuri_de_veci")} if c.has("locuri_de_veci", "cod_loc") else None
        covers = defaultdict(list)
        for r in c.q("SELECT * FROM photos"):
            cod = txt(r.get("cod_loc"))
            if cods is not None and cod not in cods:
                c.add("DOC010", "photos", r["id"], f"cod_loc '{cod[:30]}' nu există.", {}, cod_loc=cod)
            _file_state(c, r, "Fotografie", "photos")
            if r.get("is_cover") in (1, "1", True):
                covers[cod].append(r)
        for cod, grp in covers.items():
            if len(grp) > 1:
                for x in grp:
                    c.add("DOC011", "photos", x["id"], f"{len(grp)} fotografii cover la același loc.", {"id_uri_grup": [str(y["id"]) for y in grp][:20]}, cod_loc=cod)


def check_plati(c: Ctx) -> None:
    if not c.need("plati", "id", "cod_loc", "suma"):
        return
    rows = c.q("SELECT * FROM plati")
    cods = {txt(r["cod_loc"]) for r in c.q("SELECT cod_loc FROM locuri_de_veci")} if c.has("locuri_de_veci", "cod_loc") else None
    ctr = {r["id"]: txt(r.get("cod_loc")) for r in c.q("SELECT id, cod_loc FROM contracte")} if c.has("contracte", "id") else None
    dup, rec = defaultdict(list), defaultdict(list)
    for r in rows:
        cod, cid = txt(r.get("cod_loc")), r.get("contract_id")
        ref = {"uid": r.get("uid"), "cod_loc": cod or None}
        if not cod and cid is None:
            c.add("PLT001", "plati", r["id"], "Nici cod_loc, nici contract_id.", {}, **ref)
        if cod and cods is not None and cod not in cods:
            c.add("PLT002", "plati", r["id"], f"cod_loc '{cod[:30]}' nu există.", {}, **ref)
        if cid is not None and ctr is not None:
            if cid not in ctr:
                c.add("PLT003", "plati", r["id"], f"contract_id={cid} nu există.", {"contract_id": cid}, **ref)
            elif cod and ctr[cid] and ctr[cid] != cod:
                c.add("PLT004", "plati", r["id"], f"Plata este la locul indicat de cod_loc, contractul {cid} e la alt loc.", {"contract_id": cid}, **ref)
        s = r.get("suma")
        if s is None or s == "":
            c.add("PLT005", "plati", r["id"], "Suma lipsește.", {}, **ref)
        else:
            try:
                f = float(s)
                if not math.isfinite(f):
                    c.add("PLT006", "plati", r["id"], "Suma este NaN/infinită.", {}, **ref)
                elif f == 0:
                    c.add("PLT005", "plati", r["id"], "Suma este zero.", {}, **ref)
                elif f < 0:
                    c.add("PLT007", "plati", r["id"], "Sumă negativă.", {"suma": f}, **ref)
            except (TypeError, ValueError):
                c.add("PLT006", "plati", r["id"], "Suma nu este numerică.", {"tip": type(s).__name__}, **ref)
        p = parse_date(r.get("data_plata"))
        if p is None:
            c.add("PLT008", "plati", r["id"], "data_plata lipsește.", {}, **ref)
        elif not p.ok:
            c.add("PLT008", "plati", r["id"], "data_plata nu este o dată validă.", {"lungime": len(p.raw)}, **ref)
        elif p.value > c.today:
            c.add("PLT008", "plati", r["id"], "data_plata este în viitor.", {"data": p.raw}, **ref)
        else:
            date_fmt(c, "plati", r, ("data_plata",), invalid_code="")
        dup[(cod, txt(r.get("suma")), txt(r.get("data_plata")), txt(r.get("tip")).lower(), txt(r.get("chitanta_nr")).lower())].append(r)
        if not blank(r.get("chitanta_nr")):
            rec[txt(r.get("chitanta_nr")).lower()].append(r)
    for grp in dup.values():
        if len(grp) > 1:
            ids = [str(x["id"]) for x in grp][:20]
            for x in grp:
                c.add("PLT009", "plati", x["id"], f"{len(grp)} plăți identice (loc, sumă, dată, tip, chitanță).", {"id_uri_grup": ids}, uid=x.get("uid"), cod_loc=txt(x.get("cod_loc")) or None)
    for k, grp in rec.items():
        if len(grp) > 1 and len({(txt(x.get("cod_loc")), txt(x.get("suma")), txt(x.get("data_plata"))) for x in grp}) > 1:
            ids = [str(x["id"]) for x in grp][:20]
            for x in grp:
                c.add("PLT010", "plati", x["id"], f"Număr de chitanță folosit la {len(grp)} plăți diferite.", {"id_uri_grup": ids}, uid=x.get("uid"), cod_loc=txt(x.get("cod_loc")) or None)


def check_sincronizare(c: Ctx) -> None:
    for tbl in ("concesionari", "contracte", "inhumati", "plati", "amenajari_avize"):
        if not c.has(tbl, "id", "uid"):
            continue
        seen = defaultdict(list)
        for r in c.q(f"SELECT id, uid FROM {tbl}"):
            if blank(r["uid"]):
                c.add("SYN001", tbl, r["id"], "uid lipsă (nu intră în sincronizare până la inițializare).", {})
            else:
                seen[r["uid"]].append(r["id"])
        for u, ids in seen.items():
            if len(ids) > 1:
                for i in ids:
                    c.add("SYN002", tbl, i, "uid duplicat în tabel.", {"id_uri_grup": [str(x) for x in ids][:20]}, uid=u)


def check_financiar(c: Ctx) -> None:
    if c.has("plati", "id"):
        n = c.conn.execute("SELECT COUNT(*) FROM plati").fetchone()[0]
        c.add("FIN000", "plati", None, f"Există {n} plăți, dar nicio tabelă de obligații/scadențe: soldul nu se poate calcula.", {"plati": n})
