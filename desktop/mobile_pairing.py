"""mobile_pairing.py — MAIN: asocierea telefoanelor Mobile v2 direct din MAIN (fără Qt, testabil).

Folosește legătura MAIN deja asociată (cheia din SecretStore, aceeași ca la sincronizare) și mecanismul EXISTENT de pairing (`/api/v1/phone-pairing`):
nu există alt sistem și nu e nevoie de parola administratorului Cloud sau de browser. Codul brut apare doar în memorie (la emitere). Orice eroare devine un mesaj în română.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import cloud_sync as cs
import sync_v2

NEEDED = "permissions_matrix"
PROFILE_LABELS = {"administrator": "Administrator", "secretar": "Secretar", "personal_cimitir": "Personal cimitir", "doar_consultare": "Doar consultare"}


class PairingProblem(Exception):
    """Mesaj clar, în română, sigur de afișat (fără secrete)."""


def explain(exc: BaseException) -> str:
    if isinstance(exc, PairingProblem):
        return str(exc)
    if isinstance(exc, cs.RetryLater):
        return f"Serverul Cloud nu răspunde ({getattr(exc, 'detail', exc)}). Verifică internetul și adresa backendului din profilul Cloud activ."
    if isinstance(exc, cs.Rejected):
        d, st = str(getattr(exc, "detail", exc)), getattr(exc, "status", 0)
        if st == 401:
            return ("Serverul de la această adresă nu recunoaște legătura MAIN (cheie invalidă, revocată sau backend greșit/alt mediu). "
                    "Verifică în profilul Cloud activ că adresa este backendul la care a fost asociat acest calculator.")
        if st == 403:
            return "Operația cere un calculator MAIN asociat cu drepturi de administrare."
        if st == 429:
            return "Prea multe coduri active sau încercări. Revocă un cod activ sau așteaptă expirarea."
        if st == 400:
            return "Cerere invalidă respinsă de server."
        return d or f"Serverul a refuzat operația (HTTP {st})."
    return f"Operația a eșuat ({type(exc).__name__})."


def current_url() -> str:
    return sync_v2.connection()[0]


def check_instance() -> Dict[str, Any]:
    """Verifică identitatea și capabilitățile backendului ÎNAINTE de orice operație: autentificare MAIN reușită + release cu Mobile v2 (permisiuni pe categorii)."""
    url, key, _src = sync_v2.connection()
    if not url:
        raise PairingProblem("Nu este configurat niciun Cloud. Alege un profil Cloud activ și asociază acest calculator.")
    if not key:
        raise PairingProblem("Acest calculator nu este asociat cu Cloud-ul (lipsește legătura MAIN) sau asocierea nu este verificată. Asociază-l din Centrul Cloud.")
    try:
        info = sync_v2.request("GET", "instance").json()
    except cs.Rejected as exc:
        if getattr(exc, "status", 0) in (404, 405):
            raise PairingProblem(f"Backendul de la {url} rulează o versiune veche, fără Permisiuni Mobile v2. Actualizează-l (ACTUALIZARE_BACKEND_TEST.md) înainte de a atribui drepturi.") from None
        raise PairingProblem(explain(exc)) from None
    except cs.RetryLater as exc:
        raise PairingProblem(explain(exc)) from None
    if info.get("device_type") != "MAIN":
        raise PairingProblem("Cheia configurată nu este a unui calculator MAIN.")
    missing = [c for c in (NEEDED, "pairing_codes_by_main") if c not in info.get("capabilities", [])]
    if missing:
        raise PairingProblem("Baza de date a backendului nu are migrările necesare pentru Mobile v2 (027 / 023). Aplică migrările pe TEST, apoi reîncearcă.")
    prof = ""
    try:
        import cloud_profiles
        p = cloud_profiles.active_profile()
        prof = f"profil Cloud activ „{p.name}” ({p.environment}) · " if p else ""
    except Exception:  # noqa: BLE001  (profilele sunt opționale)
        pass
    return {**info, "url": url, "message": f"Backend verificat: {prof}{url} · versiune {info.get('release')} · identitate bază {info.get('db_fingerprint')}."}


def _call(method: str, op: str, **kw) -> Any:
    try:
        return sync_v2.request(method, op, prefix=sync_v2.PAIRING_API, **kw).json()
    except (cs.Rejected, cs.RetryLater) as exc:
        raise PairingProblem(explain(exc)) from None


def issue_code(ttl: int = 600) -> Dict[str, Any]:
    check_instance()
    res = _call("POST", "codes", json={"ttl": int(ttl)})
    res["deadline"] = time.time() + int(res.get("ttl") or ttl)      # ceasul local (nu cel al serverului): fără probleme de decalaj
    res["pair_path"] = "/mobile/#pair=" + str(res["code"]).replace("-", "")
    return res


def revoke_code(code_id: str) -> Dict[str, Any]:
    return _call("POST", "codes/revoke", json={"code_id": code_id})


def codes() -> List[Dict[str, Any]]:
    return _call("GET", "codes")


def phones() -> List[Dict[str, Any]]:
    """Telefoanele active, cu rezumatul drepturilor (implicit „Doar citire”)."""
    try:
        data = sync_v2.list_devices()
    except (cs.Rejected, cs.RetryLater) as exc:
        raise PairingProblem(explain(exc)) from None
    labels = {**PROFILE_LABELS, **{k: v for k, v in (data.get("profiles") or {}).items()}}
    out = []
    for d in data.get("devices", []):
        out.append({**d, "rights": rights_summary(d, labels)})
    return out


def rights_summary(d: Dict[str, Any], labels: Optional[Dict[str, str]] = None) -> str:
    m = d.get("matrix")
    if m:
        if not any(c.get(a) for c in m.values() for a in ("add", "edit", "delete")):
            return "Doar citire" + (f" ({(labels or {}).get(d['profile'], d['profile'])})" if d.get("profile") else "")
        return (labels or {}).get(d.get("profile") or "", "Personalizat")
    if d.get("editable"):
        return "Registre editabile (mod vechi): " + ", ".join(d["editable"])
    return "Doar citire (implicit)"


def revoke_phone(binding_uid: str) -> Dict[str, Any]:
    return _call("POST", f"phones/{binding_uid}/revoke")
