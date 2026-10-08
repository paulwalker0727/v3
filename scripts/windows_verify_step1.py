"""Verificare REALĂ pe Windows pentru Etapa 4 / Pasul 1 (DPAPI, izolarea secretelor, _pid_alive, lock între procese, secret în SQLite/loguri/backup).

Rulare (PowerShell, din rădăcina repo-ului, cu Python-ul aplicației):
    python scripts\\windows_verify_step1.py            # verificare reală (doar Windows)
    python scripts\\windows_verify_step1.py --json out.json

Pe alt sistem decât Windows scriptul NU poate verifica DPAPI/PID Windows și întoarce `NOT VERIFIED` (cod 2). `--logic-check` rulează aceeași
logică cu `protect/unprotect` FALSE (NU e DPAPI), doar ca să valideze scriptul însuși pe Linux; rezultatul nu înlocuiește verificarea Windows.
Folosește exclusiv foldere temporare. Nu atinge Supabase/Render/Banovița.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))
RESULTS: dict = {}


def check(group: str, name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.setdefault(group, []).append({"check": name, "ok": bool(ok), "detail": detail})
    print(f"  [{'PASS' if ok else 'FAIL'}] {group}: {name} {detail}")
    return bool(ok)


def make_store(root: Path, logic_check: bool):
    import cimitir_secretstore as ss
    if logic_check:
        def prot(b):
            return bytes(x ^ 0x5A for x in b[::-1]) + b"FAKE"

        def unprot(b):
            assert b.endswith(b"FAKE")
            return bytes(x ^ 0x5A for x in b[:-4])[::-1]
        return ss.DpapiSecretStore(root, protect=prot, unprotect=unprot)
    return ss.DpapiSecretStore(root)


def leaks(blob: bytes, secret: str) -> bool:
    return any(n in blob for n in (secret.encode(), base64.b64encode(secret.encode()), base64.urlsafe_b64encode(secret.encode())))


def dpapi_checks(tmp: Path, logic_check: bool) -> None:
    import cimitir_secretstore as ss
    g = "DPAPI"
    st = make_store(tmp / "dpapi", logic_check)
    s1 = "cimitir.AAAA.SECRET-ONE-" + os.urandom(6).hex()
    check(g, "set + get round-trip", (st.set("binding/AAAA", s1), st.get("binding/AAAA"))[1] == s1)
    blob = next((tmp / "dpapi").glob("*.bin")).read_bytes()
    check(g, "blob-ul de pe disc nu conține secretul (nici base64)", not leaks(blob, s1))
    s2 = "cimitir.AAAA.SECRET-TWO-" + os.urandom(6).hex()
    st.set("binding/AAAA", s2)
    check(g, "rotate: valoarea nouă înlocuiește pe cea veche", st.get("binding/AAAA") == s2 and not leaks(next((tmp / "dpapi").glob("*.bin")).read_bytes(), s1))
    st.delete("binding/AAAA")
    check(g, "delete: secretul dispare (get=None, fișier șters)", st.get("binding/AAAA") is None and not list((tmp / "dpapi").glob("*.bin")))
    st.delete("binding/AAAA")
    check(g, "delete idempotent", True)
    (tmp / "dpapi").mkdir(exist_ok=True)
    (tmp / "dpapi" / "binding__BAD.bin").write_bytes(b"gunoi")
    try:
        st.get("binding/BAD")
        check(g, "blob corupt => eroare, nu date false", False)
    except ss.SecretStoreError:
        check(g, "blob corupt => eroare, nu date false", True)
    (tmp / "dpapi" / "binding__BAD.bin").unlink()
    # izolare între două binding_uid
    g = "ISOLATION"
    a, b = "cimitir.UID-A.sa-" + os.urandom(6).hex(), "cimitir.UID-B.sb-" + os.urandom(6).hex()
    st.set("binding/UID-A", a)
    st.set("binding/UID-B", b)
    check(g, "fiecare binding_uid își citește propriul secret", st.get("binding/UID-A") == a and st.get("binding/UID-B") == b)
    blobs = {p.name: p.read_bytes() for p in (tmp / "dpapi").glob("*.bin")}
    check(g, "fișierele sunt distincte și fără secretul celuilalt", len(blobs) == 2 and all(not leaks(x, a) and not leaks(x, b) for x in blobs.values()))
    check(g, "alt binding_uid inexistent => None", st.get("binding/UID-C") is None)
    st.delete("binding/UID-A")
    check(g, "ștergerea unuia nu atinge celălalt", st.get("binding/UID-A") is None and st.get("binding/UID-B") == b)
    for bad in ("binding/../UID-B", "../x", "a b"):
        try:
            st.get(bad)
            check(g, f"nume invalid respins: {bad!r}", False)
        except ss.SecretStoreError:
            check(g, f"nume invalid respins: {bad!r}", True)
    st.delete("binding/UID-B")


def pid_checks(tmp: Path) -> None:
    import cimitir_bootstrap as bs
    g = "PID"
    check(g, "procesul curent este viu", bs._pid_alive(os.getpid()))
    check(g, "pid 0 / negativ => mort", not bs._pid_alive(0) and not bs._pid_alive(-5))
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        time.sleep(0.5)
        check(g, "copil viu => viu", bs._pid_alive(child.pid))
        child.terminate()
        deadline = time.time() + 15
        while time.time() < deadline and bs._pid_alive(child.pid):
            time.sleep(0.1)
        check(g, "copil terminat (handle încă deschis, fără wait) => mort", not bs._pid_alive(child.pid))
        child.wait()
        check(g, "copil terminat și colectat => mort", not bs._pid_alive(child.pid))
    finally:
        if child.poll() is None:
            child.kill()
    check(g, "pid inexistent foarte mare => mort", not bs._pid_alive(999_999_999))


HOLDER = r'''
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import cimitir_bootstrap as bs
class B: pass
b = B(); b.root = Path(sys.argv[2])
bs.acquire_lock(b, "holder-bootstrap")
Path(sys.argv[3]).write_text("held")
time.sleep(300)
'''


def lock_checks(tmp: Path) -> None:
    import cimitir_bootstrap as bs
    import cimitir_cloud_control as cc

    class B:
        pass

    g = "LOCK"
    b = B()
    b.root = tmp / "lockreg"
    b.root.mkdir()
    flag = tmp / "held.flag"
    child = subprocess.Popen([sys.executable, "-c", HOLDER, str(ROOT / "shared"), str(b.root), str(flag)])
    try:
        deadline = time.time() + 30
        while not flag.exists() and time.time() < deadline:
            time.sleep(0.1)
        check(g, "procesul copil a luat lock-ul", flag.exists())
        try:
            bs.acquire_lock(b, "altul")
            check(g, "al doilea proces este REFUZAT controlat", False)
        except cc.BootstrapLocked:
            check(g, "al doilea proces este REFUZAT controlat", True)
        check(g, "lock_held vede deținătorul viu", bs.lock_held(b))
        child.kill()
        child.wait()
        time.sleep(0.5)
        check(g, "după moartea deținătorului lock-ul se eliberează (fără PID)", not bs.lock_held(b))
        try:
            bs.acquire_lock(b, "altul")
            check(g, "noul proces poate lua lock-ul", True)
            bs.release_lock(b)
        except cc.BootstrapLocked:
            check(g, "noul proces poate lua lock-ul", False)
    finally:
        if child.poll() is None:
            child.kill()


def secret_scan_checks(tmp: Path, logic_check: bool) -> None:
    import cimitir_backup
    import cimitir_bootstrap as bs
    import cimitir_cloud_control as cc
    import cimitir_registry as reg
    g = "NO-LEAK"
    data = tmp / "appdata"
    data.mkdir()
    cat = reg.RegistryCatalog(data)
    cp = cc.LocalControlPlane(tmp / "cloud.sqlite")
    pa = cp.create_principal("pa", True)
    owner = cp.create_principal("owner")
    import uuid
    org_cloud = str(uuid.uuid4())
    cp.create_organization(pa, org_cloud, "Org", first_owner=owner)
    with cat._conn() as c:
        c.execute("INSERT INTO organizations (uid, org_type, official_name, display_name, active) VALUES (?,?,?,?,1)", (org_cloud, "parohie", "Org", "Org"))
    cem = cat.create_cemetery(org_cloud, "Cimitir")
    b = cat.binding_for(cem)
    reg.prepare_registry_db(b)
    inst = bs.installation_uid(cat)
    store = make_store(tmp / "secrets", logic_check)
    code = cp.issue_code(owner, org_cloud)
    plan = bs.plan_flow_a(b, cp, code, inst, cemetery_name="Cimitir")
    log = tmp / "app.log"
    rep = bs.execute_flow_a(b, cp, store, plan, plan["plan_hash"], code, inst)
    log.write_text(json.dumps(rep), encoding="utf-8")
    key = store.get(bs.LocalRegistry(b).get("cloud_secret_ref"))
    check(g, "Flux A complet cu SecretStore real (BOUND)", rep["result"] == "completed" and bool(key))
    bk = cimitir_backup.backup(b.root, tmp / "backups")
    bad = [str(f) for f in tmp.rglob("*") if f.is_file() and leaks(f.read_bytes(), key)]
    check(g, "secretul nu apare în SQLite (registru + cloud + WAL), loguri, backup, secrets/", not bad, ",".join(bad))
    check(g, "backup-ul registrului s-a creat și se verifică", bool(list(bk.rglob("cimitir.db"))) and cimitir_backup.verify(bk) == [])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logic-check", action="store_true", help="validează scriptul cu protect FALS (NU e DPAPI real)")
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    if sys.platform != "win32" and not args.logic_check:
        print("DPAPI WINDOWS REAL: NOT VERIFIED (nu rulează pe Windows)\nPID CHECK WINDOWS: NOT VERIFIED (nu rulează pe Windows)")
        return 2
    tmp = Path(tempfile.mkdtemp(prefix="cimitir-win-verify-"))
    os.environ.update({"CIMITIR_DATA_DIR": str(tmp / "appdata"), "CIMITIR_IGNORE_PROJECT_ENV": "1", "CIMITIR_DB_ENGINE": "sqlite",
                       "CIMITIR_MODE": "local", "DATABASE_URL": "", "CIMITIR_JOBS": "0"})
    try:
        print(f"Platformă: {sys.platform} | Python {sys.version.split()[0]} | temp {tmp}" + ("  [LOGIC-CHECK, NU DPAPI REAL]" if args.logic_check else ""))
        for fn in (lambda: dpapi_checks(tmp, args.logic_check), lambda: pid_checks(tmp), lambda: lock_checks(tmp), lambda: secret_scan_checks(tmp, args.logic_check)):
            try:
                fn()
            except Exception as exc:  # o eroare neașteptată = FAIL, nu crash mut
                check("EXCEPTION", getattr(fn, "__name__", "step"), False, f"{type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    verdict = lambda groups: "PASS" if all(c["ok"] for g in groups for c in RESULTS.get(g, [])) and any(RESULTS.get(g) for g in groups) else "FAIL"  # noqa: E731
    mode = " (LOGIC-CHECK, NU DPAPI REAL)" if args.logic_check else ""
    dp = verdict(["DPAPI", "ISOLATION", "NO-LEAK"]) if not RESULTS.get("EXCEPTION") else "FAIL"
    pid = verdict(["PID"]) if not RESULTS.get("EXCEPTION") else "FAIL"
    lock = verdict(["LOCK"]) if not RESULTS.get("EXCEPTION") else "FAIL"
    print(f"\nDPAPI WINDOWS REAL: {dp}{mode}\nPID CHECK WINDOWS: {pid}{mode}\nLOCK WINDOWS (între procese): {lock}{mode}")
    if args.json:
        Path(args.json).write_text(json.dumps({"platform": sys.platform, "logic_check": args.logic_check, "dpapi": dp, "pid": pid, "lock": lock, "results": RESULTS}, indent=1),
                                   encoding="utf-8")
    return 0 if dp == pid == lock == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
