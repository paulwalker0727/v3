# ETAPA 10 — STATUS FINAL (dezvoltare oprită la cererea utilizatorului)

Branch: `feature/etapa10-stabilizare` (pornit din `feature/etapa5-9-finalizare` @ `5b502f0`). Fără merge, fără force-push, istoricul Git neatins.
**Nimic din acest document nu a fost validat pe Windows real sau pe telefon.** Nu s-a folosit Supabase TEST/PROD, nu s-au executat migrații reale, Docker-ul existent nu a fost atins, nicio bază reală nu a fost modificată.

## 1. Implementat complet (verificat prin teste automate locale)
| Funcționalitate | Dovadă |
|---|---|
| **MAIN: un singur buton de backup.** „💾 Backup" eliminat; „💾 Copii de rezervă" este interfața unică („Fă copie acum" = vechiul buton) | `desktop/tests/test_etapa10_main_ui.py` |
| Ferestrele avize / integritate / persoane duplicate / backup accesibile din bara MAIN, fără erori de import. Butonul „Amenajări" afișa un placeholder („Aici va fi modulul"); acum deschide tabul „Amenajări" (avizele) al locului selectat sau cere selectarea unui loc | idem |
| **Curățare repo:** cele 4 `shared_data-backups/*/manifest.json` scoase din index (`git rm --cached`; fișierele **rămân pe disc**) + reguli `.gitignore`; `.emergent/cron/*` (artefact de pod, deja ignorat de `.gitignore` dar urmărit) scos din index | `git ls-files -i -c --exclude-standard` = gol |
| **Backup programat în MAIN** (infrastructura existentă `cimitir_backup`): interval/oră/folder/retenție configurabile; **verificare a fiecărei copii** imediat după creare (SHA-256 + `integrity_check`; copia nevalidă → carantină `*.invalid` + EȘEC); **avertizare vizibilă** (etichetă roșie în bara de stare + banner); **retenție protejată** (minim 3 copii din MAIN, nimic șters dacă cele mai noi nu sunt întregi, copii marcate „Protejat", doar foldere `cimitir-AAAAMMZZ-HHMMSS`); **jurnal** (`*.backup-log.jsonl`: dată, rezultat, declanșare, locație, eroare) vizibil în fereastră; lock contra copiilor simultane; reîncercare din aplicație limitată la 1/oră după eșec; **restaurarea rămâne manuală** (confirmare cu „Nu" implicit; nicio programare nu o apelează) | `desktop/tests/test_etapa10_backup_schedule.py` (13 teste) + `test_backup_window.py`, `test_etapa7_backup_restore.py` |
| Rulare fără interfață: `Cimitir.exe --backup-run` / `python scripts/backup_scheduled.py` (cod 1 = eșec) | `test_etapa10_windows_prep.py`, `test_etapa10_backup_schedule.py::test_headless_cli_*` |
| Verificare statică a intrărilor build-ului Windows (`scripts/windows/verify_build_inputs.py`, 36 PASS / 0 FAIL pe Linux) și `build_exe.bat` fără dependență de Git (excludea doar în CI; acum și local) | `test_etapa10_windows_prep.py` |

## 2. Implementat parțial
| Subiect | Starea |
|---|---|
| **Backup automat prin Windows Task Scheduler** | **PARȚIAL.** Cod + scripturi scrise (`scripts/windows/Register-CimitirBackupTask.ps1`, `Unregister-…ps1`, CLI `--backup-run`). Partea Python este testată pe Linux. **Scripturile PowerShell NU au fost executate nici măcar sintactic** (nu există `pwsh` în mediu). Înregistrarea activității în Windows = **neverificată** |
| Pregătire Windows / `Cimitir.exe` | **PARȚIAL.** `Verify-CimitirBuild.ps1` (post-build) scris, **neexecutat**; pasul a fost adăugat în `build-windows.yml` ca `continue-on-error` (informativ), plus pasul static `verify_build_inputs.py`. **Cimitir.exe nu a fost construit și nu a fost validat** (nu există Windows). Instrucțiuni: `ETAPA10_WINDOWS_BUILD.md` |
| **Audit sincronizare MAIN↔Cloud↔Mobile** | **PARȚIAL (audit prin citire de cod + suite existente + 1 probă).** Vezi §5. Nu s-au adăugat teste noi de sincronizare: nu a fost demonstrată nicio lipsă de acoperire, ci o **decizie de politică** (O-1) |
| Raportul cerut inițial `ETAPA10_RAPORT.md` | înlocuit de acest document |

## 3. Neînceput
- Validare pe Windows real și pe telefon (Android/iPhone) a oricărei funcții din Etapa 10.
- Criptarea copiilor de rezervă (doar recomandare BitLocker).
- Teste de regresie sincronizare + restaurare backup pe calculator cu sincronizare activă.
- Etapa 11 (nu s-a început, conform instrucțiunii).

## 4. Probleme / erori nerezolvate
**Critice (de decis înainte de utilizare reală)**
1. **O-1 — documente de identitate în sincronizarea clasică.** Demonstrat cu probă pe SQLite temporar: `media_sync` (conducta Etapa 7, `/api/sync/media`) pune în coadă pentru încărcare *toate* tipurile de document, inclusiv `carte_identitate` și `cerere_doc`; conducta Sync v2 (`sync_v2_docs`, `cimitir_sync_v2_docs`) și administrarea Cloud le **exclud**. **Nu am modificat comportamentul** (decizie de politică). Recomandare: aliniere (sărit în `media_sync`) + test.
2. **Backup-uri necriptate cu date personale (CNP etc.)** pe discul ales. Mitigare: disc BitLocker. Cheile de sincronizare sunt în DPAPI, dar hash-urile parolelor MAIN sunt în copie.
3. **Utilizator care are deja repo-ul local:** după `git pull`/merge, Git va **șterge din working tree cele 4 `manifest.json`** (nu mai sunt urmărite). Înainte de merge: copiază folderul `shared_data-backups` în afara repo-ului (manifest-urile sunt necesare pentru `verify`/restaurare a acelor copii; bazele `cimitir.db` nu erau în Git oricum).

**Minore**
- 136+14+7 teste PostgreSQL au fost **SKIP în rularea finală** (vezi §6) — nu sunt dovadă de eșec, dar nici de PASS în rularea finală.
- `Ghid_Instalare_Cimitir.pdf` se regenerează la rularea testelor (nu l-am comis modificat).
- `TEST_LAPTOP_WINDOWS_DOCKER.md` conține referința proiectului Supabase TEST (identificator public, nu secret).
- `backend/.env` și `frontend/.env` au fost în istoric (commit `7275f0e`, șterse în `1ac65a8`): conțin doar valori-șablon (MONGO local) — nu am modificat istoricul. Nicio cheie reală găsită în arborele curent (scanare pe șabloane: GitHub/Stripe/AWS/Google/Slack/JWT/chei private/URL-uri cu parolă; doar valori fictive din teste/documentație).

## 5. Audit sincronizare (rezumat)
| Flux | Acoperire existentă (teste) | Concluzie |
|---|---|---|
| Concesionari și înhumați | `test_etapa6_entity_sync` (push/pull, offline→flush, idempotență `op_id`, conflict 409-logic, ștergere propagată, ack pierdut, copil-înainte-de-părinte, `cod_loc` duplicat), `test_sync_v2_main_e2e`, `test_sync_v2_pg` | acoperit |
| Contracte și plăți | idem + `test_cnp_sync_pg`, `test_f03_expiring_contracts_pg`, `test_mobile_permissions_pg` | acoperit |
| Avize (028) | `test_avize_complete_028` (MAIN→mobil, mobil→MAIN, clienți vechi nu golesc valori, replay idempotent, coadă offline + backfill), `…_028_pwa`, `test_etapa5_*avize*` | acoperit; migrarea 028 pe Supabase **neaplicată** (vezi §9) |
| Fotografii și documente | `test_etapa7_media`, `test_etapa789_pwa_photos` (coadă offline fără duplicate), `test_etapa789_docs_main_e2e`, `test_etapa6b_field_storage` | acoperit; **O-1** |
| Permisiuni și date sensibile | `test_etapa789_pii_docs_pg` (telefonul nu primește CNP/CI implicit; `view_pii` explicit), `test_cnp_sync_pg`, `test_mobile_permissions_pg` (revocarea se aplică și operațiilor offline trimise ulterior), `test_rls_isolation_pg`, `test_f02_audit_redaction` | acoperit; **O-1** |
| Offline și reluare | `test_etapa6_entity_sync::04/09/10`, `test_etapa6_pwa_compat`, `test_sync_v2_pwa`, `test_etapa4_pas4_crash`, `test_avize_complete_028::main_offline_queue…` | acoperit în teste; **neverificat pe telefon real** |
| Restaurare backup + sincronizare activă | nicio acoperire automată | **lipsă de verificare** (pas manual W6+S4 din `ETAPA5_9_TESTARE_WINDOWS_TELEFON.md`) |

## 6. Teste executate (mediu local izolat Linux; PostgreSQL 15 temporar propriu, port 55432, creat în `/app/pgtest`; fără Supabase/Docker)
| Suită | PASS | FAIL | SKIP | Note |
|---|---|---|---|---|
| Desktop (`desktop/tests`, `-n 0`) — **rulare completă înainte de ultima modificare a pragului de reîncercare** | **608** | **0** | **13** | skip: pyzbar/pypdf lipsă, `SM_TEST_PG_URL`/screenshot nesetate |
| După ultima modificare (reîncercare limitată după eșec): doar `test_etapa10_backup_schedule`, `test_backup_window`, `test_etapa7_backup_restore` | 24 | 0 | 0 | **suita desktop completă NU a fost rerulată după această modificare** |
| Backend (`backend/tests`, `-n 0`) — rulare finală | **1224** | **0** | **431** | skip: 136 „PostgreSQL local de test indisponibil" (detectorul din teste nu a recunoscut serverul pe socket unix `/tmp`), 37 Supabase TEST nesetat, 33 teste live fără mediu separat, 14+7+2 „PostgreSQL nu rulează", 10 mediu TEST real, 5 `playwright` lipsă, 1 server 8001, 2 DPAPI doar Windows |
| Backend — prima rulare (PG pe TCP) | 964 | 13 | 0 | + 471 ERROR: `Cannot assign requested address` (epuizarea porturilor efemere ale containerului: interval 32768–33791) și 1 FAIL real, cauzat de modificarea mea (`prune` forța minim 3 copii; testul `test_etapa6_ops` cere `keep_min=1`) — **corectat** (pragul de 3 se impune acum doar din programarea MAIN). Rularea a fost repetată pe socket unix |
| Teste Etapa 10 noi | 13 + 1 + 3 = 17 | 0 | 0 | backup/programare, interfață MAIN, pregătire Windows |
| `verify_build_inputs.py` | 36 | 0 | 0 | static, Linux |
| Teste PyInstaller/spec (în suita desktop, `pip install pyinstaller` făcut local) | incluse mai sus | 0 | 0 | în mediul inițial fără PyInstaller aveau 5 FAIL de mediu |

**Nerulat:** `tools/setup_manager/tests`, `validation/test_mobile_v2_*.py`, scripturile din rădăcină (`backend_test.py`, `test_logout.py` etc., cer server pe `localhost:8001/8012`), teste live Supabase, `playwright` — oprite la cererea utilizatorului / neaplicabile.

**PENDING MANUAL VALIDATION:** vezi §7.

## 7. De verificat pe Windows și pe telefon
Windows (după `desktop\build_exe.bat`): 1) `python scripts\windows\verify_build_inputs.py`; 2) `scripts\windows\Verify-CimitirBuild.ps1 -DistDir desktop\dist\Cimitir` (neexecutat până acum); 3) pornire MAIN din exe pe Windows curat; 4) „💾 Copii de rezervă": copie acum, verificare, jurnal, avertizare (simulează eșec: folder inexistent), restaurare manuală; 5) `Register-CimitirBackupTask.ps1 -Exe … -At 02:00` + `Start-ScheduledTask` + verificare Jurnal; 6) ferestrele Integritate/Persoane duplicate/tab Amenajări; 7) PDF cu diacritice; 8) instalare/dezinstalare Inno Setup. Pași detaliați: `ETAPA10_WINDOWS_BUILD.md`, `ETAPA5_9_TESTARE_WINDOWS_TELEFON.md`.
Telefon: pairing QR, coadă offline (mod avion) cu fotografii/aviz, revocare, permisiuni (P1–P5 din documentul de mai sus). Sincronizare reală: doar pe Postgres LOCAL de test.

## 8. Ce mai trebuie făcut (ordine recomandată)
**Critic**
1. Decizie + fix **O-1** (identitate în `media_sync`) cu test.
2. Rularea suitei desktop **complete** după ultima modificare + rerulare backend cu PG detectat corect (TCP sau unix) pentru a transforma cele ~157 SKIP de PG în PASS/FAIL reale.
3. Validare Windows: build exe, `Verify-CimitirBuild.ps1`, Task Scheduler, MAIN din exe.
4. Validare telefon (P1–P5, S5–S7).
5. Test manual restaurare backup + sincronizare activă; plan pentru copiile existente (`shared_data-backups`) la merge (§4.3).
**Opțional**
6. Criptare copii (parolă/DPAPI). 7. Notificare Windows la eșec. 8. Ștergere automată `*.invalid`. 9. Curățarea `_open_small_window` (cod mort). 10. Mutarea `test_reports/` și `memory/` în afara Git.

## 9. Migrarea 028 și Supabase Storage
- **028:** neaplicată pe Supabase TEST/PROD (interzis). Plan neschimbat: `PLAN_APLICARE_028_SUPABASE_TEST.md`. În teste locale (PG propriu) schema 028 este exercitată (`test_avize_complete_028*`, `test_sync_v2_migration`), cu rezervele de SKIP din §6. Scripturile Windows de build verifică doar prezența fișierelor 001–028, nu le execută.
- **Supabase Storage:** testele live (`test_etapa789_live_supabase_test`, `test_step6_mobile_upload` 02/03/04/07/15, `test_etapa6b_field_storage_live`) = **SKIP** (37 + 10 + 33 în rularea finală); nerulate, deoarece cer Supabase TEST. Stocarea locală/S3 simulat (moto) este exercitată de suitele non-live.

## 10. Commituri noi și fișiere
Vezi lista exactă mai jos (generată din Git la publicare) — secțiunea „Commituri" din mesajul de livrare.
Fișiere noi: `ETAPA10_BACKUP_AUTOMAT.md`, `ETAPA10_WINDOWS_BUILD.md`, `ETAPA10_STATUS_FINAL.md`, `scripts/backup_scheduled.py`, `scripts/windows/{Register,Unregister}-CimitirBackupTask.ps1`, `scripts/windows/Verify-CimitirBuild.ps1`, `scripts/windows/verify_build_inputs.py`, `desktop/tests/test_etapa10_{backup_schedule,main_ui,windows_prep}.py`.
Modificate: `shared/cimitir_backup.py`, `desktop/backup_window.py`, `desktop/ui_mainwindow.py`, `desktop/launcher.py`, `desktop/build_exe.bat`, `.github/workflows/build-windows.yml`, `.gitignore`.
Scoase din index (rămân pe disc): `shared_data-backups/*/manifest.json` (4), `.emergent/cron/*` (5).
