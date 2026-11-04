# Raport Etapele 5–9 — branch `feature/etapa5-9-finalizare`

Bazat pe `feature/etapa1-9-finalizare` (commit-urile 8455cf1, 7ddc365, 0041c68, 5df7ccb confirmate). Niciun merge în `main`, fără force-push.
**Mediu de lucru:** Linux, Python 3.11, PostgreSQL 15 LOCAL (izolat, date fictive), Qt offscreen. **NU** s-a atins PROD, Supabase TEST, containerele Docker existente; migrarea 028 **nu** a fost rulată pe infrastructura reală (doar pe baze locale de test).
**NU s-a testat pe Windows real și nu s-a testat pe telefon real** — vezi `ETAPA5_9_TESTARE_WINDOWS_TELEFON.md`.

## 0. Audit (rezumat)
- Arhitectură confirmată: `desktop/` (PySide6), `backend/` (FastAPI, `cimitir/postgres_store.py`, migrații 001–028 în `backend/cimitir/pg_migrations/`), `shared/` (SQLite core, sync v2, backup, integritate), PWA în `backend/static/mobile_*.js`.
- Sincronizarea (conflicte, replay idempotent, câmpuri ignorate CNP/CI, coadă offline) era deja acoperită de teste existente (`test_sync_v2_*`, `test_avize_complete_028*`, `test_mobile_*`, `test_etapa789_pii_docs_pg`); nu a fost rescrisă.
- **Defecte reale găsite** (restul eșecurilor erau teste învechite sau artefacte de mediu, listate mai jos).

## 1. Etapa 5 — Cloud, migrarea 028, sincronizare (commit 3f05ae5)
**Problemă reală:** `postgres_store.py` (Cloud admin) nu citea/scria coloanele 028 (`stare, executant, data_inceput, data_finalizare, observatii`) — pe PostgreSQL, Cloud admin ascundea câmpurile (`ENGINE != postgres`), iar fișa PDF Cloud nu le afișa.
**Corecție:** detectare compatibilă a coloanelor (`avize_optional_cols()`: coloana există ȘI rolul runtime are `UPDATE`; cache 30 s, reîmprospătabil), `avize_extended()`, `list/get/add/update_aviz` cu câmpurile complete; fără 028 → mod compatibil (nr/data/lucrare) fără eroare și fără pierderi. `server.py` folosește capabilitatea reală, nu `ENGINE`.
Fișiere: `backend/cimitir/postgres_store.py`, `backend/server.py`, `shared/cimitir_database.py`; teste noi: `test_etapa5_cloud_admin_avize_pg.py` (5), `test_etapa5_admin_avize_routes.py` (3).
**Note:** după aplicarea migrării 028 pe un server pornit, detectarea se actualizează în ≤30 s. Raportarea câmpurilor ignorate (CNP/CI) există deja în răspunsul sync v2 (`ignored_fields`) și în PWA.

## 2. Etapa 6 — Mobile PWA (commit 01a4dd8)
Existent verificat (62 teste PASS în subsetul mobile: pairing, permisiuni, avize/documente PDF, fotografii, plăți/contracte, offline, replay). Lacună completată: `navigator.storage.persist()` (reduce evacuarea IndexedDB pe iOS/Android), cache shell `v11`. Test nou `test_etapa6_pwa_compat.py` (4): shell precache complet, metadate iOS/Android, coadă fără Background Sync (Safari), revocare ⇒ date locale șterse, SW nu cache-uiește `/api/`. **Nu s-a creat un sistem Mobile paralel.**

## 3. Etapa 7 — Backup/restaurare (commit 2c6b728)
Defecte reale în `shared/cimitir_backup.py` (corectate):
1. `verify()` arunca excepție la `manifest.json` corupt și accepta intrări de cale nesigure (`../`) sau manifest fără `cimitir.db`.
2. `restore()` putea lăsa folderul de date incomplet la eșec; acum rollback automat la starea anterioară; copia de siguranță `…inainte-de-restaurare-*` e unică.
3. Căile absolute `documents.file_path`/`photos.file_path` rămâneau pe calea veche după restaurare pe alt folder/calculator ⇒ acum rebazate pe `media/` restaurat (doar dacă fișierul există).
4. `backup()` eșuat nu mai lasă copii parțiale.
Teste noi: `desktop/tests/test_etapa7_backup_restore.py` (8; SQLite real, contracte/concesionari/înhumați/plăți/avize/documente/fotografii, relații, octeți media, copie incompletă/coruptă, fără suprascriere, rollback) și `backend/tests/test_etapa7_pg_backup_restore.py` (3; `pg_dump`/`pg_restore` pe baze locale). Fereastra de integritate și cea de persoane duplicate sunt deja în bara de instrumente MAIN (🧪 / 🔁); verificate vizual (Qt offscreen) cu date fictive; am adăugat tooltip-uri pentru textele trunchiate. Nicio bază reală nu a fost restaurată/modificată.

## 4. Etapa 8 — Securitate și stabilitate (commit 323950a + completare)
Cele „7 teste eșuate":
- **Ciocnire de module (reală):** `desktop/mobile_upload.py` ⇄ `backend/mobile_upload.py`. În `Cimitir.exe --portal` / `launcher.py --portal`, portalul eșua la pornire (`sync_router` inexistent). Corectat în `desktop/start_local.py` (`_prefer_backend_modules`). Aceeași ciocnire explica cele 4 teste „instabile în paralel" (`test_local_sync_public_api`, `test_ai_extract_portable`): izolare în `backend/tests/module_isolation.py`.
- **Inventar rute (`test_etapa1_audit`):** rutele `/api/m/{token}…` sunt public-cu-token (hash, TTL, rate-limit, 404 uniform); au fost adăugate la lista documentată + test nou care verifică setul exact și respingerea tokenurilor invalide. Controalele NU au fost slăbite.
- **`backend/.env.example`:** lipsea din repo (testul de configurare Cloud) — creat, doar placeholder-e. Verificarea `backend/.env` real devine condiționată de existența fișierului local.
- **Secrete în loguri:** tokenul QR din calea `/api/m/<token>` era vizibil în jurnalul de acces uvicorn ⇒ mascat (`_TokenPathLogFilter`, test). Nu s-au găsit alte scurgeri (parole/chei) în logurile aplicației; CLI-urile afișează intenționat o singură dată codurile emise.
- `postgres_store.media_root/cache_dir` lipseau (paritate de interfață cu SQLite) — adăugate.
- Teste învechite aliniate la schema 28 (nu la cerințe mai slabe): `test_postgres.py` (versiune/migrații/tabele numai-server `cp_*`, `sync_v2_*`), `test_etapa7_media.py` (bytea doar în tabelele de staging/fundal hartă).
- Protecția la retry/duplicare: acoperită de testele existente (replay idempotent 028/sync v2, token QR cu uz limitat, numerotare documente idempotentă) — toate PASS.
**Observație (nu modificată):** există două butoane în MAIN: „💾 Backup" (vechi) și „💾 Copii de rezervă" (fereastra nouă); `shared_data-backups/*/manifest.json` (4 fișiere) sunt urmărite în Git din greșeală (artefacte de test) — lăsate neatinse, fiind date existente.

## 5. Etapa 9 — Integrare și validare (commit 8cd8a52 + completări)
Teste desktop învechite aliniate comportamentului curent: fără admin implicit (`CIMITIR_ALLOW_DEFAULT_USERS`), aprobare web pe drept `approve` (nu mai există „numai MAIN"), șablon `teren_capture.html`, migrări până la 028, cale CI relativă, import robust al helperului de test.

### Rezultate teste (mediu agent, Linux)
| Suită | PASS | FAIL | SKIP |
|---|---|---|---|
| Desktop (`desktop/tests`, `-n 0`) — înainte (baseline; nr. exact de FAIL aproximativ, din log trunchiat) | 570 | ~14 | 13 |
| **Desktop — final** | **592** | **0** | **13** |
| **Backend (fără server live; CI implicit)** | **1447** | **0** | **208** |
| **Backend cu server Cloud LOCAL pe PG local (live)** | **1559** | **5** | **85** |
| Subset mobile/PWA (rulat separat, înainte de teste noi) | 62 | 0 | 5 |
SKIP = teste care cer Supabase/Docker/pyzbar/ecran real (`SM_TEST_PG_URL`, `CIMITIR_SCREENSHOT_DIR`, pyzbar, pypdf, sau server live absent).
**Cele 5 FAIL rămase (rularea live locală) = dependente de mediu, nu defecte de cod:** `test_step6_mobile_upload.py::{02,03,04,07,15}` cer `CIMITIR_PUBLIC_URL` în procesul de test și **Supabase Storage** (`cimitir_storage.NotFound` pe stocarea locală). Nu pot fi executate fără Supabase TEST (interzis fără aprobare). `test_etapa11_e2e_live.py` (adresă fixă `127.0.0.1:8001`) nu a fost rulat.
Notă: suita backend se rulează `-n 0` (serial); în paralel, modulele PG care folosesc aceeași bază `CIMITIR_TEST_DATABASE_URL` se pot șterge reciproc schema `public`.

### Netestat / de făcut de dumneavoastră
Windows real (MAIN, EXE, PyInstaller), Docker Desktop, telefoane Android/iPhone, Supabase TEST/PROD, migrarea 028 pe Supabase (necesită aprobare), HTTPS public pentru cameră. Pași exacți: `ETAPA5_9_TESTARE_WINDOWS_TELEFON.md`.

### Comenzi de reproducere (Linux)
```
service postgresql start   # + rol/bază „cimitir_test", acces local trust
export CIMITIR_TEST_PG_ADMIN_URL=postgresql://cimitir_test@127.0.0.1:5432/postgres
export CIMITIR_TEST_DATABASE_URL=postgresql://cimitir_test@127.0.0.1:5432/cimitir_test
export PYTHONPATH=shared:desktop:backend/tests:backend QT_QPA_PLATFORM=offscreen
(cd backend && python -m pytest -n 0 -q tests)            # 1447 PASS / 0 FAIL / 208 SKIP
(cd desktop && env -u PYTHONPATH python -m pytest -n 0 -q tests)   # 592 PASS / 0 FAIL / 13 SKIP
```
