# Etapa 7-9 — Migrații SQL și rollback

| Migrare | Fișier | Ce face | Rollback |
|---|---|---|---|
| 024 | `backend/cimitir/pg_migrations/024_sync_v2_documents.sql` | tabelul `sync_v2_documents` (metadate documente/fotografii), RLS pe tenant, trigger de versiune/`change_seq`/imutabilitate, GRANT-uri minime pentru `cimitir_cp_app` | `backend/cimitir/pg_rollbacks/024_sync_v2_documents_ROLLBACK.sql` |

Proprietăți: **aditivă**, **idempotentă** (`IF NOT EXISTS` / `CREATE OR REPLACE` / `DROP POLICY IF EXISTS`), nu modifică 001–023, nu atinge `documents`, `photos`, `media_objects`, `cp_storage_objects`
(Field Inbox rămâne neschimbat), nu șterge date, **nu dezactivează RLS** (`ENABLE ROW LEVEL SECURITY`; rolul runtime nu are `BYPASSRLS`, nici `DELETE` pe tabel).

Filtrarea datelor personale (CNP, act de identitate) **nu necesită migrare**: se face în codul serverului (`cimitir_sync_v2.py`, `cimitir_mobile_registry.py`).

## Aplicare (doar după aprobare; NICIODATĂ direct pe LIVE/PROD fără backup)

```bash
# proiect TEST (gol sau deja pregătit); URL-ul administrativ vine din mediu, nu din fișiere
export CIMITIR_CP_ADMIN_DATABASE_URL='postgresql://postgres.<REF>:<PAROLA>@aws-0-<regiune>.pooler.supabase.com:5432/postgres?sslmode=require'
export CIMITIR_CP_APP_PASSWORD='<parola rolului runtime cimitir_cp_app>'
python scripts/setup_test_project.py --project-ref <REF> --check-only     # raportează, nu modifică
python scripts/setup_test_project.py --project-ref <REF>                  # aplică 001..024 în ordine, setează parola rolului runtime, verifică RLS
```
`setup_test_project.py` refuză proiectul PROD (`PROTECTED_PROJECT_REFS`) și un URL care nu corespunde `--project-ref`.
Pentru o bază care are deja 001–023 se poate aplica doar 024: `psql "$CIMITIR_CP_ADMIN_DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/cimitir/pg_migrations/024_sync_v2_documents.sql`.

## Rollback

```bash
# 1) backup al metadatelor (opțional, recomandat)
psql "$CIMITIR_CP_ADMIN_DATABASE_URL" -c "CREATE TABLE sync_v2_documents_backup AS SELECT * FROM sync_v2_documents;"
# 2) rollback 024
psql "$CIMITIR_CP_ADMIN_DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/cimitir/pg_rollbacks/024_sync_v2_documents_ROLLBACK.sql
```
Efect: dispar doar metadatele documentelor sincronizate. **Octeții din Storage rămân** (cale `docs/<organizație>/<cimitir>/<uid>`), iar documentele/fotografiile din MAIN rămân neatinse;
după un rollback urmat de re-aplicare, MAIN retrimite automat (SHA-256 evită duplicatele). Pentru 023/022 se folosesc rollback-urile existente din `pg_rollbacks/` (în ordinea inversă: 024 → 023 → 022).

## Secrete
Nu se salvează în repo. Variabile: `CIMITIR_CP_ADMIN_DATABASE_URL` (doar pentru migrații/seed), `CIMITIR_CP_DATABASE_URL` (rol runtime `cimitir_cp_app`, fără drepturi de administrator),
`SUPABASE_URL`, `SUPABASE_SECRET_KEY` (doar procesul Cloud, pentru Storage), `SUPABASE_BUCKET`, `CIMITIR_CLOUD_PROJECT_REF`, `CIMITIR_PUBLIC_URL`, `CIMITIR_PAIRING_TRUSTED_PROXIES`.


## 025 — provizionarea registrului MAIN în Cloud (`025_admin_provision_registry.sql`)
- Conține doar `CREATE OR REPLACE FUNCTION` (+ GRANT EXECUTE pentru `cimitir_cp_app`): `cp_admin_is_provisioner`, `cp_admin_registry_lookup`, `cp_admin_provision`. Nu modifică tabele, RLS sau date.
- Autorizarea e în baza de date (SECURITY DEFINER): administrator activ; organizație existentă ⇒ rol Owner; organizație nouă ⇒ administratorul inițial al instalării sau Platform Admin; cimitir cu UID aparținând altei organizații ⇒ refuz.
- Aplicare pe un Cloud existent: `python scripts/apply_cloud_provision_migration.py --project-ref <REF> --check-only`, apoi aceeași comandă fără `--check-only` (o tranzacție; verifică că numărul de rânduri rămâne identic; cere 023).
- Rollback: `--rollback` (rulează `pg_rollbacks/025_admin_provision_registry_ROLLBACK.sql`; elimină doar funcțiile, organizațiile/cimitirele create rămân). Fără 025, Cloud-ul pornește, dar endpoint-urile `/admin/registry` și `/admin/provision` răspund 501 `SCHEMA_OUTDATED`, iar asocierea manuală cu cod funcționează ca înainte.
