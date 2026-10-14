#!/usr/bin/env python3
"""Smoke test pentru aplicația cloud (read-only, fără secrete, fără scrieri).

  python scripts/cloud_smoke.py --url https://field.exemplu.ro [--origin https://portal.exemplu.ro]
  python scripts/cloud_smoke.py --clean-start --python /cale/venv/bin/python      # copiază DOAR ce copiază Dockerfile într-un director gol și pornește aplicația cu un mediu fals

Ieșire 0 = toate verificările au trecut. Nu trimite chei și nu scrie nimic în Field Inbox.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FAILS: list = []


def get(url, headers=None):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=10) as r:
            return r.status, r.read().decode("utf-8", "replace"), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), {k.lower(): v for k, v in e.headers.items()}


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail and not ok else ''}")
    if not ok:
        FAILS.append(name)


def run_checks(base, origin="", expect_ready=True):
    s, b, _ = get(base + "/healthz")
    check("liveness /healthz", s == 200 and json.loads(b) == {"ok": True, "api": "up"}, b)
    s, b, h = get(base + "/api/v1/health")
    j = json.loads(b) if b.startswith("{") else {}
    check("readiness /api/v1/health: api up", j.get("api") == "up")
    check("readiness: storage configured", j.get("storage") == "configured", b)
    if expect_ready:
        check("readiness: database up (HTTP 200)", s == 200 and j.get("database") == "up", b)
    check("health fără conexiuni/chei", not re.search(r"postgres(ql)?://|sb_secret|service_role|supabase\.co|password", b, re.I))
    check("health no-store", h.get("cache-control") == "no-store")
    s, page, h = get(base + "/phone")
    check("pagina /phone", s == 200 and 'data-testid="teren-page"' in page)
    csp = h.get("content-security-policy", "")
    check("CSP fără unsafe-inline", "script-src 'self'" in csp and "unsafe-inline" not in csp)
    for f in ("teren.js", "teren_ui.js", "teren.css"):
        check(f"static {f}", get(f"{base}/static/{f}")[0] == 200)
    check("static neexpus (app.js)", get(base + "/static/app.js")[0] == 404)
    check("docs dezactivat", get(base + "/docs")[0] == 404 and get(base + "/openapi.json")[0] == 404)
    check("API cere autentificare", get(base + "/api/v1/field-inbox/me")[0] == 401)
    leak = re.search(r"postgres(ql)?://|sb_secret|service_role|SUPABASE_SECRET", page + get(base + "/static/teren.js")[1] + get(base + "/static/teren_ui.js")[1], re.I)
    check("fără secrete în pagină/JS", not leak)
    h = get(base + "/healthz", {"Origin": "https://origine-neautorizata.invalid"})[2]
    check("CORS: origine neautorizată respinsă", "access-control-allow-origin" not in h)
    if origin:
        h = get(base + "/healthz", {"Origin": origin})[2]
        check("CORS: origine permisă", h.get("access-control-allow-origin") == origin and "access-control-allow-credentials" not in h)


def image_tree(dst: Path):
    for line in (REPO / "Dockerfile").read_text(encoding="utf-8").splitlines():
        m = re.match(r"COPY\s+(.+?)\s+(\S+)$", line.strip())
        if not m:
            continue
        for s in m.group(1).split():
            src, target = REPO / s, m.group(2)
            if src.is_dir():
                shutil.copytree(src, dst / target, ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
            else:
                (dst / target).mkdir(parents=True, exist_ok=True) if target.endswith("/") else (dst / target).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(src, dst / target / src.name if target.endswith("/") else dst / target)


def clean_start(python):
    ref = "abcdefghijklmnopqrst"
    with tempfile.TemporaryDirectory() as td:
        tree = Path(td) / "app"
        image_tree(tree)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        env = {"PATH": os.environ["PATH"], "HOME": td, "PYTHONPATH": str(tree / "shared"), "PYTHONDONTWRITEBYTECODE": "1", "PORT": str(port), "CIMITIR_CLOUD_HOST": "127.0.0.1",
               "CIMITIR_CP_DATABASE_URL": "postgresql://cimitir_cp_app:placeholder@127.0.0.1:1/postgres", "SUPABASE_URL": f"https://{ref}.supabase.co",
               "SUPABASE_SECRET_KEY": "sb_secret_placeholder", "SUPABASE_BUCKET": "field-private", "CIMITIR_CLOUD_PROJECT_REF": ref, "CIMITIR_PAIRING_TRUSTED_PROXIES": "127.0.0.1"}
        miss = subprocess.run([python, "-m", "cimitir_cloud_app"], cwd=tree, env={k: v for k, v in env.items() if k != "SUPABASE_BUCKET"}, capture_output=True, text=True, timeout=60)
        check("start fără env => ieșire 2 (fail-fast)", miss.returncode == 2 and "SUPABASE_BUCKET" in miss.stdout + miss.stderr)
        proc = subprocess.Popen([python, "-m", "cimitir_cloud_app"], cwd=tree, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            for _ in range(100):
                try:
                    if get(f"http://127.0.0.1:{port}/healthz")[0] == 200:
                        break
                except OSError:
                    time.sleep(0.2)
            else:
                check("container start", False, "nu a pornit")
                return
            check("container start (arbore curat ca în imagine)", True)
            run_checks(f"http://127.0.0.1:{port}", expect_ready=False)
            s, b, _ = get(f"http://127.0.0.1:{port}/api/v1/health")
            check("DB inaccesibilă => 503 database=down (fără scurgeri)", s == 503 and json.loads(b)["database"] == "down" and "placeholder" not in b)
        finally:
            proc.terminate()
            out = proc.communicate(timeout=15)[0]
        check("fără secrete în log", "placeholder" not in out and "sb_secret" not in out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--origin", default="")
    ap.add_argument("--clean-start", action="store_true")
    ap.add_argument("--python", default=sys.executable)
    a = ap.parse_args()
    if a.clean_start:
        clean_start(a.python)
    elif a.url:
        run_checks(a.url.rstrip("/"), a.origin)
    else:
        ap.error("--url sau --clean-start")
    print("REZULTAT:", "OK" if not FAILS else f"EȘEC ({len(FAILS)}): {', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
