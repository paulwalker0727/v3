# Etapa 7-9 — Raport de livrare (Cimitir Sync v2)

Ramură: `etapa789-finalizare` (pornită din `main` @ `11b8c61`; `main` NU a fost modificat). Proiect Supabase TEST folosit: `jqtdeuzkqtzfsdohvxmy` (nou, dedicat). PROD nu a fost atins.

## 1. Ce s-a livrat (fără a reface ce exista)

| Zonă | Modificare | Fișiere principale |
|---|---|---|
| **Etapa 9 — date personale** | Filtrare **pe server** a `cnp`, `bi_ci`, `owner_cnp`, `owner_id_card_serie_number` pentru toate dispozitivele non-MAIN: pull, rând, conflicte, răspunsuri push (și jurnalul `sync_v2_ops`), căutare (nu se mai poate căuta după CNP). Scrierile de pe telefon nu pot citi/suprascrie/șterge câmpurile sensibile (valoarea din server se păstrează). Acces excepțional doar prin capabilitatea explicită `view_pii` (niciun șablon de rol, nici „administrator", nu o include). MAIN primește tot. Cache offline: copiile vechi din IndexedDB se curăță la upgrade. | `shared/cimitir_sync_v2.py`, `shared/cimitir_mobile_registry.py`, `backend/static/mobile_offline.js` |
| **Etapa 8 — documente MAIN→Mobile** | Tabel `sync_v2_documents` (migrarea **024**, RLS), serviciu `DocumentService` (declarare idempotentă → încărcare → verificare SHA-256/mărime/semnătură → READY), Storage PRIVAT, fără URL-uri către telefon (octeții trec prin API după autorizare), duplicate evitate (uid + aceeași entitate/SHA-256), ștergere doar logică și doar de MAIN (octeții se păstrează), acte de identitate/cereri **nu** se sincronizează. MAIN trimite/primește automat documentele la fiecare ciclu (`sync_v2_docs.py`, cursor + stare locală, fără ecou, fără ștergerea fișierelor locale). | `shared/cimitir_sync_v2_docs.py`, `shared/cimitir_sync_v2_http.py`, `backend/cimitir/pg_migrations/024_*`, `desktop/sync_v2_docs.py`, `desktop/sync_v2.py` |
| **Etapa 8 — fotografii offline** | Fotografiere/alegere din galerie, coadă IndexedDB (tip, mărime, semnătură, SHA-256 verificate local), reluare automată (eveniment `online` + la 45 s + la sync), fără încărcări duplicate (local + server), indicator „în coadă / eroare" pe butonul de sincronizare, secțiune „Documente și fotografii" în fișa locului și ecranul Documente, vizualizare cu cache offline pentru documentele deja deschise, avertisment la dezasociere dacă există fotografii nesincronizate. | `backend/static/mobile_photos.js`, `mobile_app.js`, `mobile_sw.js` |
| **Etapa 8 — QR/proxy** | Scanare QR în PWA: `BarcodeDetector` **sau** fallback `jsQR` (iOS/Firefox); asocierea prin camera nativă rămâne. `public_base_url()`: `CIMITIR_PUBLIC_URL` > `X-Forwarded-*` doar de la proxy declarat în `CIMITIR_PAIRING_TRUSTED_PROXIES` > adresa cererii. Mesaje clare pentru cod invalid/expirat/revocat/fără conexiune. | `shared/cimitir_phone_pairing_http.py`, `shared/cimitir_cloud_admin_http.py`, `backend/static/jsQR.js` (Apache-2.0) |
| **Etapa 7 — Cloud Config** | Verificat: configurare simplă (nume/URL/mediu), asociere cu cod, chei în SecretStore, reconectare automată (`AutoSync` la pornire), schimbare adresă fără pierdere de date (profilul se actualizează, datele locale rămân). Adăugat: **indicator unic** conectat / offline / eroare / sincronizare în curs (`cloud_simple.sync_indicator()`), resetarea cursoarelor (și a stării documentelor, doar la schimbarea *cimitirului*) la schimbarea adresei. | `desktop/cloud_simple.py`, `desktop/sync_v2.py` |
| **Bug real găsit în LIVE** | `AdminService.bootstrap` cu organizație nouă și fără uid de cimitir lăsa instalarea pe jumătate (admin creat, bootstrap închis, apoi eroare RLS la audit). Reparat + test de regresie. | `shared/cimitir_cloud_admin.py` |
| **Scripturi** | `scripts/setup_test_project.py` (migrații 001–024 + rol runtime + verificare RLS; refuză PROD), `scripts/seed_test_project.py` (admin + organizație + cimitir FICTIVE). | `scripts/` |

Compatibilitate: Field Inbox (`/api/v1/field-inbox/*`), `documents`/`photos`/`media_objects` și migrările 001–023 sunt neatinse. Un Cloud mai vechi (fără endpoint-uri de documente) nu blochează sincronizarea registrelor (MAIN raportează „actualizează Cloud-ul").
Decizie de proiectare de confirmat: fotografiile de pe telefon intră prin `sync_v2_documents` (cu dreptul `upload_documents`) și MAIN le importă automat — nu mai trec prin aprobarea manuală din Field Inbox (care rămâne disponibil neschimbat).

## 2. Rezultate teste (LIVE vs LOCAL vs SIMULAT sunt etichetate)

### 2.1 LIVE — Supabase TEST `jqtdeuzkqtzfsdohvxmy` (PostgreSQL real, rol runtime `cimitir_cp_app`, RLS activ, **Storage Supabase real, bucket privat `cimitir-test-docs`**)
Suita `tests/test_etapa789_live_supabase_test.py` (aceleași teste ca local, rulate pe proiectul TEST): **16 PASS / 0 FAIL / 0 SKIP**.
(14 PASS într-o rulare de 34 min + 2 teste PWA reluate după mărirea timeout-urilor HTTP pentru latența de ~100 ms; Chromium rulează local, API-ul/DB/Storage sunt LIVE.)

| Test | Rezultat |
|---|---|
| MAIN (proces real SQLite) → HTTP → PostgreSQL → telefon → PostgreSQL → MAIN, documente + fotografii, ștergere logică, octeții rămân în Storage | PASS |
| MAIN offline cu documente în așteptare → reluare, fără duplicate | PASS |
| Telefonul NU primește CNP/serie CI (pull, rând, fișă, căutare, conflicte, răspunsuri, jurnal `sync_v2_ops`) | PASS |
| Editare de pe telefon nu poate citi/suprascrie/șterge câmpuri sensibile; înregistrare nouă ignoră CNP | PASS |
| `view_pii` explicit; „administrator" nu îl include; alt telefon rămâne filtrat; altă organizație nu vede nimic | PASS |
| Document MAIN ajunge pe telefon, integritate SHA-256, dedupe, cale server-side | PASS |
| Validare: hash/mărime greșite, semnătură falsă (EXE ca PNG), MIME neacceptat, act de identitate refuzat, >25 MB, părinte lipsă (retry), nedeclarat | PASS |
| Întrerupere upload → reluare; obiect existent cu alt conținut refuzat (409) | PASS |
| Permisiuni telefon (upload fără drept = 403; ștergere doar MAIN), ștergere logică vizibilă pe telefon | PASS |
| Citire documente urmează permisiunile pe registre; izolare org A/B/A2; dispozitiv revocat; cheie invalidă | PASS |
| **RLS la nivel de bază de date**: altă organizație nu poate nici citi, nici insera în `sync_v2_documents` | PASS |
| Circuit complet MAIN→PG→Mobile→PG→MAIN | PASS |
| **Storage real**: bucket privat, upload/SHA-256, `AlreadyExists` (fără suprascriere), acces fără cheie / rută publică / cheie falsă respinse, URL semnat fără cheie secretă, ștergere | PASS |
| PWA în Chromium: fotografii offline → coadă → reluare → fără duplicate; documentul MAIN se vede și se deschide; coada supraviețuiește repornirii | PASS |
| PWA: curățarea copiilor offline vechi cu date personale la upgrade | PASS |

Setup LIVE: migrările 001–024 aplicate cu `scripts/setup_test_project.py` (24/24 OK), rol `cimitir_cp_app` (LOGIN, fără SUPERUSER/BYPASSRLS), toate tabelele cheie cu RLS. Administrator + organizație + cimitir FICTIVE create (`seed_test_project.py`; parola nu e în repo).

### 2.2 LOCAL (PostgreSQL local real + Storage SIMULAT — nu LIVE)
Vezi `ETAPA789_REZULTATE_TESTE.md` — 207 PASS / 1 FAIL (test învechit preexistent). Teste noi: `test_etapa789_pii_docs_pg.py` (12), `test_etapa789_docs_main_e2e.py` (2), `test_etapa789_pwa_photos.py` (2), `test_etapa789_public_url_qr.py` (4), `desktop/tests/test_etapa789_cloud_indicator_docs.py` (3) — toate PASS local.

### 2.3 Teste vechi care eșuau (investigat, nu modificate pentru PASS)
Comparație pe același mediu, `main` vs ramură: **aceleași eșecuri, zero regresii rămase** (două regresii introduse de mine au fost găsite de această comparație și reparate: `create_app` fără `media` și textul „Cod invalid" din PWA).

| Grup | Cauză | Clasificare |
|---|---|---|
| desktop `test_build_packaging` (spec, YAML), `test_bugfix_and_qrcustom`, `test_qr_custom_and_map` (8) | lipsă `PyInstaller`, `qrcode`, `reportlab` în mediul Linux; după instalare dispar; rămân 3 cu `.github/workflows/build-windows.yml` absent din locația de test | **Mediu** (dependențe) |
| desktop `test_local_first_launcher`, `test_startup_unification`, `test_windows_guide_flow` (portal/porturi/scripturi `.bat`) | pornesc procese Windows (launcher, `reset_admin_password`, DPAPI, porturi) | **Mediu Linux/Windows** → NETESTAT pe Windows |
| desktop `test_qr_custom_and_map::test_export_current_writes_png` | blochează în `QFileDialog` offscreen | **Mediu** (dezactivat, nu rulează în Linux) |
| backend `test_first_run_bootstrap` (8) | lipsă `psycopg_pool`; restul necesită `DATABASE_URL` Postgres configurat ca la MAIN | **Mediu**; 4/8 trec după instalare |
| backend `test_etapa9b_cloud_config` (2) | `backend/.env(.example)` absent în checkout | **Mediu** |
| backend `test_etapa6c_field_inbox_pg::test_production_is_hard_blocked` | asertează lista *ultimelor 5 migrări* = 015..019 (depășit încă din 020–023) | **Test învechit (real)** — de actualizat de proprietar |
| backend `test_etapa1_audit::test_route_inventory_uses_only_clear_prefixes` | rută `/api/m/{token}/info` în afara prefixelor permise | **Eroare reală preexistentă** (nu din această etapă) |
| desktop `test_phone_capture::test_no_secret_or_signed_url_in_client_code_and_page`, `test_field_import_history`, `test_work_native_fields::test_cloud_migrations_untouched`, `test_etapa1_desktop_flows` | asertări despre starea migrărilor/șabloanelor din etapele vechi (ex. „fără migrare nouă", dimensiune PDF) | **Preexistente**, de revăzut (fără legătură cu modificările de față; `test_cloud_migrations_untouched` va rămâne roșu cât există 024 — decizie de proprietar) |

## 3. NETESTATE (explicit)
- **Windows real** (UI PySide6 pentru Cloud Config, DPAPI/SecretStore real, `.bat`/instalator, launcher): NETESTAT (doar logica fără UI, testată pe Linux, offscreen).
- **Cameră reală**: NETESTAT — nici scanarea QR din PWA, nici fotografierea (testate cu file chooser simulat și cu decodare jsQR a unui QR generat; `getUserMedia` pe telefon real rămâne de verificat, inclusiv iOS Safari și Android Chrome).
- **Cloudflare / reverse proxy real**: NETESTAT (logica `public_base_url` și lista de proxy de încredere testate unitar; fără domeniu real/HTTPS).
- **Supabase PROD**: neatins (nu a fost cerut).
- Încărcare Storage cu fișiere mari (25 MB) peste rețea mobilă: NETESTAT.

## 4. Probleme rămase / riscuri
1. Documentele șterse în MAIN rămân în Storage (ștergere logică; nu există job de purjare — decizie de retenție de luat). Obiecte orfane (upload întrerupt, rând PENDING/FAILED) nu se curăță automat.
2. Datele de contact ale concesionarilor (telefon, adresă, e-mail) **rămân vizibile** pe telefon (necesare operațional); doar CNP/act de identitate sunt filtrate. Confirmă dacă vrei și telefon/adresă în spatele unei capabilități.
3. Fotografiile de pe telefon ocolesc coada de aprobare Field Inbox (vezi 1).
4. Parola `postgres` a proiectului TEST a fost comunicată în chat (nu e în repo/loguri); rotește-o. Cheia secretă Storage și parola rolului runtime sunt doar în `/root/.cimitir_live.env` (în afara repo-ului, `chmod 600`) — de revocat/rotit după testare.
5. Testele vechi roșii de mai sus (în special `/api/m/{token}/info` și lista migrărilor) cer decizii de proprietar.
6. Documentele vizualizate pe telefon sunt memorate (Cache API) pentru consultare offline; la dezasociere se șterg, dar un telefon pierdut rămâne un risc — recomandare: revocare imediată din `/admin/`.
