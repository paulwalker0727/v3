"""cimitir_mobile_issue.py — Emitere documente din Mobile: contract (modelul DOCX din MAIN), chitanță, aviz de construcție, adeverințe + istoric.

* Contractul folosește EXACT modelul `backend/doc_templates/contract_template.docx` din MAIN și aceeași regulă de completare ca `desktop/contract_generator._build_mapping`
  (owner_* din contract, cu rezervă pe concesionar/loc). PDF-ul se randează pe server (reportlab, Liberation Sans = diacritice românești).
* Numerotare: chitanțele continuă seria/numerotarea EXISTENTĂ (prefix + lățime din cel mai mare număr existent; implicit CH-000001); avize AV-, contracte CT-, adeverințe AD-.
  Atribuirea e atomică (lock consultativ pe cimitir + contor) și un document se emite O SINGURĂ DATĂ per (tip, înregistrare) => fără chitanțe duplicate nici la retrimitere/offline.
  Numărul se scrie în rândul sincronizat (plati.chitanta_nr, amenajari_avize.nr_aviz, contracte.numar_contract, inhumati.nr_adeverinta_inhumare) => ajunge în MAIN prin Sync v2.
* Permisiuni (matricea din MAIN, impuse aici pe server): vizualizare/PDF/istoric = „Vede" pe categorie; emitere = „Adaugă" pe categorie
  (contract: contracte; chitanță: plati; aviz/adeverință: avize). Datele sensibile din contract (CNP/CI) apar doar cu acces `view_pii`.
* PDF-ul se regenerează determinist din `snapshot`; `pdf_sha256` se verifică la fiecare descărcare (integritate).
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cimitir_cloud_control as cc
import cimitir_mobile_perms as mp
import cimitir_sync_v2 as sv

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR, TEMPLATE = ROOT / "backend" / "fonts", ROOT / "backend" / "doc_templates" / "contract_template.docx"
KINDS: Dict[str, Dict[str, str]] = {
    "contract": {"label": "Contract de concesiune", "entity": "contracte", "cat": "contracte", "series": "CT"},
    "chitanta": {"label": "Chitanță", "entity": "plati", "cat": "plati", "series": "CH"},
    "aviz": {"label": "Aviz de construcție", "entity": "amenajari_avize", "cat": "avize", "series": "AV"},
    "adeverinta_inhumare": {"label": "Adeverință de înhumare", "entity": "inhumati", "cat": "avize", "series": "AD"},
    "adeverinta_concesiune": {"label": "Adeverință de concesiune", "entity": "contracte", "cat": "avize", "series": "AD"},
}
NUM_COLUMN = {"contract": ("contracte", "numar_contract"), "chitanta": ("plati", "chitanta_nr"), "aviz": ("amenajari_avize", "nr_aviz"),
              "adeverinta_inhumare": ("inhumati", "nr_adeverinta_inhumare")}
_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
_NUM = re.compile(r"^(\D*?)(\d+)(\D*)$")


def _t(v: Any) -> str:
    return "" if v is None else str(v)


class IssueService:
    def __init__(self, sync: sv.SyncV2Service):
        self.sync, self.cp = sync, sync.cp

    # ------------------------------------------------------------------ autorizare
    def _guard(self, c, a, kind: str, action: str) -> Dict[str, str]:
        if kind not in KINDS:
            raise ValueError("tip de document necunoscut")
        k = KINDS[kind]
        m, _ = sv.matrix_of(c, a)
        if not (mp.can(m, k["cat"], action) and mp.readable_entity(m, k["entity"])):
            if a["device_type"] != "MAIN":
                self.sync._audit_safe(c, a, "denied", k["entity"], None, {"reason": f"not_authorized:{k['cat']}_{action}", "issue": kind})
            raise cc.PermissionDenied("nu ai dreptul pentru această operațiune")
        return k

    # ------------------------------------------------------------------ date sursă
    def _row(self, c, a, entity: str, uid: str, lock: bool = False) -> Dict[str, Any]:
        r = self.sync._fetch(c, a, entity, str(uid)[:80], lock=lock)
        if r is None:
            raise cc.ScopeError("înregistrare inexistentă")
        d = dict(sv._wire(entity, r))
        return {"uid": d["uid"], "id": r["id"], **d["data"]}

    def _by_uid(self, c, a, entity: str, uid: Optional[str]) -> Dict[str, Any]:
        if not uid:
            return {}
        r = self.sync._fetch(c, a, entity, uid)
        return {"id": r["id"], **sv._wire(entity, r)["data"]} if r is not None else {}

    def _cemetery(self, c, a) -> Dict[str, str]:
        try:
            r = c.execute("SELECT name, locality, county FROM cemeteries WHERE uid=CAST(? AS uuid)", (a["cemetery_uid"],)).fetchone()
            return {"name": _t(r["name"]), "locality": _t(r["locality"]), "county": _t(r["county"])} if r else {"name": "Cimitir", "locality": "", "county": ""}
        except Exception:  # noqa: BLE001
            return {"name": "Cimitir", "locality": "", "county": ""}

    def _snapshot(self, c, a, kind: str, row: Dict[str, Any], number: str, fields: Dict[str, Any], pii: bool = True) -> Dict[str, Any]:
        """Instantaneu COMPLET (ca în registrul din cloud) la emitere; la afișare, datele sensibile se scot pentru cine nu are `view_pii` (vezi `pdf`/`preview`)."""
        cem = self._cemetery(c, a)
        snap: Dict[str, Any] = {"kind": kind, "number": number, "cemetery": cem, "fields": {k: _t(v)[:500] for k, v in fields.items()}, "issued_on": date.today().isoformat()}
        if kind in ("contract", "adeverinta_concesiune"):
            conc = self._by_uid(c, a, "concesionari", row.get("concesionar_uid"))
            loc = self._by_uid(c, a, "locuri_de_veci", row.get("loc_uid"))
            name = _t(row.get("owner_fullname")) or f"{_t(conc.get('nume'))} {_t(conc.get('prenume'))}".strip()
            sup = row.get("tomb_surface_m2") or loc.get("suprafata") or ""
            snap["map"] = {"contract_number": number, "contract_date": _t(row.get("data_contract")), "owner_fullname": name, "owner_fullname_2": name,
                           "owner_address": _t(row.get("owner_address")) or _t(conc.get("adresa")),
                           "owner_id_card": (_t(row.get("owner_id_card_serie_number")) or _t(conc.get("bi_ci"))) if pii else "",
                           "owner_cnp": (_t(row.get("owner_cnp")) or _t(conc.get("cnp"))) if pii else "",
                           "owner_phone": _t(row.get("owner_phone")) or _t(conc.get("telefon")), "owner_email": _t(row.get("owner_email")) or _t(conc.get("email")),
                           "cemetery_parcel": _t(row.get("cemetery_parcel")) or _t(loc.get("parcela")), "cemetery_row": _t(row.get("cemetery_row")) or _t(loc.get("rand")),
                           "cemetery_tomb": _t(row.get("cemetery_tomb")) or _t(loc.get("numar")), "tomb_surface_m2": f"{float(sup):.2f}" if isinstance(sup, (int, float)) and sup else _t(sup)}
            snap["grave_code"] = _t(row.get("cod_loc")) or _t(loc.get("cod_loc"))
            snap["data_inceput"], snap["data_sfarsit"] = _t(row.get("data_inceput")), _t(row.get("data_sfarsit"))
        elif kind == "chitanta":
            ct = self._by_uid(c, a, "contracte", row.get("contract_uid"))
            conc = self._by_uid(c, a, "concesionari", ct.get("concesionar_uid"))
            snap.update(payer=_t(ct.get("owner_fullname")) or f"{_t(conc.get('nume'))} {_t(conc.get('prenume'))}".strip(), amount=float(row.get("suma") or 0),
                        paid_on=_t(row.get("data_plata")), tip=_t(row.get("tip")) or "plată concesiune", grave_code=_t(row.get("cod_loc")),
                        contract_number=_t(ct.get("numar_contract")), observatii=_t(row.get("observatii")))
        elif kind == "aviz":
            snap.update(grave_code=_t(row.get("cod_loc")), aviz_date=_t(row.get("data_aviz")), lucrare=_t(row.get("lucrare")))
        else:   # adeverinta_inhumare
            loc = self._by_uid(c, a, "locuri_de_veci", row.get("loc_uid"))
            snap.update(person=f"{_t(row.get('nume'))} {_t(row.get('prenume'))}".strip(), death=_t(row.get("data_deces")), burial=_t(row.get("data_inhumare")),
                        grave_code=_t(loc.get("cod_loc")), sector=_t(loc.get("sector")), cert=_t(row.get("nr_certificat_deces")))
        if kind == "adeverinta_concesiune":
            snap["grave_code"] = snap.get("grave_code") or ""
        return snap

    # ------------------------------------------------------------------ PDF
    @staticmethod
    def _styles():
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        if "LibSans" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("LibSans", str(FONT_DIR / "LiberationSans-Regular.ttf")))
            pdfmetrics.registerFont(TTFont("LibSans-Bold", str(FONT_DIR / "LiberationSans-Bold.ttf")))
            pdfmetrics.registerFontFamily("LibSans", normal="LibSans", bold="LibSans-Bold", italic="LibSans", boldItalic="LibSans-Bold")
        return {"body": ParagraphStyle("b", fontName="LibSans", fontSize=10.5, leading=14.5, spaceAfter=5, alignment=4),
                "h": ParagraphStyle("h", fontName="LibSans-Bold", fontSize=12.5, leading=16, alignment=1, spaceBefore=8, spaceAfter=6),
                "c": ParagraphStyle("c", fontName="LibSans", fontSize=10.5, leading=14, alignment=1), "sm": ParagraphStyle("s", fontName="LibSans", fontSize=8.5, leading=11)}

    @staticmethod
    def _esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _contract_lines(self, mapping: Dict[str, str]) -> List[Tuple[str, str]]:
        import docx
        out: List[Tuple[str, str]] = []
        for p in docx.Document(str(TEMPLATE)).paragraphs:
            txt = _PLACEHOLDER.sub(lambda m: mapping.get(m.group(1), "") or "________", p.text).replace("\t", "    ").strip()
            if txt:
                out.append(("h" if (len(txt) < 60 and txt.upper() == txt and not any(ch.isdigit() for ch in txt[:3])) else "body", txt))
        return out

    def render(self, snap: Dict[str, Any]) -> bytes:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        st, kind, e = self._styles(), snap["kind"], self._esc
        cem = snap.get("cemetery") or {}
        head = f"{e(cem.get('name', ''))} {e(cem.get('locality', ''))}".strip()
        els: List[Any] = []
        if kind == "contract":
            for sty, txt in self._contract_lines(snap["map"]):
                els.append(Paragraph(e(txt), st[sty]))
        else:
            els += [Paragraph(f"<b>{head}</b>", st["c"]), Spacer(1, 10)]
            title = KINDS[kind]["label"].upper() + f" Nr. {e(snap['number'])}"
            els += [Paragraph(title, st["h"]), Paragraph(f"Data emiterii: {e(snap['issued_on'])}", st["c"]), Spacer(1, 14)]
            if kind == "chitanta":
                els.append(Paragraph(f"Am primit de la <b>{e(snap['payer'] or '________________')}</b> suma de <b>{snap['amount']:.2f} lei</b>, reprezentând <b>{e(snap['tip'])}</b> "
                                     f"pentru locul de veci cod <b>{e(snap['grave_code'] or '—')}</b>" + (f", contract nr. <b>{e(snap['contract_number'])}</b>" if snap["contract_number"] else "")
                                     + f". Data plății: {e(snap['paid_on'])}.", st["body"]))
                if snap["observatii"]:
                    els.append(Paragraph(f"Observații: {e(snap['observatii'])}", st["sm"]))
            elif kind == "aviz":
                els.append(Paragraph(f"Se avizează executarea lucrării: <b>{e(snap['lucrare'] or '—')}</b>, la locul de veci cod <b>{e(snap['grave_code'] or '—')}</b>, "
                                     f"conform avizului din {e(snap['aviz_date'] or snap['issued_on'])}. Lucrarea se execută cu respectarea Regulamentului cimitirelor și a limitelor locului concesionat.", st["body"]))
            elif kind == "adeverinta_inhumare":
                els.append(Paragraph(f"Se adeverește că <b>{e(snap['person'])}</b>, decedat(ă) la data de {e(snap['death'] or '—')}, a fost înhumat(ă) la data de {e(snap['burial'] or '—')} "
                                     f"în locul de veci cod <b>{e(snap['grave_code'] or '—')}</b>" + (f", sector {e(snap['sector'])}" if snap["sector"] else "")
                                     + (f", pe baza certificatului de deces nr. {e(snap['cert'])}" if snap["cert"] else "") + ".", st["body"]))
            else:
                m = snap["map"]
                els.append(Paragraph(f"Se adeverește că <b>{e(m['owner_fullname'] or '—')}</b> este concesionarul locului de veci cod <b>{e(snap['grave_code'] or '—')}</b> "
                                     f"(parcela {e(m['cemetery_parcel'])}, rândul {e(m['cemetery_row'])}, nr. {e(m['cemetery_tomb'])}), conform contractului nr. {e(m['contract_number'] or '—')} "
                                     f"din {e(m['contract_date'] or '—')}.", st["body"]))
            for k, v in (snap.get("fields") or {}).items():
                els.append(Paragraph(f"<b>{e(k)}:</b> {e(v)}", st["body"]))
            els.append(Spacer(1, 30))
            sig = Table([[Paragraph("<b>Preot / Administrator</b>", st["c"]), Paragraph("<b>Beneficiar</b>", st["c"])], ["_____________________", "_____________________"]], colWidths=[85 * mm, 85 * mm])
            sig.setStyle(TableStyle([("TOPPADDING", (0, 1), (-1, 1), 22)]))
            els.append(sig)
        buf = io.BytesIO()
        SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm, title=f"{KINDS[kind]['label']} {snap['number']}",
                          author="Cimitir", invariant=1).build(els)
        return buf.getvalue()

    # ------------------------------------------------------------------ numerotare
    def _next_number(self, c, a, kind: str) -> str:
        series = KINDS[kind]["series"]
        sc = (a["organization_uid"], a["cemetery_uid"])
        c.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (f"issue:{a['cemetery_uid']}:{series}",))
        prefix, width, suffix, top = f"{series}-", 6, "", 0
        if kind in NUM_COLUMN:
            table, col = NUM_COLUMN[kind]
            for r in c.execute(f"SELECT {col} AS n FROM {table} WHERE organization_uid=CAST(? AS uuid) AND cemetery_uid=CAST(? AS uuid) AND COALESCE({col},'')<>''", sc).fetchall():
                m = _NUM.match(_t(r["n"]).strip())
                if m and int(m.group(2)) >= top and (kind != "chitanta" or True):
                    prefix, suffix, width, top = m.group(1), m.group(3), len(m.group(2)), int(m.group(2))
        row = c.execute("SELECT last_no FROM sync_v2_counters WHERE cemetery_uid=CAST(? AS uuid) AND series=?", (a["cemetery_uid"], series)).fetchone()
        n = max(top, int(row["last_no"]) if row else 0) + 1
        taken = lambda x: c.execute("SELECT 1 FROM sync_v2_issued WHERE cemetery_uid=CAST(? AS uuid) AND number=?", (a["cemetery_uid"], x)).fetchone() is not None  # noqa: E731
        number = f"{prefix}{n:0{width}d}{suffix}"
        while taken(number):
            n += 1
            number = f"{prefix}{n:0{width}d}{suffix}"
        c.execute("INSERT INTO sync_v2_counters (organization_uid,cemetery_uid,series,last_no) VALUES (CAST(? AS uuid),CAST(? AS uuid),?,?) "
                  "ON CONFLICT (cemetery_uid, series) DO UPDATE SET last_no=GREATEST(sync_v2_counters.last_no, EXCLUDED.last_no)", (*sc, series, n))
        return number

    # ------------------------------------------------------------------ API
    def templates(self, key: str) -> Dict[str, Any]:
        a = self.sync._auth(key)
        c = self.cp._c()
        try:
            m, _ = sv.matrix_of(c, a)
            return {"kinds": {k: {"label": v["label"], "entity": v["entity"], "can_view": mp.can(m, v["cat"], "view"), "can_issue": mp.can(m, v["cat"], "add")} for k, v in KINDS.items()},
                    "contract_template": TEMPLATE.name}
        finally:
            c.close()

    def suggest(self, key: str, kind: str) -> Dict[str, Any]:
        """Următorul număr din serie, FĂRĂ rezervare (pentru formulare: ex. nr. aviz obligatoriu la creare). Rezervarea reală se face la emitere/salvare."""
        a = self.sync._auth(key)
        c = self.cp._c()
        try:
            self._guard(c, a, kind, "add")
            c.execute("BEGIN")
            try:
                return {"kind": kind, "next": self._next_number(c, a, kind)}
            finally:
                c.execute("ROLLBACK")
        finally:
            c.close()

    def preview(self, key: str, kind: str, entity_uid: str) -> bytes:
        a = self.sync._auth(key)
        c = self.cp._c()
        try:
            k = self._guard(c, a, kind, "view")
            row = self._row(c, a, k["entity"], entity_uid)
            col = NUM_COLUMN.get(kind)
            number = _t(row.get(col[1])) if col else ""
            return self.render(self._snapshot(c, a, kind, row, number or "(neemis)", {}, pii=sv.pii_allowed(c, a)))
        finally:
            c.close()

    def issue(self, key: str, kind: str, entity_uid: str, fields: Any = None) -> Dict[str, Any]:
        a = self.sync._auth(key)
        fields = {str(k)[:60]: v for k, v in (fields or {}).items()} if isinstance(fields, dict) and len(fields) <= 12 else {}
        c = self.cp._c()
        try:
            k = self._guard(c, a, kind, "add")
            sc = (a["organization_uid"], a["cemetery_uid"])
            c.execute("BEGIN")
            try:
                prev = c.execute("SELECT uid, number, pdf_sha256, created_at FROM sync_v2_issued WHERE cemetery_uid=CAST(? AS uuid) AND kind=? AND entity=? AND entity_uid=?",
                                 (a["cemetery_uid"], kind, k["entity"], str(entity_uid)[:80])).fetchone()
                if prev is not None:
                    c.execute("COMMIT")
                    return {"uid": prev["uid"], "number": prev["number"], "kind": kind, "duplicate": True, "created_at": sv._jsonable(prev["created_at"])}
                row = self._row(c, a, k["entity"], entity_uid, lock=True)
                col = NUM_COLUMN.get(kind)
                number = _t(row.get(col[1])).strip() if col else ""
                if not number:
                    number = self._next_number(c, a, kind)
                    if col:      # numărul se scrie în rândul sincronizat (versiunea crește => ajunge în MAIN)
                        c.execute(f"UPDATE {col[0]} SET {col[1]}=? WHERE uid=? AND organization_uid=CAST(? AS uuid) AND cemetery_uid=CAST(? AS uuid)", (number, row["uid"], *sc))
                elif kind == "chitanta" or kind == "aviz":
                    clash = c.execute("SELECT 1 FROM sync_v2_issued WHERE cemetery_uid=CAST(? AS uuid) AND kind=? AND number=?", (a["cemetery_uid"], kind, number)).fetchone()
                    if clash is not None:
                        raise ValueError("număr deja emis pentru alt document")
                snap = self._snapshot(c, a, kind, row, number, fields)
                pdf = self.render(snap)
                uid = uuid.uuid4().hex
                c.execute("INSERT INTO sync_v2_issued (organization_uid,cemetery_uid,uid,kind,entity,entity_uid,number,issued_by_binding,device_type,snapshot,pdf_sha256) "
                          "VALUES (CAST(? AS uuid),CAST(? AS uuid),?,?,?,?,?,CAST(? AS uuid),?,CAST(? AS jsonb),?)",
                          (*sc, uid, kind, k["entity"], row["uid"], number, a["binding_uid"], a["device_type"], json.dumps(snap, default=str), hashlib.sha256(pdf).hexdigest()))
                self.sync._audit(c, a, "issued", k["entity"], row["uid"], {"kind": kind, "number": number, "doc": uid})
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
            return {"uid": uid, "number": number, "kind": kind, "duplicate": False}
        except Exception as exc:  # noqa: BLE001
            if "duplicate key" in str(exc) or "UNIQUE" in str(exc):
                raise ValueError("documentul a fost deja emis") from None
            raise
        finally:
            c.close()

    def list(self, key: str, entity_uid: str = "", kind: str = "", limit: int = 100) -> Dict[str, Any]:
        a = self.sync._auth(key)
        c = self.cp._c()
        try:
            m, _ = sv.matrix_of(c, a)
            allowed = [k for k, v in KINDS.items() if mp.can(m, v["cat"], "view") and mp.readable_entity(m, v["entity"]) and (not kind or k == kind)]
            if not allowed:
                return {"issued": []}
            sql = ("SELECT i.uid, i.kind, i.entity, i.entity_uid, i.number, i.device_type, i.created_at, i.pdf_sha256 FROM sync_v2_issued i WHERE i.organization_uid=CAST(? AS uuid) "
                   f"AND i.cemetery_uid=CAST(? AS uuid) AND i.kind IN ({','.join('?' for _ in allowed)})")
            params: List[Any] = [a["organization_uid"], a["cemetery_uid"], *allowed]
            if entity_uid:
                sql += " AND i.entity_uid=?"
                params.append(entity_uid[:80])
            rows = c.execute(sql + " ORDER BY i.created_at DESC LIMIT ?", (*params, max(1, min(int(limit), 500)))).fetchall()
            return {"issued": [{**{x: sv._jsonable(r[x]) for x in r.keys()}, "label": KINDS[r["kind"]]["label"]} for r in rows]}
        finally:
            c.close()

    def pdf(self, key: str, uid: str) -> Tuple[bytes, str]:
        a = self.sync._auth(key)
        c = self.cp._c()
        try:
            r = c.execute("SELECT kind, number, snapshot, pdf_sha256 FROM sync_v2_issued WHERE cemetery_uid=CAST(? AS uuid) AND organization_uid=CAST(? AS uuid) AND uid=?",
                          (a["cemetery_uid"], a["organization_uid"], str(uid)[:64])).fetchone()
            if r is None:
                raise cc.ScopeError("document inexistent")
            self._guard(c, a, r["kind"], "view")
            snap = r["snapshot"] if isinstance(r["snapshot"], dict) else json.loads(r["snapshot"])
            if not sv.pii_allowed(c, a) and "map" in snap:
                snap["map"] = {**snap["map"], "owner_cnp": "", "owner_id_card": ""}
            data = self.render(snap)
            if sv.pii_allowed(c, a) and hashlib.sha256(data).hexdigest() != r["pdf_sha256"]:
                raise cc.ScopeError("integritate document: amprentă diferită")
            return data, f"{r['kind']}-{re.sub(r'[^A-Za-z0-9_-]', '_', r['number'])}.pdf"
        finally:
            c.close()
