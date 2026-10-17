"""cloud_simple.py — configurarea simplă Cloud pentru utilizatorul MAIN (fără UI; testabilă).

Utilizatorul vede și completează doar: nume profil, adresă Cloud, mediu (TEST / PROD), plus starea conexiunii, a asocierii și cimitirul asociat.
Host/utilizator/parola PostgreSQL, Supabase Secret Key și cheia brută de sincronizare NU se cer aici: rămân în configurarea serverului și în SecretStore
(cheia Binding o pune asocierea direct în SecretStore). Profilul avansat (`cloud_profiles_dialog`) există doar pentru administratorul serverului.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

import cloud_profiles as cp

ENV_ADVANCED = "CIMITIR_CLOUD_ADVANCED_UI"
ENV_CHOICES = (("TEST", "TEST"), ("PROD", "PRODUCTION"))        # (etichetă afișată, valoare stocată)
DEFAULT_NAME = "Cloud Cimitir"

CONN_NONE, CONN_UNCHECKED, CONN_OK, CONN_ERROR = "not_configured", "unchecked", "connected", "error"
PAIR_NONE, PAIR_INCOMPLETE, PAIR_UNVERIFIED, PAIR_OK = "unpaired", "incomplete", "unverified", "paired"

_CONN_TEXT = {CONN_NONE: "Neconfigurat", CONN_UNCHECKED: "Neverificat", CONN_OK: "Conectat", CONN_ERROR: "Eroare de conexiune"}
_PAIR_TEXT = {PAIR_NONE: "Neasociat", PAIR_INCOMPLETE: "Asociere întreruptă", PAIR_UNVERIFIED: "Asociat, în curs de verificare", PAIR_OK: "Asociat"}


def advanced_ui_enabled() -> bool:
    return os.environ.get(ENV_ADVANCED, "") == "1"


def env_label(stored: str) -> str:
    return "PROD" if (stored or "").upper() == "PRODUCTION" else (stored or "TEST").upper()


def env_value(label: str) -> str:
    return dict(ENV_CHOICES).get((label or "").upper(), "TEST")


@dataclass
class SimpleStatus:
    profile_name: str = ""
    cloud_url: str = ""
    environment: str = "TEST"             # etichetă: TEST / PROD
    connection: str = CONN_NONE
    connection_text: str = _CONN_TEXT[CONN_NONE]
    pairing: str = PAIR_NONE
    pairing_text: str = _PAIR_TEXT[PAIR_NONE]
    cemetery_name: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def current_profile() -> Optional[cp.Profile]:
    """Profilul folosit de interfața simplă: cel activ, altfel primul existent."""
    prof = cp.active_profile()
    if prof is not None:
        return prof
    profiles = cp.list_profiles()
    return profiles[0] if profiles else None


def save(name: str, url: str, environment_label: str, confirm_production: bool = False, store: Any = None) -> cp.Profile:
    """Creează sau actualizează profilul curent doar cu cele 3 câmpuri vizibile (restul și secretele rămân neschimbate) și îl activează.
    PROD cere `confirm_production=True` (confirmare explicită în UI)."""
    env = env_value(environment_label)
    name = (name or "").strip() or DEFAULT_NAME
    prof = current_profile()
    if env == "PRODUCTION" and not confirm_production:
        raise cp.ConfirmationRequired("production_activation")
    if prof is None:
        prof = cp.create_profile({"name": name, "environment": env, "api_base_url": url}, None, store)
    else:
        fields = {k: getattr(prof, k) for k in cp.PLAIN_FIELDS}
        fields.update(name=name, environment=env, api_base_url=url)
        if (prof.project_ref or prof.supabase_url) and prof.api_base_url.rstrip("/") != (url or "").strip().rstrip("/"):
            fields.update(project_ref="", supabase_url="")     # adresa s-a schimbat: referințele vechi ale proiectului nu mai sunt valabile
        prof = cp.update_profile(prof.id, fields, None, store)
    if not prof.is_active:
        prof = cp.activate_profile(prof.id, confirm_production=confirm_production)
    return prof


def pairing_state() -> Dict[str, str]:
    """Starea asocierii MAIN și numele cimitirului asociat (fără UID-uri sau chei)."""
    out = {"pairing": PAIR_NONE, "cemetery_name": ""}
    try:
        import cimitir_bootstrap as bs
        import cimitir_registry as reg
        binding = reg.current_binding()
        if binding is None:
            return out
        local = bs.LocalRegistry(binding)
        unverified = local.get("cloud_assoc_unverified") == "1"
        if local.sync_state in bs.SYNC_ACTIVE_STATES:
            out["pairing"] = PAIR_UNVERIFIED if unverified else PAIR_OK
            cem = reg.RegistryCatalog().get_cemetery(binding.cemetery_uid)
            out["cemetery_name"] = (cem or {}).get("name", "")
        elif unverified or local.get("cloud_assoc_plan"):
            out["pairing"] = PAIR_INCOMPLETE
    except Exception:  # noqa: BLE001  (registru indisponibil: afișăm „Neasociat”, fără detalii tehnice)
        pass
    return out


def status(check: bool = False, store: Any = None, http: Any = None) -> SimpleStatus:
    """Starea curentă. Fără `check`, conexiunea e „Neverificat” (nu face cereri de rețea); cu `check` rulează testul read-only."""
    prof = current_profile()
    st = SimpleStatus()
    st.__dict__.update(pairing_state())
    st.pairing_text = _PAIR_TEXT[st.pairing]
    if prof is None or not prof.api_base_url:
        return st
    st.profile_name, st.cloud_url, st.environment = prof.name, prof.api_base_url, env_label(prof.environment)
    st.connection = CONN_UNCHECKED
    if check:
        st.connection = CONN_OK if test_connection(prof, store, http)[0] else CONN_ERROR
    st.connection_text = _CONN_TEXT[st.connection]
    return st


_LABELS = {"API reachable": "Server Cloud", "Tenant/device binding": "Asociere MAIN", "Cemetery binding": "Cimitir asociat"}


def test_connection(prof: cp.Profile, store: Any = None, http: Any = None):
    """(ok, text) — verificare read-only a API-ului Cloud și, dacă MAIN e asociat, a legăturii și a cimitirului. Textul nu conține UID-uri/chei."""
    kwargs = {"http": http} if http is not None else {}
    rep = cp.test_connection(prof, "api", store, **kwargs)
    api_ok = bool(rep.checks) and rep.checks[0].name == "API reachable" and rep.checks[0].status == "OK"
    paired = pairing_state()["pairing"] in (PAIR_OK, PAIR_UNVERIFIED)
    ok = rep.ok if paired else api_ok
    lines = []
    for c in rep.checks:
        if c.status == "OK":
            lines.append(f"[OK] {_LABELS.get(c.name, c.name)}")
        elif c.status == "SKIP" or (not paired and c.name != "API reachable"):
            continue
        else:
            lines.append(f"[EROARE] {_LABELS.get(c.name, c.name)}" + (f" — {c.detail}" if c.detail else ""))
    if ok and not paired:
        lines.append("MAIN nu este încă asociat cu Cloud-ul: folosește „Asociază acest calculator”.")
    return ok, "\n".join(lines)
