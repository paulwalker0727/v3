#!/usr/bin/env python
"""Smoke-test REAL pentru MAIN (Etapa 3.5): launcher real -> portal real (socket 127.0.0.1:8000) -> MAIN real -> registre.

Pe Windows (sesiune desktop obișnuită, ferestrele se văd):   python scripts\\smoke_main_real\\run.py
Pe Linux/CI fără ecran:                                       QT_QPA_PLATFORM=offscreen python scripts/smoke_main_real/run.py
Opțiuni:  --offline   procesul MAIN nu are voie să iasă pe internet (loopback permis)
          --port N    portul portalului (implicit 8000)   --data-dir D   folder NOU (implicit: temporar)
          --phases full,restart,auto,busy   (implicit: toate)

Folosește NUMAI un folder de date nou. Nu atinge Supabase, Render, migrările sau datele reale.
Operatorul (sitecustomize.py) acționează ferestrele reale ale MAIN printr-un QTimer; licența se simulează doar în acel proces.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DESKTOP = ROOT / "desktop"
SHARED = ROOT / "shared"


# ------------------------------------------------------------------ utilitare socket/proces
def listening_hosts(port: int) -> list:
    """Adresele pe care ASCULTĂ efectiv `port` (psutil, altfel netstat/ss)."""
    try:
        import psutil
        return sorted({c.laddr.ip for c in psutil.net_connections(kind="inet") if c.status == "LISTEN" and c.laddr.port == port})
    except Exception:
        pass
    for cmd in (["netstat", "-an"], ["ss", "-ltn"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        hosts = set()
        for line in out.splitlines():
            if "LISTEN" not in line.upper():
                continue
            for tok in line.split():
                if tok.endswith(f":{port}"):
                    hosts.add(tok.rsplit(":", 1)[0].strip("[]"))
                    break
        return sorted(hosts)
    return []


def pid_alive(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except ImportError:
        pass
    except Exception:
        return False
    if os.name == "nt":
        return str(pid) in subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def port_open(host: str, port: int, timeout=1.0) -> bool:
    with socket.socket() as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


# ------------------------------------------------------------------ modul „launcher” (rulează în subproces; o singură instanță Qt)
def launcher_mode(args) -> int:
    os.chdir(DESKTOP)
    sys.path[:0] = [str(DESKTOP), str(SHARED)]
    out = {"checks": {}}
    path = Path(args.report + ".launcher.json")

    def rec(name, ok, detail=""):
        out["checks"][name] = {"ok": bool(ok), "detail": str(detail)[:300]}
        path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    import PySide6.QtWidgets as W
    from PySide6.QtCore import QTimer
    msgs = []
    for fn in ("warning", "critical", "information"):
        setattr(W.QMessageBox, fn, staticmethod(lambda *a, _fn=fn, **k: (msgs.append(f"{_fn}: {a[2] if len(a) > 2 else a}"), W.QMessageBox.Ok)[1]))
    import launcher

    orig_shutdown = launcher.LauncherWindow.shutdown

    def shutdown(self):
        pid = out.get("portal_pid")
        orig_shutdown(self)
        if pid:
            time.sleep(0.5)
            rec("shutdown_portal_process_stopped", not pid_alive(pid), f"pid {pid}")
            rec("shutdown_port_released", not port_open("127.0.0.1", args.port), f"port {args.port}")
        if args.mode == "busy":
            return
        try:
            import psutil
            kids = [p for p in psutil.Process().children(recursive=True) if p.is_running()]
            rec("shutdown_no_orphan_children", not kids, [p.pid for p in kids])
        except ImportError:
            pass

    launcher.LauncherWindow.shutdown = shutdown

    def drive():
        win = next(w for w in W.QApplication.topLevelWidgets() if type(w).__name__ == "LauncherWindow")
        win.port_spin.setValue(args.port)
        win.start_local()  # exact calea butonului „Doar local”
        out["portal_message"] = launcher_msg = win._portal_message
        if args.mode == "busy":
            rec("busy_port_warning_shown", any("ocupat" in m for m in msgs), msgs[:2])
            rec("busy_port_portal_not_started", win._portal_ok is False and win._portal_proc is None, launcher_msg)
            rec("busy_port_main_still_started", win._desktop_proc is not None, "desktop proc pornit")
            return
        rec("portal_started_by_launcher", win._portal_ok and win._portal_proc is not None, launcher_msg)
        if win._portal_proc is not None:
            out["portal_pid"] = win._portal_proc.pid
        hosts = listening_hosts(args.port)
        rec("port_listens_loopback_only", hosts == ["127.0.0.1"], hosts)
        rec("no_0_0_0_0_default", "0.0.0.0" not in hosts and "::" not in hosts, hosts)
        lans = {launcher.get_lan_ip()}
        try:
            import psutil
            lans |= {a.address for addrs in psutil.net_if_addrs().values() for a in addrs
                     if a.family == socket.AF_INET and not a.address.startswith("127.")}
        except ImportError:
            pass
        lans.discard("127.0.0.1")
        rec("lan_ip_refused", all(not port_open(ip, args.port) for ip in lans), f"{sorted(lans) or 'nicio interfață LAN (nu s-a putut testa)'}:{args.port}")
        import cimitir_registry as reg
        dd = os.environ["CIMITIR_DATA_DIR"]
        has_active = reg.catalog_exists(dd) and bool(reg.RegistryCatalog(dd).get_state("active_cemetery_uid"))
        try:
            status, body = 200, b""
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{args.port}/portal", timeout=10) as r:
                    status, body = r.status, r.read()
            except urllib.error.HTTPError as http_err:
                status, body = http_err.code, http_err.read()
            if has_active:
                rec("real_socket_portal_200", status == 200 and len(body) > 500, f"HTTP {status} {len(body)} bytes (registru activ)")
            else:  # primul start: portalul răspunde real, dar așteaptă primul registru din MAIN (pagină explicativă, nu eroare criptică)
                rec("real_socket_portal_waits_for_first_registry", status == 503 and b"registru" in body.lower(), f"HTTP {status}")
        except Exception as exc:
            rec("real_socket_portal_200", False, exc)

    _orig = W.QApplication.__init__

    def _init(self, *a, **k):
        _orig(self, *a, **k)
        QTimer.singleShot(800, drive)

    W.QApplication.__init__ = _init
    try:
        launcher.main()
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


# ------------------------------------------------------------------ orchestrator
def run_phase(name, scenario, data_dir, args, extra_env=None, busy=False):
    report = Path(tempfile.gettempdir()) / f"smoke_{name}_{os.getpid()}.json"
    for p in (report, Path(str(report) + ".launcher.json")):
        p.unlink(missing_ok=True)
    env = {**os.environ, "CIMITIR_DATA_DIR": str(data_dir), "CIMITIR_IGNORE_PROJECT_ENV": "1", "CIMITIR_DB_ENGINE": "sqlite",
           "CIMITIR_MODE": "local", "DATABASE_URL": "", "CIMITIR_SMOKE": "1", "CIMITIR_SMOKE_SCENARIO": scenario,
           "CIMITIR_SMOKE_REPORT": str(report), "CIMITIR_SMOKE_PORT": str(args.port), "CIMITIR_PORT": str(args.port),
           "CIMITIR_JOBS": "0", "PYTHONPATH": str(HERE) + os.pathsep + os.environ.get("PYTHONPATH", ""),
           "CIMITIR_SMOKE_OFFLINE": "1" if args.offline else "0", "PYTHONUNBUFFERED": "1", **(extra_env or {})}
    env.pop("CIMITIR_HOST", None)
    listener, stop, thread = None, threading.Event(), None
    if busy:
        listener = socket.socket()
        if os.name != "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", args.port))
        listener.listen(5)
        listener.settimeout(0.2)

        def serve():  # „alt program” care ține portul; se oprește controlat, înainte de închiderea socketului
            while not stop.is_set():
                try:
                    conn, _ = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                conn.close()

        thread = threading.Thread(target=serve, name="busy-port-listener")
        thread.start()
    try:
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--launcher", "--mode", "busy" if busy else "normal",
                               "--report", str(report), "--port", str(args.port)] + (["--offline"] if args.offline else []),
                              env=env, capture_output=True, text=True, timeout=420)
    except subprocess.TimeoutExpired:
        proc = None
    finally:
        if listener:
            stop.set()
            thread.join(3)
            listener.close()
    res = {"phase": name, "exit": None if proc is None else proc.returncode, "checks": {}, "stderr": (proc.stderr[-800:] if proc else "TIMEOUT")}
    for p in (report, Path(str(report) + ".launcher.json")):
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            for k, v in data.get("checks", {}).items():
                res["checks"][k] = v
            for k in ("done", "leakage", "unexpected_messages", "log"):
                if k in data:
                    res[k] = data[k]
    res["checks"]["no_stray_legacy_db"] = {"ok": not (Path(data_dir) / "cimitir.db").exists() or scenario == "never",
                                           "detail": "cimitir.db nu a fost creat în DATA_DIR" if not (Path(data_dir) / "cimitir.db").exists()
                                           else f"găsit {Path(data_dir) / 'cimitir.db'}"}
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--launcher", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--mode", default="normal", help=argparse.SUPPRESS)
    ap.add_argument("--report", default="", help=argparse.SUPPRESS)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--data-dir")
    ap.add_argument("--phases", default="full,restart,auto,busy")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if args.launcher:
        return launcher_mode(args)

    sys.path.insert(0, str(SHARED))
    import cimitir_registry as reg
    data = Path(args.data_dir) if args.data_dir else Path(tempfile.mkdtemp(prefix="cimitir_smoke_"))
    if data.exists() and any(data.iterdir()):
        print(f"REFUZAT: {data} nu este gol; folosește un folder NOU (nu atinge date reale).")
        return 2
    data.mkdir(parents=True, exist_ok=True)
    phases, results = [p.strip() for p in args.phases.split(",")], []
    if "full" in phases:
        results.append(run_phase("full", "full", data, args))
    cat = reg.RegistryCatalog(data) if (data / reg.CATALOG_NAME).is_file() else None
    if "restart" in phases and cat:
        results.append(run_phase("restart", "quick", data, args))
    if "auto" in phases and cat:
        names = {c["name"]: c["uid"] for c in cat.list_cemeteries()}
        if "Cimitir A" in names and "Cimitir B" in names:
            cat.archive_cemetery(names["Cimitir B"])
            results.append(run_phase("auto", "auto", data, args, {"CIMITIR_SMOKE_EXPECT_UID": names["Cimitir A"]}))
    if "busy" in phases:
        busy_dir = Path(tempfile.mkdtemp(prefix="cimitir_smoke_busy_"))
        results.append(run_phase("busy", "quick", busy_dir, args, busy=True))
        shutil.rmtree(busy_dir, ignore_errors=True)

    failed = 0
    for r in results:
        print(f"\n== faza {r['phase']} (exit {r['exit']}) done={r.get('done')} leakage={r.get('leakage')}")
        for name, c in r["checks"].items():
            expected_fail = r["phase"] == "busy" and name == "portal_up_in_quick"
            ok = c["ok"] or expected_fail
            failed += 0 if ok else 1
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {c['detail']}")
        if not r.get("done"):
            failed += 1
            print(f"  [FAIL] scenariul nu s-a terminat. stderr: {r['stderr']}")
        if r.get("unexpected_messages"):
            failed += 1
            print(f"  [FAIL] mesaje neașteptate: {r['unexpected_messages']}")
    summary = {"data_dir": str(data), "failed": failed, "phases": results}
    if args.out:
        Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nREZULTAT: {'PASS' if failed == 0 else 'FAIL'} ({failed} verificări eșuate); date: {data}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
