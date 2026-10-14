# Cloud Connection Profiles (MAIN / PySide6)

Fereastra **Sincronizare cloud → Profile conexiune…** (`desktop/cloud_profiles_dialog.py`, logica în `desktop/cloud_profiles.py`).

## Profile
`TEST` / `PRODUCTION` / `CUSTOM`; un singur profil activ (index unic parțial SQLite `ux_cloud_profiles_one_active`).
Câmpuri: profile name, environment, project_ref, api_base_url, supabase_url, db_host, db_port, db_name, db_user, sslmode, storage_bucket.
Tabel local `cloud_connection_profiles` (creat lazy, ca `cloud_sync_outbox`); nu se ating migrările PostgreSQL 001–019.

## Secrete
Exclusiv `shared/cimitir_secretstore.py` (DPAPI pe Windows). SQLite păstrează doar `*_ref`
(`cimitir/cloud/profiles/<id>/{db_password,supabase_secret_key,sync_key}`). `db_password` și `supabase_secret_key` sunt opționale (Advanced / Server mode);
profilul normal nu le are. UI arată `********` + „Înlocuiește parola" / „Înlocuiește Secret Key" (+ cheia de sincronizare API).
Ștergerea profilului șterge secretele și verifică; la eșec profilul rămâne (reluabil). Profilul activ / PRODUCTION cer confirmare.

## Cheia veche de sync
`cloud_profiles.migrate_legacy_sync_key()`: citește `settings.cloud_sync_key` → SecretStore → read-back → `cloud_sync_key_ref` → abia apoi ștergere
(`secure_delete` + checkpoint WAL). La orice eșec valoarea veche rămâne și migrarea se reia (la deschiderea ferestrei și la `AutoSync.run_once`).
`cloud_sync.save_config` nu mai scrie niciodată cheia în SQLite.

## cloud_sync
`get_config()` folosește profilul activ (url + cheie din SecretStore). După ce s-au folosit profile și niciunul nu e activ → neconfigurat.
Fără profile → setările vechi (url) + cheia din SecretStore.

## Test Connection (READ-ONLY)
- API: `/api/health`, `/api/sync/connection-info` (tenant/device), cemetery binding. Doar GET, doar `X-Sync-Key`/`X-Sync-Client`.
- Advanced: PostgreSQL (tranzacție read-only, `SELECT`), sslmode + TLS, project ref, `schema_migrations` max version (≥ 19), bucket există, `public=false`.
Testele automate folosesc mock/fixture pentru PostgreSQL/Storage; **LIVE PostgreSQL/Storage: nerulat**. `test_connection(..., pg_connect=, storage_factory=)` rulează real fără alte modificări pe Windows/Supabase.

## Backend Linux
Neschimbat: variabile de mediu (`backend/cloud_config.py`, `/admin/cloud`).

## Teste
`python -m pytest desktop/tests/test_cloud_profiles.py`
Testele de subproces care apelează `cloud_sync.save_config` primesc `CIMITIR_SECRET_STORE=test-encrypted` + `CIMITIR_ALLOW_TEST_SECRETSTORE=1` + `CIMITIR_TEST_SECRET_KEY` (doar teste).
