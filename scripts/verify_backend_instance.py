"""verify_backend_instance.py — verifică identitatea și compatibilitatea unui (sau a două) backend-uri Cloud TEST ÎNAINTE de orice schimbare de port/container.

Pentru fiecare adresă: /api/v1/health, apoi `GET /api/v1/sync-v2/instance` autentificat cu cheia MAIN (din variabila CIMITIR_MAIN_KEY; nu se afișează, nu se scrie nicăieri).
Afișează release-ul, amprenta bazei de date (identică pe orice backend care folosește aceeași bază și același cimitir) și capabilitățile.
Verdict cu două adrese: „aceeași bază” + „release diferit” = actualizare sigură (migrările sunt aditive). Nu scrie nimic în Cloud (doar GET).

  set CIMITIR_MAIN_KEY=<cheia MAIN din SecretStore>      (PowerShell: $env:CIMITIR_MAIN_KEY = "...")
  python scripts/verify_backend_instance.py --url http://127.0.0.1:8086 --url http://127.0.0.1:8087

Coduri de ieșire: 0 = OK; 2 = backend vechi (fără Mobile v2); 3 = cheie respinsă / identități diferite / backend inaccesibil.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List

import httpx

NEEDED = {"permissions_matrix", "pairing_codes_by_main"}


def inspect_backend(url: str, key: str, timeout: float = 15.0) -> Dict[str, Any]:
    url = url.rstrip("/")
    out: Dict[str, Any] = {"url": url, "health": None, "status": "unknown", "message": ""}
    try:
        h = httpx.get(url + "/api/v1/health", timeout=timeout)
        out["health"] = h.status_code
    except httpx.HTTPError as exc:
        out.update(status="unreachable", message=f"Backendul nu răspunde ({type(exc).__name__}).")
        return out
    try:
        r = httpx.get(url + "/api/v1/sync-v2/instance", headers={"Authorization": f"Binding {key}"}, timeout=timeout)
    except httpx.HTTPError as exc:
        out.update(status="unreachable", message=f"Backendul nu răspunde ({type(exc).__name__}).")
        return out
    if r.status_code == 401:
        out.update(status="auth_rejected", message="Cheia MAIN este respinsă de acest backend (alt mediu/altă bază sau cheie revocată).")
        return out
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code in (404, 405) and "ok" not in body:
        out.update(status="old", message="Backend VECHI: nu are Mobile v2 complet (lipsește /sync-v2/instance). Trebuie actualizat.")
        return out
    if r.status_code != 200 or not body.get("ok"):
        out.update(status="error", message=f"Răspuns neașteptat (HTTP {r.status_code}).")
        return out
    info = body["result"]
    missing = sorted(NEEDED - set(info.get("capabilities", [])))
    out.update(status="ok" if not missing else "schema_missing", release=info.get("release"), db_fingerprint=info.get("db_fingerprint"), capabilities=info.get("capabilities"),
               device_type=info.get("device_type"), message="" if not missing else "Lipsesc capabilități (migrările 023/027 neaplicate pe baza acestui backend): " + ", ".join(missing))
    return out


def verdict(results: List[Dict[str, Any]]) -> int:
    if any(r["status"] in ("unreachable", "auth_rejected", "error") for r in results):
        return 3
    if any(r["status"] in ("old", "schema_missing") for r in results):
        return 2
    prints = {r["db_fingerprint"] for r in results}
    return 0 if len(prints) == 1 else 3


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", action="append", required=True)
    a = ap.parse_args(argv)
    key = os.environ.get("CIMITIR_MAIN_KEY", "").strip()
    if not key:
        print("Setează CIMITIR_MAIN_KEY (cheia MAIN din SecretStore). Cheia nu se afișează.")
        return 3
    results = [inspect_backend(u, key) for u in a.url]
    for r in results:
        line = f"{r['url']}: {r['status'].upper()}"
        if r["status"] in ("ok", "schema_missing"):
            line += f" · release {r['release']} · bază {r['db_fingerprint']} · {', '.join(r['capabilities'])}"
        print(line + (f"\n   {r['message']}" if r["message"] else ""))
    code = verdict(results)
    if len(results) > 1 and code == 0:
        same_rel = len({r["release"] for r in results}) == 1
        print("Verdict: ACEEAȘI bază de date" + (" și același release." if same_rel else ", release-uri diferite: actualizare sigură (migrări aditive)."))
    elif code == 3 and all(r["status"] == "ok" for r in results):
        print("Verdict: baze de date DIFERITE — nu schimba porturile/containerele până nu lămurești care este instanța corectă.")
    return code


if __name__ == "__main__":
    sys.exit(main())
