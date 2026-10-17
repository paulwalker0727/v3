#!/usr/bin/env python3
"""Verificare LIVE (PASS/FAIL) a Cloud-ului TEST prin HTTPS public: asociere MAIN, sincronizare MAIN→Cloud→telefon, documente (Storage) și izolare.

NU folosește utilizatorul postgres, NU atinge producția, NU șterge/resetează Craiova TEST. Scrie doar înregistrări izolate (cimitir DRAFT „LIVE-VERIFY-…” în organizația indicată,
un MAIN și un telefon temporare, 1 loc, 1 document mic), iar la final revocă dispozitivele create (cheile devin inutilizabile). Cimitirul de test rămâne (nu există API de ștergere).

Mediu (valorile nu se afișează):
  CLOUD_URL                      https://…  (adresa publică; HTTP doar pe localhost)
  CIMITIR_LIVE_ADMIN_EMAIL       email Owner TEST (cont admin al Cloud-ului, creat prin /admin/)
  CIMITIR_LIVE_ADMIN_PASSWORD    parola lui
  CIMITIR_LIVE_ORG_UID           organizația TEST (ex. Craiova)
  CIMITIR_LIVE_CEMETERY_UID      (opțional) un cimitir REAL al organizației, folosit doar pentru testul de respingere cross-cimitir (nu se modifică)
  CIMITIR_LIVE_OTHER_ORG_UID     (opțional) altă organizație TEST: se verifică că adminul NU o poate accesa
Rulare:  python scripts/verify_cloud_live.py --yes-write-test-data [--label LIVE]   (fără --yes-write-test-data nu face nimic)
Cod de ieșire: 0 = toate PASS, 1 = există FAIL. Un test sărit apare SKIP (nu PASS).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))

import httpx  # noqa: E402

import cimitir_bootstrap as bs  # noqa: E402
import cimitir_cloud_admin_client as adc  # noqa: E402
import cimitir_cloud_associate as assoc  # noqa: E402
import cimitir_cloud_client as client  # noqa: E402
import cimitir_registry as reg  # noqa: E402
import cimitir_secretstore as ss  # noqa: E402

RESULTS = []


def record(section: str, name: str, status: str, detail: str = "") -> None:
    RESULTS.append((section, name, status, detail))
    print(f"[{status:4}] {section} · {name}" + (f" — {detail}" if detail else ""), flush=True)


def check(section: str, name: str, fn) -> bool:
    try:
        out = fn()
        record(section, name, "PASS", out if isinstance(out, str) else "")
        return True
    except AssertionError as exc:
        record(section, name, "FAIL", str(exc)[:200] or "aserțiune eșuată")
    except Exception as exc:  # noqa: BLE001
        record(section, name, "FAIL", f"{type(exc).__name__}: {str(exc)[:160]}")
        if os.environ.get("CIMITIR_LIVE_DEBUG"):
            traceback.print_exc()
    return False


def need_env(*names):
    miss = [n for n in names if not os.environ.get(n)]
    if miss:
        print("Variabile de mediu lipsă: " + ", ".join(miss), file=sys.stderr)
        sys.exit(2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes-write-test-data", action="store_true")
    ap.add_argument("--label", default="LIVE")
    a = ap.parse_args()
    if not a.yes_write_test_data:
        print("Nimic rulat. Adaugă --yes-write-test-data pentru a confirma că poți scrie înregistrări de test izolate pe Cloud-ul TEST.")
        return 2
    need_env("CLOUD_URL", "CIMITIR_LIVE_ADMIN_EMAIL", "CIMITIR_LIVE_ADMIN_PASSWORD", "CIMITIR_LIVE_ORG_UID")
    url = assoc.normalize_url(os.environ["CLOUD_URL"])
    org = os.environ["CIMITIR_LIVE_ORG_UID"]
    real_cem = os.environ.get("CIMITIR_LIVE_CEMETERY_UID", "")
    other_org = os.environ.get("CIMITIR_LIVE_OTHER_ORG_UID", "")
    tag = "LIVE-VERIFY-" + time.strftime("%Y%m%d-%H%M%S")
    print(f"== Verificare {a.label} · {url} · {tag} ==")
    S: dict = {}
    created_bindings = []
    adminc = adc.AdminClient(url)
    http = httpx.Client(timeout=20)
    tmp = tempfile.TemporaryDirectory(prefix="cimitir-live-")

    # ------------------------------------------------------------------ A. conexiune
    def a_health():
        r = http.get(url + "/api/v1/health")
        assert r.status_code == 200 and r.json().get("ok"), f"HTTP {r.status_code}"
        j = r.json()
        assert j.get("database") == "up" and j.get("storage") == "configured", f"db={j.get('database')} storage={j.get('storage')}"
        return "database=up storage=configured"

    def a_info():
        r = http.get(url + "/api/v1/bootstrap/info")
        assert r.status_code == 200, f"HTTP {r.status_code} (ruta lipsește: imagine Cloud veche?)"
        assert "preflight_for_code" in r.json()["ops"]

    def a_login():
        me = adminc.login(os.environ["CIMITIR_LIVE_ADMIN_EMAIL"], os.environ["CIMITIR_LIVE_ADMIN_PASSWORD"])
        mine = [o for o in me["organizations"] if o["organization_uid"] == org]
        assert mine and mine[0]["is_owner"], "contul nu este Owner în organizația TEST"
        S["org_name"] = mine[0]["name"]
        return f"owner în „{mine[0]['name']}”"

    ok_h = check("A.Conexiune", "health (API + DB + Storage)", a_health)
    check("A.Conexiune", "API de asociere /api/v1/bootstrap expus (fix 404)", a_info)
    ok_login = check("A.Conexiune", "login administrator (sesiune reală)", a_login)
    if not (ok_h and ok_login):
        record("-", "restul testelor", "SKIP", "conexiune/autentificare eșuată")
        return finish(a.label)

    # ------------------------------------------------------------------ B. asociere MAIN
    def b_cemetery():
        cat = reg.RegistryCatalog(Path(tmp.name) / "data")
        with cat._conn() as c:
            c.execute("INSERT INTO organizations (uid, org_type, official_name, display_name, active) VALUES (?,?,?,?,1)", (org, "parohie", S["org_name"] or "Org", S["org_name"] or "Org"))
        cem = cat.create_cemetery(org, tag)
        b = cat.binding_for(cem)
        reg.prepare_registry_db(b)
        r = adminc._req("POST", "/cemeteries", {"organization_uid": org, "name": tag, "cemetery_uid": cem})
        assert r["status"] == "DRAFT"
        S.update(cat=cat, b=b, cem=cem, store=ss.MemorySecretStore(), inst=bs.installation_uid(cat))
        return f"cimitir izolat {cem[:8]}… DRAFT"

    def b_assoc():
        S["a"] = assoc.Association(S["b"], S["store"], S["inst"], url, device_name="LIVE-VERIFY MAIN")
        S["a"].check_api()
        code = adminc.issue_code(org, S["cem"], "main")
        S["main_code"] = code
        S["a"].verify_code(code["code"])
        p = S["a"].plan(code["code"], tag)
        res = S["a"].execute(p["plan"], code["code"], True)
        assert res["verification"]["sync_enabled"]
        reg_l = bs.LocalRegistry(S["b"])
        assert reg_l.sync_state == "BOUND"
        S["main_key"] = S["store"].get(reg_l.get("cloud_secret_ref"))
        assert S["main_key"], "cheia lipsește din SecretStore"
        created_bindings.append(reg_l.get("cloud_binding_uid"))
        return "BOUND, cheia în SecretStore"

    def b_noplain():
        assert S["main_key"].encode() not in Path(S["b"].database).read_bytes(), "cheia apare în clar în SQLite"

    def b_code_reuse():
        try:
            client.RemoteControlPlane(url, retries=0).preflight_for_code(S["main_code"]["code"], str(uuid.uuid4()), S["cem"])
        except Exception:  # noqa: BLE001
            return "cod consumat respins"
        raise AssertionError("un cod consumat a fost acceptat din nou")

    if check("B.Asociere", "cimitir de test izolat + registru local temporar", b_cemetery) and check("B.Asociere", "asociere MAIN prin HTTPS public (plan → confirmare → cheie → verificare)", b_assoc):
        check("B.Asociere", "cheia nu apare în clar în SQLite", b_noplain)
        check("B.Asociere", "cod consumat nu mai poate fi refolosit", b_code_reuse)
        S["ok_main"] = True

    def hdr(k):
        return {"Authorization": f"Binding {k}"}

    # ------------------------------------------------------------------ C. sincronizare
    def c_push():
        loc, opid = str(uuid.uuid4()), "op-" + uuid.uuid4().hex
        S["loc"] = loc
        op = {"op_id": opid, "entity": "locuri_de_veci", "uid": loc, "action": "upsert", "base_version": 0,
              "data": {"cod_loc": tag[-15:], "points": [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]], "sector": "S-LIVE", "are_inhumari": 0}}
        r = http.post(url + "/api/v1/sync-v2/push", json={"ops": [op]}, headers=hdr(S["main_key"]))
        assert r.status_code == 200, f"HTTP {r.status_code}"
        assert r.json()["result"]["results"][0]["status"] == "applied", r.text[:150]
        r2 = http.post(url + "/api/v1/sync-v2/push", json={"ops": [op]}, headers=hdr(S["main_key"]))
        assert r2.json()["result"]["results"][0]["status"] in ("applied", "duplicate", "replayed"), "retrimiterea nu e idempotentă"
        return "MAIN → Cloud: loc aplicat; retrimitere idempotentă"

    def c_phone():
        c = adminc.issue_code(org, S["cem"], "phone")
        r = http.post(url + "/api/v1/phone-pairing/redeem", json={"code": c["display"], "device_name": "LIVE-VERIFY telefon", "client_id": "live-" + uuid.uuid4().hex[:12]})
        assert r.status_code == 201, f"HTTP {r.status_code}"
        S["phone_key"], pb = r.json()["result"]["binding_key"], r.json()["result"]["binding_uid"]
        created_bindings.append(pb)
        ch = http.get(url + "/api/v1/sync-v2/pull?entity=locuri_de_veci&since=0", headers=hdr(S["phone_key"]))
        assert ch.status_code == 200, f"HTTP {ch.status_code}"
        assert any(x["uid"] == S["loc"] for x in ch.json()["result"]["changes"]), "telefonul nu vede locul trimis de MAIN"
        return "Cloud → telefon: locul trimis de MAIN este vizibil"

    if S.get("ok_main"):
        check("C.Sincronizare", "MAIN → Cloud (push, idempotent)", c_push)
        if S.get("loc"):
            check("C.Sincronizare", "asociere telefon cu cod din interfață + Cloud → telefon (pull)", c_phone)
    else:
        record("C.Sincronizare", "MAIN → Cloud → telefon", "SKIP", "asocierea MAIN nu a reușit")

    # ------------------------------------------------------------------ D. documente
    def d_doc():
        pdf = b"%PDF-1.4\n" + os.urandom(200) + b"\n%%EOF\n"
        it = http.post(url + "/api/v1/field-inbox/items", json={"idempotency_key": "live-" + uuid.uuid4().hex, "item_type": "DOCUMENT", "payload": {"doc_type": "adeverinta_inhumare"}, "grave_uid": S["loc"]},
                       headers=hdr(S["phone_key"]))
        assert it.status_code in (200, 201), f"creare element HTTP {it.status_code}: {it.text[:120]}"
        uid = it.json()["result"]["item"]["uid"]
        fu = str(uuid.uuid4())
        up = http.post(url + f"/api/v1/field-inbox/items/{uid}/files", content=pdf,
                       headers={**hdr(S["phone_key"]), "Content-Type": "application/pdf", "X-File-Uid": fu, "X-File-Name": "live-verify.pdf", "X-File-Sha256": hashlib.sha256(pdf).hexdigest()})
        assert up.status_code in (200, 201), f"încărcare HTTP {up.status_code}: {up.text[:120]}"
        got = http.get(url + f"/api/v1/field-inbox/items/{uid}/files/{fu}/url?ttl=60", headers=hdr(S["main_key"]))
        assert got.status_code == 200, f"URL semnat HTTP {got.status_code}"
        return "document încărcat în Storage privat; URL semnat emis pentru MAIN"

    if S.get("phone_key"):
        check("D.Documente", "telefon → Storage → MAIN (PDF mic)", d_doc)
    else:
        record("D.Documente", "document telefon → Storage → MAIN", "SKIP", "telefon neasociat")

    # ------------------------------------------------------------------ E. izolare
    def e_random_org():
        r = adminc._req
        for method, path, body in (("GET", "/devices", None), ("GET", "/codes", None)):
            try:
                r(method, path, params={"organization_uid": str(uuid.uuid4())})
            except adc.AdminApiError as exc:
                assert exc.status in (403, 404), f"{path}: HTTP {exc.status}"
            else:
                raise AssertionError(f"{path}: organizație necunoscută acceptată")

    def e_other_org():
        try:
            adminc.list_devices(other_org)
        except adc.AdminApiError as exc:
            assert exc.status in (403, 404)
            return "adminul nu poate accesa altă organizație"
        raise AssertionError("acces la altă organizație permis")

    def e_cross_cemetery_code():
        try:
            client.RemoteControlPlane(url, retries=0).preflight_for_code(adminc.issue_code(org, S["cem"], "main")["code"], str(uuid.uuid4()), real_cem)
        except Exception:  # noqa: BLE001
            return "cod emis pentru cimitirul de test respins pe alt cimitir"
        raise AssertionError("codul a fost acceptat pe alt cimitir")

    def e_phone_no_admin():
        r = http.get(url + f"/api/v1/admin/devices?organization_uid={org}", headers={"Authorization": f"Bearer {S['phone_key']}"})
        assert r.status_code == 401, f"HTTP {r.status_code}"
        r = http.post(url + "/api/v1/bootstrap/authenticate", headers=hdr(S["phone_key"]), json={})
        return "cheia de telefon nu deschide interfața de administrare"

    def e_bad_keys():
        for k in ("cimitir.%s.%s" % (uuid.uuid4(), "x" * 43), "garbage"):
            r = http.get(url + "/api/v1/sync-v2/pull?entity=locuri_de_veci&since=0", headers=hdr(k))
            assert r.status_code == 401, f"HTTP {r.status_code}"

    check("E.Izolare", "organizație inexistentă/necunoscută → 403/404", e_random_org)
    if other_org:
        check("E.Izolare", "altă organizație TEST inaccesibilă adminului", e_other_org)
    else:
        record("E.Izolare", "altă organizație TEST inaccesibilă", "SKIP", "setează CIMITIR_LIVE_OTHER_ORG_UID")
    if real_cem and S.get("cem"):
        check("E.Izolare", "cod pentru un cimitir nu funcționează pe alt cimitir", e_cross_cemetery_code)
    else:
        record("E.Izolare", "cod cross-cimitir", "SKIP", "setează CIMITIR_LIVE_CEMETERY_UID")
    if S.get("phone_key"):
        check("E.Izolare", "cheie de telefon ≠ sesiune administrativă", e_phone_no_admin)
    check("E.Izolare", "chei de legătură inventate sunt respinse (401)", e_bad_keys)

    # ------------------------------------------------------------------ F. curățare + revocare efectivă
    def f_revoke():
        devs = adminc.list_devices(org)
        mine = [d for d in devs if d["binding_uid"] in created_bindings and d["state"] != "revoked"]
        for d in mine:
            adminc.revoke_device(org, d["binding_uid"])
        for k in (S.get("main_key"), S.get("phone_key")):
            if k:
                r = http.get(url + "/api/v1/sync-v2/pull?entity=locuri_de_veci&since=0", headers=hdr(k))
                assert r.status_code == 401, f"cheie revocată încă validă (HTTP {r.status_code})"
        return f"{len(mine)} dispozitive de test revocate; cheile nu mai funcționează"

    if created_bindings:
        check("F.Curățare", "revocare dispozitive de test + cheile devin inutilizabile", f_revoke)
    adminc.logout() if adminc.logged_in else None
    tmp.cleanup()
    return finish(a.label)


def finish(label: str) -> int:
    n = {s: sum(1 for r in RESULTS if r[2] == s) for s in ("PASS", "FAIL", "SKIP")}
    print(f"\n== Rezultat {label}: PASS={n['PASS']} FAIL={n['FAIL']} SKIP={n['SKIP']} ==")
    if n["SKIP"]:
        print("Atenție: testele SKIP NU sunt trecute.")
    return 1 if n["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
