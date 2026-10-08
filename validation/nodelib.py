import json, subprocess, sys
def N(name, *args, timeout=180):
    r = subprocess.run([sys.executable, "/app/validation/node.py", name, *map(str, args)], capture_output=True, text=True, timeout=timeout)
    line = [l for l in r.stdout.splitlines() if l.startswith("@@")]
    if not line:
        return {"error": "no output", "stderr": r.stderr[-500:], "stdout": r.stdout[-300:]}
    return json.loads(line[-1][2:])
