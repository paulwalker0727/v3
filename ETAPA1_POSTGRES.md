# Etapa 1 — PostgreSQL (motor online)

Serverul poate rula acum pe PostgreSQL, **fără a modifica desktopul**. SQLite rămâne motorul implicit (desktop, offline, teste).
Nu se migrează date (cele existente sunt demo). MongoDB rămâne neatins.

## Pornire

```bash
# 1. variabile (vezi config/cimitir-online-postgres.env.example)
export CIMITIR_DB_ENGINE=postgres
export DATABASE_URL='postgresql://<utilizator>:<parola>@<host>:5432/<baza>?sslmode=require'
export CIMITIR_MODE=online
# 2. backend (aici: supervisor rulează uvicorn server:app pe 8001; local: python -m uvicorn server:app --port 8000)
cd backend && python -m uvicorn server:app --host 0.0.0.0 --port 8001
```

Schema se creează/actualizează singură la pornire (`pg_migrations/NNN_*.sql`, evidență în `schema_migrations`).

## Configurare (implicit: SQLite, nimic activat automat)

| Variabilă | Valori | Implicit |
|---|---|---|
| `CIMITIR_DB_ENGINE` | `sqlite` \| `postgres` | `sqlite` |
| `DATABASE_URL` | connection string PostgreSQL | gol |

`DATABASE_URL` singur NU activează PostgreSQL; doar `CIMITIR_DB_ENGINE=postgres` o face. Dacă motorul e `postgres` și baza nu
răspunde, serverul NU trece pe SQLite (ar scrie datele în alt loc): pornește degradat, iar `/api/public/status` dă 503.

## Verificare conexiune: `scripts/check_postgres.py`

```bash
DATABASE_URL='postgresql://...' python scripts/check_postgres.py            # text
DATABASE_URL='postgresql://...' python scripts/check_postgres.py --json --strict
```

Raportează conexiunea, versiunea (minim 13), latența (conectare + interogări) și starea schemei (migrații aplicate/lipsă, tabele,
drept de creare). Doar citire; nu activează nimic. Nu afișează `DATABASE_URL`, parola, host-ul sau utilizatorul (nici în erori).
Ieșire: `0` OK, `1` eșec conectare/config/versiune, `2` (cu `--strict`) schemă incompletă.

## Verificare server


`GET /api/public/status` (public, fără secrete):

```json
{"server":"online","status":"ok","mode":"online",
 "database":{"engine":"postgres","connected":true,"schema_version":1,"latency_ms":0.8}}
```

Cu PostgreSQL indisponibil: HTTP 503, `"status":"degraded"`, `"connected":false`.

## Arhitectură

| Fișier | Rol |
|---|---|
| `backend/cimitir/postgres_store.py` | Aceeași interfață ca `shared/cimitir_database.py` (funcții, argumente, forme de răspuns) + migrații + health |
| `backend/cimitir/pg_compat.py` | Pool psycopg3 + conexiune cu API `sqlite3` (`?`, `Row`, `lastrowid`, `INSERT OR IGNORE/REPLACE`, `LIKE`→`ILIKE`, `BEGIN IMMEDIATE`) pentru codul care rulează SQL direct |
| `backend/cimitir/pg_migrations/001_initial.sql` | Schema de la zero |
| `backend/cimitir/store.py` | Facade: alege motorul după `CIMITIR_DB_ENGINE` |
| `shared/cimitir_db_router.py` | Modulele comune (auth, cereri, notificări, public) merg automat pe motorul activ |

Entitățile sincronizabile au `uid` (UUID text stabil) și `modified_at` (TIMESTAMPTZ, trigger la UPDATE).
Poligoanele (`points`, `baseline`) sunt `JSONB` (fără PostGIS).

## Teste

```bash
scripts/dev_postgres.sh start     # PostgreSQL local de dezvoltare (port 55432, auth trust, doar 127.0.0.1)
createdb -h 127.0.0.1 -p 55432 -U cimitir cimitir_test
export CIMITIR_TEST_DATABASE_URL=postgresql://cimitir@127.0.0.1:55432/cimitir_test   # baza se GOLEȘTE la fiecare test
cd backend && python -m pytest tests/test_postgres.py -q
```

Fără `CIMITIR_TEST_DATABASE_URL`, testele PostgreSQL care au nevoie de server sunt sărite.

## Etapa 3 — schema PostgreSQL (migrațiile 001 + 002)

`001_initial.sql` (22 tabele) + `002_extra_tables_fks_indexes.sql` (repetabilă): tabelele suplimentare din SQLite
(`ai_scan_history`, `qr_generations`, `cloud_sync_map/outbox/docs`), `created_ts` pe toate tabelele, FK noi
(`plati.contract_id`, `cereri_concesionare.contract_id`, `qr_generations.loc_id`), indexuri de căutare și funcția
`cimitir_reset_sequences()` (aliniază secvențele după import cu ID-uri explicite).
Total: **27 tabele**, ID-uri `IDENTITY BY DEFAULT` (acceptă ID explicit), `uid` stabil (text, UNIQUE) pe entitățile sincronizabile,
poligoane `JSONB` (fără PostGIS — nu există interogări spațiale în aplicație).
Fără FK pe `plati.cod_loc`/`contracte.cod_loc`/`cereri.cod_loc` (copii istorice care supraviețuiesc ștergerii locului) și
fără CHECK-uri pe date de business (ar respinge date vechi legitime). `documents` e polimorfică: orfanii se raportează la migrare.
Testul `test_postgres_schema_covers_every_sqlite_table_column_key_fk_and_index` compară schema PostgreSQL cu schema SQLite
REALĂ (inclusiv tabelele „lazy”): tabele, coloane, tipuri, chei primare, UNIQUE, FK (+ON DELETE) și indexuri.

## Etapa 4 — migrare SQLite → PostgreSQL multi-tenant: `scripts/migrate_sqlite_to_postgres.py`

Importul este limitat la UN tenant (organizație + cimitir) care există deja în PostgreSQL (migrările 008–013 aplicate).

```bash
# 1. dry-run (implicit; nu scrie nimic în PostgreSQL; produce MIGRATION_REPORT.md cu rânduri înainte/după pe tabele)
python scripts/migrate_sqlite_to_postgres.py --source shared_data/cimitir.db --organization-uid <ORG_UUID> --cemetery-uid <CEM_UUID>
# 2. abia apoi (după verdict GATA): încărcare într-o singură tranzacție + verificare, apoi COMMIT
DATABASE_URL=... python scripts/migrate_sqlite_to_postgres.py --source shared_data/cimitir.db --organization-uid <ORG_UUID> --cemetery-uid <CEM_UUID> --apply
```

* `--organization-uid` și `--cemetery-uid` sunt obligatorii; cimitirul trebuie să aparțină organizației. Toate rândurile încărcate primesc aceste valori;
  `settings.cemetery_id` devine `cemeteries.uid` (refuz dacă ar muta tenantul activ al unei baze cu alt cimitir care are date).
* Cimitirul țintă trebuie să fie gol; `--truncate-target` șterge DOAR rândurile cimitirului țintă (alte cimitire rămân neatinse). `users` (fără tenant) se importă
  doar dacă nu există utilizatori străini; apar și în `identities`/`organization_members`/`cemetery_permissions`. `cloud_sync_*` (starea MAIN) nu se importă.
* Proiectul Supabase de producție este blocat (nici dry-run, nici `--apply`).
* SQLite: deschis doar read-only (`immutable`), neschimbat (SHA-256 înainte/după în raport); media nu se ating.
* Se păstrează ID-urile, UID-urile, hash-urile parolelor (checksum), relațiile, data/ora (text original) și `audit_log`.
  UID lipsă ⇒ `uuid5(cemetery_uid:tabel:id)` (determinist, rularea repetată dă același rezultat).
* `--on-problem abort` (implicit) oprește la probleme blocante (orfani, duplicate, poligoane invalide, tabele/coloane nemapate…);
  `--on-problem fix` aplică corecții documentate rând cu rând în raport (referință orfană ⇒ NULL, rând fără părinte ⇒ sărit, poligon invalid ⇒ `[]`).
* `--apply` cere un cimitir țintă gol (sau `--truncate-target`) și confirmarea numelui bazei (interactiv sau `--yes --confirm-db NUME`).
* Coduri de ieșire: 0 OK · 1 eroare/confirmare · 2 argumente · 3 probleme blocante · 4 verificare eșuată (ROLLBACK) / sursă modificată.
* Raportul nu conține parole, hash-uri, valori din `settings`, nici date personale — doar numere, tabele/coloane și ID-uri.
