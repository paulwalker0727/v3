# Raport — #7 Cloud Config simplificat, #8 Mobile v2 (asociere + verificare), #9 protecții

Ramura: `cloud-config-simplify-mobile-v2-verify` (din `main` @ 4edfd02, după PR #17; cc3a074 verificat în main). `main`, `mobile-v2`, migrațiile 001–023, RLS, ID-urile Craiova: **neatinse**.
Regresie finală locală: 98 teste PG/HTTP/Chromium trecute (cloud admin+associate, phone pairing, sync v2 PG/e2e/PWA/migrare, verify script, teste noi) + 85 desktop (cloud simple/profiles/field inbox).
Nu s-a rulat nimic pe Supabase TEST/PROD, nu s-a cerut nicio parolă/cheie. Toate rezultatele de mai jos sunt **LOCALE** (PostgreSQL local, rol `cimitir_cp_app` fără BYPASSRLS, HTTP real, Storage simulat, MAIN = proces SQLite real). **LIVE: NETESTAT.**

## Ce s-a implementat
### #7 Simplificare Cloud Config (MAIN, PySide6)
* `desktop/cloud_simple.py` + `desktop/cloud_simple_dialog.py`: dialog nou „Configurare Cloud" cu DOAR: Nume profil, Adresă Cloud, Mediu (TEST/PROD), Stare conexiune, Stare asociere, Cimitir asociat (nume, nu UID). Butoane: Salvează, Verifică conexiunea, Asociază acest calculator.
* Nu mai cere: host/utilizator/parolă PostgreSQL, Supabase Secret Key, cheia brută de sincronizare. La salvare, câmpurile ascunse și secretele deja existente din SecretStore rămân neschimbate. PROD cere confirmare explicită.
* Cele 3 puncte de intrare (Sincronizare cloud, Telefoane teren, Field Inbox) deschid dialogul simplu. Câmpul „Cheie sincronizare" e ascuns în fereastra „Sincronizare cloud". Dialogul avansat vechi rămâne, vizibil doar dacă administratorul pornește MAIN cu `CIMITIR_CLOUD_ADVANCED_UI=1`.
* Bug existent reparat în `cloud_profiles.py` (din commit 47431c2): diacritice corupte („r?spunde") + testele rămăseseră pe rutele vechi (`/api/health`) → 2 teste picau pe `main`. Testul de conexiune folosește acum și cheia Binding a legăturii MAIN din SecretStore, deci „Conectat" reflectă o verificare reală (health + cheie + cimitir).

### #8 Mobile v2
* Asociere prin **link/QR**: `<cloud>/mobile/#pair=<cod>` (codul în fragment → nu ajunge în jurnalele serverului; PWA îl scoate din bara de adrese). PWA asociază automat, arată „Asociat automat: <organizație> — <cimitir>"; cod invalid/folosit → păstrează asocierea existentă; buton „Scanează codul QR" (unde browserul are BarcodeDetector).
* QR generat în: interfața web `/admin/` (`pair_url` + `qr` SVG în răspunsul `POST /codes` pentru telefon) și în aplicația Windows (fila „Coduri și dispozitive"). Dependență nouă: `segno` (pur Python).
* Ecranul **Documente** din PWA nu mai e „neimplementat": listează fotografiile/documentele trimise de acel telefon (Field Inbox), cu stare și deschidere prin URL semnat; în fișa locului: „Adaugă fotografie" (JPEG/PNG, online).
* Service worker: cache v4.

### Teste noi
* `desktop/tests/test_cloud_simple.py` (13), `backend/tests/test_mobile_v2_verification_pg.py` (7), `backend/tests/test_mobile_v2_pwa_pairing.py` (2, Chromium real), +asert. QR în `test_cloud_admin_pg.py`.
* Acoperire din cerință: asociere cod/QR → organizație+cimitir corecte, cod de unică folosință, revocare; harta; locuri; concesionari; înhumați; contracte; plăți; avize; documente/fotografii (upload idempotent, SHA-256, izolare); sincronizare repetată + întrerupere internet fără duplicate; modificări simultane (exact unul aplicat, celălalt conflict); izolare A/B (mobile, pull, push, field-inbox).

## Ce NU am reușit / NU e verificat (nu considerați PASS)
1. **LIVE pe Supabase TEST — nerulat** (fără acces/chei, conform cerinței). Migrarea 023 pe TEST rămâne neconfirmată; nu am aplicat nimic. Rulați întâi read-only: `scripts/apply_cloud_admin_migration.py --project-ref fommbiwndlxkfsnlqatq --check-only`, apoi `scripts/verify_cloud_live.py` (cere aprobarea voastră, scrie date de test).
2. **Windows/DPAPI/PySide6 pe Windows** — verificat doar offscreen pe Linux; aspectul vizual al dialogului nou nu a fost văzut pe Windows.
3. **Documente/fotografii MAIN → telefon: NU există.** Sync v2 sincronizează doar 6 registre (locuri, concesionari, contracte, înhumați, plăți, avize). Documentele atașate în MAIN (contracte scanate, poze) nu ajung pe telefon; telefonul vede doar ce a trimis el prin Field Inbox. Necesită etapă separată (migrare + Storage).
4. **Fotografii offline** (coadă offline pentru poze) — neimplementat; încărcarea cere conexiune. Fotografia de pe telefon nu a fost testată cu cameră reală, doar prin file chooser în Chromium.
5. **Scanarea QR din PWA** (BarcodeDetector) — cod scris, **netestat** (cameră indisponibilă în test). Scanarea cu camera nativă a telefonului → link → auto-asociere este testată (Chromium).
6. QR-ul folosește `CIMITIR_PUBLIC_URL` sau host-ul cererii; în spatele unui proxy/tunel Cloudflare setați `CIMITIR_PUBLIC_URL` corect (netestat cu tunel real).
7. **Constatare de confidențialitate, NEremediată:** entitățile sincronizate către telefon includ câmpuri precum `cnp` (concesionari/înhumați) și `owner_cnp`/serie CI (contracte); accesul e limitat doar prin rolul telefonului (implicit „limitat"). Recomand filtrarea câmpurilor sensibile pe server pentru MOBILE — nu am schimbat comportamentul fără aprobare.
8. Suita completă `backend/tests` pe Linux: 805 trecute, 7 picate + 43 erori **înainte** de modificările mele (rulare de bază oprită la 50 eșecuri; teste vechi ale backend-ului web/Windows, ex. `test_attachments`, `test_etapa1_audit`, `test_cloud_config_ui`, fără legătură cu Sync v2/Cloud). Desktop de bază: 21 picate (packaging Windows/launcher), din care 2 din `test_cloud_profiles` — reparate acum. Nu am reparat restul (în afara scopului).
9. `desktop/tests` fără Windows: `test_build_packaging`, `test_local_first_launcher`, etc. continuă să pice pe Linux.

## Aplicare
```
git checkout main && git checkout -b cloud-config-simplify-mobile-v2-verify
git am cloud-config-mobile-v2.patch      # sau: git apply --index
pip install segno
python -m pytest desktop/tests/test_cloud_simple.py desktop/tests/test_cloud_profiles.py -n 0
CIMITIR_TEST_PG_ADMIN_URL=postgresql://postgres@127.0.0.1:<port>/postgres python -m pytest backend/tests/test_mobile_v2_verification_pg.py backend/tests/test_mobile_v2_pwa_pairing.py -n 0
```
Variabile pentru LIVE (doar în configurarea serverului/SecretStore, nu în chat/Git): `CIMITIR_CP_DATABASE_URL` (rol `cimitir_cp_app`, nu admin), `CIMITIR_PUBLIC_URL`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY` (doar server, pentru Storage), `CIMITIR_CLOUD_PROJECT_REF`; pentru migrarea 023 separat `CIMITIR_CP_ADMIN_DATABASE_URL` (DDL, doar o dată, doar TEST).
