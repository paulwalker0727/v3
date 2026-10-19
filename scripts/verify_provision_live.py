#!/usr/bin/env python3
"""Verificare LIVE (PASS/FAIL) a circuitului nou: provizionare automată a unui registru MAIN FICTIV în Cloud TEST + asociere + verificare + izolare + revocare.

Scrie doar un registru izolat „LIVE-PROVISION-<data>” (organizație + cimitir FICTIVE noi, cu UID-uri generate local și păstrate în Cloud). NU atinge organizațiile existente, NU folosește postgres,
NU rulează pe PROD. Cheia legăturii rămâne într-un SecretStore în memorie; la final dispozitivul creat este revocat. Organizația/cimitirul de test rămân (nu există API de ștergere).

Mediu (valorile nu se afișează):
  CLOUD_URL, CIMITIR_LIVE_ADMIN_EMAIL, CIMITIR_LIVE_ADMIN_PASSWORD   (administratorul instalării Cloud TEST; numai el poate crea organizații noi)
  CIMITIR_LIVE_OTHER_ORG_UID   (opțional) o organizație existentă: se verifică respingerea provizionării peste ea
Rulare:  python scripts/verify_provision_live.py --yes-write-test-data
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("CIMITIR_ALLOW_TEST_SECRETSTORE", "1")      # doar SecretStore în memorie pentru scriptul de verificare
sys.path.insert(0, str(ROOT / "shared"))
sys.path.insert(0, str(ROOT / "scripts"))

import verify_cloud_live as v  # noqa: E402
import cimitir_bootstrap as bs  # noqa: E402
import cimitir_cloud_admin_client as adc  # noqa: E402
import cimitir_cloud_associate as assoc  # noqa: E402
import cimitir_registry as reg  # noqa: E402
import cimitir_secretstore as ss  # noqa: E402

check, record = v.check, v.record


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes-write-test-data", action="store_true")
    ap.add_argument("--label", default="LIVE")
    a = ap.parse_args()
    if not a.yes_write_test_data:
        print("Nimic rulat. Adaugă --yes-write-test-data pentru a confirma crearea unui registru FICTIV izolat în Cloud-ul TEST.")
        return 2
    v.need_env("CLOUD_URL", "CIMITIR_LIVE_ADMIN_EMAIL", "CIMITIR_LIVE_ADMIN_PASSWORD")
    url = assoc.normalize_url(os.environ["CLOUD_URL"])
    tag = "LIVE-PROVISION-" + time.strftime("%Y%m%d-%H%M%S")
    print(f"== Provizionare automată {a.label} · {url} · {tag} ==")
    S: dict = {}
    admin = adc.AdminClient(url)
    tmp = tempfile.TemporaryDirectory(prefix="cimitir-live-prov-")

    def login():
        me = admin.login(os.environ["CIMITIR_LIVE_ADMIN_EMAIL"], os.environ["CIMITIR_LIVE_ADMIN_PASSWORD"])
        return f"autentificat ({len(me['organizations'])} organizații vizibile)"

    if not check("A", "login administrator Cloud", login):
        return v.finish(a.label)

    def local():
        cat = reg.RegistryCatalog(Path(tmp.name) / "data")
        org = cat.create_organization("parohie", tag)
        cem = cat.create_cemetery(org, "test")
        b = cat.binding_for(cem)
        reg.prepare_registry_db(b)
        S.update(cat=cat, b=b, org=org, cem=cem, store=ss.MemorySecretStore(), inst=bs.installation_uid(cat))
        return f"registru local nou org={org[:8]}… cem={cem[:8]}…"
    check("B", "registru local nou (UID-uri originale)", local)
    a_ = None

    def status0():
        nonlocal a_
        a_ = assoc.Association(S["b"], S["store"], S["inst"], url)
        st = a_.registry_in_cloud(admin)
        assert st["action"] == "provision_all", st
        return st["message"]
    check("C", "registrul lipsește în Cloud și poate fi provizionat", status0)

    def sans_confirm():
        try:
            a_.associate_with_admin(admin, tag, "test", False)
        except assoc.AssociationError as exc:
            assert exc.kind == "confirm"
            assert a_.registry_in_cloud(admin)["action"] == "provision_all", "s-a scris ceva fără confirmare"
            return
        raise AssertionError("asocierea a rulat fără confirmare")
    check("C", "fără confirmare nu se scrie nimic", sans_confirm)

    def go():
        r = a_.associate_with_admin(admin, tag, "test", True)
        assert r["provision"]["organization_uid"] == S["org"] and r["provision"]["cemetery_uid"] == S["cem"], "UID-urile nu sunt cele originale"
        assert r["verification"]["sync_enabled"]
        return "provizionat + asociat + verificat; UID-uri păstrate"
    ok = check("D", "provizionare + cod + asociere + verificare", go)

    def state():
        assert bs.LocalRegistry(S["b"]).sync_state == "BOUND"
        key = S["store"].get(bs.LocalRegistry(S["b"]).get("cloud_secret_ref"))
        assert key and key.startswith("cimitir.") and key.encode() not in Path(S["b"].database).read_bytes()
        assert a_.registry_in_cloud(admin)["action"] == "ready"
        devs = [d for d in admin.list_devices(S["org"]) if d["cemetery_uid"] == S["cem"]]
        assert [d["state"] for d in devs] == ["active"]
        return "BOUND, cheia doar în SecretStore, dispozitiv activ"
    if ok:
        check("D", "stare finală", state)

    other = os.environ.get("CIMITIR_LIVE_OTHER_ORG_UID", "")
    if other:
        def other_org():
            st = admin.registry_status(other, S["cem"])
            assert st["action"] == "conflict", st["action"]
            try:
                admin.provision(other, "Intrus", S["cem"], "x")
            except adc.AdminApiError as exc:
                assert exc.status in (400, 403), exc.status
                return f"respins ({exc.status})"
            raise AssertionError("provizionare acceptată cu UID de cimitir deja folosit în altă organizație")
        check("E", "izolare: UID de cimitir deja folosit nu se reutilizează", other_org)
    else:
        record("E", "izolare: organizație străină", "SKIP", "CIMITIR_LIVE_OTHER_ORG_UID nesetat")

    def revoke():
        for d in admin.list_devices(S["org"]):
            if d["cemetery_uid"] == S["cem"] and d["state"] == "active":
                admin.revoke_device(S["org"], d["binding_uid"])
        left = [d for d in admin.list_devices(S["org"]) if d["cemetery_uid"] == S["cem"] and d["state"] == "active"]
        assert not left
        return "dispozitivul de test a fost revocat"
    if ok:
        check("F", "revocare dispozitiv", revoke)
    def request_flow():
        org2 = cat_org2 = None
        cat = S["cat"]
        org2 = cat.create_organization("parohie", tag + "-REQ")
        cem2 = cat.create_cemetery(org2, "test-cerere")
        b2 = cat.binding_for(cem2)
        reg.prepare_registry_db(b2)
        a2 = assoc.Association(b2, S["store"], S["inst"], url)
        try:
            a2.request_registration(tag + "-REQ", "test-cerere", False)
            raise AssertionError("cerere trimisă fără confirmare")
        except assoc.AssociationError as exc:
            assert exc.kind == "confirm"
        r = a2.request_registration(tag + "-REQ", "test-cerere", True)
        assert a2.registry_in_cloud(admin)["action"] == "provision_all", "cererea a scris în Cloud înainte de confirmare"
        assert a2.complete_registration("test-cerere")["state"] == "pending"
        mine = [q for q in admin.list_requests() if q["request_id"] == r["request_id"]]
        assert len(mine) == 1 and mine[0]["organization_uid"] == org2 and mine[0]["cemetery_uid"] == cem2 and not mine[0]["organization_exists"]
        try:
            assoc.Association(b2, S["store"], S["inst"], url).request_registration(tag, "x", True)
            raise AssertionError("duplicat acceptat")
        except assoc.AssociationError as exc:
            assert "admin_conflict" in exc.kind or "conflict" in exc.kind.lower(), exc.kind
        admin.decide_request(r["request_id"], True)
        out = a2.complete_registration("test-cerere")
        assert out["state"] == "associated" and out["verification"]["sync_enabled"] and bs.LocalRegistry(b2).sync_state == "BOUND"
        for d in admin.list_devices(org2):
            if d["state"] == "active":
                admin.revoke_device(org2, d["binding_uid"])
        return "cerere → confirmare admin → cod MAIN automat → asociere; duplicat respins; dispozitiv revocat"
    if ok:
        check("G", "cerere de înregistrare din MAIN confirmată de administrator", request_flow)
    admin.close()
    return v.finish(a.label)


if __name__ == "__main__":
    sys.exit(main())
