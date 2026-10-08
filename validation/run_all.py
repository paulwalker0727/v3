"""Ruleaza TOATE scripturile de validare in ordine, de la o stare curata. Scrie /app/validation/results/*.log si results/summary.json"""
import json
import os
import re
import subprocess
import sys
import time

from common import get_secrets, save_secrets

os.makedirs("/app/validation/results", exist_ok=True)
S = get_secrets()
summary = {}


def run(name, script=None, timeout=900):
    t0 = time.time()
    p = subprocess.run([sys.executable, script or f"/app/validation/{name}.py"], cwd="/app/validation", capture_output=True, text=True, timeout=timeout)
    out = p.stdout + (("\n[stderr]\n" + p.stderr[-1500:]) if p.stderr.strip() else "")
    open(f"/app/validation/results/{name}.log", "w").write(out)
    m = re.search(r"REZUMAT:\s*(\d+)\s*/\s*(\d+)", out)
    fails = re.findall(r"^FAIL (.*)$", out, re.M)
    summary[name] = {"pass": int(m.group(1)) if m else None, "total": int(m.group(2)) if m else None, "fails": fails, "exit": p.returncode, "sec": round(time.time() - t0)}
    print(name, summary[name]["pass"], "/", summary[name]["total"], "FAIL:", fails, f"({summary[name]['sec']}s)", flush=True)
    json.dump(summary, open("/app/validation/results/summary.json", "w"), indent=1)


# admin temporar proaspat (stare 'must change') -> t3 il duce pe parola finala
run("reset_tmp_admin")
run("t3_admin_flow")
# cheie de sincronizare noua + 2 noduri MAIN curate
p = subprocess.run([sys.executable, "/app/scripts/issue_sync_key.py", "--source", "cimitir"], capture_output=True, text=True, timeout=120)
key = [l for l in p.stdout.splitlines() if l.startswith("cimitir.")][-1].strip()
S = get_secrets()
S["sync_key"] = key
save_secrets(S)
from nodelib import N  # noqa: E402
for n in ("A", "B"):
    r = N(n, "setup", "http://127.0.0.1:8001", key)
    print("setup", n, r.get("ok"), flush=True)
for name in ("t4_sync", "t4b_reverse", "t5_idem", "t6_conflict", "t6b_resolve", "t9_tomb", "t2_storage", "t7_offline", "t10_portal", "t10b_request_to_main", "t1_persist"):
    run(name)
# persistenta: restart prin supervisor + verificare
subprocess.run(["sudo", "supervisorctl", "restart", "backend"], capture_output=True, timeout=90)
import requests  # noqa: E402
for _ in range(40):
    try:
        if requests.get("http://127.0.0.1:8001/api/health", timeout=3).status_code == 200:
            break
    except requests.RequestException:
        pass
    time.sleep(1.5)
run("t1_persist_after", timeout=300)
for name in ("t11_security", "t11b_logs_audit", "t13_backup", "t14_recovery"):
    run(name)
print("GATA", flush=True)
