"""field_inbox_client.py — client HTTPS al MAIN pentru Field Inbox v1 + Phone Pairing v1 (fără Qt, fără acces direct la PostgreSQL/Storage).

MAIN -> HTTPS API (`/api/v1/field-inbox`, `/api/v1/phone-pairing`) cu `Authorization: Binding <cheie>`; cheia vine din profilul Cloud activ
(SecretStore, vezi cloud_profiles.py) și nu apare în UI, loguri, excepții sau răspunsuri. Mesajele de eroare sunt texte fixe (nu se afișează
mesajul serverului). Nu există metodă pentru `imported` și nici ștergere: UI-ul poate doar PENDING -> ACCEPTED | REJECTED.
URL-urile semnate se folosesc o singură dată, în memorie, fără header de autentificare, și nu se salvează.
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

import cloud_profiles
import cloud_sync

log = logging.getLogger("cimitir.field_inbox_client")

API = "/api/v1"
INBOX = API + "/field-inbox"
PAIRING = API + "/phone-pairing"
ITEM_TYPES = ("DECEASED", "WORK", "PERMIT", "DOCUMENT", "GRAVE_PHOTO", "WORK_PHOTO")
STATUSES = ("PENDING", "ACCEPTED", "REJECTED", "IMPORTED")
WORK_STAGES = ("BEFORE", "DURING", "AFTER")
UI_TRANSITIONS = {"PENDING": ("ACCEPTED", "REJECTED")}
DEFAULT_REJECT_REASON = "Respins de operator"
MAX_REASON = 500
TIMEOUT = 15
MAX_PAGE = 200
MAX_COUNT = 1000
MAX_PREVIEW_BYTES = 25 * 1024 * 1024
PREVIEW_MIMES = {"image/jpeg": (b"\xff\xd8\xff",), "image/png": (b"\x89PNG\r\n\x1a\n",), "application/pdf": (b"%PDF-",)}
UNAVAILABLE = "Cloud indisponibil"
NOT_CONFIGURED = "Cloud neconfigurat"

TYPE_LABELS = {"DECEASED": "Defunct", "WORK": "Lucrare", "PERMIT": "Aviz / autorizație", "DOCUMENT": "Document",
               "GRAVE_PHOTO": "Foto mormânt", "WORK_PHOTO": "Foto lucrare"}
STATUS_LABELS = {"PENDING": "În așteptare", "ACCEPTED": "Acceptat", "REJECTED": "Respins", "IMPORTED": "Importat"}
STAGE_LABELS = {"BEFORE": "ÎNAINTE", "DURING": "ÎN TIMPUL lucrării", "AFTER": "DUPĂ"}
FIELD_LABELS = {"nume": "Nume", "prenume": "Prenume", "data_nasterii": "Data nașterii", "data_deces": "Data decesului", "data_inhumare": "Data înhumării",
                "sex": "Sex", "observatii": "Observații", "descriere": "Descriere", "data_inceput": "Data început", "data_sfarsit": "Data sfârșit",
                "executant": "Executant", "nr_aviz": "Nr. aviz", "data_aviz": "Data aviz", "lucrare": "Lucrare", "doc_type": "Tip document",
                "caption": "Descriere foto/document", "nr_evidenta": "Nr. evidență"}

_ERROR_TEXT = {
    "UNAUTHENTICATED": "Cheia de legătură MAIN este invalidă sau revocată. Verifică profilul Cloud activ.",
    "FORBIDDEN": "Operația cere un dispozitiv MAIN.", "SCOPE_MISMATCH": "Elementul nu există (sau aparține altui cimitir).",
    "INVALID_TRANSITION": "Tranziție invalidă: starea elementului s-a schimbat sau nu permite această operație.",
    "ITEM_INVALID": "Cerere invalidă.", "NOT_READY": "Fișierul nu este disponibil pentru descărcare.", "RATE_LIMITED": "Prea multe încercări; reîncearcă mai târziu.",
    "TOO_MANY_OPEN_CODES": "Prea multe coduri active; așteaptă expirarea sau folosirea lor.", "REQUEST_INVALID": "Cerere invalidă.",
    "STORAGE_UNAVAILABLE": UNAVAILABLE, "INTERNAL": "Eroare internă a serverului.",
}


class FieldCloudError(Exception):
    """Mesajul este întotdeauna un text fix, sigur de afișat."""


class NotConfigured(FieldCloudError):
    def __init__(self, detail: str = NOT_CONFIGURED):
        super().__init__(detail)


class CloudUnavailable(FieldCloudError):
    def __init__(self):
        super().__init__(UNAVAILABLE)


class ApiError(FieldCloudError):
    def __init__(self, code: str, status: int = 0):
        super().__init__(_ERROR_TEXT.get(code, "Cererea a fost refuzată de server."))
        self.code, self.status = code, status


class InvalidTransition(ApiError):
    def __init__(self, detail: str = ""):
        super().__init__("INVALID_TRANSITION")
        if detail:
            self.args = (detail,)


def _scrub(text: str, key: str) -> str:
    return text.replace(key, "***") if key and len(key) >= 8 else text


class FieldCloudClient:
    def __init__(self, base_url: str, key: str, http: Any = None, timeout: int = TIMEOUT):
        if cloud_sync.validate_url(base_url):
            raise NotConfigured()
        if (key or "").count(".") != 2 or not key.strip():
            raise NotConfigured("Cloud neconfigurat: cheia profilului nu este o cheie de legătură MAIN (cimitir.<id>.<secret>).")
        self._base = base_url.rstrip("/")
        self._key = key.strip()
        self._http = http if http is not None else requests
        self._timeout = timeout

    def __repr__(self) -> str:
        return f"FieldCloudClient({self._base})"

    # ------------------------------------------------------------------ transport
    def _call(self, method: str, path: str, params: Optional[dict] = None, body: Optional[dict] = None) -> Any:
        headers = {"Authorization": f"Binding {self._key}", "Accept": "application/json"}
        try:
            resp = self._http.request(method, self._base + path, headers=headers, params=params, json=body, timeout=self._timeout, allow_redirects=False)
        except requests.RequestException as exc:
            log.warning("Field Inbox API indisponibil (%s)", type(exc).__name__)
            raise CloudUnavailable() from None
        status = resp.status_code
        if status in (301, 302, 303, 307, 308, 502, 503, 504):
            raise CloudUnavailable()
        try:
            data = resp.json()
        except ValueError:
            raise (CloudUnavailable() if status >= 500 else ApiError("INVALID_RESPONSE", status)) from None
        if 200 <= status < 300 and isinstance(data, dict) and data.get("ok"):
            return data.get("result")
        code = str(data.get("code") or "ERROR")[:40] if isinstance(data, dict) else "ERROR"
        if code == "INVALID_TRANSITION":
            raise InvalidTransition()
        raise ApiError(code, status)

    # ------------------------------------------------------------------ Field Inbox
    def list_items(self, status: Optional[str] = None, item_type: Optional[str] = None, limit: int = MAX_PAGE, offset: int = 0) -> List[Dict[str, Any]]:
        if status is not None and status not in STATUSES:
            raise ValueError("status invalid")
        params: Dict[str, Any] = {"limit": min(max(1, int(limit)), MAX_PAGE), "offset": max(0, int(offset))}
        if status:
            params["status"] = status
        if item_type:
            params["item_type"] = item_type
        return list(self._call("GET", INBOX + "/items", params=params) or [])

    def count_pending(self) -> Tuple[int, bool]:
        """(număr PENDING, True dacă există mai multe decât plafonul MAX_COUNT) — peste lista existentă, fără API nou."""
        total, offset = 0, 0
        while total < MAX_COUNT:
            page = self.list_items("PENDING", limit=MAX_PAGE, offset=offset)
            total += len(page)
            if len(page) < MAX_PAGE:
                return total, False
            offset += MAX_PAGE
        return total, True

    def get_item(self, uid: str) -> Dict[str, Any]:
        return self._call("GET", f"{INBOX}/items/{uid}")

    def signed_url(self, item_uid: str, file_uid: str, ttl: int = 60) -> Dict[str, Any]:
        return self._call("GET", f"{INBOX}/items/{item_uid}/files/{file_uid}/url", params={"ttl": min(max(1, int(ttl)), 300)})

    def fetch_attachment(self, item_uid: str, file_uid: str) -> Tuple[bytes, str]:
        """(octeți, mime): URL semnat cerut la momentul previzualizării, descărcat fără autentificare, verificat (mărime, SHA-256, tip), apoi uitat."""
        info = self.signed_url(item_uid, file_uid)
        url, mime = str(info.get("url") or ""), str(info.get("mime") or "")
        if mime not in PREVIEW_MIMES or cloud_sync.validate_url(_origin(url)) or not url:
            raise ApiError("PREVIEW_REJECTED")
        try:
            resp = self._http.request("GET", url, headers={}, timeout=self._timeout, allow_redirects=False)
        except requests.RequestException:
            raise CloudUnavailable() from None
        data = resp.content if resp.status_code == 200 else b""
        if not data or len(data) > MAX_PREVIEW_BYTES or (info.get("size") and len(data) != int(info["size"])):
            raise ApiError("PREVIEW_REJECTED")
        if info.get("sha256") and hashlib.sha256(data).hexdigest() != str(info["sha256"]).lower():
            raise ApiError("PREVIEW_REJECTED")
        if not any(data.startswith(m) for m in PREVIEW_MIMES[mime]):
            raise ApiError("PREVIEW_REJECTED")
        return data, mime

    def accept(self, item_uid: str, current_status: Optional[str] = None) -> Dict[str, Any]:
        _guard(current_status, "ACCEPTED")
        return self._call("POST", f"{INBOX}/items/{item_uid}/accept")

    def reject(self, item_uid: str, reason: str = "", current_status: Optional[str] = None) -> Dict[str, Any]:
        _guard(current_status, "REJECTED")
        reason = (reason or "").strip()[:MAX_REASON] or DEFAULT_REJECT_REASON
        return self._call("POST", f"{INBOX}/items/{item_uid}/reject", body={"reason": reason})

    # ------------------------------------------------------------------ Phone Pairing
    def issue_pairing_code(self, ttl: int = 600) -> Dict[str, Any]:
        return self._call("POST", PAIRING + "/codes", body={"ttl": int(ttl)})

    def list_phones(self) -> List[Dict[str, Any]]:
        return list(self._call("GET", PAIRING + "/phones") or [])

    def revoke_phone(self, binding_uid: str) -> Dict[str, Any]:
        return self._call("POST", f"{PAIRING}/phones/{binding_uid}/revoke")


def _origin(url: str) -> str:
    from urllib.parse import urlparse
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else ""


def _guard(current: Optional[str], to: str) -> None:
    if current is not None and to not in UI_TRANSITIONS.get(current, ()):
        raise InvalidTransition(f"Tranziția {current} -> {to} nu este permisă.")


def connect(store: Any = None, http: Any = None) -> Tuple[FieldCloudClient, Any]:
    """Clientul profilului Cloud ACTIV (cheia din SecretStore) + profilul. NotConfigured dacă lipsește profilul activ / URL / cheia."""
    prof = cloud_profiles.active_profile()
    if prof is None:
        raise NotConfigured()
    url, key = cloud_profiles.effective_connection(store)
    if not url or not key:
        raise NotConfigured()
    return FieldCloudClient(url, key, http=http), prof


# ------------------------------------------------------------------ logică de prezentare (fără Qt)
def badge_text(count: int, more: bool = False) -> str:
    return f"{count}{'+' if more else ''}"


def new_items_message(count: int, more: bool = False) -> str:
    n = badge_text(count, more)
    return f"Ai {n} elemente noi din teren" if count != 1 or more else "Ai 1 element nou din teren"


def toolbar_label(count: int, more: bool = False) -> str:
    return f"📱 Elemente din teren [{badge_text(count, more)}]" if count else "📱 Elemente din teren"


def phone_names(phones: List[Dict[str, Any]]) -> Dict[str, str]:
    return {p["binding_uid"]: p.get("device_name") or "Telefon" for p in phones}


def short_date(iso: Optional[str]) -> str:
    return (iso or "").replace("T", " ")[:16]


def item_row(item: Dict[str, Any], devices: Optional[Dict[str, str]] = None, cemetery_name: str = "") -> Dict[str, str]:
    devices = devices or {}
    cem = cemetery_name or (item.get("cemetery_uid") or "")[:8]
    place = item.get("grave_uid") or item.get("target_uid") or ""
    stage = f" ({STAGE_LABELS[item['work_stage']]})" if item.get("work_stage") in STAGE_LABELS else ""
    return {"uid": item["uid"], "type": TYPE_LABELS.get(item["item_type"], item["item_type"]) + stage, "cemetery": cem, "place": place,
            "date": short_date(item.get("created_at")), "device": devices.get(item.get("created_by") or "", "Telefon necunoscut"),
            "status": STATUS_LABELS.get(item["status"], item["status"])}


def item_preview(item: Dict[str, Any], devices: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Previzualizare structurată: titlu, perechi (etichetă, valoare), fișiere previzualizabile, etapa foto (BEFORE/DURING/AFTER)."""
    row = item_row(item, devices)
    lines = [(FIELD_LABELS.get(k, k), str(v)) for k, v in (item.get("payload") or {}).items()]
    if item.get("grave_uid"):
        lines.append(("Loc / grave_uid", item["grave_uid"]))
    if item.get("target_uid"):
        lines.append(("Țintă (target_uid)", item["target_uid"]))
    lines += [("Dispozitiv", row["device"]), ("Primit la", row["date"]), ("Status", row["status"])]
    if item.get("rejection_reason"):
        lines.append(("Motiv respingere", item["rejection_reason"]))
    files = [{"uid": f["uid"], "filename": f.get("filename") or "fișier", "mime": f.get("mime"), "size": f.get("size"), "ready": f.get("status") == "READY",
              "previewable": f.get("mime") in PREVIEW_MIMES} for f in item.get("files") or []]
    return {"title": TYPE_LABELS.get(item["item_type"], item["item_type"]), "stage": STAGE_LABELS.get(item.get("work_stage") or ""), "lines": lines, "files": files,
            "can_review": item["status"] == "PENDING"}


def format_expiry(expires_at: float, now: Optional[float] = None) -> str:
    left = int(expires_at - (time.time() if now is None else now))
    return "Expirat" if left <= 0 else f"Expiră în {left // 60:02d}:{left % 60:02d}"


def phone_row(p: Dict[str, Any], cemetery_name: str = "") -> Dict[str, str]:
    state = "Revocat" if p.get("revoked_at") or not p.get("active") else "Activ"
    return {"binding_uid": p["binding_uid"], "name": p.get("device_name") or "Telefon", "cemetery": cemetery_name, "paired": short_date(p.get("created_at")),
            "last_seen": short_date(p.get("last_seen_at")) or "—", "state": state, "revocable": state == "Activ"}
