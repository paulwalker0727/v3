"""cimitir_bootstrap.py — bootstrap MAIN ↔ Cloud, partea locală (Etapa 4, Pasul 1): identitate, stări, plan_hash, lock, Flux A.

Doar Flux A (MAIN gol ↔ cloud gol) este implementat; B/C/D/E sunt doar clasificate de `decide_flow`.
Cloud-ul este un plan de control (`cimitir_cloud_control.LocalControlPlane`) — în Pasul 1 un SQLite temporar, niciodată o bază reală.
Cheia legăturii trăiește numai în SecretStore; în registru rămâne doar `cloud_secret_ref`.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import cimitir_cloud_control as cc
from cimitir_secretstore import SecretStore

STATES = ("UNBOUND", "PAIRED", "PLANNED", "BOOTSTRAPPING", "BOUND", "BOUND_MEDIA_PENDING", "ABORTED", "REBUILD_REQUIRED")
SYNC_ACTIVE_STATES = ("BOUND", "BOUND_MEDIA_PENDING")
ENTITY_TABLES = ("locuri_de_veci", "concesionari", "contracte", "inhumati", "plati", "amenajari_avize", "documents")
PLAN_TTL = 600
LOCK_NAME = "bootstrap.lock"
BINDING_SETTINGS = ("cloud_binding_uid", "cloud_url", "cloud_secret_ref", "bound_installation_uid", "sync_epoch", "last_change_seq", "cloud_device_uid")


class BootstrapError(Exception):
    pass


class BlockedFlow(BootstrapError):
    def __init__(self, decision: str, message: str):
        super().__init__(message)
        self.decision = decision


class PlanError(BootstrapError):
    pass


class Crash(BaseException):
    """Simulează căderea procesului în teste: nu declanșează rollback (starea rămâne reluabilă)."""


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ identitate
def installation_uid(catalog) -> str:
    uid = catalog.get_state("installation_uid")
    if not uid:
        uid = str(uuid.uuid4())
        catalog.set_state("installation_uid", uid)
    return uid


# ------------------------------------------------------------------ registrul local (direct pe SQLite-ul registrului)
_RUNS = """CREATE TABLE IF NOT EXISTS bootstrap_runs (bootstrap_id TEXT PRIMARY KEY, kind TEXT NOT NULL, plan_hash TEXT NOT NULL, state TEXT NOT NULL,
  checkpoint TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL, finished_at REAL, error TEXT, report TEXT)"""


class LocalRegistry:
    def __init__(self, binding) -> None:
        self.b = binding

    def _c(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.b.database, timeout=15, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute(_RUNS)
        return c

    def get(self, key: str, default: str = "") -> str:
        c = self._c()
        try:
            row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row["value"] if row and row["value"] is not None else default
        finally:
            c.close()

    def put(self, **items: Any) -> None:
        c = self._c()
        try:
            for k, v in items.items():
                c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (k, str(v)))
        finally:
            c.close()

    def clear(self, *keys: str) -> None:
        c = self._c()
        try:
            for k in keys:
                c.execute("DELETE FROM settings WHERE key=?", (k,))
        finally:
            c.close()

    @property
    def sync_state(self) -> str:
        return self.get("sync_state", "UNBOUND")

    def inspect(self) -> Dict[str, Any]:
        c = self._c()
        try:
            names = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            counts, digests = {}, {}
            for t in ENTITY_TABLES:
                if t in names:
                    ids = [r[0] for r in c.execute(f"SELECT rowid FROM {t} ORDER BY rowid")]
                else:
                    ids = []
                counts[t], digests[t] = len(ids), _sha(ids)
            outbox = c.execute("SELECT COUNT(*) FROM cloud_sync_outbox").fetchone()[0] if "cloud_sync_outbox" in names else 0
        finally:
            c.close()
        total = sum(counts.values())
        return {"organization_uid": self.b.organization_uid, "cemetery_uid": self.b.cemetery_uid, "counts": counts, "digests": digests,
                "outbox_pending": outbox, "rows": total, "sync_state": self.sync_state,
                "classification": "nonempty" if total else ("metadata_only" if outbox else "empty")}

    # -- bootstrap_runs locale
    def run_get(self, bootstrap_id: str) -> Optional[Dict[str, Any]]:
        c = self._c()
        try:
            r = c.execute("SELECT * FROM bootstrap_runs WHERE bootstrap_id=?", (bootstrap_id,)).fetchone()
            return dict(r) if r else None
        finally:
            c.close()

    def run_insert(self, bootstrap_id: str, kind: str, plan_hash: str) -> None:
        c = self._c()
        try:
            c.execute("INSERT INTO bootstrap_runs (bootstrap_id, kind, plan_hash, state, created_at) VALUES (?,?,?,?,?)",
                      (bootstrap_id, kind, plan_hash, "planned", time.time()))
        finally:
            c.close()

    def run_update(self, bootstrap_id: str, state: Optional[str] = None, phase: Optional[str] = None, error: Optional[str] = None,
                   report: Optional[Dict[str, Any]] = None) -> None:
        c = self._c()
        try:
            r = c.execute("SELECT checkpoint FROM bootstrap_runs WHERE bootstrap_id=?", (bootstrap_id,)).fetchone()
            cp = json.loads(r["checkpoint"]) if r else {}
            if phase:
                cp["phase"] = phase
            done = state in ("completed", "failed", "rolled_back")
            c.execute("UPDATE bootstrap_runs SET state=COALESCE(?,state), checkpoint=?, error=COALESCE(?,error), finished_at=?, report=COALESCE(?,report) "
                      "WHERE bootstrap_id=?", (state, json.dumps(cp), error, time.time() if done else None,
                                              json.dumps(report, sort_keys=True) if report is not None else None, bootstrap_id))
        finally:
            c.close()

    def phase(self, bootstrap_id: str) -> str:
        r = self.run_get(bootstrap_id)
        return json.loads(r["checkpoint"]).get("phase", "") if r else ""


# ------------------------------------------------------------------ lock local
def _pid_alive(pid: int) -> bool:
    """Informativ (afișare/diagnostic). Autoritatea pentru excludere este lock-ul de SO (`acquire_lock`), nu PID-ul."""
    if pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover - os.kill(pid, 0) ar TERMINA procesul pe Windows
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5  # ACCESS_DENIED => procesul există
        try:
            code = wintypes.DWORD()
            return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    try:  # zombie (terminat, încă nepreluat de părinte) = mort
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat[stat.rindex(")") + 2:][:1] != "Z"
    except (OSError, ValueError):
        return True


_LOCK_BYTE = 1 << 20  # Windows: regiunea blocată e departe de conținut, ca informația să rămână citibilă de alte procese
_HELD: Dict[str, Any] = {}  # cale -> (fd, bootstrap_id): lock-urile ținute de ACEST proces


def _os_lock(fd: int) -> bool:
    try:
        if os.name == "nt":  # pragma: no cover - doar Windows
            import msvcrt
            os.lseek(fd, _LOCK_BYTE, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            os.lseek(fd, 0, os.SEEK_SET)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _os_unlock(fd: int) -> None:
    try:
        if os.name == "nt":  # pragma: no cover - doar Windows
            import msvcrt
            os.lseek(fd, _LOCK_BYTE, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


def acquire_lock(binding, bootstrap_id: str) -> Path:
    """Lock EXCLUSIV între procese: lock de SO pe `bootstrap.lock`, eliberat automat de SO la moartea procesului (nu depinde de PID).
    Fișierul nu se șterge niciodată (evită cursa unlink/flock); conținutul JSON (pid, bootstrap_id) e doar informativ."""
    p = binding.root / LOCK_NAME
    key = str(p)
    held = _HELD.get(key)
    if held:
        if held[1] == bootstrap_id:
            return p  # re-intrare în același proces pentru același bootstrap
        raise cc.BootstrapLocked("un alt bootstrap rulează pe acest registru")
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_RDWR | os.O_CREAT, 0o600)
    if not _os_lock(fd):
        os.close(fd)
        raise cc.BootstrapLocked("un alt bootstrap rulează pe acest registru")
    os.ftruncate(fd, 0)
    os.write(fd, json.dumps({"pid": os.getpid(), "bootstrap_id": bootstrap_id, "ts": time.time()}).encode("utf-8"))
    _HELD[key] = (fd, bootstrap_id)
    return p


def release_lock(binding) -> None:
    key = str(binding.root / LOCK_NAME)
    held = _HELD.pop(key, None)
    if held:
        try:
            os.ftruncate(held[0], 0)
        except OSError:
            pass
        _os_unlock(held[0])
        os.close(held[0])


def lock_info(binding) -> Optional[Dict[str, Any]]:
    """Ultimul deținător înregistrat (informativ; poate fi un proces mort)."""
    p = binding.root / LOCK_NAME
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() and p.stat().st_size else None
    except (OSError, ValueError):
        return None


def lock_held(binding) -> bool:
    """Adevărat dacă un proces VIU (inclusiv acesta) ține lock-ul."""
    p = binding.root / LOCK_NAME
    if str(p) in _HELD:
        return True
    if not p.exists():
        return False
    fd = os.open(str(p), os.O_RDWR)
    try:
        if _os_lock(fd):
            _os_unlock(fd)
            return False
        return True
    finally:
        os.close(fd)


def incomplete_bootstrap(binding) -> Optional[Dict[str, Any]]:
    """Detectează un bootstrap rămas `running` (proces căzut sau încă activ). Acțiuni oferite: resume / rollback (doar dacă nu rulează)."""
    c = LocalRegistry(binding)._c()
    try:
        row = c.execute("SELECT * FROM bootstrap_runs WHERE state IN ('running','verifying') ORDER BY created_at DESC LIMIT 1").fetchone()
    finally:
        c.close()
    if not row:
        return None
    alive = lock_held(binding)
    info = lock_info(binding) or {}
    return {"bootstrap_id": row["bootstrap_id"], "kind": row["kind"], "state": row["state"], "phase": json.loads(row["checkpoint"]).get("phase", ""),
            "plan_hash": row["plan_hash"], "holder_alive": alive, "holder_pid": info.get("pid"),
            "actions": [] if alive else ["resume", "rollback"]}


# ------------------------------------------------------------------ matricea de decizie (§1.4)
def decide_flow(local_class: str, cloud_class: str, sync_state: str = "UNBOUND", digests_equal: bool = False,
                cloud_status: Optional[str] = None) -> str:
    if sync_state == "REBUILD_REQUIRED":
        return "REBUILD"
    l_empty, c_empty = local_class == "empty", cloud_class == "empty"
    if sync_state in SYNC_ACTIVE_STATES and not l_empty and c_empty:
        return "BLOCKED_ALARM"
    if sync_state in SYNC_ACTIVE_STATES:
        return "ALREADY_BOUND"
    if l_empty and c_empty:
        # ACTIVE + gol NU este „nou”: poate fi un cloud golit accidental. Fail-closed până la aprobarea regulii (ETAPA4 §13.2).
        return "BLOCKED_ACTIVE_EMPTY" if cloud_status == "ACTIVE" else "A"
    if not l_empty and c_empty:
        return "B"
    if l_empty and not c_empty:
        return "C"
    return "LINK" if digests_equal else "BLOCKED"


# ------------------------------------------------------------------ plan (preflight + plan_hash)
def _plan_hash(fields: Dict[str, Any]) -> str:
    return _sha(fields)


def _app_versions() -> Dict[str, str]:
    import cimitir_config
    return {"app_version": cimitir_config.app_version(), "schema_version": "local-v1"}


def _preflight_a(binding, cp: cc.LocalControlPlane, code: str, inst: str, clock: Callable[[], float]) -> Dict[str, Any]:
    local = LocalRegistry(binding)
    li = local.inspect()
    cloud = cp.preflight_for_code(code, inst, binding.cemetery_uid)
    if cloud["organization_uid"] != binding.organization_uid:
        raise BlockedFlow("SCOPE", "codul aparține altei organizații decât registrul local")
    if cloud["status"] == "ARCHIVED":
        raise BlockedFlow("BLOCKED", "cimitirul este arhivat în cloud (UID-ul nu se reutilizează)")
    incomplete = incomplete_bootstrap(binding)
    if incomplete:
        raise BlockedFlow("INCOMPLETE_BOOTSTRAP", f"bootstrap incomplet {incomplete['bootstrap_id']} (faza {incomplete['phase']!r}): reia-l sau anulează-l înainte de un plan nou")
    decision = decide_flow(li["classification"], cloud["classification"], li["sync_state"], cloud_status=cloud["status"])
    if decision == "BLOCKED_ACTIVE_EMPTY":
        raise BlockedFlow(decision, "cimitirul este ACTIVE în cloud dar gol: nu se tratează automat ca „nou” (posibilă ștergere în cloud); "
                                    "regula propusă așteaptă aprobarea (ETAPA4 §13.2)")
    if decision != "A":
        raise BlockedFlow(decision, f"Flux A indisponibil: decizia pentru starea curentă este {decision}")
    return {"local": li, "cloud": cloud}


def _hash_fields(binding, inst: str, pre: Dict[str, Any]) -> Dict[str, Any]:
    return {"kind": "A", "organization_uid": binding.organization_uid, "cemetery_uid": binding.cemetery_uid,
            "source_digest": pre["local"]["digests"], "target": {"exists": pre["cloud"]["exists"], "status": pre["cloud"]["status"],
                                                                 "rows": pre["cloud"]["rows"], "tombstones": pre["cloud"]["tombstones"]},
            "epoch": pre["cloud"]["data_epoch"] or 1, "installation_uid": inst, **_app_versions()}


def plan_flow_a(binding, cp: cc.LocalControlPlane, code: str, inst: str, cemetery_name: str = "",
                clock: Callable[[], float] = time.time) -> Dict[str, Any]:
    """Preflight + plan. Nu modifică nimic în afara unui rând local `planned` (stare PLANNED)."""
    local = LocalRegistry(binding)
    pre = _preflight_a(binding, cp, code, inst, clock)
    fields = _hash_fields(binding, inst, pre)
    plan = {"bootstrap_id": str(uuid.uuid4()), "kind": "A", "plan_hash": _plan_hash(fields), "fields": fields, "created_at": clock(),
            "expires_at": clock() + PLAN_TTL, "cemetery_name": cemetery_name,
            "summary": f"Creez organizația și cimitirul în cloud cu aceleași identificatoare ({binding.organization_uid} / {binding.cemetery_uid})"}
    local.run_insert(plan["bootstrap_id"], "A", plan["plan_hash"])
    local.put(sync_state="PLANNED")
    return plan


def _unbind_if_idle(local: "LocalRegistry") -> None:
    """Revine la UNBOUND doar dacă starea e a unui plan (nu atinge un bootstrap activ sau un registru legat)."""
    if local.sync_state in ("PLANNED", "BOOTSTRAPPING", "PAIRED", "ABORTED", "UNBOUND"):
        local.put(sync_state="UNBOUND")


def cancel_plan(binding, bootstrap_id: str) -> None:
    local = LocalRegistry(binding)
    r = local.run_get(bootstrap_id)
    if r and r["state"] == "planned":
        local.run_update(bootstrap_id, state="rolled_back", error="anulat")
        if not incomplete_bootstrap(binding):
            _unbind_if_idle(local)


# ------------------------------------------------------------------ executarea Flux A
def secret_name(binding_uid: str) -> str:
    return f"binding/{binding_uid}"


def execute_flow_a(binding, cp: cc.LocalControlPlane, store: SecretStore, plan: Dict[str, Any], confirm_hash: str, code: str, inst: str,
                   cloud_url: str = "local://control-plane", clock: Callable[[], float] = time.time,
                   hook: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Confirmă (plan_hash) și execută. Reluabil după `Crash`/moartea procesului; orice altă eroare => rollback complet (local + cloud + secret).
    Lock-ul de SO se ia ÎNAINTE de orice verificare de stare; refuzul lock-ului nu atinge nimic din starea comună."""
    local = LocalRegistry(binding)
    bid = plan["bootstrap_id"]
    run = local.run_get(bid)
    if run is None or run["plan_hash"] != plan["plan_hash"]:
        raise PlanError("plan necunoscut local")
    if confirm_hash != plan["plan_hash"]:
        raise PlanError("confirmarea nu corespunde planului (plan_hash)")
    if run["state"] == "completed":
        return json.loads(run["report"])
    if run["state"] in ("failed", "rolled_back"):
        raise PlanError(f"planul este {run['state']}; creează unul nou")
    try:
        acquire_lock(binding, bid)
    except cc.BootstrapLocked:
        if run["state"] == "planned":
            local.run_update(bid, state="rolled_back", error="refuzat: lock ținut de alt proces")
        raise
    fresh = run["state"] == "planned"
    if fresh:  # reverificare SUB lock: între plan și acum alt proces poate fi legat/început un bootstrap
        try:
            if clock() > plan["expires_at"]:
                raise PlanError("planul a expirat; refă preflight-ul")
            pre = _preflight_a(binding, cp, code, inst, clock)
            if _plan_hash(_hash_fields(binding, inst, pre)) != plan["plan_hash"]:
                raise PlanError("starea s-a schimbat între preflight și confirmare (TOCTOU)")
        except (PlanError, BlockedFlow, cc.ControlError) as exc:
            local.run_update(bid, state="rolled_back", error=str(exc)[:500])
            if not incomplete_bootstrap(binding):
                _unbind_if_idle(local)
            release_lock(binding)
            raise
    key: Optional[str] = None
    try:
        tick = hook or (lambda phase: None)
        local.put(sync_state="BOOTSTRAPPING")
        local.run_update(bid, state="running", phase="begin")
        tick("begin")
        ref = local.get("cloud_secret_ref")
        key = store.get(ref) if ref else None
        if key:
            cp.resume_run(key, bid, plan["plan_hash"])
        else:
            import cimitir_plan_guard as pg
            cp.begin_run(code, inst, bid, "A", binding.cemetery_uid, plan["plan_hash"], plan=pg.claim_for(plan))
        tick("after_begin")
        if not key:
            res = cp.redeem_code(code, inst, plan.get("device_name") or "MAIN", binding.cemetery_uid, plan.get("cemetery_name", ""), bootstrap_id=bid)
            key = res["binding_key"]
            tick("after_redeem")
            store.set(secret_name(res["binding_uid"]), key)
            tick("after_secret")
            local.put(cloud_binding_uid=res["binding_uid"], cloud_device_uid=res["device_uid"], cloud_url=cloud_url,
                      cloud_secret_ref=secret_name(res["binding_uid"]), sync_epoch=res["sync_epoch"], last_change_seq=0)
            local.run_update(bid, phase="redeemed")
            local.put(sync_state="PAIRED")
            local.put(sync_state="BOOTSTRAPPING")
        tick("redeemed")
        crun = cp.get_run(bid)
        if crun["state"] != "completed":
            cp.mark_verifying(key, bid)
        local.run_update(bid, state="running", phase="verifying")
        tick("verifying")
        li = local.inspect()
        cloud = cp.verify_empty(key, bid)
        auth = cp.authenticate(key, binding.cemetery_uid)
        if li["rows"] != 0 or cloud["rows"] != 0 or cloud["tombstones"] != 0:
            raise BootstrapError("verificare finală eșuată: sursa sau ținta nu mai sunt goale")
        if auth["sync_epoch"] != int(local.get("sync_epoch", "1") or 1) or cloud["data_epoch"] != auth["sync_epoch"]:
            raise BootstrapError("epocă inconsistentă")
        tick("before_complete")
        if crun["state"] != "completed":
            cp.complete_run(key, bid)
        tick("after_cloud_complete")
        report = {"bootstrap_id": bid, "kind": "A", "plan_hash": plan["plan_hash"], "organization_uid": binding.organization_uid,
                  "cemetery_uid": binding.cemetery_uid, "binding_uid": auth["binding_uid"], "sync_epoch": auth["sync_epoch"],
                  "local_rows": li["rows"], "cloud_rows": cloud["rows"], "cloud_tombstones": cloud["tombstones"], "result": "completed"}
        local.put(bound_installation_uid=inst, sync_state="BOUND")
        local.run_update(bid, state="completed", phase="done", report=report)
        release_lock(binding)
        return report
    except Crash:
        raise
    except BaseException as exc:
        _abort(binding, cp, store, bid, inst, str(exc))
        raise


def _abort(binding, cp: cc.LocalControlPlane, store: SecretStore, bootstrap_id: str, inst: str, error: str) -> None:
    """Rollback complet al bootstrap-ului CURENT (singurul `running`, sub lock): cloud + secret + setări de legare + stare."""
    local = LocalRegistry(binding)
    ref = local.get("cloud_secret_ref")
    if not ref:  # cădere între store.set și salvarea referinței: legătura creată de acest run în cloud dă numele secretului orfan
        try:
            buid = cp.get_run(bootstrap_id).get("binding_uid")
            ref = secret_name(buid) if buid else ""
        except cc.ControlError:
            ref = ""
    key = store.get(ref) if ref else None
    try:
        if key:
            try:
                cp.rollback_run(key, bootstrap_id)
            except cc.ConflictError:  # run deja `completed` în cloud (cădere după completare): doar revocăm legătura
                cp.revoke_by_key(key)
        else:
            cp.mark_failed_unauthenticated(bootstrap_id, inst, error)
    except cc.ControlError:
        pass
    if ref:
        store.delete(ref)
    local.clear(*BINDING_SETTINGS)
    local.put(sync_state="UNBOUND")
    local.run_update(bootstrap_id, state="rolled_back", error=error[:500])
    release_lock(binding)


def abort_flow(binding, cp: cc.LocalControlPlane, store: SecretStore, bootstrap_id: str, inst: str) -> None:
    """Rollback explicit al unui bootstrap neterminat (ex. după o cădere). Refuzat cât timp un proces viu îl rulează."""
    local = LocalRegistry(binding)
    r = local.run_get(bootstrap_id)
    if not r or r["state"] not in ("planned", "running"):
        return
    if r["state"] == "running":
        acquire_lock(binding, bootstrap_id)  # BootstrapLocked dacă o rulare vie îl ține
    _abort(binding, cp, store, bootstrap_id, inst, "abandonat de utilizator")


# ------------------------------------------------------------------ după legare
def sync_allowed(binding, inst: str) -> bool:
    """Sincronizarea pornește doar în BOUND*; o clonă (alt installation_uid) => REBUILD_REQUIRED, editarea locală continuă."""
    local = LocalRegistry(binding)
    st = local.sync_state
    if st not in SYNC_ACTIVE_STATES:
        return False
    if local.get("bound_installation_uid") != inst:
        local.put(sync_state="REBUILD_REQUIRED")
        return False
    return True


def ping(binding, cp: cc.LocalControlPlane, store: SecretStore, inst: str) -> Dict[str, Any]:
    if not sync_allowed(binding, inst):
        raise BootstrapError(f"sincronizare indisponibilă ({LocalRegistry(binding).sync_state})")
    local = LocalRegistry(binding)
    key = store.get(local.get("cloud_secret_ref"))
    if not key:
        raise BootstrapError("cheia legăturii lipsește din SecretStore")
    row = cp.authenticate(key, binding.cemetery_uid)
    if row["sync_epoch"] != int(local.get("sync_epoch", "1") or 1):
        local.put(sync_state="REBUILD_REQUIRED")
        raise BootstrapError("epoch_mismatch")
    return {"binding_uid": row["binding_uid"], "sync_epoch": row["sync_epoch"]}


def unbind(binding, cp: cc.LocalControlPlane, store: SecretStore, actor: str) -> None:
    """Dezlegare după BOUND (fără pierderi de date): revocă legătura în cloud, șterge cheia și setările de legare."""
    local = LocalRegistry(binding)
    buid, ref = local.get("cloud_binding_uid"), local.get("cloud_secret_ref")
    if buid:
        cp.revoke_binding(actor, binding.organization_uid, buid)
    if ref:
        store.delete(ref)
    local.clear(*BINDING_SETTINGS)
    local.put(sync_state="UNBOUND")
