# CIMITIR v3 — Raport remediere buguri critice (Etapa 11, Faza 2)

Branch: `fix/audit-critical-bugs` (pornit din `feature/etapa10-stabilizare` @ `0caa41d`).
Scop: repararea exactă a bugurilor **BUG-04**, **BUG-08**, **BUG-01** din `validation/bug_audit/RAPORT_AUDIT.md`,
fără a atinge celelalte 8 buguri, fără PROD/Supabase TEST/Docker și fără migrații reale.

Mediu local izolat de reproducere: Python 3.11 + PostgreSQL 15 local pe `127.0.0.1:55432`
(`/tmp/pgtest`, superuser `postgres`, TLS off, date fictive); Node 24 pentru BUG-08.

Commit-uri pe branch (în ordine):
1. `fde5bd7` — **BUG-04** fix.
2. `1dfd103` — **BUG-08** fix (producție: `mobile_app.js`, `mobile_ext.js`, `mobile_sw.js`).
3. `c1d03e6` — **BUG-08** corectare test de audit (commit separat, justificat).
4. `a61e580` — **BUG-01** fix + 3 teste suplimentare.

---

## BUG-04 — Pierderea datelor Cloud după restaurare (**Critic**)

### Cauza exactă
- `backend/entity_sync_api.py::_last_writer_is` (:132-141) + apelul de la `:158` (ștergere) și `:196`
  (actualizare) acceptă un `base_version` învechit **fără conflict** dacă versiunea curentă a rândului
  pe server a fost produsă de **același** `client_id`.
- `client_id` este persistat în tabela `settings` a bazei SQLite (`desktop/cloud_sync.py:220-226`), deci
  copia restaurată păstrează **același** id, dar rândurile revin la versiuni vechi.
- `cloud_sync.sync_all` trimite outbox-ul **înainte** de pull (`desktop/entity_sync.py:495-500`), deci
  prima editare de pe MAIN după restaurare pleacă cu `base_version` vechi, serverul o acceptă, iar
  modificările altui calculator făcute între copie și restaurare dispar **în tăcere**.

### Fișiere modificate
- `shared/cimitir_backup.py` (23 linii):
  * adăugat helper-ul `_reset_cloud_client_id(db_file)`;
  * apel după copierea bazei și re-baza-area căilor media.

### Soluția implementată
Imediat după restaurare, înainte de orice sync:
```python
conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('cloud_client_id', '')")
conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('restored_at', ?)", (now,))
```
La următoarea rulare a `cloud_sync.client_id()`, se generează un identificator **nou**. Serverul vede
un client care nu a mai editat rândul respectiv; `base_version` vechi → **conflict standard**, vizibil
în panoul „Conflicte", nu suprascriere silențioasă.

Dacă baza nu are tabela `settings`, o creăm (`CREATE TABLE IF NOT EXISTS`); operațiunea nu ridică
excepții (rollback-ul restaurării s-a încheiat deja).

### Rezultate teste
Server clasic uvicorn pe PostgreSQL temporar `bugaudit_<rand>`, 2 MAIN izolate.

```
validation/bug_audit/test_bug04_restore_sync_silent_overwrite.py
├── test_restored_main_does_not_silently_overwrite_other_client_changes    FAIL -> PASS
├── test_fix_simulation_new_client_id_after_restore_reports_conflict        PASS -> PASS
└── test_check_restore_does_not_resurrect_row_deleted_elsewhere             PASS -> PASS
```
**ÎNAINTE:** 1 FAIL, 2 PASS. **DUPĂ:** **3 PASS**.

### Riscuri rămase
- Serverul va vedea un `client_id` nou. Cel vechi rămâne în `sync_clients` și poate declanșa alerta
  „client offline" (`sync_api.check_offline_alerts`). *Documentat ca pas manual*: la fiecare
  restaurare, administratorul poate șterge intrarea veche din panoul Cloud Admin.
- Dacă aceeași copie e restaurată pe **două** calculatoare care sincronizează simultan, ambele devin
  clienți noi, deci nu mai apare problema.
- Scenariul de **export portabil → import pe alt MAIN conectat la același server clasic** (vezi S-5
  din audit) nu este atins — rămâne separat, cu un `cimitir_portable.py` ce curăță outbox-ul; dacă
  importul se face pe un MAIN conectat, aceleași reguli de audit se aplică.

### Verificări necesare pe Windows
- `W-A`: MAIN `.exe` asociat la Cloud → 💾 Copii de rezervă → Fă copie → Restaurează → repornește →
  verifică că asocierea Cloud mai funcționează (cheia DPAPI rămâne, licența rămâne) și că serverul
  raportează conflictul obișnuit la prima editare.

---

## BUG-08 — Pierderea modificărilor offline Mobile (**Critic**)

### Cauza exactă
- `backend/static/mobile_app.js:127` (varianta veche):
  `async function outbox(){return (await kvGet('outbox').catch(()=>null))||[];}`
  Orice eroare la citirea IndexedDB era „înghițită" și `outbox()` întorcea `[]`.
- Apelanți:
  * `:240` (salvarea unei fișe): `ob.push(op); await saveOutbox(ob);` scria `[op_nou]` peste coadă.
  * `:141` (reîncărcare după push în `syncNow`) + `saveOutbox(ob)` la `:148` scriau `[]` peste coadă.
  * `mobile_ext.js:31` (`deleteRecord`): același tipar.
- Rezultat: o eroare trecătoare IndexedDB (ex. „Connection to Indexed Database server lost" pe iPhone
  Safari) ștergea coada offline cu toate modificările netrimise.

### Fișiere modificate
- `backend/static/mobile_app.js` (3 linii modificate):
  * `outbox()` nu mai are `.catch(()=>null)`; propagă eroarea.
  * `form.onsubmit`-ul formularelor din `openRecordForm`: `try{...}catch(_e){toast(...)}finally{...}`
    informează utilizatorul clar. **Ciorna nu se șterge** (`kvDel(draftKey)` este în ramura de succes).
- `backend/static/mobile_ext.js` (4 linii): `deleteRecord` cu try/catch echivalent.
- `backend/static/mobile_sw.js` (1 linie): bump cache `v11 → v12`, pentru ca telefoanele să încarce
  fișierele JS actualizate.
- `validation/bug_audit/test_bug08_pwa_outbox_wiped_on_read_error.py` (2 linii): corecția testului
  de audit (vezi mai jos, commit separat).

### Soluția implementată
`outbox()` devine o funcție strictă: dacă `kvGet('outbox')` eșuează, eroarea urcă la apelant. În
producție, fiecare apelant are deja un wrapper try/catch (form.onsubmit + syncNow + deleteRecord),
așa că nu se mai ajunge la `saveOutbox([op_nou])` sau `saveOutbox([])`. Coada offline rămâne intactă;
utilizatorul vede un toast clar: „Nu s-a putut salva local (stocarea telefonului a răspuns cu
eroare). Ciorna a fost păstrată; reîncearcă peste câteva secunde."

Pentru `syncNow`, tiparul respectă comportamentul existent: dacă primul `outbox()` sau citirea
proaspătă (`:141`) eșuează, cerșerea cade în `catch`-ul exterior care marchează sincronizarea ca
„Sincronizare incompletă" — `saveOutbox` nu se mai execută.

Fotografiile (`mobile_photos.js`) folosesc chei separate și nu sunt afectate.

### Rezultate teste
```
validation/bug_audit/test_bug08_pwa_outbox_wiped_on_read_error.py
└── test_transient_indexeddb_error_does_not_wipe_offline_queue              FAIL -> PASS
```
**ÎNAINTE (producție veche + test corectat):** FAIL — `['m-1','m-2','m-3']` dispar la salvarea unei
fișe; `['m-2','m-3']` dispar la sincronizare.
**DUPĂ (producție fixată + test corectat):** PASS — coada rămâne intactă, scenariile se refuză cu
toast; nicio pierdere.

#### Corecție de test (commit separat `c1d03e6`), justificată
- Comportament real al producției (după remediere): `outbox()` propagă erorile, iar fiecare apelant
  are un try/catch care prinde eroarea și afișează un toast fără a mai apela `saveOutbox`.
- Comportament așteptat de test: „coada păstrează operațiunile vechi (sau salvarea e refuzată)".
- Eroarea testului original: `scenarioAdd` și `scenarioSync` inlineau fragmentele extrase prin regex
  **direct, fără wrapper try/catch**. Când producția respinge (comportament corect post-fix), scenariul
  respinge, IIFE respinge, Node 15+ iese cu cod 1, iar `assert p.returncode == 0` pica. Astfel, forma
  veche a testului era **imposibilă** să dea PASS, chiar și cu bugul remediat.
- Corecția: scenariile primesc `try{...}catch(_e){}` identic cu wrapper-ul producției. Nici o
  asserție, niciun regex de extragere nu se modifică.
- Demonstrație (vezi `git show c1d03e6` + verificarea de mai jos):
  * ÎNAINTE de fix-ul producției (`HEAD~1` pentru `mobile_app.js`/`mobile_ext.js`), testul corectat
    **FAIL** cu „la salvarea unei fișe lipsesc [m-1, m-2, m-3] (rămas [m-new])". Bug detectat.
  * DUPĂ fix-ul producției, testul corectat **PASS**.

### Riscuri rămase
- Utilizatorii trebuie să reîmprospăteze PWA-ul pentru a primi JS nou; cache-ul SW v12 asigură asta.
- Dacă eroarea IndexedDB este persistentă (dispozitivul e corupt), utilizatorul vede toast la fiecare
  acțiune — comportament corect: mai bine refuz vizibil decât pierdere tăcută.

### Verificări necesare pe telefon (P-A din audit, pas cu pas)
- **iPhone/Safari**: mod avion → salvează 3 fișe → deschide/închide Safari de mai multe ori →
  salvează a 4-a → „Stare sincronizare" trebuie să arate **toate 4** în coadă.
- **Android Chrome**: același scenariu, suplimentar cu blocarea permisiunii de stocare în Setări →
  trebuie să apară toast-ul de eroare, nu pierdere tăcută.
- **NU** declarăm testarea pe iPhone finalizată fără un dispozitiv real; `P-A` din audit rămâne în
  lista de verificări manuale.

---

## BUG-01 — Sincronizarea actelor de identitate (**Critic — securitate/confidențialitate**)

### Cauza exactă
- `desktop/media_sync.py::_create_triggers` (:62-73): triggerul AFTER INSERT/DELETE pe `documents`
  excludea doar `entity_type='cerere'`, nu și `doc_type IN ('carte_identitate','cerere_doc')`.
  Aceleași documente ajungeau în `cloud_sync_outbox` cu `action='upload'`.
- `desktop/media_sync.py::enqueue_existing` (:126): aceeași lipsă.
- Serverul clasic `backend/media_api.py::complete` (:83-129) acceptă orice `doc_type`, iar pull-ul
  distribuia copiile actelor de identitate pe toate MAIN conectate.
- Sync v2 (`desktop/sync_v2_docs.py:111-114`, `shared/cimitir_sync_v2_docs.py:23,76-77`) **le exclude
  corect** — cale alternativă, cu toate filtrele de acces. Decizia A1: `carte_identitate` și
  `cerere_doc` **NU** se sincronizează automat prin conducta clasică `media_sync`.

### Fișiere modificate
- `desktop/media_sync.py`:
  * `_create_triggers`: WHEN clause adaugă
    `AND COALESCE(NEW.doc_type,'') NOT IN ('carte_identitate','cerere_doc')` (INSERT și DELETE).
  * `enqueue_existing`: același filtru.
  * `ensure_tracking`: scoate din coadă operațiunile `upload` deja inserate de o versiune anterioară,
    pentru acte de identitate — migrare nedistructivă (documentele locale rămân intacte).
- `backend/media_api.py::complete`: apărare în profunzime — 422 cu `error='doc_type_not_allowed'`
  dacă un client vechi sau o unealtă externă lovește direct acest endpoint pentru cele două tipuri.
  Fișierele parțiale de upload se curăță imediat.
- `validation/bug_audit/test_bug01_extra_cases.py` (test nou de regresie, 3 teste).

### Soluția implementată
Trei nivele de apărare:
1. Trigger-ul SQLite pe MAIN nu mai pune în coadă documentele cu `doc_type` identitate.
2. `ensure_tracking` curăță retroactiv coada (fără a atinge fișierele locale sau pe cele deja urcate).
3. Serverul respinge cererile `/complete` cu 422 pentru aceste tipuri — insensibil la majuscule și
   la spații, acoperind tentative de evadare.

Documentele deja urcate pe server **NU** sunt șterse automat (politică: tratament separat, cu decizia
administratorului), așa cum cere audit-ul. Mecanismele Sync v2 rămân neatinse.

### Rezultate teste
```
validation/bug_audit/test_bug01_media_sync_identity_docs.py
└── test_identity_documents_are_not_queued_for_classic_upload              FAIL -> PASS
validation/bug_audit/test_bug01_extra_cases.py  (nou)
├── test_classic_queue_excludes_identity_docs                              PASS (post-fix)
├── test_ensure_tracking_cleans_already_queued_identity_docs               PASS (post-fix)
└── test_server_complete_rejects_identity_docs_payload_with_422            PASS (post-fix)
```

### Riscuri rămase
- Un MAIN vechi (fără fix) trimite în continuare `upload` prin conducta clasică; serverul nou îl
  refuză cu 422, operațiunea trece în stare `error` după 4 încercări, vizibilă în Jurnal. **Nicio
  pierdere locală.**
- Documentele deja urcate pe server rămân acolo până la o decizie manuală. *Recomandare*: Cloud Admin
  → listă documente → filtru `doc_type` → export, apoi ștergere controlată.

### Verificări necesare pe Windows și telefon
- **Windows**: după actualizarea MAIN, importarea unui `carte_identitate` prin fluxul normal nu
  trebuie să mai pună operațiunea în coada clasică (verificat din „Stare sincronizare documente").
- **Portal clasic (QR)**: S-2 din audit menționează că `backend/mobile_upload.py::353-354`
  reclasifică `carte_identitate` în `alt_document`. Ne-afectat: decizia S-2 rămâne separată; dacă
  totuși un client lovește direct `/complete` cu `doc_type` identitate, apărarea de la punctul 3
  răspunde 422.
- **Telefon (Sync v2)**: `carte_identitate` continuă să circule prin Sync v2 cu filtrele existente —
  nu am atins acel cod.

---

## Rezumat global (PASS / FAIL / SKIP)

### Testele de audit țintă (`validation/bug_audit/`), la final

```
BUG-01 (1 + 3 noi):  4 PASS
BUG-04 (3):          3 PASS
BUG-08 (1):          1 PASS
                     --------
TOTAL ȚINTĂ:         8 PASS, 0 FAIL, 0 SKIP
```

### Restul audit-ului (nerezolvat intenționat, conform cerinței „NU începe repararea celorlalte opt buguri")

```
BUG-02: 3 FAIL  (nerezolvat)
BUG-03: 1 FAIL
BUG-05: 3 FAIL
BUG-06: 1 FAIL
BUG-07: 1 FAIL
BUG-09: 1 FAIL
       -------
       10 FAIL (comportament așteptat; rămân pentru o fază ulterioară)
```

### Regresii pe suitele existente (verificare pe eșantion)

```
desktop/tests/test_etapa7_backup_restore.py           8 / 0 / 0
desktop/tests/test_cloud_sync_validation.py          21 / 0 / 0
desktop/tests/ -k media (fără PySide6)                2 / 0 / 6 (skip = fără PG)
backend/tests/test_etapa7_media.py                    0 / 0 / 13 (skip = fără PG/mediu live)
```
**Nicio regresie atribuibilă remedierilor.** Testele care au nevoie de PySide6 sau de PG live sunt
sărite, nu rupte; acestea rămân responsabilitatea CI-ului dedicat.

---

## Restricții respectate

- [x] Nu s-a accesat PROD.
- [x] Nu s-a accesat Supabase TEST.
- [x] Nu s-a modificat Docker-ul existent.
- [x] Nu s-au executat migrații reale.
- [x] Nu s-au modificat date reale (totul local, `/tmp/pgtest`, baze `bugaudit_<rand>` temporare).
- [x] Nu s-au șters backup-uri.
- [x] Nu s-a făcut merge în `main`.
- [x] Nu s-a folosit force-push.
- [x] Nu s-au introdus parole sau token-uri în cod.
- [x] Nu s-a început repararea celorlalte 8 buguri.

---

## Livrare

- **Branch**: `fix/audit-critical-bugs`
- **Commit-uri** (4 pe branch):
  * `fde5bd7` BUG-04
  * `1dfd103` BUG-08 (producție)
  * `c1d03e6` BUG-08 (test corectat, commit separat)
  * `a61e580` BUG-01 (producție + 3 teste suplimentare)
- **Pull Request**: deschide în GitHub via
  `https://github.com/paulwalker0727/v3/compare/feature/etapa10-stabilizare...fix/audit-critical-bugs?expand=1`

**După repararea celor trei buguri și publicarea branch-ului, dezvoltarea este oprită**, conform
instrucțiunii. Nu continuăm cu alte modificări fără aprobare.
