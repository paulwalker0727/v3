"""Gestionare Docker TEST: doar comenzi dintr-o listă permisă. Fără `compose down`, `system prune`, `volume rm`, fără ștergerea containerelor străine.
Containerul vechi (portul 8084) nu se modifică, oprește sau șterge; mediul se citește din el doar read-only (docker inspect) dacă e ales ca sursă."""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import guard
from .config import build_db_url, repo_root
from .redact import redact, register

ALLOWED = {"version", "info", "ps", "inspect", "images", "image", "build", "run", "rename", "stop", "start", "rm", "logs", "port"}
FORBIDDEN_TOKENS = {"prune", "down", "compose", "volume", "network", "system", "rmi", "kill", "--privileged", "--volumes-from", "-v", "--volume", "--mount"}
IMAGE = "cimitir-cloud"
INTERNAL_PORT = 8080
REQUIRED_ENV = ("CIMITIR_CP_DATABASE_URL", "SUPABASE_URL", "SUPABASE_BUCKET", "CIMITIR_CLOUD_PROJECT_REF")
_SKIP_ENV = re.compile(r"^(PATH|HOME|HOSTNAME|PYTHON_|LANG|GPG_KEY|PWD|TERM)")
_created: set = set()


def run_docker(args: List[str], timeout: int = 60, extra_allowed: Optional[set] = None) -> Tuple[int, str, str]:
    if not args or args[0] not in ALLOWED:
        raise guard.GuardError(f"Comandă Docker nepermisă: {args[:1]}")
    bad = FORBIDDEN_TOKENS & {a.lower() for a in args}
    if bad:
        raise guard.GuardError(f"Argument Docker interzis: {sorted(bad)}")
    if args[0] == "rm":
        names = [a for a in args[1:] if not a.startswith("-")]
        if len(names) != 1 or names[0] not in _created:
            raise guard.GuardError("Ștergerea este permisă doar pentru containerul creat chiar în această operație.")
    p = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    return p.returncode, redact(p.stdout), redact(p.stderr)


def port_free(port: int) -> bool:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def preflight_env(env: Dict[str, str]) -> List[str]:
    """Aceleași reguli ca shared/cimitir_cloud_app.preflight (testul de drift le compară). Doar NUME de variabile, niciodată valori."""
    issues = [f"lipsește variabila {k}" for k in REQUIRED_ENV if not (env.get(k) or "").strip()]
    if not ((env.get("SUPABASE_SECRET_KEY") or "").strip() or (env.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()):
        issues.append("lipsește variabila SUPABASE_SECRET_KEY")
    ref = (env.get("CIMITIR_CLOUD_PROJECT_REF") or "").strip()
    if ref and not re.fullmatch(r"[a-z]{20}", ref):
        issues.append("CIMITIR_CLOUD_PROJECT_REF invalid (20 litere mici)")
    from urllib.parse import urlparse
    host = urlparse(env.get("SUPABASE_URL") or "").hostname or ""
    if ref and host and host != f"{ref}.supabase.co":
        issues.append("SUPABASE_URL nu corespunde CIMITIR_CLOUD_PROJECT_REF")
    db = env.get("CIMITIR_CP_DATABASE_URL") or ""
    if ref and "supabase" in db and ref not in db:
        issues.append("CIMITIR_CP_DATABASE_URL nu corespunde CIMITIR_CLOUD_PROJECT_REF")
    return issues


def compose_env(cfg: Dict[str, Any], source_env: Optional[Dict[str, str]] = None) -> Tuple[Dict[str, str], Dict[str, str], List[str]]:
    """(env final, sursa fiecărei chei, avertismente). Valorile din configurare le suprascriu pe cele copiate din containerul sursă."""
    env: Dict[str, str] = {}
    src: Dict[str, str] = {}
    warns: List[str] = []
    for k, v in (source_env or {}).items():
        env[k] = v
        src[k] = f"copiat din {cfg.get('source_container')}"
    runtime_url = ""
    if cfg.get("runtime_db_url"):
        runtime_url = build_db_url(cfg, "runtime_db_url")
    elif cfg.get("db_password"):
        runtime_url = build_db_url(cfg)
        warns.append("Cloud va folosi credențialele ADMINISTRATIVE PostgreSQL. Recomandat: rol runtime dedicat (cimitir_cp_app) în câmpul „Runtime DB URL”.")
    override = {
        "CIMITIR_CP_DATABASE_URL": runtime_url,
        "SUPABASE_URL": (cfg.get("supabase_url") or "").rstrip("/"),
        "SUPABASE_BUCKET": cfg.get("storage_bucket") or "",
        "CIMITIR_CLOUD_PROJECT_REF": cfg.get("project_ref") or "",
        "SUPABASE_SECRET_KEY": cfg.get("supabase_secret_key") or "",
        "CIMITIR_PUBLIC_URL": cfg.get("cloud_public_url") or "",
        "PORT": str(INTERNAL_PORT),
    }
    for k, v in override.items():
        if v:
            env[k] = v
            src[k] = "configurare Setup Manager"
    for k, v in env.items():
        if k == "CIMITIR_CP_DATABASE_URL" or any(w in k for w in ("SECRET", "PASSWORD", "TOKEN", "KEY")):
            register(v)
    return env, src, warns


def read_source_env(name: str, docker: Callable = run_docker) -> Dict[str, str]:
    """docker inspect (READ-ONLY) pe containerul sursă; valorile rămân în memorie."""
    rc, out, err = docker(["inspect", name], 30)
    if rc != 0:
        raise RuntimeError(f"Containerul sursă „{name}” nu poate fi inspectat.")
    env = {}
    for line in (json.loads(out)[0].get("Config", {}).get("Env") or []):
        if "=" in line and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", line) and not _SKIP_ENV.match(line):
            k, _, v = line.partition("=")
            env[k] = v
    return env


def _write_env_file(env: Dict[str, str]) -> str:
    fd, path = tempfile.mkstemp(prefix="cimitir_env_", suffix=".env")
    with os.fdopen(fd, "w", encoding="ascii", errors="ignore") as f:
        for k, v in env.items():
            if "\n" in v or "\r" in v:
                continue
            f.write(f"{k}={v}\n")
    try:
        os.chmod(path, 0o600)
        if os.name == "nt":
            subprocess.run(["icacls", path, "/inheritance:r", "/grant:r", f"{os.environ.get('USERNAME', '')}:(R,W)"], capture_output=True)
    except OSError:
        pass
    return path


def list_containers(docker: Callable = run_docker) -> List[Dict[str, Any]]:
    rc, out, err = docker(["ps", "-a", "--format", "{{json .}}"], 30)
    if rc != 0:
        raise RuntimeError(err.strip()[:300] or "docker ps a eșuat")
    rows = []
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        ports = d.get("Ports", "")
        pub = [int(x) for x in re.findall(r":(\d+)->", ports)]
        rows.append({"name": d.get("Names"), "image": d.get("Image"), "status": d.get("Status"), "ports": ports, "published": pub,
                     "protected": bool(set(pub) & set(guard.PROTECTED_PORTS)) or d.get("Names") in guard.PROTECTED_CONTAINERS})
    return rows


def status(cfg: Dict[str, Any], docker: Callable = run_docker) -> Dict[str, Any]:
    out: Dict[str, Any] = {"docker": False, "containers": [], "port": cfg.get("docker_port"), "port_free": None, "port_owner": None, "images": []}
    try:
        rc, ver, _ = docker(["version", "--format", "{{.Server.Version}}"], 20)
        out["docker"] = rc == 0
        out["docker_version"] = ver.strip() if rc == 0 else ""
        if rc != 0:
            out["error"] = "Docker Desktop nu rulează sau nu este instalat."
            return out
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        out["error"] = f"Docker indisponibil: {type(exc).__name__}"
        return out
    out["containers"] = list_containers(docker)
    try:
        port = guard.assert_port_ok(cfg.get("docker_port"))
        out["port_free"] = port_free(port)
        out["port_owner"] = next((c["name"] for c in out["containers"] if port in c["published"]), None)
    except guard.GuardError as exc:
        out["port_error"] = str(exc)
    rc, imgs, _ = docker(["images", IMAGE, "--format", "{{.Repository}}:{{.Tag}}  {{.CreatedSince}}"], 20)
    out["images"] = imgs.strip().splitlines() if rc == 0 else []
    return out


def plan(cfg: Dict[str, Any], kind: str, docker: Callable = run_docker) -> Dict[str, Any]:
    """Previzualizare fără efecte: kind = build | start | update."""
    guard.assert_read_allowed(cfg)
    issues: List[str] = []
    tag = guard.assert_tag_ok(cfg.get("image_tag"))
    name = guard.assert_container_ok(cfg.get("container_name"))
    port = guard.assert_port_ok(cfg.get("docker_port"))
    res: Dict[str, Any] = {"kind": kind, "container": name, "port": port, "image": f"{IMAGE}:{tag}", "issues": issues, "warnings": []}
    res["command"] = (f"docker build -t {IMAGE}:{tag} \"{repo_root(cfg)}\"" if kind == "build" else
                      f"docker run -d --name {name} --env-file <temporar, șters imediat> -p 127.0.0.1:{port}:{INTERNAL_PORT} --restart unless-stopped "
                      f"--read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges {IMAGE}:{tag}")
    st = status(cfg, docker)
    if not st["docker"]:
        issues.append(st.get("error", "Docker indisponibil."))
    if kind == "build":
        if not (repo_root(cfg) / "Dockerfile").exists():
            issues.append("Dockerfile nu există în rădăcina repo-ului.")
        res["ready"] = not issues
        return res
    if not st["docker"]:
        res["ready"] = False
        return res
    existing = next((c for c in st["containers"] if c["name"] == name), None)
    if kind == "start" and existing:
        issues.append(f"Containerul „{name}” există deja ({existing['status']}). Folosește „Actualizează containerul TEST”.")
    if kind == "update" and not existing:
        issues.append(f"Containerul „{name}” nu există; folosește „Pornește”.")
    if kind == "update" and existing and port not in existing["published"] and existing["published"]:
        issues.append("Portul containerului selectat nu coincide cu cel configurat.")
    if kind == "update" and existing and existing["protected"]:
        issues.append("Containerul selectat este protejat.")
    if any(c["name"] == f"{name}-prev" for c in st["containers"]):
        issues.append(f"Există deja „{name}-prev” (rămas dintr-o actualizare anterioară); nu se șterge automat. Redenumește-l manual.")
    if kind == "start" and not st["port_free"]:
        issues.append(f"Portul {port} este ocupat" + (f" de containerul „{st['port_owner']}”" if st["port_owner"] else "") + ".")
    if not any(i.startswith(f"{IMAGE}:{tag}") for i in st["images"]):
        issues.append(f"Imaginea {IMAGE}:{tag} nu există; construiește-o mai întâi.")
    try:
        src_env = read_source_env(cfg["source_container"], docker) if cfg.get("source_container") else (
            read_source_env(name, docker) if kind == "update" else {})
    except RuntimeError as exc:
        issues.append(str(exc))
        src_env = {}
    env, srcs, warns = compose_env(cfg, src_env)
    res["warnings"] = warns
    for i in preflight_env(env):
        issues.append("Mediu: " + i)
    try:
        guard.assert_not_prod({k: v for k, v in env.items()})
    except guard.GuardError as exc:
        issues.append(str(exc))
    res["env_keys"] = [{"key": k, "source": srcs.get(k, "?")} for k in sorted(env)]
    res["ready"] = not issues
    return res


def wait_healthy(port: int, seconds: int = 45) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3) as r:
                if r.status == 200:
                    return True
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(2)
    return False


def _run_args(name: str, port: int, tag: str, envf: str) -> List[str]:
    return ["run", "-d", "--name", name, "--env-file", envf, "-p", f"127.0.0.1:{port}:{INTERNAL_PORT}", "--restart", "unless-stopped",
            "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", f"{IMAGE}:{tag}"]


def execute(cfg: Dict[str, Any], kind: str, docker: Callable = run_docker, health: Callable = wait_healthy) -> Dict[str, Any]:
    guard.assert_write_allowed(cfg)
    p = plan(cfg, kind, docker)
    if not p["ready"]:
        raise guard.GuardError("Operația nu poate rula: " + " | ".join(p["issues"]))
    name, port, tag = p["container"], p["port"], guard.assert_tag_ok(cfg["image_tag"])
    src_name = cfg.get("source_container") or (name if kind == "update" else "")
    env, _, _ = compose_env(cfg, read_source_env(src_name, docker) if src_name else {})
    envf = _write_env_file(env)
    log: List[str] = []
    try:
        if kind == "start":
            _created.add(name)
            rc, out, err = docker(_run_args(name, port, tag, envf), 120)
            if rc != 0:
                raise RuntimeError(err.strip()[:400])
            log.append(f"Container „{name}” pornit pe 127.0.0.1:{port}.")
        else:
            prev = f"{name}-prev"
            docker(["stop", name], 60)
            rc, _, err = docker(["rename", name, prev], 30)
            if rc != 0:
                docker(["start", name], 30)
                raise RuntimeError("Redenumirea containerului a eșuat; containerul a fost repornit neschimbat: " + err.strip()[:200])
            _created.add(name)
            rc, _, err = docker(_run_args(name, port, tag, envf), 120)
            if rc != 0:
                docker(["rename", prev, name], 30)
                docker(["start", name], 30)
                raise RuntimeError("Containerul nou nu a pornit; versiunea veche a fost restaurată: " + err.strip()[:300])
            log.append(f"Versiunea veche păstrată oprită ca „{prev}”; nu se șterge.")
    finally:
        try:
            os.remove(envf)
        except OSError:
            pass
    ok = health(port)
    if not ok:
        _, logs, _ = docker(["logs", "--tail", "40", name], 30)
        log.append("NESĂNĂTOS după 45 s. Ultimele loguri (mascate):\n" + logs[-1500:])
        if kind == "update":
            docker(["rm", "-f", name], 30)              # permis: doar containerul creat în această operație (_created)
            docker(["rename", f"{name}-prev", name], 30)
            docker(["start", name], 30)
            log.append("Versiunea veche a fost restaurată și repornită.")
        else:
            docker(["stop", name], 30)
            log.append("Containerul nou a fost oprit (nu șters) pentru inspecție.")
        _created.discard(name)
        return {"ok": False, "log": log}
    _created.discard(name)
    log.append(f"Serviciul răspunde sănătos pe http://127.0.0.1:{port}/healthz")
    return {"ok": True, "log": log}


def build_image(cfg: Dict[str, Any], emit: Callable[[str], None]) -> bool:
    guard.assert_write_allowed(cfg)
    tag = guard.assert_tag_ok(cfg.get("image_tag"))
    root = repo_root(cfg)
    if not (root / "Dockerfile").exists():
        raise guard.GuardError("Dockerfile lipsește.")
    for bad in (root / ".env", root / "backend" / ".env"):
        if bad.exists():
            emit(f"ATENȚIE: {bad.name} există în repo; Dockerfile nu îl copiază (COPY selectiv), dar nu-l include în patch/commit.")
    emit(f"docker build -t {IMAGE}:{tag} .")
    proc = subprocess.Popen(["docker", "build", "-t", f"{IMAGE}:{tag}", str(root)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    assert proc.stdout
    for line in proc.stdout:
        emit(redact(line.rstrip())[:400])
    return proc.wait() == 0


class Jobs:
    def __init__(self):
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def start(self, title: str, fn: Callable[[Callable[[str], None]], Any]) -> str:
        jid = uuid.uuid4().hex[:10]
        job = {"id": jid, "title": title, "status": "running", "lines": [], "result": None}
        with self._lock:
            self._jobs[jid] = job

        def emit(line: str) -> None:
            job["lines"].append(redact(line))
            del job["lines"][:-400]

        def runner():
            try:
                job["result"] = fn(emit)
                job["status"] = "done"
            except Exception as exc:  # noqa: BLE001
                emit(f"EȘEC: {type(exc).__name__}: {exc}")
                job["status"] = "failed"
        threading.Thread(target=runner, daemon=True).start()
        return jid

    def get(self, jid: str) -> Optional[Dict[str, Any]]:
        j = self._jobs.get(jid)
        return {**j, "lines": list(j["lines"])} if j else None
