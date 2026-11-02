# CIMITIR — Raport de audit, Faza 1 (read-only)

Ținta auditului: `origin/main` @ **e2a3726** (merge PR #32 `feature/avize-complete-028`, 2 nov. 2026). Nicio modificare de cod în această fază; singurele fișiere noi sunt rapoartele din `docs/audit/`.
Mediul de verificare: Linux, Python 3.11, PostgreSQL 15 **local efemer** (port 5544, date sintetice), Chromium/Playwright headless, PySide6 offscreen. **Nu** s-a atins Supabase TEST/PROD, Docker, Cloudflare sau porturile 8080–8087. Nu s-a făcut push/merge/PR.

## 1. Pasul A este în `origin/main`?
**Da.** `e202bb9` („Implement complete avize sync and migration 028”) este strămoș al `origin/main` (merge PR #32). Cele 15 fișiere din patch sunt prezente (730 inserții, 39 ștergeri), inclusiv `028_avize_complete.sql`, rollback-ul, `entity_sync_api.py`, `mobile_app.js`, `mobile_sw.js` (`cimitir-mobile-shell-v9` confirmat), ambele teste noi și `PLAN_APLICARE_028_SUPABASE_TEST.md`.

## 2. Raport de arhitectură (ce există în cod, nu ce spune descrierea)
| Componentă | Realitate în cod |
|---|---|
| **MAIN** (`desktop/`, 168 fișiere .py, ~50k linii) | PySide6 + SQLite prin nucleul comun `shared/cimitir_database.py` (1807 linii; schema se creează/migrează în `init_db`). Sincronizare: `desktop/entity_sync.py` (legacy, `/entity-sync`) și `shared/cimitir_sync_v2.py` + `desktop/sync_v2_docs.py` (v2, cu operații idempotente `op_id`, versiuni, tombstone). |
| **Cloud** (`backend/`, 124 fișiere, ~37k linii) | FastAPI (`server.py`), PostgreSQL cu RLS pe tenant (`cimitir/pg_migrations` 001–028, rollback-uri `pg_rollbacks`), `entity_sync_api.py` (legacy), `sync_api.py`/`cimitir_sync_v2.py` (v2), `media_api.py`, `mobile_upload.py` (token QR → upload), `audit_entities.py` (legacy `audit_log`), permisiuni pe câmp (`shared/cimitir_mobile_perms.py`, migrarea 027). Același cod servește și un portal web (`store.py`/`postgres_store.py`/`mongo_store.py`). |
| **Mobile PWA** (`backend/static/mobile_*.js`, SW) | `mobile_app.js` (323 linii, UI+formulare generate din `shared/cimitir_mobile_registry.py`), `mobile_offline.js` (IndexedDB, coadă de operații), `mobile_photos.js` (coadă offline foto/PDF, idempotentă), `mobile_ext.js`, `mobile_sw.js` (cache shell `cimitir-mobile-shell-v9`). |
| **Date** | `locuri_de_veci` (cheie naturală `cod_loc`, `points` JSON), `concesionari`, `contracte` (`concesionar_id`, `id_loc` + `cod_loc` duplicat, câmpuri `owner_*` denormalizate), `inhumati` (`id_loc` obligatoriu în crearea din aplicație, `contract_id` opțional), `amenajari_avize`, `plati`, `documents` (`entity_type`+`entity_id`), `photos`, `sectoare` (legătură prin etichetă text), `loc_holders`, `cereri_concesionare`. Legăturile sunt prin id numeric local; sincronizarea folosește `uid`. |
| **Modelul financiar** | Există **doar** tabela `plati` (plăți efectuate). Nu există obligații/scadențe/sold în schemă. `total_plati` = sumă simplă. Stornările nu au câmp dedicat (suma negativă este posibilă în MAIN; Mobile cere sumă > 0). |
| **Istoric** | Legacy: `audit_log` cu `old`/`new` pe câmp (`audit_entities.py`). v2: `history` conține operațiile (acțiune, stare), nu valori vechi/noi pe câmp. |

## 3. Starea Pasului A

### A1. MAIN Desktop
- **Confirmat:** cele cinci câmpuri există în `ENTITIES` (`shared/cimitir_entities.py`), în schema SQLite, în `desktop/entity_sync.py` (export/import) și în `shared/cimitir_database.py` (`add_aviz`/`update_aviz`). Valorile goale/`NULL` sunt normalizate în sincronizare (`''` ≡ `NULL`); sincronizarea repetată nu pierde date (test existent trece).
- **Constatare (dialog, verificat offscreen cu PySide6):** `AddAvizDialog` (`desktop/panels.py`) are doar nr, dată, lucrare, executant, dată început. **Nu are câmpuri pentru `stare`, `data_finalizare`, `observatii`**; creat cu `existing={…}` toate trei dispar din rezultat. În plus, **nu există nicio cale în UI MAIN de editare a unui aviz existent** (`update_aviz` nu are apelanți în `desktop/`; parametrul `existing` nu este folosit de niciun apelant). Consecință: valorile venite din Mobile/Cloud sunt **păstrate** (nu sunt suprascrise, pentru că dialogul nu face update), dar MAIN le afișează doar read-only; utilizatorul MAIN nu poate seta/corecta starea sau observațiile unui aviz. Funcționalitate **incompletă**, nu defect de pierdere de date.
- **Neverificat:** dialogul pe Windows real (nu am Windows); există doar verificarea offscreen de mai sus. Nu există test Qt automat pentru dialog (lacună A5 confirmată).

### A2. Cloud
- **Confirmat prin teste rulate local pe PG 15:** serializare/validare a celor cinci câmpuri, API v2 pe schemă cu și fără 028 (respinge explicit valorile incompatibile — cod `schema_incompatible`, fără pierdere silențioasă), idempotență la retrimitere, persistență, conflicte (versiuni), istoric/audit pentru aviz.
- **Lacună confirmată (A5):** nu există test dedicat pentru `/entity-sync` (legacy) pe schemă fără 028 (`grep` în ambele teste noi: zero apariții). Cod citit: `entity_sync_api._avail` determină coloanele disponibile din `has_column_privilege`/`information_schema`, cu fallback `PRAGMA` pentru SQLite; scrierea trece doar coloanele prezente în payload **și** disponibile. Logic corect la citire, **netestat**.
- **Constatare (confidențialitate, nu de 028):** vezi F-02.

### A3. Mobile PWA
- **Confirmat:** formularul aviz afișează/editează cele cinci câmpuri; creare offline, coadă, retrimitere idempotentă și actualizare SW/cache `v9` sunt acoperite de `test_avize_complete_028_pwa.py` (Chromium headless, **dependență `playwright` absentă în `requirements-test.txt`**: testul se omite silențios fără ea — am instalat-o local ca să-l rulez).
- **Defect real, neacoperit de teste (F-01):** Mobile folosește `<input type="date">` (valoare **aaaa-ll-zz**), iar MAIN scrie **zz.ll.aaaa**. Verificat în Chromium: `value="05.03.2026"` → `''`; `FormData` trimite `''`. Cum `openRecordForm` face `{...cur, ...vals}`, **editarea unui aviz (sau contract/înhumat/plată) creat în MAIN poate goli datele din Cloud/MAIN**. Fixture-urile testelor folosesc exclusiv ISO, deci nu detectează.
- **Neverificat:** telefon real (iOS Safari/Android Chrome), instalare PWA, comportament real offline/reconectare.

### A4. Migrarea 028 (citită; **neexecutată pe Supabase**)
- SQL: `ALTER TABLE amenajari_avize ADD COLUMN IF NOT EXISTS` ×5 (TEXT, nulabile), `GRANT INSERT/UPDATE (coloane)` condiționat de existența rolului `cimitir_cp_app`, `REVOKE ALL … FROM anon, authenticated`. SELECT este deja la nivel de tabelă (migrarea 020), deci coloanele noi sunt citibile.
- Idempotent (testat: rulare dublă), compatibil înapoi (coloane nulabile, fără default), eșec: tranzacție, fără stare parțială (testat prin chain 028→027→022).
- Rollback: **nedistructiv** (revocă drepturile, nu șterge coloane/date) — corect pentru date. Nu restaurează `REVOKE ALL` (vezi F-07).
- Impact asupra datelor existente: zero rânduri modificate. Versiuni vechi de MAIN/Mobile: cloud-ul respinge explicit câmpurile necunoscute pe schema veche; clienții vechi care nu trimit câmpurile noi nu le suprascriu (cheie lipsă ≠ gol). Planul de aplicare (`PLAN_APLICARE_028_SUPABASE_TEST.md`) este coerent; **ordinea recomandată: backup → rulare pe TEST → verificare cu `tools/verificare_evidente.py` pe copie → abia apoi PROD.**
- Instrucțiunile de aplicare rămân de executat de către utilizator, după aprobare.

### A5. Lacune cunoscute — confirmare
| Lacună raportată | Situație |
|---|---|
| Fără test `/entity-sync` pe schemă fără 028 | **Confirmată.** |
| Fără test Qt pentru dialogul MAIN | **Confirmată** (verificat doar ad-hoc offscreen; dialogul nici nu are câmpurile — F-04). |
| Fără verificare separată a nr. de înregistrări de istoric în PG local | **Confirmată** (testele verifică existența istoricului, nu numărul pe tabel/operație). |
| Eșecuri preexistente | **Confirmate și clasificate în §6** (comparație HEAD vs `2b037ae`, înaintea 028). |

## 4. Probleme identificate (cu severitate)
| ID | Sev. | Problemă | Dovadă |
|---|---|---|---|
| F-01 | **Înaltă** | Incompatibilitate de format dată MAIN (`zz.ll.aaaa`) ↔ Mobile (`aaaa-ll-zz`): Mobile golește datele MAIN la editare; MAIN ignoră datele ISO (`contracts_window._populate` lasă data implicită = azi, la salvare se suprascrie). | Chromium: `''`; cod `contracts_window.py:293-299`; nicio conversie în `cimitir_entities`/`sync_v2`/`entity_sync`. |
| F-02 | **Înaltă** (confidențialitate) | `audit_entities._SENSITIVE` nu include `cnp`, `bi_ci`, `owner_cnp`, `owner_id_card_*`; `audit_log.detail` păstrează valorile **în clar** (old/new) pe calea legacy. | Înregistrare sintetică: `{"cnp": "18001…"}` apare integral în `detail`. |
| F-03 | **Înaltă** (rapoarte greșite) | `expiring_contracts()` folosește `date()` din SQLite pe `zz.ll.aaaa`; returnează NULL ⇒ contractele create în MAIN nu apar niciodată în raportul „expirate/expirând”. | Test sintetic: doar contractul cu dată ISO a fost returnat. |
| F-04 | Medie | MAIN nu poate edita `stare`/`data_finalizare`/`observatii` și nu poate edita deloc un aviz existent. | Dialog offscreen; zero apelanți `update_aviz`. |
| F-05 | Medie | Șase teste de migrare învechite față de 027/028 (constante `[24, 25, 26]`); patch-ul 028 a reparat corect alte două (`range(1,27)→range(1,29)`) dar nu acestea. Aserțiunea `A and B or len(...)==19` din `test_migrations_pg.py` este slabă (trece prin ramura `or`). | vezi §6 |
| F-06 | Medie | Teste cu cale absolută `/app/...` (`desktop/tests/test_build_packaging.py:29`): eșuează pe orice mașină unde depozitul nu e în `/app` (inclusiv Windows-ul utilizatorului). | `FileNotFoundError: /app/.github/workflows/build-windows.yml` |
| F-07 | Medie | 028 conține `REVOKE ALL … FROM anon, authenticated` pe `amenajari_avize`: nu este aditiv și nu este anulat de rollback. Inofensiv dacă aceste roluri nu sunt folosite; de confirmat pe TEST. | SQL citit |
| F-08 | Medie | Mobile PWA: testele browser nu sunt în `requirements-test.txt` (se omit silențios). | `importorskip` în test |
| F-09 | Scăzută | Portalul web (`server.py:1165`) creează avize fără cele cinci câmpuri (apel cu 4 argumente). Informativ. | cod |
| F-10 | Scăzută | Suitele PG se omit fără `CIMITIR_TEST_PG_ADMIN_URL` (nu sunt „verzi”); suita live Supabase dă ERROR în suita completă în loc de SKIP; 4 teste instabile în suita completă. | rulare |

Nicio problemă **critică** care să blocheze Pasul B. F-01…F-03 necesită decizii (Pașii G/10/sincronizare) — nu au fost modificate.

## 5. Funcționalități incomplete (față de descriere)
- Editare aviz în MAIN (inclusiv stare/observații/finalizare) — lipsește.
- Normalizarea formatelor de dată între MAIN și Mobile — lipsește.
- Mobile: fotografii/documente — există coadă offline (`mobile_photos.js`, implicit `locuri_de_veci`) și ștergere de documente; **de validat pe fiecare entitate** (concesionar/înhumat/contract/aviz), permisiuni pe categorii, vizualizarea documentelor asociate (Pasul E).
- Reutilizarea persoanelor: la nivel de date există (un `concesionari` ↔ mai multe `contracte`; cererile caută după CNP exact în `cimitir_requests.py:111`), dar **nu există** căutare/avertizare de duplicate în UI; înhumații nu au noțiunea de persoană reutilizabilă (Pasul D).
- Fișa unificată și soldul: nu există obligații de plată în schemă (Pasul F).
- Istoric pe câmp: există doar legacy `audit_log` (și acolo cu problema F-02); v2 nu stochează valori.

## 6. Teste disponibile, rezultate și limitări
### 6.1 Ce am rulat efectiv (Linux, PG 15 local efemer, date sintetice)
| Suită | Rezultat la `origin/main` (e2a3726) | Observații |
|---|---|---|
| Patch 028: `test_avize_complete_028.py`, `test_avize_complete_028_pwa.py` (Chromium headless, Playwright), `test_sync_v2_migration.py` | **21 passed** | Necesită `CIMITIR_TEST_PG_ADMIN_URL` și `playwright` (altfel se omit). Validează **doar** Linux/Chromium headless, nu telefon real. |
| `backend/tests` complet (serial) | **1237 passed, 10 failed, 200 skipped, 18 errors** (25 min) | vezi 6.2 |
| `desktop/tests` (PySide6 offscreen) | **541 passed, 18 failed, 2 errors** | identic cu `2b037ae` (înaintea 028): aceleași 18+2 nume |
| `tools/setup_manager/tests` | **131 passed, 7 failed** (la `2b037ae`: 129 passed, 9 failed) | vezi 6.3 |

Notă de transparență: ca să evit epuizarea porturilor efemere ale sandbox-ului (interval de 1024 porturi), am folosit o mică bibliotecă `LD_PRELOAD` care setează `SO_LINGER=0` **doar** pentru conexiunile către PostgreSQL-ul meu efemer (port 5544). Fără ea, suitele PG produceau erori de conectare false (observate în prima rulare).

### 6.2 Eșecuri backend — nou vs. preexistent (comparat cu `2b037ae`, rulate identic)
| Test | Rezultat | Cauză | Clasificare |
|---|---|---|---|
| `test_etapa1_audit::test_route_inventory_uses_only_clear_prefixes` | eșuează și înainte de 028 | ruta `/api/m/{token}/info` (Pasul 6, `mobile_upload.py`) nu este în lista de prefixe permise a testului | **test învechit**, preexistent |
| `test_etapa1_audit::test_start_local_reports_busy_port_and_dead_portal` | eșuează și înainte | dependență lipsă în mediul meu (`start_local.py` verifică pachetele) | **dependență**, preexistent |
| `test_etapa9b_cloud_config` (2 teste) | eșuează și înainte | cer `backend/.env.example`/`backend/.env` din structura mașinii autorului (fișiere neurmărite) | **mediu**, preexistent |
| `test_etapa6b_cleanup_race_pg::test_017_…` | eșuează și înainte | PG 15/curățare roluri: `role cp6b_mig_admin cannot be dropped … owner of database` | **mediu/versiune PG**, preexistent (de confirmat pe PG-ul Supabase) |
| `test_mobile_permissions_pg::test_issue_documents_…` | eșuează și înainte | lipsă `PyMuPDF` (`fitz`); **trece după instalare** | **dependență** (lipsește din `requirements-test.txt`) |
| `test_ai_extract_portable` (1), `test_local_sync_public_api` (3) | eșuează în suita completă, **trec izolat** (pe ambele versiuni) | interferență între teste/ordine | **instabilitate**, preexistentă; neelucidată |
| `test_etapa789_live_supabase_test` (18 teste) | **ERROR** în suita completă; **SKIPPED** izolat | este suita pentru Supabase TEST LIVE (cere `CIMITIR_LIVE_TESTS=1` + chei) | **NU a rulat live** (nu am atins TEST); eroarea în suita completă ar trebui să fie skip (higienă) |
Nicio dintre aceste probleme nu este introdusă de patch-ul 028.

### 6.3 Teste de migrare (`tools/setup_manager`) — cele două modificate de patch
- Înainte de 028 (la `2b037ae`) **eșuau 2 teste**: `test_fresh_empty_database_plans_all_in_order` și `test_real_migrations_load_in_order` (așteptau migrările 1–26, deși exista deja 027). Patch-ul 028 le-a **reparat corect** (lista 1–28) — **rezolvare confirmată ca fiind pe cauză, nu „ca să devină verzi”**.
- Modificarea din `test_sync_v2_migration.py` (lanț rollback 028→027→022): corectă; verifică explicit că rollback-ul 028 **nu șterge coloane** și abia apoi le elimină în test pentru compararea snapshot-ului. Limită: nu verifică restaurarea `REVOKE ALL` (F-07).
- **Rămân 6 teste incompatibile cu migrările actuale** (aceeași cauză, nerezolvate de 028): `test_detects_023_ok_and_024_to_026_missing`, `test_refusals[…]` ×2, `test_objects_present_but_unrecorded_only_records_history`, `test_db_checks_on_real_postgres`, `test_server::test_migration_flow_through_api` — au constante `[24, 25, 26]`, iar aplicația raportează corect `[24, 25, 26, 27, 28]`. Plus `test_pairing::test_probe_all_pass` (neelucidat; unele probe nu trec în sandbox).
- Aserțiune slabă: `test_migrations_pg.py` conține `assert A and B or len(_hist(url)) == 19` — prin precedența operatorilor trece prin ramura `or` chiar dacă A/B sunt false.

### 6.4 Teste care nu pot fi concludente aici
Windows real (dialoguri Qt, instalator, PyInstaller — `test_build_packaging` cere `/app/…` și PyInstaller), telefon real (iOS/Android), Supabase TEST/PROD (suita live), PostgreSQL versiunea Supabase, infrastructura 8080–8087.


## 7. Ce NU am făcut / nu pot confirma
Nu am rulat nimic pe Supabase/PROD/TEST; nu am avut Windows sau telefon real; PostgreSQL local este 15 (versiunea Supabase nu a fost verificată); portul 5544 al PG efemer a fost ales de mine într-un sandbox izolat (nu infrastructura dumneavoastră).
