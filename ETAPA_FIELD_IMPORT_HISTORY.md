# MAIN — Istoric importuri din teren (`field_import_journal`)

Fereastră nouă în MAIN (bară de unelte: `📜 Istoric importuri teren`) peste jurnalul local **existent** `field_import_journal` (SQLite, registrul activ).
Nu există alt sistem de import; `field_import.py` și `field_inbox_client.py` sunt **nemodificate** (mecanismul crash-safe rămâne identic).
`CLOUD MIGRATION CREATED: NU` (nicio migrare PostgreSQL 020+; nicio schimbare de schemă locală nouă).

## Componente
- `desktop/field_import_history.py` (fără Qt): `list_history(filtru)`, `local_details()`, `navigation_target()`, `cloud_snapshot()`, `finalize_cloud()`, `check_resume()`, `resume_import()`, `redact()`.
- `desktop/field_import_history_window.py`: fereastra (listă + filtre + detalii + butoane). `ui_mainwindow.py`: acțiunea de toolbar. `cimitir.spec`: hiddenimports.
- Teste: `desktop/tests/test_field_import_history.py` (61 teste, fără rețea reală; simulatoarele din `test_field_import.py` / `test_field_inbox_main.py`).

## Listă și filtre (locale, fără internet)
Coloane: data/ora, `inbox_item_uid`, tip, cimitir/registru, `grave_uid` (derivat local din loc), `local_entity_uid`, stare (`PREPARING` | `LOCAL_COMMITTED` | `COMPLETE`), număr fișiere,
ultima eroare (doar cod, trecut prin `redact`), ultima încercare (`updated_at`). Filtre: Toate / COMPLETE / LOCAL_COMMITTED / PREPARING / Cu eroare (`last_error` nevid).

## Detalii
Local imediat: payload relevant (din entitatea locală / documentele importate), destinația, entitatea locală (prezentă/LIPSĂ), fișiere/media (cale relativă la media root, SHA-256 salvat, prezent/lipsă),
stare locală, ultima eroare. Stare cloud + payload cloud: cerute într-un thread (`field_worker.run_task`); orice eșec = „indisponibil”. Nu se cere și nu se afișează niciun URL semnat.

## Acțiuni
- **Finalizează în cloud** (LOCAL_COMMITTED): doar `ACCEPTED -> IMPORTED` (via `field_import._finalize_cloud`). Cloud deja IMPORTED -> jurnal COMPLETE fără apel `/imported`. Nu apelează `_commit_local` / `_download_all`.
  Idempotent (lock per item, COMPLETE = no-op). Cloud indisponibil / REJECTED / PENDING: nimic modificat local, doar `last_error` + `updated_at`.
- **Reia importul** (PREPARING): activ numai dacă `check_resume` reușește — registru corect (jurnal == registru activ), cimitir corect (item cloud), item `ACCEPTED`, fără conflict/țintă lipsă (`plan_item`),
  fișiere valide (`validate_files`), fără fișier media local cu același nume și alt SHA, staging fără conținut neașteptat. Altfel: nimic modificat, motivele sunt afișate. Reluarea folosește `field_import.import_item`.
  Dacă reluarea eșuează după verificări, rândul PREPARING (eliminat de `import_item` — nimic comis) este recreat cu codul erorii, ca să rămână vizibil.
- **Deschide în MAIN**: mecanismul existent `features.search_plot(cod_loc)` (ca în „Cereri online”); activ numai dacă entitatea locală există. Pentru deceased/work/permit/document/foto navighează la locul de veci.

## Offline
Deschiderea, lista, filtrele și detaliile nu fac HTTP. HTTP rulează doar în worker. Fără internet: stare cloud „indisponibil”, MAIN nu cade.

## Securitate
Fără parole/chei de serviciu; niciun URL semnat solicitat/persistat; texte afișate prin `redact()` (URL-uri, chei de legătură, `token=`/`secret=`). Fără ștergere cloud/Storage, retenție, Conflict Review, schimbări schemă Work.
