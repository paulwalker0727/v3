# PRODUCTION_READINESS — validare completă end-to-end (Cloud Supabase)

Data: 2026-10-04 · Proiect: `paulwalker0727/final-v1` (Cimitir) · Mod: `CIMITIR_MODE=online`, `CIMITIR_DB_ENGINE=postgres`, Storage `supabase`, bucket `cimitir-private`.
Nicio cheie nu a fost rotită, parola PostgreSQL nu a fost schimbată, nu s-au migrat date, MongoDB nu a fost atins, nu s-au adăugat funcționalități. Secretele nu apar nicăieri în acest raport.

## Cum s-a testat (și ce NU s-a testat)
- Server real: FastAPI pe `127.0.0.1:8001` (supervisor) -> **PostgreSQL Supabase** + **Supabase Storage**. Fără mock.
- MAIN A / MAIN B / MAIN C: procese separate cu **SQLite propriu** care rulează **codul real de sincronizare al aplicației desktop** (`cloud_sync`, `entity_sync`, `media_sync`), **fără fereastra grafică**. Interfața PySide6 a fost acoperită doar de testele automate rulate headless (Qt offscreen), nu a fost folosită manual.
- Browser real (Playwright, prin agentul de testare): fluxul de login/schimbare parolă admin, 10/10.
- `/admin` și `/portal` sunt accesibile doar pe `127.0.0.1:8001` în acest pod; URL-ul de preview rutează extern doar `/api`.

## A. Tabel PASS / FAIL

| Componentă | Rezultat | Dovadă |
|---|---|---|
| POSTGRESQL CLOUD | **PASS** | Supabase pooler (nu localhost), PG 17.11, `ssl_in_use=True` pe client, 30/30 tabele, migrații 1–4, `/api/health` online, `/admin/system` = „PostgreSQL CLOUD” |
| PERSISTENȚĂ | **PASS** | Înregistrare TEST proprie + probă din aplicație -> restart supervisor -> ambele există; pornind de la „neconfirmat”, `/admin/system` devine verificat |
| SUPABASE STORAGE | **PASS** | driver `supabase`, ONLINE, cheia secretă nu apare în răspunsuri/loguri |
| BUCKET PRIVAT | **PASS** | `public=false`; acces anonim (URL public, authenticated, listare) refuzat |
| UPLOAD/DOWNLOAD | **PASS** | 22/22: upload în bucăți prin FastAPI, SHA256 greșit respins (422), bucăți lipsă 409, complet repetat = duplicate, download, ștergere |
| SHA256 | **PASS** | upload = download = metadate PostgreSQL = obiect din Storage; MIME și size corecte |
| MAIN A | **PASS** | creează loc, concesionar, contract, înhumat, plată; toate ajung pe server |
| MAIN B | **PASS** | primește totul (același UID, aceleași versiuni, FK corecte, zero duplicate) și modifică înapoi (5 entități) |
| BOOTSTRAP | **PASS** | baze locale curate A/B/C se inițializează; C primește datele existente și nu primește obiectele șterse |
| SYNC | **PASS** | 29/29 + 17/17 (A->B, B->A) |
| IDEMPOTENCY | **PASS** (după fix) | 21/21: concesionar, contract, înhumat, plată, update, delete, cursă 8x simultan |
| CONFLICT | **PASS** | conflict `version_mismatch` pe loc, concesionar, contract; fără last-write-wins; datele A și B păstrate; vizibil în MAIN și în `/admin/monitorizare`; după rezolvare A=B=server |
| OFFLINE | **PASS** | backend oprit: modificări, concesionar, contract, plată, înhumat, fotografie, document; aplicația nu se blochează; închidere/redeschidere offline fără pierderi; reconectare -> sync automat |
| OUTBOX | **PASS** | operațiile rămân în outbox cu aceleași `op_id`; ordinea părinte->copil; se golește doar după confirmare; `op_id` înregistrate în `sync_ops` |
| DOCUMENT CACHE | **PASS** | B descarcă prin FastAPI din Storage în `media_cache`, SHA256 corect, se redeschide offline din cache; control negativ: offline fără cache = indisponibil controlat |
| SOFT DELETE | **PASS** | tombstone: copia veche nu reapare; copia modificată offline = conflict `deleted_on_server`; MAIN nou nu primește obiectul șters |
| PORTAL PUBLIC | **PASS** | 38/38 + cerere→MAIN: hartă, căutare, fișă loc fără date private, înhumați doar cu publicare ON, cont cetățean, cerere + document în Storage, cod urmărire, rate limit, tip/semnătură/dimensiune respinse, izolare între cetățeni; MAIN primește cererea și documentul (SHA256 ok), serverul șterge datele personale și obiectul (ack) |
| AUTH | **PASS** | HTTP 26/26 + browser 10/10 |
| ADMIN | **PASS** | `/admin`, `/admin/cloud`, `/admin/system` blocate înainte de schimbarea parolei și fără sesiune; accesibile după; temporar șters la final |
| SECURITY | **PASS** (cu riscuri R1–R5) | 28/29 în run (singurul „FAIL” = `Secure` pe HTTP simplu, intenționat; `Secure` verificat cu `X-Forwarded-Proto: https`), RLS pe 30/30 tabele, `anon/authenticated` fără drepturi, REST Supabase refuzat |
| AUDIT | **PASS cu limitare** (R6) | 23/23: actor, acțiune, țintă, timestamp; fără parole/chei. Modificările de entități vin doar în `sync_ops` |
| BACKUP | **PASS** (cu observații R7) | `pg_dump` (format custom) prin aplicație, manifest SHA256 corect, `pg_restore` într-o bază TEMPORARĂ locală: 30/30 tabele identice; metadatele Storage sunt în dump |
| RESTART | **PASS** | restart: DB, Storage, login, sync, health, `/admin/system` ok; DB indisponibilă -> 503 controlat, fără date corupte; Storage indisponibil -> 503, document respins, fără orfani |
| TEST SUITES | **PASS** | desktop 215 passed / 0 skipped; backend 202 passed (fără live) + 56 passed / 6 skipped (live, pe mediu local); QA agent backend 32/33 |

## B. BUG-URI GĂSITE (8) și stare
1. **Cursă pe același `op_id`**: cereri simultane primeau `rejected: UniqueViolation` (datele erau corecte). **REPARAT** (`entity_sync_api.py`); retest 21/21 + agent QA.
2. **CSRF**: doar `/admin/system` și `/admin/cloud` verificau originea; `/admin/ui/*` accepta POST cross-origin (mitigat doar de `SameSite=Lax`). **REPARAT** (middleware `request_guard`, 403 pentru POST/PUT/PATCH/DELETE cross-origin pe `/admin` și `/api/admin`).
3. **Cookie fără `Secure`** pe HTTPS (admin și cetățean). **REPARAT**: `Secure` când conexiunea e HTTPS (`X-Forwarded-Proto`) sau `CIMITIR_COOKIE_SECURE=1`.
4. **`%00` în URL -> 500**. **REPARAT** (400).
5. **DB indisponibilă -> `500 Internal Server Error` simplu** pe sync/hartă/health. **REPARAT** (503 controlat + `Retry-After`; MAIN păstrează outbox-ul).
6. **Host, IP-uri și utilizator DB în `stderr`** (logger `psycopg.pool`). **REPARAT** (filtru; loguri: „PostgreSQL: conectare eșuată (password authentication failed)”). Parola nu a apărut niciodată.
7. **Cerere de cetățean cu Storage căzut răspundea „document salvat”**; documentul rămânea doar pe discul serverului, fără reîncercare. **REPARAT** (documentul e retras și raportat respins; la fel pe ruta admin, cu 503).
8. **`backend/.env.example` lipsea din repo** (testul `test_env_example...` eșua). **RECREAT** fără secrete.

Eșecuri ale testelor mele, nu ale aplicației (verificările corectate și rulate din nou): potrivirea conflictului în `/admin/monitorizare`, potrivirea documentului cererii în MAIN (după cale, nu după `media_id`, care e `NULL` local), documentele cu `acked=1` numărate ca orfane, regula „fără `db_host` în logul vechi”.

## C. TESTE CARE ÎNCĂ EȘUEAZĂ
Niciunul în ultima rulare. Cele 6 skipuri din suita live backend sunt `pytest.skip` condiționale din cod (de ex. contul de test `e11view` inexistent, login personal implicit dezactivat în producție, hartă demo goală); nu am rulat acea suită cu `-rs`, deci motivul exact pe fiecare nu e confirmat. Funcțiile respective (login personal, hartă, portal) sunt acoperite de testele mele. `moto`, `PyInstaller`, `pypdf`, `pyzbar` lipseau din mediu și au fost instalate, nu s-a sărit peste teste.

## D. RISCURI RĂMASE
- **R1 TLS nu e impus de server**: pe pooler o conexiune cu `sslmode=disable` este acceptată. Aplicația folosește `require`. Activați „Enforce SSL on incoming connections” în Supabase.
- **R2 Politica de parole**: la schimbare doar minim 8 caractere (la bootstrap 12), fără complexitate sau listă de parole comune.
- **R3 Logout nu invalidează tokenul**: JWT stateless de 12 h; un cookie copiat rămâne valid după logout.
- **R4 Rate limit pe IP**: folosește IP-ul văzut de uvicorn. În spatele unui proxy trebuie pornit cu `--proxy-headers --forwarded-allow-ips <ip-proxy>`, altfel toți cetățenii împart aceeași limită. Neverificat extern (portalul nu e rutat prin preview).
- **R5 Expunere**: preview-ul rutează extern doar `/api`; `/portal` și `/admin` cer domeniu HTTPS către FastAPI. `/api/sync/media/state` arată URL-ul proiectului Supabase clienților cu cheie de sync (nu e secret).
- **R6 Audit**: modificările de entități și conflictele nu sunt în `audit_log`, doar în `sync_ops`/`sync_conflicts` (fără valori vechi/noi).
- **R7 Backup**: `pg_dump` ia toată baza, inclusiv schemele Supabase `auth/vault/storage` (dump sensibil; pe un Postgres obișnuit dă 3 erori inofensive la extensia `vault`). Fișierele din Storage nu intră în backup (doar metadatele). Folderul e `/app/shared_data-backups`, în arborele proiectului. Restore doar manual (`pg_restore`).
- **R8 Teste pe cloud**: `pytest` din `backend/` cu `backend/.env` către cloud rotește cheia de sync și scrie date în baza reală (mi s-a întâmplat la prima rulare; curățat). Testele live rulează doar pe mediu local.
- **R9 Disc local**: `/app/shared_data` (`.jwt_secret`, copii locale de documente) trebuie să fie pe disc persistent la deploy.
- **R10 Mongo**: neatins (vezi E).

## E. Audit MongoDB (nimic modificat)
- `CIMITIR_ENABLE_MONGO` = nu; motorul configurat = `postgres`; `scripts/audit_mongo.py` cod de ieșire 0. Nicio conexiune Mongo în rulare.
- **18 fișiere** cu referințe: 1 modul legacy (`backend/cimitir/mongo_store.py`), 4 de aplicație (`cimitir/store.py` 13 linii, `system_admin.py`, `templates/access.html`, `shared/cimitir_config.py`), 4 config/dependențe (`backend/.env` `MONGO_URL`, `backend/.env.example`, `backend/requirements.txt` cu `motor`+`pymongo`, `desktop/web_requirements.txt`), 4 teste, 5 documentații.
- **PostgreSQL a înlocuit complet Mongo** pentru aplicație (server, sync, portal, admin, Storage).
- Eliminare sigură în etapa următoare, după confirmarea ta: da pentru aplicație. Excepție: serverul de licențe (FastAPI + MongoDB, din `desktop/docs/DEPLOY_LICENSE_SERVER.md`) nu este în acest repo și depinde de Mongo; confirmă dacă îl folosești. `mongod` rulează în pod, dar aplicația nu îl folosește.

## F. Curățare (confirmat)
Șterse: utilizatorul/administratorul TEST (**users = 0**, gata pentru `CREATE_FIRST_ADMIN.bat`), locuri, concesionari, contracte, înhumați, plăți, cereri, conturi de cetățean, documente, conflicte, `sync_ops`, tombstone-uri, `audit_log`, cheia de sync, setările create de teste, rezultatul probei de persistență (ștergerea lui e cerută; `/admin/system` va arăta „neconfirmat” până la o nouă probă), toate fișierele din Storage (bucket gol), nodurile MAIN, backup-urile și baza PostgreSQL locală temporară, fișierele cu secrete.
Rămase intenționat: schema (30 tabele), `schema_migrations` (4), `settings.cemetery_id` (nemodificat), configurația din `backend/.env`.
Starea finală e verificată față de snapshot-ul inițial: singura diferență este `settings` (2 -> 1, fără rezultatul probei de persistență).

## VERDICT
**NU ESTE GATA PENTRU UTILIZARE REALĂ**

Tot ce se poate testa din cod trece: nu există niciun test care eșuează și nicio componentă critică în FAIL. Verdictul e „NU” doar din cauza a ceea ce nu s-a putut verifica sau nu e configurat încă:
1. **Expunere publică**: portalul și panoul admin (`/portal`, `/admin`, `/api`) trebuie publicate pe domeniu HTTPS, cu `--proxy-headers --forwarded-allow-ips <ip-proxy>` și `CIMITIR_COOKIE_SECURE=1`. În preview nu sunt accesibile extern (R4, R5).
2. **Interfața MAIN (PySide6) nu a fost rulată interactiv**: fluxul A/B/offline a fost validat cu stack-ul real de sincronizare, fără GUI. Rulează un test real pe două calculatoare cu MAIN, o dată, după crearea administratorului real.
3. **Activează „Enforce SSL” în Supabase** (R1) și confirmă că `/admin/system` arată verificat după prima probă de persistență pe serverul final.

După acești 3 pași, nu mai rămâne niciun blocant cunoscut. R2, R3, R6, R7 și R9 sunt de planificat, nu blocante.

Scripturile și logurile de validare sunt în `/app/validation/` (fără secrete).
