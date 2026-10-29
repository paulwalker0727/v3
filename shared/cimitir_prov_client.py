"""cimitir_prov_client.py — clientul serviciului de provizionare, folosit de MAIN (Centrul Cloud) sau de scripturile operatorului. Doar stdlib/requests.

Tokenul de operator se păstrează în SecretStore-ul MAIN (DPAPI), nu în SQLite/loguri. Parola administrativă a bazei se transmite per apel și nu se reține.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class ProvisionerClientError(Exception):
    def __init__(self, code: str, message: str, status: int = 0):
        super().__init__(message)
        self.code, self.status = code, status


class ProvisionerClient:
    def __init__(self, base_url: str, token: str, timeout: float = 300.0, verify: Any = True, session: Any = None):
        self.base, self.token, self.timeout, self.verify = base_url.rstrip("/") + "/api/v1/provisioner", token, timeout, verify
        self._s = session

    def _call(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Any:
        import requests
        try:
            r = (self._s or requests).request(method, self.base + path, json=body if method != "GET" else None, headers={"Authorization": f"Bearer {self.token}"},
                                              timeout=self.timeout, verify=self.verify)
        except requests.RequestException as exc:
            raise ProvisionerClientError("UNREACHABLE", f"serviciul de provizionare nu răspunde ({type(exc).__name__})") from None
        try:
            j = r.json()
        except ValueError:
            raise ProvisionerClientError("BAD_RESPONSE", f"răspuns neașteptat ({r.status_code})", r.status_code) from None
        if not j.get("ok"):
            raise ProvisionerClientError(j.get("code", "ERROR"), j.get("message", "eroare"), r.status_code)
        return j["result"]

    def list_instances(self) -> List[Dict[str, Any]]:
        return self._call("GET", "/instances")

    def get(self, ident: str) -> Dict[str, Any]:
        return self._call("GET", f"/instances/{ident}")

    def register(self, slug: str, parish_name: str, domain: str, owner_public_key: str = "", generate_owner_key: bool = False) -> Dict[str, Any]:
        return self._call("POST", "/instances", {"slug": slug, "parish_name": parish_name, "domain": domain, "owner_public_key": owner_public_key, "generate_owner_key": generate_owner_key})

    def link(self, ident: str, params: Dict[str, Any]) -> Dict[str, Any]:
        return self._call("POST", f"/instances/{ident}/link", params)

    def provision(self, ident: str, admin_database_url: str, release: Optional[str] = None) -> Dict[str, Any]:
        return self._call("POST", f"/instances/{ident}/provision", {"admin_database_url": admin_database_url, "release": release})

    def health(self, ident: str) -> Dict[str, Any]:
        return self._call("POST", f"/instances/{ident}/health", {})

    def action(self, ident: str, name: str, **body) -> Any:
        return self._call("POST", f"/instances/{ident}/{name}", body)

    def audit(self) -> List[Dict[str, Any]]:
        return self._call("GET", "/audit")


STATUS_RO = {"registered": "Înregistrată", "linked": "Proiect Supabase asociat", "verified": "Conectivitate verificată", "migrated": "Schemă aplicată", "secured": "Rol runtime configurat",
             "running": "Activă (sănătoasă)", "degraded": "Activă, cu probleme", "failed": "Eșuată", "suspended": "Suspendată", "deactivated": "Dezactivată", "updating": "În actualizare"}


def status_lines(inst: Dict[str, Any]) -> List[str]:
    """Text pentru Centrul Cloud (fără secrete)."""
    h = inst.get("health") or {}
    lines = [f"{inst['parish_name']} — {STATUS_RO.get(inst['status'], inst['status'])}", f"Domeniu: https://{inst['domain']}  ·  Mediu: {inst['environment']}  ·  Release: {inst.get('release') or '—'}"]
    if h:
        lines.append(f"HTTPS: {'da' if h.get('https_ok') else 'NU'}  ·  Bază de date: {h.get('database') or '?'}  ·  Storage: {h.get('storage') or '?'}  ·  Certificat: {h.get('tls_days_left')} zile")
    lines.append("Administrator inițial: " + ("creat" if inst.get("admin_initialized") else "ÎN AȘTEPTARE (Cloud → Inițializare administrator)"))
    if inst.get("last_error"):
        lines.append("Ultima eroare: " + inst["last_error"])
    return lines
