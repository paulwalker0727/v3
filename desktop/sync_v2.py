"""sync_v2.py — transport MAIN (Windows, SQLite) -> Cloud TEST (Sync v2) cu `Authorization: Binding <cheie>`.

MAIN nu atinge niciodată PostgreSQL/Supabase direct: trimite/primește prin `/api/v1/sync-v2/*`. Reutilizează captarea locală (triggere SQLite + outbox),
aplicarea fără re-captare și rezolvarea conflictelor din `entity_sync`; aici există doar transportul, ciclul „Sincronizează" și administrarea telefoanelor.
Cheia vine din profilul Cloud activ (SecretStore); nu apare în loguri/excepții.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

import cloud_sync as cs
import cimitir_entities as ents
import database as db

API = "/api/v1/sync-v2"
SET_LAST_REPORT = "sync_v2_last_report"
SET_SCOPE = "sync_v2_scope"
BINDING_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}\.[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.[A-Za-z0-9_-]{32,}$")
_ERR = {401: "Cheia de legătură MAIN este invalidă sau revocată.", 403: "Operația nu este permisă pentru acest dispozitiv.",
        404: "Elementul nu există în acest cimitir.", 429: "Prea multe cereri; reîncearcă mai târziu."}


def is_binding(key: Optional[str] = None) -> bool:
    key = cs.get_config()["key"] if key is None else key
    return bool(key and BINDING_RE.match(key))


class _Resp:
    def __init__(self, payload: Any):
        self._p = payload

    def json(self) -> Any:
        return self._p


def request(method: str, op: str, **kwargs) -> _Resp:
    """`op`: 'push' | 'pull' | 'status' | ... Aceleași excepții ca `cloud_sync._request` (RetryLater: rețea/5xx; Rejected: 4xx)."""
    cfg = cs.get_config()
    if not cs.url_is_secure(cfg["url"]):
        raise cs.Rejected(0, "Adresa serverului trebuie să fie HTTPS.")
    kwargs.setdefault("timeout", cs.TIMEOUT)
    try:
        resp = requests.request(method, f"{cfg['url']}{API}/{op.lstrip('/')}",
                                headers={"Authorization": f"Binding {cfg['key']}", "X-Sync-Client": cs.client_id()}, **kwargs)
    except requests.RequestException as exc:
        cs._mark_online(False)
        raise cs.RetryLater(f"Fără conexiune la server ({type(exc).__name__})")
    if resp.status_code >= 500:
        cs._mark_online(False)
        raise cs.RetryLater(f"Server indisponibil ({resp.status_code})")
    cs._mark_online(True)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code >= 400 or body.get("ok") is False:
        raise cs.Rejected(resp.status_code, _ERR.get(resp.status_code) or body.get("message") or "cerere refuzată")
    return _Resp(body.get("result", {}))


def _reset_cursors_if_scope_changed() -> None:
    """Alt server/cimitir față de ultima sincronizare: cursoarele de descărcare pornesc de la zero (rândurile locale nu se șterg)."""
    cfg = cs.get_config()
    parts = cfg["key"].split(".")
    scope = f"{cfg['url']}|{parts[1] if len(parts) == 3 else ''}"
    if (db.get_setting(SET_SCOPE) or "") != scope:
        import entity_sync
        for e in ents.ORDER:
            db.set_setting(entity_sync.CURSOR_PREFIX + e, "0")
        db.set_setting(SET_SCOPE, scope)


def run() -> Dict[str, Any]:
    """Un ciclu „Sincronizează": descarcă (adoptă modificările telefoanelor) -> trimite outbox-ul -> descarcă din nou. Nu ridică excepții."""
    import entity_sync
    report: Dict[str, Any] = {"ok": False, "state": "error", "lines": [], "cereri_noi": 0, "pushed": 0, "applied": 0, "conflicts": 0, "errors": 0,
                              "retry": 0, "pulled": 0, "pending": 0, "started": datetime.now().isoformat(timespec="seconds")}
    try:
        if not cs.is_configured() or not is_binding():
            report["lines"].append("Sincronizarea nu este configurată (profil Cloud cu cheie MAIN).")
            return report
        entity_sync.ensure_tracking()
        _reset_cursors_if_scope_changed()
        status = request("GET", "status").json()
        if status.get("device_type") != "MAIN":
            report["lines"].append("Cheia configurată nu aparține unui dispozitiv MAIN.")
            return report
        ok_pull, msg, n1 = entity_sync.pull_entities()
        report["lines"].append(msg)
        ok_push, lines, counts = entity_sync.flush_entities()
        report["lines"] += lines
        ok_pull2, msg2, n2 = entity_sync.pull_entities()
        if n2:
            report["lines"].append(msg2)
        report.update(pushed=counts["sent"], applied=counts["applied"], conflicts=counts["conflict"], errors=counts["error"],
                      retry=counts["retry"], pulled=n1 + n2)
        report["ok"] = ok_pull and ok_push and ok_pull2
        report["state"] = "ok" if report["ok"] else ("conflict" if counts["conflict"] else "partial")
        try:
            report["server_open_conflicts"] = int(request("GET", "status").json().get("open_conflicts", 0))
        except (cs.RetryLater, cs.Rejected):
            pass
    except cs.RetryLater as exc:
        report.update(state="offline")
        report["lines"].append(f"Offline: {exc}. Modificările rămân în coadă și se trimit la reconectare.")
    except cs.Rejected as exc:
        report["lines"].append(f"Refuzat de server: {exc.detail}")
    except Exception as exc:  # noqa: BLE001
        report["lines"].append(f"Eroare neașteptată: {type(exc).__name__}")
    report["pending"] = len(entity_sync.pending_entity_ops(("pending", "conflict", "error")))
    report["finished"] = datetime.now().isoformat(timespec="seconds")
    db.set_setting(SET_LAST_REPORT, json.dumps({k: v for k, v in report.items() if k != "lines"}))
    if report["ok"]:
        db.set_setting(cs.SET_LAST, datetime.now().strftime("%d.%m.%Y %H:%M"))
        db.set_setting(cs.SET_LAST_ERROR, "")
    else:
        db.set_setting(cs.SET_LAST_ERROR, (report["lines"] or ["Sincronizare incompletă"])[-1][:200])
    return report


def last_report() -> Dict[str, Any]:
    try:
        return json.loads(db.get_setting(SET_LAST_REPORT) or "{}")
    except ValueError:
        return {}


# ------------------------------------------------------------------ telefoane / permisiuni / conflicte / jurnal (administrare din MAIN)
def list_devices() -> Dict[str, Any]:
    return request("GET", "devices").json()


def set_device_entities(ref: str, entities: List[str]) -> List[str]:
    return request("PUT", f"devices/{ref}/permissions", json={"entities": list(entities)}).json()["editable"]


def server_conflicts(include_logged: bool = True) -> List[Dict[str, Any]]:
    return request("GET", "conflicts", params={"all": "1" if include_logged else "0"}).json()["conflicts"]


def resolve_server_conflict(conflict_id: int, resolution: str) -> str:
    return request("POST", f"conflicts/{int(conflict_id)}/resolve", json={"resolution": resolution}).json()["resolution"]


def journal(limit: int = 50) -> List[Dict[str, Any]]:
    return request("GET", "runs", params={"limit": limit}).json()["runs"]
