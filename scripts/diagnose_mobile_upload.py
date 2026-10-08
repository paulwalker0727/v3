#!/usr/bin/env python
"""Diagnostic „Continuă pe telefon” (Pasul 6): URL public, token/QR, FastAPI, PostgreSQL, Storage.

Rulează pe serverul care are backend/.env (nu afișează parole sau chei):

    python scripts/diagnose_mobile_upload.py                          # URL public + FastAPI + PostgreSQL + Storage
    python scripts/diagnose_mobile_upload.py --key cimitir.XXXX       # + emite un token de probă și îl verifică prin URL-ul public
    python scripts/diagnose_mobile_upload.py --url "https://…/api/m/…"  # analizează un link din QR (nu îl consumă)

Cheia de sincronizare (--key sau variabila CIMITIR_SYNC_KEY) este cea din MAIN; nu se afișează.
Cod de ieșire: 0 = totul OK; 1 = cel puțin o verificare a eșuat (cauza probabilă se tipărește).
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import os
import socket
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "shared", ROOT / "backend" / "cimitir", ROOT / "backend"):
    sys.path.insert(0, str(p))

import cimitir_config  # noqa: E402

cimitir_config.load_env()
import requests  # noqa: E402

FAILED = []


def say(ok: bool, what: str, hint: str = "") -> bool:
    print(f"[{'OK ' if ok else 'EȘEC'}] {what}" + ("" if ok or not hint else f"\n        -> {hint}"))
    if not ok:
        FAILED.append(what)
    return ok


def private_host(h: str) -> bool:
    h = (h or "").lower().strip("[]")
    if not h or h == "localhost" or h.endswith((".local", ".localhost", ".internal", ".lan")):
        return True
    try:
        ip = ipaddress.ip_address(h)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return "." not in h


def check_public_url() -> str:
    print("\n== 1. URL public (ce vede telefonul) ==")
    url = (os.environ.get("CIMITIR_PUBLIC_URL") or "").strip().rstrip("/")
    if not say(bool(url), "CIMITIR_PUBLIC_URL este setat în backend/.env", "adaugă CIMITIR_PUBLIC_URL=https://adresa-ta-publica"):
        return ""
    u = urlparse(url)
    say(u.scheme == "https", "URL-ul este HTTPS", "telefonul pe 4G/5G și camera cer HTTPS; folosește https://")
    say(not private_host(u.hostname or ""), "URL-ul nu e localhost / IP privat / rețea locală",
        "pe 4G/5G telefonul nu poate ajunge la 192.168.x.x sau localhost; pune adresa publică")
    try:
        socket.getaddrinfo(u.hostname, 443)
        say(True, f"DNS rezolvă {u.hostname}")
    except OSError:
        say(False, f"DNS nu rezolvă {u.hostname}", "verifică domeniul / înregistrarea DNS")
        return url
    try:
        r = requests.get(url + "/api/health", timeout=15)
        say(r.status_code == 200, f"{url}/api/health răspunde prin internet (HTTP {r.status_code})",
            "serverul/ingress-ul nu e accesibil din exterior (firewall, proxy, certificat) sau pornit fără /api")
    except requests.RequestException as exc:
        say(False, f"{url}/api/health nu răspunde ({type(exc).__name__})", "certificat TLS invalid, port blocat sau serviciu oprit")
    return url


def check_fastapi() -> None:
    print("\n== 2. FastAPI ==")
    try:
        r = requests.get("http://127.0.0.1:8001/api/health", timeout=10).json()
        say(r.get("server_status") == "online", f"FastAPI local: server={r.get('server_status')}, versiune={r.get('version')}",
            "vezi sudo supervisorctl status backend și /var/log/supervisor/backend.err.log")
        say(cimitir_config.mode() == "online", f"CIMITIR_MODE={cimitir_config.mode()}", "pentru telefon prin internet: CIMITIR_MODE=online")
    except requests.RequestException as exc:
        say(False, f"FastAPI local nu răspunde ({type(exc).__name__})", "serviciul backend nu rulează")


def check_postgres() -> None:
    print("\n== 3. PostgreSQL (Supabase) ==")
    import system_admin as sa
    import store as cim
    try:
        cim.bind_source(cim.list_sources()[0])
        st = sa.db_status()
        say(bool(st.get("online")), f"PostgreSQL online ({st.get('version')}, latență {st.get('latency_ms')} ms, schema {st.get('schema_version')})",
            f"DATABASE_URL greșit / parolă / rețea: {st.get('error') or ''}. Din pod folosește Session Pooler (IPv4), nu db.<proiect>.supabase.co")
        say((st.get("schema_version") or 0) >= 7, "migrările 006 (tokenuri) și 007 (avize) sunt aplicate", "repornește backend-ul: aplică migrările automat")
        t = sa.test_db()
        say(bool(t.get("ok")), "test citire din baza de date")
    except Exception as exc:  # noqa: BLE001
        say(False, f"PostgreSQL inaccesibil ({type(exc).__name__})", "verifică DATABASE_URL din backend/.env (fără să-l afișezi)")


def check_storage() -> None:
    print("\n== 4. Storage (cimitir-private) ==")
    try:
        import cimitir_storage as st
        s = st.get_storage()
        say(s.name == "supabase", f"driver = {s.name}", "setează CIMITIR_STORAGE_DRIVER=supabase")
        if s.name != "supabase":
            return
        say(s.auth_ok(), "cheia SUPABASE_SECRET_KEY este acceptată", "cheie greșită/revocată sau SUPABASE_URL greșit")
        info = s.bucket_info()
        say(info is not None and info.get("public") is False, "bucket-ul există și este PRIVATE", "creează bucket-ul privat sau repară setarea public=false")
        key = f"_healthcheck/diag-{uuid.uuid4().hex[:8]}.bin"
        data = os.urandom(50_000)
        tmp = ROOT / "backend" / f".diag-{uuid.uuid4().hex[:6]}.bin"
        tmp.write_bytes(data)
        try:
            s.put_file(key, str(tmp), "application/octet-stream")
            back = b"".join(s.open_stream(key))
            say(hashlib.sha256(back).digest() == hashlib.sha256(data).digest(), "upload + download + sha256 identic")
            s.delete(key)
            say(not s.exists(key), "obiectul de probă a fost șters")
        finally:
            tmp.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        say(False, f"Storage: {type(exc).__name__}", "verifică SUPABASE_URL / SUPABASE_SECRET_KEY / SUPABASE_BUCKET")


def check_token(public: str, key: str) -> None:
    print("\n== 5. Token / QR de probă ==")
    base = "http://127.0.0.1:8001"
    H = {"X-Sync-Key": key, "X-Sync-Client": "diagnostic"}
    try:
        locs = requests.get(f"{base}/api/sync/ping", headers=H, timeout=15)
        say(locs.status_code == 200, f"cheia de sincronizare e acceptată (HTTP {locs.status_code})", "cheie greșită/revocată: emite una nouă cu scripts/issue_sync_key.py")
        if locs.status_code != 200:
            return
        import store as cim
        cim.bind_source(cim.list_sources()[0])
        conn = cim.get_connection()
        try:
            row = conn.execute("SELECT uid FROM locuri_de_veci WHERE uid IS NOT NULL LIMIT 1").fetchone()
        finally:
            conn.close()
        if not row:
            say(False, "nu există niciun loc pe server pentru token de probă", "rulează o sincronizare din MAIN (Sincronizează acum)")
            return
        r = requests.post(f"{base}/api/sync/mobile/tokens", headers=H, json={"entity_type": "loc", "entity_uid": row["uid"], "issued_by": "diagnostic"}, timeout=20)
        say(r.status_code == 200, f"serverul emite token (HTTP {r.status_code})", r.text[:160] if r.status_code != 200 else "")
        if r.status_code != 200:
            return
        t = r.json()
        say(t["url"].startswith(public + "/api/m/") if public else True, "URL-ul din QR începe cu CIMITIR_PUBLIC_URL")
        say(300 <= t["expires_in"] <= 600, f"valabilitate {t['expires_in'] // 60} min (5–10)")
        info = requests.get(t["url"] + "/info", timeout=20)
        say(info.status_code == 200, f"telefonul (prin URL-ul public) vede entitatea: HTTP {info.status_code}",
            "URL-ul public nu ajunge la FastAPI pe /api/m/… (proxy care nu trimite /api către backend?)")
        requests.delete(f"{base}/api/sync/mobile/tokens/{t['tid']}", headers=H, timeout=15)
        say(True, "token de probă anulat")
    except requests.RequestException as exc:
        say(False, f"eroare de rețea ({type(exc).__name__})")


def check_link(url: str) -> None:
    print("\n== Analiză link din QR (nu consumă tokenul) ==")
    u = urlparse(url)
    say(u.scheme == "https", "linkul e HTTPS", "QR generat cu URL nesecurizat: verifică CIMITIR_PUBLIC_URL")
    say(not private_host(u.hostname or ""), "linkul nu indică o adresă locală", "QR-ul arată un IP/host local: telefonul pe 4G/5G nu îl poate deschide")
    say("/api/m/" in u.path, "linkul are forma /api/m/<token>")
    try:
        r = requests.get(url.rstrip("/") + "/info", timeout=20)
        body = {}
        try:
            body = r.json()
        except ValueError:
            pass
        detail = body.get("detail") if isinstance(body, dict) else None
        err = detail.get("error") if isinstance(detail, dict) else ""
        explain = {"expired": "tokenul a expirat (5–10 min): generează un QR nou", "used": "tokenul a fost deja folosit: generează un QR nou",
                   "revoked": "tokenul a fost anulat (dialogul din MAIN a fost închis)", "invalid": "token necunoscut sau greșit"}
        say(r.status_code == 200, f"serverul răspunde HTTP {r.status_code}" + (f" ({err})" if err else ""), explain.get(err, "vezi pașii 1–4 de mai sus"))
        if r.status_code == 429:
            say(False, "prea multe încercări de pe acest IP", "așteaptă câteva minute")
        if r.status_code == 200:
            e = body["entity"]
            print(f"        entitate: {e['label']} – {e['title']}; expiră în {body['expires_in']} s; utilizări rămase: {body['remaining_uses']}")
    except requests.RequestException as exc:
        say(False, f"linkul nu poate fi deschis ({type(exc).__name__})", "problemă de rețea/DNS/TLS spre URL-ul public")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", default=os.environ.get("CIMITIR_SYNC_KEY", ""), help="cheia de sincronizare MAIN (opțional)")
    ap.add_argument("--url", default="", help="linkul din QR de analizat (opțional)")
    a = ap.parse_args()
    t0 = time.time()
    if a.url:
        check_link(a.url)
    public = check_public_url()
    check_fastapi()
    check_postgres()
    check_storage()
    if a.key:
        check_token(public, a.key)
    else:
        print("\n(Pas 5 sărit: dă --key <cheia MAIN> ca să emit și să verific un token de probă.)")
    print(f"\n{'TOT OK' if not FAILED else 'ESEC: ' + '; '.join(FAILED)}  ({time.time() - t0:.1f}s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
