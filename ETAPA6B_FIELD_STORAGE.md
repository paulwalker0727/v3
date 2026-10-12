# Etapa 6B — fundația Storage media pentru Field Inbox (fără UI)

Telefon (4G/5G) -> HTTPS -> **FieldMediaStore** (`shared/cimitir_field_storage.py`) -> Supabase Storage PRIVAT (octeți) + `cp_storage_objects` (PostgreSQL: tenant, SHA-256, cale, status).
Nu importă în SQLite și nu face sync automat. Field Inbox va folosi ulterior: `PENDING -> MAIN verifică -> ACCEPT / REJECT -> IMPORTED`.

- Migrarea `015_field_storage.sql` (neschimbată; aplicată pe Supabase TEST): un singur tabel (RLS `cp_org_ok AND cp_cem_ok`, grant doar pentru `cimitir_cp_app`). Se aplică explicit, doar pe baze de test.
- Fluxul: `begin_upload` (declarație: uid, mime, size, sha256) -> `upload` (verifică mărime + SHA-256 + semnătura fișierului ÎNAINTE de Storage; scriere fără upsert; reluare = verificare hash a obiectului existent)
  -> `READY`; `download_url` = URL semnat <= 300 s, doar pentru `READY`; `delete`; `cleanup_orphans` (strict în prefixul `field/<org>/<cem>/`).
- Tipuri: PDF, JPEG, PNG; max 25 MiB. Calea se construiește doar pe server (`field/<org>/<cem>/<uid>`).
- Cheia secretă Storage rămâne pe server (SUPABASE_SECRET_KEY din mediu); clientul primește doar URL semnat.
- Teste: `backend/tests/test_etapa6b_field_storage.py` (offline, Supabase simulat) și `..._live.py` (Supabase TEST real, sărit implicit).

Backlog 6A (nebloc.): response-loss live la `staging_put_batch` / `media_put_chunk`; oprire server exact în `publish_flow_b`; pooling de conexiuni (~0,7 s/conexiune); timeout-uri HTTP de producție.
Backlog 6B: endpointuri HTTPS pentru telefon, upload pe bucăți/TUS pentru fișiere mari, politici Storage/RLS pe `storage.objects`, rate limiting.

## Fix code review 6B (HIGH) — migrarea `016_field_storage_cleanup.sql` (015 rămâne neschimbată)
1. **Separarea drepturilor delete/cleanup.** `cimitir_cp_app` pierde `DELETE` pe `cp_storage_objects`; ștergerea normală a unui owner = `UPDATE ... -> DELETED` sub RLS (întâi DB, apoi Storage; rămășița o reia `delete` sau o curăță cleanup-ul).
   Cleanup-ul e operație de serviciu cu DOUĂ roluri interne (NOLOGIN, NOSUPERUSER, NOBYPASSRLS, NOCREATEDB, NOCREATEROLE):
   `cimitir_storage_cleanup` = OWNER-ul celor 5 funcții SECURITY DEFINER (`cp_storage_cleanup_claim`, `_claim_object`, `_finish`, `_release`, `_scope`; search_path fix, fără SQL dinamic), cu drepturi minime: SELECT/INSERT + UPDATE pe 6 coloane la `cp_storage_objects`, SELECT(uid, organization_uid) la `cemeteries`, fără DELETE/TRUNCATE nicăieri, politici RLS înguste (nu poate actualiza READY/IMPORTED; INSERT doar CLEANING) și fără membri de login;
   `cimitir_storage_cleanup_exec` = „cheia” backend-ului: doar EXECUTE pe cele 4 funcții publice (PUBLIC/anon/authenticated/service_role/cimitir_cp_app fără EXECUTE). Login-ul worker-ului: `CREATE ROLE <login> LOGIN IN ROLE cimitir_storage_cleanup_exec` (parola în afara Git), URL în `CIMITIR_CP_CLEANUP_DATABASE_URL`; fără el `cleanup_orphans` refuză.
   Funcțiile primesc (organizație, cimitir, uid, token) — niciodată o cale; DB verifică perechea tenant, construiește calea și decide eligibilitatea.
2. **Cursa cleanup vs upload/finalizare.** `STAGING(PENDING/UPLOADED/FAILED) -> READY` și `STAGING -> CLEANING -> DELETED` sunt mutual exclusive prin compare-and-set în DB (`UPDATE ... WHERE status IN (...)` pe rând blocat + `FOR UPDATE SKIP LOCKED` la claim) și un trigger (`cp_storage_objects_guard`):
   `CLEANING` nu devine niciodată `READY`/`IMPORTED`, `READY`/`IMPORTED` nu pot fi revendicate, `CLEANING` îl pot atinge doar funcțiile (proprietarul tabelului), nu `cimitir_cp_app`. Upload-ul își ia un „lease” (refresh `updated_at`) înainte de Storage și finalizează cu CAS; dacă pierde, răspunde `Conflict`, nu `READY`.
   Claim cu token + lease (900 s): un alt worker nu poate relua un obiect revendicat; un worker căzut e reluat după expirarea lease-ului. Obiectele din Storage fără rând sunt revendicate prin inserarea atomică a unui rând-santinelă CLEANING (uid-ul rămâne „ars”). `IMPORTED` e doar rezervat (Field Inbox), nu e încă folosit.
- Teste: `backend/tests/test_etapa6b_cleanup_race_pg.py` (PostgreSQL local real, conexiuni/fire separate, `CIMITIR_TEST_PG_ADMIN_URL`); live (Supabase TEST) actualizat pentru rolul de cleanup. Aplicarea 016 pe Supabase TEST rămâne un pas explicit.
