# Etapa 6B — fundația Storage media pentru Field Inbox (fără UI)

Telefon (4G/5G) -> HTTPS -> **FieldMediaStore** (`shared/cimitir_field_storage.py`) -> Supabase Storage PRIVAT (octeți) + `cp_storage_objects` (PostgreSQL: tenant, SHA-256, cale, status).
Nu importă în SQLite și nu face sync automat. Field Inbox va folosi ulterior: `PENDING -> MAIN verifică -> ACCEPT / REJECT -> IMPORTED`.

- Migrarea `015_field_storage.sql`: un singur tabel (RLS `cp_org_ok AND cp_cem_ok`, grant doar pentru `cimitir_cp_app`). Se aplică explicit, doar pe baze de test.
- Fluxul: `begin_upload` (declarație: uid, mime, size, sha256) -> `upload` (verifică mărime + SHA-256 + semnătura fișierului ÎNAINTE de Storage; scriere fără upsert; reluare = verificare hash a obiectului existent)
  -> `READY`; `download_url` = URL semnat <= 300 s, doar pentru `READY`; `delete`; `cleanup_orphans` (strict în prefixul `field/<org>/<cem>/`).
- Tipuri: PDF, JPEG, PNG; max 25 MiB. Calea se construiește doar pe server (`field/<org>/<cem>/<uid>`).
- Cheia secretă Storage rămâne pe server (SUPABASE_SECRET_KEY din mediu); clientul primește doar URL semnat.
- Teste: `backend/tests/test_etapa6b_field_storage.py` (offline, Supabase simulat) și `..._live.py` (Supabase TEST real, sărit implicit).

Backlog 6A (nebloc.): response-loss live la `staging_put_batch` / `media_put_chunk`; oprire server exact în `publish_flow_b`; pooling de conexiuni (~0,7 s/conexiune); timeout-uri HTTP de producție.
Backlog 6B: endpointuri HTTPS pentru telefon, upload pe bucăți/TUS pentru fișiere mari, politici Storage/RLS pe `storage.objects`, rate limiting.
