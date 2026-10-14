# MAIN — Import local din Field Inbox (ACCEPTED -> SQLite + media -> IMPORTED)

Flux: `Telefon -> Field Inbox PENDING -> operator MAIN -> ACCEPTED -> „Importă în MAIN” -> SQLite + media locală -> cloud IMPORTED`.
Importul rulează **numai** la acțiunea explicită a operatorului (fără import automat în fundal, fără sincronizare bidirecțională, fără ștergere în cloud).
Fără migrare PostgreSQL 020 (`MIGRATION 020 CREATED: NU`). Singura schimbare de schemă este **locală SQLite**, creată idempotent de `field_import.ensure_tables()`
(același model ca `cloud_sync.ensure_tables()` / `entity_sync.ensure_tracking()`).

## Componente
- `desktop/field_import.py` — logica (fără Qt): jurnal, plan/mapare, descărcare + verificare, commit SQLite, finalizare cloud, preview.
- `desktop/field_inbox_client.py` — `mark_imported()` (ACCEPTED -> IMPORTED, apelat doar de `field_import`) și `download_signed()` (URL semnat, fără autentificare, fără redirect).
- `desktop/field_inbox_window.py` — butoane: `Acceptă`, `Respinge`, `Acceptă și importă` (PENDING), `Importă în MAIN` (ACCEPTED); preview + confirmare; după succes lista trece pe IMPORTED.
- Teste: `desktop/tests/test_field_import.py` (local, fixtures), `backend/tests/test_field_inbox_import_live.py` (Supabase TEST, sărit implicit).

## Jurnal local `field_import_journal` (SQLite, în registrul activ)
`inbox_item_uid` (PRIMARY KEY = unic), `cemetery_uid`, `item_type`, `local_entity_uid`, `import_state` (`PREPARING` | `LOCAL_COMMITTED` | `COMPLETE`), `local_refs` (JSON: tabel/id entitate,
ids documente, căi media relative), `last_error` (doar cod), `created_at`, `updated_at`.
Coloanele `uid/version/updated_at` + index unic `uid` se adaugă (idempotent, exact ca `entity_sync`) pe `inhumati` și `amenajari_avize`; `locuri_de_veci.uid` exista deja.

## Pași (crash-safe)
1. item ACCEPTED (PENDING nu se importă direct) + cimitirul itemului == cimitirul registrului activ (binding-ul registrului sau setările `cemetery_uid`/`cloud_bound_cemetery`); altfel blocat cu
   „Elementul aparține registrului X. Deschide registrul corespunzător pentru import.” — fără comutare automată.
2. plan local fără scriere (grave/țintă existente, mapare, entitate existentă identică/conflict) + validare metadate fișiere (READY, mime JPEG/PNG/PDF, SHA-256, mărime).
3. jurnal `PREPARING` -> descărcare în staging (`<cache registru>/field_import_staging/<item>`) cu URL semnat obținut la momentul descărcării -> verificare mărime, SHA-256, semnătura fișierului.
4. o singură tranzacție SQLite `BEGIN IMMEDIATE`: re-validare plan, `os.replace` atomic al fișierelor (nume determinist `field_<file_uid>.<ext>`; nu suprascrie cu alt SHA), inserare entitate + `documents`,
   jurnal `LOCAL_COMMITTED` în **același commit**.
5. abia apoi `ACCEPTED -> IMPORTED` în cloud; jurnal `COMPLETE`. Dacă pasul 5 eșuează (offline / răspuns pierdut) datele locale rămân valide; reluarea face doar pasul 5 (nu redescarcă, nu reinserează).
   Dacă serverul e deja IMPORTED (răspuns pierdut) nu se mai apelează `/imported`.

Reluare: `PREPARING` (cădere înainte de commit) -> se reia de la descărcare (staging rescris, fișierele orfane se refolosesc prin nume determinist); `LOCAL_COMMITTED` -> doar finalizare cloud;
`COMPLETE` -> „deja importat”. `finalize_pending()` (apelat la deschiderea/reîmprospătarea ferestrei) finalizează doar statusul cloud al importurilor deja comise local.

## Mapare pe modelele reale (fără tabele/modele noi)
| Item | Destinație locală | Note |
|---|---|---|
| DECEASED | `inhumati` (uid = uid item, `id_loc` = grave) | câmpuri lipsă rămân `''` (ca `save_inhumat`); nicio dată inventată |
| WORK | `amenajari_avize` (`nr_aviz=''`, `lucrare`=descriere, `data_aviz`=data_inceput, `data_finalizare`=data_sfarsit, `stare='finalizata'` doar dacă există data_sfarsit, `observatii` + linia `Executant: …`) | `executant`/`data_inceput` nu au coloane dedicate în MAIN |
| PERMIT | `amenajari_avize` (nr_aviz, data_aviz, lucrare, observatii) | același nr_aviz pe același loc cu alt UID = conflict |
| DOCUMENT | `documents` (loc / înhumat / lucrare după `target_uid`, altfel loc) | doc_type păstrat; tip necunoscut -> `alt_document` + eticheta în descriere |
| GRAVE_PHOTO | `documents('loc', cod_loc, 'poza_loc')` | doar JPEG/PNG; nu creează loc |
| WORK_PHOTO | `documents('aviz', id, lucrare_inainte / lucrare_in_timpul / lucrare_dupa)` | etapa obligatorie; lucrarea (`target_uid`) trebuie să existe local |

Entitate cu același UID deja prezentă: identică -> tratată idempotent; diferită -> `ImportConflict` (datele locale rămân neatinse, itemul rămâne ACCEPTED).

## Securitate
MAIN nu primește/loghează parola PostgreSQL, `SUPABASE_SECRET_KEY`, service role sau credențiale de cleanup. URL-ul semnat trăiește doar într-o variabilă locală (nu se salvează, nu apare în jurnal/loguri/excepții;
mesajele sunt texte fixe). Numele fișierului din cloud nu intră niciodată în calea locală. Producția (`txuxkjgftopznvmooexq`) rămâne blocată.

## Neimplementat intenționat
import automat în fundal, sincronizare bidirecțională, ștergere/retenție în cloud, auto-merge de conflicte, plăți/contracte/concesionari remote.
