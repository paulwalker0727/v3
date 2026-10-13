# Etapa 6C — Field Inbox v1 (schemă + service layer + API + securitate + teste; fără UI)

Telefon (4G/5G) -> HTTPS -> FastAPI (`shared/cimitir_field_inbox_http.py`) -> `FieldInboxService` (`shared/cimitir_field_inbox.py`) -> PostgreSQL (`cp_field_inbox_items`, RLS) + Storage privat (6B).
Nimic din teren nu modifică SQLite-ul MAIN: itemul rămâne `PENDING` în cloud până îl aprobă operatorul MAIN. Importul în SQLite, UI-ul MAIN/telefon, notificările și cron-ul de cleanup NU fac parte din această etapă.

## Ce se reutilizează (nimic duplicat)
- `cp_storage_objects` + `FieldMediaStore` + `SupabaseStorage` (6B, 015–017): upload, SHA-256, 25 MiB, PDF/JPEG/PNG, URL semnat <= 300 s, cleanup. Un item are 0..N obiecte 6B prin `cp_field_inbox_attachments`.
- Autentificarea `Binding <cheie>` (device × cimitir) și `PostgresControlPlane.authenticate` (contextul RLS `cp.*`), rolul `cimitir_cp_app`, funcțiile `cp_org_ok/cp_cem_ok/cp_fill_org` (014), contractul `wire.API` (`/api/v1`).
- Tabelele canonice `devices` / `device_cemetery_bindings` (tipul dispozitivului: MAIN = operator, MOBILE = telefon). Nu s-a creat registru/tenant/auth nou. `portalul LAN` (backend/server.py, SQLite local) nu e atins.

## Migrarea 018 (`018_field_inbox.sql`, 001–017 neschimbate)
- `cp_field_inbox_items`: uid, organization_uid, cemetery_uid, item_type, work_stage (BEFORE/DURING/AFTER, doar WORK_PHOTO), target_uid, grave_uid, payload jsonb (<= 64 KiB), status, idempotency_key, request_hash, created_at/by, reviewed_at/by, imported_at/by, rejection_reason, updated_at.
- Statusuri: `PENDING`, `ACCEPTED`, `REJECTED`, `IMPORTED` — fără stare tehnică intermediară. Tranziții (trigger în DB + `UPDATE ... WHERE status=<de la>` pe rând blocat): PENDING->ACCEPTED, PENDING->REJECTED, ACCEPTED->IMPORTED. Orice altceva (REJECTED->IMPORTED, IMPORTED->PENDING, ...) e respins de DB. Conținutul e imutabil; aplicația nu are DELETE/TRUNCATE și UPDATE doar pe coloanele de status/review.
- Doar o legătură MAIN activă din același cimitir poate schimba statusul (verificat în trigger); telefonul MOBILE nu se poate auto-aproba nici direct în SQL.
- `cp_field_inbox_attachments`: (cemetery, item, object) cu FK compuse spre item și `cp_storage_objects`; un obiect aparține unui singur item; atașarea cere item PENDING (FOR SHARE, serializat față de accept/reject) și obiect READY în același tenant.
- `cp_storage_objects_inbox_guard`: un obiect legat de un item nerespins nu poate trece READY->DELETED. Cleanup-ul 6B nu e afectat (nu atinge READY).
- La `IMPORTED`, obiectele READY ale itemului trec în `IMPORTED` (stare rezervată de 016).
- RLS `cp_org_ok AND cp_cem_ok` pe ambele tabele, grant doar pentru `cimitir_cp_app`.

## Idempotență
`UNIQUE (organization_uid, cemetery_uid, idempotency_key)` + `INSERT ... ON CONFLICT DO NOTHING RETURNING`; cheia refolosită cu conținut diferit (`request_hash`) = 409 `IDEMPOTENCY_CONFLICT`. Fișierele: `file_uid` ales de client (aceeași reluare 6B). accept/reject/imported repetate = replay fără efect (`changed:false`).

## API (`/api/v1/field-inbox`, `Authorization: Binding <cheie>`)
`POST /items` (201 nou / 200 replay) · `POST /items/{uid}/files` (corp = octeții, `Content-Type`=mime, `X-File-Uid`, `X-File-Name`, opțional `X-File-Sha256`) · `GET /items?status=&item_type=&limit=&offset=` · `GET /items/{uid}` · `GET /items/{uid}/files/{file_uid}/url?ttl=` (<= 300 s; item PENDING/ACCEPTED; obiect READY) · `POST /items/{uid}/accept|reject|imported` (doar MAIN).
Telefonul vede/atașează doar itemurile proprii; MAIN le vede pe toate din cimitirul legăturii. Tenantul vine din legătură, niciodată din corp. Nu există rută de DELETE. Clientul nu primește parole DB, `SUPABASE_SECRET_KEY`, `service_role`, parola `cimitir_cp_app` sau credențiale de cleanup.
Pornire: `cimitir_field_inbox_http.create_app()` (citește `CIMITIR_CP_DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `SUPABASE_BUCKET` din mediu).

## Teste
- Local (PostgreSQL real, rol `cimitir_cp_app`): `CIMITIR_TEST_PG_ADMIN_URL=postgresql://<superuser>@127.0.0.1:<port>/postgres pytest -n 0 backend/tests/test_etapa6c_field_inbox_pg.py`.
- Live Supabase TEST (sărit implicit): `CIMITIR_LIVE_TESTS=1`, `CIMITIR_TEST_PROJECT_REF=nhhczdmcocbobptogvsw`, `CIMITIR_TEST_DATABASE_URL`, `SUPABASE_URL/SECRET_KEY/BUCKET`, `CIMITIR_CP_CLEANUP_DATABASE_URL`; `CIMITIR_APPLY_MIGRATION_018=1` aplică 018 (idempotent) după confirmarea ref-ului: `backend/tests/test_etapa6c_field_inbox_live.py`.

## Backlog (nu e în v1)
Obiecte READY rămase nelegate dacă procesul cade între `upload` și atașare (nu le reia cleanup-ul 6B), retenția obiectelor IMPORTED, plafon DB pentru numărul de fișiere (acum doar în aplicație), pairing pentru dispozitive MOBILE (acum legătura MOBILE se creează explicit; `redeem_code` creează doar MAIN), rate limiting pe API, upload pe bucăți/TUS, curățarea obiectelor READY ale itemurilor REJECTED (acum doar prin `delete` 6B), editarea unui item PENDING, UI MAIN/telefon, import SQLite.
