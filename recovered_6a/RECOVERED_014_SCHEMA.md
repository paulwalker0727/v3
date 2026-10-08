# RECOVERED_014_SCHEMA.md

Sursă forensic: `origin/conflict_081026_2054:.pgdata` (PostgreSQL 15, DB `cimitir_test6a`, `schema_migrations` conține `014_bootstrap_control_plane.sql`, aplicat 2026-10-08 17:41 UTC). Lucrat numai pe copie (`/app/recovered_6a/work/pgdata_copy`), pornit pe socket Unix, fără rețea, apoi oprit. Originalul `.pgdata` și `v3/` sunt nemodificate. Parola Supabase nu apare nicăieri.

## Fișiere
- `RECOVERED_014_bootstrap_control_plane.sql` – 014 reconstituit (funcții + 23 tabele cp_* + RLS + triggere + indecși + grants). NU e fișierul original (comentarii/ordine pierdute).
- `RECOVERED_full_schema_test6a_with_acl.sql` – pg_dump --schema-only complet al DB recuperate (cu ACL).
- `DIFF_013_vs_recovered.diff` – diferența dintre 001→013 din v3/main aplicate pe DB curată și DB recuperată.

## Verificări
- DB recuperată = migrările 001–013 din v3/main (identice, 0 diferențe în afara cp_* și `schema_migrations`) + 014 strict aditiv.
- Reaplicarea fișierului recuperat peste o DB 001→013 curată produce un dump identic cu cel recuperat (diferă doar `schema_migrations`, tabel creat de runner).
- 23 tabele cp_*, toate cu 0 rânduri (nu există date de test în snapshot).

## Roluri
`cimitir_cp_app` NOLOGIN, NOSUPERUSER, NOBYPASSRLS (rol runtime pentru cp_*; conectarea la el – SET ROLE / membership – NU se poate deduce). Există și `cimitir_app` (din 010) și `cimitir`.

## Funcții (9 + trigger)
`cp_org()`, `cp_platform()`, `cp_binding_uid()`, `cp_key_hash()`, `cp_code_hash()` – citesc GUC `cp.org`, `cp.platform`, `cp.binding_uid`, `cp.key_hash`, `cp.code_hash`. `cp_org_ok(org)`, `cp_cem_ok(cem)` (GUC `cp.cems`, listă separată prin virgulă sau `*`). `cp_cemetery_org(cem)` SECURITY DEFINER, `SET search_path = public, pg_catalog`, `REVOKE ... FROM PUBLIC`, EXECUTE doar `cimitir_cp_app`. `cp_fill_org()` trigger BEFORE INSERT (12 tabele): completează `organization_uid` din `cp_cemeteries` (sau din `cp_bindings` pentru `cp_binding_issuers`).

## Tabele (23)
cp_organizations, cp_principals, cp_memberships (role ∈ owner/rebuild/admin), cp_cemeteries (status DRAFT/ACTIVE/ARCHIVED, data_epoch ≥ 1, UNIQUE(uid, organization_uid)), cp_devices, cp_pairing_codes (code_hash hex64, cemetery_uid NULL = provizionare), cp_failed_redeems, cp_bindings (status pending/active/revoked, key_hash hex64 UNIQUE, sync_epoch, last_change_seq, UNIQUE(device_uid, cemetery_uid)), cp_binding_issuers, cp_bootstrap_runs (kind ∈ A/B, state ∈ planned/running/verifying/completed/failed/rolled_back, bootstrap_id UUID, checkpoint TEXT), cp_staging_sessions (state open/stale/cleaned/published), cp_staging_batches, cp_staging_rows, cp_staging_media_blobs, cp_staging_media_chunks (bytea), cp_registry_rows, cp_registry_row_data (content TEXT, change_seq bigint), cp_media_objects, cp_media_blobs (bytea), cp_media_published, cp_open_conflicts, cp_tombstones (tombstone_id IDENTITY ALWAYS), cp_audit_log (id IDENTITY BY DEFAULT; doar SELECT,INSERT pentru cimitir_cp_app).

Constrângeri: 28 PK, 27 FK (toate tabelele cu cemetery_uid au FK compus `(cemetery_uid, organization_uid) → cp_cemeteries(uid, organization_uid)` = izolare tenant la nivel de FK), 5 UNIQUE, 18 CHECK. Lista completă în SQL.

## Indecși parțiali confirmați (exact)
```
CREATE UNIQUE INDEX cp_one_active_bootstrap ON cp_bootstrap_runs (cemetery_uid)
  WHERE state IN ('planned','running','verifying');
CREATE UNIQUE INDEX cp_one_open_staging_per_cemetery ON cp_staging_sessions (cemetery_uid)
  WHERE state = 'open';
```

## RLS
ENABLE ROW LEVEL SECURITY pe toate cele 23; **FORCE = NU** pe niciuna (consistent cu 010: nici 001–013 nu folosesc FORCE). O politică `cp_tenant` (FOR ALL, TO cimitir_cp_app) per tabel: tabele cu cimitir → `cp_org_ok(organization_uid) AND cp_cem_ok(cemetery_uid)`; cp_organizations `USING cp_org_ok(uid) WITH CHECK cp_platform()`; cp_principals vizibile doar platform sau prin membership; cp_bindings acces și prin (binding_uid, key_hash) din GUC; cp_pairing_codes și prin `cp_code_hash()`; cp_failed_redeems `USING (true)` (global).

## Grants
USAGE pe schema public, DML pe cele 23 tabele (cp_audit_log doar SELECT/INSERT), USAGE/SELECT pe secvențe — inclusiv pe TOATE secvențele din 001–013 (probabil `GRANT ... ON ALL SEQUENCES`, prea larg). `schema_migrations` → cimitir_app (artefact al runner-ului).

## Ce NU se poate deduce din bază
Textul original 014 (comentarii, ordine), dacă folosea `current_schema()` sau `public` hardcodat (în catalog `cp_cemetery_org` are `search_path=public,pg_catalog` literal), adapterul PostgreSQL, cum se setează GUC `cp.*` (SET LOCAL/set_config), cum se conectează rolul NOLOGIN, algoritmul de `change_seq`, advisory lock-uri în adapter, bugfix-ul de citire rânduri, testele 6A, rapoartele 6A.

# PASUL 2 – Comparație cu ETAPA4_BOOTSTRAP_DESIGN.md și codul Pașilor 1–5.1 (main b0ebb63)

## Se potrivește
- 1:1 cu tabelele simulatorului SQLite (`cimitir_cloud_control/_staging/_media.py`): principals, memberships, organizations, cemeteries, registry_rows, registry_row_data, media_objects, open_conflicts, tombstones, pairing_codes, failed_redeems, devices, bindings, bootstrap_runs, binding_issuers, audit_log, staging_*, media_blobs, media_published – cu aceleași coloane și stări (inclusiv staging open/stale/cleaned/published).
- Indecși parțiali one_active_bootstrap / one_open_staging_per_cemetery = identici cu simulatorul și cu design §12.
- bootstrap_id UUID, plan_hash, stări planned…rolled_back (§12), audit_log append-only (design §4 „audit imuabil"), roluri owner/rebuild/admin (§8), tombstone retention 180 zile (§7.7), data_epoch ≥ 1.
- Tenant-izolare prin organization_uid + FK compuse + RLS (aliniat cu principiul 008–013).

## Diferit față de simulator (mai strict – de ținut cont în adapter/teste)
- Format UID: CHECK uuid pe organizations/cemeteries/bootstrap_id, `[0-9a-f-]{36}` pe bootstrap_runs.cemetery_uid; hash-uri hex64 – simulatorul accepta orice text.
- `organization_uid` în tabelele de date (completat de trigger); FK compus bindings→devices/cemeteries.
- change_seq/size bigint; tombstones primesc tombstone_id; bytea în loc de BLOB.
- kind ∈ {A,B} doar → Flux C/D/E vor cere migrare ulterioară (acceptabil pentru 6A).

## Lipsește față de design v2 §4/§10 (CONTRADICȚIE ARHITECTURALĂ)
1. §4 cere 014 *aditiv peste 008–013*: `cemeteries.data_epoch`, `epoch_registry`, scheme `bootstrap_stage` / `epoch_archive` (REVOKE din anon/authenticated/public), `bootstrap_runs`, `organization_roles`, funcții de swap SECURITY DEFINER. Recuperatul NU are `epoch_registry`, scheme separate, `organization_roles` (înlocuit de cp_memberships) sau swap; folosește staging în tabele `cp_staging_*` din același schema.
2. §5 spune că tenancy 008–013 (organizations, cemeteries, devices, bindings cu sync_key_hash/sync_epoch/last_change_seq, device_pairing_codes) trebuie *folosită efectiv*. Recuperatul creează un plan de control PARALEL (`cp_organizations`, `cp_cemeteries`, `cp_devices`, `cp_bindings`, `cp_pairing_codes`) fără FK către tabelele 008–013 → două surse de adevăr pentru organizații/cimitire în același DB.
3. `checkpoint` TEXT (design: JSONB); fără `manifest_source/target`, `started_at`, `device_uid` în bootstrap_runs.
4. `pg_advisory_lock` per cimitir (design §12) – nedetectabil în schemă; în pg_compat există doar lock global de scriere.
5. Media: bytea în DB (design §11: object store) – compatibil cu limita Pasului 5 / 6A fără Storage real.

## Riscuri de securitate/robustețe în schema recuperată
- `cp_cemetery_org` are `search_path=public,pg_catalog` hardcodat și `cp_*` fără prefix de schemă → **la fresh install în schemă temporară ar citi `public.cp_cemeteries` (sau ar eșua)** → 014 reconstruit trebuie parametrizat pe `current_schema()` (cum fac 010–013). Testul de fresh-install în schemă temporară trebuie oprit dacă rămâne hardcodat.
- `cp_failed_redeems` cu `USING (true)`: orice sesiune cp_app poate citi/modifica toate contoarele (DoS pe redeem).
- GRANT USAGE/SELECT pe toate secvențele 001–013 către cimitir_cp_app: prea larg (de restrâns la cp_*).
- FORCE RLS absent: proprietarul tabelelor ocolește RLS (la fel ca în 010); sesiunea runtime trebuie să fie strict `cimitir_cp_app`.
- Rolul `cimitir_cp_app` este NOLOGIN: mecanismul de conectare nu e recuperabil.

## Concluzie
Schema recuperată este completă, coerentă și reproductibilă, și acoperă exact Flux A/B ca în simulator. Contrazice însă §4/§5 din design (014 ca extensie peste tabelele 008–013 + epoch_registry/scheme stage-archive). Necesită decizia ta înainte de implementare.
