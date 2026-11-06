# CIMITIR — Raport audit și stabilizare (Livrările A, B, C)

Bază: `origin/main` = `8b69ae1` (PR #44 integrat — verificat în Git, nu presupus). PR #42, #43, #44 sunt toate în `main`; nu a rămas nicio dependență de un PR neintegrat. Fiecare branch de mai jos pleacă direct din `8b69ae1` și este independent de celelalte.
Mediu: Linux, Python 3.11, PySide6 6.8 offscreen, SQLite temporar, date fictive. **Nu s-a atins nicio bază reală, PROD, Supabase, Cloud sau port rezervat (8084/8086/8087).** Nu există PostgreSQL/Cloud TEST în acest mediu.

## 1. Audit — stare per zonă
| Zonă | Stare | Dovadă |
|---|---|---|
| `field_import_journal` la restaurare | **BUG CONFIRMAT → REPARAT** | vezi §2.1 |
| Backup automat/manual, manifest, retenție, copii protejate, restaurare în temp | **IMPLEMENTAT** (reverificat) | `test_restore_offline_protection`, `test_etapa7_backup_restore`, `validation/*` |
| Backup: întrerupere/eșec copiere | **IMPLEMENTAT** | `backup()` șterge copia incompletă; copia nevalidă merge în carantină; lock cu expirare |
| Poligoane în sync | **IMPLEMENTAT în cod, PARȚIAL validat** | `points` face parte din `locuri_de_veci` în `cimitir_entities.py` (raportul precedent „nu fac parte din sync v2" era inexact). Client MAIN testat cu simulator de server; serverul real/PostgreSQL **NEVALIDAT** (fără TEST) |
| Navigare hartă → loc/concesionari/contracte/istoric | **LIPSĂ → IMPLEMENTAT** | §2.2 |
| Concesionari/contracte | **IMPLEMENTAT (PR #43) + BUG NOU CONFIRMAT → REPARAT** (contract salvat de mai multe ori) | §2.3 |
| QThread / thread_guard | **PARȚIAL în PR #44 → COMPLETAT** | §2.4 |
| Permisiuni CNP/CI (server) | **IMPLEMENTAT** (`SENSITIVE`, `hidden_for`, `_ignored` raportat în răspuns); acum cu teste locale | §2.5 |
| Documente private / cache | **GAP MIC → REPARAT** (lipsea `Cache-Control: no-store` la descărcarea media) | §2.5 |
| Scheduler Windows | **BUG CONFIRMAT → REPARAT în cod; Windows real NECONFIRMAT** | §2.6 |
| Teste QR / libzbar | **PROBLEMĂ DE MEDIU + TESTE ÎNVECHITE/NEEXECUTATE** | §2.7 |
| Cloud TEST / PostgreSQL | **INDISPONIBIL aici** (fără PG, fără acreditări TEST) | §5 |
| Flux documente (generare contract/avize, duplicate la apăsări repetate) | **PARȚIAL** — verificat doar prin suitele existente (`test_etapa1_*`, `test_pdf_reports`); nu s-a făcut audit nou | §6 |
| Audit UX general | **PARȚIAL** — doar contractele/concesionarii/hartă | §6 |

## 2. Bug-uri: cauză, reproducere, reparație

### 2.1 `field_import_journal` și restaurarea (branch `fix/restore-field-import-journal-protection`)
- **Arhitectură**: jurnalul trăiește în același SQLite ca entitățile; `_commit_local` scrie entitatea + jurnalul `LOCAL_COMMITTED` într-o singură tranzacție. Deci jurnalul din copie e coerent cu entitățile din copie. Statusul `IMPORTED` se scrie în cloud DUPĂ.
- **Bug**: la restaurarea unei copii făcute ÎNAINTE de un import, baza locală pierde entitatea și intrarea din jurnal, dar în cloud elementul rămâne `IMPORTED`. `field_import.import_item` refuză apoi reimportul (`IMPORTED_ELSEWHERE`) → pierdere silențioasă, ireversibilă fără intervenție. `restore()` verifica doar `cloud_sync_outbox`.
- **Reproducere**: testele `test_restore_of_backup_made_before_import_is_blocked_and_changes_nothing[LOCAL_COMMITTED|COMPLETE]` (înainte de reparație restaurarea reușea).
- **Soluție aleasă: blocare preventivă (nu copiere de jurnal)**. Un jurnal vechi copiat peste baza restaurată ar descrie entități inexistente. `field_imports_at_risk()` compară intrările comise (LOCAL_COMMITTED/COMPLETE) din baza curentă cu cele din copie; dacă există importuri doar în baza curentă, `restore`, `schedule_restore` și `apply_pending_restore` refuză (la pornire anulează programarea, ca la operațiile offline). Se permite: copie făcută după import; copie cu LOCAL_COMMITTED când curentul e COMPLETE (entitatea e în copie, finalizarea cloud e idempotentă); PREPARING (nu s-a comis nimic); copie istorică fără tabelul jurnal; restaurare într-un folder nou/temporar.
- **Acceptare explicită** (`allow_import_loss` / CLI `--discard-imports`, cere `--force`): restaurează, păstrează folderul `.inainte-de-restaurare-*` cu jurnalul și entitățile și scrie `field_import_restore_report.json` cu uid-urile pierdute. Baza nu poate fi citită ⇒ blocare, nu presupunere.
- Fișiere: `shared/cimitir_backup.py`, `scripts/backup_restore.py`, `desktop/tests/test_restore_field_import_journal.py` (18 teste: import înainte/după, parțial, mixt, copie istorică, offline + jurnal, programare, CLI).
- **Limită**: lipsa fișierelor media din staging (PREPARING) nu e tratată; reconcilierea automată a elementelor pierdute (re-ACCEPT în cloud) NU e implementată — necesită decizie de produs.

### 2.2 Navigare din hartă (branch `feature/harta-legaturi-loc`)
- Lipsea. Adăugat: acțiunea „Legături loc” în bara MAIN → `LinkedRecordsDialog` existent (concesionari asociați, contract nou, istoric contracte) pentru `selected_cod_loc` (din datele poligonului, nu din text) + buton „Fișa locului” care redeschide `LocuriWindow` existent. Fără ferestre copiate. Poligon fără loc salvat → mesaj, nu se creează nimic. Documentele/fotografiile locului rămân în panoul din dreapta (deja existent).
- Fișiere: `desktop/ui_mainwindow.py`, `desktop/entity_links_ui.py`, test `desktop/tests/test_map_open_loc_links.py` (3 teste, inclusiv `L-1` vs `L-10`).

### 2.3 Contract salvat de mai multe ori (branch `fix/contract-dublu-click`)
- **Reproducere** (test înainte de reparație): 3 apăsări pe „Salvează” → 3 contracte. Cauza: `AddContractDialog._on_save` fără gardă. (În UI real dialogul modal reduce riscul, dar garda lipsea.) Reparat cu o gardă `_saved_id`. Test: `test_contract_double_save.py`.
- Observație NEREPARATĂ: același `numar_contract` poate exista de mai multe ori (nu e unic); decizie de business.
- Concesionari (PR #43): reverificat (7 + 10 teste existente trec); editare persoană = același rând, adăugare din contract = un rând.

### 2.4 QThread (branch `fix/thread-guard-qthreads-restante`)
- PR #44 spunea „toate QThread-urile urmărite", dar 5 puncte rămăseseră neprotejate: `updater.py`, `ai_scanare/{scan_dialog, ai_settings_dialog, batch_dialog, scan_window}.py`. Acum învelite în `thread_guard.track`. Test global nou: orice `= QThread(` din codul aplicației fără `track` pică.
- Rulare repetată: `test_ui_resume_button_only_when_safe_and_confirmed` 15/15 fără crash (rc=0). Windows real: NEVERIFICAT. Nu am auditat exhaustiv închiderea ferestrelor în timpul cererilor HTTP active (HTTPX/AnyIO/TestClient + PySide6): rămâne risc.

### 2.5 Permisiuni și documente (branch `audit/permisiuni-pii-teste-locale`, `fix/media-download-no-store`)
- Server impune: `SENSITIVE` (cnp/bi_ci; owner_cnp/owner_id_card_serie_number; inhumati.cnp), coloane poligon ascunse fără „Vede hartă”, scrierea valorilor ignorate este raportată (`_ignored`), revocarea se aplică per cerere (matricea se recitește). Teste locale noi (8, funcții pure) + test-gardă: orice coloană „de identitate” din entitățile sincronizate trebuie declarată sensibilă.
- Acte de identitate (`carte_identitate`, `cerere_doc`) nu se urcă niciodată în cloud (`media_sync`), cu purjare din coadă.
- `GET /api/sync/media/{id}` cerea autentificare dar nu trimitea `Cache-Control` → adăugat `no-store` + `nosniff` (2 teste).
- **Nevalidat**: comportamentul pe PostgreSQL real (RLS, grant-uri), Mobile/PWA real, alocarea fotografiilor la înregistrarea corectă pe Cloud.

### 2.6 Planificator Windows (branch `fix/windows-scheduler-data-dir`)
- Bug: comanda `schtasks /Create` din MAIN nu includea `--data-dir`; activitatea programată nu moștenește `CIMITIR_DATA_DIR` ⇒ salva alt folder decât cel din MAIN. Acum se adaugă, citat conform CommandLineToArgvW (backslash final dublat). 6 teste emulează regulile de parsare pentru căi cu spații, `D:\`, UNC, `...\`, și verifică limita de 261 caractere pentru `/TR`.
- `Register-CimitirBackupTask.ps1` (PowerShell) **nu a putut fi rulat DryRun**: nu există PowerShell în mediu. **NECONFIRMAT pe Windows real**: ghilimele PS, „utilizator neautentificat” (S4U/DPAPI/volume de rețea — avertisment existent în script), intervale de repetare.

### 2.7 QR (branch `fix/qr-teste-dependente-mediu`)
- `pyzbar` e folosit DOAR în teste; generarea QR nu depinde de libzbar (nu s-a eliminat nicio funcționalitate). Cauze găsite: (a) lipsa `libzbar` (mediu) — cu `pyzbar` instalat dădea `ImportError` la colectare, nu skip; (b) cinci fișiere de test sunt scripturi (`run()`), neîncărcate de pytest („no tests ran”) ⇒ nu rulau niciodată; (c) odată rulate, `test_qr_scanning` și `test_qr_labels` aveau așteptări învechite (`/loc/` vs `/public/loc/` emis de cod și servit de `backend/server.py`).
- Reparat: așteptări, skip explicit cu motiv, `desktop/tests/test_script_suites.py` (rulează cele 5 scripturi în subproces), `scripts/check_qr_env.py`, `docs/QR_DEPENDENTE.md`, `desktop/requirements-test.txt`. Instalare Windows documentată; Windows real NECONFIRMAT.

### 2.8 Teste client poligoane (branch `audit/sync-poligoane-teste-client`)
8 teste cu **simulator** de server (NU backend real): creare, editare (același `id`/`uid`, o singură operațiune în coadă), retrimitere cu același `op_id` fără dublare/creștere de versiune, al doilea client primește aceeași geometrie fără ecou, conflict concurent (local nesuprascris, nici după pull), poligoane istorice (id/points neschimbate, uid atribuit), pull nu suprascrie geometria locală în așteptare, ștergere.

## 3. Teste (exact, pe integrare locală a tuturor branch-urilor)
| Suită | PASS | FAIL | SKIP | XFAIL |
|---|---|---|---|---|
| `desktop/tests` (fiecare fișier rulat separat) | 716 | 0 | 11 | 0 |
| `validation/final_integration` + `validation/bug_audit` | 72 | 0 | 19 | 1 |
| `backend/tests` (rulat înainte de ultimele 2 teste media) | 960 | 0 | 671 | 0 |
Note: 671 SKIP backend = PostgreSQL/Cloud TEST indisponibile (NU sunt PASS). În `desktop/tests`, 5 fișiere „no tests ran” sunt scripturi acum acoperite de `test_script_suites.py` (5 teste incluse în cei 716). Dependențe instalate doar în mediul de test: PySide6, pytest-qt, PyInstaller, pyzbar+libzbar0, openpyxl, psycopg-pool etc. Fără ele, eșecurile inițiale (`test_build_packaging`, `test_startup_unification`, `test_local_first_launcher`) sunt de mediu, nu de cod.

## 4. Fișiere modificate (cod, nu teste)
`shared/cimitir_backup.py`, `scripts/backup_restore.py`, `desktop/ui_mainwindow.py`, `desktop/entity_links_ui.py`, `desktop/contracts_window.py`, `desktop/backup_window.py`, `desktop/updater.py`, `desktop/ai_scanare/{scan_dialog,ai_settings_dialog,batch_dialog,scan_window}.py`, `desktop/qr_labels.py` (docstring), `backend/media_api.py`; noi: `scripts/check_qr_env.py`, `docs/QR_DEPENDENTE.md`, `desktop/requirements-test.txt`.

## 5. De validat în Cloud TEST (nu s-a executat; fără acreditări, fără PG local)
1. Poligoane: push/pull al `points` (JSONB) între două MAIN-uri și Mobile; editare concurentă ⇒ `conflict` cu `server` fără geometrie suprascrisă; retrimitere `op_id`; poligoane istorice cu `points` gol; verificare în baza TEST a rândului vs. operația trimisă (nu doar HTTP 200).
2. Matricea Mobile: revocarea `harta.view`/`view_pii` se reflectă la următoarea cerere; `_ignored` apare în răspunsul push.
3. Media: `no-store` pe `/api/sync/media/{id}`; fotografiile se leagă de înregistrarea corectă.
4. Instanța TEST trebuie confirmată izolată; nu 8084, iar 8086/8087 doar cu aprobare.
**Migrații necesare: NICIUNA** (nicio schemă SQLite/PostgreSQL modificată).

## 6. Neexecutat / riscuri rămase
- Audit complet al sincronizării pentru plăți, documente, fotografii, avize, înhumați pe Cloud TEST: NEEXECUTAT.
- Audit UX complet, flux documente (generare contract/avize pe date reale, evitare duplicate la apăsări repetate la alte dialoguri): NEEXECUTAT în afara contractelor.
- Windows (crash nativ Qt, Task Scheduler, S4U), telefon real: NEVERIFICAT.
- Reconciliere automată a importurilor pierdute după restaurare forțată: nu e implementată.
- Duplicatele istorice de concesionari rămân (nu se șterg automat).
- Conflicte de conținut a testelor `desktop/tests/conftest.py` vs `validation/` când rulează în aceeași sesiune pytest (se rulează separat).

## 7. Branch-uri / PR-uri
Vezi mesajul final (linkurile depind de push). Ordine de integrare recomandată, toate independente: restore-journal, scheduler, contract, thread-guard, media, QR, permisiuni, poligoane, hartă.

## 8. Verdict
**READY TO MERGE — YES pentru fiecare PR individual** (modificări mici, testate, fără migrații, fără atingerea PROD), **NU pentru declararea sistemului „gata de producție”**: sincronizarea reală pe Cloud TEST, Windows real și telefon real nu au fost validate.
