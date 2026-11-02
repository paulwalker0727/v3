"""Verificări: locuri de veci, sectoare, concesionari, contracte. Doar SELECT; nicio corecție."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict

from .context import Ctx
from .util import (MAX_POINTS, MAX_TEXT, blank, cnp_info, cnp_key, norm_code, norm_name, parse_date, parse_points, polygon_area2,
                   polygon_signature, self_intersects, txt, valid_email)

LOC_TEXT_COLS = ("cod_loc", "color", "sector", "parcela", "rand", "numar", "capacitate", "disponibilitate", "tip", "stare", "categorie",
                 "nr_vechi", "numar_cf", "observatii", "extra_data")
CTR_TEXT_COLS = ("numar_contract", "data_contract", "data_inceput", "data_sfarsit", "cod_loc", "valabilitate", "observatii", "extra_data",
                 "owner_fullname", "owner_fullname_2", "owner_address", "owner_id_card_serie_number", "owner_cnp", "owner_phone",
                 "owner_email", "cemetery_parcel", "cemetery_row", "cemetery_tomb", "sector")


def text_limits(c: Ctx, table: str, code: str, entity: str, cols, rows) -> None:
    for r in rows:
        long_cols = [k for k in cols if k in r and isinstance(r[k], str) and len(r[k]) > MAX_TEXT]
        if long_cols:
            c.add(code, entity, r["id"], f"Coloane peste {MAX_TEXT} caractere: {', '.join(long_cols)}.", {"coloane": long_cols}, uid=r.get("uid"))


def date_fmt(c: Ctx, entity: str, r: dict, cols, invalid_code: str = "DAT002") -> None:
    """DAT001 (format ISO într-un câmp MAIN zz.ll.aaaa) și, pentru entități fără cod propriu, DAT002 (dată invalidă)."""
    for col in cols:
        pd = parse_date(r.get(col))
        if pd is None:
            continue
        if pd.fmt in ("iso", "compact") and pd.ok:
            c.add("DAT001", entity, r["id"], f"Câmpul {col} are format {'aaaa-ll-zz' if pd.fmt == 'iso' else 'zzllaaaa'}.", {"camp": col, "format": pd.fmt}, uid=r.get("uid"))
        elif not pd.ok and invalid_code:
            c.add(invalid_code, entity, r["id"], f"Câmpul {col} nu este o dată validă.", {"camp": col, "lungime": len(pd.raw)}, uid=r.get("uid"))


def polygon_issues(c: Ctx, entity: str, r: dict, code_invalid: str, code_degenerate: str, sigs: Dict) -> None:
    pts, err = parse_points(r.get("points"))
    if err:
        c.add(code_invalid, entity, r["id"], f"points: {err}.", {"eroare": err}, uid=r.get("uid"), cod_loc=r.get("cod_loc"))
        return
    if pts is None:
        return
    if len(pts) > MAX_POINTS and entity == "locuri_de_veci":
        c.add("LOC008", entity, r["id"], f"Poligonul are {len(pts)} puncte (limita de sincronizare {MAX_POINTS}).", {"puncte": len(pts), "limita": MAX_POINTS}, uid=r.get("uid"), cod_loc=r.get("cod_loc"))
    problems = []
    if len(pts) < 3:
        problems.append(f"doar {len(pts)} puncte")
    elif abs(polygon_area2(pts)) < 1e-12:
        problems.append("arie zero")
    elif self_intersects(pts):
        problems.append("laturi care se intersectează")
    if problems:
        c.add(code_degenerate, entity, r["id"], "Poligon degenerat: " + ", ".join(problems) + ".", {"probleme": problems, "puncte": len(pts)}, uid=r.get("uid"), cod_loc=r.get("cod_loc"))
    elif entity == "locuri_de_veci":
        sigs[polygon_signature(pts)].append(r)


def check_locuri(c: Ctx) -> None:
    if not c.need("locuri_de_veci", "id", "cod_loc"):
        return
    rows = c.q("SELECT * FROM locuri_de_veci")
    sector_ids = {norm_code(s["id"]) for s in c.q("SELECT id FROM sectoare")} if c.has("sectoare", "id") else set()
    inh_by_loc: Dict[int, int] = defaultdict(int)
    if c.has("inhumati", "id_loc"):
        for r in c.q("SELECT id_loc, COUNT(*) AS n FROM inhumati WHERE id_loc IS NOT NULL GROUP BY id_loc"):
            inh_by_loc[r["id_loc"]] = r["n"]
    by_code, by_pos, sigs = defaultdict(list), defaultdict(list), defaultdict(list)
    uids = defaultdict(list)
    for r in rows:
        cod = r.get("cod_loc")
        if blank(cod):
            c.add("LOC001", "locuri_de_veci", r["id"], "cod_loc este gol/NULL.", {"cod_loc": "gol"}, uid=r.get("uid"))
        else:
            if cod != cod.strip() or re.search(r"[\x00-\x1f\x7f]", cod):
                c.add("LOC002", "locuri_de_veci", r["id"], "cod_loc are spații la capete sau caractere de control.", {"lungime": len(cod), "lungime_curatata": len(cod.strip())}, uid=r.get("uid"), cod_loc=cod.strip())
            by_code[norm_code(cod)].append(r)
        if c.has("locuri_de_veci", "uid"):
            if blank(r.get("uid")):
                c.add("LOC003", "locuri_de_veci", r["id"], "uid lipsă.", {}, cod_loc=cod)
            else:
                uids[r["uid"]].append(r)
        sec = txt(r.get("sector"))
        if sec and sector_ids and norm_code(sec) not in sector_ids:
            c.add("LOC004", "locuri_de_veci", r["id"], f"Sectorul '{sec[:40]}' nu există în tabela sectoare ({len(sector_ids)} sectoare).", {"sectoare_definite": len(sector_ids)}, uid=r.get("uid"), cod_loc=cod)
        polygon_issues(c, "locuri_de_veci", r, "LOC005", "LOC006", sigs)
        pos = tuple(norm_code(r.get(k)) for k in ("sector", "parcela", "rand", "numar"))
        if all(pos):
            by_pos[pos].append(r)
        sup = r.get("suprafata")
        if sup is not None and sup != "":
            try:
                if float(sup) < 0:
                    c.add("LOC011", "locuri_de_veci", r["id"], "Suprafață negativă.", {"suprafata": float(sup)}, uid=r.get("uid"), cod_loc=cod)
            except (TypeError, ValueError):
                c.add("LOC011", "locuri_de_veci", r["id"], "Suprafața nu este numerică.", {"tip": type(sup).__name__}, uid=r.get("uid"), cod_loc=cod)
        ed = r.get("extra_data")
        if not blank(ed):
            import json
            try:
                json.loads(ed)
            except (TypeError, ValueError):
                c.add("LOC011", "locuri_de_veci", r["id"], "extra_data nu este JSON valid.", {}, uid=r.get("uid"), cod_loc=cod)
        n_inh = inh_by_loc.get(r["id"], 0)
        txt_state = f"{txt(r.get('disponibilitate'))} {txt(r.get('stare'))}".lower()
        free = not r.get("ocupat") and not r.get("are_inhumari") and not any(w in txt_state for w in ("ocupat", "rezerv", "indisponibil"))
        cap = txt(r.get("capacitate"))
        if n_inh and free and not (cap.isdigit() and n_inh < int(cap)):
            c.add("LOC010", "locuri_de_veci", r["id"], f"Apare ca liber (ocupat/are_inhumari/stare) deși are {n_inh} înhumați.", {"inhumati": n_inh, "ocupat": r.get("ocupat"), "capacitate": cap or None}, uid=r.get("uid"), cod_loc=cod)
        if r.get("are_inhumari") and not n_inh and c.has("inhumati", "id_loc"):
            c.add("LOC012", "locuri_de_veci", r["id"], "are_inhumari=1 dar nu există înhumați asociați.", {"inhumati": 0}, uid=r.get("uid"), cod_loc=cod)
    for grp in by_code.values():
        if len(grp) > 1:
            ids = [str(x["id"]) for x in grp][:20]
            for x in grp:
                c.add("LOC009", "locuri_de_veci", x["id"], "Același cod_loc după normalizare (litere mari/mici, spații, separatori).", {"id_uri_grup": ids}, uid=x.get("uid"), cod_loc=x.get("cod_loc"))
    for pos, grp in by_pos.items():
        if len(grp) > 1:
            ids = [str(x["id"]) for x in grp][:20]
            for x in grp:
                c.add("LOC009", "locuri_de_veci", x["id"], "Aceeași poziție (sector, parcelă, rând, număr) la mai multe locuri.", {"id_uri_grup": ids}, uid=x.get("uid"), cod_loc=x.get("cod_loc"))
    for grp in sigs.values():
        if len(grp) > 1:
            ids = [str(x["id"]) for x in grp][:20]
            for x in grp:
                c.add("LOC007", "locuri_de_veci", x["id"], "Poligon identic cu cel al altor locuri.", {"id_uri_grup": ids}, uid=x.get("uid"), cod_loc=x.get("cod_loc"))
    for u, grp in uids.items():
        if len(grp) > 1:
            for x in grp:
                c.add("SYN002", "locuri_de_veci", x["id"], "uid duplicat.", {"id_uri_grup": [str(y["id"]) for y in grp][:20]}, uid=u)
    text_limits(c, "locuri_de_veci", "LOC013", "locuri_de_veci", LOC_TEXT_COLS, rows)


def check_sectoare(c: Ctx) -> None:
    if not c.need("sectoare", "id", "points"):
        return
    for r in c.q("SELECT id, points FROM sectoare"):
        polygon_issues(c, "sectoare", r, "SEC001", "SEC001", defaultdict(list))


def _contact_key(r: dict):
    ph = re.sub(r"\D", "", txt(r.get("telefon")))
    return (ph if len(ph) >= 6 else "", txt(r.get("email")).lower(), norm_code(r.get("adresa")))


def check_concesionari(c: Ctx) -> None:
    if not c.need("concesionari", "id", "nume"):
        return
    rows = c.q("SELECT * FROM concesionari")
    with_contract = set()
    if c.has("contracte", "concesionar_id"):
        with_contract |= {r["concesionar_id"] for r in c.q("SELECT DISTINCT concesionar_id FROM contracte WHERE concesionar_id IS NOT NULL")}
    if c.has("cereri_concesionare", "concesionar_id"):
        with_contract |= {r["concesionar_id"] for r in c.q("SELECT DISTINCT concesionar_id FROM cereri_concesionare WHERE concesionar_id IS NOT NULL")}
    by_cnp, by_name = defaultdict(list), defaultdict(list)
    for r in rows:
        nm = norm_name(r.get("nume"), r.get("prenume"))
        if not nm:
            c.add("CON001", "concesionari", r["id"], "Nume și prenume goale.", {}, uid=r.get("uid"))
        else:
            by_name[nm].append(r)
        info = cnp_info(r.get("cnp"))
        if info is not None and not info.ok:
            c.add("CON003", "concesionari", r["id"], f"CNP invalid: {info.reason}.", {"motiv": info.reason}, uid=r.get("uid"))
        if info is not None:
            by_cnp[cnp_key(r.get("cnp"))].append(r)
        if not any(not blank(r.get(k)) for k in ("cnp", "bi_ci", "telefon", "adresa")):
            c.add("CON002", "concesionari", r["id"], "Fără CNP, serie CI, telefon și adresă.", {}, uid=r.get("uid"))
        if not blank(r.get("email")) and not valid_email(r.get("email")):
            c.add("CON009", "concesionari", r["id"], "E-mail cu format invalid.", {}, uid=r.get("uid"))
        if r["id"] not in with_contract:
            c.add("CON008", "concesionari", r["id"], "Nu apare în niciun contract sau cerere.", {}, uid=r.get("uid"))
    same_cnp_pairs = set()
    for grp in by_cnp.values():
        if len(grp) < 2:
            continue
        ids = [str(x["id"]) for x in grp][:20]
        names = {norm_name(x.get("nume"), x.get("prenume")) for x in grp}
        for x in grp:
            for y in grp:
                same_cnp_pairs.add((x["id"], y["id"]))
            if len(names) == 1:
                c.add("CON004", "concesionari", x["id"], f"CNP identic cu {len(grp) - 1} alte înregistrări, același nume (valoarea CNP omisă).", {"id_uri_grup": ids}, uid=x.get("uid"))
            else:
                c.add("CON007", "concesionari", x["id"], f"CNP identic cu {len(grp) - 1} alte înregistrări, dar nume diferite (valoarea CNP omisă).", {"id_uri_grup": ids, "nume_distincte": len(names)}, uid=x.get("uid"))
    for nm, grp in by_name.items():
        if len(grp) < 2:
            continue
        flagged = set()
        for i, x in enumerate(grp):
            for y in grp[i + 1:]:
                if (x["id"], y["id"]) in same_cnp_pairs:
                    continue
                kx, ky = cnp_key(x.get("cnp")), cnp_key(y.get("cnp"))
                if kx and ky and kx != ky:
                    continue
                cx, cy = _contact_key(x), _contact_key(y)
                if any(a and a == b for a, b in zip(cx, cy)):
                    flagged |= {x["id"], y["id"]}
        for r in grp:
            if r["id"] in flagged:
                c.add("CON006", "concesionari", r["id"], "Același nume și același telefon/e-mail/adresă cu alt concesionar, fără CNP diferit.", {"id_uri_grup": [str(z["id"]) for z in grp][:20]}, uid=r.get("uid"))
        c.add("CON005", "concesionari", grp[0]["id"], f"{len(grp)} concesionari cu același nume normalizat.", {"id_uri_grup": [str(z["id"]) for z in grp][:20]}, uid=grp[0].get("uid"))


def check_contracte(c: Ctx) -> None:
    if not c.need("contracte", "id"):
        return
    rows = c.q("SELECT * FROM contracte")
    conc = {r["id"]: r for r in c.q("SELECT * FROM concesionari")} if c.has("concesionari", "id") else {}
    locs = {r["id"]: r for r in c.q("SELECT id, cod_loc, parcela, rand, numar, uid FROM locuri_de_veci")} if c.has("locuri_de_veci", "id", "cod_loc") else {}
    loc_by_cod = {r["cod_loc"]: r for r in locs.values() if not blank(r.get("cod_loc"))}
    by_num, by_exact, by_loc = defaultdict(list), defaultdict(list), defaultdict(list)
    for r in rows:
        cid, lid, cod = r.get("concesionar_id"), r.get("id_loc"), txt(r.get("cod_loc"))
        ctx = {"uid": r.get("uid"), "cod_loc": cod or None}
        owner = txt(r.get("owner_fullname"))
        if cid is None:
            if owner:
                c.add("CTR003", "contracte", r["id"], "concesionar_id nul; există doar titularul în text.", {}, **ctx)
            else:
                c.add("CTR001", "contracte", r["id"], "Nici concesionar_id, nici titular în text.", {}, **ctx)
        elif c.has("concesionari", "id") and cid not in conc:
            c.add("CTR002", "contracte", r["id"], f"concesionar_id={cid} nu există.", {"concesionar_id": cid}, **ctx)
        if lid is None and not cod:
            c.add("CTR004", "contracte", r["id"], "Nici id_loc, nici cod_loc.", {}, **ctx)
        elif lid is not None and locs and lid not in locs:
            c.add("CTR005", "contracte", r["id"], f"id_loc={lid} nu există.", {"id_loc": lid}, **ctx)
        elif lid is not None and lid in locs and cod and txt(locs[lid].get("cod_loc")) != cod:
            c.add("CTR006", "contracte", r["id"], "cod_loc din contract diferă de cod_loc al locului legat prin id_loc.", {"id_loc": lid}, **ctx)
        elif lid is None and cod:
            c.add("CTR006", "contracte", r["id"], "id_loc nul deși cod_loc este completat" + (" și corespunde unui loc existent." if cod in loc_by_cod else " dar nu corespunde niciunui loc."),
                  {"cod_loc_exista": cod in loc_by_cod}, severity=None if cod in loc_by_cod else "critical", **ctx)
        num = txt(r.get("numar_contract"))
        if not num:
            c.add("CTR007", "contracte", r["id"], "numar_contract gol.", {}, **ctx)
        else:
            by_num[num.lower()].append(r)
        by_exact[(num.lower(), txt(r.get("data_contract")), lid, cid)].append(r) if num else None
        pi, ps = parse_date(r.get("data_inceput")), parse_date(r.get("data_sfarsit"))
        date_fmt(c, "contracte", r, ("data_contract", "data_inceput", "data_sfarsit"))
        if pi and ps and pi.ok and ps.ok and ps.value < pi.value:
            c.add("CTR010", "contracte", r["id"], "data_sfarsit este înaintea data_inceput.", {"data_inceput": pi.raw, "data_sfarsit": ps.raw}, **ctx)
        if lid is not None and pi and ps and pi.ok and ps.ok and cid is not None:
            by_loc[lid].append((pi.value, ps.value, cid, r))
        cc = conc.get(cid) if cid is not None else None
        if cc:
            toks = set(norm_name(cc.get("nume"), cc.get("prenume")))
            otoks = set(norm_name(r.get("owner_fullname"), r.get("owner_fullname_2")))
            if toks and otoks and not toks <= otoks:
                c.add("CTR012", "contracte", r["id"], f"Numele titularului din contract nu conține toate cuvintele numelui concesionarului (potriviri {len(toks & otoks)}/{len(toks)}).", {"potriviri": len(toks & otoks), "total": len(toks)}, **ctx)
            ko, kc = cnp_key(r.get("owner_cnp")), cnp_key(cc.get("cnp"))
            if ko and kc and ko != kc:
                c.add("CTR013", "contracte", r["id"], "owner_cnp diferă de CNP-ul concesionarului asociat (valorile omise).", {}, **ctx)
        lr = locs.get(lid) if lid is not None else None
        if lr:
            diffs = [k for k, ck in (("parcela", "cemetery_parcel"), ("rand", "cemetery_row"), ("numar", "cemetery_tomb")) if txt(r.get(ck)) and txt(lr.get(k)) and txt(r.get(ck)) != txt(lr.get(k))]
            if diffs:
                c.add("CTR014", "contracte", r["id"], f"Diferențe față de loc: {', '.join(diffs)}.", {"campuri": diffs}, **ctx)
    for grp in by_num.values():
        if len(grp) > 1:
            ids = [str(x["id"]) for x in grp][:20]
            for x in grp:
                c.add("CTR008", "contracte", x["id"], f"Același număr de contract la {len(grp)} contracte.", {"id_uri_grup": ids}, uid=x.get("uid"), cod_loc=x.get("cod_loc") or None)
    for grp in by_exact.values():
        if len(grp) > 1:
            ids = [str(x["id"]) for x in grp][:20]
            for x in grp:
                c.add("CTR009", "contracte", x["id"], "Contract duplicat (același număr, dată, loc, concesionar).", {"id_uri_grup": ids}, uid=x.get("uid"), cod_loc=x.get("cod_loc") or None)
    for lid, lst in by_loc.items():
        for i, (s1, e1, c1, r1) in enumerate(lst):
            for s2, e2, c2, r2 in lst[i + 1:]:
                if c1 != c2 and s1 <= e2 and s2 <= e1:
                    for r, other in ((r1, r2), (r2, r1)):
                        c.add("CTR011", "contracte", r["id"], f"Perioadă suprapusă cu contractul {other['id']} pentru același loc, alt concesionar.", {"alt_contract": other["id"]}, uid=r.get("uid"), cod_loc=r.get("cod_loc") or None)
    text_limits(c, "contracte", "CTR015", "contracte", CTR_TEXT_COLS, rows)
