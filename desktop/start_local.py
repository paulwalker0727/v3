"""Pornește portalul (uvicorn) și aplicația desktop pe aceeași bază SQLite.

Exemplu (PowerShell):
    python start_local.py --data-dir "C:\\Cimitir\\data-test"

Ordinea: config -> portal (uvicorn) -> health-check -> desktop.
Fără shell (listă de argumente); interpretul este sys.executable.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

DESKTOP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = DESKTOP_DIR.parent
WEB_DIR = PROJECT_DIR / "backend"
SHARED_DIR = PROJECT_DIR / "shared"
ASGI_APP = "server:app"

if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))

import cimitir_config  # noqa: E402

LOCAL_HOST = cimitir_config.LOCAL_HOST
LOCAL_PORT = cimitir_config.LOCAL_PORT


def _arguments(argv: Optional[list] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pornește portalul local și Cimitir Desktop cu o bază de date comună."
    )
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="Folderul cu cimitir.db (implicit: CIMITIR_DATA_DIR, apoi ./shared_data).")
    parser.add_argument("--host", default=None, help="Adresa portalului (implicit 127.0.0.1; 0.0.0.0 = rețea, doar explicit).")
    parser.add_argument("--port", type=int, default=None, help="Portul portalului (implicit 8000).")
    parser.add_argument("--desktop-only", action="store_true", help="Doar aplicația desktop.")
    parser.add_argument("--check-deps", action="store_true",
                        help="Verifică dependențele portalului în acest Python și iese (0 = OK, 2 = lipsesc pachete).")
    parser.add_argument("--portal-only", action="store_true",
                        help="Doar portalul (fără desktop), până la Ctrl+C.")
    parser.add_argument("--health-timeout", type=float, default=30.0,
                        help="Secunde de așteptare pentru health-check (implicit 30).")
    return parser.parse_args(argv)


def child_environment(data_dir: Path, host: str, port: int) -> dict:
    env = os.environ.copy()
    env["CIMITIR_DATA_DIR"] = str(data_dir)
    env["CIMITIR_HOST"] = host
    env["CIMITIR_PORT"] = str(port)
    env["PORT"] = str(port)
    env["PYTHONUNBUFFERED"] = "1"
    return env


PORTAL_MODULES = (("fastapi", "fastapi"), ("uvicorn", "uvicorn"), ("jwt", "pyjwt"), ("multipart", "python-multipart"),
                  ("jinja2", "jinja2"), ("qrcode", "qrcode[pil]"), ("PIL", "Pillow"), ("reportlab", "reportlab"),
                  ("openpyxl", "openpyxl"), ("requests", "requests"), ("psycopg", "psycopg[binary]"),
                  ("psycopg_pool", "psycopg-pool"))


def missing_portal_dependencies() -> list:
    """Pachetele portalului lipsă în INTERPRETUL CURENT (cel care pornește portalul: `sys.executable -m uvicorn`).
    `psycopg` se importă efectiv: fără [binary] lipsește libpq și portalul nu pornește."""
    if is_frozen():
        return []
    missing = []
    for module, package in PORTAL_MODULES:
        try:
            importlib.import_module(module)
        except Exception:  # ImportError, sau libpq lipsă la psycopg
            missing.append(package)
    return missing


def dependency_install_command() -> str:
    req = DESKTOP_DIR / "web_requirements.txt"
    return f'"{sys.executable}" -m pip install -r "{req}"'


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def backend_dir() -> Path:
    """Folderul backend: sursele în dezvoltare, `portal/` din pachet în executabilul înghețat."""
    return Path(getattr(sys, "_MEIPASS", ".")) / "portal" if is_frozen() else WEB_DIR


def behind_proxy() -> bool:
    return os.environ.get("CIMITIR_BEHIND_PROXY", "0").strip() == "1"


def proxy_flags() -> list:
    """Implicit uvicorn NU crede X-Forwarded-For (altfel un client ar putea ocoli limitările pe IP)."""
    if behind_proxy():
        return ["--proxy-headers", "--forwarded-allow-ips", os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")]
    return ["--no-proxy-headers"]


def portal_command(host: str, port: int) -> list:
    if is_frozen():
        return [sys.executable, "--portal", "--host", host, "--port", str(port)]
    return [sys.executable, "-m", "uvicorn", ASGI_APP, "--app-dir", str(WEB_DIR),
            "--host", host, "--port", str(port), "--log-level", "warning", *proxy_flags()]


def serve_portal(host: str, port: int) -> int:
    """Rulează portalul în procesul curent (folosit de `Cimitir.exe --portal`)."""
    os.environ["CIMITIR_BACKEND_DIR"] = str(backend_dir())
    if str(backend_dir()) not in sys.path:
        sys.path.insert(0, str(backend_dir()))
    import uvicorn
    import server
    uvicorn.run(server.app, host=host, port=port, log_level="warning", proxy_headers=behind_proxy(),
                forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"))
    return 0


def portal_cli(argv: list) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--portal", action="store_true")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)
    return serve_portal(args.host or cimitir_config.portal_host(), args.port or cimitir_config.desktop_port())


def probe_host(host: str) -> str:
    """Adresa la care se verifică un portal pornit pe `host` (0.0.0.0 se verifică pe loopback)."""
    return LOCAL_HOST if host in ("", "0.0.0.0", "::") else host


def port_is_free(host: str, port: int) -> bool:
    """Liber = nimeni nu ascultă pe port și adresa poate fi legată."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        if sock.connect_ex((probe_host(host), port)) == 0:
            return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if os.name != "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host or LOCAL_HOST, port))
        except OSError:
            return False
    return True


def health_url(host: str, port: int) -> str:
    return f"http://{host}:{port}/api/public/health"


def wait_for_health(host: str, port: int, timeout: float, process: Optional[subprocess.Popen] = None) -> bool:
    deadline = time.monotonic() + timeout
    url = health_url(host, port)
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200 and json.loads(resp.read().decode("utf-8")).get("ok") is True:
                    return True
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(0.4)
    return False


def health_info(host: str, port: int, timeout: float = 2.0) -> Optional[dict]:
    """Răspunsul /api/public/health (dict) sau None dacă pe port nu răspunde un portal Cimitir."""
    try:
        with urllib.request.urlopen(health_url(probe_host(host), port), timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body if resp.status == 200 and isinstance(body, dict) and body.get("ok") is True else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def start_portal(env: dict, host: str, port: int, log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cwd = str(backend_dir()) if backend_dir().is_dir() else str(DESKTOP_DIR)
    with open(log_path, "ab") as log_file:
        return subprocess.Popen(portal_command(host, port), cwd=cwd, env=env,
                                stdout=log_file, stderr=subprocess.STDOUT)


def stop_portal(portal: Optional[subprocess.Popen]) -> None:
    """Oprește doar procesul pornit de noi și îl „recoltează” (fără procese zombie). Idempotent."""
    if portal is None:
        return
    if portal.poll() is None:
        portal.terminate()
        try:
            portal.wait(timeout=8)
        except subprocess.TimeoutExpired:
            portal.kill()
            try:
                portal.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


class PortalStatus:
    """state: started | reused | busy_other | foreign_instance | failed."""

    def __init__(self, state: str, message: str, proc: Optional[subprocess.Popen] = None):
        self.state, self.message, self.proc = state, message, proc

    @property
    def ok(self) -> bool:
        return self.state in ("started", "reused")


def ensure_portal_status(host: str, port: int, health_timeout: float = 30.0) -> PortalStatus:
    """Pornește backend-ul local pe baza din CIMITIR_DATA_DIR. Nu ridică excepții și nu schimbă niciodată portul.

    - portul liber: pornește și așteaptă health-check;
    - ocupat de propria instanță (aceeași bază de date): o reutilizează, fără proces nou;
    - ocupat de altă instanță Cimitir sau de alt program: eroare clară, nu pornește altundeva.
    """
    try:
        if not port_is_free(host, port):
            info = health_info(host, port)
            if info is None:
                return PortalStatus("busy_other", f"Portul {port} este ocupat de alt program (nu de Cimitir). "
                                                  f"Închide programul respectiv sau alege alt port; nu pornesc pe alt port.")
            if info.get("instance") == cimitir_config.instance_id():
                return PortalStatus("reused", f"Portal deja pornit pe portul {port}.")
            return PortalStatus("foreign_instance",
                                f"Portul {port} este ocupat de altă instanță Cimitir, cu altă bază de date. "
                                f"Închide-o înainte de a continua; nu pornesc pe alt port.")
        missing = missing_portal_dependencies()
        if missing:
            return PortalStatus("failed", f"Portalul nu poate porni: lipsesc pachete în Python-ul folosit de launcher "
                                          f"({sys.executable}): {', '.join(missing)}.\nRulează: {dependency_install_command()}")
        data_dir = Path(cimitir_config.data_dir())
        log_path = data_dir / "logs" / "portal.log"
        proc = start_portal(child_environment(data_dir, host, port), host, port, log_path)
        if not wait_for_health(probe_host(host), port, health_timeout, proc):
            stop_portal(proc)
            return PortalStatus("failed", f"Portalul nu a pornit. Vezi {log_path}.\n{_tail(log_path, 8)}")
        return PortalStatus("started", f"Portal pornit pe portul {port}.", proc)
    except Exception as exc:
        return PortalStatus("failed", f"Portalul nu a pornit: {exc}")


def ensure_portal(host: str, port: int, health_timeout: float = 30.0):
    """Formă compatibilă: (proces|None, mesaj, ok)."""
    status = ensure_portal_status(host, port, health_timeout)
    return status.proc, status.message, status.ok


def _tail(path: Path, lines: int = 15) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return "(jurnal indisponibil)"


def main(argv: Optional[list] = None) -> int:
    args = _arguments(argv)
    if args.check_deps:
        missing = missing_portal_dependencies()
        if missing:
            print(f"[deps] LIPSESC în {sys.executable}: {', '.join(missing)}\n[deps] Rulează: {dependency_install_command()}")
            return 2
        print(f"[deps] OK: dependențele portalului sunt instalate în {sys.executable}")
        return 0
    if args.data_dir is not None:
        os.environ["CIMITIR_DATA_DIR"] = str(args.data_dir)
    data_dir = Path(cimitir_config.data_dir())
    host = args.host or cimitir_config.portal_host()
    port = args.port or cimitir_config.desktop_port()
    env = child_environment(data_dir, host, port)

    import cimitir_registry
    _active = cimitir_registry.binding_from_catalog(data_dir) if cimitir_registry.catalog_exists(data_dir) else None
    print(f"[local] Bază de date comună: {_active.database if _active else data_dir / 'cimitir.db'}")
    warning = cimitir_config.sync_folder_warning(str(data_dir))
    if warning:
        print(f"[local] AVERTISMENT: {warning}")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"[local] AVERTISMENT: portalul ascultă pe {host}; panoul /admin devine accesibil din rețea.")

    portal = None
    if not args.desktop_only:
        missing = missing_portal_dependencies()
        if missing:
            print(f"[local] EROARE: lipsesc pachete în {sys.executable}: {', '.join(missing)}. Rulează: {dependency_install_command()}")
            return 2
        if not port_is_free(host, port):
            print(f"[local] EROARE: portul {port} este deja ocupat. Folosește --port <altul>.")
            return 3
        log_path = data_dir / "logs" / "portal.log"
        portal = start_portal(env, host, port, log_path)
        print(f"[local] Portal pornit (pid {portal.pid}); aștept health-check {health_url(host, port)}")
        if not wait_for_health(host, port, args.health_timeout, portal):
            print("[local] EROARE: portalul nu răspunde. Ultimele linii din jurnal:")
            print(_tail(log_path))
            stop_portal(portal)
            return 4
        print(f"[local] Portal OK: http://{host}:{port}/portal   (administrare: http://{host}:{port}/admin)")

    try:
        if args.portal_only:
            print("[local] Doar portal. Ctrl+C pentru oprire.")
            while portal is not None and portal.poll() is None:
                time.sleep(1)
            return 0 if portal is None else portal.returncode or 0
        return subprocess.call([sys.executable, "main.py"], cwd=str(DESKTOP_DIR), env=env)
    except KeyboardInterrupt:
        return 130
    finally:
        stop_portal(portal)


if __name__ == "__main__":
    raise SystemExit(main())
