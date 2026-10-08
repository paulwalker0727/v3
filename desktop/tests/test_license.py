"""
Test end-to-end pentru modulul de licențiere Cimitir.

Ruleaza:
    python tests/test_license.py

Verifică:
  1. Generarea perechii de chei Ed25519
  2. Semnarea unei licențe pentru HWID-ul curent
  3. Verificarea licenței (semnătură + HWID + expirare)
  4. Salvarea/încărcarea la calea implicită (%APPDATA% / ~/.config)
  5. Detectarea licenței expirate
  6. Detectarea licenței cu HWID greșit
  7. Detectarea licenței cu semnătură invalidată (tampered)
"""
import os
import sys
import json
import shutil
import base64
import tempfile
from pathlib import Path
from datetime import datetime, timezone, date, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _reload_validator_with_pubkey(pub_pem_bytes: bytes):
    """Re-încarcă modulul validator cu o nouă cheie publică (pentru test)."""
    import importlib
    from license import validator as v
    v.PUBLIC_KEY_PEM = pub_pem_bytes
    v._PUB = v.load_public_key(pub_pem_bytes)
    return v


def main():
    # importăm generatorul standalone din tools/
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    from license_manager import generate_ed25519_keypair
    from license import validator as v

    print("=" * 60)
    print("TEST LICENȚIERE CIMITIR")
    print("=" * 60)

    # 1) generează keypair de test
    tmp = Path(tempfile.mkdtemp(prefix="cimitir_lic_test_"))
    priv_path, pub_path, pub_pem = generate_ed25519_keypair(tmp)
    print(f"\n[1] Keypair generat în {tmp}")

    # 2) încarcă cheia publică nouă în validator (înlocuiește PUBLIC_KEY_PEM)
    _reload_validator_with_pubkey(pub_pem.encode())
    priv = v.load_private_key(priv_path.read_bytes())

    hwid = v.get_hwid()
    print(f"[2] HWID curent: {hwid}")

    # 3) semnează o licență valabilă 30 zile
    expira = (date.today() + timedelta(days=30)).isoformat()
    payload = {
        "hwid": hwid,
        "client": "Primăria Test",
        "expira": expira,
        "emis_la": datetime.now(timezone.utc).isoformat(),
    }
    licenta = v.make_license(payload, priv)
    lic_file = tmp / "licenta.lic"
    lic_file.write_text(json.dumps(licenta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[3] Licență semnată salvată: {lic_file}")

    # 4) verifică fișierul
    r = v.verifica_fisier(lic_file, hwid)
    assert r.ok, f"[FAIL] Verificare licență validă: {r.motiv}"
    assert r.client == "Primăria Test"
    assert r.expira == expira
    print(f"[4] ✓ Verificare licență validă: client={r.client!r}, expira={r.expira}")

    # 5) salvează la calea implicită
    print(f"[5] Salvez la LICENSE_FILE: {v.LICENSE_FILE}")
    # backup dacă există
    backup = None
    if v.LICENSE_FILE.exists():
        backup = v.LICENSE_FILE.read_bytes()
    try:
        v.salveaza_licenta(v.LICENSE_FILE, licenta)
        r2 = v.verifica_licenta_curenta()
        assert r2.ok, f"[FAIL] Verificare la LICENSE_FILE: {r2.motiv}"
        print(f"    ✓ Verificare licență la LICENSE_FILE: OK, client={r2.client!r}")
    finally:
        # restaurează backup
        if backup is not None:
            v.LICENSE_FILE.write_bytes(backup)
        else:
            try:
                v.LICENSE_FILE.unlink()
            except FileNotFoundError:
                pass

    # 6) licență expirată
    expirat_payload = {
        **payload,
        "expira": (date.today() - timedelta(days=1)).isoformat(),
    }
    lic_exp = v.make_license(expirat_payload, priv)
    p = tmp / "expirata.lic"
    p.write_text(json.dumps(lic_exp), encoding="utf-8")
    r = v.verifica_fisier(p, hwid)
    assert not r.ok and "expirat" in r.motiv.lower(), f"[FAIL] Licență expirată nedetectată: {r}"
    print(f"[6] ✓ Licență expirată detectată corect: {r.motiv}")

    # 7) HWID greșit
    lic_wrong_hwid = v.make_license({**payload, "hwid": "deadbeef" * 8}, priv)
    p = tmp / "hwid_gresit.lic"
    p.write_text(json.dumps(lic_wrong_hwid), encoding="utf-8")
    r = v.verifica_fisier(p, hwid)
    assert not r.ok and "calculator" in r.motiv.lower(), f"[FAIL] HWID greșit nedetectat: {r}"
    print(f"[7] ✓ HWID greșit detectat corect: {r.motiv}")

    # 8) semnătură invalidată (tampering — modificăm payload-ul după semnare)
    tampered = v.make_license(payload, priv)
    tampered_data = json.loads(json.dumps(tampered))
    tampered_data["payload"]["client"] = "Atacator"  # payload modificat, semnătură veche
    p = tmp / "tampered.lic"
    p.write_text(json.dumps(tampered_data), encoding="utf-8")
    r = v.verifica_fisier(p, hwid)
    assert not r.ok and "emn" in r.motiv.lower(), f"[FAIL] Tampering nedetectat: {r}"
    print(f"[8] ✓ Tampering detectat corect: {r.motiv}")

    # 9) permanent (expira=None)
    permanent = v.make_license({**payload, "expira": None}, priv)
    p = tmp / "permanent.lic"
    p.write_text(json.dumps(permanent), encoding="utf-8")
    r = v.verifica_fisier(p, hwid)
    assert r.ok, f"[FAIL] Licență permanentă: {r.motiv}"
    print(f"[9] ✓ Licență permanentă acceptată (expira=None)")

    # 10) fișier corupt / lipsă
    r = v.verifica_fisier(Path("/nu/exista/nicaieri.lic"), hwid)
    assert not r.ok, "[FAIL] Fișier lipsă neraportat"
    print(f"[10] ✓ Fișier lipsă raportat: {r.motiv}")

    p = tmp / "corupt.lic"
    p.write_text("nu-e-json-valid", encoding="utf-8")
    r = v.verifica_fisier(p, hwid)
    assert not r.ok, "[FAIL] Fișier corupt neraportat"
    print(f"    ✓ Fișier corupt raportat: {r.motiv}")

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + "=" * 60)
    print("TOATE TESTELE AU TRECUT")
    print("=" * 60)


if __name__ == "__main__":
    main()
