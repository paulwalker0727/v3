"""cimitir_prov_runtime.py — releases, TLS, procese și furnizori de rulare pentru instanțele izolate.

* Release = copie verificabilă (manifest SHA-256) a codului backendului; instanța pornește DOAR dintr-un release verificat. Codul provisionerului NU intră în release.
* LocalProcessProvider: un proces `python -m cimitir_cloud_app` per instanță, mediu MINIM (nimic moștenit din procesul provisionerului), legat doar de 127.0.0.1.
* DockerProvider / CaddyProxy: generează comenzi/configurări pentru producție (netestate pe un Docker/Caddy real în acest mediu).
* LocalCA: CA local + certificate de frunză pentru demonstrație (HTTPS real, verificat cu numele domeniului).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from cimitir_prov_store import ProvisionError, Vault

ROOT = Path(__file__).resolve().parents[1]
RELEASE_FILES = (("shared", "*.py"), ("backend", "attachments.py"), ("scripts", "cloud_admin_recovery.py"))
RELEASE_TREES = ("backend/templates", "backend/static", "backend/cimitir/pg_migrations", "backend/cimitir/pg_rollbacks", "backend/doc_templates", "backend/fonts")
EXCLUDE_PREFIX = "cimitir_prov"          # codul provisionerului nu intră în release-ul instanței
SAFE_ENV = ("PATH", "LANG", "LC_ALL", "TZ", "SYSTEMROOT")


# ------------------------------------------------------------------ releases
def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def build_release(source_root: Path, dest: Path, version: str) -> Dict[str, Any]:
    source_root, dest = Path(source_root), Path(dest)
    if dest.exists():
        raise ProvisionError("RELEASE_EXISTS", f"release {version} există deja (imuabil)", 409)
    files: List[Path] = []
    for d, pat in RELEASE_FILES:
        files += [p for p in (source_root / d).glob(pat) if p.is_file() and not p.name.startswith(EXCLUDE_PREFIX)]
    for t in RELEASE_TREES:
        files += [p for p in (source_root / t).rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    if not any(f.name == "cimitir_cloud_app.py" for f in files):
        raise ProvisionError("RELEASE_SOURCE", "sursa nu conține cimitir_cloud_app.py", 400)
    tmp = dest.with_name(dest.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    manifest = {}
    for f in sorted(files):
        rel = f.relative_to(source_root).as_posix()
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, tmp / rel)
        manifest[rel] = _sha(f)
    meta = {"version": version, "files": manifest, "tree_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()}
    (tmp / "release.json").write_text(json.dumps(meta, indent=1, sort_keys=True))
    os.replace(tmp, dest)
    return {"version": version, "files": len(manifest), "tree_sha256": meta["tree_sha256"]}


def verify_release(path: Path) -> Dict[str, Any]:
    p = Path(path)
    try:
        meta = json.loads((p / "release.json").read_text())
    except (OSError, ValueError):
        raise ProvisionError("RELEASE_INVALID", "release fără manifest valid", 409) from None
    bad = [rel for rel, h in meta["files"].items() if not (p / rel).is_file() or _sha(p / rel) != h]
    extra = [x.relative_to(p).as_posix() for x in p.rglob("*") if x.is_file() and x.name != "release.json" and "__pycache__" not in x.parts
             and x.relative_to(p).as_posix() not in meta["files"]]
    if bad or extra:
        raise ProvisionError("RELEASE_TAMPERED", f"release modificat: {len(bad)} fișiere diferite, {len(extra)} în plus", 409)
    return {"version": meta["version"], "tree_sha256": meta["tree_sha256"], "files": len(meta["files"])}


# ------------------------------------------------------------------ TLS local
class LocalCA:
    NAME = "ca/local"

    def __init__(self, vault: Vault, root: Path):
        self.vault, self.root = vault, Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._ensure()

    def _ensure(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        if self.vault.get(self.NAME + "/key") and (self.root / "ca.pem").is_file():
            return
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Cimitir Provisioner LOCAL CA (demo)")])
        now = dt.datetime.now(dt.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=3650))
                .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True).sign(key, hashes.SHA256()))
        self.vault.set(self.NAME + "/key", key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode())
        (self.root / "ca.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    @property
    def ca_pem(self) -> Path:
        return self.root / "ca.pem"

    def issue(self, domain: str, cert_path: Path, key_path: Path, days: int = 90, ips=("127.0.0.1",)) -> Dict[str, Any]:
        import ipaddress
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        ca_key = serialization.load_pem_private_key(self.vault.get(self.NAME + "/key").encode(), None)
        ca_cert = x509.load_pem_x509_certificate(self.ca_pem.read_bytes())
        key = ec.generate_private_key(ec.SECP256R1())
        now = dt.datetime.now(dt.timezone.utc)
        san = [x509.DNSName(domain)] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
        cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)])).issuer_name(ca_cert.subject)
                .public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=days))
                .add_extension(x509.SubjectAlternativeName(san), critical=False).add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .sign(ca_key, hashes.SHA256()))
        cert_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        _chmod(key_path, 0o600)
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        return {"not_after": cert.not_valid_after_utc.timestamp(), "domain": domain}


def cert_not_after(cert_path: Path) -> Optional[float]:
    try:
        from cryptography import x509
        return x509.load_pem_x509_certificate(Path(cert_path).read_bytes()).not_valid_after_utc.timestamp()
    except Exception:  # noqa: BLE001
        return None


def https_get(domain: str, ip: str, port: int, path: str, ca_pem: Path, timeout: float = 5.0) -> Dict[str, Any]:
    """GET prin TLS cu verificarea certificatului pentru numele `domain` (conectare la `ip`:`port`). Returnează {status, headers, json}."""
    ctx = ssl.create_default_context(cafile=str(ca_pem))
    with socket.create_connection((ip, port), timeout=timeout) as raw, ctx.wrap_socket(raw, server_hostname=domain) as s:
        s.sendall(f"GET {path} HTTP/1.1\r\nHost: {domain}\r\nConnection: close\r\nAccept: application/json\r\n\r\n".encode())
        buf = b""
        while chunk := s.recv(65536):
            buf += chunk
    head, _, body = buf.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split()[1])
    headers = {k.lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
    try:
        js = json.loads(body.decode("utf-8")) if body.strip().startswith(b"{") else None
    except ValueError:
        js = None
    return {"status": status, "headers": headers, "json": js}


# ------------------------------------------------------------------ procese
def _chmod(p, mode):
    try:
        os.chmod(p, mode)
    except OSError:
        pass


def proc_start_time(pid: int) -> Optional[str]:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def proc_alive(pid: Optional[int], start: Optional[str]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    st = proc_start_time(pid)
    if st is not None and start is not None and st != start:
        return False
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


def stop_process(pid: Optional[int], start: Optional[str], grace: float = 8.0) -> bool:
    if not proc_alive(pid, start):
        return False
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except OSError:
        os.kill(pid, signal.SIGTERM)
    end = time.time() + grace
    while time.time() < end and proc_alive(pid, start):
        time.sleep(0.1)
    if proc_alive(pid, start):
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except OSError:
            pass
    return True


def free_port(lo: int, hi: int, used: set) -> int:
    for p in range(lo, hi):
        if p in used:
            continue
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
            except OSError:
                continue
        return p
    raise ProvisionError("NO_PORT", "nu există porturi libere în interval", 503)


def wait_port(port: int, timeout: float = 25.0, host: str = "127.0.0.1", alive: Optional[Callable[[], bool]] = None) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if alive is not None and not alive():
            return False
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


class LocalProcessProvider:
    """Un proces backend + (opțional) un terminator TLS per instanță. Fără Docker."""
    name = "local-process"

    def __init__(self, python: Optional[str] = None):
        self.python = python or sys.executable

    def start_backend(self, inst_dir: Path, release_dir: Path, port: int, env: Dict[str, str]) -> Dict[str, Any]:
        (inst_dir / "logs").mkdir(parents=True, exist_ok=True)
        penv = {k: v for k, v in os.environ.items() if k in SAFE_ENV}
        penv.update(env, PYTHONPATH=str(release_dir / "shared"), PORT=str(port), CIMITIR_CLOUD_HOST="127.0.0.1", PYTHONDONTWRITEBYTECODE="1")
        log = open(inst_dir / "logs" / "backend.log", "ab")
        pr = subprocess.Popen([self.python, "-m", "cimitir_cloud_app"], cwd=str(release_dir), env=penv, stdout=log, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL, start_new_session=True)
        log.close()
        time.sleep(0.2)
        return {"pid": pr.pid, "start": proc_start_time(pr.pid)}

    def start_edge(self, inst_dir: Path, listen_port: int, upstream_port: int, cert: Path, key: Path) -> Dict[str, Any]:
        (inst_dir / "logs").mkdir(parents=True, exist_ok=True)
        log = open(inst_dir / "logs" / "edge.log", "ab")
        penv = {k: v for k, v in os.environ.items() if k in SAFE_ENV}
        penv["PYTHONPATH"] = str(ROOT / "shared")
        pr = subprocess.Popen([self.python, "-m", "cimitir_prov_edge", "--listen", f"127.0.0.1:{listen_port}", "--upstream", f"127.0.0.1:{upstream_port}",
                               "--cert", str(cert), "--key", str(key)], env=penv, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        log.close()
        return {"pid": pr.pid, "start": proc_start_time(pr.pid)}


class DockerProvider:
    """Producție (Docker): comenzi generate; secretele se transmit prin `-e NUME` (valoarea vine din mediul procesului de provizionare, nu din argv/fișier).
    NETESTAT pe un Docker real în acest mediu: verificat doar prin teste unitare ale comenzii generate."""
    name = "docker"

    def __init__(self, runner: Optional[Callable[[List[str], Dict[str, str]], int]] = None, image: str = "cimitir-cloud:latest"):
        self.image = image
        self.runner = runner or (lambda cmd, env: subprocess.run(cmd, env=env, check=False).returncode)

    def build_run_command(self, slug: str, release: str, port: int, env_names: List[str]) -> List[str]:
        cmd = ["docker", "run", "-d", "--name", f"cimitir-{slug}", "--restart", "unless-stopped", "--network", f"cimitir-{slug}-net", "--read-only", "--tmpfs", "/tmp",
               "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "512m", "--pids-limit", "256",
               "-p", f"127.0.0.1:{port}:8080", "--label", f"cimitir.instance={slug}", "--label", f"cimitir.release={release}"]
        for n in env_names:
            cmd += ["-e", n]
        return cmd + [f"{self.image}"]

    def start(self, slug: str, release: str, port: int, env: Dict[str, str]) -> int:
        self.runner(["docker", "network", "create", "--internal", f"cimitir-{slug}-net"], dict(os.environ))
        return self.runner(self.build_run_command(slug, release, port, sorted(env)), {**os.environ, **env})


def caddy_site(domain: str, upstream_port: int, hsts: bool = True) -> str:
    """Site Caddy per instanță: HTTPS automat (ACME/Let's Encrypt), HSTS, reverse proxy local. Netestat cu un Caddy real aici."""
    hs = '    header Strict-Transport-Security "max-age=31536000; includeSubDomains"\n' if hsts else ""
    return (f"{domain} {{\n    encode zstd gzip\n{hs}    request_body {{\n        max_size 25MB\n    }}\n"
            f"    reverse_proxy 127.0.0.1:{upstream_port} {{\n        health_uri /healthz\n        header_up X-Forwarded-For {{remote_host}}\n    }}\n}}\n")
