# CIMITIR v3 — Audit tehnic al bugurilor (Etapa 10)

Branch auditat: `feature/etapa10-stabilizare` @ `58acdef`. Data: noiembrie 2026 (ceasul containerului).
Mediu: Linux, Python 3.11, PostgreSQL 15 **local și temporar** (port 55432, creat în `/root/pgtest`), SQLite temporar, stocare locală.
**Nu s-a accesat Supabase TEST/PROD, Docker-ul existent nu a fost atins, codul aplicației și testele existente NU au fost modificate.**
Fișierele noi sunt doar în `validation/bug_audit/`.

## Cuprins
1. Rezumat
2. Buguri CONFIRMATE (reproduse local)
3. SUSPICIUNI (cod discutabil, nereprodus / decizie de politică)
4. NECESITĂ WINDOWS SAU TELEFON REAL (pași manuali)
5. Rezultatele suitelor existente
6. PLAN DE REMEDIERE (ordonat după gravitate) — așteaptă aprobarea ta
7. Cum se rulează testele de reproducere

---

## 1. Rezumat

| # | Gravitate | Zonă | Problema (pe scurt) | Test |
|---|---|---|---|---|
| BUG-04 | **Critică** | Restaurare + sync clasic | După restaurarea unei copii, prima editare de pe MAIN **suprascrie în tăcere** modificările altui calculator făcute între copie și restaurare (fără conflict) | `test_bug04_…` |
| BUG-08 | **Critică** | Mobile PWA | O eroare trecătoare IndexedDB (cunoscută pe iPhone) face ca următoarea salvare să **șteargă coada offline** | `test_bug08_…` |
| BUG-01 | **Critică** | Documente (A1/O-1) | Conducta clasică `media_sync` urcă actele de identitate (`carte_identitate`, `cerere_doc`); serverul clasic le acceptă | `test_bug01_…` |
| BUG-07 | **Mare** | Restaurare + documente | Un document șters din Cloud între copie și restaurare este **reîncărcat** și reapare peste tot | `test_bug07_…` |
| BUG-02 | **Mare** | Restaurare | Restaurarea (mod clasic) scoate din folderul de date cheia Cloud, licența, jurnalele; în modul „bază veche” cu catalog scoate **toate registrele** | `test_bug02_…` |
| BUG-05b | **Mare** | Retenție backup | Dacă copiile recente sunt deteriorate pe disc, retenția **șterge copiile vechi bune** (rămâne 1 copie validă) | `test_bug05_…::05b` |
| BUG-09 | **Mare** | Git / copii (A2) | `git pull`/checkout peste `6a9774b` **șterge de pe disc** cele 4 `manifest.json`; copiile dispar din MAIN | `test_bug09_…` |
| BUG-05a | Medie | Backup programat | Lock rămas → `busy`: fără copie, fără jurnal, **cod 0** pentru Task Scheduler, MAIN fără avertizare | `test_bug05_…::05a` |
| BUG-05c | Medie | Carantină | Dacă redenumirea `*.invalid` eșuează, copia stricată rămâne listată ca normală (mesajul spune „pusă în carantină”) | `test_bug05_…::05c` |
| BUG-03 | Medie | Restaurare | O copie aflată în folderul de date nu poate fi restaurată niciodată | `test_bug03_…` |
| BUG-06 | Medie | UI backup | „Ora de pornire (Planificator)” se salvează, dar nu o folosește nimic | `test_bug06_…` |
| T-1…T-4 | Mică | Teste existente | Teste învechite (migrări 027/028, `mobile_ext.js`, 7 file în Centrul Cloud) și detector PG doar TCP | §5 |

Zone verificate **fără defect găsit** (cod citit + test sau suită existentă): idempotența `op_id` la reluare / ack pierdut (classic + Sync v2), rândul șters în Cloud nu reapare după restaurare (test `test_check_restore_does_not_resurrect_row_deleted_elsewhere` = PASS), revocarea telefonului pe toate rutele Sync v2, redactarea CNP/CI la pull / rând / conflicte / răspunsuri push / căutare / jurnal, comportamentul fără 028 (refuz explicit, valorile rămân pe dispozitiv), mascarea tokenului QR (`/api/m/[TOKEN]` în jurnalul de acces; codul de asociere PWA este în `#fragment`, nu ajunge la server), ferestrele noi MAIN (integritate / duplicate sunt doar citire).

---

## 2. Buguri CONFIRMATE

### BUG-04 — Restaurare + sincronizare clasică: suprascriere silențioasă a modificărilor altui calculator — **Critică**
- **Fișier și cauză exactă**
  - `backend/entity_sync_api.py:132-141` `_last_writer_is` și utilizarea la `:158` (ștergere) și `:196` (actualizare): un `base_version` depășit **nu** e conflict dacă versiunea curentă a rândului pe server a fost produsă de același `client_id`.
  - `client_id` este în tabela `settings` a bazei SQLite (`desktop/cloud_sync.py:220-226`), deci copia restaurată păstrează **același** id, dar rândurile revin la versiunile vechi.
  - `cloud_sync.sync_all` / `entity_sync.sync_entities` trimite outbox-ul **înainte** de pull (`desktop/entity_sync.py:495-500`). Rândul editat după restaurare pleacă cu `base_version` vechi și este acceptat, cu **toate coloanele** din copie.
- **Reproducere:** `validation/bug_audit/test_bug04_restore_sync_silent_overwrite.py::test_restored_main_does_not_silently_overwrite_other_client_changes`. Server clasic real (uvicorn) pe PostgreSQL temporar, 2 MAIN izolate.
  Pași: A creează rândul (v1) → copie → B schimbă telefonul (v2) → A aduce v2 și schimbă adresa (v3) → A restaurează → A schimbă prenumele → sync.
  **Obținut:** `server={'prenume': 'Ion-Corectat', 'telefon': '0700-ORIGINAL', 'adresa': 'Str. Veche 1', 'version': 4}`, `conflicte=[]`, „confirmate 1, conflicte 0”. Telefonul lui B și adresa au fost pierdute fără urmă.
- **Remediere minimă propusă:** la aplicarea restaurării (`shared/cimitir_backup.py::restore`, după copierea bazei) se golește `cloud_client_id` în baza restaurată (+ o cheie `restored_at`). MAIN devine „client nou”, iar serverul raportează conflictul obișnuit, rezolvabil din „Conflicte”.
  Testul `test_fix_simulation_new_client_id_after_restore_reports_conflict` simulează exact această schimbare și **trece** (conflict, telefonul lui B păstrat). Aproximativ 5 linii, fără modificări pe server.
- **Risc date:** pierdere silențioasă în Cloud a modificărilor altor calculatoare sau ale portalului. După fix: conflicte vizibile, fără pierdere.
- **Compatibilitate:** serverul vede un `client_id` nou. Vechiul rămâne în `sync_clients` și poate declanșa alerta „client offline” (`sync_api.check_offline_alerts`). Trebuie ignorat sau șters manual. Copiile existente nu se schimbă. Migrarea 028 nu este implicată.
  Aceeași cauză apare dacă aceeași copie e restaurată pe **două** calculatoare care sincronizează simultan. Vezi și S-5.

### BUG-08 — PWA: coada offline ștearsă după o eroare trecătoare IndexedDB — **Critică** (logică reprodusă; frecvența se confirmă pe iPhone)
- **Fișier și cauză:** `backend/static/mobile_app.js:127` `outbox()` face `kvGet('outbox').catch(()=>null)` și întoarce `[]` la orice eroare. Apelanții scriu apoi lista „goală” peste coadă:
  - `:240` la salvarea unei fișe: `ob.push(op); saveOutbox(ob)`;
  - `:141` + `saveOutbox(ob)` în `syncNow` (după push, lista recitită goală → `saveOutbox([])`);
  - `backend/static/mobile_ext.js:31` (salvarea poligonului), același tipar.
- **Reproducere:** `test_bug08_pwa_outbox_wiped_on_read_error.py` rulează în Node **exact liniile din fișier**, extrase cu regex, cu un `kvGet` care eșuează o singură dată.
  **Obținut:** coada `[m-1, m-2, m-3]` devine `['m-new']` la salvarea unei fișe și `[]` după sincronizare.
- **Remediere minimă:**
  - `outbox()` nu mai înghite eroarea, ci o propagă. Pe ramura din `:240`, eroarea ajunge în blocul `try` existent: se afișează un toast „Nu s-a putut salva, reîncearcă”, iar ciorna rămâne, pentru că `kvDel(draftKey)` vine după.
  - În `syncNow` eroarea intră în `catch`-ul existent („Sincronizare incompletă”).
  - Se aplică același lucru în `mobile_ext.js:31`.
  - Se incrementează versiunea cache-ului din `mobile_sw.js`, ca telefoanele să primească fișierele noi.
- **Risc date:** pierderea modificărilor făcute offline și netrimise (avize, plăți, fișe). Fotografiile nu sunt afectate, pentru că se păstrează pe chei separate în `mobile_photos.js`.
- **Compatibilitate:** niciun risc pentru server. Coada existentă pe telefoane rămâne în același format.

### BUG-01 (A1/O-1) — Acte de identitate în conducta clasică `media_sync` — **Critică** (expunere)
- **Fișier și cauză:**
  - `desktop/media_sync.py:62-73` `_create_triggers`: triggerul `AFTER INSERT` pe `documents` exclude doar `entity_type='cerere'`, nu și `doc_type IN ('carte_identitate','cerere_doc')`. Același lucru apare la `enqueue_existing` (`:126`).
  - Serverul clasic `backend/media_api.py:83-129` `complete` acceptă orice `doc_type`, iar pull-ul (`media_sync._apply_object`) le descarcă pe celelalte MAIN.
  - Sync v2 le exclude: `desktop/sync_v2_docs.py:111-114` și `shared/cimitir_sync_v2_docs.py:23,76-77`.
- **Reproducere:** `test_bug01_media_sync_identity_docs.py`. **Obținut:** `['carte_identitate', 'cerere_doc', 'contract_scan']` în coada de upload.
- **Remediere minimă (aliniere cu Sync v2; decizia îți aparține):**
  1. condiția `AND COALESCE(NEW.doc_type,'') NOT IN ('carte_identitate','cerere_doc')` în trigger și în `enqueue_existing`;
  2. în `_upload`, operațiunile deja aflate în coadă pentru aceste tipuri se elimină (`dropped`), cu notă;
  3. pe server, `complete` răspunde 422 pentru aceste tipuri (apărare în profunzime);
  4. test de regresie din acest audit.
  Documentele deja urcate se tratează separat, prin decizie, nu automat.
- **Risc date:** copii ale actelor de identitate ajung în storage-ul serverului clasic și pe alte calculatoare.
- **Compatibilitate:** pe un MAIN vechi, serverul nou refuză (422) → operațiunea ajunge în `error` după 4 încercări (vizibilă) și nu se pierde nimic local. Triggerul se recreează la `ensure_tracking(force=True)`.

### BUG-07 (A6) — Documentul șters în Cloud reapare după restaurare — **Mare**
- **Cauză:**
  - copia conține operațiunea `upload` aflată în așteptare (`desktop/media_sync.py:67-73`);
  - după restaurare, `_upload` (`:199-243`) o retrimite cu același `media_id`;
  - `backend/media_api.py:90-96` verifică doar `media.get_object(media_id)`, nu și tombstone-ul `sync_tombstones` (`entity='media_objects'`, `backend/cimitir/media_service.py:157`), așa că obiectul e **recreat**.
  - Sync v2 nu are problema: `shared/cimitir_sync_v2_docs.py` refuză cu „documentul a fost șters” (status `DELETED`).
- **Reproducere:** `test_bug07_restore_resurrects_deleted_document.py`. **Obținut:** `media_objects` conține din nou documentul. Sync-ul raportează „1 încărcate … 1 noi”.
- **Remediere minimă:** în `complete`, dacă există tombstone pentru `media_id` → HTTP 410 `{"error":"deleted"}`. În `media_sync._upload`, 410 → se șterge operațiunea (`dropped`). Pull-ul existent elimină apoi rândul local. Aproximativ 10 linii.
- **Risc date:** reapariția documentelor șterse intenționat, inclusiv acte eliminate la cerere.
- **Compatibilitate:** un client vechi primește 410 → `Rejected` → operațiune `error` (vizibilă), fără buclă.

### BUG-02 — Restaurarea mută în „.inainte-de-restaurare” secretele, licența, jurnalele și (mod vechi cu catalog) registrele — **Mare**
- **Cauză:**
  - `shared/cimitir_backup.py:147-161` `restore` mută **întregul** `data_dir` și readuce doar `.jwt_secret` și `registry.json`.
  - În modul clasic `working_dir() == data_dir()` (`shared/cimitir_registry.py:517-525`). Același folder conține:
    - `secrets/`: cheia de sync și profilele Cloud, prin DPAPI pe Windows (`shared/cimitir_secretstore.py:191-192`);
    - `license.dat`: pentru `.exe`, `%LOCALAPPDATA%\Cimitir` = data_dir (`desktop/security/settings_store.py:22-32`);
    - `logs/`;
    - cu catalog și `legacy_mode=1`: `registry_catalog.sqlite` + `registries/`.
  - Copia conține doar baza + media, deci aceste fișiere nu pot reveni din ea.
- **Reproducere:** `test_bug02_restore_moves_secrets_license.py`, 3 teste.
  - Obținut: lipsesc `secrets/legacy_sync_key.bin`, `license.dat`, `logs/cimitir.log`;
  - în modul cu catalog lipsesc `registry_catalog.sqlite` și `registries/alt-registru/database.sqlite`;
  - testul cap-coadă cu codul MAIN real: după restaurare `cloud_sync.get_config()['key']` este gol, deci sincronizarea se oprește.
- **Remediere minimă:** lista `keep` din `restore` devine `(".jwt_secret", "registry.json", "license.dat", "secrets", "logs", CATALOG_NAME, "registries")`. Fișierele se copiază cu `copy2`, iar folderele cu `copytree`, din folderul de siguranță. Folderul de siguranță rămâne intact.
- **Risc date:** datele nu se pierd (sunt în folderul de siguranță), dar MAIN rămâne fără Cloud și fără licență, iar registrele „dispar”. Un utilizator care se asociază din nou creează un dispozitiv nou.
- **Compatibilitate:** fără schimbare de format. Copiile vechi se restaurează la fel. Pe Windows cheia DPAPI rămâne validă (același utilizator).

### BUG-05b — Retenția șterge copiile vechi bune când cele recente sunt deteriorate — **Mare**
- **Cauză:** `shared/cimitir_backup.py:233` verifică cele mai noi `keep_min` copii doar cu `_quick_ok` (`:217-223`: fișierele există, fără SHA și fără `integrity_check`).
- **Reproducere:** 2 copii vechi bune (40 și 30 de zile), 2 copii recente deteriorate pe disc după verificare (20 și 10 zile) și o copie nouă. `prune(keep_days=14, keep_min=3)` → **rămâne o singură copie validă**.
- **Remediere minimă:** retenția păstrează copii până când există `keep_min` copii **valide** după `verify()` și șterge doar ce e mai vechi decât acestea. Costă câteva calcule SHA o dată pe zi.
- **Risc:** pierderea singurelor copii bune, de exemplu pe un disc extern defect.
- **Compatibilitate:** niciuna. Doar retenția devine mai conservatoare.

### BUG-09 (A2) — Actualizarea depozitului șterge manifestele copiilor — **Mare** (operațional)
- **Cauză:** commit-ul `6a9774b` a scos din index cele 4 `shared_data-backups/*/manifest.json`. Orice clonă care le are urmărite le pierde de pe disc la checkout sau pull. Fără manifest, `list_backups` (`shared/cimitir_backup.py:198`) nu le mai arată, iar `verify`/`restore` le refuză.
- **Reproducere:** `test_bug09_…`, pe o clonă locală temporară, fără push și fără modificarea istoricului. **Obținut:** 4 → 0 manifeste, copii listate în MAIN 4 → 0.
- **Remediere:** fără cod. Procedura: înainte de pull sau merge, pe fiecare calculator, se copiază `shared_data-backups` în afara depozitului. Opțional, un script mic `scripts/salveaza_manifeste_backup.py` care face copia automat. Istoricul Git nu se atinge.

### BUG-05a — Lock rămas: copia nu se face, dar totul pare „OK” — **Medie**
- **Cauză:** `shared/cimitir_backup.py:347-349` întoarce `busy` fără jurnal, iar `cli` (`:450-451`) iese cu 0. Lock-ul e considerat abandonat abia după 2 h (`LOCK_STALE_SECONDS`), adică exact cât `ExecutionTimeLimit` din `Register-CimitirBackupTask.ps1`.
- **Reproducere:** `05a`: cod 0, jurnal gol, stare „Nu există nicio copie” (avertisment galben, nu eroare).
- **Remediere minimă:**
  - `busy` se jurnalizează (`result="busy"`) când declanșarea nu e `app`;
  - `cli` întoarce 2 pentru `busy`;
  - opțional, PID-ul se scrie în lock și se verifică dacă procesul mai trăiește.
- **Risc:** fereastră de până la 2 h fără copie, nesemnalată. Fără pierdere directă.

### BUG-05c — Carantina eșuată lasă copia nevalidă în listă — **Medie**
- **Cauză:** `shared/cimitir_backup.py:354-357` ignoră `OSError` la redenumire, dar mesajul spune „pusă în carantină”. Copia rămâne `cimitir-…` cu manifest, apare prima în listă și contează la `keep_min`.
- **Reproducere:** `05c` (redenumire blocată simulat, ca pe Windows cu antivirus sau Explorer).
- **Remediere:** dacă redenumirea eșuează, se șterge `manifest.json` din copie (devine invizibilă pentru listă, retenție și restaurare), iar mesajul se corectează.

### BUG-03 — Copia din interiorul folderului de date nu poate fi restaurată — **Medie**
- **Cauză:** `restore` mută `data_dir` cu tot cu folderul de copii, apoi citește copia din calea veche și eșuează (`shared/cimitir_backup.py:155-161`). Rollback-ul păstrează starea. Nicio validare nu împiedică alegerea folderului (`desktop/backup_window.py:207-218`, `backend/server.py:1879-1880`).
- **Reproducere:** `test_bug03_…`: „Restaurarea nu a putut fi aplicată” la fiecare pornire.
- **Remediere minimă:** refuz la salvarea setării dacă folderul de copii este în `working_dir`/`data_dir`. În `schedule_restore`, eroare clară pentru o copie aflată în folderul de date.

### BUG-06 — „Ora de pornire (Planificator)” nu are efect — **Medie**
- **Cauză:** `backup_time` este salvată (`desktop/backup_window.py:160-165, 218`), dar nicio componentă nu o citește. `is_due` folosește doar intervalul. Comanda afișată (`scheduler_command`, `:41-46`) nu conține ora. Ora Task Scheduler vine doar din `-At` al scriptului PowerShell.
- **Remediere minimă:** câmpul afișat devine comanda completă `Register-CimitirBackupTask.ps1 -Exe … -At <ora>`, construită din setare. Alternativa e eticheta „doar pentru comanda de mai jos”.

---

## 3. SUSPICIUNI (nu sunt tratate ca buguri)

| # | Fișier | Observație | De ce nu e „confirmat” |
|---|---|---|---|
| S-1 | `shared/cimitir_sync_v2.py:464-470` | Sync v2: după restaurare, MAIN editează un rând cu `base_version` vechi → **MAIN câștigă**. Modificarea telefonului făcută între copie și restaurare este suprascrisă, iar starea veche rămâne în `sync_v2_conflicts` (`main_wins`), recuperabilă cu `restore_previous`. MAIN nu numără aceste cazuri drept conflicte, deci utilizatorul nu e avertizat | Comportament **documentat** (politica MAIN-wins). Propunere: după o restaurare, `sync_v2` să raporteze explicit câte `main_wins` au apărut |
| S-2 | `backend/mobile_upload.py:353-354` | Link-ul QR clasic **reclasifică** `carte_identitate` drept `alt_document`, nu refuză. O copie de act încărcată din telefon scapă astfel de toate filtrele (Sync v2, `media_sync`) | Interfața nu oferă tipul (`:309`), deci e nevoie de o cerere construită manual. Decizie: 422 în loc de reclasificare |
| S-3 | `backend/static/mobile_app.js:169,171` | „Reîncearcă” și „Reia” generează `op_id` nou | **Nu e bug:** se aplică doar operațiunilor refuzate sau în conflict, al căror răspuns e memorat pe server. Erorile de rețea păstrează același `op_id` (dedup corect) |
| S-4 | `shared/cimitir_sync_v2.py:399` | Memoria `op_id` e pe cimitir, nu pe dispozitiv: un `op_id` identic de pe alt dispozitiv primește răspunsul altuia | ID-uri aleatoare de 128 de biți; nereproductibil practic |
| S-5 | `shared/cimitir_portable.py:32-33,122` | Exportul portabil curăță outbox-ul și legătura, dar păstrează `cloud_client_id` și cursorii `entity_cursor_*` | Același mecanism ca BUG-04 doar dacă importul e conectat la **același** server clasic; nereprodus |
| S-6 | `desktop/main.py:26`, `backend/server.py:33` | MAIN și portalul aplică amândouă `apply_pending_restore` la pornire. Pornite simultan, restaurarea s-ar putea aplica de două ori (două foldere de siguranță) | Teoretic; pe Windows o bază deschisă blochează mutarea |
| S-7 | `desktop/backup_window.py:35-38` | Copia programată rulează într-un fir `daemon`. `QApplication.quit()` (de exemplu după programarea restaurării) îl oprește brusc, iar lock-ul rămâne (alimentează BUG-05a) | Dependent de momentul exact; nereprodus |
| S-8 | `scripts/windows/Register-CimitirBackupTask.ps1:47-54` | Activitatea e înregistrată fără `-Principal`, deci implicit „doar când utilizatorul e autentificat”. W9 (PC repornit, fără logare) nu va rula până la logare | Doar Windows |

---

## 4. NECESITĂ WINDOWS SAU TELEFON REAL

| # | Ce se verifică | Pași | Rezultat așteptat |
|---|---|---|---|
| W-A | BUG-02 cu DPAPI | MAIN `.exe` asociat la Cloud → 💾 Copii de rezervă → Fă copie → Restaurează selecția → repornește | Azi: cheia lipsește (Sincronizare cloud cere cheia), licența poate cere reactivare. După fix: totul rămâne |
| W-B | `Cimitir.exe --backup-run` (GUI, `console=False`) | `Start-Process -Wait -PassThru "…\Cimitir.exe" -ArgumentList "--backup-run"` → `.ExitCode`; repetă cu folder de copii inexistent | 0 la succes, 1 la eșec; `Task Scheduler` „Last Run Result” 0x0 / 0x1 |
| W-C | BUG-05a pe Windows | Creează `<folder copii>\.backup.lock`, apoi `Start-ScheduledTask` | Azi: 0x0 fără copie. După fix: cod 2 + rând „busy” în Jurnal |
| W-D | BUG-05c | Deschide un fișier din copia nouă în alt program în timpul verificării (sau ține Explorer în folder) | Copia nevalidă nu trebuie să apară în listă |
| W-E | S-8 | Înregistrează activitatea, deconectează-te, așteaptă ora | Azi probabil nu rulează; decizie: `-Principal` cu „Run whether user is logged on or not” |
| W-F | Scripturi `.ps1` (PS 5.1, BOM, diacritice) | `powershell -NoProfile -File Register-CimitirBackupTask.ps1 -Exe … -At 02:00 -DryRun`; `Verify-CimitirBuild.ps1 -DistDir …` | Fără erori de sintaxă; diacritice corecte |
| W-G | W1–W11 din `ETAPA10_DE_FACUT.md` | neschimbat | idem |
| P-A | BUG-08 pe iPhone/Safari | Mod avion → salvează 3 fișe → deschide/închide aplicația de mai multe ori (sau Safari în fundal ~10 min) → salvează a 4-a → „Stare sincronizare” | Toate 4 în coadă; azi pot rămâne doar ultimele |
| P-B | Coadă offline + reluare | P1–P5, S5–S7 din `ETAPA5_9_TESTARE_WINDOWS_TELEFON.md` | Fără duplicate, fără pierderi |
| P-C | CNP fără `view_pii`, revocare, token QR mascat | idem; verifică jurnalul serverului (`/api/m/[TOKEN]`) | CNP absent; după revocare → ecran de asociere; tokenul nu apare în clar |

---

## 5. Rezultatele suitelor existente (nemodificate)

| Suită | PASS | FAIL | SKIP | Note |
|---|---|---|---|---|
| Desktop (`desktop/tests`, `-n 0`), după ultima modificare din Etapa 10 | **609** | **0** | 13 | skip: pyzbar/pypdf, `SM_TEST_PG_URL`, screenshot |
| Backend (`backend/tests`, `-n 0`) cu PostgreSQL local | **1447** | **0** | 208 | 81 live (mediu separat), 53 server clasic pe :8001 / `ETAPA6_SERVER_URL`, 46 Supabase TEST, 10 mediu real TEST, 9 playwright, 7 PG dev, 2 DPAPI. **Cele 136 de SKIP „PostgreSQL local indisponibil” din raportul anterior sunt acum executate și trec** (cauza lor era variabila `CIMITIR_TEST_PG_ADMIN_URL` nesetată) |
| `tools/setup_manager/tests` + `validation/test_mobile_v2_*` fără PG | 130 | 2 | 17 | T-1, T-2 |
| Aceleași, cu `SM_TEST_PG_URL` local | 141 | 8 | 0 | T-3 |
| `desktop/tests/test_windows_validation.py` cu `SM_TEST_PG_URL` | 14 | 1 | 0 | T-4 |

Eșecurile din teste existente sunt **teste învechite**, nu defecte ale aplicației:
- **T-1:** `test_pairing.py::test_probe_all_pass`. FakeNet nu servește `mobile_ext.js`, iar aplicația îl cere corect.
- **T-2:** `test_mobile_v2_data_pipeline.py::…hex32…`. Folosește un SQLite fals pentru un serviciu PG-only, iar detecția 028 interoghează `information_schema`.
- **T-3:** `test_migrations_pg` / `test_server`. Se așteaptă migrările 24–26, dar există 27–28.
- **T-4:** UI-01 cere 6 file, dar acum sunt 7. SB-04 cere 26 de migrări, dar sunt 28. SB-02 (TLS) eșuează normal pe un PG local fără TLS.

**Notă de mediu:** detectorul PG din teste acceptă doar TCP `127.0.0.1`. Containerul are 1024 de porturi efemere, de aceea conexiunile au fost redirecționate spre socket unix printr-un `sitecustomize.py` **extern** depozitului. Testele nu au fost modificate.
Testele care cer server clasic pe `ETAPA6_SERVER_URL` (53) **nu au fost rulate**: lucrul a fost oprit la cererea ta.

---

## 6. PLAN DE REMEDIERE (ordonat după gravitate) — **așteaptă aprobarea ta**

| Ordine | Bug | Modificare minimă | Fișiere | Efort | Risc la remediere |
|---|---|---|---|---|---|
| 1 | BUG-04 | golire `cloud_client_id` (+ `restored_at`) în baza restaurată | `shared/cimitir_backup.py` | 1 h (+ test regresie = testul din audit) | alertă „client offline” pentru id-ul vechi (de documentat) |
| 2 | BUG-08 | `outbox()` propagă eroarea; mesaj clar; bump cache SW | `backend/static/mobile_app.js`, `mobile_ext.js`, `mobile_sw.js` | 1–1.5 h | mic; telefoanele primesc JS nou la reîmprospătare |
| 3 | BUG-01 | trigger + `enqueue_existing` exclud actele; `_upload` le scoate din coadă; server 422 | `desktop/media_sync.py`, `backend/media_api.py` | 1–2 h | **decizia ta A1**; documentele deja urcate: plan separat |
| 4 | BUG-07 | tombstone → 410 în `complete`; 410 → `dropped` în client | `backend/media_api.py`, `desktop/media_sync.py` | 1 h | mic |
| 5 | BUG-02 | listă extinsă de elemente păstrate la restaurare | `shared/cimitir_backup.py` | 1 h | mic (doar copiere din folderul de siguranță) |
| 6 | BUG-05b | retenție pe copii verificate complet | `shared/cimitir_backup.py` | 1 h | mic (păstrează mai mult) |
| 7 | BUG-09 | procedură + script opțional de salvare a manifestelor | doc / `scripts/` | 0.5 h | niciunul |
| 8 | BUG-05a, 05c | jurnal `busy` + cod 2; manifest șters la carantină eșuată | `shared/cimitir_backup.py` | 1 h | mic |
| 9 | BUG-03 | validare folder copii ≠ în folderul de date | `desktop/backup_window.py`, `backend/server.py`, `shared/cimitir_backup.py` | 0.5–1 h | mic |
| 10 | BUG-06 | comanda completă cu `-At` din setare | `desktop/backup_window.py` | 0.5 h | niciunul |
| 11 | T-1…T-4 | actualizarea testelor învechite (doar după aprobare, separat) | teste | 1 h | niciunul |

Total estimat: **9–12 h** de lucru efectiv. După fiecare punct: testul din `validation/bug_audit/` devine PASS, iar suitele desktop și backend se rerulează (0 FAIL noi).
Decizii care îți aparțin: **A1** (punctul 3), S-1 (raportarea `main_wins` după restaurare), S-2 (422 în loc de reclasificare), S-8 (rulare fără logare).

---

## 7. Cum se rulează testele de reproducere

```
cd <repo>
export BUGAUDIT_PG_ADMIN_URL="postgresql://postgres@127.0.0.1:5432/postgres"     # PostgreSQL LOCAL de test (superuser); sau ?host=/tmp pentru socket unix
python -m pytest validation/bug_audit -q -p no:cacheprovider
```
- Fără `BUGAUDIT_PG_ADMIN_URL`, testele BUG-04 și BUG-07 sunt SKIP. Ele pornesc un server clasic propriu (uvicorn) pe o bază temporară `bugaudit_<rand>`, ștearsă la final.
- BUG-08 cere `node`, iar BUG-09 cere `git` (clonă locală temporară).
- Fiecare test **eșuează azi** (reproduce bugul) și va trece după remediere. Excepții:
  - `test_fix_simulation_new_client_id_after_restore_reports_conflict` trece deja: validează remedierea propusă pentru BUG-04;
  - `test_check_restore_does_not_resurrect_row_deleted_elsewhere` trece: este o verificare fără defect.
