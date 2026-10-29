"""cloud_center.py — logica Centrului de configurare Cloud (fără UI): stare generală, disponibilitatea funcțiilor, inițializarea unică a administratorului Cloud.

Nu modifică licența, autentificarea locală, sincronizarea sau datele MAIN: citește starea și deleagă operațiunile modulelor existente.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import requests

import cloud_simple as simple
import database as db
from cloud_supabase import SET_LAST_CHECK

ST_OK, ST_WARN, ST_ERROR, ST_NONE = "ok", "warn", "error", "none"
COLORS = {ST_OK: "#15803d", ST_WARN: "#a16207", ST_ERROR: "#b91c1c", ST_NONE: "#64748b"}
TIMEOUT = 15


@dataclass
class Item:
    key: str
    label: str
    state: str
    text: str


# ------------------------------------------------------------------ A. stare generală
def _license_item() -> Item:
    try:
        from license import validator
        r = validator.verifica_licenta_curenta()          # doar citire: licența nu se modifică
    except Exception:  # noqa: BLE001
        return Item("license", "Licență", ST_ERROR, "Modulul de licențiere nu poate fi citit.")
    if not r.ok:
        return Item("license", "Licență", ST_ERROR, str(r.motiv))
    return Item("license", "Licență", ST_OK, f"Validă — {r.client or 'client'}, expiră: {r.expira or 'permanent'}")


def _local_db_item() -> Item:
    try:
        conn = sqlite3.connect(f"file:{Path(db.current_db_path()).resolve().as_posix()}?mode=ro", uri=True)
        try:
            ok = conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return Item("local_db", "Baza locală", ST_ERROR, "Baza de date locală nu poate fi citită.")
    return Item("local_db", "Baza locală", ST_OK if ok else ST_ERROR, "Accesibilă, integritate verificată" if ok else "Verificarea de integritate a eșuat")


def _supabase_item() -> Item:
    prof = simple.current_profile()
    if prof is None or not prof.project_ref:
        return Item("supabase", "Supabase", ST_NONE, "Neconfigurat (fila „Supabase”)")
    last: Dict[str, Any] = {}
    try:
        last = json.loads(db.get_setting(SET_LAST_CHECK) or "{}")
    except ValueError:
        pass
    if not last or last.get("ref") != prof.project_ref:
        return Item("supabase", "Supabase", ST_WARN, f"Proiect {prof.project_ref} configurat, încă neverificat")
    if not last.get("ok"):
        return Item("supabase", "Supabase", ST_ERROR, "Ultima verificare a eșuat")
    if last.get("pending"):
        return Item("supabase", "Supabase", ST_WARN, f"Verificat; lipsesc {len(last['pending'])} migrări")
    return Item("supabase", "Supabase", ST_OK, f"Proiect {prof.project_ref} verificat, schema la zi")


def remember_supabase_check(ref: str, summary: Dict[str, Any]) -> None:
    db.set_setting(SET_LAST_CHECK, json.dumps({"ref": ref, "ok": bool(summary.get("ok")), "pending": summary.get("pending", []),
                                               "bucket": summary.get("bucket", ""), "at": int(time.time())}))


def backend_health(base_url: str, http: Any = None) -> Dict[str, Any]:
    """GET /api/v1/health (read-only). {'reachable': bool, 'ready': bool, 'detail': str}"""
    import cloud_sync
    err = cloud_sync.validate_url(base_url)
    if err:
        return {"reachable": False, "ready": False, "detail": err}
    try:
        r = (http or requests).get(base_url.rstrip("/") + "/api/v1/health", timeout=TIMEOUT)
    except requests.RequestException as exc:
        return {"reachable": False, "ready": False, "detail": f"serverul nu răspunde ({type(exc).__name__})"}
    if r.status_code != 200:
        return {"reachable": True, "ready": False, "detail": f"/api/v1/health a răspuns {r.status_code}"}
    try:
        ready = bool(r.json().get("ok"))
    except ValueError:
        return {"reachable": True, "ready": False, "detail": "răspuns invalid"}
    return {"reachable": True, "ready": ready, "detail": "gata" if ready else "backendul nu este ready"}


def general_status(check_backend: bool = False, http: Any = None) -> List[Item]:
    """Cele 6 stări din secțiunea A. `check_backend` face o cerere de rețea (altfel „neverificat”)."""
    items = [_license_item(), _local_db_item(), _supabase_item()]
    prof = simple.current_profile()
    if prof is None or not prof.api_base_url:
        items.append(Item("backend", "Backend Cloud", ST_NONE, "Neconfigurat (fila „Backend Cloud”)"))
    elif not check_backend:
        items.append(Item("backend", "Backend Cloud", ST_WARN, f"{prof.api_base_url} — neverificat"))
    else:
        h = backend_health(prof.api_base_url, http)
        items.append(Item("backend", "Backend Cloud", ST_OK if h["ready"] else ST_ERROR, f"{prof.api_base_url} — {h['detail']}"))
    pair = simple.pairing_state()["pairing"]
    items.append(Item("pairing", "Asociere MAIN", {simple.PAIR_OK: ST_OK, simple.PAIR_UNVERIFIED: ST_WARN, simple.PAIR_INCOMPLETE: ST_WARN}.get(pair, ST_NONE),
                      simple._PAIR_TEXT[pair]))  # noqa: SLF001
    try:
        ind = simple.sync_indicator()
        s = {simple.IND_CONNECTED: ST_OK, simple.IND_SYNCING: ST_OK, simple.IND_OFFLINE: ST_WARN, simple.IND_ERROR: ST_ERROR}.get(ind["state"], ST_NONE)
        items.append(Item("sync", "Sincronizare Mobile", s, ind["text"] + (f" — {ind['detail']}" if ind["detail"] else "")))
    except Exception:  # noqa: BLE001
        items.append(Item("sync", "Sincronizare Mobile", ST_NONE, "Indisponibilă"))
    return items


# ------------------------------------------------------------------ funcții indisponibile (marcate explicit)
UNAVAILABLE: Dict[str, Dict[str, str]] = {
    "backend_provisioning": {
        "stage": "ETAPA 3", "title": "Provisionare automată a backendului găzduit",
        "reason": ("Necesită infrastructură care NU există în acest proiect: un serviciu de provisionare autentificat (API autorizat de dezvoltator), "
                   "un orchestrator de containere/instanțe izolate per parohie, DNS + TLS per instanță, un secret manager per organizație și "
                   "livrarea tokenului de inițializare direct în instanță. Nu se simulează un deploy reușit."),
    },
    "bootstrap_token_delivery": {
        "stage": "ETAPA 3", "title": "Livrarea automată a tokenului tehnic de inițializare",
        "reason": ("Tokenul se generează pe serverul backend (variabilă de mediu cu expirare). Livrarea lui automată spre MAIN cere canalul autorizat al "
                   "serviciului de provisionare. Până atunci tokenul este introdus într-un câmp mascat, folosit o singură dată și nu se salvează."),
    },
    "portable_backup": {
        "stage": "ETAPA 4", "title": "Export/import portabil al evidenței cimitirului (validare, previzualizare, anti-duplicare)",
        "reason": "Nu este implementat în această livrare. Rămân disponibile copiile de rezervă locale existente (SQLite + media).",
    },
    "initial_sync": {
        "stage": "ETAPA 4", "title": "Prima sincronizare controlată și reluabilă după restaurare",
        "reason": "Nu este implementată în această livrare; sincronizarea existentă (Sync v2) rămâne neschimbată.",
    },
}


# ------------------------------------------------------------------ D. inițializarea unică a administratorului Cloud
@dataclass
class BackendAdminState:
    reachable: bool
    initialized: bool = False
    bootstrap_open: bool = False
    detail: str = ""

    @property
    def can_initialize(self) -> bool:
        return self.reachable and not self.initialized and self.bootstrap_open


def admin_state(base_url: str, client_factory: Optional[Callable[[str], Any]] = None) -> BackendAdminState:
    """Starea inițializării Cloud (GET /api/v1/admin/status, fără autentificare, fără secrete)."""
    import cimitir_cloud_admin_client as adc
    cl = (client_factory or adc.AdminClient)(base_url)
    try:
        st = cl.status()
    except adc.AdminApiError as exc:
        return BackendAdminState(False, detail=str(exc))
    finally:
        cl.close()
    initialized, opened = bool(st.get("initialized")), bool(st.get("bootstrap_open"))
    detail = ("Administratorul Cloud există deja; inițializarea nu mai este permisă." if initialized
              else "Inițializarea este deschisă." if opened
              else "Inițializarea nu este activă pe server (tokenul tehnic lipsește, este slab sau a expirat).")
    return BackendAdminState(True, initialized, opened, detail)


def local_identity() -> Dict[str, str]:
    """UID-urile și denumirile organizației/cimitirului din registrul local (MAIN); gol dacă nu există registru activ."""
    try:
        import cimitir_registry as reg
        b = reg.current_binding()
        if b is None:
            return {}
        cat = reg.RegistryCatalog()
        org = cat.get_organization(b.organization_uid) or {}
        cem = cat.get_cemetery(b.cemetery_uid) or {}
        return {"organization_uid": b.organization_uid, "organization_name": org.get("display_name", ""),
                "cemetery_uid": b.cemetery_uid, "cemetery_name": cem.get("name", "")}
    except Exception:  # noqa: BLE001
        return {}


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_admin_form(email: str, password: str, password2: str, token: str) -> List[str]:
    errs: List[str] = []
    if not _EMAIL.match((email or "").strip()):
        errs.append("Adresa de email nu este validă.")
    if len(password or "") < 12:
        errs.append("Parola trebuie să aibă cel puțin 12 caractere.")
    elif len(set(password)) < 5 or password.lower() == (email or "").lower():
        errs.append("Parola este prea slabă.")
    if password != password2:
        errs.append("Cele două parole nu coincid.")
    if not (token or "").strip():
        errs.append("Tokenul tehnic de inițializare este obligatoriu.")
    return errs


def initialize_cloud_admin(base_url: str, token: str, email: str, password: str, display_name: str = "",
                           client_factory: Optional[Callable[[str], Any]] = None, license_ok: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    """Operațiune UNICĂ: creează primul administrator Cloud. Refuză dacă licența nu e validă sau dacă Cloud-ul are deja un administrator.
    Organizația/cimitirul se iau din registrul local, ca UID-urile Cloud să corespundă MAIN. Parola și tokenul nu se păstrează."""
    import cimitir_cloud_admin_client as adc
    if not (license_ok or (lambda: _license_item().state == ST_OK))():
        raise adc.AdminApiError("Licența MAIN nu este validă: inițializarea Cloud este refuzată.", "LICENSE")
    errs = validate_admin_form(email, password, password, token)
    if errs:
        raise adc.AdminApiError(" ".join(errs), "INVALID")
    st = admin_state(base_url, client_factory)
    if not st.reachable:
        raise adc.AdminApiError("Backendul Cloud nu răspunde: " + st.detail, "UNREACHABLE")
    if not st.can_initialize:
        raise adc.AdminApiError(st.detail, "BOOTSTRAP_CLOSED")
    ident = local_identity()
    cl = (client_factory or adc.AdminClient)(base_url)
    try:
        res = cl.bootstrap(token.strip(), email.strip(), password, display_name, ident.get("organization_uid") or None,
                           ident.get("organization_name") or "", ident.get("cemetery_uid") or None, ident.get("cemetery_name") or "")
    finally:
        cl.close()
    return {"email": res.get("email", email), "organization_uid": res.get("organization_uid"), "cemetery_uid": res.get("cemetery_uid")}
