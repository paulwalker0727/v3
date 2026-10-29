"""validate_windows.py — validare REALĂ a Centrului Cloud pe copia TEST (Windows), cu Supabase TEST existent. Raport PASS / FAIL / MANUAL.

Rulează prin RUN_VALIDATION.bat (setează izolarea: CIMITIR_DATA_DIR, APPDATA în <copie>\\test_env).
Parola bazei de date se cere ASCUNS în consolă (getpass); nu se scrie nicăieri și nu se trimite în conversație.

Ce NU face (garantat în cod): nu apelează `apply_schema` / nu execută migrări; verificarea PostgreSQL este într-o tranzacție doar-citire;
nu contactează portul 8084; nu scrie în instalarea funcțională, în PROD sau în datele reale (scrie doar în test_env).
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "desktop", ROOT / "shared", ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

PROTECTED_PORT = 8084
PASS, FAIL, MANUAL, SKIP = "PASS", "FAIL", "MANUAL", "SKIP"
DEFAULTS = dict(ref="vcpmcdardnumvlpxknlj", bucket="cimitir-private-test", backend="http://127.0.0.1:8085", expected_migrations=26, port=5432)


class Report:
    def __init__(self, secrets: Optional[List[str]] = None):
        self.rows: List[Dict[str, str]] = []
        self.secrets = [s for s in (secrets or []) if s and len(s) >= 4]

    def add(self, cid: str, status: str, title: str, detail: str = "") -> None:
        self.rows.append({"id": cid, "status": status, "title": title, "detail": self.scrub(detail)})
        print(f"[{status}] {cid} {title}" + (f" — {self.scrub(detail)}" if detail else ""))

    def scrub(self, text: str) -> str:
        for s in self.secrets:
            text = text.replace(s, "***")
        return text

    @property
    def failed(self) -> bool:
        return any(r["status"] == FAIL for r in self.rows)

    def count(self, status: str) -> int:
        return sum(1 for r in self.rows if r["status"] == status)

    def markdown(self) -> str:
        out = [f"# Raport validare Centru Cloud — {time.strftime('%Y-%m-%d %H:%M:%S')}", "",
               f"**PASS {self.count(PASS)} · FAIL {self.count(FAIL)} · MANUAL {self.count(MANUAL)} · SKIP {self.count(SKIP)}**", "",
               "| ID | Stare | Verificare | Detalii |", "|---|---|---|---|"]
        out += [f"| {r['id']} | {r['status']} | {r['title']} | {r['detail'].replace('|', '/')} |" for r in self.rows]
        return self.scrub("\n".join(out)) + "\n"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _inp(cfg: dict):
    import cloud_supabase as sb
    return sb.SupabaseInput(cfg["ref"], "", cfg["host"], cfg["port"], cfg.get("user") or f"postgres.{cfg['ref']}", "postgres", cfg["bucket"])


# ------------------------------------------------------------------ verificări
def check_isolation(rep: Report, env_root: Path) -> bool:
    import cimitir_config
    data = Path(cimitir_config.data_dir())
    appdata = Path(os.environ.get("APPDATA", "") or "x:/none")
    ok = _inside(data, env_root) and (_inside(appdata, env_root) or sys.platform != "win32")
    rep.add("ENV-01", PASS if ok else FAIL, "Izolare: datele și licența copiei sunt în test_env (nu cele reale)", f"data={data}")
    return ok


UNSAFE_OFF = ("cloud_sync_auto", "cloud_entity_sync_enabled", "cloud_media_sync", "cloud_publish_burials", "notify_email_enabled", "lan_opt_in")


def check_copy_settings(rep: Report, env_root: Path) -> bool:
    """Copia nu are voie să scrie în afara test_env sau să sincronizeze automat spre Cloud real."""
    import cimitir_config
    bad_backup, auto_on, prod_active = [], [], []
    for p in _db_files(Path(cimitir_config.data_dir())):
        conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "settings" in tables:
                st = dict(conn.execute("SELECT key, value FROM settings").fetchall())
                if st.get("backup_dir") and not _inside(Path(st["backup_dir"]), env_root):
                    bad_backup.append(f"{p.name}: {st['backup_dir']}")
                auto_on += [f"{p.name}:{k}" for k in UNSAFE_OFF
                            if str(st.get(k, "0")).strip().lower() not in ("", "0", "false", "off")]
            if "cloud_connection_profiles" in tables:
                prod_active += [f"{n} ({e}, {a})" for n, e, a in conn.execute("SELECT name, environment, api_base_url FROM cloud_connection_profiles WHERE is_active=1")
                                if e == "PRODUCTION" or f":{PROTECTED_PORT}" in (a or "")]
        except sqlite3.Error:
            continue
        finally:
            conn.close()
    rep.add("ENV-02", PASS if not bad_backup else FAIL, "Copiile de rezervă ale copiei se scriu în test_env (nu în folderul real de backup)", "; ".join(bad_backup))
    rep.add("ENV-03", PASS if not auto_on and not prod_active else FAIL, "Sincronizările automate sunt oprite și niciun profil activ nu e PROD / portul 8084",
            "; ".join(auto_on + prod_active))
    return not (bad_backup or auto_on or prod_active)


def check_license(rep: Report, env_root: Path) -> None:
    try:
        from license import validator
    except Exception as exc:  # noqa: BLE001
        rep.add("LIC-01", FAIL, "Modulul de licențiere se încarcă", type(exc).__name__)
        return
    res = validator.verifica_licenta_curenta()
    rep.add("LIC-01", PASS if res.ok else FAIL, "Licența .lic existentă este validă (Ed25519 + HWID + expirare)",
            f"client={res.client!r} expira={res.expira or 'permanent'}" if res.ok else str(res.motiv))
    meta = env_root / "install_meta.json"
    want = json.loads(meta.read_text(encoding="utf-8")).get("license_sha256", "") if meta.is_file() else ""
    lic = Path(validator.LICENSE_FILE)
    if want and lic.is_file():
        rep.add("LIC-02", PASS if _sha(lic) == want else FAIL, "Fișierul .lic este byte-identic cu cel real (hash)", "")
    else:
        rep.add("LIC-02", SKIP, "Comparare hash licență", "fără hash de referință (licența nu a fost copiată la instalare)")
    touched = []
    manifest = ROOT / "manifest.json"
    if manifest.is_file():
        files = [f["path"] for f in json.loads(manifest.read_text(encoding="utf-8"))["files"]]
        touched = [p for p in files if p.startswith(("desktop/license/", "desktop/tools/license_manager.py", "desktop/login_window.py", "desktop/auth.py"))]
    rep.add("LIC-03", PASS if not touched else FAIL, "Patch-ul nu atinge licența, generatorul de licențe și autentificarea locală", ", ".join(touched))


def _db_files(data: Path) -> List[Path]:
    out = [data / "cimitir.db"] + sorted(data.glob("registries/*/database.sqlite"))
    return [p for p in out if p.is_file()]


_USERS: List[dict] = []


def check_auth_and_permission(rep: Report) -> None:
    import cimitir_auth
    import cimitir_config
    dbs = _db_files(Path(cimitir_config.data_dir()))
    if not dbs:
        rep.add("AUTH-01", FAIL, "Baza locală cu utilizatori există", "nu am găsit cimitir.db / registries/*/database.sqlite")
        return
    admin_ok, non_admin_bad, users = False, [], 0
    for p in dbs:
        conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
        try:
            integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
            cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
            if not cols:
                rep.add("AUTH-01", SKIP, f"{p.name}: fără tabel users (nu e bază cu conturi)", "")
                continue
            sel = "username, role, active, id" + (", rights" if "rights" in cols else "")
            for row in conn.execute(f"SELECT {sel} FROM users"):
                users += 1
                rights = cimitir_auth.effective_rights(row[1], row[4] if len(row) > 4 else None)
                _USERS.append({"id": row[3], "username": row[0], "role": row[1], "active": row[2], "rights": list(rights)})
                has = cimitir_auth.RIGHT_MANAGE_CONFIG in rights
                if row[1] == cimitir_auth.ROLE_ADMIN and row[2]:
                    admin_ok = admin_ok or has
                elif has:
                    non_admin_bad.append(f"{row[0]}({row[1]})")
        except sqlite3.Error as exc:
            rep.add("AUTH-01", FAIL, "Baza locală se citește", type(exc).__name__)
            return
        finally:
            conn.close()
        rep.add("AUTH-01", PASS if integrity == "ok" else FAIL, f"Baza locală {p.name} accesibilă, integritate", integrity)
    rep.add("AUTH-02", PASS if admin_ok else FAIL, "Există administrator local activ cu dreptul manage_config", f"{users} utilizatori verificați")
    rep.add("AUTH-03", PASS if not non_admin_bad else FAIL, "Doar administratorul are manage_config (operator/user nu)", ", ".join(non_admin_bad))


def check_gui(rep: Report) -> None:
    import access_session
    import cimitir_auth
    from PySide6.QtWidgets import QApplication, QFrame, QMessageBox
    app = QApplication.instance() or QApplication(sys.argv)
    shown = []
    orig, orig_sess = QMessageBox.warning, cimitir_auth.get_session_user
    adm = next((u for u in _USERS if u["role"] == "admin" and u["active"]), {"id": -1, "username": "adm", "role": "admin", "active": 1, "rights": list(cimitir_auth.RIGHTS)})
    op = next((u for u in _USERS if u["role"] != "admin" and u["active"]), {"id": -2, "username": "op", "role": "operator", "active": 1,
                                                                                "rights": list(cimitir_auth.effective_rights("operator"))})
    table = {adm["id"]: adm, op["id"]: op}
    cimitir_auth.get_session_user = lambda uid, cem=None: table.get(int(uid))
    QMessageBox.warning = staticmethod(lambda *a, **k: shown.append(1) or QMessageBox.Ok)
    try:
        access_session.set_user({"id": op["id"], "username": op["username"], "role": op["role"]})
        denied = not access_session.require("manage_config", None, "Centrul Cloud")
        access_session.set_user({"id": adm["id"], "username": adm["username"], "role": "admin"})
        allowed = access_session.require("manage_config", None, "Centrul Cloud")
    finally:
        QMessageBox.warning, cimitir_auth.get_session_user = orig, orig_sess
        access_session.set_user(None)
    rep.add("PERM-01", PASS if denied and allowed and shown else FAIL, "manage_config: utilizatorul non-admin este refuzat (cu mesaj), administratorul permis",
            ("conturi reale din copie" if adm["id"] > 0 and op["id"] > 0 else "ATENȚIE: conturi SINTETICE (nu s-au găsit conturi reale ale ambelor roluri)")
            + f": admin={adm['username']!r}, non-admin={op['username']!r}")
    from cloud_center_dialog import CloudCenterDialog
    d = CloudCenterDialog(None, can_manage=lambda: True)
    titles = [d.tabs.tabText(i) for i in range(d.tabs.count())]
    want = ["A. Stare generală", "B. Supabase", "C. Backend Cloud", "D. Administratori", "E. Backup", "F. Mobile"]
    rep.add("UI-01", PASS if titles == want else FAIL, "Centrul Cloud se deschide și are cele șase file", " | ".join(titles))
    boxes = sorted(f.objectName() for f in d.findChildren(QFrame) if f.objectName().startswith("unavailable_"))
    rep.add("UI-02", PASS if len(boxes) == 4 else FAIL, "Funcțiile neimplementate sunt marcate INDISPONIBIL", ", ".join(boxes))
    blocked = CloudCenterDialog(None, can_manage=lambda: False)
    rep.add("PERM-02", PASS if not blocked.guard() else FAIL, "Dialogul refuză acțiunile când dreptul lipsește", "")
    d.close()
    blocked.close()
    del app


def check_persistence(rep: Report, cfg: dict, env_root: Path) -> None:
    import cloud_simple as simple
    import cloud_supabase as sb
    import database as db
    if not _inside(Path(db.DB_PATH), env_root):
        rep.add("CFG-00", FAIL, "Scriere permisă doar în baza copiei TEST", f"DB={db.DB_PATH}")
        return
    db.init_db()
    import cloud_profiles as cp
    try:
        sb.ensure_profile_for_env("TEST")                                 # profil TEST dedicat; niciun alt profil (PROD) nu este modificat
        simple.save("Cloud TEST (copie)", cfg["backend"], "TEST")
        sb.save_to_profile(_inp(cfg), "TEST", name="Cloud TEST (copie)")
    except cp.ProfileError as exc:
        rep.add("CFG-01", FAIL, "Configurația se salvează în profilul TEST dedicat al copiei", f"{type(exc).__name__}: {exc}")
        return
    code = ("import sys,json;sys.path[:0]=[r'%s',r'%s'];import cloud_profiles as cp;p=cp.active_profile();"
            "print(json.dumps({'ref':p.project_ref,'host':p.db_host,'bucket':p.storage_bucket,'api':p.api_base_url,'env':p.environment,"
            "'pw_ref':p.db_password_ref,'sk_ref':p.supabase_secret_ref}))") % (ROOT / "desktop", ROOT / "shared")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=os.environ.copy(), timeout=60)
    try:
        got = json.loads(out.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        rep.add("CFG-01", FAIL, "Configurația persistă după repornire (proces nou)", out.stderr[-200:])
        return
    want = (cfg["ref"], cfg["host"], cfg["bucket"], cfg["backend"].rstrip("/"), "TEST")
    ok = (got["ref"], got["host"], got["bucket"], got["api"], got["env"]) == want and not (got["pw_ref"] or got["sk_ref"])
    rep.add("CFG-01", PASS if ok else FAIL, "Configurația (ref, gazdă, bucket, backend, TEST) persistă într-un proces nou, fără secrete salvate", "")


def _snapshot(inp, password: str, connect) -> Optional[tuple]:
    import cloud_supabase as sb
    try:
        conn = (connect or sb._default_connect)(**sb._conn_kwargs(inp, password))  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return None
    try:
        conn.read_only = True
        a = conn.execute("SELECT count(*), COALESCE(max(version),0) FROM schema_migrations").fetchone()
        b = conn.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'").fetchone()[0]
        return (int(a[0]), int(a[1]), int(b))
    except Exception:  # noqa: BLE001
        return None
    finally:
        conn.rollback()
        conn.close()


def check_supabase(rep: Report, cfg: dict, password: str, connect: Optional[Callable[..., Any]] = None) -> None:
    import cloud_supabase as sb
    if not password:
        rep.add("SB-01", SKIP, "Verificare Supabase TEST", "parolă nefurnizată (rulează fără --no-db și introdu parola ascuns)")
        return
    inp = _inp(cfg)
    snap = _snapshot(inp, password, connect)
    res = sb.verify_project(inp, password, connect=connect)
    by = {c.name: c for c in res.checks}
    fail_detail = next((c.detail for c in res.checks if c.status == "FAIL"), "conectat")
    rep.add("SB-01", PASS if res.reachable else FAIL, "Conexiune și autentificare la proiectul TEST (doar-citire)", "" if res.reachable else fail_detail)
    tls = by.get("Conexiune criptată (TLS)")
    rep.add("SB-02", PASS if tls and tls.status == "OK" else FAIL, "Conexiune criptată TLS", tls.detail if tls else "negăsit")
    sup = by.get("Proiect Supabase")
    rep.add("SB-03", PASS if sup and sup.status == "OK" else FAIL, "Proiectul este identificat ca Supabase", sup.detail if sup else "negăsit")
    applied = [r for r in (res.analysis or {}).get("migrations", []) if r["state"] == "APLICATĂ"]
    n_exp = cfg["expected_migrations"]
    ok = res.plan is not None and len(applied) == n_exp and not res.pending and not res.plan["blocked"] and bool((res.analysis or {}).get("history_table"))
    rep.add("SB-04", PASS if ok else FAIL, f"Detectează corect cele {n_exp} migrări deja aplicate (0 lipsă, 0 blocate)",
            f"aplicate={len(applied)} lipsă={res.pending} blocate={(res.plan or {}).get('blocked')}")
    rep.add("SB-05", PASS if res.bucket_state == "ok" else FAIL, f"Bucketul {cfg['bucket']} există și este privat", res.bucket_state or "necunoscut")
    if res.plan is not None:
        try:
            pv = sb.preview(res, inp)
            rep.add("SB-06", PASS if not pv["steps"] else FAIL, "Previzualizarea nu propune nicio modificare (nimic de executat)", f"pași={pv['steps']}")
        except sb.CenterError as exc:
            rep.add("SB-06", FAIL, "Previzualizare", str(exc))
    after = _snapshot(inp, password, connect)
    rep.add("SB-07", PASS if snap and snap == after else FAIL, "Verificarea nu a modificat baza (istoric migrări și nr. tabele identice înainte/după)", f"{snap} -> {after}")
    if cfg.get("wrong_password_test"):
        wrong = sb.verify_project(inp, "parola-gresita-intentionat-1", connect=connect)
        rep.add("SB-08", PASS if not wrong.ok else FAIL, "O parolă greșită este respinsă (o singură încercare reală la pooler)", "")
    else:
        rep.add("SB-08", SKIP, "Respingerea parolei greșite", "opțional (face o autentificare eșuată reală la pooler): rulează cu --test-wrong-password")
    bad = sb.verify_project(sb.SupabaseInput("zzzzzzzzzzzzzzzzzzzz", f"https://{cfg['ref']}.supabase.co", cfg["host"], cfg["port"], f"postgres.{cfg['ref']}"),
                            password, connect=lambda **k: 1 / 0)
    rep.add("SB-09", PASS if not bad.ok else FAIL, "Proiect greșit (ref ≠ URL) respins fără a deschide conexiunea", "")


def check_backend(rep: Report, cfg: dict, http: Any = None, admin_factory: Any = None) -> None:
    import cloud_center as cc
    import cloud_simple as simple
    if urlparse(cfg["backend"]).port == PROTECTED_PORT:
        rep.add("BE-00", FAIL, "Backendul indicat este portul protejat 8084 — refuzat", "")
        return
    rep.add("BE-00", PASS, "Portul 8084 (protejat) nu este folosit", cfg["backend"])
    h = cc.backend_health(cfg["backend"], http)
    rep.add("BE-01", PASS if h["ready"] else FAIL, "Backend Cloud TEST răspunde la /api/v1/health", h["detail"])
    if not h["ready"]:
        return
    st = cc.admin_state(cfg["backend"], admin_factory)
    if not st.reachable:
        rep.add("BE-02", FAIL, "Interfața de administrare Cloud (/api/v1/admin/status) răspunde", st.detail)
    elif st.initialized:
        rep.add("BE-02", PASS, "Administratorul Cloud există deja; inițializarea este închisă (corect: operațiune unică)", st.detail)
    elif st.bootstrap_open:
        rep.add("BE-02", MANUAL, "Inițializarea Cloud este DESCHISĂ: primul administrator se poate crea din fila D (cu tokenul tehnic al serverului)", st.detail)
    else:
        rep.add("BE-02", MANUAL, "Inițializarea Cloud este ÎNCHISĂ și nu există administrator: serverul TEST nu are BOOTSTRAP_TOKEN valid (intervenție manuală pe server, cu aprobare)", st.detail)
    pair = simple.pairing_state()
    if pair["pairing"] in (simple.PAIR_OK, simple.PAIR_UNVERIFIED):
        prof = simple.current_profile()
        ok, text = simple.test_connection(prof, http=http) if http else simple.test_connection(prof)
        rep.add("BE-03", PASS if ok else FAIL, "MAIN este asociat și cheia Binding este acceptată de backend (citire)", text.replace("\n", "; "))
    else:
        rep.add("BE-03", MANUAL, "Asocierea MAIN cu backendul se face din UI (fila C → Asociază) cu contul administrator Cloud", simple._PAIR_TEXT[pair["pairing"]])  # noqa: SLF001


def check_no_secret_leak(rep: Report, env_root: Path, password: str) -> None:
    if not password or len(password) < 4:
        return
    needle, hits = password.encode(), []
    for p in env_root.rglob("*"):
        if p.is_file() and p.stat().st_size < 200_000_000 and "secrets" not in p.parts:
            try:
                if needle in p.read_bytes():
                    hits.append(str(p.relative_to(env_root)))
            except OSError:
                continue
    rep.add("SEC-01", PASS if not hits else FAIL, "Parola bazei de date nu apare în niciun fișier din test_env (baze, loguri, rapoarte)", ", ".join(hits[:5]))


MANUAL_STEPS = [
    ("MAN-01", "Deschide RUN_MAIN_TEST.bat, autentifică-te ca administrator local, apasă „⚙ Centru Cloud” și parcurge cele 6 file."),
    ("MAN-02", "Fila B: „1. Verifică proiectul” + parola (ascunsă). Așteptat: toate [OK], „toate cele 26 migrări sunt aplicate”, bucket privat. NU apăsa „Instalează”."),
    ("MAN-03", "Fila C: „Verifică conexiunea” pe http://127.0.0.1:8085; apoi „Asociază acest calculator…” (necesită cont administrator Cloud)."),
    ("MAN-04", "Fila D: „Verifică starea inițializării”. Crearea primului administrator Cloud cere BOOTSTRAP_TOKEN pe serverul 8085 (nu se face fără aprobarea ta)."),
    ("MAN-05", "Fila F: „Asociază telefoane (cod / QR)…” după asocierea MAIN."),
]


def run(cfg: dict, env_root: Path, password: str = "", connect=None, http=None, admin_factory=None, gui: bool = True) -> Report:
    rep = Report([password])
    try:
        import cloud_supabase as sb
        if password:
            sb.setup_manager()[2].register(password)
    except Exception:  # noqa: BLE001
        pass
    if check_isolation(rep, env_root):
        safe = check_copy_settings(rep, env_root)
        check_license(rep, env_root)
        check_auth_and_permission(rep)
        if safe:
            check_persistence(rep, cfg, env_root)
        else:
            rep.add("CFG-01", SKIP, "Persistența configurației", "omisă: copia nu este izolată (vezi ENV-02/ENV-03); nu se scrie nimic")
        if gui:
            check_gui(rep)
        check_supabase(rep, cfg, password, connect)
        check_backend(rep, cfg, http, admin_factory)
        check_no_secret_leak(rep, env_root, password)
    for cid, text in MANUAL_STEPS:
        rep.add(cid, MANUAL, text)
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", default=DEFAULTS["ref"])
    ap.add_argument("--host", help="gazda Session pooler (ex. aws-0-eu-central-1.pooler.supabase.com) — din Supabase → Connect")
    ap.add_argument("--port", type=int, default=DEFAULTS["port"])
    ap.add_argument("--user", help="implicit postgres.<ref>")
    ap.add_argument("--bucket", default=DEFAULTS["bucket"])
    ap.add_argument("--backend", default=DEFAULTS["backend"])
    ap.add_argument("--expected-migrations", type=int, default=DEFAULTS["expected_migrations"])
    ap.add_argument("--no-db", action="store_true", help="omite verificarea Supabase (fără parolă)")
    ap.add_argument("--test-wrong-password", action="store_true", help="face O încercare reală cu parolă greșită (poate declanșa limitarea autentificărilor eșuate la pooler)")
    a = ap.parse_args(argv)
    env_root = Path(os.environ.get("CIMITIR_ENV_ROOT", ""))
    if not os.environ.get("CIMITIR_ENV_ROOT") or not env_root.is_dir():
        print("[OPRIT] Rulează prin RUN_VALIDATION.bat (izolarea test_env nu este activă). Nu s-a făcut nimic.")
        return 2
    host = a.host or input("Gazda Session pooler Supabase (nu e secret; ex. aws-0-eu-central-1.pooler.supabase.com): ").strip()
    password = ""
    if not a.no_db:
        password = os.environ.get("CIMITIR_VALIDATION_DB_PASSWORD") or getpass.getpass("Parola bazei de date Supabase TEST (ascunsă, nu se salvează): ")
    cfg = dict(ref=a.ref, host=host, port=a.port, user=a.user, bucket=a.bucket, backend=a.backend, expected_migrations=a.expected_migrations,
               wrong_password_test=a.test_wrong_password)
    rep = run(cfg, env_root, password)
    out = env_root / "reports"
    out.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    (out / f"validation_{stamp}.md").write_text(rep.markdown(), encoding="utf-8")
    (out / f"validation_{stamp}.json").write_text(rep.scrub(json.dumps(rep.rows, ensure_ascii=False, indent=1)), encoding="utf-8")
    print(f"\nRaport: {out / f'validation_{stamp}.md'}")
    print("REZULTAT:", "FAIL" if rep.failed else "PASS (fără eșecuri; pașii MANUAL rămân de parcurs)")
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
