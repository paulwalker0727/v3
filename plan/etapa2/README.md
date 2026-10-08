# Etapa 2 — migrări multi-tenant (oficiale)

Migrările sunt în `backend/cimitir/pg_migrations/` (draft-urile E2_008–E2_013 au fost mutate și șterse de aici):

| Fișier | Rol |
|---|---|
| 008_tenancy_core.sql | organizații, cimitire, identități, membri, permisiuni, dispozitive, legături dispozitiv × cimitir |
| 009_tenant_scope_backfill.sql | coloane `organization_uid` / `cemetery_uid` + backfill (fără a bumpa version/change_seq/modified_at) |
| 010_rls_tenant_isolation.sql | funcții de context, rol `cimitir_app` (NOBYPASSRLS), politici RLS |
| 011_composite_tenant_keys.sql | PREFLIGHT + NOT NULL + chei/FK compuse pe cimitir, e-mail unic per organizație |
| 012_same_tenant_links.sql | PREFLIGHT + FK tenant-safe pe legăturile prin id |
| 013_tombstone_tenant.sql | tombstone-urile (sync + media) poartă tenantul |

**Protecție:** migrările >= 008 se aplică doar cu `CIMITIR_ALLOW_TENANT_MIGRATIONS=1` și NICIODATĂ pe o bază care conține
un proiect protejat (`txuxkjgftopznvmooexq`) — fără ocolire. Fără acestea, `init_db` se oprește cu mesaj explicit, fără modificări.

**Validare (doar Supabase TEST `nhhczdmcocbobptogvsw`, `CIMITIR_LIVE_TESTS=1`, scheme izolate `e2_*`):**

    python plan/etapa2/validate_migrations_on_supabase_test.py     # fresh, upgrade, preflight, cod real al aplicației, RLS, pooler, device
    python plan/etapa2/validate_on_supabase_test.py                # RLS / chei compuse / device (CIMITIR_E2_SCHEMA=<schemă>)
    CIMITIR_TEST_DATABASE_URL=$DATABASE_URL CIMITIR_TEST_SCHEMA=e2_diff ETAPA6_SERVER_URL=http://127.0.0.1:8001 \
        pytest backend/tests/test_postgres.py                      # paritate PostgreSQL ↔ SQLite
