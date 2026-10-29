"""cimitir_cloud_associate.py — fluxul „Asociază acest calculator” (MAIN -> Cloud), fără comenzi/SQL/Python manual. Logică fără UI (testabilă); fereastra Qt doar o apelează.

Pași (fiecare întoarce un rezumat afișabil; erorile sunt `AssociationError` cu mesaj în română, fără secrete):
  1. check_api(url)        — HTTPS/HTTP reachability, /health (DB+storage), /bootstrap/info (compatibilitate client↔server)
  2. verify_code(code)     — preflight real pe server: codul, drepturile emitentului, organizația/cimitirul, starea cloud (nu consumă codul)
  3. plan(code)            — alege automat fluxul din starea REALĂ (local/cloud): A = registru local gol + cloud gol; B = registru local cu date + cloud gol
                             (prima încărcare). Rezumat + plan_hash; nimic nu se scrie în cloud. Orice altă combinație este refuzată cu un mesaj clar.
  4. execute(plan, code)   — după confirmarea explicită a utilizatorului: Flux A (begin_run → redeem → cheia în SecretStore (DPAPI) → verify_empty → complete)
                             sau Flux B (… → snapshot → staging → media → verificare → publicare atomică → finalizare locală)
  5. verify()              — test automat al legăturii (cheie lizibilă din SecretStore, authenticate, cheia absentă din SQLite) ; abia apoi se activează sincronizarea
Dacă procesul e întrerupt, `incomplete()` arată starea, iar `resume()` / `abort()` o reiau sau o anulează în siguranță (rollback local + cloud + secret).
Protecțiile din cimitir_bootstrap (BLOCKED_ACTIVE_EMPTY, verify_empty, plan_hash/TOCTOU) NU sunt ocolite.
"""
from __future__ import annotations

import json
import os
import re
import secrets
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

import cimitir_bootstrap as bs
import cimitir_cloud_admin_client as adc
import cimitir_cloud_client as client
import cimitir_cloud_control as cc
import cimitir_flow_b as fb
import cimitir_manifest as mf
import cimitir_plan_guard as pg
import cimitir_preflight as pf
import cimitir_transfer_contract as tc

SET_UNVERIFIED = "cloud_assoc_unverified"      # "1" = sincronizarea rămâne blocată până trece verify()
SET_PENDING_PLAN = "cloud_assoc_plan"          # planul (fără cod/secrete) păstrat pentru reluare după întrerupere
_BINDING_RE = re.compile(r"^cimitir\.[0-9a-f-]{36}\.[A-Za-z0-9_-]{32,}$")


class AssociationError(Exception):
    def __init__(self, message: str, kind: str = "error"):
        super().__init__(message)
        self.kind = kind


def http_timeout() -> float:
    """Timeout HTTP către Cloud (s): 20 implicit (PostgreSQL/Storage la distanță cer mai mult de 5 s); CIMITIR_CLOUD_HTTP_TIMEOUT îl poate schimba (3–120)."""
    try:
        return min(max(float(os.environ.get("CIMITIR_CLOUD_HTTP_TIMEOUT", "20")), 3.0), 120.0)
    except ValueError:
        return 20.0


def normalize_url(raw: str) -> str:
    u = (raw or "").strip().rstrip("/")
    if u and "://" not in u:
        u = "https://" + u
    p = urlparse(u)
    if p.scheme not in ("https", "http") or not p.hostname:
        raise AssociationError("URL Cloud invalid (ex.: https://cloud.exemplu.ro)", "url")
    if p.scheme == "http" and p.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise AssociationError("Conexiunea către Cloud trebuie să fie HTTPS (HTTP e permis doar pe localhost).", "url")
    host = (p.hostname or "").lower()
    if host.endswith((".supabase.co", ".supabase.com", ".supabase.in")):
        raise AssociationError("Aceasta este adresa Supabase (baza de date), nu adresa backendului Cloud. Introdu adresa API a backendului (ex.: https://cloud.parohie.ro sau http://127.0.0.1:8086).", "url")
    if p.path not in ("", "/") or p.query or p.fragment or p.username:
        raise AssociationError("Introdu doar adresa serverului, fără cale sau parametri.", "url")
    return f"{p.scheme}://{p.netloc}"


_FLOW_MESSAGES = {
    "C": "Cloud-ul conține deja date, iar registrul local este gol (descărcarea din Cloud, Flux C, nu este disponibilă în această versiune). Nimic nu a fost modificat.",
    "LINK": "Registrul local și Cloud-ul conțin date. Nu se face îmbinare automată: sunt necesare o verificare și o aprobare separate. Nimic nu a fost modificat.",
    "BLOCKED": "Registrul local și Cloud-ul conțin date diferite. Nu se face îmbinare automată. Nimic nu a fost modificat.",
    "BLOCKED_ALARM": "Registrul este legat, are date locale, iar Cloud-ul pare gol (posibilă ștergere accidentală). Sincronizarea rămâne oprită; nimic nu a fost modificat.",
    "ALREADY_BOUND": "Acest registru este deja asociat cu un Cloud. Dezasociază-l mai întâi dacă vrei să-l asociezi din nou.",
    "REBUILD": "Registrul cere reconstruire (copie a altei instalări). Nu se asociază automat; nimic nu a fost modificat.",
    "REVIEW_METADATA_ONLY": "Registrul local sau Cloud-ul conțin doar rămășițe de sincronizare (coadă/conflicte), fără date. Se revizuiește manual; nimic nu a fost modificat.",
}
_FRIENDLY_PASSTHROUGH = (bs.BlockedFlow, client.ServerIncompatible, pf.PreflightError, tc.TransferBlocked, tc.ContractError, fb.VerificationFailed)


def _friendly(exc: BaseException) -> str:
    if isinstance(exc, client.ServerIncompatible):
        return str(exc)
    if isinstance(exc, client.TransportError):
        return "Serverul Cloud nu răspunde sau conexiunea a fost întreruptă. Verifică adresa și internetul."
    if isinstance(exc, cc.CodeError):
        return "Codul este invalid, expirat, revocat sau deja folosit. Cere un cod nou administratorului."
    if isinstance(exc, cc.PermissionDenied):
        return "Emitentul codului nu are dreptul de a asocia acest calculator (este necesar rolul Owner)."
    if isinstance(exc, cc.ScopeError):
        return "Codul nu este valabil pentru acest cimitir/organizație sau este invalid/expirat."
    if isinstance(exc, cc.BootstrapLocked):
        return "O altă asociere este în curs pe acest cimitir. Așteaptă sau anulează-o."
    if isinstance(exc, (bs.PlanError, pg.PlanRejected, pg.PlanExpired, pg.TargetDrift, pg.PlanBlocked)):
        return f"Planul nu mai este valid: {exc}"
    if isinstance(exc, pf.PlanInvalidated):
        return "Datele locale sau cele din Cloud s-au schimbat după generarea planului; verifică din nou codul."
    if isinstance(exc, _FRIENDLY_PASSTHROUGH):
        return str(exc)
    return f"Operația a eșuat ({type(exc).__name__})."


class Association:
    def __init__(self, binding, store, inst: str, url: str, device_name: str = "MAIN", rcp_factory: Optional[Callable[[str], Any]] = None):
        self.binding, self.store, self.inst, self.device_name = binding, store, inst, device_name or "MAIN"
        self.url = normalize_url(url)
        self._factory = rcp_factory or (lambda base: client.RemoteControlPlane(base, retries=2, timeout=http_timeout()))
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
    def _route(self, rcp, code: str) -> Dict[str, Any]:
        """Citire: starea cloud (prin cod) + starea locală => fluxul ales. Nu consumă codul și nu scrie nimic."""
        try:
            cloud = rcp.preflight_for_code(code, self.inst, self.binding.cemetery_uid)
        except (bs.Crash, cc.ControlError, pg.PlanRejected) as exc:
            raise AssociationError(_friendly(exc), "code") from None
        if cloud["organization_uid"] != self.binding.organization_uid:
            raise AssociationError("Codul aparține altei organizații decât registrul local selectat.", "scope")
        li = self.local.inspect()
        lclass = li["classification"]
        if lclass == "empty":                              # inspect() vede doar tabelele de entități; manifestul vede și media/coada/conflictele
            lclass = mf.local_manifest(self.binding)["classification"].lower()
        decision = bs.decide_flow(lclass, cloud["classification"], li["sync_state"], cloud_status=cloud.get("status"))
        flow = decision if decision in ("A", "B") else None
        return {"cloud": cloud, "local": li, "local_class": lclass, "decision": decision, "flow": flow}

    def verify_code(self, code: str) -> Dict[str, Any]:
        self._need_code(code)
        rcp = self._rcp()
        try:
            r = self._route(rcp, code)
        finally:
            rcp.close()
        cloud = r["cloud"]
        return {"organization_uid": cloud["organization_uid"], "cemetery_uid": self.binding.cemetery_uid, "cemetery_status": cloud.get("status"),
                "cloud_exists": cloud.get("exists"), "cloud_classification": cloud.get("classification"), "cloud_rows": cloud.get("rows"),
                "local_classification": r["local_class"], "flow": r["flow"], "decision": r["decision"],
                "message": "Cod valid; drepturile emitentului au fost verificate."}

    @staticmethod
    def _need_code(code: str) -> None:
        if not (code or "").strip():
            raise AssociationError("Introdu codul temporar de asociere.", "code")

    # ------------------------------------------------------------------ 3
    def plan(self, code: str, cemetery_name: str = "") -> Dict[str, Any]:
        """Alege Flux A (local gol + cloud gol) sau Flux B (local cu date + cloud gol) din starea reală; restul combinațiilor sunt refuzate explicit."""
        self._need_code(code)
        rcp = self._rcp()
        try:
            r = self._route(rcp, code)
            if r["flow"] is None:
                msg = _FLOW_MESSAGES.get(r["decision"]) or {
                    "BLOCKED_ACTIVE_EMPTY": "Cimitirul este ACTIVE în Cloud dar gol: nu se tratează automat ca nou (posibilă ștergere în Cloud). Nimic nu a fost modificat."}.get(
                    r["decision"], f"Asocierea nu este permisă pentru starea curentă ({r['decision']}). Nimic nu a fost modificat.")
                raise AssociationError(msg, "blocked")
            if r["flow"] == "A":
                plan = bs.plan_flow_a(self.binding, rcp, code, self.inst, cemetery_name=cemetery_name)
            else:
                plan = fb.plan_flow_b(self.binding, rcp, None, self.inst)
                plan["cemetery_name"] = cemetery_name
        except AssociationError:
            raise
        except (bs.BlockedFlow, bs.PlanError, bs.Crash, cc.ControlError, pg.PlanRejected, pf.PreflightError, tc.TransferBlocked) as exc:
            reason = _FLOW_MESSAGES.get(getattr(exc, "decision", ""))
            raise AssociationError(reason or _friendly(exc), "blocked" if isinstance(exc, bs.BlockedFlow) else "plan") from None
        finally:
            rcp.close()
        plan["device_name"] = self.device_name
        self.local.put(**{SET_PENDING_PLAN: json.dumps(plan)})
        return {"plan": plan, "plan_hash": plan["plan_hash"], "flow": plan["kind"], "summary": self._summary(plan)}

    def _summary(self, plan: Dict[str, Any]) -> Dict[str, str]:
        li = self.local.inspect()
        out = {"Server": self.url, "Organizație (UID)": self.binding.organization_uid, "Cimitir (UID)": self.binding.cemetery_uid,
               "Registru local": f"{li['classification']} · {li['rows']} rânduri · {li['outbox_pending']} în așteptare"}
        if plan["kind"] == "B":
            lm = plan["local_manifest"]
            counts = ", ".join(f"{e}: {v['count']}" for e, v in lm["entities"].items() if v["count"])
            out["Operațiune"] = ("Prima încărcare a registrului local în Cloud (Flux B): datele se copiază în zonă de staging, se verifică integral (număr, amprente, media), "
                                 "apoi se publică atomic. Identitățile (UID) locale se păstrează. Nu se creează organizații sau cimitire noi. Cheia definitivă se salvează în SecretStore")
            out["Date de încărcat"] = counts or "—"
            out["Fișiere media"] = f"{lm['media']['count']} ({lm['media']['bytes']} octeți)" + (f" · LIPSĂ pe disc: {len(plan['media_missing'])}" if plan.get("media_missing") else "")
        else:
            out["Operațiune"] = "Asociere nouă MAIN (Flux A): se creează legătura și cheia definitivă, salvată în SecretStore (nu în SQLite/loguri)"
        out["Dispozitiv"] = self.device_name
        out["Plan expiră în"] = f"{int(plan['expires_at'] - plan['created_at'])} s"
        return out

    def cancel_plan(self, plan: Dict[str, Any]) -> None:
        bs.cancel_plan(self.binding, plan["bootstrap_id"])
        self.local.clear(SET_PENDING_PLAN)

    # ------------------------------------------------------------------ 4 + 5
    def execute(self, plan: Dict[str, Any], code: str, confirmed: bool, accept_missing_media: bool = False) -> Dict[str, Any]:
        if confirmed is not True:
            raise AssociationError("Asocierea necesită confirmarea explicită a utilizatorului.", "confirm")
        self._need_code(code)
        self.local.put(**{SET_UNVERIFIED: "1"})                       # sync blocat până trece verificarea completă
        rcp = self._rcp()
        try:
            try:
                if plan.get("kind", "A") == "B":
                    if hasattr(rcp, "pairing_code"):
                        rcp.pairing_code = code                       # `can` (capabilitate bootstrap) rulează înainte de begin_run
                    report = fb.execute_flow_b(self.binding, rcp, self.store, None, plan, plan["plan_hash"], code, self.inst, cloud_url=self.url,
                                               accept_missing_media=accept_missing_media)
                else:
                    report = bs.execute_flow_a(self.binding, rcp, self.store, plan, plan["plan_hash"], code, self.inst, cloud_url=self.url)
            except bs.Crash as exc:
                raise AssociationError(_friendly(exc) + " Starea e salvată: folosește „Reia” sau „Anulează asocierea”.", "interrupted") from None
            except bs.BlockedFlow as exc:
                if exc.decision == "MEDIA_MISSING":                   # nimic modificat; planul rămâne valid pentru confirmarea explicită
                    self.local.clear(SET_UNVERIFIED)
                    raise AssociationError(str(exc), "media_missing") from None
                self._after_failure()
                raise AssociationError(_friendly(exc), "failed") from None
            except (bs.BootstrapError, bs.PlanError, cc.ControlError, pg.PlanRejected, pf.PreflightError, tc.TransferBlocked) as exc:
                resumable = self._after_failure()
                raise AssociationError(_friendly(exc) + (" Starea e salvată: folosește „Reia” sau „Anulează asocierea”." if resumable else ""), "failed") from None
        finally:
            rcp.close()
        return {"report": report, "verification": self.verify()}

    def _after_failure(self) -> bool:
        """După un eșec: dacă a rămas un bootstrap reluabil (ex. cloud deja publicat) păstrăm planul; altfel curățăm marcajele locale. Întoarce True dacă e reluabil."""
        if bs.incomplete_bootstrap(self.binding):
            return True
        self.local.clear(SET_PENDING_PLAN)
        if self.local.sync_state not in bs.SYNC_ACTIVE_STATES:
            self.local.clear(SET_UNVERIFIED)
        return False

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

    # ------------------------------------------------------------------ asociere automată cu administratorul Cloud (provizionare + cod + asociere)
    @staticmethod
    def _admin_call(fn, *args):
        try:
            return fn(*args)
        except adc.AdminApiError as exc:
            raise AssociationError(str(exc), "admin_" + exc.code.lower()) from None

    def registry_in_cloud(self, admin) -> Dict[str, Any]:
        """Pasul „verifică UID-urile locale și existența registrului în Cloud”: nu scrie nimic. `action`: ready | provision_cemetery | provision_all | forbidden | conflict."""
        return self._admin_call(admin.registry_status, self.binding.organization_uid, self.binding.cemetery_uid)

    def associate_with_admin(self, admin, organization_name: str, cemetery_name: str, confirmed: bool) -> Dict[str, Any]:
        """Circuit complet după confirmarea explicită: (provizionare dacă lipsește, păstrând UID-urile) → cod MAIN → verificare → plan → asociere → verificare → sync activ.
        Nu ocolește nicio protecție: codul rămâne emis de un Owner, iar Flux A/plan_hash/verify_empty rulează neschimbate."""
        if confirmed is not True:
            raise AssociationError("Asocierea necesită confirmarea explicită a utilizatorului.", "confirm")
        steps: Dict[str, Any] = {"api": self.check_api()}
        org, cem = self.binding.organization_uid, self.binding.cemetery_uid
        st = self.registry_in_cloud(admin)
        if st["action"] in ("forbidden", "conflict"):
            raise AssociationError(st["message"], st["action"])
        if st["action"] in ("provision_all", "provision_cemetery"):
            steps["provision"] = self._admin_call(admin.provision, org, organization_name, cem, cemetery_name)
            st = self.registry_in_cloud(admin)
            if st["action"] != "ready":
                raise AssociationError("Provizionarea nu a produs un registru valid în Cloud: " + st["message"], "provision")
        steps["registry"] = {"action": "ready", "organization_uid": org, "cemetery_uid": cem}
        code = self._admin_call(admin.issue_code, org, cem, "main")["code"]
        steps["code"] = self.verify_code(code)
        planned = self.plan(code, cemetery_name)
        res = self.execute(planned["plan"], code, True)
        steps["report"], steps["verification"] = res["report"], res["verification"]
        return steps

    # ------------------------------------------------------------------ cerere de înregistrare (fără parolă de administrator Cloud pe acest calculator)
    SET_REGREQ = "cloud_regreq_id"

    def request_registration(self, organization_name: str, cemetery_name: str, confirmed: bool) -> Dict[str, Any]:
        """MAIN trimite automat UID-urile; administratorul Cloud confirmă în /admin/. Secretul cererii rămâne doar în SecretStore."""
        if confirmed is not True:
            raise AssociationError("Trimiterea cererii necesită confirmarea explicită a utilizatorului.", "confirm")
        self.check_api()
        secret = secrets.token_urlsafe(32)
        cl = adc.AdminClient(self.url)
        try:
            res = self._admin_call(cl.request_registration, secret, self.binding.organization_uid, organization_name, self.binding.cemetery_uid, cemetery_name, self.device_name)
        finally:
            cl.close()
        self.store.set(f"regreq/{res['request_id']}", secret)
        self.local.put(**{self.SET_REGREQ: res["request_id"]})
        return {"request_id": res["request_id"], "short_id": res["short_id"], "message": f"Cerere trimisă (ID {res['short_id']}). Administratorul Cloud o confirmă în /admin/; aplicația continuă automat."}

    def pending_request(self) -> Optional[str]:
        return self.local.get(self.SET_REGREQ) or None

    def cancel_request(self) -> None:
        rid = self.pending_request()
        if rid:
            self.store.delete(f"regreq/{rid}")
            self.local.clear(self.SET_REGREQ)

    def complete_registration(self, cemetery_name: str = "") -> Dict[str, Any]:
        """Interoghează cererea. `pending` ⇒ așteaptă; `approved` ⇒ rulează automat verificarea codului, planul și asocierea (protecțiile Flux A rămân)."""
        rid = self.pending_request()
        if not rid:
            raise AssociationError("Nu există nicio cerere de înregistrare trimisă.", "none")
        secret = self.store.get(f"regreq/{rid}")
        if not secret:
            self.local.clear(self.SET_REGREQ)
            raise AssociationError("Secretul cererii nu mai există în SecretStore; trimite o cerere nouă.", "none")
        cl = adc.AdminClient(self.url)
        try:
            st = self._admin_call(cl.registration_status, rid, secret)
        finally:
            cl.close()
        if st["state"] == "pending":
            return {"state": "pending", "message": "Se așteaptă confirmarea administratorului Cloud în /admin/."}
        if st["state"] in ("rejected", "expired", "consumed") or not st.get("code"):
            self.cancel_request()
            raise AssociationError({"rejected": "Cererea a fost respinsă de administratorul Cloud.", "expired": "Cererea a expirat (24 h); trimite una nouă."}.get(
                st["state"], "Cererea a fost deja folosită; trimite una nouă."), "request_" + st["state"])
        code = st["code"]
        self.verify_code(code)
        planned = self.plan(code, cemetery_name)
        res = self.execute(planned["plan"], code, True)
        self.cancel_request()
        return {"state": "associated", "verification": res["verification"], "message": res["verification"]["message"]}

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
            return {"bootstrap_id": run["bootstrap_id"], "kind": run["kind"], "state": "planned", "phase": "planned", "holder_alive": False, "actions": ["resume", "rollback"]}
        if self.local.get(SET_UNVERIFIED) == "1" and self.local.sync_state in bs.SYNC_ACTIVE_STATES:
            return {"bootstrap_id": "", "kind": "A", "state": "unverified", "phase": "verify", "holder_alive": False, "actions": ["verify", "rollback"]}
        return None

    def resume(self, code: str = "", accept_missing_media: bool = False) -> Dict[str, Any]:
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
        return self.execute(plan, code or "-", True, accept_missing_media)

    def abort(self) -> Dict[str, Any]:
        info = self.incomplete()
        rcp = self._rcp()
        try:
            if info and info["state"] == "unverified":
                bs.unbind(self.binding, rcp, self.store, "main-local")
            elif info and info.get("bootstrap_id"):
                try:
                    if info.get("kind") == "B":
                        ref = self.local.get("cloud_secret_ref")
                        key = self.store.get(ref) if ref else None
                        if key and hasattr(rcp, "use_binding_key"):
                            rcp.use_binding_key(key)                  # curățarea staging/media din cloud se autentifică cu legătura începută
                        fb.abort_flow_b(self.binding, rcp, self.store, None, info["bootstrap_id"], self.inst)
                    else:
                        bs.abort_flow(self.binding, rcp, self.store, info["bootstrap_id"], self.inst)
                except cc.BootstrapLocked:
                    raise AssociationError("Asocierea rulează încă într-un alt proces; închide-l sau așteaptă.", "locked") from None
                except bs.BootstrapError as exc:
                    raise AssociationError(str(exc), "abort") from None
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
