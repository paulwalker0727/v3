"""cimitir_mobile_perms.py — Permisiuni Mobile pe categorii × acțiuni (+ câmpuri editabile + profiluri). Stocate în `sync_v2_permissions` (mecanismul existent).

Categorii (8): locuri, harta, concesionari, inhumati, contracte, plati, avize, fisiere. Acțiuni: view, add, edit, delete.
  view   = categoria apare pe telefon și se poate citi prin API (fără view: invizibilă, 403 la acces direct).
  add    = creare;  edit = modificare (opțional doar pe o listă de câmpuri);  delete = ștergere/anulare (autorizată separat și auditată).
Stocare (tabela 022 `sync_v2_permissions`, per binding): `mode:matrix`, `perm:<cat>:<act>`, `fields:<cat>` (+ `detail` JSONB = lista de câmpuri editabile),
  `profile:<cheie>` (marker), `cap:view_pii` (acces excepțional la date sensibile). Fără `mode:matrix` rămâne comportamentul vechi (capabilități/roluri din 022).
Toate verificările se fac pe SERVER (la pull, row, search, push — inclusiv pentru operațiile offline trimise ulterior — și la documente).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import cimitir_entities as ents

CATEGORIES: Dict[str, str] = {
    "locuri": "Locuri de veci", "harta": "Hartă și poligoane", "concesionari": "Concesionari", "inhumati": "Persoane înhumate",
    "contracte": "Contracte", "plati": "Plăți și chitanțe", "avize": "Avize și adeverințe", "fisiere": "Fotografii și fișiere"}
ACTIONS = ("view", "add", "edit", "delete")
ACTION_LABELS = {"view": "Vede", "add": "Adaugă", "edit": "Editează", "delete": "Șterge / Anulează"}
CAT_ENTITY: Dict[str, str] = {"locuri": "locuri_de_veci", "harta": "locuri_de_veci", "concesionari": "concesionari", "inhumati": "inhumati",
                              "contracte": "contracte", "plati": "plati", "avize": "amenajari_avize"}
ENTITY_CAT: Dict[str, str] = {"locuri_de_veci": "locuri", "concesionari": "concesionari", "inhumati": "inhumati", "contracte": "contracte",
                              "plati": "plati", "amenajari_avize": "avize"}
POLY_COLS = ("points", "color")           # câmpurile categoriei „harta" (restul coloanelor locului aparțin categoriei „locuri")
MODE_KEY, PERM_PREFIX, FIELDS_PREFIX, PROFILE_PREFIX = "mode:matrix", "perm:", "fields:", "profile:"


def category_fields(cat: str) -> List[str]:
    """Câmpurile pe care se pot configura restricții de editare pentru o categorie (inclusiv legăturile către părinți)."""
    if cat == "harta":
        return list(POLY_COLS)
    if cat == "fisiere":
        return ["caption"]
    ent = CAT_ENTITY[cat]
    cols = [c for c in ents.ENTITIES[ent]["cols"] if not (cat == "locuri" and c in POLY_COLS)]
    return cols + ents.fk_wire_names(ent)


def _cell(view=False, add=False, edit=False, delete=False, fields: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"view": view, "add": add, "edit": edit, "delete": delete, "fields": fields}


def _all(v: bool = True, **kw) -> Dict[str, Dict[str, Any]]:
    return {c: _cell(v, v, v, v) for c in CATEGORIES} | kw


# Profiluri predefinite (personalizabile din MAIN, salvate în sync_v2_profiles). Nimic implicit nu permite ștergerea, în afară de Administrator.
PROFILES: Dict[str, Dict[str, Any]] = {
    "administrator": {"label": "Administrator", "matrix": _all(True)},
    "secretar": {"label": "Secretar", "matrix": {
        "locuri": _cell(True, True, True), "harta": _cell(True, False, False), "concesionari": _cell(True, True, True), "inhumati": _cell(True, True, True),
        "contracte": _cell(True, True, True), "plati": _cell(True, True, False), "avize": _cell(True, True, True), "fisiere": _cell(True, True, True)}},
    "personal_cimitir": {"label": "Personal cimitir", "matrix": {
        "locuri": _cell(True, False, True, fields=["observatii", "stare", "disponibilitate", "ocupat"]), "harta": _cell(True), "concesionari": _cell(False),
        "inhumati": _cell(True, True, False), "contracte": _cell(False), "plati": _cell(False), "avize": _cell(True), "fisiere": _cell(True, True, False)}},
    "doar_consultare": {"label": "Doar consultare", "matrix": {
        "locuri": _cell(True), "harta": _cell(True), "concesionari": _cell(True), "inhumati": _cell(True), "contracte": _cell(True),
        "plati": _cell(False), "avize": _cell(True), "fisiere": _cell(True)}},
}


def normalize(raw: Any) -> Dict[str, Dict[str, Any]]:
    """Validează și normalizează o matrice: acțiunile add/edit/delete implică view; câmpurile editabile trebuie să existe. Necunoscutele -> eroare."""
    if not isinstance(raw, dict):
        raise ValueError("matrice invalidă")
    if any(k not in CATEGORIES for k in raw):
        raise ValueError("categorie necunoscută")
    out: Dict[str, Dict[str, Any]] = {}
    for cat in CATEGORIES:
        cell = raw.get(cat) or {}
        if not isinstance(cell, dict) or any(k not in (*ACTIONS, "fields") for k in cell):
            raise ValueError("celulă invalidă")
        flags = {a: bool(cell.get(a)) for a in ACTIONS}
        if not flags["view"]:
            flags = {a: False for a in ACTIONS}
        fields = cell.get("fields")
        if fields is not None:
            if not isinstance(fields, list) or any(f not in category_fields(cat) for f in fields):
                raise ValueError(f"câmpuri invalide pentru {cat}")
            fields = [f for f in category_fields(cat) if f in fields]
            if len(fields) == len(category_fields(cat)):
                fields = None
        out[cat] = {**flags, "fields": fields}
    return out


def full_matrix() -> Dict[str, Dict[str, Any]]:
    return normalize(_all(True))


def load_profiles(c, a) -> Dict[str, Dict[str, Any]]:
    """Profilurile predefinite + suprascrierile salvate pentru cimitir."""
    out = {k: {"label": v["label"], "matrix": normalize(v["matrix"]), "custom": False} for k, v in PROFILES.items()}
    try:
        for r in c.execute("SELECT key, label, matrix FROM sync_v2_profiles WHERE cemetery_uid=CAST(? AS uuid)", (a["cemetery_uid"],)).fetchall():
            m = r["matrix"] if isinstance(r["matrix"], dict) else json.loads(r["matrix"])
            out[r["key"]] = {"label": r["label"], "matrix": normalize(m), "custom": True}
    except Exception as exc:  # noqa: BLE001
        if "sync_v2_profiles" not in str(exc):
            raise
    return out


def store_matrix(c, scope: tuple, ref: str, matrix: Dict[str, Dict[str, Any]], profile: Optional[str], view_pii: bool) -> None:
    rows: List[tuple] = [(MODE_KEY, True, None), ("cap:view_pii", bool(view_pii), None)]
    for cat in CATEGORIES:
        for act in ACTIONS:
            rows.append((f"{PERM_PREFIX}{cat}:{act}", matrix[cat][act], None))
        f = matrix[cat]["fields"]
        rows.append((f"{FIELDS_PREFIX}{cat}", f is not None, json.dumps(f) if f is not None else None))
    c.execute("UPDATE sync_v2_permissions SET can_edit=false, updated_at=now() WHERE binding_uid=CAST(? AS uuid) AND entity LIKE 'profile:%' AND can_edit", (ref,))
    rows.append((f"{PROFILE_PREFIX}{profile or 'personalizat'}", True, None))
    for ent, flag, detail in rows:
        c.execute("INSERT INTO sync_v2_permissions (organization_uid,cemetery_uid,binding_uid,entity,can_edit,detail) VALUES (CAST(? AS uuid),CAST(? AS uuid),CAST(? AS uuid),?,?,CAST(? AS jsonb)) "
                  "ON CONFLICT (binding_uid, entity) DO UPDATE SET can_edit=EXCLUDED.can_edit, detail=EXCLUDED.detail, updated_at=now()", (*scope, ref, ent, flag, detail))


def mark_legacy(c, scope: tuple, ref: str) -> None:
    """Apelurile vechi (rol/registre) redevin autoritative: matricea se dezactivează (fără ștergere de date)."""
    c.execute("INSERT INTO sync_v2_permissions (organization_uid,cemetery_uid,binding_uid,entity,can_edit) VALUES (CAST(? AS uuid),CAST(? AS uuid),CAST(? AS uuid),?,false) "
              "ON CONFLICT (binding_uid, entity) DO UPDATE SET can_edit=false, updated_at=now()", (*scope, ref, MODE_KEY))


def device_matrix(c, binding_uid: str) -> Optional[Dict[str, Any]]:
    """Matricea unui telefon sau None dacă nu e în mod matrice (migrarea 027 neaplicată sau încă neconfigurat)."""
    try:
        rows = c.execute("SELECT entity, can_edit, detail FROM sync_v2_permissions WHERE binding_uid=CAST(? AS uuid) AND (entity LIKE 'perm:%' OR entity LIKE 'fields:%' "
                         "OR entity LIKE 'profile:%' OR entity='mode:matrix' OR entity='cap:view_pii')", (binding_uid,)).fetchall()
    except Exception as exc:  # noqa: BLE001
        if "detail" in str(exc) or "sync_v2_permissions" in str(exc):
            return None
        raise
    flags = {r["entity"]: bool(r["can_edit"]) for r in rows}
    if not flags.get(MODE_KEY):
        return None
    m = {cat: {act: flags.get(f"{PERM_PREFIX}{cat}:{act}", False) for act in ACTIONS} | {"fields": None} for cat in CATEGORIES}
    for r in rows:
        if r["entity"].startswith(FIELDS_PREFIX) and r["can_edit"]:
            cat = r["entity"][len(FIELDS_PREFIX):]
            d = r["detail"]
            if cat in m:
                m[cat]["fields"] = d if isinstance(d, list) else json.loads(d or "[]")
    profile = next((k[len(PROFILE_PREFIX):] for k, v in flags.items() if k.startswith(PROFILE_PREFIX) and v), None)
    return {"matrix": normalize(m), "profile": profile, "view_pii": flags.get("cap:view_pii", False)}


def legacy_matrix(caps: Optional[set], editable: set) -> Dict[str, Dict[str, Any]]:
    """Echivalentul matricei pentru telefoanele cu rol/registre din 022 (None = 022 neaplicată: citire totală, fără scriere)."""
    import cimitir_sync_v2 as sv      # import tardiv: evită ciclul de import
    m: Dict[str, Dict[str, Any]] = {}
    for cat in CATEGORIES:
        if cat == "fisiere":
            can = caps is not None and "upload_documents" in caps
            view = caps is None or any(k in caps for k in ("view_map", "read_registers", "read_finance"))
            m[cat] = _cell(view, view and can, False, False)
        elif cat == "harta":
            view = caps is None or "view_map" in caps
            w = "locuri_de_veci" in editable
            m[cat] = _cell(view, view and w, view and w, False)
        else:
            ent = CAT_ENTITY[cat]
            view = caps is None or any(k in caps for k in sv.READ_CAPS[ent])
            w = ent in editable
            m[cat] = _cell(view, view and w, view and w, False)
    return normalize(m)


def can(matrix: Dict[str, Dict[str, Any]], cat: str, action: str) -> bool:
    return bool(matrix.get(cat, {}).get(action))


def readable_entity(matrix: Dict[str, Dict[str, Any]], entity: str) -> bool:
    if entity == "locuri_de_veci":
        return can(matrix, "locuri", "view") or can(matrix, "harta", "view")
    return can(matrix, ENTITY_CAT[entity], "view")


def hidden_columns(matrix: Dict[str, Dict[str, Any]], entity: str) -> set:
    """Coloanele ascunse telefonului: poligonul fără „Vede hartă"; la locuri, fără „Vede locuri" rămân doar cod + poligon."""
    if entity != "locuri_de_veci":
        return set()
    hid: set = set()
    if not can(matrix, "harta", "view"):
        hid |= set(POLY_COLS)
    if not can(matrix, "locuri", "view"):
        hid |= {c for c in ents.ENTITIES[entity]["cols"] if c not in POLY_COLS and c != "cod_loc"}
    return hid


def authorize_write(matrix: Dict[str, Dict[str, Any]], entity: str, action: str, server_data: Optional[Dict[str, Any]], data: Dict[str, Any]) -> Optional[str]:
    """None = permis; altfel motivul refuzului. `action`: insert | update | delete. Compară cu starea din server pentru a găsi câmpurile modificate."""
    cat = ENTITY_CAT[entity]
    if action == "delete":
        return None if can(matrix, cat, "delete") else "not_authorized:delete"
    if action == "insert":
        if not can(matrix, cat, "add"):
            return "not_authorized:add"
        if entity == "locuri_de_veci" and (data.get("points") or []) and not can(matrix, "harta", "add"):
            return "not_authorized:harta_add"
        return None
    changed = ents.changed_fields(entity, data, server_data or {})
    if not changed:
        return None
    for col in changed:
        owner = "harta" if entity == "locuri_de_veci" and col in POLY_COLS else cat
        act = "edit"
        if owner == "harta" and not (data.get("points") or []) and (server_data or {}).get("points"):
            act = "delete"        # golirea poligonului = ștergerea lui
        if not can(matrix, owner, act):
            return f"not_authorized:{owner}_{act}"
        allowed = matrix[owner]["fields"]
        if allowed is not None and col not in allowed:
            return f"field_not_allowed:{col}"
    return None
