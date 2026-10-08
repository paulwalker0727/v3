# Etapa 6 — Desktop ONLINE + OFFLINE prin API (PostgreSQL = sursa principală)

```
Desktop PySide6 (SQLite local: cache + outbox + copie)  --HTTPS-->  FastAPI  -->  PostgreSQL
```
Desktopul **nu** se conectează niciodată direct la PostgreSQL. Etapa 7 NU a fost pornită.

## Ce s-a reutilizat (nu s-a rescris sincronizarea)
`desktop/cloud_sync.py` (cerere HTTPS cu cheia `X-Sync-Key`, `RetryLater`/`Rejected`, coada `cloud_sync_outbox`, `op_id`, `sync_all`),
`desktop/cloud_sync_auto.py` (fundal), `backend/sync_api.py` (autentificare, clienți, `sync_ops`). Au fost **adăugate**:

| Fișier | Rol |
|---|---|
| `shared/cimitir_entities.py` | specificația comună a entităților (coloane, tipuri, chei străine prin uid) |
| `desktop/entity_sync.py` | captarea modificărilor locale, trimitere pe entități, descărcare incrementală, conflicte |
| `desktop/cloud_conflicts_window.py` | dialogul „Conflicte" (alegi `server` sau `al meu`) |
| `backend/entity_sync_api.py` | `/api/sync/entities/{push,pull,conflicts,state}` |
| `backend/cimitir/pg_migrations/003_entity_sync.sql` | `version`, `change_seq` (triggere), `sync_tombstones`, `sync_conflicts`, `sync_ops.response` |

Entități sincronizate: `locuri_de_veci`, `concesionari`, `contracte`, `inhumati`, `plati` (cheile străine circulă ca uid-ul părintelui,
niciodată ca id numeric). Rămân locale (neschimbate): fotografii/documente, avize, ghiduri/rânduri, utilizatori, hartă publică, cereri.

## Comportament
**ONLINE** — modificările locale pleacă în ~30 s (cu „Sincronizează automat" bifat) sau la „Sincronizează acum" la `POST /api/sync/entities/push`; răspunsul serverului
(versiune nouă) actualizează cache-ul SQLite. Citirile importante vin de la API: `GET /api/sync/entities/pull` aduce incremental
(cursor `change_seq` per entitate) rândurile și ștergerile; UI-ul citește mereu din cache-ul SQLite proaspăt
(`entity_sync.refresh_from_server()` pentru o reîmprospătare imediată).

**OFFLINE** — aplicația lucrează normal pe SQLite. Triggerele SQLite (`cs_<tabel>_ai/au/ad`) pun fiecare INSERT/UPDATE/DELETE în
`cloud_sync_outbox` (persistent, supraviețuiește repornirii); o modificare repetată a aceluiași rând înlocuiește operațiunea în așteptare.
Bara MAIN arată `ONLINE/OFFLINE`, operațiile în coadă și conflictele.

**Reconectare** — outbox-ul pleacă automat, în ordinea dependențelor (părinți → copii; ștergeri invers), în loturi de 100. Operația confirmată
se elimină. Un părinte lipsă pe server (`retry: missing_parent`) rămâne în coadă și se reia.

**Idempotență** — fiecare operațiune are `op_id`; serverul memorează răspunsul în `sync_ops.response`: o reluare întoarce același răspuns
(`duplicate: true`), fără re-aplicare. Un conținut identic cu al serverului este „adoptat" fără a crește versiunea.

**Conflicte** — `base_version` = versiunea serverului pe care s-a bazat modificarea. Dacă rândul are altă versiune (alt calculator **sau panoul web**,
ambele versionate de trigger-ul PostgreSQL), serverul **nu suprascrie**: înregistrează în `sync_conflicts` (motiv, versiuni, datele ambelor părți),
întoarce `status: conflict` + starea serverului; MAIN păstrează operațiunea ca `conflict` și nu atinge datele locale. Motive:
`version_mismatch`, `already_exists`, `deleted_on_server`, `natural_key` (același `cod_loc` creat pe două calculatoare). Conflictele apar în
MAIN (☁ Sincronizare → ⚠ Conflicte) și în `/admin/monitorizare`. Operatorul alege explicit `server` (se aruncă modificarea locală) sau
`mine` (se retrimite peste versiunea curentă); serverul marchează conflictul rezolvat. Excepție sigură: dacă ultima versiune a rândului a fost
scrisă chiar de același client (confirmare pierdută pe drum), nu se raportează conflict cu el însuși.

## Configurare / dezactivare
Server: `CIMITIR_MODE=online`, `CIMITIR_DB_ENGINE=postgres`, `DATABASE_URL` (+ `python scripts/issue_sync_key.py`). MAIN: ☁ Sincronizare cloud →
adresă HTTPS + cheie. Bifa „Sincronizează registrul … prin server" (`cloud_entity_sync_enabled`) revine la regimul vechi (MAIN publică doar harta).
Pe un server SQLite (fără `entity_sync` în `/api/sync/ping`) captarea se oprește singură și nu se acumulează nimic în coadă.
Când registrul vine prin entități, `POST /api/sync/map` actualizează doar sectoarele (nu mai șterge/rescrie locuri și înhumați), iar
numele înhumaților apar în portalul public doar dacă operatorul a bifat publicarea (`publish_burials`, trimis de MAIN).

## Verificare (automată, date fictive „E6-…")
`backend/tests/test_etapa6_entity_sync.py` (12 teste, server real + 2 procese MAIN cu SQLite separate): date locale → PostgreSQL (FK prin uid),
descărcare pe al doilea calculator, offline→reconectare, idempotență pe `op_id`, conflict între două calculatoare (nimic suprascris, conflict
raportat), rezolvare `server`/`mine` (inclusiv modificare din panoul web), propagarea ștergerilor, confirmare pierdută, părinte lipsă, `cod_loc` duplicat,
înhumați ascunși în portal. Regresii rulate: `test_postgres.py` (aserțiunile de schemă actualizate la 3 migrații), `test_etapa2_sync.py` (rulat în
regimul „MAIN publică harta"), restul testelor backend (69 passed), desktop (206 passed; 6 eșecuri de ambalare/PyInstaller și 1 eroare de colectare
`test_qr_scanning` — fără legătură cu sincronizarea).

## Limite cunoscute (nu s-a ascuns nimic)
* Un MAIN secundar cu date locale deja existente, dar ne-sincronizate, le va trimite ca rânduri noi: concesionarii/contractele/plățile nu au cheie
  naturală, deci pot apărea duplicate (locurile se potrivesc după `cod_loc` și produc conflict `natural_key`). Recomandat: al doilea calculator pornește cu baza goală și descarcă.
* Cursorul `change_seq` este o secvență PostgreSQL: o tranzacție lungă care se comite după una mai nouă poate face ca o modificare să fie
  citită cu o sincronizare întârziată (nu se pierde la rând nou, dar poate lipsi până la următoarea modificare a aceluiași rând). Nu s-a testat la încărcare mare.
* Fotografiile/documentele locurilor, avizele și utilizatorii NU circulă pe entități (Etapa 6 acoperă registrul de bază).
* Interfața PySide6 s-a verificat doar programatic (offscreen): dialogul de conflicte, fereastra de sincronizare, `AutoSync`; nu pe Windows cu server real.
* TLS/HTTPS real, proxy și certificate nu s-au testat (clientul acceptă `http://` doar pentru localhost).
* Baza PostgreSQL de aici este de dezvoltare, locală podului (`scripts/dev_postgres.sh start`); se pierde la restart. Pentru producție: `DATABASE_URL` gestionat.
* CNP și date personale ale registrului ajung acum în PostgreSQL (necesar ca sursă principală): securizați serverul/baza (HTTPS, parole, backup).
