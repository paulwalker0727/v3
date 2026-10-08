"""Ruleaza testele live (etapa6/7/8/9b) pe preview mutat TEMPORAR pe PostgreSQL local + storage local. Restaureaza .env garantat."""
import os, shutil, subprocess, sys, time, requests
ENV = "/app/backend/.env"; BAK = "/app/validation/.env.cloud.bak"
def sup(c): subprocess.run(["sudo", "supervisorctl", c, "backend"], capture_output=True, timeout=90)
def up():
    for _ in range(40):
        try:
            if requests.get("http://127.0.0.1:8001/api/health", timeout=3).status_code in (200, 503): return True
        except requests.RequestException: pass
        time.sleep(1.5)
shutil.copy(ENV, BAK); os.chmod(BAK, 0o600)
base = open(ENV).read().split("# ==================================================")[0]
local_env = base.rstrip("\n") + '''

# ==================================================
# CONFIGURARE CLOUD - SUPABASE  (TEMPORAR: mediu local de test, fara cloud)
# ==================================================
CIMITIR_MODE=online
CIMITIR_DB_ENGINE=postgres
DATABASE_URL="postgresql://cimitir@127.0.0.1:55432/cimitir_livetest"
SUPABASE_URL=""
SUPABASE_SECRET_KEY=""
SUPABASE_BUCKET="cimitir-private"
CIMITIR_STORAGE_DRIVER="local"
'''
try:
    subprocess.run(["psql", "-h", "127.0.0.1", "-p", "55432", "-U", "postgres", "-c", "DROP DATABASE IF EXISTS cimitir_livetest", "-c", "CREATE DATABASE cimitir_livetest OWNER cimitir"], capture_output=True, env={**os.environ, "PATH": "/usr/lib/postgresql/17/bin:" + os.environ["PATH"]})
    open(ENV, "w").write(local_env); sup("restart"); assert up()
    r = requests.get("http://127.0.0.1:8001/api/public/status", timeout=10).json(); print("server temporar:", r.get("database"), flush=True)
    env = {**os.environ, "CIMITIR_TEST_DATABASE_URL": "postgresql://cimitir@127.0.0.1:55432/cimitir_test"}
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", "0", "tests/test_etapa6_entity_sync.py", "tests/test_etapa8_system.py", "tests/test_etapa11_e2e_live.py", "tests/test_live_smoke.py"],
                       cwd="/app/backend", env=env, capture_output=True, text=True, timeout=1500)
    open("/app/validation/pytest_live_local.log", "w").write(p.stdout[-20000:] + p.stderr[-3000:])
    print("pytest exit", p.returncode, flush=True); print(p.stdout.strip().splitlines()[-1], flush=True)
finally:
    shutil.copy(BAK, ENV); sup("restart"); up()
    print("ENV cloud restaurat; health:", requests.get("http://127.0.0.1:8001/api/health", timeout=10).text[:70], flush=True)
