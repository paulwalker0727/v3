# CIMITIR — Audit Etapa 0 + execuție P0 (branch `audit/etapa-integrare-stabilizare`)

Bază: `origin/main` = `b66e4ac` (PR #43 integrat). Nu s-a atins `main`, PROD, Cloud sau baze reale. Teste: Linux, Python 3.11, PySide6 6.8 offscreen, SQLite temporar, date fictive.

## 0. Starea repository-ului
- `feature/main-desktop-integrity-backup` (4c343f8) **este integrat** în main (PR #42, merge eecad94).
- `fix/concesionar-duplicat-integrare-audit` **este integrat** (PR #43, b66e4ac): duplicat concesionar, interconectare Locuri/Concesionari/Contracte, crash QThread, protecție restaurare, media sync.
- ~48 branch-uri vechi rămân pe remote (multe deja integrate); `conflict_*` și `sync-v2-emergent-wip` sunt nerevizuite — recomandare: curățare manuală de către proprietar.
- Nu s-au reimplementat funcții existente: P0 (1.1, 1.2, 1.3) era deja livrat; în această sesiune a fost **reverificat** și completat doar ce rămăsese deschis în raportul PR #43 (riscurile 1 și 3).

## 1. Matrice de funcționalități
| # | Funcționalitate | Stare | Dovadă / observație |
|---|---|---|---|
| 1.1 | Concesionar duplicat la „Adaugă concesionar” din contract | **IMPLEMENTAT (reparat în PR #43)** | `desktop/tests/test_concesionar_duplicat.py` (6 teste) trec; cauza era dublu INSERT (dialog + contract). Duplicatele istorice nu sunt șterse/combinate; se văd în „Persoane duplicate”. |
| 1.2 | Legături Locuri ↔ Concesionari ↔ Contracte | **IMPLEMENTAT** | `entity_links.py`, `entity_links_ui.py`; `test_legaturi_loc_concesionar_contract.py` trece. Fără schemă nouă (asocierea e doar prin `contracte`). |
| 1.3 | Crash import Mobile (`test_ui_resume_button_only_when_safe_and_confirmed`) | **IMPLEMENTAT (PR #43) + EXTINS aici** | Reverificat: 40/40 rulări izolate fără abort (rc=0). PR #43 acoperea doar `ApiTask`; aici: toate celelalte `QThread` (sync auto, fereastră sync, profile cloud, integritate, duplicate) sunt urmărite de `thread_guard` și așteptate la ieșire. **Windows real: NEVERIFICAT.** |
| 2 | Sync v2 (Main↔Cloud↔Mobile): `locuri_de_veci, concesionari, contracte, inhumati, plati, amenajari_avize` | **PARȚIAL** | Protocol cu `op_id` idempotent, `base_version`, conflicte (MAIN câștigă / MOBILE → conflict deschis), PII doar cu `view_pii`. Poligoanele/geometria nu sunt în lista de entități sync v2. Documente/foto prin `media_sync` (outbox, gardă pentru acte de identitate). Verificare cu Cloud TEST: **NEEXECUTATĂ** (fără acreditări); PostgreSQL local indisponibil → 671 teste backend skip. |
| 3 | Generare contract (docx), avize/adeverințe, documente | **IMPLEMENTAT / de validat** | `contract_generator.py`, `fisa_pdf.py`, avize 028 (`test_etapa1_*`). Drepturi de vizualizare pe Mobile: în `cimitir_mobile_perms`. Regenerarea automată a documentelor emise nu a fost găsită, dar nu s-a auditat exhaustiv. |
| 4 | Hartă → informații loc / concesionari / contracte | **PARȚIAL** | Selecția pe hartă setează `selected_cod_loc` și precompletează Contracte; fișa din dreapta are tab Amenajări. Lipsește acțiune directă „Concesionari/Contracte ale locului selectat” de pe hartă (se poate refolosi `LinkedRecordsDialog`). Neimplementat în această sesiune. |
| 5 | Backup/restaurare/integritate | **IMPLEMENTAT (PR #42/#43)** | `test_restore_offline_protection.py`, `validation/*` trec (72 passed, 19 skipped, 1 xfail documentat BUG-09). **Windows Task Scheduler real: NEVERIFICAT.** Jurnalul `field_import_journal` nu e protejat la restaurare (risc deschis). |
| 6 | UX (dublu-click, liste fără dubluri, modificări nesalvate) | **PARȚIAL** | Gardă dublu-click în dialogul concesionar. Alte dialoguri neauditate; navigare cu modificări nesalvate neauditată. |
| 7 | Teste/livrare | **PARȚIAL** | Vezi §3. |

## 2. Modificări în această sesiune
1. `desktop/thread_guard.py` (nou) + `track()` în `cloud_sync_auto`, `cloud_sync_window`, `cloud_profiles_dialog`, `duplicate_people_window`, `integrity_window`; `field_worker.wait_for_tasks` așteaptă și aceste thread-uri; modul adăugat în `cimitir.spec`. Test: `desktop/tests/test_thread_guard.py` (ieșire cu thread în rulare, eliberare, acoperirea punctelor de pornire).
2. `tools/make_client_guide.py`: director de ieșire opțional `CIMITIR_GUIDE_OUT_DIR`; `test_client_guide.py` scrie în director temporar → nu mai modifică `desktop/docs/Ghid_Instalare_Cimitir.pdf` din Git la fiecare rulare.
Nicio modificare de schemă, migrare sau date.

## 3. Rezultate teste (rulate aici)
- `desktop/tests`: toate fișierele rulate câte unul; toate trec, cu excepția: `test_license`, `test_qr_labels`, `test_qr_public_data` (0 teste colectate, preexistent), `test_qr_scanning` (lipsește libzbar în mediu), `test_field_import_history.py` (se rulează din `desktop/`; acolo 40/40 pass pentru testul vizat).
- `validation/final_integration` + `validation/bug_audit`: 72 passed, 19 skipped, 1 xfailed.
- Dependențe instalate doar în mediul de test (nu în repo): PySide6, pytest-qt, qrcode, reportlab, PyMuPDF, pyinstaller, psycopg[binary], python-docx, segno.

## 4. Riscuri / limitări
- Windows (crash nativ, Task Scheduler/S4U) și telefon real: neverificate.
- Sync cu Cloud TEST: neexecutat; teste PostgreSQL skip.
- Etapele 2, 3, 4, 6 rămân parțiale; nu se declară terminate.
- Duplicatele istorice de concesionari există în bazele afectate; rezolvare manuală.
- Succesul testelor unitare ≠ validare în producție.

## 5. Plan recomandat (următoarele PR-uri, în ordine)
1. Hartă → `LinkedRecordsDialog` pentru locul selectat (refolosește `entity_links_ui`).
2. Audit sync v2 cu instanță TEST: poligoane, foto/documente, conflicte offline.
3. Jurnal `field_import_journal` în protecția la restaurare.
4. UX: gardă dublu-click și „modificări nesalvate” pe celelalte dialoguri.
