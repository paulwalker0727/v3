"""install_test_copy.py — instalează Centrul Cloud într-o COPIE TEST a lui MAIN, fără a modifica instalarea funcțională (doar bibliotecă standard).

Rulează din rădăcina pachetului ZIP:
    py -3 install_test_copy.py --source D:\\v3-main --target D:\\v3-main-cloudcenter-test [--live-data D:\\v3-main\\shared_data]

Garanții:
- SURSA se citește doar (nicio scriere); se compară o amprentă (cale+mărime+mtime) înainte/după.
- Verifică exact versiunea: fișierele modificate de patch trebuie să fie identice cu cele din commitul de bază 60f292c; orice diferență oprește instalarea
  ÎNAINTE de a crea ceva (se poate trece peste doar cu --allow-drift, iar fișierul se raportează).
- Datele (SQLite, registre, secrete DPAPI) și licența se COPIAZĂ în <target>/test_env; copia rulează cu CIMITIR_DATA_DIR și APPDATA proprii,
  deci nu poate scrie în datele sau licența reale.
- Fișierele .env din proiect NU se copiază (copia nu moștenește DATABASE_URL/chei).
- Nu atinge niciun container Docker (nici 8084, nici 8085) și nu face cereri de rețea.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

BUNDLE = Path(__file__).resolve().parent
IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache", "shared_data", "shared_data-backups", "dist", "build"}
IGNORE_FILES = {".env", ".env.local", ".env.production"}
PROTECTED_PORT = "8084"


class InstallError(Exception):
    pass


def sha(path: Path, normalize: bool = False) -> str:
    data = path.read_bytes()
    if normalize:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def fingerprint(root: Path, skip_dirs=frozenset({".git", "__pycache__", "node_modules"})) -> Dict[str, object]:
    """Amprentă ieftină a unui arbore: număr fișiere, octeți, hash peste (cale, mărime, mtime_ns)."""
    h, n, total = hashlib.sha256(), 0, 0
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in skip_dirs)
        for fn in sorted(fns):
            p = Path(dp) / fn
            try:
                st = p.stat()
            except OSError:
                continue
            h.update(f"{p.relative_to(root).as_posix()}|{st.st_size}|{st.st_mtime_ns}\n".encode())
            n += 1
            total += st.st_size
    return {"files": n, "bytes": total, "digest": h.hexdigest()}


def load_manifest(bundle: Path) -> dict:
    mf = bundle / "manifest.json"
    if not mf.is_file():
        raise InstallError("manifest.json lipsește din pachet.")
    return json.loads(mf.read_text(encoding="utf-8"))


def check_exact_version(source: Path, manifest: dict) -> Dict[str, List[str]]:
    """Compară fișierele modificate de patch cu varianta din commitul de bază; întoarce {'ok': [...], 'drift': [...], 'missing': [...]}."""
    out = {"ok": [], "drift": [], "missing": [], "added_conflict": []}
    for f in manifest["files"]:
        p = source / f["path"]
        if f["status"] == "M":
            if not p.is_file():
                out["missing"].append(f["path"])
            elif f["base_sha256"] in (sha(p), sha(p, True)) or f.get("base_sha256_lf") == sha(p, True):
                out["ok"].append(f["path"])
            else:
                out["drift"].append(f["path"])
        elif p.exists():
            out["added_conflict"].append(f["path"])
    return out


def _ignore(_dir, names):
    return [n for n in names if n in IGNORE_DIRS or n in IGNORE_FILES or n.endswith(".pyc")]


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def detect_live_data(source: Path) -> Optional[Path]:
    raw = os.environ.get("CIMITIR_DATA_DIR", "").strip()
    if raw and Path(raw).is_dir():
        return Path(raw)
    for cand in (source / "shared_data", Path(os.environ.get("LOCALAPPDATA", "")) / "Cimitir"):
        if cand.is_dir() and ((cand / "cimitir.db").exists() or (cand / "registry.json").exists()):
            return cand
    return None


def live_license() -> Optional[Path]:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    p = Path(base) / "CimitirDesktop" / "license.key"
    return p if p.is_file() else None


# Setări din baza copiată care ar scrie în afara copiei sau ar sincroniza spre Cloud real; se neutralizează DOAR în copie.
UNSAFE_OFF = ("cloud_sync_auto", "cloud_entity_sync_enabled", "cloud_media_sync", "cloud_publish_burials", "notify_email_enabled", "lan_opt_in")


def sanitize_copied_databases(env: Path, log=print) -> dict:
    """În bazele COPIATE (test_env/data): backup_dir -> test_env/backups, sincronizările automate oprite, profilul Cloud activ PROD/8084 dezactivat."""
    import sqlite3
    changes: dict = {"databases": [], "settings_changed": [], "profiles_deactivated": [], "errors": []}
    dbs = [env / "data" / "cimitir.db"] + sorted((env / "data").glob("registries/*/database.sqlite"))
    for p in dbs:
        if not p.is_file():
            continue
        conn = sqlite3.connect(p)
        try:
            _sanitize_one(conn, env, p, changes)
        except sqlite3.Error as exc:
            changes["errors"].append(f"{p.name}: {type(exc).__name__}")
        finally:
            conn.close()
    return changes


def _sanitize_one(conn, env: Path, p: Path, changes: dict) -> None:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "settings" in tables:
        have = {k: v for k, v in conn.execute("SELECT key, value FROM settings")}
        if have.get("backup_dir"):
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('backup_dir', ?)", (str(env / "backups"),))
            changes["settings_changed"].append("backup_dir")
        for k in ("cloud_url", "cloud_api_url"):
            if f":{PROTECTED_PORT}" in str(have.get(k, "")):
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, '')", (k,))
                changes["settings_changed"].append(k)
        for k in UNSAFE_OFF:
            if str(have.get(k, "0")).strip().lower() not in ("", "0", "false", "off"):
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, '0')", (k,))
                changes["settings_changed"].append(k)
    if "cloud_connection_profiles" in tables:
        for pid, name, envn, api in conn.execute("SELECT id, name, environment, api_base_url FROM cloud_connection_profiles WHERE is_active=1").fetchall():
            if envn == "PRODUCTION" or f":{PROTECTED_PORT}" in (api or ""):
                conn.execute("UPDATE cloud_connection_profiles SET is_active=0 WHERE id=?", (pid,))
                changes["profiles_deactivated"].append(f"{name} ({envn})")
    conn.commit()
    changes["databases"].append(str(p.relative_to(env)))


BAT_ENV = r"""@echo off
setlocal
set "HERE=%~dp0"
set "CIMITIR_ENV_ROOT=%HERE%test_env"
set "CIMITIR_DATA_DIR=%CIMITIR_ENV_ROOT%\data"
set "APPDATA=%CIMITIR_ENV_ROOT%\appdata"
set "CIMITIR_BACKUP_DIR=%CIMITIR_ENV_ROOT%\backups"
set "CIMITIR_LOG_DIR=%CIMITIR_ENV_ROOT%\logs"
set "CIMITIR_IGNORE_PROJECT_ENV=1"
set "CIMITIR_DB_ENGINE=sqlite"
set "CIMITIR_MODE=local"
set "DATABASE_URL="
if "%CIMITIR_PYTHON%"=="" set "CIMITIR_PYTHON=python"
"""


def _crlf(text: str) -> bytes:
    return text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")


def write_launchers(target: Path) -> None:
    (target / "RUN_MAIN_TEST.bat").write_bytes(
        _crlf(BAT_ENV + 'echo MAIN (COPIE TEST) - date: %CIMITIR_DATA_DIR%\ncd /d "%HERE%desktop"\n"%CIMITIR_PYTHON%" main.py\nendlocal\n'))
    (target / "RUN_VALIDATION.bat").write_bytes(
        _crlf(BAT_ENV + 'cd /d "%HERE%"\n"%CIMITIR_PYTHON%" deploy\\windows_validation\\validate_windows.py %*\npause\nendlocal\n'))


def install(source: Path, target: Path, live_data: Optional[Path], bundle: Path = BUNDLE, allow_drift: bool = False, log=print) -> dict:
    source, target = source.resolve(), target.resolve()
    rep: dict = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "source": str(source), "target": str(target), "checks": [], "result": "FAIL"}

    def chk(cid, ok, detail=""):
        rep["checks"].append({"id": cid, "status": "PASS" if ok else "FAIL", "detail": detail})
        log(f"[{'PASS' if ok else 'FAIL'}] {cid} {detail}")
        return ok

    if not (source / "desktop" / "main.py").is_file():
        raise InstallError(f"{source} nu pare o instalare MAIN (lipsește desktop/main.py).")
    if target.exists() and any(target.iterdir()):
        raise InstallError(f"Destinația există și nu este goală: {target}. Alege alt folder (nu se suprascrie nimic).")
    if source == target or _inside(target, source) or _inside(source, target):
        raise InstallError("Destinația nu poate fi egală cu sursa sau în interiorul ei (și invers).")
    if live_data and (_inside(target, live_data) or _inside(live_data, target)):
        raise InstallError("Destinația nu poate fi în folderul de date real.")
    manifest = load_manifest(bundle)
    rep["bundle"] = {"base_commit": manifest["base_commit"], "files": len(manifest["files"])}

    before = fingerprint(source)
    ver = check_exact_version(source, manifest)
    rep["version_check"] = ver
    exact = not (ver["drift"] or ver["missing"] or ver["added_conflict"])
    chk("VER-01 versiunea MAIN corespunde commitului de bază", exact,
        f"{len(ver['ok'])} fișiere identice" + (f"; DIFERENȚE: {ver['drift'] + ver['missing'] + ver['added_conflict']}" if not exact else ""))
    if not exact and not allow_drift:
        raise InstallError("Versiunea MAIN diferă de commitul de bază pentru: " + ", ".join(ver["drift"] + ver["missing"] + ver["added_conflict"])
                           + ". Nu s-a creat nimic. Trimite lista pentru adaptarea patch-ului (sau rulează cu --allow-drift în copie).")

    log(f"Copiez {source} -> {target} (fără .git, .env, date, node_modules)…")
    shutil.copytree(source, target, ignore=_ignore, dirs_exist_ok=True)
    applied, skipped = [], []
    for f in manifest["files"]:
        if f["path"] in ver["drift"] + ver["missing"] + ver["added_conflict"]:
            skipped.append(f["path"])
            continue
        src = bundle / "overlay" / f["path"]
        dst = target / f["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        applied.append(f["path"])
    bad = [p for p in applied if sha(target / p) != next(x["new_sha256"] for x in manifest["files"] if x["path"] == p)]
    chk("OVL-01 fișierele patch-ului copiate și verificate prin hash", not bad and bool(applied), f"{len(applied)} aplicate, {len(skipped)} omise" + (f"; HASH GREȘIT: {bad}" if bad else ""))
    rep["applied"], rep["skipped"] = applied, skipped

    env = target / "test_env"
    for d in ("data", "appdata/CimitirDesktop", "backups", "reports", "logs"):
        (env / d).mkdir(parents=True, exist_ok=True)
    if live_data and live_data.is_dir():
        shutil.copytree(live_data, env / "data", dirs_exist_ok=True)
        chk("DAT-01 datele copiate în test_env (copie, originalul neatins)", True, f"{live_data} -> {env / 'data'}")
    else:
        chk("DAT-01 date reale găsite", False, "nu am găsit folderul de date; copia pornește cu bază NOUĂ (specifică --live-data pentru a testa pe datele tale)")
    san = sanitize_copied_databases(env)
    rep["sanitized"] = san
    chk("SAN-01 setările copiei neutralizate (backup în test_env, sincronizări automate oprite, profil PROD/8084 dezactivat — doar în copie)", not san["errors"],
        f"setări: {san['settings_changed'] or 'nimic de schimbat'}; profile dezactivate: {san['profiles_deactivated'] or 'niciunul'}"
        + (f"; ERORI: {san['errors']} (NU porni copia până nu verificăm)" if san["errors"] else ""))
    lic = live_license()
    if lic:
        shutil.copy2(lic, env / "appdata" / "CimitirDesktop" / "license.key")
        same = sha(lic) == sha(env / "appdata" / "CimitirDesktop" / "license.key")
        chk("LIC-01 licența .lic copiată identic (hash egal)", same, "hash păstrat în raport")
        rep["license_sha256"] = sha(lic)
    else:
        chk("LIC-01 licența .lic găsită în %APPDATA%\\CimitirDesktop", False, "copia va cere activarea licenței (nu activa pe copie dacă nu vrei un HWID nou)")
    write_launchers(target)
    shutil.copy2(bundle / "manifest.json", target / "manifest.json")
    (env / "install_meta.json").write_text(json.dumps({"license_sha256": rep.get("license_sha256", ""), "source": str(source),
                                                       "live_data": str(live_data or "")}, indent=1), encoding="utf-8")

    after = fingerprint(source)
    chk("SRC-01 instalarea sursă NEMODIFICATĂ (amprentă identică înainte/după)", before == after, f"{before['files']} fișiere, {before['bytes']} octeți")
    chk("SAF-01 niciun container/port atins (scriptul nu folosește Docker/rețea)", True, f"port protejat {PROTECTED_PORT}")
    rep["result"] = "PASS" if all(c["status"] == "PASS" for c in rep["checks"] if not c["id"].startswith(("DAT-01", "LIC-01"))) else "FAIL"
    (env / "reports" / "install_report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="instalarea MAIN funcțională (ex. D:\\v3-main) — se citește doar")
    ap.add_argument("--target", required=True, help="folder NOU pentru copia TEST (ex. D:\\v3-main-cloudcenter-test)")
    ap.add_argument("--live-data", help="folderul de date MAIN (implicit: detectat); se COPIAZĂ în test_env")
    ap.add_argument("--allow-drift", action="store_true", help="continuă în copie chiar dacă unele fișiere diferă de commitul de bază (acestea se omit)")
    a = ap.parse_args(argv)
    source = Path(a.source)
    live = Path(a.live_data) if a.live_data else detect_live_data(source)
    try:
        rep = install(source, Path(a.target), live, allow_drift=a.allow_drift)
    except InstallError as exc:
        print(f"[OPRIT] {exc}")
        return 2
    print(f"\nREZULTAT INSTALARE: {rep['result']}. Pornește: {Path(a.target) / 'RUN_MAIN_TEST.bat'} ; validare: RUN_VALIDATION.bat")
    return 0 if rep["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
