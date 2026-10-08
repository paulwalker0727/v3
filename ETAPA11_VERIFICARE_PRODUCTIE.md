# Etapa 11 — Verificare producție și test de persistență (`/admin/system`)

## Ce s-a adăugat
Cardul **„Verificare producție”** în `/admin/system` (doar administratori) și 3 acțiuni noi. Cod: `backend/system_admin.py` (`classify_database`, `production_check`, `persistence_*`), `backend/server.py` (rute), `backend/templates/system.html`. Teste: `backend/tests/test_etapa11_prod_check.py` (23).

## Cum se clasifică baza (CIMITIR_DB_ENGINE=postgres NU este dovadă)
| Nivel | Condiție | Gata de producție? |
|---|---|---|
| **SQLITE · local** | motor SQLite | Nu |
| **POSTGRESQL LOCAL / DEZVOLTARE** | gazdă loopback (127.0.0.1, localhost, ::1), socket Unix, IP privat, nume fără punct (ex. serviciu docker) sau `.local/.internal/.svc`; port 55432 / nume `*_dev`, `*_test` sunt semnale suplimentare | Nu |
| **POSTGRESQL CLOUD · persistență neconfirmată** | gazdă publică (furnizor recunoscut: Supabase, Neon, AWS RDS, Azure, Google Cloud SQL, Render, Railway, Aiven, DigitalOcean etc., sau „necunoscut”) | Nu |
| **POSTGRESQL CLOUD · PERSISTENT CONFIRMAT** | cloud (ca mai sus) **și** test de persistență reușit pe aceeași bază (amprentă gazdă:port/bază) | **Da** |
| POSTGRESQL NECONFIGURAT | motor postgres, dar `DATABASE_URL` lipsește | Nu |

Avertismente suplimentare: TLS neobligatoriu (`sslmode` diferit de require/verify-*), nume/port de dezvoltare pe o gazdă cloud, port 6543 (pooler în mod tranzacție, incompatibil cu advisory locks de sesiune). Pagina nu afișează gazda, utilizatorul, parola sau URL-ul; doar furnizorul și clasa gazdei.
Un rezultat „persistent” vechi nu se aplică după schimbarea bazei (amprenta diferă); persistența singură nu face dintr-un SQLite/PG local o bază cloud.

## Test de persistență (sigur)
1. **Creează probă**: scrie o singură înregistrare tehnică în `settings` (cheie `_probe:persistence:active`, JSON cu marker `CIMITIR-PERSISTENCE-PROBE`, UUID, ora, identificatorul pornirii serverului și amprenta bazei) și o recitește. Nu atinge locuri, contracte, înhumați sau alte date reale. O singură probă activă.
2. **Repornești serverul** (ex. `START_SERVER.bat`, restart supervisor).
3. **Verifică după restart**: dacă serverul NU a fost repornit → „în așteptare”. Dacă a fost repornit și proba există → persistență confirmată, rezultatul se salvează (`_probe:persistence:result`) și **proba se șterge**. Dacă proba lipsește după restart, nu se confirmă nimic.
4. **Curăță proba**: șterge doar cheia `_probe:persistence:active` (cu verificarea markerului), fără a conta ca verificare.
Acțiunile sunt în jurnalul de audit. Rămâne în baza de date doar cheia `_probe:persistence:result` (starea verificării, fără date personale).
Notă: și un restart al serverului declanșat de hot-reload (modificare de cod) contează ca restart.

## Ce lipsește pentru a conecta PostgreSQL cloud real
1. **Un PostgreSQL gestionat** (ex. proiect Supabase) și `DATABASE_URL` cu `sslmode=require`. Pentru Supabase: conexiunea directă (port 5432) sau poolerul în mod **sesiune**; NU poolerul în mod tranzacție (6543): aplicația folosește `pg_advisory_lock`.
2. **Configurarea mediului de producție** (fișier `.env`/variabile ale serverului, nu repo): `CIMITIR_DB_ENGINE=postgres`, `DATABASE_URL=...`, `CIMITIR_MODE=online`. Parola se introduce doar acolo. Eu nu am credențialele și nu le-am cerut.
3. **Acces din rețea**: permiterea IP-ului serverului în allowlist-ul bazei și limita de conexiuni (poolul aplicației are implicit max. 20).
4. **Schema**: migrațiile 1–4 se aplică automat la pornire; verificare cu `CHECK_DATABASE.bat` (30 de tabele așteptate).
5. **Datele existente**: cele de aici sunt demo; dacă există date reale în SQLite, se migrează cu `scripts/migrate_sqlite_to_postgres.py` (după backup).
6. **`pg_dump` pe server** (pentru „Backup acum”), cu versiune ≥ cea a serverului PostgreSQL, și un folder de backup persistent.
7. **Storage documente persistent** (Etapa 7): `CIMITIR_STORAGE_DRIVER=s3|supabase` cu credențiale; driverul `local` (activ acum) scrie pe discul serverului și nu este persistent într-un pod recreat.
8. **JWT_SECRET** stabil (variabilă de mediu sau folder de date persistent), altfel sesiunile se invalidează la fiecare redeploy.
9. **Pașii de confirmare după conectare**: `/admin/system` → „PostgreSQL cloud · persistență neconfirmată” → Creează probă → repornește serverul → Verifică după restart → „GATA PENTRU PRODUCȚIE”; apoi testele complete (`backend -n 0`, `desktop`), `python scripts/audit_mongo.py` și abia apoi decizia de ștergere a Mongo.
