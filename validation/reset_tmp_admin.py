import subprocess
from common import conn, get_secrets, scrub
import os, sys
S = get_secrets()
with conn() as c:
    for t in ("users",):
        c.execute("delete from users where username=%s", (S["user"],))
    c.execute("delete from settings where key like %s", ("%" + S["user"] + "%",))
    print("users now:", c.execute("select count(*) from users").fetchone()[0])
env = dict(os.environ, VAL_TMP_PW=S["tmp_password"])
r = subprocess.run([sys.executable, "/app/scripts/create_first_admin.py", "--username", S["user"], "--password-env", "VAL_TMP_PW"], env=env, capture_output=True, text=True, timeout=90)
print("recreate exit", r.returncode, scrub(r.stdout.replace(S["tmp_password"], "***"))[:200])
