# CIMITIR MAIN — Audit, reparare buguri și integrare (branch `fix/concesionar-duplicat-integrare-audit`)

Pornit din `origin/main` (eecad94, include deja PR #42 `feature/main-desktop-integrity-backup`). Nu s-a atins `main`, nicio bază reală, Supabase PROD sau Cloud.
Toate testele noi rulează pe SQLite temporar, date fictive, în subprocese izolate (unele cu rețeaua blocată explicit).

## 1. Cauze și remedieri

### BUG prioritar — concesionar duplicat (REPRODUS înainte de reparare)
- **Cauza reală:** două înregistrări reale în SQLite, nu doar o problemă de afișare. `AddConcesionarDialog._save()` salvează deja în DB (INSERT #1);
  `AddContractDialog._open_add_concesionar()` apoi apela încă o dată `save_concesionar(dlg.get_data())` (fără `id`) → INSERT #2; combo-ul selecta ID-ul #2,
  iar fișierul scanat AI se atașa la ID-ul #1. Nu era implicată sincronizarea, semnale multiple sau reîncărcarea listei (combo-ul se golește cu `clear()`).
  Același tipar redundant exista în `ConcesionariWindow.add_dialog/edit_dialog`.
- **Remediere:** dialogul este singurul care salvează; contractul reutilizează `get_saved_id()` și selectează acel ID. Protecții în dialog: gardă
  anti-dublu-click (`_saving`), după prima salvare dialogul trece pe UPDATE (`_id`), nu se acceptă dialogul dacă salvarea a eșuat.
  La persoană nouă se caută candidați existenți (CNP identic sau același nume+prenume) și utilizatorul alege: *folosește existent* (ID reutilizat, nu se
  creează nimic) / *persoană nouă* / *renunță*. Nu se unifică automat nimic. Duplicatele istorice NU se șterg: sunt raportate de fereastra existentă
  „Persoane duplicate” (CON005) — test dedicat.
- **Test de regresie:** `desktop/tests/test_concesionar_duplicat.py` (înainte de reparare: 5 din 6 teste picau cu `[1,'Ionescu'],[2,'Ionescu']`).

### Interconectare Locuri ↔ Concesionari ↔ Contracte
Schema reală: asocierea există DOAR prin `contracte` (`concesionar_id`, `id_loc`/`cod_loc`); un loc poate avea mai mulți concesionari și mai multe contracte
(istoric), un concesionar mai multe locuri. `loc_holders` este doar derivat (hash-uri pentru portalul public). Nu s-a adăugat nicio tabelă/coloană.
- `desktop/entity_links.py` (doar citire, id-uri reale) și `desktop/entity_links_ui.py` (`LinkedRecordsDialog`, `PickDialog`, helperi) — reutilizează `AddConcesionarDialog`,
  `AddContractDialog`, `ContractsWindow`, `LocuriWindow`, `ConcesionariWindow`.
- **Locuri:** butoane *Concesionari* (asociați; selectează existent; adaugă nou; deschide concesionar), *Contract nou* (loc precompletat), *Contracte / istoric*.
- **Concesionari:** *Locuri de veci* (asociate; alege loc existent; *Deschide loc* în `LocuriWindow` cu rândul selectat), *Contract nou* (persoană precompletată), *Contracte / istoric*.
  Coloana „Cod loc” și filtrele topologice funcționează acum (înainte `load_concesionari` nu returna deloc loc).
- **Contract:** `selected_cod_loc` precompleta doar combo-ul, nu și sector/parcelă/rând (semnalul se conecta după setare) — corectat; `selected_concesionar_id`; butoane *Date loc* / *Date concesionar*;
  confirmare dacă se introduce un cod de loc inexistent (nu se creează loc/persoană fictivă). Un contract nou nu modifică contractele vechi (test pe snapshot-ul tabelei).
- `ConcesionariWindow`/`ContractsWindow` primesc `on_data_changed` (reîncărcare fără dublare; până acum MainWindow verifica `hasattr` și nu se conecta niciodată).

### BUG crash Python în testele de import
- **Reprodus pe Linux** (nu doar pe Windows): `test_ui_resume_button_only_when_safe_and_confirmed` → `QThread: Destroyed while thread is still running` / `Fatal Python error: Aborted`
  în ~8% din rulări (2/25); un test dedicat reproduce deterministic abort-ul.
- **Cauza:** `field_worker.run_task` pornește un `QThread` și îl ține doar într-un set global. Testul revine din `pump()` imediat ce butonul e activ, deci
  `cloud_snapshot` (HTTP prin Starlette TestClient/AnyIO, în thread) încă rulează; la ieșirea procesului/teardown obiectul C++ este distrus cât thread-ul rulează → Qt face `abort()`.
  Este **bug de producție** (închiderea MAIN cu o cerere Cloud în curs poate cauza același abort) amplificat de un test care nu așteaptă thread-urile.
  Pe Windows excepția „access violation” apare în același mecanism (thread-ul HTTP încă în execuție); **nu am putut rula pe Windows** — verificarea finală pe Windows rămâne NEEXECUTATĂ.
- **Remediere:** `field_worker.wait_for_tasks()` (înregistrat la `atexit` și `aboutToQuit`) + fixture autouse în `desktop/tests/conftest.py` care așteaptă thread-urile înainte de restaurarea mediului
  (și elimină `OperationalError` din thread după ce `DB_PATH` era restaurat). Nu s-a dezactivat nimic. După: 0/40 abort-uri; test izolat `test_field_worker_shutdown.py`.

### Backup / restaurare (audit)
Funcționează conform scenariilor; testele noi (29) acoperă: blocarea restaurării pentru operațiuni `pending/retry/conflict/error` (nimic nu se modifică pe disc), `--force` singur NU permite pierderea
(`--discard-unsent` cere `--force` și păstrează copia de siguranță cu operațiunile), schemă veche a outbox-ului (fără `state`), DB curentă ilizibilă (refuz), `schedule_restore`, restaurare în director temporar nou,
manifest cu path traversal, destinație în Git (refuz/relocare cu avertisment), destinație indisponibilă (eșec jurnalizat, fără excepție), migrare explicită (originalele rămân identice, idempotentă, `--adopt-missing` marcat), dry-run implicit.
- **Bug găsit și reparat:** `apply_pending_restore` lăsa marker-ul activ când restaurarea era blocată de operațiuni nesincronizate apărute după programare → după o sincronizare, restaurarea
  s-ar fi aplicat „prin surprindere” la o pornire ulterioară. Acum programarea este anulată (marker redenumit `.anulat-<data>`), cu mesaj clar; nu se modifică date.
- Windows (`Register-CimitirBackupTask.ps1`): analiză statică — implicit fără Principal (rulează doar cu utilizatorul conectat); `-RunWhetherLoggedOn` (S4U) cere `-DataDir` și avertizează despre DPAPI/rețea/EFS;
  `TrimEnd` + `Quote-WinArg`. **Înregistrarea reală în Task Scheduler și rularea fără sesiune interactivă NU au putut fi executate (Linux).**

### Media sync și documente sensibile (`desktop/media_sync.py`)
- Ștergerea din `cloud_sync_outbox` vizează doar `kind='media' AND action='upload' AND entity='documents'` pentru `carte_identitate`/`cerere_doc`; documentele și fișierele locale nu se ating; operațiunile `entity` (Mobile v2/Sync v2) și documentele permise rămân în orice stare (test).
- **Lacune închise:** (a) purjarea nu lăsa nicio urmă → acum o linie în `audit_log` (`media_outbox_purge`, doar op_id/stare/încercări/eroare, fără date personale); (b) trigger-ele filtrează doar la INSERT: un document
  re-tipat în `carte_identitate` după punerea în coadă ar fi fost URCAT în Cloud → gardă la momentul trimiterii în `_upload` (operația este abandonată + auditată, documentul local rămâne). Test a căzut fără această gardă.
- Stări: offline → rămâne `pending`; respins → `error` (nu se retrimite automat); `conflict` nu se retrimite; 410 → operație abandonată; fișier lipsă → `error` fără ștergerea rândului.

## 2. Fișiere modificate
Cod: `desktop/concesionari_window.py`, `desktop/contracts_window.py`, `desktop/locuri_window.py`, `desktop/field_worker.py`, `desktop/media_sync.py`, `shared/cimitir_backup.py`, `desktop/cimitir.spec` (2 module noi).
Noi: `desktop/entity_links.py`, `desktop/entity_links_ui.py`.
Teste noi: `desktop/tests/test_concesionar_duplicat.py`, `test_legaturi_loc_concesionar_contract.py`, `test_field_worker_shutdown.py`, `test_media_sync_outbox.py`, `test_restore_offline_protection.py`, `test_no_prod_access.py`; modificat `desktop/tests/conftest.py`.
Teste actualizate (stale după PR #42): `validation/final_integration/test_bug09_definitive.py`, `test_backup_cli_and_windows_prep.py` (3 `xfail(strict)` deveniseră XPASS = bug-uri deja remediate; 2 teste de „caracterizare” descriau comportamentul vechi al .ps1). Asertările noi sunt mai stricte, nu relaxate.

## 3. Schimbări de schemă
Niciuna. Fără coloane/tabele noi; fără modificări în Sync v2 / Mobile v2 (`shared/cimitir_sync_v2*.py`, `desktop/sync_v2.py` neatinse). Singura scriere nouă: rânduri în `audit_log` existent.

## 4. Rezultate teste (Linux, Python 3.11, PySide6 6.8, offscreen)
| Suită | PASS | FAIL | SKIP | Neexecutat |
|---|---|---|---|---|
| `desktop/tests` (toate fișierele, câte un proces) | 670 | 0 | 13 | — |
| `validation/final_integration` + `validation/bug_audit` | 72 | 0 | 19 | 1 xfail (BUG-09 istoric Git, documentat) |
| `backend/tests` (cod neatins; rulat pentru regresii) | 960 | 0 | 671 (PostgreSQL local indisponibil) | — |

- Cele 28 de teste confirmate local de dvs. (backup automat 13, backup/restaurare 8, UI MAIN 1, pregătire Windows 3, integritate 3) rulează și trec și aici.
- Skip-uri: PostgreSQL real (`SM_TEST_PG_URL`), `pyzbar`, screenshot-uri — intenționat, fără acces la Cloud.
- 5 fișiere din `desktop/tests` (`test_license`, `test_pdf_reports`, `test_qr_labels`, `test_qr_public_data`, `test_qr_scanning`) nu colectează teste pytest (rc=5) — situație preexistentă, neschimbată.
- **NEEXECUTATE:** Windows real (Task Scheduler, S4U/fără sesiune interactivă, access violation nativ), telefon real/Mobile v2 live, Supabase TEST/PROD (blocat), GUI interactiv manual (verificat doar prin dialoguri offscreen + capturi).
- Dependențe de mediu lipsă inițial (nu bug-uri de cod): `psycopg`, `psycopg-pool`, `PyMuPDF`, `PyInstaller`; `test_field_import_history.py` se rulează din `desktop/` (importă `database` direct).
- Verificare PROD: `test_no_prod_access.py` — pytest se oprește cu cod 4 dacă `DATABASE_URL`/`SUPABASE_URL` indică proiectul de producție, chiar și cu `CIMITIR_LIVE_TESTS=1`; testele media rulează cu `socket.connect` blocat.

## 5. Riscuri rămase
1. Alți `QThread` (`IntegrityWorker`, `DuplicateScanWorker`, `cloud_sync_auto._Worker/_EntityWorker`) au aceeași clasă de risc dacă fereastra/procesul se închide în timpul rulării; nu au fost modificați (în afara cauzei raportate).
2. Protecția la restaurare acoperă `cloud_sync_outbox`; jurnalul de import din teren (`field_import_journal`, stări `PREPARING/LOCAL_COMMITTED`) nu e inclus — la restaurare elementele rămân `ACCEPTED` în cloud și pot fi reimportate, dar finalizările locale nefăcute se pierd din jurnal.
3. `test_client_guide.py` regenerează în loc fișierul versionat `desktop/docs/Ghid_Instalare_Cimitir.pdf` (modifică working tree la fiecare rulare) — nemodificat aici.
4. Dialogul de persoană nouă folosește `QInputDialog` (simplu); dacă preferați o fereastră dedicată de reconciliere, se poate extinde `DuplicatePeopleWindow`.
5. Duplicatele istorice create de bug rămân în baze; se identifică din „Persoane duplicate” și se rezolvă manual (fără script de unificare, intenționat).
6. Comportamentul pe Windows al crash-ului și al Task Scheduler trebuie confirmat pe un PC Windows înainte de merge.

## 6. Livrare
Commituri separate pe branch; fără merge. Push/PR: nu am acces de scriere la `paulwalker0727/v3` din acest mediu (clonare publică, doar citire) — branch-ul este pregătit local; push-ul și Pull Request-ul către `main` trebuie făcute de dvs.
