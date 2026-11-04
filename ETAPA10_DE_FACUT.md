# ETAPA 10 — Raport detaliat: ce mai este de făcut și de rezolvat

Stare de referință: branch `feature/etapa10-stabilizare` (publicat), după commit-ul `6f53883`. Completează `ETAPA10_STATUS_FINAL.md` (ce s-a făcut / rezultatele testelor).
Nimic din Etapa 10 nu a fost validat pe Windows real sau pe telefon. Estimările de efort sunt orientative (în ore de lucru efective, fără timpul de așteptare al build-urilor).

## Cuprins
- A. Probleme critice (blochează utilizarea reală)
- B. Importante (de făcut înainte de livrare către utilizatori)
- C. Opționale (îmbunătățiri)
- D. Ordinea recomandată
- E. Criterii de acceptare finale
- F. Decizii care îți aparțin

---

## A. PROBLEME CRITICE

### A1. Documente de identitate în sincronizarea clasică (decizie + fix)
- **Problema:** conducta Etapa 7 (`desktop/media_sync.py` → `/api/sync/media`) pune în coadă pentru încărcare **toate** tipurile de document, inclusiv `carte_identitate` și `cerere_doc`. Conducta Sync v2 (`desktop/sync_v2_docs.py`, `shared/cimitir_sync_v2_docs.py`) și administrarea Cloud (`shared/cimitir_cloud_app.py`, `backend/server.py`) le **exclud** explicit.
- **Dovadă:** probă pe SQLite temporar — 3 documente (`carte_identitate`, `contract_scan`, `cerere_doc`) → 3 operațiuni `upload` în `cloud_sync_outbox`. Nu s-a modificat comportamentul.
- **Risc:** copii ale actelor de identitate ajung în storage-ul serverului clasic fără aceeași protecție ca în Sync v2.
- **Acțiuni:**
  1. Decizie: se aliniază la Sync v2 (recomandat) sau se păstrează intenționat pentru serverul propriu?
  2. Dacă se aliniază: în `media_sync` (trigger/`_enqueue`) se marchează aceste tipuri `skipped` cu notă (ca în `sync_v2_docs.py:113`); test nou în `desktop/tests/` care verifică că nu apare operațiune `upload` pentru ele și că celelalte tipuri se încarcă.
  3. Documentele deja încărcate anterior: verificare/plan de curățare pe server (decizie separată, nu automat).
- **Efort:** 1–2 h (fix + test) după decizie. **Acceptare:** testul nou PASS; suita `test_etapa7_media`, `test_etapa789_docs_main_e2e` rămâne PASS.

### A2. Merge-ul poate șterge fișiere locale (manifest-urile backup)
- **Problema:** cele 4 `shared_data-backups/*/manifest.json` nu mai sunt urmărite. Pe un calculator care le are în working tree, `git pull`/merge le va **șterge de pe disc**; fără manifest, copiile respective nu mai pot fi verificate/restaurate.
- **Acțiuni (înainte de merge, pe fiecare calculator cu repo):** copiază integral `shared_data-backups` în afara repo-ului (ex. `D:\copii-cimitir\`), apoi fă merge, apoi (dacă e nevoie) readu folderul. Nu e nevoie de modificări de cod.
- **Efort:** 10 min. **Acceptare:** după merge, folderul copiat în afara repo-ului este intact.

### A3. Suita de teste nu a fost rulată complet și curat după ultimele modificări
- **Desktop:** ultima rulare completă (608 PASS / 0 FAIL / 13 SKIP) a fost **înainte** de limitarea reîncercărilor după eșec; după aceea s-au rulat doar 24 de teste (backup). Rerulează suita completă.
- **Backend:** rularea finală = 1224 PASS / 0 FAIL / **431 SKIP**; dintre acestea **136** „PostgreSQL local de test indisponibil" (detectorul din teste nu a recunoscut serverul pe socket unix), plus 14+7+2 „PostgreSQL nu rulează" → ~160 de teste PG nu au fost de fapt executate în rularea finală. (Rularea cu PG pe TCP a executat mai multe, dar a fost afectată de epuizarea porturilor containerului.)
- **Nerulate:** `tools/setup_manager/tests/*`, `validation/test_mobile_v2_*.py`, scripturile din rădăcină (`backend_test.py`, `test_logout.py`, `test_admin_pages.py`, `feature_validation_test.py` etc. — cer server pe `localhost:8001/8012`, nu sunt hermetice).
- **Acțiuni:**
  1. Pornește un PostgreSQL local de test (Docker nou, NU cel existent, sau instalare locală) pe TCP `127.0.0.1:5432` și rulează comenzile din `ETAPA5_9_RAPORT.md` §„Comenzi de reproducere".
  2. Rulează `cd desktop && python -m pytest -n 0 -q tests` și `cd backend && python -m pytest -n 0 -q -rs tests`.
  3. `python -m pytest tools/setup_manager/tests validation -q` (cu `SM_TEST_PG_URL` către PG LOCAL, nu Supabase).
  4. Raportează PASS/FAIL/SKIP exact; orice FAIL se analizează înainte de merge.
- **Efort:** 1–2 h (în mare parte așteptare). **Acceptare:** 0 FAIL; SKIP rămase doar pentru live Supabase, Windows-only (DPAPI), `playwright`/`pyzbar`.

### A4. Validare pe Windows real (nimic nu a fost rulat acolo)
Pregătire: `ETAPA10_WINDOWS_BUILD.md`.
| # | Pas | Rezultat așteptat |
|---|---|---|
| W1 | `python scripts\windows\verify_build_inputs.py` | `0 FAIL` |
| W2 | `desktop\build_exe.bat` (Python 3.11, fără `git` instalat) | `dist\Cimitir\Cimitir.exe` (+ portabil, + instalator dacă există Inno Setup) |
| W3 | `scripts\windows\Verify-CimitirBuild.ps1 -DistDir desktop\dist\Cimitir` | `0 FAIL`; notează SHA-256 al exe-ului. **Scriptul nu a fost executat niciodată** — este posibil să necesite corecturi (sintaxă PowerShell 5.1, căi) |
| W4 | Pornire MAIN din exe pe Windows curat (fără Python) | se deschide, login, hartă |
| W5 | 💾 Copii de rezervă: *Fă copie acum*, *Verifică selecția*, jurnal, protejare, *Salvează setările* | copie întreagă, rând „REUȘIT" în jurnal |
| W6 | Simulează eșec (folder de copii inexistent/ocupat) | etichetă roșie „⛔ Backup EȘUAT" în bara de stare + banner în fereastră; dispare după o copie reușită |
| W7 | Restaurare manuală: modifică o setare → *Restaurează selecția…* (implicit „Nu") → confirmă → repornește | starea revine; apare `…inainte-de-restaurare-*` |
| W8 | `Register-CimitirBackupTask.ps1 -Exe "…\Cimitir.exe" -At 02:00` apoi `Start-ScheduledTask -TaskName "Cimitir - Copie de rezerva"` | copie nouă + intrare în Jurnal cu declanșare `windows-task`; `Task Scheduler` arată „Last Run Result 0x0" |
| W9 | `Register…` cu MAIN închis și PC repornit după ora programată | `StartWhenAvailable` rulează copia la pornire |
| W10 | Ferestrele 🧪 Verificare integritate, 🔁 Persoane duplicate, tabul „Amenajări" al unui loc | se deschid fără erori |
| W11 | PDF (fișă loc, contract) cu diacritice; instalare/dezinstalare Inno Setup | corecte; datele din `%LOCALAPPDATA%\Cimitir` rămân |
- **Riscuri cunoscute de verificat explicit:** (a) `Cimitir.exe --backup-run` este aplicație GUI (`console=False`) — verifică că ieșirea/exit-code funcționează sub Task Scheduler; (b) scripturile `.ps1` au BOM UTF-8 (pentru diacritice în PS 5.1) — verifică afișarea; (c) `Verify-CimitirBuild.ps1` presupune că portalul creează o bază în `CIMITIR_DATA_DIR` temporar; dacă nu, pasul `--backup-run` iese `SKIP`.
- **Efort:** 3–5 h. **Acceptare:** W1–W11 completate în tabel; orice defect → ticket separat.

### A5. Validare pe telefon (Android Chrome / iPhone Safari ≥ 16.4)
Pașii P1–P5 și S5–S7 din `ETAPA5_9_TESTARE_WINDOWS_TELEFON.md` (HTTPS public necesar pentru cameră; Cloud/Postgres **LOCAL** de test): instalare PWA și asociere QR, coadă offline (mod avion) cu aviz + fotografii, repornire aplicație, reluare automată fără duplicate, schimbare permisiuni din MAIN, revocare telefon, CNP netrimis fără `view_pii`, token QR mascat în jurnal.
- **Efort:** 3–4 h. **Acceptare:** tabelul din document completat pe Android și iPhone.

### A6. Restaurare backup + sincronizare activă (lipsă de acoperire)
- **Problema:** nu există test automat (nici manual documentat în detaliu) pentru: restaurarea unei copii mai vechi pe un calculator care sincronizează. DB restaurată aduce înapoi `cloud_sync_outbox`, cursorii `change_seq` și `base_version`.
- **De verificat:** (1) operațiile din outbox retrimise nu creează duplicate (idempotență `op_id` — acoperită în general de teste, nu pe scenariul restaurare); (2) modificările făcute între copie și restaurare, deja urcate în Cloud, nu sunt „anulate" silențios de un pull/push cu `base_version` vechi (conflict 409-logic sau MAIN-wins — verifică comportamentul real); (3) fotografiile/documentele restaurate rămân consistente cu cele din Cloud.
- **Acțiuni:** scenariu manual cu 2 calculatoare/baze temporare (pașii W6 + S4), apoi, dacă apare o lacună demonstrată, test de regresie în `backend/tests/` (Postgres local).
- **Efort:** 2–4 h. **Acceptare:** scenariul documentat cu rezultat; nicio pierdere/duplicare de date.

---

## B. IMPORTANTE (înainte de livrare către utilizatori)

### B1. Patch-ul pentru workflow-ul GitHub
- `scripts/windows/build-windows.workflow.patch` adaugă în `.github/workflows/build-windows.yml`: pasul static `verify_build_inputs.py` (blocant) și pasul post-build `Verify-CimitirBuild.ps1` (`continue-on-error`, informativ).
- A fost livrat ca patch deoarece tokenul folosit nu are scope `workflow`. Aplicare: `git apply scripts/windows/build-windows.workflow.patch`, commit, push de pe un cont/token cu drept de workflow. Rulează apoi workflow-ul manual (*Actions → Run workflow*).
- **Efort:** 15 min.

### B2. Migrarea 028 și Supabase TEST/Storage
- **028 nu este aplicată pe Supabase TEST/PROD** (interzis în Etapa 10). Planul: `PLAN_APLICARE_028_SUPABASE_TEST.md`, `ETAPA789_MIGRATII_ROLLBACK.md`. Se aplică **doar cu aprobarea ta explicită**, după backup și pe TEST întâi.
- Teste live rămase SKIP (nerulate): `test_etapa789_live_supabase_test`, `test_step6_mobile_upload` 02/03/04/07/15, `test_etapa6b_field_storage_live`, `test_phone_pairing_live`, `test_live_smoke` etc. (în total 37 + 10 + 33 SKIP în rularea backend). Se rulează după 028, cu mediu separat de test (`CIMITIR_LIVE_TESTS=1`, ref de test) — **niciodată pe PROD**.
- Server fără 028: MAIN/PWA trebuie să afișeze mesajul de incompatibilitate (S3 din documentul de testare) — de verificat manual.
- **Efort:** 2–3 h după aprobare.

### B3. Securitatea backup-urilor
- Copiile conțin întreaga bază **necriptată** (CNP, adrese, hash-uri parole MAIN). Cheile de sincronizare sunt în DPAPI (nu în bază), dar o copie a bazei rămâne sensibilă.
- **Minim:** recomandă/impune folder de copii pe disc cu BitLocker; avertisment în fereastra de backup dacă folderul e pe OneDrive/Dropbox/rețea (există `cimitir_config.sync_folder_warning`, nefolosit încă pentru folderul de copii).
- **Mai bine (opțional, decizie):** criptare copii cu parolă/DPAPI — schimbă formatul manifestului; necesită migrare și teste.
- **Efort:** 0.5 h (avertisment) / 1–2 zile (criptare).

### B4. Curățenie repository
- Fișiere de raport/test urmărite: `test_reports/*` (inclusiv `v2_history`, XML-uri de ~150 KB), `memory/PRD*.md`, `.emergent/emergent.yml`, `.emergent/markers/*` — nu sunt secrete (verificat), dar sunt artefacte de mediu; decide dacă le muți în afara Git (`git rm --cached` + `.gitignore`) într-un commit separat.
- `TEST_LAPTOP_WINDOWS_DOCKER.md` conține referința proiectului Supabase TEST (`jqtdeuz…`) — identificator, nu secret; poți înlocui cu `<REF>`.
- Istoric: `backend/.env`/`frontend/.env` au fost comise la `7275f0e` (șterse la `1ac65a8`) cu valori-șablon (Mongo local). Nu s-a modificat istoricul. Dacă vreodată au conținut chei reale, rotește-le (nu am găsit chei reale).
- **Efort:** 0.5 h.

### B5. Detalii de backup de urmărit după primele utilizări
- Verifică în teren că jurnalul `<folder-date>.backup-log.jsonl` (lângă folderul de date) nu devine problematic (limitat la 500 linii) și că restaurarea nu îl pierde (e în afara folderului de date — comportament intenționat).
- Dacă discul extern lipsește la ora programată → eșec + avertisment; după reconectare, copia se reia la următorul tick (app: max 1/oră după eșec; Task Scheduler: la următoarea rulare).
- Copiile `*.invalid` (carantină) nu sunt șterse automat — verifică spațiul ocupat.

---

## C. OPȚIONALE

| # | Îmbunătățire | Beneficiu | Efort |
|---|---|---|---|
| C1 | Notificare Windows (toast/e-mail) la eșecul copiei programate când MAIN e închis | acum avertismentul apare doar la deschiderea MAIN | 2–4 h |
| C2 | Ștergere automată `*.invalid` mai vechi de N zile | spațiu pe disc | 1 h |
| C3 | Cod mort: `_open_small_window` din `ui_mainwindow.py` (nefolosit după fixul „Amenajări") | curățenie | 0.2 h |
| C4 | Fereastră dedicată „Avize" (listă globală) — acum avizele sunt în tabul „Amenajări" al fiecărui loc | confort; **funcție nouă, doar dacă o dorești** | 1–2 zile |
| C5 | Test automat pentru UI-ul de eroare al `Register-CimitirBackupTask.ps1` (validări `-At`, `-RepeatHours`) cu Pester pe Windows | siguranță | 1–2 h |
| C6 | Pas `pwsh -NoProfile -Command "[scriptblock]::Create((Get-Content …))"` în CI pentru a prinde erori de sintaxă PowerShell | prinde greșeli din timp | 0.5 h |
| C7 | Actualizarea `ETAPA5_9_TESTARE_WINDOWS_TELEFON.md` (W5) cu noua interfață unificată | documentație consecventă | 0.3 h |

---

## D. ORDINEA RECOMANDATĂ
1. **A1** decizie + fix (identitate) — mic, elimină riscul de date sensibile.
2. **A3** rulare completă teste cu PG local pe TCP → baza numerică reală (fără ~160 SKIP de PG).
3. **B1** aplică patch-ul workflow → build-ul de pe GitHub rulează verificările.
4. **A4** build + validare Windows (W1–W11); corectează orice defect din scripturile `.ps1`.
5. **A5** validare telefon.
6. **A6** scenariul restaurare + sincronizare.
7. **A2** copiază `shared_data-backups` în afara repo-ului → **apoi** merge PR-ului (nu înainte de pașii 1–6).
8. **B2** (028 + teste live) doar cu aprobarea ta, pe TEST.
9. **B3, B4, B5**, apoi opționalele C.

## E. CRITERII DE ACCEPTARE FINALE (aplicația „pregătită de utilizare")
- 0 FAIL în suitele desktop + backend + setup_manager + validation, cu PG local pe TCP; SKIP doar pentru live/Windows-only/biblioteci opționale, listate.
- `verify_build_inputs.py` 0 FAIL; `Verify-CimitirBuild.ps1` 0 FAIL pe Windows; SHA-256 al `Cimitir.exe` consemnat.
- W1–W11 și P1–P5/S5–S7 completate, fără pierderi de date, fără duplicate la reluarea sincronizării.
- Task Scheduler: o copie programată reușită vizibilă în Jurnal + una eșuată vizibilă ca avertisment.
- Decizia A1 aplicată; manifest-urile locale puse la adăpost (A2).

## F. DECIZII CARE ÎȚI APARȚIN
1. A1: actele de identitate se sincronizează sau nu în conducta clasică?
2. B2: aprobi aplicarea 028 pe Supabase **TEST** (și când)?
3. B3: backup-uri criptate (da/nu)? Minimul propus: BitLocker + avertisment.
4. B4: mutăm artefactele `test_reports/`, `memory/`, `.emergent/*` din Git?
5. C4: vrei o fereastră globală „Avize" sau rămâne în fișa locului?

## Restricții respectate până acum
Nu s-a folosit Supabase TEST/PROD, nu s-au executat migrații reale, Docker-ul existent nu a fost atins, nu s-au șters date, fără merge/force-push, fără Etapa 11.
