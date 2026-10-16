"""Verificare READ-ONLY a sincronizării pe Cloud TEST real (fără date fictive, fără scrieri).

Variabile de mediu (nu se salvează nicăieri, nu se afișează):
  CIMITIR_VERIFY_URL        ex. https://<adresa-cloud-test>
  CIMITIR_VERIFY_PHONE_KEY  cheia Binding a telefonului (Mobile v2)
  CIMITIR_VERIFY_MAIN_KEY   (opțional) cheia Binding MAIN — pentru a verifica și permisiunile/jurnalul

Raportează pe nume/coduri (fără UUID): locuri, concesionari, contracte, înhumați și dacă fiecare relație are părintele în cloud.
Cod de ieșire 0 = relații complete; 1 = înregistrări orfane/lipsă; 2 = configurare/conexiune.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

ENTITIES = ("locuri_de_veci", "concesionari", "contracte", "inhumati", "plati", "amenajari_avize")


def call(url: str, key: str, path: str):
    req = urllib.request.Request(url.rstrip("/") + path, headers={"Authorization": f"Binding {key}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())["result"]
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"HTTP {exc.code} la {path.split('?')[0]} (cheie/cimitir invalid?)")
    except (urllib.error.URLError, OSError) as exc:
        raise SystemExit(f"Fără conexiune ({type(exc).__name__})")


def pull_all(url, key, entity):
    rows, since = [], 0
    while True:
        r = call(url, key, f"/api/v1/sync-v2/pull?entity={entity}&since={since}&limit=500")
        rows += [c for c in r["changes"] if not c["deleted"]]
        if not r["more"]:
            return rows
        since = r["cursor"]


def main() -> int:
    url, key = os.environ.get("CIMITIR_VERIFY_URL", ""), os.environ.get("CIMITIR_VERIFY_PHONE_KEY", "")
    if not url or not key:
        print("Setează CIMITIR_VERIFY_URL și CIMITIR_VERIFY_PHONE_KEY.")
        return 2
    st = call(url, key, "/api/v1/sync-v2/status")
    print("Telefon autentificat. Registre editabile de pe telefon:", ", ".join(st["editable"]) or "(niciunul — doar citire)")
    print("Conflicte deschise (ale acestui telefon):", st["open_conflicts"])
    readable = st.get("entities") or list(ENTITIES)
    for e in ENTITIES:
        if e not in readable:
            print(f"  {e:16s} (fără drept de citire pentru acest telefon — rol: {st.get('role') or 'implicit limitat'})")
    data = {e: (pull_all(url, key, e) if e in readable else []) for e in ENTITIES}
    for e in ENTITIES:
        print(f"  {e:16s} {len(data[e])}")
    locs = {r["uid"]: r["data"] for r in data["locuri_de_veci"]}
    conc = {r["uid"]: r["data"] for r in data["concesionari"]}
    contr = {r["uid"]: r["data"] for r in data["contracte"]}
    problems = 0
    print("\nLocuri cu poligon, concesionari și înhumați:")
    for uid, l in sorted(locs.items(), key=lambda x: x[1].get("cod_loc") or ""):
        pts = len(l.get("points") or [])
        cts = [c for c in contr.values() if c.get("loc_uid") == uid]
        names = [f"{conc[c['concesionar_uid']].get('nume', '')} {conc[c['concesionar_uid']].get('prenume', '')}".strip() for c in cts if c.get("concesionar_uid") in conc]
        bur = [f"{b['data'].get('nume', '')} {b['data'].get('prenume', '')}".strip() for b in data["inhumati"] if b["data"].get("loc_uid") == uid]
        print(f"  {l.get('cod_loc')}: poligon {pts} puncte · concesionari: {', '.join(names) or '—'} · înhumați: {', '.join(bur) or '—'}")
    for b in data["inhumati"]:
        if b["data"].get("loc_uid") and b["data"]["loc_uid"] not in locs:
            problems += 1
            print(f"  PROBLEMĂ: înhumat {b['data'].get('nume')} are loc inexistent în cloud")
    for c in contr.values():
        if (c.get("loc_uid") and c["loc_uid"] not in locs) or (c.get("concesionar_uid") and c["concesionar_uid"] not in conc):
            problems += 1
            print(f"  PROBLEMĂ: contract {c.get('numar_contract')} are părinte inexistent în cloud")
    ds = call(url, key, "/api/v1/mobile/data-status")
    print("\nStare date pentru Mobile:", ds.get("state"), "| locuri live:", ds.get("live", {}).get("graves"))
    mk = os.environ.get("CIMITIR_VERIFY_MAIN_KEY")
    if mk:
        runs = call(url, mk, "/api/v1/sync-v2/runs?limit=5")["runs"]
        print("Ultimele rulări (MAIN):", [(r["direction"], r["device_type"], r["applied"], r["conflicts"]) for r in runs])
    print("\nREZULTAT:", "relații complete" if not problems else f"{problems} probleme")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
