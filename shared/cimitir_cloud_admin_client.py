"""cimitir_cloud_admin_client.py — clientul HTTP al interfeței administrative Cloud (/api/v1/admin) pentru MAIN și scripturi.

Parola se folosește doar pentru login și nu se păstrează; tokenul de sesiune stă în memorie (nu pe disc). Emiterea/revocarea codurilor cere sesiune de Owner.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx


class AdminApiError(Exception):
    def __init__(self, message: str, code: str = "ERROR", status: int = 0):
        super().__init__(message)
        self.code, self.status = code, status


class AdminClient:
    def __init__(self, base_url: str, timeout: float = 25.0, http: Optional[httpx.Client] = None):
        self.base = base_url.rstrip("/") + "/api/v1/admin"
        self._http = http or httpx.Client(timeout=timeout)
        self._token: Optional[str] = None

    def close(self) -> None:
        self._http.close()

    def _req(self, method: str, path: str, body: Optional[dict] = None, params: Optional[dict] = None) -> Any:
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        try:
            r = self._http.request(method, self.base + path, json=body, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise AdminApiError("Serverul Cloud nu răspunde (" + type(exc).__name__ + ").", "UNREACHABLE") from None
        try:
            j = r.json()
        except ValueError:
            raise AdminApiError(f"Răspuns neașteptat de la server (HTTP {r.status_code}); serverul nu pare să aibă interfața de administrare.", "INCOMPATIBLE", r.status_code) from None
        if not j.get("ok"):
            raise AdminApiError(j.get("message") or f"Eroare HTTP {r.status_code}", j.get("code", "ERROR"), r.status_code)
        return j.get("result")

    @property
    def logged_in(self) -> bool:
        return bool(self._token)

    def status(self) -> Dict[str, Any]:
        return self._req("GET", "/status")

    def bootstrap(self, token: str, email: str, password: str, display_name: str = "", organization_uid: Optional[str] = None,
                  organization_name: str = "", cemetery_uid: Optional[str] = None, cemetery_name: str = "") -> Dict[str, Any]:
        """Inițializare unică a primului administrator Cloud (token tehnic de inițializare; fără sesiune)."""
        return self._req("POST", "/bootstrap", {"token": token, "email": email, "password": password, "display_name": display_name,
                                                "organization_uid": organization_uid, "organization_name": organization_name,
                                                "cemetery_uid": cemetery_uid, "cemetery_name": cemetery_name})

    def login(self, email: str, password: str) -> Dict[str, Any]:
        self._token = None
        res = self._req("POST", "/login", {"email": email, "password": password})
        self._token = res["token"]
        return self.me()

    def logout(self) -> None:
        if self._token:
            try:
                self._req("POST", "/logout", {})
            finally:
                self._token = None

    def me(self) -> Dict[str, Any]:
        return self._req("GET", "/me")

    def registry_status(self, organization_uid: str, cemetery_uid: str) -> Dict[str, Any]:
        return self._req("GET", "/registry", params={"organization_uid": organization_uid, "cemetery_uid": cemetery_uid})

    def provision(self, organization_uid: str, organization_name: str, cemetery_uid: str, cemetery_name: str) -> Dict[str, Any]:
        return self._req("POST", "/provision", {"organization_uid": organization_uid, "organization_name": organization_name,
                                                "cemetery_uid": cemetery_uid, "cemetery_name": cemetery_name})

    def request_registration(self, secret: str, organization_uid: str, organization_name: str, cemetery_uid: str, cemetery_name: str, device_label: str = "") -> Dict[str, Any]:
        return self._req("POST", "/registry-requests", {"secret": secret, "organization_uid": organization_uid, "organization_name": organization_name,
                                                        "cemetery_uid": cemetery_uid, "cemetery_name": cemetery_name, "device_label": device_label})

    def registration_status(self, request_id: str, secret: str) -> Dict[str, Any]:
        return self._req("POST", "/registry-requests/status", {"request_id": request_id, "secret": secret})

    def list_requests(self) -> List[Dict[str, Any]]:
        return self._req("GET", "/registry-requests")

    def decide_request(self, request_id: str, approve: bool) -> Dict[str, Any]:
        return self._req("POST", "/registry-requests/decide", {"request_id": request_id, "approve": bool(approve)})

    def issue_code(self, organization_uid: str, cemetery_uid: str, kind: str, ttl: Optional[int] = None) -> Dict[str, Any]:
        return self._req("POST", "/codes", {"organization_uid": organization_uid, "cemetery_uid": cemetery_uid, "kind": kind, "ttl": ttl})

    def list_codes(self, organization_uid: str, cemetery_uid: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._req("GET", "/codes", params={"organization_uid": organization_uid, **({"cemetery_uid": cemetery_uid} if cemetery_uid else {})})

    def revoke_code(self, organization_uid: str, code_id: str) -> Dict[str, Any]:
        return self._req("POST", "/codes/revoke", {"organization_uid": organization_uid, "code_id": code_id})

    def list_devices(self, organization_uid: str) -> List[Dict[str, Any]]:
        return self._req("GET", "/devices", params={"organization_uid": organization_uid})

    def revoke_device(self, organization_uid: str, binding_uid: str) -> Dict[str, Any]:
        return self._req("POST", "/devices/revoke", {"organization_uid": organization_uid, "binding_uid": binding_uid})
