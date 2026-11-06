# CIMITIR — Raport P2: audit funcțional, integritate operațională, pregătire release

Branch: `audit/p2-integritate-operationala` (bază `main` @ `d2e4db4`, PR #42–#54 verificate: toate îmbinate în `main`).
Mediu: Linux, Python 3.11, SQLite. Doar baze **sintetice** (tmp). Nu s-au accesat date reale, PROD, Supabase sau backupuri reale.
Nu s-a făcut merge. Nu există PR deschis: branch-ul este împins, PR-ul se deschide manual.

**Verdict READY TO MERGE: NU (încă).** Motive: (1) totul e într-un singur commit mare (diff-ul `shared/cimitir_database.py` include reindentarea `init_db`; se citește cu `git diff -w`); (2) cele 5 teste backend care pică în rularea paralelă sunt preexistente pe `main`, dar rămân de investigat (vezi §8); (3) trei decizii de produs deschise (§Ambiguități); (4) nimic nu a fost validat pe Windows.

## 1. Stare implementare (IMPLEMENTAT / PARȚIAL / LIPSĂ / BUG)

| Zonă | Stare | Observații |
|---|---|---|
| Contract nou legat de persoană și loc | IMPLEMENTAT | `save_contract` leagă prin `concesionar_id` și `id_loc`/`cod_loc`. UI permite explicit contract fără loc (neschimbat). |
| Contracte succesive pe același loc | IMPLEMENTAT | Rămân independente (test). |
| Modificare contract fără alterarea istoricului | **LIPSĂ** | Nu există versiuni de contract; editarea suprascrie. În plus, dialogul reia datele titularului curent peste instantaneul `owner_*` al unui contract vechi (ambiguitate §4). |
| Contract expirat/prelungit/anulat/transferat | PARȚIAL | Expirare: raport + hartă. Prelungire/anulare/transfer: nu există operațiuni dedicate (nu am inventat reguli). |
| Plată legată de contractul corect | **PARȚIAL** | DB acceptă `contract_id`; interfața (PlatiWindow, panels) nu îl completează niciodată → chitanța nu afișează plătitorul. Decizie de produs necesară. |
| Plăți duble (dublu-click/retrimitere) | BUG CONFIRMAT → REPARAT | UI golește suma după salvare (protecție incidentală); în DB nu exista protecție. Acum retrimiterea aceleiași chitanțe nenule este idempotentă. Plăți fără număr de chitanță identice NU sunt deduplicate (pot fi legitime). |
| Validare plată | BUG CONFIRMAT → REPARAT | Se acceptau loc inexistent, sumă negativă/NaN (NaN ajungea NULL), contract al altui loc. |
| Plăți parțiale / sold | PARȚIAL | Se păstrează plăți multiple și `total_plati` (sumă). Nu există sold datorat: nu există preț/tarif în model → nu am inventat. |
| Dovezi de plată | IMPLEMENTAT | Chitanță PDF generată la cerere; nu se înregistrează în baza de date (vezi §Documente). |
| Înhumări: asociere prin ID, mai mulți în același loc, istoric | IMPLEMENTAT | Testat. |
| Editare înhumat fără duplicare | IMPLEMENTAT | Dar vezi bug scanare AI mai jos. |
| Ștergere loc cu istoric | **BUG CONFIRMAT → REPARAT** | Cascada ștergea fotografii + avize; înhumații/contractele rămâneau fără loc (`id_loc` NULL). Acum refuzat cu mesaj (și pre-verificare în hartă/UI). |
| Ștergere contract cu plăți/înhumați; concesionar cu contracte | **BUG CONFIRMAT → REPARAT** | Legăturile se pierdeau silențios (`contract_id` dangling, `concesionar_id` NULL). Acum refuzat. |
| Actualizare înhumat din scanare AI | **BUG CONFIRMAT → REPARAT** | `save_inhumat` suprascria cu `""` toate câmpurile absente: se pierdeau data înhumării, deshumării, profesia, religia, nr. adeverință și observațiile. Acum se actualizează doar câmpurile primite; scanarea trimite doar valorile extrase și păstrează observațiile. |
| Editare contract | **BUG CONFIRMAT → REPARAT** | Dialogul nu trimite `valabilitate`/`extra_data` → se goleau la fiecare editare. |
| Update pe ID inexistent | BUG CONFIRMAT → REPARAT | `save_contract/save_concesionar/save_inhumat` întorceau „succes”; acum `None` + motiv. |
| Migrații SQLite | **BUG CONFIRMAT → REPARAT** | Vezi §2. |
| Backup/restaurare | IMPLEMENTAT | Existent, solid. Testat suplimentar la disc plin, acces refuzat, întrerupere, backup cu schemă veche. |
| Erori de rețea la sincronizare | **BUG CONFIRMAT → REPARAT** | Vezi §3. |
| PDF cu markup în text | BUG CONFIRMAT → REPARAT (desktop) | `backend/cimitir/pdf_reports.py` are aceeași problemă — **nereparat**. |
| Jurnal de audit în MAIN | **LIPSĂ → IMPLEMENTAT** | Doar portalul scria în `audit_log`. MAIN scrie acum create/update/delete pentru plată, contract, înhumat, șters concesionar/loc (țintă `tip:id`, detalii redactate F-02; nu scrie nimic dacă nu e autentificat un utilizator MAIN). |
| Permisiuni MAIN | **AMBIGUITATE** | Vezi §4. |
| Instalare/actualizare Windows | PARȚIAL (neverificat pe Windows) | Vezi §6. |

## 2. Migrații (§4 din cerință)

Defecte confirmate în `init_db` (`shared/cimitir_database.py`):
- `_add_column_if_missing` înghițea **orice** `OperationalError` (disc plin, bază blocată) → schemă pe jumătate migrată, fără semnal. Test: disc plin simulat la ALTER-ul 1/2/4 — pe `main` testul pică (eroarea e ignorată), după fix excepția ajunge la apelant și schema/datele rămân identice.
- Nicio tranzacție: `init_db` rulează acum într-un `BEGIN IMMEDIATE … COMMIT/ROLLBACK`.
- Indexurile ignorau orice eroare; acum doar `IntegrityError` (date duplicate), nu `OperationalError`.
- `plati.contract_id` lipsea din lista de migrare (o bază veche fără coloană făcea `add_plata` să eșueze).
- Copie de siguranță consistentă (`conn.backup`) în `<folder_baza>/pre-migrare/` ÎNAINTE de o migrare care modifică o bază existentă (ultimele 5). Dacă copia eșuează, migrarea nu pornește. `main.py` afișează un mesaj clar și iese cu cod 1.

Scheme testate (toate sintetice; rânduri fictive; `desktop/tests/fixtures/historic_schemas/`):

| Schemă | Origine | Coloane de migrat | Integritate | FK | Înregistrări înainte → după | Idempotent |
|---|---|---|---|---|---|---|
| `v1_20b7e92` | DDL produs de `init_db()` din commitul `20b7e92` | amenajari_avize.* | ok | 0 | identic (settings +1: `cemetery_id`) | da |
| `v2_53ceda3` | idem, commit `53ceda3` | 2 coloane | ok | 0 | identic | da |
| `v3_current_3f05ae5` | commit `3f05ae5` (≈ curent) | 0 | ok | 0 | identic | da |
| `v0_legacy_minimal_ipotetic` | **IPOTETICĂ**, scrisă manual (tabele de bază fără ALTER-urile din cod) | ~60 coloane + tabele | ok | 0 | identic | da |

Limite: istoricul Git al fișierului de bază începe la 5 versiuni; schemele v1/v2 sunt deja rezultatul migrării proprii a acelor versiuni, deci nu dovedesc ce exista pe discul unui utilizator real. `v0` NU este o versiune livrată dovedită. Numărul de rânduri per tabel apare la rularea `pytest -s desktop/tests/test_p2_migratii_istorice.py`.

Nu s-a testat: baza ocupată de alt proces real în timpul actualizării MAIN (testat doar cu un al doilea proces SQLite), disc plin real, antivirus/OneDrive pe Windows. Restaurare întreruptă prin kill brutal al procesului: netestabil aici; după `KeyboardInterrupt`/eroare starea veche se reface (testat), iar folderul de siguranță nu se șterge niciodată.

## 3. Erori și întreruperi (§5)

Simulate (subproces, simulator de server, fără rețea): timeout, conexiune pierdută DUPĂ aplicarea pe server, JSON trunchiat (push și pull), răspuns fără rezultate.
- `entity_sync.flush_entities`: JSON ilizibil ridica `ValueError` necontrolat; răspuns `{}` era raportat ca **reușit** deși operațiunile nu fuseseră confirmate. Reparat: `_json_body` + operațiunile neconfirmate rămân `pending` cu același `op_id`; lotul este raportat ca nereușit.
- Retrimiterea după „răspuns pierdut” nu dublează înregistrarea (server la versiunea 1).
- Backup: disc plin (`ENOSPC`) și acces refuzat la copierea mediei → `result: failed` în jurnal, fără copie parțială, sursă neatinsă. Fișier media lipsă din disc: backup reușește, înregistrarea rămâne în DB și este semnalată de verificarea de integritate (DOC004).
- Restaurare cu disc plin / întreruptă: starea anterioară este refăcută integral.

## 4. Ambiguități care cer aprobare (NU modificate)

1. **Plăți fără contract.** UI nu setează `contract_id`; chitanța iese cu plătitorul necompletat. Variante: selector de contract în fereastra Plăți, sau auto-legare când locul are un singur contract. Nu am ales eu.
2. **Rol „user” (doar consultare).** `auth.ROLE_USER` = doar vizualizare, dar `UserModeWindow` (docstring: „USER / OPERATOR”) permite adăugare înhumați, contracte (inclusiv ștergere) și plăți (inclusiv ștergere) fără verificarea drepturilor. Este intenția sau o scurgere de drepturi? Dacă e scurgere, ștergerea trebuie condiționată de drept.
3. **Editare contract vechi** rescrie `owner_*` cu datele curente ale concesionarului (instantaneu istoric alterat). Păstrarea instantaneului sau versionarea contractelor = decizie de produs.
4. Numerotare chitanțe: `chitanta_nr` nu este unic; nu am impus unicitate. Contractul Word: la „încheiat astăzi” se folosește data contractului (formulare neschimbată).
5. Ștergerea unei plăți rămâne permisă (acum jurnalizată cu sumă și chitanță). Dacă plățile istorice trebuie să fie imuabile, e nevoie de stornare în loc de ștergere.

## 5. Documente (§3)

Verificate: contract Word (toate placeholderele din șablon sunt mapate, diacritice ț ș ă î â păstrate, căi cu spații/diacritice, repetarea generării nu modifică baza), PDF contract și chitanță (diacritice, date `zz.ll.aaaa`, sume), PDF cu text ostil. Rămân:
- Documentele generate (contract Word, PDF-uri) **nu sunt înregistrate în `documents`** și nu au numerotare proprie: „generat dar neînregistrat” — LIPSĂ (nu am adăugat; schimbă fluxul).
- `generate_contract` suprascrie fișierul de destinație dacă are același nume (dialogul Qt cere confirmare).
- Neverificate: Word pentru alte tipuri de documente, export Word al rapoartelor, tipărire efectivă (imprimantă).

## 6. Windows (§8) — netestat aici

Citit: `desktop/installer/cimitir_setup.iss` (instalare în Program Files, date în `%LOCALAPPDATA%\Cimitir` și `%APPDATA%\CimitirDesktop`, dezinstalarea nu șterge datele — bun), `cimitir.spec` (pathex include `shared`, deci noul modul `cimitir_pdf_safe` este inclus), `updater.py` (doar deschide pagina release; nu face backup automat — acoperit parțial de copia `pre-migrare`). De validat manual pe PC: `Cimitir.exe` pornit după actualizare peste o versiune veche cu date, căi cu spații/diacritice pentru `CIMITIR_DATA_DIR`, Task Scheduler pentru backup, QR/Word/PDF în EXE, folder pe OneDrive (avertismentul există în cod).

## 7. Performanță (§7)

`scripts/p2_benchmark.py`, baze sintetice (locuri = concesionari = contracte = plăți = N; înhumați 1,5·N), mediană ms:

| Operațiune | 1.000 | 10.000 | 50.000 |
|---|---|---|---|
| init_db (pornire) | 2,8 | 3,5 | 3,1 |
| listă locuri | 4,8 | 100 | 360 |
| listă concesionari | 3,7 | 39 | 216 |
| căutare înhumat după nume / CNP | 1,5 / 0,8 | 2,8 / 2,2 | 9,0 / 9,4 |
| căutare contract după număr | 0,4 | 0,7 | 0,6 |
| încărcare hartă (poligoane) | 3,5 | 33 | 206 |
| istoric plăți loc | 0,4 | 1,1 | 4,1 |
| contracte care expiră | 7 | 65 | 473 |
| fișă PDF loc | 50 | 99 | 504 |
| contract PDF | 14 | 13 | 29 |
| raport PDF contracte expirate | 71 | 911 | 5.956 |

Fereastra Concesionari: ~385 ms la 10.000 și ~2 s / ~570 MB RSS la 50.000 (offscreen). Pentru dimensiuni realiste (≤ 10.000) nu există blocaje măsurate → **nicio optimizare aplicată**. Singurul punct care scalează liniar vizibil este raportul PDF cu toate contractele expirate. Nu am măsurat: pornirea reală a ferestrei Locuri (necesită fereastra principală), deschiderea istoricului în UI, scurgeri de memorie pe sesiuni lungi.

## 8. Teste

Fișiere noi (`desktop/tests/`): `test_p2_migratii_istorice.py` (17), `test_p2_contracte_plati_inhumati.py` (34), `test_p2_erori_backup_restaurare.py` (7), `test_p2_sync_erori_client.py` (5), `test_p2_documente.py` (13).
- `desktop/tests` complet (rulare înainte de commit): **790 PASS, 14 SKIP, 0 FAIL**.
- Testele noi pică pe `main` pentru defectele de mai sus (verificat prin `git stash` al codului de producție pentru migrații și integritate).
- `backend/tests` (`-n 4`), branch: 965 PASS, 671 SKIP, 5 FAIL. `main` (`d2e4db4`, aceeași comandă, copie curată): 964 PASS, 671 SKIP, 6 FAIL — aceleași 5 teste din `test_etapa1_audit.py` (`public_*`, `web_request_decisions`) + `test_etapa3_requests::test_concurrent_requests_same_plot_never_double_assign` (instabil). Deci cele 5 sunt **preexistente** (trec când fișierul rulează singur, 25/25), nu regresii ale acestui branch; cauza nu a fost investigată. O primă variantă a jurnalului de audit a provocat o regresie reală (`test_attachments`: istoricul web afișa evenimente scrise de MAIN); a fost reparată activând jurnalul MAIN doar după autentificarea în MAIN.
- Neexecutate: teste Windows, imprimantă, Supabase/PostgreSQL real, Cloud TEST real.

## 9. Fișiere modificate

`shared/cimitir_database.py` (init_db, migrații, protecții, audit), `desktop/main.py`, `desktop/entity_sync.py`, `desktop/ai_scanare/{scan_window,duplicate_check}.py`, `desktop/{locuri_window,concesionari_window,contracts_window,map_editor_actions,map_editor_tools,ui_mainwindow}.py`, `desktop/{pdf_reports,fisa_pdf}.py`, `shared/cimitir_pdf_safe.py` (nou), `scripts/p2_benchmark.py` (nou), teste + fixture-uri (noi).

## 10. Riscuri pentru datele reale

- Prima pornire după actualizare creează `pre-migrare/*.bak` lângă baza reală (spațiu = mărimea bazei; verificați spațiul liber). Dacă nu se poate crea, aplicația refuză să migreze și afișează mesaj.
- Ștergerea locurilor/contractelor/concesionarilor cu istoric este acum refuzată — operatorii vor primi mesaje noi; procedura de curățare trebuie comunicată.
- `add_plata` refuză acum plăți pe loc inexistent/sumă ≤ 0: fluxuri externe (import/sincronizare) care apelau `add_plata` direct cu astfel de date vor primi `None`. Căile de sincronizare prin SQL direct nu sunt afectate.
- Backendul/portalul nu a fost modificat; PDF-ul de acolo are încă problema markup-ului.
