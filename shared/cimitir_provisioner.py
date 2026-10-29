"""cimitir_provisioner.py — orchestratorul provizionării backendului izolat per parohie (serviciul de administrare al OPERATORULUI).

Pași (idempotenți, reluabili): register → link → verify → migrate → secure(rol runtime) → deploy(release, TLS, proces) → health → (administrator inițial în MAIN).
Autorizare: tokenuri de operator (registru propriu). NU licența `.lic`, NU cheia proprietarului instanței. Acțiunile distructive cer rol `owner` + confirmare unică.
Parola ADMINISTRATIVĂ a bazei vine per apel și nu se persistă. Fiecare instanță: parole proprii (seif), port propriu, director propriu, mediu minim de proces.
"""
from __future__ import annotations

import base64
import re
import secrets
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

import cimitir_instance_grant as ig
import cimitir_prov_pg as pgops
import cimitir_prov_runtime as rt
import cimitir_prov_storage as stor
from cimitir_prov_store import ProvisionError, Registry, Vault, redact_text

PROTECTED_REFS = ("txuxkjgftopznvmooexq",)        # = cimitir_test_guard.PROTECTED_PROJECT_REFS (proiectul real al proprietarului: blocat permanent)
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{2,40}$")
DOMAIN_RE = re.compile(r"^(?=.{4,200}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
BACKEND_PORTS, EDGE_PORTS = (18100, 18500), (18500, 19000)
OWNER_ONLY = {"suspend", "resume", "deactivate", "restore", "import_release", "set_default_release", "create_operator"}
CONFIRMED = {"suspend", "deactivate", "restore"}
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class Provisioner:
    def __init__(self, home: Path, environment: str, vault: Vault, provider: Optional[rt.LocalProcessProvider] = None, clock: Callable[[], float] = time.time,
                 public_https_port: Optional[int] = None):
        if environment not in ("TEST", "PROD"):
            raise ProvisionError("BAD_ENV", "CIMITIR_PROVISIONER_ENV trebuie să fie TEST sau PROD", 500)
        self.home, self.env, self.vault, self.clock = Path(home), environment, vault, clock
        self.home.mkdir(parents=True, exist_ok=True)
        self.reg = Registry(self.home / "provisioner.db", clock)
        self.provider = provider or rt.LocalProcessProvider()
        self.ca = rt.LocalCA(vault, self.home / "ca")
        self.start_timeout = 25.0
        self._locks: Dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()
        self.releases = self.home / "releases"
        self.releases.mkdir(exist_ok=True)

    # ------------------------------------------------------------ autorizare / audit
    def _authz(self, op: Dict[str, Any], action: str, inst: Optional[Dict[str, Any]] = None) -> None:
        if op["environment"] != self.env:
            raise ProvisionError("ENV_MISMATCH", f"operatorul {op['name']} aparține mediului {op['environment']}, serviciul este {self.env}", 403)
        if inst and inst["environment"] != self.env:
            raise ProvisionError("ENV_MISMATCH", "instanța aparține altui mediu", 403)
        if action in OWNER_ONLY and op["role"] != "owner":
            raise ProvisionError("FORBIDDEN", f"acțiunea „{action}” cere rolul owner", 403)

    @contextmanager
    def _act(self, op: Dict[str, Any], action: str, ident: str = "", owner_only_check: bool = True, detail: Optional[Dict[str, Any]] = None):
        inst = self.reg.get_instance(ident) if ident else None
        if ident and not inst and action != "register":
            raise ProvisionError("NOT_FOUND", "instanță necunoscută", 404)
        try:
            self._authz(op, action, inst)
        except ProvisionError as exc:
            self.reg.audit(op["name"], action, inst["id"] if inst else "", "denied", {"reason": exc.code})
            raise
        iid = inst["id"] if inst else ""
        with self._locks_guard:
            lock = self._locks.setdefault(iid or "-", threading.RLock())
        try:
            with lock:                                      # o singură operațiune mutabilă pe instanță la un moment dat (reentrant pentru pașii înlănțuiți)
                yield (self.reg.get_instance(iid) if iid else None)
        except ProvisionError as exc:
            self.reg.audit(op["name"], action, iid, "error", {"code": exc.code, "message": redact_text(exc)[:300], **(detail or {})})
            raise
        except Exception as exc:  # noqa: BLE001
            self.reg.audit(op["name"], action, iid, "error", {"code": "INTERNAL", "message": redact_text(f"{type(exc).__name__}: {exc}")[:300]})
            raise ProvisionError("INTERNAL", "eroare internă: " + redact_text(f"{type(exc).__name__}: {str(exc)[:200]}"), 500) from None
        else:
            self.reg.audit(op["name"], action, iid, "ok", detail or {})

    def confirmation(self, op: Dict[str, Any], action: str, ident: str) -> Dict[str, Any]:
        with self._act(op, "confirm_" + action, ident) as inst:
            if action not in CONFIRMED:
                raise ProvisionError("BAD_REQUEST", "acțiune fără confirmare")
            if op["role"] != "owner":
                raise ProvisionError("FORBIDDEN", "doar owner poate cere confirmarea", 403)
            return {"code": self.reg.new_confirmation(op["id"], action, inst["id"]), "expires_in": 300, "must_type_slug": inst["slug"]}

    def _confirm(self, op, action, inst, code: str, slug: str) -> None:
        if slug != inst["slug"] or not self.reg.consume_confirmation(code, op["id"], action, inst["id"]):
            raise ProvisionError("CONFIRMATION", f"confirmare invalidă/expirată pentru „{action}”: cere un cod nou și tastează slug-ul instanței", 403)

    # ------------------------------------------------------------ utilitare
    def _dir(self, inst) -> Path:
        d = self.home / "instances" / inst["slug"]
        d.mkdir(parents=True, exist_ok=True)
        try:
            d.chmod(0o700)
        except OSError:
            pass
        return d

    def _vname(self, inst, name: str) -> str:
        return f"{self.env}/{inst['id']}/{name}"

    def _secret(self, inst, name: str, make: Optional[Callable[[], str]] = None) -> str:
        v = self.vault.get(self._vname(inst, name))
        if v is None and make:
            v = make()
            self.vault.set(self._vname(inst, name), v)
        if v is None:
            raise ProvisionError("SECRET_MISSING", f"secretul „{name}” lipsește pentru instanță", 409)
        return v

    def _link(self, inst) -> Dict[str, Any]:
        lk = inst["data"].get("link")
        if not lk:
            raise ProvisionError("NOT_LINKED", "instanța nu are un proiect Supabase asociat", 409)
        return lk

    def _check_admin_url(self, inst, admin_url: str) -> None:
        lk, u = self._link(inst), pgops.split_url(admin_url)
        if (u["host"], u["port"], u["dbname"]) != (lk["db_host"], lk["db_port"], lk["db_name"]):
            raise ProvisionError("ADMIN_URL_MISMATCH", "URL-ul administrativ nu corespunde bazei asociate instanței", 400)

    def _step(self, inst, step: str, status: str, detail: str = "") -> None:
        self.reg.log_step(inst["id"], step, status, detail)

    def _set(self, inst, status=None, **data):
        return self.reg.update_instance(inst["id"], status, data)

    def _runtime_url(self, inst) -> str:
        lk = self._link(inst)
        user = pgops.RUNTIME_ROLE + (f".{lk['project_ref']}" if lk.get("pooler") else "")
        return pgops.make_url(lk["db_host"], lk["db_port"], lk["db_name"], user, self._secret(inst, "runtime_db_password"), lk["sslmode"])

    def release_dir(self, version: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", version or ""):
            raise ProvisionError("BAD_RELEASE", "versiune de release invalidă")
        return self.releases / version

    # ------------------------------------------------------------ 1. înregistrare
    def register(self, op, slug: str, parish_name: str, domain: str, owner_public_key: str = "", generate_owner_key: bool = False) -> Dict[str, Any]:
        with self._act(op, "register", detail={"slug": slug, "domain": domain}):
            if not SLUG_RE.match(slug or "") or not DOMAIN_RE.match((domain or "").lower()):
                raise ProvisionError("BAD_REQUEST", "slug (litere mici/cifre/-) sau domeniu invalid")
            if not (parish_name or "").strip():
                raise ProvisionError("BAD_REQUEST", "numele parohiei lipsește")
            if bool(owner_public_key) == bool(generate_owner_key):
                raise ProvisionError("BAD_REQUEST", "furnizează fie cheia publică a proprietarului, fie cere generarea ei (exact una)")
            iid = uuid.uuid4().hex[:16]
            private = ""
            if generate_owner_key:
                kp = ig.generate_keypair()
                owner_public_key, private = kp["public_key"], kp["private_key"]
            else:
                try:
                    ig.public_from_private  # noqa: B018
                    ig._unb64(owner_public_key)
                except Exception:  # noqa: BLE001
                    raise ProvisionError("BAD_REQUEST", "cheia publică a proprietarului nu este base64url valid") from None
            used = self.reg.used_ports()
            bp = rt.free_port(*BACKEND_PORTS, used)
            ep = rt.free_port(*EDGE_PORTS, used | {bp})
            self.reg.create_instance({"id": iid, "slug": slug, "domain": domain.lower(), "environment": self.env, "status": "registered", "backend_port": bp, "edge_port": ep,
                                      "data": {"parish_name": parish_name.strip(), "owner_public_key": owner_public_key, "owner_fingerprint": ig.fingerprint(owner_public_key), "created_by": op["name"]}})
            inst = self.reg.get_instance(iid)
            self._secret(inst, "backup_key", lambda: base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
            self._step(inst, "register", "ok")
            out = self.public(inst)
            if private:
                out["owner_private_key_ONCE"] = private       # se returnează o singură dată; serviciul păstrează doar cheia publică
            return out

    # ------------------------------------------------------------ 2. asociere proiect Supabase
    def link(self, op, ident: str, p: Dict[str, Any]) -> Dict[str, Any]:
        with self._act(op, "link", ident, detail={"project_ref": p.get("project_ref")}) as inst:
            if inst["status"] in ("suspended", "deactivated"):
                raise ProvisionError("BAD_STATE", f"instanța este {inst['status']}", 409)
            need = ("supabase_url", "project_ref", "db_host", "db_port", "db_name", "bucket", "storage_secret_key")
            miss = [k for k in need if not p.get(k)]
            if miss:
                raise ProvisionError("BAD_REQUEST", "câmpuri lipsă: " + ", ".join(miss))
            ref, host = str(p["project_ref"]), (urlparse(p["supabase_url"]).hostname or "")
            if not re.fullmatch(r"[a-z]{20}", ref):
                raise ProvisionError("BAD_REQUEST", "project_ref invalid (20 litere mici)")
            if any(r in ref or r in p["supabase_url"] or r in p["db_host"] for r in PROTECTED_REFS):
                raise ProvisionError("PROTECTED_PROJECT", "proiectul Supabase indicat este protejat (producția proprietarului) și nu poate fi asociat", 403)
            local = host in LOOPBACK and p["db_host"] in LOOPBACK
            if not local and not str(p["db_host"]).lower().endswith((".supabase.com", ".supabase.co")):
                raise ProvisionError("BAD_REQUEST", "gazda bazei de date trebuie să fie un host Supabase (*.supabase.com / *.supabase.co)")
            if not local and host != f"{ref}.supabase.co":
                raise ProvisionError("BAD_REQUEST", "supabase_url nu corespunde project_ref")
            sslmode = p.get("sslmode") or ("disable" if local else "require")
            if not local and sslmode in ("disable", "allow", "prefer"):
                raise ProvisionError("INSECURE_TLS_DB", "conexiunea la baza de date externă cere sslmode=require/verify-*")
            if local and self.env == "PROD":
                raise ProvisionError("LOCAL_IN_PROD", "mediul PROD nu acceptă proiecte locale", 403)
            lk = {"supabase_url": p["supabase_url"].rstrip("/"), "project_ref": ref, "db_host": p["db_host"], "db_port": int(p["db_port"]), "db_name": p["db_name"],
                  "bucket": p["bucket"], "sslmode": sslmode, "pooler": bool(p.get("pooler")), "local": local}
            self.vault.set(self._vname(inst, "storage_secret_key"), p["storage_secret_key"])
            self.reg.update_instance(inst["id"], "linked", {"link": lk}, project_ref=ref, db_key=f"{lk['db_host']}:{lk['db_port']}/{lk['db_name']}")
            self._step(inst, "link", "ok", f"proiect {ref}")
            return self.public(self.reg.get_instance(inst["id"]))

    # ------------------------------------------------------------ 3. verificare conectivitate
    def verify(self, op, ident: str, admin_database_url: str) -> Dict[str, Any]:
        with self._act(op, "verify", ident) as inst:
            self._check_admin_url(inst, admin_database_url)
            lk = self._link(inst)
            db = pgops.verify_admin(admin_database_url)
            st = stor.ensure_and_verify(lk["supabase_url"], self._secret(inst, "storage_secret_key"), lk["bucket"])
            self._step(inst, "verify", "ok", f"pg {db['server_version']}, storage privat")
            if inst["status"] == "linked":
                self._set(inst, "verified")
            return {"database": db, "storage": st}

    # ------------------------------------------------------------ 4. migrări
    def migrate(self, op, ident: str, admin_database_url: str, release: Optional[str] = None) -> Dict[str, Any]:
        with self._act(op, "migrate", ident) as inst:
            self._check_admin_url(inst, admin_database_url)
            version = release or inst["data"].get("release") or self._latest_release()
            rd = self.release_dir(version)
            rt.verify_release(rd)
            mig_dir = rd / "backend" / "cimitir" / "pg_migrations"
            pre = None
            db = pgops.verify_admin(admin_database_url)
            if not db["empty"]:
                pre = self._backup(inst, admin_database_url, "pre-migrare")["id"]      # fără backup valid nu se migrează o bază cu date
            res = pgops.apply_migrations(admin_database_url, mig_dir)
            self._step(inst, "migrate", "ok", f"{len(res['applied'])} aplicate" if res["applied"] else "deja la zi")
            if inst["status"] in ("linked", "verified", "registered"):
                self._set(inst, "migrated")
            return {**res, "pre_backup": pre, "release": version}

    def set_default_release(self, op, version: str) -> Dict[str, Any]:
        with self._act(op, "set_default_release", detail={"version": version}):
            rt.verify_release(self.release_dir(version))
            (self.releases / "DEFAULT").write_text(version)
            return {"default_release": version}

    def _latest_release(self) -> str:
        d = self.releases / "DEFAULT"
        if d.is_file():
            return d.read_text().strip()
        vs = sorted(p.name for p in self.releases.iterdir() if p.is_dir() and not p.name.endswith(".tmp"))
        if not vs:
            raise ProvisionError("NO_RELEASE", "nu există niciun release importat (cere owner: import_release)", 409)
        return vs[-1]

    # ------------------------------------------------------------ 5. rol runtime cu privilegii minime
    def secure(self, op, ident: str, admin_database_url: str, rotate: bool = False) -> Dict[str, Any]:
        with self._act(op, "secure", ident, detail={"rotate": rotate}) as inst:
            self._check_admin_url(inst, admin_database_url)
            pw = secrets.token_urlsafe(32) if rotate else self._secret(inst, "runtime_db_password", lambda: secrets.token_urlsafe(32))
            rep = pgops.ensure_runtime_role(admin_database_url, pw)
            if rotate:
                self.vault.set(self._vname(inst, "runtime_db_password"), pw)
            chk = pgops.verify_runtime_login(self._runtime_url(inst))
            if not chk["least_privilege"]:
                raise ProvisionError("ROLE_NOT_MINIMAL", "rolul runtime nu respectă privilegiile minime: " + str({k: v for k, v in chk.items() if k.endswith('_denied') or k == 'minimal'}), 500)
            self._step(inst, "secure", "ok", "rol runtime minim, parolă proprie" + (" (rotită)" if rotate else ""))
            if inst["status"] in ("migrated",):
                self._set(inst, "secured")
            return {"role": rep, "login_check": {k: chk[k] for k in ("least_privilege", "create_table_denied", "create_role_denied", "app_function_ok")}, "rotated": rotate}

    # ------------------------------------------------------------ 6-9. backend izolat + TLS + domeniu
    def _env_for(self, inst) -> Dict[str, str]:
        lk = self._link(inst)
        d = inst["data"]
        port = inst["edge_port"] if lk["local"] else 443
        env = {"CIMITIR_CP_DATABASE_URL": self._runtime_url(inst), "SUPABASE_URL": lk["supabase_url"], "SUPABASE_SECRET_KEY": self._secret(inst, "storage_secret_key"),
               "SUPABASE_BUCKET": lk["bucket"], "CIMITIR_CLOUD_PROJECT_REF": lk["project_ref"],
               "CIMITIR_PUBLIC_URL": f"https://{inst['domain']}" + (f":{port}" if port != 443 else ""), "CIMITIR_INSTANCE_ID": inst["slug"],
               "CIMITIR_INSTANCE_ADMIN_PUBKEY": d["owner_public_key"], "CIMITIR_PAIRING_TRUSTED_PROXIES": "127.0.0.1", "CIMITIR_LOG_LEVEL": "INFO"}
        if lk["local"]:
            env["CIMITIR_LOCAL_DEMO"] = "1"
        return env

    def deploy(self, op, ident: str, release: Optional[str] = None) -> Dict[str, Any]:
        with self._act(op, "deploy", ident) as inst:
            if inst["status"] in ("suspended", "deactivated"):
                raise ProvisionError("BAD_STATE", f"instanța este {inst['status']}", 409)
            if inst["status"] in ("registered", "linked", "verified", "migrated"):
                raise ProvisionError("BAD_STATE", "înainte de deploy: verify → migrate → secure", 409)
            version = release or inst["data"].get("release") or self._latest_release()
            rd = self.release_dir(version)
            rt.verify_release(rd)
            self._stop_procs(inst)
            idir = self._dir(inst)
            cert, key = idir / "tls" / "cert.pem", idir / "tls" / "key.pem"
            tls = self.ca.issue(inst["domain"], cert, key)
            bp = self.provider.start_backend(idir, rd, inst["backend_port"], self._env_for(inst))
            if not rt.wait_port(inst["backend_port"], self.start_timeout, alive=lambda: rt.proc_alive(bp["pid"], bp["start"])):
                self._step(inst, "deploy", "error", "backendul nu a pornit")
                self._set(inst, "failed", last_error="backendul nu a pornit (vezi instances/<slug>/logs/backend.log)", procs={"backend": bp})
                raise ProvisionError("BACKEND_START", "backendul nu a pornit; vezi logurile instanței", 500)
            ep = self.provider.start_edge(idir, inst["edge_port"], inst["backend_port"], cert, key)
            rt.wait_port(inst["edge_port"], 10)
            prev = inst["data"].get("release")
            self._set(inst, "running", release=version, prev_release=prev if prev != version else inst["data"].get("prev_release"), procs={"backend": bp, "edge": ep},
                      tls={"not_after": tls["not_after"]}, last_error="")
            self._step(inst, "deploy", "ok", f"release {version}, https://{inst['domain']}")
            return self.public(self.reg.get_instance(inst["id"]))

    def _stop_procs(self, inst) -> None:
        for k in ("backend", "edge"):
            pr = (inst["data"].get("procs") or {}).get(k) or {}
            rt.stop_process(pr.get("pid"), pr.get("start"))

    # ------------------------------------------------------------ 10. sănătate
    def health(self, op, ident: str) -> Dict[str, Any]:
        with self._act(op, "health", ident) as inst:
            return self._health(inst)

    def _health(self, inst) -> Dict[str, Any]:
        procs = inst["data"].get("procs") or {}
        out: Dict[str, Any] = {"backend_process": rt.proc_alive((procs.get("backend") or {}).get("pid"), (procs.get("backend") or {}).get("start")),
                               "edge_process": rt.proc_alive((procs.get("edge") or {}).get("pid"), (procs.get("edge") or {}).get("start")), "checked_at": self.clock()}
        if inst["status"] in ("running", "degraded") and out["edge_process"]:
            ca = self.ca.ca_pem
            try:
                live = rt.https_get(inst["domain"], "127.0.0.1", inst["edge_port"], "/healthz", ca)
                ready = rt.https_get(inst["domain"], "127.0.0.1", inst["edge_port"], "/api/v1/health", ca)
                adm = rt.https_get(inst["domain"], "127.0.0.1", inst["edge_port"], "/api/v1/admin/status", ca)
                out.update(https_ok=True, api=live["status"] == 200, database=(ready["json"] or {}).get("database"), storage=(ready["json"] or {}).get("storage"),
                           ready=ready["status"] == 200, admin_initialized=bool(((adm["json"] or {}).get("result") or {}).get("initialized")))
            except Exception as exc:  # noqa: BLE001
                out.update(https_ok=False, error=redact_text(f"{type(exc).__name__}: {str(exc)[:120]}"), ready=False)
        else:
            out.update(https_ok=False, ready=False)
        na = (inst["data"].get("tls") or {}).get("not_after")
        out["tls_days_left"] = round((na - self.clock()) / 86400, 1) if na else None
        if inst["status"] in ("running", "degraded"):
            self.reg.update_instance(inst["id"], "running" if out["ready"] else "degraded", {"health": out, "admin_initialized": out.get("admin_initialized", False)})
        return out

    # ------------------------------------------------------------ orchestrare completă
    def provision(self, op, ident: str, admin_database_url: str, release: Optional[str] = None) -> Dict[str, Any]:
        """Reluabil: sare peste pașii deja efectuați; oprește la prima eroare cu starea `failed` + cauza."""
        with self._act(op, "provision", ident) as inst:
            inst = self.reg.get_instance(inst["id"])
            if inst["status"] in ("suspended", "deactivated"):
                raise ProvisionError("BAD_STATE", f"instanța este {inst['status']}", 409)
        steps = {}
        try:
            steps["verify"] = self.verify(op, ident, admin_database_url)
            steps["migrate"] = self.migrate(op, ident, admin_database_url, release)
            steps["secure"] = self.secure(op, ident, admin_database_url)
            steps["deploy"] = {"status": self.deploy(op, ident, release)["status"]}
            steps["health"] = self.health(op, ident)
        except ProvisionError as exc:
            cur = self.reg.get_instance(ident)
            if cur["status"] not in ("failed",):
                self.reg.update_instance(cur["id"], None, {"last_error": f"{exc.code}: {redact_text(exc)[:300]}"})
            raise
        if not steps["health"].get("ready"):
            raise ProvisionError("NOT_HEALTHY", "instanța a pornit dar verificarea de sănătate a eșuat: " + str({k: steps['health'].get(k) for k in ('https_ok', 'api', 'database', 'storage')}), 500)
        return {"instance": self.public(self.reg.get_instance(ident)), "steps": steps}

    # ------------------------------------------------------------ 13. actualizare fără pierdere de date
    def update(self, op, ident: str, admin_database_url: str, release: str) -> Dict[str, Any]:
        with self._act(op, "update", ident, detail={"release": release}) as inst:
            if inst["status"] not in ("running", "degraded"):
                raise ProvisionError("BAD_STATE", "actualizarea cere o instanță pornită", 409)
            self._check_admin_url(inst, admin_database_url)
            old = inst["data"].get("release")
            rt.verify_release(self.release_dir(release))
            bk = self._backup(inst, admin_database_url, "pre-actualizare")
            self._step(inst, "update", "info", f"backup {bk['id']} verificat")
            mig = pgops.apply_migrations(admin_database_url, self.release_dir(release) / "backend" / "cimitir" / "pg_migrations")   # eșec => ROLLBACK, vechiul backend continuă
            try:
                self.deploy(op, ident, release)
                h = self._health(self.reg.get_instance(inst["id"]))
                if not h.get("ready"):
                    raise ProvisionError("NOT_HEALTHY", "noul release nu este sănătos", 500)
            except ProvisionError as exc:
                self._step(inst, "update", "rollback", f"revenire la {old}: {exc.code}")
                self.deploy(op, ident, old)
                self.reg.audit(op["name"], "update_rollback", inst["id"], "ok", {"from": release, "to": old, "reason": exc.code})
                raise ProvisionError("UPDATE_ROLLED_BACK", f"actualizarea a eșuat ({exc.code}); s-a revenit la release {old}. Datele nu au fost modificate de rollback.", 500) from None
            self._step(inst, "update", "ok", f"{old} → {release}")
            return {"from": old, "to": release, "migrations": mig, "backup": bk["id"], "instance": self.public(self.reg.get_instance(inst["id"]))}

    # ------------------------------------------------------------ 14. backup / recuperare
    def _backup(self, inst, admin_url: str, kind: str) -> Dict[str, Any]:
        bid = time.strftime("%Y%m%dT%H%M%S", time.gmtime(self.clock())) + "-" + secrets.token_hex(3)
        dest = self._dir(inst) / "backups" / f"{bid}.cimbk"
        key = self._secret(inst, "backup_key")
        meta = pgops.make_backup(admin_url, key, dest)
        pgops.verify_backup(dest, key, meta["sha256"])
        row = {"id": bid, "instance_id": inst["id"], "path": str(dest), "sha256": meta["sha256"], "size": meta["size"], "kind": kind, "manifest": meta["manifest"]}
        self.reg.add_backup(row)
        return row

    def backup(self, op, ident: str, admin_database_url: str) -> Dict[str, Any]:
        with self._act(op, "backup", ident) as inst:
            self._check_admin_url(inst, admin_database_url)
            b = self._backup(inst, admin_database_url, "manual")
            return {k: b[k] for k in ("id", "sha256", "size", "kind")} | {"tables": len(b["manifest"]["tables"])}

    def list_backups(self, op, ident: str):
        with self._act(op, "list_backups", ident) as inst:
            return [{k: b[k] for k in ("id", "kind", "size", "sha256", "created_at")} for b in self.reg.backups(inst["id"])]

    def verify_backup(self, op, ident: str, backup_id: str) -> Dict[str, Any]:
        with self._act(op, "verify_backup", ident) as inst:
            b = self.reg.get_backup(inst["id"], backup_id)
            if not b:
                raise ProvisionError("NOT_FOUND", "backup necunoscut", 404)
            return pgops.verify_backup(Path(b["path"]), self._secret(inst, "backup_key"), b["sha256"])

    def restore(self, op, ident: str, admin_database_url: str, backup_id: str, target_database_url: str = "", confirmation: str = "", confirm_slug: str = "") -> Dict[str, Any]:
        """Fără `target_database_url`: restaurare PESTE baza instanței (cere confirmare + backup de siguranță nou). Cu țintă: restaurare într-o bază goală separată (exercițiu de recuperare)."""
        with self._act(op, "restore", ident, detail={"backup": backup_id, "in_place": not target_database_url}) as inst:
            b = self.reg.get_backup(inst["id"], backup_id)
            if not b:
                raise ProvisionError("NOT_FOUND", "backup necunoscut", 404)
            key = self._secret(inst, "backup_key")
            pgops.verify_backup(Path(b["path"]), key, b["sha256"])
            if target_database_url:
                if pgops.verify_admin(target_database_url)["public_tables"]:
                    raise ProvisionError("TARGET_NOT_EMPTY", "baza-țintă nu este goală: restaurarea separată cere o bază goală", 409)
                return {"restored_to": "target", **pgops.restore_backup(target_database_url, Path(b["path"]), key, b["sha256"], clean=False)}
            self._check_admin_url(inst, admin_database_url)
            self._confirm(op, "restore", inst, confirmation, confirm_slug)
            safety = self._backup(inst, admin_database_url, "pre-restaurare")
            was_running = inst["status"] in ("running", "degraded")
            if was_running:
                self._stop_procs(inst)
            res = pgops.restore_backup(admin_database_url, Path(b["path"]), key, b["sha256"], clean=True)
            if was_running:
                self.deploy(op, ident)
            return {"restored_to": "instance", "safety_backup": safety["id"], **res}

    # ------------------------------------------------------------ 15. suspendare / dezactivare (doar owner + confirmare)
    def _set_login(self, inst, admin_url: str, login: bool) -> None:
        from psycopg import sql
        self._check_admin_url(inst, admin_url)
        with pgops.connect(admin_url) as c:
            c.execute(sql.SQL("ALTER ROLE {} " + ("LOGIN" if login else "NOLOGIN")).format(sql.Identifier(pgops.RUNTIME_ROLE)))
            if not login:
                c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename=%s", (pgops.RUNTIME_ROLE,))

    def suspend(self, op, ident: str, admin_database_url: str, confirmation: str, confirm_slug: str) -> Dict[str, Any]:
        with self._act(op, "suspend", ident) as inst:
            if inst["status"] in ("suspended", "deactivated"):
                raise ProvisionError("BAD_STATE", f"deja {inst['status']}", 409)
            self._confirm(op, "suspend", inst, confirmation, confirm_slug)
            self._set_login(inst, admin_database_url, False)
            self._stop_procs(inst)
            self._set(inst, "suspended", suspended_by=op["name"], suspended_at=self.clock(), status_before=inst["status"])
            self._step(inst, "suspend", "ok")
            return self.public(self.reg.get_instance(inst["id"]))

    def resume(self, op, ident: str, admin_database_url: str) -> Dict[str, Any]:
        with self._act(op, "resume", ident) as inst:
            if inst["status"] != "suspended":
                raise ProvisionError("BAD_STATE", "doar o instanță suspendată poate fi reluată", 409)
            self._set_login(inst, admin_database_url, True)
            self.reg.update_instance(inst["id"], "secured", {})
            self.deploy(op, ident)
            self._step(inst, "resume", "ok")
            return self.public(self.reg.get_instance(inst["id"]))

    def deactivate(self, op, ident: str, admin_database_url: str, confirmation: str, confirm_slug: str) -> Dict[str, Any]:
        """Oprește și revocă accesul (rol NOLOGIN, secrete runtime șterse). NU șterge baza de date, Storage-ul sau backup-urile."""
        with self._act(op, "deactivate", ident) as inst:
            if inst["status"] == "deactivated":
                raise ProvisionError("BAD_STATE", "deja dezactivată", 409)
            self._confirm(op, "deactivate", inst, confirmation, confirm_slug)
            self._set_login(inst, admin_database_url, False)
            self._stop_procs(inst)
            for n in ("runtime_db_password", "storage_secret_key"):
                self.vault.delete(self._vname(inst, n))
            shutil.rmtree(self._dir(inst) / "tls", ignore_errors=True)
            self._set(inst, "deactivated", deactivated_by=op["name"], deactivated_at=self.clock(), procs={})
            self._step(inst, "deactivate", "ok", "date păstrate; acces revocat")
            return self.public(self.reg.get_instance(inst["id"]))

    # ------------------------------------------------------------ operațiuni diverse
    def renew_tls(self, op, ident: str, force: bool = False, within_days: float = 30) -> Dict[str, Any]:
        with self._act(op, "renew_tls", ident) as inst:
            idir = self._dir(inst)
            na = rt.cert_not_after(idir / "tls" / "cert.pem")
            if not force and na and (na - self.clock()) / 86400 > within_days:
                return {"renewed": False, "days_left": round((na - self.clock()) / 86400, 1)}
            t = self.ca.issue(inst["domain"], idir / "tls" / "cert.pem", idir / "tls" / "key.pem")
            self._set(inst, None, tls={"not_after": t["not_after"]})
            return {"renewed": True, "not_after": t["not_after"]}

    def restart(self, op, ident: str) -> Dict[str, Any]:
        with self._act(op, "restart", ident) as inst:
            if inst["status"] not in ("running", "degraded", "failed"):
                raise ProvisionError("BAD_STATE", "repornirea cere o instanță dislocată", 409)
            self.deploy(op, ident)
            return self._health(self.reg.get_instance(inst["id"]))

    def import_release(self, op, source_root: str, version: str) -> Dict[str, Any]:
        with self._act(op, "import_release", detail={"version": version}):
            r = rt.build_release(Path(source_root), self.release_dir(version), version)
            return r

    def public(self, inst: Dict[str, Any]) -> Dict[str, Any]:
        d = inst["data"]
        lk = d.get("link") or {}
        return {"id": inst["id"], "slug": inst["slug"], "parish_name": d.get("parish_name"), "domain": inst["domain"], "environment": inst["environment"], "status": inst["status"],
                "project_ref": lk.get("project_ref"), "release": d.get("release"), "backend_port": inst["backend_port"], "edge_port": inst["edge_port"],
                "owner_fingerprint": d.get("owner_fingerprint"), "admin_initialized": bool(d.get("admin_initialized")), "health": d.get("health"),
                "tls_not_after": (d.get("tls") or {}).get("not_after"), "last_error": d.get("last_error") or "", "updated_at": inst["updated_at"]}

    def list_public(self, op) -> list:
        self._authz(op, "list")
        return [self.public(i) for i in self.reg.list_instances(self.env)]

    def get_public(self, op, ident: str) -> Dict[str, Any]:
        inst = self.reg.get_instance(ident)
        if not inst:
            raise ProvisionError("NOT_FOUND", "instanță necunoscută", 404)
        self._authz(op, "get", inst)
        return {**self.public(inst), "steps": self.reg.steps(inst["id"], 30), "backups": len(self.reg.backups(inst["id"]))}

    def reconcile(self) -> Dict[str, str]:
        """După repornirea serverului: repornește instanțele `running` ale căror procese nu mai există (fără intervenție manuală)."""
        out = {}
        sysop = {"name": "system", "id": "system", "role": "owner", "environment": self.env}
        for inst in self.reg.list_instances(self.env):
            if inst["status"] in ("running", "degraded"):
                procs = inst["data"].get("procs") or {}
                if not all(rt.proc_alive((procs.get(k) or {}).get("pid"), (procs.get(k) or {}).get("start")) for k in ("backend", "edge")):
                    try:
                        self.deploy(sysop, inst["id"])
                        out[inst["slug"]] = "repornită"
                    except ProvisionError as exc:
                        out[inst["slug"]] = "eșec: " + exc.code
        return out
