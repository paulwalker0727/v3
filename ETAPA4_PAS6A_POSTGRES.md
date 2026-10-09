# Etapa 4 / Pasul 6A — planul de control pe PostgreSQL (014 peste 008–013)

Stare: implementat și testat pe **PostgreSQL local**. Supabase TEST: NEATINS (urmează după confirmare). Production: neatins, blocat permanent.

## Arhitectură
- **Sursă unică de adevăr** pentru tenancy: `organizations`, `cemeteries`, `devices`, `device_cemetery_bindings`, `device_pairing_codes` (008–013). 014 NU creează duplicate; le extinde aditiv:
  - `cemeteries.data_epoch integer NOT NULL DEFAULT 1 CHECK (>= 1)`;
  - `device_pairing_codes.cemetery_uid` devine NULLABLE (cod de provizionare: MAIN propune cimitirul) + coloana `used_bootstrap_id`.
- Tabele noi, strict de bootstrap, cu FK către cele canonice (FK compuse `(cemetery_uid, organization_uid)` → `cemeteries`):
  `cp_bootstrap_runs`, `cp_staging_sessions/batches/rows/media_blobs/media_chunks`, `cp_failed_redeems`, `cp_audit_log`, `cp_binding_issuers`,
  `cp_platform_admins`, `cp_org_roles` (rolurile owner/rebuild/admin nu au echivalent în `organization_members`), și ținta publicării Flux A/B
  `cp_registry_rows`, `cp_registry_row_data`, `cp_media_objects/blobs/published`, `cp_open_conflicts`, `cp_tombstones`.
- `cp_bootstrap_runs`: `bootstrap_id`, `plan_hash`, `checkpoint JSONB`, `manifest_source JSONB`, `manifest_target JSONB`, `data_epoch`, `created_at/started_at/finished_at`, `error`.
  Invarianți: `cp_one_active_bootstrap` (UNIQUE(cemetery_uid) WHERE state IN planned/running/verifying) și `cp_one_open_staging_per_cemetery` (UNIQUE(cemetery_uid) WHERE state='open').
  Run-ul nu are FK pe cimitir (în Flux A se începe înainte ca MAIN să creeze cimitirul propus); organizația are FK.
- Corespondențe față de simulatorul SQLite: `bindings.status` = (`revoked_at IS NOT NULL` → revoked; `active` → active; altfel pending); epoca (`sync_epoch` int) = `cemeteries.data_epoch`
  (`device_cemetery_bindings.sync_epoch` este UUID-ul Sync V2, neatins); re-pairing după revocare creează o legătură nouă (`replaced_by_binding_uid` pe cea veche).

## manifest_source / manifest_target
- `manifest_source` = rezumat (digesturi + numărători, `cimitir_manifest.summary`) al manifestului MAIN din planul B, scris la `begin_run`.
- `manifest_target` = rezumat al stării cloud LIVE, calculat ÎN tranzacția de publish (după ACTIVE, înainte de `completed`) și verificat contra sursei (digest, numărători pe entități, media). Dacă nu poate fi calculat sau diferă ⇒ `PublishBlocked`, rollback integral (cimitir DRAFT, zero rânduri, run nu devine `completed`).
- Retry după publish (răspuns pierdut / resume): calea `already` RE-calculează și re-verifică ținta LIVE, fără al doilea publish și fără a rescrie `manifest_target`.
- Flux A: ținta e golul intenționat (ACTIVE + 0 rânduri), nu are `manifest_target` (necesar doar pentru B).

## Fără `public` hardcodat
Toate obiectele se creează în `current_schema()`. Funcțiile SECURITY DEFINER (`cp_org_exists`, `cp_cemetery_org`) își fixează `search_path` la `current_schema()` prin `EXECUTE format(...)`. Testat: fresh install 001→014 în schemă temporară, `public` rămâne gol.

## Rol aplicație și context RLS
- Rol: **`cimitir_cp_app`** — `LOGIN`, `NOSUPERUSER`, `NOBYPASSRLS`; parola se setează separat (nu în Git/loguri). Se conectează **FastAPI** (procesul `cimitir_cloud_http`), prin `CIMITIR_CP_DATABASE_URL` (+ `CIMITIR_CP_SCHEMA` opțional). `CIMITIR_CP_ADMIN_DATABASE_URL` (owner/superuser) se folosește DOAR pentru seed/teste (`create_principal`, `sim_*`); în producție nu se setează.
- Context RLS: GUC de sesiune `cp.platform, cp.org, cp.cems, cp.principal, cp.binding_uid, cp.key_hash, cp.code_hash, cp.installation, cp.run_id`, setate de adaptor DOAR după autentificare/autorizare server-side (`_require`, `_code_row`, `authenticate`) și resetate la începutul/sfârșitul fiecărei cereri. Lipsă context ⇒ zero rânduri.
- Politicile pe tabelele canonice sunt ADITIVE și `TO cimitir_cp_app` (politicile 010 pentru `cimitir_app` rămân neschimbate). Capabilități înguste: `bootstrap_id` (+ `installation_uid`) pentru închiderea unui run fără cheie; `(binding_uid, key_hash)` pentru propria legătură; `code_hash` pentru propriul cod; `cp_failed_redeems` strict pe `installation_uid` (nu `USING (true)`).
- Grants minime: DML doar pe tabelele necesare; SELECT/INSERT pe `cp_audit_log` (append-only); USAGE doar pe cele 2 secvențe `cp_*`; fără drepturi pe celelalte tabele/secvențe 001–013.

## Lacăt per cimitir
`pg_advisory_xact_lock(hashtextextended('cimitir:cp:cemetery:'||uid,0))` prin `cp_lock_cemetery(uuid)` la: redeem, begin_run, staging open/put/mark/cleanup, media put/finalize/cleanup, publish. `cp_try_lock_cemetery` pentru teste.

## Amânat explicit (nu e necesar pentru Flux A/B în 6A)
`epoch_registry`, schemele `bootstrap_stage` / `epoch_archive`, funcțiile de swap D/E, mapping-ul din `cp_registry_rows` (format generic pe fir) în tabelele de entități (Sync V2). Fundația există: `cemeteries.data_epoch`, `cp_bootstrap_runs.data_epoch`, `cp_staging_sessions.data_epoch`.

## Teste (necesită `CIMITIR_TEST_PG_ADMIN_URL=postgresql://<superuser>@127.0.0.1:<port>/postgres`, altfel SKIP)
- `test_etapa4_pas6a_postgres.py`: schemă/RLS/grants, fresh install 001→014 în schemă temporară, upgrade 013→014 (+idempotent), hard-block production, Flux A/B direct și HTTP, commit-dar-răspuns-pierdut, server omorât + restart + resume, bootstrap concurent + advisory lock, 2 org × 2 cimitire (leakage = 0), bindingul unui tenant nu accesează alt tenant.
- `test_etapa4_pas6a_reruns_on_pg.py`: suitele existente Pas 4 / 5 / 5.1 rulate peste PostgreSQL, cu logica nemodificată.
