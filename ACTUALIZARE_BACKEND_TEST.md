# Actualizarea controlată a backendului Cloud TEST (8086 → versiunea Mobile Complet + permisiuni)

**Se aplică doar pe TEST. Nu atinge PROD. Nu atinge containerele 8086/8087 până la pasul `switch` (cu `-Confirm`).**

## De ce și când
MAIN folosește backendul de pe `8086` (vechi), iar Mobile Complet rulează pe `8087`. Ambele folosesc aceeași bază TEST, deci **datele, pairingul și organizațiile sunt în bază, nu în container**.
Un backend vechi nu are rutele noi (`/api/v1/sync-v2/instance`, `/devices/{ref}/matrix`, `/phone-pairing/codes` listare/revocare). Fără ele MAIN nu poate salva permisiuni pe categorii,
iar telefonul rămâne „Doar citire”. De aceea backendul se **înlocuiește**, nu se schimbă portul MAIN către `8087`.

## Reguli de siguranță (respectate de script)
1. **Identitatea instanței înainte de orice schimbare**: `scripts/verify_backend_instance.py` cere `GET /api/v1/sync-v2/instance` cu cheia MAIN și afișează *release*, *capabilități* și **amprenta bazei de date**.
   Două backenduri care folosesc aceeași bază + același cimitir au **aceeași amprentă**. Amprente diferite ⇒ nu se continuă.
2. **Autentificarea**: dacă backendul respinge cheia MAIN (401) este alt mediu/altă bază ⇒ nu se continuă.
3. **Migrările sunt doar aditive** (027 este deja instalată pe TEST; nu se rulează nimic nou și nu există `DROP`). Containerul nou NU rulează migrări la pornire.
4. **Canary** pe port separat (`8088`), cu mediul copiat din containerul vechi (fișier temporar, citibil doar de utilizatorul curent, șters imediat; secretele nu se afișează).
5. **Switch** doar cu `-Confirm`: containerul vechi este oprit și **redenumit** (`<nume>-previous-<data>`), nu șters. La orice eșec se revine automat la el.
6. **Rollback** instant: `rollback` șterge containerul nou și repornește pe cel vechi. Datele din bază nu sunt afectate în niciun sens.

## Pași (PowerShell, în folderul repo-ului actualizat)
```powershell
$env:CIMITIR_MAIN_KEY = "<cheia MAIN din SecretStore>"      # doar în sesiunea curentă; nu se afișează
cd deploy\windows
.\update_test_backend.ps1 inspect                            # stare 8086/8087: release, capabilități, amprentă bază
.\update_test_backend.ps1 build  -Tag mobile-perm-1 -RepoDir ..\..
.\update_test_backend.ps1 canary -Old <nume-container-8086> -Tag mobile-perm-1 -RepoDir ..\..
.\update_test_backend.ps1 verify -OldPort 8086 -RepoDir ..\..
```
`verify` trebuie să spună „ACEEAȘI bază de date … release-uri diferite: actualizare sigură”. Abia apoi:
```powershell
.\update_test_backend.ps1 switch -Old <nume-container-8086> -OldPort 8086 -Tag mobile-perm-1 -Confirm -RepoDir ..\..
.\update_test_backend.ps1 canary-stop
```
Dacă ceva nu merge: `.\update_test_backend.ps1 rollback -Old <nume-container-8086> -OldPort 8086`.

Aceeași procedură se poate aplica separat containerului `8087` (de obicei nu e nevoie: `8087` rulează deja versiunea nouă). După actualizare, `8086` și `8087` vor avea același release și aceeași amprentă.

## După actualizare
- În MAIN: Centrul Cloud → F. Mobile → «Asociază telefoane (cod / QR)…»: bannerul trebuie să arate „Backend verificat … versiune mobile-complet-…”.
- Telefoanele deja asociate rămân asociate (cheile lor sunt în bază), cu aceleași drepturi; nu se recreează organizații.

## Verificare numai în citire (fără Docker)
```powershell
python scripts\verify_backend_instance.py --url http://127.0.0.1:8086 --url http://127.0.0.1:8087
```
Cod de ieșire: `0` OK · `2` backend vechi/migrări lipsă · `3` cheie respinsă / baze diferite / inaccesibil.
