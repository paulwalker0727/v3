"""cimitir_cloud_associate.py — fluxul „Asociază acest calculator” (MAIN -> Cloud), fără comenzi/SQL/Python manual. Logică fără UI (testabilă); fereastra Qt doar o apelează.

Pași (fiecare întoarce un rezumat afișabil; erorile sunt `AssociationError` cu mesaj în română, fără secrete):
  1. check_api(url)        — HTTPS/HTTP reachability, /health (DB+storage), /bootstrap/info (compatibilitate client↔server)
  2. verify_code(code)     — preflight real pe server: codul, drepturile emitentului, organizația/cimitirul, starea cloud (nu consumă codul)
  3. plan(code)            — plan Flux A (rezumat + plan_hash) ; nimic nu se scrie în cloud
  4. execute(plan, code)   — după confirmarea explicită a utilizatorului: begin_run → redeem → cheia în SecretStore (DPAPI) → verify_empty → complete
  5. verify()              — test automat al legăturii (cheie lizibilă din SecretStore, authenticate, cheia absentă din SQLite) ; abia apoi se activează sincronizarea
Dacă procesul e întrerupt, `incomplete()` arată starea, iar `resume()` / `abort()` o reiau sau o anulează în siguranță (rollback local + cloud + secret).
Protecțiile din cimitir_bootstrap (BLOCKED_ACTIVE_EMPTY, verify_empty, plan_hash/TOCTOU) NU sunt ocolite.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

import cimitir_bootstrap as bs
import cimitir_cloud_client as client
import cimitir_cloud_control as cc
import cimitir_plan_guard as pg

SET_UNVERIFIED = "cloud_assoc_unverified"      # "1" = sincronizarea rămâne blocată până trece verify()
SET_PENDING_PLAN = "cloud_assoc_plan"          # planul (fără cod/secrete) păstrat pentru reluare după întrerupere
_BINDING_RE = re.compile(r"^cimitir\.[0-9a-f-]{36}\.[A-Za-z0-9_-]{32,}$")


class AssociationError(Exception):
    def __init__(self, message: str, kind: str = "error"):
        super().__init__(message)
        self.kind = kind


def normalize_url(raw: str) -> str:
    u = (raw or "").strip().rstrip("/")
    if u and "://" not in u:
        u = "https://" + u
    p = urlparse(u)
    if p.scheme not in ("https", "http") or not p.hostname:
        raise AssociationError("URL Cloud invalid (ex.: https://cloud.exemplu.ro)", "url")
    if p.scheme == "http" and p.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise AssociationError("Conexiunea către Cloud trebuie să fie HTTPS (HTTP e permis doar pe localhost).", "url")
    if p.path not in ("", "/") or p.query or p.fragment or p.username:
        raise AssociationError("Introdu doar adresa serverului, fără cale sau parametri.", "url")
    return f"{p.scheme}://{p.netloc}"


def _friendly(exc: BaseException) -> str:
    if isinstance(exc, client.ServerIncompatible):
        return str(exc)
    if isinstance(exc, client.TransportError):
        return "Serverul Cloud nu răspunde sau conexiunea a fost întreruptă. Verifică adresa și internetul."
    if isinstance(exc, cc.CodeError):
        return "Codul este invalid, expirat sau deja folosit. Cere un cod nou administratorului."
    if isinstance(exc, cc.PermissionDenied):
        return "Emitentul codului nu are dreptul de a asocia acest calculator (este necesar rolul Owner)."
    if isinstance(exc, cc.ScopeError):
        return "Codul nu este valabil pentru acest cimitir/organizație sau este invalid/expirat."
    if isinstance(exc, cc.BootstrapLocked):
        return "O altă asociere este în curs pe acest cimitir. Așteaptă sau anulează-o."
    if isinstance(exc, bs.BlockedFlow):
        return str(exc)
    if isinstance(exc, (bs.PlanError, pg.PlanRejected, pg.PlanExpired, pg.TargetDrift, pg.PlanBlocked)):
        return f"Planul nu mai este valid: {exc}"
    return f"Operația a eșuat ({type(exc).__name__})."


class Association:
    def __init__(self, binding, store, inst: str, url: str, device_name: str = "MAIN", rcp_factory: Optional[Callable[[str], Any]] = None):
        self.binding, self.store, self.inst, self.device_name = binding, store, inst, device_name or "MAIN"
        self.url = normalize_url(url)
        self._factory = rcp_factory or (lambda base: client.RemoteControlPlane(base, retries=2))
        self.local = bs.LocalRegistry(binding)

    def _rcp(self):
        return self._factory(self.url)

    # ------------------------------------------------------------------ 1
    def check_api(self) -> Dict[str, Any]:
        rcp = self._rcp()
        try:
            try:
                health = rcp.health()
                info = rcp.server_info()
            except client.ServerIncompatible as exc:
                raise AssociationError(str(exc), "incompatible") from None
            except Exception as exc:  # noqa: BLE001
                raise AssociationError(_friendly(exc), "unreachable") from None
        finally:
            rcp.close()
        ok = health.get("status") == 200 and health.get("ok") is not False
        if not ok:
            raise AssociationError("Serverul Cloud răspunde, dar nu este sănătos (bază de date/stocare indisponibile).", "unhealthy")
        return {"url": self.url, "https": self.url.startswith("https://"), "database": health.get("database"), "storage": health.get("storage"),
                "bootstrap_api": info.get("bootstrap_api"), "message": "Conexiune verificată: API-ul Cloud este disponibil."}

    # ------------------------------------------------------------------ 2
    def verify_code(self, code: str) -> Dict[str, Any]:
        self._need_code(code)
        rcp = self._rcp()
        try:
            try:
                cloud = rcp.preflight_for_code(code, self.inst, self.binding.cemetery_uid)
            except (bs.Crash, cc.ControlError, pg.PlanRejected) as exc:
                raise AssociationError(_friendly(exc), "code") from None
        finally:
            rcp.close()
        if cloud["organization_uid"] != self.binding.organization_uid:
            raise AssociationError("Codul aparține altei organizații decât registrul local selectat.", "scope")
        return {"organization_uid": cloud["organization_uid"], "cemetery_uid": self.binding.cemetery_uid, "cemetery_status": cloud.get("status"),
                "cloud_exists": cloud.get("exists"), "cloud_classification": cloud.get("classification"), "cloud_rows": cloud.get("rows"),
                "message": "Cod valid; drepturile emitentului au fost verificate."}

    @staticmethod
    def _need_code(code: str) -> None:
        if not (code or "").strip():
            raise AssociationError("Introdu codul temporar de asociere.", "code")

    # ------------------------------------------------------------------ 3
    def plan(self, code: str, cemetery_name: str = "") -> Dict[str, Any]:
        self._need_code(code)
        rcp = self._rcp()
        try:
            plan = bs.plan_flow_a(self.binding, rcp, code, self.inst, cemetery_name=cemetery_name)
        except (bs.BlockedFlow, bs.PlanError, bs.Crash, cc.ControlError, pg.PlanRejected) as exc:
            raise AssociationError(_friendly(exc), "blocked" if isinstance(exc, bs.BlockedFlow) else "plan") from None
        finally:
            rcp.close()
        plan["device_name"] = self.device_name
        self.local.put(**{SET_PENDING_PLAN: json.dumps(plan)})
        li = self.local.inspect()
        return {"plan": plan, "plan_hash": plan["plan_hash"], "summary": {
            "Server": self.url, "Organizație (UID)": self.binding.organization_uid, "Cimitir (UID)": self.binding.cemetery_uid,
            "Registru local": f"{li['classification']} · {li['rows']} rânduri · {li['outbox_pending']} în așteptare",
            "Operațiune": "Asociere nouă MAIN (Flux A): se creează legătura și cheia definitivă, salvată în SecretStore (nu în SQLite/loguri)",
            "Dispozitiv": self.device_name, "Plan expiră în": f"{int(plan['expires_at'] - plan['created_at'])} s"}}

    def cancel_plan(self, plan: Dict[str, Any]) -> None:
        bs.cancel_plan(self.binding, plan["bootstrap_id"])
        self.local.clear(SET_PENDING_PLAN)

    # ------------------------------------------------------------------ 4 + 5
    def execute(self, plan: Dict[str, Any], code: str, confirmed: bool) -> Dict[str, Any]:
        if confirmed is not True:
            raise AssociationError("Asocierea necesită confirmarea explicită a utilizatorului.", "confirm")
        self._need_code(code)
        self.local.put(**{SET_UNVERIFIED: "1"})                       # sync blocat până trece verificarea completă
        rcp = self._rcp()
        try:
            try:
                report = bs.execute_flow_a(self.binding, rcp, self.store, plan, plan["plan_hash"], code, self.inst, cloud_url=self.url)
            except bs.Crash as exc:
                raise AssociationError(_friendly(exc) + " Starea e salvată: folosește „Reia” sau „Anulează asocierea”.", "interrupted") from None
            except (bs.BootstrapError, bs.PlanError, cc.ControlError, pg.PlanRejected) as exc:
                self.local.clear(SET_PENDING_PLAN)
                raise AssociationError(_friendly(exc), "failed") from None
        finally:
            rcp.close()
        return {"report": report, "verification": self.verify()}

    def verify(self) -> Dict[str, Any]:
        """Test automat; doar dacă trece se ridică blocajul de sincronizare."""
        checks: Dict[str, str] = {}
        ref = self.local.get("cloud_secret_ref")
        key = self.store.get(ref) if ref else None
        if not key or not _BINDING_RE.match(key):
            raise AssociationError("Cheia legăturii nu poate fi citită din SecretStore.", "verify")
        checks["Cheia definitivă în SecretStore"] = "OK"
        try:
            with open(self.binding.database, "rb") as f:
                if key.encode() in f.read():
                    raise AssociationError("Cheia a fost găsită în clar în baza locală — asociere refuzată.", "verify")
        except FileNotFoundError:
            pass
        checks["Cheia nu apare în SQLite"] = "OK"
        rcp = self._rcp()
        try:
            try:
                row = bs.ping(self.binding, rcp, self.store, self.inst)
            except (bs.BootstrapError, bs.Crash, cc.ControlError) as exc:
                raise AssociationError("Testul legăturii a eșuat: " + _friendly(exc), "verify") from None
        finally:
            rcp.close()
        checks["Autentificare pe server (legătură + epocă)"] = "OK"
        if self.local.sync_state not in bs.SYNC_ACTIVE_STATES:
            raise AssociationError(f"Starea locală ({self.local.sync_state}) nu permite sincronizarea.", "verify")
        checks["Stare locală BOUND"] = "OK"
        self.local.clear(SET_UNVERIFIED, SET_PENDING_PLAN)
        return {"binding_uid": row["binding_uid"], "checks": checks, "sync_enabled": True,
                "message": "Asociere finalizată și verificată. Sincronizarea este activă."}

    # ------------------------------------------------------------------ întrerupere / reluare / anulare
    def incomplete(self) -> Optional[Dict[str, Any]]:
        info = bs.incomplete_bootstrap(self.binding)
        if info:
            return info
        run = None
        pend = self.local.get(SET_PENDING_PLAN)
        if pend:
            try:
                run = self.local.run_get(json.loads(pend)["bootstrap_id"])
            except (ValueError, KeyError):
                run = None
        if run and run["state"] == "planned":
            return {"bootstrap_id": run["bootstrap_id"], "kind": "A", "state": "planned", "phase": "planned", "holder_alive": False, "actions": ["resume", "rollback"]}
        if self.local.get(SET_UNVERIFIED) == "1" and self.local.sync_state in bs.SYNC_ACTIVE_STATES:
            return {"bootstrap_id": "", "kind": "A", "state": "unverified", "phase": "verify", "holder_alive": False, "actions": ["verify", "rollback"]}
        return None

    def resume(self, code: str = "") -> Dict[str, Any]:
        """Reia asocierea întreruptă: cu cheia deja salvată nu mai e nevoie de cod; altfel e nevoie de un cod valid (cel vechi poate fi expirat)."""
        info = self.incomplete()
        if not info:
            raise AssociationError("Nu există nicio asociere întreruptă.", "none")
        if info["state"] == "unverified":
            return {"verification": self.verify()}
        pend = self.local.get(SET_PENDING_PLAN)
        if not pend:
            raise AssociationError("Planul întrerupt nu mai este disponibil; anulează asocierea și începe una nouă.", "plan")
        plan = json.loads(pend)
        ref = self.local.get("cloud_secret_ref")
        if not (ref and self.store.get(ref)) and not (code or "").strip():
            raise AssociationError("Pentru reluare introdu un cod de asociere valid (cel vechi a putut expira).", "code")
        return self.execute(plan, code or "-", True)

    def abort(self) -> Dict[str, Any]:
        info = self.incomplete()
        rcp = self._rcp()
        try:
            if info and info["state"] == "unverified":
                bs.unbind(self.binding, rcp, self.store, "main-local")
            elif info and info.get("bootstrap_id"):
                try:
                    bs.abort_flow(self.binding, rcp, self.store, info["bootstrap_id"], self.inst)
                except cc.BootstrapLocked:
                    raise AssociationError("Asocierea rulează încă într-un alt proces; închide-l sau așteaptă.", "locked") from None
            self.local.clear(SET_PENDING_PLAN, SET_UNVERIFIED)
        finally:
            rcp.close()
        return {"message": "Asocierea a fost anulată; registrul local este din nou neasociat (datele locale nu au fost modificate)."}

    def disassociate(self) -> Dict[str, Any]:
        """Dezasociere după BOUND: revocă legătura în cloud (doar cu cheia proprie), șterge cheia și setările de legare."""
        ref = self.local.get("cloud_secret_ref")
        key = self.store.get(ref) if ref else None
        rcp = self._rcp()
        try:
            if key:
                try:
                    rcp.revoke_by_key(key)
                except (cc.ControlError, client.TransportError):
                    pass
            if ref:
                self.store.delete(ref)
            self.local.clear(*bs.BINDING_SETTINGS, SET_UNVERIFIED, SET_PENDING_PLAN)
            self.local.put(sync_state="UNBOUND")
        finally:
            rcp.close()
        return {"message": "Calculatorul a fost dezasociat. Poți genera un cod nou și asocia din nou."}
